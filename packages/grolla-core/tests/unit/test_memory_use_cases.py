"""Casos de uso da memória viva: aprender ao encerrar, estudar o ambiente e a página."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.investigation import (
    STAGE_LEARNING,
    STAGE_STUDY,
    AlertRule,
    BudgetExceededError,
    LanguageModelError,
)
from grolla.application.memory import (
    DOCUMENTS_NOT_INDEXED,
    INTERRUPTED,
    NO_BUDGET,
    STUDY_AUTHOR_SYSTEM,
    STUDY_OFF,
    InvalidMemoryError,
    MemoryNotFoundError,
    MemoryPolicy,
    ServiceStudy,
    StudyMode,
    StudyPolicy,
    StudyStatus,
    add_note,
    confirm_memory,
    domain_rules,
    edit_memory,
    ensure_initial_study,
    forget_memory,
    reap_stuck_learnings,
    reap_stuck_studies,
    request_study,
    restore_memory,
    run_next_learning,
    run_next_study,
)
from grolla.application.memory_retrieval import RagPolicy
from grolla.application.memory_store import memory_for
from grolla.domain.incidents import (
    UNKNOWN_SERVICE,
    EventType,
    Incident,
    IncidentEvent,
    IncidentState,
)
from grolla.domain.memory import (
    AGENT_AUTHOR,
    ENVIRONMENT,
    RELEARN_REASON,
    Confidence,
    DossierDraft,
    LearningDraft,
    Memory,
    MemoryContext,
    MemoryKind,
    Source,
    SourceKind,
    SubjectKind,
    SynthesisDraft,
)
from grolla.domain.post_mortem import PostMortemStatus
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.investigation import (
    FakeChangeHistory,
    FakeCloudInventory,
    FakeTelemetry,
    ScriptedLanguageModel,
    fake_usage,
)
from grolla.testing.memory import (
    FakeTextEmbedder,
    InMemoryLearningQueue,
    InMemoryMemoryStore,
    InMemoryStudyQueue,
)
from grolla.testing.post_mortem import InMemoryPostMortemStore, make_action, make_post_mortem

SERVICE = "pedidos-api"
FILTER = {"team": "pagamentos"}


def record(  # noqa: PLR0913 — cada campo do registro é ajustável no teste
    subject: str = SERVICE,
    kind: MemoryKind = MemoryKind.NOTE,
    *,
    body: str = "fato",
    subject_kind: SubjectKind = SubjectKind.SERVICE,
    confidence: Confidence = Confidence.LEARNED,
    consolidated: bool = False,
) -> Memory:
    return Memory(
        id=uuid4(),
        kind=kind,
        subject=subject,
        subject_kind=subject_kind,
        title=subject,
        body=body,
        confidence=confidence,
        source=Source(SourceKind.PERSON, "Ana"),
        created_at=T0,
        updated_at=T0,
        consolidated=consolidated,
    )


def rule(name: str, service: str, team: str = "pagamentos") -> AlertRule:
    return AlertRule(name, "erro > 5%", "critical", {"team": team}, service, None)


# ---- Aprender ao encerrar


class Learning:
    def __init__(self, model: ScriptedLanguageModel | None = None) -> None:
        self.repository = InMemoryIncidentRepository()
        self.queue = InMemoryLearningQueue(self.repository)
        self.memory = InMemoryMemoryStore()
        self.model = model or ScriptedLanguageModel()
        self.clock = FakeClock(T0 + timedelta(hours=1))
        self.policy = MemoryPolicy(learning_max_tokens=60_000)
        self.post_mortems = InMemoryPostMortemStore(self.repository)

    async def closed(self, state: IncidentState = IncidentState.RESOLVED) -> Incident:
        incident = replace(
            make_incident(services=(SERVICE,), state=state), resolved_at=self.clock.now()
        )
        event = IncidentEvent(
            incident.id,
            self.clock.now(),
            EventType.INCIDENT_RESOLVED,
            {"reason": "rollback"},
            "Ana",
        )
        async with self.repository.transaction() as uow:
            await uow.save(incident)
            await uow.append([event])
            await uow.link_alert(make_alert(service=SERVICE), incident.id)
            await uow.request_learning(incident.id, self.clock.now())
        return incident

    async def run(self) -> bool:
        return await run_next_learning(
            queue=self.queue,
            memory=self.memory,
            repository=self.repository,
            model=self.model,
            clock=self.clock,
            policy=self.policy,
            post_mortems=self.post_mortems,
        )

    def of_kind(self, kind: MemoryKind) -> list[Memory]:
        return [r for r in self.memory.records.values() if r.kind is kind]


async def test_no_learning_request_is_a_no_op() -> None:
    assert not await Learning().run()


async def test_resolved_incident_becomes_an_episode_and_updates_the_dossier() -> None:
    world = Learning()
    old = record(kind=MemoryKind.DOSSIER, body="dossiê antigo", confidence=Confidence.CONFIRMED)
    note = record(body="tráfego zero de madrugada é normal")
    await world.memory.save([(old, None), (note, None)])
    incident = await world.closed()

    assert await world.run()

    (brief,) = world.model.learning_briefs
    assert brief.incident == incident
    assert [e.type for e in brief.events] == [EventType.INCIDENT_RESOLVED]
    assert note in brief.memory.notes
    (episode,) = world.of_kind(MemoryKind.EPISODE)
    assert (episode.subject, episode.title, episode.incident_id, episode.noise) == (
        SERVICE,
        incident.title,
        incident.id,
        False,
    )
    assert episode.source == Source(SourceKind.INCIDENT, AGENT_AUTHOR, str(incident.id))
    dossier = world.memory.records[old.id]
    assert dossier.body == f"Dossiê de {SERVICE}: aprendido com {incident.title}."
    assert dossier.confidence is Confidence.CONFIRMED  # o agente não rebaixa o confirmado
    (version,) = await world.memory.versions(old.id)
    assert (version.author, version.reason, version.body) == (
        AGENT_AUTHOR,
        f"incidente: {incident.title}",
        "dossiê antigo",
    )
    assert world.memory.records[note.id].consolidated
    assert world.queue.rows[incident.id].status == "concluido"
    assert [u.stage for _, u, _ in world.repository.usages] == [STAGE_LEARNING]


async def test_approved_post_mortem_teaches_and_confirms_the_episode() -> None:
    world = Learning()
    old = record(kind=MemoryKind.DOSSIER, body="dossiê antigo")
    await world.memory.save([(old, None)])
    incident = await world.closed()
    await world.run()  # o aprendizado do encerramento, ainda sem post mortem
    (first,) = world.of_kind(MemoryKind.EPISODE)
    action = replace(make_action("Alarme de conexões"), owner="Bruno")
    pm = replace(
        make_post_mortem(incident.id, status=PostMortemStatus.APPROVED, actions=[action]),
        status_by="Ana",
    )
    await world.post_mortems.save(pm, [])
    world.clock.advance(timedelta(hours=1))
    async with world.repository.transaction() as uow:
        await uow.request_learning(incident.id, world.clock.now())

    await world.run()

    brief = world.model.learning_briefs[-1]
    assert brief.post_mortem is not None
    assert "Fatores contribuintes: texto do agente." in brief.post_mortem
    assert "- Alarme de conexões (detectar, Bruno, sem prazo, aberto)" in brief.post_mortem
    (episode,) = world.of_kind(MemoryKind.EPISODE)
    assert episode.id == first.id
    assert episode.confidence is Confidence.CONFIRMED
    assert episode.source == Source(SourceKind.INCIDENT, "Ana", str(incident.id))
    reasons = [v.reason for v in await world.memory.versions(first.id)]
    assert reasons == ["post mortem aprovado"]
    assert (await world.memory.versions(old.id))[0].reason == "post mortem aprovado"


async def test_first_learning_from_an_approved_post_mortem_creates_a_confirmed_episode() -> None:
    world = Learning()
    incident = await world.closed()
    closed = replace(make_post_mortem(incident.id, status=PostMortemStatus.CLOSED), status_by="Bia")
    await world.post_mortems.save(closed, [])

    await world.run()

    (episode,) = world.of_kind(MemoryKind.EPISODE)
    assert (episode.confidence, episode.source.author) == (Confidence.CONFIRMED, "Bia")


async def test_post_mortem_in_review_is_not_a_source() -> None:
    world = Learning()
    incident = await world.closed()
    await world.post_mortems.save(make_post_mortem(incident.id, status=PostMortemStatus.REVIEW), [])

    await world.run()

    assert world.model.learning_briefs[0].post_mortem is None
    (episode,) = world.of_kind(MemoryKind.EPISODE)
    assert episode.confidence is Confidence.LEARNED


async def test_discarded_incident_is_a_noise_episode() -> None:
    world = Learning()
    await world.closed(IncidentState.DISCARDED)

    await world.run()

    (episode,) = world.of_kind(MemoryKind.EPISODE)
    assert episode.noise


async def test_reclosing_updates_the_same_episode_with_a_version() -> None:
    world = Learning()
    incident = await world.closed()
    await world.run()
    (first,) = world.of_kind(MemoryKind.EPISODE)
    world.model.learning = LearningDraft("pedidos-api de novo", "causa nova", True, ())
    world.clock.advance(timedelta(hours=1))
    async with world.repository.transaction() as uow:
        await uow.request_learning(incident.id, world.clock.now())

    await world.run()

    (episode,) = world.of_kind(MemoryKind.EPISODE)
    assert episode.id == first.id
    assert (episode.title, episode.body, episode.noise) == (
        "pedidos-api de novo",
        "causa nova",
        True,
    )
    (version,) = await world.memory.versions(first.id)
    assert version.reason == f"incidente encerrado de novo: {incident.title}"


async def test_new_subjects_get_a_new_dossier_and_duplicates_count_once() -> None:
    draft = LearningDraft(
        "título",
        "corpo",
        False,
        (
            DossierDraft("grolla-lab-db", SubjectKind.INTEGRATION, "primeiro"),
            DossierDraft("grolla-lab-db", SubjectKind.INTEGRATION, "segundo"),
            DossierDraft(ENVIRONMENT, SubjectKind.SERVICE, "ambiente"),
            DossierDraft(" ", SubjectKind.SERVICE, "sem assunto"),
        ),
    )
    world = Learning(ScriptedLanguageModel(learning=draft))
    await world.closed()

    await world.run()

    dossiers = {d.subject: d for d in world.of_kind(MemoryKind.DOSSIER)}
    assert set(dossiers) == {"grolla-lab-db", ENVIRONMENT}
    assert dossiers["grolla-lab-db"].body == "segundo"
    assert dossiers["grolla-lab-db"].confidence is Confidence.LEARNED
    assert dossiers[ENVIRONMENT].subject_kind is SubjectKind.ENVIRONMENT


async def test_failure_is_retried_once_then_kept_as_failed_without_touching_the_incident() -> None:
    world = Learning(ScriptedLanguageModel(learning_error=LanguageModelError("API fora do ar")))
    incident = await world.closed()

    await world.run()
    assert world.queue.rows[incident.id].status == "pendente"
    await world.run()

    (failure,) = await world.queue.failed()
    assert failure.incident_id == incident.id
    assert failure.reason == "LLM indisponível: API fora do ar"
    assert world.memory.records == {}
    assert world.repository.incidents[incident.id] == incident


async def test_budget_and_timeout_are_failures() -> None:
    world = Learning(ScriptedLanguageModel(learning_error=BudgetExceededError("100 de 1")))
    world.policy = MemoryPolicy(learning_timeout=timedelta(seconds=5))
    incident = await world.closed()

    await world.run()

    assert world.queue.rows[incident.id].reason == "orçamento de tokens esgotado: 100 de 1"

    async def slow(*_: object) -> LearningDraft:
        await asyncio.sleep(1)
        raise AssertionError

    world.model.learn = slow  # type: ignore[method-assign]  # o modelo demora além do prazo
    world.policy = MemoryPolicy(learning_timeout=timedelta(milliseconds=10))
    await world.run()

    assert world.queue.rows[incident.id].reason == "tempo esgotado"


async def test_missing_incident_fails_the_request() -> None:
    world = Learning()
    world.repository.learning_requests[uuid4()] = T0

    await world.run()
    await world.run()

    (failure,) = await world.queue.failed()
    assert failure.reason == "incidente não existe"


async def test_reaper_fails_stuck_learnings() -> None:
    world = Learning()
    incident = await world.closed()
    await world.queue.claim(world.clock.now())
    world.clock.advance(timedelta(minutes=10))

    assert await reap_stuck_learnings(world.queue, world.clock, world.policy) == 1

    assert world.queue.rows[incident.id].status == "pendente"
    assert world.queue.rows[incident.id].reason == INTERRUPTED


# ---- Estudar o ambiente


class Study:
    def __init__(
        self,
        *,
        rules: list[AlertRule] | None = None,
        services: list[str] | None = None,
        deploys: dict[str, str] | None = None,
        model: ScriptedLanguageModel | None = None,
        policy: StudyPolicy | None = None,
    ) -> None:
        self.studies = InMemoryStudyQueue()
        self.memory = InMemoryMemoryStore()
        self.model = model or ScriptedLanguageModel()
        self.telemetry = FakeTelemetry(
            rules=rules if rules is not None else [rule("Erro alto", SERVICE)],
            services=services or [],
        )
        self.changes = FakeChangeHistory(deploys or {SERVICE: "1 deploy"})
        self.cloud = FakeCloudInventory()
        self.clock = FakeClock(T0)
        self.policy = MemoryPolicy(study=policy or StudyPolicy(alert_filter=FILTER))
        self.embedder: FakeTextEmbedder | None = None

    async def run(self, mode: StudyMode = StudyMode.INITIAL) -> bool:
        await self.studies.request(mode, "Ana", self.clock.now())
        return await self.run_next()

    async def run_next(self) -> bool:
        return await run_next_study(
            studies=self.studies,
            memory=self.memory,
            model=self.model,
            telemetry=self.telemetry,
            changes=self.changes,
            cloud=self.cloud,
            clock=self.clock,
            policy=self.policy,
            embedder=self.embedder,
        )

    def dossiers(self) -> dict[str, Memory]:
        return {
            r.subject: r
            for r in self.memory.records.values()
            if r.kind is MemoryKind.DOSSIER and r.active
        }


async def test_study_with_rag_indexes_the_documentation_of_each_studied_service() -> None:
    world = Study()
    world.policy = replace(world.policy, rag=RagPolicy())
    world.embedder = FakeTextEmbedder()
    world.changes.files = {SERVICE: {"docs/runbook.md": "## Pool de conexões esgotado\nReinicie."}}

    assert await world.run()

    chunks = [c for c, _ in world.memory.documents[SERVICE].chunks]
    assert [(c.path, c.heading) for c in chunks] == [
        ("docs/runbook.md", "Pool de conexões esgotado")
    ]
    assert {s.service: (s.ok, s.reason) for s in await world.studies.services()}[SERVICE] == (
        True,
        None,
    )


async def test_study_without_embedder_or_rag_does_not_index_documents() -> None:
    without_embedder, without_rag = Study(), Study()
    without_embedder.policy = replace(without_embedder.policy, rag=RagPolicy())
    without_rag.embedder = FakeTextEmbedder()

    for world in (without_embedder, without_rag):
        assert await world.run()
        assert world.memory.documents == {}
        assert world.changes.document_calls == []


async def test_failed_documentation_index_is_the_reason_and_the_service_stays_studied() -> None:
    world = Study()
    world.policy = replace(world.policy, rag=RagPolicy())
    world.embedder = FakeTextEmbedder(failing=True)
    world.changes.files = {SERVICE: {"README.md": "# pedidos\nServiço de pedidos."}}

    assert await world.run()

    study = {s.service: s for s in await world.studies.services()}[SERVICE]
    assert study.ok
    assert study.reason == f"{DOCUMENTS_NOT_INDEXED}: modelo de embeddings fora do ar no fake"
    assert SERVICE in world.dossiers()


def test_domain_rules_are_the_ones_with_every_filter_label() -> None:
    mine, other = rule("a", SERVICE), rule("b", SERVICE, team="outro")

    assert domain_rules([mine, other], FILTER) == [mine]


async def test_no_pending_round_is_a_no_op() -> None:
    assert not await Study().run_next()


async def test_initial_study_writes_the_service_and_environment_dossiers() -> None:
    model = ScriptedLanguageModel(
        study_calls=[("read_file", {"service": SERVICE, "path": "README.md"})],
        synthesis=SynthesisDraft(
            DossierDraft(ENVIRONMENT, SubjectKind.ENVIRONMENT, "mapa", ("https://mapa",)),
            (
                DossierDraft("grolla-lab-db", SubjectKind.SERVICE, "banco"),
                DossierDraft(SERVICE, SubjectKind.INTEGRATION, "não é integração"),
            ),
        ),
    )
    world = Study(
        rules=[rule("Erro alto", SERVICE), rule("Fora do domínio", "outro", team="x")],
        model=model,
    )
    world.changes.files = {SERVICE: {"README.md": "# pedidos"}}

    assert await world.run()

    (brief,) = model.study_briefs
    assert (brief.service, [r.name for r in brief.rules]) == (SERVICE, ["Erro alto"])
    dossiers = world.dossiers()
    assert set(dossiers) == {SERVICE, ENVIRONMENT, "grolla-lab-db"}
    service = dossiers[SERVICE]
    assert service.body == f"Dossiê de {SERVICE}. Alertas: Erro alto."
    assert service.source == Source(
        SourceKind.STUDY, AGENT_AUTHOR, f"https://repositorio.interno/{SERVICE}/README.md"
    )
    assert dossiers["grolla-lab-db"].subject_kind is SubjectKind.INTEGRATION
    assert dossiers[ENVIRONMENT].source.ref == "https://mapa"
    (synthesis,) = model.synthesis_briefs
    assert [d.subject for d in synthesis.services] == [SERVICE]
    assert (
        synthesis.cloud
        == "sem serviço: rede vpc-lab (available)\nLink: https://console.nuvem.interno/"
    )
    run = await world.studies.last()
    assert run is not None
    assert (run.status, run.reason) == (StudyStatus.DONE, None)
    assert run.tokens == 3 * 1_100  # o estudo com uma ferramenta e a síntese
    assert {s.service: s.ok for s in await world.studies.services()} == {
        SERVICE: True,
        ENVIRONMENT: True,
    }
    assert {u.stage for _, u in world.studies.usages} == {STAGE_STUDY}


async def test_services_come_from_the_rules_and_the_active_ones_with_a_repository() -> None:
    world = Study(
        rules=[rule("Erro alto", SERVICE), rule("Sem serviço", UNKNOWN_SERVICE)],
        services=[SERVICE, "captura-api", "sem-repo"],
        deploys={SERVICE: "1 deploy", "captura-api": "2 deploys"},
    )

    await world.run()

    assert [b.service for b in world.model.study_briefs] == [SERVICE, "captura-api"]
    (synthesis,) = world.model.synthesis_briefs
    assert [r.name for r in synthesis.rules] == ["Erro alto", "Sem serviço"]


async def test_unavailable_sources_while_choosing_services_are_skipped() -> None:
    world = Study(services=["captura-api"])
    world.telemetry.failing = {"active_services"}

    await world.run()

    assert [b.service for b in world.model.study_briefs] == [SERVICE]
    world.telemetry.failing = set()
    world.changes.unavailable = True
    await world.run(StudyMode.FULL)

    assert [b.service for b in world.model.study_briefs] == [SERVICE, SERVICE]


async def test_alert_rules_unavailable_fails_the_round() -> None:
    world = Study()
    world.telemetry.failing = {"alert_rules"}

    await world.run()

    run = await world.studies.last()
    assert run is not None
    assert run.status is StudyStatus.FAILED
    assert run.reason is not None
    assert run.reason.startswith("regras de alerta: não foi possível consultar")
    assert world.model.study_briefs == []


async def test_study_off_finishes_the_round_as_failed() -> None:
    world = Study()
    world.policy = MemoryPolicy()

    await world.run()

    run = await world.studies.last()
    assert run is not None
    assert (run.status, run.reason) == (StudyStatus.FAILED, STUDY_OFF)


async def test_budget_runs_out_in_the_middle_and_the_rest_waits_for_the_next_round() -> None:
    services = [f"svc-{n}" for n in range(5)]
    world = Study(
        rules=[rule(f"Regra {s}", s) for s in services],
        model=ScriptedLanguageModel(usage=fake_usage(10_000)),
        policy=StudyPolicy(alert_filter=FILTER, run_max_tokens=30_000, service_max_tokens=20_000),
    )

    await world.run()

    assert [b.service for b in world.model.study_briefs] == services[:3]
    studies = {s.service: s for s in await world.studies.services()}
    assert [studies[s].reason for s in services] == [None, None, None, NO_BUDGET, NO_BUDGET]
    assert len(world.model.synthesis_briefs) == 1

    await world.run(StudyMode.DAILY)

    assert [b.service for b in world.model.study_briefs][3:5] == services[3:]


async def test_failure_in_a_service_does_not_stop_the_others() -> None:
    model = ScriptedLanguageModel(study_errors={"svc-a": LanguageModelError("caiu")})
    world = Study(rules=[rule("a", "svc-a"), rule("b", "svc-b")], model=model)

    await world.run()

    studies = {s.service: s for s in await world.studies.services()}
    assert (studies["svc-a"].ok, studies["svc-a"].reason) == (False, "LLM indisponível: caiu")
    assert studies["svc-b"].ok
    assert set(world.dossiers()) == {"svc-b", ENVIRONMENT}


async def test_synthesis_failure_keeps_the_service_dossiers_and_fails_the_round() -> None:
    world = Study(model=ScriptedLanguageModel(synthesis_error=LanguageModelError("caiu")))
    world.cloud.failing = {"overview"}

    await world.run()

    assert set(world.dossiers()) == {SERVICE}
    (synthesis,) = world.model.synthesis_briefs
    assert synthesis.cloud.startswith("não foi possível consultar")
    run = await world.studies.last()
    assert run is not None
    assert (run.status, run.reason) == (StudyStatus.FAILED, "síntese: LLM indisponível: caiu")


async def test_synthesis_is_skipped_when_nothing_changed_and_the_environment_is_known() -> None:
    world = Study(model=ScriptedLanguageModel(study_errors={SERVICE: LanguageModelError("x")}))
    await world.memory.save([(record(ENVIRONMENT, MemoryKind.DOSSIER), None)])

    await world.run(StudyMode.DAILY)

    assert world.model.synthesis_briefs == []


async def test_study_rewrites_the_dossier_with_a_version_and_consolidates_the_notes() -> None:
    world = Study()
    old = record(kind=MemoryKind.DOSSIER, body="velho", confidence=Confidence.CONFIRMED)
    note = record(body="depende do Redis de sessão", confidence=Confidence.CONFIRMED)
    episode = replace(record(kind=MemoryKind.EPISODE), incident_id=uuid4())
    await world.memory.save([(old, None), (note, None), (episode, None)])

    await world.run(StudyMode.DAILY)

    (brief,) = world.model.study_briefs
    assert brief.memory == MemoryContext((old,), (note,), (episode,))
    assert world.memory.records[old.id].confidence is Confidence.CONFIRMED
    (version,) = await world.memory.versions(old.id)
    assert (version.author, version.reason) == (AGENT_AUTHOR, "estudo diário do ambiente")
    assert world.memory.records[note.id].consolidated


async def test_relearn_forgets_and_restudies_with_the_confirmed_notes() -> None:
    world = Study()
    dossier = record(kind=MemoryKind.DOSSIER, body="repositório antigo")
    learned = record(body="aprendido sozinho")
    taught = record(body="ensinado", confidence=Confidence.CONFIRMED, consolidated=True)
    episode = replace(record(kind=MemoryKind.EPISODE), incident_id=uuid4())
    await world.memory.save([(r, None) for r in (dossier, learned, taught, episode)])

    assert await request_study(
        studies=world.studies,
        memory=world.memory,
        mode=StudyMode.RELEARN,
        author="Ana",
        clock=world.clock,
    )

    assert not world.memory.records[dossier.id].active
    assert not world.memory.records[learned.id].active
    assert world.memory.records[taught.id].active
    assert world.memory.records[episode.id].active
    (forgot,) = await world.memory.versions(learned.id)
    assert (forgot.author, forgot.reason) == ("Ana", RELEARN_REASON)
    await world.run_next()
    (brief,) = world.model.study_briefs
    assert brief.memory.notes == (taught,)
    assert brief.memory.episodes == (episode,)
    rewritten = world.memory.records[dossier.id]
    assert rewritten.active
    assert rewritten.body == f"Dossiê de {SERVICE}. Alertas: Erro alto."


async def test_request_study_needs_an_author() -> None:
    world = Study()

    with pytest.raises(InvalidMemoryError):
        await request_study(
            studies=world.studies,
            memory=world.memory,
            mode=StudyMode.FULL,
            author=" ",
            clock=world.clock,
        )


async def test_initial_study_is_asked_only_with_the_study_on_and_no_dossier() -> None:
    world = Study()

    assert not await ensure_initial_study(world.studies, world.memory, world.clock, MemoryPolicy())
    assert await ensure_initial_study(world.studies, world.memory, world.clock, world.policy)
    (run,) = world.studies.runs
    assert (run.mode, run.author) == (StudyMode.INITIAL, STUDY_AUTHOR_SYSTEM)

    await world.memory.save([(record(kind=MemoryKind.DOSSIER), None)])
    world.studies.runs = []
    assert not await ensure_initial_study(world.studies, world.memory, world.clock, world.policy)


async def test_reaper_fails_stuck_rounds() -> None:
    world = Study()
    await world.studies.request(StudyMode.DAILY, "sistema", T0)
    await world.studies.claim(T0)
    world.clock.advance(timedelta(hours=3))

    assert await reap_stuck_studies(world.studies, world.clock, world.policy) == 1

    run = await world.studies.last()
    assert run is not None
    assert (run.status, run.reason) == (StudyStatus.FAILED, INTERRUPTED)


async def test_service_done_records_each_service_without_waiting_the_round() -> None:
    world = Study()
    await world.studies.request(StudyMode.FULL, "Ana", T0)
    run = await world.studies.claim(T0)
    assert run is not None

    await world.studies.service_done(run, ServiceStudy(SERVICE, T0, True, None), [])

    assert await world.studies.services() == [ServiceStudy(SERVICE, T0, True, None)]


# ---- Lembrar


async def test_memory_for_asks_the_store_with_services_rules_and_text() -> None:
    memory = InMemoryMemoryStore()
    episode = replace(
        record(kind=MemoryKind.EPISODE, body="alerta Erro alto de novo"), incident_id=uuid4()
    )
    await memory.save([(episode, None)])
    incident = make_incident(services=(SERVICE,))
    alerts = [make_alert(service=SERVICE, rule_name="Erro alto")] * 2

    context = await memory_for(memory, incident, alerts)

    assert context.episodes == (episode,)
    assert await memory_for(None, incident, alerts) == MemoryContext()


# ---- Página da memória


class Page:
    def __init__(self) -> None:
        self.memory = InMemoryMemoryStore()
        self.clock = FakeClock(T0 + timedelta(hours=1))

    async def saved(self, memory: Memory) -> UUID:
        await self.memory.save([(memory, None)])
        return memory.id


async def test_add_note_is_confirmed_with_the_person_as_source() -> None:
    page = Page()

    note = await add_note(
        memory=page.memory,
        subject=" captura-api ",
        subject_kind=SubjectKind.SERVICE,
        text=" depende do autorizador e do Redis de sessão ",
        author=" Ana ",
        clock=page.clock,
    )

    assert page.memory.records[note.id] == note
    assert (note.subject, note.kind, note.confidence) == (
        "captura-api",
        MemoryKind.NOTE,
        Confidence.CONFIRMED,
    )
    assert note.source == Source(SourceKind.PERSON, "Ana")
    assert note.body == "depende do autorizador e do Redis de sessão"


@pytest.mark.parametrize(
    ("subject", "text", "author"), [("a", "b", " "), ("a", " ", "Ana"), (" ", "b", "Ana")]
)
async def test_add_note_refuses_empty_fields(subject: str, text: str, author: str) -> None:
    page = Page()

    with pytest.raises(InvalidMemoryError):
        await add_note(
            memory=page.memory,
            subject=subject,
            subject_kind=SubjectKind.SERVICE,
            text=text,
            author=author,
            clock=page.clock,
        )


async def test_edit_confirm_forget_and_restore_keep_the_history() -> None:
    page = Page()
    memory_id = await page.saved(record(kind=MemoryKind.DOSSIER, body="original"))

    edited = await edit_memory(
        memory=page.memory, memory_id=memory_id, body="corrigido", author="Ana", clock=page.clock
    )
    assert (edited.body, edited.confidence) == ("corrigido", Confidence.CONFIRMED)
    page.clock.advance(timedelta(minutes=1))
    gone = await forget_memory(
        memory=page.memory, memory_id=memory_id, author="Bia", clock=page.clock
    )
    assert not gone.active
    versions = await page.memory.versions(memory_id)
    assert [v.reason for v in versions] == ["esquecido na página", "editado na página"]

    back = await restore_memory(
        memory=page.memory,
        memory_id=memory_id,
        version_at=versions[1].at,
        author="Ana",
        clock=page.clock,
    )

    assert (back.body, back.active) == ("original", True)


async def test_confirm_marks_a_learned_record_confirmed() -> None:
    page = Page()
    memory_id = await page.saved(record())

    done = await confirm_memory(
        memory=page.memory, memory_id=memory_id, author="Ana", clock=page.clock
    )

    assert done.confidence is Confidence.CONFIRMED
    assert len(await page.memory.versions(memory_id)) == 1


async def test_page_use_cases_refuse_unknown_records_versions_and_empty_text() -> None:
    page = Page()
    memory_id = await page.saved(record())

    with pytest.raises(MemoryNotFoundError):
        await confirm_memory(memory=page.memory, memory_id=uuid4(), author="Ana", clock=page.clock)
    with pytest.raises(InvalidMemoryError):
        await edit_memory(
            memory=page.memory, memory_id=memory_id, body=" ", author="Ana", clock=page.clock
        )
    with pytest.raises(InvalidMemoryError):
        await restore_memory(
            memory=page.memory, memory_id=memory_id, version_at=T0, author="Ana", clock=page.clock
        )
    with pytest.raises(InvalidMemoryError):
        await forget_memory(memory=page.memory, memory_id=memory_id, author="", clock=page.clock)
