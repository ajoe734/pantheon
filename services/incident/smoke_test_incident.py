"""
Smoke tests for INC-001 — IncidentCase and Postmortem backbone objects.

Smoke groups
------------
S1: Schema validation — IncidentCase objects pass JSON schema
S2: Schema validation — Postmortem objects pass JSON schema
S3: Full incident lifecycle — open → investigating → resolved → closed
S4: Full postmortem lifecycle — draft → review → approved → published
S5: Lineage evidence integrity — all required lineage refs present in artifacts
S6: Store referential integrity — postmortem creation blocked without incident
S7: Evolution decision link — linked_evolution_decision_id set correctly
S8: Persistence round-trip — store survives save + reload cycle

Each step prints PASS / FAIL. Exit code is 0 if all pass.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

from services.incident.incident import (
    IncidentCase,
    IncidentError,
    IncidentStatus,
    IncidentStore,
    Postmortem,
    PostmortemStatus,
    validate_incident_case,
    validate_postmortem,
)

SCHEMA_DIR = Path(__file__).parent
INCIDENT_SCHEMA_PATH = SCHEMA_DIR / "incident_case.schema.json"
POSTMORTEM_SCHEMA_PATH = SCHEMA_DIR / "postmortem.schema.json"

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    status = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_incident(**kw) -> IncidentCase:
    base = dict(
        incident_id="smoke-inc-001",
        title="Smoke: Drawdown threshold breached",
        status="open",
        severity="high",
        created_at="2026-04-10T14:00:00Z",
        binding_id="binding-smoke-01",
        deployment_stage="live",
        deployment_plan_id="plan-smoke-01",
        capital_pool_id="pool-smoke-01",
        persona_capital_binding_id="pcb-smoke-01",
        artifact_id="artifact-smoke-01",
        artifact_version="2.0.0",
        runtime_id="runtime-smoke-01",
        trace_id="trace-smoke-01",
        telemetry_event_ids=["tel-evt-01", "tel-evt-02"],
        evidence_summary="Drawdown exceeded 15% threshold on live binding.",
        lineage_ref="artifact-smoke-01@2.0.0",
    )
    base.update(kw)
    return IncidentCase(**base)


def _make_postmortem(**kw) -> Postmortem:
    base = dict(
        postmortem_id="smoke-pm-001",
        title="Smoke: Drawdown breach postmortem",
        status="draft",
        created_at="2026-04-10T16:00:00Z",
        incident_id="smoke-inc-001",
        binding_id="binding-smoke-01",
        deployment_stage="live",
        deployment_plan_id="plan-smoke-01",
        capital_pool_id="pool-smoke-01",
        persona_capital_binding_id="pcb-smoke-01",
        artifact_id="artifact-smoke-01",
        artifact_version="2.0.0",
        runtime_id="runtime-smoke-01",
        trace_id="trace-smoke-01",
        root_cause="Position sizing model underestimated intraday volatility regime.",
        contributing_factors=["Sudden vol spike", "No vol regime guard in sizing model"],
        timeline=[
            {"ts": "2026-04-10T13:55:00Z", "description": "Vol spike detected by telemetry"},
            {"ts": "2026-04-10T14:00:00Z", "description": "Drawdown threshold breached", "actor": "runtime"},
            {"ts": "2026-04-10T14:03:00Z", "description": "Kill switch activated"},
        ],
        action_items=[
            "Add vol regime guard to position sizing model",
            "Lower drawdown threshold for high-vol regimes",
        ],
        author_ids=["operator-001"],
    )
    base.update(kw)
    return Postmortem(**base)


# ---------------------------------------------------------------------------
# S1: Schema validation — IncidentCase
# ---------------------------------------------------------------------------

def smoke_s1_incident_schema():
    print("\nS1: IncidentCase schema validation")
    if not HAS_JSONSCHEMA:
        check("jsonschema available", False, "install jsonschema to enable schema validation")
        return
    schema = json.loads(INCIDENT_SCHEMA_PATH.read_text())

    inc = _make_incident()
    try:
        jsonschema.validate(instance=inc.to_dict(), schema=schema)
        check("valid IncidentCase passes schema", True)
    except jsonschema.ValidationError as e:
        check("valid IncidentCase passes schema", False, str(e.message))

    # Missing required field
    bad = inc.to_dict()
    del bad["binding_id"]
    try:
        jsonschema.validate(instance=bad, schema=schema)
        check("missing binding_id fails schema", False, "expected failure did not occur")
    except jsonschema.ValidationError:
        check("missing binding_id fails schema", True)

    # Invalid severity
    bad2 = inc.to_dict()
    bad2["severity"] = "extreme"
    try:
        jsonschema.validate(instance=bad2, schema=schema)
        check("invalid severity fails schema", False, "expected failure did not occur")
    except jsonschema.ValidationError:
        check("invalid severity fails schema", True)

    # Invalid deployment_stage
    bad3 = inc.to_dict()
    bad3["deployment_stage"] = "staging"
    try:
        jsonschema.validate(instance=bad3, schema=schema)
        check("invalid deployment_stage fails schema", False, "expected failure did not occur")
    except jsonschema.ValidationError:
        check("invalid deployment_stage fails schema", True)


# ---------------------------------------------------------------------------
# S2: Schema validation — Postmortem
# ---------------------------------------------------------------------------

def smoke_s2_postmortem_schema():
    print("\nS2: Postmortem schema validation")
    if not HAS_JSONSCHEMA:
        check("jsonschema available", False, "install jsonschema to enable schema validation")
        return
    schema = json.loads(POSTMORTEM_SCHEMA_PATH.read_text())

    pm = _make_postmortem()
    try:
        jsonschema.validate(instance=pm.to_dict(), schema=schema)
        check("valid Postmortem passes schema", True)
    except jsonschema.ValidationError as e:
        check("valid Postmortem passes schema", False, str(e.message))

    # Missing incident_id
    bad = pm.to_dict()
    del bad["incident_id"]
    try:
        jsonschema.validate(instance=bad, schema=schema)
        check("missing incident_id fails schema", False, "expected failure did not occur")
    except jsonschema.ValidationError:
        check("missing incident_id fails schema", True)

    # Invalid status
    bad2 = pm.to_dict()
    bad2["status"] = "completed"
    try:
        jsonschema.validate(instance=bad2, schema=schema)
        check("invalid status fails schema", False, "expected failure did not occur")
    except jsonschema.ValidationError:
        check("invalid status fails schema", True)


# ---------------------------------------------------------------------------
# S3: Full incident lifecycle
# ---------------------------------------------------------------------------

def smoke_s3_incident_lifecycle():
    print("\nS3: Incident lifecycle — open → investigating → resolved → closed")
    store = IncidentStore()
    inc = _make_incident()
    store.create_incident(inc)

    check("incident created with status=open", store.get_incident("smoke-inc-001").status == "open")

    updated = store.update_incident_status("smoke-inc-001", "investigating")
    check("transition to investigating", updated.status == "investigating")
    check("is_open() true while investigating", updated.is_open())

    updated = store.update_incident_status("smoke-inc-001", "resolved")
    check("transition to resolved", updated.status == "resolved")
    check("resolved_at set automatically", updated.resolved_at is not None)
    check("is_open() false when resolved", not updated.is_open())

    updated = store.update_incident_status("smoke-inc-001", "closed")
    check("transition to closed", updated.status == "closed")

    # Validate errors are empty for final state
    errors = validate_incident_case(updated)
    check("no validation errors in final state", errors == [], str(errors))


# ---------------------------------------------------------------------------
# S4: Full postmortem lifecycle
# ---------------------------------------------------------------------------

def smoke_s4_postmortem_lifecycle():
    print("\nS4: Postmortem lifecycle — draft → review → approved → published")
    store = IncidentStore()
    store.create_incident(_make_incident())
    store.create_postmortem(_make_postmortem())

    check("postmortem created with status=draft", store.get_postmortem("smoke-pm-001").status == "draft")

    for new_status in ("review", "approved"):
        updated = store.update_postmortem_status("smoke-pm-001", new_status)
        check(f"transition to {new_status}", updated.status == new_status)

    updated = store.update_postmortem_status("smoke-pm-001", "published")
    check("transition to published", updated.status == "published")
    check("published_at set automatically", updated.published_at is not None)
    check("is_published() true", updated.is_published())

    errors = validate_postmortem(updated)
    check("no validation errors in published state", errors == [], str(errors))


# ---------------------------------------------------------------------------
# S5: Lineage evidence integrity
# ---------------------------------------------------------------------------

def smoke_s5_lineage_evidence():
    print("\nS5: Lineage evidence integrity")
    inc = _make_incident()
    pm = _make_postmortem()

    # IncidentCase carries incident_case.runtime_binding edge
    check("IncidentCase.binding_id present", bool(inc.binding_id))
    check("IncidentCase.deployment_stage present", bool(inc.deployment_stage))
    check("IncidentCase.deployment_plan_id present", bool(inc.deployment_plan_id))
    check("IncidentCase.capital_pool_id present", bool(inc.capital_pool_id))
    check("IncidentCase.persona_capital_binding_id present", bool(inc.persona_capital_binding_id))
    check("IncidentCase.artifact_id present", bool(inc.artifact_id))
    check("IncidentCase.artifact_version present", bool(inc.artifact_version))
    check("IncidentCase.runtime_id present", bool(inc.runtime_id))
    check("IncidentCase.trace_id present", bool(inc.trace_id))

    # Postmortem carries postmortem.incident_case edge
    check("Postmortem.incident_id present", bool(pm.incident_id))
    check("Postmortem.binding_id propagated", bool(pm.binding_id))
    check("Postmortem.deployment_stage propagated", bool(pm.deployment_stage))
    check("Postmortem.deployment_plan_id propagated", bool(pm.deployment_plan_id))
    check("Postmortem.capital_pool_id propagated", bool(pm.capital_pool_id))
    check("Postmortem.persona_capital_binding_id propagated", bool(pm.persona_capital_binding_id))
    check("Postmortem.artifact_id propagated", bool(pm.artifact_id))
    check("Postmortem.artifact_version propagated", bool(pm.artifact_version))
    check("Postmortem.runtime_id propagated", bool(pm.runtime_id))
    check("Postmortem.trace_id propagated", bool(pm.trace_id))

    # evolution_decision.postmortem link slot exists
    check("Postmortem.linked_evolution_decision_id slot exists", hasattr(pm, "linked_evolution_decision_id"))

    # validate_* confirm no errors on well-formed objects
    inc_errors = validate_incident_case(inc)
    check("validate_incident_case: no errors", inc_errors == [], str(inc_errors))
    pm_errors = validate_postmortem(pm)
    check("validate_postmortem: no errors", pm_errors == [], str(pm_errors))


# ---------------------------------------------------------------------------
# S6: Store referential integrity
# ---------------------------------------------------------------------------

def smoke_s6_store_referential_integrity():
    print("\nS6: Store referential integrity")
    store = IncidentStore()

    # Postmortem without incident must fail
    try:
        store.create_postmortem(_make_postmortem())
        check("postmortem creation blocked without incident", False, "expected IncidentError not raised")
    except IncidentError:
        check("postmortem creation blocked without incident", True)

    # After creating incident, postmortem succeeds
    store.create_incident(_make_incident())
    store.create_postmortem(_make_postmortem())
    check("postmortem created after incident exists", store.get_postmortem("smoke-pm-001") is not None)

    try:
        store.create_postmortem(
            _make_postmortem(
                postmortem_id="smoke-pm-002",
                binding_id="binding-smoke-other",
                deployment_plan_id="plan-smoke-other",
                persona_capital_binding_id="pcb-smoke-other",
                artifact_id="artifact-smoke-other",
                runtime_id="runtime-smoke-other",
                trace_id="trace-smoke-other",
            )
        )
        check("postmortem propagated evidence must match incident", False, "expected IncidentError not raised")
    except IncidentError:
        check("postmortem propagated evidence must match incident", True)

    # find_postmortem_for_incident works
    pm = store.find_postmortem_for_incident("smoke-inc-001")
    check("find_postmortem_for_incident returns correct record", pm is not None and pm.postmortem_id == "smoke-pm-001")


# ---------------------------------------------------------------------------
# S7: Evolution decision link
# ---------------------------------------------------------------------------

def smoke_s7_evolution_decision_link():
    print("\nS7: Evolution decision link")
    store = IncidentStore()
    store.create_incident(_make_incident())
    store.create_postmortem(_make_postmortem())

    updated = store.link_evolution_decision("smoke-pm-001", "evo-dec-smoke-01")
    check("linked_evolution_decision_id set", updated.linked_evolution_decision_id == "evo-dec-smoke-01")

    # Persists in store
    retrieved = store.require_postmortem("smoke-pm-001")
    check("evolution link persisted in store", retrieved.linked_evolution_decision_id == "evo-dec-smoke-01")


# ---------------------------------------------------------------------------
# S8: Persistence round-trip
# ---------------------------------------------------------------------------

def smoke_s8_persistence_round_trip():
    print("\nS8: Persistence round-trip")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "store.json"

        store = IncidentStore(path=path)
        store.create_incident(_make_incident())
        store.create_postmortem(_make_postmortem())
        store.update_incident_status("smoke-inc-001", "investigating")
        store.link_evolution_decision("smoke-pm-001", "evo-dec-smoke-01")

        # Load into fresh store
        store2 = IncidentStore(path=path)
        inc = store2.get_incident("smoke-inc-001")
        pm = store2.get_postmortem("smoke-pm-001")

        check("incident survives round-trip", inc is not None)
        check("incident status preserved", inc.status == "investigating")
        check("incident binding_id preserved", inc.binding_id == "binding-smoke-01")
        check("postmortem survives round-trip", pm is not None)
        check("postmortem incident_id preserved", pm.incident_id == "smoke-inc-001")
        check("evolution link preserved", pm.linked_evolution_decision_id == "evo-dec-smoke-01")
        check("incident telemetry_event_ids preserved", inc.telemetry_event_ids == ["tel-evt-01", "tel-evt-02"])
        check("postmortem contributing_factors preserved", len(pm.contributing_factors) == 2)
        check("postmortem timeline preserved", len(pm.timeline) == 3)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print("INC-001 Smoke Tests")
    print("=" * 60)

    smoke_s1_incident_schema()
    smoke_s2_postmortem_schema()
    smoke_s3_incident_lifecycle()
    smoke_s4_postmortem_lifecycle()
    smoke_s5_lineage_evidence()
    smoke_s6_store_referential_integrity()
    smoke_s7_evolution_decision_link()
    smoke_s8_persistence_round_trip()

    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"Results: {passed} passed, {failed} failed out of {len(results)} checks")
    if failed:
        print("\nFailed checks:")
        for label, ok, detail in results:
            if not ok:
                print(f"  FAIL: {label}" + (f" — {detail}" if detail else ""))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
