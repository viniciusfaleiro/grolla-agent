import asyncio
import logging

import procrastinate
from grolla.bootstrap import ConfigError, Container, load_settings, prepare

from grolla_worker.app import build_app


async def run(app: procrastinate.App, container: Container) -> None:
    try:
        async with app.open_async():
            await app.run_worker_async()
    finally:
        await container.database.dispose()


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as error:
        raise SystemExit(f"grolla-worker: {error}") from error
    logging.basicConfig(level=settings.log_level)
    container = prepare(settings)
    asyncio.run(run(build_app(container), container))
