"""Os casos e a correção dos evals, com o modelo roteirizado (sem rede, sem custo)."""

import pytest
from grolla.domain.analysis import AnalysisDraft, Evidence, Hypothesis
from grolla.testing.investigation import ScriptedLanguageModel

from evals.run import CASES, Case, grade, run, toolbox

DEPLOY, SLOW, UNCLEAR = CASES


def draft(*hypotheses: tuple[str, float, str]) -> AnalysisDraft:
    return AnalysisDraft(
        summary="resumo",
        hypotheses=tuple(
            Hypothesis(title, confidence, (Evidence(text, None),))
            for title, confidence, text in hypotheses
        ),
        plan=("avaliar",),
    )


@pytest.mark.parametrize(
    ("case", "analysis"),
    [
        (DEPLOY, draft(("Regressão no deploy 2.4.1", 0.8, "build às 09:58"))),
        (SLOW, draft(("Banco", 0.2, "p99 ok"), ("Dependência lenta", 0.7, "antifraude-api"))),
        (UNCLEAR, draft(("Sazonalidade", 0.3, "queda gradual"))),
    ],
)
def test_expected_hypothesis_passes(case: Case, analysis: AnalysisDraft) -> None:
    assert grade(case, analysis) is None


@pytest.mark.parametrize(
    ("case", "analysis", "reason"),
    [
        (DEPLOY, draft(("Banco lento", 0.6, "p99 do banco")), "não cita"),
        (SLOW, draft(), "nenhuma hipótese"),
        (UNCLEAR, draft(("Deploy ruim", 0.9, "certeza")), "sem causa clara"),
    ],
)
def test_wrong_hypothesis_fails(case: Case, analysis: AnalysisDraft, reason: str) -> None:
    failure = grade(case, analysis)

    assert failure is not None
    assert reason in failure


async def test_case_toolbox_answers_with_the_script() -> None:
    box = toolbox(DEPLOY)

    deploys = await box.call("recent_deploys", {"service": DEPLOY.service})
    metric = await box.call("query_metric", {"service": DEPLOY.service, "signal": "taxa_de_erro"})

    assert "2.4.1" in deploys
    assert "8%" in metric


async def test_run_grades_every_case() -> None:
    model = ScriptedLanguageModel(draft(("Deploy 2.4.1", 0.9, "build")))

    outcomes = await run(model)

    assert [case.name for case, _ in outcomes] == [case.name for case in CASES]
    assert [failure is None for _, failure in outcomes] == [True, False, False]
    assert [brief.incident.services for brief in model.briefs] == [
        (case.service,) for case in CASES
    ]
