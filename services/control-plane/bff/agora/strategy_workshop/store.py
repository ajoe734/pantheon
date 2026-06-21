"""Postgres bootstrap for Agora Strategy Workshop persistence.

The workshop event table stores only encrypted-content references and
redacted summaries for message events. Raw private content belongs in the
private content object store, never in these relational rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.foundation.postgres_json_store import ensure_postgres_schema, quote_pg_identifier


DEFAULT_SCHEMA = ""
IDEMPOTENCY_AGGREGATE_TYPE = "strategy_workshop"
WORKSHOP_STATUSES = ("open", "in_review", "concluded", "archived")


@dataclass(frozen=True)
class StrategyWorkshopTableNames:
    session: str
    event: str
    version_link: str
    completeness_snapshot: str
    private_content_object: str


def _clean_schema(schema: str | None) -> str:
    return str(schema or "").strip()


def _qualified(schema: str | None, name: str) -> str:
    clean_schema = _clean_schema(schema)
    return f"{clean_schema}.{name}" if clean_schema else name


def _quoted(schema: str | None, name: str) -> str:
    return quote_pg_identifier(_qualified(schema, name))


def _tables(schema: str | None = DEFAULT_SCHEMA) -> StrategyWorkshopTableNames:
    return StrategyWorkshopTableNames(
        session=_quoted(schema, "strategy_workshop_session"),
        event=_quoted(schema, "strategy_workshop_event"),
        version_link=_quoted(schema, "strategy_workshop_version_link"),
        completeness_snapshot=_quoted(schema, "strategy_completeness_snapshot"),
        private_content_object=_quoted(schema, "agora_private_content_object"),
    )


def build_strategy_workshop_table_ddl(schema: str | None = DEFAULT_SCHEMA) -> list[str]:
    tables = _tables(schema)
    return [
        f"""
        CREATE TABLE IF NOT EXISTS {tables.session} (
            workshop_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            servant_persona_id TEXT NOT NULL,
            openclaw_session_id TEXT NULL,
            strategy_id TEXT NULL,
            active_strategy_spec_registry_id TEXT NULL,
            active_workshop_version_id TEXT NULL,
            final_strategy_spec_registry_id TEXT NULL,
            final_workshop_version_id TEXT NULL,
            status TEXT NOT NULL,
            lock_version BIGINT NOT NULL DEFAULT 1,
            title TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            concluded_at TIMESTAMPTZ NULL,
            archived_at TIMESTAMPTZ NULL,
            CONSTRAINT ck_strategy_workshop_session_status
                CHECK (status IN ('open','in_review','concluded','archived'))
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {tables.event} (
            event_id TEXT PRIMARY KEY,
            workshop_id TEXT NOT NULL REFERENCES {tables.session}(workshop_id),
            sequence_no BIGINT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_ref TEXT NULL,
            event_type TEXT NOT NULL,
            private_content_ref TEXT NULL,
            redacted_summary TEXT NULL,
            redaction_policy_version TEXT NULL,
            payload_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            trace_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ux_workshop_event_sequence UNIQUE (workshop_id, sequence_no),
            CONSTRAINT ck_strategy_workshop_event_message_redaction CHECK (
                event_type <> 'message'
                OR (
                    private_content_ref IS NOT NULL
                    AND redacted_summary IS NOT NULL
                    AND redaction_policy_version IS NOT NULL
                )
            )
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {tables.version_link} (
            workshop_version_id TEXT PRIMARY KEY,
            workshop_id TEXT NOT NULL REFERENCES {tables.session}(workshop_id),
            strategy_id TEXT NOT NULL,
            strategy_spec_registry_id TEXT NOT NULL,
            parent_workshop_version_id TEXT NULL,
            source_event_id TEXT NULL,
            sequence_no BIGINT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ux_workshop_version_sequence UNIQUE (workshop_id, sequence_no),
            CONSTRAINT ux_workshop_registry_version UNIQUE (workshop_id, strategy_spec_registry_id)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {tables.completeness_snapshot} (
            snapshot_id TEXT PRIMARY KEY,
            workshop_id TEXT NOT NULL REFERENCES {tables.session}(workshop_id),
            workshop_version_id TEXT NULL,
            assessment_version BIGINT NOT NULL,
            state_map_json JSONB NOT NULL,
            blocking_items_json JSONB NOT NULL,
            next_question_json JSONB NULL,
            created_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ux_workshop_completeness_version UNIQUE (workshop_id, assessment_version)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {tables.private_content_object} (
            private_content_ref TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            workshop_id TEXT NOT NULL,
            event_id TEXT NULL,
            object_uri TEXT NOT NULL,
            ciphertext_sha256 CHAR(64) NOT NULL,
            encrypted_dek BYTEA NOT NULL,
            kek_key_version TEXT NOT NULL,
            content_type TEXT NOT NULL,
            retention_class TEXT NOT NULL,
            expires_at TIMESTAMPTZ NULL,
            state TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            deleted_at TIMESTAMPTZ NULL
        )
        """,
    ]


def build_strategy_workshop_index_ddl(schema: str | None = DEFAULT_SCHEMA) -> list[str]:
    tables = _tables(schema)

    def index_name(name: str) -> str:
        return quote_pg_identifier(_qualified(schema, name))

    return [
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_workshop_user_status_updated")}
        ON {tables.session}
        (tenant_id, user_id, status, updated_at DESC)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_workshop_servant_status_updated")}
        ON {tables.session}
        (servant_persona_id, status, updated_at DESC)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_workshop_strategy_updated")}
        ON {tables.session}
        (strategy_id, updated_at DESC)
        WHERE strategy_id IS NOT NULL
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_workshop_active_registry_ref")}
        ON {tables.session}
        (active_strategy_spec_registry_id)
        WHERE active_strategy_spec_registry_id IS NOT NULL
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {index_name("ux_workshop_openclaw_session")}
        ON {tables.session}
        (openclaw_session_id)
        WHERE openclaw_session_id IS NOT NULL
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {index_name("ux_workshop_event_sequence")}
        ON {tables.event}
        (workshop_id, sequence_no)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_workshop_event_created")}
        ON {tables.event}
        (workshop_id, created_at, sequence_no)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_workshop_event_trace")}
        ON {tables.event}
        (trace_id)
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {index_name("ux_workshop_event_private_ref")}
        ON {tables.event}
        (private_content_ref)
        WHERE private_content_ref IS NOT NULL
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {index_name("ux_workshop_version_sequence")}
        ON {tables.version_link}
        (workshop_id, sequence_no)
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {index_name("ux_workshop_registry_version")}
        ON {tables.version_link}
        (workshop_id, strategy_spec_registry_id)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_workshop_version_strategy")}
        ON {tables.version_link}
        (strategy_id, created_at DESC)
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {index_name("ux_workshop_completeness_version")}
        ON {tables.completeness_snapshot}
        (workshop_id, assessment_version)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_workshop_completeness_latest")}
        ON {tables.completeness_snapshot}
        (workshop_id, created_at DESC)
        """,
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {index_name("ux_private_content_object_uri")}
        ON {tables.private_content_object}
        (object_uri)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_private_content_owner_expiry")}
        ON {tables.private_content_object}
        (tenant_id, owner_user_id, expires_at)
        WHERE state = 'active'
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_private_content_workshop_created")}
        ON {tables.private_content_object}
        (workshop_id, created_at DESC)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS {index_name("ix_private_content_expiry_gc")}
        ON {tables.private_content_object}
        (expires_at)
        WHERE state = 'active' AND expires_at IS NOT NULL
        """,
    ]


def build_strategy_workshop_bootstrap_ddl(schema: str | None = DEFAULT_SCHEMA) -> list[str]:
    return build_strategy_workshop_table_ddl(schema) + build_strategy_workshop_index_ddl(schema)


class PostgresStrategyWorkshopStore:
    """Small bootstrap wrapper for the strategy workshop owner tables."""

    def __init__(self, *, dsn: str, schema: str | None = DEFAULT_SCHEMA, bootstrap: bool = True) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        self.dsn = dsn
        self.schema = _clean_schema(schema)
        if bootstrap:
            self.bootstrap()

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required for PostgresStrategyWorkshopStore") from exc
        return psycopg.connect(self.dsn)

    def bootstrap(self) -> None:
        with self._connect() as conn:
            ensure_postgres_schema(conn, self.schema)
            for statement in build_strategy_workshop_bootstrap_ddl(self.schema):
                conn.execute(statement)
