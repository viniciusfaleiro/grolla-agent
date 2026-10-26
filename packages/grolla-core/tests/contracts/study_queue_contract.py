"""Suíte de contrato de StudyQueue.

O adapter sobrescreve `studies`. As rodadas são globais (uma pendente ou em andamento por vez),
então cada teste começa com `settle`, que encerra o que outros testes deixaram; os serviços de
cada teste são únicos (uuid4).
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from grolla.application.investigation import STAGE_STUDY
from grolla.application.memory import (
    ServiceStudy,
    StudyMode,
    StudyQueue,
    StudyRun,
    StudyStatus,
)
from grolla.testing.builders import T0
from grolla.testing.investigation import fake_usage

STARTED = T0 + timedelta(seconds=5)
FINISHED = T0 + timedelta(minutes=10)
FAR = T0 + timedelta(days=3650)


async def settle(studies: StudyQueue) -> None:
    """Nenhuma rodada pendente ou em andamento."""
    for run in await studies.stuck(FAR):
        await studies.finish(run, StudyStatus.FAILED, "limpeza do teste", T0)
    while (run := await studies.claim(T0)) is not None:
        await studies.finish(run, StudyStatus.DONE, None, T0)


def unique() -> str:
    return f"svc-{uuid4().hex[:10]}"


class StudyQueueContract:
    @pytest.fixture
    def studies(self) -> StudyQueue:
        raise NotImplementedError

    async def test_request_queues_one_run_at_a_time(self, studies: StudyQueue) -> None:
        await settle(studies)

        assert await studies.request(StudyMode.INITIAL, "sistema", T0)
        assert not await studies.request(StudyMode.DAILY, "sistema", T0 + timedelta(seconds=1))
        assert not await studies.request(StudyMode.FULL, "Ana", T0 + timedelta(seconds=2))

        run = await studies.claim(STARTED)
        assert run is not None
        assert (run.mode, run.author, run.status) == (
            StudyMode.INITIAL,
            "sistema",
            StudyStatus.RUNNING,
        )
        assert (run.requested_at, run.started_at) == (T0, STARTED)
        assert await studies.claim(STARTED) is None
        assert not await studies.request(StudyMode.DAILY, "sistema", STARTED)
        assert await studies.last() == run

    async def test_relearn_replaces_the_pending_run(self, studies: StudyQueue) -> None:
        await settle(studies)
        await studies.request(StudyMode.DAILY, "sistema", T0)

        assert await studies.request(StudyMode.RELEARN, "Ana", T0 + timedelta(seconds=1))

        run = await studies.claim(STARTED)
        assert run is not None
        assert (run.mode, run.author) == (StudyMode.RELEARN, "Ana")
        assert await studies.claim(STARTED) is None

    async def test_relearn_is_queued_even_while_a_run_is_going(self, studies: StudyQueue) -> None:
        await settle(studies)
        await studies.request(StudyMode.DAILY, "sistema", T0)
        running = await studies.claim(STARTED)
        assert running is not None

        assert await studies.request(StudyMode.RELEARN, "Ana", STARTED)

        relearn = await studies.claim(STARTED)
        assert relearn is not None
        assert relearn.mode is StudyMode.RELEARN
        assert relearn.id != running.id

    async def test_stuck_lists_running_runs_started_before(self, studies: StudyQueue) -> None:
        await settle(studies)
        await studies.request(StudyMode.FULL, "Ana", T0)
        run = await studies.claim(STARTED)
        assert run is not None

        assert [r.id for r in await studies.stuck(STARTED + timedelta(seconds=1))] == [run.id]
        assert await studies.stuck(STARTED) == []

    async def test_finish_records_status_reason_and_tokens_of_the_run(
        self, studies: StudyQueue
    ) -> None:
        await settle(studies)
        await studies.request(StudyMode.FULL, "Ana", T0)
        run = await studies.claim(STARTED)
        assert run is not None
        first, second = unique(), unique()
        await studies.service_done(
            run, ServiceStudy(first, STARTED, True, None), [fake_usage(1_000, STAGE_STUDY)]
        )
        await studies.service_done(
            run, ServiceStudy(second, None, False, "sem orçamento nesta rodada"), []
        )

        await studies.finish(run, StudyStatus.DONE, None, FINISHED)
        await studies.finish(run, StudyStatus.FAILED, "atrasado", FINISHED)

        last = await studies.last()
        assert last == StudyRun(
            run.id,
            StudyMode.FULL,
            "Ana",
            StudyStatus.DONE,
            T0,
            STARTED,
            FINISHED,
            tokens=1_100,
            reason=None,
        )
        assert await studies.stuck(FAR) == []

    async def test_services_keep_the_last_study_of_each_service(self, studies: StudyQueue) -> None:
        await settle(studies)
        await studies.request(StudyMode.FULL, "Ana", T0)
        run = await studies.claim(STARTED)
        assert run is not None
        service = unique()
        await studies.service_done(run, ServiceStudy(service, STARTED, True, None), [])
        failed = ServiceStudy(service, FINISHED, False, "tempo esgotado")
        await studies.service_done(run, failed, [])

        mine = [s for s in await studies.services() if s.service == service]

        assert mine == [failed]

    async def test_service_order_puts_new_and_failed_first_then_the_oldest(
        self, studies: StudyQueue
    ) -> None:
        await settle(studies)
        await studies.request(StudyMode.DAILY, "sistema", T0)
        run = await studies.claim(STARTED)
        assert run is not None
        old, recent, new, failed = unique(), unique(), unique(), unique()
        await studies.service_done(
            run, ServiceStudy(recent, T0 + timedelta(days=2), True, None), []
        )
        await studies.service_done(run, ServiceStudy(old, T0 + timedelta(days=1), True, None), [])
        await studies.service_done(
            run, ServiceStudy(failed, None, False, "sem orçamento nesta rodada"), []
        )

        for mode in StudyMode:
            order = await studies.service_order([recent, old, new, failed], mode)
            assert order == [new, failed, old, recent]
        await studies.finish(run, StudyStatus.DONE, None, FINISHED)
