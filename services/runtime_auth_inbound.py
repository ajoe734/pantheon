"""Inbound bearer/JWT/RBAC/MFA validation for protected runtime command routes.

Production hardening for SVC-RUNTIME-HARDENING. The runtime-manager and the
legacy ``services/control_plane/internal_api`` operator command surface share a
single auth contract:

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
"""
from __future__ import annotations

import base64
import functools
import hmac
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple


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


def _claims_to_context(claims: Mapping[str, Any], default_role: str) -> AuthContext:
    actor_id = str(
        claims.get("sub")
        or claims.get("actor_id")
        or claims.get("preferred_username")
        or "internal-api-operator"
    ).strip() or "internal-api-operator"
    raw_roles = claims.get("roles")
    if isinstance(raw_roles, str):
        roles_iter: Iterable[str] = [raw_roles]
    elif isinstance(raw_roles, Sequence):
        roles_iter = [str(role) for role in raw_roles if str(role).strip()]
    else:
        single = claims.get("role")
        roles_iter = [str(single)] if single else [default_role]
    roles = frozenset(role.strip() for role in roles_iter if role and str(role).strip())
    if not roles:
        roles = frozenset({default_role})
    return AuthContext(
        actor_id=actor_id,
        roles=roles,
        claims=dict(claims),
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

    if _looks_like_jwt(token):
        if not secret and mode == "strict":
            raise AuthError(
                "AUTH_JWT_SECRET_MISSING",
                "Strict auth mode requires PANTHEON_RUNTIME_JWT_SECRET",
                500,
            )
        if secret:
            claims = _verify_jwt_hs256(
                token,
                secret=secret,
                issuer=issuer,
                audience=audience,
            )
            ctx = _claims_to_context(claims, default_role=default_role)
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
    mfa_verified = False
    if mfa_token:
        if not _MFA_PATTERN.fullmatch(mfa_token):
            raise AuthError(
                "MFA_VALIDATION_FAILED",
                "MFA token invalid",
                400,
            )
        mfa_verified = True
    elif enforce_mfa:
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
