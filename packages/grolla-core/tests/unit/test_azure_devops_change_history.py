"""AzureDevOpsChangeHistory contra a API REST fake (httpx2.MockTransport), sem rede."""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from contracts.change_history_contract import ChangeHistoryContract
from grolla.adapters.azure_devops import AzureDevOpsChangeHistory, RepositoryRef
from grolla.application.investigation import NO_REPOSITORY, ChangeHistory, ToolUnavailableError

FIXTURES = Path(__file__).parent.parent / "fixtures" / "azure_devops"
BASE = "https://dev.azure.com/minha-org"
TOKEN = "pat-somente-leitura"  # noqa: S105 — token fake dos testes
SINCE = datetime(2026, 10, 25, 5, 0, tzinfo=UTC)
REPO_ID = "5febef5a-833d-4e14-b9c0-14cb638f91e6"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeAzureDevOps:
    """Responde repositórios conhecidos por (projeto, nome) e os builds; grava as requisições."""

    def __init__(self, repositories: set[tuple[str, str]] | None = None) -> None:
        self.repositories = (
            repositories if repositories is not None else {("Core", "authorizer-api")}
        )
        self.builds: Any = fixture("builds")
        self.status = 200
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx2.Response(self.status, text="<html>login</html>")
        parts = request.url.path.split("/")  # /minha-org/{projeto}/_apis/...
        project = parts[2]
        if "/_apis/git/repositories/" in request.url.path:
            if (project, parts[-1]) not in self.repositories:
                return httpx2.Response(404, json={"message": "TF401019: repository not found"})
            return httpx2.Response(200, json={**fixture("repository"), "name": parts[-1]})
        return httpx2.Response(200, json=self.builds)

    def history(
        self, projects: tuple[str, ...] = ("Core",), mapping: dict[str, RepositoryRef] | None = None
    ) -> AzureDevOpsChangeHistory:
        return AzureDevOpsChangeHistory(
            BASE, TOKEN, projects, mapping or {}, httpx2.MockTransport(self)
        )

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


class TestAzureDevOpsChangeHistoryContract(ChangeHistoryContract):
    @pytest.fixture
    def changes(self) -> ChangeHistory:
        return FakeAzureDevOps().history()

    @pytest.fixture
    def unavailable_changes(self) -> ChangeHistory:
        fake = FakeAzureDevOps()
        fake.status = 503
        return fake.history()


async def test_deploys_list_time_version_author_and_link() -> None:
    fake = FakeAzureDevOps()

    result = await fake.history().recent_deploys("authorizer-api", SINCE)

    assert result.summary == (
        "2 deploy(s) do repositório authorizer-api desde 25/10 05:00 UTC: "
        "25/10 10:38 UTC authorizer-api-deploy 20261025.3 (main a1b2c3d) por Fulana de Tal: "
        "sucesso; 25/10 08:12 UTC authorizer-api-deploy 20261025.2 (refs/pull/931/merge "
        "9f8e7d6) por Beltrano Souza: em andamento"
    )
    assert result.url == (
        "https://dev.azure.com/minha-org/7f0c8a3e-2c1b-4f55-9b2e-0d8a1c3e9f10"
        "/_build/results?buildId=48211"
    )


async def test_builds_are_filtered_by_repository_and_since_with_basic_pat() -> None:
    fake = FakeAzureDevOps()

    await fake.history().recent_deploys("authorizer-api", SINCE)

    builds = fake.requests[-1]
    assert builds.url.path == "/minha-org/Core/_apis/build/builds"
    assert dict(builds.url.params) == {
        "repositoryId": REPO_ID,
        "repositoryType": "TfsGit",
        "minTime": "2026-10-25T05:00:00Z",
        "queryOrder": "startTimeDescending",
        "$top": "10",
        "api-version": "7.1",
    }
    expected = base64.b64encode(f":{TOKEN}".encode()).decode()
    assert builds.headers["authorization"] == f"Basic {expected}"


async def test_repository_is_found_by_service_name_in_the_projects() -> None:
    fake = FakeAzureDevOps({("Pagamentos", "captura-api")})

    result = await fake.history(("Core", "Pagamentos")).recent_deploys("captura-api", SINCE)

    assert fake.paths() == [
        "/minha-org/Core/_apis/git/repositories/captura-api",
        "/minha-org/Pagamentos/_apis/git/repositories/captura-api",
        "/minha-org/Pagamentos/_apis/build/builds",
    ]
    assert "repositório captura-api" in result.summary


async def test_map_covers_the_exceptions() -> None:
    fake = FakeAzureDevOps()
    mapping = {"autorizador": RepositoryRef("Core", "authorizer-api")}

    result = await fake.history(("Outro",), mapping).recent_deploys("autorizador", SINCE)

    assert fake.paths()[0] == "/minha-org/Core/_apis/git/repositories/authorizer-api"
    assert "repositório authorizer-api" in result.summary


async def test_lookup_is_cached_in_memory() -> None:
    fake = FakeAzureDevOps()
    history = fake.history()

    await history.recent_deploys("authorizer-api", SINCE)
    await history.recent_deploys("authorizer-api", SINCE)
    await history.recent_deploys("sem-repo", SINCE)
    await history.recent_deploys("sem-repo", SINCE)

    lookups = [p for p in fake.paths() if "/git/repositories/" in p]
    assert lookups == [
        "/minha-org/Core/_apis/git/repositories/authorizer-api",
        "/minha-org/Core/_apis/git/repositories/sem-repo",
    ]


async def test_service_without_repository_is_an_answer() -> None:
    result = await FakeAzureDevOps().history().recent_deploys("sem-repo", SINCE)

    assert result.summary == f"sem-repo: {NO_REPOSITORY}"
    assert result.url is None


async def test_no_builds_links_to_the_pipelines() -> None:
    fake = FakeAzureDevOps()
    fake.builds = {"count": 0, "value": []}

    result = await fake.history().recent_deploys("authorizer-api", SINCE)

    assert result.summary == "nenhum deploy do repositório authorizer-api desde 25/10 05:00 UTC"
    assert result.url == f"{BASE}/Core/_build"


async def test_completed_build_without_web_link_and_failed() -> None:
    fake = FakeAzureDevOps()
    build = {
        "buildNumber": "7",
        "status": "completed",
        "result": "failed",
        "queueTime": "2026-10-25T09:00:00Z",
        "definition": {"name": "ci"},
    }
    fake.builds = {"count": 1, "value": [build]}

    result = await fake.history().recent_deploys("authorizer-api", SINCE)

    assert result.summary.endswith("25/10 09:00 UTC ci 7 por ?: falhou")
    assert result.url == f"{BASE}/Core/_build"


@pytest.mark.parametrize("status", [203, 401, 500])
async def test_refused_or_failing_api_is_unavailable(status: int) -> None:
    fake = FakeAzureDevOps()
    fake.status = status

    with pytest.raises(ToolUnavailableError, match=f"HTTP {status}"):
        await fake.history().recent_deploys("authorizer-api", SINCE)


async def test_unexpected_payload_is_unavailable() -> None:
    fake = FakeAzureDevOps()
    fake.builds = {"unexpected": True}

    with pytest.raises(ToolUnavailableError, match="formato inesperado"):
        await fake.history().recent_deploys("authorizer-api", SINCE)


async def test_non_json_payload_is_unavailable() -> None:
    def html(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, text="<html>")

    history = AzureDevOpsChangeHistory(BASE, TOKEN, ["Core"], {}, httpx2.MockTransport(html))

    with pytest.raises(ToolUnavailableError, match="fora de JSON"):
        await history.recent_deploys("authorizer-api", SINCE)


async def test_network_failure_is_unavailable_and_not_cached() -> None:
    calls: list[int] = []

    def refuse(_request: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        raise httpx2.ConnectError("recusada")

    history = AzureDevOpsChangeHistory(BASE, TOKEN, ["Core"], {}, httpx2.MockTransport(refuse))

    for _ in range(2):
        with pytest.raises(ToolUnavailableError, match="falha de rede"):
            await history.recent_deploys("authorizer-api", SINCE)
    assert len(calls) == 2
