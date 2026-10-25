"""Azure DevOps: deploys recentes pela API REST de builds, com token somente leitura."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import httpx2

from grolla.application.investigation import ToolResult


@dataclass(frozen=True)
class RepositoryRef:
    project: str
    repo: str


class AzureDevOpsChangeHistory:
    def __init__(
        self,
        base_url: str,
        token: str,
        projects: Sequence[str],
        service_repo_map: Mapping[str, RepositoryRef],
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._projects = tuple(projects)
        self._service_repo_map = dict(service_repo_map)
        self._transport = transport  # testes injetam um transporte fake

    async def recent_deploys(self, service: str, since: datetime) -> ToolResult:
        raise NotImplementedError
