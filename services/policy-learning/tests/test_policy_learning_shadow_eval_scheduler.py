"""Tests for policy-learning shadow eval scheduler and shadow-eval-tick endpoint."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

from conftest import authorized_client


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


def _agora_record(
    dataset_version_id: str,
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "user-a",
    evidence_id: str | None = None,
    content: dict | None = None,
    order: int = 0,
) -> dict:
    """One row shaped like ``agora.agora_dataset_records``."""

    resolved_evidence = evidence_id or f"ev-{dataset_version_id}"
    return {
        "evidence_id": resolved_evidence,
        "dataset_version_id": dataset_version_id,
        "dataset_kind": "learn",
        "interaction_kind": "feedback",
        "persona_id": "persona-1",
        "session_id": "session-1",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "content": content
        or {
            "steps": [
                {
                    "observation": [0.9, 0.1, -0.2],
                    "action": "buy_small",
                    "reward": 0.3,
                    "feedback_event_id": f"{resolved_evidence}-a",
                },
                {
                    "observation": [-0.8, 0.2, 0.55],
                    "action": "reduce_risk",
                    "reward": 0.1,
                    "feedback_event_id": f"{resolved_evidence}-b",
                },
            ]
        },
        "source_refs": ["artifact://source-1"],
        "learning_eligible": True,
        "captured_at": "2026-07-26T00:00:00Z",
        "extracted_at": f"2026-07-26T00:00:{order:02d}Z",
        "version": 1,
    }


def _install_memory_authority(svc, records: list[dict], *, tenant_id: str = "tenant-a"):
    """Point the service at an in-memory Agora dataset authority.

    Keeps the tests on the real product code path — discovery, resolution, and
    lineage all run — without needing a live Postgres Agora owner.
    """

    from agora_dataset_authority import AgoraDatasetAuthority

    svc.DATASET_AUTHORITY = AgoraDatasetAuthority(records=records)
    os.environ["POLICY_LEARNING_AGORA_TENANT_ID"] = tenant_id
    return svc.DATASET_AUTHORITY


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


def test_scheduler_health_requires_recent_successful_fail_closed_tick(
    tmp_path: Path,
) -> None:
    scheduler = _load_scheduler_module()
    health_file = tmp_path / "shadow-eval-health.json"
    success = {
        "status": "ok",
        "candidate_count": 0,
        "production_training": "fail_closed",
    }
    cycle = {"status": "ok", "processed_count": 0}

    with mock.patch.dict(
        "os.environ",
        {
            "POLICY_LEARNING_SERVICE_TOKEN": "test-token",
            "AGORA_HANDOFF_SERVICE_TOKEN": "agora-test-token",
            "POLICY_LEARNING_AGORA_TENANT_ID": "tenant-health",
            "SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS": "1",
            "SHADOW_EVAL_SCHEDULER_MAX_TICKS": "1",
            "SHADOW_EVAL_SCHEDULER_HEALTH_FILE": str(health_file),
        },
        clear=False,
    ), mock.patch.object(
        scheduler,
        "run_recovery",
        return_value={"status": "ok", "reset_count": 0},
    ), mock.patch.object(
        scheduler,
        "run_intake_cycle",
        return_value=success,
    ), mock.patch.object(
        scheduler,
        "run_claim_cycle",
        return_value=cycle,
    ):
        assert scheduler.main() == 0
        assert scheduler.healthcheck() == 0

    state = json.loads(health_file.read_text(encoding="utf-8"))
    assert state["worker_name"] == "policy-learning-shadow-eval-scheduler"
    assert state["status"] == "ok"
    assert state["ticks"] == 1
    assert state["production_training"] == "fail_closed"


def test_scheduler_health_degrades_when_handoff_intake_is_partial(
    tmp_path: Path,
) -> None:
    scheduler = _load_scheduler_module()
    health_file = tmp_path / "shadow-eval-degraded-health.json"
    degraded = {
        "status": "degraded",
        "processed_count": 1,
        "acked_count": 0,
        "errors": [{"step": "ack", "detail": "BFF unavailable"}],
    }

    with mock.patch.dict(
        "os.environ",
        {
            "POLICY_LEARNING_SERVICE_TOKEN": "test-token",
            "AGORA_HANDOFF_SERVICE_TOKEN": "agora-test-token",
            "POLICY_LEARNING_AGORA_TENANT_ID": "tenant-health",
            "SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS": "1",
            "SHADOW_EVAL_SCHEDULER_MAX_TICKS": "1",
            "SHADOW_EVAL_SCHEDULER_HEALTH_FILE": str(health_file),
        },
        clear=False,
    ), mock.patch.object(
        scheduler,
        "run_recovery",
        return_value={"status": "ok", "reset_count": 0},
    ), mock.patch.object(
        scheduler,
        "run_intake_cycle",
        return_value=degraded,
    ), mock.patch.object(
        scheduler,
        "run_claim_cycle",
        return_value={"status": "ok", "processed_count": 1},
    ):
        assert scheduler.main() == 0

    state = json.loads(health_file.read_text(encoding="utf-8"))
    assert state["status"] == "degraded"
    assert state["last_failure_at"] is not None
    assert state["last_success_at"] is None


# ---------------------------------------------------------------------------
# /api/policy-learning/shadow-eval-tick endpoint tests
# ---------------------------------------------------------------------------


def test_shadow_eval_tick_empty_tenant_creates_no_candidates() -> None:
    """A reachable authority holding no data for the tenant ticks empty."""
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_memory_authority(svc, [_agora_record("dsv-other", tenant_id="tenant-z")])
        client = authorized_client(svc.app)

        resp = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={"tick_id": "tick-001", "eval_type": "shadow", "tenant_id": "tenant-a"},
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
        assert payload["seed_fallback_used"] is False


def test_shadow_eval_tick_fails_closed_without_dataset_authority() -> None:
    """No authority means no candidates and no seed substitution."""
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        from agora_dataset_authority import AgoraDatasetAuthority

        svc.DATASET_AUTHORITY = AgoraDatasetAuthority(backend="", dsn="")
        client = authorized_client(svc.app)

        resp = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={"tick_id": "tick-unconfigured", "eval_type": "shadow"},
        )
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["status"] == "degraded"
        assert detail["reason"] == "agora_authority_unconfigured"
        assert detail["seed_fallback_used"] is False
        assert detail["candidate_count"] == 0
        assert client.get("/api/policy-learning/candidates").json() == []


def test_shadow_eval_tick_with_dataset_refs() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = authorized_client(svc.app)

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
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = authorized_client(svc.app)

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
        # The repeat resolves to the same derived candidate id it skipped.
        assert payload["skipped_ids"] == first.json()["candidate_ids"]

        # Exactly one candidate exists
        listed = client.get("/api/policy-learning/candidates", params={"tick_id": "tick-idem-001"})
        assert len(listed.json()) == 1


def test_shadow_eval_tick_different_tick_ids_create_separate_candidates() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = authorized_client(svc.app)

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
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = authorized_client(svc.app)

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
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = authorized_client(svc.app)

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
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = authorized_client(svc.app)

        resp = client.get("/api/policy-learning/candidates/nonexistent-id")
        assert resp.status_code == 404


def test_list_candidates_filter_by_eval_type() -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        client = authorized_client(svc.app)

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
    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        _install_memory_authority(
            svc,
            [_agora_record("dsv-limit-1", order=1), _agora_record("dsv-limit-2", order=2)],
        )
        client = authorized_client(svc.app)

        # 1. Propose candidates
        resp = client.post(
            "/api/policy-learning/shadow-eval-tick",
            json={"tick_id": "tick-worker-test", "eval_type": "shadow"},
        )
        assert resp.status_code == 201
        assert resp.json()["candidate_count"] == 2

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
            assert c["dataset_lineage"]["seed_fallback_used"] is False

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

        # Restart recovers leases orphaned by a crashed worker.  A DLQ entry is
        # NOT silently re-run: it needs an explicit replay, so a poison
        # candidate cannot loop forever across restarts.
        orphan = listed.json()[0]
        orphan["status"] = "claimed"
        orphan["lease_owner"] = "worker-that-died"
        orphan["lease_token"] = "lease-stale"
        orphan["lease_expires_at"] = "2020-01-01T00:00:00Z"
        svc.store.put_candidate(orphan)

        poisoned = listed.json()[1]
        poisoned["status"] = "failed"
        poisoned["error_message"] = "mock evaluation failure"
        svc.store.put_candidate(poisoned)

        restart_resp = client.post("/api/policy-learning/worker/restart")
        assert restart_resp.status_code == 200
        restart_payload = restart_resp.json()
        assert restart_payload["released_count"] == 1
        assert orphan["candidate_id"] in restart_payload["released_candidate_ids"]
        # The released orphan was re-processed in the same cycle...
        assert svc.store.get_candidate(orphan["candidate_id"])["status"] == "processed"
        # ...while the DLQ entry stayed in the DLQ.
        assert svc.store.get_candidate(poisoned["candidate_id"])["status"] == "failed"

        # Check readback
        readback_resp = client.get(f"/api/policy-learning/worker/readback/{orphan['candidate_id']}")
        assert readback_resp.status_code == 200
        assert readback_resp.json()["status"] == "processed"


def test_agora_dataset_resolution_covers_governed_content_shapes() -> None:
    """Every governed Agora content shape resolves without seed substitution.

    Replaces the old ``_get_dataset_payload`` Postgres test: dataset content no
    longer arrives through the policy-learning store backend, it arrives
    through the tenant-scoped Agora dataset authority.
    """

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)

        sessions_content = {
            "strategy_id": "test-strat",
            "source_strategy_spec_id": "test-spec",
            "sessions": [
                {
                    "trajectory_id": "t-100",
                    "actor_id": "act-1",
                    "actor_role": "operator",
                    "decision": "approve",
                    "target": {"registry_id": "reg-1"},
                    "steps": [{"observation": [1.0, 2.0], "action": "buy"}],
                }
            ],
        }
        single_session_content = {
            "trajectory_id": "t-200",
            "actor_id": "act-2",
            "actor_role": "approver",
            "decision": "edit",
            "steps": [{"observation": [3.0, 4.0], "action": "sell"}],
        }
        single_transition_content = {
            "actor_id": "act-3",
            "actor_role": "operator",
            "decision": "approve",
            "observation": [5.0, 6.0],
            "action": "hold",
        }

        _install_memory_authority(
            svc,
            [
                _agora_record("dsv-sessions", evidence_id="ev-1", content=sessions_content, order=1),
                _agora_record("dsv-steps", evidence_id="ev-2", content=single_session_content, order=2),
                _agora_record(
                    "dsv-transition", evidence_id="ev-3", content=single_transition_content, order=3
                ),
                _agora_record("dsv-foreign", tenant_id="tenant-z", evidence_id="ev-4", order=4),
            ],
        )

        def resolve(dataset_version_id: str):
            return svc.resolve_candidate_dataset(
                {"dataset_ref": {"dataset_version_id": dataset_version_id, "tenant_id": "tenant-a"}}
            )

        payload, lineage = resolve("dsv-sessions")
        assert payload["dataset_id"] == "dsv-sessions"
        assert payload["strategy_id"] == "test-strat"
        assert payload["source_strategy_spec_id"] == "test-spec"
        assert payload["sessions"][0]["trajectory_id"] == "t-100"
        assert payload["sessions"][0]["steps"][0]["observation"] == [1.0, 2.0]
        assert "agora://dataset-version/tenant-a/dsv-sessions" in payload["source_dataset_refs"]
        assert "evidence://ev-1" in payload["source_dataset_refs"]
        assert "artifact://source-1" in payload["source_dataset_refs"]
        assert lineage["tenant_id"] == "tenant-a"
        assert lineage["dataset_version_ids"] == ["dsv-sessions"]
        assert lineage["seed_fallback_used"] is False
        assert lineage["authoritative"] is True

        payload, _ = resolve("dsv-steps")
        assert payload["sessions"][0]["trajectory_id"] == "t-200"
        assert payload["sessions"][0]["actor_role"] == "approver"
        assert payload["sessions"][0]["decision"] == "edit"

        payload, _ = resolve("dsv-transition")
        assert payload["sessions"][0]["trajectory_id"] == "traj-ev-3"
        assert payload["sessions"][0]["actor_role"] == "operator"
        assert payload["sessions"][0]["steps"][0]["observation"] == [5.0, 6.0]
        assert payload["sessions"][0]["steps"][0]["action"] == "hold"
        assert payload["sessions"][0]["steps"][0]["feedback_event_id"] == "ev-3"

        # A version owned by another tenant is invisible, and the failure is an
        # error rather than a quiet fall back to the seed fixture.
        try:
            resolve("dsv-foreign")
        except svc.DatasetResolutionError as exc:
            assert exc.reason == "dataset_version_not_found"
        else:  # pragma: no cover - guards the tenant isolation contract
            raise AssertionError("cross-tenant dataset version must not resolve")
