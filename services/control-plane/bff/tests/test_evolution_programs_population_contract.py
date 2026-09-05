"""Contract test: /bff/evolution-programs population from real evolution proposals.

Verifies that when the BFF evolution_programs store is seeded with a real
proposal projection (evo-vslice-1, produced by the evolution service), the
/bff/evolution-programs surface returns count>0 and surface status != unavailable.

Task: CONSOLE-DATA-EVOLUTION
Pattern: console-population-research-slice paradigm
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from services.control_plane.bff import main as bff_main
from ports import create_in_memory_read_surface_ports

AUTH = "Bearer op-dev:admin:mfa"
HEADERS = {"Authorization": AUTH}

# Real evo-vslice-1 record produced by the evolution service and projected by
# scripts/project_evolution_to_bff_surfaces.py.
EVO_VSLICE_1 = {
    "id": "evo-vslice-1",
    "program_id": "evo-vslice-1",
    "name": "Retrain — artifact-vslice-momentum-001 v1",
    "status": "active",
    "action_type": "retrain",
    "target_type": "candidate_artifact",
    "target_id": "artifact-vslice-momentum-001",
    "target_version": "v1",
    "rationale": "sharpe_pct_of_baseline fell to 0.38, below the 0.50 governed threshold.",
    "created_by": "evolution-controller",
    "created_at": "2026-06-15T00:00:00Z",
    "updated_at": "2026-06-15T00:00:00Z",
    "params": {
        "source": "evolution_proposals",
        "signal_type": "performance_degradation",
        "metric_name": "sharpe_pct_of_baseline",
        "observed_value": 0.38,
        "threshold_value": 0.50,
        "evidence_ref_count": 1,
    },
}


def _client_with_evolution_programs_store(td: str) -> TestClient:
    """Wire a temp store file with evo-vslice-1 as PANTHEON_BFF_EVOLUTION_PROGRAM_STORE."""
    store_path = os.path.join(td, "evolution_programs.json")
    with open(store_path, "w") as f:
        json.dump({"evo-vslice-1": EVO_VSLICE_1}, f)

    os.environ["PANTHEON_BFF_EVOLUTION_PROGRAM_STORE"] = store_path
    bff_main.read_store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"evolution_programs": [EVO_VSLICE_1]}
    )
    return TestClient(bff_main.app)


def test_evolution_programs_count_gt_zero() -> None:
    """After wiring the store, /bff/evolution-programs must return count > 0."""
    original_store = bff_main.read_store
    original_env = os.environ.get("PANTHEON_BFF_EVOLUTION_PROGRAM_STORE")
    try:
        with tempfile.TemporaryDirectory() as td:
            client = _client_with_evolution_programs_store(td)
            resp = client.get("/bff/evolution-programs", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            items = body.get("data") or body.get("items") or []
            assert len(items) > 0, f"Expected count>0 but got {len(items)} items"
    finally:
        bff_main.read_store = original_store
        if original_env is None:
            os.environ.pop("PANTHEON_BFF_EVOLUTION_PROGRAM_STORE", None)
        else:
            os.environ["PANTHEON_BFF_EVOLUTION_PROGRAM_STORE"] = original_env


def test_evolution_programs_surface_status_not_unavailable() -> None:
    """After wiring the store, surface status must not be unavailable."""
    original_store = bff_main.read_store
    original_env = os.environ.get("PANTHEON_BFF_EVOLUTION_PROGRAM_STORE")
    try:
        with tempfile.TemporaryDirectory() as td:
            client = _client_with_evolution_programs_store(td)
            resp = client.get("/bff/evolution-programs", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            meta = body.get("meta") or {}
            surface = meta.get("surface") or {}
            status = surface.get("status") or meta.get("surface_status") or ""
            assert status != "unavailable", f"Surface status must not be unavailable, got: {status!r}"
    finally:
        bff_main.read_store = original_store
        if original_env is None:
            os.environ.pop("PANTHEON_BFF_EVOLUTION_PROGRAM_STORE", None)
        else:
            os.environ["PANTHEON_BFF_EVOLUTION_PROGRAM_STORE"] = original_env


def test_evolution_programs_evo_vslice_1_detail() -> None:
    """After wiring the store, evo-vslice-1 detail must return 200."""
    original_store = bff_main.read_store
    original_env = os.environ.get("PANTHEON_BFF_EVOLUTION_PROGRAM_STORE")
    try:
        with tempfile.TemporaryDirectory() as td:
            client = _client_with_evolution_programs_store(td)
            resp = client.get("/bff/evolution-programs/evo-vslice-1", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            data = body.get("data") or body
            assert (data.get("program_id") or data.get("id")) == "evo-vslice-1"
            assert data.get("action_type") == "retrain"
    finally:
        bff_main.read_store = original_store
        if original_env is None:
            os.environ.pop("PANTHEON_BFF_EVOLUTION_PROGRAM_STORE", None)
        else:
            os.environ["PANTHEON_BFF_EVOLUTION_PROGRAM_STORE"] = original_env


def test_evolution_programs_projection_shape() -> None:
    """Projected evo-vslice-1 record has all required BFF fields."""
    original_store = bff_main.read_store
    original_env = os.environ.get("PANTHEON_BFF_EVOLUTION_PROGRAM_STORE")
    try:
        with tempfile.TemporaryDirectory() as td:
            client = _client_with_evolution_programs_store(td)
            resp = client.get("/bff/evolution-programs/evo-vslice-1", headers=HEADERS)
            assert resp.status_code == 200, resp.text
            data = resp.json().get("data") or resp.json()
            for field in ("program_id", "name", "status", "action_type", "created_at"):
                assert field in data, f"Missing field: {field}"
            assert data["status"] == "active"
    finally:
        bff_main.read_store = original_store
        if original_env is None:
            os.environ.pop("PANTHEON_BFF_EVOLUTION_PROGRAM_STORE", None)
        else:
            os.environ["PANTHEON_BFF_EVOLUTION_PROGRAM_STORE"] = original_env
