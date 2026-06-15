#!/usr/bin/env python3
"""E2E governance/promotion-integrity verifier for deployment plans.

Business flow under test (governance ladder):
    deployment-plan -> approval decision -> stage promotion (paper -> canary ->
    live), each transition gated by an authorization/audit record.

Safety invariant: a plan that claims `status == "approved"` (or that references an
approval decision) MUST have a resolvable approval record. An "approved" plan
pointing at a non-existent approval decision is a governance/authorization
integrity violation — it asserts authorization that cannot be audited.

Additional reported invariants:
  * a plan whose `stage` is canary/live MUST reference a resolvable approval.
  * `current_stage` vs `stage` consistency (informational).

Failure semantics (CI-safe):
  * FAIL (exit 1) on a PHANTOM approval — a plan that is approved / promoted but
    whose approval_decision_id does not resolve (404/5xx or degraded envelope).
  * REPORT (exit 0) stage-consistency observations.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_promotion_governance.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

PROMOTED_STAGES = {"canary", "live"}


def _ctx():
    ctx = ssl.create_default_context()
    if os.environ.get("BFF_INSECURE", "1") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get(base, token, ctx, path):
    req = urllib.request.Request(base.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def _items(payload):
    if isinstance(payload, dict):
        for k in ("items", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _resolves(code, payload) -> bool:
    if code != 200 or not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if isinstance(data, dict):
        if str(data.get("status") or "").lower() == "degraded":
            return False
        rs = data.get("readSurface")
        if isinstance(rs, dict) and str(rs.get("status") or "").lower() == "unavailable":
            return False
    return True


def _approval_id(plan):
    aid = plan.get("approval_decision_id")
    if aid:
        return aid
    ref = plan.get("approval_ref")
    if isinstance(ref, dict):
        return ref.get("approval_decision_id")
    return None


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    plans = _items(_get(base, token, ctx, "/bff/deployments")[1])
    if not plans:
        print("ERROR: no deployment plans", file=sys.stderr)
        return 1

    phantom = []
    checked = 0
    promoted_no_approval = []
    for p in plans:
        pid = p.get("plan_id") or p.get("id")
        status = str(p.get("status") or "").lower()
        stage = str(p.get("stage") or p.get("deployment_stage") or "").lower()
        aid = _approval_id(p)
        needs_approval = status == "approved" or stage in PROMOTED_STAGES
        if not needs_approval:
            continue
        checked += 1
        if not aid:
            promoted_no_approval.append((pid, stage, status))
            continue
        code, payload = _get(base, token, ctx, f"/bff/approvals/{aid}")
        if not _resolves(code, payload):
            phantom.append((pid, aid, stage, status, code))

    print(f"== governance integrity over {len(plans)} deployment plans ({checked} need approval) ==")
    print(f"  phantom approvals (approved/promoted but approval missing): {len(phantom)}")
    print(f"  approved/promoted with no approval ref at all: {len(promoted_no_approval)}")

    for pid, stage, status in promoted_no_approval[:10]:
        print(f"   NO-REF  {pid} (stage={stage} status={status})")

    if phantom:
        print(f"\nFAIL: {len(phantom)} plan(s) claim approval against a non-existent approval decision:")
        for pid, aid, stage, status, code in phantom[:20]:
            print(f"   {pid}: status={status} stage={stage} approval={aid} -> {code}")
        return 1
    if promoted_no_approval:
        print(f"\nFAIL: {len(promoted_no_approval)} approved/promoted plan(s) carry no approval reference")
        return 1
    print("\nOK: every approved/promoted plan has a resolvable approval decision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
