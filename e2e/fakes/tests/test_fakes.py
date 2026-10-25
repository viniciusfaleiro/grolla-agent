from fastapi.testclient import TestClient
from grolla_fakes.app import create_app


def test_scripted_route_answers_and_is_recorded() -> None:
    client = TestClient(create_app())
    client.post(
        "/_control/scenario",
        json={"routes": [{"method": "POST", "path": "/pager", "status": 202, "body": {"ok": 1}}]},
    )

    response = client.post("/pager", json={"title": "queda de aprovação"})

    assert response.status_code == 202
    assert response.json() == {"ok": 1}
    assert client.get("/_control/requests").json() == [
        {"method": "POST", "path": "/pager", "body": {"title": "queda de aprovação"}, "status": 202}
    ]


def test_route_outside_the_scenario_is_404_but_still_recorded() -> None:
    client = TestClient(create_app())

    response = client.get("/unknown")

    assert response.status_code == 404
    assert client.get("/_control/requests").json() == [
        {"method": "GET", "path": "/unknown", "body": None, "status": 404}
    ]


def test_reset_clears_scenario_and_requests() -> None:
    client = TestClient(create_app())
    client.post("/_control/scenario", json={"routes": [{"method": "GET", "path": "/x"}]})
    client.get("/x")

    client.post("/_control/reset")

    assert client.get("/_control/requests").json() == []
    assert client.get("/x").status_code == 404
