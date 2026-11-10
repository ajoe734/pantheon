"""Exercise the real fleet collector and projector with independent owner faults."""
import json
import logging
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
from services.control_plane.bff.shared.cross_domain_utils import (
    _sort_records_latest_first,
)

_FLEET_FUNCS = None


def _get_fleet_collector(store, personas, service=None, utc_now=None):
    global _FLEET_FUNCS
    if _FLEET_FUNCS is None:
        target_names = {
            "_mgmt_nl_collect_context",
            "_mgmt_nl_filter_deployment_scoped_records",
            "_mgmt_nl_runtime_is_retired",
            "_mgmt_nl_persona_state",
            "_mgmt_nl_persona_is_operational",
            "_management_count_by",
            "_mgmt_nl_filter_tenant_records",
            "_mgmt_nl_record_matches_tenant",
            "_mgmt_nl_record_tenant_ids",
            "_mgmt_nl_scope_values",
            "_mgmt_nl_merge_owner_observations",
            "_mgmt_nl_add_record_entities",
            "_mgmt_nl_add_entity",
            "_project_persona_fleet_item",
            "_project_persona_fleet_item_impl",
            "_mgmt_nl_unavailable_fleet_item",
            "get_project_persona_fleet_item",
            "get_management_ai_context_service",
            "_project_persona_dto",
            "_project_persona_fleet_health",
            "_is_persona_lifecycle_operational",
            "_persona_fleet_runtime_matches",
        }
        bff_dir = Path(__file__).resolve().parents[1]
        trees = [
            ast.parse((bff_dir / "assistant" / "management_service.py").read_text(encoding="utf-8")),
            ast.parse((bff_dir / "personas" / "service.py").read_text(encoding="utf-8")),
            ast.parse((bff_dir / "main.py").read_text(encoding="utf-8")),
        ]
        funcs = {}
        for tree in trees:
            for n in tree.body:
                if isinstance(n, ast.FunctionDef) and n.name in target_names and n.name not in funcs:
                    funcs[n.name] = n
        _FLEET_FUNCS = list(funcs.values())

    clock = utc_now or (lambda: NOW)
    context_service = service or ManagementService(read_store=store, utc_now=clock)
    persona_supplier = (lambda *a: personas(*a)) if callable(personas) else (lambda *a: personas if personas is not None else (store.list_personas() if hasattr(store, "list_personas") else []))

    ns = dict(__import__("typing").__dict__)
    ns.update({
        "__package__": "services.control_plane.bff.assistant",
        "re": re,
        "json": json,
        "logging": logging,
        "read_store": store,
        "_management_ai_context_service": context_service,
        "_MANAGEMENT_AI_CONTEXT_SERVICE": context_service,
        "get_management_ai_context_service": lambda: context_service,
        "_list_persona_records": persona_supplier,
        "utc_now": clock,
        "_normalize_lifecycle_state": _normalize_lifecycle_state,
        "_normalize_risk_level": _normalize_risk_level,
        "_PERSONA_OPERATIONAL_LIFECYCLE_STATES": _PERSONA_OPERATIONAL_LIFECYCLE_STATES,
        "_sort_records_latest_first": _sort_records_latest_first,
    })
    mod = ast.Module(body=_FLEET_FUNCS, type_ignores=[])
    exec(compile(mod, "main_fleet.py", "exec"), ns)
    if "_project_persona_fleet_item_impl" in ns:
        ns["_project_persona_fleet_item"] = ns["_project_persona_fleet_item_impl"]
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
        list_incidents=lambda **_kw: [owner_row("incident", incident_id="i1")],
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


# ---------------------------------------------------------------------------
# BFF-AI-FLEET-EMPTY-OK-20261109: a persona that holds no session, teaching
# session, binding or strategy is a real empty, and a failed owner read is not.
# These run through the production ReadSurfacePorts composition.
# ---------------------------------------------------------------------------
from services.control_plane.bff.ports import create_in_memory_read_surface_ports  # noqa: E402
from services.control_plane.bff.ports.persona_capital_runtime import CapitalPoolPort  # noqa: E402
from services.control_plane.bff.ports.persona_training import (  # noqa: E402
    PersonaRegistryReadsPort,
    PersonaTrainingDomainPort,
)

_TENANT_FILTER = lambda rows: [r for r in rows if r.get("tenant_id") in (None, "tenant-a")]  # noqa: E731


def _real_persona(persona_id="p-draft", lifecycle_state="draft"):
    # Shape of the persona rows the fleet reads: no provenance fields.
    return {
        "id": persona_id,
        "persona_id": persona_id,
        "name": persona_id,
        "lifecycle_state": lifecycle_state,
        "tenant_id": "tenant-a",
    }


class _TrainingStore:
    """The persona registry store behind PersonaRegistryReadsPort."""

    def __init__(self, sessions=None, teaching=None, fail=None):
        self.sessions = list(sessions or [])
        self.teaching = list(teaching or [])
        self.fail = fail

    def list_sessions_for_persona(self, persona_id, status=None):
        if self.fail == "sessions":
            raise RuntimeError("sessions owner offline")
        return list(self.sessions)

    def list_teaching_sessions_for_persona(self, persona_id, status=None):
        if self.fail == "teaching":
            raise RuntimeError("teaching owner offline")
        return list(self.teaching)


def _ports(*, personas=None, bindings=None, training_store="default", binding_error=None,
           strategy_error=None, monkeypatch=None):
    ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={"personas": personas if personas is not None else [_real_persona()]},
    )
    if binding_error is not None:
        def broken():
            raise binding_error
        ports.persona_capital_runtime.capital = CapitalPoolPort(
            pools_provider=lambda: [], bindings_provider=broken
        )
    elif bindings is not None:
        ports.persona_capital_runtime.capital = CapitalPoolPort(
            pools_provider=lambda: [], bindings_provider=lambda: list(bindings)
        )
    if training_store == "default":
        training_store = _TrainingStore()
    if training_store is not None:
        ports.persona_training = PersonaTrainingDomainPort(
            persona_port=PersonaRegistryReadsPort(store=training_store)
        )
    if strategy_error is not None:
        def broken_specs(**kwargs):
            raise strategy_error
        monkeypatch.setattr(ports.research_knowledge_source, "list_strategy_specs", broken_specs)
    return ports


def _service(ports):
    return ManagementService(read_store=ports, utc_now=lambda: NOW)


_READS = {
    "persona_sessions": "get_context_sessions_for_persona",
    "persona_teaching_sessions": "get_context_teaching_sessions_for_persona",
    "persona_bindings": "get_context_bindings_for_persona",
    "persona_strategies": "get_context_strategies_for_persona",
}


def _read(service, subject):
    return getattr(service, _READS[subject])("p-draft", record_filter=_TENANT_FILTER)


@pytest.mark.parametrize("subject", sorted(_READS))
def test_successful_empty_persona_read_is_ok(subject):
    rows, obs = _read(_service(_ports()), subject)
    assert rows == []
    assert obs["status"] == "ok", obs
    assert not obs.get("degradation_reason")


@pytest.mark.parametrize("subject", sorted(_READS))
def test_failed_persona_owner_read_is_unavailable(subject, monkeypatch):
    kwargs = {
        "persona_sessions": {"training_store": _TrainingStore(fail="sessions")},
        "persona_teaching_sessions": {"training_store": _TrainingStore(fail="teaching")},
        "persona_bindings": {"binding_error": RuntimeError("bindings owner offline")},
        "persona_strategies": {"strategy_error": RuntimeError("strategies owner offline"), "monkeypatch": monkeypatch},
    }[subject]
    rows, obs = _read(_service(_ports(**kwargs)), subject)
    assert rows == []
    assert obs["status"] == "unavailable", obs
    assert "owner offline" in obs["degradation_reason"]


def test_persona_rows_from_the_owner_are_returned_and_scoped():
    session = {"id": "s1", "persona_id": "p-draft", "tenant_id": "tenant-a", "status": "active"}
    foreign = {"id": "s2", "persona_id": "p-draft", "tenant_id": "tenant-b"}
    binding = {"id": "b1", "binding_id": "b1", "persona_id": "p-draft", "capital_pool_id": "pool1",
               "tenant_id": "tenant-a"}
    other = {"id": "b2", "binding_id": "b2", "persona_id": "p-other", "tenant_id": "tenant-a"}
    service = _service(_ports(training_store=_TrainingStore(sessions=[session, foreign]),
                              bindings=[binding, other]))
    rows, obs = _read(service, "persona_sessions")
    assert [r["id"] for r in rows] == ["s1"] and obs["status"] == "ok"
    rows, obs = _read(service, "persona_bindings")
    assert [r["id"] for r in rows] == ["b1"] and obs["status"] == "ok"


def test_unconfigured_training_owner_cannot_tell_empty_from_failure():
    ports = _ports(training_store=None)
    for subject, strict in (
        ("persona_sessions", "list_persona_sessions"),
        ("persona_teaching_sessions", "list_persona_teaching_sessions"),
    ):
        rows, obs = _read(_service(ports), subject)
        assert rows == []
        assert obs["status"] == "degraded", obs
        assert strict in obs["degradation_reason"] and "not configured" in obs["degradation_reason"]


def test_unconfigured_binding_owner_cannot_tell_empty_from_failure():
    ports = _ports()
    ports.persona_capital_runtime.capital = CapitalPoolPort()
    rows, obs = _read(_service(ports), "persona_bindings")
    assert rows == []
    assert obs["status"] == "degraded", obs
    assert "read_bindings_for_persona" in obs["degradation_reason"]
    assert "not configured" in obs["degradation_reason"]


def test_store_without_a_raising_read_keeps_the_degraded_rule_and_names_the_reader():
    store = SimpleNamespace(
        get_bindings_for_persona=lambda _: [],
        get_sessions_for_persona=lambda _: [],
        get_teaching_sessions_for_persona=lambda _: [],
    )
    service = ManagementService(read_store=store, utc_now=lambda: NOW)
    for subject, reader in (
        ("persona_bindings", "get_bindings_for_persona"),
        ("persona_sessions", "get_sessions_for_persona"),
        ("persona_teaching_sessions", "get_teaching_sessions_for_persona"),
    ):
        rows, obs = _read(service, subject)
        assert obs["status"] == "degraded", obs
        assert reader in obs["degradation_reason"] and "cannot tell" in obs["degradation_reason"]


def test_swallowing_get_methods_still_return_empty_to_other_callers():
    ports = _ports(training_store=_TrainingStore(fail="sessions"),
                   binding_error=RuntimeError("bindings owner offline"))
    assert ports.get_sessions_for_persona("p-draft") == []
    assert ports.get_bindings_for_persona("p-draft") == []


def test_empty_read_still_fails_closed_for_other_subjects():
    store = SimpleNamespace(list_evolution_decisions=lambda: [], list_capital_pools=lambda: [])
    service = ManagementService(read_store=store, utc_now=lambda: NOW)
    assert service.get_context_evolution_decisions()[1]["status"] == "degraded"
    assert service.get_context_capital_pools()[1]["status"] == "degraded"


def test_allowed_actions_tell_empty_set_from_unheld_persona_and_owner_failure():
    personas = [_real_persona("p-draft", "draft"), _real_persona("p-paper", "paper_running")]
    service = _service(_ports(personas=personas))

    actions, obs = service.get_context_persona_allowed_actions("p-draft", record_filter=_TENANT_FILTER)
    assert actions["canActivate"] is True and obs["status"] == "ok"

    # paper_running is not a state the owner derives any action for: the real
    # code returns {} for a persona it holds.
    actions, obs = service.get_context_persona_allowed_actions("p-paper", record_filter=_TENANT_FILTER)
    assert actions == {} and obs["status"] == "ok", obs

    actions, obs = service.get_context_persona_allowed_actions("p-missing", record_filter=_TENANT_FILTER)
    assert actions is None and obs["status"] == "degraded"
    assert "not held by the persona owner" in obs["degradation_reason"]


def test_allowed_actions_owner_read_failure_is_unavailable():
    def broken():
        raise RuntimeError("persona owner offline")

    ports = _ports()
    from services.control_plane.bff.ports.persona_capital_runtime import PersonaFleetPort
    ports.persona_capital_runtime.persona = PersonaFleetPort(records_provider=broken)
    actions, obs = _service(ports).get_context_persona_allowed_actions("p-draft", record_filter=_TENANT_FILTER)
    assert actions is None and obs["status"] == "unavailable", obs


def test_allowed_actions_of_a_foreign_tenant_persona_are_not_returned():
    foreign = dict(_real_persona("p-foreign"), tenant_id="tenant-b")
    service = _service(_ports(personas=[foreign]))
    actions, obs = service.get_context_persona_allowed_actions("p-foreign", record_filter=_TENANT_FILTER)
    assert actions is None and obs["status"] == "degraded"


def test_fleet_surface_is_ok_with_healthy_owners_and_real_row_shapes(monkeypatch):
    personas = [_real_persona("p-draft", "draft"), _real_persona("p-research", "research_only"),
                _real_persona("p-paper", "paper_running")]
    # Shapes taken from the rows the owners hand the fleet in the mgmt-read-001
    # fixtures: none carries owner provenance fields.
    runtime = {"id": "rb-1", "binding_id": "rb-1", "runtime_id": "rt-1", "capital_pool_id": "pool-1",
               "deployment_stage": "paper", "status": "running", "tenant_id": "tenant-a"}
    binding = {"id": "binding-1", "binding_id": "binding-1", "persona_id": "p-paper",
               "capital_pool_id": "pool-1", "status": "active", "tenant_id": "tenant-a"}
    ports = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "personas": personas,
            "runtime_bindings": [runtime],
            "bindings": [binding],
            "capital_pools": [{"id": "pool-1", "pool_id": "pool-1", "tenant_id": "tenant-a"}],
            "evolution_decisions": [{"decision_id": "dec-1", "target_id": "p-other", "tenant_id": "tenant-a"}],
        },
        lifecycle_telemetry_governance_kwargs={
            "telemetry_summaries": {
                "rt-1": {"runtime_id": "rt-1", "pnl": 1.0, "collected_at": NOW, "tenant_id": "tenant-a"},
            },
            "incidents": {},
        },
        persona_training_kwargs={"persona_port": PersonaRegistryReadsPort(store=_TrainingStore())},
    )
    monkeypatch.setattr(bff_main, "read_store", ports)
    monkeypatch.setattr(bff_main, "_management_ai_context_service", _service(ports))
    monkeypatch.setattr(bff_main, "_list_persona_records", lambda *args: personas)
    result = collect()
    surface = result["surfaces"]["persona_fleet"]
    assert surface["status"] == "ok", [o for o in surface["owner_observations"] if o["status"] != "ok"]
    items = result["snippets"]["persona_fleet"]["items"]
    assert sorted(item["id"] for item in items) == ["p-draft", "p-paper", "p-research"]
    # The projection the fleet summary is built from is the same as before.
    paper = next(item for item in items if item["id"] == "p-paper")
    assert [b["id"] for b in paper["bindings"]] == ["binding-1"]
    assert next(item for item in items if item["id"] == "p-draft")["bindings"] == []
