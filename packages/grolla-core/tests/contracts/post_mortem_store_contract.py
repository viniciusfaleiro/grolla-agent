"""Suíte de contrato de PostMortemStore.

O adapter sobrescreve `repository` e `store`, que precisam enxergar o mesmo armazenamento: o
pedido do rascunho nasce do `request_post_mortem` do unit of work, na transação do encerramento.
A suíte roda também contra o banco real compartilhado: cada teste cria incidentes novos (uuid4,
com serviço único), esvazia a fila com `drain` e só confere os próprios pedidos e documentos.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import STAGE_POST_MORTEM
from grolla.application.post_mortem import PostMortemFilters, PostMortemJob, PostMortemStore
from grolla.domain.incidents import Incident, IncidentState, Severity
from grolla.domain.post_mortem import (
    AGENT_AUTHOR,
    ActionKind,
    ActionStatus,
    AgentEvaluation,
    PostMortemStatus,
    Section,
    SectionKey,
    SectionVersion,
)
from grolla.testing.builders import T0, make_incident
from grolla.testing.investigation import fake_usage
from grolla.testing.post_mortem import make_action, make_post_mortem

STARTED = T0 + timedelta(seconds=5)
FINISHED = T0 + timedelta(seconds=30)
TODAY = date(2026, 1, 10)


async def resolved(  # noqa: PLR0913 — cada campo do incidente é ajustável no teste
    repository: IncidentRepository,
    *,
    at: datetime = T0,
    detected_at: datetime = T0,
    severity: Severity = Severity.P1,
    services: tuple[str, ...] | None = None,
    request: bool = True,
) -> Incident:
    """Incidente novo resolvido, com o rascunho do post mortem pedido em `at`."""
    incident = make_incident(
        state=IncidentState.RESOLVED,
        severity=severity,
        services=services,
        detected_at=detected_at,
    )
    async with repository.transaction() as uow:
        await uow.save(incident)
        if request:
            await uow.request_post_mortem(incident.id, at)
    return incident


async def drain(store: PostMortemStore, now: datetime = STARTED) -> list[PostMortemJob]:
    jobs: list[PostMortemJob] = []
    while (job := await store.claim(now)) is not None:
        jobs.append(job)
    return jobs


def mine(jobs: list[PostMortemJob], *incidents: UUID) -> list[PostMortemJob]:
    return [j for j in jobs if j.incident_id in incidents]


def fields(job: PostMortemJob) -> tuple[object, ...]:
    """O pedido sem o id, que é do armazenamento."""
    return (job.incident_id, job.key, job.instruction, job.author, job.started_at)


class PostMortemStoreContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def store(self, repository: IncidentRepository) -> PostMortemStore:
        raise NotImplementedError

    # ---- Documento e versões ----

    async def test_saved_post_mortem_reads_back_with_every_field(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository, request=False)
        action = replace(
            make_action("Revisar o pool de conexões", kind=ActionKind.PREVENT),
            owner="Ana Souza",
            due=date(2026, 2, 1),
            status=ActionStatus.ACCEPTED,
        )
        pm = replace(
            make_post_mortem(incident.id, status=PostMortemStatus.REVIEW, actions=[action]),
            status_by="Ana Souza",
            status_at=T0 + timedelta(minutes=3),
            updated_at=T0 + timedelta(minutes=3),
        )

        await store.save(pm, [])

        assert await store.get(incident.id) == pm
        assert [s.key for s in pm.sections] == list(SectionKey)

    async def test_evaluation_reads_back_and_can_be_partial(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository, request=False)
        full = AgentEvaluation(
            main_hypothesis_correct=False,
            minutes_to_right=12,
            memory_helped=True,
            author="Ana Souza",
            at=T0 + timedelta(minutes=4),
        )
        pm = replace(
            make_post_mortem(incident.id, status=PostMortemStatus.APPROVED), evaluation=full
        )

        await store.save(pm, [])
        assert await store.get(incident.id) == pm

        partial = replace(pm, evaluation=AgentEvaluation(memory_helped=False, author="Bia", at=T0))
        await store.save(partial, [])
        assert await store.get(incident.id) == partial

    async def test_unknown_post_mortem_is_none_and_has_no_versions(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository, request=False)

        assert await store.get(incident.id) is None
        assert await store.versions(incident.id, SectionKey.SUMMARY) == []

    async def test_save_replaces_the_document_and_keeps_versions_newest_first(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository, request=False)
        pm = make_post_mortem(incident.id)
        await store.save(pm, [])
        first = SectionVersion(
            incident.id, SectionKey.SUMMARY, T0 + timedelta(minutes=1), "Ana", "editada", "v1"
        )
        second = replace(first, at=T0 + timedelta(minutes=2), author=AGENT_AUTHOR, body="v2")
        other = replace(first, key=SectionKey.FACTORS, body="fatores")
        body = Section(SectionKey.SUMMARY, "v3 com acentuação", "Ana", T0 + timedelta(minutes=2))
        updated = replace(pm, sections=(body, *pm.sections[1:]), status=PostMortemStatus.REVIEW)

        await store.save(replace(pm, status=PostMortemStatus.REVIEW), [first, other])
        await store.save(updated, [second])

        assert await store.get(incident.id) == updated
        assert await store.versions(incident.id, SectionKey.SUMMARY) == [second, first]
        assert await store.versions(incident.id, SectionKey.FACTORS) == [other]

    # ---- Fila de rascunhos e regenerações ----

    async def test_resolution_request_becomes_one_pending_draft(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository)
        async with repository.transaction() as uow:
            await uow.request_post_mortem(incident.id, T0 + timedelta(minutes=1))

        jobs = mine(await drain(store), incident.id)

        assert [fields(j) for j in jobs] == [(incident.id, None, None, None, STARTED)]

    async def test_draft_is_not_requested_again_after_it_finished(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository)
        (job,) = mine(await drain(store), incident.id)
        await store.finish(job, None, versions=[], usages=[], reason=None, at=FINISHED)

        async with repository.transaction() as uow:
            await uow.request_post_mortem(incident.id, T0 + timedelta(hours=1))

        assert mine(await drain(store), incident.id) == []

    async def test_claim_takes_the_oldest_request_first(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        later = await resolved(repository, at=T0 + timedelta(seconds=3))
        earlier = await resolved(repository, at=T0 + timedelta(seconds=1))
        await store.request_regeneration(
            earlier.id,
            SectionKey.FACTORS,
            instruction="incluir a falta de alarme no banco",
            author="Ana",
            at=T0 + timedelta(seconds=2),
        )

        jobs = mine(await drain(store), earlier.id, later.id)

        assert [fields(j) for j in jobs] == [
            (earlier.id, None, None, None, STARTED),
            (earlier.id, SectionKey.FACTORS, "incluir a falta de alarme no banco", "Ana", STARTED),
            (later.id, None, None, None, STARTED),
        ]

    async def test_regeneration_without_instruction(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository, request=False)
        await store.request_regeneration(
            incident.id, SectionKey.SUMMARY, instruction=None, author="Ana", at=T0
        )

        (job,) = mine(await drain(store), incident.id)

        assert fields(job) == (incident.id, SectionKey.SUMMARY, None, "Ana", STARTED)

    async def test_stuck_lists_running_requests_started_before(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository)
        (job,) = mine(await drain(store), incident.id)

        assert job in await store.stuck(STARTED + timedelta(seconds=1))
        assert job not in await store.stuck(STARTED)

    async def test_finish_saves_the_document_and_versions_and_ends_the_request(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository)
        (job,) = mine(await drain(store), incident.id)
        pm = make_post_mortem(incident.id)
        version = SectionVersion(incident.id, SectionKey.IMPACT, FINISHED, AGENT_AUTHOR, "r", "x")

        await store.finish(
            job,
            pm,
            versions=[version],
            usages=[fake_usage(1_000, STAGE_POST_MORTEM)],
            reason=None,
            at=FINISHED,
        )

        assert await store.get(incident.id) == pm
        assert await store.versions(incident.id, SectionKey.IMPACT) == [version]
        assert job not in await store.stuck(FINISHED + timedelta(hours=1))
        assert mine(await drain(store), incident.id) == []

    async def test_failed_request_is_not_claimed_again(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository)
        (job,) = mine(await drain(store), incident.id)

        await store.finish(job, None, versions=[], usages=[], reason="LLM indisponível", at=T0)

        assert await store.get(incident.id) is None
        assert job not in await store.stuck(FINISHED + timedelta(hours=1))
        assert mine(await drain(store), incident.id) == []

    async def test_finish_only_counts_while_running(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        incident = await resolved(repository)
        (job,) = mine(await drain(store), incident.id)
        pm = make_post_mortem(incident.id)
        await store.finish(job, pm, versions=[], usages=[], reason=None, at=FINISHED)

        late = replace(pm, status=PostMortemStatus.REVIEW)
        await store.finish(job, late, versions=[], usages=[], reason="atrasado", at=FINISHED)

        assert await store.get(incident.id) == pm

    # ---- Lista ----

    async def test_summaries_filter_by_service_newest_first_with_overdue_items(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        service = f"servico-{uuid4().hex[:8]}"
        older = await resolved(repository, request=False, services=(service,))
        newer = await resolved(
            repository,
            request=False,
            services=("outro", service),
            detected_at=T0 + timedelta(days=1),
        )
        late = replace(make_action(), owner="Ana", due=TODAY - timedelta(days=1))
        done = replace(late, status=ActionStatus.DONE)
        today = replace(make_action(), owner="Ana", due=TODAY)
        await store.save(make_post_mortem(older.id, actions=[late, done, today]), [])
        await store.save(make_post_mortem(newer.id, status=PostMortemStatus.REVIEW), [])

        found = await store.summaries(PostMortemFilters(service=service), TODAY)

        assert [(s.incident, s.status, s.overdue) for s in found] == [
            (newer, PostMortemStatus.REVIEW, 0),
            (older, PostMortemStatus.DRAFT, 1),
        ]
        assert found[1].updated_at == T0

    async def test_summaries_filter_by_status_severity_and_period(
        self, repository: IncidentRepository, store: PostMortemStore
    ) -> None:
        service = f"servico-{uuid4().hex[:8]}"
        first = await resolved(repository, request=False, services=(service,))
        second = await resolved(
            repository,
            request=False,
            services=(service,),
            severity=Severity.P3,
            detected_at=T0 + timedelta(days=2),
        )
        await store.save(make_post_mortem(first.id, status=PostMortemStatus.REVIEW), [])
        await store.save(make_post_mortem(second.id), [])

        base = PostMortemFilters(service=service)

        async def ids(filters: PostMortemFilters) -> list[UUID]:
            return [s.incident.id for s in await store.summaries(filters, TODAY)]

        day = T0.date()
        assert await ids(replace(base, status=PostMortemStatus.REVIEW)) == [first.id]
        assert await ids(replace(base, severity=Severity.P3)) == [second.id]
        assert await ids(replace(base, since=day + timedelta(days=1))) == [second.id]
        assert await ids(replace(base, until=day)) == [first.id]
        both = replace(base, since=day, until=day + timedelta(days=2))
        assert await ids(both) == [second.id, first.id]
        assert await ids(replace(base, limit=1)) == [second.id]
        assert await ids(replace(base, status=PostMortemStatus.CLOSED)) == []
