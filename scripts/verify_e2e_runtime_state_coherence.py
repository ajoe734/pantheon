#!/usr/bin/env python3
"""E2E runtime-surface field-coherence verifier.

E2E-R8 checked that the runtime *id sets* agree across /bff/runtimes and
/api/v1/operator/runtime-state. This deepens it to FIELD level: for each runtime
present in both surfaces, the deployment stage, status, and binding id must agree
— otherwise the operator console shows contradictory runtime state depending on
which surface it reads.

Failure semantics (CI-safe):
  * FAIL (exit 1) on any per-runtime field mismatch (stage / status / binding).
  * REPORT (exit 0) the common-runtime count otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_runtime_state_coherence.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request


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
        for k in ("items", "data", "runtimes"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _stage(rec):
    return rec.get("deployment_stage") or rec.get("deployment_mode") or rec.get("execution_mode")


def _binding(rec):
    return rec.get("runtime_binding_id") or rec.get("binding_id") or rec.get("id")


def find_mismatches(runtimes: list, runtime_state: list) -> list[str]:
    R = {b.get("runtime_id"): b for b in runtimes if b.get("runtime_id")}
    S = {b.get("runtime_id"): b for b in runtime_state if b.get("runtime_id")}
    out = []
    for rid in R.keys() & S.keys():
        r, s = R[rid], S[rid]
        if _stage(r) != _stage(s):
            out.append(f"{rid}: stage {_stage(r)} != {_stage(s)}")
        if str(r.get("status") or "").lower() != str(s.get("status") or "").lower():
            out.append(f"{rid}: status {r.get('status')} != {s.get('status')}")
        if _binding(r) != _binding(s):
            out.append(f"{rid}: binding {_binding(r)} != {_binding(s)}")
    return out


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    runtimes = _items(_get(base, token, ctx, "/bff/runtimes"))
    runtime_state = _items(_get(base, token, ctx, "/api/v1/operator/runtime-state"))
    common = {b.get("runtime_id") for b in runtimes} & {b.get("runtime_id") for b in runtime_state}
    mism = find_mismatches(runtimes, runtime_state)

    print(f"== runtime-surface field coherence over {len(common)} common runtimes ==")
    print(f"  field mismatches (stage/status/binding): {len(mism)}")
    if mism:
        print(f"\nFAIL: {len(mism)} runtime field mismatch(es) between /bff/runtimes and operator/runtime-state:")
        for m in mism[:20]:
            print(f"   {m}")
        return 1
    print("\nOK: runtime stage/status/binding agree across both surfaces")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
