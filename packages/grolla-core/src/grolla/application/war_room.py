"""Casos de uso do war room: ações humanas e perguntas ao agente."""

from collections.abc import Callable
from dataclasses import replace
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.domain.chat import ChatMessage
from grolla.domain.incidents import (
    EventType,
    Incident,
    IncidentEvent,
    Notification,
    NotificationKind,
)
from grolla.domain.war_room import HumanAction, InvalidActionError, decide_action


class IncidentNotFoundError(LookupError):
    """O incidente da ação não existe."""


async def act(  # noqa: PLR0913 — a ação precisa de quem, o quê, onde e quando
    *,
    repository: IncidentRepository,
    incident_id: UUID,
    action: HumanAction,
    author: str,
    clock: Clock,
    page_url: Callable[[UUID], str],
) -> Incident:
    """Numa transação, trava o incidente, decide a ação e grava incidente, eventos e card.

    Levanta InvalidActionError (ação recusada) ou IncidentNotFoundError.
    """
    async with repository.transaction() as uow:
        incident = await uow.incident_for_update(incident_id)
        if incident is None:
            raise IncidentNotFoundError(f"incidente {incident_id} não existe")
        now = clock.now()
        decision = decide_action(incident, action, author, now, page_url)
        updated = decision.incident or incident  # toda ação devolve o incidente
        await uow.save(updated)
        await uow.append(decision.events)
        for notification in decision.notifications:
            if notification.kind is NotificationKind.INCIDENT_RESOLVED:
                notification = await _closing_card(repository, notification)  # noqa: PLW2901
            await uow.enqueue(notification, now)
    return updated


async def _closing_card(repository: IncidentRepository, card: Notification) -> Notification:
    """O card de encerramento leva a causa raiz da validação e as ações registradas."""
    events = await repository.events_of(card.incident_id)
    actions = [
        f"- {e.data['text']} ({e.author})" for e in events if e.type is EventType.ACTION_RECORDED
    ]
    evidence = "\n".join([card.evidence, "Ações do time:", *actions]) if actions else card.evidence
    verification = await repository.verification_of(card.incident_id)
    return replace(card, evidence=evidence, verification=verification)


async def ask(
    repository: IncidentRepository, incident_id: UUID, author: str, text: str, clock: Clock
) -> None:
    """Grava a pergunta (evento `human_message` e mensagem pendente de resposta do agente).

    Levanta InvalidActionError para autor ou texto vazio e IncidentNotFoundError.
    """
    author, text = author.strip(), text.strip()
    if not author:
        raise InvalidActionError("informe o seu nome antes de escrever")
    if not text:
        raise InvalidActionError("escreva a pergunta")
    if await repository.get_incident(incident_id) is None:
        raise IncidentNotFoundError(f"incidente {incident_id} não existe")
    async with repository.transaction() as uow:
        now = clock.now()
        question = ChatMessage(id=0, incident_id=incident_id, author=author, text=text, at=now)
        saved = await uow.add_message(question, wants_reply=True)
        data = {"message_id": saved.id, "text": text}
        await uow.append([IncidentEvent(incident_id, now, EventType.HUMAN_MESSAGE, data, author)])
