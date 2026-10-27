import re
from collections.abc import Callable
from datetime import datetime, timedelta

import grolla_api.app
import grolla_api.war_room
import pytest
from grolla.application import safety
from grolla.application.incidents import IntakePolicy
from grolla.domain import safety as domain_safety
from grolla.domain.incidents import Severity, SeverityPolicy
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import FakeAlertIngress, InMemoryIncidentRepository
from grolla.testing.memory import InMemoryLearningQueue, InMemoryMemoryStore, InMemoryStudyQueue
from grolla.testing.messages import FakeMessageCounter
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
        messages=FakeMessageCounter(),
    )


# Enquanto as regras do cerco não chegam do grolla-dominio (corpos com NotImplementedError no
# commit de contrato), os testes usam um substituto mínimo. Com a regra real, ela é usada e
# este bloco pode sair na integração.
_DESTRUCTIVE = re.compile(r"\b(delete|drop|truncate|destroy|purge)\b|rm -rf|--replicas=0|--force")


def _is_destructive(command: str) -> bool:
    try:
        return domain_safety.is_destructive(command)
    except NotImplementedError:
        return _DESTRUCTIVE.search(command.lower()) is not None


async def _ensure_under_limit(
    counter: safety.MessageCounter, author: str, now: datetime, per_hour: int
) -> None:
    try:
        return await safety.ensure_under_limit(counter, author, now, per_hour)
    except NotImplementedError:
        sent = sorted(await counter.sent_since(author, now - safety.WINDOW))
        if len(sent) >= per_hour:
            raise safety.RateLimitedError(per_hour, sent[-per_hour] + safety.WINDOW) from None


_STAND_INS: tuple[tuple[object, str, Callable[..., object]], ...] = (
    (grolla_api.war_room, "is_destructive", _is_destructive),
    (grolla_api.app, "ensure_under_limit", _ensure_under_limit),
)


@pytest.fixture(autouse=True)
def safety_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    for module, name, stand_in in _STAND_INS:
        monkeypatch.setattr(module, name, stand_in)
