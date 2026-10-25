import pytest
from grolla.bootstrap.settings import ConfigError, load_settings


def test_reads_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROLLA_DATABASE_URL", "postgresql://grolla@db/grolla")
    monkeypatch.setenv("GROLLA_LOG_LEVEL", "DEBUG")

    settings = load_settings()

    assert settings.database_url == "postgresql://grolla@db/grolla"
    assert settings.log_level == "DEBUG"


def test_missing_database_url_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROLLA_DATABASE_URL", raising=False)

    with pytest.raises(ConfigError, match="GROLLA_DATABASE_URL"):
        load_settings()


def test_invalid_log_level_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROLLA_DATABASE_URL", "postgresql://grolla@db/grolla")
    monkeypatch.setenv("GROLLA_LOG_LEVEL", "VERBOSE")

    with pytest.raises(ConfigError, match="GROLLA_LOG_LEVEL"):
        load_settings()
