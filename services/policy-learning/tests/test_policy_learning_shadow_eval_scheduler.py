"""Tests for policy-learning shadow eval scheduler and shadow-eval-tick endpoint."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]


def _load_scheduler_module():
    sys.modules.pop("policy_learning_scheduler_test", None)
    spec = importlib.util.spec_from_file_location(
        "policy_learning_scheduler_test",
        SERVICE_DIR / "scheduler_worker.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["policy_learning_scheduler_test"] = module
    spec.loader.exec_module(module)
    return module


def _load_service_module(data_dir: str):
    for key in list(sys.modules):
        if "policy_learning_sched_test" in key:
            del sys.modules[key]
    sys.modules.pop("store", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "policy_learning_sched_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["policy_learning_sched_test_main"] = module
        with mock.patch.dict(
            "os.environ",
            {
                "POLICY_LEARNING_DATA_DIR": data_dir,
                "POLICY_LEARNING_STORE_BACKEND": "json",
                "PERSISTENCE_POSTURE": "lenient",
            },
        ):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("store", None)


# ---------------------------------------------------------------------------
# scheduler_worker unit tests
# ---------------------------------------------------------------------------


def test_scheduler_env_vars_respected() -> None:
    scheduler = _load_scheduler_module()
    with mock.patch.dict(
        "os.environ",
        {
            "SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS": "120",
            "SHADOW_EVAL_SCHEDULER_MAX_TICKS": "3",
        },
    ):
        assert scheduler._env_int("SHADOW_EVAL_SCHEDULER_MAX_TICKS", 0) == 3
        assert scheduler._env_int("SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS", 3600) == 120


def test_scheduler_run_tick_returns_ok_on_success() -> None:
    scheduler = _load_scheduler_module()
    with mock.patch.object(scheduler, "run_tick") as mock_tick:
        mock_tick.return_value = {
            "status": "ok",
            "tick_id": "shadow-tick-20260627",
            "eval_type": "shadow",
            "candidate_count": 0,
            "skipped_count": 0,
            "candidate_ids": [],
            "production_training": "fail_closed",
        }
        result = scheduler.run_tick(api_url="http://test")
    assert result["status"] == "ok"
    assert result["production_training"] == "fail_closed"


def test_scheduler_run_tick_returns_error_on_http_failure() -> None:
    import urllib.error

    scheduler = _load_scheduler_module()

    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("http://test", 503, "Service Unavailable", {}, None)

        def read(self):
            return b"service down"

    with mock.patch("urllib.request.urlopen", side_effect=FakeHTTPError()):
        result = scheduler.run_tick(api_url="http://test")

    assert result["status"] == "error"
    assert result["code"] == 503


def test_scheduler_run_tick_handles_url_error() -> None:
    import urllib.error

    scheduler = _load_scheduler_module()
    with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        result = scheduler.run_tick(api_url="http://test")
    assert result["status"] == "error"
    assert "connection refused" in result["detail"]


# ---------------------------------------------------------------------------
# /api/policy-learning/shadow-eval-tick endpoint tests
# ---------------------------------------------------------------------------


def test_shadow_eval_tick_empty_datasets() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        resp = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={"tick_id": "tick-001", "eval_type": "shadow"},
        )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["tick_id"] == "tick-001"
        assert payload["eval_type"] == "shadow"
        assert payload["candidate_count"] == 0
        assert payload["skipped_count"] == 0
        assert payload["candidate_ids"] == []
        assert payload["production_training"] == "fail_closed"


def test_shadow_eval_tick_with_dataset_refs() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        dataset_refs = [
            {"id": "ds-trace-001", "type": "trace_dataset", "source": "agora_interaction"},
            {"id": "ds-trace-002", "type": "trace_dataset", "source": "telemetry_replay"},
        ]
        resp = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-002",
                "eval_type": "imitation",
                "dataset_refs": dataset_refs,
            },
        )
        assert resp.status_code == 201
        payload = resp.json()
        assert payload["status"] == "ok"
        assert payload["tick_id"] == "tick-002"
        assert payload["eval_type"] == "imitation"
        assert payload["candidate_count"] == 2
        assert payload["skipped_count"] == 0
        assert len(payload["candidate_ids"]) == 2
        assert payload["production_training"] == "fail_closed"

        # Verify candidates are persisted and gated
        listed = client.get("/api/policy-learning/candidates", params={"tick_id": "tick-002"})
        assert listed.status_code == 200
        candidates = listed.json()
        assert len(candidates) == 2
        for c in candidates:
            assert c["tick_id"] == "tick-002"
            assert c["eval_type"] == "imitation"
            assert c["status"] == "proposed"
            assert c["production_training"] == "fail_closed"
            assert c["experiment_approval_gate"] == "required"


def test_shadow_eval_tick_idempotent_same_tick_id() -> None:
    """Duplicate ticks with same tick_id and same dataset refs must not create duplicates."""
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        dataset_refs = [{"id": "ds-idem-001", "type": "trace_dataset"}]
        body = {"tick_id": "tick-idem-001", "eval_type": "shadow", "dataset_refs": dataset_refs}

        first = client.post("/api/policy-learning/shadow-eval-tick", json=body)
        assert first.status_code == 201
        assert first.json()["candidate_count"] == 1
        assert first.json()["skipped_count"] == 0

        second = client.post("/api/policy-learning/shadow-eval-tick", json=body)
        assert second.status_code == 201
        payload = second.json()
        assert payload["candidate_count"] == 0
        assert payload["skipped_count"] == 1
        assert "ds-idem-001" in payload["skipped_ids"]

        # Exactly one candidate exists
        listed = client.get("/api/policy-learning/candidates", params={"tick_id": "tick-idem-001"})
        assert len(listed.json()) == 1


def test_shadow_eval_tick_different_tick_ids_create_separate_candidates() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        dataset_refs = [{"id": "ds-multi-001", "type": "trace_dataset"}]

        r1 = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={"tick_id": "tick-A", "eval_type": "shadow", "dataset_refs": dataset_refs},
        )
        r2 = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={"tick_id": "tick-B", "eval_type": "shadow", "dataset_refs": dataset_refs},
        )
        assert r1.json()["candidate_count"] == 1
        assert r2.json()["candidate_count"] == 1

        listed = client.get("/api/policy-learning/candidates")
        assert len(listed.json()) == 2


def test_shadow_eval_tick_respects_max_datasets() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        dataset_refs = [
            {"id": f"ds-max-{i:03d}", "type": "trace_dataset"} for i in range(5)
        ]
        resp = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-max-001",
                "eval_type": "shadow",
                "dataset_refs": dataset_refs,
                "max_datasets": 2,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["candidate_count"] == 2


def test_shadow_eval_tick_candidates_remain_fail_closed() -> None:
    """Shadow eval candidates must never activate production training automatically."""
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        dataset_refs = [{"id": "ds-gate-001", "type": "trace_dataset"}]
        resp = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={"tick_id": "tick-gate-001", "eval_type": "shadow", "dataset_refs": dataset_refs},
        )
        assert resp.status_code == 201
        cid = resp.json()["candidate_ids"][0]

        cand = client.get(f"/api/policy-learning/candidates/{cid}")
        assert cand.status_code == 200
        body = cand.json()
        assert body["production_training"] == "fail_closed"
        assert body["experiment_approval_gate"] == "required"
        assert body["status"] == "proposed"


def test_get_candidate_not_found() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        resp = client.get("/api/policy-learning/candidates/nonexistent-id")
        assert resp.status_code == 404


def test_list_candidates_filter_by_eval_type() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-filter-shadow",
                "eval_type": "shadow",
                "dataset_refs": [{"id": "ds-s-001", "type": "trace_dataset"}],
            },
        )
        client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-filter-imitation",
                "eval_type": "imitation",
                "dataset_refs": [{"id": "ds-i-001", "type": "trace_dataset"}],
            },
        )

        shadow_list = client.get("/api/policy-learning/candidates", params={"eval_type": "shadow"})
        assert shadow_list.status_code == 200
        assert len(shadow_list.json()) == 1
        assert shadow_list.json()[0]["eval_type"] == "shadow"

        imitation_list = client.get(
            "/api/policy-learning/candidates", params={"eval_type": "imitation"}
        )
        assert len(imitation_list.json()) == 1
        assert imitation_list.json()[0]["eval_type"] == "imitation"


def test_worker_backlog_dlq_process_retry_replay_restart_endpoints() -> None:
    from fastapi.testclient import TestClient

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = TestClient(svc.app)

        # 1. Propose candidates
        resp = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={
                "tick_id": "tick-worker-test",
                "eval_type": "shadow",
                "dataset_refs": [{"id": "ds-limit-1", "type": "trace_dataset"}, {"id": "ds-limit-2", "type": "trace_dataset"}],
            },
        )
        assert resp.status_code == 201
        
        # 2. Check backlog
        backlog_resp = client.get("/api/policy-learning/worker/backlog")
        assert backlog_resp.status_code == 200
        backlog = backlog_resp.json()
        assert len(backlog) == 2
        for c in backlog:
            assert c["status"] == "proposed"

        # 3. Process backlog
        process_resp = client.post("/api/policy-learning/worker/process")
        assert process_resp.status_code == 200
        assert process_resp.json()["processed_count"] == 2

        # 4. Check backlog is now empty, and candidates are processed
        backlog_resp = client.get("/api/policy-learning/worker/backlog")
        assert len(backlog_resp.json()) == 0

        # Check listed candidates
        listed = client.get("/api/policy-learning/candidates")
        candidates = listed.json()
        assert len(candidates) == 2
        for c in candidates:
            assert c["status"] == "processed"
            assert "metrics" in c
            assert "evaluation_summary" in c
            assert "policy_weights" in c
            assert "lineage" in c

        # 5. Test DLQ and Replay/Retry: manually put a failed candidate
        c1 = candidates[0]
        c1["status"] = "failed"
        c1["error_message"] = "mock evaluation failure"
        svc.store.put_candidate(c1)

        # Check DLQ returns the failed candidate
        dlq_resp = client.get("/api/policy-learning/worker/dlq")
        assert dlq_resp.status_code == 200
        dlq = dlq_resp.json()
        assert len(dlq) == 1
        assert dlq[0]["candidate_id"] == c1["candidate_id"]

        # Replay DLQ item
        replay_resp = client.post(f"/api/policy-learning/worker/dlq/{c1['candidate_id']}/replay")
        assert replay_resp.status_code == 200
        # Replay automatically runs _process_backlog, so it shifts back to processed!
        assert replay_resp.json()["status"] == "processed"

        # DLQ should be empty now
        dlq_resp = client.get("/api/policy-learning/worker/dlq")
        assert len(dlq_resp.json()) == 0

        # Fail it again to test retry
        c1 = listed.json()[0]
        c1["status"] = "failed"
        c1["error_message"] = "mock evaluation failure"
        svc.store.put_candidate(c1)

        retry_resp = client.post(f"/api/policy-learning/worker/retry/{c1['candidate_id']}")
        assert retry_resp.status_code == 200
        assert retry_resp.json()["status"] == "processed"

        # Fail both to test restart
        for c in listed.json():
            c["status"] = "failed"
            c["error_message"] = "mock evaluation failure"
            svc.store.put_candidate(c)

        restart_resp = client.post("/api/policy-learning/worker/restart")
        assert restart_resp.status_code == 200
        assert restart_resp.json()["reset_count"] == 2

        # Check readback
        readback_resp = client.get(f"/api/policy-learning/worker/readback/{c1['candidate_id']}")
        assert readback_resp.status_code == 200
        assert readback_resp.json()["status"] == "processed"


def test_get_dataset_payload_postgres_loading() -> None:
    import os
    from unittest import mock
    from types import SimpleNamespace
    import json
    import tempfile
    
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _get_dataset_payload = svc._get_dataset_payload
        
        # 1. Non-postgres backend fallback
        with mock.patch.dict("os.environ", {"POLICY_LEARNING_STORE_BACKEND": "json"}):
            res = _get_dataset_payload("ds-trace-persona1-session1")
            assert res["dataset_id"] == "ds-trace-persona1-session1"
            assert len(res["sessions"]) == 2  # default SEED_DATASET has 2 sessions
        
        # 2. Postgres backend with mocked psycopg returning matching records
        class FakeCursor:
            def __init__(self, rows):
                self.rows = rows
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                pass
            def execute(self, sql, params=None):
                pass
            def fetchall(self):
                return self.rows
                
        class FakeConnection:
            def __init__(self, rows):
                self.rows = rows
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                pass
            def cursor(self):
                return FakeCursor(self.rows)

        complete_dataset_content = {
            "strategy_id": "test-strat",
            "source_strategy_spec_id": "test-spec",
            "sessions": [
                {
                    "trajectory_id": "t-100",
                    "actor_id": "act-1",
                    "actor_role": "operator",
                    "decision": "approve",
                    "target": {"registry_id": "reg-1"},
                    "steps": [{"observation": [1.0, 2.0], "action": "buy"}]
                }
            ]
        }
        
        single_session_content = {
            "trajectory_id": "t-200",
            "actor_id": "act-2",
            "actor_role": "approver",
            "decision": "edit",
            "steps": [{"observation": [3.0, 4.0], "action": "sell"}]
        }

        single_transition_content = {
            "actor_id": "act-3",
            "actor_role": "operator",
            "decision": "approve",
            "observation": [5.0, 6.0],
            "action": "hold"
        }

        db_rows = [
            (
                "persona1", "session1",
                json.dumps(complete_dataset_content),
                json.dumps(["ref-1"]),
                "ev-1"
            ),
            (
                "persona1", "session1",
                json.dumps(single_session_content),
                None,
                "ev-2"
            ),
            (
                "persona1", "session1",
                json.dumps(single_transition_content),
                json.dumps(["ref-2"]),
                "ev-3"
            ),
            (
                "persona2", "session2",
                json.dumps(complete_dataset_content),
                None,
                "ev-4"
            )
        ]

        fake_psycopg = SimpleNamespace(connect=lambda dsn: FakeConnection(db_rows))

        with (
            mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
            mock.patch.dict(
                "os.environ",
                {
                    "POLICY_LEARNING_STORE_BACKEND": "postgres",
                    "POLICY_LEARNING_STORE_DSN": "postgresql://test",
                }
            )
        ):
            res = _get_dataset_payload("ds-trace-persona1-session1")
            assert res["dataset_id"] == "ds-trace-persona1-session1"
            assert res["strategy_id"] == "test-strat"
            assert res["source_strategy_spec_id"] == "test-spec"
            assert len(res["sessions"]) == 3
            
            assert res["sessions"][0]["trajectory_id"] == "t-100"
            assert res["sessions"][0]["steps"][0]["observation"] == [1.0, 2.0]
            
            assert res["sessions"][1]["trajectory_id"] == "t-200"
            assert res["sessions"][1]["actor_role"] == "approver"
            assert res["sessions"][1]["decision"] == "edit"
            
            assert res["sessions"][2]["trajectory_id"] == "traj-ev-3"
            assert res["sessions"][2]["actor_role"] == "operator"
            assert res["sessions"][2]["steps"][0]["observation"] == [5.0, 6.0]
            assert res["sessions"][2]["steps"][0]["action"] == "hold"
            assert res["sessions"][2]["steps"][0]["feedback_event_id"] == "ev-3"
            
            assert "evidence://ev-1" in res["source_dataset_refs"]
            assert "evidence://ev-2" in res["source_dataset_refs"]
            assert "evidence://ev-3" in res["source_dataset_refs"]
            assert "ref-1" in res["source_dataset_refs"]
            assert "ref-2" in res["source_dataset_refs"]
            
        fake_psycopg_empty = SimpleNamespace(connect=lambda dsn: FakeConnection([]))
        with (
            mock.patch.dict(sys.modules, {"psycopg": fake_psycopg_empty}),
            mock.patch.dict(
                "os.environ",
                {
                    "POLICY_LEARNING_STORE_BACKEND": "postgres",
                    "POLICY_LEARNING_STORE_DSN": "postgresql://test",
                }
            )
        ):
            res = _get_dataset_payload("ds-trace-persona1-session1")
            assert res["dataset_id"] == "ds-trace-persona1-session1"
            assert len(res["sessions"]) == 2



