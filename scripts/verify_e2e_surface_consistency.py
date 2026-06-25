#!/usr/bin/env python3
"""E2E operator read-surface cross-consistency verifier.

The operator console reads the same fleet through several BFF surfaces. They must
agree, or an operator's view of "what is running / healthy" is wrong:

  * runtime ids:   /bff/runtimes  ==  /api/v1/operator/runtime-state
  * personas:      every persona bound to an ACTIVE runtime-binding must appear in
                   /bff/v5/execution/persona-health (the operator persona view).

Failure semantics (CI-safe):
  * FAIL (exit 1) on a cross-surface mismatch — a runtime present in one runtime
    surface but not the other, or a persona backing an active binding that is
    absent from persona-health.
  * REPORT (exit 0) the per-surface counts otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_surface_consistency.py
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
        return None


def _items(payload):
    if isinstance(payload, dict):
        for k in ("items", "data", "runtimes"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _persona_of(binding):
    return (binding.get("metadata") or {}).get("persona_id") or binding.get("persona_id")


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    runtimes = _items(_get(base, token, ctx, "/bff/runtimes"))
    runtime_state = _items(_get(base, token, ctx, "/api/v1/operator/runtime-state"))
    persona_health = _items(_get(base, token, ctx, "/bff/v5/execution/persona-health"))

    rt_ids = {b.get("runtime_id") for b in runtimes if b.get("runtime_id")}
    rs_ids = {b.get("runtime_id") for b in runtime_state if b.get("runtime_id")}
    ph_personas = {p.get("persona_id") for p in persona_health if p.get("persona_id")}
    active_personas = {
        _persona_of(b) for b in runtimes
        if str(b.get("status") or "").lower() == "active" and _persona_of(b)
    }

    violations = []
    rt_only = sorted(rt_ids - rs_ids)
    rs_only = sorted(rs_ids - rt_ids)
    if rt_only:
        violations.append(f"runtimes in /bff/runtimes not in operator/runtime-state: {rt_only[:8]}")
    if rs_only:
        violations.append(f"runtimes in operator/runtime-state not in /bff/runtimes: {rs_only[:8]}")
    persona_gap = sorted(active_personas - ph_personas)
    if persona_gap:
        violations.append(
            f"{len(persona_gap)} persona(s) bound to ACTIVE runtimes are absent from "
            f"persona-health: {persona_gap[:8]}"
        )

    print("== operator read-surface cross-consistency ==")
    print(f"  runtime ids: /bff/runtimes={len(rt_ids)} operator/runtime-state={len(rs_ids)} "
          f"(match={rt_ids == rs_ids})")
    print(f"  personas: persona-health={len(ph_personas)} active-binding-personas={len(active_personas)} "
          f"(active∖health={len(active_personas - ph_personas)})")

    if violations:
        print(f"\nFAIL: {len(violations)} cross-surface consistency violation(s):")
        for v in violations:
            print(f"   {v}")
        return 1
    print("\nOK: operator read-surfaces are cross-consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
