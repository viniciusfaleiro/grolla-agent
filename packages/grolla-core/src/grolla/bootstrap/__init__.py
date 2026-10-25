"""Composition root: único lugar que liga cada port ao adapter configurado."""

from grolla.bootstrap.container import (
    ApiContainer,
    WorkerContainer,
    build_api,
    build_worker,
    prepare_api,
    prepare_worker,
)
from grolla.bootstrap.settings import (
    ApiSettings,
    ConfigError,
    Settings,
    WorkerSettings,
    load_settings,
)
from grolla.bootstrap.team_config import TeamConfig, load_team_config

__all__ = [
    "ApiContainer",
    "ApiSettings",
    "ConfigError",
    "Settings",
    "TeamConfig",
    "WorkerContainer",
    "WorkerSettings",
    "build_api",
    "build_worker",
    "load_settings",
    "load_team_config",
    "prepare_api",
    "prepare_worker",
]
