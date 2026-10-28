"""Comandos sugeridos nas respostas do chat e do console: o passo a passo com onde rodar.

Revision ID: 0015
Revises: 0014
"""

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE chat_messages ADD COLUMN commands jsonb NOT NULL DEFAULT '[]';
        ALTER TABLE console_messages ADD COLUMN commands jsonb NOT NULL DEFAULT '[]';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
