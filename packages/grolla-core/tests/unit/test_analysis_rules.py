"""Regras puras da análise: concluída e parcial."""

from datetime import timedelta
from uuid import uuid4

from grolla.domain.analysis import (
    PARTIAL_REASON_TEXT,
    Analysis,
    AnalysisDraft,
    AnalysisStatus,
    Evidence,
    Hypothesis,
    PartialReason,
    complete,
    partial,
)
from grolla.testing.builders import T0, make_analysis, make_draft, make_evaluations

DONE = T0 + timedelta(minutes=2)


def running() -> Analysis:
    return make_analysis(uuid4(), status=AnalysisStatus.RUNNING)


def test_complete_keeps_the_draft_with_hypotheses_by_confidence() -> None:
    weak = Hypothesis("Dependência lenta", 0.3, (Evidence("p99 do banco subiu", None),))
    strong = Hypothesis("Deploy ruim", 0.8, (Evidence("deploy às 09:58", None),))
    draft = AnalysisDraft("Deploy 2.4.1 elevou a taxa de erro", (weak, strong), ("Reverter",))
    pending = running()

    analysis = complete(pending, draft, list(make_evaluations()), DONE)

    assert analysis.status is AnalysisStatus.COMPLETED
    assert analysis.hypotheses == (strong, weak)
    assert analysis.summary == draft.summary
    assert analysis.plan == draft.plan
    assert analysis.evaluations == make_evaluations()
    assert analysis.finished_at == DONE
    assert analysis.started_at == pending.started_at
    assert analysis.partial_reason is None


def test_partial_keeps_the_evaluations_and_says_the_reason() -> None:
    analysis = partial(running(), PartialReason.TIMEOUT, make_evaluations(), DONE)

    assert analysis.status is AnalysisStatus.PARTIAL
    assert analysis.partial_reason is PartialReason.TIMEOUT
    assert analysis.summary == "Análise parcial: tempo esgotado."
    assert analysis.evaluations == make_evaluations()
    assert analysis.hypotheses == ()
    assert analysis.plan == ()
    assert analysis.finished_at == DONE


def test_every_partial_reason_has_a_text() -> None:
    assert set(PARTIAL_REASON_TEXT) == set(PartialReason)


def test_builder_draft_is_ordered_by_confidence() -> None:
    confidences = [h.confidence for h in make_draft(hypotheses=3).hypotheses]

    assert confidences == sorted(confidences, reverse=True)
