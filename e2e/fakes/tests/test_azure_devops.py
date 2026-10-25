"""API fake do Azure DevOps: repositórios, builds, roteiro e gravação."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from grolla_fakes.app import create_app

BASE = "/azure-devops/credenciadora/Core/_apis"
AUTH = {"Authorization": "Basic OnRva2Vu"}


def test_every_repository_exists_by_default() -> None:
    client = TestClient(create_app())

    response = client.get(f"{BASE}/git/repositories/captura-api", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["name"] == "captura-api"


def test_default_is_one_deploy_twenty_minutes_ago() -> None:
    client = TestClient(create_app())
    since = (datetime.now(UTC) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")

    response = client.get(
        f"{BASE}/build/builds",
        params={"repositoryId": "authorizer-api", "minTime": since},
        headers=AUTH,
    )

    builds = response.json()["value"]
    assert len(builds) == 1
    started = datetime.fromisoformat(builds[0]["startTime"])
    assert timedelta(minutes=19) < datetime.now(UTC) - started < timedelta(minutes=21)
    assert builds[0]["definition"]["name"] == "authorizer-api-deploy"


def test_deploy_older_than_min_time_is_left_out() -> None:
    client = TestClient(create_app())
    since = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    response = client.get(
        f"{BASE}/build/builds", params={"repositoryId": "x", "minTime": since}, headers=AUTH
    )

    assert response.json() == {"count": 0, "value": []}


def test_script_limits_repositories_fixes_builds_and_reset_clears() -> None:
    client = TestClient(create_app())
    client.post(
        "/_control/azure-devops",
        json={"repositories": ["authorizer-api"], "builds": [{"buildNumber": "7"}]},
    )

    assert client.get(f"{BASE}/git/repositories/outro", headers=AUTH).status_code == 404
    assert client.get(f"{BASE}/build/builds", headers=AUTH).json()["value"] == [
        {"buildNumber": "7"}
    ]

    client.post("/_control/reset")
    assert client.get(f"{BASE}/git/repositories/outro", headers=AUTH).status_code == 200


def test_scripted_failure_and_missing_pat() -> None:
    client = TestClient(create_app())

    assert client.get(f"{BASE}/build/builds").status_code == 401
    client.post("/_control/azure-devops", json={"status": 503})
    assert client.get(f"{BASE}/build/builds", headers=AUTH).status_code == 503


def test_requests_are_recorded_with_the_query() -> None:
    client = TestClient(create_app())

    client.get(f"{BASE}/build/builds", params={"repositoryId": "r"}, headers=AUTH)

    assert client.get("/_control/requests").json() == [
        {
            "method": "GET",
            "path": f"{BASE}/build/builds",
            "body": {"repositoryId": "r"},
            "status": 200,
        }
    ]
