"""Transcrição de call: o que a call decidiu, quem ficou com o quê e o que disse das hipóteses.

Contrato da transcrição de call: os corpos são do grolla-dominio.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from grolla.domain.incidents import IncidentEvent

TRANSCRIPT_ORIGIN = "transcrição"


class TranscriptItemKind(StrEnum):
    DECISION = "decisao"
    ACTION = "acao"
    TIME = "horario"


@dataclass(frozen=True)
class TranscriptItem:
    kind: TranscriptItemKind
    text: str
    owner: str | None  # ação: quem ficou responsável
    said_at: str | None  # horário dito na call, como "03:12"
    excerpt: str  # o trecho da fala


class HypothesisVerdict(StrEnum):  # o que a call disse da hipótese
    CONFIRMED = "confirmada"
    DISCARDED = "descartada"


@dataclass(frozen=True)
class HypothesisCheck:
    hypothesis: str
    verdict: HypothesisVerdict
    excerpt: str


@dataclass(frozen=True)
class TranscriptDraft:
    items: tuple[TranscriptItem, ...]
    participants: tuple[str, ...]
    hypotheses: tuple[HypothesisCheck, ...]


@dataclass(frozen=True)
class Transcript:
    id: int  # 0 antes de gravar
    incident_id: UUID
    author: str
    text: str
    received_at: datetime


def clean_transcript(raw: str, filename: str | None) -> str:
    """O texto falado: tira o cabeçalho WEBVTT, os números de bloco e as linhas de tempo de
    `.vtt`/`.srt` e mantém "Pessoa: fala"; texto puro passa como está (sem espaços nas pontas).
    """
    raise NotImplementedError


def transcript_events(
    incident_id: UUID, transcript: Transcript, draft: TranscriptDraft, now: datetime
) -> tuple[IncidentEvent, ...]:
    """Um TRANSCRIPT_ITEM por item, TRANSCRIPT_PARTICIPANTS e um TRANSCRIPT_HYPOTHESIS por
    hipótese, todos com autor TRANSCRIPT_ORIGIN e `"origin": TRANSCRIPT_ORIGIN` no data.
    """
    raise NotImplementedError


def transcript_items(events: Sequence[IncidentEvent]) -> list[TranscriptItem]:
    """Os itens de transcrição do log, na ordem."""
    raise NotImplementedError
