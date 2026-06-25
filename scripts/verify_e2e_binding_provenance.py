#!/usr/bin/env python3
"""E2E provenance-integrity verifier for active runtime bindings.

Business flow under test (left half of the OODA loop):
    strategy -> research/experiment -> artifact -> deployment-plan ->
    capital-pool -> runtime-binding (active, paper)

Every active runtime-binding carries provenance references (artifact_id,
strategy_id, capital_pool_id, plan_id). For the loop to be trustworthy each of
those references must resolve to a live object. This verifier walks every active
binding from `/bff/runtimes` and asserts each referenced object resolves via its
BFF detail endpoint, and flags list/detail inconsistencies (an id that resolves
by detail but is absent from its collection listing).

Failure semantics (CI-safe):
  * FAIL (exit 1) on a DANGLING reference — a binding points at an artifact /
    strategy / plan / capital-pool whose detail endpoint returns 404/5xx.
  * REPORT (exit 0) list/detail inconsistencies and per-ref summary — these are
    surfaced for the record but do not fail the gate on their own.

Usage:
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/verify_e2e_binding_provenance.py
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

REF_ENDPOINTS = {
    "artifact": ("artifact_id", "/bff/artifacts/{id}", "/bff/artifacts"),
    "strategy": ("strategy_id", "/bff/strategies/{id}", "/bff/strategies"),
    "deployment": ("plan_id", "/bff/deployments/{id}", "/bff/deployments"),
    "capital_pool": ("capital_pool_id", "/bff/capital-pools/{id}", "/bff/capital-pools"),
}


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


def _is_resolved(code, payload) -> bool:
    """A detail endpoint counts as resolved only on 200 with a non-degraded body.

    Several BFF detail endpoints return 200 with a graceful-degradation envelope
    (``data.status == "degraded"`` / ``readSurface.status == "unavailable"``) for
    ANY id when the read-model source is down — that is NOT a real resolution and
    must not be counted as a healthy provenance reference.
    """
    if code != 200 or not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if isinstance(data, dict):
        if str(data.get("status") or "").lower() == "degraded":
            return False
        rs = data.get("readSurface")
        if isinstance(rs, dict) and str(rs.get("status") or "").lower() == "unavailable":
            return False
    return True


def _items(payload):
    if isinstance(payload, dict):
        for k in ("items", "data"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def _ref(binding, field):
    return binding.get(field) or (binding.get("metadata") or {}).get(field)


def _collection_ids(base, token, ctx, list_path):
    _, payload = _get(base, token, ctx, list_path)
    ids = set()
    for it in _items(payload):
        for key in ("id", "artifact_id", "strategy_id", "plan_id", "deployment_id",
                    "capital_pool_id", "pool_id", "binding_id"):
            if it.get(key):
                ids.add(it[key])
    return ids


def main() -> int:
    base = os.environ.get("BFF_BASE")
    token = os.environ.get("BFF_TOKEN", "op-dev:admin:mfa")
    if not base:
        print("ERROR: set BFF_BASE", file=sys.stderr)
        return 2
    ctx = _ctx()

    _, runtimes_payload = _get(base, token, ctx, "/bff/runtimes")
    bindings = _items(runtimes_payload)
    if not bindings:
        print("ERROR: no runtime bindings returned", file=sys.stderr)
        return 1

    collections = {name: _collection_ids(base, token, ctx, spec[2])
                   for name, spec in REF_ENDPOINTS.items()}

    dangling = []
    inconsistent = []
    summary = {name: {"ok": 0, "dangling": 0, "not_listed": 0} for name in REF_ENDPOINTS}

    for b in bindings:
        bid = b.get("binding_id") or b.get("id")
        for name, (field, detail_tmpl, _list) in REF_ENDPOINTS.items():
            ref = _ref(b, field)
            if not ref:
                continue
            code, payload = _get(base, token, ctx, detail_tmpl.format(id=ref))
            if _is_resolved(code, payload):
                summary[name]["ok"] += 1
                if ref not in collections[name]:
                    summary[name]["not_listed"] += 1
                    inconsistent.append((name, ref, bid))
            else:
                summary[name]["dangling"] += 1
                dangling.append((name, ref, bid, code))

    print(f"== provenance integrity over {len(bindings)} active bindings ==")
    for name, s in summary.items():
        print(f"  {name:12s} ok={s['ok']:2d} dangling={s['dangling']:2d} resolves-but-not-listed={s['not_listed']:2d}")

    if inconsistent:
        print(f"\n-- list/detail inconsistencies (resolve by detail, absent from listing): {len(inconsistent)} --")
        for name, ref, bid in inconsistent[:10]:
            print(f"   {name}: {ref} (binding {bid})")

    if dangling:
        print(f"\nFAIL: {len(dangling)} dangling provenance reference(s):")
        seen = set()
        for name, ref, bid, code in dangling:
            key = (name, ref)
            if key in seen:
                continue
            seen.add(key)
            print(f"   {name} {ref} -> {code} (e.g. binding {bid})")
        return 1

    print("\nOK: every active binding's provenance references resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
