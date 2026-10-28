"""A conexão do modelo no bootstrap: API da Anthropic com a chave ou Bedrock pela região do YAML."""

from pathlib import Path

import pytest
from grolla.adapters.anthropic import AnthropicApi, Bedrock
from grolla.bootstrap import WorkerSettings
from grolla.bootstrap.container import _connection  # pyright: ignore[reportPrivateUsage] — só o ponto testado
from grolla.bootstrap.settings import ConfigError
from grolla.bootstrap.team_config import LlmConfig
from pydantic import SecretStr

STAGES = {"investigation": {"model": "claude-sonnet-5-5"}}


def settings(key: str | None = "chave") -> WorkerSettings:
    return WorkerSettings(
        database_url="postgresql://grolla:grolla@localhost:5433/grolla",
        config_file=Path("/etc/grolla/grolla.yaml"),
        teams_webhook_url=SecretStr("http://chat"),
        anthropic_api_key=None if key is None else SecretStr(key),
        azure_devops_token=SecretStr("token"),
        anthropic_base_url="http://fake",
    )


def test_without_provider_uses_the_anthropic_api_with_the_key() -> None:
    llm = LlmConfig.model_validate({"stages": STAGES})

    assert _connection(settings(), llm) == AnthropicApi("chave", "http://fake")


def test_anthropic_without_the_key_names_the_variable() -> None:
    llm = LlmConfig.model_validate({"provider": "anthropic", "stages": STAGES})

    with pytest.raises(ConfigError, match="GROLLA_ANTHROPIC_API_KEY"):
        _connection(settings(key=None), llm)


def test_bedrock_uses_the_region_and_needs_no_key() -> None:
    llm = LlmConfig.model_validate({"provider": "bedrock", "region": "us-east-1", "stages": STAGES})

    assert _connection(settings(key=None), llm) == Bedrock("us-east-1")
