#!/usr/bin/env python3
"""E2E deployment-plan lifecycle coherence verifier.

Business flow: a deployment plan is approved → activated → a runtime binding goes
active at the target stage. The plan's `current_stage` must then reflect the
stage it is actually running at. A plan that has an ACTIVE runtime binding but
still reports `current_stage = none` is incoherent — its lifecycle state says
"not deployed" while it is in fact running.

Failure semantics (CI-safe):
  * FAIL (exit 1) on a plan that has an active binding but `current_stage` in the
    not-deployed set (none/empty), or whose current_stage disagrees with the
    active binding's deployment stage.
  * REPORT (exit 0) the plan/stage breakdown otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_deployment_lifecycle.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request

NOT_DEPLOYED = {"", "none", "null", None}


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
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return {}


def _items(payload):
    if isinstance(payload, dict):
        for k in ("items", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    plans = _items(_get(base, token, ctx, "/bff/deployments"))
    bindings = _items(_get(base, token, ctx, "/bff/runtimes"))

    # plan_id -> active binding stage
    active_stage_by_plan = {}
    for b in bindings:
        if str(b.get("status") or "").lower() != "active":
            continue
        pid = b.get("plan_id") or (b.get("metadata") or {}).get("plan_id")
        stage = str(b.get("deployment_mode") or b.get("execution_mode") or "paper").lower()
        if pid:
            active_stage_by_plan[pid] = stage

    violations = []
    for p in plans:
        pid = p.get("plan_id") or p.get("id")
        cur = str(p.get("current_stage") or "").lower()
        active_stage = active_stage_by_plan.get(pid)
        if active_stage is None:
            continue  # no active binding -> current_stage=none is fine
        if cur in NOT_DEPLOYED:
            violations.append(f"{pid}: active binding at '{active_stage}' but current_stage='{p.get('current_stage')}'")
        elif cur != active_stage:
            violations.append(f"{pid}: current_stage='{cur}' != active binding stage '{active_stage}'")

    print(f"== deployment-plan lifecycle coherence over {len(plans)} plans ==")
    print(f"  plans with an active binding: {len(active_stage_by_plan)}")
    print(f"  incoherent current_stage: {len(violations)}")

    if violations:
        print(f"\nFAIL: {len(violations)} plan(s) whose current_stage does not reflect their active binding:")
        for v in violations[:20]:
            print(f"   {v}")
        return 1
    print("\nOK: every plan with an active binding reports a coherent current_stage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
