import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.configurar_instancia import FAKE_TEAMS_URL, DeployConfigError, build, main

EXAMPLE = Path("deploy/implantacao.exemplo.yaml")


def example() -> dict[str, Any]:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def env_of(content: str) -> dict[str, str]:
    lines = [line for line in content.splitlines() if line and not line.startswith("#")]
    return {k: v.strip("'") for k, v in (line.split("=", 1) for line in lines)}


def test_the_example_is_a_valid_instance(tmp_path: Path) -> None:
    assert main(["", str(EXAMPLE), str(tmp_path)]) == 0

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        ".env",
        "grolla.env",
        "grolla.yaml",
        "signoz-mcp.env",
    ]
    modes = {p.name: stat.S_IMODE(p.stat().st_mode) for p in tmp_path.iterdir()}
    assert modes.pop("grolla.yaml") == 0o644  # o agente lê com outro usuário
    assert set(modes.values()) == {0o600}
    compose = env_of((tmp_path / ".env").read_text(encoding="utf-8"))
    assert compose == {
        "COMPOSE_PROFILES": "signoz-mcp",
        "GROLLA_DB_PASSWORD": "troque-esta-senha",
        "GROLLA_API_PORT": "8000",
    }


def test_secrets_and_teams_go_to_the_agent_environment() -> None:
    raw = example()
    raw["teams"] = {"webhook_url": "fake"}
    raw["kubeconfig"] = "~/kube.yaml"

    instance = build(raw)

    env = env_of(instance.files["grolla.env"])
    assert env["GROLLA_TEAMS_WEBHOOK_URL"] == FAKE_TEAMS_URL
    assert env["GROLLA_ANTHROPIC_API_KEY"] == "sk-ant-..."
    assert env["KUBECONFIG"] == "/etc/grolla/kubeconfig.yaml"
    assert "teams-fake" in env_of(instance.files[".env"])["COMPOSE_PROFILES"]
    assert instance.summary["kubeconfig"] == "~/kube.yaml"


def test_https_writes_the_caddyfile_and_the_page_url() -> None:
    raw = example()
    del raw["grolla"]["page_base_url"]
    raw["https"] = {
        "host": "grolla.exemplo.com",
        "senha": "x",
        "cloudfront": {"origem": "ec2-1-2-3-4.compute-1.amazonaws.com", "segredo": "abc"},
    }

    instance = build(raw, web_hash="$2a$14$hash")

    caddy = instance.files["Caddyfile"]
    assert "grolla.exemplo.com {" in caddy
    assert "grolla $2a$14$hash" in caddy
    assert "http://ec2-1-2-3-4.compute-1.amazonaws.com {" in caddy
    assert "header X-Grolla-Origin abc" in caddy
    assert instance.summary["url"] == "https://grolla.exemplo.com"
    assert "page_base_url: https://grolla.exemplo.com" in instance.files["grolla.yaml"]


def test_lists_every_problem_at_once() -> None:
    raw = example()
    raw["instancia"] = {}
    del raw["teams"]
    raw["segredos"] = {"GROLLA_ALERTS_WEBHOOK_USER": "signoz"}
    raw["https"] = {"host": "x"}

    with pytest.raises(DeployConfigError) as error:
        build(raw)

    message = str(error.value)
    for expected in (
        "instancia.senha_banco",
        "teams.webhook_url",
        "segredos.GROLLA_ALERTS_WEBHOOK_PASSWORD",
        "segredos.GROLLA_AZURE_DEVOPS_TOKEN",
        "segredos.GROLLA_ANTHROPIC_API_KEY",
        "https: precisa de host e senha",
    ):
        assert expected in message


def test_bedrock_does_not_need_the_anthropic_key() -> None:
    raw = example()
    del raw["segredos"]["GROLLA_ANTHROPIC_API_KEY"]
    raw["grolla"]["llm"]["provider"] = "bedrock"

    assert "GROLLA_ANTHROPIC_API_KEY" not in env_of(build(raw).files["grolla.env"])


def test_an_invalid_team_config_fails_like_the_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = example()
    raw["grolla"]["default_severity"] = "P9"
    source = tmp_path / "implantacao.yaml"
    source.write_text(yaml.safe_dump(raw), encoding="utf-8")
    out = tmp_path / "saida"
    out.mkdir()

    assert main(["", str(source), str(out)]) == 1
    assert "default_severity" in capsys.readouterr().err


def test_a_quote_in_a_secret_is_refused() -> None:
    raw = example()
    raw["segredos"]["GROLLA_AZURE_DEVOPS_TOKEN"] = "it's"  # noqa: S105 — valor de teste

    with pytest.raises(DeployConfigError, match="GROLLA_AZURE_DEVOPS_TOKEN"):
        build(raw)
