from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.domain.analysis import (
    Analysis,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    PartialReason,
)
from grolla.domain.incidents import EventType, IncidentEvent, Severity
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app


class RepositoryWithAnalyses(InMemoryIncidentRepository):
    """Stub local de `analysis_of` até o fake do domínio chegar."""

    def __init__(self) -> None:
        super().__init__()
        self.analyses: dict[UUID, Analysis] = {}

    async def analysis_of(self, incident_id: UUID) -> Analysis | None:
        return self.analyses.get(incident_id)


@pytest.fixture
def repository() -> RepositoryWithAnalyses:
    return RepositoryWithAnalyses()


def make_analysis(
    incident_id: UUID,
    status: AnalysisStatus = AnalysisStatus.COMPLETED,
    partial_reason: PartialReason | None = None,
) -> Analysis:
    return Analysis(
        incident_id=incident_id,
        status=status,
        requested_at=T0,
        started_at=T0 + timedelta(seconds=5),
        finished_at=T0 + timedelta(minutes=2),
        summary="Erro subiu logo depois do deploy 1.4.2 do autorizador.",
        evaluations=(
            Evaluation(
                tool="query_metric",
                subject="taxa_de_erro de autorizador, últimos 60 min",
                finding="subiu de 0,2% para 7% às 10:02",
                url="https://observabilidade.interno/metrica/1",
                ok=True,
            ),
            Evaluation(
                tool="recent_deploys",
                subject="deploys de autorizador, últimas 2 h",
                finding="não foi possível consultar",
                url=None,
                ok=False,
            ),
        ),
        hypotheses=(
            Hypothesis(
                title="Deploy 1.4.2 introduziu regressão",
                confidence=0.72,
                evidence=(
                    Evidence("Erro começa 2 min após o deploy", "https://devops.interno/deploy/9"),
                    Evidence("Traces falham no mesmo endpoint", None),
                ),
            ),
            Hypothesis(title="Adquirente lenta", confidence=0.15, evidence=()),
        ),
        plan=("Comparar o deploy 1.4.2 com o anterior", "Avaliar rollback do 1.4.2"),
        partial_reason=partial_reason,
    )


@pytest.fixture
def client(services: Services, repository: RepositoryWithAnalyses) -> TestClient:
    return TestClient(create_app(replace(services, repository=repository)))


def test_list_shows_the_most_recent_incident_first(
    client: TestClient, repository: RepositoryWithAnalyses
) -> None:
    older = make_incident(services=("captura",), detected_at=T0)
    newer = make_incident(services=("autorizador",), detected_at=T0 + timedelta(hours=1))
    repository.incidents = {older.id: older, newer.id: newer}

    response = client.get("/incidents")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    page = response.text
    assert f'href="/incidents/{newer.id}"' in page
    assert page.index("autorizador") < page.index("captura")


def test_empty_list_says_there_is_nothing(client: TestClient) -> None:
    response = client.get("/incidents")

    assert response.status_code == 200
    assert "Nenhum incidente registrado" in response.text


def test_incident_page_shows_alerts_and_timeline_in_order(
    client: TestClient, repository: RepositoryWithAnalyses
) -> None:
    incident = make_incident(severity=Severity.P1, services=("captura",))
    first = make_alert(service="captura", summary="Erro em 7%")
    second = make_alert(service="captura", summary="Latência p99 em 2s", source_url=None)
    repository.incidents = {incident.id: incident}
    repository.links = [(incident.id, first), (incident.id, second)]
    repository.events = [
        IncidentEvent(incident.id, T0, EventType.ALERT_RECEIVED, {"fingerprint": "a"}),
        IncidentEvent(incident.id, T0 + timedelta(minutes=1), EventType.ALERT_GROUPED, {}),
        IncidentEvent(incident.id, T0 + timedelta(minutes=2), EventType.SEVERITY_CHANGED, {}),
    ]

    response = client.get(f"/incidents/{incident.id}")

    assert response.status_code == 200
    page = response.text
    assert "P1" in page
    assert "Erro em 7%" in page
    assert "Latência p99 em 2s" in page
    assert 'href="https://observabilidade.interno/alerta/1"' in page
    received = page.index("Alerta recebido")
    assert received < page.index("Alerta agrupado") < page.index("Severidade alterada")


def test_page_escapes_alert_text(client: TestClient, repository: RepositoryWithAnalyses) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    repository.links = [(incident.id, make_alert(summary="<script>x</script>"))]

    page = client.get(f"/incidents/{incident.id}").text

    assert "<script>" not in page
    assert "&lt;script&gt;" in page


@pytest.mark.parametrize("incident_id", [str(uuid4()), "nao-e-um-uuid"])
def test_unknown_incident_is_not_found(client: TestClient, incident_id: str) -> None:
    assert client.get(f"/incidents/{incident_id}").status_code == 404


def test_completed_analysis_shows_summary_evaluations_hypotheses_and_plan(
    client: TestClient, repository: RepositoryWithAnalyses
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    repository.analyses = {incident.id: make_analysis(incident.id)}

    page = client.get(f"/incidents/{incident.id}").text

    assert "análise concluída" in page
    assert "Erro subiu logo depois do deploy 1.4.2" in page
    assert "O que avaliei" in page
    assert "taxa_de_erro de autorizador, últimos 60 min" in page
    assert "subiu de 0,2% para 7% às 10:02" in page
    assert 'href="https://observabilidade.interno/metrica/1"' in page
    assert "falhou:" in page
    assert page.index("Deploy 1.4.2 introduziu regressão") < page.index("Adquirente lenta")
    assert "72%" in page
    assert "15%" in page
    assert 'href="https://devops.interno/deploy/9"' in page
    assert "Plano proposto" in page
    assert "Avaliar rollback do 1.4.2" in page
    assert page.index("Análise") < page.index("Alertas")


@pytest.mark.parametrize("status", [AnalysisStatus.PENDING, AnalysisStatus.RUNNING])
def test_analysis_not_finished_shows_in_progress(
    client: TestClient, repository: RepositoryWithAnalyses, status: AnalysisStatus
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    pending = replace(
        make_analysis(incident.id, status), summary="", evaluations=(), hypotheses=(), plan=()
    )
    repository.analyses = {incident.id: pending}

    page = client.get(f"/incidents/{incident.id}").text

    assert "análise em andamento" in page
    assert "O que avaliei" not in page
    assert "Plano proposto" not in page


def test_partial_analysis_shows_the_reason_in_portuguese(
    client: TestClient, repository: RepositoryWithAnalyses
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    partial = replace(
        make_analysis(incident.id, AnalysisStatus.PARTIAL, PartialReason.TIMEOUT),
        summary="",
        hypotheses=(),
        plan=(),
    )
    repository.analyses = {incident.id: partial}

    page = client.get(f"/incidents/{incident.id}").text

    assert "análise parcial" in page
    assert "o tempo da análise esgotou" in page
    assert "tempo_esgotado" not in page
    assert "O que avaliei" in page
    assert "Hipóteses" not in page


def test_incident_without_analysis_says_so(
    client: TestClient, repository: RepositoryWithAnalyses
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}

    assert "Nenhuma análise pedida" in client.get(f"/incidents/{incident.id}").text


def test_analysis_text_is_escaped(client: TestClient, repository: RepositoryWithAnalyses) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    repository.analyses = {
        incident.id: replace(make_analysis(incident.id), summary="<img src=x onerror=alert(1)>")
    }

    page = client.get(f"/incidents/{incident.id}").text

    assert "<img src=x" not in page
    assert "&lt;img src=x" in page
