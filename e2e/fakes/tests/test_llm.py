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
    assert set(draft) == {"summary", "hypotheses", "plan", "seen_before"}
    assert draft["seen_before"] is None  # sem memória na mensagem
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

    body = first_turn(client)

    assert time.monotonic() - start >= 0.2
    assert body["stop_reason"] == "end_turn"
    assert json.loads(body["content"][0]["text"])["hypotheses"] == []


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


# ---- Validação: reconhecida pelo schema com `verdicts`

VERIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"verdicts": {}, "root_cause": {}},
}
VERIFICATION_BRIEF = (
    "Incidente: Taxa de erro alta\nServiços: pedidos-api\n\nResumo da análise inicial: x\n\n"
    "Hipóteses da análise inicial:\n"
    "- Deploy recente introduziu a falha (confiança 70%)\n"
    "  Evidência: build 20261025.2\n"
    "- Dependência externa instável (confiança 30%)\n"
    "  Evidência: timeout no banco\n\n"
    "O que a análise inicial avaliou:\n- query_metric: taxa → subiu"
)


def verification_request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **request(messages),
        "output_config": {"format": {"type": "json_schema", "schema": VERIFICATION_SCHEMA}},
    }


def verification_turns(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A primeira resposta (ferramentas) e a final da validação."""
    brief = {"role": "user", "content": VERIFICATION_BRIEF}
    calls = client.post(PATH, json=verification_request([brief])).json()
    results = [
        {"type": "tool_result", "tool_use_id": block["id"], "content": f"achado de {block['name']}"}
        for block in calls["content"]
    ]
    messages = [
        brief,
        {"role": "assistant", "content": calls["content"]},
        {"role": "user", "content": results},
    ]
    return calls, client.post(PATH, json=verification_request(messages)).json()


def test_complete_verification_confirms_the_first_hypothesis_and_plans_a_rollback() -> None:
    calls, final = verification_turns(TestClient(create_app()))

    assert calls["stop_reason"] == "tool_use"
    assert [(b["name"], b["input"]) for b in calls["content"]] == [
        ("query_metric", {"service": "pedidos-api", "signal": "taxa_de_erro"}),
        ("recent_deploys", {"service": "pedidos-api"}),
    ]
    assert final["stop_reason"] == "end_turn"
    result = json.loads(final["content"][0]["text"])
    assert [(v["hypothesis"], v["verdict"]) for v in result["verdicts"]] == [
        ("Deploy recente introduziu a falha", "confirmada"),
        ("Dependência externa instável", "rejeitada"),
    ]
    assert "achado de recent_deploys" in json.dumps(result, ensure_ascii=False)
    assert "pedidos-api" in result["root_cause"]
    assert result["actions"] == [
        {
            "action": "Fazer rollback do pedidos-api para o deploy anterior",
            "target": "pedidos-api",
            "expected": "A taxa de erro volta ao patamar anterior ao deploy",
            "link": None,
        }
    ]
    assert result["missing"] == ""


def test_inconclusive_verification_has_no_plan_and_says_what_was_missing() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"verification_mode": "inconclusiva"})

    _, final = verification_turns(client)

    result = json.loads(final["content"][0]["text"])
    assert {v["verdict"] for v in result["verdicts"]} == {"inconclusiva"}
    assert len(result["verdicts"]) == 2
    assert result["root_cause"] is None
    assert result["actions"] == []
    assert result["missing"]


def test_verification_mode_does_not_change_the_initial_analysis() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"verification_mode": "fora_do_ar"})

    first_turn(client)  # análise inicial segue completa

    brief = {"role": "user", "content": VERIFICATION_BRIEF}
    response = client.post(PATH, json=verification_request([brief]))
    assert response.status_code == 529


def test_each_field_of_the_control_leaves_the_other_phase_alone() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"mode": "fora_do_ar"})
    client.post("/_control/llm", json={"verification_mode": "inconclusiva"})

    response = client.post(PATH, json=request([{"role": "user", "content": BRIEF}]))
    _, final = verification_turns(client)

    assert response.status_code == 529
    assert json.loads(final["content"][0]["text"])["root_cause"] is None


def test_initial_mode_does_not_change_the_verification() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"mode": "fora_do_ar"})

    calls, _ = verification_turns(client)

    assert calls["stop_reason"] == "tool_use"


def test_slow_verification_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grolla_fakes.llm, "SLOW_SECONDS", 0.2)
    client = TestClient(create_app())
    client.post("/_control/llm", json={"verification_mode": "lenta"})
    start = time.monotonic()

    verification_turns(client)

    assert time.monotonic() - start >= 0.4


def test_verification_without_hypotheses_section_still_answers() -> None:
    client = TestClient(create_app())
    brief = {"role": "user", "content": "Serviços: pedidos-api"}
    calls = client.post(PATH, json=verification_request([brief])).json()
    messages = [
        brief,
        {"role": "assistant", "content": calls["content"]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_0", "content": "x"}],
        },
    ]

    final = client.post(PATH, json=verification_request(messages)).json()

    result = json.loads(final["content"][0]["text"])
    assert [v["hypothesis"] for v in result["verdicts"]] == [grolla_fakes.llm.FALLBACK_HYPOTHESIS]


def test_reset_brings_the_verification_back_to_complete() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"verification_mode": "inconclusiva"})
    client.post("/_control/reset")

    _, final = verification_turns(client)

    assert json.loads(final["content"][0]["text"])["root_cause"]


# ---- Chat do war room: reconhecido pelo schema com `reply`

CHAT_SCHEMA: dict[str, Any] = {"type": "object", "properties": {"reply": {"type": "string"}}}
CHAT_MESSAGE = (
    "Incidente: Pedidos falhando\nServiços: pedidos-db, pedidos-api\n\n"
    "Chat:\n- Ana: Pergunta: antiga\n\n"
    "Pergunta: olha os logs do pedidos-db desde 21:40"
)


def chat_request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **request(messages),
        "output_config": {"format": {"type": "json_schema", "schema": CHAT_SCHEMA}},
    }


def chat_turns(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    """A primeira resposta (ferramentas) e a final do chat."""
    question = {"role": "user", "content": CHAT_MESSAGE}
    calls = client.post(PATH, json=chat_request([question])).json()
    results = [
        {"type": "tool_result", "tool_use_id": block["id"], "content": "timeout no pool às 21:42"}
        for block in calls["content"]
    ]
    messages = [
        question,
        {"role": "assistant", "content": calls["content"]},
        {"role": "user", "content": results},
    ]
    return calls, client.post(PATH, json=chat_request(messages)).json()


def test_chat_first_searches_the_logs_of_the_service() -> None:
    calls, _ = chat_turns(TestClient(create_app()))

    assert calls["stop_reason"] == "tool_use"
    assert [(block["name"], block["input"]) for block in calls["content"]] == [
        ("search_logs", {"service": "pedidos-db"})
    ]


def test_chat_answers_citing_the_logs_and_the_question() -> None:
    _, final = chat_turns(TestClient(create_app()))

    assert final["stop_reason"] == "end_turn"
    answer = json.loads(final["content"][0]["text"])
    assert set(answer) == {"reply"}
    assert answer["reply"].startswith(
        'Consultei os logs de pedidos-db: timeout no pool às 21:42 Sobre "olha os logs do '
        'pedidos-db desde 21:40":'
    )


def test_chat_down_mode_answers_overloaded_and_leaves_the_analysis_alone() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"chat_mode": "fora_do_ar"})

    chat = client.post(PATH, json=chat_request([{"role": "user", "content": CHAT_MESSAGE}]))

    assert chat.status_code == 529
    assert first_turn(client)["stop_reason"] == "tool_use"
    _, final = verification_turns(client)
    assert final["stop_reason"] == "end_turn"


def test_other_modes_do_not_change_the_chat() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"mode": "fora_do_ar", "verification_mode": "fora_do_ar"})

    _, final = chat_turns(client)

    assert final["stop_reason"] == "end_turn"


def test_slow_chat_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grolla_fakes.llm, "SLOW_SECONDS", 0.2)
    client = TestClient(create_app())
    client.post("/_control/llm", json={"chat_mode": "lenta"})
    start = time.monotonic()

    response = client.post(PATH, json=chat_request([{"role": "user", "content": CHAT_MESSAGE}]))

    assert time.monotonic() - start >= 0.2
    assert response.status_code == 200


def test_reset_brings_the_chat_back_to_complete() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"chat_mode": "fora_do_ar"})
    client.post("/_control/reset")

    _, final = chat_turns(client)

    assert final["stop_reason"] == "end_turn"


# ---- Memória: "já vi isso", estudo, síntese e aprendizado

PAST = "0193a3a0-7c1e-7000-8000-000000000042"
MEMORY = (
    "<memoria>\nMemória do agente.\n\n## Episódios\n"
    f"### [episodio] estudo-api: estudo-api com 5xx (aprendido, 20/10/2026, incidente {PAST})\n"
    "Pool esgotado depois do deploy.\n</memoria>"
)


def answer_after_tools(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    """Primeira volta (ferramentas) e a resposta final, com os resultados roteirizados."""
    calls = client.post(PATH, json=payload).json()["content"]
    results = [
        {"type": "tool_result", "tool_use_id": b["id"], "content": RESULTS.get(b["name"], "ok")}
        for b in calls
    ]
    messages = [
        *payload["messages"],
        {"role": "assistant", "content": calls},
        {"role": "user", "content": results},
    ]
    return client.post(PATH, json={**payload, "messages": messages}).json()


RESULTS = {
    "read_file": "/README.md de estudo-api:\n# estudo-api",
    "service_dependencies": "1 dependência(s) vistas nos traces de estudo-api entre 10:00 UTC "
    "e 11:00 UTC: banco postgresql estudo (estudo-db): 4200 chamadas, 0,3% com erro, p99 35 ms",
}


def test_analysis_with_an_episode_in_the_memory_says_seen_before() -> None:
    client = TestClient(create_app())
    brief = f"{BRIEF}\n\n{MEMORY}"

    final = answer_after_tools(client, request([{"role": "user", "content": brief}]))

    draft = json.loads(final["content"][0]["text"])
    assert draft["seen_before"]["incident_id"] == PAST
    assert "estudo-api com 5xx" in draft["seen_before"]["reason"]


def memory_request(prop: str, content: str, tools: list[dict[str, Any]]) -> dict[str, Any]:
    schema = {"type": "object", "properties": {prop: {"type": "string"}}}
    return {
        **request([{"role": "user", "content": content}]),
        "tools": tools,
        "output_config": {"format": {"type": "json_schema", "schema": schema}},
    }


STUDY_TOOLS = [
    {
        "name": "read_file",
        "description": "lê",
        "input_schema": {
            "type": "object",
            "properties": {"service": {"type": "string"}, "path": {"type": "string"}},
            "required": ["service", "path"],
        },
    },
    tool("service_dependencies", ["service"]),
]


def test_study_reads_the_readme_and_the_dependencies_and_cites_the_rules() -> None:
    client = TestClient(create_app())
    brief = "Serviço: estudo-api\n\nRegras de alerta:\n- Taxa de erro alta no estudo-api"
    payload = memory_request("dossier", brief, STUDY_TOOLS)

    first = client.post(PATH, json=payload).json()
    final = answer_after_tools(client, payload)

    assert [(b["name"], b["input"]) for b in first["content"]] == [
        ("read_file", {"service": "estudo-api", "path": "README.md"}),
        ("service_dependencies", {"service": "estudo-api"}),
    ]
    dossier = json.loads(final["content"][0]["text"])["dossier"]
    assert "Taxa de erro alta no estudo-api" in dossier
    assert "Integração: estudo-db chamada por estudo-api" in dossier


def test_synthesis_writes_the_environment_and_one_dossier_per_integration() -> None:
    client = TestClient(create_app())
    services = "### estudo-api\nIntegração: estudo-db chamada por estudo-api\n"
    payload = memory_request("environment", services, [])

    body = client.post(PATH, json=payload).json()

    result = json.loads(body["content"][0]["text"])
    assert "estudo-api" in result["environment"]
    assert result["integrations"] == [
        {"subject": "estudo-db", "body": "# estudo-db\n\nDependência chamada por estudo-api."}
    ]


@pytest.mark.parametrize(
    ("closing", "noise"), [("incident_resolved", False), ("incident_discarded", True)]
)
def test_learning_writes_the_episode_of_the_service(closing: str, noise: bool) -> None:
    client = TestClient(create_app())
    brief = f"Incidente: estudo-api com 5xx\nServiços: estudo-api\nEventos:\n- {closing}"

    body = client.post(PATH, json=memory_request("episode_title", brief, [])).json()

    learned = json.loads(body["content"][0]["text"])
    assert learned["episode_title"] == "estudo-api: estudo-api com 5xx"
    assert learned["noise"] is noise
    assert learned["dossiers"] == []


def test_memory_phases_ignore_the_analysis_mode() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"mode": "fora_do_ar"})

    response = client.post(PATH, json=memory_request("environment", "nada", []))

    assert response.status_code == 200


# ---- Acompanhamento da crise

FOLLOW_UP_SCHEMA = {
    "type": "object",
    "properties": {
        "trend": {"type": "string", "enum": ["piorando", "estavel", "melhorando", "normalizado"]},
        "error_rate": {"type": ["number", "null"]},
        "summary": {"type": "string"},
        "changed": {"type": "boolean"},
        "evidence": {"type": "array"},
    },
}
FOLLOW_UP_MESSAGE = "Incidente: Taxa de erro alta\nServiços: pedidos-api\nCiclo anterior: nenhum"


def follow_up_request(messages: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        **request(messages),
        "output_config": {"format": {"type": "json_schema", "schema": FOLLOW_UP_SCHEMA}},
    }


def follow_up_turns(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    first = {"role": "user", "content": FOLLOW_UP_MESSAGE}
    calls = client.post(PATH, json=follow_up_request([first])).json()
    results = [
        {"type": "tool_result", "tool_use_id": block["id"], "content": "taxa de erro 0,3%"}
        for block in calls["content"]
    ]
    messages = [
        first,
        {"role": "assistant", "content": calls["content"]},
        {"role": "user", "content": results},
    ]
    return calls, client.post(PATH, json=follow_up_request(messages)).json()


def cycle_of(final: dict[str, Any]) -> dict[str, Any]:
    assert final["stop_reason"] == "end_turn"
    return json.loads(final["content"][0]["text"])


def test_follow_up_queries_the_metric_and_answers_normalized_by_default() -> None:
    calls, final = follow_up_turns(TestClient(create_app()))

    assert [(block["name"], block["input"]) for block in calls["content"]] == [
        ("query_metric", {"service": "pedidos-api", "signal": "taxa_de_erro"})
    ]
    assert cycle_of(final) == {
        "trend": "normalizado",
        "error_rate": 0.0,
        "summary": "Taxa de erro em 0% no pedidos-api.",
        "changed": False,
        "evidence": [{"text": "taxa de erro 0,3%", "url": None}],
    }


def test_follow_up_answers_the_script_from_the_control() -> None:
    client = TestClient(create_app())
    script = {"trend": "piorando", "error_rate": 12.5, "changed": True}
    client.post("/_control/llm", json={"follow_up": script})

    cycle = cycle_of(follow_up_turns(client)[1])

    assert cycle["trend"] == "piorando"
    assert cycle["error_rate"] == 12.5
    assert cycle["changed"] is True
    assert cycle["summary"] == "Taxa de erro em 12,5% no pedidos-api."


def test_follow_up_script_without_measure_and_with_summary() -> None:
    client = TestClient(create_app())
    script = {"trend": "estavel", "error_rate": None, "summary": "Sem dado de erro."}
    client.post("/_control/llm", json={"follow_up": script})

    cycle = cycle_of(follow_up_turns(client)[1])

    assert (cycle["error_rate"], cycle["summary"]) == (None, "Sem dado de erro.")


def test_follow_up_script_with_unknown_trend_is_rejected() -> None:
    client = TestClient(create_app())

    response = client.post("/_control/llm", json={"follow_up": {"trend": "caindo"}})

    assert response.status_code == 422


def test_follow_up_down_mode_answers_overloaded_and_leaves_the_others_alone() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"follow_up_mode": "fora_do_ar"})

    response = client.post(PATH, json=follow_up_request([{"role": "user", "content": "x"}]))

    assert response.status_code == 529
    assert first_turn(client)["stop_reason"] == "tool_use"
    assert chat_turns(client)[1]["stop_reason"] == "end_turn"


def test_other_modes_do_not_change_the_follow_up() -> None:
    client = TestClient(create_app())
    client.post("/_control/llm", json={"mode": "fora_do_ar", "chat_mode": "fora_do_ar"})

    assert cycle_of(follow_up_turns(client)[1])["trend"] == "normalizado"


def test_slow_follow_up_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grolla_fakes.llm, "SLOW_SECONDS", 0.2)
    client = TestClient(create_app())
    client.post("/_control/llm", json={"follow_up_mode": "lenta"})
    start = time.monotonic()

    response = client.post(PATH, json=follow_up_request([{"role": "user", "content": "x"}]))

    assert time.monotonic() - start >= 0.2
    assert response.status_code == 200


def test_reset_brings_the_follow_up_back_to_the_default() -> None:
    client = TestClient(create_app())
    control = {"follow_up_mode": "fora_do_ar", "follow_up": {"trend": "piorando"}}
    client.post("/_control/llm", json=control)
    client.post("/_control/reset")

    assert cycle_of(follow_up_turns(client)[1])["trend"] == "normalizado"
