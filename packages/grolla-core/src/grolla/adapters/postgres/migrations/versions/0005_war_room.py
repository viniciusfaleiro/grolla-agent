"""War room: IC e horários do incidente, e o chat com o agente.

Revision ID: 0005
Revises: 0004
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE incidents
            ADD COLUMN commander text,
            ADD COLUMN handling_at timestamptz,
            ADD COLUMN mitigated_at timestamptz,
            ADD COLUMN resolved_at timestamptz;

        -- Mensagens do chat do war room. A pergunta de uma pessoa leva `reply_status` e é a
        -- fila; a resposta do agente aponta a pergunta em `reply_to`.
        CREATE TABLE chat_messages (
            id bigserial PRIMARY KEY,
            incident_id uuid NOT NULL REFERENCES incidents (id),
            author text NOT NULL,
            text text NOT NULL,
            at timestamptz NOT NULL,
            evaluations jsonb NOT NULL DEFAULT '[]',
            reply_status text,
            reply_to bigint REFERENCES chat_messages (id),
            started_at timestamptz
        );
        CREATE INDEX chat_messages_incident ON chat_messages (incident_id, id);
        CREATE INDEX chat_messages_pending ON chat_messages (at) WHERE reply_status = 'pendente';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
