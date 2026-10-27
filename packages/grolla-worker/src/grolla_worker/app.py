import procrastinate
from grolla.application.analysis import (
    reap_stuck_analyses,
    reap_stuck_verifications,
    run_next_analysis,
    run_next_verification,
)
from grolla.application.chat import reap_stuck_replies, run_next_reply
from grolla.application.console import (
    reap_stuck_console_replies,
    run_next_console_reply,
)
from grolla.application.follow_up import (
    CycleQueue,
    reap_stuck_cycles,
    run_next_cycle,
    schedule_cycles,
)
from grolla.application.incidents import deliver_notifications
from grolla.application.memory import (
    STUDY_AUTHOR_SYSTEM,
    StudyMode,
    reap_stuck_learnings,
    reap_stuck_studies,
    request_study,
    run_next_learning,
    run_next_study,
)
from grolla.application.memory_retrieval import index_memory
from grolla.application.post_mortem import (
    PostMortemStore,
    reap_stuck_post_mortems,
    run_next_post_mortem,
)
from grolla.application.transcript import (
    reap_stuck_transcripts,
    run_next_transcript,
)
from grolla.application.triage import (
    TriagePolicy,
    TriageQueue,
    reap_stuck_triages,
    run_next_triage,
)
from grolla.bootstrap import WorkerContainer
from grolla.domain.follow_up import FollowUpPolicy

from grolla_worker.heartbeat import HEARTBEAT_CRON, beat

# Três filas, cada uma com o próprio laço de worker (main.py): uma análise de minutos na fila
# `analysis` nunca atrasa o heartbeat nem a entrega dos cards na `default`, e as respostas do
# console, de prioridade menor, ficam na `console`, com concorrência 1, sem atrasar os incidentes.
DEFAULT_QUEUE = "default"
ANALYSIS_QUEUE = "analysis"
CONSOLE_QUEUE = "console"

# Crons com campo de segundos.
DELIVERY_CRON = "* * * * * */5"
ANALYSES_CRON = "* * * * * */5"
VERIFICATIONS_CRON = "* * * * * */5"
REPLIES_CRON = "* * * * * */2"  # quem pergunta no chat está olhando a página
REAPER_CRON = "* * * * *"
LEARNINGS_CRON = "* * * * * */10"
STUDIES_CRON = "* * * * *"
INDEX_CRON = "* * * * * */30"
SCHEDULE_CYCLES_CRON = "* * * * * */30"
CYCLES_CRON = "* * * * * */10"
POST_MORTEMS_CRON = "* * * * * */10"
TRIAGES_CRON = "* * * * * */5"
CONSOLE_REPLIES_CRON = "* * * * * */3"
TRANSCRIPTS_CRON = "* * * * * */5"


def build_app(container: WorkerContainer) -> procrastinate.App:
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=container.settings.database_url)
    )
    reaper_ports = (
        container.analysis_queue,
        container.repository,
        container.clock,
        container.analysis,
    )

    @app.periodic(cron=HEARTBEAT_CRON)
    @app.task(name="heartbeat", queue=DEFAULT_QUEUE, queueing_lock="heartbeat")
    async def heartbeat(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await beat(container.clock, container.database.record_heartbeat)

    # Uma rodada na fila (queueing_lock) e uma rodando (lock): a outbox não precisa de SKIP LOCKED.
    @app.periodic(cron=DELIVERY_CRON)
    @app.task(
        name="deliver_notifications",
        queue=DEFAULT_QUEUE,
        queueing_lock="deliver_notifications",
        lock="deliver_notifications",
    )
    async def deliver(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await deliver_notifications(container.outbox, container.notifier, container.clock)

    # Sem `lock`: até `investigation.max_parallel` rodadas ao mesmo tempo, cada uma reivindicando
    # pedidos diferentes da fila de análises (SKIP LOCKED) até ela esvaziar.
    @app.periodic(cron=ANALYSES_CRON)
    @app.task(name="run_analyses", queue=ANALYSIS_QUEUE, queueing_lock="run_analyses")
    async def analyses(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_analysis(
            queue=container.analysis_queue,
            repository=container.repository,
            model=container.model,
            telemetry=container.telemetry,
            changes=container.changes,
            cloud=container.cloud,
            cluster=container.cluster,
            clock=container.clock,
            policy=container.analysis,
            memory=container.memory,
            embedder=container.embedder,
        ):
            pass

    # A validação de cada análise com hipóteses: mesma fila e mesmas regras das análises.
    @app.periodic(cron=VERIFICATIONS_CRON)
    @app.task(name="run_verifications", queue=ANALYSIS_QUEUE, queueing_lock="run_verifications")
    async def verifications(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_verification(
            queue=container.analysis_queue,
            repository=container.repository,
            model=container.model,
            telemetry=container.telemetry,
            changes=container.changes,
            cloud=container.cloud,
            cluster=container.cluster,
            clock=container.clock,
            policy=container.analysis,
            memory=container.memory,
            embedder=container.embedder,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_analyses",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_analyses",
        lock="reap_stuck_analyses",
    )
    async def reap(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_analyses(*reaper_ports)

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_verifications",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_verifications",
        lock="reap_stuck_verifications",
    )
    async def reap_verifications(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_verifications(*reaper_ports)

    # As respostas do chat do war room: mesma fila e mesmas regras das análises.
    @app.periodic(cron=REPLIES_CRON)
    @app.task(name="run_replies", queue=ANALYSIS_QUEUE, queueing_lock="run_replies")
    async def replies(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_reply(
            queue=container.chat_queue,
            repository=container.repository,
            model=container.model,
            telemetry=container.telemetry,
            changes=container.changes,
            cloud=container.cloud,
            cluster=container.cluster,
            clock=container.clock,
            policy=container.analysis,
            memory=container.memory,
            embedder=container.embedder,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_replies",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_replies",
        lock="reap_stuck_replies",
    )
    async def reap_replies(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_replies(
            container.chat_queue, container.repository, container.clock, container.analysis
        )

    # O aprendizado de cada incidente encerrado: uma chamada ao modelo, sem ferramentas.
    @app.periodic(cron=LEARNINGS_CRON)
    @app.task(name="run_learnings", queue=ANALYSIS_QUEUE, queueing_lock="run_learnings")
    async def learnings(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_learning(
            queue=container.learning_queue,
            memory=container.memory,
            repository=container.repository,
            model=container.model,
            clock=container.clock,
            policy=container.memory_policy,
            post_mortems=container.post_mortems,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_learnings",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_learnings",
        lock="reap_stuck_learnings",
    )
    async def reap_learnings(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_learnings(
            container.learning_queue, container.clock, container.memory_policy
        )

    # Uma rodada de estudo por vez (lock): ela percorre todos os serviços do domínio.
    @app.periodic(cron=STUDIES_CRON)
    @app.task(
        name="run_studies", queue=ANALYSIS_QUEUE, queueing_lock="run_studies", lock="run_studies"
    )
    async def studies(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_study(
            studies=container.studies,
            memory=container.memory,
            embedder=container.embedder,
            model=container.model,
            telemetry=container.telemetry,
            changes=container.changes,
            cloud=container.cloud,
            clock=container.clock,
            policy=container.memory_policy,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_studies",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_studies",
        lock="reap_stuck_studies",
    )
    async def reap_studies(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_studies(container.studies, container.clock, container.memory_policy)

    rag, embedder = container.memory_policy.rag, container.embedder
    if rag is not None and embedder is not None:
        # Indexa em trechos o que mudou na memória e limpa o que saiu; uma rodada por vez.
        @app.periodic(cron=INDEX_CRON)
        @app.task(
            name="index_memory",
            queue=ANALYSIS_QUEUE,
            queueing_lock="index_memory",
            lock="index_memory",
        )
        async def index(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
            while (
                await index_memory(
                    memory=container.memory, embedder=embedder, clock=container.clock, policy=rag
                )
                >= rag.batch
            ):
                pass

    cycles, follow_up = container.cycle_queue, container.follow_up_policy
    if cycles is not None and follow_up is not None:
        _follow_up_jobs(app, container, cycles, follow_up)

    if container.post_mortems is not None:
        _post_mortem_jobs(app, container, container.post_mortems)

    triages, triage = container.triage_queue, container.triage_policy
    if triages is not None and triage is not None:
        _triage_jobs(app, container, triages, triage)

    _transcript_jobs(app, container)
    _console_jobs(app, container)

    study = container.config.memory.study
    if container.memory_policy.study is not None and study is not None:
        # O estudo diário só pede a rodada; quem estuda é o `run_studies`.
        @app.periodic(cron=study.cron)
        @app.task(
            name="schedule_daily_study", queue=DEFAULT_QUEUE, queueing_lock="schedule_daily_study"
        )
        async def daily_study(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
            await request_study(
                studies=container.studies,
                memory=container.memory,
                mode=StudyMode.DAILY,
                author=STUDY_AUTHOR_SYSTEM,
                clock=container.clock,
            )

    return app


def _follow_up_jobs(
    app: procrastinate.App,
    container: WorkerContainer,
    cycles: CycleQueue,
    policy: FollowUpPolicy,
) -> None:
    """O acompanhamento da crise, só com a seção `follow_up` no YAML."""
    limits = container.follow_up_limits

    # A varredura é barata: só decide e enfileira, na fila `default`.
    @app.periodic(cron=SCHEDULE_CYCLES_CRON)
    @app.task(name="schedule_cycles", queue=DEFAULT_QUEUE, queueing_lock="schedule_cycles")
    async def schedule(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await schedule_cycles(cycles, container.repository, container.clock, policy, limits)

    # Uma rodada por vez (lock): os ciclos de todos os incidentes até a fila esvaziar.
    @app.periodic(cron=CYCLES_CRON)
    @app.task(
        name="run_cycles", queue=ANALYSIS_QUEUE, queueing_lock="run_cycles", lock="run_cycles"
    )
    async def run(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_cycle(
            queue=cycles,
            repository=container.repository,
            model=container.model,
            telemetry=container.telemetry,
            changes=container.changes,
            cloud=container.cloud,
            cluster=container.cluster,
            clock=container.clock,
            policy=policy,
            limits=limits,
            impact=container.impact_policy,
            page_url=container.analysis.page_url,
            memory=container.memory,
            embedder=container.embedder,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_cycles",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_cycles",
        lock="reap_stuck_cycles",
    )
    async def reap(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_cycles(cycles, container.repository, container.clock, policy, limits)


def _post_mortem_jobs(
    app: procrastinate.App, container: WorkerContainer, store: PostMortemStore
) -> None:
    """O rascunho de cada incidente resolvido e as regenerações pedidas na página."""

    # Uma rodada por vez (lock): os pedidos da fila até ela esvaziar.
    @app.periodic(cron=POST_MORTEMS_CRON)
    @app.task(
        name="run_post_mortems",
        queue=ANALYSIS_QUEUE,
        queueing_lock="run_post_mortems",
        lock="run_post_mortems",
    )
    async def run(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_post_mortem(
            store=store,
            repository=container.repository,
            model=container.model,
            clock=container.clock,
            policy=container.post_mortem_policy,
            impact=container.impact_policy,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_post_mortems",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_post_mortems",
        lock="reap_stuck_post_mortems",
    )
    async def reap(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_post_mortems(
            store, container.repository, container.clock, container.post_mortem_policy
        )


def _triage_jobs(
    app: procrastinate.App, container: WorkerContainer, queue: TriageQueue, policy: TriagePolicy
) -> None:
    """A triagem de cada incidente novo, só com a seção `noise` no YAML."""

    # Uma rodada por vez (lock): as triagens pedidas até a fila esvaziar.
    @app.periodic(cron=TRIAGES_CRON)
    @app.task(
        name="run_triages", queue=ANALYSIS_QUEUE, queueing_lock="run_triages", lock="run_triages"
    )
    async def run(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_triage(
            queue=queue,
            repository=container.repository,
            model=container.model,
            clock=container.clock,
            policy=policy,
            page_url=container.analysis.page_url,
            memory=container.memory,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_triages",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_triages",
        lock="reap_stuck_triages",
    )
    async def reap(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_triages(queue, container.clock, policy)


def _transcript_jobs(app: procrastinate.App, container: WorkerContainer) -> None:
    """A extração das transcrições de call, só com `transcripts` no container, na fila
    `analysis`: é do war room de um incidente, com a prioridade das análises, e não do console.
    """
    store, policy = container.transcripts, container.transcript_policy
    if store is None:
        return

    # Uma rodada por vez (lock): as transcrições enviadas até a fila esvaziar.
    @app.periodic(cron=TRANSCRIPTS_CRON)
    @app.task(
        name="run_transcripts",
        queue=ANALYSIS_QUEUE,
        queueing_lock="run_transcripts",
        lock="run_transcripts",
    )
    async def run(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_transcript(
            store=store,
            repository=container.repository,
            model=container.model,
            clock=container.clock,
            policy=policy,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_transcripts",
        queue=ANALYSIS_QUEUE,
        queueing_lock="reap_stuck_transcripts",
        lock="reap_stuck_transcripts",
    )
    async def reap(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_transcripts(store, container.clock, policy)


def _console_jobs(app: procrastinate.App, container: WorkerContainer) -> None:
    """As respostas do console de operação, só com a seção `console` no YAML."""
    store, policy = container.console, container.console_policy
    if store is None or policy is None:
        return

    # Uma rodada por vez (lock), na fila de prioridade menor: as perguntas até ela esvaziar.
    @app.periodic(cron=CONSOLE_REPLIES_CRON)
    @app.task(
        name="run_console_replies",
        queue=CONSOLE_QUEUE,
        queueing_lock="run_console_replies",
        lock="run_console_replies",
    )
    async def run(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        while await run_next_console_reply(
            store=store,
            model=container.model,
            telemetry=container.telemetry,
            changes=container.changes,
            cloud=container.cloud,
            cluster=container.cluster,
            clock=container.clock,
            policy=policy,
            memory=container.memory,
            embedder=container.embedder,
        ):
            pass

    @app.periodic(cron=REAPER_CRON)
    @app.task(
        name="reap_stuck_console_replies",
        queue=CONSOLE_QUEUE,
        queueing_lock="reap_stuck_console_replies",
        lock="reap_stuck_console_replies",
    )
    async def reap(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await reap_stuck_console_replies(store, container.clock, policy)
