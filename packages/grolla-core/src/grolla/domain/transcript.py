"""Transcrição de call: o que a call decidiu, quem ficou com o quê e o que disse das hipóteses.

A transcrição chega como texto (colado, `.txt`, `.vtt` ou `.srt`), o modelo extrai os itens e
cada um vira um evento do incidente com a origem "transcrição". Nada aqui muda o estado: o que a
call disse das hipóteses é registro, e quem decide é o time.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath
from uuid import UUID

from grolla.domain.incidents import EventType, IncidentEvent

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


_SUBTITLE_SUFFIXES = frozenset({".vtt", ".srt"})
# Linha de tempo de legenda: "00:00:01.000 --> 00:00:04.000" (.vtt aceita "mm:ss.ttt", .srt usa
# vírgula), com ou sem os ajustes de posição depois.
_TIMING = re.compile(r"^\s*(\d+:)?\d{1,2}:\d{2}[.,]\d{3}\s+-->\s+")
_SKIPPED_BLOCKS = ("WEBVTT", "NOTE", "STYLE", "REGION")  # cabeçalho e blocos sem fala do .vtt
_VOICE = re.compile(r"^<v(?:\.[^\s>]*)?\s+([^>]+)>")  # "<v Ana>" ou "<v.loud Ana>"
_TAG = re.compile(r"</?[^>]*>")  # "</v>", "<i>", "<c.yellow>", "<00:00:01.000>"


def clean_transcript(raw: str, filename: str | None) -> str:
    """O texto falado: tira o cabeçalho WEBVTT, os números de bloco e as linhas de tempo de
    `.vtt`/`.srt` e mantém "Pessoa: fala"; texto puro passa como está (sem espaços nas pontas).

    É legenda pelo nome do arquivo (`.vtt`, `.srt`) ou pelo conteúdo (começa com WEBVTT ou tem
    uma linha de tempo). Cada bloco de fala vira uma linha; `<v Nome>` vira "Nome: ".
    """
    text = raw.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not _is_subtitle(text, filename):
        return text
    lines: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        cue = _cue_text([line.strip() for line in block.splitlines() if line.strip()])
        if cue:
            lines.append(cue)
    return "\n".join(lines)


def _is_subtitle(text: str, filename: str | None) -> bool:
    if filename is not None and PurePath(filename).suffix.lower() in _SUBTITLE_SUFFIXES:
        return True
    return text.startswith("WEBVTT") or any(_TIMING.match(line) for line in text.splitlines())


def _cue_text(block: list[str]) -> str:
    """A fala de um bloco: o que vem depois da linha de tempo (o número ou o id do bloco vêm
    antes); bloco sem linha de tempo é cabeçalho ou nota do .vtt, ou texto solto que fica.
    """
    if not block or block[0].split(" ", 1)[0] in _SKIPPED_BLOCKS:
        return ""
    timing = next((i for i, line in enumerate(block) if _TIMING.match(line)), None)
    spoken = block if timing is None else block[timing + 1 :]
    text = " ".join(spoken)
    voice = _VOICE.match(text)
    speaker = ""
    if voice is not None:
        speaker, text = f"{voice.group(1).strip()}: ", text[voice.end() :]
    text = " ".join(_TAG.sub("", text).split())
    return f"{speaker}{text}" if text else ""


_CLOCK = re.compile(r"\b(\d{1,2})[:h](\d{2})\b")
_NO_TIME = "99:99"  # sem horário dito: depois dos que têm


def said_time(said_at: str | None) -> str | None:
    """O horário dito como HH:MM ("3:07" e "3h07" viram "03:07"); outro texto fica como veio."""
    said = (said_at or "").strip()
    found = _CLOCK.fullmatch(said)
    return f"{int(found.group(1)):02d}:{found.group(2)}" if found else said or None


def transcript_events(
    incident_id: UUID, transcript: Transcript, draft: TranscriptDraft, now: datetime
) -> tuple[IncidentEvent, ...]:
    """Um TRANSCRIPT_ITEM por item (na ordem dos horários ditos, em HH:MM),
    TRANSCRIPT_PARTICIPANTS (só com nomes) e um TRANSCRIPT_HYPOTHESIS por hipótese, todos com
    autor TRANSCRIPT_ORIGIN e, no data, `"origin": TRANSCRIPT_ORIGIN` e o id da transcrição
    (`"transcript"`). Itens e hipóteses sem texto ficam de fora.
    """

    def event(kind: EventType, data: dict[str, object]) -> IncidentEvent:
        data = {**data, "origin": TRANSCRIPT_ORIGIN, "transcript": transcript.id}
        return IncidentEvent(incident_id, now, kind, data, TRANSCRIPT_ORIGIN)

    items = sorted(
        (replace(i, said_at=said_time(i.said_at)) for i in draft.items if i.text.strip()),
        key=lambda i: i.said_at if i.said_at and _CLOCK.fullmatch(i.said_at) else _NO_TIME,
    )
    events = [
        event(
            EventType.TRANSCRIPT_ITEM,
            {
                "kind": i.kind.value,
                "text": i.text.strip(),
                "owner": (i.owner or "").strip() or None,
                "said_at": i.said_at,
                "excerpt": i.excerpt.strip(),
            },
        )
        for i in items
    ]
    names = list(dict.fromkeys(n.strip() for n in draft.participants if n.strip()))
    if names:
        events.append(event(EventType.TRANSCRIPT_PARTICIPANTS, {"names": names}))
    events.extend(
        event(
            EventType.TRANSCRIPT_HYPOTHESIS,
            {
                "hypothesis": h.hypothesis.strip(),
                "verdict": h.verdict.value,
                "excerpt": h.excerpt.strip(),
            },
        )
        for h in draft.hypotheses
        if h.hypothesis.strip()
    )
    return tuple(events)


def transcript_items(events: Sequence[IncidentEvent]) -> list[TranscriptItem]:
    """Os itens de transcrição do log, na ordem."""
    return [
        TranscriptItem(
            TranscriptItemKind(e.data["kind"]),
            str(e.data["text"]),
            e.data.get("owner"),
            e.data.get("said_at"),
            str(e.data.get("excerpt", "")),
        )
        for e in events
        if e.type is EventType.TRANSCRIPT_ITEM
    ]
