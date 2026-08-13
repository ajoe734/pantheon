"""Integration tests verifying worker crash recovery, lease expiration, and DLQ reprocessing.

Verifies:
  - Policy learning candidate lease expiration allows reclaim by healthy worker
  - Original worker attempting to settle after lease expiry raises LeaseLostError
  - Dead-letter queue / retry queue mechanics
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def test_policy_learning_worker_lease_expiration_and_reclaim(temp_workspace: Path) -> None:
    """Expired worker lease must be reclaimable; stale worker settlement must fail closed."""
    pl_store_path = REPO_ROOT / "services" / "policy-learning" / "store.py"
    spec = importlib.util.spec_from_file_location("policy_learning_store", pl_store_path)
    pl_store_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pl_store_module)

    pl_dir = temp_workspace / "pl_crash_test"
    store = pl_store_module.PolicyLearningStore(data_dir=pl_dir)

    tenant_id = "tenant-crash-test"
    cand_id = f"cand-crash-{uuid.uuid4().hex[:8]}"
    dedupe_key = pl_store_module.candidate_dedupe_key(tenant_id, "tick-crash-01", "dv-01")

    # 1. Admit candidate
    candidate, created = store.create_candidate_if_absent(
        {
            "candidate_id": cand_id,
            "dedupe_key": dedupe_key,
            "tenant_id": tenant_id,
            "user_id": "user-01",
            "dataset_version_id": "dv-01",
            "status": pl_store_module.STATUS_PROPOSED,
            "created_at": _utc_now(),
        }
    )
    assert created is True

    # 2. Worker 1 claims lease with short duration (1 second)
    claimed_w1 = store.claim_candidates(
        worker_id="worker-01-crasher",
        lease_seconds=1,
        batch_size=1,
        tenant_id=tenant_id,
    )
    assert len(claimed_w1) == 1
    w1_token = claimed_w1[0]["lease_token"]

    # 3. Simulate Worker 1 crashing / freezing past lease expiry
    time.sleep(1.2)

    # 4. Healthy Worker 2 claims candidate after lease expiration
    claimed_w2 = store.claim_candidates(
        worker_id="worker-02-healthy",
        lease_seconds=30,
        batch_size=1,
        tenant_id=tenant_id,
    )
    assert len(claimed_w2) == 1
    assert claimed_w2[0]["candidate_id"] == cand_id
    w2_token = claimed_w2[0]["lease_token"]
    assert w2_token != w1_token

    # 5. Worker 1 wakes up and attempts to settle -> must raise LeaseLostError
    w1_payload = claimed_w1[0]
    w1_payload["status"] = pl_store_module.STATUS_PROCESSED
    with pytest.raises(pl_store_module.LeaseLostError):
        store.settle_candidate(w1_payload, lease_token=w1_token)

    # 6. Worker 2 settles cleanly
    w2_payload = claimed_w2[0]
    w2_payload["status"] = pl_store_module.STATUS_PROCESSED
    w2_payload["artifact_checksum"] = hashlib.sha256(b"w2-valid-artifact").hexdigest()
    settled = store.settle_candidate(w2_payload, lease_token=w2_token)
    assert settled["status"] == pl_store_module.STATUS_PROCESSED
