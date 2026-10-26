"""Memória viva do agente: a base de conhecimento do ambiente, os episódios e as notas.

A memória é organizada por assunto (o ambiente, cada serviço, cada integração). Cada assunto tem
um dossiê, que o agente reescreve; episódios guardam o que aconteceu em cada incidente; notas são
fatos curtos até serem incorporados ao dossiê. Nada é apagado: toda mudança guarda uma versão.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

AGENT_AUTHOR = "Grolla"
ENVIRONMENT = "ambiente"  # nome do assunto único do tipo ambiente
RELEARN_REASON = "reaprender o ambiente"


class SubjectKind(StrEnum):
    ENVIRONMENT = "ambiente"
    SERVICE = "servico"
    INTEGRATION = "integracao"


class MemoryKind(StrEnum):
    DOSSIER = "dossie"
    EPISODE = "episodio"
    NOTE = "nota"


class Confidence(StrEnum):
    LEARNED = "aprendido"  # escrito pelo agente
    CONFIRMED = "confirmado"  # escrito, editado ou confirmado por uma pessoa


class SourceKind(StrEnum):
    STUDY = "estudo"  # ref = links consultados, um por linha
    INCIDENT = "incidente"  # ref = id do incidente
    CHAT = "chat"  # ref = id do incidente; author = quem escreveu a mensagem
    PERSON = "pessoa"  # ref = None; author = quem escreveu na página


@dataclass(frozen=True)
class Source:
    kind: SourceKind
    author: str
    ref: str | None = None


@dataclass(frozen=True)
class Memory:
    id: UUID
    kind: MemoryKind
    subject: str
    subject_kind: SubjectKind
    title: str
    body: str
    confidence: Confidence
    source: Source
    created_at: datetime
    updated_at: datetime
    active: bool = True
    consolidated: bool = False  # nota já incorporada ao dossiê
    noise: bool = False  # episódio de alarme falso
    incident_id: UUID | None = None  # episódio: um por (incidente, assunto)


@dataclass(frozen=True)
class MemoryVersion:
    """O estado de um registro ANTES de uma mudança; restaurar volta a ele."""

    memory_id: UUID
    at: datetime
    author: str
    reason: str
    body: str
    active: bool


@dataclass(frozen=True)
class MemoryContext:
    """O que a investigação, a validação, o chat e o estudo recebem da memória."""

    dossiers: tuple[Memory, ...] = ()  # ambiente, serviços do incidente e integrações citadas
    notes: tuple[Memory, ...] = ()  # ativas, não consolidadas, desses assuntos
    episodes: tuple[Memory, ...] = ()  # até EPISODE_LIMIT parecidos

    @property
    def empty(self) -> bool:
        return not (self.dossiers or self.notes or self.episodes)


EPISODE_LIMIT = 5


@dataclass(frozen=True)
class DossierDraft:
    """Dossiê que o modelo de linguagem escreveu para um assunto."""

    subject: str
    subject_kind: SubjectKind
    body: str
    sources: tuple[str, ...] = ()  # links consultados


@dataclass(frozen=True)
class SynthesisDraft:
    environment: DossierDraft
    integrations: tuple[DossierDraft, ...]


@dataclass(frozen=True)
class LearningDraft:
    """O que o modelo aprendeu de um incidente encerrado."""

    episode_title: str
    episode_body: str
    noise: bool
    dossiers: tuple[DossierDraft, ...]  # serviços e integrações reescritos


def is_human(author: str) -> bool:
    """Quem não é o agente é uma pessoa do time."""
    raise NotImplementedError


def written(
    memory: Memory, body: str, author: str, reason: str, now: datetime
) -> tuple[Memory, MemoryVersion]:
    """Novo texto do registro e a versão anterior.

    Autor humano deixa o registro confirmado; o agente mantém a confiança atual. O registro
    volta ativo.
    """
    raise NotImplementedError


def confirmed(memory: Memory, author: str, now: datetime) -> tuple[Memory, MemoryVersion]:
    """O registro passa a confirmado por uma pessoa."""
    raise NotImplementedError


def forgotten(
    memory: Memory, author: str, reason: str, now: datetime
) -> tuple[Memory, MemoryVersion]:
    """O registro deixa de ser usado pelo agente, mas continua no histórico."""
    raise NotImplementedError


def restored(
    memory: Memory, version: MemoryVersion, author: str, now: datetime
) -> tuple[Memory, MemoryVersion]:
    """O registro volta ao texto e ao estado (ativo ou não) de uma versão anterior."""
    raise NotImplementedError


def relearn_targets(records: Sequence[Memory]) -> list[Memory]:
    """O que reaprender esquece: dossiês e notas aprendidas ativos.

    Episódios (o que aconteceu) e notas confirmadas (o que o time ensinou) ficam.
    """
    raise NotImplementedError


def memory_text(context: MemoryContext) -> str:
    """Bloco <memoria> marcado como dado, com tipo, assunto, confiança, data e fonte de cada
    registro; vazio quando não há memória.
    """
    raise NotImplementedError
