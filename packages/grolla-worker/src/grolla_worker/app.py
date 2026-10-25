import procrastinate
from grolla.application.incidents import deliver_notifications
from grolla.bootstrap import WorkerContainer

from grolla_worker.heartbeat import HEARTBEAT_CRON, beat

# Segundos entre rodadas de entrega da outbox (cron com campo de segundos).
DELIVERY_CRON = "* * * * * */5"


def build_app(container: WorkerContainer) -> procrastinate.App:
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=container.settings.database_url)
    )

    @app.periodic(cron=HEARTBEAT_CRON)
    @app.task(name="heartbeat", queueing_lock="heartbeat")
    async def heartbeat(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await beat(container.clock, container.database.record_heartbeat)

    # Uma rodada na fila (queueing_lock) e uma rodando (lock): a outbox não precisa de SKIP LOCKED.
    @app.periodic(cron=DELIVERY_CRON)
    @app.task(
        name="deliver_notifications",
        queueing_lock="deliver_notifications",
        lock="deliver_notifications",
    )
    async def deliver(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await deliver_notifications(container.outbox, container.notifier, container.clock)

    return app
