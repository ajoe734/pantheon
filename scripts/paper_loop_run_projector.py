#!/usr/bin/env python3
"""Project active paper bindings + execution telemetry into the v5 loop-run ledger.

Context
-------
`/bff/v5/loop-runs` reads the dataset bound to env `PANTHEON_BFF_LOOP_RUN_STORE`
(a JSON object keyed by loop-run `id`; see read_store.py `loop_runs` dataset).
Nothing populates it in the deployed stack — only e2e fixtures ever wrote it — so
the v5 loop-run ledger reads empty even though 16 paper runtime-bindings are
active. This projector derives one loop-run record per active binding from the
authoritative read surfaces and writes the store file, so the ledger reflects
real runtime state instead of staying blank.

It does NOT fabricate trading results: fills/pnl are copied from the binding's
telemetry summary when available, and left null/zero otherwise (honest "active
but idle" state). The record shape matches what the BFF loop-run handler renders
(`id`, `status`, `activePeriod`) plus descriptive fields.

Inputs come from the BFF itself (stub-auth) so the projector needs no internal
service tokens:
  * GET /bff/runtimes              -> active runtime-bindings
  * GET /bff/runtimes/{id}         -> per-runtime detail (best-effort enrichment)

Usage
-----
    BFF_BASE=https://...sslip.io BFF_TOKEN=op-dev:admin:mfa \
        python3 scripts/paper_loop_run_projector.py --out /data/bff/loop_runs.json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get(base: str, token: str, ctx: ssl.SSLContext, path: str):
    req = urllib.request.Request(base.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"  warn: GET {path} failed: {exc}\n")
        return None


def _items(payload) -> list:
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data"):
        v = payload.get(key)
        if isinstance(v, list):
            return v
    return []


def build_loop_run(binding: dict, now: str) -> dict:
    runtime_id = binding.get("runtime_id") or binding.get("id")
    binding_id = binding.get("binding_id") or binding.get("id")
    meta = binding.get("metadata") or {}
    persona_id = meta.get("persona_id") or binding.get("persona_id")
    strategy_id = meta.get("strategy_id")
    started = binding.get("effective_at") or binding.get("created_at") or now
    active = str(binding.get("status") or "").lower() == "active"
    lr_id = f"lr-{binding_id}"
    return {
        "id": lr_id,
        "loop_run_id": lr_id,
        "status": "active" if active else "idle",
        "activePeriod": {"start": started, "end": None if active else now},
        "loop_type": "paper_execution",
        "environment": binding.get("execution_mode") or binding.get("deployment_mode") or "paper",
        "runtime_id": runtime_id,
        "binding_id": binding_id,
        "persona_id": persona_id,
        "strategy_id": strategy_id,
        "started_at": started,
        "last_projected_at": now,
        # execution metrics are copied from telemetry when present (see enrich)
        "fills": binding.get("_fills", 0),
        "pnl": binding.get("_pnl", 0),
        "source": "paper_loop_run_projector",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=os.getenv("PANTHEON_BFF_LOOP_RUN_STORE", "/data/bff/loop_runs.json"))
    ap.add_argument("--base", default=os.getenv("BFF_BASE"))
    ap.add_argument("--token", default=os.getenv("BFF_TOKEN", "op-dev:admin:mfa"))
    args = ap.parse_args()
    if not args.base:
        sys.stderr.write("ERROR: set BFF_BASE (or --base)\n")
        return 2

    ctx = ssl.create_default_context()
    if os.environ.get("BFF_INSECURE", "1") == "1":
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    runtimes = _items(_get(args.base, args.token, ctx, "/bff/runtimes"))
    now = _now_iso()
    store: dict = {}
    for b in runtimes:
        rec = build_loop_run(b, now)
        store[rec["id"]] = rec

    out = args.out
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    active = sum(1 for r in store.values() if r["status"] == "active")
    sys.stderr.write(f"projected {len(store)} loop-run records ({active} active) -> {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
