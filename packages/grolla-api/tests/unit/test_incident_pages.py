from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.domain.analysis import (
    Action,
    Analysis,
    AnalysisStatus,
    Evaluation,
    Evidence,
    Hypothesis,
    HypothesisVerdict,
    PartialReason,
    Verdict,
    Verification,
)
from grolla.domain.incidents import EventType, IncidentEvent, Severity
from grolla.testing.builders import T0, make_alert, make_incident
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


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
def client(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    return TestClient(create_app(replace(services, repository=repository)))


def test_list_shows_the_most_recent_incident_first(
    client: TestClient, repository: InMemoryIncidentRepository
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
    assert "Nenhum incidente por aqui" in response.text


def test_incident_page_shows_alerts_and_timeline_in_order(
    client: TestClient, repository: InMemoryIncidentRepository
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


def test_page_escapes_alert_text(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
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
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    repository.analyses = {incident.id: make_analysis(incident.id)}

    page = client.get(f"/incidents/{incident.id}").text

    assert "análise concluída" in page
    assert "Erro subiu logo depois do deploy 1.4.2" in page
    assert "O que o Grolla consultou" in page
    assert "taxa_de_erro de autorizador, últimos 60 min" in page
    assert "subiu de 0,2% para 7% às 10:02" in page
    assert 'href="https://observabilidade.interno/metrica/1"' in page
    assert "falhou:" in page
    assert page.index("Deploy 1.4.2 introduziu regressão") < page.index("Adquirente lenta")
    assert "72%" in page
    assert "15%" in page
    assert 'href="https://devops.interno/deploy/9"' in page
    assert "O Grolla investigaria primeiro" in page  # sem validação, o plano é o da análise
    assert "Avaliar rollback do 1.4.2" in page
    assert page.index("Como o Grolla chegou lá") < page.index("Alertas")


@pytest.mark.parametrize("status", [AnalysisStatus.PENDING, AnalysisStatus.RUNNING])
def test_analysis_not_finished_shows_in_progress(
    client: TestClient, repository: InMemoryIncidentRepository, status: AnalysisStatus
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    pending = replace(
        make_analysis(incident.id, status), summary="", evaluations=(), hypotheses=(), plan=()
    )
    repository.analyses = {incident.id: pending}

    page = client.get(f"/incidents/{incident.id}").text

    assert "análise em andamento" in page
    assert "O que o Grolla consultou" not in page
    assert "Plano proposto" not in page


def test_partial_analysis_shows_the_reason_in_portuguese(
    client: TestClient, repository: InMemoryIncidentRepository
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
    assert "tempo esgotado" in page
    assert "tempo_esgotado" not in page
    assert "O que o Grolla consultou" in page
    assert 'class="hypotheses"' not in page


def test_incident_without_analysis_says_so(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}

    assert "Nenhuma análise pedida" in client.get(f"/incidents/{incident.id}").text


def test_analysis_text_is_escaped(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    repository.analyses = {
        incident.id: replace(make_analysis(incident.id), summary="<img src=x onerror=alert(1)>")
    }

    page = client.get(f"/incidents/{incident.id}").text

    assert "<img src=x" not in page
    assert "&lt;img src=x" in page


ROOT_CAUSE = "Banco grolla-lab-db parado por StopDBInstance às 10:00"


def make_verification(
    incident_id: UUID,
    status: AnalysisStatus = AnalysisStatus.COMPLETED,
    partial_reason: PartialReason | None = None,
) -> Verification:
    return Verification(
        incident_id=incident_id,
        status=status,
        requested_at=T0 + timedelta(minutes=2),
        started_at=T0 + timedelta(minutes=2, seconds=5),
        finished_at=T0 + timedelta(minutes=5),
        verdicts=(
            HypothesisVerdict(
                hypothesis="Banco degradado",
                verdict=Verdict.CONFIRMED,
                reasoning="O banco está parado desde 10:00 e os erros começam no mesmo minuto.",
                evidence=(
                    Evidence("StopDBInstance às 10:00", "https://nuvem.interno/auditoria/1"),
                ),
            ),
            HypothesisVerdict(
                hypothesis="Deploy 1.4.2 introduziu regressão",
                verdict=Verdict.REJECTED,
                reasoning="O deploy é um revert e saiu 10 min antes do problema.",
                evidence=(),
            ),
            HypothesisVerdict(
                hypothesis="Adquirente lenta",
                verdict=Verdict.INCONCLUSIVE,
                reasoning="Sem traces da adquirente na janela.",
                evidence=(),
            ),
        ),
        root_cause=ROOT_CAUSE,
        actions=(
            Action(
                action="Religar o banco grolla-lab-db",
                target="RDS grolla-lab-db",
                expected="Conexões voltam e a taxa de erro cai para o normal",
                link="https://nuvem.interno/rds/grolla-lab-db",
            ),
            Action(
                action="Reiniciar o pool do pedidos-api",
                target="pedidos-api",
                expected="Pool reabre as conexões",
                link=None,
            ),
        ),
        missing="",
        evaluations=(
            Evaluation(
                tool="cloud_changes",
                subject="mudanças nos recursos de pedidos-api",
                finding="StopDBInstance em grolla-lab-db às 10:00",
                url="https://nuvem.interno/auditoria/1",
                ok=True,
            ),
        ),
        partial_reason=partial_reason,
    )


def page_with(
    client: TestClient, repository: InMemoryIncidentRepository, verification: Verification | None
) -> str:
    incident = make_incident()
    repository.incidents = {incident.id: incident}
    repository.analyses = {incident.id: make_analysis(incident.id)}
    if verification is not None:
        repository.verifications = {incident.id: replace(verification, incident_id=incident.id)}
    return client.get(f"/incidents/{incident.id}").text


def test_confirmed_verification_shows_root_cause_verdicts_plan_and_evaluations(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    page = page_with(client, repository, make_verification(uuid4()))

    agora = page.index('id="agora-titulo"')
    investigation = page.index("Como o Grolla chegou lá")
    assert agora < investigation < page.index('<h2 id="alertas-titulo">Alertas</h2>')
    assert "validação concluída" in page
    assert agora < page.index(ROOT_CAUSE) < investigation  # em destaque, no cartão "Agora"
    assert page.count(ROOT_CAUSE) == 1
    assert "Causa raiz confirmada" in page
    assert "confirmada" in page
    assert "rejeitada" in page
    assert "inconclusiva" in page
    assert "O deploy é um revert e saiu 10 min antes do problema." in page
    assert 'href="https://nuvem.interno/auditoria/1"' in page
    assert "Comece por aqui" in page
    assert "Religar o banco grolla-lab-db" in page
    assert "RDS grolla-lab-db" in page
    assert "Conexões voltam e a taxa de erro cai para o normal" in page
    assert 'href="https://nuvem.interno/rds/grolla-lab-db"' in page
    assert "O que o Grolla consultou (" in page  # análise e validação juntas
    assert "StopDBInstance em grolla-lab-db às 10:00" in page
    assert "não confirmada" not in page


@pytest.mark.parametrize("status", [AnalysisStatus.PENDING, AnalysisStatus.RUNNING])
def test_verification_not_finished_shows_validating(
    client: TestClient, repository: InMemoryIncidentRepository, status: AnalysisStatus
) -> None:
    pending = replace(
        make_verification(uuid4(), status),
        finished_at=None,
        verdicts=(),
        root_cause=None,
        actions=(),
        evaluations=(),
    )

    page = page_with(client, repository, pending)

    assert "validando as hipóteses" in page
    assert "Deploy 1.4.2 introduziu regressão" in page  # as hipóteses iniciais continuam
    assert "não confirmada" not in page
    assert "Plano de ação" not in page


def test_verification_without_root_cause_says_what_was_missing(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    unconfirmed = replace(
        make_verification(uuid4()),
        root_cause=None,
        actions=(),
        missing="Sem acesso aos eventos de auditoria da nuvem",
    )

    page = page_with(client, repository, unconfirmed)

    assert "Causa raiz não confirmada" in page
    assert "Sem acesso aos eventos de auditoria da nuvem" in page
    assert "Causa raiz:" not in page
    assert "Plano de ação" not in page


def test_partial_verification_shows_the_reason_in_portuguese(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    partial = replace(
        make_verification(uuid4(), AnalysisStatus.PARTIAL, PartialReason.LLM_FAILED),
        verdicts=(),
        root_cause=None,
        actions=(),
    )

    page = page_with(client, repository, partial)

    assert "validação parcial" in page
    assert "LLM indisponível" in page
    assert "falha_do_llm" not in page
    assert "Causa raiz não confirmada" in page
    assert "O que o Grolla consultou (" in page  # análise e validação juntas


def test_incident_without_verification_says_so(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    page = page_with(client, repository, None)

    assert "Hipótese mais provável" in page  # sem validação, o cartão mostra a melhor hipótese
    assert "Causa raiz" not in page


def test_verification_text_is_escaped(
    client: TestClient, repository: InMemoryIncidentRepository
) -> None:
    hostile = replace(make_verification(uuid4()), root_cause="<script>x</script>")

    page = page_with(client, repository, hostile)

    assert "<script>" not in page
    assert "&lt;script&gt;" in page
