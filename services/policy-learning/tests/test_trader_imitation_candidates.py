"""Tests for IMITATION-TRADER-SHADOW-COMPARISON-20261127 in services/policy-learning.

Covers:
  - AC 1: One behaviour candidate per trader and strategy is trained from
    AGORA-DECISION-EVIDENCE-KINDS-20261127 data (decision, rejection, deferral,
    intervention) through the existing policy-learning imitation loop after
    existing training approval and only with at least 30 decisions.
  - AC 4: Runs in the existing hourly shadow-eval scheduler
    (POST /api/policy-learning/worker/process / run_worker_cycle) and keeps at
    most 3 candidates per trader and strategy.
  - AC 5: services/learning/imitation/ is documented as not run as a runtime
    worker and imitation-worker is removed from cloudbuild.yaml.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any, Dict, List
from unittest import mock

from conftest import authorized_client


SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]


def _load_service_module(data_dir: str):
    for key in list(sys.modules):
        if "policy_learning_imit_test" in key:
            del sys.modules[key]
    sys.modules.pop("store", None)
    sys.modules.pop("trader_imitation", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "policy_learning_imit_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["policy_learning_imit_test_main"] = module
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


def _make_decision_evidence_version(
    idx: int,
    *,
    tenant_id: str = "tenant-a",
    user_id: str = "trader-lin",
    strategy_id: str = "strat-twse-1",
    salt: str = "",
) -> Dict[str, Any]:
    kinds = ("decision", "rejection", "deferral", "intervention")
    kind = kinds[idx % len(kinds)]
    verdict = {
        "decision": "approve",
        "rejection": "reject",
        "deferral": "defer",
        "intervention": "modify",
    }[kind]
    action = "enter" if kind in ("decision", "intervention") else "no_trade"
    conf = 0.85 if action == "enter" else 0.35
    prob = 0.80 if action == "enter" else 0.30
    return {
        "dataset_version_id": f"dsv-{tenant_id}-{user_id}-{strategy_id}-{idx:03d}{salt}",
        "evidence_id": f"ev-{tenant_id}-{user_id}-{strategy_id}-{idx:03d}{salt}",
        "tenant_id": tenant_id,
        "user_id": user_id,
        "persona_id": "servant-lin",
        "session_id": f"decision_lock:hdl-{idx:03d}{salt}",
        "interaction_kind": kind,
        "dataset_kind": "learn",
        "consent_policy": "both",
        "retention_days": 30,
        "expires_at": "2026-12-27T00:00:00Z",
        "extracted_at": f"2026-11-27T01:{idx % 60:02d}:00Z",
        "redaction_applied": False,
        "redacted_fields": [],
        "content": {
            "source_kind": "decision_lock",
            "evidence_kind": kind,
            "human_decision_id": f"hdl-{idx:03d}{salt}",
            "decision_event_id": f"evt-{idx:03d}{salt}",
            "strategy_id": strategy_id,
            "symbol": "2330.TW",
            "decision": action,
            "trading_room_verdict": verdict,
            "rationale": f"rationale-{idx}{salt}",
            "modifications": {"size_hint": "small"} if kind == "intervention" else None,
            "decision_time_snapshot": {
                "symbol": "2330.TW",
                "strategy_id": strategy_id,
                "event_kind": "entry",
                "suggested_action": "enter" if action == "enter" else "review",
                "confidence": {"value": conf},
                "probability": {"value": prob},
                "expected_value": {"net": 0.02 if action == "enter" else -0.01, "downside": -0.01},
            },
        },
        "lineage": {
            "source": "agora_decision_lock",
            "evidence_kind": kind,
            "strategy_id": strategy_id,
            "human_decision_id": f"hdl-{idx:03d}{salt}",
            "decision_event_id": f"evt-{idx:03d}{salt}",
        },
    }


def test_under_30_decisions_returns_not_enough_decisions_even_when_approved(tmp_path) -> None:
    module = _load_service_module(str(tmp_path))
    client = authorized_client(module.app)

    # Record training approval for (tenant-a, trader-lin, strat-twse-1)
    approve_resp = client.post(
        "/api/policy-learning/trader-imitation/approvals",
        json={
            "tenant_id": "tenant-a",
            "user_id": "trader-lin",
            "strategy_id": "strat-twse-1",
            "approved": True,
            "approved_by": "operator-1",
        },
    )
    assert approve_resp.status_code == 201, approve_resp.text

    # Seed only 29 T4 decision evidence records (< 30 minimum)
    for i in range(29):
        v = _make_decision_evidence_version(i)
        module.store.record_decision_evidence_version(v)

    train_resp = client.post(
        "/api/policy-learning/trader-imitation/train",
        json={
            "tenant_id": "tenant-a",
            "user_id": "trader-lin",
            "strategy_id": "strat-twse-1",
        },
    )
    assert train_resp.status_code == 200, train_resp.text
    body = train_resp.json()
    assert body["status"] == "ok"
    assert body["trained_count"] == 0
    assert body["status_counts"].get("not_enough_decisions") == 1
    res = body["groups"][0]
    assert res["status"] == "not_enough_decisions"
    assert res["decision_count"] == 29
    assert res["minimum_decisions_required"] == 30
    assert res["remaining_decisions_needed"] == 1
    assert res["created"] is False
    assert res["candidate"] is None


def test_30_decisions_requires_training_approval_then_trains_in_hourly_scheduler_and_caps_at_3(tmp_path) -> None:
    module = _load_service_module(str(tmp_path))
    client = authorized_client(module.app)

    # Seed 30 T4 decision evidence records covering decision, rejection, deferral, intervention
    for i in range(30):
        v = _make_decision_evidence_version(i)
        module.store.record_decision_evidence_version(v)

    # 1. Without training approval, training is blocked with awaiting_training_approval
    unapproved_resp = client.post(
        "/api/policy-learning/trader-imitation/train",
        json={
            "tenant_id": "tenant-a",
            "user_id": "trader-lin",
            "strategy_id": "strat-twse-1",
        },
    )
    assert unapproved_resp.status_code == 200, unapproved_resp.text
    unapproved = unapproved_resp.json()
    assert unapproved["status_counts"].get("awaiting_training_approval") == 1
    res0 = unapproved["groups"][0]
    assert res0["status"] == "awaiting_training_approval"
    assert res0["decision_count"] == 30
    assert res0["training_approved"] is False
    assert res0["created"] is False
    assert res0["candidate"] is None

    # 2. Grant training approval
    approve_resp = client.post(
        "/api/policy-learning/trader-imitation/approvals",
        json={
            "tenant_id": "tenant-a",
            "user_id": "trader-lin",
            "strategy_id": "strat-twse-1",
            "approved": True,
            "approved_by": "operator-1",
        },
    )
    assert approve_resp.status_code == 201, approve_resp.text

    # 3. Trigger the hourly shadow-eval scheduler worker endpoint (/api/policy-learning/worker/process)
    worker_resp = client.post("/api/policy-learning/worker/process")
    assert worker_resp.status_code == 200, worker_resp.text
    worker_body = worker_resp.json()
    imit_cycle = worker_body.get("trader_imitation") or {}
    assert imit_cycle.get("trained_count") == 1

    # 4. Verify candidate is stored and predicts deterministically
    list_resp = client.get(
        "/api/policy-learning/trader-imitation/candidates",
        params={
            "tenant_id": "tenant-a",
            "user_id": "trader-lin",
            "strategy_id": "strat-twse-1",
        },
    )
    assert list_resp.status_code == 200, list_resp.text
    list_body = list_resp.json()
    assert list_body["status"] == "ok"
    assert list_body["state"] == "ready"
    assert list_body["candidate_count"] == 1
    assert list_body["max_candidates_kept"] == 3
    first_cand_id = list_body["active_candidate"]["candidate_id"]
    assert set(list_body["active_candidate"]["evidence_kinds"]) == {
        "decision",
        "rejection",
        "deferral",
        "intervention",
    }

    pred_resp = client.post(
        "/api/policy-learning/trader-imitation/predict",
        json={
            "tenant_id": "tenant-a",
            "user_id": "trader-lin",
            "strategy_id": "strat-twse-1",
            "decision_time_snapshot": {
                "suggested_action": "enter",
                "confidence": {"value": 0.9},
                "probability": {"value": 0.85},
                "expected_value": {"net": 0.03, "downside": -0.01},
            },
        },
    )
    assert pred_resp.status_code == 200, pred_resp.text
    pred_body = pred_resp.json()
    assert pred_body["available"] is True
    assert pred_body["action"] in {"enter", "no_trade"}
    assert pred_body["candidate_id"] == first_cand_id

    # 5. Train 3 more distinct dataset revisions (total 4 revisions) and verify at most 3 candidates are kept
    created_ids: List[str] = [first_cand_id]
    for rev in range(1, 4):
        extra = _make_decision_evidence_version(30 + rev, salt=f"-r{rev}")
        module.store.record_decision_evidence_version(extra)
        cycle_resp = client.post("/api/policy-learning/worker/process")
        assert cycle_resp.status_code == 200, cycle_resp.text
        cands_now = client.get(
            "/api/policy-learning/trader-imitation/candidates",
            params={
                "tenant_id": "tenant-a",
                "user_id": "trader-lin",
                "strategy_id": "strat-twse-1",
            },
        ).json()
        assert cands_now["candidate_count"] <= 3
        created_ids.append(cands_now["active_candidate"]["candidate_id"])

    final_list = client.get(
        "/api/policy-learning/trader-imitation/candidates",
        params={
            "tenant_id": "tenant-a",
            "user_id": "trader-lin",
            "strategy_id": "strat-twse-1",
        },
    ).json()
    assert final_list["candidate_count"] == 3
    kept_ids = [c["candidate_id"] for c in final_list["candidates"]]
    assert first_cand_id not in kept_ids
    assert kept_ids == list(reversed(created_ids[-3:]))


def test_training_approval_gate_rejects_proposed_dispatched_and_tenantless_jobs_and_binds_actor_id(tmp_path) -> None:
    module = _load_service_module(str(tmp_path))
    client = authorized_client(module.app, tenant_id="tenant-a")

    # 1. Unauthenticated POST /api/policy-learning/jobs creates status='proposed' jobs.
    # Even if a caller passes matching policy_id and training_approved=True, it must NOT count as approved.
    prop_resp = client.post(
        "/api/policy-learning/jobs",
        json={
            "policy_id": "trader-imitation-approval:tenant-b:victim:s1",
            "objective": "attempt unauthorized training approval via proposed job",
            "constraints": {
                "tenant_id": "tenant-b",
                "user_id": "victim",
                "strategy_id": "s1",
                "training_approved": True,
            },
        },
    )
    assert prop_resp.status_code == 201, prop_resp.text
    assert prop_resp.json()["status"] == "proposed"
    assert (
        module.get_trader_training_approval(
            module.store,
            tenant_id="tenant-b",
            user_id="victim",
            strategy_id="s1",
        )
        is None
    )

    # 2. Direct store jobs with status proposed/dispatched or missing tenant/user/strategy constraints must yield None.
    module.store.put_job(
        {
            "job_id": "plj-repro-proposed-tenantless",
            "policy_id": "any-policy",
            "status": "proposed",
            "constraints": {"training_approved": True},
        }
    )
    module.store.put_job(
        {
            "job_id": "plj-repro-dispatched",
            "policy_id": "trader-imitation-approval:tenant-b:victim:s1",
            "status": "dispatched",
            "constraints": {
                "tenant_id": "tenant-b",
                "user_id": "victim",
                "strategy_id": "s1",
                "training_approved": True,
            },
        }
    )
    module.store.put_job(
        {
            "job_id": "plj-repro-approved-missing-tenant",
            "policy_id": "trader-imitation-approval:tenant-b:victim:s1",
            "status": "approved",
            "constraints": {
                "user_id": "victim",
                "strategy_id": "s1",
                "training_approved": True,
            },
        }
    )
    module.store.put_job(
        {
            "job_id": "plj-repro-approved-missing-user",
            "policy_id": "trader-imitation-approval:tenant-b:victim:s1",
            "status": "approved",
            "constraints": {
                "tenant_id": "tenant-b",
                "strategy_id": "s1",
                "training_approved": True,
            },
        }
    )
    module.store.put_job(
        {
            "job_id": "plj-repro-approved-other-tenant",
            "policy_id": "trader-imitation-approval:tenant-a:*:*",
            "status": "approved",
            "constraints": {
                "tenant_id": "tenant-a",
                "user_id": "*",
                "strategy_id": "*",
                "training_approved": True,
            },
        }
    )
    assert (
        module.get_trader_training_approval(
            module.store,
            tenant_id="tenant-b",
            user_id="victim",
            strategy_id="s1",
        )
        is None
    )

    # 3. Only an explicitly approved job with tenant_id, user_id, and strategy_id present and matching succeeds.
    module.store.put_job(
        {
            "job_id": "plj-explicit-approved-tenant-b",
            "policy_id": "trader-imitation-approval:tenant-b:victim:s1",
            "status": "approved",
            "approved_by": "approval-authority",
            "constraints": {
                "tenant_id": "tenant-b",
                "user_id": "victim",
                "strategy_id": "s1",
                "training_approved": True,
            },
        }
    )
    approved_b = module.get_trader_training_approval(
        module.store,
        tenant_id="tenant-b",
        user_id="victim",
        strategy_id="s1",
    )
    assert approved_b is not None
    assert approved_b["approval_id"] == "plj-explicit-approved-tenant-b"
    assert (
        module.get_trader_training_approval(
            module.store,
            tenant_id="tenant-b",
            user_id="other-trader",
            strategy_id="s1",
        )
        is None
    )

    # 4. POST /api/policy-learning/trader-imitation/approvals binds approved_by to authority.actor_id,
    # ignoring any self-asserted approved_by in the request body.
    approve_resp = client.post(
        "/api/policy-learning/trader-imitation/approvals",
        json={
            "tenant_id": "tenant-a",
            "user_id": "trader-lin",
            "strategy_id": "strat-twse-1",
            "approved_by": "spoofed-self-asserted-approver",
        },
    )
    assert approve_resp.status_code == 201, approve_resp.text
    approval_payload = approve_resp.json()["approval"]
    assert approval_payload["approved_by"] == "policy-learning-service"
    assert approval_payload["experiment_approval"]["approved_by"] == "policy-learning-service"

    # 5. Approvals and decision-evidence records are persisted in durable job storage and survive restart.
    module.store.record_decision_evidence_version(_make_decision_evidence_version(1))
    restarted_store = module.PolicyLearningStore(tmp_path)
    persisted_approval = module.get_trader_training_approval(
        restarted_store,
        tenant_id="tenant-a",
        user_id="trader-lin",
        strategy_id="strat-twse-1",
    )
    assert persisted_approval is not None
    assert persisted_approval["approved_by"] == "policy-learning-service"
    persisted_evidence = restarted_store.list_decision_evidence_records(
        tenant_id="tenant-a",
        user_id="trader-lin",
    )
    assert len(persisted_evidence) == 1
    assert persisted_evidence[0]["evidence_id"] == "ev-tenant-a-trader-lin-strat-twse-1-001"


def test_dead_learning_imitation_service_not_built_in_cloudbuild() -> None:
    cloudbuild_text = (REPO_ROOT / "cloudbuild.yaml").read_text(encoding="utf-8")
    assert 'DOCKERFILES[imitation-worker]="services/learning/imitation/Dockerfile"' not in cloudbuild_text
    readme_text = (REPO_ROOT / "services/learning/imitation/README.md").read_text(encoding="utf-8")
    assert "NOT RUN AS A SERVICE" in readme_text.upper()
