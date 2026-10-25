"""Configuração lida do ambiente (prefixo GROLLA_) e validada na inicialização."""

from typing import Literal

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "GROLLA_"


class ConfigError(Exception):
    """Configuração ausente ou inválida; a mensagem nomeia as variáveis de ambiente."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, frozen=True)

    database_url: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


def load_settings() -> Settings:
    try:
        return Settings()  # pyright: ignore[reportCallIssue] — campos vêm do ambiente
    except ValidationError as error:
        problems = [
            f"{ENV_PREFIX}{str(issue['loc'][0]).upper()}: {issue['msg']}"
            for issue in error.errors()
        ]
        raise ConfigError("configuração inválida — " + "; ".join(problems)) from error
