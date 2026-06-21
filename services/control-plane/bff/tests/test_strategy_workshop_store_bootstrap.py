from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[4]
STORE_PATH = ROOT / "services/control-plane/bff/agora/strategy_workshop/store.py"
PERSISTENCE_SCHEMA_PATH = ROOT / "services/control-plane/specs/agora/v3/workshop_persistence.schema.json"


def _load_store():
    spec = importlib.util.spec_from_file_location("strategy_workshop_store", STORE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _compact(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_strategy_workshop_table_ddl_matches_deep_closure_schema() -> None:
    store = _load_store()
    statements = [_compact(statement) for statement in store.build_strategy_workshop_table_ddl()]
    ddl = "\n".join(statements)

    assert len(statements) == 5
    for table_name in (
        "strategy_workshop_session",
        "strategy_workshop_event",
        "strategy_workshop_version_link",
        "strategy_completeness_snapshot",
        "agora_private_content_object",
    ):
        assert f'CREATE TABLE IF NOT EXISTS "{table_name}"' in ddl

    assert "workshop_id TEXT PRIMARY KEY" in ddl
    assert "lock_version BIGINT NOT NULL DEFAULT 1" in ddl
    assert "status TEXT NOT NULL" in ddl
    assert "CHECK (status IN ('open','in_review','concluded','archived'))" in ddl

    assert "private_content_ref TEXT NULL" in ddl
    assert "redacted_summary TEXT NULL" in ddl
    assert "redaction_policy_version TEXT NULL" in ddl
    assert "payload_refs_json JSONB NOT NULL DEFAULT '[]'::jsonb" in ddl
    assert "event_type <> 'message' OR" in ddl
    assert "private_content_ref IS NOT NULL" in ddl
    assert "redacted_summary IS NOT NULL" in ddl
    assert "redaction_policy_version IS NOT NULL" in ddl

    assert "ciphertext_sha256 CHAR(64) NOT NULL" in ddl
    assert "encrypted_dek BYTEA NOT NULL" in ddl
    assert "plaintext" not in ddl.lower()


def test_strategy_workshop_index_ddl_covers_all_deep_closure_indexes() -> None:
    store = _load_store()
    statements = [_compact(statement) for statement in store.build_strategy_workshop_index_ddl()]
    ddl = "\n".join(statements)
    index_names = {
        match.group(1)
        for statement in statements
        for match in [re.search(r'INDEX IF NOT EXISTS "([^"]+)"', statement)]
        if match
    }

    assert len(statements) == 18
    assert index_names == {
        "ix_workshop_user_status_updated",
        "ix_workshop_servant_status_updated",
        "ix_workshop_strategy_updated",
        "ix_workshop_active_registry_ref",
        "ux_workshop_openclaw_session",
        "ux_workshop_event_sequence",
        "ix_workshop_event_created",
        "ix_workshop_event_trace",
        "ux_workshop_event_private_ref",
        "ux_workshop_version_sequence",
        "ux_workshop_registry_version",
        "ix_workshop_version_strategy",
        "ux_workshop_completeness_version",
        "ix_workshop_completeness_latest",
        "ux_private_content_object_uri",
        "ix_private_content_owner_expiry",
        "ix_private_content_workshop_created",
        "ix_private_content_expiry_gc",
    }

    assert "WHERE strategy_id IS NOT NULL" in ddl
    assert "WHERE active_strategy_spec_registry_id IS NOT NULL" in ddl
    assert "WHERE openclaw_session_id IS NOT NULL" in ddl
    assert "WHERE private_content_ref IS NOT NULL" in ddl
    assert "WHERE state = 'active'" in ddl
    assert "WHERE state = 'active' AND expires_at IS NOT NULL" in ddl
    assert "CREATE UNIQUE INDEX IF NOT EXISTS" in ddl


def test_persistence_schema_index_manifest_matches_store_index_names() -> None:
    store = _load_store()
    schema = json.loads(PERSISTENCE_SCHEMA_PATH.read_text(encoding="utf-8"))
    schema_index_names = {entry["name"] for entry in schema["x-required-indexes"]}
    store_index_names = {
        match.group(1)
        for statement in store.build_strategy_workshop_index_ddl()
        for match in [re.search(r'INDEX IF NOT EXISTS "([^"]+)"', _compact(statement))]
        if match
    }

    assert schema["properties"]["idempotency_aggregate_type"]["const"] == store.IDEMPOTENCY_AGGREGATE_TYPE
    assert schema["definitions"]["workshop_status"]["enum"] == list(store.WORKSHOP_STATUSES)
    assert schema_index_names == store_index_names


def test_postgres_store_bootstrap_executes_schema_tables_then_indexes() -> None:
    store = _load_store()

    class FakeConnection:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def execute(self, statement: str, *args, **kwargs):
            self.statements.append(_compact(statement))
            return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    conn = FakeConnection()
    fake_psycopg = SimpleNamespace(connect=lambda dsn: conn)

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store.PostgresStrategyWorkshopStore(dsn="postgresql://example/pantheon", schema="agora")

    assert conn.statements[0] == 'CREATE SCHEMA IF NOT EXISTS "agora"'
    assert len(conn.statements) == 24
    assert conn.statements[1].startswith('CREATE TABLE IF NOT EXISTS "agora"."strategy_workshop_session"')
    assert conn.statements[5].startswith('CREATE TABLE IF NOT EXISTS "agora"."agora_private_content_object"')
    assert conn.statements[6].startswith('CREATE INDEX IF NOT EXISTS "agora"."ix_workshop_user_status_updated"')
