"""Suíte de contrato de LearningQueue.

O adapter sobrescreve `repository` e `queue`, que precisam enxergar o mesmo armazenamento: o
pedido nasce do `request_learning` do unit of work, na transação do encerramento. A suíte roda
também contra o banco real compartilhado: cada teste cria incidentes novos (uuid4), esvazia a
fila com `drain` e só confere os próprios pedidos.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import STAGE_LEARNING
from grolla.application.memory import LEARNING_ATTEMPTS, LearningJob, LearningQueue
from grolla.testing.builders import T0, make_incident
from grolla.testing.investigation import fake_usage

STARTED = T0 + timedelta(seconds=5)
FINISHED = T0 + timedelta(seconds=30)


async def requested(repository: IncidentRepository, at: datetime = T0) -> UUID:
    """Incidente novo com o aprendizado pedido em `at`."""
    incident = make_incident()
    async with repository.transaction() as uow:
        await uow.save(incident)
        await uow.request_learning(incident.id, at)
    return incident.id


async def drain(queue: LearningQueue, now: datetime = STARTED) -> list[LearningJob]:
    jobs: list[LearningJob] = []
    while (job := await queue.claim(now)) is not None:
        jobs.append(job)
    return jobs


async def claimed(
    repository: IncidentRepository, queue: LearningQueue, incident_id: UUID | None = None
) -> LearningJob:
    incident_id = incident_id or await requested(repository)
    return next(job for job in await drain(queue) if job.incident_id == incident_id)


class LearningQueueContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    @pytest.fixture
    def queue(self, repository: IncidentRepository) -> LearningQueue:
        raise NotImplementedError

    async def test_claim_takes_the_oldest_request_and_counts_the_attempt(
        self, repository: IncidentRepository, queue: LearningQueue
    ) -> None:
        later = await requested(repository, T0 + timedelta(seconds=2))
        earlier = await requested(repository, T0 + timedelta(seconds=1))

        mine = [j for j in await drain(queue) if j.incident_id in {earlier, later}]

        assert mine == [LearningJob(earlier, 1, STARTED), LearningJob(later, 1, STARTED)]

    async def test_stuck_lists_running_requests_started_before(
        self, repository: IncidentRepository, queue: LearningQueue
    ) -> None:
        job = await claimed(repository, queue)

        assert job in await queue.stuck(STARTED + timedelta(seconds=1))
        assert job not in await queue.stuck(STARTED)

    async def test_finished_request_is_not_claimed_again(
        self, repository: IncidentRepository, queue: LearningQueue
    ) -> None:
        job = await claimed(repository, queue)
        usages = [fake_usage(1_000, STAGE_LEARNING)]

        await queue.finish(job, True, None, usages, FINISHED)

        assert all(j.incident_id != job.incident_id for j in await drain(queue))
        assert job not in await queue.stuck(FINISHED + timedelta(hours=1))
        assert all(f.incident_id != job.incident_id for f in await queue.failed())

    async def test_failure_is_retried_once_then_kept_as_failed(
        self, repository: IncidentRepository, queue: LearningQueue
    ) -> None:
        first = await claimed(repository, queue)
        await queue.finish(first, False, "falha do LLM", [], FINISHED)
        assert all(f.incident_id != first.incident_id for f in await queue.failed())

        second = await claimed(repository, queue, first.incident_id)
        assert second.attempts == LEARNING_ATTEMPTS
        await queue.finish(second, False, "tempo esgotado", [], FINISHED + timedelta(seconds=1))

        assert all(j.incident_id != first.incident_id for j in await drain(queue))
        failure = next(f for f in await queue.failed() if f.incident_id == first.incident_id)
        assert (failure.title, failure.reason) == ("Taxa de erro alta", "tempo esgotado")
        assert failure.at == FINISHED + timedelta(seconds=1)

    async def test_finish_only_counts_while_running(
        self, repository: IncidentRepository, queue: LearningQueue
    ) -> None:
        job = await claimed(repository, queue)
        await queue.finish(job, True, None, [], FINISHED)

        await queue.finish(job, False, "atrasado", [], FINISHED)
        await queue.finish(job, False, "atrasado", [], FINISHED)

        assert all(f.incident_id != job.incident_id for f in await queue.failed())
        assert all(j.incident_id != job.incident_id for j in await drain(queue))

    async def test_request_again_after_failure_starts_over(
        self, repository: IncidentRepository, queue: LearningQueue
    ) -> None:
        job = await claimed(repository, queue)
        await queue.finish(job, False, "falhou", [], FINISHED)
        retry = await claimed(repository, queue, job.incident_id)
        await queue.finish(retry, False, "falhou", [], FINISHED)

        async with repository.transaction() as uow:
            await uow.request_learning(job.incident_id, T0 + timedelta(hours=1))

        again = await claimed(repository, queue, job.incident_id)
        assert again.attempts == 1
