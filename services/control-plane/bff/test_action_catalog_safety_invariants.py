"""E2E-R11: operator command-safety invariants over the BFF action catalog.

Named destructive commands retain confirmation and idempotency, and every
action retains role requirements. Capital and execution owner tests cover
approval checks before actual effects.

Invariants:
  1. Every named destructive action (rollback execute / hard rollback / kill
      switch / liquidate-all / risk-off / safe-mode) requires confirm_token AND
      idempotency.
  2. No action is fully ungated — every entry has at least one required role.

Risk labels alone do not prescribe approval workflows. Requirements follow
the actual owner effect; capital execution is checked by its owner.
"""
import unittest

from action_catalog import _CATALOG_ENTRIES

# Capital-affecting / trading-halting commands whose safety gates must not regress.
DESTRUCTIVE_ACTIONS = {
    "ExecuteRollback",
    "HardRollback",
    "ActivateKillSwitch",
    "LiquidateAll",
    "IssueRiskOff",
    "IssueSafeMode",
}


class TestActionCatalogSafetyInvariants(unittest.TestCase):
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
