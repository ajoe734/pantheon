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
        self.spec = {
            "id": "OPS-PRIV-001", "title": "Bound privileged work",
            "owner": "Codex2", "reviewer": "Codex", "target_repo": "pantheon",
            "phase": "Development", "summary": "One approved scope",
            "depends_on": ["PLAN-001"], "dependency_tracks": {"PLAN-001": "functional"},
            "execution_resources": ["dev-supervisor"], "artifacts": [],
            "acceptance": ["No unauthorized effects"],
        }
        self.policy = ea.derive_execution_policy(
            task_id="OPS-PRIV-001",
            work_class="security",
            repository="pantheon",
            environment="pantheon-dev",
            resources=["dev-supervisor"],
            action_scope="execute",
            task_spec=self.spec,
        )
        # is_execution_authorized recomputes the policy digest against the
        # task's *current* target/resources/artifacts, so any granted-task
        # fixture must mirror exactly what derive_execution_policy above was
        # given, the same way scripts/ai_status.py's command_assign mirrors
        # them onto the real task row at intake.
        self.current_scope_fields = {
            **deepcopy(self.spec),
            "summary_zh": self.spec["summary"],
            "target_repo": "pantheon",
            "execution_resources": ["dev-supervisor"],
            "artifacts": [],
            "dev_bridge": {
                "work_class": "security", "task_spec": deepcopy(self.spec),
                "task_spec_hash": self.policy["task_spec_hash"],
            },
        }

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
        task = {
            "id": "OPS-PRIV-001",
            "generation": 0,
            "execution_authorization": record,
            **self.current_scope_fields,
        }
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
        fingerprint = ea.verify_execution_grant(
            grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
            trusted_issuers=self.trusted_issuers, now=self.now,
        )
        ledger: dict = {}
        ea.consume_grant_nonce(ledger, grant, task_id="OPS-PRIV-001", now=self.now, issuer_fingerprint=fingerprint)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "already consumed"):
            ea.consume_grant_nonce(ledger, grant, task_id="OPS-PRIV-001", now=self.now, issuer_fingerprint=fingerprint)

    def test_replayed_nonce_against_a_different_task_is_still_rejected(self) -> None:
        grant = self._grant()
        fingerprint = ea.verify_execution_grant(
            grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
            trusted_issuers=self.trusted_issuers, now=self.now,
        )
        ledger: dict = {}
        ea.consume_grant_nonce(ledger, grant, task_id="OPS-PRIV-001", now=self.now, issuer_fingerprint=fingerprint)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "already consumed"):
            ea.consume_grant_nonce(ledger, grant, task_id="OTHER-TASK", now=self.now, issuer_fingerprint=fingerprint)

    def test_unsigned_issuer_alias_cannot_replay_one_verified_assertion(self) -> None:
        grant = self._grant()
        aliases = {**self.trusted_issuers, "same-key-alias": self.trusted_issuers["mfa-issuer-1"]}
        fingerprint = ea.verify_execution_grant(
            grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
            trusted_issuers=aliases, now=self.now,
        )
        ledger = {}
        ea.consume_grant_nonce(ledger, grant, task_id="OPS-PRIV-001", now=self.now, issuer_fingerprint=fingerprint)
        grant["signature"]["key_id"] = "same-key-alias"
        alias_fingerprint = ea.verify_execution_grant(
            grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
            trusted_issuers=aliases, now=self.now,
        )
        self.assertEqual(alias_fingerprint, fingerprint)
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "already consumed"):
            ea.consume_grant_nonce(ledger, grant, task_id="OPS-PRIV-001", now=self.now, issuer_fingerprint=alias_fingerprint)

    # -- reassignment / reopen / revoke ---------------------------------------

    def test_reassignment_generation_bump_invalidates_grant(self) -> None:
        grant = self._grant()
        record = ea.build_granted_authorization(policy=self.policy, grant=grant)
        task = {
            "id": "OPS-PRIV-001",
            "generation": 0,
            "execution_authorization": record,
            **self.current_scope_fields,
        }
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))
        task["generation"] = 1
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_revocation_prevents_new_effects(self) -> None:
        grant = self._grant()
        record = ea.build_granted_authorization(policy=self.policy, grant=grant)
        task = {
            "id": "OPS-PRIV-001",
            "generation": 0,
            "execution_authorization": record,
            **self.current_scope_fields,
        }
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

    def test_false_capability_value_is_not_treated_as_enabled(self) -> None:
        # 2026-09-06 diagnostic case 4: a mapping declares capability
        # *values*, not just presence of the key.
        self.assertFalse(
            ea.runtime_supports_execution_authorization(
                {ea.RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION: False}
            )
        )
        self.assertTrue(
            ea.runtime_supports_execution_authorization(
                {ea.RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION: True}
            )
        )

    def test_current_runtime_declares_its_own_capability(self) -> None:
        self.assertTrue(
            ea.runtime_supports_execution_authorization(ea.RUNTIME_CAPABILITIES)
        )

    # -- 2026-09-06 Codex2 exact-head review REJECT regressions --------------

    def _privileged_hosted_task(self, **overrides) -> dict:
        task = {
            "id": "DIAGNOSTIC-HOSTED-NO-EXECUTE",
            "generation": 1,
            "target_repo": "pantheon",
            "dev_bridge": {
                "work_class": "hosted",
                "operator_authorization_required": True,
            },
        }
        task.update(overrides)
        return task

    def test_privileged_source_with_missing_subrecord_fails_closed(self) -> None:
        # Diagnostic case 1: privileged source provenance with no
        # execution_authorization subrecord at all must not silently become
        # ordinary functional work.
        task = self._privileged_hosted_task()
        self.assertNotIn("execution_authorization", task)
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_privileged_source_with_corrupt_policy_fails_closed(self) -> None:
        # Diagnostic case 2: a malformed policy shape must not authorize.
        task = self._privileged_hosted_task(
            execution_authorization={"policy": "corrupt"}
        )
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_privileged_source_with_downgraded_flag_fails_closed(self) -> None:
        # Diagnostic case 3: an erased/downgraded
        # requires_execution_authorization flag must never override the
        # durable, verified privileged classification.
        task = self._privileged_hosted_task(
            execution_authorization={
                "policy": {"requires_execution_authorization": False}
            }
        )
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_non_privileged_task_with_no_subrecord_is_unaffected(self) -> None:
        task = {
            "id": "FUNCTIONAL-TASK",
            "generation": 0,
            "dev_bridge": {"work_class": "functional"},
        }
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))
        task_no_bridge = {"id": "PLAIN-TASK", "generation": 0}
        self.assertTrue(ea.is_execution_authorized(task_no_bridge, now=self.now))

    # -- Codex2 finding 4: current resources/artifacts must be rebound -------

    def _granted_task(self) -> dict:
        grant = self._grant()
        record = ea.build_granted_authorization(policy=self.policy, grant=grant)
        return {
            "id": "OPS-PRIV-001",
            "generation": 0,
            "execution_authorization": record,
            **self.current_scope_fields,
        }

    def test_execution_resource_revision_invalidates_outstanding_grant(self) -> None:
        task = self._granted_task()
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))
        # command_execution_resource revises this field directly, without
        # bumping generation or touching the frozen policy/grant digests.
        task["execution_resources"] = ["dev-supervisor", "extra-resource"]
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_artifact_contract_revision_invalidates_outstanding_grant(self) -> None:
        task = self._granted_task()
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))
        # command_artifact_contract revises this field directly, without
        # bumping generation or touching the frozen policy/grant digests.
        task["artifacts"] = ["services/new-surface/"]
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_target_repo_change_invalidates_outstanding_grant(self) -> None:
        task = self._granted_task()
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))
        task["target_repo"] = "execute-plans"
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    # -- Codex2 finding 3: MFA issuer trust must bind to independent authority

    def test_source_signing_key_is_not_an_automatic_mfa_issuer(self) -> None:
        # A packet-source key must never double as an MFA issuer even if a
        # caller labels it that way; verify_execution_grant only accepts a
        # signature verified against the trust root the caller explicitly
        # passes in, and never falls back to any bridge/source trust root of
        # its own. This documents that verify_execution_grant has no notion
        # of "packet-source keys" at all -- the isolated separation is the
        # caller's responsibility (scripts/ai_status.py sourcing
        # ``execution_authorization.mfa_issuer_public_keys`` from
        # ``.orchestrator/config.json``, never from the packet-source
        # ``BRIDGE_SIGNING_PUBLIC_KEYS_JSON`` trust root or the grant
        # submitter's own environment).
        grant = self._grant(_key=self.other_key, _key_id="bridge-packet-source-key")
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "issuer is not trusted"):
            ea.verify_execution_grant(
                grant, policy=self.policy, task_id="OPS-PRIV-001", generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now,
            )

    # -- Codex2 finding 1/6: reservation_is_current direct worker-entry gate -

    def test_reservation_is_current_requires_matching_run_id(self) -> None:
        task = self._granted_task()
        reserved = ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)
        task["execution_authorization"] = reserved
        self.assertTrue(
            ea.reservation_is_current(task, run_id="run-1", now=self.now)
        )
        self.assertFalse(
            ea.reservation_is_current(task, run_id="run-2", now=self.now)
        )
        self.assertFalse(
            ea.reservation_is_current(task, run_id="", now=self.now)
        )

    def test_reservation_is_current_rejects_granted_but_unreserved_task(self) -> None:
        # A task that is merely GRANTED (the canonical claim/lease boundary
        # never reserved it) must not pass the direct worker-entry check --
        # this is exactly the "direct runner without canonical run binding"
        # required negative.
        task = self._granted_task()
        self.assertFalse(
            ea.reservation_is_current(task, run_id="any-run", now=self.now)
        )

    def test_reservation_is_current_rejects_expired_run_ttl(self) -> None:
        task = self._granted_task()
        reserved = ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)
        task["execution_authorization"] = reserved
        much_later = self.now + timedelta(seconds=reserved["grant"]["run_ttl_seconds"] + 1)
        self.assertFalse(
            ea.reservation_is_current(task, run_id="run-1", now=much_later)
        )

    def test_reservation_is_current_rejects_revoked_task(self) -> None:
        task = self._granted_task()
        reserved = ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)
        task["execution_authorization"] = reserved
        revoked = ea.revoked_execution_authorization(
            task, actor="Human/Ops", now=self.now, reason="incident"
        )
        task["execution_authorization"] = revoked
        self.assertFalse(
            ea.reservation_is_current(task, run_id="run-1", now=self.now)
        )

    def test_reservation_is_current_unaffected_for_non_privileged_task(self) -> None:
        task = {"id": "F1", "generation": 0}
        self.assertTrue(
            ea.reservation_is_current(task, run_id="anything-or-nothing", now=self.now)
        )

    def test_reservation_is_current_fails_closed_for_privileged_missing_record(self) -> None:
        task = self._privileged_hosted_task()
        self.assertFalse(
            ea.reservation_is_current(task, run_id="any-run", now=self.now)
        )

    # -- 2026-09-06 Codex2 exact-head REJECT P1-4: reservation must re-apply
    # the exact-binding scope check, not just state/run-id/TTL -------------

    def test_reservation_is_current_rejects_generation_bump_after_reserve(self) -> None:
        # A reassignment committed after the claim/lease boundary reserved
        # this exact run must invalidate the reservation, the same way it
        # already invalidates an outstanding (not-yet-reserved) grant.
        task = self._granted_task()
        reserved = ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)
        task["execution_authorization"] = reserved
        self.assertTrue(
            ea.reservation_is_current(task, run_id="run-1", now=self.now)
        )
        task["generation"] = 1
        self.assertFalse(
            ea.reservation_is_current(task, run_id="run-1", now=self.now)
        )

    def test_reservation_is_current_rejects_scope_revision_after_reserve(self) -> None:
        # command_execution_resource/command_artifact_contract revise scope
        # without bumping generation; a reservation made before that revision
        # must not still be treated as current at actual worker entry.
        task = self._granted_task()
        reserved = ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)
        task["execution_authorization"] = reserved
        self.assertTrue(
            ea.reservation_is_current(task, run_id="run-1", now=self.now)
        )
        task["execution_resources"] = ["dev-supervisor", "extra-resource"]
        self.assertFalse(
            ea.reservation_is_current(task, run_id="run-1", now=self.now)
        )

    def test_every_signed_contract_field_is_bound_at_grant_and_entry(self) -> None:
        changes = {
            "title": "A different job", "summary_zh": "Broadened task",
            "phase": "Production", "depends_on": [],
            "dependency_tracks": {"PLAN-001": "hosted"},
            "acceptance": ["Changed acceptance"], "owner": "Claude",
            "reviewer": "Gemini", "target_repo": "execute-plans",
        }
        for field, value in changes.items():
            for reserved in (False, True):
                with self.subTest(field=field, reserved=reserved):
                    task = deepcopy(self._granted_task())
                    if reserved:
                        task["execution_authorization"] = ea.reserve_execution_authorization(
                            task, run_id="run-1", now=self.now
                        )
                    task[field] = value
                    self.assertFalse(ea.is_execution_authorized(task, now=self.now))
                    self.assertFalse(ea.reservation_is_current(task, run_id="run-1", now=self.now))

    def test_source_spec_mutation_or_missing_hash_cannot_authorize(self) -> None:
        for corruption in ("missing-spec", "missing-hash", "changed-spec", "downgrade", "policy-class", "policy-hash"):
            with self.subTest(corruption=corruption):
                task = deepcopy(self._granted_task())
                if corruption == "missing-spec":
                    task["dev_bridge"].pop("task_spec")
                elif corruption == "missing-hash":
                    task["dev_bridge"].pop("task_spec_hash")
                elif corruption == "changed-spec":
                    task["dev_bridge"]["task_spec"]["acceptance"] = ["Changed acceptance"]
                    task["acceptance"] = ["Changed acceptance"]
                elif corruption == "downgrade":
                    task["dev_bridge"]["work_class"] = "functional"
                elif corruption == "policy-class":
                    task["execution_authorization"]["policy"]["work_class"] = "hosted"
                else:
                    task["execution_authorization"]["policy"].pop("task_spec_hash")
                self.assertFalse(ea.is_execution_authorized(task, now=self.now))
                with self.assertRaises(ea.ExecutionAuthorizationError):
                    ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)

    def test_malformed_policy_and_scalar_shapes_fail_closed(self) -> None:
        for field, values in {
            "resources": ({}, "dev-supervisor", 7, [False]),
            "work_class": ([], {}, True),
            "requires_execution_authorization": ("true", 1, False),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    task = deepcopy(self._granted_task())
                    task["execution_authorization"]["policy"][field] = value
                    self.assertFalse(ea.is_execution_authorized(task, now=self.now))
                    self.assertFalse(ea.reservation_is_current(task, run_id="run-1", now=self.now))
        for value in (True, 0.9, "0", {}, []):
            with self.subTest(generation=value):
                with self.assertRaises(ea.ExecutionAuthorizationError):
                    ea.verify_execution_grant(
                        self._grant(generation=value), policy=self.policy,
                        task_id=self.spec["id"], generation=0,
                        trusted_issuers=self.trusted_issuers, now=self.now,
                    )

    def test_fresh_grant_after_reassignment_snapshots_current_assignment(self) -> None:
        task = deepcopy(self._granted_task())
        original_policy = deepcopy(task["execution_authorization"]["policy"])
        task.update(owner="Claude", reviewer="Gemini", generation=1)
        grant = self._grant(generation=1, nonce="new-assignment")
        ea.verify_execution_grant(
            grant, policy=original_policy, task_id=task["id"], generation=1,
            trusted_issuers=self.trusted_issuers, now=self.now, task=task,
        )
        task["execution_authorization"] = ea.build_granted_authorization(
            policy=original_policy, grant=grant, task=task
        )
        self.assertTrue(ea.is_execution_authorized(task, now=self.now))
        self.assertEqual(task["execution_authorization"]["policy"], original_policy)
        task["owner"] = "Codex2"
        self.assertFalse(ea.is_execution_authorized(task, now=self.now))

    def test_scope_mutation_is_rejected_before_grant_submission(self) -> None:
        task = deepcopy(self._granted_task())
        task["acceptance"] = ["Expanded acceptance"]
        with self.assertRaisesRegex(ea.ExecutionAuthorizationError, "current signed task contract"):
            ea.verify_execution_grant(
                self._grant(), policy=self.policy, task_id=task["id"], generation=0,
                trusted_issuers=self.trusted_issuers, now=self.now, task=task,
            )

    def test_reserved_run_lifetime_is_bounded_separately_from_start_expiry(self) -> None:
        task = deepcopy(self._granted_task())
        task["execution_authorization"] = ea.reserve_execution_authorization(task, run_id="run-1", now=self.now)
        self.assertTrue(ea.reservation_is_current(task, run_id="run-1", now=self.now + timedelta(seconds=121)))
        task["execution_authorization"]["grant"]["run_ttl_seconds"] = ea.MAX_RUN_TTL_SECONDS + 1
        self.assertFalse(ea.reservation_is_current(task, run_id="run-1", now=self.now))


if __name__ == "__main__":
    unittest.main()
