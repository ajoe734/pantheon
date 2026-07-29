from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_store_module():
    spec = importlib.util.spec_from_file_location("training_session_store_test", SERVICE_DIR / "store.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["training_session_store_test"] = module
    spec.loader.exec_module(module)
    return module


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    events = []
    statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("INSERT INTO"):
            payload = json.loads(params[5])
            if all(event["event_id"] != params[0] for event in self.events):
                self.events.append(
                    {
                        "event_id": params[0],
                        "session_id": params[1],
                        "payload": payload,
                    }
                )
                return _FakeCursor([(payload,)])
            return _FakeCursor([])
        if normalized.startswith("SELECT PAYLOAD"):
            if "WHERE EVENT_ID = %S" in normalized:
                rows = [
                    (event["payload"],)
                    for event in self.events
                    if event["event_id"] == params[0]
                ]
                return _FakeCursor(rows)
            session_id = params[0] if params else None
            rows = [
                (event["payload"],)
                for event in self.events
                if session_id is None or event["session_id"] == session_id
            ]
            return _FakeCursor(rows)
        return _FakeCursor([])


def test_build_training_session_store_keeps_jsonl_default() -> None:
    module = _load_store_module()
    data_dir = tempfile.mkdtemp()

    with mock.patch.dict("os.environ", {}, clear=True):
        store = module.build_training_session_store(data_dir)

    assert store.event_store is None
    store.append_event({"session_id": "trn-1", "event_id": "tevt-1", "sequence_number": 1})
    assert store.list_event_log("trn-1")[0]["event_id"] == "tevt-1"


def test_postgres_event_store_is_env_gated_and_uses_table_payload() -> None:
    module = _load_store_module()
    _FakeConnection.events = []
    _FakeConnection.statements = []
    fake_psycopg = SimpleNamespace(connect=lambda dsn: _FakeConnection())

    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        mock.patch.dict(
            "os.environ",
            {
                "TRAINING_SESSION_EVENT_STORE_BACKEND": "postgres",
                "TRAINING_SESSION_EVENT_STORE_DSN": "postgresql://training-session-writer@example/db",
            },
            clear=True,
        ),
    ):
        store = module.build_training_session_store(tempfile.mkdtemp())
        record = store.append_event(
            {
                "session_id": "trn-1",
                "event_id": "tevt-1",
                "event_type": "message",
                "sequence_number": 1,
                "message_body": "persist through postgres",
            }
        )
        events = store.list_event_log("trn-1")

    assert record["event_id"] == "tevt-1"
    assert events[0]["message_body"] == "persist through postgres"
    assert any("CREATE SCHEMA IF NOT EXISTS" in statement for statement in _FakeConnection.statements)
    assert any("CREATE TABLE IF NOT EXISTS" in statement for statement in _FakeConnection.statements)
    assert not (Path(store.data_dir) / "teaching_events.jsonl").exists()


def test_postgres_event_store_replays_durable_duplicate_and_rejects_conflict() -> None:
    module = _load_store_module()
    _FakeConnection.events = []
    _FakeConnection.statements = []
    fake_psycopg = SimpleNamespace(connect=lambda dsn: _FakeConnection())
    first = {
        "session_id": "trn-conflict-1",
        "tenant_id": "tenant-a",
        "event_id": "tevt-conflict-1",
        "sequence_number": 1,
        "message_body": "durable-first",
    }

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store = module.PostgresTrainingSessionEventStore(
            dsn="postgresql://training-session-writer@example/db",
            bootstrap=False,
        )
        assert store.append_event(first) == first
        assert store.append_event(dict(first)) == first
        with pytest.raises(ValueError, match="event_id conflict"):
            store.append_event({**first, "message_body": "conflicting-second"})

    assert _FakeConnection.events == [
        {
            "event_id": "tevt-conflict-1",
            "session_id": "trn-conflict-1",
            "payload": first,
        }
    ]


class _AuthoritativeConnection:
    records: dict[tuple[str, str], dict] = {}
    advisory_locks: dict[str, threading.Lock] = {}
    state_lock = threading.Lock()

    def __init__(self) -> None:
        self.held_locks: list[threading.Lock] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        for lock in reversed(self.held_locks):
            lock.release()
        self.held_locks.clear()
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split()).upper()
        if "PG_ADVISORY_XACT_LOCK" in normalized:
            lock_ref = str(params[0])
            with self.state_lock:
                lock = self.advisory_locks.setdefault(lock_ref, threading.Lock())
            lock.acquire()
            self.held_locks.append(lock)
            return _FakeCursor([(1,)])
        if normalized.startswith(("CREATE", "SELECT 1")):
            return _FakeCursor([(1,)] if normalized.startswith("SELECT 1") else [])
        if normalized.startswith("SELECT PAYLOAD") and "RECORD_KIND = %S AND RECORD_ID = %S" in normalized:
            record = self.records.get((str(params[0]), str(params[1])))
            return _FakeCursor([(json.loads(json.dumps(record)),)] if record is not None else [])
        if normalized.startswith("SELECT PAYLOAD") and "RECORD_KIND = %S" in normalized:
            rows = [
                (json.loads(json.dumps(payload)),)
                for (kind, _record_id), payload in self.records.items()
                if kind == str(params[0])
            ]
            return _FakeCursor(rows)
        if normalized.startswith("INSERT INTO") and len(params) == 4:
            kind, record_id, _tenant_id, encoded = params
            with self.state_lock:
                self.records[(str(kind), str(record_id))] = json.loads(encoded)
            return _FakeCursor([])
        return _FakeCursor([])


def test_postgres_replay_lock_allows_one_terminal_commit_across_workers_and_restart() -> None:
    module = _load_store_module()
    _AuthoritativeConnection.records = {}
    _AuthoritativeConnection.advisory_locks = {}
    fake_psycopg = SimpleNamespace(connect=lambda dsn: _AuthoritativeConnection())
    commit_calls: list[str] = []
    calls_lock = threading.Lock()

    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        first_worker = module.PostgresTrainingSessionStore(
            tempfile.mkdtemp(),
            dsn="postgresql://training-session-writer@example/db",
            bootstrap=False,
        )
        second_worker = module.PostgresTrainingSessionStore(
            tempfile.mkdtemp(),
            dsn="postgresql://training-session-writer@example/db",
            bootstrap=False,
        )
        first_worker.put_replay(
            "trn-ha-1",
            {
                "session_id": "trn-ha-1",
                "tenant_id": "tenant-a",
                "replay_resolution": {"state": "pending_decision"},
            },
        )

        def commit_once(store):
            def decide(replay):
                assert replay is not None
                if replay["replay_resolution"]["state"] == "committed":
                    return replay
                with calls_lock:
                    commit_calls.append("persona-target-commit")
                time.sleep(0.05)
                replay["replay_resolution"]["state"] = "committed"
                replay["artifacts"] = {
                    "persona_target_controller_record_ref": "persona-target:terminal-1"
                }
                return replay

            return store.mutate_replay("trn-ha-1", decide)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(commit_once, (first_worker, second_worker))
            )

        restarted_worker = module.PostgresTrainingSessionStore(
            tempfile.mkdtemp(),
            dsn="postgresql://training-session-writer@example/db",
            bootstrap=False,
        )
        terminal = restarted_worker.get_replay("trn-ha-1")

    assert commit_calls == ["persona-target-commit"]
    assert [result["replay_resolution"]["state"] for result in results] == [
        "committed",
        "committed",
    ]
    assert terminal is not None
    assert terminal["artifacts"]["persona_target_controller_record_ref"] == "persona-target:terminal-1"


@pytest.mark.skipif(
    not os.getenv("TRAINING_SESSION_TEST_POSTGRES_DSN"),
    reason="TRAINING_SESSION_TEST_POSTGRES_DSN is not configured",
)
def test_real_postgres_two_workers_and_restart_observe_one_terminal_commit() -> None:
    module = _load_store_module()
    dsn = str(os.environ["TRAINING_SESSION_TEST_POSTGRES_DSN"])
    suffix = uuid.uuid4().hex[:12]
    records_table = f"training_session.l12_teach_records_{suffix}"
    events_table = f"training_session.l12_teach_events_{suffix}"
    first_worker = module.PostgresTrainingSessionStore(
        tempfile.mkdtemp(),
        dsn=dsn,
        records_table=records_table,
        events_table=events_table,
    )
    second_worker = module.PostgresTrainingSessionStore(
        tempfile.mkdtemp(),
        dsn=dsn,
        records_table=records_table,
        events_table=events_table,
        bootstrap=False,
    )
    commit_calls: list[str] = []
    calls_lock = threading.Lock()
    try:
        first_worker.put_replay(
            "trn-real-ha-1",
            {
                "session_id": "trn-real-ha-1",
                "tenant_id": "tenant-real",
                "replay_resolution": {"state": "pending_decision"},
            },
        )

        def commit_once(store):
            def decide(replay):
                assert replay is not None
                if replay["replay_resolution"]["state"] == "committed":
                    return replay
                with calls_lock:
                    commit_calls.append("persona-target-commit")
                time.sleep(0.1)
                replay["replay_resolution"]["state"] = "committed"
                replay["artifacts"] = {
                    "persona_target_controller_record_ref": "persona-target:real-terminal-1"
                }
                return replay

            return store.mutate_replay("trn-real-ha-1", decide)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(commit_once, (first_worker, second_worker)))

        restarted_worker = module.PostgresTrainingSessionStore(
            tempfile.mkdtemp(),
            dsn=dsn,
            records_table=records_table,
            events_table=events_table,
            bootstrap=False,
        )
        terminal = restarted_worker.get_replay("trn-real-ha-1")

        assert commit_calls == ["persona-target-commit"]
        assert all(
            result["replay_resolution"]["state"] == "committed"
            for result in results
        )
        assert terminal is not None
        assert (
            terminal["artifacts"]["persona_target_controller_record_ref"]
            == "persona-target:real-terminal-1"
        )
    finally:
        import psycopg

        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {module._quote_pg_identifier(events_table)}")
            conn.execute(f"DROP TABLE IF EXISTS {module._quote_pg_identifier(records_table)}")


@pytest.mark.skipif(
    not os.getenv("TRAINING_SESSION_TEST_POSTGRES_DSN"),
    reason="TRAINING_SESSION_TEST_POSTGRES_DSN is not configured",
)
def test_real_postgres_two_workers_serialize_session_append_and_reject_event_conflict() -> None:
    module = _load_store_module()
    dsn = str(os.environ["TRAINING_SESSION_TEST_POSTGRES_DSN"])
    suffix = uuid.uuid4().hex[:12]
    records_table = f"training_session.l12_teach_append_records_{suffix}"
    events_table = f"training_session.l12_teach_append_events_{suffix}"
    first_worker = module.PostgresTrainingSessionStore(
        tempfile.mkdtemp(),
        dsn=dsn,
        records_table=records_table,
        events_table=events_table,
    )
    second_worker = module.PostgresTrainingSessionStore(
        tempfile.mkdtemp(),
        dsn=dsn,
        records_table=records_table,
        events_table=events_table,
        bootstrap=False,
    )
    try:
        first_worker.put_session(
            {
                "id": "trn-real-append-1",
                "session_id": "trn-real-append-1",
                "tenant_id": "tenant-real",
                "events": [],
                "outcomes": [],
            }
        )

        def append_once(store, label: str):
            def build_event(session):
                assert session is not None
                sequence = (
                    max(
                        (
                            int(event.get("sequence_number") or 0)
                            for event in session.get("events", [])
                        ),
                        default=0,
                    )
                    + 1
                )
                time.sleep(0.1)
                return {
                    "session_id": "trn-real-append-1",
                    "tenant_id": "tenant-real",
                    "event_id": f"tevt-real-append-{sequence}",
                    "event_type": "message",
                    "sequence_number": sequence,
                    "message_body": label,
                }

            return store.append_session_event("trn-real-append-1", build_event)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(append_once, first_worker, "worker-one"),
                executor.submit(append_once, second_worker, "worker-two"),
            ]
            results = [future.result() for future in futures]

        restarted_worker = module.PostgresTrainingSessionStore(
            tempfile.mkdtemp(),
            dsn=dsn,
            records_table=records_table,
            events_table=events_table,
            bootstrap=False,
        )
        durable_session = restarted_worker.get_session("trn-real-append-1")
        durable_events = restarted_worker.list_event_log("trn-real-append-1")

        assert durable_session is not None
        assert [event["sequence_number"] for event in durable_session["events"]] == [1, 2]
        assert {event["message_body"] for event in durable_session["events"]} == {
            "worker-one",
            "worker-two",
        }
        assert {event["event_id"] for event in durable_events} == {
            "tevt-real-append-1",
            "tevt-real-append-2",
        }
        assert all(result[0]["events"] for result in results)

        durable_conflict = {
            "session_id": "trn-real-append-1",
            "tenant_id": "tenant-real",
            "event_id": "tevt-real-conflict",
            "event_type": "message",
            "sequence_number": 99,
            "message_body": "durable-first",
        }
        assert first_worker.append_event(durable_conflict) == durable_conflict
        assert second_worker.append_event(dict(durable_conflict)) == durable_conflict
        with pytest.raises(ValueError, match="event_id conflict"):
            second_worker.append_event(
                {**durable_conflict, "message_body": "conflicting-second"}
            )
        conflict_rows = [
            event
            for event in restarted_worker.list_event_log("trn-real-append-1")
            if event["event_id"] == "tevt-real-conflict"
        ]
        assert conflict_rows == [durable_conflict]
    finally:
        import psycopg

        with psycopg.connect(dsn) as conn:
            conn.execute(f"DROP TABLE IF EXISTS {module._quote_pg_identifier(events_table)}")
            conn.execute(f"DROP TABLE IF EXISTS {module._quote_pg_identifier(records_table)}")
