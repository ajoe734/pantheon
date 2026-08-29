"""Tests for independent persistent Research domain write owner.

Verifies:
1. Postgres-backed durable persistence using PostgresJsonOwnerStore foundation.
2. Ticket operations: create, patch, lifecycle state transitions, allowedActions, list filtering.
3. Experiment operations: create, cancel, allowedActions, ticket linking, list filtering.
4. Note operations: create, get, list.
5. Fresh independent read-after-write proof across distinct store instances with zero caching/overlays.
6. Environment-gated builder behavior and required DSN configuration validation.
7. Absence of prohibited generic fallbacks, overlays, or read_store imports.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from services.foundation.postgres_json_store import PostgresJsonOwnerStore
from services.research.write_owner import (
    ResearchWriteOwner,
    build_research_write_owner,
)
from services.research.write_service import (
    ResearchWriteService,
    build_research_write_service,
)


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    rows: dict[str, dict[str, dict]] = {}
    statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        table = self._table_name(sql)
        if normalized.startswith("CREATE"):
            return _FakeCursor([])
        if normalized.startswith("INSERT INTO"):
            payload = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            table_rows = self.rows.setdefault(table, {})
            record_id = str(params[0])
            if "DO NOTHING" in normalized and record_id in table_rows:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)] if "RETURNING PAYLOAD" in normalized else [])
        if normalized.startswith("UPDATE"):
            payload = json.loads(params[0]) if isinstance(params[0], str) else params[0]
            record_id = str(params[1])
            expected = json.loads(params[2]) if isinstance(params[2], str) else params[2]
            table_rows = self.rows.setdefault(table, {})
            if table_rows.get(record_id) != expected:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)])
        if normalized.startswith("DELETE FROM"):
            record_id = str(params[0])
            expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            table_rows = self.rows.setdefault(table, {})
            if table_rows.get(record_id) != expected:
                return _FakeCursor([])
            removed = table_rows.pop(record_id)
            return _FakeCursor([(removed,)])
        if normalized.startswith("SELECT PAYLOAD") and "WHERE RECORD_ID" in normalized:
            record = self.rows.get(table, {}).get(str(params[0]))
            if "AND PAYLOAD" in normalized and record is not None:
                expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
                if record != expected:
                    record = None
            return _FakeCursor([(record,)] if record is not None else [])
        if normalized.startswith("SELECT PAYLOAD"):
            return _FakeCursor([(payload,) for payload in self.rows.get(table, {}).values()])
        return _FakeCursor([])

    def rollback(self):
        pass

    @staticmethod
    def _table_name(sql: str) -> str:
        match = re.search(
            r"(?:FROM|INTO|UPDATE)\s+((?:\"[^\"]+\"\.)?\"[^\"]+\")",
            sql,
            re.IGNORECASE,
        )
        return match.group(1) if match else "<unknown>"


def _fake_psycopg():
    conn = _FakeConnection()
    return SimpleNamespace(connect=lambda dsn: conn)


@pytest.fixture(autouse=True)
def _reset_fake_db():
    _FakeConnection.rows = {}
    _FakeConnection.statements = []


def test_research_write_owner_uses_postgres_json_owner_store():
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        owner = ResearchWriteOwner(
            dsn="postgresql://writer@example/db",
            schema="research",
        )
        assert isinstance(owner._tickets_store, PostgresJsonOwnerStore)
        assert isinstance(owner._experiments_store, PostgresJsonOwnerStore)
        assert isinstance(owner._notes_store, PostgresJsonOwnerStore)
        assert owner._tickets_store.table_name == "research.research_tickets"
        assert owner._experiments_store.table_name == "research.research_experiments"
        assert owner._notes_store.table_name == "research.research_notes"

        # Verify bootstrap created tables
        ddl = " ".join(_FakeConnection.statements).lower()
        assert "research.research_tickets" in ddl or '"research"."research_tickets"' in ddl
        assert "research.research_experiments" in ddl or '"research"."research_experiments"' in ddl
        assert "research.research_notes" in ddl or '"research"."research_notes"' in ddl


def test_research_ticket_lifecycle_and_allowed_actions():
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        owner = ResearchWriteOwner(dsn="postgresql://writer@example/db")

        # 1. Create ticket
        created = owner.create_research_ticket(
            title="Alpha Strategy Investigation",
            description="Investigating momentum anomaly",
            priority="high",
            owner="alpha_team",
            actor_id="researcher_1",
            created_at="2026-08-29T10:00:00Z",
            ticket_id="rt-20260829-001",
        )
        assert created["ticket_id"] == "rt-20260829-001"
        assert created["status"] == "open"
        assert created["priority"] == "high"
        assert created["owner"] == "alpha_team"
        assert created["allowedActions"] == {"canEdit": True, "canClose": True, "canArchive": False}
        assert len(created["lifecycle_history"]) == 1
        assert created["lifecycle_history"][0]["to_status"] == "open"

        # 2. Patch ticket metadata
        patched_meta = owner.patch_research_ticket(
            "rt-20260829-001",
            patch={"title": "Updated Alpha Strategy Investigation", "priority": "critical"},
            actor_id="researcher_1",
            updated_at="2026-08-29T10:30:00Z",
        )
        assert patched_meta is not None
        assert patched_meta["title"] == "Updated Alpha Strategy Investigation"
        assert patched_meta["priority"] == "critical"
        assert patched_meta["status"] == "open"

        # 3. Transition to in_progress
        in_progress = owner.patch_research_ticket(
            "rt-20260829-001",
            patch={"status": "in_progress"},
            actor_id="researcher_1",
            updated_at="2026-08-29T11:00:00Z",
        )
        assert in_progress is not None
        assert in_progress["status"] == "in_progress"
        assert in_progress["allowedActions"] == {"canEdit": True, "canClose": True, "canArchive": False}

        # 4. Transition to closed
        closed = owner.patch_research_ticket(
            "rt-20260829-001",
            patch={"status": "closed"},
            actor_id="lead_1",
            updated_at="2026-08-29T12:00:00Z",
        )
        assert closed is not None
        assert closed["status"] == "closed"
        assert closed["closed_at"] == "2026-08-29T12:00:00Z"
        assert closed["allowedActions"] == {"canEdit": False, "canClose": False, "canArchive": True}

        # 5. Transition to archived
        archived = owner.patch_research_ticket(
            "rt-20260829-001",
            patch={"status": "archived"},
            actor_id="lead_1",
            updated_at="2026-08-29T13:00:00Z",
        )
        assert archived is not None
        assert archived["status"] == "archived"
        assert archived["archived_at"] == "2026-08-29T13:00:00Z"
        assert archived["allowedActions"] == {"canEdit": False, "canClose": False, "canArchive": False}
        assert len(archived["lifecycle_history"]) == 4

        # 6. List tickets with status filter
        listed = owner.list_research_tickets(statuses=["archived"])
        assert len(listed) == 1
        assert listed[0]["ticket_id"] == "rt-20260829-001"
        assert listed[0]["allowedActions"] == {"canEdit": False, "canClose": False, "canArchive": False}

        empty_list = owner.list_research_tickets(statuses=["open"])
        assert len(empty_list) == 0


def test_research_experiment_creation_cancellation_and_linking():
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        owner = ResearchWriteOwner(dsn="postgresql://writer@example/db")

        # Create ticket first
        ticket = owner.create_research_ticket(
            title="Momentum Experimentation",
            description="Testing cross-asset momentum",
            priority="medium",
            owner="quant_dev",
            actor_id="quant_dev",
            ticket_id="rt-20260829-002",
        )
        assert ticket["linked_experiments"] == []

        # Create experiment linked to ticket
        exp = owner.create_research_experiment(
            ticket_id="rt-20260829-002",
            experiment_name="Exp-Momentum-Q3",
            strategy_selector={"strategy_id": "strat_mom_1", "variant_id": "v1"},
            parameter_set={"lookback": 20, "threshold": 0.05},
            run_config={"backend": "qlib", "dataset_ref": "ds_equity_daily", "stage": "backtest"},
            launch_context={"analysis_refs": ["ana-001"]},
            queued_at="2026-08-29T10:15:00Z",
            experiment_id="exp-20260829-001",
        )
        assert exp["experiment_id"] == "exp-20260829-001"
        assert exp["ticket_id"] == "rt-20260829-002"
        assert exp["status"] == "queued"
        assert exp["allowedActions"] == {"canCancel": True}

        # Check ticket automatically received linked experiment
        refreshed_ticket = owner.get_research_ticket("rt-20260829-002")
        assert refreshed_ticket is not None
        assert "exp-20260829-001" in refreshed_ticket["linked_experiments"]

        # Cancel experiment
        canceled = owner.cancel_research_experiment(
            "exp-20260829-001",
            completed_at="2026-08-29T10:20:00Z",
        )
        assert canceled is not None
        assert canceled["status"] == "canceled"
        assert canceled["completed_at"] == "2026-08-29T10:20:00Z"
        assert canceled["allowedActions"] == {"canCancel": False}

        # Canceling already canceled experiment returns None
        assert owner.cancel_research_experiment("exp-20260829-001") is None

        # Query experiment by ticket
        listed_exps = owner.list_research_experiments(ticket_id="rt-20260829-002")
        assert len(listed_exps) == 1
        assert listed_exps[0]["experiment_id"] == "exp-20260829-001"
        assert listed_exps[0]["allowedActions"] == {"canCancel": False}


def test_research_note_creation_and_query():
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        owner = ResearchWriteOwner(dsn="postgresql://writer@example/db")

        note_payload = {
            "note_id": "note-20260829-001",
            "title": "Alpha Research Log 1",
            "content": "Observed significant turnover reduction with 30d smoothing.",
            "author": "analyst_1",
            "tags": ["alpha", "turnover"],
            "created_at": "2026-08-29T09:00:00Z",
        }
        created_note = owner.create_research_note(note_payload)
        assert created_note is not None
        assert created_note["note_id"] == "note-20260829-001"
        assert created_note["title"] == "Alpha Research Log 1"

        fetched_note = owner.get_research_note("note-20260829-001")
        assert fetched_note == created_note

        all_notes = owner.list_research_notes()
        assert len(all_notes) == 1
        assert all_notes[0]["note_id"] == "note-20260829-001"


def test_fresh_independent_read_after_write_across_instances():
    """Proves that writes are durable in Postgres and immediately readable by independent instances."""
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        # Instance A performs writes
        writer_instance = ResearchWriteOwner(dsn="postgresql://writer@example/db")
        writer_instance.create_research_ticket(
            title="Cross-Instance Verification",
            description="Ensures zero memory caching / instant durability",
            priority="high",
            owner="verifier",
            actor_id="verifier",
            ticket_id="rt-20260829-verify",
        )
        writer_instance.create_research_experiment(
            ticket_id="rt-20260829-verify",
            experiment_name="Exp-Verify",
            strategy_selector={"strategy_id": "strat_v"},
            parameter_set={"p": 1},
            run_config={"backend": "qlib"},
            launch_context={},
            experiment_id="exp-20260829-verify",
        )
        writer_instance.create_research_note({
            "note_id": "note-20260829-verify",
            "title": "Verify Note",
            "content": "Durable content",
        })

        # Instance B (separate fresh instance, separate memory space) reads immediately
        reader_instance = ResearchWriteOwner(dsn="postgresql://reader@example/db")
        ticket_read = reader_instance.get_research_ticket("rt-20260829-verify")
        assert ticket_read is not None
        assert ticket_read["title"] == "Cross-Instance Verification"
        assert "exp-20260829-verify" in ticket_read["linked_experiments"]

        exp_read = reader_instance.get_research_experiment("exp-20260829-verify")
        assert exp_read is not None
        assert exp_read["experiment_name"] == "Exp-Verify"

        note_read = reader_instance.get_research_note("note-20260829-verify")
        assert note_read is not None
        assert note_read["content"] == "Durable content"

        # Now Instance B mutates the ticket
        reader_instance.patch_research_ticket(
            "rt-20260829-verify",
            patch={"status": "in_progress", "priority": "critical"},
            actor_id="reader_worker",
        )

        # Instance A reads freshly from DB and sees Instance B's mutation
        ticket_re_read = writer_instance.get_research_ticket("rt-20260829-verify")
        assert ticket_re_read is not None
        assert ticket_re_read["status"] == "in_progress"
        assert ticket_re_read["priority"] == "critical"


def test_builder_configuration_validation():
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        # Fails when DSN and DATABASE_URL are not set
        with mock.patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="DATABASE_URL or RESEARCH_STORE_DSN is required"):
                build_research_write_owner()

        # Succeeds when RESEARCH_STORE_DSN is set
        with mock.patch.dict(os.environ, {"RESEARCH_STORE_DSN": "postgresql://user@host/db"}, clear=True):
            owner1 = build_research_write_owner()
            assert isinstance(owner1, ResearchWriteOwner)

        # Succeeds with DATABASE_URL
        with mock.patch.dict(os.environ, {"DATABASE_URL": "postgresql://user@host/db"}, clear=True):
            owner2 = build_research_write_service()
            assert isinstance(owner2, ResearchWriteService)


def test_no_prohibited_generic_persistence_or_read_store_imports():
    """Verify that generic persistence and read_store are not imported or referenced."""
    import services.research.write_owner as wo
    import services.research.write_service as ws

    for module_file in (wo.__file__, ws.__file__):
        tree = ast.parse(open(module_file, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "read_store" not in alias.name
                    assert "persistence" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "read_store" not in module
                assert "persistence" not in module
