"""Regras das métricas do agente: períodos e agregação."""

from datetime import UTC, date, datetime, timedelta, timezone

from grolla.domain.metrics import (
    Granularity,
    MetricsFacts,
    Period,
    PeriodMetrics,
    aggregate,
    periods,
)
from grolla.domain.post_mortem import AgentEvaluation

TODAY = date(2026, 10, 27)  # uma terça
OCT = Period(date(2026, 10, 1), date(2026, 11, 1), "out/2026")
SEP = Period(date(2026, 9, 1), date(2026, 10, 1), "set/2026")


def at(day: date, hour: int = 12) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def right(minutes: int | None, *, helped: bool | None = None) -> AgentEvaluation:
    return AgentEvaluation(
        main_hypothesis_correct=True, minutes_to_right=minutes, memory_helped=helped
    )


def test_months_are_the_current_and_the_previous_across_the_year() -> None:
    found = periods(TODAY, Granularity.MONTH, 11)

    assert found[:2] == (OCT, SEP)
    assert found[-2:] == (
        Period(date(2026, 1, 1), date(2026, 2, 1), "jan/2026"),
        Period(date(2025, 12, 1), date(2026, 1, 1), "dez/2025"),
    )
    assert [p.end for p in found[1:]] == [p.start for p in found[:-1]]


def test_weeks_start_on_monday() -> None:
    found = periods(TODAY, Granularity.WEEK, 2)

    assert found == (
        Period(date(2026, 10, 26), date(2026, 11, 2), "semana de 26/10"),
        Period(date(2026, 10, 19), date(2026, 10, 26), "semana de 19/10"),
    )
    assert periods(date(2026, 10, 26), Granularity.WEEK, 1) == found[:1]


def test_aggregate_counts_each_period_and_the_median_of_right_hypotheses() -> None:
    day = date(2026, 10, 5)
    facts = MetricsFacts(
        resolved=(at(day), at(day), at(date(2026, 9, 30))),
        approved=(
            (at(day), right(8, helped=True)),
            (at(day), right(4)),
            (at(day), right(None, helped=True)),
            (at(day), AgentEvaluation(main_hypothesis_correct=False, minutes_to_right=60)),
            (at(day), AgentEvaluation(memory_helped=False)),
            (at(date(2026, 9, 2)), right(30)),
        ),
        answers=((at(day), True), (at(day), True), (at(day), False)),
    )

    october, september = aggregate(facts, [OCT, SEP])

    assert october == PeriodMetrics(
        period=OCT,
        resolved=2,
        approved=5,
        evaluated=4,
        correct=3,
        median_minutes=6.0,
        seen_before_confirmed=2,
        seen_before_rejected=1,
        memory_helped=2,
    )
    assert (september.resolved, september.approved, september.median_minutes) == (1, 1, 30.0)


def test_period_without_data_is_zero_and_without_median() -> None:
    (empty,) = aggregate(MetricsFacts(), [OCT])

    assert empty == PeriodMetrics(OCT, 0, 0, 0, 0, None, 0, 0, 0)


def test_facts_are_placed_by_their_utc_day() -> None:
    late_night = datetime(2026, 9, 30, 22, tzinfo=timezone(timedelta(hours=-3)))  # 1/10 em UTC

    october, september = aggregate(MetricsFacts(resolved=(late_night,)), [OCT, SEP])

    assert (october.resolved, september.resolved) == (1, 0)


def test_refusals_are_counted_by_period() -> None:
    day = date(2026, 10, 5)
    facts = MetricsFacts(refusals=(at(day), at(day, 13), at(date(2026, 9, 30))))

    october, september = aggregate(facts, [OCT, SEP])

    assert (october.refused, september.refused) == (2, 1)
