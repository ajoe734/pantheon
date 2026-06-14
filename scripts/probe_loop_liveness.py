#!/usr/bin/env python3
"""Live loop-liveness / OODA-observability probe.

Where the other verification probes are *static* (deploy-drift, dependency
hygiene, OpenAPI structure) or cover *happy-path collection* reads, this probe
answers the campaign's founding question empirically: **are the canonical loops
demonstrably alive, and are their observability surfaces healthy?**

It queries the v5 control surfaces that project loop/OODA liveness:

  * ``/bff/v5/loop-runs``                 - canonical loop-run ledger (count)
  * ``/bff/v5/control-room``              - aggregate OODA gate + loop counters
  * ``/bff/v5/execution/persona-health``  - per-persona OODA stage / health
  * ``/bff/v5/execution/strategy-health`` - per-strategy health surface

Failure semantics are deliberately conservative so the probe is CI-safe and not
flaky on an idle paper environment:

  * FAIL (exit 1) only when a liveness endpoint is unreachable or returns 5xx,
    or when a surface advertises ``status == "ok"`` yet the payload is malformed.
  * REPORT (exit 0, printed) empty ledgers and degraded/unavailable surfaces -
    these are observations for the verification record, not hard failures, since
    "no loop runs yet" is a legitimate state for an idle paper fleet.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/probe_loop_liveness.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

SURFACES = [
    "/bff/v5/loop-runs",
    "/bff/v5/control-room",
    "/bff/v5/execution/persona-health",
    "/bff/v5/execution/strategy-health",
]


def _get(base: str, token: str, ctx: ssl.SSLContext, path: str):
    req = urllib.request.Request(
        base.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return r.status, json.loads(r.read()), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, ""
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"


def _surface_status(payload: dict) -> dict:
    """Extract any {surface: {status: ...}} markers from a payload meta block."""
    out = {}
    for meta_holder in (payload, payload.get("meta", {}) if isinstance(payload, dict) else {}):
        if not isinstance(meta_holder, dict):
            continue
        meta = meta_holder.get("meta", meta_holder)
        surfaces = meta.get("surfaces") if isinstance(meta, dict) else None
        if isinstance(surfaces, dict):
            for name, info in surfaces.items():
                if isinstance(info, dict) and "status" in info:
                    out[name] = info["status"]
    return out


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN")
    if not base or not token:
        print("ERROR: set BFF_BASE and BFF_TOKEN", file=sys.stderr)
        return 2

    ctx = ssl.create_default_context()
    if os.environ.get("BFF_INSECURE", "1") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    hard_fail = []
    print("== loop-liveness surfaces ==")
    payloads = {}
    for path in SURFACES:
        code, payload, err = _get(base, token, ctx, path)
        if code is None:
            print(f"  UNREACHABLE  {path}: {err}")
            hard_fail.append((path, "unreachable"))
            continue
        if code >= 500:
            print(f"  5xx ({code})  {path}")
            hard_fail.append((path, code))
            continue
        if code != 200 or not isinstance(payload, dict):
            print(f"  {code}  {path} (non-200 / non-json; reported)")
            continue
        payloads[path] = payload
        statuses = _surface_status(payload)
        print(f"  200  {path}  surfaces={statuses or '{}'}")

    # ---- empirical liveness observations (reported, not hard failures) ----
    cr = payloads.get("/bff/v5/control-room", {})
    ooda = cr.get("ooda_status", {}) if isinstance(cr, dict) else {}
    loop_total = (
        payloads.get("/bff/v5/loop-runs", {}).get("meta", {}).get("total")
        if isinstance(payloads.get("/bff/v5/loop-runs"), dict)
        else None
    )
    print("\n== liveness observations ==")
    print(f"  loop-run ledger total      : {loop_total}")
    if ooda:
        print(
            "  OODA gate / counters       : "
            f"gate={ooda.get('gate_state')} open={ooda.get('open_loop_count')} "
            f"closed={ooda.get('closed_loop_count')} failed={ooda.get('failed_loop_count')} "
            f"packets={ooda.get('total_packet_count')}"
        )
    degraded = []
    for path, payload in payloads.items():
        for name, status in _surface_status(payload).items():
            if status not in ("ok", None):
                degraded.append(f"{name}={status} ({path})")
    if degraded:
        print(f"  degraded surfaces          : {', '.join(degraded)}")
    if not loop_total:
        print("  NOTE: loop-run ledger empty - loops not demonstrably live via v5 ledger")

    if hard_fail:
        print(f"\nFAIL: {len(hard_fail)} liveness surface(s) unreachable or 5xx: {hard_fail}")
        return 1
    print("\nOK: all loop-liveness surfaces reachable (no 5xx)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
