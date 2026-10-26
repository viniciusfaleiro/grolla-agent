"""Casos de uso do war room: ações humanas e perguntas ao agente."""

from collections.abc import Callable
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.domain.incidents import Incident
from grolla.domain.war_room import HumanAction


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
    raise NotImplementedError


async def ask(
    repository: IncidentRepository, incident_id: UUID, author: str, text: str, clock: Clock
) -> None:
    """Grava a pergunta (evento `human_message` e mensagem pendente de resposta do agente).

    Levanta InvalidActionError para texto vazio ou incidente inexistente.
    """
    raise NotImplementedError
