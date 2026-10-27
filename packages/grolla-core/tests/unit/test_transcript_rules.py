"""Transcrição de call: limpeza do texto, eventos com a origem e a timeline do post mortem."""

from datetime import timedelta
from uuid import uuid4

import pytest
from grolla.domain.incidents import EventType, IncidentEvent
from grolla.domain.post_mortem import timeline_text
from grolla.domain.transcript import (
    TRANSCRIPT_ORIGIN,
    HypothesisCheck,
    HypothesisVerdict,
    Transcript,
    TranscriptDraft,
    TranscriptItem,
    TranscriptItemKind,
    clean_transcript,
    said_time,
    transcript_events,
    transcript_items,
)
from grolla.testing.builders import T0

VTT = """﻿WEBVTT
Kind: captions

NOTE gravado pela ferramenta de reunião
com duas linhas

STYLE
::cue { color: yellow }

1
00:00:01.000 --> 00:00:04.000 align:start
<v Ana>o banco voltou às 03:07</v>

a1b2-3
00:01:05.500 --> 00:01:09.000
<v.loud Pedro Souza>vamos fazer o rollback
agora, 03:12</v>

00:10.000 --> 00:12.000
<c.yellow>sem voz</c> <00:00:11.000>marcada
"""

SRT = """1\r
00:00:01,000 --> 00:00:04,000\r
Ana: o banco voltou às 03:07\r
\r
2\r
00:01:05,500 --> 00:01:09,000\r
<i>Pedro: vamos fazer o rollback</i>\r
"""


def test_vtt_keeps_only_who_said_what() -> None:
    assert clean_transcript(VTT, "call.vtt").splitlines() == [
        "Ana: o banco voltou às 03:07",
        "Pedro Souza: vamos fazer o rollback agora, 03:12",
        "sem voz marcada",
    ]


def test_vtt_is_recognized_by_the_header_without_a_filename() -> None:
    assert clean_transcript("WEBVTT\n\n00:01.000 --> 00:02.000\n<v Ana>fala</v>", None) == (
        "Ana: fala"
    )


def test_srt_drops_block_numbers_and_comma_timings() -> None:
    assert clean_transcript(SRT, "call.SRT").splitlines() == [
        "Ana: o banco voltou às 03:07",
        "Pedro: vamos fazer o rollback",
    ]


def test_srt_is_recognized_by_the_timing_lines_without_a_filename() -> None:
    assert clean_transcript(SRT, None).splitlines()[0] == "Ana: o banco voltou às 03:07"


def test_subtitle_extension_cleans_even_without_timing_lines() -> None:
    assert clean_transcript("WEBVTT\n\nAna: texto solto", "call.vtt") == "Ana: texto solto"


def test_plain_text_passes_as_is_without_the_edges() -> None:
    raw = "\n  Ana: o banco caiu\n1\nPedro: <ok>  \n"

    assert clean_transcript(raw, "call.txt") == "Ana: o banco caiu\n1\nPedro: <ok>"
    assert clean_transcript(raw, None) == "Ana: o banco caiu\n1\nPedro: <ok>"


def test_empty_subtitle_is_empty() -> None:
    assert clean_transcript("WEBVTT\n\n", "call.vtt") == ""


INCIDENT = uuid4()
TRANSCRIPT = Transcript(7, INCIDENT, "Ana", "Pedro: vamos fazer o rollback", T0)
NOW = T0 + timedelta(minutes=1)
ROLLBACK = TranscriptItem(
    TranscriptItemKind.DECISION, " fazer o rollback ", None, "03:12", "Pedro: vamos, 03:12"
)
POOL = TranscriptItem(
    TranscriptItemKind.ACTION, "olhar o pool", " Pedro ", None, "Ana: o Pedro fica com o pool"
)
BANK = TranscriptItem(TranscriptItemKind.TIME, "o banco voltou", None, "3:07", "Ana: 3:07")
DEPLOY = HypothesisCheck("deploy 20261027.1", HypothesisVerdict.DISCARDED, "não foi o deploy")


def draft(
    items: tuple[TranscriptItem, ...] = (ROLLBACK, POOL, BANK),
    participants: tuple[str, ...] = ("Ana", " Pedro", "Ana", ""),
    hypotheses: tuple[HypothesisCheck, ...] = (DEPLOY,),
) -> TranscriptDraft:
    return TranscriptDraft(items, participants, hypotheses)


def test_events_carry_the_origin_the_said_time_and_the_excerpt_in_said_order() -> None:
    events = transcript_events(INCIDENT, TRANSCRIPT, draft(), NOW)

    assert {(e.incident_id, e.at, e.author) for e in events} == {(INCIDENT, NOW, TRANSCRIPT_ORIGIN)}
    assert all(e.data["origin"] == TRANSCRIPT_ORIGIN for e in events)
    assert all(e.data["transcript"] == TRANSCRIPT.id for e in events)
    assert [e.type for e in events] == [
        EventType.TRANSCRIPT_ITEM,
        EventType.TRANSCRIPT_ITEM,
        EventType.TRANSCRIPT_ITEM,
        EventType.TRANSCRIPT_PARTICIPANTS,
        EventType.TRANSCRIPT_HYPOTHESIS,
    ]
    bank, rollback, pool, participants, hypothesis = (dict(e.data) for e in events)
    assert bank["said_at"] == "03:07"
    assert rollback == {
        "kind": "decisao",
        "text": "fazer o rollback",
        "owner": None,
        "said_at": "03:12",
        "excerpt": "Pedro: vamos, 03:12",
        "origin": TRANSCRIPT_ORIGIN,
        "transcript": 7,
    }
    assert (pool["owner"], pool["said_at"]) == ("Pedro", None)
    assert participants["names"] == ["Ana", "Pedro"]
    assert hypothesis == {
        "hypothesis": "deploy 20261027.1",
        "verdict": "descartada",
        "excerpt": "não foi o deploy",
        "origin": TRANSCRIPT_ORIGIN,
        "transcript": 7,
    }


def test_no_participants_event_without_names_and_blank_items_are_dropped() -> None:
    blank = TranscriptItem(TranscriptItemKind.DECISION, "  ", None, None, "")
    vague = HypothesisCheck(" ", HypothesisVerdict.CONFIRMED, "")

    events = transcript_events(INCIDENT, TRANSCRIPT, draft((blank,), (" ",), (vague,)), NOW)

    assert events == ()


def test_items_come_back_from_the_log_in_order() -> None:
    events = [
        IncidentEvent(INCIDENT, T0, EventType.HUMAN_MESSAGE, {"text": "oi"}, "Ana"),
        *transcript_events(INCIDENT, TRANSCRIPT, draft(), NOW),
    ]

    assert transcript_items(events) == [
        TranscriptItem(TranscriptItemKind.TIME, "o banco voltou", None, "03:07", "Ana: 3:07"),
        TranscriptItem(
            TranscriptItemKind.DECISION, "fazer o rollback", None, "03:12", "Pedro: vamos, 03:12"
        ),
        TranscriptItem(
            TranscriptItemKind.ACTION, "olhar o pool", "Pedro", None, "Ana: o Pedro fica com o pool"
        ),
    ]


def test_post_mortem_timeline_shows_the_transcript_origin_and_the_said_time() -> None:
    received = IncidentEvent(
        INCIDENT,
        T0,
        EventType.TRANSCRIPT_RECEIVED,
        {"transcript": 7, "chars": 100, "truncated": False},
        "Ana",
    )
    confirmed = HypothesisCheck("banco fora", HypothesisVerdict.CONFIRMED, "o banco caiu")
    extracted = transcript_events(INCIDENT, TRANSCRIPT, draft(hypotheses=(DEPLOY, confirmed)), NOW)

    assert timeline_text([received, *extracted]).splitlines() == [
        "- 10:00 [pessoa] Ana: enviou a transcrição da call",
        "- 03:07 [transcrição] horário: o banco voltou",
        "- 03:12 [transcrição] decisão: fazer o rollback",
        "- 10:01 [transcrição] ação (Pedro): olhar o pool",
        "- 10:01 [transcrição] participantes da call: Ana, Pedro",
        "- 10:01 [transcrição] a call descartou: deploy 20261027.1",
        "- 10:01 [transcrição] a call confirmou: banco fora",
    ]


@pytest.mark.parametrize("reason", ["LLM indisponível", "interrompida"])
def test_failed_extraction_shows_on_the_timeline_as_the_agent(reason: str) -> None:
    failed = IncidentEvent(
        INCIDENT, T0, EventType.TRANSCRIPT_FAILED, {"transcript": 7, "reason": reason}
    )

    assert timeline_text([failed]) == f"- 10:00 [agente] não conseguiu ler a transcrição: {reason}"


@pytest.mark.parametrize(
    ("said", "normalized"),
    [("3:07", "03:07"), ("03h12", "03:12"), (" 14:05 ", "14:05"), ("de manhã", "de manhã")],
)
def test_said_time_is_normalized_when_it_is_a_clock(said: str, normalized: str) -> None:
    assert said_time(said) == normalized


def test_said_time_blank_is_none() -> None:
    assert said_time("  ") is None
    assert said_time(None) is None
