from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from grolla.adapters.postgres.urls import sqlalchemy_url

# Chave do advisory lock que serializa migrações quando api e worker sobem juntos.
MIGRATION_LOCK_KEY = 7_041_955_001

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def migrate(database_url: str) -> None:
    """Aplica as migrações pendentes. Seguro de rodar em paralelo a partir de vários processos."""
    engine = sa.create_engine(sqlalchemy_url(database_url))
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
            )
            config = Config()
            config.set_main_option("script_location", str(MIGRATIONS_DIR))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()
