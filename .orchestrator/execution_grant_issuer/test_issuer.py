"""Comprehensive tests for the Execution Grant Issuer service.

OPS-EXECUTION-MFA-ISSUER-001.
Covers:
- Cryptographic verification of genuine Identity Platform user MFA ID tokens
- Rejection of invalid signatures, wrong projects, wrong issuers, missing MFA,
  password-only, anonymous, and service-account/ADC tokens
- Allowlist and email verification enforcement
- Stale and future auth_time rejection
- Ephemeral single-use challenge lifecycle, expiry, and replay protection
- Atomic concurrent challenge consumption (race condition prevention)
- Actor substitution, task substitution, and client policy modification rejection
- Mandatory S5 / step-5 execution-authorization pause
- Full cryptographic integration with execution_authorization.verify_execution_grant
- Redaction verification (no raw tokens, private keys, or bearer secrets logged)
"""
from __future__ import annotations

import concurrent.futures
import json
import sys
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

# Ensure .orchestrator is in sys.path
_orchestrator_dir = Path(__file__).resolve().parents[1]
if str(_orchestrator_dir) not in sys.path:
    sys.path.insert(0, str(_orchestrator_dir))

import execution_authorization as ea
from execution_grant_issuer.challenge_store import ChallengeStore
from execution_grant_issuer.models import AuthenticationError, ChallengeError, PolicyValidationError
from execution_grant_issuer.service import ExecutionGrantIssuerService
from execution_grant_issuer.signer import Ed25519GrantSigner
from execution_grant_issuer.token_verifier import IdentityPlatformTokenVerifier


class TestExecutionGrantIssuer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Generate RSA keys once at class level for fast test execution
        cls.rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.rsa_public_key = cls.rsa_private_key.public_key()
        cls.other_rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        cls.ed25519_private_key = ed25519.Ed25519PrivateKey.generate()

    def setUp(self) -> None:
        self.now = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
        self.project_id = "pantheon-dev-20260902"
        self.operator_uid = "operator-chloe-primary"
        self.operator_email = "operator-chloe@pantheon.trade"
        self.key_id = "test-rsa-kid-1"

        self.signer_key_id = "pantheon-mfa-issuer-test-key-1"
        self.signer = Ed25519GrantSigner(self.ed25519_private_key, key_id=self.signer_key_id)

        # Token verifier with explicit trusted public key and allowlist
        self.verifier = IdentityPlatformTokenVerifier(
            project_id=self.project_id,
            allowed_operator_uids=[self.operator_uid],
            trusted_public_keys={self.key_id: self.rsa_public_key},
            max_auth_age_seconds=3600,
        )

        # Canonical TRACE task specification and derived policy
        self.task_id = "DEV502-TRACE-001"
        self.generation = 3
        self.spec = {
            "id": self.task_id,
            "title": "Obtain authenticated candidate failure evidence",
            "owner": "Antigravity",
            "reviewer": "Codex",
            "target_repo": "pantheon",
            "phase": "step-3a",
            "summary": "Urgent TRACE evidence",
            "depends_on": ["DEV502-FAILPATH-001"],
            "execution_resources": ["pantheon-dev"],
            "artifacts": ["docs/deployment/evidence/DEV502-TRACE-001/"],
            "acceptance": ["Trace evidence captured"],
        }
        self.policy = ea.derive_execution_policy(
            task_id=self.task_id,
            work_class="hosted",
            repository="pantheon",
            environment="pantheon-dev",
            resources=["pantheon-dev"],
            action_scope="execute",
            artifacts=["docs/deployment/evidence/DEV502-TRACE-001/"],
            task_spec=self.spec,
        )

        self.service = ExecutionGrantIssuerService(
            verifier=self.verifier,
            signer=self.signer,
            challenge_store=ChallengeStore(),
            allowed_tasks=[self.task_id, "DEV502-FAILPATH-001"],
            allowed_environments=["pantheon-dev"],
        )

    def _mint_id_token(
        self,
        *,
        uid: str | None = None,
        email: str | None = None,
        email_verified: bool = True,
        project_id: str | None = None,
        auth_time_offset: int = 0,
        second_factor: str | None = "totp",
        sign_in_provider: str = "password",
        custom_claims: dict[str, Any] | None = None,
        key=None,
        kid: str | None = None,
        expired: bool = False,
        sub: str | None = None,
    ) -> str:
        current_ts = int(self.now.timestamp())
        effective_project = project_id if project_id is not None else self.project_id
        effective_uid = uid if uid is not None else self.operator_uid
        effective_sub = sub if sub is not None else effective_uid
        effective_email = email if email is not None else self.operator_email

        claims: dict[str, Any] = {
            "iss": f"https://securetoken.google.com/{effective_project}",
            "aud": effective_project,
            "sub": effective_sub,
            "user_id": effective_uid,
            "email": effective_email,
            "email_verified": email_verified,
            "auth_time": current_ts + auth_time_offset,
            "iat": current_ts,
            "exp": current_ts - 100 if expired else current_ts + 3600,
            "firebase": {
                "sign_in_provider": sign_in_provider,
            },
        }
        if second_factor is not None:
            claims["firebase"]["sign_in_second_factor"] = second_factor

        if custom_claims:
            claims.update(custom_claims)

        effective_key = key if key is not None else self.rsa_private_key
        effective_kid = kid if kid is not None else self.key_id

        return jwt.encode(
            claims,
            effective_key,
            algorithm="RS256",
            headers={"kid": effective_kid},
        )

    # -------------------------------------------------------------------------
    # 1. Token Verification & Cryptographic Rejection Tests
    # -------------------------------------------------------------------------

    def test_valid_token_with_mfa_succeeds(self) -> None:
        token = self._mint_id_token()
        operator = self.verifier.verify_token(token, now=self.now)
        self.assertEqual(operator.uid, self.operator_uid)
        self.assertEqual(operator.email, self.operator_email)
        self.assertEqual(operator.second_factor, "totp")

    def test_rejects_wrong_signature(self) -> None:
        token = self._mint_id_token(key=self.other_rsa_key)
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("signature verification failed", str(cm.exception).lower())

    def test_rejects_wrong_issuer(self) -> None:
        token = self._mint_id_token(
            custom_claims={"iss": "https://securetoken.google.com/wrong-project"}
        )
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("issuer", str(cm.exception).lower())

    def test_rejects_wrong_audience_project(self) -> None:
        token = self._mint_id_token(
            custom_claims={"aud": "pantheon-benjamin-20260528"}  # retired project
        )
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("audience", str(cm.exception).lower())

    def test_rejects_missing_second_factor_password_only(self) -> None:
        # Password-only token has no sign_in_second_factor
        token = self._mint_id_token(second_factor=None)
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("second-factor mfa", str(cm.exception).lower())

    def test_rejects_anonymous_token(self) -> None:
        token = self._mint_id_token(sign_in_provider="anonymous", second_factor="totp")
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("anonymous", str(cm.exception).lower())

    def test_rejects_service_account_or_adc_token(self) -> None:
        token = self._mint_id_token(sub="pantheon-runner@pantheon-dev.iam.gserviceaccount.com")
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("service account", str(cm.exception).lower())

    def test_rejects_unverified_email(self) -> None:
        token = self._mint_id_token(email_verified=False)
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("not verified", str(cm.exception).lower())

    def test_rejects_non_allowed_operator_uid(self) -> None:
        token = self._mint_id_token(uid="unauthorized-intruder-uid")
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("not in the allowed operators list", str(cm.exception).lower())

    def test_rejects_stale_auth_time(self) -> None:
        # Auth occurred 4000 seconds ago (> max 3600 seconds)
        token = self._mint_id_token(auth_time_offset=-4000)
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("stale", str(cm.exception).lower())

    def test_rejects_future_auth_time(self) -> None:
        token = self._mint_id_token(auth_time_offset=600)
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("future", str(cm.exception).lower())

    def test_rejects_expired_token(self) -> None:
        token = self._mint_id_token(expired=True)
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("expired", str(cm.exception).lower())

    def test_rejects_malformed_token(self) -> None:
        with self.assertRaises(AuthenticationError):
            self.verifier.verify_token("not.a.valid.jwt", now=self.now)

    # -------------------------------------------------------------------------
    # 2. Challenge Lifecycle, Expiry, and Replay Tests
    # -------------------------------------------------------------------------

    def test_challenge_creation_and_consumption_success(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        self.assertEqual(challenge_resp["status"], "ok")
        cid = challenge_resp["challenge_id"]

        issue_resp = self.service.handle_issue_grant(
            token,
            {
                "challenge_id": cid,
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        self.assertEqual(issue_resp["status"], "ok")
        self.assertIn("grant", issue_resp)
        self.assertEqual(issue_resp["grant"]["task_id"], self.task_id)

    def test_challenge_replay_is_rejected(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        cid = challenge_resp["challenge_id"]

        # First consumption succeeds
        self.service.handle_issue_grant(
            token,
            {
                "challenge_id": cid,
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )

        # Second consumption fails
        with self.assertRaises(ChallengeError) as cm:
            self.service.handle_issue_grant(
                token,
                {
                    "challenge_id": cid,
                    "task_id": self.task_id,
                    "generation": self.generation,
                    "policy_snapshot": self.policy,
                },
                now=self.now,
            )
        self.assertIn("already consumed", str(cm.exception).lower())

    def test_challenge_expiry(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        cid = challenge_resp["challenge_id"]

        # Consume 500 seconds later (> 180s challenge TTL)
        later = self.now + timedelta(seconds=500)
        later_token = self._mint_id_token(auth_time_offset=500)
        with self.assertRaises(ChallengeError) as cm:
            self.service.handle_issue_grant(
                later_token,
                {
                    "challenge_id": cid,
                    "task_id": self.task_id,
                    "generation": self.generation,
                    "policy_snapshot": self.policy,
                },
                now=later,
            )
        self.assertIn("expired", str(cm.exception).lower())

    def test_concurrent_challenge_consumption_race_condition(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        cid = challenge_resp["challenge_id"]

        results = []

        def _attempt_consume():
            try:
                resp = self.service.handle_issue_grant(
                    token,
                    {
                        "challenge_id": cid,
                        "task_id": self.task_id,
                        "generation": self.generation,
                        "policy_snapshot": self.policy,
                    },
                    now=self.now,
                )
                return ("success", resp)
            except Exception as e:
                return ("error", e)

        # 10 concurrent threads racing to consume the same challenge
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_attempt_consume) for _ in range(10)]
            for f in concurrent.futures.as_completed(futures):
                results.append(f.result())

        successes = [r for r in results if r[0] == "success"]
        errors = [r for r in results if r[0] == "error"]

        # Exactly ONE must succeed, and nine must fail
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(errors), 9)

    # -------------------------------------------------------------------------
    # 3. Security Binding Enforcement (Actor, Task, Gen, Policy)
    # -------------------------------------------------------------------------

    def test_rejects_actor_substitution(self) -> None:
        # Operator 1 requests challenge
        token1 = self._mint_id_token(uid=self.operator_uid)
        challenge_resp = self.service.handle_create_challenge(
            token1,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        cid = challenge_resp["challenge_id"]

        # Allow secondary operator in verifier
        other_uid = "operator-root-secondary"
        self.verifier.allowed_operator_uids = frozenset([self.operator_uid, other_uid])
        token2 = self._mint_id_token(uid=other_uid)

        # Operator 2 attempts to consume challenge issued to Operator 1
        with self.assertRaises(ChallengeError) as cm:
            self.service.handle_issue_grant(
                token2,
                {
                    "challenge_id": cid,
                    "task_id": self.task_id,
                    "generation": self.generation,
                    "policy_snapshot": self.policy,
                },
                now=self.now,
            )
        self.assertIn("actor mismatch", str(cm.exception).lower())

    def test_rejects_task_substitution(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        cid = challenge_resp["challenge_id"]

        # Derive valid policy for a different allowed task
        other_spec = deepcopy(self.spec)
        other_spec["id"] = "DEV502-FAILPATH-001"
        other_policy = ea.derive_execution_policy(
            task_id="DEV502-FAILPATH-001",
            work_class="hosted",
            repository="pantheon",
            environment="pantheon-dev",
            resources=["pantheon-dev"],
            action_scope="execute",
            artifacts=["docs/deployment/evidence/DEV502-TRACE-001/"],
            task_spec=other_spec,
        )

        with self.assertRaises(ChallengeError) as cm:
            self.service.handle_issue_grant(
                token,
                {
                    "challenge_id": cid,
                    "task_id": "DEV502-FAILPATH-001",
                    "generation": self.generation,
                    "policy_snapshot": other_policy,
                },
                now=self.now,
            )
        self.assertIn("task mismatch", str(cm.exception).lower())

    def test_rejects_generation_mismatch(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        cid = challenge_resp["challenge_id"]

        with self.assertRaises(ChallengeError) as cm:
            self.service.handle_issue_grant(
                token,
                {
                    "challenge_id": cid,
                    "task_id": self.task_id,
                    "generation": self.generation + 1,
                    "policy_snapshot": self.policy,
                },
                now=self.now,
            )
        self.assertIn("generation mismatch", str(cm.exception).lower())

    def test_rejects_client_selected_policy_modifications(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        cid = challenge_resp["challenge_id"]

        tampered_policy = deepcopy(self.policy)
        tampered_policy["action_scope"] = "root_shell_exec"
        # Recompute digest so validate_task_policy passes, but challenge store detects discrepancy
        tampered_policy["policy_digest"] = ea.execution_policy_digest(
            task_id=self.task_id,
            repository=tampered_policy["repository"],
            environment=tampered_policy["environment"],
            resources=tampered_policy["resources"],
            action_scope=tampered_policy["action_scope"],
            artifacts=tampered_policy["artifacts"],
            work_class=tampered_policy["work_class"],
            task_spec_hash=tampered_policy["task_spec_hash"],
        )

        with self.assertRaises(ChallengeError) as cm:
            self.service.handle_issue_grant(
                token,
                {
                    "challenge_id": cid,
                    "task_id": self.task_id,
                    "generation": self.generation,
                    "policy_snapshot": tampered_policy,
                },
                now=self.now,
            )
        self.assertIn("does not match", str(cm.exception).lower())

    def test_rejects_s5_tasks_strictly(self) -> None:
        token = self._mint_id_token()
        s5_policy = deepcopy(self.policy)
        s5_policy["action_scope"] = "step-5"

        with self.assertRaises(PolicyValidationError) as cm:
            self.service.handle_create_challenge(
                token,
                {
                    "task_id": "DEV502-S5-DEPLOY",
                    "generation": 0,
                    "policy_snapshot": s5_policy,
                },
                now=self.now,
            )
        self.assertIn("step 5 / s5", str(cm.exception).lower())

    def test_rejects_unauthorized_tasks_outside_allowed_list(self) -> None:
        token = self._mint_id_token()
        other_policy = deepcopy(self.policy)
        with self.assertRaises(PolicyValidationError) as cm:
            self.service.handle_create_challenge(
                token,
                {
                    "task_id": "UNAUTHORIZED-TASK-999",
                    "generation": 0,
                    "policy_snapshot": other_policy,
                },
                now=self.now,
            )
        self.assertIn("allowed task scope", str(cm.exception).lower())

    # -------------------------------------------------------------------------
    # 4. Redaction Verification (No Secrets Leaked)
    # -------------------------------------------------------------------------

    def test_redaction_in_audit_receipts(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        self.service.handle_issue_grant(
            token,
            {
                "challenge_id": challenge_resp["challenge_id"],
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )

        receipts = self.service._audit_receipts
        self.assertEqual(len(receipts), 1)
        receipt_str = json.dumps(receipts[0])

        # Assert no private keys or bearer tokens in receipts
        self.assertNotIn(token, receipt_str)
        self.assertNotIn("BEGIN PRIVATE KEY", receipt_str)
        self.assertNotIn("Ed25519PrivateKey", receipt_str)

    # -------------------------------------------------------------------------
    # 5. Full End-to-End Cryptographic Integration with execution_authorization
    # -------------------------------------------------------------------------

    def test_issued_grant_verifies_against_execution_authorization(self) -> None:
        token = self._mint_id_token()
        challenge_resp = self.service.handle_create_challenge(
            token,
            {
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        issue_resp = self.service.handle_issue_grant(
            token,
            {
                "challenge_id": challenge_resp["challenge_id"],
                "task_id": self.task_id,
                "generation": self.generation,
                "policy_snapshot": self.policy,
            },
            now=self.now,
        )
        grant = issue_resp["grant"]

        # Build trusted issuers map as read by orchestrator config
        trusted_issuers = {
            self.signer_key_id: self.signer.public_key_base64url
        }

        # Build task structure expected by verify_execution_grant matching canonical projection
        canonical_task = {
            **deepcopy(self.spec),
            "summary_zh": self.spec["summary"],
            "target_repo": "pantheon",
            "execution_resources": ["pantheon-dev"],
            "artifacts": ["docs/deployment/evidence/DEV502-TRACE-001/"],
            "dev_bridge": {
                "work_class": "hosted",
                "operator_authorization_required": True,
                "task_spec": deepcopy(self.spec),
                "task_spec_hash": self.policy["task_spec_hash"],
            },
        }

        # Run canonical verify_execution_grant
        issuer_fingerprint = ea.verify_execution_grant(
            grant,
            policy=self.policy,
            task_id=self.task_id,
            generation=self.generation,
            trusted_issuers=trusted_issuers,
            now=self.now + timedelta(seconds=10),
            task=canonical_task,
        )
        self.assertEqual(issuer_fingerprint, self.signer.public_key_fingerprint)

        # Run replay consumption against durable ledger
        ledger: dict[str, Any] = {}
        ea.consume_grant_nonce(
            ledger,
            grant,
            task_id=self.task_id,
            now=self.now + timedelta(seconds=10),
            issuer_fingerprint=issuer_fingerprint,
        )
        self.assertEqual(len(ledger), 1)

        # Second consumption in ledger raises replay error
        with self.assertRaises(ea.ExecutionAuthorizationError):
            ea.consume_grant_nonce(
                ledger,
                grant,
                task_id=self.task_id,
                now=self.now + timedelta(seconds=11),
                issuer_fingerprint=issuer_fingerprint,
            )


if __name__ == "__main__":
    unittest.main()
