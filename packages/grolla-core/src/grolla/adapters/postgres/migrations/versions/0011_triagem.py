"""Menos ruído: a fila de triagem dos incidentes novos.

Revision ID: 0011
Revises: 0010
"""

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE triages (
            incident_id uuid PRIMARY KEY REFERENCES incidents (id),
            status text NOT NULL,
            requested_at timestamptz NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            reason text
        );
        CREATE INDEX triages_pending ON triages (requested_at) WHERE status = 'pendente';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
