"""Anthropic (Claude): o modelo de linguagem da investigação, com laço de ferramentas manual.

A cada turno o modelo pede ferramentas do `Toolbox` (tool_use) ou devolve o resultado final,
que o structured outputs (`output_config.format`) obriga a seguir o schema do rascunho. O uso
de cada resposta passa pelo `TokenBudget` antes de qualquer outra coisa.

As três chamadas usam o mesmo laço: a análise inicial (`investigate`, etapa `investigation`), a
validação das hipóteses até a causa raiz (`verify`, etapa `verification`, com modelo e
`effort` próprios) e a resposta no chat do war room (`reply`, etapa `warroom_chat`, idem, com o
resultado no schema `{"reply": string}`). Na validação, um plano com passo de investigação
ganha uma única mensagem de correção; se vier de novo, o resultado é inválido.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx2
from anthropic import Omit, omit
from anthropic.types import (
    Message,
    MessageParam,
    TextBlockParam,
    ToolChoiceParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlock,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grolla.application.investigation import (
    CHAT_PROMPT,
    STAGE_INVESTIGATION,
    STAGE_VERIFICATION,
    STAGE_WARROOM_CHAT,
    SYSTEM_PROMPT,
    VERIFICATION_PROMPT,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModelError,
    LearningBrief,
    LlmUsage,
    ServiceStudyBrief,
    SynthesisBrief,
    TokenBudget,
    Toolbox,
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
    NotConcreteError,
    Verdict,
    VerificationDraft,
    check_plan,
)
from grolla.domain.chat import ChatDraft
from grolla.domain.memory import DossierDraft, LearningDraft, SynthesisDraft

# Poucas retentativas e timeout curto por requisição: a análise inteira tem o próprio timeout,
# e uma API fora do ar precisa virar análise parcial "LLM indisponível" em segundos.
MAX_RETRIES = 2
REQUEST_TIMEOUT_SECONDS = 60.0

# Cada volta reenvia a conversa inteira e custa um pouco mais que a anterior: com menos de duas
# voltas no orçamento, o chat fecha a resposta com o que já consultou.
WRAP_UP_TURNS = 2
WRAP_UP = (
    "O orçamento desta resposta está acabando: responda agora, no formato pedido, com o que já "
    "consultou e diga em uma linha o que não deu para verificar."
)

# Cache de 1 h no prefixo estável (ferramentas e system): as análises de um mesmo dia o reusam.
_CACHE_1H: Any = {"type": "ephemeral", "ttl": "1h"}

_EVIDENCE_SCHEMA: dict[str, object] = {
    "type": "array",
    "minItems": 1,  # toda hipótese e todo veredito citam ao menos uma evidência
    "items": {
        "type": "object",
        "properties": {"text": {"type": "string"}, "url": {"type": ["string", "null"]}},
        "required": ["text", "url"],
        "additionalProperties": False,
    },
}

# Schema do resultado final. Structured outputs não aceita limites numéricos (só minItems 0
# ou 1): a confiança entre 0 e 1 é conferida na validação.
DRAFT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "confidence": {"type": "number"},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": ["title", "confidence", "evidence"],
                "additionalProperties": False,
            },
        },
        "plan": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "hypotheses", "plan"],
    "additionalProperties": False,
}

# Schema da validação: um veredito por hipótese, a causa raiz (null sem confirmação), o plano de
# ações concretas e o que faltou. O plano concreto é conferido depois, por `check_plan`.
VERIFICATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis": {"type": "string"},
                    "verdict": {"type": "string", "enum": [v.value for v in Verdict]},
                    "reasoning": {"type": "string"},
                    "evidence": _EVIDENCE_SCHEMA,
                },
                "required": ["hypothesis", "verdict", "reasoning", "evidence"],
                "additionalProperties": False,
            },
        },
        "root_cause": {"type": ["string", "null"]},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "target": {"type": "string"},
                    "expected": {"type": "string"},
                    "link": {"type": ["string", "null"]},
                },
                "required": ["action", "target", "expected", "link"],
                "additionalProperties": False,
            },
        },
        "missing": {"type": "string"},
    },
    "required": ["verdicts", "root_cause", "actions", "missing"],
    "additionalProperties": False,
}

# Schema da resposta do chat: só o texto, que vai para o chat como está.
CHAT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"reply": {"type": "string"}},
    "required": ["reply"],
    "additionalProperties": False,
}

NOT_CONCRETE = (
    "O plano tem passo de investigação: troque por ações concretas, ou deixe sem plano se a "
    "causa não foi confirmada."
)


@dataclass(frozen=True)
class ModelSettings:
    """Parâmetros da etapa, lidos do YAML do time (`llm.stages.<etapa>`)."""

    model: str
    max_tokens: int
    effort: str | None = None


@dataclass(frozen=True)
class _Phase:
    """O que muda entre a análise inicial e a validação."""

    system: str
    schema: dict[str, object]
    settings: ModelSettings
    stage: str
    wrap_up: bool = False  # chat: com o orçamento acabando, responde com o que já consultou

    def output_config(self) -> Any:
        config: dict[str, object] = {"format": {"type": "json_schema", "schema": self.schema}}
        if self.settings.effort is not None:
            config["effort"] = self.settings.effort
        return config


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Evidence(_Strict):
    text: str
    url: str | None


class _Hypothesis(_Strict):
    title: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[_Evidence] = Field(min_length=1)


class _Draft(_Strict):
    summary: str
    hypotheses: list[_Hypothesis]
    plan: list[str]


class _Verdict(_Strict):
    hypothesis: str
    verdict: Verdict
    reasoning: str
    evidence: list[_Evidence] = Field(min_length=1)


class _Action(_Strict):
    action: str
    target: str
    expected: str
    link: str | None


class _Verification(_Strict):
    verdicts: list[_Verdict]
    root_cause: str | None
    actions: list[_Action]
    missing: str


class _Reply(_Strict):
    reply: str


class ClaudeLanguageModel:
    def __init__(  # noqa: PLR0913 — modelos por etapa e conexão vêm da configuração
        self,
        api_key: str,
        settings: ModelSettings,
        *,
        base_url: str | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
        verification: ModelSettings | None = None,
        chat: ModelSettings | None = None,
        learning: ModelSettings | None = None,
        study: ModelSettings | None = None,
    ) -> None:
        self._api_key = api_key
        self._settings = settings
        self._verification = verification or settings  # etapa `verification` herda a da análise
        self._chat = chat or settings  # etapa `warroom_chat` também herda
        self._learning = learning or settings  # etapas da memória também herdam
        self._study = study or settings
        self._base_url = base_url  # só o e2e aponta para o fake
        self._transport = transport  # testes injetam um transporte fake

    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        phase = _Phase(SYSTEM_PROMPT, DRAFT_SCHEMA, self._settings, STAGE_INVESTIGATION)
        messages: list[MessageParam] = [{"role": "user", "content": brief_text(brief)}]
        async with self._client() as client:
            try:
                return _draft(await _loop(client, phase, messages, toolbox, budget))
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error

    async def reply(
        self,
        brief: InvestigationBrief,
        context: str,
        question: str,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> ChatDraft:
        del brief  # o contexto montado pela application já traz o incidente
        phase = _Phase(CHAT_PROMPT, CHAT_SCHEMA, self._chat, STAGE_WARROOM_CHAT, wrap_up=True)
        messages: list[MessageParam] = [{"role": "user", "content": chat_text(context, question)}]
        async with self._client() as client:
            try:
                return _chat_draft(await _loop(client, phase, messages, toolbox, budget))
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error

    async def verify(
        self,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> VerificationDraft:
        phase = _Phase(
            VERIFICATION_PROMPT, VERIFICATION_SCHEMA, self._verification, STAGE_VERIFICATION
        )
        text = verification_text(brief, analysis)
        messages: list[MessageParam] = [{"role": "user", "content": text}]
        async with self._client() as client:
            try:
                return await _concrete(client, phase, messages, toolbox, budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error

    async def learn(self, brief: LearningBrief, budget: TokenBudget) -> LearningDraft:
        raise NotImplementedError  # contrato da memória viva: implementado pelo grolla-integracoes

    async def study_service(
        self, brief: ServiceStudyBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> DossierDraft:
        raise NotImplementedError

    async def synthesize(self, brief: SynthesisBrief, budget: TokenBudget) -> SynthesisDraft:
        raise NotImplementedError

    def _client(self) -> anthropic.AsyncAnthropic:
        http_client = (
            None
            if self._transport is None
            else anthropic.DefaultAsyncHttpxClient(transport=self._transport)
        )
        return anthropic.AsyncAnthropic(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=http_client,
            max_retries=MAX_RETRIES,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )


async def _loop(
    client: anthropic.AsyncAnthropic,
    phase: _Phase,
    messages: list[MessageParam],
    toolbox: Toolbox,
    budget: TokenBudget,
) -> Message:
    """Roda as ferramentas pedidas até a resposta final e a devolve; estende `messages`.

    Com `wrap_up`, quando o que sobra do orçamento não paga mais uma volta, a última chamada vai
    sem ferramentas: o modelo responde com o que já consultou em vez de estourar o orçamento.
    """
    tools = _tools(toolbox)
    wrapping_up = False
    while True:
        choice: ToolChoiceParam | Omit = {"type": "none"} if wrapping_up else omit
        response = await client.messages.create(
            model=phase.settings.model,
            max_tokens=phase.settings.max_tokens,
            system=[{"type": "text", "text": phase.system, "cache_control": _CACHE_1H}],
            tools=tools,
            tool_choice=choice,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config=phase.output_config(),
        )
        usage = _usage(response, phase.stage)
        budget.spend(usage)
        if response.stop_reason != "tool_use" or wrapping_up:
            return response
        calls = [block for block in response.content if isinstance(block, ToolUseBlock)]
        results: list[ToolResultBlockParam | TextBlockParam] = list(
            await asyncio.gather(*(_run(toolbox, call) for call in calls))
        )
        if phase.wrap_up and budget.remaining < WRAP_UP_TURNS * _tokens(usage):
            results.append({"type": "text", "text": WRAP_UP})
            wrapping_up = True
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})


async def _concrete(
    client: anthropic.AsyncAnthropic,
    phase: _Phase,
    messages: list[MessageParam],
    toolbox: Toolbox,
    budget: TokenBudget,
) -> VerificationDraft:
    """Validação com plano concreto: uma correção só, na mesma conversa e no mesmo orçamento."""
    response = await _loop(client, phase, messages, toolbox, budget)
    draft = _verification(response)
    try:
        check_plan(draft)
    except NotConcreteError:
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": NOT_CONCRETE})
        draft = _verification(await _loop(client, phase, messages, toolbox, budget))
        try:
            check_plan(draft)
        except NotConcreteError as error:
            raise InvalidAnalysisError(f"plano não concreto: {error}") from error
    return draft


def _tools(toolbox: Toolbox) -> list[ToolParam]:
    tools: list[ToolParam] = [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": dict(spec.input_schema),
        }
        for spec in toolbox.specs()
    ]
    if tools:
        tools[-1]["cache_control"] = _CACHE_1H
    return tools


def _tokens(usage: LlmUsage) -> int:
    return (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_creation_tokens
    )


async def _run(toolbox: Toolbox, call: ToolUseBlock) -> ToolResultBlockParam:
    text = await toolbox.call(call.name, call.input)
    return {"type": "tool_result", "tool_use_id": call.id, "content": text}


def _usage(response: Message, stage: str) -> LlmUsage:
    usage = response.usage
    return LlmUsage(
        stage=stage,
        model=response.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_creation_tokens=usage.cache_creation_input_tokens or 0,
    )


def _final_text(response: Message) -> str:
    if response.stop_reason == "refusal":
        raise LanguageModelError("o modelo recusou a análise")
    if response.stop_reason != "end_turn":
        raise InvalidAnalysisError(f"resposta incompleta ({response.stop_reason})")
    return "".join(block.text for block in response.content if block.type == "text")


def _draft(response: Message) -> AnalysisDraft:
    try:
        draft = _Draft.model_validate_json(_final_text(response))
    except ValidationError as error:
        raise InvalidAnalysisError("resultado fora do formato da análise") from error
    return AnalysisDraft(
        summary=draft.summary,
        hypotheses=tuple(
            Hypothesis(
                title=item.title,
                confidence=item.confidence,
                evidence=tuple(Evidence(text=e.text, url=e.url) for e in item.evidence),
            )
            for item in draft.hypotheses
        ),
        plan=tuple(draft.plan),
    )


def _verification(response: Message) -> VerificationDraft:
    try:
        draft = _Verification.model_validate_json(_final_text(response))
    except ValidationError as error:
        raise InvalidAnalysisError("resultado fora do formato da validação") from error
    return VerificationDraft(
        verdicts=tuple(
            HypothesisVerdict(
                hypothesis=item.hypothesis,
                verdict=item.verdict,
                reasoning=item.reasoning,
                evidence=tuple(Evidence(text=e.text, url=e.url) for e in item.evidence),
            )
            for item in draft.verdicts
        ),
        root_cause=draft.root_cause or None,  # texto vazio também é "não confirmada"
        actions=tuple(Action(a.action, a.target, a.expected, a.link) for a in draft.actions),
        missing=draft.missing,
    )


def chat_text(context: str, question: str) -> str:
    """Mensagem do chat: o contexto do incidente e, por último, a pergunta."""
    return f"{context}\n\nPergunta: {question}"


def _chat_draft(response: Message) -> ChatDraft:
    try:
        draft = _Reply.model_validate_json(_final_text(response))
    except ValidationError as error:
        raise InvalidAnalysisError("resultado fora do formato da resposta do chat") from error
    if not draft.reply.strip():
        raise InvalidAnalysisError("resposta do chat vazia")
    return ChatDraft(text=draft.reply.strip())


def _describe(error: anthropic.APIError) -> str:
    if isinstance(error, anthropic.APIStatusError):
        return f"HTTP {error.status_code}"
    return type(error).__name__
