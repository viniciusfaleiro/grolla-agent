"""Análise inicial de um incidente: o que foi avaliado, hipóteses e plano proposto."""

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
    PartialReason.LLM_FAILED: "falha do modelo de linguagem",
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
    """Análise concluída com o rascunho do modelo e as avaliações reais."""
    return replace(
        pending,
        status=AnalysisStatus.COMPLETED,
        finished_at=now,
        summary=draft.summary,
        evaluations=tuple(evaluations),
        hypotheses=tuple(sorted(draft.hypotheses, key=lambda h: h.confidence, reverse=True)),
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
