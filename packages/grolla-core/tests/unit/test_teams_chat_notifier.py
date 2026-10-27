import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx2
import pytest
from contracts.chat_notifier_contract import ChatNotifierContract
from grolla.adapters.teams import MERGE_NOTE, PROPOSAL_NOTE, TeamsChatNotifier, adaptive_card
from grolla.application.incidents import ChatNotifier, NotificationFailedError
from grolla.domain.analysis import (
    Analysis,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    PartialReason,
    SeenBefore,
    Verification,
)
from grolla.domain.follow_up import Cycle, CycleStatus, Trend
from grolla.domain.incidents import Notification, NotificationKind, Severity
from grolla.testing.builders import T0, make_verification, make_verification_draft

URL = "https://chat.interno/workflows/abc?sig=segredo"


def notification(
    kind: NotificationKind = NotificationKind.INCIDENT_OPENED,
    source_url: str | None = "https://origem/alerta/42",
    analysis: Analysis | None = None,
) -> Notification:
    return Notification(
        kind=kind,
        incident_id=UUID(int=1),
        title="Taxa de erro alta no capture-api",
        severity=Severity.P2,
        services=("capture-api", "authorizer-api"),
        summary="Taxa de erro de 7,2%",
        evidence="A taxa de erro HTTP 5xx está em 7,2% (limite 2%).",
        source_url=source_url,
        page_url="https://grolla/incidents/1",
        analysis=analysis,
    )


EVALUATIONS = (
    Evaluation(
        tool="query_metric",
        subject="taxa_de_erro de capture-api, últimos 60 min",
        finding="subiu de 0,3% para 7,2% às 10:04",
        url="https://signoz/metricas/1",
        ok=True,
    ),
    Evaluation(
        tool="search_logs",
        subject="logs de erro de capture-api",
        finding="não foi possível consultar",
        url=None,
        ok=False,
    ),
)


def analysis(reason: PartialReason | None = None) -> Analysis:
    return Analysis(
        incident_id=UUID(int=1),
        status=AnalysisStatus.COMPLETED if reason is None else AnalysisStatus.PARTIAL,
        requested_at=T0,
        started_at=T0,
        finished_at=T0,
        summary="Análise parcial." if reason else "Erros começaram logo depois do deploy 1.4.2.",
        evaluations=EVALUATIONS,
        hypotheses=()
        if reason
        else (
            Hypothesis(
                title="Deploy 1.4.2 com regressão",
                confidence=0.8,
                evidence=(
                    Evidence("build 1.4.2 às 10:02", "https://devops/build/7"),
                    Evidence("erro começou às 10:04", None),
                ),
            ),
            Hypothesis(title="Banco lento", confidence=0.15, evidence=()),
        ),
        plan=() if reason else ("Comparar erro antes e depois do deploy", "Avaliar rollback"),
        partial_reason=reason,
    )


def analysis_card(item: Analysis, source_url: str | None = "https://signoz") -> dict[str, Any]:
    return adaptive_card(notification(NotificationKind.ANALYSIS_READY, source_url, item))


def texts(card: dict[str, Any]) -> list[str]:
    """Os textos do card em ordem, descendo nos containers."""
    found: list[str] = []
    for element in card["body"]:
        for item in element.get("items", [element]):
            if item["type"] == "TextBlock":
                found.append(item["text"])
            elif item["type"] == "FactSet":
                found.extend(f"{fact['title']}: {fact['value']}" for fact in item["facts"])
    return found


class Recorder:
    def __init__(self, status: int = 202) -> None:
        self.status = status
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(self.status)

    def body(self) -> dict[str, Any]:
        return json.loads(self.requests[-1].content)


class TestTeamsChatNotifierContract(ChatNotifierContract):
    @pytest.fixture
    def notifier(self) -> ChatNotifier:
        return TeamsChatNotifier(URL, httpx2.MockTransport(Recorder()))

    @pytest.fixture
    def refusing_notifier(self) -> ChatNotifier:
        return TeamsChatNotifier(URL, httpx2.MockTransport(Recorder(500)))


def card_text(body: dict[str, Any]) -> str:
    return json.dumps(body["attachments"][0]["content"], ensure_ascii=False)


async def test_posts_an_adaptive_card_message_to_the_webhook() -> None:
    recorder = Recorder()
    notifier = TeamsChatNotifier(URL, httpx2.MockTransport(recorder))

    await notifier.post(notification())

    [request] = recorder.requests
    assert request.method == "POST"
    assert str(request.url) == URL
    body = recorder.body()
    assert body["type"] == "message"
    [attachment] = body["attachments"]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"]["type"] == "AdaptiveCard"


async def test_new_incident_card_has_everything_the_spec_asks_for() -> None:
    recorder = Recorder()

    await TeamsChatNotifier(URL, httpx2.MockTransport(recorder)).post(notification())

    text = card_text(recorder.body())
    for expected in (
        "Estou investigando · P2",
        "Vou checar",
        "métricas do serviço",
        "deploys recentes",
        "traces com erro",
        "logs de erro",
        "Taxa de erro alta no capture-api",
        "P2",
        "capture-api, authorizer-api",
        "Taxa de erro de 7,2%",
        "A taxa de erro HTTP 5xx está em 7,2% (limite 2%).",
        "https://grolla/incidents/1",
        "https://origem/alerta/42",
    ):
        assert expected in text


@pytest.mark.parametrize(
    ("kind", "headline"),
    [
        (NotificationKind.SEVERITY_RAISED, "Severidade elevada"),
        (NotificationKind.INCIDENT_NORMALIZED, "Incidente normalizado"),
    ],
)
async def test_each_kind_has_its_headline(kind: NotificationKind, headline: str) -> None:
    recorder = Recorder()

    await TeamsChatNotifier(URL, httpx2.MockTransport(recorder)).post(notification(kind))

    assert headline in card_text(recorder.body())


async def test_card_without_source_link_only_links_the_page() -> None:
    recorder = Recorder()

    await TeamsChatNotifier(URL, httpx2.MockTransport(recorder)).post(notification(source_url=None))

    actions = recorder.body()["attachments"][0]["content"]["actions"]
    assert [action["url"] for action in actions] == ["https://grolla/incidents/1"]


@pytest.mark.parametrize("status", [400, 429, 500, 503])
async def test_error_status_fails_without_leaking_the_url(status: int) -> None:
    notifier = TeamsChatNotifier(URL, httpx2.MockTransport(Recorder(status)))

    with pytest.raises(NotificationFailedError, match=str(status)) as error:
        await notifier.post(notification())

    assert "segredo" not in str(error.value)


@pytest.mark.parametrize("error", [httpx2.ConnectError("recusada"), httpx2.ReadTimeout("lento")])
async def test_network_error_fails(error: Exception) -> None:
    def explode(_request: httpx2.Request) -> httpx2.Response:
        raise error

    with pytest.raises(NotificationFailedError, match="rede"):
        await TeamsChatNotifier(URL, httpx2.MockTransport(explode)).post(notification())


def test_completed_analysis_card_shows_evaluations_hypotheses_and_plan() -> None:
    card = analysis_card(analysis())

    lines = texts(card)
    assert lines[0] == "Análise pronta · P2"
    assert "Serviços: capture-api, authorizer-api" in lines
    assert "Erros começaram logo depois do deploy 1.4.2." in lines
    section = {title: lines[i + 1] for i, title in enumerate(lines[:-1])}
    assert section["O que avaliei"] == (
        "- taxa_de_erro de capture-api, últimos 60 min: subiu de 0,3% para 7,2% às 10:04"
        " ([abrir](https://signoz/metricas/1))\n"
        "- logs de erro de capture-api: não foi possível consultar"
    )
    assert section["Hipóteses"] == (
        "- Deploy 1.4.2 com regressão (confiança 80%): "
        "[build 1.4.2 às 10:02](https://devops/build/7); erro começou às 10:04\n"
        "- Banco lento (confiança 15%)"
    )
    assert section["Plano proposto"] == (
        "- Comparar erro antes e depois do deploy\n- Avaliar rollback"
    )


def test_long_analysis_is_summarized_in_the_card() -> None:
    many = Hypothesis("Hipótese\ncom quebra " + "x" * 400, 0.5, (Evidence("e", None),) * 5)
    long = replace(analysis(), hypotheses=(many,) * 6, plan=tuple(f"passo {n}" for n in range(9)))

    lines = texts(analysis_card(long))

    section = {title: lines[i + 1] for i, title in enumerate(lines[:-1])}
    hypotheses = section["Hipóteses"].split("\n")
    assert len(hypotheses) == 4
    assert hypotheses[-1] == "- … mais 3 na página do incidente"
    assert "\n" not in hypotheses[0].removeprefix("- ")
    assert len(hypotheses[0]) < 400
    assert hypotheses[0].count("; ") == 1  # no máximo 2 evidências por hipótese
    assert section["Plano proposto"].split("\n")[-1] == "- … mais 4 na página do incidente"
    assert len(json.dumps(analysis_card(long))) < 8_000


def test_analysis_card_links_the_incident_and_the_telemetry() -> None:
    card = analysis_card(analysis())

    assert [(a["title"], a["url"]) for a in card["actions"]] == [
        ("Abrir incidente", "https://grolla/incidents/1"),
        ("Ver no SigNoz", "https://signoz"),
    ]


def test_analysis_card_without_telemetry_link_only_links_the_page() -> None:
    card = analysis_card(analysis(), source_url=None)

    assert [a["title"] for a in card["actions"]] == ["Abrir incidente"]


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        (PartialReason.LLM_FAILED, "LLM indisponível"),
        (PartialReason.TIMEOUT, "tempo esgotado"),
        (PartialReason.BUDGET, "orçamento de tokens esgotado"),
        (PartialReason.INVALID_RESULT, "resultado inválido"),
        (PartialReason.INTERRUPTED, "interrompida"),
    ],
)
def test_partial_analysis_card_says_why_right_below_the_headline(
    reason: PartialReason, text: str
) -> None:
    lines = texts(analysis_card(analysis(reason)))

    assert lines[0] == "Análise parcial · P2"
    assert lines[1] == f"Motivo: {text}"
    assert "Serviços: capture-api, authorizer-api" in lines
    assert "O que avaliei" in lines
    assert "Análise parcial." not in lines
    assert "Hipóteses" not in lines
    assert "Plano proposto" not in lines


def test_partial_analysis_without_evaluations_says_nothing_was_checked() -> None:
    item = analysis(PartialReason.TIMEOUT)
    empty = replace(item, evaluations=())

    lines = texts(analysis_card(empty))

    assert lines[lines.index("O que avaliei") + 1] == "- nenhuma consulta concluída"


def test_analysis_with_hypotheses_says_the_verification_is_on_the_way() -> None:
    assert texts(analysis_card(analysis()))[-1] == (
        "Estou validando as hipóteses; a causa raiz vem em seguida."
    )
    partial = texts(analysis_card(analysis(PartialReason.LLM_FAILED)))
    assert "Estou validando as hipóteses; a causa raiz vem em seguida." not in partial


def root_cause_card(item: Verification) -> dict[str, Any]:
    return adaptive_card(
        replace(
            notification(NotificationKind.ROOT_CAUSE, "https://signoz"),
            severity=Severity.P1,
            verification=item,
        )
    )


def sections(card: dict[str, Any]) -> dict[str, str]:
    lines = texts(card)
    return {title: lines[i + 1] for i, title in enumerate(lines[:-1])}


def test_confirmed_root_cause_card_has_cause_verdicts_and_concrete_plan() -> None:
    card = root_cause_card(make_verification(UUID(int=1)))

    lines = texts(card)
    assert lines[0] == "Causa raiz · P1"
    assert "Serviços: capture-api, authorizer-api" in lines
    section = sections(card)
    assert section["Causa raiz"] == "Deploy 2.4.1 introduziu erro em POST /capturas às 10:02"
    assert section["Hipóteses"] == (
        "- Hipótese 1: confirmada — Erros após o deploy\n"
        "- Hipótese 2: rejeitada — Dependência saudável"
    )
    assert section["Plano de ação"] == (
        "- Fazer rollback do capture-api para o build 2.4.0 (capture-api) → "
        "Taxa de erro volta para 0,2% ([abrir](https://origem.interno/1))\n"
        "- Escalar o time de capturas (time de capturas) → Correção do bug"
    )
    assert "O que faltou" not in lines
    assert [(a["title"], a["url"]) for a in card["actions"]] == [
        ("Abrir incidente", "https://grolla/incidents/1")
    ]


def test_unconfirmed_root_cause_card_says_what_was_missing_without_a_plan() -> None:
    draft = make_verification_draft(confirmed=False)
    item = replace(
        make_verification(UUID(int=1)),
        verdicts=draft.verdicts,
        root_cause=None,
        actions=(),
        missing=draft.missing,
    )

    card = root_cause_card(item)

    lines = texts(card)
    assert lines[0] == "Causa raiz não confirmada · P1"
    assert "Plano de ação" not in lines
    assert "Causa raiz" not in lines
    assert sections(card)["O que faltou"] == "Faltou o histórico de mudanças da dependência"
    assert sections(card)["Hipóteses"].startswith("- Hipótese 1: inconclusiva — Sem dado")


def test_partial_verification_card_says_why_right_below_the_headline() -> None:
    item = make_verification(
        UUID(int=1), status=AnalysisStatus.PARTIAL, partial_reason=PartialReason.BUDGET
    )

    lines = texts(root_cause_card(item))

    assert lines[:2] == ["Validação parcial · P1", "Motivo: orçamento de tokens esgotado"]
    assert "Serviços: capture-api, authorizer-api" in lines
    assert "O que faltou" not in lines
    assert "Plano de ação" not in lines


def test_long_verification_is_summarized_in_the_card() -> None:
    item = make_verification(UUID(int=1))
    verdict = replace(item.verdicts[0], reasoning="motivo\nlongo " + "x" * 500)
    action = replace(item.actions[0], action="Religar " + "y" * 500)
    long = replace(item, verdicts=(verdict,) * 6, actions=(action,) * 9)

    card = root_cause_card(long)

    section = sections(card)
    assert section["Hipóteses"].split("\n")[-1] == "- … mais 3 na página do incidente"
    assert section["Plano de ação"].split("\n")[-1] == "- … mais 4 na página do incidente"
    assert len(json.dumps(card)) < 8_000


def test_card_uses_only_elements_the_test_console_renders() -> None:
    allowed = {"TextBlock", "FactSet", "Container", "ColumnSet"}
    verification = root_cause_card(make_verification(UUID(int=1)))
    for card in (adaptive_card(notification()), analysis_card(analysis()), verification):
        types = {e["type"] for e in card["body"]}
        types |= {i["type"] for e in card["body"] for i in e.get("items", [])}
        assert types <= allowed
        assert {a["type"] for a in card["actions"]} == {"Action.OpenUrl"}


def normalized_card(verification: Verification | None) -> list[str]:
    card = replace(
        notification(NotificationKind.INCIDENT_NORMALIZED),
        summary="Normalizado: durou 32 min",
        verification=verification,
    )
    return texts(adaptive_card(card))


def test_normalization_closes_the_story_with_the_root_cause() -> None:
    verification = make_verification(UUID(int=1))
    lines = normalized_card(verification)

    assert lines[0] == "Incidente normalizado · P2"
    assert "Normalizado: durou 32 min" in lines
    section = {title: lines[i + 1] for i, title in enumerate(lines[:-1])}
    assert section["Causa raiz"] == verification.root_cause
    assert section["Plano de ação sugerido"].startswith(f"- {verification.actions[0].action}")


def test_normalization_during_validation_points_to_the_page() -> None:
    running = make_verification(UUID(int=1), status=AnalysisStatus.RUNNING)

    assert "Validação em andamento" in "\n".join(normalized_card(running))


def test_normalization_without_confirmed_cause_says_what_was_missing() -> None:
    unconfirmed = replace(
        make_verification(UUID(int=1)), root_cause=None, actions=(), missing="faltou o log do banco"
    )

    expected = "Causa raiz não confirmada. Faltou: faltou o log do banco"
    assert expected in normalized_card(unconfirmed)


def test_partial_validation_on_normalization_says_why() -> None:
    partial = make_verification(UUID(int=1), status=AnalysisStatus.PARTIAL)

    assert any("validação parcial (tempo esgotado)" in line for line in normalized_card(partial))


def test_normalization_without_validation_has_no_outcome() -> None:
    assert "Causa raiz" not in normalized_card(None)


# ---- Ações humanas do war room


def human_card(
    kind: NotificationKind,
    summary: str,
    evidence: str = "",
    verification: Verification | None = None,
) -> dict[str, Any]:
    card = replace(
        notification(kind, source_url=None),
        summary=summary,
        evidence=evidence,
        verification=verification,
    )
    return adaptive_card(card)


@pytest.mark.parametrize(
    ("kind", "headline", "summary"),
    [
        (NotificationKind.COMMANDER_ASSIGNED, "IC assumiu · P2", "Ana assumiu como IC"),
        (NotificationKind.SEVERITY_CHANGED_BY_HUMAN, "Severidade alterada · P2", "P1 → P2 por Ana"),
        (NotificationKind.INCIDENT_MITIGATED, "Incidente mitigado · P2", "Mitigado por Ana"),
    ],
)
def test_short_human_action_cards_cite_the_author(
    kind: NotificationKind, headline: str, summary: str
) -> None:
    card = human_card(kind, summary)

    assert texts(card) == [
        headline,
        "Taxa de erro alta no capture-api",
        "Severidade: P2",
        "Serviços: capture-api, authorizer-api",
        summary,
    ]
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "Abrir incidente", "url": "https://grolla/incidents/1"}
    ]


def test_severity_change_card_shows_the_reason_when_given() -> None:
    card = human_card(
        NotificationKind.SEVERITY_CHANGED_BY_HUMAN, "P1 → P3 por Ana", "Motivo: só o sandbox"
    )

    assert texts(card)[-2:] == ["P1 → P3 por Ana", "Motivo: só o sandbox"]


RESOLVED_EVIDENCE = (
    "Motivo: rollback do build .3\nAções do time:\n- rollback do build .3 (Ana)\n- avisei (Bia)"
)


def test_resolved_card_has_duration_author_reason_actions_and_root_cause() -> None:
    verification = make_verification(UUID(int=1))
    card = human_card(
        NotificationKind.INCIDENT_RESOLVED,
        "Encerrado por Ana · durou 35 min",
        RESOLVED_EVIDENCE,
        verification,
    )

    lines = texts(card)
    assert lines[0] == "Incidente encerrado · P2"
    assert "Encerrado por Ana · durou 35 min" in lines
    assert RESOLVED_EVIDENCE in lines
    assert lines[-2:] == ["Causa raiz", verification.root_cause]


@pytest.mark.parametrize("with_verification", [False, True])
def test_resolved_card_without_confirmed_cause_has_no_root_cause_section(
    with_verification: bool,
) -> None:
    unconfirmed = replace(make_verification(UUID(int=1)), root_cause=None, actions=())
    card = human_card(
        NotificationKind.INCIDENT_RESOLVED,
        "Encerrado por Ana · durou 35 min",
        "Motivo: x",
        unconfirmed if with_verification else None,
    )

    assert "Causa raiz" not in texts(card)


def test_discarded_card_shows_the_reason() -> None:
    card = human_card(
        NotificationKind.INCIDENT_DISCARDED, "Descartado por Ana", "Motivo: alerta de teste"
    )

    lines = texts(card)
    assert lines[0] == "Incidente descartado · P2"
    assert lines[-2:] == ["Descartado por Ana", "Motivo: alerta de teste"]


@pytest.mark.parametrize(
    "kind",
    [
        NotificationKind.COMMANDER_ASSIGNED,
        NotificationKind.SEVERITY_CHANGED_BY_HUMAN,
        NotificationKind.INCIDENT_MITIGATED,
        NotificationKind.INCIDENT_RESOLVED,
        NotificationKind.INCIDENT_DISCARDED,
    ],
)
def test_human_action_cards_use_only_elements_the_test_console_renders(
    kind: NotificationKind,
) -> None:
    card = human_card(kind, "resumo", "Motivo: x", make_verification(UUID(int=1)))

    types = {e["type"] for e in card["body"]}
    types |= {i["type"] for e in card["body"] for i in e.get("items", [])}
    assert types <= {"TextBlock", "FactSet", "Container"}
    assert {a["type"] for a in card["actions"]} == {"Action.OpenUrl"}


PAST = UUID("0193a3a0-7c1e-7000-8000-000000000042")


def test_analysis_card_says_seen_before_with_the_past_incident_link_and_reason() -> None:
    seen = SeenBefore(
        incident_id=PAST,
        title="pedidos-api com 5xx",
        at=datetime(2026, 10, 20, 14, 5, tzinfo=UTC),
        reason="mesmo erro de pool depois de deploy com migration",
    )
    current = notification(NotificationKind.ANALYSIS_READY, analysis=analysis())
    current = replace(
        current,
        page_url=f"https://grolla/incidents/{current.incident_id}",
        analysis=replace(analysis(), seen_before=seen),
    )

    section = sections(adaptive_card(current))

    assert section["Já vi isso: pedidos-api com 5xx (20/10)"] == (
        "mesmo erro de pool depois de deploy com migration\n"
        f"[Abrir o incidente anterior](https://grolla/incidents/{PAST})"
    )
    assert texts(adaptive_card(current)).index("Já vi isso: pedidos-api com 5xx (20/10)") < (
        texts(adaptive_card(current)).index("O que avaliei")
    )


def test_analysis_card_without_seen_before_has_no_mention() -> None:
    assert not any(line.startswith("Já vi isso") for line in texts(analysis_card(analysis())))


# ---- Acompanhamento da crise

IMPACT = "R$ 3.000,00 estimado"


def cycle(trend: Trend = Trend.WORSE, error_rate: float | None = 12.5) -> Cycle:
    return Cycle(
        incident_id=UUID(int=1),
        number=2,
        status=CycleStatus.COMPLETED,
        requested_at=T0,
        trend=trend,
        error_rate=error_rate,
        summary="Erro subiu de 7% para 12,5% no capture-api.",
        changed=True,
        evidence=(
            Evidence("taxa de erro 12,5% às 10:20", "https://signoz/metricas/2"),
            Evidence("sem deploy novo", None),
        ),
    )


def follow_up_card(
    kind: NotificationKind = NotificationKind.FOLLOW_UP_UPDATE,
    *,
    summary: str = "Taxa de erro de 7,2%",
    evidence: str = "",
    item: Cycle | None = None,
    impact: str | None = IMPACT,
) -> dict[str, Any]:
    return adaptive_card(
        replace(notification(kind), summary=summary, evidence=evidence, cycle=item, impact=impact)
    )


def war_room_only(card: dict[str, Any]) -> None:
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "Abrir o war room", "url": "https://grolla/incidents/1"}
    ]


def test_update_card_has_trend_error_rate_impact_summary_and_evidence() -> None:
    card = follow_up_card(item=cycle())

    assert texts(card) == [
        "Atualização · P2",
        "Taxa de erro alta no capture-api",
        "Tendência: Piorando",
        "Taxa de erro: 12,5%",
        f"Impacto: {IMPACT}",
        "Erro subiu de 7% para 12,5% no capture-api.",
        "Evidências",
        "- [taxa de erro 12,5% às 10:20](https://signoz/metricas/2)\n- sem deploy novo",
    ]
    assert card["body"][0]["color"] == "Attention"
    war_room_only(card)


@pytest.mark.parametrize(
    ("trend", "label", "color"),
    [
        (Trend.WORSE, "Piorando", "Attention"),
        (Trend.STABLE, "Estável", "Warning"),
        (Trend.BETTER, "Melhorando", "Accent"),
        (Trend.NORMAL, "Normalizado", "Good"),
    ],
)
def test_update_card_shows_the_trend_with_a_readable_label(
    trend: Trend, label: str, color: str
) -> None:
    card = follow_up_card(item=cycle(trend))

    assert f"Tendência: {label}" in texts(card)
    assert card["body"][0]["color"] == color


@pytest.mark.parametrize(("rate", "text"), [(None, "não medida"), (0.0, "0%"), (10.0, "10%")])
def test_update_card_error_rate_format(rate: float | None, text: str) -> None:
    assert f"Taxa de erro: {text}" in texts(follow_up_card(item=cycle(error_rate=rate)))


def test_update_card_without_impact_or_evidence_leaves_them_out() -> None:
    card = follow_up_card(item=replace(cycle(), evidence=()), impact=None)

    assert not any(line.startswith(("Impacto", "Evidências")) for line in texts(card))


def test_no_change_card_is_short_with_the_core_text_and_impact() -> None:
    card = follow_up_card(summary="Sem mudança: estável, 12% de erro")

    assert texts(card) == [
        "Sem mudança · P2",
        "Taxa de erro alta no capture-api",
        "Sem mudança: estável, 12% de erro",
        f"Impacto: {IMPACT}",
    ]
    war_room_only(card)
    assert len(texts(follow_up_card(summary="Sem mudança", impact=None))) == 3


def test_proposal_card_says_what_grolla_suggests_and_why() -> None:
    card = follow_up_card(
        NotificationKind.AGENT_PROPOSAL,
        summary="Grolla sugere marcar como mitigado",
        evidence="Normalizado há 15 min: taxa de erro em 0% nos últimos 3 ciclos.",
    )

    lines = texts(card)
    assert lines[0] == "Grolla sugere marcar como mitigado · P2"
    assert "Normalizado há 15 min: taxa de erro em 0% nos últimos 3 ciclos." in lines
    assert lines[-1] == PROPOSAL_NOTE
    assert card["body"][0]["color"] == "Accent"
    war_room_only(card)


def test_alert_reopened_card_says_it_is_back_to_handling_and_links_the_alert() -> None:
    card = follow_up_card(
        NotificationKind.ALERT_REOPENED,
        summary="Alerta voltou a disparar: de volta a em tratamento",
        evidence="A taxa de erro HTTP 5xx está em 7,2% (limite 2%).",
    )

    lines = texts(card)
    assert lines[0] == "Alerta voltou a disparar · P2"
    assert "Alerta voltou a disparar: de volta a em tratamento" in lines
    assert "A taxa de erro HTTP 5xx está em 7,2% (limite 2%)." in lines
    assert card["body"][0]["color"] == "Attention"
    assert [a["title"] for a in card["actions"]] == ["Abrir o war room", "Ver alerta na origem"]


def test_follow_up_cards_use_only_elements_the_test_console_renders() -> None:
    cards = [
        follow_up_card(item=cycle()),
        follow_up_card(),
        follow_up_card(NotificationKind.AGENT_PROPOSAL, evidence="motivo"),
        follow_up_card(NotificationKind.ALERT_REOPENED, evidence="alerta"),
        follow_up_card(NotificationKind.MERGE_SUGGESTED, evidence="motivo"),
        follow_up_card(NotificationKind.INCIDENT_MERGED, evidence="juntado"),
    ]
    for card in cards:
        types = {e["type"] for e in card["body"]}
        types |= {i["type"] for e in card["body"] for i in e.get("items", [])}
        assert types <= {"TextBlock", "FactSet", "Container"}
        assert {a["type"] for a in card["actions"]} == {"Action.OpenUrl"}


# ---- Menos ruído: sugestão de junção e incidentes juntados


def test_merge_suggestion_card_names_the_other_incident_and_why() -> None:
    card = follow_up_card(
        NotificationKind.MERGE_SUGGESTED,
        summary="Parece o mesmo problema que Taxa de erro alta no pagamentos-api",
        evidence="Os dois dependem do banco pedidos-db, que está lento.",
        impact=None,
    )

    assert texts(card) == [
        "Parece o mesmo problema que Taxa de erro alta no pagamentos-api · P2",
        "Taxa de erro alta no capture-api",
        "Severidade: P2",
        "Serviços: capture-api, authorizer-api",
        "Os dois dependem do banco pedidos-db, que está lento.",
        MERGE_NOTE,
    ]
    assert card["body"][0]["color"] == "Accent"
    war_room_only(card)


def test_merge_suggestion_card_without_summary_keeps_the_default_headline() -> None:
    card = follow_up_card(NotificationKind.MERGE_SUGGESTED, summary=" ", evidence="motivo")

    assert texts(card)[0] == "Parece o mesmo problema · P2"


def test_merged_card_says_which_incidents_were_joined() -> None:
    card = follow_up_card(
        NotificationKind.INCIDENT_MERGED,
        summary="Juntado em Taxa de erro alta no pagamentos-api por ana",
        evidence="Alertas e serviços passaram para o incidente principal.",
        impact=None,
    )

    lines = texts(card)
    assert lines[0] == "Incidentes juntados · P2"
    assert "Juntado em Taxa de erro alta no pagamentos-api por ana" in lines
    assert "Alertas e serviços passaram para o incidente principal." in lines
    assert MERGE_NOTE not in lines
    assert card["body"][0]["color"] == "Default"
    war_room_only(card)
