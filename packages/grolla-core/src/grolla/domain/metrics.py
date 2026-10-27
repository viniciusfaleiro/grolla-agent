"""Métricas de acerto do agente por período, só de respostas humanas e do log.

Contrato das métricas do agente: os corpos são do grolla-dominio.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from grolla.domain.post_mortem import AgentEvaluation


class Granularity(StrEnum):
    WEEK = "semana"
    MONTH = "mes"


@dataclass(frozen=True)
class Period:
    start: date
    end: date  # exclusivo
    label: str  # "out/2026" ou "semana de 20/10"


@dataclass(frozen=True)
class MetricsFacts:
    resolved: tuple[datetime, ...] = ()  # resolved_at dos incidentes resolvidos
    approved: tuple[tuple[datetime, AgentEvaluation], ...] = ()  # aprovação e a avaliação
    answers: tuple[tuple[datetime, bool], ...] = ()  # última resposta do "já vi isso"


@dataclass(frozen=True)
class PeriodMetrics:
    period: Period
    resolved: int
    approved: int
    evaluated: int  # com a hipótese principal respondida
    correct: int
    median_minutes: float | None
    seen_before_confirmed: int
    seen_before_rejected: int
    memory_helped: int


def periods(today: date, granularity: Granularity, count: int) -> tuple[Period, ...]:
    """Os `count` últimos períodos até o que contém `today`, do mais recente ao mais antigo."""
    raise NotImplementedError


def aggregate(facts: MetricsFacts, periods: Sequence[Period]) -> tuple[PeriodMetrics, ...]:
    raise NotImplementedError
