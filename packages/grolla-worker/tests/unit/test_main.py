from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast

import grolla_worker.main
import pytest
from grolla_worker.main import main, run


def test_refuses_to_start_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROLLA_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit, match="GROLLA_DATABASE_URL"):
        main()


class FakeApp:
    @asynccontextmanager
    async def open_async(self) -> AsyncGenerator[None]:
        yield


class FakeDatabase:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


async def test_run_asks_for_the_initial_study_before_the_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def ensure(*ports: Any) -> bool:
        order.append(f"estudo inicial com {len(ports)} ports")
        return True

    async def loops(app: Any, max_parallel: int) -> None:  # noqa: ARG001 — mesmo do run_workers
        order.append(f"laços com {max_parallel}")

    monkeypatch.setattr(grolla_worker.main, "ensure_initial_study", ensure)
    monkeypatch.setattr(grolla_worker.main, "run_workers", loops)
    database = FakeDatabase()
    container = SimpleNamespace(
        studies=object(),
        memory=object(),
        clock=object(),
        memory_policy=object(),
        database=database,
        config=SimpleNamespace(investigation=SimpleNamespace(max_parallel=3)),
    )

    await run(cast(Any, FakeApp()), cast(Any, container))

    assert order == ["estudo inicial com 4 ports", "laços com 3"]
    assert database.disposed
