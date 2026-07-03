#!/usr/bin/env python3
"""Backfill / reconcile OpenClaw OODA cron jobs for existing personas.

Persona OODA cron registration is best-effort at persona-creation time; personas
created before that path worked (or while it silently ran in dry_run) have NO
recurring cron jobs, so their OODA loop never fires. This script registers the
missing jobs idempotently for a set of personas.

Transport: uses PersonaCronRegistrar, which auto-selects the openclaw-gateway
adapter HTTP proxy when PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL is set (the BFF
path), else the host docker-exec runtime when OPENCLAW_PAPER_ADAPTER_ENABLED=true.

Persona discovery (in order):
  1. --persona-id ID (repeatable), or newline-separated IDs on stdin;
  2. --agents-from-docker CONTAINER → `docker exec CONTAINER openclaw agents list
     --json`, taking every agent id that starts with "persona-".

Example (on the dev VM host):
  PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL=http://127.0.0.1:18104 \
    python3 scripts/reconcile_persona_ooda_cron.py \
      --agents-from-docker pantheon-openclaw-gateway-1
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "control-plane" / "cron"))

from persona_cron_registrar import PersonaCronRegistrar  # noqa: E402


def _discover_from_docker(container: str) -> list[str]:
    proc = subprocess.run(
        ["docker", "exec", container, "openclaw", "agents", "list", "--json"],
        capture_output=True, text=True, timeout=60,
    )
    out = (proc.stdout or "").strip()
    ids: list[str] = []
    try:
        data = json.loads(out)
        agents = data.get("agents") if isinstance(data, dict) else data
        for a in agents or []:
            aid = a.get("id") if isinstance(a, dict) else a
            if aid:
                ids.append(str(aid))
    except (ValueError, TypeError):
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("- "):
                ids.append(line[2:].split(" ")[0].strip())
    return [i for i in ids if i.startswith("persona-")]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--persona-id", action="append", default=[], help="persona id (repeatable)")
    ap.add_argument("--agents-from-docker", help="gateway container to discover persona-* agents from")
    args = ap.parse_args()

    persona_ids = list(args.persona_id)
    if args.agents_from_docker:
        persona_ids.extend(_discover_from_docker(args.agents_from_docker))
    if not persona_ids and not sys.stdin.isatty():
        persona_ids.extend(line.strip() for line in sys.stdin if line.strip())
    persona_ids = sorted(set(persona_ids))

    if not persona_ids:
        print("no persona ids supplied (use --persona-id / --agents-from-docker / stdin)", file=sys.stderr)
        return 2

    registrar = PersonaCronRegistrar()
    results = registrar.reconcile_personas(persona_ids)

    dry_run = 0
    total_registered = total_skipped = total_failed = 0
    for r in results:
        d = r.to_dict()
        if d["mode"] == "dry_run":
            dry_run += 1
        total_registered += len(d["registered"])
        total_skipped += len(d["skipped"])
        total_failed += len(d["failed"])
        print(f"{r.persona_id}: mode={d['mode']} registered={len(d['registered'])} "
              f"skipped={len(d['skipped'])} failed={len(d['failed'])}")
        for f in d["failed"]:
            print(f"    FAIL {f.get('workflow_id')}: {f.get('error')}")

    print(f"\nTotals: personas={len(results)} registered={total_registered} "
          f"skipped={total_skipped} failed={total_failed} dry_run_personas={dry_run}")
    if dry_run:
        print("WARNING: some personas registered in dry_run (no live gateway transport). "
              "Set PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL or OPENCLAW_PAPER_ADAPTER_ENABLED=true.",
              file=sys.stderr)
    # Non-zero if nothing landed live or any hard failure occurred.
    return 1 if (total_failed or (dry_run and total_registered == 0)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
