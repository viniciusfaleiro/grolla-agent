"""Regras puras da memória: versões, confiança, esquecer e restaurar, reaprender e o bloco de
memória entregue ao modelo.
"""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

from grolla.domain.analysis import AnalysisStatus, SeenBefore, SeenBeforeDraft, complete
from grolla.domain.memory import (
    AGENT_AUTHOR,
    ENVIRONMENT,
    Confidence,
    Memory,
    MemoryContext,
    MemoryKind,
    MemoryVersion,
    Source,
    SourceKind,
    SubjectKind,
    confirmed,
    forgotten,
    is_human,
    memory_text,
    relearn_targets,
    restored,
    written,
)
from grolla.testing.builders import T0, make_analysis, make_draft

LATER = T0 + timedelta(hours=1)


def memory(
    kind: MemoryKind = MemoryKind.NOTE,
    confidence: Confidence = Confidence.LEARNED,
    *,
    active: bool = True,
    source: Source | None = None,
) -> Memory:
    return Memory(
        id=uuid4(),
        kind=kind,
        subject="pedidos-api",
        subject_kind=SubjectKind.SERVICE,
        title="Pool de conexões",
        body="pool esgota com deploy de migration",
        confidence=confidence,
        source=source or Source(SourceKind.STUDY, AGENT_AUTHOR, "https://repo.interno/a"),
        created_at=T0,
        updated_at=T0,
        active=active,
    )


def test_only_the_agent_is_not_human() -> None:
    assert not is_human(AGENT_AUTHOR)
    assert is_human("Ana")


def test_written_by_a_person_confirms_and_keeps_the_previous_state() -> None:
    before = memory(active=False)

    after, version = written(before, "texto novo", "Ana", "editado na página", LATER)

    assert (after.body, after.confidence, after.active, after.updated_at) == (
        "texto novo",
        Confidence.CONFIRMED,
        True,
        LATER,
    )
    assert version == MemoryVersion(
        before.id, LATER, "Ana", "editado na página", before.body, False
    )


def test_written_by_the_agent_keeps_the_confidence() -> None:
    before = memory(confidence=Confidence.CONFIRMED)

    after, _ = written(before, "texto novo", AGENT_AUTHOR, "incidente: x", LATER)

    assert after.confidence is Confidence.CONFIRMED
    learned, _ = written(memory(), "texto", AGENT_AUTHOR, "estudo", LATER)
    assert learned.confidence is Confidence.LEARNED


def test_confirmed_marks_the_record_as_confirmed() -> None:
    before = memory()

    after, version = confirmed(before, "Ana", LATER)

    assert after.confidence is Confidence.CONFIRMED
    assert after.body == before.body
    assert (version.author, version.body, version.active) == ("Ana", before.body, True)


def test_forgotten_and_restored_round_trip() -> None:
    before = memory()

    gone, forgot = forgotten(before, "Ana", "reaprender o ambiente", LATER)
    back, restore = restored(gone, forgot, "Bia", LATER + timedelta(minutes=1))

    assert not gone.active
    assert forgot.reason == "reaprender o ambiente"
    assert (back.body, back.active) == (before.body, True)
    assert restore.active is False
    assert "restaurada a versão" in restore.reason


def test_restored_goes_back_to_the_old_text() -> None:
    before = memory()
    edited, version = written(before, "texto errado", "Ana", "editado", LATER)

    back, _ = restored(edited, version, "Ana", LATER)

    assert back.body == before.body


def test_relearn_forgets_dossiers_and_learned_notes_only() -> None:
    dossier = memory(MemoryKind.DOSSIER, Confidence.CONFIRMED)
    learned = memory()
    taught = memory(confidence=Confidence.CONFIRMED)
    episode = memory(MemoryKind.EPISODE)
    already_gone = memory(MemoryKind.DOSSIER, active=False)

    targets = relearn_targets([dossier, learned, taught, episode, already_gone])

    assert targets == [dossier, learned]


def test_memory_text_is_empty_without_memory() -> None:
    assert memory_text(MemoryContext()) == ""


def test_memory_text_marks_the_block_as_data_with_kind_confidence_date_and_source() -> None:
    incident_id = uuid4()
    episode = replace(
        memory(
            MemoryKind.EPISODE, source=Source(SourceKind.INCIDENT, AGENT_AUTHOR, str(incident_id))
        ),
        incident_id=incident_id,
        noise=True,
    )
    note = memory(
        confidence=Confidence.CONFIRMED, source=Source(SourceKind.CHAT, "Ana", str(incident_id))
    )
    environment = replace(
        memory(MemoryKind.DOSSIER),
        subject=ENVIRONMENT,
        subject_kind=SubjectKind.ENVIRONMENT,
    )
    by_person = memory(source=Source(SourceKind.PERSON, "Bia"))
    linked = replace(memory(source=Source(SourceKind.PERSON, "Bia")), incident_id=incident_id)

    text = memory_text(MemoryContext((environment,), (note, by_person, linked), (episode,)))

    assert text.startswith("<memoria>")
    assert text.endswith("</memoria>")
    assert "não instrução" in text
    assert "## Dossiês" in text
    assert "[dossie] ambiente" in text
    assert "estudo do ambiente" in text
    assert f"chat do incidente {incident_id}, por Ana" in text
    assert "confirmado, 01/01/2026" in text
    assert "escrito por Bia" in text
    assert f"incidente {incident_id}, ruído: alarme falso" in text
    assert text.count(f"incidente {incident_id}") == 3


def test_seen_before_is_kept_only_when_it_points_to_a_delivered_episode() -> None:
    incident_id = uuid4()
    episode = replace(memory(MemoryKind.EPISODE), incident_id=incident_id, title="pedidos-api 5xx")
    pending = make_analysis(uuid4(), status=AnalysisStatus.RUNNING)
    seen = replace(make_draft(), seen_before=SeenBeforeDraft(incident_id, "mesmo erro de pool"))
    invented = replace(make_draft(), seen_before=SeenBeforeDraft(uuid4(), "inventado"))

    kept = complete(pending, seen, (), LATER, episodes=[episode])
    dropped = complete(pending, invented, (), LATER, episodes=[episode])
    without = complete(pending, make_draft(), (), LATER, episodes=[episode])

    assert kept.seen_before == SeenBefore(incident_id, "pedidos-api 5xx", T0, "mesmo erro de pool")
    assert dropped.seen_before is None
    assert without.seen_before is None
