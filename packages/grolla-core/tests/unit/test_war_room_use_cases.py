"""Casos de uso do war room: ações humanas gravadas numa transação e perguntas ao agente."""

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from grolla.application.war_room import IncidentNotFoundError, act, ask
from grolla.domain.chat import ChatMessage, ReplyStatus
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState, NotificationKind
from grolla.domain.war_room import (
    AssumeCommander,
    HumanAction,
    InvalidActionError,
    RecordAction,
    Resolve,
)
from grolla.testing.builders import T0, make_incident, make_verification
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository


def page_url(incident_id: UUID) -> str:
    return f"https://grolla.interno/incidents/{incident_id}"


class World:
    def __init__(self) -> None:
        self.repository = InMemoryIncidentRepository()
        self.clock = FakeClock(T0 + timedelta(minutes=4))

    async def open_incident(self, state: IncidentState = IncidentState.DETECTED) -> UUID:
        incident = make_incident(state=state, detected_at=T0)
        async with self.repository.transaction() as uow:
            await uow.save(incident)
        return incident.id

    async def act(self, incident_id: UUID, action: HumanAction, author: str = "Ana") -> None:
        await act(
            repository=self.repository,
            incident_id=incident_id,
            action=action,
            author=author,
            clock=self.clock,
            page_url=page_url,
        )

    def cards(self) -> list[NotificationKind]:
        return [entry.notification.kind for entry in self.repository.outbox]


@pytest.fixture
def world() -> World:
    return World()


async def test_action_saves_the_incident_events_and_card(world: World) -> None:
    incident_id = await world.open_incident()

    await world.act(incident_id, AssumeCommander())

    incident = await world.repository.get_incident(incident_id)
    assert incident is not None
    assert incident.state is IncidentState.HANDLING
    assert incident.commander == "Ana"
    events = await world.repository.events_of(incident_id)
    assert [e.type for e in events] == [
        EventType.COMMANDER_ASSIGNED,
        EventType.NOTIFICATION_QUEUED,
    ]
    assert world.cards() == [NotificationKind.COMMANDER_ASSIGNED]
    assert world.repository.outbox[0].next_attempt_at == world.clock.now()


async def test_refused_action_changes_nothing(world: World) -> None:
    incident_id = await world.open_incident()
    before = await world.repository.get_incident(incident_id)

    with pytest.raises(InvalidActionError):
        await world.act(incident_id, Resolve(""))

    assert await world.repository.get_incident(incident_id) == before
    assert await world.repository.events_of(incident_id) == []
    assert world.cards() == []


async def test_unknown_incident_is_not_found(world: World) -> None:
    with pytest.raises(IncidentNotFoundError):
        await world.act(uuid4(), AssumeCommander())


async def test_closing_card_carries_root_cause_and_team_actions(world: World) -> None:
    incident_id = await world.open_incident(IncidentState.HANDLING)
    world.repository.verifications[incident_id] = make_verification(incident_id)
    await world.act(incident_id, RecordAction("fiz rollback do build .3"))
    await world.act(incident_id, RecordAction("religuei o banco"), author="Bruno")
    world.clock.advance(timedelta(minutes=31))

    await world.act(incident_id, Resolve("rollback do build .3"))

    card = world.repository.outbox[-1].notification
    assert card.kind is NotificationKind.INCIDENT_RESOLVED
    assert card.summary == "Encerrado por Ana · durou 35 min"
    assert card.evidence == (
        "Motivo: rollback do build .3\n"
        "Ações do time:\n"
        "- fiz rollback do build .3 (Ana)\n"
        "- religuei o banco (Bruno)"
    )
    assert card.verification == make_verification(incident_id)


async def test_closing_card_without_actions_or_verification(world: World) -> None:
    incident_id = await world.open_incident()

    await world.act(incident_id, Resolve("falso alarme"))

    card = world.repository.outbox[-1].notification
    assert card.evidence == "Motivo: falso alarme"
    assert card.verification is None


async def test_question_is_recorded_pending_with_its_event(world: World) -> None:
    incident_id = await world.open_incident()

    await ask(world.repository, incident_id, "Ana", " o rollback resolveu? ", world.clock)

    now = world.clock.now()
    assert await world.repository.chat_of(incident_id) == [
        ChatMessage(1, incident_id, "Ana", "o rollback resolveu?", now, (), ReplyStatus.PENDING)
    ]
    assert await world.repository.events_of(incident_id) == [
        IncidentEvent(
            incident_id,
            now,
            EventType.HUMAN_MESSAGE,
            {"message_id": 1, "text": "o rollback resolveu?"},
            "Ana",
        )
    ]


@pytest.mark.parametrize(("author", "text"), [("Ana", "  "), (" ", "pergunta")])
async def test_question_without_text_or_author_is_refused(
    world: World, author: str, text: str
) -> None:
    incident_id = await world.open_incident()

    with pytest.raises(InvalidActionError):
        await ask(world.repository, incident_id, author, text, world.clock)

    assert await world.repository.chat_of(incident_id) == []


async def test_question_to_an_unknown_incident_is_not_found(world: World) -> None:
    with pytest.raises(IncidentNotFoundError):
        await ask(world.repository, uuid4(), "Ana", "oi", world.clock)
