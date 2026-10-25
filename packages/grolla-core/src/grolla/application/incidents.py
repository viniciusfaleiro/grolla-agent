"""Ports e casos de uso dos incidentes: receber alertas e entregar as notificações."""

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from grolla.application.clock import Clock
from grolla.domain.analysis import Analysis
from grolla.domain.incidents import (
    Alert,
    AlertStatus,
    Incident,
    IncidentEvent,
    Notification,
    SeverityPolicy,
    decide_firing,
    decide_resolved,
    labels_match,
)

DELIVERY_BATCH = 100


class InvalidAlertPayloadError(Exception):
    """O payload recebido não segue o formato de alertas esperado."""


class NotificationFailedError(Exception):
    """O canal de chat recusou ou não respondeu à notificação."""


class AlertIngress(Protocol):
    """Entrada de alertas vindos da plataforma de observabilidade."""

    def authenticate(self, headers: Mapping[str, str]) -> bool: ...

    def parse(self, payload: Any) -> list[Alert]:
        """Converte o payload em alertas neutros; levanta InvalidAlertPayloadError."""
        ...


class ChatNotifier(Protocol):
    """Canal de chat do time."""

    async def post(self, notification: Notification) -> None:
        """Publica a notificação; levanta NotificationFailedError."""
        ...


class IncidentUnitOfWork(Protocol):
    """Operações de escrita de uma transação."""

    async def register_alert(self, alert: Alert) -> bool:
        """Grava o alerta; False se o mesmo (fingerprint, início, status) já foi visto."""
        ...

    async def active_incidents(self, service: str, since: datetime) -> list[Incident]:
        """Incidentes do serviço com alerta disparando ou último alerta desde `since`."""
        ...

    async def incident_firing(self, fingerprint: str) -> Incident | None: ...

    async def save(self, incident: Incident) -> None: ...

    async def append(self, events: Sequence[IncidentEvent]) -> None: ...

    async def link_alert(self, alert: Alert, incident_id: UUID) -> None: ...

    async def enqueue(self, notification: Notification, at: datetime) -> None:
        """Põe a notificação na outbox, pronta para entrega a partir de `at`."""
        ...

    async def request_analysis(self, incident_id: UUID, at: datetime) -> None:
        """Grava o pedido de análise inicial do incidente, pendente desde `at`."""
        ...


class IncidentRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[IncidentUnitOfWork]: ...

    async def list_incidents(self, limit: int) -> list[Incident]:
        """Mais recentes primeiro."""
        ...

    async def get_incident(self, incident_id: UUID) -> Incident | None: ...

    async def alerts_of(self, incident_id: UUID) -> list[Alert]: ...

    async def analysis_of(self, incident_id: UUID) -> Analysis | None:
        """A análise do incidente em qualquer estado; None se nunca foi pedida."""
        ...

    async def events_of(self, incident_id: UUID) -> list[IncidentEvent]:
        """Em ordem cronológica."""
        ...


@dataclass(frozen=True)
class OutboxItem:
    id: int
    notification: Notification
    attempts: int


class NotificationOutbox(Protocol):
    async def due(self, now: datetime, limit: int) -> list[OutboxItem]:
        """Itens pendentes com horário de tentativa vencido, em ordem de criação.

        Para manter a ordem por incidente, um item não vence enquanto houver item mais antigo
        do mesmo incidente pendente e com a próxima tentativa ainda no futuro.
        """
        ...

    async def delivered(self, item: OutboxItem, at: datetime) -> None:
        """Marca entregue e registra o evento no incidente."""
        ...

    async def retry_later(self, item: OutboxItem, at: datetime, error: str) -> None:
        """Incrementa as tentativas e agenda a próxima para `at`."""
        ...

    async def give_up(self, item: OutboxItem, at: datetime, error: str) -> None:
        """Marca falha definitiva e registra o evento no incidente."""
        ...


@dataclass(frozen=True)
class IntakePolicy:
    label_filter: Mapping[str, str]
    severity: SeverityPolicy
    window: timedelta
    page_url: Callable[[UUID], str]


async def receive_alerts(
    alerts: Sequence[Alert], repository: IncidentRepository, policy: IntakePolicy, clock: Clock
) -> None:
    """Processa cada alerta numa transação própria."""
    for alert in alerts:
        if not labels_match(alert.labels, policy.label_filter):
            continue
        async with repository.transaction() as uow:
            await _receive(alert, uow, policy, clock)


async def _receive(
    alert: Alert, uow: IncidentUnitOfWork, policy: IntakePolicy, clock: Clock
) -> None:
    if not await uow.register_alert(alert):
        return  # reenvio
    now = clock.now()
    if alert.status is AlertStatus.FIRING:
        decision = decide_firing(
            alert,
            candidates=await uow.active_incidents(alert.service, now - policy.window),
            policy=policy.severity,
            window=policy.window,
            now=now,
            page_url=policy.page_url,
            new_id=uuid4,
        )
    else:
        incident = await uow.incident_firing(alert.fingerprint)
        if incident is None:
            return  # resolvido sem incidente: só fica no histórico
        decision = decide_resolved(alert, incident, now, policy.page_url)
    if decision.incident is None:
        return
    await uow.save(decision.incident)
    await uow.append(decision.events)
    await uow.link_alert(alert, decision.incident.id)
    if decision.request_analysis:
        await uow.request_analysis(decision.incident.id, now)
    for notification in decision.notifications:
        await uow.enqueue(notification, now)


async def deliver_notifications(
    outbox: NotificationOutbox,
    notifier: ChatNotifier,
    clock: Clock,
    max_attempts: int = 8,
    base_delay: timedelta = timedelta(seconds=5),
) -> int:
    """Entrega o que venceu na outbox e devolve quantas foram entregues.

    Falha num card adia os cards seguintes do mesmo incidente nesta rodada, para manter a ordem.
    A espera dobra a cada tentativa: base_delay, 2x, 4x, ...
    """
    delivered = 0
    held: set[UUID] = set()
    for item in await outbox.due(clock.now(), limit=DELIVERY_BATCH):
        incident_id = item.notification.incident_id
        if incident_id in held:
            continue
        try:
            await notifier.post(item.notification)
        except NotificationFailedError as error:
            held.add(incident_id)
            attempts = item.attempts + 1
            now = clock.now()
            if attempts >= max_attempts:
                await outbox.give_up(item, now, str(error))
            else:
                await outbox.retry_later(item, now + base_delay * 2 ** (attempts - 1), str(error))
            continue
        await outbox.delivered(item, clock.now())
        delivered += 1
    return delivered
