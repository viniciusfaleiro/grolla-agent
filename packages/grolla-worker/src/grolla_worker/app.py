import procrastinate
from grolla.application.analysis import reap_stuck_analyses, run_next_analysis
from grolla.application.incidents import deliver_notifications
from grolla.bootstrap import WorkerContainer

from grolla_worker.heartbeat import HEARTBEAT_CRON, beat

# Duas filas, cada uma com o próprio laço de worker (main.py): uma análise de minutos na fila
# `analysis` nunca atrasa o heartbeat nem a entrega dos cards na `default`.
DEFAULT_QUEUE = "default"
ANALYSIS_QUEUE = "analysis"

# Crons com campo de segundos.
DELIVERY_CRON = "* * * * * */5"
ANALYSES_CRON = "* * * * * */5"
REAPER_CRON = "* * * * *"


def build_app(container: WorkerContainer) -> procrastinate.App:
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=container.settings.database_url)
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
            clock=container.clock,
            policy=container.analysis,
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
        await reap_stuck_analyses(
            container.analysis_queue, container.repository, container.clock, container.analysis
        )

    return app
