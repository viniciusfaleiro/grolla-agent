"""Memória com RAG: extensão pgvector e os trechos indexados da memória e da documentação.

Revision ID: 0007
Revises: 0006
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE EXTENSION IF NOT EXISTS vector;

        -- Um trecho por seção de um registro da memória ou de um .md do repositório. O vetor não
        -- tem dimensão fixa: trocar de modelo é reindexar, e só se compara com o mesmo `model`.
        CREATE TABLE memory_chunks (
            id bigserial PRIMARY KEY,
            origin text NOT NULL,
            subject text NOT NULL,
            memory_id uuid REFERENCES memories (id),
            kind text,
            path text,
            url text,
            heading text NOT NULL,
            text text NOT NULL,
            model text NOT NULL,
            embedding vector NOT NULL,
            indexed_at timestamptz NOT NULL,
            search tsvector GENERATED ALWAYS AS (
                to_tsvector('portuguese', subject || ' ' || heading || ' ' || text)
            ) STORED
        );
        CREATE INDEX memory_chunks_search ON memory_chunks USING gin (search);
        CREATE INDEX memory_chunks_memory ON memory_chunks (memory_id);
        CREATE INDEX memory_chunks_origin ON memory_chunks (origin, subject);
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
