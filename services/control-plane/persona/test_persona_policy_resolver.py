"""
Unit tests for PersonaPolicyResolver.

Task:     MPOS-P1-PER-001
Owner:    Claude
Reviewer: Codex

Acceptance coverage
-------------------
1. Allowed session: all policies resolve, capital eligible
2. Denied tool: tool_profile_id unknown → tool access denied (fail closed)
3. Denied capital scope: binding exists but scope is insufficient
4. Suspended persona: all capabilities denied regardless of catalog

Run:
    python3 -m unittest discover -s services/control-plane/persona -p 'test_persona_policy_resolver.py'
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "control-plane" / "governance"))

from persona_policy_resolver import (
    ConsultPolicy,
    PersonaPolicyResolver,
    PolicyResolutionResult,
    RoutePolicy,
    ToolProfile,
)
from persona_registry import (
    CapabilitySnapshot,
    Persona,
    utc_now,
)


# ---------------------------------------------------------------------------
# Stub binding store for capital eligibility tests
# ---------------------------------------------------------------------------

class _StubBindingStore:
    """Minimal stub that mimics PersonaCapitalBindingStore.persona_may_deploy_to."""

    def __init__(self, grants: dict[tuple[str, str, str], bool]) -> None:
        # grants: {(persona_id, capital_pool_id, target_scope) -> bool}
        self._grants = grants

    def persona_may_deploy_to(
        self, *, persona_id: str, capital_pool_id: str, target_scope: str
    ) -> bool:
        return self._grants.get((persona_id, capital_pool_id, target_scope), False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_persona(**kwargs) -> Persona:
    defaults = dict(
        persona_id="p-test",
        name="Test Persona",
        mandate="Test mandate",
        lifecycle_state="consultable",
        created_at="2026-06-01T00:00:00Z",
        tool_profile_id="tp-standard",
        route_policy_id="rp-standard",
        consult_policy_id="cp-standard",
        status="active",
    )
    defaults.update(kwargs)
    return Persona(**defaults)


def _make_resolver(**kwargs) -> PersonaPolicyResolver:
    """Return a resolver pre-populated with standard catalog entries."""
    defaults = dict(
        tool_profiles={
            "tp-standard": ToolProfile(
                profile_id="tp-standard",
                allowed_tools=["web_search", "code_exec"],
            ),
        },
        route_policies={
            "rp-standard": RoutePolicy(
                policy_id="rp-standard",
                allowed_workflows=["trade", "monitor"],
                allowed_skills=["status-summary"],
            ),
        },
        consult_policies={
            "cp-standard": ConsultPolicy(
                policy_id="cp-standard",
                allowed_skills=["market-analysis", "governance-review"],
            ),
        },
    )
    defaults.update(kwargs)
    return PersonaPolicyResolver(**defaults)


# ---------------------------------------------------------------------------
# Acceptance case 1: allowed session — all policies resolve
# ---------------------------------------------------------------------------

class TestAllowedSession(unittest.TestCase):

    def setUp(self):
        self.resolver = _make_resolver()
        self.persona = _make_persona()

    def test_resolve_returns_non_denied_result(self):
        result = self.resolver.resolve(self.persona)
        self.assertFalse(result.denied)
        self.assertEqual(result.denial_reasons, [])

    def test_effective_tools_populated(self):
        result = self.resolver.resolve(self.persona)
        self.assertIn("web_search", result.effective_tools)
        self.assertIn("code_exec", result.effective_tools)

    def test_effective_workflows_populated(self):
        result = self.resolver.resolve(self.persona)
        self.assertIn("trade", result.effective_workflows)
        self.assertIn("monitor", result.effective_workflows)

    def test_effective_skills_include_route_and_consult(self):
        result = self.resolver.resolve(self.persona)
        # from route policy
        self.assertIn("status-summary", result.effective_skills)
        # from consult policy
        self.assertIn("market-analysis", result.effective_skills)
        self.assertIn("governance-review", result.effective_skills)

    def test_skills_deduplicated_across_policies(self):
        # Put the same skill in both route and consult policies.
        resolver = _make_resolver(
            route_policies={
                "rp-standard": RoutePolicy("rp-standard", allowed_skills=["shared-skill"]),
            },
            consult_policies={
                "cp-standard": ConsultPolicy("cp-standard", allowed_skills=["shared-skill"]),
            },
        )
        result = resolver.resolve(self.persona)
        self.assertEqual(result.effective_skills.count("shared-skill"), 1)

    def test_source_refs_record_all_resolved_policy_ids(self):
        result = self.resolver.resolve(self.persona)
        self.assertIn("tool_profile:tp-standard", result.source_refs)
        self.assertIn("route_policy:rp-standard", result.source_refs)
        self.assertIn("consult_policy:cp-standard", result.source_refs)

    def test_resolve_to_snapshot_returns_capability_snapshot(self):
        snapshot = self.resolver.resolve_to_snapshot(
            self.persona,
            snapshot_id="snap-001",
            generated_at="2026-06-01T00:00:00Z",
        )
        self.assertIsInstance(snapshot, CapabilitySnapshot)
        self.assertEqual(snapshot.snapshot_id, "snap-001")
        self.assertEqual(snapshot.persona_id, "p-test")
        self.assertIn("web_search", snapshot.effective_tools)
        self.assertEqual(snapshot.denial_reasons, [])

    def test_persona_with_no_policy_ids_returns_empty_but_not_denied(self):
        bare_persona = _make_persona(
            tool_profile_id=None,
            route_policy_id=None,
            consult_policy_id=None,
        )
        result = self.resolver.resolve(bare_persona)
        self.assertFalse(result.denied)
        self.assertEqual(result.effective_tools, [])
        self.assertEqual(result.effective_skills, [])
        self.assertEqual(result.effective_workflows, [])


# ---------------------------------------------------------------------------
# Acceptance case 2: denied tool — unknown tool_profile_id fails closed
# ---------------------------------------------------------------------------

class TestDeniedTool(unittest.TestCase):

    def test_unknown_tool_profile_id_produces_denial_reason(self):
        resolver = _make_resolver()
        persona = _make_persona(tool_profile_id="tp-does-not-exist")
        result = resolver.resolve(persona)
        self.assertTrue(result.denied)
        self.assertTrue(
            any("tp-does-not-exist" in r for r in result.denial_reasons),
            msg=f"Expected denial reason mentioning the unknown profile ID; got: {result.denial_reasons}",
        )

    def test_unknown_tool_profile_id_yields_empty_tools(self):
        resolver = _make_resolver()
        persona = _make_persona(tool_profile_id="tp-unknown")
        result = resolver.resolve(persona)
        self.assertEqual(result.effective_tools, [])

    def test_unknown_tool_profile_does_not_deny_other_buckets(self):
        """Route and consult policies still resolve even when the tool profile is unknown."""
        resolver = _make_resolver()
        persona = _make_persona(tool_profile_id="tp-unknown")
        result = resolver.resolve(persona)
        # Workflows and skills are still populated from the known policies.
        self.assertIn("trade", result.effective_workflows)
        self.assertIn("market-analysis", result.effective_skills)

    def test_unknown_route_policy_id_produces_denial_reason(self):
        resolver = _make_resolver()
        persona = _make_persona(route_policy_id="rp-unknown")
        result = resolver.resolve(persona)
        self.assertTrue(result.denied)
        self.assertTrue(
            any("rp-unknown" in r for r in result.denial_reasons),
        )

    def test_unknown_route_policy_yields_empty_workflows(self):
        resolver = _make_resolver()
        persona = _make_persona(route_policy_id="rp-unknown")
        result = resolver.resolve(persona)
        self.assertEqual(result.effective_workflows, [])

    def test_unknown_consult_policy_id_produces_denial_reason(self):
        resolver = _make_resolver()
        persona = _make_persona(consult_policy_id="cp-unknown")
        result = resolver.resolve(persona)
        self.assertTrue(result.denied)
        self.assertTrue(
            any("cp-unknown" in r for r in result.denial_reasons),
        )

    def test_all_three_unknown_produces_three_denial_reasons(self):
        resolver = _make_resolver()
        persona = _make_persona(
            tool_profile_id="tp-x",
            route_policy_id="rp-x",
            consult_policy_id="cp-x",
        )
        result = resolver.resolve(persona)
        self.assertEqual(len(result.denial_reasons), 3)

    def test_snapshot_denial_reasons_propagated(self):
        resolver = _make_resolver()
        persona = _make_persona(tool_profile_id="tp-unknown")
        snapshot = resolver.resolve_to_snapshot(
            persona,
            snapshot_id="snap-002",
            generated_at="2026-06-01T00:00:00Z",
        )
        self.assertTrue(len(snapshot.denial_reasons) > 0)

    def test_empty_catalog_denies_all_set_policy_ids(self):
        resolver = PersonaPolicyResolver()  # empty catalog
        persona = _make_persona()  # has all three policy IDs set
        result = resolver.resolve(persona)
        self.assertTrue(result.denied)
        self.assertEqual(len(result.denial_reasons), 3)
        self.assertEqual(result.effective_tools, [])
        self.assertEqual(result.effective_skills, [])
        self.assertEqual(result.effective_workflows, [])


# ---------------------------------------------------------------------------
# Acceptance case 3: denied capital scope
# ---------------------------------------------------------------------------

class TestDeniedCapitalScope(unittest.TestCase):

    def test_no_binding_for_pool_adds_denial_reason(self):
        binding_store = _StubBindingStore(grants={})  # no grants at all
        resolver = _make_resolver(binding_store=binding_store)
        persona = _make_persona()
        result = resolver.resolve(
            persona,
            capital_pool_id="pool-001",
            target_scope="live",
        )
        self.assertTrue(result.denied)
        self.assertTrue(
            any("capital eligibility denied" in r for r in result.denial_reasons),
        )

    def test_insufficient_scope_adds_denial_reason(self):
        # Has paper-level grant but requests live scope.
        binding_store = _StubBindingStore(
            grants={("p-test", "pool-001", "paper"): True}
        )
        resolver = _make_resolver(binding_store=binding_store)
        persona = _make_persona()
        result = resolver.resolve(
            persona,
            capital_pool_id="pool-001",
            target_scope="live",  # not granted
        )
        self.assertTrue(result.denied)
        self.assertTrue(
            any("pool-001" in r and "live" in r for r in result.denial_reasons),
        )

    def test_sufficient_scope_does_not_deny(self):
        binding_store = _StubBindingStore(
            grants={("p-test", "pool-001", "live"): True}
        )
        resolver = _make_resolver(binding_store=binding_store)
        persona = _make_persona()
        result = resolver.resolve(
            persona,
            capital_pool_id="pool-001",
            target_scope="live",
        )
        self.assertFalse(result.denied)
        self.assertIn("capital_binding:pool-001:live", result.source_refs)

    def test_capital_eligibility_not_checked_without_pool_id(self):
        """When capital_pool_id is absent, binding_store is not consulted."""
        binding_store = _StubBindingStore(grants={})
        resolver = _make_resolver(binding_store=binding_store)
        persona = _make_persona()
        result = resolver.resolve(persona)  # no capital_pool_id / target_scope
        self.assertFalse(result.denied)
        self.assertFalse(
            any("capital" in r for r in result.denial_reasons)
        )

    def test_capital_eligibility_not_checked_without_binding_store(self):
        """When no binding_store is configured, capital checks are skipped."""
        resolver = _make_resolver()  # no binding_store
        persona = _make_persona()
        result = resolver.resolve(
            persona,
            capital_pool_id="pool-001",
            target_scope="live",
        )
        self.assertFalse(result.denied)

    def test_capital_denial_does_not_empty_other_buckets(self):
        """Capital eligibility failure adds a denial reason but doesn't clear tools/skills."""
        binding_store = _StubBindingStore(grants={})
        resolver = _make_resolver(binding_store=binding_store)
        persona = _make_persona()
        result = resolver.resolve(
            persona,
            capital_pool_id="pool-001",
            target_scope="live",
        )
        # Tools and skills still populated from valid policies.
        self.assertIn("web_search", result.effective_tools)
        self.assertIn("market-analysis", result.effective_skills)
        # But capital is denied.
        self.assertTrue(any("capital eligibility denied" in r for r in result.denial_reasons))


# ---------------------------------------------------------------------------
# Acceptance case 4: suspended persona
# ---------------------------------------------------------------------------

class TestSuspendedPersona(unittest.TestCase):

    def test_suspended_persona_is_fully_denied(self):
        resolver = _make_resolver()
        persona = _make_persona(status="suspended")
        result = resolver.resolve(persona)
        self.assertTrue(result.denied)
        self.assertTrue(
            any("suspended" in r for r in result.denial_reasons),
        )

    def test_suspended_persona_yields_no_tools(self):
        resolver = _make_resolver()
        persona = _make_persona(status="suspended")
        result = resolver.resolve(persona)
        self.assertEqual(result.effective_tools, [])

    def test_suspended_persona_yields_no_skills(self):
        resolver = _make_resolver()
        persona = _make_persona(status="suspended")
        result = resolver.resolve(persona)
        self.assertEqual(result.effective_skills, [])

    def test_suspended_persona_yields_no_workflows(self):
        resolver = _make_resolver()
        persona = _make_persona(status="suspended")
        result = resolver.resolve(persona)
        self.assertEqual(result.effective_workflows, [])

    def test_suspended_persona_has_no_source_refs(self):
        resolver = _make_resolver()
        persona = _make_persona(status="suspended")
        result = resolver.resolve(persona)
        self.assertEqual(result.source_refs, [])

    def test_suspended_persona_denial_message_includes_persona_id(self):
        resolver = _make_resolver()
        persona = _make_persona(persona_id="p-alpha", status="suspended")
        result = resolver.resolve(persona)
        self.assertTrue(
            any("p-alpha" in r for r in result.denial_reasons),
        )

    def test_suspended_overrides_capital_eligibility(self):
        """Even if capital grant exists, a suspended persona is fully denied."""
        binding_store = _StubBindingStore(
            grants={("p-test", "pool-001", "live"): True}
        )
        resolver = _make_resolver(binding_store=binding_store)
        persona = _make_persona(status="suspended")
        result = resolver.resolve(
            persona,
            capital_pool_id="pool-001",
            target_scope="live",
        )
        self.assertTrue(result.denied)
        # The only denial reason must be suspension (no capital check attempted).
        self.assertEqual(len(result.denial_reasons), 1)
        self.assertIn("suspended", result.denial_reasons[0])

    def test_snapshot_for_suspended_persona_has_denial_reasons(self):
        resolver = _make_resolver()
        persona = _make_persona(status="suspended")
        snapshot = resolver.resolve_to_snapshot(
            persona,
            snapshot_id="snap-003",
            generated_at="2026-06-01T00:00:00Z",
        )
        self.assertTrue(len(snapshot.denial_reasons) > 0)
        self.assertEqual(snapshot.effective_tools, [])


# ---------------------------------------------------------------------------
# PolicyResolutionResult — unit tests
# ---------------------------------------------------------------------------

class TestPolicyResolutionResult(unittest.TestCase):

    def test_denied_false_when_no_denial_reasons(self):
        result = PolicyResolutionResult(
            effective_tools=["tool-a"],
        )
        self.assertFalse(result.denied)

    def test_denied_true_when_denial_reasons_present(self):
        result = PolicyResolutionResult(
            denial_reasons=["something went wrong"],
        )
        self.assertTrue(result.denied)


if __name__ == "__main__":
    unittest.main()
