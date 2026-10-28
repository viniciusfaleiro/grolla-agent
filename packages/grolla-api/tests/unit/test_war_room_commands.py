"""O passo a passo de comandos do "Agora": onde rodar, Copiar, "Apliquei" e a timeline."""

from dataclasses import replace
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from grolla.domain.analysis import Action, AnalysisStatus, Command, Verification
from grolla.domain.incidents import EventType, IncidentEvent, IncidentState
from grolla.testing.builders import T0, make_analysis, make_incident
from grolla.testing.clock import FakeClock
from grolla.testing.incidents import InMemoryIncidentRepository
from grolla_api.app import Services, create_app
from grolla_api.war_room import AUTHOR_COOKIE, StepCommand, applied_label, situation_of

NOW = T0 + timedelta(minutes=20)
STEP = "Restaurar as réplicas do antifraude-api"
REDEPLOY = "az pipelines run --name pedidos-api-cd --branch main"
SCALE = "kubectl scale deploy/antifraude-api --replicas=2 -n loja"
DELETE = "kubectl delete pod antifraude-api-7d4f -n loja"
PIPELINE = "Azure DevOps, pipeline pedidos-api-cd"
CLUSTER = "cluster loja-prod, namespace loja"

TWO_COMMANDS = Action(
    STEP,
    "Deployment antifraude-api",
    "O ConnectError some",
    None,
    (Command(REDEPLOY, PIPELINE), Command(SCALE, CLUSTER)),
)


def verification(*actions: Action) -> Verification:
    return Verification(
        incident_id=uuid4(),
        status=AnalysisStatus.COMPLETED,
        requested_at=T0,
        started_at=T0,
        finished_at=T0 + timedelta(minutes=1),
        verdicts=(),
        root_cause="Deployment antifraude-api com 0 réplicas desde 15:16.",
        actions=actions,
        missing="",
        evaluations=(),
        partial_reason=None,
    )


@pytest.fixture
def repository() -> InMemoryIncidentRepository:
    return InMemoryIncidentRepository()


@pytest.fixture
def app(services: Services, repository: InMemoryIncidentRepository) -> TestClient:
    return TestClient(
        create_app(replace(services, repository=repository, clock=FakeClock(NOW))),
        follow_redirects=False,
    )


@pytest.fixture
def ana(app: TestClient) -> TestClient:
    app.cookies.set(AUTHOR_COOKIE, "Ana")
    return app


def with_plan(repository: InMemoryIncidentRepository, *actions: Action) -> UUID:
    incident = make_incident(state=IncidentState.HANDLING)
    repository.incidents = {incident.id: incident}
    repository.verifications = {
        incident.id: replace(verification(*actions), incident_id=incident.id)
    }
    return incident.id


def first_step(page: str) -> str:
    return (
        page.split('<div class="next-move')[1]
        .split('class="more-steps"', maxsplit=1)[0]
        .split("Esperado:", maxsplit=1)[0]
    )


def applied(incident_id: UUID, command: str, minute: int, author: str = "Ana") -> IncidentEvent:
    data = {"text": STEP, "command": command, "run_in": CLUSTER}
    at = T0 + timedelta(minutes=minute)
    return IncidentEvent(incident_id, at, EventType.ACTION_RECORDED, data, author=author)


# ---- montagem dos passos


def test_commands_of_the_action_keep_the_order_and_where_to_run() -> None:
    s = situation_of(None, verification(TWO_COMMANDS))

    assert s.steps[0].commands == (
        StepCommand(REDEPLOY, PIPELINE, False),
        StepCommand(SCALE, CLUSTER, False),
    )


def test_old_verification_uses_the_quoted_command_without_where_to_run() -> None:
    s = situation_of(None, verification(Action(f"Restaurar: ` {SCALE} `", "", "", None)))

    assert s.steps[0].commands == (StepCommand(SCALE, None, False),)


def test_destructive_is_per_command_and_flags_the_step() -> None:
    action = replace(TWO_COMMANDS, commands=(Command(SCALE, CLUSTER), Command(DELETE, CLUSTER)))

    step = situation_of(None, verification(action)).steps[0]

    assert [c.destructive for c in step.commands] == [False, True]
    assert step.destructive


# ---- a página


def test_now_card_shows_back_to_normal_and_each_command_in_order(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_plan(repository, TWO_COMMANDS)

    first = first_step(ana.get(f"/incidents/{incident_id}").text)

    assert "Para voltar ao normal" in first
    assert "Comece por aqui" not in first
    assert first.count('class="step-command"') == 2
    assert first.index(PIPELINE) < first.index(REDEPLOY) < first.index(CLUSTER)
    assert first.index(CLUSTER) < first.index(f'data-copy="{SCALE}"')
    assert f"Onde rodar: <strong>{PIPELINE}</strong>" in first
    assert f"Onde rodar: <strong>{CLUSTER}</strong>" in first
    assert first.count("<span>Copiar</span>") == 2
    assert first.count("Apliquei") == 2
    assert "data-confirm" not in first


def test_later_steps_also_show_their_commands(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    later = replace(TWO_COMMANDS, action="Trocar o pod", commands=(Command(DELETE, CLUSTER),))
    incident_id = with_plan(repository, Action("Olhar o painel", "", "", None), later)

    page = ana.get(f"/incidents/{incident_id}").text
    rest = page.split('class="more-steps"')[1].split("</details>")[0]

    assert "Onde rodar" not in first_step(page)
    assert f"Onde rodar: <strong>{CLUSTER}</strong>" in rest
    assert f'data-copy="{DELETE}" data-confirm="' in rest
    assert "destrutivo, confira antes" in rest
    assert "Apliquei" in rest


def test_old_verification_shows_the_command_without_where_to_run(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_plan(repository, Action(f"Restaurar: `{SCALE}`", "", "", None))

    first = first_step(ana.get(f"/incidents/{incident_id}").text)

    assert f'data-copy="{SCALE}"' in first
    assert "Onde rodar" not in first
    assert 'name="onde"' not in first


def test_applied_posts_the_step_the_command_and_where_to_run(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_plan(repository, TWO_COMMANDS)

    first = first_step(ana.get(f"/incidents/{incident_id}").text)

    assert f'hx-post="/incidents/{incident_id}/actions/record"' in first
    assert f'name="texto" value="{STEP}"' in first
    assert f'name="comando" value="{SCALE}"' in first
    assert f'name="onde" value="{CLUSTER}"' in first


def test_applied_needs_a_name(app: TestClient, repository: InMemoryIncidentRepository) -> None:
    incident_id = with_plan(repository, TWO_COMMANDS)

    page = app.get(f"/incidents/{incident_id}").text

    assert f'data-copy="{SCALE}"' in page
    assert "Apliquei" not in page


def test_applied_is_recorded_and_the_command_shows_who_applied_it(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_plan(repository, TWO_COMMANDS)
    form = {"texto": STEP, "comando": SCALE, "onde": CLUSTER}

    fragment = ana.post(f"/incidents/{incident_id}/actions/record", data=form).text

    recorded = [e for e in repository.events if e.type is EventType.ACTION_RECORDED]
    assert recorded[-1].data == {"text": STEP, "command": SCALE, "run_in": CLUSTER}
    assert recorded[-1].author == "Ana"
    assert "aplicado por Ana às" in fragment
    assert fragment.count("Apliquei") == 1  # o outro comando do passo segue com o botão


def test_applied_command_shows_the_last_author_and_time(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_plan(repository, TWO_COMMANDS)
    repository.events = [
        applied(incident_id, SCALE, 10, "Pedro"),
        applied(incident_id, SCALE, 11, "Ana"),
    ]

    first = first_step(ana.get(f"/incidents/{incident_id}").text)
    before, scale = first.split(f'data-copy="{SCALE}"')

    assert "aplicado por Ana às" in scale
    assert f">{(T0 + timedelta(minutes=11)).strftime('%H:%M')}</time>" in scale
    assert "Pedro" not in scale
    assert "Apliquei" not in scale
    assert "Apliquei" in before


def test_timeline_says_what_was_applied_and_where(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident_id = with_plan(repository, TWO_COMMANDS)
    repository.events = [applied(incident_id, SCALE, 10)]

    timeline = ana.get(f"/incidents/{incident_id}/timeline").text

    assert f"Aplicou: {STEP} — {SCALE} ({CLUSTER})" in timeline
    assert "por Ana" in timeline
    assert 'class="event-detail"' not in timeline


def test_applied_label_without_where_to_run() -> None:
    assert applied_label({"text": STEP, "command": SCALE, "run_in": ""}) == (
        f"Aplicou: {STEP} — {SCALE}"
    )


def test_analysis_plan_shows_the_command_but_no_applied_button(
    ana: TestClient, repository: InMemoryIncidentRepository
) -> None:
    incident = make_incident(state=IncidentState.HANDLING)
    analysis = replace(make_analysis(incident.id), plan=("Conferir `kubectl get deploy -n loja`",))
    repository.incidents = {incident.id: incident}
    repository.analyses = {incident.id: analysis}

    step = first_step(ana.get(f"/incidents/{incident.id}").text)

    assert "O Grolla investigaria primeiro" in step
    assert "kubectl get deploy -n loja" in step
    assert "Apliquei" not in step  # o plano da análise é o que investigar, não o que rodar
