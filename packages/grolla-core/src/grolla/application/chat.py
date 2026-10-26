"""Respostas do agente no chat do war room: fila, contexto e a garantia de que chegam."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from grolla.application.analysis import AnalysisPolicy
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    LlmUsage,
    Telemetry,
)
from grolla.domain.analysis import Analysis, Verification
from grolla.domain.chat import ChatMessage
from grolla.domain.incidents import Incident, IncidentEvent

CHAT_LIMIT_REACHED = "limite do chat deste incidente atingido"


@dataclass(frozen=True)
class ChatJob:
    message_id: int  # a pergunta a responder
    incident_id: UUID
    author: str
    text: str
    asked_at: datetime
    started_at: datetime


class ChatQueue(Protocol):
    async def claim(self, now: datetime) -> ChatJob | None:
        """A pergunta pendente mais antiga, marcada em andamento; None se não há."""
        ...

    async def stuck(self, started_before: datetime) -> list[ChatJob]:
        """Perguntas em andamento desde antes de `started_before`."""
        ...

    async def answer(
        self,
        job: ChatJob,
        reply: ChatMessage,
        events: Sequence[IncidentEvent],
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        """Numa transação: grava a resposta, marca a pergunta respondida (ou falhou, conforme
        `reply`), os eventos e o uso. Só grava se a pergunta ainda está em andamento.
        """
        ...

    async def chat_tokens(self, incident_id: UUID) -> int:
        """Tokens já usados no chat do incidente (etapa warroom_chat)."""
        ...


def chat_context(
    incident: Incident,
    analysis: Analysis | None,
    verification: Verification | None,
    events: Sequence[IncidentEvent],
    messages: Sequence[ChatMessage],
) -> str:
    """Texto compacto com o incidente, a análise, a validação, a timeline e o chat."""
    raise NotImplementedError


async def run_next_reply(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    queue: ChatQueue,
    repository: IncidentRepository,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    clock: Clock,
    policy: AnalysisPolicy,
    cloud: CloudInventory | None = None,
) -> bool:
    """Responde a próxima pergunta; False se não havia. A resposta sempre chega."""
    raise NotImplementedError


async def reap_stuck_replies(
    queue: ChatQueue, repository: IncidentRepository, clock: Clock, policy: AnalysisPolicy
) -> int:
    """Responde "não consegui responder: interrompida" às perguntas presas; devolve quantas."""
    raise NotImplementedError
