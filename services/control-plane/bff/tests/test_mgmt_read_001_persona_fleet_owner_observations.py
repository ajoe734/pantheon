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

import ast
import json
import re
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.personas.service import (
    _normalize_lifecycle_state,
    _normalize_risk_level,
    _PERSONA_OPERATIONAL_LIFECYCLE_STATES,
)
from services.control_plane.bff.ports import create_in_memory_read_surface_ports

_FLEET_FUNCS = None


def _get_fleet_collector(store, personas=None, service=None, utc_now=None):
    global _FLEET_FUNCS
    if _FLEET_FUNCS is None:
        tree = ast.parse(Path("services/control-plane/bff/main.py").read_text(encoding="utf-8"))
        target_names = {
            "_mgmt_nl_collect_context",
            "_mgmt_nl_filter_tenant_records",
            "_mgmt_nl_record_matches_tenant",
            "_mgmt_nl_record_tenant_ids",
            "_mgmt_nl_scope_values",
            "_mgmt_nl_merge_owner_observations",
            "_mgmt_nl_add_record_entities",
            "_mgmt_nl_add_entity",
            "_project_persona_fleet_item",
            "_project_persona_dto",
            "_project_persona_fleet_health",
            "_is_persona_lifecycle_operational",
            "_persona_fleet_runtime_matches",
            "_sort_records_latest_first",
        }
        _FLEET_FUNCS = [
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in target_names
        ]

    clock = utc_now or (lambda: NOW)
    context_service = service or ManagementService(read_store=store, utc_now=clock)
    persona_supplier = (lambda *a: personas(*a)) if callable(personas) else (lambda *a: personas if personas is not None else (store.list_personas() if hasattr(store, "list_personas") else []))

    ns = dict(__import__("typing").__dict__)
    ns.update({
        "re": re,
        "json": json,
        "read_store": store,
        "_management_ai_context_service": context_service,
        "_list_persona_records": persona_supplier,
        "utc_now": clock,
        "_normalize_lifecycle_state": _normalize_lifecycle_state,
        "_normalize_risk_level": _normalize_risk_level,
        "_PERSONA_OPERATIONAL_LIFECYCLE_STATES": _PERSONA_OPERATIONAL_LIFECYCLE_STATES,
    })
    mod = ast.Module(body=_FLEET_FUNCS, type_ignores=[])
    exec(compile(mod, "main_fleet.py", "exec"), ns)
    return ns


class _MockBffMain:
    def __init__(self):
        self.store = None
        self._management_ai_context_service = None
        self._list_persona_records = None

    def _mgmt_nl_collect_context(self, focus, snapshot_at, tenant_id=None):
        ns = _get_fleet_collector(
            store=self.store,
            personas=self._list_persona_records,
            service=self._management_ai_context_service,
            utc_now=lambda: snapshot_at,
        )
        return ns["_mgmt_nl_collect_context"](focus, snapshot_at, tenant_id)


bff_main = _MockBffMain()

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
    bff_main.store = store
    bff_main._management_ai_context_service = ManagementService(read_store=store, utc_now=lambda: NOW)


def _restore(original_store: Any, original_service: Any) -> None:
    bff_main.store = original_store
    bff_main._management_ai_context_service = original_service


def test_persona_fleet_surfaces_telemetry_owner_reported_unavailable() -> None:
    original_store = bff_main.store
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
    original_store = bff_main.store
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
    original_store = bff_main.store
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
