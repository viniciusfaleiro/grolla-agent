import asyncio
import logging
import signal

import procrastinate
from grolla.application.memory import ensure_initial_study
from grolla.bootstrap import ConfigError, WorkerContainer, prepare_worker

from grolla_worker.app import ANALYSIS_QUEUE, DEFAULT_QUEUE, build_app


async def run_workers(app: procrastinate.App, max_parallel: int) -> None:
    """Os dois laços do worker até SIGINT/SIGTERM, que encerra os dois com graça.

    Os sinais ficam aqui, não no procrastinate: com dois laços, cada um instalaria o seu e só o
    último receberia o aviso.
    """
    async with asyncio.TaskGroup() as group:
        workers = [
            group.create_task(
                app.run_worker_async(
                    name=queue,
                    queues=[queue],
                    concurrency=concurrency,
                    install_signal_handlers=False,
                )
            )
            for queue, concurrency in ((DEFAULT_QUEUE, 1), (ANALYSIS_QUEUE, max_parallel))
        ]

        def stop() -> None:
            for worker in workers:
                worker.cancel()

        loop = asyncio.get_running_loop()
        for received in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(received, stop)


async def run(app: procrastinate.App, container: WorkerContainer) -> None:
    try:
        async with app.open_async():
            # Base vazia com o estudo ligado: pede o estudo inicial do ambiente.
            await ensure_initial_study(
                container.studies, container.memory, container.clock, container.memory_policy
            )
            await run_workers(app, container.config.investigation.max_parallel)
    finally:
        await container.database.dispose()


def main() -> None:
    try:
        container = prepare_worker()
    except ConfigError as error:
        raise SystemExit(f"grolla-worker: {error}") from error
    logging.basicConfig(level=container.settings.log_level)
    asyncio.run(run(build_app(container), container))
