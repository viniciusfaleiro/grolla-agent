"""Fakes dos ports da investigação: telemetria, histórico de mudanças e modelo de linguagem."""

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from grolla.application.investigation import (
    NO_REPOSITORY,
    STAGE_INVESTIGATION,
    InvestigationBrief,
    LlmUsage,
    Signal,
    TokenBudget,
    Toolbox,
    ToolResult,
    ToolUnavailableError,
)
from grolla.domain.analysis import Analysis, AnalysisDraft, VerificationDraft
from grolla.testing.builders import make_draft

FAKE_SOURCE_URL = "https://observabilidade.interno/consulta"


class FakeTelemetry:
    """Telemetria roteirizada por ferramenta.

    `results` troca a resposta de uma ferramenta (`query_metric`, `search_logs`, `find_traces`);
    as ferramentas em `failing` levantam `error` (ToolUnavailableError por padrão). Cada chamada
    fica em `calls`.
    """

    def __init__(
        self,
        results: Mapping[str, ToolResult] | None = None,
        failing: Iterable[str] = (),
        error: type[Exception] = ToolUnavailableError,
    ) -> None:
        self.results = dict(results or {})
        self.failing = set(failing)
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def query_metric(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> ToolResult:
        args = {"service": service, "signal": signal, "start": start, "end": end}
        return self._answer("query_metric", args, f"{signal} de {service} estável na janela")

    async def search_logs(
        self, service: str, text: str | None, start: datetime, end: datetime
    ) -> ToolResult:
        args = {"service": service, "text": text, "start": start, "end": end}
        return self._answer("search_logs", args, f"nenhum log de erro de {service} na janela")

    async def find_traces(
        self, service: str, only_errors: bool, start: datetime, end: datetime
    ) -> ToolResult:
        args = {"service": service, "only_errors": only_errors, "start": start, "end": end}
        return self._answer("find_traces", args, f"nenhum trace com erro de {service} na janela")

    def _answer(self, tool: str, args: dict[str, Any], default: str) -> ToolResult:
        self.calls.append((tool, args))
        if tool in self.failing:
            raise self.error(f"{tool} fora do ar no fake")
        return self.results.get(tool, ToolResult(default, FAKE_SOURCE_URL))


class FakeChangeHistory:
    """Histórico de mudanças roteirizado: `deploys` mapeia serviço → resumo dos deploys.

    Serviço fora do mapa responde NO_REPOSITORY; com `unavailable`, toda chamada levanta
    ToolUnavailableError. Cada chamada fica em `calls` como (serviço, since).
    """

    def __init__(self, deploys: Mapping[str, str] | None = None, unavailable: bool = False) -> None:
        self.deploys = dict(deploys or {})
        self.unavailable = unavailable
        self.calls: list[tuple[str, datetime]] = []

    async def recent_deploys(self, service: str, since: datetime) -> ToolResult:
        self.calls.append((service, since))
        if self.unavailable:
            raise ToolUnavailableError("histórico de mudanças fora do ar no fake")
        if service not in self.deploys:
            return ToolResult(NO_REPOSITORY, None)
        return ToolResult(self.deploys[service], f"https://pipelines.interno/{service}")


FAKE_MODEL = "modelo-fake"


def fake_usage(tokens: int = 1_000) -> LlmUsage:
    """Uso de uma resposta do modelo: `tokens` de entrada e um décimo disso de saída."""
    return LlmUsage(STAGE_INVESTIGATION, FAKE_MODEL, tokens, tokens // 10, 0, 0)


class ScriptedLanguageModel:
    """Modelo de linguagem roteirizado.

    Em `investigate`: espera `delay` segundos, faz as chamadas de ferramenta de `calls` pelo
    toolbox (gastando `usage` no orçamento a cada turno, inclusive o final) e então levanta
    `error`, se houver, ou devolve `draft`. Os briefs recebidos ficam em `briefs` e as
    respostas das ferramentas em `tool_answers`.
    """

    def __init__(
        self,
        draft: AnalysisDraft | None = None,
        *,
        calls: Sequence[tuple[str, Mapping[str, Any]]] = (),
        error: Exception | None = None,
        delay: float = 0.0,
        usage: LlmUsage | None = None,
    ) -> None:
        self.draft = draft or make_draft()
        self.calls = list(calls)
        self.error = error
        self.delay = delay
        self.usage = usage or fake_usage()
        self.briefs: list[InvestigationBrief] = []
        self.tool_answers: list[str] = []

    async def verify(
        self,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> VerificationDraft:
        raise NotImplementedError

    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        self.briefs.append(brief)
        if self.delay:
            await asyncio.sleep(self.delay)
        for name, arguments in self.calls:
            budget.spend(self.usage)
            self.tool_answers.append(await toolbox.call(name, arguments))
        budget.spend(self.usage)
        if self.error is not None:
            raise self.error
        return self.draft
