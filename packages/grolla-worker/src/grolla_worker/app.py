import procrastinate
from grolla.bootstrap import Container

from grolla_worker.heartbeat import HEARTBEAT_CRON, beat


def build_app(container: Container) -> procrastinate.App:
    app = procrastinate.App(
        connector=procrastinate.PsycopgConnector(conninfo=container.settings.database_url)
    )

    @app.periodic(cron=HEARTBEAT_CRON)
    @app.task(name="heartbeat", queueing_lock="heartbeat")
    async def heartbeat(timestamp: int) -> None:  # pyright: ignore[reportUnusedFunction]  # noqa: ARG001
        await beat(container.clock, container.database.record_heartbeat)

    return app
