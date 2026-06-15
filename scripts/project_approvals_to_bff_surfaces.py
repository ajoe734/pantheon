#!/usr/bin/env python3
"""Project real promotion-service approvals into the BFF approval_decisions store.

Root cause pattern (same as research slice E2E finding): BFF `/bff/approvals`
reads from `PANTHEON_BFF_APPROVAL_DECISION_STORE`; when that file is absent or
unset the surface reports `source: unavailable` even after real approvals are
created via the promotion svc.

This script reads live approval decisions from the promotion service and writes
`approval_decisions.json` into the BFF store dir. Wire
`PANTHEON_BFF_APPROVAL_DECISION_STORE` to that file and `/bff/approvals` returns
count>0 with surface status=ok.

Emits ONLY real records returned by the promotion service — no fabricated data.

Acceptance posture (stub dispatch, dev safety):
- promotion svc is contacted read-only (GET /api/v1/approvals)
- no live broker orders are issued
- no capital allocation side-effects

Usage (run where the promotion service API is reachable):
    PROMOTION_URL=http://promotion-svc:8089 OUT_DIR=/data/bff \\
        python3 scripts/project_approvals_to_bff_surfaces.py

    # or with defaults (docker-compose service names):
    python3 scripts/project_approvals_to_bff_surfaces.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def _get(url: str) -> object:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: GET {url} failed: {exc}", file=sys.stderr)
        return None


def project(promotion_url: str) -> dict:
    """Return a dict keyed by decision_id with real approval decision records."""
    payload = _get(f"{promotion_url}/api/v1/approvals")
    if not isinstance(payload, dict):
        return {}
    items = payload.get("items") or []
    decisions: dict = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        did = item.get("decision_id") or item.get("id")
        if not did:
            continue
        decisions[str(did)] = item
    return decisions


def main() -> int:
    promotion_url = os.environ.get(
        "PROMOTION_URL", "http://promotion-svc:8089"
    ).rstrip("/")
    out_dir = os.environ.get("OUT_DIR", "/data/bff")

    decisions = project(promotion_url)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "approval_decisions.json")
    with open(out_path, "w") as f:
        json.dump(decisions, f, indent=2)

    print(
        f"projected {len(decisions)} approval decision(s) -> {out_path}",
        file=sys.stderr,
    )
    if not decisions:
        print(
            "  note: 0 decisions projected — create approvals via "
            f"POST {promotion_url}/api/v1/approvals first",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
