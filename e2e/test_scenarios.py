"""Runner dos cenários e2e: um teste por arquivo em e2e/scenarios/.

Espera o stack do profile `e2e` do compose de pé (o `make e2e` sobe e derruba). Cada cenário:
reinicia os fakes, carrega o roteiro (`given`), executa as ações (`when`) e confere (`then`).

Ações: `http` (corpo em `json` ou em `form` urlencoded, `cookies`, e `content_type` para
conferir o início do header da resposta), `expect_status`,
`expect_page` (espera o GET responder 200 com todos os trechos `contains` no HTML e nenhum dos
trechos `absent`; com `in_order: true`, os `contains` aparecem nessa ordem; aceita `cookies`),
`wait_for_db_row`, `expect_db_count`, `expect_fake_requests` (contagem exata por trechos do
corpo), `wait_for_fake_request` (espera ao menos uma requisição com os trechos),
`expect_fake_request_order` (a primeira requisição com os trechos `first` chegou antes da com
`then`) e `capture` (guarda a primeira coluna da primeira linha de um SQL com um nome; o `{nome}`
é trocado pelo valor no `path`, no `sql` e nos `contains` dos passos seguintes; um
`INSERT ... RETURNING id` semeia um registro e guarda o id, porque a conexão faz commit ao
fechar) e `for_each` (para cada linha do `sql`, roda os passos de `do` com cada coluna capturada
pelo nome). O modo do LLM fake se troca com `http` em `POST /_control/llm`.

O banco não é limpo entre cenários (só o `make e2e` o recria, uma vez por rodada): cada cenário
usa serviços e fingerprints próprios e filtra as consultas e os cards por eles. A memória do
agente também persiste, e o estudo inicial roda uma vez, na subida do worker.
"""

import html
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
Captured = dict[str, str]


def load(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))


def http(
    service: str, method: str, path: str, options: dict[str, Any] | None = None
) -> httpx2.Response:
    """Requisição a um serviço; `options` traz `json`, `form`, `auth` e `cookies` do passo."""
    options = options or {}
    auth = cast(list[str] | None, options.get("auth"))
    cookies = cast(dict[str, str] | None, options.get("cookies"))
    return httpx2.request(
        method,
        f"{SERVICES[service]}{path}",
        json=options.get("json"),
        data=options.get("form"),
        auth=(auth[0], auth[1]) if auth else None,
        # Direto no header: cookie por requisição está depreciado no cliente.
        headers={"Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())} if cookies else None,
        timeout=10,
    )


def fill(text: str, captured: Captured) -> str:
    """Troca cada `{nome}` capturado pelo valor."""
    for name, value in captured.items():
        text = text.replace(f"{{{name}}}", value)
    return text


def filled(key: str, value: Any, captured: Captured) -> Any:
    """Troca os capturados no `path`, no `sql`, em cada trecho de `contains` e no `form`."""
    if key in ("path", "sql"):
        return fill(cast(str, value), captured)
    if key in ("contains", "absent"):
        return [fill(text, captured) for text in cast(list[str], value)]
    if key == "form":
        return {
            name: fill(str(text), captured) for name, text in cast(dict[str, Any], value).items()
        }
    return value


def run_step(step: Step, captured: Captured) -> None:
    (action, args), *extra = step.items()
    if extra:
        raise ValueError(f"um passo tem uma ação só: {step}")
    args: dict[str, Any] = {key: filled(key, value, captured) for key, value in args.items()}
    match action:
        case "http":
            http_step(args)
        case "expect_status":
            response = http(args["service"], args.get("method", "GET"), args["path"])
            assert response.status_code == args["status"], response.text
        case "expect_page":
            expect_page(
                args["service"],
                args["path"],
                cast(list[str], args.get("contains") or []),
                cast(list[str], args.get("absent") or []),
                args.get("timeout_seconds", 30),
                in_order=bool(args.get("in_order")),
                cookies=cast(dict[str, str] | None, args.get("cookies")),
            )
        case "wait_for_db_row":
            wait_for_db_row(args["sql"], args["at_least"], args["timeout_seconds"])
        case "capture":
            captured[args["name"]] = db_value(args["sql"])
        case "for_each":
            for_each(args["sql"], cast(list[Step], args["do"]), captured)
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
        case "wait_for_fake_request":
            wait_for_fake_request(args["path"], args["contains"], args.get("timeout_seconds", 30))
        case "expect_fake_request_order":
            expect_fake_request_order(
                args["path"],
                cast(list[str], args["first"]),
                cast(list[str], args["then"]),
                args.get("timeout_seconds", 30),
            )
        case _:
            raise ValueError(f"ação desconhecida no cenário: {action}")


def for_each(sql: str, steps: list[Step], captured: Captured) -> None:
    """Roda os passos uma vez por linha do SQL, com as colunas da linha capturadas pelo nome."""
    for row in db_rows(sql):
        for step in steps:
            run_step(step, captured | row)


def http_step(args: dict[str, Any]) -> None:
    """A requisição do passo, conferindo o `status` e o `content_type` quando o passo os traz."""
    response = http(args["service"], args.get("method", "GET"), args["path"], args)
    if "status" in args:
        assert response.status_code == args["status"], response.text
    if "content_type" in args:
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith(args["content_type"]), content_type


def db_count(sql: str) -> int:
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(cast(Any, sql)).fetchone()
    return int(row[0]) if row else 0


def db_value(sql: str) -> str:
    with psycopg.connect(DATABASE_URL) as connection:
        row = connection.execute(cast(Any, sql)).fetchone()
    assert row is not None, f"nenhuma linha em `{sql}`"
    return str(row[0])


def db_rows(sql: str) -> list[dict[str, str]]:
    """Cada linha do SQL com as colunas pelo nome."""
    with psycopg.connect(DATABASE_URL) as connection:
        cursor = connection.execute(cast(Any, sql))
        names = [column.name for column in cursor.description or []]
        return [dict(zip(names, map(str, row), strict=True)) for row in cursor.fetchall()]


def wait_for_db_row(sql: str, at_least: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    count = 0
    while time.monotonic() < deadline:
        count = db_count(sql)
        if count >= at_least:
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"esperava ao menos {at_least} em `{sql}`, achei {count} em {timeout_seconds}s")


def missing_in(page: str, contains: list[str], *, in_order: bool) -> list[str]:
    """Os trechos que faltam; com `in_order`, cada um é procurado depois do anterior."""
    if not in_order:
        return [text for text in contains if text not in page]
    start = 0
    for i, text in enumerate(contains):
        found = page.find(text, start)
        if found < 0:
            return contains[i:]
        start = found + len(text)
    return []


def expect_page(  # noqa: PLR0913 — cada opção do passo vira um argumento
    service: str,
    path: str,
    contains: list[str],
    absent: list[str],
    timeout_seconds: float,
    *,
    in_order: bool = False,
    cookies: dict[str, str] | None = None,
) -> None:
    """Espera o GET da página responder 200 com todos os trechos `contains` (na ordem, com
    `in_order`) e nenhum dos `absent` (o HTML vem desescapado)."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        response = http(service, "GET", path, {"cookies": cookies})
        page = html.unescape(response.text)
        missing = missing_in(page, contains, in_order=in_order)
        present = [text for text in absent if text in page]
        ok = response.status_code == 200 and not missing and not present
        if ok or time.monotonic() >= deadline:
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    assert response.status_code == 200, f"GET {path}: {response.status_code}"
    assert not missing, f"faltou {missing} em GET {path} em {timeout_seconds}s"
    assert not present, f"sobrou {present} em GET {path} em {timeout_seconds}s"


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


def wait_for_fake_request(path: str, contains: list[str], timeout_seconds: float) -> None:
    """Espera ao menos uma requisição ao fake em `path` com todos os trechos."""
    deadline = time.monotonic() + timeout_seconds
    while matching_fake_requests(path, contains) == 0:
        if time.monotonic() >= deadline:
            pytest.fail(f"nenhuma requisição a {path} com {contains} em {timeout_seconds}s")
        time.sleep(POLL_INTERVAL_SECONDS)


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
    http("fakes", "POST", "/_control/scenario", {"json": given.get("fakes") or {}})
    captured: Captured = {}
    for step in steps:
        run_step(step, captured)
