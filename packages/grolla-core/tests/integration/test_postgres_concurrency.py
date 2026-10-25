"""Webhooks simultâneos sobre Postgres real: o agrupamento não pode depender da sorte.

O SigNoz manda grupos de alertas em requisições paralelas; cada uma vira transações próprias.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from grolla.adapters.postgres import PostgresDatabase, PostgresIncidentRepository, migrate
from grolla.application.incidents import IntakePolicy, receive_alerts
from grolla.domain.incidents import AlertStatus, Severity, SeverityPolicy
from grolla.testing.builders import T0, make_alert
from grolla.testing.clock import FakeClock

pytestmark = pytest.mark.integration

PARALLEL_WEBHOOKS = 8
POLICY = IntakePolicy(
    label_filter={},
    severity=SeverityPolicy(rules=(), default=Severity.P3),
    window=timedelta(minutes=15),
    page_url=lambda incident_id: f"http://grolla/incidents/{incident_id}",
)


@pytest.fixture(scope="module")
async def repository(database_url: str) -> AsyncIterator[PostgresIncidentRepository]:
    migrate(database_url)
    database = PostgresDatabase(database_url)
    yield PostgresIncidentRepository(database)
    await database.dispose()


async def incidents_of(repository: PostgresIncidentRepository, service: str) -> list[UUID]:
    return [i.id for i in await repository.list_incidents(10_000) if service in i.services]


async def test_parallel_alerts_of_one_service_open_a_single_incident(
    repository: PostgresIncidentRepository,
) -> None:
    service = f"concorrente-{uuid4().hex[:8]}"
    alerts = [make_alert(service=service) for _ in range(PARALLEL_WEBHOOKS)]

    await asyncio.gather(
        *(receive_alerts([alert], repository, POLICY, FakeClock(T0)) for alert in alerts)
    )

    [incident_id] = await incidents_of(repository, service)
    assert len(await repository.alerts_of(incident_id)) == PARALLEL_WEBHOOKS


async def test_parallel_resolutions_normalize_the_incident(
    repository: PostgresIncidentRepository,
) -> None:
    service = f"concorrente-{uuid4().hex[:8]}"
    firing = [make_alert(service=service) for _ in range(PARALLEL_WEBHOOKS)]
    await receive_alerts(firing, repository, POLICY, FakeClock(T0))
    resolved = [
        make_alert(fingerprint=a.fingerprint, service=service, status=AlertStatus.RESOLVED)
        for a in firing
    ]

    later = T0 + timedelta(minutes=5)
    await asyncio.gather(
        *(receive_alerts([alert], repository, POLICY, FakeClock(later)) for alert in resolved)
    )

    [incident_id] = await incidents_of(repository, service)
    incident = await repository.get_incident(incident_id)
    assert incident is not None
    assert incident.firing == frozenset()
    normalized = [
        e for e in await repository.events_of(incident_id) if e.type == "incident_normalized"
    ]
    assert len(normalized) == 1
