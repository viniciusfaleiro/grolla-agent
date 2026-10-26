"""Chat do war room: mensagens das pessoas e respostas do agente."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from grolla.domain.analysis import Evaluation

AGENT_AUTHOR = "agente"


class ReplyStatus(StrEnum):
    PENDING = "pendente"
    RUNNING = "em_andamento"
    ANSWERED = "respondida"
    FAILED = "falhou"  # a resposta foi "não consegui responder: <motivo>"


@dataclass(frozen=True)
class ChatMessage:
    id: int
    incident_id: UUID
    author: str  # nome da pessoa ou AGENT_AUTHOR
    text: str
    at: datetime
    evaluations: tuple[Evaluation, ...] = ()  # só nas respostas do agente
    reply_status: ReplyStatus | None = None  # só nas perguntas das pessoas
    reply_to: int | None = None  # só nas respostas: a pergunta respondida


@dataclass(frozen=True)
class ChatDraft:
    """O que o modelo de linguagem devolve no chat."""

    text: str
