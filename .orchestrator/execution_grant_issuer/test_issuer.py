"""Comprehensive tests for the Execution Grant Issuer service.

OPS-EXECUTION-MFA-ISSUER-001.
Covers:
- Real firebase-admin SDK integration (structurally malformed tokens are
  rejected by the actual, unmocked ``firebase_admin.auth.verify_id_token``)
- Fail-closed denial when the SDK reports a revoked, disabled, expired,
  invalid, or certificate-unavailable token (``check_revoked=True``)
- Domain policy layered on top of verified claims: allowlist, email
  verification, tenant scoping, MFA second-factor, and auth_time freshness
- Ephemeral single-use challenge lifecycle, expiry, and replay protection
- Atomic concurrent challenge consumption (race condition prevention)
- Actor substitution, task substitution, and client policy modification rejection
- Mandatory S5 / step-5 execution-authorization pause
- Full cryptographic integration with execution_authorization.verify_execution_grant
- Redaction verification (no raw tokens, private keys, or bearer secrets logged)
- Readiness exercises the real ADC dependency and fails closed independently of liveness
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
import base64
from unittest.mock import MagicMock, patch

import google.auth.credentials
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import NameOID
from firebase_admin import _token_gen as firebase_token_gen
from firebase_admin import _user_mgt as firebase_user_mgt
from firebase_admin import auth as firebase_auth

# Ensure .orchestrator is in sys.path
_orchestrator_dir = Path(__file__).resolve().parents[1]
if str(_orchestrator_dir) not in sys.path:
    sys.path.insert(0, str(_orchestrator_dir))

import execution_authorization as ea
from execution_grant_issuer.challenge_store import ChallengeStore
from execution_grant_issuer.models import AuthenticationError, ChallengeError, PolicyValidationError
from execution_grant_issuer.secure_io import (
    UnsafeCredentialFileError,
    read_private_file_strict,
    write_private_exclusive_file,
)
from execution_grant_issuer.service import ExecutionGrantIssuerService
from execution_grant_issuer.signer import Ed25519GrantSigner
from execution_grant_issuer.token_verifier import IdentityPlatformTokenVerifier


def _decode_unverified_claims(token_str: str) -> Mapping[str, Any]:
    """Stand in for what the real firebase-admin SDK would return.

    Only used as the default patched behavior of
    ``firebase_auth.verify_id_token`` in tests that are not specifically
    exercising SDK-level denial (revoked/disabled/expired/invalid/cert
    outage) -- those tests instead patch the same call site with a genuine
    ``firebase_admin.auth`` exception instance. This keeps the extensively
    tested cryptographic signature/issuer/audience verification itself as
    the SDK's responsibility (proven separately by
    ``test_malformed_token_is_rejected_by_real_sdk`` against the real,
    unmocked SDK call) while deterministically covering this module's own
    domain policy (allowlist, tenant, MFA, freshness) without depending on
    live network access to Google's certificate endpoint in CI.
    """
    return jwt.decode(
        token_str,
        options={
            "verify_signature": False,
            "verify_aud": False,
            "verify_iss": False,
            "verify_exp": False,
        },
    )


def _self_signed_cert_pem(private_key: rsa.RSAPrivateKey) -> str:
    """Wrap an RSA public key in a self-signed x509 cert, as Google's real
    ID-token cert endpoint returns (``{kid: x509 PEM cert}``)."""
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "pantheon-test-cert")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2020, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2040, 1, 1, tzinfo=timezone.utc))
        .sign(private_key, hashes.SHA256())
    )
    return cert.public_bytes(Encoding.PEM).decode("utf-8")


class _FakeTransportResponse:
    """Mimics ``google.auth.transport.Response`` for the certificate fetch."""

    def __init__(self, data: bytes, status: int = 200) -> None:
        self.status = status
        self.data = data
        self.headers: dict[str, str] = {}


class TestRealFirebaseSdkCryptographicVerification(unittest.TestCase):
    """Genuine ``firebase_admin.auth`` cryptographic verification tests.

    Only the two real network dependencies -- the ID-token certificate
    endpoint and the account-lookup (revocation/disabled) REST call -- are
    mocked, at the exact transport boundary. Signature verification,
    audience/issuer checking, and revoked/disabled-account denial are all
    performed by the real, unmodified ``firebase_admin``/``google-auth``
    code paths, proving genuine SDK wiring beyond the structurally-malformed
    token case covered by ``test_malformed_token_is_rejected_by_real_sdk``.
    """

    PROJECT_ID = "pantheon-dev-20260902"

    @classmethod
    def setUpClass(cls) -> None:
        cls.rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.cert_kid = "real-sdk-test-kid-1"
        cls.cert_pem = _self_signed_cert_pem(cls.rsa_private_key)

    def setUp(self) -> None:
        # The real (unmocked) google-auth JWT decoder checks `iat`/`exp`
        # against the actual wall clock, not an injectable `now`, so this
        # class must mint tokens against real current time rather than the
        # fixed synthetic dates used elsewhere in this file.
        self.now = datetime.now(timezone.utc)
        self.operator_uid = "operator-real-sdk-uid"
        self.verifier = IdentityPlatformTokenVerifier(
            project_id=self.PROJECT_ID,
            allowed_operator_uids=[self.operator_uid],
        )

        # Supply synthetic SDK credentials in fixture so tests never depend
        # on ambient ADC (e.g. absent GOOGLE_APPLICATION_CREDENTIALS) while
        # keeping the real firebase-admin SDK cryptography and revocation code
        # fully real and exercised.
        self._cred_patch = patch.object(
            self.verifier._firebase_app.credential,
            "get_credential",
            return_value=google.auth.credentials.AnonymousCredentials(),
        )
        self.mock_cred = self._cred_patch.start()
        self.addCleanup(self._cred_patch.stop)

        # Ensure the cached client is reset so it inherits synthetic credentials
        app = self.verifier._firebase_app
        if hasattr(app, "_auth"):
            delattr(app, "_auth")
        if hasattr(app, "_services") and "_auth" in app._services:
            app._services.pop("_auth", None)

        def _cleanup_cached_client():
            if hasattr(app, "_auth"):
                delattr(app, "_auth")
            if hasattr(app, "_services") and "_auth" in app._services:
                app._services.pop("_auth", None)
        self.addCleanup(_cleanup_cached_client)

        # Mock only the certificate-fetch transport, at the exact boundary
        # where the real SDK issues its outbound HTTP GET. Everything above
        # this (JWT signature check, aud/iss validation) is real.
        cert_response = _FakeTransportResponse(
            json.dumps({self.cert_kid: self.cert_pem}).encode("utf-8")
        )
        self._cert_fetch_patch = patch.object(
            firebase_token_gen.CertificateFetchRequest,
            "__call__",
            return_value=cert_response,
        )
        self.mock_cert_fetch = self._cert_fetch_patch.start()
        self.addCleanup(self._cert_fetch_patch.stop)

        # Mock only the account-lookup (revocation/disabled) REST transport,
        # not the real_check_jwt_revoked_or_disabled logic that consumes it.
        self._user_record_response: dict[str, Any] = {
            "localId": self.operator_uid,
            "disabled": False,
            "validSince": str(int(self.now.timestamp()) - 1000),
        }
        self._get_user_patch = patch.object(
            firebase_user_mgt.UserManager,
            "get_user",
            side_effect=lambda **kwargs: dict(self._user_record_response),
        )
        self.mock_get_user = self._get_user_patch.start()
        self.addCleanup(self._get_user_patch.stop)

    def _mint(self, *, key=None, kid: str | None = None, aud: str | None = None, iss: str | None = None) -> str:
        current_ts = int(self.now.timestamp())
        claims: dict[str, Any] = {
            "iss": iss if iss is not None else f"https://securetoken.google.com/{self.PROJECT_ID}",
            "aud": aud if aud is not None else self.PROJECT_ID,
            "sub": self.operator_uid,
            "email": "operator-real-sdk@pantheon.trade",
            "email_verified": True,
            "auth_time": current_ts,
            "iat": current_ts,
            "exp": current_ts + 3600,
            "firebase": {
                "sign_in_provider": "password",
                "sign_in_second_factor": "totp",
            },
        }
        effective_key = key if key is not None else self.rsa_private_key
        effective_kid = kid if kid is not None else self.cert_kid
        return jwt.encode(claims, effective_key, algorithm="RS256", headers={"kid": effective_kid})

    def test_real_sdk_accepts_correctly_signed_and_verified_token(self) -> None:
        token = self._mint()
        operator = self.verifier.verify_token(token, now=self.now)
        self.assertEqual(operator.uid, self.operator_uid)
        self.assertEqual(operator.second_factor, "totp")
        # Assert genuine SDK transport and code-path execution
        self.assertEqual(self.mock_cert_fetch.call_count, 1)
        self.assertEqual(self.mock_get_user.call_count, 1)

    def test_real_sdk_rejects_wrong_signature(self) -> None:
        # Signed by a key that does not match the fetched certificate's
        # public key -- the real SDK's signature check must fail.
        token = self._mint(key=self.other_rsa_key)
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("could not verify token signature", str(cm.exception).lower())
        # Certificate fetch was executed, but revocation check was not reached
        self.assertEqual(self.mock_cert_fetch.call_count, 1)
        self.assertEqual(self.mock_get_user.call_count, 0)

    def test_real_sdk_rejects_unknown_kid(self) -> None:
        token = self._mint(kid="unknown-kid-not-in-certs")
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("certificate for key id", str(cm.exception).lower())
        self.assertEqual(self.mock_cert_fetch.call_count, 1)
        self.assertEqual(self.mock_get_user.call_count, 0)

    def test_real_sdk_rejects_wrong_audience(self) -> None:
        token = self._mint(aud="some-other-project")
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn('incorrect "aud"', str(cm.exception).lower())
        self.assertEqual(self.mock_cert_fetch.call_count, 0)
        self.assertEqual(self.mock_get_user.call_count, 0)

    def test_real_sdk_rejects_wrong_issuer(self) -> None:
        token = self._mint(iss="https://securetoken.google.com/some-other-project")
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn('incorrect "iss"', str(cm.exception).lower())
        self.assertEqual(self.mock_cert_fetch.call_count, 0)
        self.assertEqual(self.mock_get_user.call_count, 0)

    def test_real_sdk_denies_revoked_account_via_genuine_revocation_check(self) -> None:
        # tokens_valid_after is after this token's issued-at time, so the
        # real (unmocked) revocation comparison in
        # Client._check_jwt_revoked_or_disabled denies it.
        self._user_record_response["validSince"] = str(int(self.now.timestamp()) + 1000)
        token = self._mint()
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("revoked", str(cm.exception).lower())
        self.assertEqual(self.mock_cert_fetch.call_count, 1)
        self.assertEqual(self.mock_get_user.call_count, 1)

    def test_real_sdk_denies_disabled_account_via_genuine_lookup(self) -> None:
        self._user_record_response["disabled"] = True
        token = self._mint()
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("disabled", str(cm.exception).lower())
        self.assertEqual(self.mock_cert_fetch.call_count, 1)
        self.assertEqual(self.mock_get_user.call_count, 1)

    def test_real_sdk_rejects_firebase_auth_emulator_host_env_var(self) -> None:
        """P1 regression: refuse emulator host before app/client creation & verification."""
        with patch.dict(os.environ, {"FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"}):
            # Construction refuses emulator configuration before app creation
            with self.assertRaises(AuthenticationError) as cm:
                IdentityPlatformTokenVerifier(
                    project_id=self.PROJECT_ID,
                    allowed_operator_uids=[self.operator_uid],
                )
            self.assertIn("FIREBASE_AUTH_EMULATOR_HOST", str(cm.exception))

            # Verification refuses emulator configuration before calling SDK
            token = self._mint()
            with self.assertRaises(AuthenticationError) as cm:
                self.verifier.verify_token(token, now=self.now)
            self.assertIn("FIREBASE_AUTH_EMULATOR_HOST", str(cm.exception))

            # Readiness check refuses emulator configuration
            with self.assertRaises(AuthenticationError) as cm:
                self.verifier.check_identity_platform_readiness()
            self.assertIn("FIREBASE_AUTH_EMULATOR_HOST", str(cm.exception))

    def test_real_sdk_rejects_cached_emulated_auth_client(self) -> None:
        """P1 regression: refuse cached emulator auth client reuse."""
        app = self.verifier._firebase_app
        client = firebase_auth._get_client(app)
        client.emulated = True
        token = self._mint()
        try:
            with self.assertRaises(AuthenticationError) as cm:
                self.verifier.verify_token(token, now=self.now)
            self.assertIn("emulator", str(cm.exception).lower())
        finally:
            client.emulated = False

    def test_real_sdk_probe_alg_none_and_emulator_cannot_obtain_challenge_or_sign_grant(self) -> None:
        """Exact reproduction of Codex P1 reviewer probe:
        Attempts to bypass signature verification via alg=none or emulator
        MUST NOT result in challenge creation or grant signing.
        """
        def _b64url(d: Mapping[str, Any]) -> str:
            return base64.urlsafe_b64encode(json.dumps(d).encode("utf-8")).decode("utf-8").rstrip("=")

        current_ts = int(self.now.timestamp())
        raw_header = {"alg": "none", "typ": "JWT"}
        raw_payload = {
            "iss": f"https://securetoken.google.com/{self.PROJECT_ID}",
            "aud": self.PROJECT_ID,
            "sub": self.operator_uid,
            "email": "operator-real-sdk@pantheon.trade",
            "email_verified": True,
            "auth_time": current_ts,
            "iat": current_ts,
            "exp": current_ts + 3600,
            "firebase": {
                "sign_in_provider": "password",
                "sign_in_second_factor": "totp",
            },
        }
        alg_none_token = f"{_b64url(raw_header)}.{_b64url(raw_payload)}."

        # Case A: alg=none token under standard configuration fails cryptographic check
        # Case A1: Without kid, SDK rejects missing kid
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(alg_none_token, now=self.now)
        self.assertIn("kid", str(cm.exception).lower())

        # Case A2: With kid supplied, SDK rejects invalid algorithm
        raw_header_with_kid = {"alg": "none", "typ": "JWT", "kid": "some-kid"}
        alg_none_token_with_kid = f"{_b64url(raw_header_with_kid)}.{_b64url(raw_payload)}."
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(alg_none_token_with_kid, now=self.now)
        self.assertIn("algorithm", str(cm.exception).lower())

        # Case B: If FIREBASE_AUTH_EMULATOR_HOST is set in environment,
        # create_challenge and issue_grant MUST fail before challenge/signing.
        service = ExecutionGrantIssuerService(
            verifier=self.verifier,
            signer=Ed25519GrantSigner(ed25519.Ed25519PrivateKey.generate(), key_id="probe-key"),
            allowed_tasks=["DEV502-TRACE-001"],
            allowed_environments=["pantheon-dev"],
        )
        task_policy = ea.derive_execution_policy(
            task_id="DEV502-TRACE-001",
            work_class="hosted",
            repository="pantheon",
            environment="pantheon-dev",
            resources=["pantheon-dev"],
            action_scope="execute",
            artifacts=["docs/deployment/evidence/DEV502-TRACE-001/"],
            task_spec={"id": "DEV502-TRACE-001", "phase": "trace"},
        )

        with patch.dict(os.environ, {"FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"}):
            with self.assertRaises(AuthenticationError):
                service.handle_create_challenge(
                    alg_none_token,
                    {"task_id": "DEV502-TRACE-001", "generation": 1, "policy_snapshot": task_policy},
                    now=self.now,
                )
            self.assertEqual(len(service.challenge_store._challenges), 0)

            with self.assertRaises(AuthenticationError):
                service.handle_issue_grant(
                    alg_none_token,
                    {"challenge_id": "cid-none", "task_id": "DEV502-TRACE-001", "generation": 1, "policy_snapshot": task_policy},
                    now=self.now,
                )


class TestExecutionGrantIssuer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Generate RSA keys once at class level for fast test execution
        cls.rsa_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        cls.ed25519_private_key = ed25519.Ed25519PrivateKey.generate()

    def setUp(self) -> None:
        self.now = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
        self.project_id = "pantheon-dev-20260902"
        self.operator_uid = "operator-chloe-primary"
        self.operator_email = "operator-chloe@pantheon.trade"

        self.signer_key_id = "pantheon-mfa-issuer-test-key-1"
        self.signer = Ed25519GrantSigner(self.ed25519_private_key, key_id=self.signer_key_id)

        # Real IdentityPlatformTokenVerifier: constructs a real (lazily
        # ADC-backed) firebase_admin App. Application Default Credentials are
        # only actually resolved if verify_id_token or the readiness check is
        # exercised without a patch installed.
        self.verifier = IdentityPlatformTokenVerifier(
            project_id=self.project_id,
            allowed_operator_uids=[self.operator_uid],
            max_auth_age_seconds=3600,
        )

        self._verify_id_token_patch = patch(
            "execution_grant_issuer.token_verifier.firebase_auth.verify_id_token",
            side_effect=self._verify_id_token_stub,
        )
        self.mock_verify_id_token = self._verify_id_token_patch.start()
        self.addCleanup(self._verify_id_token_patch.stop)

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

    @staticmethod
    def _verify_id_token_stub(token_str, app=None, check_revoked=False, clock_skew_seconds=0):
        return _decode_unverified_claims(token_str)

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
        effective_kid = kid if kid is not None else "test-rsa-kid-1"

        return jwt.encode(
            claims,
            effective_key,
            algorithm="RS256",
            headers={"kid": effective_kid},
        )

    # -------------------------------------------------------------------------
    # 1. Real SDK Integration & Fail-Closed Denial Tests
    # -------------------------------------------------------------------------

    def test_valid_token_with_mfa_succeeds(self) -> None:
        token = self._mint_id_token()
        operator = self.verifier.verify_token(token, now=self.now)
        self.assertEqual(operator.uid, self.operator_uid)
        self.assertEqual(operator.email, self.operator_email)
        self.assertEqual(operator.second_factor, "totp")
        # Confirm the real SDK entrypoint was actually invoked with
        # check_revoked=True (the default), not bypassed.
        self.mock_verify_id_token.assert_called_once()
        _, kwargs = self.mock_verify_id_token.call_args
        self.assertTrue(kwargs["check_revoked"])
        self.assertIs(kwargs["app"], self.verifier._firebase_app)

    def test_malformed_token_is_rejected_by_real_sdk(self) -> None:
        """Exercises the real, unmocked firebase_admin.auth.verify_id_token.

        A structurally malformed token fails inside the SDK's own segment
        parsing before any certificate fetch or network call, so this proves
        genuine SDK wiring without depending on live network access in CI.
        """
        self._verify_id_token_patch.stop()
        try:
            with self.assertRaises(AuthenticationError) as cm:
                self.verifier.verify_token("not.a.valid.jwt", now=self.now)
            self.assertIn("token verification failed", str(cm.exception).lower())
        finally:
            self.mock_verify_id_token = self._verify_id_token_patch.start()

    def test_sdk_denies_revoked_token(self) -> None:
        self.mock_verify_id_token.side_effect = firebase_auth.RevokedIdTokenError(
            "Firebase ID token has been revoked"
        )
        token = self._mint_id_token()
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("revoked", str(cm.exception).lower())

    def test_sdk_denies_disabled_account(self) -> None:
        self.mock_verify_id_token.side_effect = firebase_auth.UserDisabledError(
            "The user record is disabled"
        )
        token = self._mint_id_token()
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("disabled", str(cm.exception).lower())

    def test_sdk_denies_expired_token(self) -> None:
        self.mock_verify_id_token.side_effect = firebase_auth.ExpiredIdTokenError(
            "Firebase ID token has expired", cause=None
        )
        token = self._mint_id_token()
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("expired", str(cm.exception).lower())

    def test_sdk_certificate_fetch_failure_fails_closed(self) -> None:
        self.mock_verify_id_token.side_effect = firebase_auth.CertificateFetchError(
            "Could not fetch certificates", cause=None
        )
        token = self._mint_id_token()
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("failed to fetch identity platform public keys", str(cm.exception).lower())

    def test_sdk_invalid_token_denied(self) -> None:
        self.mock_verify_id_token.side_effect = firebase_auth.InvalidIdTokenError(
            "Firebase ID token has invalid signature"
        )
        token = self._mint_id_token()
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("token verification failed", str(cm.exception).lower())

    def test_unexpected_sdk_error_fails_closed(self) -> None:
        self.mock_verify_id_token.side_effect = RuntimeError("synthetic transport outage")
        token = self._mint_id_token()
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("token verification failed", str(cm.exception).lower())

    def test_check_revocation_cannot_be_disabled(self) -> None:
        """Revocation/disabled-account denial has no disable switch at all.

        There is no ``check_revocation`` constructor parameter to pass, and
        every real verification call is hardcoded to ``check_revoked=True``
        regardless of how the verifier is constructed. See also
        ``deploy/execution-grant-issuer/test_run_server.py`` for proof that
        the real ``run_service`` entrypoint refuses to start if a
        configuration file tries to disable this.
        """
        with self.assertRaises(TypeError):
            IdentityPlatformTokenVerifier(
                project_id=self.project_id,
                allowed_operator_uids=[self.operator_uid],
                check_revocation=False,
            )
        v = IdentityPlatformTokenVerifier(
            project_id=self.project_id,
            allowed_operator_uids=[self.operator_uid],
        )
        with patch(
            "execution_grant_issuer.token_verifier.firebase_auth.verify_id_token",
            side_effect=self._verify_id_token_stub,
        ) as mock_verify:
            v.verify_token(self._mint_id_token(), now=self.now)
        self.assertTrue(mock_verify.call_args.kwargs["check_revoked"])

    # -------------------------------------------------------------------------
    # 2. Domain Policy Layered On Top Of Verified Claims
    # -------------------------------------------------------------------------

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

    def test_rejects_empty_allowlist(self) -> None:
        v = IdentityPlatformTokenVerifier(
            project_id=self.project_id,
            allowed_operator_uids=[],
        )
        token = self._mint_id_token()
        with self.assertRaises(AuthenticationError) as cm:
            v.verify_token(token, now=self.now)
        self.assertIn("allowlist is empty", str(cm.exception).lower())

    def test_rejects_wrong_tenant_or_unexpected_tenant(self) -> None:
        token = self._mint_id_token(
            custom_claims={"firebase": {"sign_in_provider": "password", "sign_in_second_factor": "totp", "tenant": "untrusted-tenant"}}
        )
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("tenant", str(cm.exception).lower())

        v_tenant = IdentityPlatformTokenVerifier(
            project_id=self.project_id,
            allowed_operator_uids=[self.operator_uid],
            expected_tenant_id="my-tenant-1",
        )
        with self.assertRaises(AuthenticationError) as cm:
            v_tenant.verify_token(token, now=self.now)
        self.assertIn("tenant mismatch", str(cm.exception).lower())

        valid_tenant_token = self._mint_id_token(
            custom_claims={"firebase": {"sign_in_provider": "password", "sign_in_second_factor": "totp", "tenant": "my-tenant-1"}}
        )
        op = v_tenant.verify_token(valid_tenant_token, now=self.now)
        self.assertEqual(op.uid, self.operator_uid)

    def test_rejects_factor_identifier_without_method(self) -> None:
        token = self._mint_id_token(
            second_factor=None,
            custom_claims={"firebase": {"sign_in_provider": "password", "second_factor_identifier": "totp"}}
        )
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("sign_in_second_factor", str(cm.exception).lower())

    def test_rejects_custom_provider(self) -> None:
        token = self._mint_id_token(
            sign_in_provider="custom",
            second_factor="totp",
        )
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(token, now=self.now)
        self.assertIn("custom", str(cm.exception).lower())

    def test_malformed_auth_time_claim_rejected(self) -> None:
        t_auth_str = self._mint_id_token(custom_claims={"auth_time": "invalid"})
        with self.assertRaises(AuthenticationError) as cm:
            self.verifier.verify_token(t_auth_str, now=self.now)
        self.assertIn("auth_time", str(cm.exception).lower())

    # -------------------------------------------------------------------------
    # 3. Readiness Exercises The Real ADC Dependency
    # -------------------------------------------------------------------------

    def test_readiness_fails_closed_when_adc_unavailable(self) -> None:
        with patch.object(
            self.verifier._firebase_app.credential,
            "get_credential",
            side_effect=RuntimeError("synthetic ADC outage"),
        ):
            with self.assertRaises(AuthenticationError) as cm:
                self.verifier.check_identity_platform_readiness()
            self.assertIn("application default credentials", str(cm.exception).lower())

    def test_readiness_succeeds_when_adc_resolves_and_refreshes(self) -> None:
        credential = MagicMock()
        with patch.object(self.verifier._firebase_app.credential, "get_credential", return_value=credential):
            self.verifier.check_identity_platform_readiness()  # does not raise
        credential.refresh.assert_called_once()

    def test_readiness_fails_closed_when_credential_refresh_fails(self) -> None:
        """A cached-but-stale credential object must not report ready.

        Reproduces the exact reviewer-found gap: obtaining the cached ADC
        object alone said nothing about whether it can still actually be
        refreshed against Google's token endpoint.
        """
        credential = MagicMock()
        credential.refresh.side_effect = RuntimeError("synthetic refresh failure")
        with patch.object(self.verifier._firebase_app.credential, "get_credential", return_value=credential):
            with self.assertRaises(AuthenticationError) as cm:
                self.verifier.check_identity_platform_readiness()
            self.assertIn("application default credentials", str(cm.exception).lower())
        credential.refresh.assert_called_once()

    def test_readiness_failure_message_never_echoes_raw_exception_text(self) -> None:
        """Only the exception type name is reported, never str(exc)."""
        credential = MagicMock()
        credential.refresh.side_effect = RuntimeError("SYNTHETIC_SECRET_SENTINEL_IN_URL")
        with patch.object(self.verifier._firebase_app.credential, "get_credential", return_value=credential):
            with self.assertRaises(AuthenticationError) as cm:
                self.verifier.check_identity_platform_readiness()
        self.assertNotIn("SYNTHETIC_SECRET_SENTINEL_IN_URL", str(cm.exception))
        self.assertIn("RuntimeError", str(cm.exception))

    def test_service_readiness_returns_503_shape_on_adc_failure(self) -> None:
        with patch.object(
            self.verifier._firebase_app.credential,
            "get_credential",
            side_effect=RuntimeError("synthetic ADC outage"),
        ):
            result = self.service.get_readiness()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("unavailable", result["checks"]["identity_platform_credentials"])

    def test_service_readiness_ok_when_all_dependencies_healthy(self) -> None:
        with patch.object(
            self.verifier._firebase_app.credential, "get_credential", return_value=MagicMock()
        ):
            result = self.service.get_readiness()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["checks"]["identity_platform_credentials"], "ok")

    def test_liveness_is_trivial_and_independent_of_readiness(self) -> None:
        with patch.object(
            self.verifier._firebase_app.credential,
            "get_credential",
            side_effect=RuntimeError("synthetic ADC outage"),
        ):
            liveness = self.service.get_liveness()
            readiness = self.service.get_readiness()
        self.assertEqual(liveness["status"], "ok")
        self.assertEqual(readiness["status"], "unavailable")

    # -------------------------------------------------------------------------
    # 4. Challenge Lifecycle, Expiry, and Replay Tests
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
    # 5. Security Binding Enforcement (Actor, Task, Gen, Policy)
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
    # 6. Redaction Verification (No Secrets Leaked)
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
    # 7. Full End-to-End Cryptographic Integration with execution_authorization
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

    # -------------------------------------------------------------------------
    # 8. Additional Regression Tests for Reviewer Findings
    # -------------------------------------------------------------------------

    def test_wrong_actor_does_not_burn_challenge(self) -> None:
        token = self._mint_id_token(uid=self.operator_uid)
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

        with self.assertRaises(ChallengeError):
            self.service.challenge_store.consume_challenge(
                challenge_id=cid,
                actor_uid="synthetic-wrong-actor",
                task_id=self.task_id,
                generation=self.generation,
                policy_snapshot=self.policy,
                now=self.now,
            )

        stored = self.service.challenge_store.get_challenge(cid)
        self.assertIsNotNone(stored)
        self.assertFalse(stored.consumed)

        consumed = self.service.challenge_store.consume_challenge(
            challenge_id=cid,
            actor_uid=self.operator_uid,
            task_id=self.task_id,
            generation=self.generation,
            policy_snapshot=self.policy,
            now=self.now,
        )
        self.assertTrue(consumed.consumed)

    def test_challenge_store_returns_defensive_copies(self) -> None:
        store = ChallengeStore()
        challenge = store.create_challenge(
            actor_uid=self.operator_uid,
            actor_email=self.operator_email,
            task_id=self.task_id,
            generation=self.generation,
            policy_snapshot=self.policy,
            policy_digest=self.policy["policy_digest"],
            environment="pantheon-dev",
            resources=["pantheon-dev"],
            now=self.now,
        )
        cid = challenge.challenge_id
        challenge.actor_uid = "tampered-uid"
        challenge.policy_snapshot["action_scope"] = "tampered"

        stored = store.get_challenge(cid)
        self.assertEqual(stored.actor_uid, self.operator_uid)
        self.assertEqual(stored.policy_snapshot["action_scope"], "execute")


class TestSecureIoAndOperationalPatterns(unittest.TestCase):
    """OPS-EXECUTION-MFA-ISSUER-001.

    Validates:
    - write_private_exclusive_file writes private mode 0600 files atomically.
    - Reused-path attempts (pre-existing 0644 or 0600 file) fail closed without overwrite.
    - Symlink attempts fail closed and do not clobber target files.
    - Synthetic reproduction of documented operational MFA pattern validates that
      writing outputs exclusively inside a freshly created private 0700 dir succeeds,
      while pre-existing or symlinked outputs are strictly rejected.
    """

    def test_write_private_exclusive_file_creates_0600(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "secret.txt"
            written = write_private_exclusive_file(target, "super-secret-token", description="Test token")
            self.assertEqual(written, target)
            self.assertTrue(target.is_file())
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
            self.assertEqual(target.read_text(encoding="utf-8"), "super-secret-token")

    def test_write_private_exclusive_file_rejects_reused_path_preserving_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "reused.txt"
            target.write_text("ORIGINAL_0644_CONTENT", encoding="utf-8")
            os.chmod(target, 0o644)

            with self.assertRaises(UnsafeCredentialFileError) as cm:
                write_private_exclusive_file(target, "NEW_SECRET_ATTEMPT", description="Reused test")
            self.assertIn("already exists or is a symlink", str(cm.exception))
            self.assertEqual(target.read_text(encoding="utf-8"), "ORIGINAL_0644_CONTENT")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

    def test_write_private_exclusive_file_rejects_symlink_preserving_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            real_target = Path(td) / "real_file.txt"
            real_target.write_text("SENSITIVE_FILE_DO_NOT_CLOBBER", encoding="utf-8")

            symlink = Path(td) / "symlink_dest.txt"
            symlink.symlink_to(real_target)

            with self.assertRaises(UnsafeCredentialFileError) as cm:
                write_private_exclusive_file(symlink, "ATTACKER_CONTENT", description="Symlink test")
            self.assertIn("already exists or is a symlink", str(cm.exception))
            self.assertEqual(real_target.read_text(encoding="utf-8"), "SENSITIVE_FILE_DO_NOT_CLOBBER")

    def test_read_private_file_strict_enforcements(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            nonexistent = Path(td) / "absent.txt"
            with self.assertRaises(UnsafeCredentialFileError) as cm:
                read_private_file_strict(nonexistent, description="Absent test")
            self.assertIn("not found or inaccessible", str(cm.exception))

            real_file = Path(td) / "real.txt"
            real_file.write_text("payload", encoding="utf-8")
            os.chmod(real_file, 0o600)
            symlink = Path(td) / "link.txt"
            symlink.symlink_to(real_file)
            with self.assertRaises(UnsafeCredentialFileError) as cm:
                read_private_file_strict(symlink, description="Symlink test")
            self.assertIn("must not be a symlink", str(cm.exception))

            os.chmod(real_file, 0o644)
            with self.assertRaises(UnsafeCredentialFileError) as cm:
                read_private_file_strict(real_file, description="Mode test")
            self.assertIn("require mode 0600", str(cm.exception))

            os.chmod(real_file, 0o600)
            data = read_private_file_strict(real_file, description="Valid test")
            self.assertEqual(data, b"payload")

    def test_synthetic_operational_curl_and_python_pattern_symlink_and_reused_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            mfa_dir = Path(td) / "pantheon-mfa-test"
            mfa_dir.mkdir(mode=0o700)
            self.assertEqual(stat.S_IMODE(mfa_dir.stat().st_mode), 0o700)

            step1_dest = mfa_dir / "signin-step1.json"
            token_dest = mfa_dir / "operator-token.txt"

            step1_dest.write_text('{"initial": "0644_data"}', encoding="utf-8")
            os.chmod(step1_dest, 0o644)

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            with self.assertRaises(FileExistsError):
                os.open(str(step1_dest), flags, 0o600)
            self.assertEqual(step1_dest.read_text(encoding="utf-8"), '{"initial": "0644_data"}')
            self.assertEqual(stat.S_IMODE(step1_dest.stat().st_mode), 0o644)
            step1_dest.unlink()

            target_outside = Path(td) / "target_sensitive.txt"
            target_outside.write_text("PRESERVED_TARGET", encoding="utf-8")
            token_dest.symlink_to(target_outside)

            with self.assertRaises((FileExistsError, OSError)):
                os.open(str(token_dest), flags, 0o600)
            self.assertEqual(target_outside.read_text(encoding="utf-8"), "PRESERVED_TARGET")
            token_dest.unlink()

            fd = os.open(str(step1_dest), flags, 0o600)
            with open(fd, "w", encoding="utf-8") as f:
                f.write('{"mfaPendingCredential": "cred", "mfaInfo": [{"mfaEnrollmentId": "id"}]}')
            self.assertEqual(stat.S_IMODE(step1_dest.stat().st_mode), 0o600)

            fd2 = os.open(str(token_dest), flags, 0o600)
            with open(fd2, "w", encoding="utf-8") as f:
                f.write("mock-id-token")
            self.assertEqual(stat.S_IMODE(token_dest.stat().st_mode), 0o600)

    def test_documented_shell_flow_offline_regression_success_and_failure_modes(self) -> None:
        """OPS-EXECUTION-MFA-ISSUER-001 offline shell flow regression.

        Extracts the exact dedented markdown snippets from docs/operations/execution-grant-issuer.md
        and executes them via bash with a synthetic curl stub under:
        - Successful end-to-end flow: verifying private 0600 file creation, secret unsetting,
          and credential shredding.
        - Step 1 error response: verifying nonzero exit status and no file creation.
        - Step 1 non-MFA challenge response: verifying nonzero exit status and no file creation.
        - Step 2 error response: verifying nonzero exit status and no file creation.
        - Step 2 missing idToken: verifying nonzero exit status and no file creation.
        - Step 3 pre-existing destination collision: verifying nonzero exit status.
        """
        doc_path = Path(__file__).resolve().parents[2] / "docs" / "operations" / "execution-grant-issuer.md"
        self.assertTrue(doc_path.is_file(), f"Document not found at {doc_path}")
        doc = doc_path.read_text(encoding="utf-8")

        m1 = re.search(r"1\.\s+\*\*Sign in with password.*?\n\s*```bash\n(.*?)```", doc, re.DOTALL)
        self.assertIsNotNone(m1, "Step 1 block not found in docs")
        step1_block = textwrap.dedent(m1.group(1))

        m2 = re.search(r"2\.\s+\*\*Finalize the second factor.*?\n\s*```bash\n(.*?)```", doc, re.DOTALL)
        self.assertIsNotNone(m2, "Step 2 block not found in docs")
        step2_block = textwrap.dedent(m2.group(1))

        m3 = re.search(r"3\.\s+\*\*Extracting Token.*?\n\s*```bash\n(.*?)```", doc, re.DOTALL)
        self.assertIsNotNone(m3, "Step 3 block not found in docs")
        step3_block = textwrap.dedent(m3.group(1))

        # 1. Test End-to-end success flow
        with tempfile.TemporaryDirectory() as td:
            mfa_dir = Path(td) / "mfa"
            mfa_dir.mkdir(mode=0o700)

            # Step 1: Sign-in initiating MFA
            step1_response = json.dumps({"mfaPendingCredential": "test-cred-123", "mfaInfo": [{"mfaEnrollmentId": "test-enroll-456"}]})
            script1 = f"""
curl() {{
    cat > /dev/null
    echo '{step1_response}'
}}
export IDENTITY_PLATFORM_API_KEY="test-api-key"
export MFA_DIR="{mfa_dir}"
{step1_block}
"""
            p1 = subprocess.run(["bash", "-c", script1], input=b"test-password\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p1.returncode, 0, f"Step 1 failed with stderr: {p1.stderr.decode()}")
            step1_file = mfa_dir / "signin-step1.json"
            self.assertTrue(step1_file.is_file(), "step 1 output file was not created")
            self.assertFalse(step1_file.is_symlink(), "step 1 output file is a symlink")
            self.assertEqual(stat.S_IMODE(step1_file.stat().st_mode), 0o600)
            data1 = json.loads(step1_file.read_text(encoding="utf-8"))
            self.assertEqual(data1["mfaPendingCredential"], "test-cred-123")

            # Step 2: Finalize second factor
            step2_response = json.dumps({"idToken": "test-verified-mfa-token.jwt"})
            script2 = f"""
curl() {{
    cat > /dev/null
    echo '{step2_response}'
}}
export IDENTITY_PLATFORM_API_KEY="test-api-key"
export MFA_DIR="{mfa_dir}"
{step2_block}
"""
            p2 = subprocess.run(["bash", "-c", script2], input=b"654321\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p2.returncode, 0, f"Step 2 failed with stderr: {p2.stderr.decode()}")
            self.assertFalse(step1_file.exists(), "step 1 output file was not shredded")
            step2_file = mfa_dir / "signin-step2.json"
            self.assertTrue(step2_file.is_file(), "step 2 output file was not created")
            self.assertFalse(step2_file.is_symlink(), "step 2 output file is a symlink")
            self.assertEqual(stat.S_IMODE(step2_file.stat().st_mode), 0o600)
            data2 = json.loads(step2_file.read_text(encoding="utf-8"))
            self.assertEqual(data2["idToken"], "test-verified-mfa-token.jwt")

            # Step 3: Extract token
            script3 = f"""
export MFA_DIR="{mfa_dir}"
{step3_block}
"""
            p3 = subprocess.run(["bash", "-c", script3], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(p3.returncode, 0, f"Step 3 failed with stderr: {p3.stderr.decode()}")
            self.assertFalse(step2_file.exists(), "step 2 output file was not shredded")
            token_file = mfa_dir / "operator-token.txt"
            self.assertTrue(token_file.is_file(), "operator token file was not created")
            self.assertFalse(token_file.is_symlink(), "operator token file is a symlink")
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
            self.assertEqual(token_file.read_text(encoding="utf-8").strip(), "test-verified-mfa-token.jwt")

        # 2. Test Step 1 Error response -> nonzero exit status, no output file
        with tempfile.TemporaryDirectory() as td:
            mfa_dir = Path(td) / "mfa"
            mfa_dir.mkdir(mode=0o700)
            err_response = json.dumps({"error": {"message": "INVALID_PASSWORD"}})
            script_err = f"""
curl() {{
    cat > /dev/null
    echo '{err_response}'
}}
export IDENTITY_PLATFORM_API_KEY="test-api-key"
export MFA_DIR="{mfa_dir}"
{step1_block}
"""
            p = subprocess.run(["bash", "-c", script_err], input=b"wrong-password\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(p.returncode, 0, "Step 1 should fail with nonzero returncode on API error")
            self.assertFalse((mfa_dir / "signin-step1.json").exists(), "step 1 file created despite error")

        # 3. Test Step 1 non-MFA challenge response -> nonzero exit status, no output file
        with tempfile.TemporaryDirectory() as td:
            mfa_dir = Path(td) / "mfa"
            mfa_dir.mkdir(mode=0o700)
            nomfa_response = json.dumps({"idToken": "single-factor-only-token"})
            script_nomfa = f"""
curl() {{
    cat > /dev/null
    echo '{nomfa_response}'
}}
export IDENTITY_PLATFORM_API_KEY="test-api-key"
export MFA_DIR="{mfa_dir}"
{step1_block}
"""
            p = subprocess.run(["bash", "-c", script_nomfa], input=b"pass\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(p.returncode, 0, "Step 1 should fail with nonzero returncode on non-MFA response")
            self.assertFalse((mfa_dir / "signin-step1.json").exists(), "step 1 file created despite missing MFA")

        # 4. Test Step 2 Error response -> nonzero exit status, no output file
        with tempfile.TemporaryDirectory() as td:
            mfa_dir = Path(td) / "mfa"
            mfa_dir.mkdir(mode=0o700)
            (mfa_dir / "signin-step1.json").write_text(
                json.dumps({"mfaPendingCredential": "c", "mfaInfo": [{"mfaEnrollmentId": "e"}]}),
                encoding="utf-8",
            )
            mfa_err_response = json.dumps({"error": {"message": "INVALID_MFA_CODE"}})
            script_mfa_err = f"""
curl() {{
    cat > /dev/null
    echo '{mfa_err_response}'
}}
export IDENTITY_PLATFORM_API_KEY="test-api-key"
export MFA_DIR="{mfa_dir}"
{step2_block}
"""
            p = subprocess.run(["bash", "-c", script_mfa_err], input=b"000000\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(p.returncode, 0, "Step 2 should fail with nonzero returncode on MFA error")
            self.assertFalse((mfa_dir / "signin-step2.json").exists(), "step 2 file created despite MFA error")

        # 5. Test Step 2 missing idToken -> nonzero exit status, no output file
        with tempfile.TemporaryDirectory() as td:
            mfa_dir = Path(td) / "mfa"
            mfa_dir.mkdir(mode=0o700)
            (mfa_dir / "signin-step1.json").write_text(
                json.dumps({"mfaPendingCredential": "c", "mfaInfo": [{"mfaEnrollmentId": "e"}]}),
                encoding="utf-8",
            )
            script_no_token = f"""
curl() {{
    cat > /dev/null
    echo '{{}}'
}}
export IDENTITY_PLATFORM_API_KEY="test-api-key"
export MFA_DIR="{mfa_dir}"
{step2_block}
"""
            p = subprocess.run(["bash", "-c", script_no_token], input=b"123456\n", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(p.returncode, 0, "Step 2 should fail with nonzero returncode when idToken missing")
            self.assertFalse((mfa_dir / "signin-step2.json").exists(), "step 2 file created despite missing idToken")

        # 6. Test Step 3 collision -> nonzero exit status
        with tempfile.TemporaryDirectory() as td:
            mfa_dir = Path(td) / "mfa"
            mfa_dir.mkdir(mode=0o700)
            (mfa_dir / "signin-step2.json").write_text(json.dumps({"idToken": "secret"}), encoding="utf-8")
            (mfa_dir / "operator-token.txt").write_text("pre-existing", encoding="utf-8")
            script_collision = f"""
export MFA_DIR="{mfa_dir}"
{step3_block}
"""
            p = subprocess.run(["bash", "-c", script_collision], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(p.returncode, 0, "Step 3 should fail with nonzero returncode on collision")


if __name__ == "__main__":
    unittest.main()

