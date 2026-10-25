"""Análise inicial de um incidente: o que foi avaliado, hipóteses e plano proposto."""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

# O que o card "investigando" promete checar; é o roteiro curto da análise inicial.
INITIAL_CHECKS = ("métricas do serviço", "deploys recentes", "traces com erro", "logs de erro")


class AnalysisStatus(StrEnum):
    PENDING = "pendente"
    RUNNING = "em_andamento"
    COMPLETED = "concluida"
    PARTIAL = "parcial"


class PartialReason(StrEnum):
    LLM_FAILED = "falha_do_llm"
    TIMEOUT = "tempo_esgotado"
    BUDGET = "orcamento_esgotado"
    INVALID_RESULT = "resultado_invalido"
    INTERRUPTED = "interrompida"


# Como o motivo aparece para o time: "análise parcial: tempo esgotado".
PARTIAL_REASON_TEXT = {
    PartialReason.LLM_FAILED: "LLM indisponível",
    PartialReason.TIMEOUT: "tempo esgotado",
    PartialReason.BUDGET: "orçamento de tokens esgotado",
    PartialReason.INVALID_RESULT: "resultado inválido",
    PartialReason.INTERRUPTED: "interrompida",
}


@dataclass(frozen=True)
class Evidence:
    text: str
    url: str | None


@dataclass(frozen=True)
class Evaluation:
    """Uma chamada de ferramenta realmente executada durante a análise."""

    tool: str  # query_metric | search_logs | find_traces | recent_deploys
    subject: str  # ex.: "taxa_de_erro de authorizer-api, últimos 60 min"
    finding: str  # resumo devolvido pela ferramenta ou "não foi possível consultar"
    url: str | None
    ok: bool


@dataclass(frozen=True)
class Hypothesis:
    title: str
    confidence: float  # 0..1
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class AnalysisDraft:
    """O que o modelo de linguagem devolve, já validado."""

    summary: str
    hypotheses: tuple[Hypothesis, ...]
    plan: tuple[str, ...]


@dataclass(frozen=True)
class Analysis:
    incident_id: UUID
    status: AnalysisStatus
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    summary: str
    evaluations: tuple[Evaluation, ...]
    hypotheses: tuple[Hypothesis, ...]  # maior confiança primeiro
    plan: tuple[str, ...]
    partial_reason: PartialReason | None


def complete(
    pending: Analysis, draft: AnalysisDraft, evaluations: Sequence[Evaluation], now: datetime
) -> Analysis:
    """Análise concluída com o rascunho do modelo e as avaliações reais.

    O modelo escreve as evidências, mas um link só é mantido se veio de uma avaliação: link
    que nenhuma ferramenta devolveu pode ter sido inventado.
    """
    known = {e.url for e in evaluations if e.url}
    hypotheses = (
        replace(
            h,
            evidence=tuple(replace(e, url=e.url if e.url in known else None) for e in h.evidence),
        )
        for h in draft.hypotheses
    )
    return replace(
        pending,
        status=AnalysisStatus.COMPLETED,
        finished_at=now,
        summary=draft.summary,
        evaluations=tuple(evaluations),
        hypotheses=tuple(sorted(hypotheses, key=lambda h: h.confidence, reverse=True)),
        plan=draft.plan,
        partial_reason=None,
    )


def partial(
    pending: Analysis, reason: PartialReason, evaluations: Sequence[Evaluation], now: datetime
) -> Analysis:
    """Análise encerrada sem rascunho válido: mantém o que foi avaliado e o motivo."""
    return replace(
        pending,
        status=AnalysisStatus.PARTIAL,
        finished_at=now,
        summary=f"Análise parcial: {PARTIAL_REASON_TEXT[reason]}.",
        evaluations=tuple(evaluations),
        hypotheses=(),
        plan=(),
        partial_reason=reason,
    )


# ---- Validação: segunda fase, que confirma ou rejeita cada hipótese até a causa raiz ----


class Verdict(StrEnum):
    CONFIRMED = "confirmada"
    REJECTED = "rejeitada"
    INCONCLUSIVE = "inconclusiva"


@dataclass(frozen=True)
class HypothesisVerdict:
    hypothesis: str  # título da hipótese da análise inicial
    verdict: Verdict
    reasoning: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class Action:
    """Ação concreta para o humano validar e executar; o agente não executa nada."""

    action: str  # imperativo: "Fazer rollback do pedidos-api para o build 20261025.1"
    target: str  # componente ou recurso: "pedidos-api", "RDS grolla-lab-db"
    expected: str  # efeito esperado
    link: str | None  # só se veio de uma avaliação


@dataclass(frozen=True)
class VerificationDraft:
    """O que o modelo devolve na validação, já validado."""

    verdicts: tuple[HypothesisVerdict, ...]
    root_cause: str | None  # None: causa não confirmada
    actions: tuple[Action, ...]  # vazio quando root_cause é None
    missing: str  # o que faltou para confirmar ("" quando confirmada)


@dataclass(frozen=True)
class Verification:
    incident_id: UUID
    status: AnalysisStatus  # mesmos estados da análise
    requested_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    verdicts: tuple[HypothesisVerdict, ...]
    root_cause: str | None
    actions: tuple[Action, ...]
    missing: str
    evaluations: tuple[Evaluation, ...]
    partial_reason: PartialReason | None


class NotConcreteError(ValueError):
    """O plano traz passo de investigação, ou ações sem causa confirmada."""


# Verbos de passo de investigação: no plano da validação só entram ações diretas.
INVESTIGATION_VERBS = frozenset(
    {
        "investigar",
        "verificar",
        "checar",
        "analisar",
        "consultar",
        "confirmar",
        "avaliar",
        "revisar",
        "examinar",
        "observar",
        "acompanhar",
        "monitorar",
    }
)


# Radicais dos verbos acima, para pegar também o imperativo ("Verifique", "Cheque", "Avalie").
_INVESTIGATION_STEMS = (
    "investig",
    "verifi",
    "chec",
    "chequ",
    "analis",
    "consult",
    "confirm",
    "avali",
    "revis",
    "examin",
    "observ",
    "acompanh",
    "monitor",
)


def _is_investigation(word: str) -> bool:
    return word in INVESTIGATION_VERBS or word.startswith(_INVESTIGATION_STEMS)


def _first_word(text: str) -> str:
    """Primeira palavra, minúscula e sem acento: "Verificar a saúde" -> "verificar"."""
    words = text.split()
    if not words:
        return ""
    decomposed = unicodedata.normalize("NFKD", words[0].casefold())
    return "".join(c for c in decomposed if c.isalnum())


def check_plan(draft: VerificationDraft) -> None:
    """Plano só com ações concretas e só quando há causa confirmada; levanta NotConcreteError."""
    if draft.root_cause is None:
        if draft.actions:
            raise NotConcreteError("ações sem causa raiz confirmada")
        return
    if not draft.actions:
        raise NotConcreteError("causa raiz confirmada sem nenhuma ação")
    for action in draft.actions:
        if _is_investigation(_first_word(action.action)):
            raise NotConcreteError(f"passo de investigação no plano: {action.action}")


def _known_link(url: str | None, known: set[str]) -> str | None:
    return url if url in known else None


def verified(
    pending: Verification,
    draft: VerificationDraft,
    evaluations: Sequence[Evaluation],
    now: datetime,
) -> Verification:
    """Validação concluída; links de evidência e de ação só se vieram de uma avaliação.

    Levanta NotConcreteError se o plano não passa no `check_plan`.
    """
    check_plan(draft)
    known = {e.url for e in evaluations if e.url}
    verdicts = tuple(
        replace(
            v,
            evidence=tuple(replace(e, url=_known_link(e.url, known)) for e in v.evidence),
        )
        for v in draft.verdicts
    )
    actions = tuple(replace(a, link=_known_link(a.link, known)) for a in draft.actions)
    return replace(
        pending,
        status=AnalysisStatus.COMPLETED,
        finished_at=now,
        verdicts=verdicts,
        root_cause=draft.root_cause,
        actions=actions,
        missing=draft.missing,
        evaluations=tuple(evaluations),
        partial_reason=None,
    )


def verification_partial(
    pending: Verification, reason: PartialReason, evaluations: Sequence[Evaluation], now: datetime
) -> Verification:
    """Validação encerrada sem rascunho válido: mantém o que foi avaliado e o motivo."""
    return replace(
        pending,
        status=AnalysisStatus.PARTIAL,
        finished_at=now,
        verdicts=(),
        root_cause=None,
        actions=(),
        missing=f"Validação parcial: {PARTIAL_REASON_TEXT[reason]}.",
        evaluations=tuple(evaluations),
        partial_reason=reason,
    )
