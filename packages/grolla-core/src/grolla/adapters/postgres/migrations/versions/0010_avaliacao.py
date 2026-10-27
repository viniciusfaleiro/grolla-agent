"""Métricas do agente: a avaliação estruturada no post mortem.

Revision ID: 0010
Revises: 0009
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE post_mortems ADD COLUMN evaluation jsonb")


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
