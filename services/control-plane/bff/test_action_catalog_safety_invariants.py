"""E2E-R11: operator command-safety invariants over the BFF action catalog.

Destructive / capital-affecting operator commands must stay gated. This is a
regression guard: a future edit that weakens a destructive action's
confirmation, two-man, approval, idempotency, or role requirement fails here.

Invariants (all currently hold):
  1. Every CRITICAL action requires confirm_token AND two_man AND approval AND
     idempotency.
  2. Every named destructive action (rollback execute / hard rollback / kill
     switch / liquidate-all / risk-off / safe-mode) requires confirm_token AND
     idempotency.
  3. No action is fully ungated — every entry has at least one required role.
"""
import unittest

from services.control_plane.bff.action_catalog import _CATALOG_ENTRIES

# Capital-affecting / trading-halting commands whose safety gates must not regress.
DESTRUCTIVE_ACTIONS = {
    "ExecuteRollback",
    "HardRollback",
    "ActivateKillSwitch",
    "LiquidateAll",
    "IssueRiskOff",
    "IssueSafeMode",
}


def _risk(entry) -> str:
    return str(getattr(entry, "risk_level", "")).split(".")[-1].upper()


class TestActionCatalogSafetyInvariants(unittest.TestCase):
    def test_critical_actions_fully_gated(self):
        for e in _CATALOG_ENTRIES:
            if _risk(e) != "CRITICAL":
                continue
            aid = e.action_id
            self.assertTrue(e.requires_confirm_token, f"CRITICAL {aid} missing confirm_token")
            self.assertTrue(e.requires_two_man, f"CRITICAL {aid} missing two_man")
            self.assertTrue(e.requires_approval, f"CRITICAL {aid} missing approval")
            self.assertTrue(e.idempotency_required, f"CRITICAL {aid} missing idempotency")

    def test_destructive_actions_require_confirm_and_idempotency(self):
        by_id = {e.action_id: e for e in _CATALOG_ENTRIES}
        for aid in DESTRUCTIVE_ACTIONS:
            self.assertIn(aid, by_id, f"destructive action {aid} missing from catalog")
            e = by_id[aid]
            self.assertTrue(e.requires_confirm_token, f"destructive {aid} lost confirm_token")
            self.assertTrue(e.idempotency_required, f"destructive {aid} lost idempotency")

    def test_no_action_is_fully_ungated(self):
        ungated = [e.action_id for e in _CATALOG_ENTRIES if not getattr(e, "required_roles", None)]
        self.assertEqual(ungated, [], f"actions with no required roles: {ungated}")


if __name__ == "__main__":
    unittest.main()
