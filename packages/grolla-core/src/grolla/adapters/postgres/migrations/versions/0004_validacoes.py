"""Validação das hipóteses até a causa raiz: segunda fase da investigação.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- Uma validação por incidente, pedida quando a análise inicial termina com hipóteses.
        CREATE TABLE verifications (
            incident_id uuid PRIMARY KEY REFERENCES incidents (id),
            status text NOT NULL,
            requested_at timestamptz NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            verdicts jsonb NOT NULL DEFAULT '[]',
            root_cause text,
            actions jsonb NOT NULL DEFAULT '[]',
            missing text NOT NULL DEFAULT '',
            evaluations jsonb NOT NULL DEFAULT '[]',
            partial_reason text
        );
        CREATE INDEX verifications_pending ON verifications (requested_at)
            WHERE status = 'pendente';
        CREATE INDEX verifications_running ON verifications (started_at)
            WHERE status = 'em_andamento';
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
