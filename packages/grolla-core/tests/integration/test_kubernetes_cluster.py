"""KubernetesClusterInventory contra um k3s de verdade (testcontainers).

O cluster recebe um Deployment do `pedidos-api` que lê o ConfigMap `pedidos-config` por
`envFrom`; depois o ConfigMap é editado e o limite de memória reduzido como o `kubectl` faria
(field managers `kubectl-edit` e `kubectl-set`). O adapter tem de achar as duas mudanças pelo
`managedFields` e pelas revisões do ReplicaSet. A preparação usa o cliente oficial com escrita;
o adapter, só as leituras da lista fechada.
"""

import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import yaml
from contracts.cluster_inventory_contract import ClusterInventoryContract
from grolla.adapters.kubernetes import ClusterTarget, KubernetesClusterInventory
from grolla.application.investigation import ClusterInventory
from kubernetes import client as k8s
from kubernetes import config as k8s_config
from testcontainers.community.k3s import K3SContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

pytestmark = pytest.mark.integration

K3S_IMAGE = "rancher/k3s:v1.31.4-k3s1"
NAMESPACE = "pagamentos"
SERVICE = "pedidos-api"
LABELS = {"app.kubernetes.io/name": SERVICE}
_LOADER: Any = k8s_config  # o carregador de configuração do cliente não tem tipos completos


class K3s(K3SContainer):
    """O K3SContainer com a espera estruturada: a dele usa uma API depreciada do testcontainers."""

    def _connect(self) -> None:
        """A espera é a estratégia de `waiting_for`, aplicada pelo `start`."""


@pytest.fixture(scope="module")
def k3s_client() -> Iterator[Any]:
    ready = LogMessageWaitStrategy("Node controller sync successful")
    with K3s(K3S_IMAGE).waiting_for(ready) as k3s:
        api_client: Any = _LOADER.new_client_from_config_dict(yaml.safe_load(k3s.config_yaml()))
        prepare(api_client)
        yield api_client


@pytest.fixture(scope="module")
def since() -> datetime:
    return datetime.now(UTC) - timedelta(minutes=10)


def prepare(api_client: Any) -> None:
    core: Any = k8s.CoreV1Api(api_client)
    apps: Any = k8s.AppsV1Api(api_client)
    core.create_namespace({"metadata": {"name": NAMESPACE}})
    wait(lambda: core.list_namespaced_service_account(NAMESPACE).items)  # namespace pronto
    core.create_namespaced_config_map(
        NAMESPACE,
        {"metadata": {"name": "pedidos-config"}, "data": {"DB_POOL_SIZE": "20"}},
        field_manager="kubectl-client-side-apply",
    )
    container = {
        "name": "app",
        "image": "registry.invalid/pedidos-api:1.4.2",
        "envFrom": [{"configMapRef": {"name": "pedidos-config"}}],
        "resources": {"limits": {"memory": "512Mi"}},
    }
    apps.create_namespaced_deployment(
        NAMESPACE,
        {
            "metadata": {"name": SERVICE, "labels": LABELS},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": LABELS},
                "template": {"metadata": {"labels": LABELS}, "spec": {"containers": [container]}},
            },
        },
        field_manager="kubectl-client-side-apply",
    )
    wait(lambda: revisions(apps) >= {"1"})
    time.sleep(1)  # a revisão nova precisa de um horário depois da primeira
    core.patch_namespaced_config_map(
        "pedidos-config", NAMESPACE, {"data": {"DB_POOL_SIZE": "2"}}, field_manager="kubectl-edit"
    )
    smaller = {"name": "app", "resources": {"limits": {"memory": "128Mi"}}}
    limits = {"spec": {"template": {"spec": {"containers": [smaller]}}}}
    apps.patch_namespaced_deployment(SERVICE, NAMESPACE, limits, field_manager="kubectl-set")
    wait(lambda: revisions(apps) >= {"1", "2"})


def revisions(apps: Any) -> set[str]:
    found: Any = apps.list_namespaced_replica_set(NAMESPACE).items
    return {item.metadata.annotations.get("deployment.kubernetes.io/revision") for item in found}


def wait(ready: Any, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while not ready():
        assert time.monotonic() < deadline, "o k3s não chegou ao estado esperado"
        time.sleep(0.5)


def inventory(api_client: Any, **changes: Any) -> KubernetesClusterInventory:
    target = ClusterTarget(name="k3s", mode="kubeconfig", namespaces=(NAMESPACE,), **changes)
    return KubernetesClusterInventory([target], api_clients={"k3s": api_client})


class TestKubernetesClusterInventoryContract(ClusterInventoryContract):
    @pytest.fixture
    def cluster(self, k3s_client: Any) -> ClusterInventory:
        return inventory(k3s_client)

    @pytest.fixture
    def unavailable_cluster(self, k3s_client: Any) -> ClusterInventory:
        anonymous = k8s.Configuration(host=k3s_client.configuration.host)
        anonymous.ssl_ca_cert = k3s_client.configuration.ssl_ca_cert  # sem credencial: recusa
        return inventory(k8s.ApiClient(anonymous))


async def test_changes_find_the_edit_and_the_new_revision(k3s_client: Any, since: datetime) -> None:
    summary = (await inventory(k3s_client).changes(SERVICE, since)).summary

    assert "ConfigMap pedidos-config alterado por kubectl-edit: DB_POOL_SIZE=2" in summary
    assert "revisão 2 por kubectl-set: limite de memória 512Mi → 128Mi" in summary


async def test_hidden_values_on_a_real_cluster(k3s_client: Any, since: datetime) -> None:
    summary = (await inventory(k3s_client, show_values=False).changes(SERVICE, since)).summary

    assert "alterado por kubectl-edit: DB_POOL_SIZE (valor oculto)" in summary
    assert "=2" not in summary


async def test_state_shows_the_deployment(k3s_client: Any, since: datetime) -> None:
    summary = (await inventory(k3s_client).state(SERVICE, since)).summary

    assert "Deployment pedidos-api em pagamentos:" in summary
    assert "limites memória 128Mi" in summary or "nenhum pod" in summary
