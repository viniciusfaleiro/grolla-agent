"""Configuração do time da instância, lida do YAML apontado por GROLLA_CONFIG_FILE."""

from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grolla.application.analysis import AnalysisPolicy
from grolla.application.incidents import IntakePolicy
from grolla.bootstrap.settings import ConfigError
from grolla.domain.incidents import Severity, SeverityPolicy, SeverityRule


class SeverityRuleConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    match: dict[str, str] = Field(min_length=1)
    severity: Severity


class _Section(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class InvestigationConfig(_Section):
    timeout_seconds: int = Field(default=300, gt=0)
    max_tokens: int = Field(default=300_000, gt=0)
    lookback_minutes: int = Field(default=60, gt=0)
    max_parallel: int = Field(default=3, gt=0)


class StageConfig(_Section):
    """Modelo e parâmetros de uma etapa que chama o modelo de linguagem; nenhum ID no código."""

    model: str = Field(min_length=1)
    max_tokens: int = Field(default=16_000, gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class LlmConfig(_Section):
    stages: dict[Literal["investigation"], StageConfig]


class TelemetryConfig(_Section):
    mcp_url: str
    base_url: str  # links "ver na origem" nos cards e nas avaliações


class RepositoryConfig(_Section):
    project: str
    repo: str


class ChangeHistoryConfig(_Section):
    base_url: str  # ex.: https://dev.azure.com/<organização>
    projects: list[str] = Field(min_length=1)  # onde procurar o repositório pelo nome do serviço
    service_repo_map: dict[str, RepositoryConfig] = Field(
        default_factory=dict[str, RepositoryConfig]
    )  # só as exceções ao nome do serviço


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
    # Seções da investigação: o worker exige llm, signoz e azure_devops; a api não usa.
    investigation: InvestigationConfig = Field(default_factory=InvestigationConfig)
    llm: LlmConfig | None = None
    signoz: TelemetryConfig | None = None
    azure_devops: ChangeHistoryConfig | None = None

    def page_url(self, incident_id: UUID) -> str:
        return f"{self.page_base_url.rstrip('/')}/incidents/{incident_id}"

    def analysis_policy(self) -> AnalysisPolicy:
        return AnalysisPolicy(
            timeout=timedelta(seconds=self.investigation.timeout_seconds),
            max_tokens=self.investigation.max_tokens,
            lookback=timedelta(minutes=self.investigation.lookback_minutes),
            page_url=self.page_url,
            telemetry_url=self.signoz.base_url if self.signoz else None,
        )

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
