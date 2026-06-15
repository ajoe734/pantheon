#!/usr/bin/env python3
"""E2E consistency check: telemetry runtime-summary vs operator paper-live-drift.

Business flow under test (right half of the OODA loop):
    paper fill -> telemetry ingest -> runtime_summary projection ->
    reconciliation/drift -> operator paper-live-drift view

The telemetry runtime-summary correctly reflects executed paper trades
(positions, total_trades, pnl). The operator paper-live-drift view's
``observed_state`` must reflect the same reality — otherwise the drift/
reconciliation surface is blind to real executions and cannot detect paper-vs-
live drift.

This verifier cross-checks, for every runtime, the telemetry summary against the
paper-live-drift observed_state and flags runtimes where telemetry shows trades
(total_trades > 0) but the drift observed_state reports zero — a disconnect
between the live data path and the operator drift surface.

Failure semantics (CI-safe):
  * FAIL (exit 1) on any runtime where telemetry total_trades > 0 while the
    drift observed_state reports total_trades == 0 (hard disconnect).
  * REPORT (exit 0) per-runtime consistency otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_telemetry_drift_consistency.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request


def _ctx() -> ssl.SSLContext:
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


def _runtime_ids(base, token, ctx) -> list[str]:
    _, payload = _get(base, token, ctx, "/bff/runtimes")
    items = (payload or {}).get("items") or (payload or {}).get("data") or []
    return [it.get("runtime_id") or it.get("id") for it in items if it.get("runtime_id") or it.get("id")]


def telemetry_trades(base, token, ctx, rt):
    _, payload = _get(base, token, ctx, f"/api/v1/telemetry/{rt}/summary")
    d = (payload or {}).get("data") or {}
    return _num(d.get("total_trades")), _num(d.get("pnl"))


def drift_observed_trades(base, token, ctx, rt):
    code, payload = _get(base, token, ctx, f"/api/v1/operator/paper-live-drift/{rt}")
    if code != 200 or not isinstance(payload, dict):
        return None, None
    m = ((payload.get("observed_state") or {}).get("metrics") or {})
    return _num(m.get("total_trades")), _num(m.get("pnl"))


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    rts = _runtime_ids(base, token, ctx)
    if not rts:
        print("ERROR: no runtimes", file=sys.stderr)
        return 1

    disconnects = []
    traded = 0
    print(f"== telemetry vs paper-live-drift over {len(rts)} runtimes ==")
    for rt in rts:
        t_trades, t_pnl = telemetry_trades(base, token, ctx, rt)
        d_trades, d_pnl = drift_observed_trades(base, token, ctx, rt)
        if t_trades and t_trades > 0:
            traded += 1
            flag = ""
            if not d_trades:
                disconnects.append((rt, t_trades, t_pnl))
                flag = "  <-- DISCONNECT (drift observed=0)"
            print(f"  {rt}: telemetry trades={t_trades:g} pnl={t_pnl:g} | drift observed trades={d_trades:g}{flag}")

    print(f"\nruntimes with telemetry trades: {traded}; disconnects: {len(disconnects)}")
    if disconnects:
        print("\nFAIL: paper-live-drift observed_state is blind to real telemetry executions:")
        for rt, tt, tp in disconnects:
            print(f"   {rt}: telemetry shows {tt:g} trades / pnl {tp:g}, drift observed_state shows 0")
        return 1
    print("\nOK: paper-live-drift observed_state reflects telemetry executions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
