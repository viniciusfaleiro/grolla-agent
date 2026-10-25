import json
import time
from typing import Any

import grolla_fakes.llm
import pytest
from fastapi.testclient import TestClient
from grolla_fakes.app import create_app

PATH = "/anthropic/v1/messages"
SCHEMA = {
    "type": "object",
    "properties": {
        "service": {"type": "string"},
        "signal": {"type": "string", "enum": ["taxa_de_erro", "latencia_p99", "vazao"]},
        "text": {"type": "string"},
        "only_errors": {"type": "boolean"},
    },
}


def tool(name: str, required: list[str]) -> dict[str, Any]:
    return {"name": name, "description": name, "input_schema": {**SCHEMA, "required": required}}


TOOLS = [
    tool("query_metric", ["service", "signal"]),
    tool("search_logs", ["service"]),
    tool("find_traces", ["service", "only_errors"]),
    tool("recent_deploys", ["service"]),
]
BRIEF = "Incidente: Taxa de erro alta\nServiços: authorizer-api, capture-api\nJanela: 60 min"


def request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {"model": "claude-sonnet-5-5", "max_tokens": 16000, "tools": TOOLS, "messages": messages}


def first_turn(client: TestClient) -> dict[str, Any]:
    response = client.post(PATH, json=request([{"role": "user", "content": BRIEF}]))
    assert response.status_code == 200
    return response.json()


def test_complete_mode_first_asks_for_metric_deploys_and_logs() -> None:
    client = TestClient(create_app())

    body = first_turn(client)

    assert body["type"] == "message"
    assert body["stop_reason"] == "tool_use"
    assert body["model"] == "claude-sonnet-5-5"
    assert body["usage"]["input_tokens"] > 0
    assert [(block["name"], block["input"]) for block in body["content"]] == [
        ("query_metric", {"service": "authorizer-api", "signal": "taxa_de_erro"}),
        ("recent_deploys", {"service": "authorizer-api"}),
        ("search_logs", {"service": "authorizer-api"}),
    ]


def test_complete_mode_answers_the_draft_citing_the_tool_results() -> None:
    client = TestClient(create_app())
    calls = first_turn(client)["content"]
    results = [
        {"type": "tool_result", "tool_use_id": block["id"], "content": f"achado de {block['name']}"}
        for block in calls
    ]
    messages = [
        {"role": "user", "content": [{"type": "text", "text": BRIEF}]},
        {"role": "assistant", "content": calls},
        {"role": "user", "content": results},
    ]

    body = client.post(PATH, json=request(messages)).json()

    assert body["stop_reason"] == "end_turn"
    draft = json.loads(body["content"][0]["text"])
    assert set(draft) == {"summary", "hypotheses", "plan"}
    assert draft["plan"]
    text = json.dumps(draft, ensure_ascii=False)
    for cited in ("achado de recent_deploys", "achado de query_metric", "achado de search_logs"):
        assert cited in text
    assert all(0 <= h["confidence"] <= 1 for h in draft["hypotheses"])


def test_tool_result_with_text_blocks_is_cited_too() -> None:
    client = TestClient(create_app())
    calls = first_turn(client)["content"]
    results = [
        {
            "type": "tool_result",
            "tool_use_id": calls[1]["id"],
            "content": [{"type": "text", "text": "build 1.4.2"}],
        }
    ]
    messages = [
        {"role": "user", "content": BRIEF},
        {"role": "assistant", "content": calls},
        {"role": "user", "content": results},
    ]

    body = client.post(PATH, json=request(messages)).json()

    assert "build 1.4.2" in body["content"][0]["text"]


def test_brief_without_services_line_still_asks_for_tools() -> None:
    client = TestClient(create_app())

    body = client.post(PATH, json=request([{"role": "user", "content": "sem serviço"}])).json()

    assert body["content"][0]["input"]["service"] == grolla_fakes.llm.UNKNOWN_SERVICE


def test_down_mode_answers_overloaded() -> None:
    client = TestClient(create_app())
    assert client.post("/_control/llm", json={"mode": "fora_do_ar"}).status_code == 204

    response = client.post(PATH, json=request([{"role": "user", "content": BRIEF}]))

    assert response.status_code == 529
    assert response.json()["error"]["type"] == "overloaded_error"
    assert response.headers["retry-after-ms"] == "10"


def test_slow_mode_waits_before_answering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grolla_fakes.llm, "SLOW_SECONDS", 0.2)
    client = TestClient(create_app())
    client.post("/_control/llm", json={"mode": "lenta"})
    start = time.monotonic()

    first_turn(client)

    assert time.monotonic() - start >= 0.2


def test_unknown_mode_is_rejected() -> None:
    client = TestClient(create_app())

    assert client.post("/_control/llm", json={"mode": "quebrada"}).status_code == 422


def test_requests_are_recorded_and_reset_goes_back_to_complete() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"mode": "fora_do_ar"})
    client.post(PATH, json=request([{"role": "user", "content": BRIEF}]))

    [recorded] = client.get("/_control/requests").json()
    assert (recorded["path"], recorded["status"]) == (PATH, 529)

    client.post("/_control/reset")

    first_turn(client)
