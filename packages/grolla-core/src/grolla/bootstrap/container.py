from dataclasses import dataclass

from grolla.adapters.postgres import PostgresDatabase, migrate
from grolla.adapters.system_clock import SystemClock
from grolla.application.clock import Clock
from grolla.bootstrap.settings import Settings


@dataclass(frozen=True)
class Container:
    """Dependências montadas para um processo (api ou worker)."""

    settings: Settings
    clock: Clock
    database: PostgresDatabase


def prepare(settings: Settings) -> Container:
    """Aplica as migrações pendentes e monta as dependências do processo."""
    migrate(settings.database_url)
    return Container(
        settings=settings,
        clock=SystemClock(),
        database=PostgresDatabase(settings.database_url),
    )
