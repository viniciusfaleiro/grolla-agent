import asyncio
import signal
from typing import Any, cast

import pytest
from grolla_worker.main import run_workers


class FakeApp:
    """Só o que o run_workers usa: grava cada laço e espera até ser cancelado."""

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.cancelled = 0
        self.both_started = asyncio.Event()

    async def run_worker_async(self, **options: Any) -> None:
        self.started.append(options)
        if len(self.started) == 2:
            self.both_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled += 1
            raise


@pytest.mark.parametrize("received", [signal.SIGTERM, signal.SIGINT])
async def test_runs_one_loop_per_queue_until_a_stop_signal(received: signal.Signals) -> None:
    app = FakeApp()

    async def stop_soon() -> None:
        await app.both_started.wait()
        signal.raise_signal(received)

    stopper = asyncio.create_task(stop_soon())
    await asyncio.wait_for(run_workers(cast(Any, app), max_parallel=3), timeout=5)
    await stopper

    assert app.started == [
        {
            "name": "default",
            "queues": ["default"],
            "concurrency": 1,
            "install_signal_handlers": False,
        },
        {
            "name": "analysis",
            "queues": ["analysis"],
            "concurrency": 3,
            "install_signal_handlers": False,
        },
    ]
    assert app.cancelled == 2
