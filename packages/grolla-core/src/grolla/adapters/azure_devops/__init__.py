"""Azure DevOps: deploys recentes pela API REST de builds e arquivos do repositório pela Git
Items API, com token somente leitura.

O repositório do serviço vem do mapa (só as exceções) ou, sem entrada, é procurado pelo nome
do serviço nos projetos configurados. O resultado da busca fica em memória.

`read_file` lê no branch padrão do repositório: um arquivo vem com o conteúdo, cortado em
READ_LIMIT caracteres; o caminho vazio ou de pasta devolve a listagem de um nível.
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, Field, ValidationError

from grolla.application.investigation import (
    BINARY_FILE,
    NO_REPOSITORY,
    READ_LIMIT,
    TRUNCATED,
    ToolResult,
    ToolUnavailableError,
)

API_VERSION = "7.1"
TIMEOUT_SECONDS = 20.0
MAX_BUILDS = 10
MESSAGE_CHARS = 120
HTTP_NOT_FOUND = 404
HTTP_OK = 200
MAX_ENTRIES = 200  # itens de uma listagem de pasta que vão para o modelo

_RESULTS = {
    "succeeded": "sucesso",
    "partiallySucceeded": "sucesso parcial",
    "failed": "falhou",
    "canceled": "cancelado",
}
_STATUSES = {
    "inProgress": "em andamento",
    "notStarted": "na fila",
    "cancelling": "cancelando",
    "postponed": "adiado",
}


@dataclass(frozen=True)
class RepositoryRef:
    project: str
    repo: str


@dataclass(frozen=True)
class _Repository:
    project: str
    name: str
    id: str
    branch: str | None  # branch padrão, sem `refs/heads/`; None em repositório vazio


class AzureDevOpsChangeHistory:
    def __init__(
        self,
        base_url: str,
        token: str,
        projects: Sequence[str],
        service_repo_map: Mapping[str, RepositoryRef],
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._projects = tuple(projects)
        self._service_repo_map = dict(service_repo_map)
        self._transport = transport  # testes injetam um transporte fake
        self._repositories: dict[str, _Repository | None] = {}  # cache por serviço

    async def recent_deploys(self, service: str, since: datetime) -> ToolResult:
        try:
            async with self._http() as client:
                repository = await self._repository(client, service)
                if repository is None:
                    return ToolResult(f"{service}: {NO_REPOSITORY}", None)
                builds = await self._builds(client, repository, since)
        except httpx2.HTTPError as error:
            raise ToolUnavailableError(f"falha de rede: {type(error).__name__}") from error
        return self._summary(repository, builds, since)

    async def read_file(self, service: str, path: str) -> ToolResult:
        try:
            async with self._http() as client:
                repository = await self._repository(client, service)
                if repository is None:
                    return ToolResult(f"{service}: {NO_REPOSITORY}", None)
                wanted = "/" + path.strip().strip("/")
                item = None
                if wanted != "/":
                    item = await self._items(client, repository, wanted, file=True)
                    if item is None:
                        return ToolResult(
                            f"{wanted} não existe em {repository.name} "
                            f"(branch {repository.branch or 'padrão'})",
                            self._web(repository, "/"),
                        )
                if item is None or _validate(_Item, item).is_folder:
                    listing = await self._items(client, repository, wanted, file=False)
                    return self._listing(repository, wanted, _validate(_Items, listing).value)
        except httpx2.HTTPError as error:
            raise ToolUnavailableError(f"falha de rede: {type(error).__name__}") from error
        return self._file(repository, wanted, _validate(_Item, item))

    def _http(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=self._transport,
            timeout=TIMEOUT_SECONDS,
            auth=httpx2.BasicAuth("", self._token),  # PAT em Basic, usuário vazio
        )

    async def _items(
        self, client: httpx2.AsyncClient, repository: _Repository, path: str, *, file: bool
    ) -> Any:
        """Git Items API no branch padrão: o arquivo com o conteúdo, ou a listagem da pasta."""
        params = {"$format": "json"}
        if file:
            params |= {"path": path, "includeContent": "true", "includeContentMetadata": "true"}
        else:  # a listagem de pasta usa `scopePath`: com `path` a API responde 400
            params |= {"scopePath": path, "recursionLevel": "OneLevel"}
        if repository.branch:
            params |= {
                "versionDescriptor.version": repository.branch,
                "versionDescriptor.versionType": "branch",
            }
        return await self._get(
            client,
            f"{quote(repository.project)}/_apis/git/repositories/{quote(repository.id)}/items",
            params,
            missing_ok=True,
        )

    def _file(self, repository: _Repository, path: str, item: "_Item") -> ToolResult:
        url = self._web(repository, path)
        where = f"{path} de {repository.name}"
        content = item.content or ""
        if (item.metadata and item.metadata.is_binary) or "\x00" in content:
            return ToolResult(f"{where}: {BINARY_FILE}", url)
        if len(content) > READ_LIMIT:
            cut = f" ({TRUNCATED}: os primeiros {READ_LIMIT} de {len(content)} caracteres)"
            return ToolResult(f"{where}{cut}:\n{content[:READ_LIMIT]}", url)
        return ToolResult(f"{where}:\n{content}", url)

    def _listing(self, repository: _Repository, path: str, items: list["_Item"]) -> ToolResult:
        prefix = path.rstrip("/") + "/"
        names = [
            item.path.removeprefix(prefix) + ("/" if item.is_folder else "")
            for item in items
            if item.path.rstrip("/") != path.rstrip("/")  # a própria pasta vem na lista
        ]
        where = f"{path} de {repository.name}"
        if not names:
            return ToolResult(f"{where} está vazia", self._web(repository, path))
        shown = ", ".join(sorted(names)[:MAX_ENTRIES])
        more = f" e mais {len(names) - MAX_ENTRIES}" if len(names) > MAX_ENTRIES else ""
        summary = f"{len(names)} item(ns) em {where} (pastas terminam em /): {shown}{more}"
        return ToolResult(summary, self._web(repository, path))

    def _web(self, repository: _Repository, path: str) -> str:
        url = (
            f"{self._base_url}/{quote(repository.project)}/_git/{quote(repository.name)}"
            f"?path={quote(path)}"
        )
        return f"{url}&version=GB{quote(repository.branch)}" if repository.branch else url

    async def _repository(self, client: httpx2.AsyncClient, service: str) -> _Repository | None:
        if service in self._repositories:
            return self._repositories[service]
        mapped = self._service_repo_map.get(service)
        candidates = [mapped] if mapped else [RepositoryRef(p, service) for p in self._projects]
        found: _Repository | None = None
        for candidate in candidates:
            path = f"{quote(candidate.project)}/_apis/git/repositories/{quote(candidate.repo)}"
            response = await self._get(client, path, {}, missing_ok=True)
            if response is not None:
                repo = _validate(_Repo, response)
                branch = (repo.default_branch or "").removeprefix("refs/heads/") or None
                found = _Repository(candidate.project, repo.name, repo.id, branch)
                break
        self._repositories[service] = found
        return found

    async def _builds(
        self, client: httpx2.AsyncClient, repository: _Repository, since: datetime
    ) -> list["_Build"]:
        params = {
            "repositoryId": repository.id,
            "repositoryType": "TfsGit",
            "minTime": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # Com minTime, o Azure DevOps real devolve zero builds se a ordem for por início;
            # pela fila funciona e dá a mesma ordem para deploys em sequência.
            "queryOrder": "queueTimeDescending",
            "$top": str(MAX_BUILDS),
        }
        path = f"{quote(repository.project)}/_apis/build/builds"
        builds = _validate(_Builds, await self._get(client, path, params)).value
        return list(
            await asyncio.gather(*(self._with_message(client, repository, b) for b in builds))
        )

    async def _with_message(
        self, client: httpx2.AsyncClient, repository: _Repository, build: "_Build"
    ) -> "_Build":
        """Sem a mensagem na lista, busca a do commit (só dos builds listados).

        Falha nessa busca não derruba os deploys: o build segue sem a mensagem.
        """
        if build.message() is not None or not build.source_version:
            return build
        path = (
            f"{quote(repository.project)}/_apis/git/repositories/{quote(repository.id)}"
            f"/commits/{quote(build.source_version)}"
        )
        try:
            commit = _validate(_Commit, await self._get(client, path, {}, missing_ok=True))
        except ToolUnavailableError:
            return build
        return build.model_copy(update={"source_version_message": commit.comment})

    async def _get(
        self,
        client: httpx2.AsyncClient,
        path: str,
        params: dict[str, str],
        *,
        missing_ok: bool = False,
    ) -> Any:
        response = await client.get(
            f"{self._base_url}/{path}", params={**params, "api-version": API_VERSION}
        )
        if missing_ok and response.status_code == HTTP_NOT_FOUND:
            return None
        # Token inválido costuma voltar 203 com a página de login, não 401.
        if response.status_code != HTTP_OK:
            raise ToolUnavailableError(f"Azure DevOps respondeu HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as error:
            raise ToolUnavailableError("resposta do Azure DevOps fora de JSON") from error

    def _summary(
        self, repository: _Repository, builds: list["_Build"], since: datetime
    ) -> ToolResult:
        pipelines = f"{self._base_url}/{quote(repository.project)}/_build"
        if not builds:
            return ToolResult(
                f"nenhum deploy do repositório {repository.name} desde {_when(since)}", pipelines
            )
        lines = "; ".join(_line(build) for build in builds)
        summary = (
            f"{len(builds)} deploy(s) do repositório {repository.name} desde {_when(since)}: "
            f"{lines}"
        )
        first = builds[0].links.web
        return ToolResult(summary, first.href if first else pipelines)


class _Repo(BaseModel):
    id: str
    name: str
    default_branch: str | None = Field(default=None, alias="defaultBranch")


class _Metadata(BaseModel):
    is_binary: bool = Field(default=False, alias="isBinary")


class _Item(BaseModel):
    path: str
    is_folder: bool = Field(default=False, alias="isFolder")
    content: str | None = None
    metadata: _Metadata | None = Field(default=None, alias="contentMetadata")


class _Items(BaseModel):
    value: list[_Item]


class _Href(BaseModel):
    href: str


class _Links(BaseModel):
    web: _Href | None = None


class _Named(BaseModel):
    name: str


class _Identity(BaseModel):
    display_name: str = Field(alias="displayName")


class _Build(BaseModel):
    build_number: str = Field(alias="buildNumber")
    status: str
    result: str | None = None
    queue_time: datetime = Field(alias="queueTime")
    start_time: datetime | None = Field(default=None, alias="startTime")
    source_branch: str | None = Field(default=None, alias="sourceBranch")
    source_version: str | None = Field(default=None, alias="sourceVersion")
    definition: _Named
    requested_for: _Identity | None = Field(default=None, alias="requestedFor")
    links: _Links = Field(default_factory=_Links, alias="_links")
    trigger_info: dict[str, str] = Field(default_factory=dict[str, str], alias="triggerInfo")
    source_version_message: str | None = Field(default=None, alias="sourceVersionMessage")

    def message(self) -> str | None:
        """Primeira linha da mensagem do commit, se o build a trouxe."""
        text = (self.trigger_info.get("ci.message") or self.source_version_message or "").strip()
        if not text:
            return None
        first = text.splitlines()[0]
        return first if len(first) <= MESSAGE_CHARS else first[: MESSAGE_CHARS - 1] + "…"


class _Commit(BaseModel):
    comment: str | None = None


class _Builds(BaseModel):
    value: list[_Build]


def _validate[T: BaseModel](model: type[T], payload: Any) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise ToolUnavailableError("resposta do Azure DevOps em formato inesperado") from error


def _line(build: _Build) -> str:
    branch = (build.source_branch or "").removeprefix("refs/heads/")
    commit = (build.source_version or "")[:7]
    source = " ".join(part for part in (branch, commit) if part)
    author = build.requested_for.display_name if build.requested_for else "?"
    state = (
        _RESULTS.get(build.result or "", build.result or "?")
        if build.status == "completed"
        else _STATUSES.get(build.status, build.status)
    )
    origin = f" ({source})" if source else ""
    message = build.message()
    origin += f' "{message}"' if message else ""
    started = _when(build.start_time or build.queue_time)
    return f"{started} {build.definition.name} {build.build_number}{origin} por {author}: {state}"


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%d/%m %H:%M UTC")
