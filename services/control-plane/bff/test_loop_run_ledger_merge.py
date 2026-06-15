"""Regression: list_loop_runs / get_loop_run must merge the dedicated v5
loop-run ledger (PANTHEON_BFF_LOOP_RUN_STORE, written by the loop-run projector)
with incident-derived recovery loops.

Before this fix, an available `incidents` dataset short-circuited the method, so
the projector's `lr-rb-*` records never surfaced whenever any incident existed —
leaving `/bff/v5/loop-runs` blank even when paper runtimes were active and the
projector had populated the store (paper-loop activation, 2026-06-15).
"""
from __future__ import annotations

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


def test_list_merges_projector_runs_and_incidents():
    svc = _adapter(LR, INC)
    ok, runs = rs.ServiceBackedReadAdapter.list_loop_runs(svc)
    assert ok
    ids = {r.get("id") for r in runs}
    assert {"lr-rb-aaa", "lr-rb-bbb"} <= ids, "projector loop-runs missing"
    assert "inc-1" in ids, "incident-derived loop-run missing"
    assert len(runs) == 3


def test_list_returns_projector_runs_even_with_incidents_present():
    # The exact regression: incidents present must NOT hide the ledger.
    svc = _adapter(LR, INC)
    _, runs = rs.ServiceBackedReadAdapter.list_loop_runs(svc)
    assert any(r.get("id", "").startswith("lr-rb-") for r in runs)


def test_get_resolves_projector_id_and_incident_id():
    svc = _adapter(LR, INC)
    ok_a, run_a = rs.ServiceBackedReadAdapter.get_loop_run(svc, "lr-rb-aaa")
    assert ok_a and run_a and run_a["id"] == "lr-rb-aaa"
    ok_i, run_i = rs.ServiceBackedReadAdapter.get_loop_run(svc, "inc-1")
    assert ok_i and run_i and run_i["id"] == "inc-1"
