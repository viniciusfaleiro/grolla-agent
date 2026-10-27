"""Recusas do agente: o escopo do pedido recusado na resposta do chat e do console.

Revision ID: 0014
Revises: 0013
"""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE chat_messages ADD COLUMN refusal text;  -- fora_do_escopo, pedido_de_execucao
        ALTER TABLE console_messages ADD COLUMN refusal text;
        CREATE INDEX chat_messages_author_at ON chat_messages (author, at);
        CREATE INDEX console_messages_author_at ON console_messages (author, at);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
