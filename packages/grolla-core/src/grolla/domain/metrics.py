"""Métricas de acerto do agente por período, só de respostas humanas e do log.

Os períodos são em UTC: a semana começa na segunda e o mês no dia 1.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from statistics import median

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
    refusals: tuple[datetime, ...] = ()  # respostas recusadas do chat e do console


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
    refused: int = 0


MONTHS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
WEEK = timedelta(days=7)
MONTHS_PER_YEAR = 12


def _first_day(months: int) -> date:
    """O dia 1 do mês contado em meses desde o ano zero."""
    year, month = divmod(months, MONTHS_PER_YEAR)
    return date(year, month + 1, 1)


def _month(months: int) -> Period:
    start = _first_day(months)
    return Period(start, _first_day(months + 1), f"{MONTHS[start.month - 1]}/{start.year}")


def periods(today: date, granularity: Granularity, count: int) -> tuple[Period, ...]:
    """Os `count` últimos períodos até o que contém `today`, do mais recente ao mais antigo."""
    if granularity is Granularity.MONTH:
        current = today.year * MONTHS_PER_YEAR + today.month - 1
        return tuple(_month(current - n) for n in range(count))
    monday = today - timedelta(days=today.weekday())
    starts = (monday - n * WEEK for n in range(count))
    return tuple(Period(s, s + WEEK, f"semana de {s:%d/%m}") for s in starts)


def _within(at: datetime, period: Period) -> bool:
    return period.start <= at.astimezone(UTC).date() < period.end


def aggregate(facts: MetricsFacts, periods: Sequence[Period]) -> tuple[PeriodMetrics, ...]:
    """Os números de cada período; a mediana usa só os minutos das avaliações com a hipótese
    principal certa, e fica None sem nenhuma.
    """
    result: list[PeriodMetrics] = []
    for period in periods:
        evaluations = [e for at, e in facts.approved if _within(at, period)]
        answered = [e for e in evaluations if e.main_hypothesis_correct is not None]
        right = [e for e in answered if e.main_hypothesis_correct]
        minutes = [e.minutes_to_right for e in right if e.minutes_to_right is not None]
        answers = [same for at, same in facts.answers if _within(at, period)]
        result.append(
            PeriodMetrics(
                period=period,
                resolved=sum(1 for at in facts.resolved if _within(at, period)),
                approved=len(evaluations),
                evaluated=len(answered),
                correct=len(right),
                median_minutes=float(median(minutes)) if minutes else None,
                seen_before_confirmed=sum(1 for same in answers if same),
                seen_before_rejected=sum(1 for same in answers if not same),
                memory_helped=sum(1 for e in evaluations if e.memory_helped),
            )
        )
    return tuple(result)
