from __future__ import annotations

import unittest

from rewrite.task_contract import (
    acceptance_identity_mentions,
    validate_reassignment_against_acceptance,
    validate_role_based_acceptance,
)


class TaskContractTests(unittest.TestCase):
    def test_role_based_acceptance_is_allowed(self) -> None:
        validate_role_based_acceptance(
            ["Assigned reviewer approves the exact PR head."],
            ["Codex2", "Antigravity"],
        )

    def test_configured_identity_in_new_acceptance_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Codex2"):
            validate_role_based_acceptance(
                ["Codex2 independently approves the exact head."],
                ["Codex", "Codex2"],
            )

    def test_reassignment_rejects_changed_identity_pin(self) -> None:
        task = {
            "owner": "Claude",
            "reviewer": "Codex2",
            "acceptance": ["Codex2 independently approves the exact head."],
        }
        with self.assertRaisesRegex(ValueError, "supersede"):
            validate_reassignment_against_acceptance(
                task,
                new_owner="Claude",
                new_reviewer="Antigravity",
            )

    def test_unrelated_identity_does_not_block_owner_move(self) -> None:
        task = {
            "owner": "Claude",
            "reviewer": "Codex2",
            "acceptance": ["Codex2 independently approves the exact head."],
        }
        validate_reassignment_against_acceptance(
            task,
            new_owner="Antigravity",
            new_reviewer="Codex2",
        )
        self.assertIn(
            "Codex2",
            acceptance_identity_mentions(task["acceptance"], ["Codex2"]),
        )


if __name__ == "__main__":
    unittest.main()
