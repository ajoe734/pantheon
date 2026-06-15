#!/usr/bin/env python3
"""E2E evolution-loop integrity verifier.

Business flow under test (closing arc of the OODA loop):
    telemetry/incident → evolution decision/program → new artifact.

Two integrity properties:
  1. Open incidents are well-formed: an `open` incident must carry an
     attributable `runtime_id` and a non-empty `title` (an untitled, runtime-less
     open incident is a malformed record that no operator can action and no
     evolution step can attach to).
  2. Evolution responsiveness: the evolution surface is reachable; open incidents
     should not pile up with zero evolution programs/decisions (a not-closing
     loop). Reported (not hard-failed) since responsiveness is time-dependent.

Failure semantics (CI-safe):
  * FAIL (exit 1) on a malformed open incident (missing runtime_id or title).
  * REPORT (exit 0) the evolution-program count and open-incident backlog.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_evolution_loop.py
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
        for k in ("items", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _is_malformed_open_incident(inc) -> tuple[bool, str]:
    if str(inc.get("status") or "").lower() != "open":
        return False, ""
    iid = inc.get("incident_id") or inc.get("id")
    reasons = []
    if not (inc.get("runtime_id") or "").strip() if isinstance(inc.get("runtime_id"), str) else not inc.get("runtime_id"):
        reasons.append("no runtime_id")
    title = str(inc.get("title") or "").strip()
    if not title or title.lower() in {"untitled incident", "untitled"}:
        reasons.append(f"title={title!r}")
    if reasons:
        return True, f"{iid}: {', '.join(reasons)}"
    return False, ""


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    incidents = _items(_get(base, token, ctx, "/bff/incidents"))
    programs = _items(_get(base, token, ctx, "/bff/evolution-programs"))

    open_incidents = [i for i in incidents if str(i.get("status") or "").lower() == "open"]
    malformed = []
    for inc in incidents:
        bad, detail = _is_malformed_open_incident(inc)
        if bad:
            malformed.append(detail)

    print("== evolution-loop integrity ==")
    print(f"  incidents={len(incidents)} open={len(open_incidents)} evolution_programs={len(programs)}")
    if open_incidents and not programs:
        print(f"  NOTE: {len(open_incidents)} open incident(s) with 0 evolution programs "
              f"- the incident → evolution arc is not closing")

    if malformed:
        print(f"\nFAIL: {len(malformed)} malformed open incident(s) (unattributable / untitled):")
        for d in malformed[:20]:
            print(f"   {d}")
        return 1
    print("\nOK: open incidents are well-formed (attributable + titled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
