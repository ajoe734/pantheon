from __future__ import annotations

import base64
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import execution_authorization as ea


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class ExecutionAuthorizationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
        self.issuer_key = Ed25519PrivateKey.generate()
        self.other_key = Ed25519PrivateKey.generate()
        self.trusted_issuers = {
            "mfa-issuer-1": _b64(
                self.issuer_key.public_key().public_bytes(
                    encoding=Encoding.Raw, format=PublicFormat.Raw
                )
            )
        }
        self.policy = ea.derive_execution_policy(
            task_id="OPS-PRIV-001",
            work_class="security",
            repository="pantheon",
            environment="pantheon-dev",
            resources=["dev-supervisor"],
            action_scope="execute",
        )

    def _sign(self, body: dict, key, key_id: str = "mfa-issuer-1") -> dict:
        payload = deepcopy(body)
        canonical = ea._canonical_json(payload)
        payload["signature"] = {
            "key_id": key_id,
            "algorithm": "Ed25519",
            "value": _b64(key.sign(canonical)),
        }
        return payload

    def _grant(self, **overrides) -> dict:
        key = overrides.pop("_key", self.issuer_key)
        key_id = overrides.pop("_key_id", "mfa-issuer-1")
        body = {
            "task_id": "OPS-PRIV-001",
            "generation": 0,
            "policy_digest": self.policy["policy_digest"],
            "repository": "pantheon",
            "environment": "pantheon-dev",
            "resources": ["dev-supervisor"],
            "action_scope": "execute",
            "purpose": ea.EXECUTION_GRANT_PURPOSE,
            "capability": ea.EXECUTION_GRANT_CAPABILITY,
            "audience": "OPS-PRIV-001",
            "mfa_verified": True,
            "mfa_actor": "human-ops-1",
            "nonce": "nonce-1",
            "issued_at": self.now.isoformat().replace("+00:00", "Z"),
            "expires_at": (self.now + timedelta(seconds=120)).isoformat().replace("+00:00", "Z"),
            "run_ttl_seconds": 1800,
        }
        body.update(overrides)
        return self._sign(body, key, key_id=key_id)

    # -- policy / pending hold --------------------------------------------

    def test_functional_work_class_does_not_require_authorization(self) -> None:
        policy = ea.derive_execution_policy(
            task_id="T1", work_class="functional", repository="pantheon"
        )
        self.assertFalse(policy["requires_execution_authorization"])
        with self.assertRaises(ea.ExecutionAuthorizationError):
            ea.pending_authorization_hold(policy)

    def test_privileged_classes_are_exactly_security_hosted_live(self) -> None:
        self.assertEqual(ea.PRIVILEGED_WORK_CLASSES, frozenset({"security", "hosted", "live"}))
        for work_class in ("security", "hosted", "live"):
            policy = ea.derive_execution_policy(
                task_id="T", work_class=work_class, repository="pantheon"
            )
            self.assertTrue(policy["requires_execution_authorization"])

    def test_pending_hold_is_not_runnable(self) -> None:
        hold = ea.pending_authorization_hold(self.policy)
        task = {"id": "OPS-PRIV-001", "generation": 0, "execution_authorization": hold}
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_dependency_completion_alone_cannot_launch_pending_task(self) -> None:
        # A pending record has no grant at all; regardless of what dependency
        # state the caller believes is satisfied, the query is False.
        hold = ea.pending_authorization_hold(self.policy)
        task = {"id": "OPS-PRIV-001", "generation": 0, "execution_authorization": hold}
        self.assertIsNone(task["execution_authorization"]["grant"])
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_ordinary_task_without_subrecord_is_authorized(self) -> None:
        task = {"id": "T2", "generation": 0}
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))

    # -- verify_execution_grant positives -----------------------------------

    def test_genuine_synthetic_grant_authorizes_exactly_one_attempt(self) -> None:
        grant = self._grant()
        ea.verify_execution_grant(
            grant,
            policy=self.policy,
            task_id="OPS-PRIV-001",
            generation=0,
            trusted_issuers=self.trusted_issuers,
            now=self.now,
        )
        record = ea.build_granted_authorization(policy=self.policy, grant=grant)
        task = {"id": "OPS-PRIV-001", "generation": 0, "execution_authorization": record}
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))

        reserved = ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)
        task["execution_authorization"] = reserved
        self.assertEqual(reserved["state"], ea.STATE_RESERVED)
        # Spent: a second reservation attempt against the now-reserved record
        # must fail -- this is the one-shot / two-process-race guard.
        with self.assertRaises(ea.ExecutionAuthorizationError):
            ea.reserve_execution_authorization(task, run_id="run-2", now=self.now)
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_normal_functional_task_still_dispatches_while_privileged_waits(self) -> None:
        functional_task = {"id": "F1", "generation": 0}
        privileged_task = {
            "id": "OPS-PRIV-001",
            "generation": 0,
            "execution_authorization": ea.pending_authorization_hold(self.policy),
        }
        self.assertTrue(ea.is_execution_authorized(functional_task, now=self.now))
        self.assertFalse(ea.is_execution_authorized(privileged_task, now=self.now))

    # -- verify_execution_grant negatives ------------------------------------

    def test_untrusted_issuer_key_is_rejected(self) -> None:
        grant = self._grant(_key=self.other_key)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "signature verification failed"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_unknown_key_id_is_rejected(self) -> None:
        grant = self._grant(_key_id="unknown-issuer")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "issuer is not trusted"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_no_trusted_issuers_configured_fails_closed(self) -> None:
        grant = self._grant()
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "no trusted MFA issuer"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers={}, now=self.now,
            )

    def test_wrong_purpose_is_rejected(self) -> None:
        grant = self._grant(purpose="something.else")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "purpose"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_wrong_audience_is_rejected(self) -> None:
        grant = self._grant(audience="OTHER-TASK")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "audience"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_mfa_not_verified_is_rejected(self) -> None:
        grant = self._grant(mfa_verified=False)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "MFA"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_expired_grant_is_rejected(self) -> None:
        grant = self._grant(
            issued_at=(self.now - timedelta(seconds=400)).isoformat().replace("+00:00", "Z"),
            expires_at=(self.now - timedelta(seconds=100)).isoformat().replace("+00:00", "Z"),
        )
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "expired"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_not_yet_valid_grant_is_rejected(self) -> None:
        grant = self._grant(
            issued_at=(self.now + timedelta(seconds=60)).isoformat().replace("+00:00", "Z"),
            expires_at=(self.now + timedelta(seconds=180)).isoformat().replace("+00:00", "Z"),
        )
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "not yet valid"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_task_id_mismatch_is_rejected(self) -> None:
        grant = self._grant(task_id="OTHER-TASK")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "task_id mismatch"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_generation_mismatch_is_rejected(self) -> None:
        grant = self._grant(generation=1)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "generation mismatch"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_policy_digest_mismatch_is_rejected(self) -> None:
        grant = self._grant(policy_digest="0" * 64)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "policy_digest mismatch"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_repository_mismatch_is_rejected(self) -> None:
        grant = self._grant(repository="execute-plans")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "repository mismatch"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_environment_mismatch_is_rejected(self) -> None:
        grant = self._grant(environment="pantheon-prod")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "environment mismatch"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_action_scope_mismatch_is_rejected(self) -> None:
        grant = self._grant(action_scope="revoke")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "action_scope mismatch"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_resources_mismatch_is_rejected(self) -> None:
        grant = self._grant(resources=["other-resource"])
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "resources mismatch"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_changed_payload_after_signing_breaks_signature(self) -> None:
        grant = self._grant()
        grant["resources"] = ["tampered"]
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "signature verification failed"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_missing_nonce_is_rejected(self) -> None:
        grant = self._grant(nonce="")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "nonce"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    def test_run_ttl_out_of_bounds_is_rejected(self) -> None:
        grant = self._grant(run_ttl_seconds=999999)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "run_ttl_seconds"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    # -- replay / nonce ledger ------------------------------------------------

    def test_replayed_nonce_is_rejected(self) -> None:
        grant = self._grant()
        ledger: dict = {}
        ea.consume_grant_nonce(ledger, grant, task_id="OPS-PRIV-001", now=self.now)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "already consumed"):
            ea.consume_grant_nonce(ledger, grant, task_id="OPS-PRIV-001", now=self.now)

    def test_replayed_nonce_against_a_different_task_is_still_rejected(self) -> None:
        grant = self._grant()
        ledger: dict = {}
        ea.consume_grant_nonce(ledger, grant, task_id="OPS-PRIV-001", now=self.now)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "already consumed"):
            ea.consume_grant_nonce(ledger, grant, task_id="OTHER-TASK", now=self.now)

    # -- reassignment / reopen / revoke ---------------------------------------

    def test_reassignment_generation_bump_invalidates_grant(self) -> None:
        grant = self._grant()
        record = ea.build_granted_authorization(policy=self.policy, grant=grant)
        task = {"id": "OPS-PRIV-001", "generation": 0, "execution_authorization": record}
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))
        task["generation"] = 1
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_revocation_prevents_new_effects(self) -> None:
        grant = self._grant()
        record = ea.build_granted_authorization(policy=self.policy, grant=grant)
        task = {"id": "OPS-PRIV-001", "generation": 0, "execution_authorization": record}
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))
        revoked = ea.revoked_execution_authorization(task, actor="Human/Ops", now=self.now, reason="incident")
        task["execution_authorization"] = revoked
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))
        self.assertEqual(revoked["state"], ea.STATE_REVOKED)

    def test_corrupt_or_malformed_state_fails_closed(self) -> None:
        for bad_record in ("not-a-mapping", 5, ["list"]):
            task = {"id": "T", "generation": 0, "execution_authorization": bad_record}
            self.assertFalse(ea.is_execution_authorized(task, now=self.now))
        task_missing_grant = {
            "id": "T",
            "generation": 0,
            "execution_authorization": {"state": ea.STATE_GRANTED, "policy": self.policy, "grant": None},
        }
        self.assertFalse(ea.is_execution_authorized(task_missing_grant, now=self.now))

    def test_reserve_without_prior_grant_fails(self) -> None:
        task = {
            "id": "OPS-PRIV-001",
            "generation": 0,
            "execution_authorization": ea.pending_authorization_hold(self.policy),
        }
        with self.assertRaises(ea.ExecutionAuthorizationError):
            ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)

    # -- old-runtime rollback guard --------------------------------------------

    def test_old_runtime_without_capability_is_not_recognized(self) -> None:
        self.assertFalse(ea.runtime_supports_execution_authorization([]))
        self.assertFalse(ea.runtime_supports_execution_authorization(None))
        self.assertTrue(
            ea.runtime_supports_execution_authorization(
                [ea.RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION]
            )
        )


if __name__ == "__main__":
    unittest.main()
