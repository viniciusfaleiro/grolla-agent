"""ClaudeLanguageModel contra uma Messages API fake (transporte httpx2), sem rede."""

import json
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import grolla.adapters.anthropic
import httpx2
import pytest
from grolla.adapters.anthropic import ClaudeLanguageModel, ModelSettings
from grolla.application.investigation import (
    BudgetExceededError,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModelError,
    LlmUsage,
    TokenBudget,
    Toolbox,
    ToolSpec,
)
from grolla.domain.analysis import AnalysisDraft, Evidence, Hypothesis
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.clock import FakeClock

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SETTINGS = ModelSettings(model="claude-sonnet-5-5", max_tokens=16000, effort="medium")

Handler = Callable[[httpx2.Request], httpx2.Response]


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def final(draft: object, stop_reason: str = "end_turn") -> dict[str, Any]:
    body = fixture("anthropic_final.json")
    body["content"][-1]["text"] = draft if isinstance(draft, str) else json.dumps(draft)
    body["stop_reason"] = stop_reason
    return body


class ScriptedApi:
    """Responde em ordem as respostas roteirizadas e grava os corpos recebidos."""

    def __init__(self, *responses: httpx2.Response) -> None:
        self._responses = list(responses)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        return self._responses.pop(0)


def ok(body: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json=body)


class RecordingToolbox(Toolbox):
    """Toolbox com duas ferramentas e respostas fixas; grava as chamadas."""

    def __init__(self) -> None:
        unused = cast(Any, None)  # as ferramentas são sobrescritas abaixo
        super().__init__(unused, unused, FakeClock(), timedelta(minutes=60))
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def specs(self) -> list[ToolSpec]:
        schema = {"type": "object", "properties": {"service": {"type": "string"}}}
        return [
            ToolSpec("query_metric", "Série de um sinal do serviço", schema),
            ToolSpec("recent_deploys", "Deploys recentes do serviço", schema),
        ]

    async def call(self, name: str, arguments: Mapping[str, Any]) -> str:
        self.calls.append((name, dict(arguments)))
        return f"resultado de {name}"


class RecordingBudget(TokenBudget):
    def __init__(self, fail_at: int | None = None) -> None:
        super().__init__(limit=1_000_000)
        self.spent: list[LlmUsage] = []
        self._fail_at = fail_at

    def spend(self, usage: LlmUsage) -> None:
        self.spent.append(usage)
        if self._fail_at is not None and len(self.spent) >= self._fail_at:
            raise BudgetExceededError("estourou")


@pytest.fixture(autouse=True)
def fixed_brief_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def text(_brief: InvestigationBrief) -> str:
        return "Serviços: authorizer-api"

    monkeypatch.setattr(grolla.adapters.anthropic, "brief_text", text)
    monkeypatch.setattr(grolla.adapters.anthropic, "SYSTEM_PROMPT", "Investigue.")


def brief() -> InvestigationBrief:
    alert = make_alert(service="authorizer-api")
    return InvestigationBrief(
        incident=make_incident(services=("authorizer-api",)), alerts=(alert,), now=T0
    )


def model(handler: Handler, settings: ModelSettings = SETTINGS) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(
        "chave", settings, base_url="http://fake/anthropic", transport=httpx2.MockTransport(handler)
    )


async def investigate(
    handler: Handler,
    toolbox: Toolbox | None = None,
    budget: TokenBudget | None = None,
    settings: ModelSettings = SETTINGS,
) -> AnalysisDraft:
    return await model(handler, settings).investigate(
        brief(), toolbox or RecordingToolbox(), budget or RecordingBudget()
    )


async def test_runs_the_requested_tools_and_returns_the_structured_draft() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(fixture("anthropic_final.json")))
    toolbox = RecordingToolbox()

    draft = await investigate(api, toolbox)

    assert toolbox.calls == [
        ("query_metric", {"service": "authorizer-api", "signal": "taxa_de_erro"}),
        ("recent_deploys", {"service": "authorizer-api"}),
    ]
    assert draft == AnalysisDraft(
        summary="Erros subiram depois do deploy 1.4.2.",
        hypotheses=(
            Hypothesis(
                title="Deploy 1.4.2 com regressão",
                confidence=0.8,
                evidence=(
                    Evidence(
                        text="build 1.4.2 às 10:02",
                        url="https://dev.azure.com/org/Core/_build/results?buildId=7",
                    ),
                ),
            ),
            Hypothesis(title="Banco lento", confidence=0.2, evidence=()),
        ),
        plan=("Avaliar rollback do 1.4.2",),
    )


async def test_sends_tool_results_back_after_the_assistant_turn() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(fixture("anthropic_final.json")))

    await investigate(api)

    first, second = api.bodies
    assert first["messages"] == [{"role": "user", "content": "Serviços: authorizer-api"}]
    user, assistant, results = second["messages"]
    assert user == first["messages"][0]
    assert assistant["role"] == "assistant"
    assert [block["type"] for block in assistant["content"]] == ["thinking", "tool_use", "tool_use"]
    assert results == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01Metric",
                "content": "resultado de query_metric",
            },
            {
                "type": "tool_result",
                "tool_use_id": "toolu_01Deploys",
                "content": "resultado de recent_deploys",
            },
        ],
    }


async def test_request_uses_the_stage_settings_structured_output_and_cache() -> None:
    api = ScriptedApi(ok(fixture("anthropic_final.json")))

    await investigate(api)

    [body] = api.bodies
    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == 16000
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"]["required"] == [
        "summary",
        "hypotheses",
        "plan",
    ]
    assert "tool_choice" not in body
    assert body["system"] == [
        {"type": "text", "text": "Investigue.", "cache_control": {"type": "ephemeral", "ttl": "1h"}}
    ]
    assert [tool["name"] for tool in body["tools"]] == ["query_metric", "recent_deploys"]
    assert "cache_control" not in body["tools"][0]
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


async def test_without_effort_the_api_default_is_kept() -> None:
    api = ScriptedApi(ok(fixture("anthropic_final.json")))

    await investigate(api, settings=ModelSettings(model="claude-opus-5-5", max_tokens=8000))

    assert "effort" not in api.bodies[0]["output_config"]


async def test_every_response_is_spent_with_the_model_that_answered() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(fixture("anthropic_final.json")))
    budget = RecordingBudget()

    await investigate(api, budget=budget)

    assert budget.spent == [
        LlmUsage("investigation", "claude-sonnet-5-5", 2048, 180, 0, 1500),
        LlmUsage("investigation", "claude-sonnet-5-5", 300, 220, 1500, 0),
    ]


async def test_budget_exceeded_stops_the_loop() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(fixture("anthropic_final.json")))
    toolbox = RecordingToolbox()

    with pytest.raises(BudgetExceededError):
        await investigate(api, toolbox, RecordingBudget(fail_at=1))

    assert toolbox.calls == []
    assert len(api.bodies) == 1


@pytest.mark.parametrize("status", [400, 401, 429, 500, 529])
async def test_api_errors_become_language_model_error(status: int) -> None:
    def failing(_request: httpx2.Request) -> httpx2.Response:
        error = {"type": "error", "error": {"type": "api_error", "message": "falhou"}}
        return httpx2.Response(status, json=error, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match=str(status)):
        await investigate(failing)


async def test_network_error_becomes_language_model_error() -> None:
    def unreachable(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("recusada")

    with pytest.raises(LanguageModelError, match="APIConnectionError"):
        await investigate(unreachable)


async def test_refusal_becomes_language_model_error() -> None:
    with pytest.raises(LanguageModelError, match="recusou"):
        await investigate(ScriptedApi(ok(final("", stop_reason="refusal"))))


INVALID_DRAFTS: list[object] = [
    "não é json",
    {"summary": "s", "hypotheses": [], "plan": [], "extra": 1},
    {"summary": "s", "plan": []},
    {
        "summary": "s",
        "hypotheses": [{"title": "t", "confidence": 1.5, "evidence": []}],
        "plan": [],
    },
    {
        "summary": "s",
        "hypotheses": [{"title": "t", "confidence": "alta", "evidence": []}],
        "plan": [],
    },
]


@pytest.mark.parametrize("draft", INVALID_DRAFTS)
async def test_result_outside_the_schema_is_invalid(draft: object) -> None:
    with pytest.raises(InvalidAnalysisError):
        await investigate(ScriptedApi(ok(final(draft))))


async def test_truncated_result_is_invalid() -> None:
    with pytest.raises(InvalidAnalysisError, match="max_tokens"):
        await investigate(ScriptedApi(ok(final('{"summary": "s', stop_reason="max_tokens"))))
