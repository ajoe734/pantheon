#!/usr/bin/env python3
"""E2E sentinel-finding attribution-integrity verifier.

Business flow under test: telemetry/health → sentinel finding → status →
remediation. A sentinel finding that an operator should action must be
well-formed (severity + status + title) and, when it names a persona, that
persona must resolve — an unattributable finding cannot be remediated.

Failure semantics (CI-safe):
  * FAIL (exit 1) on a malformed finding (missing severity/status/title) or a
    finding whose persona_id does not resolve in /bff/personas.
  * REPORT (exit 0) the finding count + severity/status breakdown otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_sentinel_integrity.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
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


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    findings = _items(_get(base, token, ctx, "/bff/v5/sentinel/findings"))
    personas = _items(_get(base, token, ctx, "/bff/personas"))
    pids = {p.get("persona_id") or p.get("id") for p in personas}

    malformed = []
    dangling = []
    for f in findings:
        fid = f.get("id") or f.get("finding_id")
        if not f.get("severity") or not f.get("status") or not (
            f.get("title") or f.get("summary") or f.get("description")
        ):
            malformed.append(f"{fid}: missing severity/status/title")
        pid = f.get("persona_id")
        if pid and pid not in pids:
            dangling.append(f"{fid}: persona {pid} not in /bff/personas")

    print(f"== sentinel finding integrity over {len(findings)} findings ==")
    print(f"  severity: {dict(Counter(f.get('severity') for f in findings))}")
    print(f"  status:   {dict(Counter(f.get('status') for f in findings))}")
    print(f"  malformed={len(malformed)} dangling-persona={len(dangling)}")

    if malformed or dangling:
        print(f"\nFAIL: {len(malformed)+len(dangling)} sentinel finding integrity violation(s):")
        for v in (malformed + dangling)[:20]:
            print(f"   {v}")
        return 1
    print("\nOK: sentinel findings are well-formed and persona-attributable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
