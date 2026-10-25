"""Schema da fila de jobs e tabela de heartbeats do worker.

Revision ID: 0001
Revises:
"""

from typing import Any, cast

from alembic import op
from procrastinate.schema import SchemaManager

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # O SQL da fila é do procrastinate; vai direto ao driver para não passar pelo parser de binds.
    driver_connection = cast(Any, op.get_bind().connection.driver_connection)
    driver_connection.execute(SchemaManager.get_schema())
    op.execute(
        "CREATE TABLE heartbeats ("
        " id bigserial PRIMARY KEY,"
        " beat_at timestamptz NOT NULL"
        ")"
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
