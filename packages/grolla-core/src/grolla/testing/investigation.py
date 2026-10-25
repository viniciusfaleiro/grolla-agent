"""Fakes dos ports da investigação: telemetria, histórico de mudanças, nuvem e modelo de
linguagem.
"""

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from grolla.application.investigation import (
    NO_CLOUD_RESOURCES,
    NO_REPOSITORY,
    STAGE_INVESTIGATION,
    STAGE_VERIFICATION,
    InvestigationBrief,
    LlmUsage,
    Signal,
    TokenBudget,
    Toolbox,
    ToolResult,
    ToolUnavailableError,
)
from grolla.domain.analysis import Analysis, AnalysisDraft, VerificationDraft
from grolla.testing.builders import make_draft, make_verification_draft

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


CLOUD_TOOLS = ("resources", "changes", "alarms")


class FakeCloudInventory:
    """Nuvem roteirizada: `resources`, `changes` e `alarms` mapeiam serviço → resumo.

    Serviço sem recursos no mapa responde NO_CLOUD_RESOURCES; sem mudanças ou alarmes no mapa,
    responde que não há. As consultas em `failing` (nomes de `CLOUD_TOOLS`) levantam
    ToolUnavailableError. Cada chamada fica em `calls` como (consulta, serviço).
    """

    def __init__(
        self,
        resources: Mapping[str, str] | None = None,
        changes: Mapping[str, str] | None = None,
        alarms: Mapping[str, str] | None = None,
        failing: Iterable[str] = (),
    ) -> None:
        self.answers = {
            "resources": dict(resources or {}),
            "changes": dict(changes or {}),
            "alarms": dict(alarms or {}),
        }
        self.failing = set(failing)
        self.calls: list[tuple[str, str]] = []
        self.since: list[datetime] = []

    async def resources(self, service: str) -> ToolResult:
        return self._answer("resources", service, NO_CLOUD_RESOURCES)

    async def changes(self, service: str, since: datetime) -> ToolResult:
        self.since.append(since)
        return self._answer("changes", service, f"nenhuma mudança nos recursos de {service}")

    async def alarms(self, service: str) -> ToolResult:
        return self._answer("alarms", service, f"não há alarmes ativos para {service}")

    def _answer(self, tool: str, service: str, default: str) -> ToolResult:
        self.calls.append((tool, service))
        if tool in self.failing:
            raise ToolUnavailableError(f"{tool} da nuvem fora do ar no fake")
        summary = self.answers[tool].get(service)
        if summary is None:
            return ToolResult(default, None)
        return ToolResult(summary, f"https://console.nuvem.interno/{service}")


FAKE_MODEL = "modelo-fake"


def fake_usage(tokens: int = 1_000, stage: str = STAGE_INVESTIGATION) -> LlmUsage:
    """Uso de uma resposta do modelo: `tokens` de entrada e um décimo disso de saída."""
    return LlmUsage(stage, FAKE_MODEL, tokens, tokens // 10, 0, 0)


ToolCalls = Sequence[tuple[str, Mapping[str, Any]]]


class ScriptedLanguageModel:
    """Modelo de linguagem roteirizado.

    Em `investigate`: espera `delay` segundos, faz as chamadas de ferramenta de `calls` pelo
    toolbox (gastando `usage` no orçamento a cada turno, inclusive o final) e então levanta
    `error`, se houver, ou devolve `draft`. Os briefs recebidos ficam em `briefs` e as
    respostas das ferramentas em `tool_answers`.

    Em `verify`, o mesmo roteiro com `verification_calls`, `verification_error`,
    `verification_delay` e `verification` (o rascunho devolvido), gastando o uso com a etapa
    `verification`. As análises recebidas ficam em `verified_analyses`.
    """

    def __init__(  # noqa: PLR0913 — o roteiro de cada fase é ajustável no teste
        self,
        draft: AnalysisDraft | None = None,
        *,
        calls: ToolCalls = (),
        error: Exception | None = None,
        delay: float = 0.0,
        usage: LlmUsage | None = None,
        verification: VerificationDraft | None = None,
        verification_calls: ToolCalls = (),
        verification_error: Exception | None = None,
        verification_delay: float = 0.0,
    ) -> None:
        self.draft = draft or make_draft()
        self.calls = list(calls)
        self.error = error
        self.delay = delay
        self.usage = usage or fake_usage()
        self.verification = verification or make_verification_draft()
        self.verification_calls = list(verification_calls)
        self.verification_error = verification_error
        self.verification_delay = verification_delay
        self.briefs: list[InvestigationBrief] = []
        self.verified_analyses: list[Analysis] = []
        self.tool_answers: list[str] = []

    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        self.briefs.append(brief)
        await self._play(self.delay, self.calls, self.usage, toolbox, budget)
        if self.error is not None:
            raise self.error
        return self.draft

    async def verify(
        self,
        brief: InvestigationBrief,
        analysis: Analysis,
        toolbox: Toolbox,
        budget: TokenBudget,
    ) -> VerificationDraft:
        self.briefs.append(brief)
        self.verified_analyses.append(analysis)
        usage = replace(self.usage, stage=STAGE_VERIFICATION)
        await self._play(self.verification_delay, self.verification_calls, usage, toolbox, budget)
        if self.verification_error is not None:
            raise self.verification_error
        return self.verification

    async def _play(
        self, delay: float, calls: ToolCalls, usage: LlmUsage, toolbox: Toolbox, budget: TokenBudget
    ) -> None:
        if delay:
            await asyncio.sleep(delay)
        for name, arguments in calls:
            budget.spend(usage)
            self.tool_answers.append(await toolbox.call(name, arguments))
        budget.spend(usage)
