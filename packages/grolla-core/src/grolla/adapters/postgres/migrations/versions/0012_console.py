"""Console de operação: sessões e mensagens fora de incidente.

Revision ID: 0012
Revises: 0011
"""

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE console_sessions (
            id uuid PRIMARY KEY,
            title text NOT NULL,
            author text NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            incident_id uuid REFERENCES incidents (id)
        );
        CREATE INDEX console_sessions_recent ON console_sessions (updated_at DESC);

        -- Como `chat_messages`: a pergunta leva `reply_status` e é a fila; a resposta aponta a
        -- pergunta em `reply_to` e pode trazer os gráficos pedidos pelo modelo.
        CREATE TABLE console_messages (
            id bigserial PRIMARY KEY,
            session_id uuid NOT NULL REFERENCES console_sessions (id),
            author text NOT NULL,
            text text NOT NULL,
            at timestamptz NOT NULL,
            charts jsonb NOT NULL DEFAULT '[]',
            evaluations jsonb NOT NULL DEFAULT '[]',
            reply_status text,
            reply_to bigint REFERENCES console_messages (id),
            started_at timestamptz
        );
        CREATE INDEX console_messages_session ON console_messages (session_id, id);
        CREATE INDEX console_messages_pending ON console_messages (at)
            WHERE reply_status = 'pendente';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
