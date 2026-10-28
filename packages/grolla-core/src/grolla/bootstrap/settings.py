"""Configuração lida do ambiente (prefixo GROLLA_) e validada na inicialização.

Cada processo exige só os segredos que usa: a api, as credenciais do webhook de alertas; o
worker, a URL do canal de chat.
"""

from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "GROLLA_"


class ConfigError(Exception):
    """Configuração ausente ou inválida; a mensagem nomeia as variáveis ou os campos."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True)

    database_url: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class ApiSettings(Settings):
    config_file: Path
    alerts_webhook_user: str
    alerts_webhook_password: SecretStr


class WorkerSettings(Settings):
    config_file: Path
    teams_webhook_url: SecretStr
    anthropic_api_key: SecretStr | None = None  # exigida só com llm.provider anthropic
    azure_devops_token: SecretStr
    anthropic_base_url: str | None = None  # só o e2e aponta para o fake


def load_settings[S: Settings](kind: type[S]) -> S:
    try:
        return kind()  # pyright: ignore[reportCallIssue] — campos vêm do ambiente
    except ValidationError as error:
        problems = [
            f"{ENV_PREFIX}{str(issue['loc'][0]).upper()}: {issue['msg']}"
            for issue in error.errors()
        ]
        raise ConfigError("configuração inválida — " + "; ".join(problems)) from error
