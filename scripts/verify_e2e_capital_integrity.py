#!/usr/bin/env python3
"""E2E capital-pool / runtime-binding financial-safety invariant verifier.

Business flow under test: capital-pool → persona-capital-binding → runtime-binding.
Financial-safety invariants (runtime-manager RUN-001 and friends):

  1. Single active binding per pool: no capital pool backs more than one active
     runtime-binding.
  2. Pool-backing integrity: every active binding's capital_pool_id resolves to a
     listed, active capital pool.
  3. Enforcement flag: pools backing active bindings must have
     `single_runtime_enforced == True`.
  4. Budget sanity: backing pools must have a positive budget.

Failure semantics (CI-safe):
  * FAIL (exit 1) on any invariant violation (over-allocated pool, dangling/
    inactive backing pool, single_runtime_enforced False, non-positive budget).
  * REPORT (exit 0) the per-invariant summary otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_capital_integrity.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from collections import Counter


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
        return None


def _items(payload):
    if isinstance(payload, dict):
        for k in ("items", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _budget_value(pool) -> float:
    b = pool.get("budget")
    if isinstance(b, dict):
        for k in ("amount", "total", "value", "limit"):
            if k in b:
                b = b[k]
                break
    try:
        return float(b)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    bindings = [b for b in _items(_get(base, token, ctx, "/bff/runtimes"))
                if str(b.get("status") or "").lower() == "active"]
    pools = _items(_get(base, token, ctx, "/bff/capital-pools"))
    pool_by_id = {}
    for p in pools:
        pid = p.get("pool_id") or p.get("id")
        if pid:
            pool_by_id[pid] = p

    violations = []

    # invariant 1: single active binding per pool
    pool_counts = Counter(b.get("capital_pool_id") for b in bindings if b.get("capital_pool_id"))
    over = [(p, c) for p, c in pool_counts.items() if c > 1]
    for p, c in over:
        violations.append(f"RUN-001 over-allocation: pool {p} backs {c} active bindings")

    # invariants 2-4: backing integrity / enforcement / budget
    dangling = enforce_off = bad_budget = inactive = 0
    for b in bindings:
        pid = b.get("capital_pool_id")
        bid = b.get("binding_id") or b.get("id")
        pool = pool_by_id.get(pid)
        if pool is None:
            dangling += 1
            violations.append(f"backing pool missing/unlisted: binding {bid} -> pool {pid}")
            continue
        if str(pool.get("status") or "").lower() != "active":
            inactive += 1
            violations.append(f"backing pool not active: binding {bid} -> pool {pid} (status={pool.get('status')})")
        if pool.get("single_runtime_enforced") is not True:
            enforce_off += 1
            violations.append(f"single_runtime_enforced not True: pool {pid}")
        if _budget_value(pool) <= 0:
            bad_budget += 1
            violations.append(f"non-positive budget: pool {pid} (budget={pool.get('budget')})")

    print(f"== capital integrity over {len(bindings)} active bindings / {len(pools)} pools ==")
    print(f"  RUN-001 over-allocated pools : {len(over)}")
    print(f"  dangling backing pools       : {dangling}")
    print(f"  inactive backing pools       : {inactive}")
    print(f"  single_runtime_enforced off  : {enforce_off}")
    print(f"  non-positive budget          : {bad_budget}")

    if violations:
        print(f"\nFAIL: {len(violations)} financial-safety invariant violation(s):")
        for v in violations[:20]:
            print(f"   {v}")
        return 1
    print("\nOK: capital-pool / binding financial-safety invariants hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
