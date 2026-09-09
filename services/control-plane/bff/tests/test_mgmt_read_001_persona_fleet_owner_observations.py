"""MGMT-READ-001 ninth-review regression: persona_fleet must aggregate every
contributing owner, including the persona read itself and per-runtime
telemetry reads, not only runtime_bindings/incidents/evolution_decisions.

Before this fix, ``_mgmt_nl_collect_context(focus="persona_fleet")`` only
aggregated runtime_bindings_obs/incidents_obs/evolution_decisions_obs, and
``_project_persona_fleet_item`` read telemetry through the bare
``read_store.get_telemetry_summary`` accessor instead of the owner-observation
query. With all three aggregated owners healthy, a persona or telemetry
record explicitly marked ``status=unavailable``/``source_kind=unavailable``/
``degradation_reason=owner offline`` was silently dropped: the fleet
projector completed but ``persona_fleet`` reported ``ok`` and omitted that
owner from ``owner_observations``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

BFF_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BFF_DIR))

import main as bff_main  # noqa: E402
from management_read_models.service import ManagementService  # noqa: E402
from ports import create_in_memory_read_surface_ports  # noqa: E402

NOW = "2026-09-08T18:00:00Z"


def _persona() -> Dict[str, Any]:
    return {
        "id": "persona-under-test",
        "persona_id": "persona-under-test",
        "name": "Persona Under Test",
        "lifecycle_state": "active",
    }


def _runtime_binding() -> Dict[str, Any]:
    return {
        "id": "rb-1",
        "binding_id": "rb-1",
        "runtime_id": "rt-1",
        "capital_pool_id": "pool-1",
        "deployment_stage": "paper",
        "status": "running",
    }


def _persona_capital_binding() -> Dict[str, Any]:
    return {
        "id": "binding-1",
        "binding_id": "binding-1",
        "persona_id": "persona-under-test",
        "capital_pool_id": "pool-1",
        "status": "active",
    }


def _use_store(store: Any) -> None:
    bff_main.read_store = store
    bff_main._management_ai_context_service = ManagementService(read_store=store, utc_now=lambda: NOW)


def _restore(original_store: Any, original_service: Any) -> None:
    bff_main.read_store = original_store
    bff_main._management_ai_context_service = original_service


def test_persona_fleet_surfaces_telemetry_owner_reported_unavailable() -> None:
    original_store = bff_main.read_store
    original_service = bff_main._management_ai_context_service
    try:
        store = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "personas": [_persona()],
                "runtime_bindings": [_runtime_binding()],
                "bindings": [_persona_capital_binding()],
                "capital_pools": [{"id": "pool-1", "pool_id": "pool-1"}],
            },
            lifecycle_telemetry_governance_kwargs={
                "telemetry_summaries": {
                    "rt-1": {
                        "runtime_id": "rt-1",
                        "status": "unavailable",
                        "source_kind": "unavailable",
                        "owner": "telemetry-owner",
                        "degradation_reason": "owner offline",
                    }
                },
            },
        )
        _use_store(store)

        context = bff_main._mgmt_nl_collect_context("persona_fleet", NOW, None)

        surface = context["surfaces"]["persona_fleet"]
        assert surface["status"] == "unavailable", surface
        reasons = [obs.get("degradation_reason") for obs in surface["owner_observations"]]
        assert "owner offline" in reasons, surface["owner_observations"]
    finally:
        _restore(original_store, original_service)


def test_persona_fleet_surfaces_persona_owner_reported_unavailable() -> None:
    original_store = bff_main.read_store
    original_service = bff_main._management_ai_context_service
    try:
        persona = dict(_persona())
        persona.update(
            status="unavailable",
            source_kind="unavailable",
            owner="persona-owner",
            degradation_reason="owner offline",
        )
        store = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "personas": [persona],
                "runtime_bindings": [],
                "bindings": [],
                "capital_pools": [],
            },
        )
        _use_store(store)

        context = bff_main._mgmt_nl_collect_context("persona_fleet", NOW, None)

        surface = context["surfaces"]["persona_fleet"]
        assert surface["status"] == "unavailable", surface
        reasons = [obs.get("degradation_reason") for obs in surface["owner_observations"]]
        assert "owner offline" in reasons, surface["owner_observations"]
    finally:
        _restore(original_store, original_service)


def test_persona_fleet_reports_degraded_when_auxiliary_owners_lack_records() -> None:
    original_store = bff_main.read_store
    original_service = bff_main._management_ai_context_service
    try:
        store = create_in_memory_read_surface_ports(
            persona_capital_runtime_kwargs={
                "personas": [_persona()],
                "runtime_bindings": [_runtime_binding()],
                "bindings": [_persona_capital_binding()],
                "capital_pools": [{"id": "pool-1", "pool_id": "pool-1"}],
                "evolution_decisions": [{"decision_id": "dec-1", "target_id": "persona-other"}],
            },
            lifecycle_telemetry_governance_kwargs={
                "telemetry_summaries": {
                    "rt-1": {"runtime_id": "rt-1", "pnl": 1.0, "collected_at": NOW},
                },
                "incidents": {"inc-1": {"incident_id": "inc-1", "status": "resolved"}},
            },
        )
        _use_store(store)

        context = bff_main._mgmt_nl_collect_context("persona_fleet", NOW, None)

        surface = context["surfaces"]["persona_fleet"]
        assert surface["status"] == "degraded", surface
        assert any(
            obs["subject_type"] == "persona_teaching_sessions" and obs["status"] == "degraded"
            for obs in surface["owner_observations"]
        )
    finally:
        _restore(original_store, original_service)
