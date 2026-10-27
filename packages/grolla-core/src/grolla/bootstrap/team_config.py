"""Configuração do time da instância, lida do YAML apontado por GROLLA_CONFIG_FILE."""

from datetime import timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from grolla.application.analysis import AnalysisPolicy
from grolla.application.follow_up import FollowUpLimits, ImpactPolicy
from grolla.application.incidents import IntakePolicy
from grolla.application.memory import MemoryPolicy, StudyPolicy
from grolla.application.memory_retrieval import RagPolicy
from grolla.application.post_mortem import PostMortemPolicy
from grolla.application.triage import TriagePolicy
from grolla.bootstrap.settings import ConfigError
from grolla.domain.follow_up import FollowUpPolicy
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
    verification_timeout_seconds: int = Field(default=300, gt=0)
    verification_max_tokens: int = Field(default=150_000, gt=0)
    chat_timeout_seconds: int = Field(default=120, gt=0)
    chat_max_tokens: int = Field(default=60_000, gt=0)  # por resposta do agente
    chat_max_tokens_per_incident: int = Field(default=400_000, gt=0)


class StageConfig(_Section):
    """Modelo e parâmetros de uma etapa que chama o modelo de linguagem; nenhum ID no código."""

    model: str = Field(min_length=1)
    max_tokens: int = Field(default=16_000, gt=0)
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None


class LlmConfig(_Section):
    stages: dict[
        Literal[
            "investigation",
            "verification",
            "warroom_chat",
            "learning",
            "study",
            "follow_up",
            "post_mortem",
            "triage",
        ],
        StageConfig,
    ]


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


class CloudConfig(_Section):
    regions: list[str] = Field(min_length=1)
    # Opcional: serviço -> ARNs ou identificadores, para recursos sem a tag `service=<nome>`.
    resource_map: dict[str, list[str]] = Field(default_factory=dict[str, list[str]])


class StudyConfig(_Section):
    """Estudo do ambiente: inicial na primeira subida e diário no `cron`."""

    cron: str = "0 6 * * *"
    run_max_tokens: int = Field(default=400_000, gt=0)
    service_max_tokens: int = Field(default=100_000, gt=0)
    lookback_days: int = Field(default=7, gt=0)


class RagConfig(_Section):
    """Busca híbrida com embeddings locais; o modelo precisa estar embutido na imagem."""

    size: Literal["small", "large"] = "small"  # small: rápido e leve; large: mais preciso
    chunk_chars: int = Field(default=1200, ge=200, le=8000)
    max_doc_files: int = Field(default=30, gt=0)
    max_doc_bytes: int = Field(default=300_000, gt=0)


class MemoryConfig(_Section):
    learning_max_tokens: int = Field(default=60_000, gt=0)
    study: StudyConfig | None = None  # sem a seção, nenhum estudo do ambiente roda
    rag: RagConfig | None = None  # sem a seção, só a busca textual


class FollowUpConfig(_Section):
    """Acompanhamento de P0/P1: ciclos periódicos, card só quando muda e propostas."""

    severities: list[Severity] = Field(default_factory=lambda: [Severity.P0, Severity.P1])
    cycle_minutes: int = Field(default=5, gt=0)
    quiet_minutes: int = Field(default=15, gt=0)
    stable_minutes: int = Field(default=15, gt=0)
    max_cycles: int = Field(default=24, gt=0)
    cycle_max_tokens: int = Field(default=40_000, gt=0)
    cycle_timeout_seconds: int = Field(default=180, gt=0)
    max_tokens_per_incident: int = Field(default=600_000, gt=0)


class ServiceImpactConfig(_Section):
    per_minute: float = Field(gt=0)  # valor em risco por minuto com 100% de erro


class ImpactConfig(_Section):
    currency: str = "BRL"
    services: dict[str, ServiceImpactConfig] = Field(default_factory=dict[str, ServiceImpactConfig])


class PostMortemConfig(_Section):
    max_tokens: int = Field(default=80_000, gt=0)
    timeout_seconds: int = Field(default=180, gt=0)


class NoiseConfig(_Section):
    """Triagem do incidente novo: sugestão de junção e severidade sugerida."""

    window_minutes: int = Field(default=30, gt=0)


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
    cloud: CloudConfig | None = None  # sem a seção, as ferramentas de nuvem dizem que não há
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    follow_up: FollowUpConfig | None = None  # sem a seção, nenhum ciclo de acompanhamento
    impact: ImpactConfig = Field(default_factory=ImpactConfig)
    post_mortem: PostMortemConfig = Field(default_factory=PostMortemConfig)
    noise: NoiseConfig | None = None  # sem a seção, nenhuma triagem

    def page_url(self, incident_id: UUID) -> str:
        return f"{self.page_base_url.rstrip('/')}/incidents/{incident_id}"

    def analysis_policy(self) -> AnalysisPolicy:
        return AnalysisPolicy(
            timeout=timedelta(seconds=self.investigation.timeout_seconds),
            max_tokens=self.investigation.max_tokens,
            lookback=timedelta(minutes=self.investigation.lookback_minutes),
            page_url=self.page_url,
            telemetry_url=self.signoz.base_url if self.signoz else None,
            verification_timeout=timedelta(seconds=self.investigation.verification_timeout_seconds),
            verification_max_tokens=self.investigation.verification_max_tokens,
            chat_timeout=timedelta(seconds=self.investigation.chat_timeout_seconds),
            chat_max_tokens=self.investigation.chat_max_tokens,
            chat_max_tokens_per_incident=self.investigation.chat_max_tokens_per_incident,
        )

    def memory_policy(self) -> MemoryPolicy:
        study, rag = self.memory.study, self.memory.rag
        return MemoryPolicy(
            learning_max_tokens=self.memory.learning_max_tokens,
            rag=None
            if rag is None
            else RagPolicy(
                chunk_chars=rag.chunk_chars,
                max_doc_files=rag.max_doc_files,
                max_doc_bytes=rag.max_doc_bytes,
            ),
            study=None
            if study is None
            else StudyPolicy(
                alert_filter=self.alert_filter,
                run_max_tokens=study.run_max_tokens,
                service_max_tokens=study.service_max_tokens,
                lookback=timedelta(days=study.lookback_days),
            ),
        )

    def follow_up_policy(self) -> FollowUpPolicy | None:
        if self.follow_up is None:
            return None
        return FollowUpPolicy(
            severities=frozenset(self.follow_up.severities),
            cycle=timedelta(minutes=self.follow_up.cycle_minutes),
            quiet=timedelta(minutes=self.follow_up.quiet_minutes),
            stable=timedelta(minutes=self.follow_up.stable_minutes),
            max_cycles=self.follow_up.max_cycles,
        )

    def follow_up_limits(self) -> FollowUpLimits:
        section = self.follow_up or FollowUpConfig()
        return FollowUpLimits(
            timeout=timedelta(seconds=section.cycle_timeout_seconds),
            max_tokens=section.cycle_max_tokens,
            max_tokens_per_incident=section.max_tokens_per_incident,
        )

    def triage_policy(self) -> TriagePolicy | None:
        if self.noise is None:
            return None
        return TriagePolicy(window=timedelta(minutes=self.noise.window_minutes))

    def post_mortem_policy(self) -> PostMortemPolicy:
        return PostMortemPolicy(
            max_tokens=self.post_mortem.max_tokens,
            timeout=timedelta(seconds=self.post_mortem.timeout_seconds),
        )

    def impact_policy(self) -> ImpactPolicy:
        return ImpactPolicy(
            currency=self.impact.currency,
            per_minute={s: c.per_minute for s, c in self.impact.services.items()},
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
