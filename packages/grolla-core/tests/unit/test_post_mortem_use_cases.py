"""Casos de uso do post mortem: rascunho e regeneração no worker, edição, status, plano de ação,
lista e PDF.
"""

from dataclasses import replace
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.follow_up import ImpactPolicy
from grolla.application.investigation import (
    POST_MORTEM_PROMPT,
    STAGE_POST_MORTEM,
    InvalidAnalysisError,
    LanguageModelError,
    PostMortemBrief,
    post_mortem_text,
    regeneration_text,
)
from grolla.application.post_mortem import (
    NO_INCIDENT,
    NO_POST_MORTEM,
    PostMortemFilters,
    PostMortemNotFoundError,
    PostMortemPolicy,
    RenderError,
    add_action,
    edit_section,
    list_post_mortems,
    move_status,
    overdue_items,
    reap_stuck_post_mortems,
    render_pdf,
    request_regeneration,
    run_next_post_mortem,
    update_action,
)
from grolla.domain.follow_up import Cycle, CycleStatus, Trend
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.domain.post_mortem import (
    AGENT_AUTHOR,
    DETERMINISTIC,
    ActionKind,
    ActionStatus,
    InvalidPostMortemError,
    PostMortem,
    PostMortemStatus,
    SectionKey,
)
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.investigation import ScriptedLanguageModel
from grolla.testing.post_mortem import (
    FakeDocumentRenderer,
    InMemoryPostMortemStore,
    make_action,
    make_post_mortem,
)

SERVICE = "pedidos-api"


class World:
    def __init__(self, model: ScriptedLanguageModel | None = None) -> None:
        self.repository = InMemoryIncidentRepository()
        self.store = InMemoryPostMortemStore(self.repository)
        self.model = model or ScriptedLanguageModel()
        self.clock = FakeClock(T0 + timedelta(hours=1))
        self.policy = PostMortemPolicy(max_tokens=100_000, timeout=timedelta(seconds=5))

    async def resolved(self, *, request: bool = True) -> UUID:
        incident = replace(
            make_incident(state=IncidentState.RESOLVED, services=(SERVICE,)),
            resolved_at=T0 + timedelta(minutes=32),
        )
        alert = make_alert(service=SERVICE, starts_at=T0 - timedelta(minutes=3))
        resolution = IncidentEvent(
            incident.id, T0 + timedelta(minutes=32), EventType.INCIDENT_RESOLVED, {}, "Ana"
        )
        async with self.repository.transaction() as uow:
            await uow.save(incident)
            await uow.link_alert(alert, incident.id)
            await uow.append([resolution])
            if request:
                await uow.request_post_mortem(incident.id, self.clock.now())
        return incident.id

    async def run(self, impact: ImpactPolicy | None = None) -> bool:
        return await run_next_post_mortem(
            store=self.store,
            repository=self.repository,
            model=self.model,
            clock=self.clock,
            policy=self.policy,
            impact=impact,
        )

    async def document(self, incident_id: UUID) -> PostMortem:
        pm = await self.store.get(incident_id)
        assert pm is not None
        return pm

    async def drafted(self) -> UUID:
        incident_id = await self.resolved()
        await self.run()
        return incident_id

    async def regenerate(
        self, incident_id: UUID, key: SectionKey, instruction: str | None = None
    ) -> None:
        await request_regeneration(
            store=self.store,
            incident_id=incident_id,
            key=key,
            instruction=instruction,
            author="Ana",
            clock=self.clock,
        )


def body_of(pm: PostMortem, key: SectionKey) -> str:
    return next(s.body for s in pm.sections if s.key is key)


def job_status(world: World, incident_id: UUID) -> list[tuple[str, str | None]]:
    return [(j.status, j.reason) for j in world.store.jobs if j.incident_id == incident_id]


# ---- Rascunho ----


async def test_nothing_to_do_without_requests() -> None:
    assert await World().run() is False


async def test_resolved_incident_gets_the_draft_with_metrics_timeline_and_plan() -> None:
    world = World()
    incident_id = await world.resolved()

    assert await world.run() is True

    pm = await world.document(incident_id)
    assert pm.status is PostMortemStatus.DRAFT
    assert body_of(pm, SectionKey.SUMMARY) == "Resumo executivo de Taxa de erro alta."
    assert "**TTD** (do primeiro alerta à detecção): 3 min" in body_of(pm, SectionKey.METRICS)
    assert "[pessoa] Ana: encerrou o incidente" in body_of(pm, SectionKey.TIMELINE)
    assert len(pm.actions) == 2
    assert job_status(world, incident_id) == [("feito", None)]
    assert [u.stage for _, u, _ in world.repository.usages] == [STAGE_POST_MORTEM]


async def test_model_receives_the_war_room_context_cycles_and_impact() -> None:
    world = World()
    incident_id = await world.resolved()
    cycle = Cycle(
        incident_id, 1, CycleStatus.COMPLETED, T0, trend=Trend.BETTER, summary="Erro caiu."
    )
    pending = Cycle(incident_id, 2, CycleStatus.PENDING, T0)
    world.repository.cycles[(incident_id, 1)] = cycle
    world.repository.cycles[(incident_id, 2)] = pending

    await world.run(ImpactPolicy(per_minute={SERVICE: 100.0}))

    (brief,) = world.model.post_mortem_briefs
    assert f"Serviços: {SERVICE}" in brief.context
    assert "- 1 (melhorando): Erro caiu." in brief.context
    assert "- 2 (pendente): " in brief.context
    assert "Timeline completa" in brief.context
    assert brief.impact is not None
    assert brief.current is None
    assert brief.impact in post_mortem_text(brief)


async def test_draft_data_carries_the_applied_command_and_where() -> None:
    world = World()
    incident_id = await world.resolved()
    applied = {
        "text": "Restaurar as réplicas",
        "command": "kubectl scale deploy/antifraude-api --replicas=2 -n loja",
        "run_in": "cluster loja-prod, namespace loja",
    }
    async with world.repository.transaction() as uow:
        await uow.append(
            [IncidentEvent(incident_id, T0, EventType.ACTION_RECORDED, applied, "Ana")]
        )

    await world.run()

    (brief,) = world.model.post_mortem_briefs
    line = (
        "[pessoa] Ana: Aplicou: Restaurar as réplicas — "
        "kubectl scale deploy/antifraude-api --replicas=2 -n loja "
        "(cluster loja-prod, namespace loja)"
    )
    assert line in post_mortem_text(brief)
    assert line in body_of(await world.document(incident_id), SectionKey.TIMELINE)


async def test_model_failure_still_writes_the_post_mortem_with_the_reason() -> None:
    world = World(ScriptedLanguageModel(post_mortem_error=LanguageModelError("fora do ar")))
    incident_id = await world.resolved()

    await world.run()

    pm = await world.document(incident_id)
    assert body_of(pm, SectionKey.FACTORS) == "não gerada: LLM indisponível"
    assert body_of(pm, SectionKey.METRICS).startswith("- **TTD**")
    assert job_status(world, incident_id) == [("falho", "LLM indisponível")]


async def test_slow_model_times_out() -> None:
    world = World(ScriptedLanguageModel(post_mortem_delay=1.0))
    world.policy = PostMortemPolicy(timeout=timedelta(milliseconds=10))
    incident_id = await world.resolved()

    await world.run()

    assert body_of(await world.document(incident_id), SectionKey.SUMMARY) == (
        "não gerada: tempo esgotado"
    )


async def test_draft_is_not_rewritten_when_it_already_exists() -> None:
    world = World()
    incident_id = await world.resolved()
    existing = make_post_mortem(incident_id, status=PostMortemStatus.REVIEW)
    await world.store.save(existing, [])

    await world.run()

    assert await world.document(incident_id) == existing
    assert world.model.post_mortem_briefs == []


async def test_request_without_incident_ends_failed() -> None:
    world = World()
    incident_id = uuid4()
    world.repository.post_mortem_requests[incident_id] = T0

    await world.run()

    assert job_status(world, incident_id) == [("falho", NO_INCIDENT)]


# ---- Regeneração ----


async def test_regeneration_rewrites_the_section_keeping_the_previous_version() -> None:
    world = World()
    incident_id = await world.drafted()
    await edit_section(
        store=world.store,
        incident_id=incident_id,
        key=SectionKey.SUMMARY,
        body="Resumo do time.",
        author="Bruno",
        clock=world.clock,
    )
    before = body_of(await world.document(incident_id), SectionKey.FACTORS)

    await world.regenerate(incident_id, SectionKey.FACTORS, " incluir a falta de alarme ")
    await world.run()

    pm = await world.document(incident_id)
    assert body_of(pm, SectionKey.FACTORS) == (
        "Fatores contribuintes reescrita (incluir a falta de alarme)."
    )
    assert body_of(pm, SectionKey.SUMMARY) == "Resumo do time."
    (version,) = await world.store.versions(incident_id, SectionKey.FACTORS)
    assert (version.author, version.body) == (AGENT_AUTHOR, before)
    assert version.reason == "regenerada pelo agente a pedido de Ana: incluir a falta de alarme"
    brief, key, instruction = world.model.regenerations[0]
    assert (key, instruction) == (SectionKey.FACTORS, "incluir a falta de alarme")
    assert brief.current is not None
    assert body_of(brief.current, SectionKey.SUMMARY) == "Resumo do time."
    text = regeneration_text(brief, key, instruction)
    assert "Resumo do time." in text
    assert "Instrução: incluir a falta de alarme" in text


async def test_deterministic_section_is_recalculated_without_the_model() -> None:
    world = World()
    incident_id = await world.drafted()
    async with world.repository.transaction() as uow:
        await uow.append(
            [IncidentEvent(incident_id, T0, EventType.ACTION_RECORDED, {"text": "rollback"}, "Ana")]
        )

    await world.regenerate(incident_id, SectionKey.TIMELINE)
    await world.regenerate(incident_id, SectionKey.METRICS)
    await world.run()
    await world.run()

    pm = await world.document(incident_id)
    assert "registrou uma ação: rollback" in body_of(pm, SectionKey.TIMELINE)
    assert world.model.regenerations == []
    (version,) = await world.store.versions(incident_id, SectionKey.METRICS)
    assert version.reason == "recalculada do log a pedido de Ana"


async def test_regeneration_failure_keeps_the_section() -> None:
    world = World(ScriptedLanguageModel(regeneration_error=InvalidAnalysisError("fora")))
    incident_id = await world.drafted()
    before = await world.document(incident_id)

    await world.regenerate(incident_id, SectionKey.SUMMARY)
    await world.run()

    assert await world.document(incident_id) == before
    assert job_status(world, incident_id)[-1] == ("falho", "resultado inválido")


async def test_post_mortem_approved_meanwhile_is_not_regenerated() -> None:
    world = World()
    incident_id = await world.drafted()
    await world.regenerate(incident_id, SectionKey.SUMMARY)
    await world.regenerate(incident_id, SectionKey.METRICS)
    approved = replace(await world.document(incident_id), status=PostMortemStatus.APPROVED)
    await world.store.save(approved, [])

    await world.run()
    await world.run()

    assert await world.document(incident_id) == approved
    reasons = [reason for _, reason in job_status(world, incident_id)[1:]]
    assert all(r is not None and "aprovado" in r for r in reasons)


async def test_regeneration_of_a_missing_post_mortem_ends_failed() -> None:
    world = World()
    incident_id = await world.resolved(request=False)
    await world.store.request_regeneration(
        incident_id, SectionKey.SUMMARY, instruction=None, author="Ana", at=T0
    )

    await world.run()

    assert job_status(world, incident_id) == [("falho", NO_POST_MORTEM)]


async def test_regeneration_request_is_refused_when_approved_or_without_author() -> None:
    world = World()
    incident_id = await world.drafted()
    with pytest.raises(InvalidPostMortemError, match="informe o seu nome"):
        await request_regeneration(
            store=world.store,
            incident_id=incident_id,
            key=SectionKey.SUMMARY,
            instruction=None,
            author=" ",
            clock=world.clock,
        )
    approved = replace(await world.document(incident_id), status=PostMortemStatus.APPROVED)
    await world.store.save(approved, [])

    with pytest.raises(InvalidPostMortemError, match="aprovado"):
        await world.regenerate(incident_id, SectionKey.SUMMARY)
    with pytest.raises(PostMortemNotFoundError):
        await world.regenerate(uuid4(), SectionKey.SUMMARY)


# ---- Pedidos presos ----


async def test_stuck_draft_is_written_as_interrupted_and_stuck_regeneration_just_ends() -> None:
    world = World(ScriptedLanguageModel())
    regenerated_id = await world.drafted()
    draft_id = await world.resolved()
    await world.regenerate(regenerated_id, SectionKey.SUMMARY)
    before = await world.document(regenerated_id)
    while await world.store.claim(world.clock.now()) is not None:
        pass
    world.clock.advance(timedelta(minutes=10))

    reaped = await reap_stuck_post_mortems(world.store, world.repository, world.clock, world.policy)

    assert reaped == 2
    pm = await world.document(draft_id)
    assert body_of(pm, SectionKey.SUMMARY) == "não gerada: interrompida"
    assert await world.document(regenerated_id) == before
    assert job_status(world, draft_id) == [("falho", "interrompida")]
    assert (
        await reap_stuck_post_mortems(world.store, world.repository, world.clock, world.policy) == 0
    )


# ---- Página ----


async def test_edit_moves_to_review_and_keeps_the_version() -> None:
    world = World()
    incident_id = await world.drafted()

    pm = await edit_section(
        store=world.store,
        incident_id=incident_id,
        key=SectionKey.SUMMARY,
        body="Novo resumo.",
        author="Ana",
        clock=world.clock,
    )

    assert pm == await world.document(incident_id)
    assert pm.status is PostMortemStatus.REVIEW
    (version,) = await world.store.versions(incident_id, SectionKey.SUMMARY)
    assert version.body == "Resumo executivo de Taxa de erro alta."


async def test_edit_of_a_missing_post_mortem_is_not_found() -> None:
    world = World()
    with pytest.raises(PostMortemNotFoundError):
        await edit_section(
            store=world.store,
            incident_id=uuid4(),
            key=SectionKey.SUMMARY,
            body="x",
            author="Ana",
            clock=world.clock,
        )


async def test_plan_items_are_added_updated_and_the_post_mortem_approved() -> None:
    world = World()
    incident_id = await world.drafted()
    due = date(2026, 2, 1)

    async def move(to: PostMortemStatus) -> PostMortem:
        return await move_status(
            store=world.store, incident_id=incident_id, to=to, author="Ana", clock=world.clock
        )

    added = await add_action(
        store=world.store,
        incident_id=incident_id,
        description="Teste de carga no pipeline",
        kind=ActionKind.PREVENT,
        owner="Bruno",
        due=due,
        author="Ana",
        clock=world.clock,
    )
    with pytest.raises(InvalidPostMortemError, match="Criar alarme de conexões no banco"):
        await move(PostMortemStatus.APPROVED)
    for item in added.actions[:2]:
        await update_action(
            store=world.store,
            incident_id=incident_id,
            action_id=item.id,
            description=item.description,
            kind=item.kind,
            owner="Carla",
            due=due,
            status=ActionStatus.OPEN,
            author="Ana",
            clock=world.clock,
        )

    approved = await move(PostMortemStatus.APPROVED)

    assert (approved.status, approved.status_by) == (PostMortemStatus.APPROVED, "Ana")
    assert [a.owner for a in approved.actions] == ["Carla", "Carla", "Bruno"]
    assert await world.document(incident_id) == approved
    assert overdue_items(approved, due + timedelta(days=1)) == approved.actions


async def test_approving_requests_the_learning_again_and_other_moves_do_not() -> None:
    world = World()
    incident_id = await world.resolved(request=False)
    await world.store.save(make_post_mortem(incident_id, status=PostMortemStatus.REVIEW), [])

    async def move(to: PostMortemStatus) -> None:
        await move_status(
            store=world.store,
            incident_id=incident_id,
            to=to,
            author="Ana",
            clock=world.clock,
            repository=world.repository,
        )

    await move(PostMortemStatus.APPROVED)
    assert world.repository.learning_requests == {incident_id: world.clock.now()}

    world.repository.learning_requests.clear()
    world.clock.advance(timedelta(minutes=1))
    await move(PostMortemStatus.REVIEW)
    assert world.repository.learning_requests == {}


async def test_unknown_plan_item_is_refused() -> None:
    world = World()
    incident_id = await world.drafted()

    with pytest.raises(InvalidPostMortemError, match="o item do plano não existe"):
        await update_action(
            store=world.store,
            incident_id=incident_id,
            action_id=uuid4(),
            description="x",
            kind=ActionKind.DETECT,
            owner=None,
            due=None,
            status=ActionStatus.OPEN,
            author="Ana",
            clock=world.clock,
        )


async def test_list_counts_overdue_items_today() -> None:
    world = World()
    incident_id = await world.drafted()
    pm = await world.document(incident_id)
    late = replace(make_action(), owner="Ana", due=world.clock.now().date() - timedelta(days=1))
    await world.store.save(replace(pm, actions=(late,)), [])

    (summary,) = await list_post_mortems(world.store, PostMortemFilters(), world.clock)

    assert (summary.incident.id, summary.overdue) == (incident_id, 1)


async def test_pdf_only_for_approved_or_closed() -> None:
    world = World()
    renderer = FakeDocumentRenderer()
    incident_id = await world.drafted()

    async def pdf(incident: UUID) -> bytes:
        return await render_pdf(
            store=world.store, repository=world.repository, renderer=renderer, incident_id=incident
        )

    with pytest.raises(InvalidPostMortemError, match="aprovado ou encerrado"):
        await pdf(incident_id)
    approved = replace(await world.document(incident_id), status=PostMortemStatus.APPROVED)
    await world.store.save(approved, [])

    assert (await pdf(incident_id)).startswith(b"%PDF-")
    with pytest.raises(PostMortemNotFoundError):
        await pdf(uuid4())
    orphan = make_post_mortem(status=PostMortemStatus.CLOSED)
    await world.store.save(orphan, [])
    with pytest.raises(PostMortemNotFoundError):
        await pdf(orphan.incident_id)
    renderer.error = RenderError("sem fonte")
    with pytest.raises(RenderError):
        await pdf(incident_id)


# ---- Prompt e mensagens ----


def test_prompt_is_blameless_and_lists_the_model_sections() -> None:
    assert "fatores contribuintes" in POST_MORTEM_PROMPT
    assert "nunca em culpados" in POST_MORTEM_PROMPT
    for key in SectionKey:
        assert (f"- {key.value}:" in POST_MORTEM_PROMPT) is (key not in DETERMINISTIC)
    assert "prevenir" in POST_MORTEM_PROMPT
    assert "Não atribua dono nem prazo" in POST_MORTEM_PROMPT
    assert "cite o comando e o lugar" in POST_MORTEM_PROMPT


def test_messages_carry_the_context_and_say_when_something_is_missing() -> None:
    incident = make_incident()
    brief = PostMortemBrief(incident, "Incidente: x", current=None, impact=None, now=T0)

    draft = post_mortem_text(brief)
    regeneration = regeneration_text(brief, SectionKey.SUMMARY, None)

    assert "Incidente: x" in draft
    assert "Impacto estimado: não configurado" in draft
    assert "- nenhum documento" in regeneration
    assert "Seção a reescrever: Resumo executivo (resumo)" in regeneration
    assert "Instrução: nenhuma" in regeneration
    with_document = replace(brief, current=make_post_mortem(incident.id))
    assert "- nenhum item" in regeneration_text(with_document, SectionKey.SUMMARY, None)
