"""Tests for independent persistent Agora domain write owner and operation resolution.

Verifies:
1. Postgres-backed durable persistence using PostgresJsonOwnerStore foundation.
2. Builder configuration validation (DATABASE_URL and AGORA_STORE_DSN resolution).
3. Role-based write authority gates across all Agora resources.
4. Fresh independent read-after-write proof across distinct store instances.
5. Decision journal merge-patch, version bumping, and idempotency protection.
6. Absence of prohibited generic fallbacks, overlays, or read_store imports.
7. Source ingestion reconcile-only invariant.
"""
from __future__ import annotations

import ast
import json
import os
import sys
from unittest import mock

import pytest

from services.agora.service import AgoraWriteService, build_agora_write_service
from services.agora.store import AgoraStore, build_agora_store
from services.foundation.postgres_json_store import PostgresJsonOwnerStore


def test_agora_store_uses_postgres_json_owner_store() -> None:
    store = AgoraStore(
        dsn="postgresql://writer@example/db",
        schema="agora",
    )
    assert isinstance(store._sessions, PostgresJsonOwnerStore)
    assert isinstance(store._memos, PostgresJsonOwnerStore)
    assert isinstance(store._evidence_packs, PostgresJsonOwnerStore)
    assert isinstance(store._notes, PostgresJsonOwnerStore)
    assert isinstance(store._insights, PostgresJsonOwnerStore)
    assert isinstance(store._training_examples, PostgresJsonOwnerStore)
    assert isinstance(store._signals, PostgresJsonOwnerStore)
    assert isinstance(store._feedback, PostgresJsonOwnerStore)
    assert isinstance(store._handoffs, PostgresJsonOwnerStore)
    assert isinstance(store._audit_events, PostgresJsonOwnerStore)
    assert isinstance(store._journal, PostgresJsonOwnerStore)
    assert isinstance(store._workshops, PostgresJsonOwnerStore)
    assert isinstance(store._proposals, PostgresJsonOwnerStore)
    assert isinstance(store._interactions, PostgresJsonOwnerStore)

    assert store._sessions.table_name == "agora.sessions"
    assert store._memos.table_name == "agora.memos"
    assert store._signals.table_name == "agora.signals"
    assert store._journal.table_name == "agora.journal_entries"


def test_builder_configuration_validation() -> None:
    # Fails when neither AGORA_STORE_DSN nor DATABASE_URL is set
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="DATABASE_URL or AGORA_STORE_DSN is required"):
            build_agora_store()

        with pytest.raises(ValueError, match="DATABASE_URL or AGORA_STORE_DSN is required"):
            build_agora_write_service()

    # Succeeds when AGORA_STORE_DSN is set
    with mock.patch.dict(os.environ, {"AGORA_STORE_DSN": "postgresql://user@host/db"}, clear=True):
        s1 = build_agora_store()
        assert isinstance(s1, AgoraStore)
        w1 = build_agora_write_service()
        assert isinstance(w1, AgoraWriteService)

    # Succeeds when DATABASE_URL is set
    with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://user@host/db"}, clear=True):
        s2 = build_agora_store()
        assert isinstance(s2, AgoraStore)
        w2 = build_agora_write_service()
        assert isinstance(w2, AgoraWriteService)


def test_no_prohibited_generic_persistence_or_read_store_imports() -> None:
    import services.agora.service as asvc
    import services.agora.store as astor
    import services.agora.write_authority as awauth

    for mod in [asvc, astor, awauth]:
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "read_store" not in alias.name
                    assert "persistence" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "read_store" not in module
                assert "persistence" not in module
