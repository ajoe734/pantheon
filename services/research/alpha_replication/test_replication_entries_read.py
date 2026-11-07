"""Owner route tests for GET /api/research-orchestrator/alpha-replication/entries.

Covers ALPHA-FACTORY-REPLICATION-READ-20261104:
- Joins ReplicationAdmissionStore.list_admissions with AlphaReplicationQueue.list_all
  on (tenant_id, strategy_spec_id), including tw-official strategy_spec_id values
  containing colons in the join key.
- Returns every admission of the authenticated tenant, preservingqueue_status=null
  and null queue fields when an admission has not yet been enqueued.
- Enforces verified caller authentication and strict two-tenant isolation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict
from unittest import mock

from fastapi.testclient import TestClient

from services.research.alpha_replication.admission import ReplicationAdmissionStore
from services.research.alpha_replication.queue import AlphaReplicationQueue

REPO_ROOT = Path(__file__).resolve().parents[3]
JWT_SECRET = "alpha-replication-entries-test-secret-0123456789"

HOSTED_TW_OFFICIAL_SPEC_ID = (
    "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-da7cb8297afb"
)
HOSTED_TW_OFFICIAL_STRATEGY_ID = (
    "strat-tw-official:tw_price_daily:TWSE:2330:e4b598d99f084c19-60f9c8a78715"
)
VALID_CHECKSUM = "sha256:" + ("e" * 64)


def _hs256_bearer(tenant_id: str, *, sub: str = "pantheon-dev-operator", secret: str = JWT_SECRET) -> str:
    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    payload = _b64(
        json.dumps(
            {
                "sub": sub,
                "roles": ["operator", "reviewer"],
                "tenant_id": tenant_id,
            }
        ).encode("utf-8")
    )
    sig = _b64(hmac.new(secret.encode("utf-8"), f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest())
    return f"Bearer {header}.{payload}.{sig}"


def _tenant_headers(tenant_id: str) -> Dict[str, str]:
    return {
        "Authorization": _hs256_bearer(tenant_id),
        "X-Tenant-Id": tenant_id,
    }


def _load_research_app(tmp_path: Path, unique: str) -> tuple[Any, TestClient]:
    research_dir = tmp_path / f"research-{unique}"
    alpha_dir = tmp_path / f"alpha-{unique}"
    env = {
        "RESEARCH_ORCHESTRATOR_DATA_DIR": str(research_dir),
        "ALPHA_REPLICATION_DATA_DIR": str(alpha_dir),
        "PANTHEON_RESEARCH_AUTH_MODE": "strict",
        "PANTHEON_RESEARCH_JWT_SECRET": JWT_SECRET,
    }
    module_name = f"research_main_entries_{unique}"
    with mock.patch.dict(os.environ, env):
        spec = importlib.util.spec_from_file_location(
            module_name, REPO_ROOT / "services" / "research" / "main.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module, TestClient(module.app)


def _write_admissions_and_queue(
    alpha_dir: Path,
    *,
    admissions: Dict[str, Dict[str, Any]],
    queue_entries: Dict[str, Dict[str, Any]],
) -> None:
    alpha_dir.mkdir(parents=True, exist_ok=True)
    (alpha_dir / "admissions.json").write_text(
        json.dumps(admissions, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (alpha_dir / "queue.json").write_text(
        json.dumps(queue_entries, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def test_entries_route_joins_hosted_colon_spec_and_preserves_unqueued_admissions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_RESEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", JWT_SECRET)

    module, client = _load_research_app(tmp_path, "join_shape")
    alpha_dir = Path(module.ALPHA_REPLICATION_DATA_DIR)

    completed_key = f"tenant-dev:{HOSTED_TW_OFFICIAL_SPEC_ID}"
    pending_spec_id = "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2317:99aabbccddee0011-112233445566"
    pending_key = f"tenant-dev:{pending_spec_id}"
    unqueued_spec_id = "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2454:778899aabbccdde0-ffeeddccbbaa"
    unqueued_key = f"tenant-dev:{unqueued_spec_id}"

    admissions = {
        completed_key: {
            "admission_id": "adm-5dc7be51a48996e4",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": HOSTED_TW_OFFICIAL_SPEC_ID,
            "strategy_id": HOSTED_TW_OFFICIAL_STRATEGY_ID,
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2330-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-14T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "status": "admitted",
            "created_at": "2026-08-14T01:05:00Z",
        },
        pending_key: {
            "admission_id": "adm-pending2317",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": pending_spec_id,
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2317:99aabbccddee0011-0001",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2317-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-15T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "status": "admitted",
            "created_at": "2026-08-15T01:05:00Z",
        },
        unqueued_key: {
            "admission_id": "adm-unqueued2454",
            "tenant_id": "tenant-dev",
            "strategy_spec_id": unqueued_spec_id,
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2454:778899aabbccdde0-0002",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2454-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-16T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "status": "admitted",
            "created_at": "2026-08-16T01:05:00Z",
        },
    }

    queue_entries = {
        completed_key: {
            "strategy_spec_id": HOSTED_TW_OFFICIAL_SPEC_ID,
            "tenant_id": "tenant-dev",
            "strategy_id": HOSTED_TW_OFFICIAL_STRATEGY_ID,
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2330-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-14T01:00:00Z",
            "enqueued_at": "2026-08-14T01:10:00Z",
            "enqueued_by": "alpha-replication-controller",
            "status": "completed",
            "attempt_count": 1,
            "max_attempts": 1,
            "experiment_task_id": "etask-alpha-6ef7a5a6d3a4ae4b",
            "experiment_run_ids": ["run-exp-2330-001"],
            "authority_task_id": "rtask-20261015-002",
            "authority_run_ids": ["run-auth-2330-001"],
            "last_revalidation_at": "2026-10-15T08:30:00Z",
        },
        pending_key: {
            "strategy_spec_id": pending_spec_id,
            "tenant_id": "tenant-dev",
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2317:99aabbccddee0011-0001",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-tw-2317-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-15T01:00:00Z",
            "enqueued_at": "2026-08-15T01:10:00Z",
            "enqueued_by": "alpha-replication-controller",
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": 3,
            "experiment_task_id": None,
            "experiment_run_ids": [],
            "authority_task_id": None,
            "authority_run_ids": None,
            "last_revalidation_at": None,
        },
    }

    _write_admissions_and_queue(alpha_dir, admissions=admissions, queue_entries=queue_entries)

    resp = client.get(
        "/api/research-orchestrator/alpha-replication/entries",
        headers=_tenant_headers("tenant-dev"),
    )
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    assert len(items) == 3

    by_spec = {item["strategy_spec_id"]: item for item in items}

    completed = by_spec[HOSTED_TW_OFFICIAL_SPEC_ID]
    assert completed["admission_id"] == "adm-5dc7be51a48996e4"
    assert completed["strategy_id"] == HOSTED_TW_OFFICIAL_STRATEGY_ID
    assert completed["status"] == "admitted"
    assert completed["admission_status"] == "admitted"
    assert completed["queue_status"] == "completed"
    assert completed["attempt_count"] == 1
    assert completed["experiment_task_id"] == "etask-alpha-6ef7a5a6d3a4ae4b"
    assert completed["experiment_run_ids"] == ["run-exp-2330-001"]
    assert completed["authority_task_id"] == "rtask-20261015-002"
    assert completed["authority_run_ids"] == ["run-auth-2330-001"]
    assert completed["last_revalidation_at"] == "2026-10-15T08:30:00Z"
    assert completed["approver"] == "pantheon-dev-approver"
    assert completed["approved_at"] == "2026-08-14T01:00:00Z"
    for invented_field in ("sharpe", "sharpe_ratio", "metrics", "risk", "win_rate"):
        assert invented_field not in completed

    pending = by_spec[pending_spec_id]
    assert pending["admission_status"] == "admitted"
    assert pending["queue_status"] == "pending"
    assert pending["attempt_count"] == 0
    assert pending["experiment_task_id"] is None
    assert pending["experiment_run_ids"] == []
    assert pending["authority_task_id"] is None
    assert pending["last_revalidation_at"] is None

    unqueued = by_spec[unqueued_spec_id]
    assert unqueued["admission_status"] == "admitted"
    assert unqueued["queue_status"] is None
    assert unqueued["attempt_count"] is None
    assert unqueued["experiment_task_id"] is None
    assert unqueued["experiment_run_ids"] is None
    assert unqueued["authority_task_id"] is None
    assert unqueued["last_revalidation_at"] is None


def test_entries_route_enforces_two_tenant_isolation_and_auth_boundary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("PANTHEON_RESEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", JWT_SECRET)

    module, client = _load_research_app(tmp_path, "tenant_isolation")
    alpha_dir = Path(module.ALPHA_REPLICATION_DATA_DIR)
    adm_store = ReplicationAdmissionStore(alpha_dir)
    queue_store = AlphaReplicationQueue(alpha_dir)

    adm_store.create_admission(
        {
            "tenant_id": "tenant-dev",
            "strategy_spec_id": HOSTED_TW_OFFICIAL_SPEC_ID,
            "strategy_id": HOSTED_TW_OFFICIAL_STRATEGY_ID,
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-dev-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-14T01:00:00Z",
            "mode": "initial",
        }
    )
    queue_store.enqueue(
        {
            "tenant_id": "tenant-dev",
            "strategy_spec_id": HOSTED_TW_OFFICIAL_SPEC_ID,
            "strategy_id": HOSTED_TW_OFFICIAL_STRATEGY_ID,
            "spec_version": "1.0.0",
            "artifact_state": "approved",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-dev-001",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-08-14T01:00:00Z",
        },
        enqueued_by="alpha-replication-controller",
    )

    other_spec_id = "reg-strategy-spec-tw-official:tw_price_daily:TWSE:2303:other-tenant-spec"
    adm_store.create_admission(
        {
            "tenant_id": "tenant-other",
            "strategy_spec_id": other_spec_id,
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2303:other-tenant",
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-other-001",
            "approver": "other-approver",
            "approved_at": "2026-08-14T02:00:00Z",
            "mode": "initial",
        }
    )
    queue_store.enqueue(
        {
            "tenant_id": "tenant-other",
            "strategy_spec_id": other_spec_id,
            "strategy_id": "strat-tw-official:tw_price_daily:TWSE:2303:other-tenant",
            "spec_version": "1.0.0",
            "artifact_state": "approved",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-other-001",
            "approver": "other-approver",
            "approved_at": "2026-08-14T02:00:00Z",
        },
        enqueued_by="alpha-replication-controller",
    )

    dev_resp = client.get(
        "/api/research-orchestrator/alpha-replication/entries",
        headers=_tenant_headers("tenant-dev"),
    )
    assert dev_resp.status_code == 200
    dev_items = dev_resp.json()
    assert [item["strategy_spec_id"] for item in dev_items] == [HOSTED_TW_OFFICIAL_SPEC_ID]
    assert all(item["tenant_id"] == "tenant-dev" for item in dev_items)

    other_resp = client.get(
        "/api/research-orchestrator/alpha-replication/entries",
        headers=_tenant_headers("tenant-other"),
    )
    assert other_resp.status_code == 200
    other_items = other_resp.json()
    assert [item["strategy_spec_id"] for item in other_items] == [other_spec_id]
    assert all(item["tenant_id"] == "tenant-other" for item in other_items)

    # Cross-tenant header spoofing is rejected with 403
    spoof_resp = client.get(
        "/api/research-orchestrator/alpha-replication/entries",
        headers={
            "Authorization": _hs256_bearer("tenant-dev"),
            "X-Tenant-Id": "tenant-other",
        },
    )
    assert spoof_resp.status_code == 403

    # Anonymous or missing tenant header is rejected
    assert client.get("/api/research-orchestrator/alpha-replication/entries").status_code == 401
    assert (
        client.get(
            "/api/research-orchestrator/alpha-replication/entries",
            headers={"X-Tenant-Id": "tenant-dev"},
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/research-orchestrator/alpha-replication/entries",
            headers={"Authorization": _hs256_bearer("tenant-dev")},
        ).status_code
        == 400
    )


def test_entries_route_returns_failure_reason_and_replay_facts_from_real_queue_shapes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """BFF-EVO-ALPHA-PAGE-TRUTH-20261107: last_error comes from failure_reason,
    and replay_count / last_replayed_at / revalidation_count pass through unchanged."""
    monkeypatch.setenv("PANTHEON_RESEARCH_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_RESEARCH_JWT_SECRET", JWT_SECRET)

    module, client = _load_research_app(tmp_path, "truth_fields")
    alpha_dir = Path(module.ALPHA_REPLICATION_DATA_DIR)

    replayed_spec = "reg-strategy-spec-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8"
    failed_spec = "reg-strategy-spec-failed-example"
    plain_spec = "reg-strategy-spec-spy-anchor"

    def _admission(spec_id: str, strategy_id: str, admission_id: str) -> Dict[str, Any]:
        return {
            "admission_id": admission_id,
            "tenant_id": "tenant-dev",
            "strategy_spec_id": spec_id,
            "strategy_id": strategy_id,
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-1",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-10-10T01:00:00Z",
            "requested_by": "pantheon-dev-operator",
            "review_ref": None,
            "mode": "initial",
            "status": "admitted",
            "created_at": "2026-10-10T01:05:00Z",
        }

    def _queue(spec_id: str, strategy_id: str, **extra: Any) -> Dict[str, Any]:
        record = {
            "strategy_spec_id": spec_id,
            "tenant_id": "tenant-dev",
            "strategy_id": strategy_id,
            "spec_version": "1.0.0",
            "checksum": VALID_CHECKSUM,
            "approval_decision_id": "approval-1",
            "approver": "pantheon-dev-approver",
            "approved_at": "2026-10-10T01:00:00Z",
            "enqueued_at": "2026-10-10T01:10:00Z",
            "enqueued_by": "alpha-replication-controller",
            "max_attempts": 3,
            "experiment_task_id": "etask-1",
            "experiment_run_ids": ["run-1"],
            "authority_task_id": "rtask-1",
            "authority_run_ids": ["run-a"],
            "lifecycle_state": "approved",
            "claim_generation": 1,
            "reclaimed_count": 0,
            "consumed_replay_ids": [],
        }
        record.update(extra)
        return record

    admissions = {
        f"tenant-dev:{replayed_spec}": _admission(replayed_spec, "strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8", "adm-replayed"),
        f"tenant-dev:{failed_spec}": _admission(failed_spec, "strat-failed-example", "adm-failed"),
        f"tenant-dev:{plain_spec}": _admission(plain_spec, "strat-src-spy-anchor", "adm-plain"),
    }
    queue_entries = {
        f"tenant-dev:{replayed_spec}": _queue(
            replayed_spec,
            "strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8",
            status="completed",
            attempt_count=0,
            replay_count=1,
            revalidation_count=1,
            last_replayed_at="2026-10-24T02:28:22Z",
            last_replay_id="replay-1",
            last_replay_reason="operator replay",
            last_replayed_by="pantheon-dev-operator",
            failure_reason=None,
            last_revalidation_at="2026-10-24T02:30:00Z",
        ),
        f"tenant-dev:{failed_spec}": _queue(
            failed_spec,
            "strat-failed-example",
            status="failed",
            attempt_count=2,
            failure_reason="experiment run timed out",
            last_revalidation_at=None,
        ),
        f"tenant-dev:{plain_spec}": _queue(
            plain_spec,
            "strat-src-spy-anchor",
            status="completed",
            attempt_count=0,
            replay_count=0,
            revalidation_count=1,
            last_replayed_at=None,
            failure_reason=None,
            last_revalidation_at="2026-10-10T02:00:00Z",
        ),
    }
    _write_admissions_and_queue(alpha_dir, admissions=admissions, queue_entries=queue_entries)

    resp = client.get(
        "/api/research-orchestrator/alpha-replication/entries",
        headers=_tenant_headers("tenant-dev"),
    )
    assert resp.status_code == 200
    by_spec = {item["strategy_spec_id"]: item for item in resp.json()}

    replayed = by_spec[replayed_spec]
    assert replayed["replay_count"] == 1
    assert replayed["last_replayed_at"] == "2026-10-24T02:28:22Z"
    assert replayed["revalidation_count"] == 1
    assert replayed["attempt_count"] == 0
    assert replayed["last_error"] is None

    failed = by_spec[failed_spec]
    assert failed["last_error"] == "experiment run timed out"
    assert failed["attempt_count"] == 2
    assert failed["replay_count"] is None
    assert failed["last_replayed_at"] is None
    assert failed["revalidation_count"] is None

    plain = by_spec[plain_spec]
    assert plain["replay_count"] == 0
    assert plain["last_replayed_at"] is None
    assert plain["revalidation_count"] == 1
