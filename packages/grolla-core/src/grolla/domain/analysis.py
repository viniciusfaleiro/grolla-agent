"""Análise inicial de um incidente: o que foi avaliado, hipóteses e plano proposto."""

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from grolla.domain.memory import Memory

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
class SeenBeforeDraft:
    """O modelo diz que o incidente repete um caso da memória."""

    incident_id: UUID
    reason: str


@dataclass(frozen=True)
class SeenBefore:
    """Caso "já vi isso": o incidente passado, como a memória o registrou, e o motivo."""

    incident_id: UUID
    title: str  # título do episódio
    at: datetime  # quando o episódio foi escrito (encerramento do incidente)
    reason: str


@dataclass(frozen=True)
class AnalysisDraft:
    """O que o modelo de linguagem devolve, já validado."""

    summary: str
    hypotheses: tuple[Hypothesis, ...]
    plan: tuple[str, ...]
    seen_before: SeenBeforeDraft | None = None


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
    seen_before: SeenBefore | None = None


def complete(
    pending: Analysis,
    draft: AnalysisDraft,
    evaluations: Sequence[Evaluation],
    now: datetime,
    episodes: Sequence[Memory] = (),
) -> Analysis:
    """Análise concluída com o rascunho do modelo e as avaliações reais.

    O modelo escreve as evidências, mas um link só é mantido se veio de uma avaliação: link
    que nenhuma ferramenta devolveu pode ter sido inventado. Pelo mesmo motivo, "já vi isso"
    só é mantido se aponta um dos `episodes` entregues ao modelo.
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
        seen_before=_seen_before(draft.seen_before, episodes),
    )


def _seen_before(draft: SeenBeforeDraft | None, episodes: Sequence[Memory]) -> SeenBefore | None:
    if draft is None:
        return None
    episode = next((e for e in episodes if e.incident_id == draft.incident_id), None)
    if episode is None:
        return None
    return SeenBefore(draft.incident_id, episode.title, episode.created_at, draft.reason)


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


MAX_COMMANDS = 5  # comandos por ação
MAX_COMMAND_CHARS = 300  # por comando, numa linha, para quem confere antes de rodar


@dataclass(frozen=True)
class Command:
    """Um comando do passo a passo de uma ação; o agente não executa."""

    command: str  # exato, numa linha, na ferramenta que o time usa
    run_in: str  # onde rodar: "cluster loja-prod, namespace loja", "conta producao, us-east-1"


@dataclass(frozen=True)
class Action:
    """Ação concreta para o humano validar e executar; o agente não executa nada."""

    action: str  # imperativo: "Fazer rollback do pedidos-api para o build 20261025.1"
    target: str  # componente ou recurso: "pedidos-api", "RDS grolla-lab-db"
    expected: str  # efeito esperado
    link: str | None  # só se veio de uma avaliação
    commands: tuple[Command, ...] = ()  # o passo a passo, em ordem; vazio quando não deu


def usable_commands(raw: Iterable[tuple[str, str]]) -> tuple[Command, ...]:
    """Os comandos do modelo que cabem no plano, em ordem e no máximo MAX_COMMANDS.

    Cada um perde os espaços e as crases em volta; sai, sozinho, o que tem mais de uma linha,
    passa de MAX_COMMAND_CHARS ou vem sem onde rodar. Encadear (`&&`, `|`, `;`) numa linha vale.
    """
    kept: list[Command] = []
    for raw_command, raw_run_in in raw:
        command, run_in = raw_command.strip().strip("`").strip(), raw_run_in.strip()
        one_line = "\n" not in command and "\r" not in command
        if command and run_in and one_line and len(command) <= MAX_COMMAND_CHARS:
            kept.append(Command(command, run_in))
    return tuple(kept[:MAX_COMMANDS])


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
