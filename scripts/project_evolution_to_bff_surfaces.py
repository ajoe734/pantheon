#!/usr/bin/env python3
"""Project real evolution-service proposals into the BFF evolution_programs store.

Root cause of the empty /bff/evolution-programs surface: the BFF reads from the
store file named by PANTHEON_BFF_EVOLUTION_PROGRAM_STORE, but that env var was
unset and the proposals→programs projection did not exist.  The evolution service
already holds real proposals (e.g. evo-vslice-1); this script reads them and
writes the evolution_programs.json store file that the BFF consumes.

Mapping: each EvolutionDecision proposal → one evolution program entry, keyed by
decision_id.  The record shape matches what the BFF list/detail surfaces expect.
Stub dispatch posture: this script is read-only against the evolution service.

Usage (run where the evolution API is reachable, e.g. inside the stack):
    EVO_URL=http://evolution:8093 OUT_DIR=/data/bff/evolution \
        python3 scripts/project_evolution_to_bff_surfaces.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  warn: GET {url} failed: {exc}", file=sys.stderr)
        return None


def _items(payload):
    if isinstance(payload, dict):
        for k in ("items", "data", "decisions", "proposals"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return payload if isinstance(payload, list) else []


def _proposal_to_program(proposal: dict) -> dict:
    """Map a raw evolution-service proposal to the BFF evolution_programs shape."""
    decision_id = str(proposal.get("decision_id") or proposal.get("id") or "")
    action_type = str(proposal.get("action_type") or "")
    target_id = str(proposal.get("target_id") or "")
    target_version = str(proposal.get("target_version") or "")
    rationale = str(proposal.get("rationale") or "")
    threshold_snapshots = proposal.get("threshold_snapshots") or []
    evidence_refs = proposal.get("evidence_refs") or []

    metric_name = ""
    observed_value = None
    threshold_value = None
    signal_type = ""
    if threshold_snapshots and isinstance(threshold_snapshots[0], dict):
        snap = threshold_snapshots[0]
        metric_name = str(snap.get("metric_name") or "")
        observed_value = snap.get("observed_value")
        threshold_value = snap.get("threshold_value")
        signal_type = str(snap.get("signal_type") or "")

    return {
        "id": decision_id,
        "program_id": decision_id,
        "name": f"{action_type.replace('_', ' ').title()} — {target_id} {target_version}".strip(),
        "status": "active",
        "action_type": action_type,
        "target_type": str(proposal.get("target_type") or ""),
        "target_id": target_id,
        "target_version": target_version,
        "rationale": rationale,
        "created_by": str(proposal.get("created_by_id") or ""),
        "created_at": str(proposal.get("created_at") or ""),
        "updated_at": str(proposal.get("updated_at") or proposal.get("created_at") or ""),
        "params": {
            "source": "evolution_proposals",
            "signal_type": signal_type,
            "metric_name": metric_name,
            "observed_value": observed_value,
            "threshold_value": threshold_value,
            "evidence_ref_count": len(evidence_refs),
        },
    }


def project(evo_url: str) -> dict:
    """Fetch proposals from the evolution service and return programs dict."""
    raw = _get(f"{evo_url}/api/evolution/proposals")
    proposals = _items(raw)
    programs: dict = {}
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        program = _proposal_to_program(proposal)
        pid = program.get("program_id")
        if pid:
            programs[pid] = program
    return programs


def main() -> int:
    evo_url = os.environ.get("EVO_URL", "http://evolution:8093").rstrip("/")
    out_dir = os.environ.get("OUT_DIR", "/data/bff/evolution")
    programs = project(evo_url)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "evolution_programs.json")
    with open(out_path, "w") as f:
        json.dump(programs, f, indent=2)
    print(f"projected {len(programs)} evolution programs -> {out_path}")
    if not programs:
        print("  warn: 0 programs projected — evolution service may be unreachable or empty", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
