"""ClaudeLanguageModel contra uma Messages API fake (transporte httpx2), sem rede."""

import json
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from contracts.language_model_contract import LanguageModelContract
from grolla.adapters.anthropic import ClaudeLanguageModel, ModelSettings
from grolla.application.investigation import (
    SYSTEM_PROMPT,
    VERIFICATION_PROMPT,
    BudgetExceededError,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModel,
    LanguageModelError,
    LlmUsage,
    TokenBudget,
    Toolbox,
    ToolSpec,
    brief_text,
    verification_text,
)
from grolla.domain.analysis import (
    Action,
    Analysis,
    AnalysisDraft,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    Verdict,
    VerificationDraft,
)
from grolla.testing.builders import T0, make_alert, make_analysis, make_incident
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


class TestClaudeLanguageModelContract(LanguageModelContract):
    @pytest.fixture
    def model(self) -> LanguageModel:
        return claude(
            ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(fixture("anthropic_final.json")))
        )

    @pytest.fixture
    def failing_model(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        return claude(ScriptedApi(response, response, response))

    @pytest.fixture
    def invalid_model(self) -> LanguageModel:
        return claude(ScriptedApi(ok(final("não é json"))))

    @pytest.fixture
    def verifying_model(self) -> LanguageModel:
        tool_use = ok(fixture("anthropic_tool_use.json"))
        return verifier(ScriptedApi(tool_use, ok(final(verification()))))

    @pytest.fixture
    def failing_verifier(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        return verifier(ScriptedApi(response, response, response))

    @pytest.fixture
    def invalid_verifier(self) -> LanguageModel:
        return verifier(ScriptedApi(ok(final({"verdicts": "não é lista"}))))


def brief() -> InvestigationBrief:
    alert = make_alert(service="authorizer-api")
    return InvestigationBrief(
        incident=make_incident(services=("authorizer-api",)), alerts=(alert,), now=T0
    )


def claude(handler: Handler, settings: ModelSettings = SETTINGS) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(
        "chave", settings, base_url="http://fake/anthropic", transport=httpx2.MockTransport(handler)
    )


async def investigate(
    handler: Handler,
    toolbox: Toolbox | None = None,
    budget: TokenBudget | None = None,
    settings: ModelSettings = SETTINGS,
) -> AnalysisDraft:
    return await claude(handler, settings).investigate(
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
            Hypothesis(
                title="Banco lento",
                confidence=0.2,
                evidence=(Evidence(text="p99 do banco estável", url=None),),
            ),
        ),
        plan=("Avaliar rollback do 1.4.2",),
    )


async def test_sends_tool_results_back_after_the_assistant_turn() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(fixture("anthropic_final.json")))

    await investigate(api)

    first, second = api.bodies
    assert first["messages"] == [{"role": "user", "content": brief_text(brief())}]
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
        {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
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
    {"summary": "s", "hypotheses": [{"title": "t", "confidence": 0.5, "evidence": []}], "plan": []},
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


# ---- Validação: a segunda passada, até a causa raiz

VERIFICATION_SETTINGS = ModelSettings(model="claude-opus-5-5", max_tokens=32000, effort="high")
DEPLOY_LINK = "https://dev.azure.com/org/Core/_build/results?buildId=7"
ROOT_CAUSE = "Deploy 1.4.2 introduziu KeyError em POST /authorize às 10:04"
ROLLBACK = {
    "action": "Fazer rollback do authorizer-api para o build 1.4.1",
    "target": "authorizer-api",
    "expected": "taxa de erro volta para 0,4%",
    "link": DEPLOY_LINK,
}
INVESTIGATE_STEP = {
    "action": "Investigar o banco",
    "target": "authorizer-db",
    "expected": "saber se está lento",
    "link": None,
}


def verification(
    *, actions: list[dict[str, Any]] | None = None, root_cause: str | None = ROOT_CAUSE
) -> dict[str, Any]:
    return {
        "verdicts": [
            {
                "hypothesis": "Deploy 1.4.2 com regressão",
                "verdict": "confirmada",
                "reasoning": "Os erros começam 2 min depois do deploy.",
                "evidence": [{"text": "build 1.4.2 às 10:02", "url": DEPLOY_LINK}],
            },
            {
                "hypothesis": "Banco lento",
                "verdict": "rejeitada",
                "reasoning": "p99 do banco estável.",
                "evidence": [{"text": "p99 180 ms", "url": None}],
            },
        ],
        "root_cause": root_cause,
        "actions": [ROLLBACK] if actions is None else actions,
        "missing": "" if root_cause else "Faltou o log da aplicação",
    }


def verifier(handler: Handler) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(
        "chave",
        SETTINGS,
        base_url="http://fake/anthropic",
        transport=httpx2.MockTransport(handler),
        verification=VERIFICATION_SETTINGS,
    )


def analysis() -> Analysis:
    return make_analysis(brief().incident.id)


async def verify(
    handler: Handler, toolbox: Toolbox | None = None, budget: TokenBudget | None = None
) -> VerificationDraft:
    return await verifier(handler).verify(
        brief(), analysis(), toolbox or RecordingToolbox(), budget or RecordingBudget()
    )


async def test_verification_returns_verdicts_root_cause_and_actions() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final(verification())))
    toolbox = RecordingToolbox()

    draft = await verify(api, toolbox)

    assert [name for name, _ in toolbox.calls] == ["query_metric", "recent_deploys"]
    assert draft == VerificationDraft(
        verdicts=(
            HypothesisVerdict(
                "Deploy 1.4.2 com regressão",
                Verdict.CONFIRMED,
                "Os erros começam 2 min depois do deploy.",
                (Evidence("build 1.4.2 às 10:02", DEPLOY_LINK),),
            ),
            HypothesisVerdict(
                "Banco lento",
                Verdict.REJECTED,
                "p99 do banco estável.",
                (Evidence("p99 180 ms", None),),
            ),
        ),
        root_cause=ROOT_CAUSE,
        actions=(
            Action(
                "Fazer rollback do authorizer-api para o build 1.4.1",
                "authorizer-api",
                "taxa de erro volta para 0,4%",
                DEPLOY_LINK,
            ),
        ),
        missing="",
    )


async def test_verification_request_uses_its_prompt_schema_and_stage_settings() -> None:
    api = ScriptedApi(ok(final(verification())))
    budget = RecordingBudget()

    await verify(api, budget=budget)

    [body] = api.bodies
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 32000
    assert body["output_config"]["effort"] == "high"
    assert body["output_config"]["format"]["schema"]["required"] == [
        "verdicts",
        "root_cause",
        "actions",
        "missing",
    ]
    assert body["system"][0]["text"] == VERIFICATION_PROMPT
    assert body["messages"] == [{"role": "user", "content": verification_text(brief(), analysis())}]
    assert [usage.stage for usage in budget.spent] == ["verification"]


async def test_verification_without_its_own_stage_inherits_the_investigation_settings() -> None:
    api = ScriptedApi(ok(final(verification())))

    await claude(api).verify(brief(), analysis(), RecordingToolbox(), RecordingBudget())

    assert api.bodies[0]["model"] == "claude-sonnet-5-5"
    assert api.bodies[0]["output_config"]["effort"] == "medium"


async def test_unconfirmed_cause_has_no_plan_and_says_what_was_missing() -> None:
    api = ScriptedApi(ok(final(verification(actions=[], root_cause=""))))

    draft = await verify(api)

    assert draft.root_cause is None
    assert draft.actions == ()
    assert draft.missing == "Faltou o log da aplicação"


@pytest.mark.parametrize(
    "first",
    [
        verification(actions=[INVESTIGATE_STEP]),  # passo de investigação no plano
        verification(root_cause=None),  # ações sem causa confirmada
    ],
)
async def test_plan_that_is_not_concrete_gets_one_correction(first: dict[str, Any]) -> None:
    api = ScriptedApi(ok(final(first)), ok(final(verification())))
    budget = RecordingBudget()

    draft = await verify(api, budget=budget)

    assert draft.root_cause == ROOT_CAUSE
    first_body, second_body = api.bodies
    assert second_body["messages"][: len(first_body["messages"])] == first_body["messages"]
    assistant, correction = second_body["messages"][-2:]
    assert assistant["role"] == "assistant"
    assert correction == {
        "role": "user",
        "content": "O plano tem passo de investigação: troque por ações concretas, ou deixe "
        "sem plano se a causa não foi confirmada.",
    }
    assert len(budget.spent) == 2


async def test_plan_that_stays_not_concrete_is_invalid() -> None:
    step = final(verification(actions=[INVESTIGATE_STEP]))
    api = ScriptedApi(ok(step), ok(step))

    with pytest.raises(InvalidAnalysisError, match="plano não concreto"):
        await verify(api)

    assert len(api.bodies) == 2


def _with(**changes: Any) -> dict[str, Any]:
    return {**verification(), **changes}


INVALID_VERIFICATIONS: list[object] = [
    "não é json",
    _with(verdicts=[{**verification()["verdicts"][0], "verdict": "provável"}]),
    _with(verdicts=[{**verification()["verdicts"][0], "evidence": []}]),
    _with(extra=1),
    {key: value for key, value in verification().items() if key != "missing"},
]


@pytest.mark.parametrize("draft", INVALID_VERIFICATIONS)
async def test_verification_outside_the_schema_is_invalid(draft: object) -> None:
    with pytest.raises(InvalidAnalysisError, match="formato da validação"):
        await verify(ScriptedApi(ok(final(draft))))


async def test_verification_refusal_becomes_language_model_error() -> None:
    with pytest.raises(LanguageModelError, match="recusou"):
        await verify(ScriptedApi(ok(final("", stop_reason="refusal"))))
