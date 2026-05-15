#!/usr/bin/env python3
"""
Governance Service — HTTP smoke test.

Runs against a live server.  Set GOVERNANCE_URL to override the default.

Usage:
  python3 smoke_test.py                  # default localhost:8005
  GOVERNANCE_URL=http://host:8005 python3 smoke_test.py
"""
from __future__ import annotations

import os
import sys
import uuid

import httpx

BASE = os.getenv("GOVERNANCE_URL", "http://localhost:8005")
TIMEOUT = 10


def run() -> None:
    client = httpx.Client(base_url=BASE, timeout=TIMEOUT)
    decision_id = f"apv-smoke-{uuid.uuid4().hex[:6]}"
    target_id   = f"artifact-smoke-{uuid.uuid4().hex[:4]}"

    # 1. Health
    r = client.get("/health")
    assert r.status_code == 200, f"Health failed: {r.text}"
    print("✓ /health")

    # 2. Write-authority matrix
    r = client.get("/api/governance/write-authority")
    assert r.status_code == 200, f"Write-authority failed: {r.text}"
    assert "matrix" in r.json()
    print("✓ GET /api/governance/write-authority")

    # 3. Propose
    r = client.post("/api/governance/approvals", json={
        "decision_id":    decision_id,
        "target_type":    "registry_entry",
        "target_id":      target_id,
        "target_version": "v1.0",
        "risk_level":     "low",
    })
    assert r.status_code == 201, f"Propose failed: {r.text}"
    assert r.json()["decision_state"] == "proposed"
    print(f"✓ POST /api/governance/approvals → {decision_id}")

    # 4. Duplicate rejected
    r = client.post("/api/governance/approvals", json={
        "decision_id":    decision_id,
        "target_type":    "registry_entry",
        "target_id":      target_id,
        "target_version": "v1.0",
    })
    assert r.status_code == 409, f"Expected 409 for duplicate, got {r.status_code}"
    print("✓ Duplicate decision_id rejected with 409")

    # 5. Accept review
    r = client.post(f"/api/governance/approvals/{decision_id}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "smoke-reviewer-01",
    })
    assert r.status_code == 200, f"Accept review failed: {r.text}"
    assert r.json()["decision_state"] == "under_review"
    print(f"✓ POST /api/governance/approvals/{decision_id}/review")

    # 6. Record decision (actor_role must be authorized for low-risk decisions)
    r = client.post(f"/api/governance/approvals/{decision_id}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "Smoke-test approval — all checks passed",
        "actor_id":   "smoke-reviewer-01",
    })
    assert r.status_code == 200, f"Decide failed: {r.text}"
    body = r.json()
    assert body["decision_state"] == "decided"
    assert body["decision"]       == "approved"
    print(f"✓ POST /api/governance/approvals/{decision_id}/decide")

    # 7. Get single decision
    r = client.get(f"/api/governance/approvals/{decision_id}")
    assert r.status_code == 200
    assert r.json()["decision_id"] == decision_id
    print(f"✓ GET /api/governance/approvals/{decision_id}")

    # 8. Latest-approved lookup
    r = client.get("/api/governance/approvals/latest-approved", params={
        "target_type": "registry_entry",
        "target_id":   target_id,
    })
    assert r.status_code == 200, f"Latest-approved failed: {r.text}"
    result = r.json()
    assert result is not None
    assert result["decision_id"] == decision_id
    print("✓ GET /api/governance/approvals/latest-approved")

    # 9. List with filter
    r = client.get("/api/governance/approvals", params={"target_id": target_id})
    assert r.status_code == 200
    assert len(r.json()) >= 1
    print("✓ GET /api/governance/approvals?target_id=...")

    # 10. Audit log
    r = client.get("/api/governance/audit", params={"decision_id": decision_id})
    assert r.status_code == 200
    events = r.json()
    assert len(events) >= 1
    event_types = {e["event_type"] for e in events}
    assert "approval_decision_created" in event_types
    print(f"✓ GET /api/governance/audit ({len(events)} events)")

    # 11. Revoke route — full lifecycle on a separate decision
    revoke_did = f"apv-smoke-rev-{uuid.uuid4().hex[:6]}"
    revoke_target = f"artifact-rev-{uuid.uuid4().hex[:4]}"

    client.post("/api/governance/approvals", json={
        "decision_id":    revoke_did,
        "target_type":    "registry_entry",
        "target_id":      revoke_target,
        "target_version": "v1.0",
        "risk_level":     "medium",
    })
    client.post(f"/api/governance/approvals/{revoke_did}/review", json={
        "actor_role": "governance_reviewer",
        "actor_id":   "smoke-rev-actor",
    })
    client.post(f"/api/governance/approvals/{revoke_did}/decide", json={
        "actor_role": "governance_reviewer",
        "outcome":    "approved",
        "rationale":  "Smoke revoke test — approved to be revoked",
        "actor_id":   "smoke-rev-actor",
    })

    # Unauthorized revoke role should be rejected
    r = client.post(f"/api/governance/approvals/{revoke_did}/revoke", json={
        "actor_role": "governance_reviewer",  # not in revoke_roles
        "actor_id":   "smoke-rev-actor",
    })
    assert r.status_code == 400, f"Expected 400 for unauthorized revoke role, got {r.status_code}"
    print("✓ Unauthorized revoke role → 400")

    # Authorized revoke
    r = client.post(f"/api/governance/approvals/{revoke_did}/revoke", json={
        "actor_role": "risk_owner",
        "actor_id":   "smoke-risk-owner",
    })
    assert r.status_code == 200, f"Revoke failed: {r.text}"
    assert r.json()["decision_state"] == "revoked"
    print(f"✓ POST /api/governance/approvals/{revoke_did}/revoke")

    # 12. Unauthorized decide role rejected
    bad_did = f"apv-smoke-bad-{uuid.uuid4().hex[:4]}"
    client.post("/api/governance/approvals", json={
        "decision_id":    bad_did,
        "target_type":    "registry_entry",
        "target_id":      "art-unauth",
        "target_version": "v1",
        "risk_level":     "high",
    })
    r = client.post(f"/api/governance/approvals/{bad_did}/review", json={
        "actor_role": "governance_reviewer",  # not authorized for high
        "actor_id":   "unauth-1",
    })
    assert r.status_code == 400, f"Expected 400 for unauthorized role, got {r.status_code}"
    print("✓ Unauthorized role for high-risk decision → 400")

    print("\n✓ All smoke tests passed")


if __name__ == "__main__":
    try:
        run()
    except AssertionError as exc:
        print(f"\n✗ Smoke test failed: {exc}", file=sys.stderr)
        sys.exit(1)
