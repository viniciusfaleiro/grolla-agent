"""Regras puras da validação: plano concreto, links reais e encerramento parcial."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from grolla.domain.analysis import (
    MAX_COMMAND_CHARS,
    PARTIAL_REASON_TEXT,
    Action,
    AnalysisStatus,
    Command,
    Evaluation,
    NotConcreteError,
    PartialReason,
    Verdict,
    Verification,
    VerificationDraft,
    check_plan,
    usable_commands,
    verification_partial,
    verified,
)
from grolla.testing.builders import (
    T0,
    make_evaluations,
    make_verification,
    make_verification_draft,
)

DONE = T0 + timedelta(minutes=3)


def pending() -> Verification:
    return make_verification(uuid4(), status=AnalysisStatus.RUNNING, requested_at=T0)


def with_action(text: str) -> VerificationDraft:
    draft = make_verification_draft()
    return replace(draft, actions=(Action(text, "capture-api", "Erros param", None),))


def test_confirmed_draft_with_concrete_actions_passes() -> None:
    check_plan(make_verification_draft())


def test_unconfirmed_draft_without_actions_passes() -> None:
    check_plan(make_verification_draft(confirmed=False))


@pytest.mark.parametrize(
    "text",
    [
        "Investigar o pool de conexões",
        "Verificar a saúde do banco",
        "verificar o banco",
        "  CHECAR os logs",
        "Análisar a fila",  # acento e caixa não escondem o verbo
        "Consultar o time",
        "Confirmar o deploy",
        "Avaliar o rollback",
        "Revisar a configuração",
        "Examinar o trace",
        "Observar a métrica",
        "Acompanhar a taxa de erro",
        "Monitorar: latência",
        "Monitorar, latência",
        "Verifique a saúde do banco",  # imperativo também é investigação
        "Cheque os logs",
        "Avalie reverter o deploy",
        "Investigue a fila",
    ],
)
def test_investigation_step_in_the_plan_is_not_concrete(text: str) -> None:
    with pytest.raises(NotConcreteError, match="passo de investigação"):
        check_plan(with_action(text))


@pytest.mark.parametrize(
    "text",
    [
        "Fazer rollback do capture-api para o build 2.4.0",
        "Religar o banco pedidos-db",
        "Escalar o time de capturas",
        "Reverificação não é o verbo",  # só a primeira palavra inteira conta
    ],
)
def test_direct_action_is_concrete(text: str) -> None:
    check_plan(with_action(text))


def test_empty_action_text_is_not_an_investigation_verb() -> None:
    check_plan(with_action("   "))


def test_actions_without_root_cause_are_not_concrete() -> None:
    draft = replace(make_verification_draft(), root_cause=None)

    with pytest.raises(NotConcreteError, match="sem causa raiz"):
        check_plan(draft)


def test_root_cause_without_actions_is_not_concrete() -> None:
    draft = replace(make_verification_draft(), actions=())

    with pytest.raises(NotConcreteError, match="nenhuma ação"):
        check_plan(draft)


def test_verified_keeps_the_draft_and_the_real_evaluations() -> None:
    draft = make_verification_draft()
    evaluations = (
        Evaluation(
            "cloud_resources", "recursos de nuvem", "banco parado", draft.actions[0].link, True
        ),
    )

    result = verified(pending(), draft, evaluations, DONE)

    assert result.status is AnalysisStatus.COMPLETED
    assert result.finished_at == DONE
    assert result.root_cause == draft.root_cause
    assert result.verdicts == draft.verdicts
    assert result.actions == draft.actions
    assert result.missing == ""
    assert result.evaluations == evaluations
    assert result.partial_reason is None


def test_verified_drops_links_no_tool_returned() -> None:
    draft = make_verification_draft()

    result = verified(pending(), draft, make_evaluations(), DONE)

    assert [a.link for a in result.actions] == [None, None]
    assert {e.url for v in result.verdicts for e in v.evidence} == {None}
    assert [v.verdict for v in result.verdicts] == [Verdict.CONFIRMED, Verdict.REJECTED]


def test_all_inconclusive_is_completed_without_plan_and_says_what_was_missing() -> None:
    draft = make_verification_draft(confirmed=False)

    result = verified(pending(), draft, (), DONE)

    assert result.status is AnalysisStatus.COMPLETED
    assert result.root_cause is None
    assert result.actions == ()
    assert result.missing == draft.missing


def test_verified_refuses_a_plan_that_is_not_concrete() -> None:
    with pytest.raises(NotConcreteError):
        verified(pending(), with_action("Verificar o banco"), (), DONE)


def test_partial_keeps_the_evaluations_and_says_the_reason() -> None:
    evaluations = make_evaluations()

    result = verification_partial(pending(), PartialReason.TIMEOUT, evaluations, DONE)

    assert result.status is AnalysisStatus.PARTIAL
    assert result.partial_reason is PartialReason.TIMEOUT
    assert result.finished_at == DONE
    assert result.evaluations == evaluations
    assert (result.verdicts, result.root_cause, result.actions) == ((), None, ())
    assert result.missing == f"Validação parcial: {PARTIAL_REASON_TEXT[PartialReason.TIMEOUT]}."


def test_builder_verification_states() -> None:
    incident_id = uuid4()

    assert make_verification(incident_id, status=AnalysisStatus.PENDING).started_at is None
    partial = make_verification(incident_id, status=AnalysisStatus.PARTIAL)
    assert partial.partial_reason is PartialReason.TIMEOUT
    assert partial.root_cause is None
    assert make_verification(incident_id).root_cause is not None


CLUSTER = "cluster loja-prod, namespace loja"
SCALE = "kubectl scale deploy/antifraude-api --replicas=2 -n loja"


def test_usable_commands_trims_spaces_and_backticks() -> None:
    assert usable_commands([(f"  `{SCALE}`  ", f" {CLUSTER} ")]) == (Command(SCALE, CLUSTER),)


def test_short_chained_command_is_kept_whole() -> None:
    chained = (
        "kubectl rollout undo deploy/pedidos-api -n loja && "
        "kubectl rollout status deploy/pedidos-api -n loja | tail -1; echo fim"
    )

    assert usable_commands([(chained, CLUSTER)]) == (Command(chained, CLUSTER),)


@pytest.mark.parametrize(
    ("command", "run_in"),
    [
        ("kubectl scale deploy/a --replicas=2 \\\n -n loja", CLUSTER),
        ("kubectl get pods\r\nkubectl delete pod x", CLUSTER),
        ("x" * (MAX_COMMAND_CHARS + 1), CLUSTER),
        (SCALE, "   "),
        ("  ``  ", CLUSTER),
    ],
)
def test_unusable_command_is_dropped_alone(command: str, run_in: str) -> None:
    pipeline = ("az pipelines run --name pedidos-api-cd", "pipeline pedidos-api-cd")

    assert usable_commands([pipeline, (command, run_in), (SCALE, CLUSTER)]) == (
        Command(*pipeline),
        Command(SCALE, CLUSTER),
    )


def test_command_at_the_char_limit_is_kept() -> None:
    longest = "x" * MAX_COMMAND_CHARS

    assert usable_commands([(longest, CLUSTER)]) == (Command(longest, CLUSTER),)


def test_only_the_first_five_usable_commands_stay_in_order() -> None:
    raw = [("", CLUSTER)] + [(f"cmd {n}", CLUSTER) for n in range(7)]

    assert [c.command for c in usable_commands(raw)] == [f"cmd {n}" for n in range(5)]


def test_action_without_commands_has_an_empty_list() -> None:
    assert Action("religar o banco", "grolla-lab-db", "banco disponível", None).commands == ()
