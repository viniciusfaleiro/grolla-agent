"""Memória viva: registros da base de conhecimento, versões, aprendizado e estudo do ambiente.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        -- Dossiês, episódios e notas. Nada é apagado: esquecer é `active = false`.
        CREATE TABLE memories (
            id uuid PRIMARY KEY,
            kind text NOT NULL,
            subject text NOT NULL,
            subject_kind text NOT NULL,
            title text NOT NULL,
            body text NOT NULL,
            confidence text NOT NULL,
            source_kind text NOT NULL,
            source_author text NOT NULL,
            source_ref text,
            incident_id uuid,  -- sem FK: o episódio é registro da memória, não do incidente
            active boolean NOT NULL DEFAULT true,
            consolidated boolean NOT NULL DEFAULT false,
            noise boolean NOT NULL DEFAULT false,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL,
            search tsvector GENERATED ALWAYS AS (
                to_tsvector('portuguese', subject || ' ' || title || ' ' || body)
            ) STORED
        );
        CREATE INDEX memories_search ON memories USING gin (search);
        CREATE INDEX memories_subject ON memories (subject, kind);
        -- Um dossiê por assunto e um episódio por (incidente, assunto).
        CREATE UNIQUE INDEX memories_dossier ON memories (subject) WHERE kind = 'dossie';
        CREATE UNIQUE INDEX memories_episode ON memories (incident_id, subject)
            WHERE kind = 'episodio';

        -- Estado de um registro ANTES de cada mudança; só inserção.
        CREATE TABLE memory_versions (
            id bigserial PRIMARY KEY,
            memory_id uuid NOT NULL REFERENCES memories (id),
            at timestamptz NOT NULL,
            author text NOT NULL,
            reason text NOT NULL,
            body text NOT NULL,
            active boolean NOT NULL
        );
        CREATE INDEX memory_versions_memory ON memory_versions (memory_id, at DESC);

        -- Fila do aprendizado ao encerrar um incidente.
        CREATE TABLE learnings (
            incident_id uuid PRIMARY KEY REFERENCES incidents (id),
            status text NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            requested_at timestamptz NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            reason text
        );
        CREATE INDEX learnings_pending ON learnings (requested_at) WHERE status = 'pendente';

        -- Rodadas de estudo do ambiente e o último estudo de cada serviço.
        CREATE TABLE study_runs (
            id bigserial PRIMARY KEY,
            mode text NOT NULL,
            author text NOT NULL,
            status text NOT NULL,
            requested_at timestamptz NOT NULL,
            started_at timestamptz,
            finished_at timestamptz,
            reason text
        );
        CREATE INDEX study_runs_open ON study_runs (requested_at)
            WHERE status IN ('pendente', 'em_andamento');
        -- No máximo uma rodada pendente: pedidos simultâneos não duplicam o estudo.
        CREATE UNIQUE INDEX study_runs_one_pending ON study_runs ((true))
            WHERE status = 'pendente';

        CREATE TABLE service_studies (
            service text PRIMARY KEY,
            studied_at timestamptz,
            ok boolean NOT NULL,
            reason text
        );

        -- O uso do LLM do estudo não é de um incidente: aponta a rodada.
        -- Os tokens da rodada são a soma desse uso.
        ALTER TABLE llm_usage ADD COLUMN study_run_id bigint REFERENCES study_runs (id);
        CREATE INDEX llm_usage_study_run ON llm_usage (study_run_id);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
