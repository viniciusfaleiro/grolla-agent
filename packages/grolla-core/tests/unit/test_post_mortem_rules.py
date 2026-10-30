"""Regras do post mortem: métricas e timeline do log, rascunho, edição, regeneração, ciclo de
vida e plano de ação.
"""

from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import pytest
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.domain.post_mortem import (
    AGENT_AUTHOR,
    DETAIL_LIMIT,
    DETERMINISTIC,
    EDIT_REASON,
    REGENERATE_REASON,
    ActionKind,
    ActionStatus,
    AgentEvaluation,
    InvalidPostMortemError,
    PostMortem,
    PostMortemDraft,
    PostMortemStatus,
    SectionKey,
    action_changed,
    check_regenerable,
    drafted,
    edited,
    evaluated,
    lessons_text,
    metrics_text,
    moved,
    overdue,
    regenerated,
    timeline_text,
)
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.post_mortem import make_action, make_post_mortem

NOW = T0 + timedelta(hours=1)
TODAY = date(2026, 1, 10)


def resolved_incident() -> Any:
    return replace(
        make_incident(state=IncidentState.RESOLVED, detected_at=T0),
        handling_at=T0 + timedelta(minutes=4),
        mitigated_at=T0 + timedelta(minutes=20),
        resolved_at=T0 + timedelta(minutes=32),
    )


def event(
    kind: EventType, data: dict[str, Any] | None = None, author: str = "sistema", minute: int = 0
) -> IncidentEvent:
    return IncidentEvent(
        make_incident().id, T0 + timedelta(minutes=minute), kind, data or {}, author
    )


def body_of(pm: PostMortem, key: SectionKey) -> str:
    return next(s.body for s in pm.sections if s.key is key)


# ---- Métricas e timeline ----


def test_metrics_measure_from_the_first_alert_and_from_detection() -> None:
    alerts = [
        make_alert(starts_at=T0 - timedelta(minutes=2)),
        make_alert(starts_at=T0 - timedelta(minutes=5)),
    ]

    text = metrics_text(resolved_incident(), alerts)

    assert text.splitlines() == [
        "- **TTD** (do primeiro alerta à detecção): 5 min",
        "- **TTA** (da detecção a alguém assumir): 4 min",
        "- **TTM** (da detecção à mitigação): 20 min",
        "- **TTR** (da detecção ao encerramento): 32 min",
    ]


def test_metrics_that_did_not_happen_show_a_dash() -> None:
    incident = make_incident(state=IncidentState.RESOLVED)

    text = metrics_text(incident, [])

    assert text.count("—") == 4


def test_timeline_has_the_origin_of_each_item_and_skips_card_delivery() -> None:
    events = [
        event(EventType.ALERT_RECEIVED, {"rule_name": "Taxa de erro alta", "summary": "7%"}),
        event(EventType.STATE_CHANGED, {"from": "detectado", "to": "informado"}),
        event(EventType.NOTIFICATION_QUEUED, {"kind": "incident_opened"}),
        event(EventType.ANALYSIS_COMPLETED, {"summary": "pool\nesgotado"}, AGENT_AUTHOR, 2),
        event(EventType.COMMANDER_ASSIGNED, {"commander": "Ana"}, "Ana", 4),
        event(EventType.HUMAN_MESSAGE, {"text": "o rollback resolveu?"}, "Bruno", 5),
        event(EventType.INCIDENT_RESOLVED, {"reason": "rollback"}, "Ana", 32),
    ]

    assert timeline_text(events).splitlines() == [
        "- 10:00 [alerta] alerta disparou: Taxa de erro alta · 7%",
        "- 10:00 [alerta] estado mudou: detectado → informado",
        "- 10:02 [agente] análise inicial pronta: pool esgotado",
        "- 10:04 [pessoa] Ana: assumiu como IC",
        "- 10:05 [chat] Bruno: escreveu: o rollback resolveu?",
        "- 10:32 [pessoa] Ana: encerrou o incidente: rollback",
    ]


def test_timeline_shows_the_applied_command_whole_and_where() -> None:
    command = "kubectl scale deploy/antifraude-api --replicas=2 -n loja " + "x" * DETAIL_LIMIT
    applied = {
        "text": "Restaurar as réplicas",
        "command": command,
        "run_in": "cluster loja-prod, namespace loja",
    }
    events = [
        event(EventType.ACTION_RECORDED, applied, "Ana", 31),
        event(EventType.ACTION_RECORDED, {"text": "religuei o banco"}, "Bruno", 32),
    ]

    assert timeline_text(events).splitlines() == [
        f"- 10:31 [pessoa] Ana: Aplicou: Restaurar as réplicas — {command} "
        "(cluster loja-prod, namespace loja)",
        "- 10:32 [pessoa] Bruno: registrou uma ação: religuei o banco",
    ]


def test_empty_timeline_says_so() -> None:
    assert timeline_text([]) == "- nenhum evento registrado"


# ---- Rascunho ----


def test_draft_has_every_section_in_order_with_the_model_text_and_plan() -> None:
    incident = resolved_incident()
    sections = {k: f"texto de {k.value}" for k in SectionKey if k not in DETERMINISTIC}
    draft = PostMortemDraft(
        sections, (("Criar alarme", ActionKind.DETECT), ("  ", ActionKind.PREVENT))
    )

    pm = drafted(incident, [make_alert()], [], draft=draft, failure=None, now=NOW)

    assert [s.key for s in pm.sections] == list(SectionKey)
    assert body_of(pm, SectionKey.SUMMARY) == "texto de resumo"
    assert body_of(pm, SectionKey.METRICS) == metrics_text(incident, [make_alert()])
    assert body_of(pm, SectionKey.TIMELINE) == "- nenhum evento registrado"
    assert {s.author for s in pm.sections} == {AGENT_AUTHOR}
    assert [(a.description, a.kind, a.owner, a.due) for a in pm.actions] == [
        ("Criar alarme", ActionKind.DETECT, None, None)
    ]
    assert (pm.status, pm.status_by, pm.status_at) == (PostMortemStatus.DRAFT, AGENT_AUTHOR, NOW)
    assert pm.created_at == pm.updated_at == NOW


def test_without_the_model_the_sections_say_why_they_were_not_written() -> None:
    pm = drafted(resolved_incident(), [], [], draft=None, failure="LLM indisponível", now=NOW)

    assert body_of(pm, SectionKey.FACTORS) == "não gerada: LLM indisponível"
    assert body_of(pm, SectionKey.METRICS).startswith("- **TTD**")
    assert pm.actions == ()


def test_section_the_model_left_blank_is_marked() -> None:
    draft = PostMortemDraft({SectionKey.SUMMARY: "Resumo."}, ())

    pm = drafted(resolved_incident(), [], [], draft=draft, failure=None, now=NOW)

    assert body_of(pm, SectionKey.IMPACT) == "não gerada: o modelo não escreveu a seção"


# ---- Edição e regeneração ----


def test_first_human_edit_moves_the_draft_to_review_and_keeps_the_agent_version() -> None:
    pm = make_post_mortem()

    updated, version = edited(pm, SectionKey.SUMMARY, " Novo resumo. ", " Ana ", NOW)

    assert body_of(updated, SectionKey.SUMMARY) == "Novo resumo."
    section = updated.sections[0]
    assert (section.author, section.updated_at) == ("Ana", NOW)
    assert (updated.status, updated.status_by, updated.status_at) == (
        PostMortemStatus.REVIEW,
        "Ana",
        NOW,
    )
    assert (version.key, version.author, version.reason, version.at) == (
        SectionKey.SUMMARY,
        "Ana",
        EDIT_REASON,
        NOW,
    )
    assert version.body == body_of(pm, SectionKey.SUMMARY)


def test_edit_in_review_keeps_the_status() -> None:
    pm = make_post_mortem(status=PostMortemStatus.REVIEW)

    updated, _ = edited(pm, SectionKey.IMPACT, "Impacto.", "Bruno", NOW)

    assert (updated.status, updated.status_by, updated.updated_at) == (
        PostMortemStatus.REVIEW,
        AGENT_AUTHOR,
        NOW,
    )


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (PostMortemStatus.APPROVED, "aprovado não pode ser alterado"),
        (PostMortemStatus.CLOSED, "encerrado não pode ser alterado"),
    ],
)
def test_approved_or_closed_is_not_edited_nor_regenerated(
    status: PostMortemStatus, message: str
) -> None:
    pm = make_post_mortem(status=status)

    with pytest.raises(InvalidPostMortemError, match=message):
        edited(pm, SectionKey.SUMMARY, "x", "Ana", NOW)
    with pytest.raises(InvalidPostMortemError, match=message):
        regenerated(pm, SectionKey.SUMMARY, "x", NOW)
    with pytest.raises(InvalidPostMortemError, match=message):
        check_regenerable(pm)


@pytest.mark.parametrize(
    ("body", "author", "message"),
    [("texto", " ", "informe o seu nome"), ("  ", "Ana", "escreva o texto da seção")],
)
def test_edit_needs_author_and_text(body: str, author: str, message: str) -> None:
    with pytest.raises(InvalidPostMortemError, match=message):
        edited(make_post_mortem(), SectionKey.SUMMARY, body, author, NOW)


def test_regeneration_is_written_by_the_agent_and_keeps_the_status() -> None:
    pm, _ = edited(make_post_mortem(), SectionKey.FACTORS, "Fatores do time.", "Ana", NOW)
    later = NOW + timedelta(minutes=1)

    updated, version = regenerated(pm, SectionKey.FACTORS, "Fatores com o alarme.", later)

    section = next(s for s in updated.sections if s.key is SectionKey.FACTORS)
    assert (section.body, section.author) == ("Fatores com o alarme.", AGENT_AUTHOR)
    assert updated.status is PostMortemStatus.REVIEW
    assert (version.author, version.reason, version.body) == (
        AGENT_AUTHOR,
        REGENERATE_REASON,
        "Fatores do time.",
    )
    check_regenerable(updated)


def test_blank_regeneration_is_marked() -> None:
    updated, _ = regenerated(make_post_mortem(), SectionKey.SUMMARY, " ", NOW)

    assert body_of(updated, SectionKey.SUMMARY) == "o modelo não escreveu a seção"


# ---- Ciclo de vida ----


def owned(description: str = "Criar alarme", **changes: Any) -> Any:
    return replace(make_action(description), **{"owner": "Ana", "due": TODAY, **changes})


def test_lifecycle_goes_from_draft_to_closed_with_author_and_date() -> None:
    pm = make_post_mortem(actions=[owned()])

    review = moved(pm, PostMortemStatus.REVIEW, "Ana", NOW)
    approved = moved(review, PostMortemStatus.APPROVED, "Bruno", NOW + timedelta(1))
    back = moved(approved, PostMortemStatus.REVIEW, "Ana", NOW + timedelta(2))
    done = replace(approved, actions=(replace(approved.actions[0], status=ActionStatus.DONE),))
    closed = moved(done, PostMortemStatus.CLOSED, "Carla", NOW + timedelta(3))

    assert (approved.status, approved.status_by) == (PostMortemStatus.APPROVED, "Bruno")
    assert back.status is PostMortemStatus.REVIEW
    assert (closed.status, closed.status_by, closed.status_at, closed.updated_at) == (
        PostMortemStatus.CLOSED,
        "Carla",
        NOW + timedelta(3),
        NOW + timedelta(3),
    )


def test_approving_without_owner_or_due_names_the_items() -> None:
    pm = make_post_mortem(
        status=PostMortemStatus.REVIEW,
        actions=[owned(), make_action("Criar alarme no banco"), owned("Teste", due=None)],
    )

    with pytest.raises(InvalidPostMortemError) as raised:
        moved(pm, PostMortemStatus.APPROVED, "Ana", NOW)

    assert str(raised.value) == (
        "para aprovar, todo item do plano precisa de dono e prazo; "
        'falta em: "Criar alarme no banco", "Teste"'
    )


def test_closing_needs_every_item_done_or_accepted() -> None:
    items = [owned(status=ActionStatus.ACCEPTED), owned("Aberto")]
    pm = make_post_mortem(status=PostMortemStatus.APPROVED, actions=items)

    with pytest.raises(InvalidPostMortemError, match='concluído ou aceito; falta em: "Aberto"'):
        moved(pm, PostMortemStatus.CLOSED, "Ana", NOW)


@pytest.mark.parametrize(
    ("current", "to", "message"),
    [
        (PostMortemStatus.DRAFT, PostMortemStatus.APPROVED, "de rascunho para aprovado"),
        (PostMortemStatus.REVIEW, PostMortemStatus.CLOSED, "de em revisão para encerrado"),
        (PostMortemStatus.CLOSED, PostMortemStatus.REVIEW, "de encerrado para em revisão"),
        (PostMortemStatus.REVIEW, PostMortemStatus.REVIEW, "já está em revisão"),
    ],
)
def test_invalid_transitions_are_refused(
    current: PostMortemStatus, to: PostMortemStatus, message: str
) -> None:
    with pytest.raises(InvalidPostMortemError, match=message):
        moved(make_post_mortem(status=current), to, "Ana", NOW)


def test_moving_needs_an_author() -> None:
    with pytest.raises(InvalidPostMortemError, match="informe o seu nome"):
        moved(make_post_mortem(), PostMortemStatus.REVIEW, "", NOW)


# ---- Plano de ação ----


def test_new_action_is_appended_and_moves_the_draft_to_review() -> None:
    item = replace(make_action(" Criar alarme "), owner="  ")

    pm = action_changed(make_post_mortem(), item, "Ana", NOW)

    assert [(a.description, a.owner) for a in pm.actions] == [("Criar alarme", None)]
    assert (pm.status, pm.status_by) == (PostMortemStatus.REVIEW, "Ana")


def test_changed_action_replaces_the_item() -> None:
    item = make_action()
    pm = make_post_mortem(status=PostMortemStatus.REVIEW, actions=[item, make_action("Outro")])

    updated = action_changed(pm, replace(item, owner=" Ana ", due=TODAY), "Ana", NOW)

    assert (updated.actions[0].owner, updated.actions[0].due) == ("Ana", TODAY)
    assert updated.actions[1] == pm.actions[1]


def test_approved_plan_keeps_owner_and_due_and_closed_does_not_change() -> None:
    item = owned()
    approved = make_post_mortem(status=PostMortemStatus.APPROVED, actions=[item])

    done = action_changed(approved, replace(item, status=ActionStatus.DONE), "Ana", NOW)

    assert done.actions[0].status is ActionStatus.DONE
    assert done.status is PostMortemStatus.APPROVED
    with pytest.raises(InvalidPostMortemError, match="dono e prazo"):
        action_changed(approved, make_action("Sem dono"), "Ana", NOW)
    closed = replace(approved, status=PostMortemStatus.CLOSED)
    with pytest.raises(InvalidPostMortemError, match="encerrado"):
        action_changed(closed, item, "Ana", NOW)


@pytest.mark.parametrize(
    ("description", "author", "message"),
    [(" ", "Ana", "descreva o item"), ("Criar", " ", "informe o seu nome")],
)
def test_action_needs_description_and_author(description: str, author: str, message: str) -> None:
    with pytest.raises(InvalidPostMortemError, match=message):
        action_changed(make_post_mortem(), make_action(description), author, NOW)


def test_overdue_items_are_open_with_due_before_today() -> None:
    late = owned(due=TODAY - timedelta(days=1))
    items = [
        late,
        owned(),
        make_action(),
        owned(due=TODAY - timedelta(1), status=ActionStatus.DONE),
    ]

    assert overdue(make_post_mortem(actions=items), TODAY) == (late,)


# ---- Avaliação do agente ----

EVALUATION = AgentEvaluation(main_hypothesis_correct=True, minutes_to_right=8, memory_helped=True)


def test_evaluation_is_signed_and_a_draft_goes_to_review() -> None:
    pm = make_post_mortem()

    result = evaluated(pm, replace(EVALUATION, author="outro"), " Ana ", NOW)

    assert result.evaluation == replace(EVALUATION, author="Ana", at=NOW)
    assert (result.status, result.status_by, result.updated_at) == (
        PostMortemStatus.REVIEW,
        "Ana",
        NOW,
    )


def test_approved_post_mortem_can_still_be_evaluated() -> None:
    pm = make_post_mortem(status=PostMortemStatus.APPROVED)

    result = evaluated(pm, AgentEvaluation(minutes_to_right=0), "Ana", NOW)

    assert result.status is PostMortemStatus.APPROVED
    assert result.evaluation.minutes_to_right == 0


@pytest.mark.parametrize(
    ("status", "evaluation", "author", "message"),
    [
        (PostMortemStatus.CLOSED, EVALUATION, "Ana", "encerrado não pode ser alterado"),
        (PostMortemStatus.REVIEW, EVALUATION, " ", "informe o seu nome"),
        (
            PostMortemStatus.REVIEW,
            AgentEvaluation(minutes_to_right=-1),
            "Ana",
            "não podem ser negativos",
        ),
    ],
)
def test_evaluation_is_refused(
    status: PostMortemStatus, evaluation: AgentEvaluation, author: str, message: str
) -> None:
    with pytest.raises(InvalidPostMortemError, match=message):
        evaluated(make_post_mortem(status=status), evaluation, author, NOW)


def test_lessons_text_has_factors_lessons_plan_with_items_and_summary() -> None:
    owned = replace(make_action("Alarme no banco"), owner="Bruno", due=date(2026, 2, 1))
    pm = make_post_mortem(actions=[owned, make_action("Teste de carga", kind=ActionKind.PREVENT)])
    empty = replace(pm, sections=(), actions=())

    text = lessons_text(pm)

    titles = [line for line in text.splitlines() if line.startswith("## ")]
    assert titles == [
        "## Fatores contribuintes",
        "## O que funcionou, o que não funcionou e onde tivemos sorte",
        "## Plano de ação",
        "## Resumo executivo",
    ]
    assert "- Alarme no banco (detectar, Bruno, 01/02/2026, aberto)" in text
    assert "- Teste de carga (prevenir, sem dono, sem prazo, aberto)" in text
    assert "Impacto" not in text
    assert lessons_text(empty).count("\n—") == 4


def test_seen_before_answer_shows_on_the_timeline_as_a_person() -> None:
    answer = event(EventType.SEEN_BEFORE_ANSWERED, {"past": "x", "same": True}, author="Ana")

    assert timeline_text([answer]) == "- 10:00 [pessoa] Ana: respondeu se era o mesmo problema"
