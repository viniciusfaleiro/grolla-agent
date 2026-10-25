from pathlib import Path
from uuid import UUID

import pytest
from grolla.bootstrap.settings import ConfigError
from grolla.bootstrap.team_config import load_team_config
from grolla.domain.incidents import Severity

INCIDENT = UUID("00000000-0000-0000-0000-000000000001")
EXAMPLE = """
team: credenciadora
page_base_url: https://grolla.interno/
alert_filter: { team: credenciadora }
service_labels: [app]
grouping_window_minutes: 10
default_severity: P2
severity_rules:
  - match: { severity: critical }
    severity: P1
"""


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "grolla.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_reads_the_team_config(tmp_path: Path) -> None:
    config = load_team_config(write(tmp_path, EXAMPLE))

    policy = config.intake_policy()

    assert config.service_labels == ["app"]
    assert policy.label_filter == {"team": "credenciadora"}
    assert policy.window.total_seconds() == 600
    assert policy.severity.default is Severity.P2
    assert policy.severity.rules[0].match == {"severity": "critical"}
    assert policy.page_url(INCIDENT) == f"https://grolla.interno/incidents/{INCIDENT}"


def test_defaults_cover_the_unknown_labels(tmp_path: Path) -> None:
    config = load_team_config(write(tmp_path, "team: t\npage_base_url: http://x\n"))

    assert config.service_labels == ["service_name", "service.name"]
    assert config.alert_filter == {}
    assert config.default_severity is Severity.P3
    assert config.grouping_window_minutes == 15


def test_invalid_severity_points_to_the_rule(tmp_path: Path) -> None:
    content = EXAMPLE.replace("severity: P1", "severity: P7")

    with pytest.raises(ConfigError, match=r"severity_rules\.0\.severity"):
        load_team_config(write(tmp_path, content))


def test_unknown_field_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="webhook_password"):
        load_team_config(write(tmp_path, EXAMPLE + "webhook_password: segredo\n"))


def test_missing_file_names_the_variable(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="GROLLA_CONFIG_FILE"):
        load_team_config(tmp_path / "nao-existe.yaml")


def test_broken_yaml_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="YAML inválido"):
        load_team_config(write(tmp_path, "team: [\n"))
