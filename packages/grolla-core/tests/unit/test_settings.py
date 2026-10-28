import pytest
from grolla.bootstrap.settings import (
    ApiSettings,
    ConfigError,
    Settings,
    WorkerSettings,
    load_settings,
)


def test_reads_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROLLA_DATABASE_URL", "postgresql://grolla@db/grolla")
    monkeypatch.setenv("GROLLA_LOG_LEVEL", "DEBUG")

    settings = load_settings(Settings)

    assert settings.database_url == "postgresql://grolla@db/grolla"
    assert settings.log_level == "DEBUG"


def test_missing_database_url_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROLLA_DATABASE_URL", raising=False)

    with pytest.raises(ConfigError, match="GROLLA_DATABASE_URL"):
        load_settings(Settings)


def test_invalid_log_level_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROLLA_DATABASE_URL", "postgresql://grolla@db/grolla")
    monkeypatch.setenv("GROLLA_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ConfigError, match="GROLLA_LOG_LEVEL"):
        load_settings(Settings)


def test_api_requires_the_webhook_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROLLA_DATABASE_URL", "postgresql://grolla@db/grolla")
    monkeypatch.setenv("GROLLA_CONFIG_FILE", "/etc/grolla.yaml")
    monkeypatch.setenv("GROLLA_ALERTS_WEBHOOK_USER", "signoz")
    monkeypatch.delenv("GROLLA_ALERTS_WEBHOOK_PASSWORD", raising=False)

    with pytest.raises(ConfigError, match="GROLLA_ALERTS_WEBHOOK_PASSWORD"):
        load_settings(ApiSettings)


def test_worker_requires_its_secrets_but_not_the_api_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROLLA_DATABASE_URL", "postgresql://grolla@db/grolla")
    monkeypatch.setenv("GROLLA_CONFIG_FILE", "/etc/grolla.yaml")
    monkeypatch.setenv("GROLLA_TEAMS_WEBHOOK_URL", "https://chat.example/hook")
    monkeypatch.setenv("GROLLA_ANTHROPIC_API_KEY", "chave")
    monkeypatch.setenv("GROLLA_AZURE_DEVOPS_TOKEN", "token")
    monkeypatch.delenv("GROLLA_ALERTS_WEBHOOK_PASSWORD", raising=False)

    settings = load_settings(WorkerSettings)

    assert settings.teams_webhook_url.get_secret_value() == "https://chat.example/hook"
    assert settings.anthropic_base_url is None


def test_worker_settings_do_not_require_the_language_model_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROLLA_DATABASE_URL", "postgresql://grolla@db/grolla")
    monkeypatch.setenv("GROLLA_CONFIG_FILE", "/etc/grolla.yaml")
    monkeypatch.setenv("GROLLA_TEAMS_WEBHOOK_URL", "https://chat.example/hook")
    monkeypatch.setenv("GROLLA_AZURE_DEVOPS_TOKEN", "token")
    monkeypatch.delenv("GROLLA_ANTHROPIC_API_KEY", raising=False)

    settings = load_settings(
        WorkerSettings
    )  # o container exige a chave só com llm.provider anthropic

    assert settings.anthropic_api_key is None
