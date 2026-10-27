"""Console de operação: conversas com o agente fora de incidente, com sessões salvas e gráficos
pedidos pelo modelo e desenhados com os dados reais da telemetria.

Contrato do console de operação: os corpos são do grolla-dominio.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from grolla.domain.analysis import Evaluation
from grolla.domain.chat import ReplyStatus

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


def session_title(question: str) -> str:
    """O título da sessão: a primeira pergunta, numa linha, cortada em TITLE_LIMIT."""
    raise NotImplementedError


def valid_charts(charts: Sequence[ChartRequest]) -> tuple[ChartRequest, ...]:
    """Até MAX_CHARTS gráficos com sinal conhecido e serviço não vazio, janela limitada entre
    MIN_CHART_MINUTES e MAX_CHART_MINUTES.
    """
    raise NotImplementedError
