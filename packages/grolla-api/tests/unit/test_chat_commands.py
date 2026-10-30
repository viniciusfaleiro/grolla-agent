"""Comandos na resposta do chat do war room e do console: onde rodar, Copiar e "Apliquei"."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from grolla.domain.analysis import Command
from grolla.domain.chat import AGENT_AUTHOR, ChatMessage
from grolla.domain.console import ConsoleMessage
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.domain.safety import Scope
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.console import InMemoryConsoleStore
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app
from grolla_api.war_room import AUTHOR_COOKIE, chat_version

NOW = T0 + timedelta(minutes=20)
SCALE = "kubectl scale deployment antifraude-api --replicas=2 -n loja"
STATUS = "kubectl rollout status deployment/antifraude-api -n loja"
DELETE = "kubectl delete pod antifraude-api-7d4f -n loja"
CLUSTER = "cluster lab-k3s, namespace loja"
SUGGESTED = "Sugerido no chat"
TWO = (Command(SCALE, CLUSTER), Command(STATUS, CLUSTER))


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def store() -> InMemoryConsoleStore:
    return InMemoryConsoleStore()


@pytest.fixture
def app(
    services: Services, repository: InMemoryIncidentRepository, store: InMemoryConsoleStore
) -> TestClient:
    changed = replace(services, repository=repository, console=store, clock=FakeClock(NOW))
    return TestClient(create_app(changed), follow_redirects=False)


@pytest.fixture
def ana(app: TestClient) -> TestClient:
    app.cookies.set(AUTHOR_COOKIE, "Ana")
    return app


def with_reply(
    repository: InMemoryIncidentRepository,
    commands: tuple[Command, ...],
    state: IncidentState = IncidentState.HANDLING,
    refusal: Scope | None = None,
) -> UUID:
    incident = make_incident(state=state)
    repository.incidents = {incident.id: incident}
    question = repository.add_message(
        ChatMessage(0, incident.id, "Ana", "quais comandos e aonde?", T0), wants_reply=False
    )
    reply = ChatMessage(
        0,
        incident.id,
        AGENT_AUTHOR,
        "Escale o antifraude-api de volta para 2 réplicas.",
        T0 + timedelta(minutes=1),
        reply_to=question.id,
        refusal=refusal,
        commands=commands,
    )
    repository.add_message(reply, wants_reply=False)
    return incident.id


def chat(client: TestClient, incident_id: UUID) -> str:
    return client.get(f"/incidents/{incident_id}/chat").text


# ---- chat do war room


def test_chat_reply_shows_the_commands_in_order_with_where_to_run(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_reply(repository, TWO)

    fragment = chat(ana, incident_id)
    bubble = fragment.split("Escale o antifraude-api")[1]

    assert bubble.count('class="step-command"') == 2
    assert bubble.index(f'data-copy="{SCALE}"') < bubble.index(f'data-copy="{STATUS}"')
    assert bubble.count(f"Onde rodar: <strong>{CLUSTER}</strong>") == 2
    assert bubble.index("Onde rodar") < bubble.index(f'data-copy="{SCALE}"')
    assert bubble.count("<span>Copiar</span>") == 2
    assert "data-confirm" not in bubble
    assert "destrutivo, confira antes" not in bubble


def test_chat_applied_records_suggested_in_chat_with_the_command(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_reply(repository, TWO)

    fragment = chat(ana, incident_id)

    assert fragment.count("Apliquei") == 2
    assert f'hx-post="/incidents/{incident_id}/actions/record"' in fragment
    assert f'name="texto" value="{SUGGESTED}"' in fragment
    assert f'name="comando" value="{SCALE}"' in fragment
    assert f'name="onde" value="{CLUSTER}"' in fragment

    form = {"texto": SUGGESTED, "comando": SCALE, "onde": CLUSTER}
    ana.post(f"/incidents/{incident_id}/actions/record", data=form)

    recorded = [e for e in repository.events if e.type is EventType.ACTION_RECORDED]
    assert recorded[-1].data == {"text": SUGGESTED, "command": SCALE, "run_in": CLUSTER}
    assert recorded[-1].author == "Ana"
    after = chat(ana, incident_id)
    assert "aplicado por Ana às" in after
    assert after.count("Apliquei") == 1  # o rollout status segue com o botão


def test_applying_changes_the_chat_version_so_the_polling_redraws(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_reply(repository, TWO)
    before = chat_version(repository.messages)
    visto = f"/incidents/{incident_id}/chat?visto={before}"
    assert ana.get(visto).status_code == 204

    event = IncidentEvent(
        incident_id,
        NOW,
        EventType.ACTION_RECORDED,
        {"text": SUGGESTED, "command": SCALE, "run_in": CLUSTER},
        author="Pedro",
    )
    repository.events = [event]

    response = ana.get(visto)
    assert response.status_code == 200
    assert "aplicado por Pedro às" in response.text
    assert 'hx-trigger="every 3s, chat:refresh"' in response.text


def test_chat_applied_needs_a_name(app: TestClient, repository: InMemoryIncidentRepository) -> None:
    incident_id = with_reply(repository, TWO)

    fragment = chat(app, incident_id)

    assert f'data-copy="{SCALE}"' in fragment
    assert "Apliquei" not in fragment


def test_chat_applied_needs_an_incident_that_accepts_records(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_reply(repository, TWO, state=IncidentState.RESOLVED)

    fragment = chat(ana, incident_id)

    assert f'data-copy="{SCALE}"' in fragment
    assert "Apliquei" not in fragment


def test_refused_reply_shows_no_commands(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_reply(repository, TWO, refusal=Scope.EXECUTION)

    fragment = chat(ana, incident_id)

    assert 'class="refusal"' in fragment
    assert "step-command" not in fragment
    assert "Apliquei" not in fragment


def test_destructive_command_in_the_chat_has_the_badge_and_asks_before_copying(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_reply(repository, (Command(SCALE, CLUSTER), Command(DELETE, CLUSTER)))

    fragment = chat(ana, incident_id)

    assert fragment.count("destrutivo, confira antes") == 1
    assert f'data-copy="{DELETE}" data-confirm="' in fragment
    assert f'data-copy="{SCALE}" data-confirm' not in fragment


def test_whole_page_shows_the_chat_commands(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_reply(repository, TWO)

    page = ana.get(f"/incidents/{incident_id}").text
    conversation = page.split('id="chat-messages"')[1]

    assert f"Onde rodar: <strong>{CLUSTER}</strong>" in conversation
    assert f'name="texto" value="{SUGGESTED}"' in conversation


# ---- console


async def console_reply(ana: TestClient, store: InMemoryConsoleStore, *commands: Command) -> str:
    response = ana.post("/console", data={"texto": "como volto o antifraude-api?"})
    session_id = response.headers["location"].removeprefix("/console/")
    job = await store.claim(NOW)
    assert job is not None
    answer = ConsoleMessage(
        id=0,
        session_id=job.session_id,
        author=AGENT_AUTHOR,
        text="Escale de volta para 2 réplicas.",
        at=NOW,
        reply_to=job.message_id,
        commands=commands,
    )
    await store.answer(job, answer, [], NOW)
    return ana.get(f"/console/{session_id}").text


async def test_console_shows_the_commands_with_copy_and_no_applied(
    ana: TestClient, store: InMemoryConsoleStore
) -> None:
    page = await console_reply(ana, store, *TWO)
    bubble = page.split("Escale de volta")[1]

    assert bubble.count('class="step-command"') == 2
    assert bubble.index(f'data-copy="{SCALE}"') < bubble.index(f'data-copy="{STATUS}"')
    assert bubble.count(f"Onde rodar: <strong>{CLUSTER}</strong>") == 2
    assert bubble.count("<span>Copiar</span>") == 2
    assert "Apliquei" not in page
    assert "actions/record" not in page


async def test_console_destructive_command_has_the_badge(
    ana: TestClient, store: InMemoryConsoleStore
) -> None:
    page = await console_reply(ana, store, Command(DELETE, CLUSTER))

    assert "destrutivo, confira antes" in page
    assert f'data-copy="{DELETE}" data-confirm="' in page
