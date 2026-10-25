import pytest
from grolla.bootstrap import WorkerSettings, build_worker
from grolla_worker.app import build_app
from pydantic import SecretStr

pytestmark = pytest.mark.integration


async def test_worker_processes_a_deferred_heartbeat(database_url: str) -> None:
    container = build_worker(
        WorkerSettings(database_url=database_url, teams_webhook_url=SecretStr("http://chat"))
    )
    app = build_app(container)
    try:
        async with app.open_async():
            await app.configure_task("heartbeat").defer_async(timestamp=0)
            await app.run_worker_async(wait=False)
        assert await container.database.latest_heartbeat() is not None
    finally:
        await container.database.dispose()
