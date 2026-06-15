#!/usr/bin/env python3
"""E2E BFF surface-status consistency verifier.

A BFF list response carries both data and a `meta.surfaces[*]` status. These must
not contradict: if a surface returns items, it must not simultaneously report its
source as `missing` / `unavailable`. A "data present but source unavailable"
response misleads the operator console (it shows a surface as down while serving
rows, or serves stale rows while claiming the live source is gone).

Failure semantics (CI-safe):
  * FAIL (exit 1) on any surface that returns >0 items while reporting
    status=unavailable or source=missing.
  * REPORT (exit 0) the swept surface count otherwise.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_surface_status_consistency.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request

SURFACES = [
    "/bff/agora/signals", "/bff/agora/inbox", "/bff/agora/journal", "/bff/agora/postmortems",
    "/bff/strategies", "/bff/personas", "/bff/capital-pools", "/bff/deployments", "/bff/runtimes",
    "/bff/incidents", "/bff/v5/sentinel/findings", "/bff/v5/loop-runs", "/bff/skills",
    "/bff/channels", "/bff/mcp-servers", "/bff/research-experiments", "/bff/jobs",
    "/bff/alerts", "/bff/artifacts",
]


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


def find_contradictions(payload: dict, path: str) -> list[str]:
    """Return contradiction strings for a single list payload."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        items = payload.get("data") if isinstance(payload.get("data"), list) else []
    n = len(items)
    out = []
    surfaces = (payload.get("meta") or {}).get("surfaces") or {}
    if n > 0 and isinstance(surfaces, dict):
        for name, info in surfaces.items():
            if not isinstance(info, dict):
                continue
            if info.get("status") == "unavailable" or info.get("source") == "missing":
                out.append(f"{path}: {n} items but surface {name} "
                           f"status={info.get('status')} source={info.get('source')}")
    return out


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    contradictions = []
    for path in SURFACES:
        contradictions.extend(find_contradictions(_get(base, token, ctx, path), path))

    print(f"== surface-status consistency over {len(SURFACES)} list surfaces ==")
    print(f"  items-present-but-source-missing contradictions: {len(contradictions)}")

    if contradictions:
        print(f"\nFAIL: {len(contradictions)} surface-status contradiction(s):")
        for c in contradictions:
            print(f"   {c}")
        return 1
    print("\nOK: no surface serves data while reporting its source unavailable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
