"""Os casos e a correção dos evals, com o modelo roteirizado (sem rede, sem custo)."""

from dataclasses import replace

import pytest
from grolla.domain.analysis import AnalysisDraft, Evidence, Hypothesis, SeenBeforeDraft
from grolla.testing.investigation import ScriptedLanguageModel

from evals.run import CASES, PAST_INCIDENT, Case, grade, run, toolbox

DEPLOY, SLOW, UNCLEAR, REPEATED_ALONE, REPEATED_WITH_MEMORY = CASES


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
    assert [failure is None for _, failure in outcomes] == [True, False, False, False, False]
    assert [brief.incident.services for brief in model.briefs] == [
        (case.service,) for case in CASES
    ]


def test_repeated_with_memory_needs_the_past_incident_and_its_cause() -> None:
    seen = SeenBeforeDraft(PAST_INCIDENT, "mesmo pool esgotado de madrugada")
    right = replace(
        draft(("Job de conciliação esgota o pool", 0.7, "caso passado")), seen_before=seen
    )

    assert grade(REPEATED_WITH_MEMORY, right) is None
    assert "já vi isso" in (grade(REPEATED_WITH_MEMORY, replace(right, seen_before=None)) or "")
    assert "não cita" in (
        grade(REPEATED_WITH_MEMORY, replace(draft(("Banco lento", 0.6, "p99")), seen_before=seen))
        or ""
    )


def test_repeated_without_memory_must_not_claim_a_past_incident() -> None:
    invented = SeenBeforeDraft(PAST_INCIDENT, "inventado")
    analysis = draft(("Pool de conexões esgotado", 0.6, "logs do pool"))

    assert grade(REPEATED_ALONE, analysis) is None
    assert "já vi isso" in (grade(REPEATED_ALONE, replace(analysis, seen_before=invented)) or "")


async def test_case_with_memory_hands_the_episode_to_the_model() -> None:
    model = ScriptedLanguageModel(draft(("x", 0.1, "y")))

    await run(model)

    episodes = [brief.memory.episodes for brief in model.briefs]
    assert episodes[3] == ()
    assert [e.incident_id for e in episodes[4]] == [PAST_INCIDENT]
