import asyncio
import logging

import procrastinate
from grolla.bootstrap import ConfigError, WorkerContainer, prepare_worker

from grolla_worker.app import build_app


async def run(app: procrastinate.App, container: WorkerContainer) -> None:
    try:
        async with app.open_async():
            await app.run_worker_async()
    finally:
        await container.database.dispose()


def main() -> None:
    try:
        container = prepare_worker()
    except ConfigError as error:
        raise SystemExit(f"grolla-worker: {error}") from error
    logging.basicConfig(level=container.settings.log_level)
    asyncio.run(run(build_app(container), container))
