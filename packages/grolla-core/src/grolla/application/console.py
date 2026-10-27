"""Casos de uso do console de operação: perguntar, responder com prioridade menor, desenhar os
gráficos e abrir um incidente a partir da conversa.

Contrato do console de operação: os corpos são do grolla-dominio.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import (
    ChangeHistory,
    CloudInventory,
    LanguageModel,
    LlmUsage,
    Telemetry,
)
from grolla.application.memory_store import MemoryStore, TextEmbedder
from grolla.domain.console import ChartRequest, ConsoleMessage, ConsoleSession
from grolla.domain.incidents import Incident, Severity

CONSOLE_LIMIT_REACHED = "limite diário do console atingido"


@dataclass(frozen=True)
class ConsoleJob:
    message_id: int  # a pergunta
    session_id: UUID
    author: str
    text: str
    asked_at: datetime
    started_at: datetime


class ConsoleStore(Protocol):
    async def create_session(
        self, session: ConsoleSession, question: ConsoleMessage
    ) -> ConsoleMessage:
        """Cria a sessão com a primeira pergunta, pendente de resposta."""
        ...

    async def add_question(self, question: ConsoleMessage) -> ConsoleMessage:
        """Pergunta nova numa sessão existente, pendente de resposta; atualiza a sessão."""
        ...

    async def sessions(self, limit: int) -> list[ConsoleSession]:
        """Mais recentes primeiro (pela última atividade)."""
        ...

    async def session(self, session_id: UUID) -> ConsoleSession | None: ...

    async def messages(self, session_id: UUID) -> list[ConsoleMessage]:
        """Em ordem cronológica."""
        ...

    async def link_incident(self, session_id: UUID, incident_id: UUID) -> None: ...

    async def claim(self, now: datetime) -> ConsoleJob | None: ...

    async def stuck(self, started_before: datetime) -> list[ConsoleJob]: ...

    async def answer(
        self, job: ConsoleJob, reply: ConsoleMessage, usages: Sequence[LlmUsage], at: datetime
    ) -> None:
        """Numa transação, só se a pergunta está em andamento: grava a resposta, marca a
        pergunta e grava o uso (etapa console).
        """
        ...

    async def tokens_since(self, since: datetime) -> int:
        """Tokens do console (etapa console) desde `since`."""
        ...


@dataclass(frozen=True)
class ConsolePolicy:
    max_tokens: int = 60_000
    daily_max_tokens: int = 500_000
    timeout: timedelta = timedelta(minutes=2)
    lookback: timedelta = timedelta(minutes=60)  # janela das ferramentas


async def ask_console(
    *, store: ConsoleStore, session_id: UUID | None, author: str, text: str, clock: Clock
) -> ConsoleSession:
    """Pergunta numa sessão (ou cria uma nova); levanta InvalidActionError sem autor ou texto, e
    LookupError com sessão inexistente.
    """
    raise NotImplementedError


async def run_next_console_reply(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    store: ConsoleStore,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    cloud: CloudInventory,
    clock: Clock,
    policy: ConsolePolicy,
    memory: MemoryStore | None = None,
    embedder: TextEmbedder | None = None,
) -> bool:
    raise NotImplementedError


async def reap_stuck_console_replies(
    store: ConsoleStore, clock: Clock, policy: ConsolePolicy
) -> int:
    raise NotImplementedError


async def open_incident_from_console(  # noqa: PLR0913 — o incidente manual tem todos os campos
    *,
    store: ConsoleStore,
    repository: IncidentRepository,
    session_id: UUID,
    title: str,
    service: str,
    severity: Severity,
    author: str,
    clock: Clock,
    page_url: Callable[[UUID], str],
) -> Incident:
    raise NotImplementedError


async def chart_series(
    telemetry: Telemetry, chart: ChartRequest, clock: Clock
) -> list[tuple[datetime, float]]:
    """A série do gráfico na janela até agora; levanta ToolUnavailableError."""
    raise NotImplementedError
