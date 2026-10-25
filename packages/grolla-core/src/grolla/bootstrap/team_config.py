"""Configuração do time da instância, lida do YAML apontado por GROLLA_CONFIG_FILE."""

from datetime import timedelta
from pathlib import Path
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grolla.application.incidents import IntakePolicy
from grolla.bootstrap.settings import ConfigError
from grolla.domain.incidents import Severity, SeverityPolicy, SeverityRule


class SeverityRuleConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    match: dict[str, str] = Field(min_length=1)
    severity: Severity


class TeamConfig(BaseModel):
    """Nomes de labels são todos configuráveis: dependem de como o time escreve os alertas."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    team: str
    page_base_url: str
    alert_filter: dict[str, str] = Field(default_factory=dict[str, str])
    service_labels: list[str] = Field(
        default_factory=lambda: ["service_name", "service.name"], min_length=1
    )
    grouping_window_minutes: int = Field(default=15, gt=0)
    default_severity: Severity = Severity.P3
    severity_rules: list[SeverityRuleConfig] = Field(default_factory=list[SeverityRuleConfig])

    def page_url(self, incident_id: UUID) -> str:
        return f"{self.page_base_url.rstrip('/')}/incidents/{incident_id}"

    def intake_policy(self) -> IntakePolicy:
        return IntakePolicy(
            label_filter=self.alert_filter,
            severity=SeverityPolicy(
                rules=tuple(SeverityRule(r.match, r.severity) for r in self.severity_rules),
                default=self.default_severity,
            ),
            window=timedelta(minutes=self.grouping_window_minutes),
            page_url=self.page_url,
        )


def load_team_config(path: Path) -> TeamConfig:
    """Lê e valida o YAML; levanta ConfigError apontando o campo inválido."""
    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"GROLLA_CONFIG_FILE: não foi possível ler {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ConfigError(f"{path}: YAML inválido: {error}") from error
    try:
        return TeamConfig.model_validate(content)
    except ValidationError as error:
        problems = [
            f"{'.'.join(str(part) for part in issue['loc']) or '(raiz)'}: {issue['msg']}"
            for issue in error.errors()
        ]
        raise ConfigError(f"{path}: configuração inválida — " + "; ".join(problems)) from error
