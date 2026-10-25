"""Suíte de contrato de IncidentRepository e IncidentUnitOfWork.

Roda também contra o banco real compartilhado na sessão: cada teste cria dados únicos (uuid4) e
só confere os próprios registros.
"""

from datetime import timedelta
from uuid import uuid4

import pytest
from grolla.application.incidents import IncidentRepository
from grolla.domain.incidents import (
    AlertStatus,
    EventType,
    IncidentEvent,
    IncidentState,
    Severity,
)
from grolla.testing.builders import T0, make_alert, make_incident


class IncidentRepositoryContract:
    @pytest.fixture
    def repository(self) -> IncidentRepository:
        raise NotImplementedError

    async def test_register_alert_is_false_for_the_same_fingerprint_start_and_status(
        self, repository: IncidentRepository
    ) -> None:
        alert = make_alert()
        resolved = make_alert(fingerprint=alert.fingerprint, status=AlertStatus.RESOLVED)
        restarted = make_alert(fingerprint=alert.fingerprint, starts_at=T0 + timedelta(hours=1))

        async with repository.transaction() as uow:
            assert await uow.register_alert(alert)
        async with repository.transaction() as uow:
            assert not await uow.register_alert(alert)
            assert await uow.register_alert(resolved)
            assert await uow.register_alert(restarted)

    async def test_saved_incident_is_read_back_and_overwritten(
        self, repository: IncidentRepository
    ) -> None:
        incident = make_incident(firing=frozenset({"fp-1", "fp-2"}))
        raised = make_incident(
            id=incident.id,
            services=incident.services,
            severity=Severity.P1,
            state=IncidentState.DETECTED,
            last_alert_at=T0 + timedelta(minutes=3),
            firing=frozenset({"fp-2"}),
        )

        async with repository.transaction() as uow:
            await uow.save(incident)
        assert await repository.get_incident(incident.id) == incident

        async with repository.transaction() as uow:
            await uow.save(raised)
        assert await repository.get_incident(incident.id) == raised

    async def test_unknown_incident_is_none(self, repository: IncidentRepository) -> None:
        assert await repository.get_incident(uuid4()) is None

    async def test_active_incidents_are_of_the_service_and_recent_or_firing(
        self, repository: IncidentRepository
    ) -> None:
        service = f"servico-{uuid4().hex[:8]}"
        recent = make_incident(services=(service,), last_alert_at=T0)
        firing_old = make_incident(
            services=(service,),
            detected_at=T0 - timedelta(hours=2),
            firing=frozenset({uuid4().hex}),
        )
        quiet_old = make_incident(services=(service,), detected_at=T0 - timedelta(hours=2))
        other_service = make_incident(last_alert_at=T0)

        async with repository.transaction() as uow:
            for incident in (recent, firing_old, quiet_old, other_service):
                await uow.save(incident)
        async with repository.transaction() as uow:
            active = await uow.active_incidents(service, since=T0 - timedelta(minutes=15))

        assert {incident.id for incident in active} == {recent.id, firing_old.id}

    async def test_incident_firing_finds_the_incident_by_fingerprint(
        self, repository: IncidentRepository
    ) -> None:
        fingerprint = uuid4().hex
        incident = make_incident(firing=frozenset({fingerprint}))

        async with repository.transaction() as uow:
            await uow.save(incident)
        async with repository.transaction() as uow:
            assert await uow.incident_firing(fingerprint) == incident
            assert await uow.incident_firing(uuid4().hex) is None

    async def test_events_come_back_in_order(self, repository: IncidentRepository) -> None:
        incident = make_incident()
        events = [
            IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {"fingerprint": "fp-1"}),
            IncidentEvent(
                incident.id, T0, EventType.STATE_CHANGED, {"from": "detectado", "to": "informado"}
            ),
            IncidentEvent(
                incident.id,
                T0 + timedelta(minutes=5),
                EventType.SEVERITY_CHANGED,
                {"from": "P3", "to": "P1"},
                author="sistema",
            ),
        ]

        async with repository.transaction() as uow:
            await uow.save(incident)
            await uow.append(events[:2])
        async with repository.transaction() as uow:
            await uow.append(events[2:])

        assert await repository.events_of(incident.id) == events

    async def test_linked_alerts_belong_to_the_incident(
        self, repository: IncidentRepository
    ) -> None:
        incident = make_incident()
        first = make_alert()
        second = make_alert(service=first.service, status=AlertStatus.RESOLVED)
        loose = make_alert()

        async with repository.transaction() as uow:
            await uow.save(incident)
            for alert in (first, second, loose):
                await uow.register_alert(alert)
            await uow.link_alert(first, incident.id)
            await uow.link_alert(second, incident.id)

        assert await repository.alerts_of(incident.id) == [first, second]
        assert await repository.alerts_of(uuid4()) == []

    async def test_list_incidents_is_most_recent_first_and_limited(
        self, repository: IncidentRepository
    ) -> None:
        older = make_incident(detected_at=T0)
        newer = make_incident(detected_at=T0 + timedelta(minutes=1))

        async with repository.transaction() as uow:
            await uow.save(older)
            await uow.save(newer)

        listed = [i.id for i in await repository.list_incidents(limit=10_000)]
        assert listed.index(newer.id) < listed.index(older.id)
        assert len(await repository.list_incidents(limit=1)) == 1

    async def test_failed_transaction_writes_nothing(self, repository: IncidentRepository) -> None:
        incident = make_incident()
        alert = make_alert()

        async def write_then_fail() -> None:
            async with repository.transaction() as uow:
                await uow.register_alert(alert)
                await uow.save(incident)
                await uow.append([IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {})])
                await uow.link_alert(alert, incident.id)
                raise RuntimeError("falha no meio")

        with pytest.raises(RuntimeError, match="falha no meio"):
            await write_then_fail()

        assert await repository.get_incident(incident.id) is None
        assert await repository.events_of(incident.id) == []
        async with repository.transaction() as uow:
            assert await uow.register_alert(alert)
