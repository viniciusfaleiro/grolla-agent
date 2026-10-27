"""API do Kubernetes fake: o cluster-api nos dois clusters, o ConfigMap editado e a gravação."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from grolla_fakes.app import create_app

AUTH = {"Authorization": "Bearer token-do-e2e"}
NAO_PCI = "/k8s/nao-pci"
EDIT = {
    "configmap_edit": {
        "minutes_ago": 5,
        "manager": "kubectl-edit",
        "key": "DB_POOL_SIZE",
        "values": {"nao-pci": "2", "pci": "7"},
    }
}


def get(client: TestClient, path: str, **params: str) -> Any:
    response = client.get(path, params=params, headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_service_runs_in_both_clusters() -> None:
    client = TestClient(create_app())
    selector = {"labelSelector": "app.kubernetes.io/name=cluster-api"}

    nao_pci = get(client, f"{NAO_PCI}/apis/apps/v1/namespaces/pagamentos/deployments", **selector)
    pci = get(client, "/k8s/pci/apis/apps/v1/namespaces/cartoes/deployments", **selector)

    assert [item["metadata"]["name"] for item in nao_pci["items"]] == ["cluster-api"]
    assert [item["metadata"]["namespace"] for item in pci["items"]] == ["cartoes"]
    template = nao_pci["items"][0]["spec"]["template"]["spec"]["containers"][0]
    assert template["envFrom"] == [{"configMapRef": {"name": "cluster-config"}}]


def test_other_selector_namespace_or_resource_is_empty() -> None:
    client = TestClient(create_app())
    base = f"{NAO_PCI}/apis/apps/v1/namespaces"

    other = get(client, f"{base}/pagamentos/deployments", labelSelector="app=outro")
    wrong = get(client, f"{base}/cartoes/deployments")
    sets = get(client, f"{base}/pagamentos/statefulsets")
    warnings = get(
        client, f"{NAO_PCI}/api/v1/namespaces/pagamentos/events", fieldSelector="type=Warning"
    )
    hpas = get(
        client, f"{NAO_PCI}/apis/autoscaling/v2/namespaces/pagamentos/horizontalpodautoscalers"
    )

    assert (
        other["items"]
        == wrong["items"]
        == sets["items"]
        == warnings["items"]
        == hpas["items"]
        == []
    )


def test_pods_and_replica_set_of_the_deployment() -> None:
    client = TestClient(create_app())
    selector = {"labelSelector": "app.kubernetes.io/name=cluster-api"}

    pods = get(client, f"{NAO_PCI}/api/v1/namespaces/pagamentos/pods", **selector)
    sets = get(client, f"{NAO_PCI}/apis/apps/v1/namespaces/pagamentos/replicasets", **selector)

    assert len(pods["items"]) == 2
    assert pods["items"][0]["status"]["phase"] == "Running"
    revision = sets["items"][0]["metadata"]["annotations"]["deployment.kubernetes.io/revision"]
    assert revision == "1"
    assert sets["items"][0]["metadata"]["ownerReferences"][0]["uid"] == "nao-pci-deployment"


def test_config_map_by_default_is_old_and_from_helm() -> None:
    client = TestClient(create_app())

    config_map = get(client, f"{NAO_PCI}/api/v1/namespaces/pagamentos/configmaps/cluster-config")

    assert config_map["data"]["DB_POOL_SIZE"] == "20"
    [entry] = config_map["metadata"]["managedFields"]
    assert entry["manager"] == "helm"
    assert datetime.now(UTC) - datetime.fromisoformat(entry["time"]) > timedelta(days=1)


def test_scripted_edit_has_the_manager_the_time_and_the_value_of_each_cluster() -> None:
    client = TestClient(create_app())
    assert client.post("/_control/k8s", json=EDIT).status_code == 204

    nao_pci = get(client, f"{NAO_PCI}/api/v1/namespaces/pagamentos/configmaps/cluster-config")
    pci = get(client, "/k8s/pci/api/v1/namespaces/cartoes/configmaps/cluster-config")

    assert nao_pci["data"]["DB_POOL_SIZE"] == "2"
    assert pci["data"]["DB_POOL_SIZE"] == "7"
    edit = nao_pci["metadata"]["managedFields"][-1]
    assert edit["manager"] == "kubectl-edit"
    assert edit["fieldsV1"] == {"f:data": {"f:DB_POOL_SIZE": {}}}
    age = datetime.now(UTC) - datetime.fromisoformat(edit["time"])
    assert timedelta(minutes=4) < age < timedelta(minutes=6)
    assert "f:DB_POOL_SIZE" not in nao_pci["metadata"]["managedFields"][0]["fieldsV1"]["f:data"]

    client.post("/_control/reset")
    again = get(client, f"{NAO_PCI}/api/v1/namespaces/pagamentos/configmaps/cluster-config")
    assert again["data"]["DB_POOL_SIZE"] == "20"


def test_unknown_object_is_404_and_reads_need_a_token() -> None:
    client = TestClient(create_app())

    missing = client.get(f"{NAO_PCI}/api/v1/namespaces/pagamentos/configmaps/outro", headers=AUTH)
    anonymous = client.get(f"{NAO_PCI}/apis/apps/v1/namespaces/pagamentos/deployments")

    assert missing.status_code == 404
    assert anonymous.status_code == 401


def test_reads_are_recorded_with_the_query() -> None:
    client = TestClient(create_app())

    get(client, f"{NAO_PCI}/api/v1/namespaces/pagamentos/events", fieldSelector="type=Warning")

    [recorded] = client.get("/_control/requests").json()
    assert recorded == {
        "method": "GET",
        "path": f"{NAO_PCI}/api/v1/namespaces/pagamentos/events",
        "body": {"fieldSelector": "type=Warning"},
        "status": 200,
    }
