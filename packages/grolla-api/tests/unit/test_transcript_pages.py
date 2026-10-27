"""Transcrição da call no war room: envio, itens na timeline com a origem e reprocessar."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import grolla_api.app
import pytest
from fastapi.testclient import TestClient
from grolla.application.transcript import TranscriptPolicy
from grolla.domain.incidents import EventType, Incident, IncidentEvent, Severity
from grolla.domain.transcript import TRANSCRIPT_ORIGIN, Transcript
from grolla.domain.war_room import InvalidActionError
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla.testing.transcript import InMemoryTranscriptStore
from grolla_api.app import Services, create_app
from grolla_api.war_room import (
    AUTHOR_COOKIE,
    event_detail,
    event_label,
    failed_transcript,
    reading_transcript,
)

NOW = T0 + timedelta(minutes=20)
MAX_CHARS = 1_000


@dataclass
class TranscriptStub:
    """Dublê do `upload_transcript` e do `retry_transcript`: registra a chamada ou levanta."""

    uploads: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    retries: list[tuple[int, str]] = field(default_factory=list[tuple[int, str]])
    error: Exception | None = None

    async def upload(self, **kwargs: Any) -> Transcript:
        self.uploads.append(kwargs)
        if self.error is not None:
            raise self.error
        return Transcript(1, kwargs["incident_id"], kwargs["author"], kwargs["raw"], NOW)

    async def retry(self, *, store: Any, transcript_id: int, author: str, clock: Any) -> None:
        del store, clock  # o dublê só registra
        self.retries.append((transcript_id, author))
        if self.error is not None:
            raise self.error


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def store(repository: InMemoryIncidentRepository) -> InMemoryTranscriptStore:
    """O fake do port; os casos de uso que o usam são dublados abaixo."""
    return InMemoryTranscriptStore(repository)


@pytest.fixture(autouse=True)
def stub(monkeypatch: pytest.MonkeyPatch) -> TranscriptStub:
    stub = TranscriptStub()
    monkeypatch.setattr(grolla_api.app, "upload_transcript", stub.upload)
    monkeypatch.setattr(grolla_api.app, "retry_transcript", stub.retry)
    return stub


@pytest.fixture
def incident(repository: InMemoryIncidentRepository) -> Incident:
    found = make_incident(severity=Severity.P1)
    repository.incidents[found.id] = found
    return found


@pytest.fixture
def client(
    services: Services, repository: InMemoryIncidentRepository, store: InMemoryTranscriptStore
) -> TestClient:
    app = create_app(
        replace(
            services,
            repository=repository,
            clock=FakeClock(NOW),
            transcripts=store,
            transcript_policy=TranscriptPolicy(max_chars=MAX_CHARS),
        )
    )
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def url(incident: Incident) -> str:
    return f"/incidents/{incident.id}/transcricao"


# ---- envio


def test_pasted_text_is_sent_and_goes_back_to_the_incident(
    ana: TestClient, incident: Incident, stub: TranscriptStub
) -> None:
    response = ana.post(url(incident), data={"texto": "Pedro: vamos fazer o rollback"})

    assert response.status_code == 303
    assert response.headers["location"] == f"/incidents/{incident.id}"
    [call] = stub.uploads
    assert call["raw"] == "Pedro: vamos fazer o rollback"
    assert call["filename"] is None
    assert call["author"] == "Ana"
    assert call["incident_id"] == incident.id
    assert call["policy"].max_chars == MAX_CHARS


def test_file_wins_over_the_text_and_loses_the_bom(
    ana: TestClient, incident: Incident, stub: TranscriptStub
) -> None:
    vtt = "﻿WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nPedro: vamos fazer o rollback\n"

    response = ana.post(
        url(incident),
        data={"texto": "ignorado"},
        files={"arquivo": ("call.vtt", vtt.encode(), "text/vtt")},
    )

    assert response.status_code == 303
    [call] = stub.uploads
    assert call["raw"] == vtt.removeprefix("﻿")
    assert call["filename"] == "call.vtt"


def test_empty_file_field_falls_back_to_the_text(
    ana: TestClient, incident: Incident, stub: TranscriptStub
) -> None:
    response = ana.post(
        url(incident),
        data={"texto": "Ana: fica com o Pedro"},
        files={"arquivo": ("", b"", "application/octet-stream")},
    )

    assert response.status_code == 303
    assert stub.uploads[0]["raw"] == "Ana: fica com o Pedro"


def test_htmx_upload_navigates_by_hx_redirect(ana: TestClient, incident: Incident) -> None:
    response = ana.post(url(incident), data={"texto": "x"}, headers={"HX-Request": "true"})

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/incidents/{incident.id}"


@pytest.mark.parametrize(
    "case",
    [
        ("call.pdf", b"%PDF", "envie um arquivo .vtt, .srt ou .txt"),
        ("call.txt", b"x" * (MAX_CHARS * 4 + 1), "passa do limite de 1000 caracteres"),
        ("call.srt", b"\xff\xfe\x00", "o arquivo precisa ser texto em UTF-8"),
    ],
)
def test_bad_file_is_refused_with_the_message(
    ana: TestClient, incident: Incident, stub: TranscriptStub, case: tuple[str, bytes, str]
) -> None:
    name, content, message = case
    response = ana.post(url(incident), files={"arquivo": (name, content, "text/plain")})

    assert response.status_code == 422
    assert message in response.text
    assert response.headers["HX-Retarget"] == "#erro-transcricao"
    assert stub.uploads == []


def test_invalid_upload_shows_the_use_case_message(
    ana: TestClient, incident: Incident, stub: TranscriptStub
) -> None:
    stub.error = InvalidActionError("a transcrição está vazia")

    response = ana.post(url(incident), data={"texto": ""})

    assert response.status_code == 422
    assert "a transcrição está vazia" in response.text


def test_upload_needs_a_name(client: TestClient, incident: Incident) -> None:
    response = client.post(url(incident), data={"texto": "x"})

    assert response.status_code == 401
    assert response.headers["HX-Retarget"] == "#erro-transcricao"


def test_upload_to_unknown_incident_is_404(ana: TestClient, stub: TranscriptStub) -> None:
    assert ana.post(f"/incidents/{uuid4()}/transcricao", data={"texto": "x"}).status_code == 404
    assert ana.post("/incidents/nao-e-id/transcricao", data={"texto": "x"}).status_code == 404
    assert stub.uploads == []


def test_incident_gone_during_upload_is_404(
    ana: TestClient, incident: Incident, stub: TranscriptStub
) -> None:
    stub.error = LookupError(incident.id)

    assert ana.post(url(incident), data={"texto": "x"}).status_code == 404


def test_without_store_the_upload_is_404(
    services: Services, repository: InMemoryIncidentRepository, incident: Incident
) -> None:
    client = TestClient(create_app(replace(services, repository=repository)))
    client.cookies.set(AUTHOR_COOKIE, "Ana")

    assert client.post(url(incident), data={"texto": "x"}).status_code == 404
    assert client.post(f"{url(incident)}/1/reprocessar").status_code == 404


def test_war_room_offers_the_upload_with_a_name(
    ana: TestClient, client: TestClient, incident: Incident
) -> None:
    page = ana.get(f"/incidents/{incident.id}").text

    assert 'popovertarget="transcricao"' in page
    assert ">Enviar transcrição</button>" in page
    assert f'hx-post="/incidents/{incident.id}/transcricao"' in page
    assert 'hx-encoding="multipart/form-data"' in page
    assert 'accept=".vtt,.srt,.txt"' in page

    client.cookies.clear()
    assert ">Enviar transcrição</button>" not in client.get(f"/incidents/{incident.id}").text


# ---- reprocessar


def saved(store: InMemoryTranscriptStore, incident_id: UUID) -> Transcript:
    """Grava pelo fake: a primeira transcrição recebe o id 1."""
    transcript = Transcript(0, incident_id, "Ana", "Pedro: vamos", T0)
    received = IncidentEvent(incident_id, T0, EventType.TRANSCRIPT_RECEIVED, {}, author="Ana")
    return asyncio.run(store.add(transcript, received))


def retry_url(incident: Incident, transcript_id: object = 1) -> str:
    return f"{url(incident)}/{transcript_id}/reprocessar"


def test_retry_asks_again_and_goes_back(
    ana: TestClient, incident: Incident, store: InMemoryTranscriptStore, stub: TranscriptStub
) -> None:
    saved(store, incident.id)

    response = ana.post(retry_url(incident))

    assert response.status_code == 303
    assert response.headers["location"] == f"/incidents/{incident.id}"
    assert stub.retries == [(1, "Ana")]


def test_retry_of_one_that_did_not_fail_shows_the_message(
    ana: TestClient, incident: Incident, store: InMemoryTranscriptStore, stub: TranscriptStub
) -> None:
    saved(store, incident.id)
    stub.error = InvalidActionError("esta transcrição não falhou")

    response = ana.post(retry_url(incident))

    assert response.status_code == 422
    assert "esta transcrição não falhou" in response.text
    assert response.headers["HX-Retarget"] == "#erro-transcricao-1"


@pytest.mark.parametrize("transcript_id", ["8", "abc"])
def test_retry_of_unknown_transcript_is_404(
    ana: TestClient,
    incident: Incident,
    store: InMemoryTranscriptStore,
    stub: TranscriptStub,
    transcript_id: str,
) -> None:
    saved(store, incident.id)

    assert ana.post(retry_url(incident, transcript_id)).status_code == 404
    assert stub.retries == []


def test_retry_of_another_incidents_transcript_is_404(
    ana: TestClient, incident: Incident, store: InMemoryTranscriptStore, stub: TranscriptStub
) -> None:
    saved(store, uuid4())

    assert ana.post(retry_url(incident)).status_code == 404
    assert stub.retries == []


def test_retry_needs_a_name(
    client: TestClient, incident: Incident, store: InMemoryTranscriptStore
) -> None:
    saved(store, incident.id)

    response = client.post(retry_url(incident))

    assert response.status_code == 401
    assert response.headers["HX-Retarget"] == "#erro-transcricao-1"


# ---- timeline


def event(incident: Incident, kind: EventType, data: dict[str, Any], author: str) -> IncidentEvent:
    return IncidentEvent(incident.id, NOW, kind, data, author=author)


def origin(data: dict[str, Any]) -> dict[str, Any]:
    return {**data, "origin": TRANSCRIPT_ORIGIN}


def received(incident: Incident, truncated: bool = False) -> IncidentEvent:
    data = {"transcript": 7, "chars": 1234, "truncated": truncated}
    return event(incident, EventType.TRANSCRIPT_RECEIVED, data, "Ana")


def from_call(incident: Incident, kind: EventType, data: dict[str, Any]) -> IncidentEvent:
    return event(incident, kind, origin(data), TRANSCRIPT_ORIGIN)


def decision(incident: Incident) -> IncidentEvent:
    data = {
        "kind": "decisao",
        "text": "fazer o rollback",
        "owner": None,
        "said_at": "03:12",
        "excerpt": "Pedro: vamos fazer o rollback agora, 03:12",
    }
    return from_call(incident, EventType.TRANSCRIPT_ITEM, data)


def call_events(incident: Incident) -> list[IncidentEvent]:
    action = {
        "kind": "acao",
        "text": "abrir chamado com o banco",
        "owner": "Pedro",
        "said_at": None,
        "excerpt": "Ana: fica com o Pedro abrir o chamado",
    }
    hypothesis = {
        "hypothesis": "deploy 20261027.1",
        "verdict": "descartada",
        "excerpt": "não foi o deploy, o banco caiu",
    }
    return [
        received(incident),
        decision(incident),
        from_call(incident, EventType.TRANSCRIPT_ITEM, action),
        from_call(incident, EventType.TRANSCRIPT_PARTICIPANTS, {"names": ["Ana", "Pedro"]}),
        from_call(incident, EventType.TRANSCRIPT_HYPOTHESIS, hypothesis),
    ]


def test_timeline_shows_the_call_with_the_origin(
    ana: TestClient, repository: InMemoryIncidentRepository, incident: Incident
) -> None:
    repository.events.extend(call_events(incident))

    page = ana.get(f"/incidents/{incident.id}").text

    assert "Transcrição enviada por Ana" in page
    assert "1234 caracteres" in page
    assert "Decisão (transcrição): fazer o rollback" in page
    assert "às 03:12 · “Pedro: vamos fazer o rollback agora, 03:12”" in page
    assert "Ação (transcrição): abrir chamado com o banco · Pedro" in page
    assert "Participantes da call" in page
    assert "Ana, Pedro" in page
    assert "A call descartou: deploy 20261027.1" in page
    assert "“não foi o deploy, o banco caiu”" in page
    assert page.count("origem: transcrição") == 4
    assert "por transcrição" not in page
    assert "Grolla está lendo a transcrição" not in page


def test_reading_indicator_while_nothing_came_back(
    ana: TestClient, repository: InMemoryIncidentRepository, incident: Incident
) -> None:
    repository.events.append(received(incident))

    assert "Grolla está lendo a transcrição" in ana.get(f"/incidents/{incident.id}").text


def test_failure_shows_the_reason_and_the_retry_button(
    ana: TestClient, client: TestClient, repository: InMemoryIncidentRepository, incident: Incident
) -> None:
    failed = {"transcript": 7, "reason": "o modelo não respondeu"}
    repository.events.extend(
        [received(incident), event(incident, EventType.TRANSCRIPT_FAILED, failed, "sistema")]
    )

    page = ana.get(f"/incidents/{incident.id}/timeline").text

    assert "A transcrição falhou: o modelo não respondeu" in page
    assert f'hx-post="/incidents/{incident.id}/transcricao/7/reprocessar"' in page
    assert ">Reprocessar</button>" in page
    assert 'id="erro-transcricao-7"' in page
    assert "Grolla está lendo a transcrição" not in page

    client.cookies.clear()
    assert ">Reprocessar</button>" not in client.get(f"/incidents/{incident.id}").text


# ---- rótulos


def test_labels_survive_unexpected_data(incident: Incident) -> None:
    odd_item = from_call(incident, EventType.TRANSCRIPT_ITEM, {"kind": "outro", "text": "x"})
    odd_hypothesis = from_call(
        incident, EventType.TRANSCRIPT_HYPOTHESIS, {"hypothesis": "h", "verdict": "talvez"}
    )
    no_names = from_call(incident, EventType.TRANSCRIPT_PARTICIPANTS, {"names": "Ana"})

    assert event_label(odd_item, {}) == "Item (transcrição): x"
    assert event_detail(odd_item) == ""
    assert event_label(odd_hypothesis, {}) == "A call falou da hipótese: h"
    assert event_detail(odd_hypothesis) == ""
    assert event_detail(no_names) == ""
    assert event_detail(received(incident, truncated=True)) == "1234 caracteres, cortada no limite"


def test_failed_transcript_reads_only_the_failure_with_an_id(incident: Incident) -> None:
    failed = event(incident, EventType.TRANSCRIPT_FAILED, {"transcript": 3}, "sistema")
    no_id = event(incident, EventType.TRANSCRIPT_FAILED, {"transcript": "3"}, "sistema")

    assert failed_transcript(failed) == 3
    assert failed_transcript(no_id) is None
    assert failed_transcript(decision(incident)) is None
    assert event_detail(failed) == ""


def test_reading_follows_the_last_upload(incident: Incident) -> None:
    assert not reading_transcript([])
    assert reading_transcript([received(incident)])
    assert not reading_transcript([received(incident), decision(incident)])
    assert reading_transcript([received(incident), decision(incident), received(incident)])
    assert not reading_transcript([make_event_unrelated(incident)])


def make_event_unrelated(incident: Incident) -> IncidentEvent:
    return event(incident, EventType.ACTION_RECORDED, {"text": "rollback"}, "Ana")
