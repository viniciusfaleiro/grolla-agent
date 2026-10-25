"""Azure DevOps: deploys recentes pela API REST de builds, com token somente leitura.

O repositório do serviço vem do mapa (só as exceções) ou, sem entrada, é procurado pelo nome
do serviço nos projetos configurados. O resultado da busca fica em memória.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx2
from pydantic import BaseModel, Field, ValidationError

from grolla.application.investigation import NO_REPOSITORY, ToolResult, ToolUnavailableError

API_VERSION = "7.1"
TIMEOUT_SECONDS = 20.0
MAX_BUILDS = 10
HTTP_NOT_FOUND = 404
HTTP_OK = 200

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
            async with httpx2.AsyncClient(
                transport=self._transport,
                timeout=TIMEOUT_SECONDS,
                auth=httpx2.BasicAuth("", self._token),  # PAT em Basic, usuário vazio
            ) as client:
                repository = await self._repository(client, service)
                if repository is None:
                    return ToolResult(f"{service}: {NO_REPOSITORY}", None)
                builds = await self._builds(client, repository, since)
        except httpx2.HTTPError as error:
            raise ToolUnavailableError(f"falha de rede: {type(error).__name__}") from error
        return self._summary(repository, builds, since)

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
                found = _Repository(candidate.project, repo.name, repo.id)
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
        return _validate(_Builds, await self._get(client, path, params)).value

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
    started = _when(build.start_time or build.queue_time)
    return f"{started} {build.definition.name} {build.build_number}{origin} por {author}: {state}"


def _when(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%d/%m %H:%M UTC")
