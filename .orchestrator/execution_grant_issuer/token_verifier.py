"""Cryptographic Identity Platform token verification.

OPS-EXECUTION-MFA-ISSUER-001.
Verifies fresh genuine Identity Platform user MFA tokens against configured
project, allowed operator UIDs, second factor claims, and freshness limits.

Signature, issuer, audience, expiry, and revoked/disabled-account denial are
delegated entirely to the pinned ``firebase-admin`` SDK's
``auth.verify_id_token(check_revoked=True)``, always invoked with
``check_revoked=True`` -- there is no configuration path in this module,
``run_server.py``, or any caller that can disable the revocation/disabled-
account check.
authenticated with Application Default Credentials on the isolated issuer
host. This module never re-implements token cryptography, never talks to
Google endpoints directly, and never requires a downloadable service-account
key file. It only layers the domain-specific MFA/operator-allowlist/tenant/
freshness policy on top of the claims the SDK already verified.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import firebase_admin
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials as firebase_credentials
from google.auth.transport import requests as google_auth_transport_requests

from .models import AuthenticationError, VerifiedOperator

logger = logging.getLogger("execution_grant_issuer.token_verifier")

SUPPORTED_SECOND_FACTORS = frozenset({"phone", "totp", "sms", "email", "security_key"})
DEFAULT_MAX_AUTH_AGE_SECONDS = 3600
CLOCK_SKEW_TOLERANCE_SECONDS = 10


def check_no_firebase_auth_emulator(app: firebase_admin.App | None = None) -> None:
    """Ensure Firebase Auth emulator is strictly not used or configured.

    Pinned firebase-admin 6.5.0 skips token signature verification entirely
    when FIREBASE_AUTH_EMULATOR_HOST is set or when an emulated auth client is
    used. Official contract: https://firebase.google.com/docs/emulator-suite/connect_auth.
    To preserve genuine cryptographic verification, emulator mode is refused
    prior to app creation, client initialization, readiness checks, and token
    verification.
    """
    emulator_host = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")
    if emulator_host is not None:
        raise AuthenticationError(
            f"FIREBASE_AUTH_EMULATOR_HOST is set ({emulator_host!r}); "
            "Firebase Auth emulator is strictly prohibited"
        )
    try:
        from firebase_admin import _auth_utils
        if _auth_utils.is_emulated():
            raise AuthenticationError(
                "Firebase Auth emulator is active; emulator mode is strictly prohibited"
            )
    except ImportError:
        pass

    if app is not None:
        auth_attr = getattr(firebase_auth, "_AUTH_ATTRIBUTE", "_auth")
        client = None
        if hasattr(app, "_services") and isinstance(app._services, dict):
            client = app._services.get(auth_attr)
        if client is None:
            client = getattr(app, auth_attr, None)
        if client is not None and getattr(client, "emulated", False):
            raise AuthenticationError(
                "Cached Firebase Auth client is in emulator mode; emulator mode is strictly prohibited"
            )


class IdentityPlatformTokenVerifier:
    """Verifies Identity Platform user ID tokens with genuine MFA claims.

    Cryptographic verification (signature, issuer, audience, expiry, and
    revoked/disabled-account denial) is delegated to ``firebase_admin.auth``.
    This class only enforces the additional operational policy: an explicit
    operator UID allowlist, verified email, tenant scoping, a genuine
    second-factor claim, and freshness of the ``auth_time`` claim.
    """

    def __init__(
        self,
        *,
        project_id: str = "pantheon-dev-20260902",
        allowed_operator_uids: Sequence[str] | None = None,
        max_auth_age_seconds: int = DEFAULT_MAX_AUTH_AGE_SECONDS,
        allowed_second_factors: Sequence[str] | None = None,
        expected_tenant_id: str | None = None,
        clock_skew_seconds: int = CLOCK_SKEW_TOLERANCE_SECONDS,
    ) -> None:
        if not project_id or not project_id.strip():
            raise ValueError("project_id must be non-empty")
        check_no_firebase_auth_emulator()
        self.project_id = project_id.strip()
        self.allowed_operator_uids = (
            frozenset(uid.strip() for uid in allowed_operator_uids if uid and uid.strip())
            if allowed_operator_uids is not None
            else frozenset()
        )
        self.max_auth_age_seconds = max_auth_age_seconds
        self.allowed_second_factors = (
            frozenset(f.strip().lower() for f in allowed_second_factors if f and f.strip())
            if allowed_second_factors is not None
            else SUPPORTED_SECOND_FACTORS
        )
        self.expected_tenant_id = (
            expected_tenant_id.strip() if expected_tenant_id and expected_tenant_id.strip() else None
        )
        self.clock_skew_seconds = clock_skew_seconds
        self._firebase_app = self._get_or_init_firebase_app(self.project_id)

    @staticmethod
    def _get_or_init_firebase_app(project_id: str) -> firebase_admin.App:
        """Return a cached Firebase Admin app bound to this exact project.

        Uses Application Default Credentials on the isolated issuer host --
        never a downloadable service-account key file. ``get_app`` is reused
        across instances constructed for the same project so repeated
        verifier construction (e.g. one per request) does not raise
        "app already exists" or leak duplicate credentialed clients.
        """
        check_no_firebase_auth_emulator()
        app_name = f"pantheon-execution-grant-issuer-{project_id}"
        try:
            app = firebase_admin.get_app(app_name)
            check_no_firebase_auth_emulator(app)
            return app
        except ValueError:
            app = firebase_admin.initialize_app(
                firebase_credentials.ApplicationDefault(),
                {"projectId": project_id},
                name=app_name,
            )
            check_no_firebase_auth_emulator(app)
            return app

    def check_identity_platform_readiness(self) -> None:
        """Exercise the real ADC dependency the verifier relies on.

        Readiness must fail closed if Application Default Credentials are
        not actually resolvable *and refreshable* on this host, since that
        is precisely the condition under which the mandatory
        ``check_revoked=True`` verification (and any live token
        verification at all) would fail at request time. Obtaining the
        cached credential object alone (without ``refresh()``) does not
        prove it is currently valid -- a credential can be cached from an
        earlier, now-expired process lifetime -- so this performs one
        bounded, real refresh call against Google's token endpoint. Any
        exception is reported by type only, never by ``str(exc)``, since
        google-auth transport errors can embed the outbound request URL
        (including query parameters).
        """
        check_no_firebase_auth_emulator(self._firebase_app)
        try:
            credential = self._firebase_app.credential.get_credential()
            credential.refresh(google_auth_transport_requests.Request())
        except Exception as exc:
            raise AuthenticationError(
                "Application Default Credentials are not available or could "
                f"not be refreshed ({type(exc).__name__})"
            ) from exc

    def verify_token(self, token_str: str, *, now: datetime | None = None) -> VerifiedOperator:
        """Cryptographically verify an Identity Platform ID token and its MFA claim."""
        check_no_firebase_auth_emulator(self._firebase_app)
        if not token_str or not isinstance(token_str, str):
            raise AuthenticationError("Authorization token is missing or empty")

        token_str = token_str.strip()
        if token_str.lower().startswith("bearer "):
            token_str = token_str[7:].strip()

        if not token_str:
            raise AuthenticationError("Bearer token is empty")

        try:
            claims: Mapping[str, Any] = firebase_auth.verify_id_token(
                token_str,
                app=self._firebase_app,
                check_revoked=True,
                clock_skew_seconds=self.clock_skew_seconds,
            )
        except firebase_auth.RevokedIdTokenError as exc:
            raise AuthenticationError(f"Operator ID token has been revoked: {exc}") from exc
        except firebase_auth.UserDisabledError as exc:
            raise AuthenticationError(f"Operator account is disabled: {exc}") from exc
        except firebase_auth.ExpiredIdTokenError as exc:
            raise AuthenticationError(f"Token has expired: {exc}") from exc
        except firebase_auth.CertificateFetchError as exc:
            raise AuthenticationError(
                f"Failed to fetch Identity Platform public keys: {exc}"
            ) from exc
        except firebase_auth.InvalidIdTokenError as exc:
            raise AuthenticationError(f"Token verification failed: {exc}") from exc
        except AuthenticationError:
            raise
        except Exception as exc:
            # Fail closed: any unexpected SDK error (network, ADC, malformed
            # response) denies the operator rather than proceeding unverified.
            raise AuthenticationError(f"Token verification failed: {exc}") from exc

        current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        current_epoch = int(current_time.timestamp())

        # Disallow Service Account / ADC tokens. The SDK already confirms this
        # is a genuine Identity Platform user ID token (issuer/audience), but
        # a defense-in-depth check against the subject shape is kept here.
        sub = str(claims.get("sub") or claims.get("uid") or "").strip()
        if not sub or sub.endswith(".gserviceaccount.com"):
            raise AuthenticationError("Service account and ADC tokens are not permitted")

        # Disallow anonymous and custom provider tokens.
        firebase_claims = claims.get("firebase")
        if not isinstance(firebase_claims, Mapping):
            raise AuthenticationError("Token is missing required 'firebase' claims object")

        sign_in_provider = str(firebase_claims.get("sign_in_provider") or "").strip()
        if sign_in_provider in ("anonymous", "custom"):
            raise AuthenticationError(
                f"Authentication provider {sign_in_provider!r} is not permitted; "
                "custom and anonymous providers are rejected"
            )

        # Enforce project-only or expected tenant.
        tenant = claims.get("tenant") or firebase_claims.get("tenant")
        if self.expected_tenant_id is None:
            if tenant:
                raise AuthenticationError(
                    f"Tenant tokens are not permitted for project-level operator authentication; found tenant {tenant!r}"
                )
        else:
            if tenant != self.expected_tenant_id:
                raise AuthenticationError(
                    f"Tenant mismatch: expected {self.expected_tenant_id!r}, got {tenant!r}"
                )

        # Explicit operator UID allowlist verification.
        if not self.allowed_operator_uids:
            raise AuthenticationError("No operator UIDs are allowed; allowlist is empty")
        if sub not in self.allowed_operator_uids:
            raise AuthenticationError(f"Operator UID {sub!r} is not in the allowed operators list")

        # Email and verified status check.
        email = str(claims.get("email") or "").strip()
        if not email:
            raise AuthenticationError("Token does not contain an email address")
        if claims.get("email_verified") is not True:
            raise AuthenticationError("Operator email address is not verified")

        # auth_time freshness checks. This is a Pantheon-specific policy claim
        # that the SDK's own verification does not enforce, so it is validated
        # here defensively (type and range) rather than trusted blindly.
        auth_time_epoch = claims.get("auth_time")
        if type(auth_time_epoch) is not int and type(auth_time_epoch) is not float:
            raise AuthenticationError("Token 'auth_time' claim must be a numeric timestamp")
        auth_time_int = int(auth_time_epoch)

        if auth_time_int > current_epoch + self.clock_skew_seconds:
            raise AuthenticationError("Token 'auth_time' is in the future")

        auth_age = current_epoch - auth_time_int
        if auth_age > self.max_auth_age_seconds:
            raise AuthenticationError(
                f"Authentication is too stale ({auth_age}s > {self.max_auth_age_seconds}s); "
                "fresh MFA re-authentication is required"
            )

        # Multi-factor authentication (MFA) second-factor verification.
        # Requires actual signed 'sign_in_second_factor'; second_factor_identifier alone is insufficient.
        second_factor = firebase_claims.get("sign_in_second_factor")
        if not second_factor or not str(second_factor).strip():
            raise AuthenticationError(
                "ID token does not contain a verified second-factor MFA assertion ('sign_in_second_factor' is required; single-factor password or identifier-only is insufficient)"
            )

        second_factor_str = str(second_factor).strip().lower()
        if second_factor_str not in self.allowed_second_factors:
            raise AuthenticationError(
                f"Unsupported MFA second factor {second_factor_str!r}; allowed: {sorted(self.allowed_second_factors)}"
            )

        auth_time_dt = datetime.fromtimestamp(auth_time_int, tz=timezone.utc)
        return VerifiedOperator(
            uid=sub,
            email=email,
            auth_time=auth_time_dt,
            second_factor=second_factor_str,
            claims=dict(claims),
        )
