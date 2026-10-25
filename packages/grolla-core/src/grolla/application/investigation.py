"""Ferramentas neutras da investigação, o orçamento de tokens e o port do modelo de linguagem.

O modelo de linguagem só investiga por estas ferramentas, que têm nome e parâmetros neutros e
são implementadas sobre os ports `Telemetry` e `ChangeHistory`. Trocar o fornecedor não muda
o prompt, os evals nem os cenários.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

from grolla.application.clock import Clock
from grolla.domain.analysis import AnalysisDraft, Evaluation
from grolla.domain.incidents import Alert, Incident

STAGE_INVESTIGATION = "investigation"
UNAVAILABLE = "não foi possível consultar"

Signal = Literal["taxa_de_erro", "latencia_p99", "vazao"]


class ToolUnavailableError(Exception):
    """A fonte da ferramenta falhou ou não respondeu a tempo."""


@dataclass(frozen=True)
class ToolResult:
    summary: str  # texto compacto para o modelo, em português
    url: str | None  # link para a origem


class Telemetry(Protocol):
    """Métricas, logs e traces dos serviços."""

    async def query_metric(
        self, service: str, signal: Signal, start: datetime, end: datetime
    ) -> ToolResult:
        """Série de um sinal do serviço na janela; levanta ToolUnavailableError."""
        ...

    async def search_logs(
        self, service: str, text: str | None, start: datetime, end: datetime
    ) -> ToolResult:
        """Logs de erro do serviço, filtrados por texto; levanta ToolUnavailableError."""
        ...

    async def find_traces(
        self, service: str, only_errors: bool, start: datetime, end: datetime
    ) -> ToolResult:
        """Traces com erro ou lentos do serviço; levanta ToolUnavailableError."""
        ...


class ChangeHistory(Protocol):
    """Deploys e execuções de pipeline."""

    async def recent_deploys(self, service: str, since: datetime) -> ToolResult:
        """Deploys do repositório do serviço desde `since`; levanta ToolUnavailableError.

        Serviço sem repositório conhecido não é erro: o resumo diz isso.
        """
        ...


@dataclass(frozen=True)
class ToolSpec:
    """Definição neutra de uma ferramenta, que o adapter do modelo traduz para o fornecedor."""

    name: str
    description: str
    input_schema: Mapping[str, Any]  # JSON Schema


class Toolbox:
    """Liga as ferramentas neutras aos ports e registra cada chamada como avaliação."""

    def __init__(
        self, telemetry: Telemetry, changes: ChangeHistory, clock: Clock, lookback: timedelta
    ) -> None:
        self._telemetry = telemetry
        self._changes = changes
        self._clock = clock
        self._lookback = lookback
        self._evaluations: list[Evaluation] = []

    def specs(self) -> list[ToolSpec]:
        raise NotImplementedError

    async def call(self, name: str, arguments: Mapping[str, Any]) -> str:
        """Executa a ferramenta e devolve o texto para o modelo; nunca levanta."""
        raise NotImplementedError

    @property
    def evaluations(self) -> tuple[Evaluation, ...]:
        return tuple(self._evaluations)


@dataclass(frozen=True)
class LlmUsage:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int


class BudgetExceededError(Exception):
    """A análise atingiu o limite de tokens configurado."""


class TokenBudget:
    """Orçamento de tokens de uma análise; o adapter do modelo chama `spend` a cada resposta."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._usages: list[LlmUsage] = []

    def spend(self, usage: LlmUsage) -> None:
        """Registra o uso; levanta BudgetExceededError quando o total atinge o limite."""
        raise NotImplementedError

    @property
    def usages(self) -> tuple[LlmUsage, ...]:
        return tuple(self._usages)


@dataclass(frozen=True)
class InvestigationBrief:
    incident: Incident
    alerts: tuple[Alert, ...]
    now: datetime


class LanguageModelError(Exception):
    """O modelo de linguagem falhou: API fora, recusa ou erro do modelo."""


class InvalidAnalysisError(Exception):
    """O modelo devolveu um resultado fora do formato esperado."""


class LanguageModel(Protocol):
    async def investigate(
        self, brief: InvestigationBrief, toolbox: Toolbox, budget: TokenBudget
    ) -> AnalysisDraft:
        """Investiga usando só as ferramentas do toolbox; levanta LanguageModelError,
        InvalidAnalysisError ou BudgetExceededError.
        """
        ...


SYSTEM_PROMPT = ""  # roteiro curto da análise inicial, em português (grolla-dominio escreve)


def brief_text(brief: InvestigationBrief) -> str:
    """Mensagem inicial ao modelo: o incidente, os alertas e a janela da investigação."""
    raise NotImplementedError
