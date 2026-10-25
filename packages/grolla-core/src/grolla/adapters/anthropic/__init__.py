"""Anthropic (Claude): o modelo de linguagem da investigação, com laço de ferramentas manual.

A cada turno o modelo pede ferramentas do `Toolbox` (tool_use) ou devolve o resultado final,
que o structured outputs (`output_config.format`) obriga a seguir o schema do rascunho. O uso
de cada resposta passa pelo `TokenBudget` antes de qualquer outra coisa.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx2
from anthropic.types import (
    Message,
    MessageParam,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlock,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grolla.application.investigation import (
    STAGE_INVESTIGATION,
    SYSTEM_PROMPT,
    InvalidAnalysisError,
    InvestigationBrief,
    LanguageModelError,
    LlmUsage,
    TokenBudget,
    Toolbox,
    brief_text,
)
from grolla.domain.analysis import AnalysisDraft, Evidence, Hypothesis

# Poucas retentativas e timeout curto por requisição: a análise inteira tem o próprio timeout,
# e uma API fora do ar precisa virar análise parcial "LLM indisponível" em segundos.
MAX_RETRIES = 2
REQUEST_TIMEOUT_SECONDS = 60.0

# Cache de 1 h no prefixo estável (ferramentas e system): as análises de um mesmo dia o reusam.
_CACHE_1H: Any = {"type": "ephemeral", "ttl": "1h"}

# Schema do resultado final. Structured outputs não aceita limites numéricos: a confiança
# entre 0 e 1 é conferida na validação.
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
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "url": {"type": ["string", "null"]},
                            },
                            "required": ["text", "url"],
                            "additionalProperties": False,
                        },
                    },
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


@dataclass(frozen=True)
class ModelSettings:
    """Parâmetros da etapa, lidos do YAML do time (`llm.stages.<etapa>`)."""

    model: str
    max_tokens: int
    effort: str | None = None


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Evidence(_Strict):
    text: str
    url: str | None


class _Hypothesis(_Strict):
    title: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[_Evidence]


class _Draft(_Strict):
    summary: str
    hypotheses: list[_Hypothesis]
    plan: list[str]


class ClaudeLanguageModel:
    def __init__(
        self,
        api_key: str,
        settings: ModelSettings,
        base_url: str | None = None,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._settings = settings
        self._base_url = base_url  # só o e2e aponta para o fake
        self._transport = transport  # testes injetam um transporte fake

    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        http_client = (
            None
            if self._transport is None
            else anthropic.DefaultAsyncHttpxClient(transport=self._transport)
        )
        async with anthropic.AsyncAnthropic(
            api_key=self._api_key,
            base_url=self._base_url,
            http_client=http_client,
            max_retries=MAX_RETRIES,
            timeout=REQUEST_TIMEOUT_SECONDS,
        ) as client:
            try:
                return await self._loop(client, brief, toolbox, budget)
            except anthropic.APIError as error:
                raise LanguageModelError(f"API do modelo falhou: {_describe(error)}") from error

    async def _loop(
        self,
        client: anthropic.AsyncAnthropic,
        brief: InvestigationBrief,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> AnalysisDraft:
        tools = _tools(toolbox)
        messages: list[MessageParam] = [{"role": "user", "content": brief_text(brief)}]
        while True:
            response = await client.messages.create(
                model=self._settings.model,
                max_tokens=self._settings.max_tokens,
                system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": _CACHE_1H}],
                tools=tools,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config=self._output_config(),
            )
            budget.spend(_usage(response))
            if response.stop_reason != "tool_use":
                return _draft(response)
            calls = [block for block in response.content if isinstance(block, ToolUseBlock)]
            results = await asyncio.gather(*(_run(toolbox, call) for call in calls))
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": list(results)})

    def _output_config(self) -> Any:
        config: dict[str, object] = {"format": {"type": "json_schema", "schema": DRAFT_SCHEMA}}
        if self._settings.effort is not None:
            config["effort"] = self._settings.effort
        return config


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


async def _run(toolbox: Toolbox, call: ToolUseBlock) -> ToolResultBlockParam:
    text = await toolbox.call(call.name, call.input)
    return {"type": "tool_result", "tool_use_id": call.id, "content": text}


def _usage(response: Message) -> LlmUsage:
    usage = response.usage
    return LlmUsage(
        stage=STAGE_INVESTIGATION,
        model=response.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens or 0,
        cache_creation_tokens=usage.cache_creation_input_tokens or 0,
    )


def _draft(response: Message) -> AnalysisDraft:
    if response.stop_reason == "refusal":
        raise LanguageModelError("o modelo recusou a análise")
    if response.stop_reason != "end_turn":
        raise InvalidAnalysisError(f"resposta incompleta ({response.stop_reason})")
    text = "".join(block.text for block in response.content if block.type == "text")
    try:
        draft = _Draft.model_validate_json(text)
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


def _describe(error: anthropic.APIError) -> str:
    if isinstance(error, anthropic.APIStatusError):
        return f"HTTP {error.status_code}"
    return type(error).__name__
