"""Console de operação: conversas com o agente fora de incidente, com sessões salvas e gráficos
pedidos pelo modelo e desenhados com os dados reais da telemetria.

Contrato do console de operação: os corpos são do grolla-dominio.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from grolla.domain.analysis import Command, Evaluation
from grolla.domain.chat import ReplyStatus
from grolla.domain.safety import Scope

CHART_SIGNALS = ("taxa_de_erro", "latencia_p99", "vazao")
MAX_CHARTS = 2
MIN_CHART_MINUTES = 5
MAX_CHART_MINUTES = 1440
TITLE_LIMIT = 80


@dataclass(frozen=True)
class ChartRequest:
    """O gráfico que o modelo pede: o número nunca vem dele, vem da telemetria."""

    service: str
    signal: str  # um de CHART_SIGNALS
    minutes: int


@dataclass(frozen=True)
class ConsoleDraft:
    text: str
    charts: tuple[ChartRequest, ...] = ()
    scope: Scope = Scope.DOMAIN
    commands: tuple[Command, ...] = ()  # o passo a passo sugerido, já limpo por usable_commands


@dataclass(frozen=True)
class ConsoleSession:
    id: UUID
    title: str
    author: str
    created_at: datetime
    updated_at: datetime
    incident_id: UUID | None = None  # incidente aberto a partir da conversa


@dataclass(frozen=True)
class ConsoleMessage:
    id: int
    session_id: UUID
    author: str
    text: str
    at: datetime
    charts: tuple[ChartRequest, ...] = ()
    evaluations: tuple[Evaluation, ...] = ()
    reply_status: ReplyStatus | None = None  # só na pergunta
    reply_to: int | None = None  # só na resposta
    refusal: Scope | None = None  # só na resposta recusada
    commands: tuple[Command, ...] = ()  # só na resposta: o passo a passo sugerido


def session_title(question: str) -> str:
    """O título da sessão: a primeira pergunta, numa linha, cortada em TITLE_LIMIT (com "…")."""
    line = " ".join(question.split())
    if len(line) <= TITLE_LIMIT:
        return line
    return line[: TITLE_LIMIT - 1].rstrip() + "…"


def valid_charts(charts: Sequence[ChartRequest]) -> tuple[ChartRequest, ...]:
    """Até MAX_CHARTS gráficos com sinal conhecido e serviço não vazio, janela limitada entre
    MIN_CHART_MINUTES e MAX_CHART_MINUTES; o resto é descartado.
    """
    valid = [
        ChartRequest(
            chart.service.strip(),
            chart.signal,
            min(max(chart.minutes, MIN_CHART_MINUTES), MAX_CHART_MINUTES),
        )
        for chart in charts
        if chart.signal in CHART_SIGNALS and chart.service.strip()
    ]
    return tuple(valid[:MAX_CHARTS])
