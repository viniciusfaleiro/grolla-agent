"""Acompanhamento da crise: ciclos por incidente e o pedido de ciclo antecipado.

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE cycles (
            incident_id uuid NOT NULL REFERENCES incidents (id),
            number integer NOT NULL,
            status text NOT NULL,
            requested_at timestamptz NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            trend text,
            error_rate double precision,
            summary text NOT NULL DEFAULT '',
            changed boolean NOT NULL DEFAULT false,
            evidence jsonb NOT NULL DEFAULT '[]',
            evaluations jsonb NOT NULL DEFAULT '[]',
            partial_reason text,
            posted boolean NOT NULL DEFAULT false,
            PRIMARY KEY (incident_id, number)
        );
        CREATE INDEX cycles_pending ON cycles (requested_at) WHERE status = 'pendente';
        CREATE INDEX cycles_running ON cycles (started_at) WHERE status = 'em_andamento';

        -- Pedido de ciclo antecipado (alerta agrupado ou mensagem no chat).
        CREATE TABLE follow_ups (
            incident_id uuid PRIMARY KEY REFERENCES incidents (id),
            requested_at timestamptz NOT NULL
        );
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
