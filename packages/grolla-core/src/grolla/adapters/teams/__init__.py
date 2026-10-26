"""Microsoft Teams: Adaptive Cards postados por um Workflow (webhook) como ChatNotifier."""

from typing import Any

import httpx2

from grolla.application.incidents import NotificationFailedError
from grolla.domain.analysis import (
    INITIAL_CHECKS,
    PARTIAL_REASON_TEXT,
    Action,
    Analysis,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    Verification,
)
from grolla.domain.incidents import Notification, NotificationKind

TIMEOUT_SECONDS = 10.0

_HEADLINES = {
    NotificationKind.INCIDENT_OPENED: "Estou investigando",
    NotificationKind.ANALYSIS_READY: "Análise pronta",
    NotificationKind.ROOT_CAUSE: "Causa raiz",
    NotificationKind.COMMANDER_ASSIGNED: "IC assumiu",
    NotificationKind.SEVERITY_CHANGED_BY_HUMAN: "Severidade alterada",
    NotificationKind.INCIDENT_MITIGATED: "Incidente mitigado",
    NotificationKind.INCIDENT_RESOLVED: "Incidente encerrado",
    NotificationKind.INCIDENT_DISCARDED: "Incidente descartado",
    NotificationKind.SEVERITY_RAISED: "Severidade elevada",
    NotificationKind.INCIDENT_NORMALIZED: "Incidente normalizado",
}

_COLORS = {
    NotificationKind.INCIDENT_OPENED: "Attention",
    NotificationKind.ANALYSIS_READY: "Accent",
    NotificationKind.ROOT_CAUSE: "Good",
    NotificationKind.COMMANDER_ASSIGNED: "Accent",
    NotificationKind.SEVERITY_CHANGED_BY_HUMAN: "Warning",
    NotificationKind.INCIDENT_MITIGATED: "Good",
    NotificationKind.INCIDENT_RESOLVED: "Good",
    NotificationKind.INCIDENT_DISCARDED: "Default",
    NotificationKind.SEVERITY_RAISED: "Warning",
    NotificationKind.INCIDENT_NORMALIZED: "Good",
}


_NOTHING = "- nenhuma consulta concluída"
VERIFYING = "Estou validando as hipóteses; a causa raiz vem em seguida."


class TeamsChatNotifier:
    def __init__(
        self, webhook_url: str, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._webhook_url = webhook_url
        self._transport = transport  # testes injetam um transporte fake

    async def post(self, notification: Notification) -> None:
        # As mensagens de erro não citam a URL: ela carrega a assinatura do Workflow.
        try:
            async with httpx2.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_SECONDS
            ) as client:
                response = await client.post(self._webhook_url, json=message(notification))
        except httpx2.HTTPError as error:
            raise NotificationFailedError(f"falha de rede: {type(error).__name__}") from error
        if response.status_code >= 400:  # noqa: PLR2004 — faixa de erro do HTTP
            raise NotificationFailedError(f"canal respondeu HTTP {response.status_code}")


def message(notification: Notification) -> dict[str, Any]:
    """Mensagem no formato que o Workflow do Teams espera, com o Adaptive Card dentro."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": adaptive_card(notification),
            }
        ],
    }


def adaptive_card(notification: Notification) -> dict[str, Any]:
    analysis = notification.analysis
    verification = notification.verification
    source_title: str | None = None  # o card da causa raiz só leva ao incidente
    if notification.kind is NotificationKind.ROOT_CAUSE and verification is not None:
        body = _verification_body(notification, verification)
    elif notification.kind is NotificationKind.ANALYSIS_READY and analysis is not None:
        body = _analysis_body(notification, analysis)
        source_title = "Ver no SigNoz"
    else:
        body = _alert_body(notification)
        source_title = "Ver alerta na origem"
    actions = [{"type": "Action.OpenUrl", "title": "Abrir incidente", "url": notification.page_url}]
    if notification.source_url and source_title:
        actions.append(
            {"type": "Action.OpenUrl", "title": source_title, "url": notification.source_url}
        )
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
        "actions": actions,
    }


def _alert_body(notification: Notification) -> list[dict[str, Any]]:
    kind = notification.kind
    body = [
        _headline(_HEADLINES[kind], notification, _COLORS[kind]),
        *_identity(notification),
        _text(notification.summary),
        {"type": "TextBlock", "text": notification.evidence, "wrap": True, "isSubtle": True},
    ]
    if kind is NotificationKind.INCIDENT_OPENED:
        body.append(_section("Vou checar", [f"- {check}" for check in INITIAL_CHECKS]))
    if kind is NotificationKind.INCIDENT_NORMALIZED and notification.verification is not None:
        body.extend(_outcome(notification.verification))
    return body


def _outcome(verification: Verification) -> list[dict[str, Any]]:
    """Desfecho da investigação no card de normalização: fecha a história do incidente."""
    if verification.status in (AnalysisStatus.PENDING, AnalysisStatus.RUNNING):
        return [_section("Causa raiz", [VERIFICATION_RUNNING])]
    if verification.root_cause:
        sections = [_section("Causa raiz", [_clip(verification.root_cause)])]
        if verification.actions:
            steps = [_action(a) for a in verification.actions]
            sections.append(_section("Plano de ação sugerido", _limit(steps, MAX_PLAN_STEPS)))
        return sections
    if verification.partial_reason is not None:
        reason = PARTIAL_REASON_TEXT[verification.partial_reason]
        return [
            _section("Causa raiz", [f"Causa raiz não confirmada: validação parcial ({reason})"])
        ]
    missing = verification.missing.strip()
    text = f"Causa raiz não confirmada. Faltou: {_clip(missing)}" if missing else NO_ROOT_CAUSE_TEXT
    return [_section("Causa raiz", [text])]


def _analysis_body(notification: Notification, analysis: Analysis) -> list[dict[str, Any]]:
    if analysis.partial_reason is None:
        body = [
            _headline("Análise pronta", notification, "Accent"),
            *_identity(notification),
            _text(analysis.summary),
        ]
    else:  # o resumo da parcial só repete o motivo, que já vai logo abaixo do título
        body = [
            _headline("Análise parcial", notification, "Warning"),
            _text(f"Motivo: {PARTIAL_REASON_TEXT[analysis.partial_reason]}"),
            *_identity(notification),
        ]
    evaluations = [_evaluation(e) for e in analysis.evaluations]
    body.append(_section("O que avaliei", _limit(evaluations, MAX_EVALUATIONS) or [_NOTHING]))
    if analysis.hypotheses:
        hypotheses = [_hypothesis(h) for h in analysis.hypotheses]
        body.append(_section("Hipóteses", _limit(hypotheses, MAX_HYPOTHESES)))
    if analysis.plan:
        steps = [f"- {_clip(step)}" for step in analysis.plan]
        body.append(_section("Plano proposto", _limit(steps, MAX_PLAN_STEPS)))
    if analysis.hypotheses:  # com hipóteses, a validação foi pedida junto
        body.append({"type": "TextBlock", "text": VERIFYING, "wrap": True, "isSubtle": True})
    return body


def _verification_body(
    notification: Notification, verification: Verification
) -> list[dict[str, Any]]:
    if verification.partial_reason is not None:
        body = [
            _headline("Validação parcial", notification, "Warning"),
            _text(f"Motivo: {PARTIAL_REASON_TEXT[verification.partial_reason]}"),
            *_identity(notification),
        ]
    elif verification.root_cause:
        body = [_headline("Causa raiz", notification, "Good"), *_identity(notification)]
    else:
        body = [
            _headline("Causa raiz não confirmada", notification, "Warning"),
            *_identity(notification),
        ]
    if verification.root_cause:
        body.append(_section("Causa raiz", [_clip(verification.root_cause)]))
    if verification.verdicts:
        verdicts = [_verdict(v) for v in verification.verdicts]
        body.append(_section("Hipóteses", _limit(verdicts, MAX_HYPOTHESES)))
    if verification.actions:
        steps = [_action(a) for a in verification.actions]
        body.append(_section("Plano de ação", _limit(steps, MAX_PLAN_STEPS)))
    unconfirmed = verification.partial_reason is None and not verification.root_cause
    if unconfirmed and verification.missing.strip():  # na parcial, o motivo já está no topo
        body.append(_section("O que faltou", [_clip(verification.missing)]))
    return body


VERIFICATION_RUNNING = "Validação em andamento: a causa raiz aparecerá na página do incidente."
NO_ROOT_CAUSE_TEXT = "Causa raiz não confirmada."

# O Workflow do Teams recusa mensagens grandes; o card resume e a página do incidente tem tudo.
MAX_EVALUATIONS = 8
MAX_HYPOTHESES = 3
MAX_PLAN_STEPS = 5
MAX_LINE = 280
MAX_EVIDENCE = 2


def _limit(lines: list[str], most: int) -> list[str]:
    if len(lines) <= most:
        return lines
    return [*lines[:most], f"- … mais {len(lines) - most} na página do incidente"]


def _clip(text: str) -> str:
    """Uma linha só e curta: quebra de linha no meio desmontaria a lista do card."""
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_LINE else flat[: MAX_LINE - 1] + "…"


def _headline(text: str, notification: Notification, color: str) -> dict[str, Any]:
    return {
        "type": "TextBlock",
        "text": f"{text} · {notification.severity}",
        "weight": "Bolder",
        "color": color,
        "size": "Medium",
    }


def _identity(notification: Notification) -> list[dict[str, Any]]:
    return [
        {"type": "TextBlock", "text": notification.title, "weight": "Bolder", "wrap": True},
        {
            "type": "FactSet",
            "facts": [
                {"title": "Severidade", "value": str(notification.severity)},
                {"title": "Serviços", "value": ", ".join(notification.services)},
            ],
        },
    ]


def _text(text: str) -> dict[str, Any]:
    return {"type": "TextBlock", "text": text, "wrap": True}


def _section(title: str, lines: list[str]) -> dict[str, Any]:
    return {
        "type": "Container",
        "separator": True,
        "items": [
            {"type": "TextBlock", "text": title, "weight": "Bolder", "wrap": True},
            _text("\n".join(lines)),
        ],
    }


def _evaluation(evaluation: Evaluation) -> str:
    line = f"- {evaluation.subject}: {_clip(evaluation.finding)}"
    return f"{line} ([abrir]({evaluation.url}))" if evaluation.url else line


def _hypothesis(hypothesis: Hypothesis) -> str:
    line = f"- {_clip(hypothesis.title)} (confiança {hypothesis.confidence:.0%})"
    evidence = "; ".join(_evidence(e) for e in hypothesis.evidence[:MAX_EVIDENCE])
    return f"{line}: {evidence}" if evidence else line


def _verdict(verdict: HypothesisVerdict) -> str:
    return f"- {_clip(verdict.hypothesis)}: {verdict.verdict} — {_clip(verdict.reasoning)}"


def _action(action: Action) -> str:
    line = f"- {_clip(action.action)} ({_clip(action.target)}) → {_clip(action.expected)}"
    return f"{line} ([abrir]({action.link}))" if action.link else line


def _evidence(evidence: Evidence) -> str:
    text = _clip(evidence.text)
    return f"[{text}]({evidence.url})" if evidence.url else text
