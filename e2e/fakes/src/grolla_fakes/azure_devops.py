"""API REST fake do Azure DevOps em `/azure-devops/{org}/{project}/_apis/...`.

Sem roteiro, todo repositório existe (em qualquer projeto) e tem 1 deploy bem-sucedido há
20 min, com a mensagem do commit em `triggerInfo`, coerente com a história padrão do MCP fake
do SigNoz. Os repositórios estão no branch `main` e têm na raiz um `README.md` (que cita o
banco `<serviço sem -api>-db`) e um `azure-pipelines.yml`, pela Git Items API. O roteiro
(`POST /_control/azure-devops`) limita os repositórios, fixa os builds ou faz a API falhar.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from grolla_fakes.state import FakesState, RecordedRequest

PREFIX = "/azure-devops/{org}/{project}/_apis"
DEPLOY_AGE = timedelta(minutes=20)


class AzureDevOpsScript(BaseModel):
    repositories: list[str] | None = None  # nomes que existem; None = todos
    builds: list[dict[str, Any]] | None = None  # builds fixos no formato da API; None = padrão
    status: int = 200  # diferente de 200: a API inteira responde esse status


class AzureDevOpsFake:
    def __init__(self, state: FakesState) -> None:
        self._state = state
        self.script = AzureDevOpsScript()

    def reset(self) -> None:
        self.script = AzureDevOpsScript()

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/_control/azure-devops", status_code=204)
        async def load(script: AzureDevOpsScript) -> None:  # pyright: ignore[reportUnusedFunction]
            self.script = script

        @router.get(PREFIX + "/git/repositories/{repo}")
        async def repository(  # pyright: ignore[reportUnusedFunction]
            project: str, repo: str, request: Request
        ) -> JSONResponse:
            known = self.script.repositories is None or repo in self.script.repositories
            body = {
                "id": repo,
                "name": repo,
                "project": {"name": project},
                "defaultBranch": "refs/heads/main",
            }
            if not known:
                body = {"message": f"TF401019: o repositório {repo} não existe"}
            status = 200 if known else 404
            return self._answer(request, body, status)

        @router.get(PREFIX + "/git/repositories/{repo}/items")
        async def items(  # pyright: ignore[reportUnusedFunction]
            repo: str, request: Request
        ) -> JSONResponse:
            path = request.query_params.get("path", "/")
            if "recursionLevel" in request.query_params:
                listing = [{"path": "/", "isFolder": True, "gitObjectType": "tree"}]
                listing += [{"path": f"/{name}", "gitObjectType": "blob"} for name in _files(repo)]
                listing.append({"path": "/src", "isFolder": True, "gitObjectType": "tree"})
                return self._answer(request, {"count": len(listing), "value": listing}, 200)
            content = _files(repo).get(path.lstrip("/"))
            if content is None:
                return self._answer(request, {"message": f"TF401174: {path} não existe"}, 404)
            item = {"path": path, "gitObjectType": "blob", "content": content}
            return self._answer(request, item, 200)

        @router.get(PREFIX + "/build/builds")
        async def builds(  # pyright: ignore[reportUnusedFunction]
            org: str, project: str, request: Request
        ) -> JSONResponse:
            repo = request.query_params.get("repositoryId", "")
            values = self.script.builds
            if values is None:
                values = [_deploy(org, project, repo, datetime.now(UTC) - DEPLOY_AGE)]
                since = request.query_params.get("minTime")
                if since:
                    values = [b for b in values if b["startTime"] >= since]
            return self._answer(request, {"count": len(values), "value": values}, 200)

        return router

    def _answer(self, request: Request, body: Any, status: int) -> JSONResponse:
        if self.script.status != 200:
            status, body = self.script.status, {"message": "falha roteirizada"}
        elif not request.headers.get("authorization", "").startswith("Basic "):
            status, body = 401, {"message": "sem PAT"}
        self._state.requests.append(
            RecordedRequest(
                method="GET",
                path=request.url.path,
                body=dict(request.query_params) or None,
                status=status,
            )
        )
        return JSONResponse(body, status_code=status)


def _deploy(org: str, project: str, repo: str, started: datetime) -> dict[str, Any]:
    return {
        "id": 4821,
        "buildNumber": started.strftime("%Y%m%d") + ".1",
        "status": "completed",
        "result": "succeeded",
        "queueTime": _iso(started - timedelta(seconds=10)),
        "startTime": _iso(started),
        "finishTime": _iso(started + timedelta(minutes=6)),
        "definition": {"id": 1, "name": f"{repo}-deploy"},
        "sourceBranch": "refs/heads/main",
        "sourceVersion": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "triggerInfo": {"ci.message": "Aumenta o pool de conexões"},
        "requestedFor": {"displayName": "Fulana de Tal"},
        "repository": {"id": repo, "type": "TfsGit", "name": repo},
        "_links": {
            "web": {
                "href": f"http://localhost:9000/azure-devops/{org}/{project}"
                "/_build/results?buildId=4821"
            }
        },
    }


def _files(repo: str) -> dict[str, str]:
    """Os arquivos da raiz de todo repositório: o README e o pipeline."""
    database = repo.removesuffix("-api")
    readme = (
        f"# {repo}\n\n"
        f"Serviço {repo} da Credenciadora. Guarda os dados no PostgreSQL `{database}-db` "
        "(pool de 20 conexões) e sobe por pipeline a cada merge na main.\n\n"
        "## Como investigar\n\n"
        "- Erros de conexão com o banco costumam ser o pool esgotado depois de deploy.\n"
    )
    pipeline = f"trigger:\n  - main\nstages:\n  - stage: deploy\n    displayName: {repo}-deploy\n"
    return {"README.md": readme, "azure-pipelines.yml": pipeline}


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
