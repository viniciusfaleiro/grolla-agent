from pathlib import Path

import pytest
from grolla.bootstrap import WorkerSettings, build_worker, load_team_config
from grolla_worker.app import build_app
from pydantic import SecretStr

pytestmark = pytest.mark.integration

E2E_CONFIG = Path(__file__).parents[4] / "e2e" / "config.yaml"


async def test_worker_processes_a_deferred_heartbeat(database_url: str) -> None:
    settings = WorkerSettings(
        database_url=database_url,
        config_file=E2E_CONFIG,
        teams_webhook_url=SecretStr("http://chat"),
        anthropic_api_key=SecretStr("chave"),
        azure_devops_token=SecretStr("token"),
    )
    config = load_team_config(E2E_CONFIG)
    # O modelo de embeddings só existe na imagem do worker: aqui o RAG fica desligado.
    config = config.model_copy(update={"memory": config.memory.model_copy(update={"rag": None})})
    container = build_worker(settings, config)
    app = build_app(container)
    try:
        async with app.open_async():
            await app.configure_task("heartbeat").defer_async(timestamp=0)
            await app.run_worker_async(wait=False)
        assert await container.database.latest_heartbeat() is not None
    finally:
        await container.database.dispose()
