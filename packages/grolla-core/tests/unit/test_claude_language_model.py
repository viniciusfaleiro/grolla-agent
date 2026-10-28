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
    ConsoleModelContract,
    FollowUpModelContract,
    LanguageModelContract,
    MemoryModelContract,
    PostMortemModelContract,
    TranscriptModelContract,
    TriageModelContract,
)
from grolla.adapters.anthropic import (
    WRAP_UP,
    AnthropicApi,
    Bedrock,
    ClaudeLanguageModel,
    ModelSettings,
)
from grolla.application.investigation import (
    CHAT_PROMPT,
    CONSOLE_PROMPT,
    FOLLOW_UP_PROMPT,
    LEARNING_PROMPT,
    POST_MORTEM_PROMPT,
    STUDY_PROMPT,
    SYNTHESIS_PROMPT,
    SYSTEM_PROMPT,
    TRANSCRIPT_PROMPT,
    TRIAGE_PROMPT,
    VERIFICATION_PROMPT,
    AlertRule,
    BudgetExceededError,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModel,
    LanguageModelError,
    LearningBrief,
    LlmUsage,
    PostMortemBrief,
    ServiceStudyBrief,
    SynthesisBrief,
    TokenBudget,
    Toolbox,
    ToolSpec,
    TriageBrief,
    brief_text,
    verification_text,
)
from grolla.domain.analysis import (
    Action,
    Analysis,
    AnalysisDraft,
    Command,
    Evaluation,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    SeenBeforeDraft,
    Verdict,
    VerificationDraft,
)
from grolla.domain.chat import ChatDraft
from grolla.domain.console import ChartRequest, ConsoleDraft
from grolla.domain.follow_up import Cycle, CycleDraft, CycleStatus, Trend
from grolla.domain.incidents import Severity
from grolla.domain.memory import (
    ENVIRONMENT,
    DossierDraft,
    LearningDraft,
    MemoryContext,
    SubjectKind,
    SynthesisDraft,
)
from grolla.domain.post_mortem import ActionKind, PostMortemDraft, SectionKey
from grolla.domain.safety import Scope
from grolla.domain.transcript import (
    HypothesisCheck,
    TranscriptDraft,
    TranscriptItem,
    TranscriptItemKind,
)
from grolla.domain.transcript import HypothesisVerdict as CallVerdict
from grolla.domain.triage import TriageDraft
from grolla.testing.builders import T0, make_alert, make_analysis, make_incident
from grolla.testing.clock import FakeClock

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SETTINGS = ModelSettings(model="claude-sonnet-5-5", max_tokens=16000, effort="medium")
CONNECTION = AnthropicApi("chave", "http://fake/anthropic")

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
    return ClaudeLanguageModel(CONNECTION, settings, transport=httpx2.MockTransport(handler))


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
ROLLBACK: dict[str, Any] = {
    "action": "Fazer rollback do authorizer-api para o build 1.4.1",
    "target": "authorizer-api",
    "expected": "taxa de erro volta para 0,4%",
    "link": DEPLOY_LINK,
    "commands": [],
}
INVESTIGATE_STEP: dict[str, Any] = {
    "action": "Investigar o banco",
    "target": "authorizer-db",
    "expected": "saber se está lento",
    "link": None,
    "commands": [],
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
        CONNECTION,
        SETTINGS,
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


async def test_verification_schema_asks_for_the_commands_of_each_action() -> None:
    api = ScriptedApi(ok(final(verification())))

    await verify(api)

    action = api.bodies[0]["output_config"]["format"]["schema"]["properties"]["actions"]["items"]

    assert action["required"] == ["action", "target", "expected", "link", "commands"]
    assert action["properties"]["commands"] == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"command": {"type": "string"}, "run_in": {"type": "string"}},
            "required": ["command", "run_in"],
            "additionalProperties": False,
        },
    }


async def test_commands_become_the_steps_of_the_action_in_order() -> None:
    commands = [
        {"command": "az pipelines run --name authorizer-cd", "run_in": "Azure DevOps"},
        {
            "command": "`kubectl rollout status deploy/authorizer-api -n pagamentos`",
            "run_in": "eks",
        },
        {"command": "kubectl get pods", "run_in": " "},  # sem onde rodar: fora
    ]
    api = ScriptedApi(ok(final(verification(actions=[{**ROLLBACK, "commands": commands}]))))

    [action] = (await verify(api)).actions

    assert action.commands == (
        Command("az pipelines run --name authorizer-cd", "Azure DevOps"),
        Command("kubectl rollout status deploy/authorizer-api -n pagamentos", "eks"),
    )


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
    _with(actions=[{key: value for key, value in ROLLBACK.items() if key != "commands"}]),
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
SCOPE_SCHEMA = {"type": "string", "enum": ["dominio", "fora_do_escopo", "pedido_de_execucao"]}


def chatter(handler: Handler) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(
        CONNECTION,
        SETTINGS,
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
            "properties": {"reply": {"type": "string"}, "scope": SCOPE_SCHEMA},
            "required": ["reply", "scope"],
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


@pytest.mark.parametrize("scope", list(Scope))
async def test_reply_carries_the_scope_of_the_question(scope: Scope) -> None:
    draft = await reply(ScriptedApi(ok(final({"reply": "texto", "scope": scope.value}))))

    assert draft == ChatDraft(text="texto", scope=scope)


async def test_reply_without_scope_is_from_the_domain() -> None:
    draft = await reply(ScriptedApi(ok(final({"reply": "texto"}))))

    assert draft.scope is Scope.DOMAIN


async def test_reply_api_error_becomes_language_model_error() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await reply(ScriptedApi(response, response, response))


async def test_reply_refusal_becomes_language_model_error() -> None:
    with pytest.raises(LanguageModelError, match="recusou"):
        await reply(ScriptedApi(ok(final("", stop_reason="refusal"))))


@pytest.mark.parametrize(
    "answer",
    [
        "não é json",
        {"reply": 1},
        {"reply": "ok", "extra": 1},
        {},
        {"reply": "  "},
        {"reply": "ok", "scope": "poesia"},
    ],
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
        CONNECTION,
        SETTINGS,
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
        CONNECTION,
        SETTINGS,
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


class TestClaudeFollowUpModelContract(FollowUpModelContract):
    @pytest.fixture
    def following_model(self) -> LanguageModel:
        tool_use = ok(fixture("anthropic_tool_use.json"))
        return follower(ScriptedApi(tool_use, ok(final(cycle_result()))))

    @pytest.fixture
    def failing_follower(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        return follower(ScriptedApi(response, response, response))


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


# ---- Post mortem: rascunho e regeneração, uma chamada sem ferramentas cada ----

POST_MORTEM_SETTINGS = ModelSettings(model="claude-opus-5-5", max_tokens=32000, effort="high")
WRITTEN = {
    "resumo": " O pedidos-api devolveu 5xx por 20 min. ",
    "impacto": "12% dos pedidos falharam.",
    "deteccao": "Alerta de taxa de erro em 2 min.",
    "fatores": "Pool de conexões pequeno para o pico.",
    "hipoteses": "- Deploy 1.4.2: confirmada",
    "licoes": "Funcionou: o rollback. Sorte: fora do horário de pico.",
    "plano": "Itens abaixo.",
    "avaliacao_agente": "A causa raiz saiu em 4 min.",
    "actions": [
        {"description": " Alarme de conexões no RDS ", "kind": "detectar"},
        {"description": "Aumentar o pool", "kind": "prevenir"},
        {"description": "  ", "kind": "mitigar"},
    ],
}


@pytest.fixture(autouse=True)
def post_mortem_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    """As mensagens são do grolla-dominio: aqui o adapter só precisa entregá-las."""

    def drafting(brief: PostMortemBrief) -> str:
        return f"post mortem de {brief.incident.title}"

    def regenerating(brief: PostMortemBrief, key: SectionKey, instruction: str | None) -> str:
        return f"regenerar {key.value} de {brief.incident.title}: {instruction}"

    module = "grolla.adapters.anthropic"
    monkeypatch.setattr(f"{module}.post_mortem_text", drafting)
    monkeypatch.setattr(f"{module}.regeneration_text", regenerating)


def post_mortem_brief() -> PostMortemBrief:
    incident = make_incident(services=("pedidos-api",))
    return PostMortemBrief(incident, "contexto", current=None, impact=None, now=T0)


async def test_draft_post_mortem_writes_every_model_section_in_one_call_without_tools() -> None:
    api = ScriptedApi(ok(final(WRITTEN)))
    budget = RecordingBudget()
    model = memory_model(api, post_mortem=POST_MORTEM_SETTINGS)

    draft = await model.draft_post_mortem(post_mortem_brief(), budget)

    assert draft == PostMortemDraft(
        sections={
            SectionKey.SUMMARY: "O pedidos-api devolveu 5xx por 20 min.",
            SectionKey.IMPACT: "12% dos pedidos falharam.",
            SectionKey.DETECTION: "Alerta de taxa de erro em 2 min.",
            SectionKey.FACTORS: "Pool de conexões pequeno para o pico.",
            SectionKey.HYPOTHESES: "- Deploy 1.4.2: confirmada",
            SectionKey.LESSONS: "Funcionou: o rollback. Sorte: fora do horário de pico.",
            SectionKey.ACTIONS: "Itens abaixo.",
            SectionKey.AGENT: "A causa raiz saiu em 4 min.",
        },
        actions=(
            ("Alarme de conexões no RDS", ActionKind.DETECT),
            ("Aumentar o pool", ActionKind.PREVENT),
        ),
    )
    [body] = api.bodies
    assert "tools" not in body
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "high"
    schema = body["output_config"]["format"]["schema"]
    assert schema["required"] == [
        "resumo",
        "impacto",
        "deteccao",
        "fatores",
        "hipoteses",
        "licoes",
        "plano",
        "avaliacao_agente",
        "actions",
    ]
    assert schema["properties"]["actions"]["items"]["properties"]["kind"]["enum"] == [
        "prevenir",
        "detectar",
        "mitigar",
    ]
    assert body["system"][0]["text"] == POST_MORTEM_PROMPT
    title = post_mortem_brief().incident.title
    assert body["messages"] == [{"role": "user", "content": f"post mortem de {title}"}]
    assert [usage.stage for usage in budget.spent] == ["post_mortem"]


async def test_post_mortem_without_its_own_stage_inherits_the_investigation_settings() -> None:
    api = ScriptedApi(ok(final(WRITTEN)))

    await memory_model(api).draft_post_mortem(post_mortem_brief(), RecordingBudget())

    assert api.bodies[0]["model"] == SETTINGS.model


@pytest.mark.parametrize(
    "written",
    [
        "não é json",
        {**WRITTEN, "fatores": "  "},
        {**WRITTEN, "actions": [{"description": "x", "kind": "corrigir"}]},
        {k: v for k, v in WRITTEN.items() if k != "licoes"},
        {**WRITTEN, "metricas": "o modelo não escreve métricas"},
    ],
)
async def test_post_mortem_outside_the_schema_is_invalid(written: object) -> None:
    with pytest.raises(InvalidAnalysisError):
        await memory_model(ScriptedApi(ok(final(written)))).draft_post_mortem(
            post_mortem_brief(), RecordingBudget()
        )


async def test_post_mortem_api_error_refusal_and_budget() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await memory_model(ScriptedApi(response, response, response)).draft_post_mortem(
            post_mortem_brief(), RecordingBudget()
        )
    with pytest.raises(LanguageModelError, match="recusou"):
        await memory_model(ScriptedApi(ok(final("", stop_reason="refusal")))).draft_post_mortem(
            post_mortem_brief(), RecordingBudget()
        )
    with pytest.raises(BudgetExceededError):
        await memory_model(ScriptedApi(ok(final(WRITTEN)))).draft_post_mortem(
            post_mortem_brief(), RecordingBudget(fail_at=1)
        )


async def regenerate(api: ScriptedApi, budget: TokenBudget | None = None) -> str:
    model = memory_model(api, post_mortem=POST_MORTEM_SETTINGS)
    return await model.regenerate_section(
        post_mortem_brief(),
        SectionKey.FACTORS,
        "incluir a falta de alarme no RDS",
        budget or RecordingBudget(),
    )


async def test_regenerate_section_returns_the_new_text_in_one_call_without_tools() -> None:
    api = ScriptedApi(ok(final({"body": " Faltou alarme no RDS. "})))
    budget = RecordingBudget()

    assert await regenerate(api, budget) == "Faltou alarme no RDS."

    [body] = api.bodies
    assert "tools" not in body
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"]["schema"]["required"] == ["body"]
    assert body["system"][0]["text"] == POST_MORTEM_PROMPT
    title = post_mortem_brief().incident.title
    text = f"regenerar fatores de {title}: incluir a falta de alarme no RDS"
    assert body["messages"] == [{"role": "user", "content": text}]
    assert [usage.stage for usage in budget.spent] == ["post_mortem"]


@pytest.mark.parametrize("answer", ["não é json", {"body": "  "}, {"text": "x"}])
async def test_regenerated_section_outside_the_schema_is_invalid(answer: object) -> None:
    with pytest.raises(InvalidAnalysisError):
        await regenerate(ScriptedApi(ok(final(answer))))


async def test_regeneration_api_error_becomes_language_model_error() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await regenerate(ScriptedApi(response, response, response))


def post_mortem_api(request: httpx2.Request) -> httpx2.Response:
    """Responde pela etapa, reconhecida pelo schema: o rascunho inteiro ou uma seção."""
    body = json.loads(request.content)
    required = body["output_config"]["format"]["schema"]["required"]
    return ok(final({"body": "Seção nova."} if required == ["body"] else WRITTEN))


class TestClaudePostMortemModelContract(PostMortemModelContract):
    @pytest.fixture
    def post_mortem_model(self) -> LanguageModel:
        return memory_model(post_mortem_api)

    @pytest.fixture
    def failing_post_mortem_model(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        return memory_model(
            lambda _request: httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        )


# ---- Triagem: uma chamada sem ferramentas ----

TRIAGE_SETTINGS = ModelSettings(model="claude-haiku-5-5", max_tokens=4000, effort="low")
OTHER_ID = "0193a3a0-7c1e-7000-8000-000000000009"
TRIAGED = {
    "same_as": OTHER_ID,
    "same_reason": " os dois usam o banco pedidos-db ",
    "severity": "P1",
    "severity_reason": " pagamentos parados ",
}


@pytest.fixture(autouse=True)
def triage_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mensagem é do grolla-dominio: aqui o adapter só precisa entregá-la."""

    def text(brief: TriageBrief) -> str:
        return f"triagem de {brief.incident.title} com {len(brief.others)} outros"

    monkeypatch.setattr("grolla.adapters.anthropic.triage_text", text)


def triage_brief() -> TriageBrief:
    incident = make_incident(services=("pedidos-api",))
    other = make_incident(id=UUID(OTHER_ID), services=("pagamentos-api",))
    return TriageBrief(
        incident,
        (make_alert(service="pedidos-api"),),
        ((other, "erros 5xx"),),
        MemoryContext(),
        opened_by_default=True,
        now=T0,
    )


async def triage(
    api: Handler, budget: TokenBudget | None = None, **stages: ModelSettings
) -> TriageDraft:
    return await memory_model(api, **stages).triage(triage_brief(), budget or RecordingBudget())


async def test_triage_returns_the_draft_in_one_call_without_tools() -> None:
    api = ScriptedApi(ok(final(TRIAGED)))
    budget = RecordingBudget()

    draft = await triage(api, budget, triage=TRIAGE_SETTINGS)

    assert draft == TriageDraft(
        same_as=UUID(OTHER_ID),
        same_reason="os dois usam o banco pedidos-db",
        severity=Severity.P1,
        severity_reason="pagamentos parados",
    )
    [body] = api.bodies
    assert "tools" not in body
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]["schema"]
    assert schema["required"] == ["same_as", "same_reason", "severity", "severity_reason"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["severity"]["anyOf"][0]["enum"] == ["P0", "P1", "P2", "P3"]
    assert body["system"][0]["text"] == TRIAGE_PROMPT
    title = triage_brief().incident.title
    assert body["messages"] == [{"role": "user", "content": f"triagem de {title} com 1 outros"}]
    assert [usage.stage for usage in budget.spent] == ["triage"]


async def test_triage_without_its_own_stage_inherits_the_investigation_settings() -> None:
    api = ScriptedApi(ok(final(TRIAGED)))

    await triage(api)

    assert api.bodies[0]["model"] == SETTINGS.model


async def test_triage_without_merge_nor_severity() -> None:
    answer = {"same_as": None, "same_reason": "", "severity": None, "severity_reason": ""}

    draft = await triage(ScriptedApi(ok(final(answer))))

    assert draft == TriageDraft(None, "", None, "")


@pytest.mark.parametrize("same_as", ["incidente-9", "", "0193a3a0"])
async def test_triage_with_an_id_that_is_not_a_uuid_has_no_merge(same_as: str) -> None:
    draft = await triage(ScriptedApi(ok(final({**TRIAGED, "same_as": same_as}))))

    assert draft.same_as is None
    assert draft.severity is Severity.P1


@pytest.mark.parametrize(
    "answer",
    [
        "não é json",
        {**TRIAGED, "severity": "P9"},
        {k: v for k, v in TRIAGED.items() if k != "same_reason"},
        {**TRIAGED, "merge": True},
    ],
)
async def test_triage_outside_the_schema_is_invalid(answer: object) -> None:
    with pytest.raises(InvalidAnalysisError, match="triagem"):
        await triage(ScriptedApi(ok(final(answer))))


async def test_triage_api_error_refusal_and_budget() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await triage(ScriptedApi(response, response, response))
    with pytest.raises(LanguageModelError, match="recusou"):
        await triage(ScriptedApi(ok(final("", stop_reason="refusal"))))
    with pytest.raises(BudgetExceededError):
        await triage(ScriptedApi(ok(final(TRIAGED))), RecordingBudget(fail_at=1))


class TestClaudeTriageModelContract(TriageModelContract):
    @pytest.fixture
    def triage_model(self) -> LanguageModel:
        answer = {**TRIAGED, "same_as": None}
        return memory_model(lambda _request: ok(final(answer)))

    @pytest.fixture
    def failing_triage_model(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        return memory_model(
            lambda _request: httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        )


# ---- Console de operação

CONSOLE_SETTINGS = ModelSettings(model="claude-haiku-5-5", max_tokens=4000, effort="low")
CONSOLE_QUESTION = "como está a taxa de erro do captura-api?"
CONSOLE_ANSWER = {
    "text": "  Erro em 0,4% no captura-api, estável.  ",
    "charts": [{"service": "captura-api", "signal": "taxa_de_erro", "minutes": 60}],
}


@pytest.fixture(autouse=True)
def console_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mensagem é do grolla-dominio: aqui o adapter só precisa entregá-la."""

    def text(context: str, question: str) -> str:
        return f"{context}\n\nPergunta: {question}"

    monkeypatch.setattr("grolla.adapters.anthropic.console_text", text)


def consultant(handler: Handler, **stages: ModelSettings) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(
        CONNECTION,
        SETTINGS,
        transport=httpx2.MockTransport(handler),
        **stages,
    )


async def consult(
    handler: Handler, toolbox: Toolbox | None = None, budget: TokenBudget | None = None
) -> ConsoleDraft:
    model = consultant(handler, console=CONSOLE_SETTINGS)
    return await model.consult(
        "sem mensagens anteriores",
        CONSOLE_QUESTION,
        toolbox or RecordingToolbox(),
        budget or RecordingBudget(),
    )


class TestClaudeConsoleModelContract(ConsoleModelContract):
    @pytest.fixture
    def consulting_model(self) -> LanguageModel:
        tool_use = ok(fixture("anthropic_tool_use.json"))
        return consultant(ScriptedApi(tool_use, ok(final(CONSOLE_ANSWER))))

    @pytest.fixture
    def failing_consultant(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        return consultant(ScriptedApi(response, response, response))


async def test_consult_runs_the_tools_and_returns_the_text_and_the_charts() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final(CONSOLE_ANSWER)))
    toolbox = RecordingToolbox()

    draft = await consult(api, toolbox)

    assert draft == ConsoleDraft(
        text="Erro em 0,4% no captura-api, estável.",
        charts=(ChartRequest("captura-api", "taxa_de_erro", 60),),
    )
    assert [name for name, _ in toolbox.calls] == ["query_metric", "recent_deploys"]


async def test_consult_request_uses_the_console_prompt_schema_and_stage_settings() -> None:
    api = ScriptedApi(ok(final({"text": "ok", "charts": []})))
    budget = RecordingBudget()

    draft = await consult(api, budget=budget)

    assert draft == ConsoleDraft(text="ok")
    [body] = api.bodies
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["effort"] == "low"
    chart = {
        "type": "object",
        "properties": {
            "service": {"type": "string"},
            "signal": {"type": "string", "enum": ["taxa_de_erro", "latencia_p99", "vazao"]},
            "minutes": {"type": "integer"},
        },
        "required": ["service", "signal", "minutes"],
        "additionalProperties": False,
    }
    assert body["output_config"]["format"]["schema"] == {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "scope": SCOPE_SCHEMA,
            "charts": {"type": "array", "items": chart},
        },
        "required": ["text", "scope", "charts"],
        "additionalProperties": False,
    }
    assert body["system"][0]["text"] == CONSOLE_PROMPT
    content = f"sem mensagens anteriores\n\nPergunta: {CONSOLE_QUESTION}"
    assert body["messages"] == [{"role": "user", "content": content}]
    assert [usage.stage for usage in budget.spent] == ["console"]


@pytest.mark.parametrize(
    ("stages", "model"),
    [({"chat": CHAT_SETTINGS}, "claude-haiku-5-5"), ({}, "claude-sonnet-5-5")],
)
async def test_consult_without_its_own_stage_inherits_the_chat_settings(
    stages: dict[str, ModelSettings], model: str
) -> None:
    api = ScriptedApi(ok(final({"text": "ok", "charts": []})))

    await consultant(api, **stages).consult("", "oi", RecordingToolbox(), RecordingBudget())

    assert api.bodies[0]["model"] == model


async def test_consult_with_the_budget_ending_answers_with_what_it_already_consulted() -> None:
    tool_use, answer = fixture("anthropic_tool_use.json"), final(CONSOLE_ANSWER)
    budget = TokenBudget(limit=tokens_of(tool_use) + tokens_of(answer) + 10)
    api = ScriptedApi(ok(tool_use), ok(answer))

    await consult(api, budget=budget)

    assert api.bodies[-1]["tool_choice"] == {"type": "none"}


async def test_consult_budget_exceeded_stops_the_loop() -> None:
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(final(CONSOLE_ANSWER)))
    toolbox = RecordingToolbox()

    with pytest.raises(BudgetExceededError):
        await consult(api, toolbox, RecordingBudget(fail_at=1))

    assert toolbox.calls == []


@pytest.mark.parametrize("scope", list(Scope))
async def test_consult_carries_the_scope_of_the_question(scope: Scope) -> None:
    api = ScriptedApi(ok(final({"text": "texto", "charts": [], "scope": scope.value})))

    draft = await consult(api)

    assert draft == ConsoleDraft(text="texto", scope=scope)


async def test_consult_api_error_and_refusal_become_language_model_error() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await consult(ScriptedApi(response, response, response))
    with pytest.raises(LanguageModelError, match="recusou"):
        await consult(ScriptedApi(ok(final("", stop_reason="refusal"))))


@pytest.mark.parametrize(
    "answer",
    [
        "não é json",
        {"text": "ok"},
        {"text": "ok", "charts": [{"service": "x", "signal": "vazao"}]},
        {"text": "ok", "charts": [], "extra": 1},
        {"text": "  ", "charts": []},
        {"text": "ok", "charts": [], "scope": "executar_agora"},
    ],
)
async def test_consult_outside_the_schema_is_invalid(answer: object) -> None:
    with pytest.raises(InvalidAnalysisError, match="resposta do console"):
        await consult(ScriptedApi(ok(final(answer))))


# ---- Transcrição de call: uma chamada sem ferramentas, herdando o modelo do post mortem

TRANSCRIPT_SETTINGS = ModelSettings(model="claude-haiku-5-5", max_tokens=8000, effort="low")
EXTRACTED: dict[str, Any] = {
    "items": [
        {
            "kind": "decisao",
            "text": " Fazer rollback do pedidos-api ",
            "owner": None,
            "said_at": None,
            "excerpt": " Ana: vamos fazer rollback ",
        },
        {
            "kind": "acao",
            "text": "Abrir chamado na AWS",
            "owner": " Bruno ",
            "said_at": "  ",
            "excerpt": "Bruno fica com o chamado",
        },
        {
            "kind": "horario",
            "text": "Erros começaram",
            "owner": "",
            "said_at": "03:12",
            "excerpt": "Carla: começou 03:12",
        },
        {"kind": "acao", "text": "  ", "owner": None, "said_at": None, "excerpt": "x"},
    ],
    "participants": [" Ana ", "Bruno", "Ana", " "],
    "hypotheses": [
        {"hypothesis": " Deploy 1.4.2 ", "verdict": "descartada", "excerpt": "não foi o deploy"},
        {"hypothesis": "", "verdict": "confirmada", "excerpt": "foi isso"},
    ],
}


@pytest.fixture(autouse=True)
def transcript_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mensagem é do grolla-dominio: aqui o adapter só precisa entregá-la."""

    def text(context: str, transcript: str) -> str:
        return f"{context}\n\nTranscrição:\n{transcript}"

    monkeypatch.setattr("grolla.adapters.anthropic.transcript_text", text)


async def extract(
    api: Handler, budget: TokenBudget | None = None, **stages: ModelSettings
) -> TranscriptDraft:
    return await memory_model(api, **stages).extract_transcript(
        "Incidente: 5xx no pedidos-api", "Ana: vamos fazer rollback", budget or RecordingBudget()
    )


async def test_extract_transcript_returns_the_draft_in_one_call_without_tools() -> None:
    api = ScriptedApi(ok(final(EXTRACTED)))
    budget = RecordingBudget()

    draft = await extract(api, budget, transcript=TRANSCRIPT_SETTINGS)

    assert draft == TranscriptDraft(
        items=(
            TranscriptItem(
                TranscriptItemKind.DECISION,
                "Fazer rollback do pedidos-api",
                None,
                None,
                "Ana: vamos fazer rollback",
            ),
            TranscriptItem(
                TranscriptItemKind.ACTION,
                "Abrir chamado na AWS",
                "Bruno",
                None,
                "Bruno fica com o chamado",
            ),
            TranscriptItem(
                TranscriptItemKind.TIME, "Erros começaram", None, "03:12", "Carla: começou 03:12"
            ),
        ),
        participants=("Ana", "Bruno"),
        hypotheses=(HypothesisCheck("Deploy 1.4.2", CallVerdict.DISCARDED, "não foi o deploy"),),
    )
    [body] = api.bodies
    assert "tools" not in body
    assert body["model"] == "claude-haiku-5-5"
    assert body["output_config"]["effort"] == "low"
    schema = body["output_config"]["format"]["schema"]
    assert schema["required"] == ["items", "participants", "hypotheses"]
    assert schema["additionalProperties"] is False
    item = schema["properties"]["items"]["items"]
    assert item["required"] == ["kind", "text", "owner", "said_at", "excerpt"]
    assert item["properties"]["kind"]["enum"] == ["decisao", "acao", "horario"]
    assert item["properties"]["owner"] == {"type": ["string", "null"]}
    assert schema["properties"]["participants"] == {"type": "array", "items": {"type": "string"}}
    hypothesis = schema["properties"]["hypotheses"]["items"]
    assert hypothesis["required"] == ["hypothesis", "verdict", "excerpt"]
    assert hypothesis["properties"]["verdict"]["enum"] == ["confirmada", "descartada"]
    assert body["system"][0]["text"] == TRANSCRIPT_PROMPT
    content = "Incidente: 5xx no pedidos-api\n\nTranscrição:\nAna: vamos fazer rollback"
    assert body["messages"] == [{"role": "user", "content": content}]
    assert [usage.stage for usage in budget.spent] == ["transcript"]


@pytest.mark.parametrize(
    ("stages", "model"),
    [({"post_mortem": POST_MORTEM_SETTINGS}, "claude-opus-5-5"), ({}, "claude-sonnet-5-5")],
)
async def test_extract_transcript_without_its_own_stage_inherits_the_post_mortem_settings(
    stages: dict[str, ModelSettings], model: str
) -> None:
    api = ScriptedApi(ok(final(EXTRACTED)))

    await memory_model(api, **stages).extract_transcript("contexto", "Ana: oi", RecordingBudget())

    assert api.bodies[0]["model"] == model


async def test_extract_transcript_of_a_call_with_nothing_said() -> None:
    answer: dict[str, list[Any]] = {"items": [], "participants": [], "hypotheses": []}

    assert await extract(ScriptedApi(ok(final(answer)))) == TranscriptDraft((), (), ())


@pytest.mark.parametrize(
    "answer",
    [
        "não é json",
        {k: v for k, v in EXTRACTED.items() if k != "participants"},
        {**EXTRACTED, "items": [{**EXTRACTED["items"][0], "kind": "tarefa"}]},
        {**EXTRACTED, "hypotheses": [{"hypothesis": "x", "verdict": "talvez", "excerpt": ""}]},
        {**EXTRACTED, "summary": "o modelo não resume"},
    ],
)
async def test_extract_transcript_outside_the_schema_is_invalid(answer: object) -> None:
    with pytest.raises(InvalidAnalysisError, match="transcrição"):
        await extract(ScriptedApi(ok(final(answer))))


async def test_extract_transcript_api_error_refusal_and_budget() -> None:
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
    response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})

    with pytest.raises(LanguageModelError, match="529"):
        await extract(ScriptedApi(response, response, response))
    with pytest.raises(LanguageModelError, match="recusou"):
        await extract(ScriptedApi(ok(final("", stop_reason="refusal"))))
    with pytest.raises(BudgetExceededError):
        await extract(ScriptedApi(ok(final(EXTRACTED))), RecordingBudget(fail_at=1))


class TestClaudeTranscriptModelContract(TranscriptModelContract):
    @pytest.fixture
    def transcript_model(self) -> LanguageModel:
        return memory_model(lambda _request: ok(final(EXTRACTED)))

    @pytest.fixture
    def failing_transcript_model(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        return memory_model(
            lambda _request: httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        )


# O mesmo adapter pelo Amazon Bedrock: o SDK troca o host, põe o modelo no caminho e assina com
# SigV4; o corpo e a resposta são os da Messages API, então as suítes de contrato valem iguais.

BEDROCK = Bedrock("us-east-1")


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Credenciais falsas no ambiente, sem ler ~/.aws nem consultar a rede."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAFAKE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "segredo")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


def on_bedrock(handler: Handler, **stages: ModelSettings) -> ClaudeLanguageModel:
    return ClaudeLanguageModel(BEDROCK, SETTINGS, transport=httpx2.MockTransport(handler), **stages)


@pytest.mark.usefixtures("aws_credentials")
class TestClaudeOnBedrockContract(LanguageModelContract):
    @pytest.fixture
    def model(self) -> LanguageModel:
        return on_bedrock(
            ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(fixture("anthropic_final.json")))
        )

    @pytest.fixture
    def failing_model(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        return on_bedrock(ScriptedApi(response, response, response))

    @pytest.fixture
    def invalid_model(self) -> LanguageModel:
        return on_bedrock(ScriptedApi(ok(final("não é json"))))

    @pytest.fixture
    def verifying_model(self) -> LanguageModel:
        tool_use = ok(fixture("anthropic_tool_use.json"))
        return on_bedrock(
            ScriptedApi(tool_use, ok(final(verification()))), verification=VERIFICATION_SETTINGS
        )

    @pytest.fixture
    def failing_verifier(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        return on_bedrock(ScriptedApi(response, response, response))

    @pytest.fixture
    def invalid_verifier(self) -> LanguageModel:
        return on_bedrock(ScriptedApi(ok(final({"verdicts": "não é lista"}))))


@pytest.mark.usefixtures("aws_credentials")
class TestClaudeOnBedrockChatReplyContract(ChatReplyContract):
    @pytest.fixture
    def replying_model(self) -> LanguageModel:
        tool_use = ok(fixture("anthropic_tool_use.json"))
        answer = ok(final({"reply": "Os erros pararam às 10:20."}))
        return on_bedrock(ScriptedApi(tool_use, answer), chat=CHAT_SETTINGS)

    @pytest.fixture
    def failing_replier(self) -> LanguageModel:
        overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}
        response = httpx2.Response(529, json=overloaded, headers={"retry-after-ms": "1"})
        return on_bedrock(ScriptedApi(response, response, response))


@pytest.mark.usefixtures("aws_credentials")
async def test_bedrock_signs_the_call_and_sends_the_same_request_with_the_model_in_the_path() -> (
    None
):
    requests: list[httpx2.Request] = []
    api = ScriptedApi(ok(fixture("anthropic_tool_use.json")), ok(fixture("anthropic_final.json")))

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return api(request)

    await on_bedrock(handler).investigate(brief(), RecordingToolbox(), RecordingBudget())

    first = requests[0]
    assert first.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
    assert first.url.path == "/model/claude-sonnet-5-5/invoke"
    assert first.headers["authorization"].startswith("AWS4-HMAC-SHA256 ")
    assert "x-api-key" not in first.headers
    body = api.bodies[0]
    assert "model" not in body  # vai no caminho
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"][0]["cache_control"]["ttl"] == "1h"
    assert len(requests) == 2  # a volta com o resultado das ferramentas também vai ao Bedrock
