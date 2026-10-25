"""Runner dos cenários e2e: um teste por arquivo em e2e/scenarios/.

Espera o stack do profile `e2e` do compose de pé (o `make e2e` sobe e derruba). Cada cenário:
reinicia os fakes, carrega o roteiro (`given`), executa as ações (`when`) e confere (`then`).

Ações: `http`, `expect_status`, `wait_for_db_row`, `expect_db_count`, `expect_fake_requests`
(contagem exata por trechos do corpo) e `expect_fake_request_order` (a primeira requisição com
os trechos `first` chegou antes da com `then`). O modo do LLM fake se troca com `http` em
`POST /_control/llm`.

O banco não é limpo entre cenários: cada cenário usa serviços e fingerprints próprios e filtra
as consultas e os cards por eles.
"""

import json
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
# Maior que o intervalo de 5 s da entrega da outbox: um card a mais teria tempo de chegar.
SETTLE_SECONDS = 7.0

Step = dict[str, dict[str, Any]]


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def http(
    service: str,
    method: str,
    path: str,
    body: Any = None,
    auth: tuple[str, str] | None = None,
) -> httpx2.Response:
    return httpx2.request(method, f"{SERVICES[service]}{path}", json=body, auth=auth, timeout=10)


def run_step(step: Step) -> None:
    (action, args), *extra = step.items()
    if extra:
        raise ValueError(f"um passo tem uma ação só: {step}")
    match action:
        case "http":
            auth = cast(list[str] | None, args.get("auth"))
            response = http(
                args["service"],
                args.get("method", "GET"),
                args["path"],
                args.get("json"),
                (auth[0], auth[1]) if auth else None,
            )
            if "status" in args:
                assert response.status_code == args["status"], response.text
        case "expect_status":
            response = http(args["service"], args.get("method", "GET"), args["path"])
            assert response.status_code == args["status"], response.text
        case "wait_for_db_row":
            wait_for_db_row(args["sql"], args["at_least"], args["timeout_seconds"])
        case "expect_db_count":
            count = db_count(args["sql"])
            assert count == args["count"], f"esperava {args['count']} em `{args['sql']}`: {count}"
        case "expect_fake_requests":
            expect_fake_requests(
                args["path"],
                args["count"],
                cast(list[str], args.get("contains") or []),
                args.get("timeout_seconds", 30),
            )
        case "expect_fake_request_order":
            expect_fake_request_order(
                args["path"],
                cast(list[str], args["first"]),
                cast(list[str], args["then"]),
                args.get("timeout_seconds", 30),
            )
        case _:
            raise ValueError(f"ação desconhecida no cenário: {action}")


def db_count(sql: str) -> int:
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(cast(Any, sql)).fetchone()
    return int(row[0]) if row else 0


def wait_for_db_row(sql: str, at_least: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    count = 0
    while time.monotonic() < deadline:
        count = db_count(sql)
        if count >= at_least:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"esperava ao menos {at_least} em `{sql}`, achei {count} em {timeout_seconds}s")


def fake_request_bodies(path: str) -> list[str]:
    recorded = cast(list[dict[str, Any]], http("fakes", "GET", "/_control/requests").json())
    return [
        json.dumps(request["body"], ensure_ascii=False)
        for request in recorded
        if request["path"] == path
    ]


def first_match(bodies: list[str], contains: list[str]) -> int | None:
    return next(
        (i for i, body in enumerate(bodies) if all(text in body for text in contains)), None
    )


def matching_fake_requests(path: str, contains: list[str]) -> int:
    return sum(all(text in body for text in contains) for body in fake_request_bodies(path))


def expect_fake_request_order(
    path: str, first: list[str], then: list[str], timeout_seconds: float
) -> None:
    """Confere que a primeira requisição com os trechos `first` chegou antes da com `then`."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        bodies = fake_request_bodies(path)
        before, after = first_match(bodies, first), first_match(bodies, then)
        if (before is not None and after is not None) or time.monotonic() >= deadline:
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    assert before is not None, f"nenhuma requisição a {path} com {first}"
    assert after is not None, f"nenhuma requisição a {path} com {then}"
    assert before < after, f"{first} chegou depois de {then} em {path}"


def expect_fake_requests(
    path: str, count: int, contains: list[str], timeout_seconds: float
) -> None:
    """Confere que o fake recebeu exatamente `count` requisições em `path` com todos os trechos.

    A entrega é assíncrona: espera até o timeout chegar a `count` e depois observa mais um
    intervalo de entrega, para pegar uma requisição a mais (ex.: card duplicado).
    """
    deadline = time.monotonic() + timeout_seconds
    matched = matching_fake_requests(path, contains)
    while matched < count and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        matched = matching_fake_requests(path, contains)
    time.sleep(SETTLE_SECONDS)
    matched = matching_fake_requests(path, contains)
    assert matched == count, f"esperava {count} requisições a {path} com {contains}: {matched}"


@pytest.mark.parametrize("path", sorted(SCENARIOS_DIR.glob("*.yaml")), ids=lambda p: p.stem)
def test_scenario(path: Path) -> None:
    scenario = load(path)
    given = cast(dict[str, Any], scenario.get("given") or {})
    steps = cast(list[Step], (scenario.get("when") or []) + scenario["then"])
    http("fakes", "POST", "/_control/reset")
    http("fakes", "POST", "/_control/scenario", given.get("fakes") or {})
    for step in steps:
        run_step(step)
