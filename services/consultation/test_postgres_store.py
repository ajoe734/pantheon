"""Tests for optional Postgres store pilot (consultation service).

Uses fakes instead of a real DB so the tests run without psycopg installed.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    lifecycle_rows: list = []
    audit_rows: list = []
    memo_publication_rows: list = []
    outbox_rows: list = []
    statements: list = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("CREATE"):
            return _FakeCursor([])
        if normalized.startswith("INSERT INTO"):
            payload = json.loads(params[-1]) if isinstance(params[-1], str) else params[-1]
            if "LIFECYCLE_EVENTS" in normalized:
                self.lifecycle_rows.append(payload)
            elif "AUDIT_EVENTS" in normalized:
                self.audit_rows.append(payload)
            elif "MEMO_PUBLICATIONS" in normalized:
                self.memo_publication_rows.append(payload)
            elif "OUTBOX_RECORDS" in normalized:
                self.outbox_rows.append(payload)
            return _FakeCursor([])
        if normalized.startswith("SELECT EVENT_JSON"):
            return _FakeCursor([(row,) for row in self.lifecycle_rows])
        if normalized.startswith("SELECT PAYLOAD") and "AUDIT_EVENTS" in normalized:
            return _FakeCursor([(row,) for row in self.audit_rows])
        if normalized.startswith("SELECT PAYLOAD") and "OUTBOX_RECORDS" in normalized:
            return _FakeCursor([(row,) for row in self.outbox_rows])
        return _FakeCursor([])


def _make_fake_psycopg():
    conn = _FakeConnection()
    return SimpleNamespace(connect=lambda dsn: conn), conn


def _reset_fake_connection() -> None:
    _FakeConnection.lifecycle_rows = []
    _FakeConnection.audit_rows = []
    _FakeConnection.memo_publication_rows = []
    _FakeConnection.outbox_rows = []
    _FakeConnection.statements = []


def _request():
    from services.consultation.models import ActorRef, ConsultRequest, ConsultRequestType

    return ConsultRequest(
        request_id="cr-pg-001",
        request_type=ConsultRequestType.STRATEGY_REVIEW,
        requested_by=ActorRef(actor_type="operator", actor_id="alice"),
        target_type="strategy",
        target_id="strat-001",
        trace_id="trace-pg-001",
    )


def _audit_event():
    from services.consultation.models import ActorRef, ConsultAuditEvent

    return ConsultAuditEvent(
        audit_id="aud-pg-001",
        request_id="cr-pg-001",
        actor_ref=ActorRef(actor_type="operator", actor_id="alice"),
        action="request_created",
        after_state="draft",
        trace_id="trace-pg-001",
    )


def _published_memo():
    from services.consultation.models import (
        AuthorType,
        ConsultMemo,
        MemoStatus,
        MemoType,
        Recommendation,
    )

    return ConsultMemo(
        memo_id="mem-pg-001",
        request_id="cr-pg-001",
        memo_type=MemoType.COMMITTEE_SUMMARY,
        author_type=AuthorType.COMMITTEE,
        author_ref="committee-001",
        target_type="strategy",
        target_id="strat-001",
        summary="Approve pilot request.",
        recommendation=Recommendation.APPROVE,
        status=MemoStatus.PUBLISHED,
        trace_id="trace-pg-001",
        published_at="2026-04-29T00:00:00Z",
    )


def _handoff():
    from services.consultation.models import ConsultGateHandoff

    return ConsultGateHandoff(
        handoff_id="gh-pg-001",
        request_id="cr-pg-001",
        target_gate="governance:paper",
        memo_ids=["mem-pg-001"],
        evidence_refs=["ev-001"],
        audit_refs=["aud-pg-001"],
        trace_id="trace-pg-001",
    )


def test_build_consultation_store_jsonl_default():
    from services.consultation.store import ConsultationStore, build_consultation_store

    with mock.patch.dict("os.environ", {}, clear=True):
        store = build_consultation_store(tempfile.mkdtemp())

    assert isinstance(store, ConsultationStore)


def test_build_consultation_store_postgres_env_gated():
    from services.consultation.store import PostgresConsultationStore, build_consultation_store

    _reset_fake_connection()
    fake_psycopg, _conn = _make_fake_psycopg()

    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        mock.patch.dict(
            "os.environ",
            {
                "CONSULTATION_STORE_BACKEND": "postgres",
                "CONSULTATION_STORE_DSN": "postgresql://consult-writer@example/db",
            },
            clear=True,
        ),
    ):
        store = build_consultation_store(tempfile.mkdtemp())

    assert isinstance(store, PostgresConsultationStore)
    assert any("CREATE SCHEMA IF NOT EXISTS" in s for s in _FakeConnection.statements)
    assert any("CREATE TABLE IF NOT EXISTS" in s for s in _FakeConnection.statements)


def test_postgres_consultation_store_lifecycle_audit_outbox_and_reload():
    from services.consultation.store import PostgresConsultationStore

    _reset_fake_connection()
    fake_psycopg, _conn = _make_fake_psycopg()

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store = PostgresConsultationStore(
            dsn="postgresql://consult-writer@example/db",
            bootstrap=True,
        )
        request = _request()
        store.put_request(request)
        store.put_memo(_published_memo())
        store.put_handoff(_handoff())
        store.append_audit(_audit_event())

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        assert store.get_request("cr-pg-001") is not None
        assert store.get_memo("mem-pg-001") is not None
        assert store.get_handoff("gh-pg-001") is not None
        assert store.list_audit_for_request("cr-pg-001")[0].audit_id == "aud-pg-001"
        assert all(record.owner_service == "consultation-svc" for record in store.outbox.load())
        assert _FakeConnection.memo_publication_rows
        assert any("INSERT INTO" in s.upper() for s in _FakeConnection.statements)

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        reloaded = PostgresConsultationStore(
            dsn="postgresql://consult-writer@example/db",
            bootstrap=False,
        )

    assert reloaded.get_request("cr-pg-001") is not None
    assert reloaded.get_memo("mem-pg-001") is not None
    assert reloaded.get_handoff("gh-pg-001") is not None
    assert reloaded.list_requests()[0].target_id == "strat-001"


def test_build_consultation_store_invalid_backend():
    import pytest
    from services.consultation.store import build_consultation_store

    with mock.patch.dict("os.environ", {"CONSULTATION_STORE_BACKEND": "redis"}, clear=True):
        with pytest.raises(ValueError, match="must be jsonl or postgres"):
            build_consultation_store(str(Path(tempfile.mkdtemp())))
