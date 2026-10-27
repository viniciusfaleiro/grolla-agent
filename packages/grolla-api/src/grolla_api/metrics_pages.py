"""Apresentação das métricas do agente e leitura do form da avaliação no post mortem."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from grolla.domain.metrics import Granularity, PeriodMetrics
from grolla.domain.post_mortem import AgentEvaluation, InvalidPostMortemError

GRANULARITY_LABELS = {Granularity.MONTH: "Por mês", Granularity.WEEK: "Por semana"}

EMPTY = "—"


def granularity_of(value: str) -> Granularity:
    """O `por` da query; valor desconhecido vale o padrão (mês), para um link velho não quebrar."""
    try:
        return Granularity(value)
    except ValueError:
        return Granularity.MONTH


def _number(value: float) -> str:
    return f"{value:.1f}".removesuffix(".0").replace(".", ",")


def hit_rate(metrics: PeriodMetrics) -> str:
    """'75% (3 de 4)'; '—' sem avaliação no período."""
    if not metrics.evaluated:
        return EMPTY
    rate = _number(100 * metrics.correct / metrics.evaluated)
    return f"{rate}% ({metrics.correct} de {metrics.evaluated})"


def median_minutes(metrics: PeriodMetrics) -> str:
    """'8 min' ou '7,5 min'; '—' sem minutos informados."""
    if metrics.median_minutes is None:
        return EMPTY
    return f"{_number(metrics.median_minutes)} min"


def yes_no_label(value: bool | None) -> str:
    return EMPTY if value is None else ("sim" if value else "não")


def _yes_no(form: Mapping[str, str], name: str) -> bool | None:
    value = form.get(name, "")
    if value not in ("sim", "nao", ""):
        raise InvalidPostMortemError("responda sim, não ou deixe sem resposta")
    return None if not value else value == "sim"


def _minutes(value: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise InvalidPostMortemError("minutos inválidos: use um número inteiro") from error


def evaluation_from(form: Mapping[str, str]) -> AgentEvaluation:
    """Os três campos do form (`hipotese_certa`, `minutos`, `memoria_ajudou`); vazio é sem
    resposta. Quem avaliou e quando o domínio preenche.
    """
    return AgentEvaluation(
        main_hypothesis_correct=_yes_no(form, "hipotese_certa"),
        minutes_to_right=_minutes(form.get("minutos", "")),
        memory_helped=_yes_no(form, "memoria_ajudou"),
    )


def is_empty(metrics: PeriodMetrics) -> bool:
    """Período sem nada: a tabela diz "sem incidentes" em vez de uma linha de zeros."""
    return not any(
        (
            metrics.resolved,
            metrics.approved,
            metrics.evaluated,
            metrics.seen_before_confirmed,
            metrics.seen_before_rejected,
            metrics.memory_helped,
            metrics.refused,
        )
    )


@dataclass(frozen=True)
class Bar:
    label: str
    value: int
    height: int  # % da maior barra


def resolved_bars(rows: Sequence[PeriodMetrics]) -> tuple[Bar, ...]:
    """Os resolvidos por período para o gráfico, do mais antigo ao mais recente."""
    top = max((r.resolved for r in rows), default=0) or 1
    return tuple(
        Bar(r.period.label, r.resolved, round(100 * r.resolved / top)) for r in reversed(rows)
    )
