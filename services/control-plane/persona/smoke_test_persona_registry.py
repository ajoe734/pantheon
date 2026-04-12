#!/usr/bin/env python3
"""
Smoke test for Persona Registry platform objects.

Task: PER-001
Owner: Claude
Reviewer: Codex

Run:
    python3 services/control-plane/persona/smoke_test_persona_registry.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from persona_registry import (
    CapabilitySnapshot,
    Persona,
    PersonaRegistry,
    PersonaRegistryError,
    PersonaSessionError,
    PersonaSessionStore,
    SessionPersona,
    validate_persona,
    validate_session,
)

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def check_raises(label: str, exc_type: type, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        FAIL += 1
        print(f"[FAIL] {label} (no exception raised)")
    except exc_type:
        PASS += 1
        print(f"[PASS] {label}")
    except Exception as e:
        FAIL += 1
        print(f"[FAIL] {label} (wrong exception: {e!r})")


# ---------------------------------------------------------------------------
# Persona — construction
# ---------------------------------------------------------------------------

def _make_persona(**kwargs) -> Persona:
    defaults = dict(
        persona_id="persona-alpha",
        name="Alpha Momentum",
        mandate="Execute momentum strategies on US equities",
        lifecycle_state="research_only",
        created_at="2026-04-10T00:00:00Z",
    )
    defaults.update(kwargs)
    return Persona(**defaults)


p = _make_persona()
check("Persona constructs with valid fields", p.persona_id == "persona-alpha")
check("Persona default status is active", p.status == "active")
check("validate_persona passes for valid persona", validate_persona(p) == [])

check_raises(
    "invalid lifecycle_state raises PersonaRegistryError",
    PersonaRegistryError,
    lambda: _make_persona(lifecycle_state="zombie"),
)
check_raises(
    "invalid status raises PersonaRegistryError",
    PersonaRegistryError,
    lambda: _make_persona(status="unknown"),
)

p_full = _make_persona(
    strategy_family="momentum",
    workspace_ref="ws-001",
    tool_profile_id="tp-001",
    owner="governance-bot",
)
check(
    "Persona to_dict/from_dict roundtrip",
    Persona.from_dict(p_full.to_dict()) == p_full,
)
check(
    "to_dict excludes None optional fields",
    "workspace_ref" not in _make_persona().to_dict(),
)

# ---------------------------------------------------------------------------
# CapabilitySnapshot
# ---------------------------------------------------------------------------

snap = CapabilitySnapshot(
    snapshot_id="snap-001",
    persona_id="persona-alpha",
    generated_at="2026-04-10T00:00:00Z",
    effective_tools=["web_search", "code_exec"],
    restrictions=["no_live_trading"],
    source_refs=["route-policy-v1", "rbac-rules-v3"],
)
check("CapabilitySnapshot constructs correctly", snap.snapshot_id == "snap-001")
check("effective_tools captured", "web_search" in snap.effective_tools)
check("CapabilitySnapshot roundtrip", CapabilitySnapshot.from_dict(snap.to_dict()) == snap)

# ---------------------------------------------------------------------------
# SessionPersona — non-deployment session
# ---------------------------------------------------------------------------

def _make_session(**kwargs) -> SessionPersona:
    defaults = dict(
        session_id="session-001",
        persona_id="persona-alpha",
        session_type="research_task",
        status="active",
        started_at="2026-04-10T00:00:00Z",
        capability_snapshot_id="snap-001",
        trace_id="trace-001",
        request_id="req-001",
    )
    defaults.update(kwargs)
    return SessionPersona(**defaults)


s_research = _make_session()
check("SessionPersona (research_task) constructs", s_research.session_id == "session-001")
check("research_task has no runtime_binding_id", s_research.runtime_binding_id is None)
check("validate_session passes for research_task", validate_session(s_research) == [])

# ---------------------------------------------------------------------------
# SessionPersona — deployment-bound session (PER-001 first-class fields)
# ---------------------------------------------------------------------------

s_live = _make_session(
    session_id="session-002",
    session_type="interactive",
    runtime_binding_id="rb-001",
    deployment_stage="live",
    capital_pool_id="pool-001",
    trace_id="trace-abc",
    request_id="req-001",
)
check("interactive session with runtime_binding_id constructs", s_live.runtime_binding_id == "rb-001")
check("deployment_stage is first-class field", s_live.deployment_stage == "live")
check("capital_pool_id is first-class field", s_live.capital_pool_id == "pool-001")
check("validate_session passes for deployment-bound session", validate_session(s_live) == [])
check(
    "deployment-bound session roundtrip",
    SessionPersona.from_dict(s_live.to_dict()) == s_live,
)

s_paper = _make_session(
    session_id="session-003",
    session_type="background_job",
    runtime_binding_id="rb-002",
    deployment_stage="paper",
    capital_pool_id="pool-002",
)
check("paper stage background_job session constructs", s_paper.deployment_stage == "paper")

# Consistency rules
check_raises(
    "runtime_binding_id without deployment_stage raises",
    PersonaSessionError,
    lambda: _make_session(
        session_type="interactive",
        runtime_binding_id="rb-001",
        capital_pool_id="pool-001",
    ),
)
check_raises(
    "runtime_binding_id without capital_pool_id raises",
    PersonaSessionError,
    lambda: _make_session(
        session_type="interactive",
        runtime_binding_id="rb-001",
        deployment_stage="live",
    ),
)
# Inverse: stage/pool without runtime_binding_id must also raise
check_raises(
    "deployment_stage without runtime_binding_id raises (inverse constraint)",
    PersonaSessionError,
    lambda: _make_session(deployment_stage="live"),
)
check_raises(
    "capital_pool_id without runtime_binding_id raises (inverse constraint)",
    PersonaSessionError,
    lambda: _make_session(capital_pool_id="pool-001"),
)

# trace_id / request_id are required; blank strings fail validate_session
s_blank_trace = _make_session(trace_id="   ")
check(
    "validate_session catches blank trace_id",
    any("trace_id" in e for e in validate_session(s_blank_trace)),
)
s_blank_request = _make_session(request_id="")
check(
    "validate_session catches blank request_id",
    any("request_id" in e for e in validate_session(s_blank_request)),
)

# validate_session catches interactive without runtime_binding_id
s_bad_interactive = _make_session(session_type="interactive")
errors = validate_session(s_bad_interactive)
check(
    "validate_session catches interactive missing runtime_binding_id",
    any("runtime_binding_id" in e for e in errors),
)

# Blank audit-chain identifiers must be rejected at construction (PER-001 re-review blocker)
check_raises(
    "blank runtime_binding_id rejected at construction",
    PersonaSessionError,
    lambda: _make_session(
        session_type="interactive",
        runtime_binding_id="",
        deployment_stage="live",
        capital_pool_id="pool-001",
    ),
)
check_raises(
    "whitespace runtime_binding_id rejected at construction",
    PersonaSessionError,
    lambda: _make_session(
        session_type="interactive",
        runtime_binding_id="   ",
        deployment_stage="live",
        capital_pool_id="pool-001",
    ),
)
check_raises(
    "blank capital_pool_id rejected at construction",
    PersonaSessionError,
    lambda: _make_session(
        session_type="interactive",
        runtime_binding_id="rb-001",
        deployment_stage="live",
        capital_pool_id="",
    ),
)
check_raises(
    "whitespace capital_pool_id rejected at construction",
    PersonaSessionError,
    lambda: _make_session(
        session_type="interactive",
        runtime_binding_id="rb-001",
        deployment_stage="live",
        capital_pool_id="   ",
    ),
)
# validate_session independently catches blank capital_pool_id
import dataclasses as _dataclasses
_s_blank_pool = _make_session(
    session_type="research_task",
    runtime_binding_id="rb-001",
    deployment_stage="paper",
    capital_pool_id="pool-001",
)
# Bypass __post_init__ via object.__setattr__ to test that validate_session
# independently catches blank capital_pool_id (defense-in-depth; covers
# values introduced via direct mutation or JSON deserialization).
import copy as _copy
_s_blank_pool2 = _copy.copy(_s_blank_pool)
object.__setattr__(_s_blank_pool2, "capital_pool_id", "")
check(
    "validate_session catches blank capital_pool_id",
    any("capital_pool_id" in e for e in validate_session(_s_blank_pool2)),
)

# ---------------------------------------------------------------------------
# PersonaRegistry — lifecycle
# ---------------------------------------------------------------------------

reg = PersonaRegistry()
reg.create(_make_persona())
check("registry create and get", reg.get("persona-alpha") is not None)

check_raises(
    "duplicate persona_id raises",
    PersonaRegistryError,
    lambda: reg.create(_make_persona()),
)
check_raises(
    "require missing raises",
    PersonaRegistryError,
    lambda: reg.require("nonexistent"),
)

# Lifecycle transitions
reg2 = PersonaRegistry()
reg2.create(_make_persona())
p1 = reg2.advance_lifecycle("persona-alpha", "consultable")
check("research_only -> consultable transition", p1.lifecycle_state == "consultable")
p2 = reg2.advance_lifecycle("persona-alpha", "paper_owner")
check("consultable -> paper_owner transition", p2.lifecycle_state == "paper_owner")
p3 = reg2.advance_lifecycle("persona-alpha", "live_owner")
check("paper_owner -> live_owner transition", p3.lifecycle_state == "live_owner")
p4 = reg2.advance_lifecycle("persona-alpha", "retired")
check("live_owner -> retired transition", p4.lifecycle_state == "retired")

check_raises(
    "retired -> research_only invalid transition raises",
    PersonaRegistryError,
    lambda: reg2.advance_lifecycle("persona-alpha", "research_only"),
)

def _check_draft_to_live_owner():
    r = PersonaRegistry()
    r.create(_make_persona(lifecycle_state="draft"))
    r.advance_lifecycle("persona-alpha", "live_owner")

check_raises(
    "draft -> live_owner invalid transition raises",
    PersonaRegistryError,
    _check_draft_to_live_owner,
)

# Frozen path
reg3 = PersonaRegistry()
reg3.create(_make_persona())
reg3.advance_lifecycle("persona-alpha", "frozen")
p_frozen = reg3.require("persona-alpha")
check("lifecycle -> frozen succeeds", p_frozen.lifecycle_state == "frozen")
revalidated = reg3.advance_lifecycle("persona-alpha", "research_only")
check("frozen -> research_only (revalidation) succeeds", revalidated.lifecycle_state == "research_only")

# List filtering
reg4 = PersonaRegistry()
reg4.create(_make_persona(persona_id="p1", lifecycle_state="research_only"))
reg4.create(_make_persona(persona_id="p2", lifecycle_state="consultable"))
check("list by lifecycle_state filters", len(reg4.list(lifecycle_state="research_only")) == 1)
check("list returns all without filter", len(reg4.list()) == 2)

# Persistence
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "personas.json"
    r1 = PersonaRegistry(path=path)
    r1.create(_make_persona(strategy_family="momentum"))
    r2 = PersonaRegistry(path=path)
    loaded_p = r2.get("persona-alpha")
    check("PersonaRegistry JSON persistence roundtrip", loaded_p is not None and loaded_p.strategy_family == "momentum")

# ---------------------------------------------------------------------------
# PersonaSessionStore
# ---------------------------------------------------------------------------

ss = PersonaSessionStore()
ss.create(_make_session())
check("session store create and get", ss.get("session-001") is not None)

check_raises(
    "duplicate session_id raises",
    PersonaSessionError,
    lambda: ss.create(_make_session()),
)

# Terminate
ss2 = PersonaSessionStore()
ss2.create(_make_session())
terminated = ss2.terminate("session-001", ended_at="2026-04-10T01:00:00Z")
check("terminate sets status=terminated", terminated.status == "terminated")
check("terminate sets ended_at", terminated.ended_at == "2026-04-10T01:00:00Z")

check_raises(
    "terminate already-terminated session raises",
    PersonaSessionError,
    lambda: ss2.terminate("session-001"),
)

# Degrade and quarantine
ss3 = PersonaSessionStore()
ss3.create(_make_session())
degraded = ss3.mark_degraded("session-001")
check("mark_degraded sets status=degraded", degraded.status == "degraded")

ss4 = PersonaSessionStore()
ss4.create(_make_session())
quarantined = ss4.quarantine("session-001")
check("quarantine sets status=quarantined", quarantined.status == "quarantined")

# List filtering
ss5 = PersonaSessionStore()
ss5.create(_make_session(session_id="s1", persona_id="persona-alpha", session_type="research_task"))
ss5.create(_make_session(session_id="s2", persona_id="persona-beta", session_type="consult"))
check("list by persona_id", len(ss5.list(persona_id="persona-alpha")) == 1)
check("list by session_type", len(ss5.list(session_type="consult")) == 1)
check("list all", len(ss5.list()) == 2)

# Deployment-bound session persistence (PER-001 first-class fields)
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "sessions.json"
    ps1 = PersonaSessionStore(path=path)
    ps1.create(_make_session(
        session_type="interactive",
        runtime_binding_id="rb-001",
        deployment_stage="live",
        capital_pool_id="pool-001",
        trace_id="trace-abc",
        request_id="req-abc",
    ))
    ps2 = PersonaSessionStore(path=path)
    loaded_s = ps2.get("session-001")
    check("deployment-bound session persists runtime_binding_id", loaded_s is not None and loaded_s.runtime_binding_id == "rb-001")
    check("deployment-bound session persists deployment_stage", loaded_s is not None and loaded_s.deployment_stage == "live")
    check("deployment-bound session persists capital_pool_id", loaded_s is not None and loaded_s.capital_pool_id == "pool-001")
    check("deployment-bound session persists trace_id", loaded_s is not None and loaded_s.trace_id == "trace-abc")
    check("deployment-bound session persists request_id", loaded_s is not None and loaded_s.request_id == "req-abc")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

total = PASS + FAIL
print()
print("SUMMARY")
print(f"{PASS}/{total} checks passed")
if FAIL:
    sys.exit(1)
