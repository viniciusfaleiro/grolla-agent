"""Composition root: único lugar que liga cada port ao adapter configurado."""

from grolla.bootstrap.container import Container, prepare
from grolla.bootstrap.settings import ConfigError, Settings, load_settings

__all__ = ["ConfigError", "Container", "Settings", "load_settings", "prepare"]
