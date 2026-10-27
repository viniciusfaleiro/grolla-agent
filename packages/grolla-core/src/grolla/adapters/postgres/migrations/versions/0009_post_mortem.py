"""Post mortem: o documento por incidente, as versões das seções e a fila de rascunhos e
regenerações.

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE post_mortems (
            incident_id uuid PRIMARY KEY REFERENCES incidents (id),
            status text NOT NULL,
            sections jsonb NOT NULL,
            actions jsonb NOT NULL DEFAULT '[]',
            status_by text NOT NULL,
            status_at timestamptz,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );

        -- O texto de uma seção antes de cada mudança; só inserção.
        CREATE TABLE post_mortem_versions (
            id bigserial PRIMARY KEY,
            incident_id uuid NOT NULL REFERENCES incidents (id),
            section text NOT NULL,
            at timestamptz NOT NULL,
            author text NOT NULL,
            reason text NOT NULL,
            body text NOT NULL
        );
        CREATE INDEX post_mortem_versions_section
            ON post_mortem_versions (incident_id, section, at DESC);

        -- Pedidos de rascunho (section nula) e de regeneração de uma seção.
        CREATE TABLE post_mortem_jobs (
            id bigserial PRIMARY KEY,
            incident_id uuid NOT NULL REFERENCES incidents (id),
            section text,
            instruction text,
            author text,
            status text NOT NULL,
            requested_at timestamptz NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            reason text
        );
        CREATE INDEX post_mortem_jobs_pending ON post_mortem_jobs (requested_at)
            WHERE status = 'pendente';
        -- Um rascunho por incidente: pedir de novo não duplica.
        CREATE UNIQUE INDEX post_mortem_jobs_draft ON post_mortem_jobs (incident_id)
            WHERE section IS NULL;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
