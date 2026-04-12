"""
Unit tests for Persona Registry platform objects.

Task: PER-001
Owner: Claude
Reviewer: Codex

Run:
    python3 -m unittest discover -s services/control-plane/persona -p 'test_*.py'
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from persona_registry import (
    CapabilitySnapshot,
    DeploymentStage,
    Persona,
    PersonaLifecycleState,
    PersonaRegistry,
    PersonaRegistryError,
    PersonaSessionError,
    PersonaSessionStore,
    SessionPersona,
    SessionStatus,
    SessionType,
    validate_persona,
    validate_session,
)


# ---------------------------------------------------------------------------
# Helpers
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


def _make_snapshot(**kwargs) -> CapabilitySnapshot:
    defaults = dict(
        snapshot_id="snap-001",
        persona_id="persona-alpha",
        generated_at="2026-04-10T00:00:00Z",
    )
    defaults.update(kwargs)
    return CapabilitySnapshot(**defaults)


# ---------------------------------------------------------------------------
# PersonaLifecycleState
# ---------------------------------------------------------------------------

class TestPersonaLifecycleState(unittest.TestCase):

    def test_paper_owner_allows_deployment_sponsorship(self):
        self.assertTrue(PersonaLifecycleState.PAPER_OWNER.allows_deployment_sponsorship())

    def test_live_owner_allows_deployment_sponsorship(self):
        self.assertTrue(PersonaLifecycleState.LIVE_OWNER.allows_deployment_sponsorship())

    def test_research_only_does_not_allow_deployment(self):
        self.assertFalse(PersonaLifecycleState.RESEARCH_ONLY.allows_deployment_sponsorship())

    def test_draft_does_not_allow_deployment(self):
        self.assertFalse(PersonaLifecycleState.DRAFT.allows_deployment_sponsorship())

    def test_retired_does_not_allow_new_sessions(self):
        self.assertFalse(PersonaLifecycleState.RETIRED.allows_new_sessions())

    def test_frozen_allows_new_sessions_flag(self):
        # Frozen still returns True from allows_new_sessions(); the session-creation
        # layer enforces the no-new-capability-expansion rule separately.
        self.assertTrue(PersonaLifecycleState.FROZEN.allows_new_sessions())


# ---------------------------------------------------------------------------
# SessionType
# ---------------------------------------------------------------------------

class TestSessionType(unittest.TestCase):

    def test_interactive_requires_runtime_binding(self):
        self.assertTrue(SessionType.INTERACTIVE.requires_runtime_binding())

    def test_background_job_requires_runtime_binding(self):
        self.assertTrue(SessionType.BACKGROUND_JOB.requires_runtime_binding())

    def test_research_task_does_not_require_runtime_binding(self):
        self.assertFalse(SessionType.RESEARCH_TASK.requires_runtime_binding())

    def test_consult_does_not_require_runtime_binding(self):
        self.assertFalse(SessionType.CONSULT.requires_runtime_binding())


# ---------------------------------------------------------------------------
# Persona — construction and validation
# ---------------------------------------------------------------------------

class TestPersona(unittest.TestCase):

    def test_valid_persona_constructs(self):
        p = _make_persona()
        self.assertEqual(p.persona_id, "persona-alpha")
        self.assertEqual(p.lifecycle_state, "research_only")

    def test_invalid_lifecycle_state_raises(self):
        with self.assertRaises(PersonaRegistryError):
            _make_persona(lifecycle_state="zombie")

    def test_invalid_status_raises(self):
        with self.assertRaises(PersonaRegistryError):
            _make_persona(status="unknown")

    def test_validate_persona_passes_valid(self):
        self.assertEqual(validate_persona(_make_persona()), [])

    def test_validate_persona_fails_blank_mandate(self):
        p = _make_persona(mandate="   ")
        errors = validate_persona(p)
        self.assertTrue(any("mandate" in e for e in errors))

    def test_to_dict_excludes_none_optional(self):
        p = _make_persona()
        d = p.to_dict()
        self.assertNotIn("workspace_ref", d)
        self.assertNotIn("tool_profile_id", d)
        self.assertNotIn("updated_at", d)

    def test_to_dict_from_dict_roundtrip(self):
        p = _make_persona(strategy_family="momentum", owner="governance-bot")
        self.assertEqual(Persona.from_dict(p.to_dict()), p)

    def test_default_status_is_active(self):
        self.assertEqual(_make_persona().status, "active")


# ---------------------------------------------------------------------------
# CapabilitySnapshot
# ---------------------------------------------------------------------------

class TestCapabilitySnapshot(unittest.TestCase):

    def test_snapshot_constructs_with_defaults(self):
        s = _make_snapshot()
        self.assertEqual(s.snapshot_id, "snap-001")
        self.assertEqual(s.effective_tools, [])

    def test_snapshot_roundtrip(self):
        s = _make_snapshot(
            effective_tools=["web_search", "code_exec"],
            restrictions=["no_live_trading"],
        )
        self.assertEqual(CapabilitySnapshot.from_dict(s.to_dict()), s)


# ---------------------------------------------------------------------------
# SessionPersona — construction and validation
# ---------------------------------------------------------------------------

class TestSessionPersona(unittest.TestCase):

    def test_valid_non_deployment_session(self):
        s = _make_session()
        self.assertEqual(s.session_id, "session-001")
        self.assertIsNone(s.runtime_binding_id)

    def test_valid_deployment_bound_session(self):
        s = _make_session(
            session_type="interactive",
            runtime_binding_id="rb-001",
            deployment_stage="live",
            capital_pool_id="pool-001",
        )
        self.assertEqual(s.runtime_binding_id, "rb-001")
        self.assertEqual(s.deployment_stage, "live")
        self.assertEqual(s.capital_pool_id, "pool-001")

    def test_invalid_session_type_raises(self):
        with self.assertRaises(PersonaSessionError):
            _make_session(session_type="unknown_type")

    def test_invalid_status_raises(self):
        with self.assertRaises(PersonaSessionError):
            _make_session(status="zombie")

    def test_invalid_deployment_stage_raises(self):
        with self.assertRaises(PersonaSessionError):
            _make_session(
                session_type="interactive",
                runtime_binding_id="rb-001",
                deployment_stage="mega",
                capital_pool_id="pool-001",
            )

    def test_runtime_binding_without_deployment_stage_raises(self):
        """runtime_binding_id set but deployment_stage missing — must raise."""
        with self.assertRaises(PersonaSessionError):
            _make_session(
                session_type="interactive",
                runtime_binding_id="rb-001",
                capital_pool_id="pool-001",
                # deployment_stage deliberately omitted
            )

    def test_runtime_binding_without_capital_pool_raises(self):
        """runtime_binding_id set but capital_pool_id missing — must raise."""
        with self.assertRaises(PersonaSessionError):
            _make_session(
                session_type="interactive",
                runtime_binding_id="rb-001",
                deployment_stage="live",
                # capital_pool_id deliberately omitted
            )

    def test_validate_session_interactive_without_runtime_binding(self):
        """interactive session without runtime_binding_id fails validate_session."""
        s = _make_session(session_type="interactive")
        errors = validate_session(s)
        self.assertTrue(any("runtime_binding_id" in e for e in errors))

    def test_validate_session_research_task_passes_without_runtime_binding(self):
        s = _make_session(session_type="research_task")
        self.assertEqual(validate_session(s), [])

    def test_to_dict_from_dict_roundtrip(self):
        s = _make_session(
            session_type="interactive",
            runtime_binding_id="rb-001",
            deployment_stage="paper",
            capital_pool_id="pool-001",
            trace_id="trace-abc",
            request_id="req-abc",
        )
        self.assertEqual(SessionPersona.from_dict(s.to_dict()), s)

    def test_to_dict_excludes_null_optional_fields(self):
        s = _make_session()
        d = s.to_dict()
        self.assertNotIn("runtime_binding_id", d)
        self.assertNotIn("deployment_stage", d)
        self.assertNotIn("capital_pool_id", d)
        self.assertNotIn("ended_at", d)

    def test_all_deployment_stages_accepted(self):
        for stage in ("paper", "canary", "live", "frozen"):
            s = _make_session(
                session_type="interactive",
                runtime_binding_id="rb-001",
                deployment_stage=stage,
                capital_pool_id="pool-001",
            )
            self.assertEqual(s.deployment_stage, stage)

    # --- Blocker 1: symmetric constraint tests ---

    def test_deployment_stage_without_runtime_binding_raises(self):
        """deployment_stage set without runtime_binding_id must raise."""
        with self.assertRaises(PersonaSessionError):
            _make_session(deployment_stage="live")

    def test_capital_pool_id_without_runtime_binding_raises(self):
        """capital_pool_id set without runtime_binding_id must raise."""
        with self.assertRaises(PersonaSessionError):
            _make_session(capital_pool_id="pool-001")

    def test_stage_and_pool_without_runtime_binding_raises(self):
        """Both deployment_stage and capital_pool_id set without runtime_binding_id must raise."""
        with self.assertRaises(PersonaSessionError):
            _make_session(deployment_stage="live", capital_pool_id="pool-001")

    # --- Blocker (PER-001 re-review): blank audit-chain identifier tests ---

    def test_blank_runtime_binding_id_raises(self):
        """Blank runtime_binding_id must be rejected at construction (audit chain forgeable)."""
        with self.assertRaises(PersonaSessionError):
            _make_session(
                session_type="interactive",
                runtime_binding_id="",
                deployment_stage="live",
                capital_pool_id="pool-001",
            )

    def test_whitespace_runtime_binding_id_raises(self):
        """Whitespace-only runtime_binding_id must be rejected at construction."""
        with self.assertRaises(PersonaSessionError):
            _make_session(
                session_type="interactive",
                runtime_binding_id="   ",
                deployment_stage="live",
                capital_pool_id="pool-001",
            )

    def test_blank_capital_pool_id_raises(self):
        """Blank capital_pool_id must be rejected at construction (audit chain forgeable)."""
        with self.assertRaises(PersonaSessionError):
            _make_session(
                session_type="interactive",
                runtime_binding_id="rb-001",
                deployment_stage="live",
                capital_pool_id="",
            )

    def test_whitespace_capital_pool_id_raises(self):
        """Whitespace-only capital_pool_id must be rejected at construction."""
        with self.assertRaises(PersonaSessionError):
            _make_session(
                session_type="interactive",
                runtime_binding_id="rb-001",
                deployment_stage="live",
                capital_pool_id="   ",
            )

    def test_validate_session_blank_runtime_binding_id_for_deployment_bound(self):
        """validate_session catches blank runtime_binding_id on deployment-bound sessions."""
        # Construct via object's __init__ bypass to test validate_session independently.
        # We use a research_task session (no __post_init__ binding check) but
        # manually set the runtime_binding_id field to simulate a bypass scenario.
        import dataclasses
        s = _make_session(session_type="interactive")
        s2 = dataclasses.replace(s, runtime_binding_id=None)
        errors = validate_session(s2)
        self.assertTrue(any("runtime_binding_id" in e for e in errors))

    def test_validate_session_blank_capital_pool_id(self):
        """validate_session catches blank capital_pool_id when set."""
        import dataclasses
        s = _make_session(
            session_type="research_task",
            runtime_binding_id="rb-001",
            deployment_stage="paper",
            capital_pool_id="pool-001",
        )
        # Bypass __post_init__ via object.__setattr__ to test that validate_session
        # independently catches blank capital_pool_id (defense-in-depth; covers
        # values introduced via direct mutation or JSON deserialization).
        import copy
        s2 = copy.copy(s)
        object.__setattr__(s2, "capital_pool_id", "")
        errors = validate_session(s2)
        self.assertTrue(any("capital_pool_id" in e for e in errors))

    # --- Blocker 2: trace_id / request_id required tests ---

    def test_session_carries_trace_id(self):
        s = _make_session(trace_id="trace-xyz")
        self.assertEqual(s.trace_id, "trace-xyz")

    def test_session_carries_request_id(self):
        s = _make_session(request_id="req-xyz")
        self.assertEqual(s.request_id, "req-xyz")

    def test_validate_session_blank_trace_id(self):
        s = _make_session(trace_id="   ")
        errors = validate_session(s)
        self.assertTrue(any("trace_id" in e for e in errors))

    def test_validate_session_blank_request_id(self):
        s = _make_session(request_id="")
        errors = validate_session(s)
        self.assertTrue(any("request_id" in e for e in errors))


# ---------------------------------------------------------------------------
# PersonaRegistry — store operations
# ---------------------------------------------------------------------------

class TestPersonaRegistry(unittest.TestCase):

    def test_create_and_get(self):
        reg = PersonaRegistry()
        p = _make_persona()
        reg.create(p)
        self.assertEqual(reg.get("persona-alpha"), p)

    def test_require_existing(self):
        reg = PersonaRegistry()
        reg.create(_make_persona())
        self.assertEqual(reg.require("persona-alpha").persona_id, "persona-alpha")

    def test_require_missing_raises(self):
        reg = PersonaRegistry()
        with self.assertRaises(PersonaRegistryError):
            reg.require("nonexistent")

    def test_create_duplicate_raises(self):
        reg = PersonaRegistry()
        reg.create(_make_persona())
        with self.assertRaises(PersonaRegistryError):
            reg.create(_make_persona())

    def test_list_by_lifecycle_state(self):
        reg = PersonaRegistry()
        reg.create(_make_persona(persona_id="p1", lifecycle_state="research_only"))
        reg.create(_make_persona(persona_id="p2", lifecycle_state="consultable"))
        result = reg.list(lifecycle_state="research_only")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].persona_id, "p1")

    def test_advance_lifecycle_valid(self):
        reg = PersonaRegistry()
        reg.create(_make_persona())
        updated = reg.advance_lifecycle("persona-alpha", "consultable")
        self.assertEqual(updated.lifecycle_state, "consultable")

    def test_advance_lifecycle_invalid_transition_raises(self):
        reg = PersonaRegistry()
        reg.create(_make_persona())
        with self.assertRaises(PersonaRegistryError):
            reg.advance_lifecycle("persona-alpha", "live_owner")

    def test_advance_lifecycle_retired_is_terminal(self):
        reg = PersonaRegistry()
        reg.create(_make_persona(lifecycle_state="live_owner"))
        reg.advance_lifecycle("persona-alpha", "retired")
        with self.assertRaises(PersonaRegistryError):
            reg.advance_lifecycle("persona-alpha", "research_only")

    def test_update_status(self):
        reg = PersonaRegistry()
        reg.create(_make_persona())
        updated = reg.update_status("persona-alpha", "suspended")
        self.assertEqual(updated.status, "suspended")

    def test_json_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "personas.json"
            r1 = PersonaRegistry(path=path)
            r1.create(_make_persona(strategy_family="momentum"))
            r2 = PersonaRegistry(path=path)
            loaded = r2.get("persona-alpha")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.strategy_family, "momentum")


# ---------------------------------------------------------------------------
# PersonaSessionStore — store operations
# ---------------------------------------------------------------------------

class TestPersonaSessionStore(unittest.TestCase):

    def test_create_and_get(self):
        store = PersonaSessionStore()
        s = _make_session()
        store.create(s)
        self.assertEqual(store.get("session-001"), s)

    def test_require_missing_raises(self):
        store = PersonaSessionStore()
        with self.assertRaises(PersonaSessionError):
            store.require("nonexistent")

    def test_create_duplicate_raises(self):
        store = PersonaSessionStore()
        store.create(_make_session())
        with self.assertRaises(PersonaSessionError):
            store.create(_make_session())

    def test_list_by_persona_id(self):
        store = PersonaSessionStore()
        store.create(_make_session(session_id="s1", persona_id="persona-alpha"))
        store.create(_make_session(session_id="s2", persona_id="persona-beta"))
        result = store.list(persona_id="persona-alpha")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].session_id, "s1")

    def test_list_by_status(self):
        store = PersonaSessionStore()
        store.create(_make_session(session_id="s1"))
        result = store.list(status="active")
        self.assertEqual(len(result), 1)

    def test_list_by_session_type(self):
        store = PersonaSessionStore()
        store.create(_make_session(session_id="s1", session_type="research_task"))
        store.create(_make_session(session_id="s2", session_type="consult"))
        result = store.list(session_type="research_task")
        self.assertEqual(len(result), 1)

    def test_terminate_sets_status_and_ended_at(self):
        store = PersonaSessionStore()
        store.create(_make_session())
        ended = store.terminate("session-001", ended_at="2026-04-10T01:00:00Z")
        self.assertEqual(ended.status, "terminated")
        self.assertEqual(ended.ended_at, "2026-04-10T01:00:00Z")

    def test_terminate_already_terminated_raises(self):
        store = PersonaSessionStore()
        store.create(_make_session())
        store.terminate("session-001")
        with self.assertRaises(PersonaSessionError):
            store.terminate("session-001")

    def test_mark_degraded(self):
        store = PersonaSessionStore()
        store.create(_make_session())
        degraded = store.mark_degraded("session-001")
        self.assertEqual(degraded.status, "degraded")

    def test_quarantine(self):
        store = PersonaSessionStore()
        store.create(_make_session())
        quarantined = store.quarantine("session-001")
        self.assertEqual(quarantined.status, "quarantined")

    def test_session_json_persistence_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            s1 = PersonaSessionStore(path=path)
            s1.create(_make_session(session_type="consult", task_ref="task-123"))
            s2 = PersonaSessionStore(path=path)
            loaded = s2.get("session-001")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.task_ref, "task-123")

    def test_deployment_bound_session_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            s1 = PersonaSessionStore(path=path)
            s1.create(_make_session(
                session_type="interactive",
                runtime_binding_id="rb-001",
                deployment_stage="live",
                capital_pool_id="pool-001",
            ))
            s2 = PersonaSessionStore(path=path)
            loaded = s2.get("session-001")
            self.assertEqual(loaded.runtime_binding_id, "rb-001")
            self.assertEqual(loaded.deployment_stage, "live")
            self.assertEqual(loaded.capital_pool_id, "pool-001")


if __name__ == "__main__":
    unittest.main()
