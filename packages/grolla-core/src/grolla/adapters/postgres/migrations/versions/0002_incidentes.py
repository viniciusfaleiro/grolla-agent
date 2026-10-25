"""Incidentes, log de eventos imutável, alertas recebidos e outbox de notificações.

Revision ID: 0002
Revises: 0001
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE incidents (
            id uuid PRIMARY KEY,
            title text NOT NULL,
            state text NOT NULL,
            severity text NOT NULL,
            services text[] NOT NULL,
            detected_at timestamptz NOT NULL,
            last_alert_at timestamptz NOT NULL,
            firing text[] NOT NULL DEFAULT '{}'
        );
        CREATE INDEX incidents_detected_at ON incidents (detected_at DESC);

        CREATE TABLE incident_events (
            id bigserial PRIMARY KEY,
            incident_id uuid NOT NULL REFERENCES incidents (id),
            at timestamptz NOT NULL,
            type text NOT NULL,
            author text NOT NULL,
            data jsonb NOT NULL
        );
        CREATE INDEX incident_events_incident ON incident_events (incident_id, id);

        -- O log de eventos é a base da auditoria e da memória: só aceita inserção.
        CREATE FUNCTION incident_events_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'incident_events só aceita inserção';
        END;
        $$;
        CREATE TRIGGER incident_events_append_only
            BEFORE UPDATE OR DELETE ON incident_events
            FOR EACH ROW EXECUTE FUNCTION incident_events_append_only();
        CREATE TRIGGER incident_events_no_truncate
            BEFORE TRUNCATE ON incident_events
            FOR EACH STATEMENT EXECUTE FUNCTION incident_events_append_only();

        CREATE TABLE alerts (
            id bigserial PRIMARY KEY,
            incident_id uuid REFERENCES incidents (id),
            fingerprint text NOT NULL,
            status text NOT NULL,
            starts_at timestamptz NOT NULL,
            ends_at timestamptz,
            rule_name text NOT NULL,
            service text NOT NULL,
            labels jsonb NOT NULL,
            summary text NOT NULL,
            description text NOT NULL,
            source_url text,
            raw jsonb NOT NULL,
            received_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (fingerprint, starts_at, status)
        );
        CREATE INDEX alerts_incident ON alerts (incident_id);

        CREATE TABLE outbox (
            id bigserial PRIMARY KEY,
            incident_id uuid NOT NULL REFERENCES incidents (id),
            notification jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            attempts integer NOT NULL DEFAULT 0,
            next_attempt_at timestamptz NOT NULL,
            delivered_at timestamptz,
            failed_at timestamptz,
            last_error text
        );
        CREATE INDEX outbox_pending ON outbox (next_attempt_at, id)
            WHERE delivered_at IS NULL AND failed_at IS NULL;
        """
    )


def downgrade() -> None:
    raise NotImplementedError("migrações do agent-grolla só avançam")
