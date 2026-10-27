"""AzureDevOpsChangeHistory contra a API REST fake (httpx2.MockTransport), sem rede."""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from contracts.change_history_contract import ChangeHistoryContract, RepositoryFilesContract
from grolla.adapters.azure_devops import AzureDevOpsChangeHistory, RepositoryRef
from grolla.application.investigation import (
    BINARY_FILE,
    NO_REPOSITORY,
    READ_LIMIT,
    ChangeHistory,
    ToolUnavailableError,
)

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
        self.commit_status = 200
        self.files: dict[str, Any] = {"/README.md": fixture("item_file")}  # Git Items por caminho
        self.listing: Any = fixture("items_root")
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx2.Response(self.status, text="<html>login</html>")
        parts = request.url.path.split("/")  # /minha-org/{projeto}/_apis/...
        project = parts[2]
        if "/commits/" in request.url.path:
            return httpx2.Response(self.commit_status, json=fixture("commit"))
        if request.url.path.endswith("/items"):
            return self._items(request.url.params)
        if "/_apis/git/repositories/" in request.url.path:
            if (project, parts[-1]) not in self.repositories:
                return httpx2.Response(404, json={"message": "TF401019: repository not found"})
            return httpx2.Response(200, json={**fixture("repository"), "name": parts[-1]})
        return httpx2.Response(200, json=self.builds)

    def _items(self, params: httpx2.QueryParams) -> httpx2.Response:
        """Git Items: a listagem com `recursionLevel` (a pasta em `scopePath`; com `path` a API
        real responde 400), senão o arquivo do caminho (ou 404).
        """
        if "recursionLevel" in params:
            if "scopePath" not in params:
                return httpx2.Response(400, json={"message": "VS403403: scopePath obrigatório"})
            return httpx2.Response(200, json=self.listing)
        item = self.files.get(params["path"])
        if item is None:
            return httpx2.Response(404, json={"message": "TF401174: item not found"})
        return httpx2.Response(200, json=item)

    def history(
        self, projects: tuple[str, ...] = ("Core",), mapping: dict[str, RepositoryRef] | None = None
    ) -> AzureDevOpsChangeHistory:
        return AzureDevOpsChangeHistory(
            BASE, TOKEN, projects, mapping or {}, httpx2.MockTransport(self)
        )

    def paths(self, *, commits: bool = False) -> list[str]:
        """Caminhos pedidos; sem as buscas de mensagem de commit, salvo se `commits`."""
        return [r.url.path for r in self.requests if commits or "/commits/" not in r.url.path]


class TestAzureDevOpsChangeHistoryContract(ChangeHistoryContract):
    @pytest.fixture
    def changes(self) -> ChangeHistory:
        return FakeAzureDevOps().history()

    @pytest.fixture
    def unavailable_changes(self) -> ChangeHistory:
        fake = FakeAzureDevOps()
        fake.status = 503
        return fake.history()


class TestAzureDevOpsRepositoryFilesContract(RepositoryFilesContract):
    @pytest.fixture
    def changes(self, large_path: str, binary_path: str) -> ChangeHistory:
        fake = FakeAzureDevOps()
        fake.files[f"/{large_path}"] = {"path": f"/{large_path}", "content": "x" * 30_000}
        fake.files[f"/{binary_path}"] = {
            "path": f"/{binary_path}",
            "contentMetadata": {"isBinary": True},
        }
        return fake.history()

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
        "25/10 10:38 UTC authorizer-api-deploy 20261025.3 (main a1b2c3d) "
        '"Aumenta o pool de conexões" por Fulana de Tal: sucesso; 25/10 08:12 UTC '
        'authorizer-api-deploy 20261025.2 (refs/pull/931/merge 9f8e7d6) "Merge PR 931: Corrige '
        'o arredondamento da captura" por Beltrano Souza: em andamento'
    )
    assert result.url == (
        "https://dev.azure.com/minha-org/7f0c8a3e-2c1b-4f55-9b2e-0d8a1c3e9f10"
        "/_build/results?buildId=48211"
    )


async def test_builds_are_filtered_by_repository_and_since_with_basic_pat() -> None:
    fake = FakeAzureDevOps()

    await fake.history().recent_deploys("authorizer-api", SINCE)

    [builds] = [r for r in fake.requests if r.url.path.endswith("/build/builds")]
    assert builds.url.path == "/minha-org/Core/_apis/build/builds"
    assert dict(builds.url.params) == {
        "repositoryId": REPO_ID,
        "repositoryType": "TfsGit",
        "minTime": "2026-10-25T05:00:00Z",
        "queryOrder": "queueTimeDescending",
        "$top": "10",
        "api-version": "7.1",
    }
    expected = base64.b64encode(f":{TOKEN}".encode()).decode()
    assert builds.headers["authorization"] == f"Basic {expected}"


async def test_message_comes_from_the_commit_only_when_the_build_lacks_it() -> None:
    fake = FakeAzureDevOps()

    await fake.history().recent_deploys("authorizer-api", SINCE)

    commits = [p for p in fake.paths(commits=True) if "/commits/" in p]
    assert commits == [
        "/minha-org/Core/_apis/git/repositories/5febef5a-833d-4e14-b9c0-14cb638f91e6/commits/"
        "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432"
    ]


@pytest.mark.parametrize("status", [404, 500])
async def test_commit_lookup_failure_keeps_the_build_without_message(status: int) -> None:
    fake = FakeAzureDevOps()
    fake.commit_status = status

    result = await fake.history().recent_deploys("authorizer-api", SINCE)

    assert result.summary.endswith(
        "20261025.2 (refs/pull/931/merge 9f8e7d6) por Beltrano Souza: em andamento"
    )


async def test_build_message_is_its_first_line_clipped() -> None:
    fake = FakeAzureDevOps()
    build = {
        "buildNumber": "8",
        "status": "completed",
        "result": "succeeded",
        "queueTime": "2026-10-25T09:00:00Z",
        "definition": {"name": "ci"},
        "sourceVersionMessage": "Revert " + "x" * 200 + "\nsegunda linha",
    }
    fake.builds = {"count": 1, "value": [build]}

    result = await fake.history().recent_deploys("authorizer-api", SINCE)

    assert '"Revert xxx' in result.summary
    assert "segunda linha" not in result.summary
    assert "…" in result.summary


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
    with pytest.raises(ToolUnavailableError, match="falha de rede"):
        await history.read_file("authorizer-api", "README.md")


# ---- read_file: Git Items API no branch padrão

WEB = f"{BASE}/Core/_git/authorizer-api"


async def test_read_file_brings_the_content_from_the_default_branch_and_the_link() -> None:
    fake = FakeAzureDevOps()

    result = await fake.history().read_file("authorizer-api", "README.md")

    items = fake.requests[-1]
    assert items.url.path == f"/minha-org/Core/_apis/git/repositories/{REPO_ID}/items"
    assert dict(items.url.params) == {
        "path": "/README.md",
        "$format": "json",
        "includeContent": "true",
        "includeContentMetadata": "true",
        "versionDescriptor.version": "main",
        "versionDescriptor.versionType": "branch",
        "api-version": "7.1",
    }
    assert result.summary.startswith("/README.md de authorizer-api:\n# authorizer-api\n")
    assert "pool de 20 conexões" in result.summary
    assert result.url == f"{WEB}?path=/README.md&version=GBmain"


@pytest.mark.parametrize("path", ["", "/", "  "])
async def test_empty_path_lists_the_root(path: str) -> None:
    fake = FakeAzureDevOps()

    result = await fake.history().read_file("authorizer-api", path)

    assert fake.requests[-1].url.params["recursionLevel"] == "OneLevel"
    assert fake.requests[-1].url.params["scopePath"] == "/"
    assert result.summary == (
        "4 item(ns) em / de authorizer-api (pastas terminam em /): "
        "README.md, azure-pipelines.yml, docs/, src/"
    )
    assert result.url == f"{WEB}?path=/&version=GBmain"


async def test_folder_path_lists_the_folder() -> None:
    fake = FakeAzureDevOps()
    fake.files["/docs"] = {"path": "/docs", "isFolder": True, "gitObjectType": "tree"}
    fake.listing = {
        "count": 3,
        "value": [
            {"path": "/docs", "isFolder": True},
            {"path": "/docs/runbook.md"},
            {"path": "/docs/adr", "isFolder": True},
        ],
    }

    result = await fake.history().read_file("authorizer-api", "docs/")

    assert result.summary == (
        "2 item(ns) em /docs de authorizer-api (pastas terminam em /): adr/, runbook.md"
    )


async def test_empty_folder_and_long_listing() -> None:
    fake = FakeAzureDevOps()
    fake.listing = {"value": [{"path": "/", "isFolder": True}]}

    assert (await fake.history().read_file("authorizer-api", "")).summary == (
        "/ de authorizer-api está vazia"
    )

    fake.listing = {"value": [{"path": f"/f{i:03}.md"} for i in range(205)]}

    assert (await fake.history().read_file("authorizer-api", "")).summary.endswith(
        "f199.md e mais 5"
    )


async def test_large_file_is_cut_and_says_so() -> None:
    fake = FakeAzureDevOps()
    fake.files["/big.md"] = {"path": "/big.md", "content": "x" * (READ_LIMIT + 10)}

    result = await fake.history().read_file("authorizer-api", "big.md")

    header, _, content = result.summary.partition(":\n")
    assert header == (
        f"/big.md de authorizer-api (arquivo cortado: os primeiros {READ_LIMIT} de "
        f"{READ_LIMIT + 10} caracteres)"
    )
    assert content == "x" * READ_LIMIT


@pytest.mark.parametrize(
    "item",
    [
        {"path": "/logo.png", "content": "", "contentMetadata": {"isBinary": True}},
        {"path": "/logo.png", "content": "PNG\x00\x01"},
    ],
)
async def test_binary_file_is_refused_with_a_warning(item: dict[str, Any]) -> None:
    fake = FakeAzureDevOps()
    fake.files["/logo.png"] = item

    result = await fake.history().read_file("authorizer-api", "logo.png")

    assert result.summary == f"/logo.png de authorizer-api: {BINARY_FILE}"


async def test_missing_file_is_an_answer() -> None:
    result = await FakeAzureDevOps().history().read_file("authorizer-api", "nao-existe.md")

    assert result.summary == "/nao-existe.md não existe em authorizer-api (branch main)"
    assert result.url == f"{WEB}?path=/&version=GBmain"


async def test_read_file_of_service_without_repository() -> None:
    result = await FakeAzureDevOps().history().read_file("servico-sem-repositorio", "README.md")

    assert result.summary == f"servico-sem-repositorio: {NO_REPOSITORY}"


async def test_empty_repository_has_no_branch() -> None:
    def empty(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/items"):
            assert "versionDescriptor.version" not in request.url.params
            return httpx2.Response(404, json={"message": "TF401174"})
        return httpx2.Response(200, json={"id": REPO_ID, "name": "authorizer-api"})

    history = AzureDevOpsChangeHistory(BASE, TOKEN, ["Core"], {}, httpx2.MockTransport(empty))

    result = await history.read_file("authorizer-api", "README.md")

    assert result.summary == "/README.md não existe em authorizer-api (branch padrão)"
    assert result.url == f"{WEB}?path=/"


async def test_read_file_with_refused_api_is_unavailable() -> None:
    fake = FakeAzureDevOps()
    fake.status = 203

    with pytest.raises(ToolUnavailableError, match="HTTP 203"):
        await fake.history().read_file("authorizer-api", "README.md")


def documented() -> FakeAzureDevOps:
    """O repositório inteiro com três `.md` (um em maiúsculas) e arquivos que não são `.md`."""
    fake = FakeAzureDevOps()
    fake.listing = fixture("items_full")
    fake.files["/docs/runbook.md"] = {
        "path": "/docs/runbook.md",
        "content": "# Runbook\n\n## Pool de conexões esgotado\n\nPause a reconciliação.\n",
    }
    fake.files["/docs/ADR-001.MD"] = {"path": "/docs/ADR-001.MD", "content": "# ADR 1\n"}
    return fake


async def test_documents_are_the_md_files_smallest_first_with_their_links() -> None:
    fake = documented()

    documents = await fake.history().documents("authorizer-api", 10, 100_000)

    [listing] = [r for r in fake.requests if "recursionLevel" in r.url.params]
    assert dict(listing.url.params) == {
        "$format": "json",
        "scopePath": "/",
        "recursionLevel": "Full",
        "versionDescriptor.version": "main",
        "versionDescriptor.versionType": "branch",
        "api-version": "7.1",
    }
    assert [d.path for d in documents] == ["docs/ADR-001.MD", "docs/runbook.md", "README.md"]
    assert documents[1].url == f"{WEB}?path=/docs/runbook.md&version=GBmain"
    assert "## Pool de conexões esgotado" in documents[1].content
    fetched = sorted(r.url.params["path"] for r in fake.requests if "path" in r.url.params)
    assert fetched == ["/README.md", "/docs/ADR-001.MD", "/docs/runbook.md"]


async def test_documents_stop_at_the_file_and_byte_limits() -> None:
    history = documented().history()
    smallest = len(b"# ADR 1\n")

    assert [d.path for d in await history.documents("authorizer-api", 1, 100_000)] == [
        "docs/ADR-001.MD"
    ]
    assert [d.path for d in await history.documents("authorizer-api", 10, smallest)] == [
        "docs/ADR-001.MD"
    ]
    assert await history.documents("authorizer-api", 10, smallest - 1) == []


async def test_documents_skip_binary_and_vanished_files() -> None:
    fake = documented()
    fake.files["/docs/ADR-001.MD"] = {"path": "/docs/ADR-001.MD", "content": "PNG\x00"}
    del fake.files["/docs/runbook.md"]

    documents = await fake.history().documents("authorizer-api", 10, 100_000)

    assert [d.path for d in documents] == ["README.md"]


async def test_documents_of_service_without_repository_are_empty() -> None:
    assert await documented().history().documents("servico-sem-repositorio", 10, 1000) == []


async def test_documents_with_failing_api_or_network_are_unavailable() -> None:
    fake = documented()
    fake.status = 503

    with pytest.raises(ToolUnavailableError, match="HTTP 503"):
        await fake.history().documents("authorizer-api", 10, 1000)

    def refuse(_request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("recusada")

    history = AzureDevOpsChangeHistory(BASE, TOKEN, ["Core"], {}, httpx2.MockTransport(refuse))
    with pytest.raises(ToolUnavailableError, match="falha de rede"):
        await history.documents("authorizer-api", 10, 1000)
