"""Transcrições de call: o texto recebido e a fila de extração.

Revision ID: 0013
Revises: 0012
"""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE transcripts (
            id bigserial PRIMARY KEY,
            incident_id uuid NOT NULL REFERENCES incidents (id),
            author text NOT NULL,
            text text NOT NULL,
            received_at timestamptz NOT NULL,
            queued_at timestamptz NOT NULL,  -- posição na fila: o recebimento ou o reprocessar
            status text NOT NULL DEFAULT 'pendente',  -- pendente, em_andamento, concluida, falhou
            started_at timestamptz,
            finished_at timestamptz,
            reason text
        );
        CREATE INDEX transcripts_incident ON transcripts (incident_id, id);
        CREATE INDEX transcripts_pending ON transcripts (queued_at) WHERE status = 'pendente';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
