"""API do Kubernetes fake em `/k8s/{cluster}/...`, só com as leituras que o adapter usa.

Dois clusters, como o `e2e/kubeconfig.yaml`: `nao-pci` (namespace `pagamentos`) e `pci`
(namespace `cartoes`). Em cada um roda o serviço `cluster-api`: um Deployment (rótulo
`app.kubernetes.io/name=cluster-api`) na revisão 1, de 2 dias atrás, com dois pods prontos e o
ConfigMap `cluster-config` (`DB_POOL_SIZE=20`, criado pelo `helm`) lido por `envFrom`. Sem
eventos de aviso, HPA nem NetworkPolicy.

O roteiro (`POST /_control/k8s {"configmap_edit": {...}}`) faz o ConfigMap ter sido editado por
um gerenciador (`kubectl-edit`) há N minutos, com o valor novo da chave em cada cluster; o
`/_control/reset` volta ao padrão. Toda leitura exige um token Bearer (senão 401) e fica gravada
com a query em `body`. Escrita não existe aqui: outro método cai na superfície genérica (404).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from grolla_fakes.state import FakesState, RecordedRequest

PREFIX = "/k8s/{cluster}"
SERVICE = "cluster-api"
CONFIG_MAP = "cluster-config"
LABEL = "app.kubernetes.io/name"
NAMESPACES = {"nao-pci": "pagamentos", "pci": "cartoes"}
CREATED_AGO = timedelta(days=2)
# Recurso da URL -> tipo dos objetos.
_KINDS = {
    "deployments": "Deployment",
    "statefulsets": "StatefulSet",
    "daemonsets": "DaemonSet",
    "replicasets": "ReplicaSet",
    "pods": "Pod",
    "events": "Event",
    "configmaps": "ConfigMap",
    "horizontalpodautoscalers": "HorizontalPodAutoscaler",
    "networkpolicies": "NetworkPolicy",
}
_GROUPS = ("api/v1", "apis/apps/v1", "apis/autoscaling/v2", "apis/networking.k8s.io/v1")


class ConfigMapEdit(BaseModel):
    """O ConfigMap `cluster-config` editado há `minutes_ago` minutos por `manager`."""

    minutes_ago: int = 5
    manager: str = "kubectl-edit"
    key: str = "DB_POOL_SIZE"
    values: dict[str, str] = {}  # cluster -> valor novo; cluster fora daqui não muda


class KubernetesScript(BaseModel):
    configmap_edit: ConfigMapEdit | None = None


class KubernetesFake:
    def __init__(self, state: FakesState) -> None:
        self._state = state
        self.script = KubernetesScript()

    def reset(self) -> None:
        self.script = KubernetesScript()

    def router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/_control/k8s", status_code=204)
        async def load(script: KubernetesScript) -> None:  # pyright: ignore[reportUnusedFunction]
            self.script = script

        for group in _GROUPS:
            base = f"{PREFIX}/{group}/namespaces/{{namespace}}/{{plural}}"

            @router.get(base)
            async def listing(  # pyright: ignore[reportUnusedFunction]
                cluster: str, namespace: str, plural: str, request: Request
            ) -> JSONResponse:
                found = [
                    item
                    for item in self._objects(cluster, namespace, plural)
                    if _selected(item, request)
                ]
                kind = _KINDS.get(plural, "Unknown")
                return self._answer(request, {"kind": f"{kind}List", "items": found}, 200)

            @router.get(base + "/{name}")
            async def reading(  # pyright: ignore[reportUnusedFunction]
                cluster: str, namespace: str, plural: str, name: str, request: Request
            ) -> JSONResponse:
                found = [
                    item
                    for item in self._objects(cluster, namespace, plural)
                    if item["metadata"]["name"] == name
                ]
                if not found:
                    return self._answer(request, _status(404, f'{plural} "{name}" not found'), 404)
                return self._answer(request, found[0], 200)

        return router

    def _answer(self, request: Request, body: Any, status: int) -> JSONResponse:
        if not request.headers.get("authorization", "").startswith("Bearer "):
            status, body = 401, _status(401, "Unauthorized")
        self._state.requests.append(
            RecordedRequest(
                method="GET",
                path=request.url.path,
                body=dict(request.query_params) or None,
                status=status,
            )
        )
        return JSONResponse(body, status_code=status)

    def _objects(self, cluster: str, namespace: str, plural: str) -> list[dict[str, Any]]:
        if NAMESPACES.get(cluster) != namespace:
            return []
        kind = _KINDS.get(plural)
        return [item for item in self._cluster(cluster, namespace) if item["kind"] == kind]

    def _cluster(self, cluster: str, namespace: str) -> list[dict[str, Any]]:
        """Os objetos do cluster agora: o Deployment, o ReplicaSet, os pods e o ConfigMap."""
        now = datetime.now(UTC)
        created = _iso(now - CREATED_AGO)
        data = {"DB_POOL_SIZE": "20", "DB_TIMEOUT": "30s"}
        fields = [
            _entry("helm", created, {"f:data": {".": {}, "f:DB_POOL_SIZE": {}, "f:DB_TIMEOUT": {}}})
        ]
        edit = self.script.configmap_edit
        if edit is not None and cluster in edit.values:
            data[edit.key] = edit.values[cluster]
            edited = _iso(now - timedelta(minutes=edit.minutes_ago))
            fields = [
                _entry("helm", created, {"f:data": {".": {}, "f:DB_TIMEOUT": {}}}),
                _entry(edit.manager, edited, {"f:data": {f"f:{edit.key}": {}}}),
            ]
        labels = {LABEL: SERVICE}
        template = _template(labels)
        deployment = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                **_meta(SERVICE, namespace, created, labels),
                "uid": f"{cluster}-deployment",
                "generation": 1,
                "annotations": {"deployment.kubernetes.io/revision": "1"},
                "managedFields": [
                    _entry("helm", created, {"f:spec": {"f:replicas": {}, "f:template": {}}})
                ],
            },
            "spec": {"replicas": 2, "selector": {"matchLabels": labels}, "template": template},
            "status": {
                "observedGeneration": 1,
                "replicas": 2,
                "updatedReplicas": 2,
                "readyReplicas": 2,
                "availableReplicas": 2,
            },
        }
        replica_set = {
            "apiVersion": "apps/v1",
            "kind": "ReplicaSet",
            "metadata": {
                **_meta(f"{SERVICE}-6f7d8c9b5a", namespace, created, labels),
                "annotations": {"deployment.kubernetes.io/revision": "1"},
                "ownerReferences": [
                    {
                        "apiVersion": "apps/v1",
                        "kind": "Deployment",
                        "name": SERVICE,
                        "uid": f"{cluster}-deployment",
                    }
                ],
            },
            "spec": {"replicas": 2, "template": template},
            "status": {"replicas": 2, "readyReplicas": 2},
        }
        pods = [
            _pod(f"{SERVICE}-6f7d8c9b5a-{suffix}", namespace, created, labels)
            for suffix in ("a1b2c", "d3e4f")
        ]
        config_map = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {**_meta(CONFIG_MAP, namespace, created, {}), "managedFields": fields},
            "data": data,
        }
        return [deployment, replica_set, *pods, config_map]


def _selected(item: dict[str, Any], request: Request) -> bool:
    """`labelSelector` e `fieldSelector` de igualdade (`k=v,k2=v2`), como o adapter pede."""
    labels: dict[str, str] = item["metadata"].get("labels") or {}
    for pair in filter(None, request.query_params.get("labelSelector", "").split(",")):
        key, _, value = pair.partition("=")
        if labels.get(key) != value:
            return False
    for pair in filter(None, request.query_params.get("fieldSelector", "").split(",")):
        key, _, value = pair.partition("=")
        if str(item.get(key)) != value:
            return False
    return True


def _template(labels: dict[str, str]) -> dict[str, Any]:
    container = {
        "name": "app",
        "image": f"registry.interno/{SERVICE}:1.0.0",
        "envFrom": [{"configMapRef": {"name": CONFIG_MAP}}],
        "resources": {"limits": {"cpu": "500m", "memory": "256Mi"}},
    }
    return {"metadata": {"labels": labels}, "spec": {"containers": [container]}}


def _pod(name: str, namespace: str, created: str, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": _meta(name, namespace, created, labels),
        "spec": _template(labels)["spec"],
        "status": {
            "phase": "Running",
            "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [
                {
                    "name": "app",
                    "ready": True,
                    "restartCount": 0,
                    "state": {"running": {"startedAt": created}},
                }
            ],
        },
    }


def _meta(name: str, namespace: str, created: str, labels: dict[str, str]) -> dict[str, Any]:
    return {"name": name, "namespace": namespace, "creationTimestamp": created, "labels": labels}


def _entry(manager: str, time: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "manager": manager,
        "operation": "Update",
        "apiVersion": "v1",
        "time": time,
        "fieldsType": "FieldsV1",
        "fieldsV1": fields,
    }


def _status(code: int, message: str) -> dict[str, Any]:
    return {
        "kind": "Status",
        "apiVersion": "v1",
        "status": "Failure",
        "message": message,
        "code": code,
    }


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")
