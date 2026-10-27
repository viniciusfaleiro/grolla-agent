"""ClaudeLanguageModel contra uma Messages API fake (transporte httpx2), sem rede."""

import json
from collections.abc import Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx2
import pytest
from contracts.language_model_contract import (
    ChatReplyContract,
    LanguageModelContract,
    MemoryModelContract,
)
from grolla.adapters.anthropic import WRAP_UP, ClaudeLanguageModel, ModelSettings
from grolla.application.investigation import (
    CHAT_PROMPT,
    FOLLOW_UP_PROMPT,
    LEARNING_PROMPT,
    STUDY_PROMPT,
    SYNTHESIS_PROMPT,
    SYSTEM_PROMPT,
    VERIFICATION_PROMPT,
    AlertRule,
    BudgetExceededError,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModel,
    LanguageModelError,
    LearningBrief,
    LlmUsage,
    ServiceStudyBrief,
    SynthesisBrief,
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
    Evaluation,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    SeenBeforeDraft,
    Verdict,
    VerificationDraft,
)
from grolla.domain.chat import ChatDraft
from grolla.domain.follow_up import Cycle, CycleDraft, CycleStatus, Trend
from grolla.domain.memory import (
    ENVIRONMENT,
    DossierDraft,
    LearningDraft,
    MemoryContext,
    SubjectKind,
    SynthesisDraft,
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
        "seen_before",
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


# ---- Chat do war room

CHAT_SETTINGS = ModelSettings(model="claude-haiku-5-5", max_tokens=4000, effort="low")
CONTEXT = "Incidente: Taxa de erro alta\nServiços: authorizer-api"
QUESTION = "o rollback resolveu?"


def chatter(handler: Handler) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(
        "chave",
        SETTINGS,
        base_url="http://fake/anthropic",
        transport=httpx2.MockTransport(handler),
        chat=CHAT_SETTINGS,
    )


async def reply(
    handler: Handler, toolbox: Toolbox | None = None, budget: TokenBudget | None = None
) -> ChatDraft:
    return await chatter(handler).reply(
        brief(), CONTEXT, QUESTION, toolbox or RecordingToolbox(), budget or RecordingBudget()
    )


class TestClaudeChatReplyContract(ChatReplyContract):
    @pytest.fixture
    def replying_model(self) -> LanguageModel:
        tool_use = ok(fixture("anthropic_tool_use.json"))
        return chatter(ScriptedApi(tool_use, ok(final({"reply": "Os erros pararam às 10:20."}))))

    @pytest.fixture
    def failing_replier(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        return chatter(ScriptedApi(response, response, response))


async def test_reply_runs_the_tools_and_returns_the_text() -> None:
    answer = {"reply": "  Sim: a taxa de erro voltou a 0,4% depois do rollback.  "}
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final(answer)))
    toolbox = RecordingToolbox()

    draft = await reply(api, toolbox)

    assert draft == ChatDraft(text="Sim: a taxa de erro voltou a 0,4% depois do rollback.")
    assert [name for name, _ in toolbox.calls] == ["query_metric", "recent_deploys"]
    assert len(api.bodies) == 2


async def test_reply_request_uses_the_chat_prompt_schema_and_stage_settings() -> None:
    api = ScriptedApi(ok(final({"reply": "ok"})))
    budget = RecordingBudget()

    await reply(api, budget=budget)

    [body] = api.bodies
    assert body["model"] == "claude-haiku-5-5"
    assert body["max_tokens"] == 4000
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"] == {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {"reply": {"type": "string"}},
            "required": ["reply"],
            "additionalProperties": False,
        },
    }
    assert body["system"][0]["text"] == CHAT_PROMPT
    assert body["messages"] == [{"role": "user", "content": f"{CONTEXT}\n\nPergunta: {QUESTION}"}]
    assert [usage.stage for usage in budget.spent] == ["warroom_chat"]


async def test_reply_without_its_own_stage_inherits_the_investigation_settings() -> None:
    api = ScriptedApi(ok(final({"reply": "ok"})))

    await claude(api).reply(brief(), CONTEXT, QUESTION, RecordingToolbox(), RecordingBudget())

    assert api.bodies[0]["model"] == "claude-sonnet-5-5"
    assert api.bodies[0]["output_config"]["effort"] == "medium"


async def test_reply_budget_exceeded_stops_the_loop() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final({"reply": "ok"})))
    toolbox = RecordingToolbox()

    with pytest.raises(BudgetExceededError):
        await reply(api, toolbox, RecordingBudget(fail_at=1))

    assert toolbox.calls == []


async def test_reply_api_error_becomes_language_model_error() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await reply(ScriptedApi(response, response, response))


async def test_reply_refusal_becomes_language_model_error() -> None:
    with pytest.raises(LanguageModelError, match="recusou"):
        await reply(ScriptedApi(ok(final("", stop_reason="refusal"))))


@pytest.mark.parametrize(
    "answer", ["não é json", {"reply": 1}, {"reply": "ok", "extra": 1}, {}, {"reply": "  "}]
)
async def test_reply_outside_the_schema_is_invalid(answer: object) -> None:
    with pytest.raises(InvalidAnalysisError, match="resposta do chat"):
        await reply(ScriptedApi(ok(final(answer))))


def tokens_of(body: dict[str, Any]) -> int:
    usage = body["usage"]
    return (
        usage["input_tokens"]
        + usage["output_tokens"]
        + usage["cache_read_input_tokens"]
        + usage["cache_creation_input_tokens"]
    )


async def test_reply_with_the_budget_ending_answers_with_what_it_already_consulted() -> None:
    tool_use, answer = fixture("anthropic_tool_use.json"), final({"reply": "Parcial: 0% de erro."})
    # Cabe a volta das ferramentas e a resposta final, mas não mais duas voltas.
    budget = TokenBudget(limit=tokens_of(tool_use) + tokens_of(answer) + 10)
    api = ScriptedApi(ok(tool_use), ok(answer))
    toolbox = RecordingToolbox()

    draft = await reply(api, toolbox, budget)

    assert draft == ChatDraft(text="Parcial: 0% de erro.")
    first, last = api.bodies
    assert "tool_choice" not in first
    assert last["tool_choice"] == {"type": "none"}
    assert last["messages"][-1]["content"][-1] == {"type": "text", "text": WRAP_UP}
    assert [name for name, _ in toolbox.calls] == ["query_metric", "recent_deploys"]


async def test_reply_with_room_in_the_budget_keeps_the_tools() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final({"reply": "ok"})))

    await reply(api, budget=TokenBudget(limit=1_000_000))

    assert all("tool_choice" not in body for body in api.bodies)


async def test_investigation_does_not_wrap_up_early() -> None:
    tool_use = fixture("anthropic_tool_use.json")
    api = ScriptedApi(ok(tool_use), ok(fixture("anthropic_final.json")))
    budget = TokenBudget(limit=tokens_of(tool_use) * 3)

    await claude(api).investigate(brief(), RecordingToolbox(), budget)

    assert all("tool_choice" not in body for body in api.bodies)


# ---- "Já vi isso" na análise

EPISODE_INCIDENT = UUID("0193a3a0-7c1e-7000-8000-000000000001")


def analysis_with(seen_before: object) -> dict[str, Any]:
    body = json.loads(fixture("anthropic_final.json")["content"][-1]["text"])
    return final({**body, "seen_before": seen_before})


async def test_analysis_points_a_past_incident_from_the_memory() -> None:
    reason = "mesmo erro de pool depois de deploy com migration"
    seen = {"incident_id": str(EPISODE_INCIDENT), "reason": f" {reason} "}

    draft = await investigate(ScriptedApi(ok(analysis_with(seen))))

    assert draft.seen_before == SeenBeforeDraft(EPISODE_INCIDENT, reason)


@pytest.mark.parametrize(
    "seen",
    [
        None,
        {"incident_id": "não-é-uuid", "reason": "x"},
        {"incident_id": str(UUID(int=1)), "reason": " "},
    ],
)
async def test_analysis_without_a_valid_reference_has_no_seen_before(seen: object) -> None:
    draft = await investigate(ScriptedApi(ok(analysis_with(seen))))

    assert draft.seen_before is None
    assert draft.hypotheses  # o resto da análise continua valendo


# ---- Memória: aprender, estudar um serviço e sintetizar o ambiente

LEARNING_SETTINGS = ModelSettings(model="claude-haiku-5-5", max_tokens=8000, effort="low")
STUDY_SETTINGS = ModelSettings(model="claude-opus-5-5", max_tokens=32000, effort="high")
RULE = AlertRule(
    name="Erros 5xx na pedidos-api",
    condition="count() de spans acima de 20",
    severity="critical",
    labels={"team": "credenciadora"},
    service="pedidos-api",
    url=None,
)


@pytest.fixture(autouse=True)
def memory_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    """As mensagens são do grolla-dominio: aqui o adapter só precisa entregá-las."""

    def learning(brief: LearningBrief) -> str:
        return f"aprender {brief.incident.title}"

    def study(brief: ServiceStudyBrief) -> str:
        return f"estudar {brief.service}"

    def synthesis(brief: SynthesisBrief) -> str:
        return f"sintetizar {brief.cloud}"

    module = "grolla.adapters.anthropic"
    monkeypatch.setattr(f"{module}.learning_text", learning)
    monkeypatch.setattr(f"{module}.study_text", study)
    monkeypatch.setattr(f"{module}.synthesis_text", synthesis)


def memory_model(handler: Handler, **stages: ModelSettings) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(
        "chave",
        SETTINGS,
        base_url="http://fake/anthropic",
        transport=httpx2.MockTransport(handler),
        **stages,
    )


def learning_brief() -> LearningBrief:
    return LearningBrief(make_incident(services=("pedidos-api",)), (), MemoryContext(), now=T0)


def study_brief() -> ServiceStudyBrief:
    return ServiceStudyBrief("pedidos-api", (RULE,), MemoryContext(), now=T0)


def synthesis_brief() -> SynthesisBrief:
    return SynthesisBrief((RULE,), (), MemoryContext(), cloud="2 recursos", now=T0)


LEARNED = {
    "episode_title": " pedidos-api com 5xx ",
    "episode_body": "Pool de conexões esgotado depois do deploy 1.4.2; rollback resolveu.",
    "noise": False,
    "dossiers": [
        {"subject": "pedidos-api", "subject_kind": "servico", "body": "Dossiê novo."},
        {"subject": "pedidos-db", "subject_kind": "integracao", "body": "Banco do serviço."},
        {"subject": " ", "subject_kind": "integracao", "body": "sem assunto: descartado"},
    ],
}


async def test_learn_writes_the_episode_and_the_dossiers_in_one_call_without_tools() -> None:
    api = ScriptedApi(ok(final(LEARNED)))
    budget = RecordingBudget()

    draft = await memory_model(api, learning=LEARNING_SETTINGS).learn(learning_brief(), budget)

    assert draft == LearningDraft(
        episode_title="pedidos-api com 5xx",
        episode_body="Pool de conexões esgotado depois do deploy 1.4.2; rollback resolveu.",
        noise=False,
        dossiers=(
            DossierDraft("pedidos-api", SubjectKind.SERVICE, "Dossiê novo."),
            DossierDraft("pedidos-db", SubjectKind.INTEGRATION, "Banco do serviço."),
        ),
    )
    [body] = api.bodies
    assert "tools" not in body
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["schema"]["required"] == [
        "episode_title",
        "episode_body",
        "noise",
        "dossiers",
    ]
    assert body["system"][0]["text"] == LEARNING_PROMPT
    assert body["messages"] == [
        {"role": "user", "content": f"aprender {learning_brief().incident.title}"}
    ]
    assert [usage.stage for usage in budget.spent] == ["learning"]


@pytest.mark.parametrize(
    "learned",
    [
        "não é json",
        {**LEARNED, "noise": "talvez"},
        {**LEARNED, "episode_title": "  "},
        {**LEARNED, "dossiers": [{"subject": "x", "subject_kind": "ambiente", "body": "y"}]},
    ],
)
async def test_learning_outside_the_schema_is_invalid(learned: object) -> None:
    with pytest.raises(InvalidAnalysisError):
        await memory_model(ScriptedApi(ok(final(learned)))).learn(
            learning_brief(), RecordingBudget()
        )


async def test_learning_api_error_and_refusal_become_language_model_error() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await memory_model(ScriptedApi(response, response, response)).learn(
            learning_brief(), RecordingBudget()
        )
    with pytest.raises(LanguageModelError, match="recusou"):
        await memory_model(ScriptedApi(ok(final("", stop_reason="refusal")))).learn(
            learning_brief(), RecordingBudget()
        )


async def test_learning_budget_exceeded_raises() -> None:
    with pytest.raises(BudgetExceededError):
        await memory_model(ScriptedApi(ok(final(LEARNED)))).learn(
            learning_brief(), RecordingBudget(fail_at=1)
        )


class StudyToolbox(RecordingToolbox):
    """As avaliações que o toolbox real gravaria: só os links devolvidos viram fonte."""

    @property
    def evaluations(self) -> tuple[Evaluation, ...]:
        readme = "https://dev.azure.com/org/Core/_git/pedidos-api?path=/README.md"
        return (
            Evaluation("read_file", "README.md", "# pedidos-api", readme, ok=True),
            Evaluation("query_metric", "vazão", "20 chamadas/s", "https://signoz/x", ok=True),
            Evaluation("read_file", "README.md", "# pedidos-api", readme, ok=True),
            Evaluation("cloud_resources", "nuvem", "não foi possível", None, ok=False),
        )


async def test_study_service_runs_the_tools_and_returns_the_dossier_with_the_links() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final({"dossier": " Dossiê. "})))
    toolbox = StudyToolbox()
    budget = RecordingBudget()

    draft = await memory_model(api, study=STUDY_SETTINGS).study_service(
        study_brief(), toolbox, budget
    )

    assert draft == DossierDraft(
        "pedidos-api",
        SubjectKind.SERVICE,
        "Dossiê.",
        ("https://dev.azure.com/org/Core/_git/pedidos-api?path=/README.md", "https://signoz/x"),
    )
    assert [name for name, _ in toolbox.calls] == ["query_metric", "recent_deploys"]
    first = api.bodies[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["system"][0]["text"] == STUDY_PROMPT
    assert first["messages"][0] == {"role": "user", "content": "estudar pedidos-api"}
    assert first["output_config"]["format"]["schema"]["required"] == ["dossier"]
    assert [usage.stage for usage in budget.spent] == ["study", "study"]


async def test_study_with_the_budget_ending_writes_the_dossier_with_what_it_has() -> None:
    tool_use, answer = fixture("anthropic_tool_use.json"), final({"dossier": "Parcial."})
    budget = TokenBudget(limit=tokens_of(tool_use) + tokens_of(answer) + 10)
    api = ScriptedApi(ok(tool_use), ok(answer))

    draft = await memory_model(api).study_service(study_brief(), RecordingToolbox(), budget)

    assert draft.body == "Parcial."
    assert api.bodies[-1]["tool_choice"] == {"type": "none"}


@pytest.mark.parametrize("answer", ["não é json", {"dossier": "  "}, {"body": "x"}])
async def test_study_outside_the_schema_is_invalid(answer: object) -> None:
    with pytest.raises(InvalidAnalysisError):
        await memory_model(ScriptedApi(ok(final(answer)))).study_service(
            study_brief(), RecordingToolbox(), RecordingBudget()
        )


async def test_study_api_error_becomes_language_model_error() -> None:
    with pytest.raises(LanguageModelError, match="400"):
        await memory_model(ScriptedApi(httpx2.Response(400, json={}))).study_service(
            study_brief(), RecordingToolbox(), RecordingBudget()
        )


SYNTHESIZED = {
    "environment": " Ambiente da Credenciadora. ",
    "integrations": [
        {"subject": "pedidos-db", "body": "Banco chamado pela pedidos-api."},
        {"subject": "", "body": "descartado"},
    ],
}


async def test_synthesize_writes_the_environment_and_the_integrations_without_tools() -> None:
    api = ScriptedApi(ok(final(SYNTHESIZED)))
    budget = RecordingBudget()

    draft = await memory_model(api, study=STUDY_SETTINGS).synthesize(synthesis_brief(), budget)

    assert draft == SynthesisDraft(
        environment=DossierDraft(
            ENVIRONMENT, SubjectKind.ENVIRONMENT, "Ambiente da Credenciadora."
        ),
        integrations=(
            DossierDraft("pedidos-db", SubjectKind.INTEGRATION, "Banco chamado pela pedidos-api."),
        ),
    )
    [body] = api.bodies
    assert "tools" not in body
    assert body["model"] == "claude-opus-5-5"
    assert body["system"][0]["text"] == SYNTHESIS_PROMPT
    assert body["messages"][0]["content"] == "sintetizar 2 recursos"
    assert [usage.stage for usage in budget.spent] == ["study"]


@pytest.mark.parametrize("answer", ["não é json", {**SYNTHESIZED, "environment": " "}])
async def test_synthesis_outside_the_schema_is_invalid(answer: object) -> None:
    with pytest.raises(InvalidAnalysisError):
        await memory_model(ScriptedApi(ok(final(answer)))).synthesize(
            synthesis_brief(), RecordingBudget()
        )


async def test_synthesis_api_error_becomes_language_model_error() -> None:
    with pytest.raises(LanguageModelError, match="400"):
        await memory_model(ScriptedApi(httpx2.Response(400, json={}))).synthesize(
            synthesis_brief(), RecordingBudget()
        )


def memory_api(request: httpx2.Request) -> httpx2.Response:
    """Responde pela etapa, reconhecida pelo schema: o estudo pede ferramentas antes."""
    body = json.loads(request.content)
    required = body["output_config"]["format"]["schema"]["required"]
    if "episode_title" in required:
        return ok(final(LEARNED))
    if "environment" in required:
        return ok(final(SYNTHESIZED))
    answered = any(
        block.get("type") == "tool_result"
        for message in body["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
    )
    return ok(final({"dossier": "Dossiê."}) if answered else fixture("anthropic_tool_use.json"))


class TestClaudeMemoryModelContract(MemoryModelContract):
    @pytest.fixture
    def memory_model(self) -> LanguageModel:
        return memory_model(memory_api)

    @pytest.fixture
    def failing_memory_model(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        return memory_model(
            lambda _request: httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        )


# ---- Acompanhamento da crise

FOLLOW_UP_SETTINGS = ModelSettings(model="claude-haiku-5-5", max_tokens=8000, effort="low")
PREVIOUS = Cycle(
    incident_id=UUID("0193a3a0-7c1e-7000-8000-000000000002"),
    number=1,
    status=CycleStatus.COMPLETED,
    requested_at=T0,
    trend=Trend.WORSE,
    error_rate=40.0,
    summary="Erro subindo.",
)


@pytest.fixture(autouse=True)
def follow_up_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mensagem é do grolla-dominio: aqui o adapter só precisa entregá-la."""

    def text(context: str, previous: Cycle | None) -> str:
        return f"{context}\nAnterior: {previous.trend if previous else 'nenhum'}"

    monkeypatch.setattr("grolla.adapters.anthropic.follow_up_text", text)


def cycle_result(**changes: object) -> dict[str, object]:
    result: dict[str, object] = {
        "trend": "melhorando",
        "error_rate": 12.5,
        "summary": "  Erro caindo depois do rollback.  ",
        "changed": True,
        "evidence": [{"text": "taxa de erro 12,5%", "url": "https://signoz/x"}],
    }
    return {**result, **changes}


def follower(handler: Handler) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(
        "chave",
        SETTINGS,
        base_url="http://fake/anthropic",
        transport=httpx2.MockTransport(handler),
        follow_up=FOLLOW_UP_SETTINGS,
    )


async def follow_up(
    handler: Handler,
    toolbox: Toolbox | None = None,
    budget: TokenBudget | None = None,
    previous: Cycle | None = PREVIOUS,
) -> CycleDraft:
    return await follower(handler).follow_up(
        brief(), CONTEXT, previous, toolbox or RecordingToolbox(), budget or RecordingBudget()
    )


async def test_follow_up_runs_the_tools_and_returns_the_cycle() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final(cycle_result())))
    toolbox = RecordingToolbox()

    draft = await follow_up(api, toolbox)

    assert draft == CycleDraft(
        trend=Trend.BETTER,
        error_rate=12.5,
        summary="Erro caindo depois do rollback.",
        changed=True,
        evidence=(Evidence("taxa de erro 12,5%", "https://signoz/x"),),
    )
    assert [name for name, _ in toolbox.calls] == ["query_metric", "recent_deploys"]


async def test_follow_up_request_uses_its_prompt_schema_message_and_stage() -> None:
    api = ScriptedApi(ok(final(cycle_result())))
    budget = RecordingBudget()

    await follow_up(api, budget=budget)

    [body] = api.bodies
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]["schema"]
    assert schema["required"] == ["trend", "error_rate", "summary", "changed", "evidence"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["trend"]["enum"] == [
        "piorando",
        "estavel",
        "melhorando",
        "normalizado",
    ]
    assert schema["properties"]["error_rate"] == {"type": ["number", "null"]}
    assert body["system"][0]["text"] == FOLLOW_UP_PROMPT
    assert body["messages"] == [{"role": "user", "content": f"{CONTEXT}\nAnterior: piorando"}]
    assert [usage.stage for usage in budget.spent] == ["follow_up"]


async def test_follow_up_without_its_own_stage_inherits_the_investigation_settings() -> None:
    api = ScriptedApi(ok(final(cycle_result())))

    await claude(api).follow_up(brief(), CONTEXT, None, RecordingToolbox(), RecordingBudget())

    assert api.bodies[0]["model"] == "claude-sonnet-5-5"
    assert api.bodies[0]["messages"][0]["content"].endswith("Anterior: nenhum")


async def test_follow_up_without_measure_has_no_error_rate_and_at_most_three_evidences() -> None:
    evidence = [{"text": f"e{i}", "url": None} for i in range(5)]
    api = ScriptedApi(ok(final(cycle_result(error_rate=None, evidence=evidence))))

    draft = await follow_up(api)

    assert draft.error_rate is None
    assert [e.text for e in draft.evidence] == ["e0", "e1", "e2"]


async def test_follow_up_with_the_budget_ending_answers_with_what_it_already_consulted() -> None:
    tool_use, answer = fixture("anthropic_tool_use.json"), final(cycle_result())
    budget = TokenBudget(limit=tokens_of(tool_use) + tokens_of(answer) + 10)
    api = ScriptedApi(ok(tool_use), ok(answer))

    draft = await follow_up(api, budget=budget)

    assert draft.trend is Trend.BETTER
    assert api.bodies[-1]["tool_choice"] == {"type": "none"}
    assert api.bodies[-1]["messages"][-1]["content"][-1] == {"type": "text", "text": WRAP_UP}


async def test_follow_up_budget_exceeded_stops_the_loop() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final(cycle_result())))
    toolbox = RecordingToolbox()

    with pytest.raises(BudgetExceededError):
        await follow_up(api, toolbox, RecordingBudget(fail_at=1))

    assert toolbox.calls == []


async def test_follow_up_api_error_and_refusal_become_language_model_error() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await follow_up(ScriptedApi(response, response, response))
    with pytest.raises(LanguageModelError, match="recusou"):
        await follow_up(ScriptedApi(ok(final("", stop_reason="refusal"))))


@pytest.mark.parametrize(
    "result",
    [
        "não é json",
        cycle_result(trend="caindo"),
        cycle_result(error_rate=120),
        cycle_result(error_rate=-1),
        cycle_result(summary="  "),
        cycle_result(extra=1),
        {"trend": "estavel"},
    ],
)
async def test_follow_up_outside_the_schema_is_invalid(result: object) -> None:
    with pytest.raises(InvalidAnalysisError):
        await follow_up(ScriptedApi(ok(final(result))))
