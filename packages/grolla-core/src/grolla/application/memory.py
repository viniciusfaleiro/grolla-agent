"""Casos de uso da memória viva: estudar o ambiente, aprender com cada incidente encerrado e
deixar o time ler, corrigir e semear a base de conhecimento.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    AlertRule,
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    LlmUsage,
    Telemetry,
)
from grolla.application.memory_store import MemoryStore
from grolla.domain.incidents import Alert, Incident
from grolla.domain.memory import Memory, MemoryContext, SubjectKind


class MemoryNotFoundError(LookupError):
    """O registro da memória não existe."""


class InvalidMemoryError(ValueError):
    """Pedido recusado: autor ou texto vazio, versão inexistente."""


@dataclass(frozen=True)
class MemoryPolicy:
    learning_max_tokens: int = 60_000
    learning_timeout: timedelta = timedelta(minutes=3)
    study: "StudyPolicy | None" = None  # None: nenhum estudo do ambiente roda


@dataclass(frozen=True)
class StudyPolicy:
    alert_filter: Mapping[str, str]  # o domínio do time: regras com todos estes labels
    run_max_tokens: int = 400_000  # limite de uma rodada (inicial, diária ou completa)
    service_max_tokens: int = 100_000  # limite do estudo de um serviço
    lookback: timedelta = timedelta(days=7)  # janela de deploys, traces e métricas
    service_timeout: timedelta = timedelta(minutes=5)
    synthesis_timeout: timedelta = timedelta(minutes=3)


# ---- Aprender ao encerrar ----


@dataclass(frozen=True)
class LearningJob:
    incident_id: UUID
    attempts: int  # tentativas já começadas, inclusive esta
    started_at: datetime


@dataclass(frozen=True)
class LearningFailure:
    incident_id: UUID
    title: str  # título do incidente
    reason: str
    at: datetime


class LearningQueue(Protocol):
    async def claim(self, now: datetime) -> LearningJob | None:
        """O pedido pendente mais antigo, marcado em andamento (conta uma tentativa)."""
        ...

    async def stuck(self, started_before: datetime) -> list[LearningJob]: ...

    async def finish(
        self,
        job: LearningJob,
        ok: bool,
        reason: str | None,
        usages: Sequence[LlmUsage],
        now: datetime,
    ) -> None:
        """Encerra o pedido e grava o uso; ok=False com `attempts` < LEARNING_ATTEMPTS volta
        para pendente, senão fica falho com o motivo. Só grava se ainda em andamento.
        """
        ...

    async def failed(self) -> list[LearningFailure]:
        """Aprendizados falhos, mais recentes primeiro."""
        ...


LEARNING_ATTEMPTS = 2


async def run_next_learning(  # noqa: PLR0913 — o caso de uso liga os ports do aprendizado
    *,
    queue: LearningQueue,
    memory: MemoryStore,
    repository: IncidentRepository,
    model: LanguageModel,
    clock: Clock,
    policy: MemoryPolicy,
) -> bool:
    """Aprende com o próximo incidente encerrado; False se não havia pedido.

    Escreve o episódio de cada serviço afetado (atualizando o existente se o incidente foi
    reaberto), marca ruído no descarte, grava os dossiês reescritos e marca as notas entregues
    como consolidadas. Falha nunca afeta o incidente.
    """
    raise NotImplementedError


async def reap_stuck_learnings(queue: LearningQueue, clock: Clock, policy: MemoryPolicy) -> int:
    """Encerra como falhos (interrompidos) os aprendizados presos; devolve quantos."""
    raise NotImplementedError


# ---- Estudar o ambiente ----


class StudyMode(StrEnum):
    INITIAL = "inicial"  # base vazia na subida do worker
    DAILY = "diario"
    FULL = "completo"  # pedido na página, mantém a memória
    RELEARN = "reaprender"  # pedido na página, esquece antes de estudar


class StudyStatus(StrEnum):
    PENDING = "pendente"
    RUNNING = "em_andamento"
    DONE = "concluido"
    FAILED = "falhou"


@dataclass(frozen=True)
class StudyRun:
    id: int
    mode: StudyMode
    author: str
    status: StudyStatus
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tokens: int = 0
    reason: str | None = None


@dataclass(frozen=True)
class ServiceStudy:
    service: str
    studied_at: datetime | None
    ok: bool
    reason: str | None  # falha ou "sem orçamento nesta rodada"


class StudyQueue(Protocol):
    async def request(self, mode: StudyMode, author: str, now: datetime) -> bool:
        """Pede uma rodada; False (nada muda) se já há uma pendente ou em andamento, exceto
        RELEARN, que substitui a pendente.
        """
        ...

    async def claim(self, now: datetime) -> StudyRun | None:
        """A rodada pendente mais antiga, marcada em andamento."""
        ...

    async def stuck(self, started_before: datetime) -> list[StudyRun]: ...

    async def service_order(self, services: Sequence[str], mode: StudyMode) -> list[str]:
        """A ordem de estudo: nunca estudados e os que ficaram sem orçamento ou falharam
        primeiro, depois os estudados há mais tempo. FULL e RELEARN usam a mesma ordem, mas
        estudam todos.
        """
        ...

    async def service_done(
        self,
        run: StudyRun,
        study: ServiceStudy,
        usages: Sequence[LlmUsage],
    ) -> None:
        """Grava o resultado do estudo do serviço e o uso, sem esperar o fim da rodada."""
        ...

    async def finish(
        self, run: StudyRun, status: StudyStatus, reason: str | None, now: datetime
    ) -> None:
        """Encerra a rodada; `tokens` vem do uso gravado nela."""
        ...

    async def last(self) -> StudyRun | None:
        """A rodada mais recente, em qualquer estado (página da memória)."""
        ...

    async def services(self) -> list[ServiceStudy]:
        """O último estudo de cada serviço (página da memória)."""
        ...


STUDY_AUTHOR_SYSTEM = "sistema"  # quem pede as rodadas inicial e diária


def domain_rules(rules: Sequence[AlertRule], alert_filter: Mapping[str, str]) -> list[AlertRule]:
    """As regras do domínio do time: as que têm todos os labels do filtro."""
    raise NotImplementedError


async def ensure_initial_study(
    studies: StudyQueue, memory: MemoryStore, clock: Clock, policy: MemoryPolicy
) -> bool:
    """Na subida do worker: com o estudo ligado e sem nenhum dossiê, pede a rodada inicial."""
    raise NotImplementedError


async def request_study(
    *,
    studies: StudyQueue,
    memory: MemoryStore,
    mode: StudyMode,
    author: str,
    clock: Clock,
) -> bool:
    """Pede uma rodada (página ou cron). RELEARN esquece antes, com `relearn_targets`, com o
    autor e o motivo RELEARN_REASON. Levanta InvalidMemoryError para autor vazio.
    """
    raise NotImplementedError


async def run_next_study(  # noqa: PLR0913 — o caso de uso liga os ports do estudo
    *,
    studies: StudyQueue,
    memory: MemoryStore,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    cloud: CloudInventory,
    clock: Clock,
    policy: MemoryPolicy,
) -> bool:
    """Executa a próxima rodada de estudo; False se não havia.

    Ordem: regras do domínio → serviços (das regras e os ativos com repositório) → estudo de
    cada serviço com teto próprio, parando no teto da rodada → síntese do ambiente e das
    integrações. Falha num serviço é registrada e não para os outros.
    """
    raise NotImplementedError


async def reap_stuck_studies(studies: StudyQueue, clock: Clock, policy: MemoryPolicy) -> int:
    """Encerra como falhas (interrompidas) as rodadas presas; devolve quantas."""
    raise NotImplementedError


# ---- Lembrar ----


async def memory_for(
    memory: MemoryStore | None, incident: Incident, alerts: Sequence[Alert]
) -> MemoryContext:
    """A memória para investigar o incidente; vazia sem memória ligada."""
    raise NotImplementedError


# ---- Página da memória ----


async def add_note(  # noqa: PLR0913 — a nota precisa de onde, o quê, quem e quando
    *,
    memory: MemoryStore,
    subject: str,
    subject_kind: SubjectKind,
    text: str,
    author: str,
    clock: Clock,
) -> Memory:
    """Nota confirmada escrita por uma pessoa; levanta InvalidMemoryError."""
    raise NotImplementedError


async def edit_memory(
    *, memory: MemoryStore, memory_id: UUID, body: str, author: str, clock: Clock
) -> Memory:
    """Levanta MemoryNotFoundError ou InvalidMemoryError."""
    raise NotImplementedError


async def confirm_memory(
    *, memory: MemoryStore, memory_id: UUID, author: str, clock: Clock
) -> Memory:
    raise NotImplementedError


async def forget_memory(
    *, memory: MemoryStore, memory_id: UUID, author: str, clock: Clock
) -> Memory:
    raise NotImplementedError


async def restore_memory(
    *, memory: MemoryStore, memory_id: UUID, version_at: datetime, author: str, clock: Clock
) -> Memory:
    """Volta o registro à versão gravada em `version_at`; levanta MemoryNotFoundError ou
    InvalidMemoryError (versão inexistente).
    """
    raise NotImplementedError
