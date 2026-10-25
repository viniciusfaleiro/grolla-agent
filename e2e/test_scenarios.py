"""Runner dos cenários e2e: um teste por arquivo em e2e/scenarios/.

Espera o stack do profile `e2e` do compose de pé (o `make e2e` sobe e derruba). Cada cenário:
reinicia os fakes, carrega o roteiro (`given`), executa as ações (`when`) e confere (`then`).
"""

import time
from pathlib import Path
from typing import Any, cast

import httpx2
import psycopg
import pytest
import yaml

SCENARIOS_DIR = Path(__file__).parent / "scenarios"
SERVICES = {"api": "http://localhost:8000", "fakes": "http://localhost:9000"}
DATABASE_URL = "postgresql://grolla:grolla@localhost:5433/grolla"
POLL_INTERVAL_SECONDS = 1.0

Step = dict[str, dict[str, Any]]


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def http(service: str, method: str, path: str, json: Any = None) -> httpx2.Response:
    return httpx2.request(method, f"{SERVICES[service]}{path}", json=json, timeout=10)


def run_step(step: Step) -> None:
    (action, args), *extra = step.items()
    if extra:
        raise ValueError(f"um passo tem uma ação só: {step}")
    match action:
        case "http":
            http(args["service"], args.get("method", "GET"), args["path"], args.get("json"))
        case "expect_status":
            response = http(args["service"], args.get("method", "GET"), args["path"])
            assert response.status_code == args["status"], response.text
        case "wait_for_db_row":
            wait_for_db_row(args["sql"], args["at_least"], args["timeout_seconds"])
        case _:
            raise ValueError(f"ação desconhecida no cenário: {action}")


def wait_for_db_row(sql: str, at_least: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    count = 0
    while time.monotonic() < deadline:
        with psycopg.connect(DATABASE_URL) as connection:
            row = connection.execute(cast(Any, sql)).fetchone()
        count = int(row[0]) if row else 0
        if count >= at_least:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"esperava ao menos {at_least} em `{sql}`, achei {count} em {timeout_seconds}s")


@pytest.mark.parametrize("path", sorted(SCENARIOS_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_scenario(path: Path) -> None:
    scenario = load(path)
    given = cast(dict[str, Any], scenario.get("given") or {})
    steps = cast(list[Step], (scenario.get("when") or []) + scenario["then"])
    http("fakes", "POST", "/_control/reset")
    http("fakes", "POST", "/_control/scenario", json=given.get("fakes") or {})
    for step in steps:
        run_step(step)
