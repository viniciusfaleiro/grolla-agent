"""Fila e casos de uso da análise inicial: todo pedido termina, concluído ou parcial."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import UUID

from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.investigation import ChangeHistory, LanguageModel, LlmUsage, Telemetry
from grolla.domain.analysis import Analysis
from grolla.domain.incidents import IncidentEvent, Notification


@dataclass(frozen=True)
class AnalysisJob:
    incident_id: UUID
    requested_at: datetime
    started_at: datetime


class AnalysisQueue(Protocol):
    async def claim(self, now: datetime) -> AnalysisJob | None:
        """Pega o pedido pendente mais antigo e o marca em andamento; None se não há."""
        ...

    async def stuck(self, started_before: datetime) -> list[AnalysisJob]:
        """Pedidos em andamento desde antes de `started_before` (worker caiu no meio)."""
        ...

    async def finish(
        self,
        analysis: Analysis,
        events: Sequence[IncidentEvent],
        notification: Notification,
        usages: Sequence[LlmUsage],
        at: datetime,
    ) -> None:
        """Numa transação: grava a análise, os eventos, o card na outbox e o uso do modelo."""
        ...


@dataclass(frozen=True)
class AnalysisPolicy:
    timeout: timedelta
    max_tokens: int
    lookback: timedelta  # janela das ferramentas
    page_url: Callable[[UUID], str]
    telemetry_url: str | None  # botão "ver na origem" do card de análise


async def run_next_analysis(  # noqa: PLR0913 — o caso de uso liga os ports da investigação
    *,
    queue: AnalysisQueue,
    repository: IncidentRepository,
    model: LanguageModel,
    telemetry: Telemetry,
    changes: ChangeHistory,
    clock: Clock,
    policy: AnalysisPolicy,
) -> bool:
    """Executa o próximo pedido de análise; False se não havia pedido."""
    raise NotImplementedError


async def reap_stuck_analyses(
    queue: AnalysisQueue, repository: IncidentRepository, clock: Clock, policy: AnalysisPolicy
) -> int:
    """Encerra como parciais (interrompidas) as análises presas; devolve quantas."""
    raise NotImplementedError
