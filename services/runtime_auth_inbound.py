"""Inbound bearer/JWT/RBAC/MFA validation for protected runtime command routes.

Production hardening for SVC-RUNTIME-HARDENING. The runtime-manager and the
legacy ``services.control_plane.internal.internal_api`` operator command
surface share a single auth contract:

* Authorization header must be ``Bearer <token>``.
* The token may be either an HS256 JWT (signed with
  ``PANTHEON_RUNTIME_JWT_SECRET``) or, in permissive mode, a structured legacy
  token shaped ``actor_id[:role1,role2]``. The structured form is sufficient for
  internal-zone callers that already authenticate at the network boundary; the
  JWT form is required when ``PANTHEON_RUNTIME_AUTH_MODE=strict``.
* RBAC: each protected route names the roles permitted to call it. Callers must
  carry one of those roles in their JWT/structured token claims.
* MFA: critical write routes (kill-switch, deployment approve, rollback,
  safe-mode advance) require ``X-MFA-Token`` (six-digit OTP) when
  ``PANTHEON_RUNTIME_MFA_REQUIRED=true``. The header is always validated when
  present so production gates and dev/test paths share a single contract.

The module exposes a ``validate_request_auth`` helper plus a Flask
``require_authn(roles=..., mfa_required=...)`` decorator. Both return errors as
plain ``(payload, status_code)`` tuples so the existing Flask routes can return
the result unchanged.

Environment
-----------
PANTHEON_RUNTIME_AUTH_MODE
    ``strict`` (require JWT validated against the configured secret)
    or ``permissive`` (default; accept structured legacy tokens too).
PANTHEON_RUNTIME_JWT_SECRET
    HS256 secret. Required when mode is ``strict`` *and* the inbound token has
    JWT shape. When unset, JWT-shaped tokens cannot be verified and only
    structured tokens are accepted.
PANTHEON_RUNTIME_JWT_ISSUER
    Optional ``iss`` claim that must match when verifying a JWT.
PANTHEON_RUNTIME_JWT_AUDIENCE
    Optional ``aud`` claim that must match when verifying a JWT.
PANTHEON_RUNTIME_MFA_REQUIRED
    ``true`` to require ``X-MFA-Token`` on routes marked ``mfa_required=True``.
    Default ``false`` keeps the legacy behaviour of validating only when
    present, which existing integration tests depend on.
PANTHEON_RUNTIME_DEFAULT_ROLE
    Role assigned to plain (non-structured, non-JWT) bearer tokens in
    permissive mode. Defaults to ``operator``.
PANTHEON_RUNTIME_JWKS_URI
    Optional JWKS endpoint. When set, JWT-shaped bearer tokens are verified
    through the JWKS path instead of the HS256 shared-secret path.
PANTHEON_RUNTIME_OIDC_DISCOVERY_URL
    Optional OIDC discovery metadata URL. When set and JWKS_URI is unset,
    ``jwks_uri`` is resolved from this metadata document.
PANTHEON_RUNTIME_OIDC_ISSUER
    Optional issuer override for JWKS/OIDC tokens.
PANTHEON_RUNTIME_OIDC_AUDIENCE
    Optional audience override for JWKS/OIDC tokens.
PANTHEON_RUNTIME_ROLE_CLAIMS
    Comma-separated claim paths used to resolve roles. Defaults to
    ``roles,role``. Dot paths such as ``realm_access.roles`` are supported.
PANTHEON_RUNTIME_ROLE_MAP
    Optional semicolon-separated external-to-internal role map, for example
    ``idp-admins=admin;idp-operators=operator``.
PANTHEON_RUNTIME_ROLE_MAP_MODE
    ``passthrough`` (default; preserve unmapped role values) or ``strict``
    (only mapped values become Pantheon roles).
PANTHEON_RUNTIME_MFA_CLAIMS
    Comma-separated claim paths used to resolve IdP-confirmed MFA. Defaults to
    ``amr,acr,mfa,mfa_verified``.
PANTHEON_RUNTIME_MFA_VALUES
    Comma-separated values treated as MFA proof. Defaults to common true/MFA
    markers such as ``true,mfa,otp,totp,webauthn``.
"""
from __future__ import annotations

import base64
import binascii
import functools
import hmac
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple


_DEFAULT_ROLE = "operator"
_MFA_PATTERN = re.compile(r"\d{6}")


@dataclass(frozen=True)
class AuthContext:
    """Resolved caller identity for a protected request."""

    actor_id: str
    roles: frozenset[str]
    claims: Mapping[str, Any] = field(default_factory=dict)
    mfa_token: Optional[str] = None
    mfa_verified: bool = False
    token_kind: str = "structured"  # "jwt" | "structured"

    def has_role(self, *required: str) -> bool:
        if not required:
            return True
        return any(role in self.roles for role in required)


class AuthError(Exception):
    """Auth failure raised internally; converted to a Flask error tuple."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_response(self) -> Tuple[Mapping[str, Any], int]:
        return ({"error": {"code": self.code, "message": self.message}}, self.status_code)


# --------------------------------------------------------------------------- #
# JWT (HS256) verification — implemented against stdlib so the runtime-manager
# requirements stay minimal. We accept only HS256; production deployments
# choose the secret rotation cadence. Asymmetric algorithms can be added by
# replacing the signature step without touching the call sites.
# --------------------------------------------------------------------------- #


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def _looks_like_jwt(token: str) -> bool:
    if ":" in token:
        return False
    return token.count(".") == 2 and all(part for part in token.split("."))


def _verify_jwt_hs256(
    token: str,
    *,
    secret: str,
    issuer: Optional[str],
    audience: Optional[str],
    now: Optional[float] = None,
) -> Mapping[str, Any]:
    if not secret:
        raise AuthError(
            "AUTH_JWT_SECRET_MISSING",
            "JWT verification requires PANTHEON_RUNTIME_JWT_SECRET to be configured",
            500,
        )
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
    except ValueError as exc:
        raise AuthError("AUTH_JWT_MALFORMED", "Bearer token is not a valid JWT", 401) from exc

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("AUTH_JWT_DECODE_FAILED", "JWT decode failed", 401) from exc

    alg = header.get("alg")
    if alg != "HS256":
        raise AuthError(
            "AUTH_JWT_ALG_UNSUPPORTED",
            f"JWT alg {alg!r} is not supported (HS256 only)",
            401,
        )

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected_sig = hmac.new(
        secret.encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected_sig, signature):
        raise AuthError("AUTH_JWT_BAD_SIGNATURE", "JWT signature is invalid", 401)

    current = now if now is not None else time.time()
    exp = payload.get("exp")
    if exp is not None and current >= float(exp):
        raise AuthError("AUTH_JWT_EXPIRED", "JWT has expired", 401)
    nbf = payload.get("nbf")
    if nbf is not None and current < float(nbf):
        raise AuthError("AUTH_JWT_NOT_YET_VALID", "JWT is not yet valid", 401)

    if issuer and payload.get("iss") != issuer:
        raise AuthError(
            "AUTH_JWT_ISSUER_MISMATCH",
            f"JWT issuer mismatch (expected {issuer!r})",
            401,
        )
    if audience:
        aud_claim = payload.get("aud")
        aud_values = aud_claim if isinstance(aud_claim, list) else [aud_claim]
        if audience not in aud_values:
            raise AuthError(
                "AUTH_JWT_AUDIENCE_MISMATCH",
                f"JWT audience mismatch (expected {audience!r})",
                401,
            )

    return payload


def encode_jwt_hs256(
    payload: Mapping[str, Any],
    *,
    secret: str,
    header: Optional[Mapping[str, Any]] = None,
) -> str:
    """Test helper: encode a HS256 JWT (no third-party dependency)."""
    final_header = {"alg": "HS256", "typ": "JWT"}
    if header:
        final_header.update(header)

    def _b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    header_b64 = _b64(json.dumps(final_header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64(json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


# --------------------------------------------------------------------------- #
# JWKS / OIDC verification
# --------------------------------------------------------------------------- #

_JWKS_CACHE: dict[str, tuple[list, float]] = {}
_JWKS_CACHE_LOCK = threading.Lock()
_JWKS_CACHE_TTL = 300.0  # 5 minutes
_JWKS_ALLOWED_ALGS = frozenset({"RS256", "ES256"})
_OIDC_DISCOVERY_CACHE: dict[str, tuple[dict[str, Any], float]] = {}
_OIDC_DISCOVERY_CACHE_LOCK = threading.Lock()
_DEFAULT_ROLE_CLAIMS = ("roles", "role")
_DEFAULT_MFA_CLAIMS = ("amr", "acr", "mfa", "mfa_verified")
_DEFAULT_MFA_VALUES = frozenset({"true", "1", "yes", "mfa", "otp", "totp", "webauthn"})


def _fetch_jwks_keys(
    uri: str,
    *,
    now: Optional[float] = None,
    force_refresh: bool = False,
) -> list:
    """Fetch JWKS keys from *uri* with an in-memory TTL cache.

    Returns the list of JWK dicts from the ``keys`` array.
    Raises ``AuthError`` on any fetch or parse failure so callers never see
    raw network exceptions or internal URIs in error output.
    """
    current = now if now is not None else time.time()
    if not force_refresh:
        with _JWKS_CACHE_LOCK:
            entry = _JWKS_CACHE.get(uri)
            if entry is not None:
                cached_keys, fetched_at = entry
                if current - fetched_at < _JWKS_CACHE_TTL:
                    return cached_keys

    try:
        req = urllib.request.Request(uri, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
        data = json.loads(raw)
        keys = data.get("keys", [])
        if not isinstance(keys, list):
            raise ValueError("JWKS 'keys' is not a list")
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise AuthError("JWKS_FETCH_FAILED", "JWKS endpoint unavailable", 401) from exc

    with _JWKS_CACHE_LOCK:
        _JWKS_CACHE[uri] = (keys, current)
    return keys


def _fetch_oidc_metadata(
    uri: str,
    *,
    now: Optional[float] = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Fetch OIDC discovery metadata and cache it for the JWKS cache TTL."""
    current = now if now is not None else time.time()
    if not force_refresh:
        with _OIDC_DISCOVERY_CACHE_LOCK:
            entry = _OIDC_DISCOVERY_CACHE.get(uri)
            if entry is not None:
                cached_metadata, fetched_at = entry
                if current - fetched_at < _JWKS_CACHE_TTL:
                    return cached_metadata

    try:
        req = urllib.request.Request(uri, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode("utf-8")
        metadata = json.loads(raw)
        if not isinstance(metadata, dict):
            raise ValueError("OIDC metadata must be an object")
        jwks_uri = metadata.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri.strip():
            raise ValueError("OIDC metadata missing jwks_uri")
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
        raise AuthError("OIDC_DISCOVERY_FAILED", "OIDC discovery unavailable", 401) from exc

    with _OIDC_DISCOVERY_CACHE_LOCK:
        _OIDC_DISCOVERY_CACHE[uri] = (metadata, current)
    return metadata


def _find_jwks_key(keys: list, kid: Optional[str]) -> Optional[dict]:
    """Return the JWK matching *kid*, or the first key when *kid* is absent."""
    if kid:
        for k in keys:
            if isinstance(k, dict) and k.get("kid") == kid:
                return k
        return None
    return keys[0] if keys else None


def _b64url_uint(segment: str) -> int:
    return int.from_bytes(_b64url_decode(segment), "big")


def _public_key_from_jwk(jwk: Mapping[str, Any], alg: str) -> Any:
    try:
        from cryptography.hazmat.primitives.asymmetric import ec, rsa
    except ImportError as exc:
        raise AuthError(
            "JWKS_LIBRARY_UNAVAILABLE",
            "JWKS validation requires cryptography to be installed",
            500,
        ) from exc

    try:
        jwk_alg = str(jwk.get("alg") or "")
        if jwk_alg and jwk_alg != alg:
            raise ValueError("JWK alg mismatch")
        jwk_use = str(jwk.get("use") or "")
        if jwk_use and jwk_use != "sig":
            raise ValueError("JWK use is not sig")
        kty = str(jwk.get("kty") or "")
        if alg == "RS256":
            if kty != "RSA":
                raise ValueError("RSA key required")
            return rsa.RSAPublicNumbers(
                e=_b64url_uint(str(jwk["e"])),
                n=_b64url_uint(str(jwk["n"])),
            ).public_key()
        if alg == "ES256":
            if kty != "EC" or jwk.get("crv") != "P-256":
                raise ValueError("P-256 EC key required")
            return ec.EllipticCurvePublicNumbers(
                x=_b64url_uint(str(jwk["x"])),
                y=_b64url_uint(str(jwk["y"])),
                curve=ec.SECP256R1(),
            ).public_key()
    except (binascii.Error, KeyError, TypeError, ValueError) as exc:
        raise AuthError("JWKS_INVALID_KEY", "JWKS key is not usable", 401) from exc
    raise AuthError("AUTH_JWT_ALG_UNSUPPORTED", "JWT alg is not supported", 401)


def _verify_jwks_signature(
    *,
    alg: str,
    public_key: Any,
    signing_input: bytes,
    signature: bytes,
) -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding, utils
    except ImportError as exc:
        raise AuthError(
            "JWKS_LIBRARY_UNAVAILABLE",
            "JWKS validation requires cryptography to be installed",
            500,
        ) from exc

    try:
        if alg == "RS256":
            public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
            return
        if alg == "ES256":
            if len(signature) != 64:
                raise InvalidSignature("ES256 signature must be raw r||s")
            r = int.from_bytes(signature[:32], "big")
            s = int.from_bytes(signature[32:], "big")
            public_key.verify(
                utils.encode_dss_signature(r, s),
                signing_input,
                ec.ECDSA(hashes.SHA256()),
            )
            return
    except InvalidSignature as exc:
        raise AuthError("AUTH_JWT_INVALID", "JWT validation failed", 401) from exc
    raise AuthError("AUTH_JWT_ALG_UNSUPPORTED", "JWT alg is not supported", 401)


def _validate_jwt_time_claims(payload: Mapping[str, Any], *, now: Optional[float]) -> None:
    current = now if now is not None else time.time()
    exp = payload.get("exp")
    try:
        if exp is not None and current >= float(exp):
            raise AuthError("AUTH_JWT_EXPIRED", "JWT has expired", 401)
        nbf = payload.get("nbf")
        if nbf is not None and current < float(nbf):
            raise AuthError("AUTH_JWT_NOT_YET_VALID", "JWT is not yet valid", 401)
    except (TypeError, ValueError) as exc:
        raise AuthError("AUTH_JWT_CLAIMS_INVALID", "JWT claims validation failed", 401) from exc


def _validate_jwt_issuer_audience(
    payload: Mapping[str, Any],
    *,
    issuer: Optional[str],
    audience: Optional[str],
) -> None:
    if issuer and payload.get("iss") != issuer:
        raise AuthError("AUTH_JWT_ISSUER_MISMATCH", "JWT issuer mismatch", 401)
    if audience:
        aud_claim = payload.get("aud")
        aud_values = aud_claim if isinstance(aud_claim, list) else [aud_claim]
        if audience not in aud_values:
            raise AuthError("AUTH_JWT_AUDIENCE_MISMATCH", "JWT audience mismatch", 401)


def _verify_jwt_jwks(
    token: str,
    *,
    jwks_uri: str,
    issuer: Optional[str],
    audience: Optional[str],
    now: Optional[float] = None,
) -> Mapping[str, Any]:
    """Verify *token* against the JWKS at *jwks_uri*.

    Validates issuer, audience, expiry, nbf, and signature.
    Returns the verified payload dict.
    Raises ``AuthError`` on any failure; error messages never include the URI
    or raw exception text to avoid leaking configuration.
    """
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(signature_b64)
    except (binascii.Error, ValueError, json.JSONDecodeError) as exc:
        raise AuthError("AUTH_JWT_MALFORMED", "Bearer token is not a valid JWT", 401) from exc

    alg = str(header.get("alg") or "")
    if alg not in _JWKS_ALLOWED_ALGS:
        raise AuthError("AUTH_JWT_ALG_UNSUPPORTED", "JWT alg is not supported", 401)
    kid = header.get("kid")

    keys = _fetch_jwks_keys(jwks_uri, now=now)
    key = _find_jwks_key(keys, kid)
    if key is None:
        keys = _fetch_jwks_keys(jwks_uri, now=now, force_refresh=True)
        key = _find_jwks_key(keys, kid)
    if key is None:
        raise AuthError("JWKS_NO_MATCHING_KEY", "No matching JWKS key for this token", 401)

    public_key = _public_key_from_jwk(key, alg)
    _verify_jwks_signature(
        alg=alg,
        public_key=public_key,
        signing_input=f"{header_b64}.{payload_b64}".encode("ascii"),
        signature=signature,
    )
    _validate_jwt_time_claims(payload, now=now)
    _validate_jwt_issuer_audience(payload, issuer=issuer, audience=audience)

    return payload


# --------------------------------------------------------------------------- #
# Token resolution
# --------------------------------------------------------------------------- #


def _parse_structured_token(token: str, default_role: str) -> AuthContext:
    """Parse the legacy ``actor_id[:role1,role2]`` shape, accepting plain tokens too."""
    parts = token.split(":", 1)
    actor_id = parts[0].strip() or "internal-api-operator"
    raw_roles: list[str] = []
    if len(parts) == 2:
        raw_roles = [role.strip() for role in parts[1].split(",") if role.strip()]
    if not raw_roles:
        raw_roles = [default_role]
    return AuthContext(
        actor_id=actor_id,
        roles=frozenset(raw_roles),
        claims={"sub": actor_id, "roles": list(raw_roles)},
        token_kind="structured",
    )


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _claim_path_value(claims: Mapping[str, Any], path: str) -> Any:
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _claim_path_exists(claims: Mapping[str, Any], path: str) -> bool:
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current.get(part)
    return True


def _claim_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, str):
        return [part for part in re.split(r"[\s,]+", value.strip()) if part]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        values: list[str] = []
        for item in value:
            values.extend(_claim_values(item))
        return values
    return [str(value)]


def _parse_role_map(raw: str) -> dict[str, str]:
    role_map: dict[str, str] = {}
    if not raw.strip():
        return role_map
    for entry in raw.split(";"):
        if not entry.strip() or "=" not in entry:
            continue
        external, internal = entry.split("=", 1)
        external = external.strip()
        internal = internal.strip()
        if external and internal:
            role_map[external] = internal
    return role_map


def _claim_indicates_mfa(value: Any, accepted_values: frozenset[str]) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value and "true" in accepted_values
    return any(str(item).strip().lower() in accepted_values for item in _claim_values(value))


def _claims_to_context(
    claims: Mapping[str, Any],
    default_role: str,
    *,
    role_claims: Sequence[str] = _DEFAULT_ROLE_CLAIMS,
    role_map: Optional[Mapping[str, str]] = None,
    role_map_mode: str = "passthrough",
    mfa_claims: Sequence[str] = _DEFAULT_MFA_CLAIMS,
    mfa_values: frozenset[str] = _DEFAULT_MFA_VALUES,
) -> AuthContext:
    actor_id = str(
        claims.get("sub")
        or claims.get("actor_id")
        or claims.get("preferred_username")
        or "internal-api-operator"
    ).strip() or "internal-api-operator"
    role_values: list[str] = []
    role_claim_seen = False
    for claim_path in role_claims:
        if _claim_path_exists(claims, claim_path):
            role_claim_seen = True
        role_values.extend(_claim_values(_claim_path_value(claims, claim_path)))

    mapping = role_map or {}
    strict_mapping = bool(mapping) and role_map_mode.strip().lower() == "strict"
    mapped_roles: list[str] = []
    for role_value in role_values:
        mapped = mapping.get(role_value)
        if mapped:
            mapped_roles.append(mapped)
        elif not strict_mapping:
            mapped_roles.append(role_value)

    roles = frozenset(role.strip() for role in mapped_roles if role and str(role).strip())
    if not roles:
        roles = (
            frozenset()
            if role_claim_seen or (strict_mapping and role_values)
            else frozenset({default_role})
        )

    mfa_verified = any(
        _claim_indicates_mfa(_claim_path_value(claims, claim_path), mfa_values)
        for claim_path in mfa_claims
    )
    return AuthContext(
        actor_id=actor_id,
        roles=roles,
        claims=dict(claims),
        mfa_verified=mfa_verified,
        token_kind="jwt",
    )


def _env(env: Optional[Mapping[str, str]], key: str, default: str = "") -> str:
    src = env if env is not None else os.environ
    return str(src.get(key, default)).strip()


def validate_request_auth(
    *,
    authorization: Optional[str],
    mfa_header: Optional[str] = None,
    required_roles: Optional[Sequence[str]] = None,
    mfa_required: bool = False,
    env: Optional[Mapping[str, str]] = None,
) -> AuthContext:
    """Validate inbound auth, returning the resolved ``AuthContext``.

    Raises ``AuthError`` on any failure; callers convert via ``as_response``.
    """
    raw = authorization or ""
    if not raw.startswith("Bearer "):
        raise AuthError("401", "Unauthorized: missing Bearer token", 401)
    token = raw.split(None, 1)[1].strip() if " " in raw else ""
    if not token:
        raise AuthError("401", "Unauthorized: empty token", 401)

    mode = _env(env, "PANTHEON_RUNTIME_AUTH_MODE", "permissive").lower()
    secret = _env(env, "PANTHEON_RUNTIME_JWT_SECRET")
    issuer = _env(env, "PANTHEON_RUNTIME_JWT_ISSUER") or None
    audience = _env(env, "PANTHEON_RUNTIME_JWT_AUDIENCE") or None
    default_role = _env(env, "PANTHEON_RUNTIME_DEFAULT_ROLE") or _DEFAULT_ROLE
    role_claims = _split_csv(_env(env, "PANTHEON_RUNTIME_ROLE_CLAIMS", ",".join(_DEFAULT_ROLE_CLAIMS)))
    if not role_claims:
        role_claims = list(_DEFAULT_ROLE_CLAIMS)
    role_map = _parse_role_map(_env(env, "PANTHEON_RUNTIME_ROLE_MAP"))
    role_map_mode = _env(env, "PANTHEON_RUNTIME_ROLE_MAP_MODE", "passthrough")
    mfa_claims = _split_csv(_env(env, "PANTHEON_RUNTIME_MFA_CLAIMS", ",".join(_DEFAULT_MFA_CLAIMS)))
    if not mfa_claims:
        mfa_claims = list(_DEFAULT_MFA_CLAIMS)
    mfa_values = frozenset(
        value.lower() for value in _split_csv(_env(env, "PANTHEON_RUNTIME_MFA_VALUES", ",".join(sorted(_DEFAULT_MFA_VALUES))))
    )
    if not mfa_values:
        mfa_values = _DEFAULT_MFA_VALUES

    if _looks_like_jwt(token):
        jwks_uri = _env(env, "PANTHEON_RUNTIME_JWKS_URI")
        oidc_discovery_url = _env(env, "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL")
        if jwks_uri:
            # OIDC/JWKS path: active when PANTHEON_RUNTIME_JWKS_URI is configured.
            # OIDC-specific issuer/audience override the generic JWT ones when set.
            oidc_issuer = _env(env, "PANTHEON_RUNTIME_OIDC_ISSUER") or issuer
            oidc_audience = _env(env, "PANTHEON_RUNTIME_OIDC_AUDIENCE") or audience
            claims = _verify_jwt_jwks(
                token,
                jwks_uri=jwks_uri,
                issuer=oidc_issuer,
                audience=oidc_audience,
            )
            ctx = _claims_to_context(
                claims,
                default_role=default_role,
                role_claims=role_claims,
                role_map=role_map,
                role_map_mode=role_map_mode,
                mfa_claims=mfa_claims,
                mfa_values=mfa_values,
            )
        elif oidc_discovery_url:
            metadata = _fetch_oidc_metadata(oidc_discovery_url)
            oidc_issuer = _env(env, "PANTHEON_RUNTIME_OIDC_ISSUER") or str(metadata.get("issuer") or "").strip() or issuer
            oidc_audience = _env(env, "PANTHEON_RUNTIME_OIDC_AUDIENCE") or audience
            claims = _verify_jwt_jwks(
                token,
                jwks_uri=str(metadata["jwks_uri"]).strip(),
                issuer=oidc_issuer,
                audience=oidc_audience,
            )
            ctx = _claims_to_context(
                claims,
                default_role=default_role,
                role_claims=role_claims,
                role_map=role_map,
                role_map_mode=role_map_mode,
                mfa_claims=mfa_claims,
                mfa_values=mfa_values,
            )
        elif not secret and mode == "strict":
            raise AuthError(
                "AUTH_JWT_SECRET_MISSING",
                "Strict auth mode requires PANTHEON_RUNTIME_JWT_SECRET",
                500,
            )
        elif secret:
            claims = _verify_jwt_hs256(
                token,
                secret=secret,
                issuer=issuer,
                audience=audience,
            )
            ctx = _claims_to_context(
                claims,
                default_role=default_role,
                role_claims=role_claims,
                role_map=role_map,
                role_map_mode=role_map_mode,
                mfa_claims=mfa_claims,
                mfa_values=mfa_values,
            )
        else:
            raise AuthError(
                "AUTH_JWT_UNVERIFIED",
                "JWT bearer token cannot be verified without PANTHEON_RUNTIME_JWT_SECRET",
                401,
            )
    else:
        if mode == "strict":
            raise AuthError(
                "AUTH_TOKEN_FORMAT",
                "Strict auth mode requires a JWT bearer token",
                401,
            )
        ctx = _parse_structured_token(token, default_role=default_role)

    if required_roles:
        if not ctx.has_role(*required_roles):
            raise AuthError(
                "AUTH_FORBIDDEN",
                f"Role {sorted(ctx.roles)} not authorized; need one of {sorted(required_roles)}",
                403,
            )

    enforce_mfa = (
        mfa_required
        and _env(env, "PANTHEON_RUNTIME_MFA_REQUIRED", "false").lower() == "true"
    )

    mfa_token = (mfa_header or "").strip()
    mfa_verified = ctx.mfa_verified
    if mfa_token:
        if not _MFA_PATTERN.fullmatch(mfa_token):
            raise AuthError(
                "MFA_VALIDATION_FAILED",
                "MFA token invalid",
                400,
            )
        mfa_verified = True
    elif enforce_mfa and not mfa_verified:
        raise AuthError(
            "MFA_REQUIRED",
            "MFA token (X-MFA-Token) is required for this operation",
            401,
        )

    return AuthContext(
        actor_id=ctx.actor_id,
        roles=ctx.roles,
        claims=ctx.claims,
        mfa_token=mfa_token or None,
        mfa_verified=mfa_verified,
        token_kind=ctx.token_kind,
    )


# --------------------------------------------------------------------------- #
# Flask integration
# --------------------------------------------------------------------------- #


def require_authn(
    roles: Optional[Sequence[str]] = None,
    mfa_required: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Flask decorator: validate Authorization/MFA headers and stash context.

    The decorated function may inspect ``flask.request._auth_context`` for the
    resolved ``AuthContext``. On any failure the decorator returns a Flask
    response tuple (jsonified error payload + status code) without entering
    the route handler.
    """
    from flask import jsonify, request  # local import keeps module Flask-optional

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            try:
                ctx = validate_request_auth(
                    authorization=request.headers.get("Authorization", ""),
                    mfa_header=request.headers.get("X-MFA-Token", ""),
                    required_roles=roles,
                    mfa_required=mfa_required,
                )
            except AuthError as exc:
                payload, status = exc.as_response()
                return jsonify(payload), status
            request._auth_context = ctx  # type: ignore[attr-defined]
            request._validated_token = ctx.actor_id  # legacy compat
            if ctx.mfa_verified:
                request._mfa_token = ctx.mfa_token  # legacy compat
            return func(*args, **kwargs)

        return wrapper

    return decorator


def get_auth_context() -> Optional[AuthContext]:
    """Return the AuthContext attached to the current Flask request, if any."""
    try:
        from flask import request  # local import
    except ImportError:
        return None
    return getattr(request, "_auth_context", None)
