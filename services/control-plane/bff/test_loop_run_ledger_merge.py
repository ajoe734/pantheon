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

import read_store as rs


def _adapter(loop_runs, incidents):
    svc = rs.ServiceBackedReadAdapter.__new__(rs.ServiceBackedReadAdapter)

    def fake_load(dataset):
        if dataset == "loop_runs":
            return (loop_runs is not None), (loop_runs or {})
        if dataset == "incidents":
            return (incidents is not None), (incidents or {})
        return False, {}

    svc._load_dataset = fake_load  # type: ignore[attr-defined]
    return svc


LR = {
    "lr-rb-aaa": {"id": "lr-rb-aaa", "status": "active", "binding_id": "rb-aaa"},
    "lr-rb-bbb": {"id": "lr-rb-bbb", "status": "active", "binding_id": "rb-bbb"},
}
INC = {"inc-1": {"id": "inc-1", "title": "rescue loop", "status": "open"}}


def test_list_uses_only_projector_runs_when_incidents_are_also_available():
    svc = _adapter(LR, INC)
    ok, runs = rs.ServiceBackedReadAdapter.list_loop_runs(svc)
    assert ok
    ids = {r.get("id") for r in runs}
    assert ids == {"lr-rb-aaa", "lr-rb-bbb"}
    assert "inc-1" not in ids


def test_list_returns_projector_runs_even_with_incidents_present():
    svc = _adapter(LR, INC)
    _, runs = rs.ServiceBackedReadAdapter.list_loop_runs(svc)
    assert any(r.get("id", "").startswith("lr-rb-") for r in runs)


def test_get_canonical_ledger_is_conclusive_and_does_not_fall_through():
    svc = _adapter(LR, INC)
    ok_a, run_a = rs.ServiceBackedReadAdapter.get_loop_run(svc, "lr-rb-aaa")
    assert ok_a and run_a and run_a["id"] == "lr-rb-aaa"
    ok_i, run_i = rs.ServiceBackedReadAdapter.get_loop_run(svc, "inc-1")
    assert ok_i is True
    assert run_i is None


def test_incident_only_fallback_is_explicitly_legacy_backfill_degraded():
    svc = _adapter(None, INC)
    ok, runs = rs.ServiceBackedReadAdapter.list_loop_runs(svc)
    assert ok is True
    assert len(runs) == 1
    assert runs[0]["source"] == "legacy_incident_backfill"
    assert runs[0]["projection_mode"] == "backfill"
    assert runs[0]["truth_level"] == "legacy_backfill"
    assert runs[0]["accepted_live"] is False
    assert runs[0]["read_state"] == "degraded"


def test_loop_store_projector_wrapper_reads_nested_records(tmp_path, monkeypatch):
    store_path = tmp_path / "loop_runs.json"
    store_path.write_text(
        json.dumps(
            {
                "schema_version": "pantheon.loop-run-projection.v1",
                "generation": 3,
                "controller": {
                    "accepted_live": True,
                    "status": "ready",
                    "mode": "live",
                    "truth_level": "canonical_live",
                },
                "records": LR,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("PANTHEON_BFF_LOOP_RUN_STORE", str(store_path))
    svc = rs.ServiceBackedReadAdapter(allow_snapshot_fallback=False)

    ok, runs = svc.list_loop_runs()

    assert ok is True
    assert {run["id"] for run in runs} == {"lr-rb-aaa", "lr-rb-bbb"}
    assert svc.envelope_metadata("loop_runs") == {
        "schema_version": "pantheon.loop-run-projection.v1",
        "generation": 3,
        "controller": {
            "accepted_live": True,
            "status": "ready",
            "mode": "live",
            "truth_level": "canonical_live",
        },
    }
