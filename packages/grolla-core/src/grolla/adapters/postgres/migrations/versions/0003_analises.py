"""Fila e resultado das análises iniciais, e uso do modelo de linguagem.

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- Uma análise inicial por incidente: a tabela é a fila e o resultado.
        CREATE TABLE analyses (
            incident_id uuid PRIMARY KEY REFERENCES incidents (id),
            status text NOT NULL,
            requested_at timestamptz NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            summary text NOT NULL DEFAULT '',
            evaluations jsonb NOT NULL DEFAULT '[]',
            hypotheses jsonb NOT NULL DEFAULT '[]',
            plan jsonb NOT NULL DEFAULT '[]',
            partial_reason text
        );
        CREATE INDEX analyses_pending ON analyses (requested_at) WHERE status = 'pendente';
        CREATE INDEX analyses_running ON analyses (started_at) WHERE status = 'em_andamento';

        CREATE TABLE llm_usage (
            id bigserial PRIMARY KEY,
            incident_id uuid REFERENCES incidents (id),
            stage text NOT NULL,
            model text NOT NULL,
            input_tokens integer NOT NULL,
            output_tokens integer NOT NULL,
            cache_read_tokens integer NOT NULL,
            cache_creation_tokens integer NOT NULL,
            at timestamptz NOT NULL
        );
        CREATE INDEX llm_usage_incident ON llm_usage (incident_id);
        CREATE INDEX llm_usage_at ON llm_usage (at);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
