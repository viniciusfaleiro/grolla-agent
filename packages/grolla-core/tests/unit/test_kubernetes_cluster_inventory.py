"""KubernetesClusterInventory com o ApiClient real do cliente oficial, sem rede.

O transporte HTTP (o `pool_manager` do urllib3) é trocado por uma API fake que serve os objetos
gravados em `fixtures/kubernetes/pedidos.json` (o `pedidos-api` no namespace `pagamentos`): o
cliente monta as URLs e os parâmetros como contra um cluster de verdade, e a fake responde como
o servidor da API (listagens com `labelSelector` e `fieldSelector`, leitura por nome, 404, 403).
"""

import base64
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import boto3  # pyright: ignore[reportMissingTypeStubs] — o boto3 não traz stubs de tipo
import pytest
import urllib3
from botocore.stub import Stubber  # pyright: ignore[reportMissingTypeStubs] — sem stubs
from contracts.cluster_inventory_contract import ClusterInventoryContract
from grolla.adapters.kubernetes import (
    CLOUD_OPERATIONS,
    READ_OPERATIONS,
    ClusterTarget,
    KubernetesClusterInventory,
    connect_eks,
)
from grolla.application.investigation import (
    NO_CLUSTER_CHANGES,
    NO_WORKLOADS,
    ClusterInventory,
    ToolUnavailableError,
)
from kubernetes import client as k8s

FIXTURES = Path(__file__).parent.parent / "fixtures" / "kubernetes"
SINCE = datetime(2026, 10, 27, 2, 0, tzinfo=UTC)
NAMESPACE = "pagamentos"
_PATH = re.compile(r"^/(?:api/v1|apis/[^/]+/[^/]+)/namespaces/([^/]+)/([^/]+)(?:/([^/]+))?$")
_PLURALS = {
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


def recorded() -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = json.loads((FIXTURES / "pedidos.json").read_text())
    return objects


class FakeKubeApi:
    """Faz o papel do servidor da API do Kubernetes atrás do urllib3 do cliente."""

    def __init__(self, objects: list[dict[str, Any]]) -> None:
        self.objects = objects
        self.requests: list[tuple[str, str]] = []  # método e caminho com a query
        self.refusing: dict[str, int] = {}  # recurso (plural) -> status; "*" recusa tudo

    def request(self, method: str, url: str, **_: Any) -> urllib3.HTTPResponse:
        parts = urlsplit(url)
        self.requests.append((method, f"{parts.path}?{parts.query}"))
        match = _PATH.match(parts.path)
        assert match, parts.path
        namespace, plural, name = match.groups()
        status = self.refusing.get(plural) or self.refusing.get("*")
        if status:
            return _response({"kind": "Status", "code": status}, status)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        found = [
            item
            for item in self.objects
            if item["kind"] == _PLURALS[plural] and item["metadata"]["namespace"] == namespace
        ]
        if name is not None:
            named = [item for item in found if item["metadata"]["name"] == name]
            return _response(named[0], 200) if named else _response({"code": 404}, 404)
        selected = [item for item in found if _selected(item, query)]
        return _response({"kind": f"{_PLURALS[plural]}List", "items": selected}, 200)


def _selected(item: dict[str, Any], query: dict[str, str]) -> bool:
    labels: dict[str, str] = item["metadata"].get("labels") or {}
    for pair in filter(None, query.get("labelSelector", "").split(",")):
        key, _, value = pair.partition("=")
        if labels.get(key) != value:
            return False
    for pair in filter(None, query.get("fieldSelector", "").split(",")):
        key, _, value = pair.partition("=")
        if str(item.get(key)) != value:
            return False
    return True


_REASONS = {200: "OK", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found"}


def _response(body: Any, status: int) -> urllib3.HTTPResponse:
    return urllib3.HTTPResponse(
        body=json.dumps(body).encode(),
        status=status,
        reason=_REASONS.get(status, "Erro"),
        headers={"content-type": "application/json"},
    )


def api_client(api: FakeKubeApi) -> Any:
    client: Any = k8s.ApiClient(k8s.Configuration(host="https://k8s.test"))
    client.rest_client.pool_manager = api
    return client


def target(name: str = "nao-pci", **changes: Any) -> ClusterTarget:
    return ClusterTarget(name=name, mode="kubeconfig", namespaces=(NAMESPACE,), **changes)


def inventory(api: FakeKubeApi, **changes: Any) -> KubernetesClusterInventory:
    return KubernetesClusterInventory([target(**changes)], api_clients={"nao-pci": api_client(api)})


@pytest.fixture
def api() -> FakeKubeApi:
    return FakeKubeApi(recorded())


class TestKubernetesClusterInventoryContract(ClusterInventoryContract):
    @pytest.fixture
    def cluster(self) -> ClusterInventory:
        return inventory(FakeKubeApi(recorded()))

    @pytest.fixture
    def unavailable_cluster(self) -> ClusterInventory:
        api = FakeKubeApi(recorded())
        api.refusing["*"] = 401
        return inventory(api)


# ---- Estado


async def test_state_shows_replicas_pods_hpa_and_events(api: FakeKubeApi) -> None:
    summary = (await inventory(api).state("pedidos-api", SINCE)).summary

    assert summary.startswith("1 workload(s) de pedidos-api no cluster:\n")
    assert "Deployment pedidos-api em pagamentos no cluster nao-pci: 1 de 3 réplicas prontas." in (
        summary
    )
    assert "rollout em andamento" not in summary
    assert (
        "pod pedidos-api-7c9b4f6d8e-k2x9p: Running, pronto, 4 reinício(s), último término "
        "OOMKilled (exit 137) às 03:05, limites cpu 500m, memória 128Mi"
    ) in summary
    assert "esperando CrashLoopBackOff" in summary
    assert "pod pedidos-api-7c9b4f6d8e-w8n3c: Running, não pronto, probe falhando: readiness" in (
        summary
    )
    assert "HPA pedidos-api: no máximo (3 de 3), 3 desejadas" in summary
    assert (
        "eventos de aviso desde 02:00 UTC: 03:07 Unhealthy pod/pedidos-api-7c9b4f6d8e-w8n3c: "
        "Readiness probe failed: HTTP probe failed with statuscode: 503 (x12); 03:06 BackOff "
        "pod/pedidos-api-7c9b4f6d8e-q7m4z"
    ) in summary
    assert "fora da janela" not in summary  # evento antes de `since`
    assert "outro serviço" not in summary
    assert "ScalingReplicaSet" not in summary  # só eventos de aviso


async def test_not_ready_pods_come_first(api: FakeKubeApi) -> None:
    summary = (await inventory(api).state("pedidos-api", SINCE)).summary

    not_ready = summary.index("pedidos-api-7c9b4f6d8e-q7m4z:")
    assert not_ready < summary.index("pedidos-api-7c9b4f6d8e-k2x9p:")


async def test_state_shows_at_most_ten_pods(api: FakeKubeApi) -> None:
    pod = next(item for item in api.objects if item["kind"] == "Pod")
    for index in range(12):
        api.objects.append({**pod, "metadata": {**pod["metadata"], "name": f"extra-{index:02d}"}})

    summary = (await inventory(api).state("pedidos-api", SINCE)).summary

    assert "pods (mostrando 10 de 15):" in summary
    assert summary.count(": Running, ") == 10


async def test_hpa_below_the_maximum(api: FakeKubeApi) -> None:
    hpa = next(item for item in api.objects if item["kind"] == "HorizontalPodAutoscaler")
    hpa["spec"]["maxReplicas"] = 10

    summary = (await inventory(api).state("pedidos-api", SINCE)).summary

    assert "HPA pedidos-api: 3 atuais, 3 desejadas, máximo 10" in summary


async def test_rollout_in_progress(api: FakeKubeApi) -> None:
    deployment = api.objects[0]
    deployment["status"]["updatedReplicas"] = 1

    summary = (await inventory(api).state("pedidos-api", SINCE)).summary

    assert "1 de 3 réplicas prontas, rollout em andamento" in summary


async def test_statefulset_and_daemonset_are_workloads_too(api: FakeKubeApi) -> None:
    labels = {"app.kubernetes.io/name": "fila-api"}
    base = {"metadata": {"namespace": NAMESPACE, "labels": labels, "generation": 2}}
    api.objects += [
        {
            **base,
            "kind": "StatefulSet",
            "metadata": {**base["metadata"], "name": "fila"},
            "spec": {"replicas": 2, "selector": {"matchLabels": labels}},
            "status": {"readyReplicas": 2, "currentRevision": "a", "updateRevision": "b"},
        },
        {
            **base,
            "kind": "DaemonSet",
            "metadata": {**base["metadata"], "name": "fila-agente"},
            "spec": {"selector": {"matchLabels": labels}},
            "status": {
                "desiredNumberScheduled": 3,
                "numberReady": 3,
                "updatedNumberScheduled": 3,
                "observedGeneration": 2,
            },
        },
    ]

    summary = (await inventory(api).state("fila-api", SINCE)).summary

    assert (
        "StatefulSet fila em pagamentos no cluster nao-pci: 2 de 2 réplicas prontas, "
        "rollout em andamento"
    ) in summary
    assert (
        "DaemonSet fila-agente em pagamentos no cluster nao-pci: 3 de 3 réplicas prontas. "
        "nenhum pod" in summary
    )


async def test_the_second_label_is_the_fallback(api: FakeKubeApi) -> None:
    summary = (await inventory(api).state("outro-api", SINCE)).summary

    assert (
        "Deployment outro-api em pagamentos no cluster nao-pci: 1 de 1 réplicas prontas" in summary
    )
    assert "pedidos-api" not in summary


async def test_workload_map_links_the_exceptions(api: FakeKubeApi) -> None:
    mapped = inventory(api, workload_map={"legado": ("pagamentos/outro-api", "pagamentos/sumiu")})

    summary = (await mapped.state("legado", SINCE)).summary

    assert "Deployment outro-api em pagamentos" in summary


async def test_service_outside_the_cluster(api: FakeKubeApi) -> None:
    result = await inventory(api).state("servico-sem-workload", SINCE)

    assert result.summary == NO_WORKLOADS
    assert (await inventory(api).changes("servico-sem-workload", SINCE)).summary == NO_WORKLOADS


async def test_denied_events_keep_the_rest_of_the_state(api: FakeKubeApi) -> None:
    api.refusing["events"] = 403

    summary = (await inventory(api).state("pedidos-api", SINCE)).summary

    assert "Deployment pedidos-api em pagamentos no cluster nao-pci" in summary
    assert summary.endswith(
        "eventos no cluster nao-pci: não foi possível consultar (o cluster recusou: 403 Forbidden)"
    )


async def test_denied_workloads_are_unavailable(api: FakeKubeApi) -> None:
    api.refusing["deployments"] = 403

    with pytest.raises(ToolUnavailableError, match="o cluster recusou: 403 Forbidden"):
        await inventory(api).state("pedidos-api", SINCE)


# ---- Mudanças


async def test_changes_from_the_most_recent(api: FakeKubeApi) -> None:
    summary = (await inventory(api).changes("pedidos-api", SINCE)).summary

    assert summary.split("\n") == [
        "4 mudança(s) no cluster para pedidos-api desde 02:00 UTC, da mais recente:",
        "03:10 ConfigMap pedidos-config alterado por kubectl-edit no cluster nao-pci: "
        "DB_POOL_SIZE=2",
        "03:00 Deployment pedidos-api réplicas por kubectl-scale no cluster nao-pci: agora 3",
        "02:50 Deployment pedidos-api revisão 5 por kubectl-set no cluster nao-pci: "
        "env LOG_LEVEL: info → debug, limite de memória 512Mi → 128Mi",
        "02:30 NetworkPolicy pagamentos-padrao em pagamentos alterada por "
        "kubectl-client-side-apply no cluster nao-pci",
    ]


async def test_hidden_values_never_leave_the_adapter(api: FakeKubeApi) -> None:
    pci = KubernetesClusterInventory(
        [target("pci", show_values=False)], api_clients={"pci": api_client(api)}
    )

    summary = (await pci.changes("pedidos-api", SINCE)).summary

    assert (
        "03:10 ConfigMap pedidos-config alterado por kubectl-edit no cluster pci: "
        "DB_POOL_SIZE (valor oculto)"
    ) in summary
    assert (
        "02:30 NetworkPolicy pagamentos-padrao em pagamentos alterada por "
        "kubectl-client-side-apply no cluster pci" in summary
    )
    assert "env LOG_LEVEL alterado (valor oculto)" in summary
    assert "=2" not in summary
    assert "debug" not in summary
    assert "info" not in summary


async def test_new_variables_and_secret_references(api: FakeKubeApi) -> None:
    revision = next(
        item for item in api.objects if item["metadata"]["name"] == "pedidos-api-7c9b4f6d8e"
    )
    container = revision["spec"]["template"]["spec"]["containers"][0]
    container["image"] = "registry.interno/pedidos-api:1.5.0"
    container["env"] = [
        {"name": "LOG_LEVEL", "value": "info"},
        {"name": "DB_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "novo", "key": "senha"}}},
        {"name": "MODO", "value": "rapido"},
        {"name": "FILA", "valueFrom": {"configMapKeyRef": {"name": "filas", "key": "nome"}}},
        {"name": "NO", "valueFrom": {"fieldRef": {"fieldPath": "spec.nodeName"}}},
    ]

    shown = (await inventory(api).changes("pedidos-api", SINCE)).summary
    hidden = (await inventory(api, show_values=False).changes("pedidos-api", SINCE)).summary

    assert "imagem registry.interno/pedidos-api:1.4.2 → registry.interno/pedidos-api:1.5.0" in shown
    assert "env DB_PASSWORD: do Secret pedidos-segredos → do Secret novo" in shown
    assert "env MODO=rapido adicionada" in shown
    assert "env FILA adicionada (do ConfigMap filas)" in shown
    assert "env NO adicionada (de um campo do pod)" in shown
    assert "env MODO adicionada (valor oculto)" in hidden
    assert "rapido" not in hidden


async def test_removed_variable_container_and_restart(api: FakeKubeApi) -> None:
    revision = next(
        item for item in api.objects if item["metadata"]["name"] == "pedidos-api-7c9b4f6d8e"
    )
    template = revision["spec"]["template"]
    template["metadata"]["annotations"] = {"kubectl.kubernetes.io/restartedAt": "2026-10-27"}
    app = template["spec"]["containers"][0]
    app["env"] = app["env"][:1]
    template["spec"]["containers"].append({"name": "proxy", "image": "envoy:1"})

    summary = (await inventory(api).changes("pedidos-api", SINCE)).summary

    assert "contêiner proxy adicionado" in summary
    assert "app: env DB_PASSWORD removida" in summary
    assert "reinício pedido (rollout restart)" in summary


async def test_first_revision_and_unchanged_template(api: FakeKubeApi) -> None:
    for item in api.objects:
        if item["metadata"]["name"] == "pedidos-api-6d8f7b9c4d":
            item["metadata"]["creationTimestamp"] = "2026-10-27T02:40:00Z"
            item["spec"]["template"] = json.loads(json.dumps(api.objects[2]["spec"]["template"]))

    summary = (await inventory(api).changes("pedidos-api", SINCE)).summary

    assert (
        "02:40 Deployment pedidos-api revisão 4 por kubectl-set no cluster nao-pci: "
        "primeira revisão"
    ) in summary
    assert (
        "02:50 Deployment pedidos-api revisão 5 por kubectl-set no cluster nao-pci: "
        "template alterado"
    ) in summary


async def test_hpa_and_statefulset_changes(api: FakeKubeApi) -> None:
    hpa = next(item for item in api.objects if item["kind"] == "HorizontalPodAutoscaler")
    hpa["metadata"]["managedFields"][0]["manager"] = "kubectl-patch"
    hpa["metadata"]["managedFields"][0]["time"] = "2026-10-27T03:01:00Z"
    labels = {"app.kubernetes.io/name": "pedidos-api"}
    api.objects.append(
        {
            "kind": "StatefulSet",
            "metadata": {
                "name": "pedidos-cache",
                "namespace": NAMESPACE,
                "labels": labels,
                "managedFields": [
                    {
                        "manager": "helm",
                        "time": "2026-10-27T02:45:00Z",
                        "fieldsV1": {"f:spec": {"f:template": {}, "f:replicas": {}}},
                    }
                ],
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "cache"}},
                "template": {"spec": {"containers": [{"name": "cache"}]}},
            },
        }
    )

    summary = (await inventory(api).changes("pedidos-api", SINCE)).summary

    assert (
        "03:01 HPA pedidos-api alterado por kubectl-patch no cluster nao-pci: mínimo 1, máximo 3"
        in summary
    )
    assert (
        "02:45 StatefulSet pedidos-cache alterado por helm no cluster nao-pci: template do pod"
        in summary
    )
    assert "StatefulSet pedidos-cache réplicas" not in summary  # veio junto do template


async def test_config_maps_from_variables_and_projected_volumes(api: FakeKubeApi) -> None:
    deployment = api.objects[0]
    spec = deployment["spec"]["template"]["spec"]
    spec["containers"][0]["envFrom"] = []
    spec["containers"][0]["env"].append(
        {"name": "POOL", "valueFrom": {"configMapKeyRef": {"name": "pedidos-config", "key": "x"}}}
    )
    spec["volumes"] = [
        {"name": "p", "projected": {"sources": [{"configMap": {"name": "pedidos-arquivos"}}]}}
    ]
    arquivos = next(item for item in api.objects if item["metadata"]["name"] == "pedidos-arquivos")
    arquivos["metadata"]["managedFields"][0]["time"] = "2026-10-27T02:20:00Z"
    del arquivos["data"]["regras.yaml"]

    summary = (await inventory(api).changes("pedidos-api", SINCE)).summary

    assert (
        "ConfigMap pedidos-config alterado por kubectl-edit no cluster nao-pci: DB_POOL_SIZE=2"
        in summary
    )
    assert (
        "02:20 ConfigMap pedidos-arquivos alterado por helm no cluster nao-pci: "
        "regras.yaml removida"
    ) in summary


async def test_no_changes_in_the_window(api: FakeKubeApi) -> None:
    late = datetime(2026, 10, 27, 4, 0, tzinfo=UTC)

    assert (await inventory(api).changes("pedidos-api", late)).summary == NO_CLUSTER_CHANGES


async def test_denied_config_maps_keep_the_other_changes(api: FakeKubeApi) -> None:
    api.refusing["configmaps"] = 403
    api.refusing["networkpolicies"] = 403

    summary = (await inventory(api).changes("pedidos-api", SINCE)).summary

    assert "revisão 5 por kubectl-set" in summary
    assert (
        "ConfigMap pedidos-config no cluster nao-pci: não foi possível consultar "
        "(o cluster recusou: 403"
    ) in summary
    assert (
        "NetworkPolicies no cluster nao-pci: não foi possível consultar "
        "(o cluster recusou: 403 Forbidden)" in summary
    )


async def test_without_field_records(api: FakeKubeApi) -> None:
    del api.objects[0]["metadata"]["managedFields"]

    summary = (await inventory(api).changes("pedidos-api", SINCE)).summary

    assert (
        "02:50 Deployment pedidos-api revisão 5 por sem registro de campo no cluster nao-pci"
        in (summary)
    )


# ---- Vários clusters


def two_clusters(nao_pci: FakeKubeApi, pci: FakeKubeApi) -> KubernetesClusterInventory:
    return KubernetesClusterInventory(
        [target("nao-pci"), target("pci", show_values=False)],
        api_clients={"nao-pci": api_client(nao_pci), "pci": api_client(pci)},
    )


async def test_each_item_says_its_cluster() -> None:
    clusters = two_clusters(FakeKubeApi(recorded()), FakeKubeApi(recorded()))

    state = (await clusters.state("pedidos-api", SINCE)).summary
    changes = (await clusters.changes("pedidos-api", SINCE)).summary

    assert state.startswith("2 workload(s) de pedidos-api no cluster:")
    assert "Deployment pedidos-api em pagamentos no cluster nao-pci: 1 de 3" in state
    assert "Deployment pedidos-api em pagamentos no cluster pci: 1 de 3" in state
    assert (
        "ConfigMap pedidos-config alterado por kubectl-edit no cluster nao-pci: DB_POOL_SIZE=2"
        in (changes)
    )
    assert (
        "ConfigMap pedidos-config alterado por kubectl-edit no cluster pci: "
        "DB_POOL_SIZE (valor oculto)"
    ) in changes


async def test_one_cluster_down_does_not_hide_the_other() -> None:
    pci = FakeKubeApi(recorded())
    pci.refusing["*"] = 401

    summary = (await two_clusters(FakeKubeApi(recorded()), pci).state("pedidos-api", SINCE)).summary

    assert "no cluster nao-pci" in summary
    assert summary.endswith(
        "cluster pci: não foi possível consultar (o cluster recusou: 401 Unauthorized)"
    )


async def test_every_cluster_down_is_unavailable() -> None:
    nao_pci, pci = FakeKubeApi(recorded()), FakeKubeApi(recorded())
    nao_pci.refusing["*"] = pci.refusing["*"] = 403

    with pytest.raises(ToolUnavailableError, match=r"cluster nao-pci: .*; cluster pci: "):
        await two_clusters(nao_pci, pci).changes("pedidos-api", SINCE)


# ---- Só leitura


async def test_only_reads_leave_the_adapter(api: FakeKubeApi) -> None:
    await inventory(api).state("pedidos-api", SINCE)
    await inventory(api).changes("pedidos-api", SINCE)

    assert {method for method, _ in api.requests} == {"GET"}
    assert not any("secret" in path for _, path in api.requests)


def test_the_closed_list_has_only_reads() -> None:
    assert all(operation.startswith(("list_", "read_")) for _, operation in READ_OPERATIONS)
    assert not any("secret" in operation for _, operation in READ_OPERATIONS)
    assert {("eks", "describe_cluster"), ("sts", "assume_role")} == CLOUD_OPERATIONS


async def test_a_read_outside_the_list_is_refused(api: FakeKubeApi) -> None:
    clusters = inventory(api)
    cluster: Any = clusters._clusters[0]  # pyright: ignore[reportPrivateUsage] — guarda interna

    with pytest.raises(ToolUnavailableError, match="leitura fora da lista"):
        cluster.read("CoreV1Api", "list_namespaced_secret", namespace=NAMESPACE)


# ---- Acesso


async def test_missing_kubeconfig_context_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        "apiVersion: v1\nkind: Config\nclusters: []\nusers: []\ncontexts: []\ncurrent-context: ''\n"
    )
    monkeypatch.setenv("KUBECONFIG", str(kubeconfig))
    clusters = KubernetesClusterInventory([target(context="nao-existe")])

    with pytest.raises(ToolUnavailableError, match="acesso mal configurado"):
        await clusters.state("pedidos-api", SINCE)


async def test_in_cluster_outside_a_pod_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    clusters = KubernetesClusterInventory(
        [ClusterTarget(name="local", mode="in_cluster", namespaces=(NAMESPACE,))]
    )

    with pytest.raises(ToolUnavailableError, match="acesso mal configurado"):
        await clusters.changes("pedidos-api", SINCE)


async def test_eks_without_cloud_credentials_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", "/nao/existe")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/nao/existe")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    eks = ClusterTarget(
        name="eks", mode="eks", namespaces=(NAMESPACE,), eks_name="lab", region="us-east-1"
    )

    with pytest.raises(ToolUnavailableError, match="nuvem indisponível: NoCredentialsError"):
        await KubernetesClusterInventory([eks]).state("pedidos-api", SINCE)


REGION = "us-east-1"
ROLE = "arn:aws:iam::222233334444:role/grolla-somente-leitura"
CA = base64.b64encode(b"-----BEGIN CERTIFICATE-----\nteste\n-----END CERTIFICATE-----\n")


class EksSession:
    """Faz o papel do `boto3.Session`: o EKS e o AssumeRole sob Stubber, o resto real."""

    def __init__(self) -> None:
        self._session: Any = boto3.Session(
            aws_access_key_id="teste",
            aws_secret_access_key="teste",  # noqa: S106 — credencial fake: nada sai daqui
        )
        self.eks: Any = self._session.client("eks", region_name=REGION)
        self.eks_stub: Any = Stubber(self.eks)
        self.sts: Any = self._session.client("sts", region_name=REGION)
        self.sts_stub: Any = Stubber(self.sts)
        self.credentials: list[dict[str, str]] = []

    def client(self, service: str, **arguments: Any) -> Any:
        credentials = {k: v for k, v in arguments.items() if k.startswith("aws_")}
        self.credentials.append(credentials)
        if service == "eks":
            return self.eks
        if "endpoint_url" not in arguments:
            return self.sts  # o AssumeRole
        return self._session.client(service, **arguments)  # a URL pré-assinada, sem rede

    def expect(self, *, assume: int) -> None:
        for _ in range(assume):
            self.sts_stub.add_response(
                "assume_role",
                {
                    "Credentials": {
                        "AccessKeyId": "ASIAROLEEXEMPLO01",
                        "SecretAccessKey": "segredo-da-role",
                        "SessionToken": "sessao-da-role",
                        "Expiration": datetime(2026, 10, 27, 5, 0, tzinfo=UTC),
                    }
                },
                {"RoleArn": ROLE, "RoleSessionName": "grolla-somente-leitura"},
            )
        self.eks_stub.add_response(
            "describe_cluster",
            {
                "cluster": {
                    "name": "lab",
                    "endpoint": "https://ABC123.gr7.us-east-1.eks.amazonaws.com",
                    "certificateAuthority": {"data": CA.decode()},
                }
            },
            {"name": "lab"},
        )
        self.eks_stub.activate()
        self.sts_stub.activate()


def eks_target(role_arn: str | None = None) -> ClusterTarget:
    return ClusterTarget(
        name="eks",
        mode="eks",
        namespaces=(NAMESPACE,),
        eks_name="lab",
        region=REGION,
        role_arn=role_arn,
    )


def decoded(token: str) -> str:
    assert token.startswith("k8s-aws-v1.")
    raw = token.removeprefix("k8s-aws-v1.")
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()


def test_eks_client_uses_the_cluster_endpoint_ca_and_a_presigned_token() -> None:
    session = EksSession()
    session.expect(assume=0)

    client: Any = connect_eks(eks_target(), session)

    configuration = client.configuration
    assert configuration.host == "https://ABC123.gr7.us-east-1.eks.amazonaws.com"
    assert Path(configuration.ssl_ca_cert).read_bytes() == base64.b64decode(CA)
    url = decoded(configuration.api_key["authorization"])
    assert url.startswith("https://sts.us-east-1.amazonaws.com/?Action=GetCallerIdentity")
    assert "X-Amz-Expires=60" in url
    assert "x-k8s-aws-id" in parse_qs(urlsplit(url).query)["X-Amz-SignedHeaders"][0]
    assert configuration.get_api_key_with_prefix("BearerToken", alias="authorization").startswith(
        "Bearer k8s-aws-v1."
    )
    session.eks_stub.assert_no_pending_responses()


def test_eks_with_role_assumes_it_and_renews_the_token() -> None:
    session = EksSession()
    session.expect(assume=3)  # o DescribeCluster, o primeiro token e o renovado

    client: Any = connect_eks(eks_target(ROLE), session)
    first = client.configuration.api_key["authorization"]
    hook = client.configuration.refresh_api_key_hook
    token = hook.__self__
    token._made -= timedelta(minutes=14)
    hook(client.configuration)

    assert client.configuration.api_key["authorization"].startswith("k8s-aws-v1.")
    assert "X-Amz-Security-Token=sessao-da-role" in decoded(first)
    assert {"aws_access_key_id": "ASIAROLEEXEMPLO01"}.items() <= session.credentials[1].items()
    session.sts_stub.assert_no_pending_responses()  # a renovação assumiu a role de novo


async def test_old_pods_still_up_is_a_rollout(api: FakeKubeApi) -> None:
    api.objects[0]["status"]["replicas"] = 4

    summary = (await inventory(api).state("pedidos-api", SINCE)).summary

    assert "1 de 3 réplicas prontas, rollout em andamento" in summary
