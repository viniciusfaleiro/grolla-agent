"""Junção de incidentes no war room: sugestão do Grolla, "Juntar com…" e os rótulos."""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from uuid import UUID, uuid4

import grolla_api.app
import pytest
from fastapi.testclient import TestClient
from grolla.application.clock import Clock
from grolla.application.incidents import IncidentRepository
from grolla.application.war_room import IncidentNotFoundError
from grolla.domain.chat import AGENT_AUTHOR
from grolla.domain.incidents import EventType, Incident, IncidentEvent, IncidentState, Severity
from grolla.domain.war_room import InvalidActionError
from grolla.testing.builders import T0, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app
from grolla_api.war_room import AUTHOR_COOKIE

NOW = T0 + timedelta(minutes=12)


@dataclass
class MergeStub:
    """Dublê do `merge_incidents`: registra a chamada e devolve o principal ou levanta o erro."""

    repository: InMemoryIncidentRepository
    calls: list[tuple[UUID, UUID, str]] = field(default_factory=list[tuple[UUID, UUID, str]])
    error: Exception | None = None

    async def merge(  # noqa: PLR0913 — mesma assinatura do caso de uso
        self,
        *,
        repository: IncidentRepository,
        source_id: UUID,
        target_id: UUID,
        author: str,
        clock: Clock,
        page_url: Callable[[UUID], str],
    ) -> Incident:
        del clock, page_url  # o dublê não decide nada: não usa o relógio nem o link
        self.calls.append((source_id, target_id, author))
        if self.error is not None:
            raise self.error
        target = await repository.get_incident(target_id)
        if target is None:
            raise IncidentNotFoundError(target_id)
        return target


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture(autouse=True)
def stub(repository: InMemoryIncidentRepository, monkeypatch: pytest.MonkeyPatch) -> MergeStub:
    stub = MergeStub(repository)
    monkeypatch.setattr(grolla_api.app, "merge_incidents", stub.merge)
    return stub


@pytest.fixture
def client(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    return TestClient(
        create_app(replace(services, repository=repository, clock=FakeClock(NOW))),
        follow_redirects=False,
    )


@pytest.fixture
def ana(client: TestClient) -> TestClient:
    client.cookies.set(AUTHOR_COOKIE, "Ana")
    return client


def add_incident(repository: InMemoryIncidentRepository, **changes: object) -> Incident:
    incident = replace(make_incident(severity=Severity.P2), **changes)
    repository.incidents[incident.id] = incident
    return incident


def pair(repository: InMemoryIncidentRepository) -> tuple[Incident, Incident]:
    """O incidente do pedidos-api (principal) e o da captura-api, que abriu 2 min depois."""
    pedidos = add_incident(repository, title="Erros no pedidos-api", services=("pedidos-api",))
    captura = add_incident(
        repository,
        title="Erros no captura-api",
        services=("captura-api",),
        detected_at=T0 + timedelta(minutes=2),
    )
    return pedidos, captura


def suggest(repository: InMemoryIncidentRepository, incident: Incident, other: Incident) -> None:
    data = {"other": str(other.id), "reason": "os dois usam grolla-lab-db"}
    repository.events.append(
        IncidentEvent(incident.id, T0, EventType.MERGE_SUGGESTED, data, author=AGENT_AUTHOR)
    )


# ---- sugestão do Grolla


def test_suggestion_shows_the_other_incident_the_reason_and_the_button(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)
    suggest(repository, captura, pedidos)

    page = ana.get(f"/incidents/{captura.id}").text

    assert (
        f'Parece o mesmo problema que <a href="/incidents/{pedidos.id}">Erros no pedidos-api</a>'
    ) in page
    assert "os dois usam grolla-lab-db" in page
    assert f'hx-post="/incidents/{captura.id}/juntar"' in page
    assert f'name="destino" value="{pedidos.id}"' in page
    assert ">Juntar</button>" in page


def test_suggestion_without_name_has_no_button(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)
    suggest(repository, captura, pedidos)

    page = client.get(f"/incidents/{captura.id}").text

    assert "Parece o mesmo problema que" in page
    assert f'name="destino" value="{pedidos.id}"' not in page


@pytest.mark.parametrize("closed", [IncidentState.RESOLVED, IncidentState.DISCARDED])
def test_suggestion_disappears_when_the_other_is_closed(
    ana: TestClient, repository: InMemoryIncidentRepository, closed: IncidentState
) -> None:
    pedidos, captura = pair(repository)
    suggest(repository, captura, pedidos)
    repository.incidents[pedidos.id] = replace(pedidos, state=closed)

    assert "Parece o mesmo problema que" not in ana.get(f"/incidents/{captura.id}").text


def test_suggestion_disappears_when_this_one_is_closed(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)
    suggest(repository, captura, pedidos)
    repository.incidents[captura.id] = replace(captura, state=IncidentState.DISCARDED)

    assert "Parece o mesmo problema que" not in ana.get(f"/incidents/{captura.id}").text


def test_only_the_last_suggestion_counts(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)
    terceiro = add_incident(repository, title="Erros no autorizador")
    suggest(repository, captura, terceiro)
    suggest(repository, captura, pedidos)

    page = ana.get(f"/incidents/{captura.id}").text

    assert f'Parece o mesmo problema que <a href="/incidents/{pedidos.id}">' in page
    assert f'Parece o mesmo problema que <a href="/incidents/{terceiro.id}">' not in page


def test_suggestion_of_an_unknown_incident_is_ignored(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    captura, outro = add_incident(repository), add_incident(repository)
    data = {"other": "nao-e-uuid", "reason": "?"}
    repository.events.append(IncidentEvent(captura.id, T0, EventType.MERGE_SUGGESTED, data))
    suggest(repository, outro, make_incident())  # o outro não está no repositório

    assert "Parece o mesmo problema que" not in ana.get(f"/incidents/{captura.id}").text
    assert "Parece o mesmo problema que" not in ana.get(f"/incidents/{outro.id}").text


# ---- "Juntar com…"


def test_merge_with_lists_the_other_open_incidents_newest_first(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)
    fechado = add_incident(repository, title="Incidente encerrado", state=IncidentState.RESOLVED)

    page = ana.get(f"/incidents/{pedidos.id}").text

    assert 'popovertarget="juntar">Juntar com…</button>' in page
    assert f'<option value="{captura.id}">P2 · Erros no captura-api</option>' in page
    assert f'<option value="{pedidos.id}">' not in page
    assert f'<option value="{fechado.id}">' not in page


def test_no_other_open_incident_has_no_merge_with(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)

    assert "Juntar com…" not in ana.get(f"/incidents/{incident.id}").text


# ---- rota de juntar


def test_merge_goes_to_the_main_incident(
    ana: TestClient, repository: InMemoryIncidentRepository, stub: MergeStub
) -> None:
    pedidos, captura = pair(repository)

    response = ana.post(f"/incidents/{captura.id}/juntar", data={"destino": str(pedidos.id)})

    assert response.status_code == 303
    assert response.headers["location"] == f"/incidents/{pedidos.id}"
    assert stub.calls == [(captura.id, pedidos.id, "Ana")]


def test_merge_from_htmx_redirects_with_the_header(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)

    response = ana.post(
        f"/incidents/{captura.id}/juntar",
        data={"destino": str(pedidos.id)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/incidents/{pedidos.id}"


def test_merge_without_name_is_refused(
    client: TestClient, repository: InMemoryIncidentRepository, stub: MergeStub
) -> None:
    pedidos, captura = pair(repository)

    response = client.post(f"/incidents/{captura.id}/juntar", data={"destino": str(pedidos.id)})

    assert response.status_code == 401
    assert response.headers["HX-Retarget"] == "#war-room-error"
    assert stub.calls == []


@pytest.mark.parametrize("destino", ["", "nao-e-uuid"])
def test_merge_without_a_valid_target_is_refused(
    ana: TestClient, repository: InMemoryIncidentRepository, stub: MergeStub, destino: str
) -> None:
    captura = add_incident(repository)

    response = ana.post(f"/incidents/{captura.id}/juntar", data={"destino": destino})

    assert response.status_code == 422
    assert "escolha o incidente" in response.text
    assert stub.calls == []


def test_merge_refused_by_the_domain_shows_the_message(
    ana: TestClient, repository: InMemoryIncidentRepository, stub: MergeStub
) -> None:
    captura = add_incident(repository)
    stub.error = InvalidActionError("não dá para juntar um incidente nele mesmo")

    response = ana.post(f"/incidents/{captura.id}/juntar", data={"destino": str(captura.id)})

    assert response.status_code == 422
    assert "não dá para juntar um incidente nele mesmo" in response.text


def test_merge_of_unknown_incidents_is_not_found(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    captura = add_incident(repository)
    target = {"destino": str(uuid4())}

    assert ana.post(f"/incidents/{uuid4()}/juntar", data=target).status_code == 404
    assert ana.post("/incidents/nao-e-uuid/juntar", data=target).status_code == 404
    assert ana.post(f"/incidents/{captura.id}/juntar", data=target).status_code == 404


# ---- timeline e incidente juntado


def test_timeline_labels_link_the_other_incident(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)
    suggest(repository, captura, pedidos)
    repository.events += [
        IncidentEvent(captura.id, T0, EventType.TRIAGE_COMPLETED, {}, author=AGENT_AUTHOR),
        IncidentEvent(captura.id, T0, EventType.MERGED_INTO, {"into": str(pedidos.id)}, "Ana"),
        IncidentEvent(captura.id, T0, EventType.INCIDENT_MERGED, {"merged": str(uuid4())}),
    ]
    link = f'<a href="/incidents/{pedidos.id}">Erros no pedidos-api</a>'

    fragment = client.get(f"/incidents/{captura.id}/timeline").text

    assert f"Grolla sugere juntar com {link}" in fragment
    assert "os dois usam grolla-lab-db" in fragment
    assert "Triagem do Grolla" in fragment
    assert f"Juntado em {link}" in fragment
    assert "Recebeu o incidente <a href=" in fragment
    assert ">outro incidente</a>" in fragment  # o citado não existe mais: link sem título


def test_main_incident_timeline_says_it_received_the_other(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)
    data = {"merged": str(captura.id)}
    repository.events.append(IncidentEvent(pedidos.id, T0, EventType.INCIDENT_MERGED, data, "Ana"))

    fragment = client.get(f"/incidents/{pedidos.id}/timeline").text

    assert (
        f'Recebeu o incidente <a href="/incidents/{captura.id}">Erros no captura-api</a>'
        in fragment
    )
    assert "por Ana" in fragment


def test_severity_suggested_by_the_agent_has_its_label_and_reason(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = add_incident(repository)
    data = {"from": "P3", "to": "P1", "reason": "sugerida pelo agente: o serviço é o autorizador"}
    repository.events.append(
        IncidentEvent(incident.id, T0, EventType.SEVERITY_CHANGED, data, author=AGENT_AUTHOR)
    )

    fragment = client.get(f"/incidents/{incident.id}/timeline").text

    assert "Severidade sugerida pelo agente" in fragment
    assert "P3 → P1: sugerida pelo agente: o serviço é o autorizador" in fragment


def test_merged_incident_page_highlights_where_it_went(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    pedidos, captura = pair(repository)
    repository.incidents[captura.id] = replace(captura, state=IncidentState.DISCARDED)
    data = {"into": str(pedidos.id)}
    repository.events.append(IncidentEvent(captura.id, T0, EventType.MERGED_INTO, data, "Ana"))

    page = client.get(f"/incidents/{captura.id}").text

    assert (
        f'class="merged-into" aria-label="Incidente juntado">\n    <p>Juntado em '
        f'<a href="/incidents/{pedidos.id}">Erros no pedidos-api</a>'
    ) in page
    assert "Juntar com…" not in page
