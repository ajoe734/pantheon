#!/usr/bin/env python3
"""Project real research-orchestrator output into the BFF read-surface stores.

Root cause of the empty left-half console pages (E2E campaign finding): the BFF
read-surfaces for `strategies` / `artifacts` read from the store files named by
`PANTHEON_BFF_STRATEGY_SPEC_STORE` / `PANTHEON_BFF_RESEARCH_ARTIFACT_STORE`, but
those are unset/unpopulated on the deployed BFF — so even after the research
orchestrator produces a real artifact (task -> run -> artifact -> registry
writeback), it never reaches the console. The missing piece is a PROJECTION from
the live orchestrator/registry into the BFF read-surface stores.

This script reads the orchestrator's completed runs + artifacts and writes:
  * <out_dir>/research_artifacts.json   (keyed by artifact_id)
  * <out_dir>/strategy_specs.json       (keyed by strategy_id, from run parameters)

It only emits REAL records produced by the orchestrator — no fabricated data.
Wire the two PANTHEON_BFF_*_STORE envs at `<out_dir>/...` and the Strategies /
Artifacts pages render this real data.

Usage (run where the orchestrator API is reachable, e.g. inside the stack):
    RO_URL=http://research-orchestrator-svc:8101 OUT_DIR=/data/bff \
        python3 scripts/project_research_to_bff_surfaces.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        print(f"  warn: GET {url} failed: {e}", file=sys.stderr)
        return None


def _items(payload):
    if isinstance(payload, dict):
        for k in ("items", "data", "runs", "artifacts"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return payload if isinstance(payload, list) else []


def project(ro_url: str):
    runs = _items(_get(f"{ro_url}/api/research-orchestrator/runs"))
    artifacts, specs = {}, {}
    for run in runs:
        rid = run.get("run_id")
        params = run.get("parameters") or {}
        sid = params.get("strategy_id")
        # strategy spec (from run parameters)
        if sid and sid not in specs:
            specs[sid] = {
                "strategy_id": sid,
                "current_spec_version_id": f"specver-{sid}-0001",
                "title": run.get("title") or sid,
                "source_kind": "research",
                "persona_ids": [],
                "updated_at": run.get("completed_at") or run.get("requested_at"),
                "versions": [{
                    "spec_version_id": f"specver-{sid}-0001",
                    "spec_version": params.get("strategy_spec_version") or "v1",
                    "lifecycle_state": "candidate",
                    "title": run.get("title") or sid,
                }],
            }
        # artifacts for this run
        for art in _items(_get(f"{ro_url}/api/research-orchestrator/runs/{rid}/artifacts")):
            aid = art.get("artifact_id")
            if not aid:
                continue
            artifacts[aid] = {
                "artifact_id": aid,
                "lineage_id": art.get("lineage_id") or f"lin-{aid}",
                "version": 1,
                "status": (art.get("registry_hints") or {}).get("artifact_state") or "candidate",
                "name": art.get("title") or aid,
                "artifact_type": art.get("artifact_type") or "model_artifact",
                "description": f"Produced by research run {rid}.",
                "produced_by_experiment_id": run.get("task_id"),
                "created_at": art.get("created_at"),
                "metrics": art.get("metrics") or {},
            }
    return artifacts, specs


def main() -> int:
    ro_url = os.environ.get("RO_URL", "http://research-orchestrator-svc:8101").rstrip("/")
    out_dir = os.environ.get("OUT_DIR", "/data/bff")
    artifacts, specs = project(ro_url)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "research_artifacts.json"), "w") as f:
        json.dump(artifacts, f, indent=2)
    with open(os.path.join(out_dir, "strategy_specs.json"), "w") as f:
        json.dump(specs, f, indent=2)
    print(f"projected {len(artifacts)} artifacts + {len(specs)} strategies -> {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
