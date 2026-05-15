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
    spec = importlib.util.spec_from_file_location("policy_learning_store_test", SERVICE_DIR / "store.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["policy_learning_store_test"] = module
    spec.loader.exec_module(module)
    return module


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeConnection:
    jobs = {}
    statements = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("INSERT INTO"):
            self.jobs[params[0]] = json.loads(params[4])
            return _FakeCursor([])
        if "WHERE JOB_ID" in normalized:
            payload = self.jobs.get(params[0])
            return _FakeCursor([] if payload is None else [(payload,)])
        if normalized.startswith("SELECT PAYLOAD"):
            return _FakeCursor([(payload,) for payload in self.jobs.values()])
        return _FakeCursor([])


def test_policy_learning_store_keeps_json_default() -> None:
    module = _load_store_module()
    data_dir = tempfile.mkdtemp()

    with mock.patch.dict("os.environ", {}, clear=True):
        store = module.build_policy_learning_store(data_dir)

    assert store.job_store is None
    store.put_job({"job_id": "plj-1", "policy_id": "policy-1", "status": "proposed"})
    assert store.get_job("plj-1")["policy_id"] == "policy-1"


def test_policy_learning_store_postgres_env_gated() -> None:
    module = _load_store_module()
    _FakeConnection.jobs = {}
    _FakeConnection.statements = []
    fake_psycopg = SimpleNamespace(connect=lambda dsn: _FakeConnection())

    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        mock.patch.dict(
            "os.environ",
            {
                "POLICY_LEARNING_STORE_BACKEND": "postgres",
                "POLICY_LEARNING_STORE_DSN": "postgresql://policy-learning-writer@example/db",
            },
            clear=True,
        ),
    ):
        store = module.build_policy_learning_store(tempfile.mkdtemp())
        store.put_job({"job_id": "plj-1", "policy_id": "policy-1", "status": "proposed"})
        job = store.get_job("plj-1")

    assert job["status"] == "proposed"
    assert any("CREATE SCHEMA IF NOT EXISTS" in statement for statement in _FakeConnection.statements)
    assert any("CREATE TABLE IF NOT EXISTS" in statement for statement in _FakeConnection.statements)
    assert not (Path(store.data_dir) / "policy_learning_jobs.json").exists()
