"""Exercise the real fleet collector and projector with independent owner faults."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import ast
import re
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.personas.service import (
    _normalize_lifecycle_state,
    _normalize_risk_level,
    _PERSONA_OPERATIONAL_LIFECYCLE_STATES,
)

_FLEET_FUNCS = None


def _get_fleet_collector(store, personas, service=None, utc_now=None):
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
        self.read_store = None
        self._management_ai_context_service = None
        self._list_persona_records = None

    def _mgmt_nl_collect_context(self, focus, snapshot_at, tenant_id=None):
        ns = _get_fleet_collector(
            store=self.read_store,
            personas=self._list_persona_records,
            service=self._management_ai_context_service,
            utc_now=lambda: snapshot_at,
        )
        return ns["_mgmt_nl_collect_context"](focus, snapshot_at, tenant_id)


bff_main = _MockBffMain()

NOW = "2026-09-08T18:00:00Z"


def owner_row(owner, **extra):
    return dict(
        owner=owner + "-owner", status="ok", source_kind="live",
        source_version=owner + "-v1", correlation_id=owner + "-correlation",
        observed_at=NOW, tenant_id="tenant-a", **extra,
    )


@pytest.fixture
def fleet(monkeypatch):
    personas = [owner_row("persona", persona_id="p1", lifecycle_state="paper_running")]
    runtime = owner_row("runtime", runtime_id="r1", binding_id="b1", persona_capital_binding_id="b1")
    store = SimpleNamespace(
        list_runtime_bindings=lambda: [runtime],
        list_incidents=lambda: [owner_row("incident", incident_id="i1")],
        list_evolution_decisions=lambda: [owner_row("evolution", decision_id="e1")],
        list_strategy_specs=lambda **kw: [owner_row("strategy", id="s1")],
        get_bindings_for_persona=lambda _: [owner_row("binding", id="b1", capital_pool_id="pool1")],
        get_sessions_for_persona=lambda _: [owner_row("session", id="session1", runtime_id="r1")],
        get_telemetry_summary=lambda _: owner_row("telemetry", runtime_id="r1"),
        get_teaching_sessions_for_persona=lambda _: [owner_row("teaching", id="teach1")],
        get_capital_pool=lambda _: owner_row("pool", id="pool1"),
        get_persona_allowed_actions=lambda _: owner_row("actions", can_pause=True),
    )
    monkeypatch.setattr(bff_main, "read_store", store)
    monkeypatch.setattr(bff_main, "_management_ai_context_service", ManagementService(read_store=store))
    monkeypatch.setattr(bff_main, "_list_persona_records", lambda *args: personas)
    return store, personas


def collect():
    return bff_main._mgmt_nl_collect_context("persona_fleet", NOW, "tenant-a")


@pytest.mark.parametrize("method", [
    "get_telemetry_summary", "get_bindings_for_persona", "get_teaching_sessions_for_persona",
    "get_sessions_for_persona", "get_capital_pool", "get_persona_allowed_actions",
    "list_strategy_specs",
])
def test_raising_owner_keeps_other_personas_and_owner_provenance(fleet, monkeypatch, method):
    store, personas = fleet
    personas.append(owner_row("second-persona", persona_id="p2", lifecycle_state="paper_running"))

    def fail(*args, **kwargs):
        raise RuntimeError(method + " owner offline")

    monkeypatch.setattr(store, method, fail)
    result = collect()
    surface = result["surfaces"]["persona_fleet"]
    assert surface["status"] == "unavailable"
    encoded = json.dumps(surface)
    assert method + " owner offline" in encoded
    assert "runtime-v1" in encoded and "incident-correlation" in encoded
    assert len(result["snippets"]["persona_fleet"]["items"]) == 2


def test_persona_owner_failure_keeps_other_owner_observations(fleet, monkeypatch):
    def fail(*args):
        raise RuntimeError("persona owner offline")

    monkeypatch.setattr(bff_main, "_list_persona_records", fail)
    result = collect()
    surface = result["surfaces"]["persona_fleet"]
    assert surface["status"] == "unavailable"
    assert "persona owner offline" in json.dumps(surface)
    assert "runtime-v1" in json.dumps(surface)


def test_flaky_telemetry_read_is_shared_by_items_and_surface(fleet, monkeypatch):
    store, personas = fleet
    personas.append(owner_row("second-persona", persona_id="p2", lifecycle_state="paper_running"))
    calls = []

    def telemetry(runtime_id):
        calls.append(runtime_id)
        row = owner_row("telemetry", runtime_id=runtime_id)
        if len(calls) == 1:
            row.update(status="unavailable", source_kind="unavailable",
                       source_version="failed-v1", degradation_reason="telemetry owner offline")
        return row

    monkeypatch.setattr(store, "get_telemetry_summary", telemetry)
    result = collect()
    assert calls == ["r1"]
    for item in result["snippets"]["persona_fleet"]["items"]:
        assert item["telemetrySummary"]["latest"]["source_version"] == "failed-v1"
    surface = result["surfaces"]["persona_fleet"]
    assert surface["status"] == "unavailable" and "failed-v1" in json.dumps(surface)
    assert len([o for o in surface["owner_observations"] if o["subject_type"] == "telemetry"]) == 1


def test_complete_owner_evidence_is_healthy(fleet):
    result = collect()
    assert result["surfaces"]["persona_fleet"]["status"] == "ok"


@pytest.mark.parametrize("method", [
    "get_bindings_for_persona", "get_teaching_sessions_for_persona", "get_sessions_for_persona",
    "list_strategy_specs", "get_capital_pool", "get_persona_allowed_actions",
    "get_telemetry_summary",
])
def test_foreign_owner_records_are_filtered_before_provenance(fleet, monkeypatch, method):
    store, _ = fleet
    original = getattr(store, method)

    def foreign(*args, **kwargs):
        result = original(*args, **kwargs)
        rows = result if isinstance(result, list) else [result]
        for row in rows:
            row.update(tenant_id="tenant-b", owner="foreign-owner", source_version="foreign-version")
        return result

    monkeypatch.setattr(store, method, foreign)
    encoded = json.dumps(collect(), default=list)
    assert "foreign-owner" not in encoded and "foreign-version" not in encoded


def test_capital_pool_enrichment_reads_each_owner_once(fleet, monkeypatch):
    store, _ = fleet
    original = store.get_capital_pool
    calls = []

    def pool(pool_id):
        calls.append(pool_id)
        return original(pool_id)

    monkeypatch.setattr(store, "get_capital_pool", pool)
    result = collect()
    item = result["snippets"]["persona_fleet"]["items"][0]
    assert calls == ["pool1"]
    assert item["bindings"][0]["capital_pool"] == item["capital_pools"][0]


def test_snippet_limit_does_not_hide_an_unavailable_owner(fleet, monkeypatch):
    store, personas = fleet
    personas.extend(owner_row("persona", persona_id=f"p{i}") for i in range(2, 22))
    original = store.get_teaching_sessions_for_persona

    def teaching(persona_id):
        if persona_id == "p21":
            raise RuntimeError("last persona teaching owner offline")
        return original(persona_id)

    monkeypatch.setattr(store, "get_teaching_sessions_for_persona", teaching)
    result = collect()
    assert len(result["snippets"]["persona_fleet"]["items"]) == 20
    surface = result["surfaces"]["persona_fleet"]
    assert surface["status"] == "unavailable"
    assert "last persona teaching owner offline" in json.dumps(surface)
