from datetime import datetime, timedelta

import pytest
from grolla.application.incidents import IntakePolicy
from grolla.domain.incidents import Severity, SeverityPolicy
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import FakeAlertIngress, InMemoryIncidentRepository
from grolla.testing.memory import InMemoryLearningQueue, InMemoryMemoryStore, InMemoryStudyQueue
from grolla.testing.metrics import InMemoryMetricsReader
from grolla.testing.post_mortem import FakeDocumentRenderer, InMemoryPostMortemStore
from grolla_api.app import Services


async def database_up() -> bool:
    return True


async def no_heartbeat() -> datetime | None:
    return None


@pytest.fixture
def services() -> Services:
    """Services com os fakes; cada teste troca o que precisa com `dataclasses.replace`."""
    repository = InMemoryIncidentRepository()
    post_mortems = InMemoryPostMortemStore(repository)
    return Services(
        database_is_up=database_up,
        latest_heartbeat=no_heartbeat,
        ingress=FakeAlertIngress(),
        repository=repository,
        intake=IntakePolicy(
            label_filter={},
            severity=SeverityPolicy(rules=(), default=Severity.P3),
            window=timedelta(minutes=15),
            page_url=lambda incident_id: f"http://grolla.interno/incidents/{incident_id}",
        ),
        clock=FakeClock(),
        memory=InMemoryMemoryStore(),
        studies=InMemoryStudyQueue(),
        learning_queue=InMemoryLearningQueue(),
        study_enabled=True,
        post_mortems=post_mortems,
        renderer=FakeDocumentRenderer(),
        metrics=InMemoryMetricsReader(repository, post_mortems),
    )
