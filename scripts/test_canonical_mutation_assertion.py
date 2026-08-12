from __future__ import annotations

import sys
import unittest
import base64
import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".orchestrator"))

from canonical_mutation_assertion import (  # noqa: E402
    DEV_BRIDGE_CONSUMED_KEY,
    CONSUMED_KEY,
    issue_assertion,
    migrate_legacy_consumed_ledgers,
    public_key_bytes,
    validate_signing_key_pair,
    verify_and_consume,
)


class CanonicalMutationAssertionTests(unittest.TestCase):
    key = b"k" * 32
    task_id = "AUTH-P0-001"
    args = [task_id, "go"]
    old = {"owner": "Codex", "reviewer": "Claude", "status": "todo", "generation": 1}
    new = {"owner": "Codex", "reviewer": "Claude", "status": "in_progress", "generation": 1}

    def assertion(self):
        return issue_assertion(
            operator_id="op-1",
            control_activation_id="activation-1",
            task_id=self.task_id,
            action="start",
            args=self.args,
            old_assignment=self.old,
            new_assignment=self.new,
            reason="operator approved exact start",
            key=self.key,
        )

    def verify(self, assertion, state):
        verify_and_consume(
            assertion,
            state=state,
            task_id=self.task_id,
            action="start",
            args=self.args,
            old_assignment=self.old,
            new_assignment=self.new,
            key=self.key,
        )

    def test_valid_once_then_replay_rejected(self):
        state = {}
        assertion = self.assertion()
        self.verify(assertion, state)
        with self.assertRaisesRegex(ValueError, "already been consumed"):
            self.verify(assertion, state)

    def test_expiry_rejected(self):
        assertion = self.assertion()
        expired_now = datetime.now(timezone.utc) + timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError, "not currently valid"):
            verify_and_consume(
                assertion,
                state={},
                task_id=self.task_id,
                action="start",
                args=self.args,
                old_assignment=self.old,
                new_assignment=self.new,
                key=self.key,
                now=expired_now,
            )

    def test_task_action_args_and_assignment_binding_rejected(self):
        cases = [
            {"task_id": "OTHER"},
            {"action": "progress"},
            {"args": [self.task_id, "different"]},
            {"old_assignment": self.old | {"status": "blocked"}},
            {"new_assignment": self.new | {"status": "review"}},
            {"old_assignment": self.old | {"generation": 3}},
        ]
        for override in cases:
            kwargs = {
                "state": {}, "task_id": self.task_id, "action": "start",
                "args": self.args, "old_assignment": self.old,
                "new_assignment": self.new, "key": self.key,
            }
            kwargs.update(override)
            with self.subTest(override=override), self.assertRaisesRegex(ValueError, "binding failed"):
                verify_and_consume(self.assertion(), **kwargs)

    def test_forged_signature_rejected(self):
        assertion = deepcopy(self.assertion())
        assertion["reason"] = "forged"
        with self.assertRaisesRegex(ValueError, "signature verification failed"):
            self.verify(assertion, {})

    def test_production_private_public_pair_is_validated(self):
        public = base64.urlsafe_b64encode(public_key_bytes(self.key)).decode().rstrip("=")
        environment = {
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY": self.key.hex(),
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID": "operator-v1",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON": json.dumps({"operator-v1": public}),
        }
        with patch.dict(os.environ, environment, clear=True):
            validate_signing_key_pair()
            issued = issue_assertion(
                operator_id="op-1",
                control_activation_id="activation-1",
                task_id=self.task_id,
                action="note",
                args=[self.task_id, "audited"],
                old_assignment=self.old,
                new_assignment=self.old,
                reason="operator note",
            )
        self.assertEqual(issued["signature"]["key_id"], "operator-v1")

    def test_production_private_public_mismatch_is_rejected(self):
        environment = {
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PRIVATE_KEY": self.key.hex(),
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_KEY_ID": "operator-v1",
            "PANTHEON_CANONICAL_MUTATION_ASSERTION_PUBLIC_KEYS_JSON": json.dumps({
                "operator-v1": base64.urlsafe_b64encode(b"z" * 32).decode().rstrip("=")
            }),
        }
        with patch.dict(os.environ, environment, clear=True), self.assertRaisesRegex(ValueError, "does not match"):
            validate_signing_key_pair()

    def test_legacy_mixed_replay_ledger_is_split_by_capability(self):
        state = {
            "consumed_operator_assertions": {
                "op_old": {"nonce": "operator"},
                "bridge:pkt:nonce": {"nonce": "bridge"},
            }
        }
        operator, bridge = migrate_legacy_consumed_ledgers(state)
        self.assertEqual(set(operator), {"op_old"})
        self.assertEqual(set(bridge), {"bridge:pkt:nonce"})
        self.assertIs(state[CONSUMED_KEY], operator)
        self.assertIs(state[DEV_BRIDGE_CONSUMED_KEY], bridge)
        self.assertNotIn("consumed_operator_assertions", state)

    def test_operator_consume_does_not_evict_bridge_receipts(self):
        state = {
            DEV_BRIDGE_CONSUMED_KEY: {
                "bridge:pkt:nonce": {
                    "nonce": "bridge",
                    "consumed_at": "2026-01-01T00:00:00Z",
                }
            }
        }
        self.verify(self.assertion(), state)
        self.assertIn("bridge:pkt:nonce", state[DEV_BRIDGE_CONSUMED_KEY])
        self.assertEqual(len(state[CONSUMED_KEY]), 1)


if __name__ == "__main__":
    unittest.main()
