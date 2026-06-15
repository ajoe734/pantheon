#!/usr/bin/env python3
"""E2E telemetry-coverage verifier: every active runtime must report telemetry.

An active runtime with no telemetry runtime_summary is running blind — its fills
and heartbeats are not reaching the projection, so the operator/reconciliation
view has no signal for it. This asserts that every ACTIVE runtime-binding has a
resolvable telemetry summary.

Failure semantics (CI-safe):
  * FAIL (exit 1) on any active runtime without a telemetry summary.
  * REPORT (exit 0) coverage otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_telemetry_coverage.py
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
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # noqa: BLE001
        return None, None


def _items(payload):
    if isinstance(payload, dict):
        for k in ("items", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def has_summary(code, payload) -> bool:
    if code != 200 or not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, dict):
        return False
    # a real summary exposes at least a trade count, a heartbeat, or an artifact id
    return (
        data.get("total_trades") is not None
        or bool(data.get("last_heartbeat_at"))
        or bool(data.get("artifact_id"))
    )


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    active = [b for b in _items(_get(base, token, ctx, "/bff/runtimes")[1] or {})
              if str(b.get("status") or "").lower() == "active"]
    missing = []
    for b in active:
        rid = b.get("runtime_id")
        code, payload = _get(base, token, ctx, f"/api/v1/telemetry/{rid}/summary")
        if not has_summary(code, payload):
            missing.append(f"{rid} (code={code})")

    print(f"== telemetry coverage over {len(active)} active runtimes ==")
    print(f"  with summary: {len(active) - len(missing)}  missing: {len(missing)}")
    if missing:
        print(f"\nFAIL: {len(missing)} active runtime(s) running without a telemetry summary:")
        for m in missing[:20]:
            print(f"   {m}")
        return 1
    print("\nOK: every active runtime reports a telemetry summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
