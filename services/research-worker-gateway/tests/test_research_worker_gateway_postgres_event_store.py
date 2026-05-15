from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SERVICE_DIR = Path(__file__).resolve().parents[1]


def _load_store_module():
    spec = importlib.util.spec_from_file_location("research_worker_gateway_store_test", SERVICE_DIR / "store.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_worker_gateway_store_test"] = module
    spec.loader.exec_module(module)
    return module


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


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
                self.events.append({"event_id": params[0], "job_id": params[1], "payload": payload})
            return _FakeCursor([])
        if normalized.startswith("SELECT PAYLOAD"):
            job_id = params[0] if params else None
            rows = [(event["payload"],) for event in self.events if job_id is None or event["job_id"] == job_id]
            return _FakeCursor(rows)
        return _FakeCursor([])


def test_worker_event_store_keeps_jsonl_default() -> None:
    module = _load_store_module()
    data_dir = tempfile.mkdtemp()

    with mock.patch.dict("os.environ", {}, clear=True):
        store = module.build_research_worker_gateway_store(data_dir)

    assert store.event_store is None
    store.append_event({"event_id": "wevt-1", "job_id": "job-1"})
    assert store.list_events("job-1")[0]["event_id"] == "wevt-1"


def test_worker_event_store_postgres_env_gated() -> None:
    module = _load_store_module()
    _FakeConnection.events = []
    _FakeConnection.statements = []
    fake_psycopg = SimpleNamespace(connect=lambda dsn: _FakeConnection())

    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        mock.patch.dict(
            "os.environ",
            {
                "RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND": "postgres",
                "RESEARCH_WORKER_GATEWAY_EVENT_STORE_DSN": "postgresql://worker-writer@example/db",
            },
            clear=True,
        ),
    ):
        store = module.build_research_worker_gateway_store(tempfile.mkdtemp())
        store.append_event({"event_id": "wevt-1", "job_id": "job-1", "event_type": "job_queued"})
        events = store.list_events("job-1")

    assert events[0]["event_type"] == "job_queued"
    assert any("CREATE SCHEMA IF NOT EXISTS" in statement for statement in _FakeConnection.statements)
    assert any("CREATE TABLE IF NOT EXISTS" in statement for statement in _FakeConnection.statements)
    assert not (Path(store.data_dir) / "worker_events.jsonl").exists()
