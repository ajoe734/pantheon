"""Canonical loop-run ledger truth precedence regressions.

The lifecycle projector ledger is conclusive whenever available.  Incident
reconstruction remains an explicitly degraded legacy backfill only when the
canonical ledger is unavailable; the two sources must never be merged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from domain_ports.lifecycle_telemetry_governance import DomainLifecyclePort


def _adapter(loop_runs, incidents):
    return DomainLifecyclePort(loop_runs=loop_runs, incidents=incidents)


LR = {
    "lr-rb-aaa": {"id": "lr-rb-aaa", "status": "active", "binding_id": "rb-aaa"},
    "lr-rb-bbb": {"id": "lr-rb-bbb", "status": "active", "binding_id": "rb-bbb"},
}
INC = {"inc-1": {"id": "inc-1", "title": "rescue loop", "status": "open"}}


def test_list_uses_only_projector_runs_when_incidents_are_also_available():
    svc = _adapter(LR, INC)
    ok, runs = svc.list_loop_runs()
    assert ok
    ids = {r.get("id") for r in runs}
    assert ids == {"lr-rb-aaa", "lr-rb-bbb"}
    assert "inc-1" not in ids


def test_list_returns_projector_runs_even_with_incidents_present():
    svc = _adapter(LR, INC)
    _, runs = svc.list_loop_runs()
    assert any(r.get("id", "").startswith("lr-rb-") for r in runs)


def test_get_canonical_ledger_is_conclusive_and_does_not_fall_through():
    svc = _adapter(LR, INC)
    ok_a, run_a = svc.get_loop_run("lr-rb-aaa")
    assert ok_a and run_a and run_a["id"] == "lr-rb-aaa"
    ok_i, run_i = svc.get_loop_run("inc-1")
    assert ok_i is True
    assert run_i is None


def test_incident_only_fallback_is_explicitly_legacy_backfill_degraded():
    svc = _adapter(None, INC)
    ok, runs = svc.list_loop_runs()
    assert ok is True
    assert len(runs) == 1
    assert runs[0]["source"] == "legacy_incident_backfill"
    assert runs[0]["projection_mode"] == "backfill"
    assert runs[0]["truth_level"] == "legacy_backfill"
    assert runs[0]["accepted_live"] is False
    assert runs[0]["read_state"] == "degraded"


def test_loop_store_projector_wrapper_reads_nested_records():
    metadata = {
        "schema_version": "pantheon.loop-run-projection.v1",
        "generation": 3,
        "controller": {
            "accepted_live": True,
            "status": "ready",
            "mode": "live",
            "truth_level": "canonical_live",
        },
    }
    svc = DomainLifecyclePort(loop_runs=LR, projection_metadata=metadata)

    ok, runs = svc.list_loop_runs()

    assert ok is True
    assert {run["id"] for run in runs} == {"lr-rb-aaa", "lr-rb-bbb"}
    assert svc.loop_run_projection_metadata() == metadata
