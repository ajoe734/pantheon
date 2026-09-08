"""Cryptographic Identity Platform token verification.

OPS-EXECUTION-MFA-ISSUER-001.
Verifies fresh genuine Identity Platform user MFA tokens against configured
project, allowed operator UIDs, second factor claims, and freshness limits.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import jwt
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from .models import AuthenticationError, VerifiedOperator

logger = logging.getLogger("execution_grant_issuer.token_verifier")

GOOGLE_SECURETOKEN_CERTS_URL = (
    "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
)
SUPPORTED_SECOND_FACTORS = frozenset({"phone", "totp", "sms", "email", "security_key"})
DEFAULT_MAX_AUTH_AGE_SECONDS = 3600
CLOCK_SKEW_TOLERANCE_SECONDS = 10


class IdentityPlatformTokenVerifier:
    """Verifies Identity Platform user ID tokens with genuine MFA claims."""

    def __init__(
        self,
        *,
        project_id: str = "pantheon-dev-20260902",
        allowed_operator_uids: Sequence[str] | None = None,
        max_auth_age_seconds: int = DEFAULT_MAX_AUTH_AGE_SECONDS,
        allowed_second_factors: Sequence[str] | None = None,
        trusted_public_keys: Mapping[str, RSAPublicKey | str] | None = None,
        certs_url: str = GOOGLE_SECURETOKEN_CERTS_URL,
        check_revocation: bool = False,
    ) -> None:
        if not project_id or not project_id.strip():
            raise ValueError("project_id must be non-empty")
        self.project_id = project_id.strip()
        self.allowed_operator_uids = (
            frozenset(uid.strip() for uid in allowed_operator_uids if uid.strip())
            if allowed_operator_uids is not None
            else frozenset()
        )
        self.max_auth_age_seconds = max_auth_age_seconds
        self.allowed_second_factors = (
            frozenset(f.strip().lower() for f in allowed_second_factors if f.strip())
            if allowed_second_factors is not None
            else SUPPORTED_SECOND_FACTORS
        )
        self._trusted_public_keys: dict[str, RSAPublicKey] = {}
        if trusted_public_keys:
            for kid, key in trusted_public_keys.items():
                if isinstance(key, RSAPublicKey):
                    self._trusted_public_keys[kid] = key
                elif isinstance(key, str):
                    self._trusted_public_keys[kid] = self._load_public_key_from_pem(key)
        self.certs_url = certs_url
        self.check_revocation = check_revocation
        self._certs_cache: dict[str, RSAPublicKey] = {}
        self._certs_cache_expires_at: float = 0.0

    @staticmethod
    def _load_public_key_from_pem(pem_data: str) -> RSAPublicKey:
        data = pem_data.strip().encode("utf-8")
        if b"BEGIN CERTIFICATE" in data:
            cert = x509.load_pem_x509_certificate(data)
            pub = cert.public_key()
            if not isinstance(pub, RSAPublicKey):
                raise ValueError("Certificate does not contain an RSA public key")
            return pub
        raise ValueError("Unsupported key/certificate PEM format")

    def _get_google_public_keys(self) -> dict[str, RSAPublicKey]:
        now = time.time()
        if self._certs_cache and now < self._certs_cache_expires_at:
            return self._certs_cache

        try:
            req = urllib.request.Request(
                self.certs_url,
                headers={"User-Agent": "pantheon-execution-grant-issuer/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                content = response.read().decode("utf-8")
                certs_data: dict[str, str] = json.loads(content)

                new_cache: dict[str, RSAPublicKey] = {}
                for kid, cert_pem in certs_data.items():
                    new_cache[kid] = self._load_public_key_from_pem(cert_pem)

                cache_control = response.headers.get("Cache-Control", "")
                max_age = 3600
                for part in cache_control.split(","):
                    part = part.strip()
                    if part.startswith("max-age="):
                        try:
                            max_age = int(part.split("=")[1])
                        except ValueError:
                            pass
                self._certs_cache = new_cache
                self._certs_cache_expires_at = now + max_age
                return self._certs_cache
        except Exception as exc:
            if self._certs_cache:
                logger.warning("Failed to refresh Google certs, using cached: %s", exc)
                return self._certs_cache
            raise AuthenticationError(
                f"Failed to fetch Identity Platform public keys: {exc}"
            ) from exc

    def _resolve_public_key(self, kid: str) -> RSAPublicKey:
        if kid in self._trusted_public_keys:
            return self._trusted_public_keys[kid]
        google_keys = self._get_google_public_keys()
        if kid in google_keys:
            return google_keys[kid]
        raise AuthenticationError(f"Token key ID {kid!r} not found in trusted certificates")

    def verify_token(self, token_str: str, *, now: datetime | None = None) -> VerifiedOperator:
        """Cryptographically verify an Identity Platform ID token and its MFA claim."""
        if not token_str or not isinstance(token_str, str):
            raise AuthenticationError("Authorization token is missing or empty")

        token_str = token_str.strip()
        if token_str.lower().startswith("bearer "):
            token_str = token_str[7:].strip()

        if not token_str:
            raise AuthenticationError("Bearer token is empty")

        # Inspect unverified header for key ID and algorithm
        try:
            unverified_header = jwt.get_unverified_header(token_str)
        except Exception as exc:
            raise AuthenticationError(f"Malformed token header: {exc}") from exc

        alg = unverified_header.get("alg")
        if alg != "RS256":
            raise AuthenticationError(f"Invalid token algorithm: expected RS256, got {alg!r}")

        kid = unverified_header.get("kid")
        if not kid or not isinstance(kid, str):
            raise AuthenticationError("Token header is missing 'kid' (key ID)")

        public_key = self._resolve_public_key(kid)

        expected_issuer = f"https://securetoken.google.com/{self.project_id}"
        current_time = now.astimezone(timezone.utc) if now else datetime.now(timezone.utc)
        current_epoch = int(current_time.timestamp())

        try:
            claims = jwt.decode(
                token_str,
                key=public_key,
                algorithms=["RS256"],
                audience=self.project_id,
                issuer=expected_issuer,
                options={
                    "require": ["exp", "iat", "aud", "iss", "sub", "auth_time"],
                    "verify_signature": True,
                    "verify_exp": False,  # Checked below against current_time
                    "verify_iat": False,  # Checked below against current_time
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )
        except jwt.InvalidIssuerError as exc:
            raise AuthenticationError(f"Invalid token issuer: {exc}") from exc
        except jwt.InvalidAudienceError as exc:
            raise AuthenticationError(f"Invalid token audience (wrong project): {exc}") from exc
        except jwt.InvalidSignatureError as exc:
            raise AuthenticationError(f"Token signature verification failed: {exc}") from exc
        except Exception as exc:
            raise AuthenticationError(f"Token verification failed: {exc}") from exc

        # Timestamp validations against current_time (supporting caller-provided time)
        exp_val = claims.get("exp")
        if not isinstance(exp_val, (int, float)):
            raise AuthenticationError("Token 'exp' claim must be a numeric timestamp")
        if int(exp_val) <= current_epoch - CLOCK_SKEW_TOLERANCE_SECONDS:
            raise AuthenticationError("Token has expired")

        iat_val = claims.get("iat")
        if not isinstance(iat_val, (int, float)):
            raise AuthenticationError("Token 'iat' claim must be a numeric timestamp")
        if int(iat_val) > current_epoch + CLOCK_SKEW_TOLERANCE_SECONDS:
            raise AuthenticationError("Token 'iat' is in the future")

        # Disallow Service Account / ADC tokens
        # Standard user ID token has iss = https://securetoken.google.com/<project_id>
        # (already checked by jwt.decode), but ensure sub is not service account email
        sub = str(claims.get("sub") or "").strip()
        if not sub or sub.endswith(".gserviceaccount.com"):
            raise AuthenticationError("Service account and ADC tokens are not permitted")

        # Disallow anonymous tokens
        firebase_claims = claims.get("firebase")
        if not isinstance(firebase_claims, Mapping):
            raise AuthenticationError("Token is missing required 'firebase' claims object")

        sign_in_provider = str(firebase_claims.get("sign_in_provider") or "").strip()
        if sign_in_provider == "anonymous" or claims.get("provider_id") == "anonymous":
            raise AuthenticationError("Anonymous authentication is not permitted")

        # Explicit operator UID allowlist verification
        if self.allowed_operator_uids and sub not in self.allowed_operator_uids:
            raise AuthenticationError(f"Operator UID {sub!r} is not in the allowed operators list")

        # Email and verified status check
        email = str(claims.get("email") or "").strip()
        if not email:
            raise AuthenticationError("Token does not contain an email address")
        if claims.get("email_verified") is not True:
            raise AuthenticationError("Operator email address is not verified")

        # auth_time freshness checks
        auth_time_epoch = claims.get("auth_time")
        if not isinstance(auth_time_epoch, (int, float)):
            raise AuthenticationError("Token 'auth_time' claim must be a numeric timestamp")
        auth_time_int = int(auth_time_epoch)

        if auth_time_int > current_epoch + CLOCK_SKEW_TOLERANCE_SECONDS:
            raise AuthenticationError("Token 'auth_time' is in the future")

        auth_age = current_epoch - auth_time_int
        if auth_age > self.max_auth_age_seconds:
            raise AuthenticationError(
                f"Authentication is too stale ({auth_age}s > {self.max_auth_age_seconds}s); "
                "fresh MFA re-authentication is required"
            )

        # Multi-factor authentication (MFA) second-factor verification
        second_factor = (
            firebase_claims.get("sign_in_second_factor")
            or firebase_claims.get("second_factor_identifier")
        )
        if not second_factor or not str(second_factor).strip():
            raise AuthenticationError(
                "ID token does not contain a verified second-factor MFA assertion (single-factor password is insufficient)"
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
            claims=claims,
        )
