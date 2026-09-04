"""Composition handlers for the BFF auth/session facade.

The composition-root refactor keeps auth routes in :mod:`auth.router`, while
the concrete policy and stores are injected via typed :class:`AuthDependencies`
at assembly time. No reverse import or dynamic getattr lookup of ``main`` is permitted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from fastapi import HTTPException

from ..models import ErrorCode


@dataclass(frozen=True)
class AuthDependencies:
    """Explicit domain dependencies for auth and session handlers."""

    bff_error: Callable[..., HTTPException]
    dev_login_forbidden_environment: Callable[[], bool]
    dev_login_identity_registry: Callable[[], Dict[str, Any]]
    extract_identity: Callable[..., Any]
    require_read_role: Callable[[Any], None]
    raise_if_session_logged_out: Callable[[Any], None]
    session_lifecycle_store: Any
    bff_me_tenant_payload: Callable[..., Dict[str, Any]]
    capabilities_for_identity: Callable[[Any], List[str]]
    bff_auth_stub_enabled: Callable[[], bool]
    bff_auth_mode: Callable[[], str]
    bff_source_commit: Callable[[], str]
    write_roles: frozenset[str]
    utc_now: Callable[[], str]


def _first(*values: Any) -> Optional[str]:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return None


def _csv(value: Any) -> list[str]:
    return [part for part in re.split(r"[\s,]+", str(value or "").strip()) if part]


def _ttl() -> int:
    try:
        value = int(os.getenv("PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS", "1800"))
    except ValueError:
        value = 1800
    return max(300, min(value, 3600))


def _dev_profile(payload: Mapping[str, Any], deps: AuthDependencies) -> Dict[str, Any]:
    if deps.dev_login_forbidden_environment():
        raise deps.bff_error(
            403,
            ErrorCode.PRECONDITION_FAILED,
            "Dev login is disabled for this BFF",
            "dev_login_disabled",
            precondition_failed="dev_login",
            suggestion="Use the dev BFF with configured client credentials; staging-live must use IdP OIDC/JWKS auth",
        )
    if str(payload.get("grant_type") or "client_credentials").strip() != "client_credentials":
        raise deps.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            "Unsupported grant_type for dev login",
            "grant_type must be client_credentials",
            precondition_failed="grant_type",
        )
    client_id = str(payload.get("client_id") or payload.get("clientId") or "").strip()
    client_secret = str(payload.get("client_secret") or payload.get("clientSecret") or "").strip()
    profile = None
    for candidate in deps.dev_login_identity_registry().values():
        if hmac.compare_digest(client_id, candidate["client_id"]) and hmac.compare_digest(client_secret, candidate["client_secret"]):
            profile = candidate
            break
    if profile is None:
        raise deps.bff_error(
            401,
            ErrorCode.AUTH_REQUIRED,
            "Invalid dev login client credentials",
            "AUTH_DEV_LOGIN_CLIENT_CREDENTIALS",
            suggestion="Use the configured per-identity PANTHEON_BFF_DEV_LOGIN_<IDENTITY>_CLIENT_ID/SECRET",
        )
    requested_roles = payload.get("roles")
    if requested_roles is not None:
        if isinstance(requested_roles, str):
            requested_roles = _csv(requested_roles)
        if not requested_roles or not set(requested_roles).issubset(set(profile["roles"])):
            raise deps.bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Requested roles exceed the dev-login identity's bound roles",
                "AUTH_DEV_LOGIN_ESCALATION_DENIED",
                precondition_failed="roles",
                suggestion=f"Identity '{profile['identity']}' is bound to roles {sorted(profile['roles'])}",
            )
    requested_tenant = str(payload.get("tenant_id") or payload.get("tenantId") or "").strip()
    if requested_tenant and requested_tenant != profile["tenant_id"]:
        raise deps.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Requested tenant is outside the dev-login identity's bound tenant",
            "AUTH_DEV_LOGIN_ESCALATION_DENIED",
            precondition_failed="tenant_id",
            suggestion=f"Identity '{profile['identity']}' is bound to tenant '{profile['tenant_id']}'",
        )
    requested_allowed = payload.get("allowed_tenants") or payload.get("allowedTenants")
    if isinstance(requested_allowed, str):
        requested_allowed = _csv(requested_allowed)
    if requested_allowed is not None and set(requested_allowed) - set(profile["allowed_tenants"]):
        raise deps.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Requested allowed_tenants exceed the dev-login identity's bound tenants",
            "AUTH_DEV_LOGIN_ESCALATION_DENIED",
            precondition_failed="allowed_tenants",
            suggestion=f"Identity '{profile['identity']}' is bound to tenants {profile['allowed_tenants']}",
        )
    return profile


def _issue_token(profile: Mapping[str, Any], deps: AuthDependencies) -> Dict[str, Any]:
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = os.getenv("PANTHEON_BFF_DEV_LOGIN_JWT_SECRET") or os.getenv("PANTHEON_BFF_JWT_SECRET", "")
    if not secret:
        raise deps.bff_error(
            500,
            ErrorCode.PRECONDITION_FAILED,
            "Dev login JWT signing secret is not configured",
            "PANTHEON_BFF_JWT_SECRET is required to issue dev-login JWTs",
            precondition_failed="jwt_secret",
        )
    now = int(time.time())
    ttl = _ttl()
    exp = now + ttl
    claims = {
        "sub": profile["subject"],
        "roles": list(profile["roles"]),
        "iss": _first(os.getenv("PANTHEON_BFF_JWT_ISSUER"), "pantheon-dev"),
        "aud": _first(os.getenv("PANTHEON_BFF_JWT_AUDIENCE"), "bff-operators"),
        "iat": now,
        "nbf": now,
        "exp": exp,
        "jti": f"dev-login-{uuid.uuid4().hex}",
        "client_id": profile["client_id"],
        "identity": profile["identity"],
        "token_use": "pantheon-bff-dev-login",
        "tenant_id": profile["tenant_id"],
        "allowed_tenants": profile["allowed_tenants"],
    }
    if profile.get("mfa_verified"):
        claims["mfa_verified"] = True
    token = encode_jwt_hs256(claims, secret=secret)
    iso = lambda value: datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ttl,
        "issued_at": iso(now),
        "expires_at": iso(exp),
        "scope": " ".join(profile["roles"]),
    }


async def bff_auth_dev_login(*, payload: Dict[str, Any], deps: AuthDependencies) -> Dict[str, Any]:
    profile = _dev_profile(payload or {}, deps)
    token = _issue_token(profile, deps)
    return {
        **token,
        "meta": {
            "route": "POST /bff/auth/dev-login",
            "contract": "FE-INT-GATE-OIDC-DEV-LOGIN",
            "ttl_seconds": token["expires_in"],
            "identity": profile["identity"],
        },
    }


def _session_kind(identity: Any) -> str:
    return "stub" if identity.token_kind == "stub" else ("cookie" if identity.token_kind == "cookie" else "bearer")


def _claims(identity: Any) -> Dict[str, Any]:
    return dict(identity.claims or {}) if isinstance(identity.claims, dict) else {}


def _claim_values(identity: Any, names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    claims = _claims(identity)
    for name in names:
        value = claims.get(name)
        if isinstance(value, list):
            values.extend(str(item).strip() for item in value if str(item).strip())
        elif value:
            values.extend(_csv(value))
    return list(dict.fromkeys(values))


def _locale(raw: Any) -> Optional[str]:
    clean = str(raw or "").strip().replace("_", "-")
    if not clean or not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", clean):
        return None
    return "-".join(part.lower() if i == 0 else (part.upper() if len(part) == 2 else part.title() if len(part) == 4 else part) for i, part in enumerate(clean.split("-")))


def _user(identity: Any, deps: AuthDependencies) -> Dict[str, Any]:
    claims = _claims(identity)
    caps = _claim_values(identity, ("capabilities", "permissions", "scp", "scope"))
    caps.extend(deps.capabilities_for_identity(identity))
    caps = list(dict.fromkeys(caps))
    return {
        "id": identity.operator_id,
        "operator_id": identity.operator_id,
        "display_name": _first(claims.get("name"), claims.get("preferred_username"), claims.get("email"), identity.operator_id),
        "roles": list(identity.roles),
        "capabilities": caps,
        "mfa_verified": bool(identity.mfa_verified),
    }


def _feature_flags(identity: Any) -> Dict[str, Any]:
    flags: Dict[str, Any] = {"executePlansBff": True, "sessionAuthMe": True}
    claims = _claims(identity)
    raw_values = [claims.get("feature_flags"), claims.get("features"), os.getenv("PANTHEON_BFF_FEATURE_FLAGS")]
    for raw in raw_values:
        if isinstance(raw, Mapping):
            items = raw.items()
        else:
            parsed: Dict[str, Any] = {}
            for item in str(raw or "").split(","):
                if "=" in item:
                    key, value = item.split("=", 1)
                    parsed[key.strip()] = value.strip()
                elif item.strip():
                    parsed[item.strip()] = True
            items = parsed.items()
        for key, value in items:
            lowered = str(value).strip().lower()
            flags[str(key)] = True if lowered in {"1", "true", "yes", "on", "enabled"} else False if lowered in {"0", "false", "no", "off", "disabled"} else value
    return flags


def _session(identity: Any, *, checked_at: str) -> Dict[str, Any]:
    claims = _claims(identity)
    exp = claims.get("exp")
    try:
        remaining = max(0, int(float(exp) - time.time())) if exp is not None else None
    except (TypeError, ValueError):
        remaining = None
    sid = _first(claims.get("sid"), claims.get("session_id"), claims.get("jti"), f"bff-session-{identity.operator_id}")
    return {
        "id": sid,
        "authenticated": True,
        "auth_mode": identity.token_kind,
        "session_kind": _session_kind(identity),
        "fresh": exp is None or float(exp) > time.time(),
        "freshness_seconds_remaining": remaining,
        "issued_at": _epoch_iso(claims.get("iat")),
        "expires_at": _epoch_iso(claims.get("exp")),
        "mfa_verified": bool(identity.mfa_verified),
        "checked_at": checked_at,
    }


def _epoch_iso(value: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        clean = str(value or "").strip()
        return clean or None


def _tenant(identity: Any, requested: Optional[str], deps: AuthDependencies) -> Dict[str, Any]:
    return deps.bff_me_tenant_payload(identity, requested_tenant=requested)


def _state(identity: Any, deps: AuthDependencies) -> Dict[str, Any]:
    session_id = _first(
        _claims(identity).get("sid"),
        _claims(identity).get("session_id"),
        _claims(identity).get("jti"),
        f"bff-session-{identity.operator_id}",
    )
    key = f"operator:{identity.operator_id}:session:{session_id}"
    store = deps.session_lifecycle_store
    return store.get_session(key) or store.get_session(f"operator:{identity.operator_id}") or {}


def _idempotency_key(route: str, identity: Any, key: Optional[str]) -> Optional[str]:
    clean = str(key or "").strip()
    if not clean:
        return None
    sid = _first(_claims(identity).get("sid"), _claims(identity).get("session_id"), _claims(identity).get("jti"), f"bff-session-{identity.operator_id}")
    return f"{route}:{identity.operator_id}:{sid}:{clean}"


def _request_hash(payload: Any) -> str:
    encoded = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _assert_identity(authorization: Optional[str], deps: AuthDependencies, *, mfa: Optional[str] = None, cookie: Optional[str] = None) -> Any:
    identity = deps.extract_identity(authorization, mfa_token=mfa, session_cookie=cookie)
    deps.require_read_role(identity)
    deps.raise_if_session_logged_out(identity)
    return identity


async def bff_me(
    *,
    response: Any,
    tenant_id: Optional[str] = None,
    authorization: Optional[str] = None,
    pantheon_session: Optional[str] = None,
    x_mfa_token: Optional[str] = None,
    x_tenant_id: Optional[str] = None,
    x_pantheon_tenant: Optional[str] = None,
    x_correlation_id: Optional[str] = None,
    x_locale: Optional[str] = None,
    accept_language: Optional[str] = None,
    deps: AuthDependencies,
) -> Dict[str, Any]:
    correlation = _first(x_correlation_id, str(uuid.uuid4()))
    if response is not None:
        response.headers["X-Correlation-Id"] = correlation
    try:
        identity = _assert_identity(authorization, deps, mfa=x_mfa_token, cookie=pantheon_session)
        state = _state(identity, deps)
        requested = _first(x_tenant_id, x_pantheon_tenant, tenant_id, state.get("tenant_id"))
        tenant = _tenant(identity, requested, deps)
        tenant["source"] = "request" if _first(x_tenant_id, x_pantheon_tenant, tenant_id) else ("session" if state.get("tenant_id") else "default")
        accepted = _locale(str(accept_language or "").split(",", 1)[0])
        locale = {
            "resolved": _locale(x_locale) or accepted or _locale(state.get("locale")) or _locale(os.getenv("PANTHEON_BFF_DEFAULT_LOCALE")) or "en-US",
            "requested": _locale(x_locale),
            "accept_language": accepted,
            "default": _locale(os.getenv("PANTHEON_BFF_DEFAULT_LOCALE")) or "en-US",
            "timezone": os.getenv("PANTHEON_TIMEZONE", "UTC"),
            "source": "header" if x_locale else ("accept_language" if accept_language else ("session" if state.get("locale") else "default")),
        }
        user = _user(identity, deps)
        session = _session(identity, checked_at=deps.utc_now())
        session["state"] = str(state.get("state") or "active")
    except HTTPException as exc:
        raise exc
    data = {
        "operator_id": user["operator_id"], "operatorId": user["operator_id"],
        "user": user, "current_user": user, "currentUser": user,
        "tenant": tenant, "tenant_id": tenant["id"], "tenantId": tenant["id"],
        "allowed_tenants": tenant["allowed_ids"], "allowedTenants": tenant["allowed_ids"],
        "locale": locale,
        "environment": {
            "name": os.getenv("PANTHEON_ENV", "dev"),
            "deployment_stage": os.getenv("PANTHEON_DEPLOYMENT_STAGE", "dev"),
            "region": os.getenv("PANTHEON_REGION", ""),
            "timezone": os.getenv("PANTHEON_TIMEZONE", "UTC"),
            "auth_mode": "stub" if deps.bff_auth_stub_enabled() else deps.bff_auth_mode(),
            "strict_auth": not deps.bff_auth_stub_enabled() and deps.bff_auth_mode() == "strict",
        },
        "feature_flags": _feature_flags(identity), "featureFlags": _feature_flags(identity),
        "session": session, "session_kind": session["session_kind"], "sessionKind": session["session_kind"],
        "roles": user["roles"], "capabilities": user["capabilities"],
    }
    return {"data": data, "meta": {"route": "GET /bff/me", "contract": "BFF-LUV-GAP-009", "correlationId": correlation, "snapshot_at": deps.utc_now()}}


def _verifier() -> Dict[str, Any]:
    asymmetric = bool(os.getenv("PANTHEON_BFF_JWKS_URI", "").strip() or os.getenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", "").strip())
    return {
        "kind": "oidc_jwks" if asymmetric else "hs256",
        "configured": bool(asymmetric or os.getenv("PANTHEON_BFF_JWT_SECRET", "").strip()),
        "jwksConfigured": bool(os.getenv("PANTHEON_BFF_JWKS_URI", "").strip()),
        "discoveryConfigured": bool(os.getenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", "").strip()),
        "sharedSecretConfigured": bool(os.getenv("PANTHEON_BFF_JWT_SECRET", "").strip()),
        "issuerConfigured": bool(_first(os.getenv("PANTHEON_BFF_OIDC_ISSUER"), os.getenv("PANTHEON_BFF_JWT_ISSUER"))),
        "audienceConfigured": bool(_first(os.getenv("PANTHEON_BFF_OIDC_AUDIENCE"), os.getenv("PANTHEON_BFF_JWT_AUDIENCE"))),
        "roleClaimsConfigured": True,
        "roleClaimPaths": _csv(os.getenv("PANTHEON_BFF_ROLE_CLAIMS", "roles,role")),
        "roleMapConfigured": bool(os.getenv("PANTHEON_BFF_ROLE_MAP", "").strip()),
        "roleMapMode": os.getenv("PANTHEON_BFF_ROLE_MAP_MODE", "passthrough"),
    }


async def bff_auth_readiness(
    *,
    authorization: Optional[str] = None,
    pantheon_session: Optional[str] = None,
    x_mfa_token: Optional[str] = None,
    x_tenant_id: Optional[str] = None,
    deps: AuthDependencies,
) -> Dict[str, Any]:
    identity = _assert_identity(authorization, deps, mfa=x_mfa_token, cookie=pantheon_session)
    kind = _session_kind(identity)
    tenant = _tenant(identity, x_tenant_id, deps)
    user = _user(identity, deps)
    capabilities = set(user["capabilities"])
    try:
        from ..agora.identity.scope import resolve_agora_user_scope
        capabilities.update(resolve_agora_user_scope(identity, utc_now=deps.utc_now, requested_tenant_id=tenant["id"]).granted_capabilities)
    except Exception:
        pass
    # The dev operator identities are server-bound and carry the canonical
    # workshop capability even when the optional Agora scope module is not
    # available during a cold-start probe.
    if set(identity.roles) & set(deps.write_roles):
        capabilities.add("agora.workshop.v1")
    verifier = _verifier()
    strict = deps.bff_auth_mode() == "strict" and not deps.bff_auth_stub_enabled()
    operator_ready = bool(set(deps.write_roles) & set(identity.roles))
    interaction_ready = "agora.workshop.v1" in capabilities
    verifier_ready = bool(verifier["configured"] and verifier["issuerConfigured"] and verifier["audienceConfigured"] and verifier["roleClaimsConfigured"])
    ready = bool(strict and kind in {"bearer", "cookie"} and operator_ready and interaction_ready and verifier_ready)
    return {
        "data": {
            "ready": ready,
            "authReady": ready,
            "providerReady": False,
            "sourceCommitSha": deps.bff_source_commit(),
            "auth": {
                "mode": deps.bff_auth_mode(),
                "stub": deps.bff_auth_stub_enabled(),
                "strict": strict,
                "sessionKind": kind,
                "sessionReady": kind in {"bearer", "cookie"},
                "operatorRoleReady": operator_ready,
                "interactionCapabilityReady": interaction_ready,
                "verifierReady": verifier_ready,
                "verifier": verifier,
            },
            "identity": {
                "operatorId": identity.operator_id,
                "roles": sorted(identity.roles),
                "tenantId": tenant["id"],
                "capabilities": sorted(capabilities),
            },
            "provider": {"provider": "openclaw", "ready": False, "status": "unavailable", "reason": "provider_readiness_observability_only"},
            "authority": {"interaction": "advisory", "execution": "none", "broker": "none", "capital": "none"},
        },
        "meta": {"route": "GET /bff/auth/readiness", "contract": "PINT-016-STRICT-BROWSER-READINESS", "snapshot_at": deps.utc_now()},
    }


async def bff_auth_refresh(
    *,
    payload: Dict[str, Any],
    authorization: Optional[str] = None,
    pantheon_session: Optional[str] = None,
    pantheon_refresh: Optional[str] = None,
    pantheon_refresh_token: Optional[str] = None,
    x_mfa_token: Optional[str] = None,
    x_refresh_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    x_idempotency_key: Optional[str] = None,
    deps: AuthDependencies,
) -> Dict[str, Any]:
    credential = _first((payload or {}).get("refresh_token"), (payload or {}).get("refreshToken"), x_refresh_token, pantheon_refresh, pantheon_refresh_token, pantheon_session, authorization)
    if not credential:
        raise deps.bff_error(401, ErrorCode.AUTH_REQUIRED, "Refresh credential is required", "AUTH_REFRESH_CREDENTIAL_REQUIRED", precondition_failed="refresh_credential")
    bearer = credential if str(credential).lower().startswith("bearer ") else f"Bearer {credential}"
    source = "body" if (payload or {}).get("refresh_token") or (payload or {}).get("refreshToken") else ("header" if x_refresh_token else ("refresh_cookie" if pantheon_refresh or pantheon_refresh_token else ("session_cookie" if pantheon_session else "bearer")))
    if source in {"refresh_cookie", "session_cookie"}:
        identity = _assert_identity(None, deps, mfa=x_mfa_token, cookie=str(credential))
    else:
        identity = _assert_identity(bearer, deps, mfa=x_mfa_token, cookie=pantheon_session)
    now = deps.utc_now()
    state = _state(identity, deps)
    state.update({"state": "active", "last_refreshed_at": now, "last_refresh_credential_source": source})
    deps.session_lifecycle_store.upsert_session(f"operator:{identity.operator_id}:session:{_first(_claims(identity).get('sid'), _claims(identity).get('session_id'), _claims(identity).get('jti'), f'bff-session-{identity.operator_id}')}", state, now=now)
    idem = idempotency_key or x_idempotency_key
    record_key = _idempotency_key("POST /bff/auth/refresh", identity, idem)
    request_hash = _request_hash({"route": "POST /bff/auth/refresh", "payload": payload or {}, "source": source})
    if record_key:
        cached = deps.session_lifecycle_store.get_idempotency(record_key)
        if cached:
            if cached.get("request_hash") != request_hash:
                raise deps.bff_error(409, ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency key was reused with a different refresh payload", "The idempotency key already belongs to another refresh payload", precondition_failed="idempotency_key")
            result = cached["result"]
            result.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
            return result
    result = _lifecycle(identity, "refresh", idem, now, deps=deps)
    descriptor = {"source": source, "session_kind": _session_kind(identity), "sessionKind": _session_kind(identity), "token_kind": identity.token_kind, "tokenKind": identity.token_kind}
    result["data"]["session"]["last_refreshed_at"] = now
    result["data"]["session"]["last_refresh_credential_source"] = source
    result["data"]["operation"].update({"refresh_credential": descriptor, "refreshCredential": descriptor})
    result["data"]["auth"] = {"refresh_credential": descriptor, "refreshCredential": descriptor}
    result["meta"]["auth"] = {"refreshCredentialSource": source, "sessionKind": _session_kind(identity)}
    if record_key:
        deps.session_lifecycle_store.put_idempotency(record_key, request_hash=request_hash, result=result, now=now)
    return result


async def bff_logout(
    *,
    response: Any,
    payload: Dict[str, Any],
    authorization: Optional[str] = None,
    pantheon_session: Optional[str] = None,
    x_mfa_token: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    x_idempotency_key: Optional[str] = None,
    deps: AuthDependencies,
) -> Dict[str, Any]:
    identity = deps.extract_identity(authorization, mfa_token=x_mfa_token, session_cookie=pantheon_session)
    deps.require_read_role(identity)
    idem = idempotency_key or x_idempotency_key
    record_key = _idempotency_key("POST /bff/logout", identity, idem)
    request_hash = _request_hash({"route": "POST /bff/logout", "payload": payload or {}})
    if record_key:
        cached = deps.session_lifecycle_store.get_idempotency(record_key)
        if cached:
            if cached.get("request_hash") != request_hash:
                raise deps.bff_error(409, ErrorCode.IDEMPOTENCY_CONFLICT, "Idempotency key was reused with a different logout payload", "The idempotency key already belongs to another logout payload", precondition_failed="idempotency_key")
            result = cached["result"]
            result.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
            if response is not None:
                response.delete_cookie("pantheon_session", path="/")
            return result
    now = deps.utc_now()
    key = f"operator:{identity.operator_id}:session:{_first(_claims(identity).get('sid'), _claims(identity).get('session_id'), _claims(identity).get('jti'), f'bff-session-{identity.operator_id}')}"
    deps.session_lifecycle_store.upsert_session(key, {"state": "logged_out", "logged_out_at": now}, now=now)
    if response is not None:
        response.delete_cookie("pantheon_session", path="/")
    result = _lifecycle(identity, "logout", idem, now, deps=deps)
    result["data"]["session"].update({"authenticated": False, "fresh": False, "state": "logged_out", "logged_out_at": now})
    if record_key:
        deps.session_lifecycle_store.put_idempotency(record_key, request_hash=request_hash, result=result, now=now)
    return result


async def bff_switch_tenant(
    *,
    payload: Dict[str, Any],
    authorization: Optional[str] = None,
    deps: AuthDependencies,
) -> Dict[str, Any]:
    identity = _assert_identity(authorization, deps)
    tenant = _tenant(identity, str(payload.get("tenantId") or payload.get("tenant_id") or "").strip(), deps)
    tenant["source"] = "session"
    now = deps.utc_now()
    deps.session_lifecycle_store.upsert_session(
        f"operator:{identity.operator_id}:session:{_first(_claims(identity).get('sid'), _claims(identity).get('session_id'), _claims(identity).get('jti'), f'bff-session-{identity.operator_id}')}",
        {"state": "active", "tenant_id": tenant["id"]},
        now=now,
    )
    return _lifecycle(identity, "switch_tenant", None, now, tenant=tenant, deps=deps)


async def bff_update_locale(
    *,
    payload: Dict[str, Any],
    authorization: Optional[str] = None,
    deps: AuthDependencies,
) -> Dict[str, Any]:
    identity = _assert_identity(authorization, deps)
    value = _locale(payload.get("locale"))
    if not value:
        raise deps.bff_error(400, ErrorCode.VALIDATION_FAILED, "locale is required", "locale must be a non-empty BCP-47-ish language tag", precondition_failed="locale")
    now = deps.utc_now()
    deps.session_lifecycle_store.upsert_session(
        f"operator:{identity.operator_id}:session:{_first(_claims(identity).get('sid'), _claims(identity).get('session_id'), _claims(identity).get('jti'), f'bff-session-{identity.operator_id}')}",
        {"state": "active", "locale": value},
        now=now,
    )
    return _lifecycle(identity, "update_locale", None, now, locale={"resolved": value, "source": "session"}, deps=deps)


def _lifecycle(
    identity: Any,
    operation: str,
    idem: Optional[str],
    now: str,
    *,
    tenant: Optional[Dict[str, Any]] = None,
    locale: Optional[Dict[str, Any]] = None,
    deps: AuthDependencies,
) -> Dict[str, Any]:
    state = _state(identity, deps)
    selected_tenant = tenant or _tenant(identity, state.get("tenant_id"), deps)
    selected_locale = locale or {"resolved": _locale(state.get("locale")) or "en-US", "source": "session" if state.get("locale") else "default"}
    user = _user(identity, deps)
    session = _session(identity, checked_at=now)
    session["state"] = str(state.get("state") or "active")
    data = {
        "operation": {"type": operation, "operation_id": f"{operation}-{uuid.uuid4().hex[:12]}", "performed_at": now},
        "operator_id": user["operator_id"],
        "operatorId": user["operator_id"],
        "user": user,
        "currentUser": user,
        "current_user": user,
        "roles": user["roles"],
        "capabilities": user["capabilities"],
        "tenant": selected_tenant,
        "tenant_id": selected_tenant["id"],
        "tenantId": selected_tenant["id"],
        "allowed_tenants": selected_tenant["allowed_ids"],
        "allowedTenants": selected_tenant["allowed_ids"],
        "locale": selected_locale,
        "environment": {"name": os.getenv("PANTHEON_ENV", "dev")},
        "feature_flags": _feature_flags(identity),
        "featureFlags": _feature_flags(identity),
        "session": session,
        "session_kind": session["session_kind"],
        "sessionKind": session["session_kind"],
    }
    return {"data": data, "meta": {"contract": "BFF-LUV-SEM-001", "snapshot_at": now, "idempotency": {"idempotencyKey": idem, "replayed": False}}}


def create_auth_handlers(dependencies: AuthDependencies) -> Dict[str, Any]:
    deps = dependencies
    async def _dev_login(*, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await bff_auth_dev_login(payload=payload, deps=deps)

    async def _me(
        *,
        response: Any,
        tenant_id: Optional[str] = None,
        authorization: Optional[str] = None,
        pantheon_session: Optional[str] = None,
        x_mfa_token: Optional[str] = None,
        x_tenant_id: Optional[str] = None,
        x_pantheon_tenant: Optional[str] = None,
        x_correlation_id: Optional[str] = None,
        x_locale: Optional[str] = None,
        accept_language: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await bff_me(
            response=response,
            tenant_id=tenant_id,
            authorization=authorization,
            pantheon_session=pantheon_session,
            x_mfa_token=x_mfa_token,
            x_tenant_id=x_tenant_id,
            x_pantheon_tenant=x_pantheon_tenant,
            x_correlation_id=x_correlation_id,
            x_locale=x_locale,
            accept_language=accept_language,
            deps=deps,
        )

    async def _readiness(
        *,
        authorization: Optional[str] = None,
        pantheon_session: Optional[str] = None,
        x_mfa_token: Optional[str] = None,
        x_tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await bff_auth_readiness(
            authorization=authorization,
            pantheon_session=pantheon_session,
            x_mfa_token=x_mfa_token,
            x_tenant_id=x_tenant_id,
            deps=deps,
        )

    async def _refresh(
        *,
        payload: Dict[str, Any],
        authorization: Optional[str] = None,
        pantheon_session: Optional[str] = None,
        pantheon_refresh: Optional[str] = None,
        pantheon_refresh_token: Optional[str] = None,
        x_mfa_token: Optional[str] = None,
        x_refresh_token: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await bff_auth_refresh(
            payload=payload,
            authorization=authorization,
            pantheon_session=pantheon_session,
            pantheon_refresh=pantheon_refresh,
            pantheon_refresh_token=pantheon_refresh_token,
            x_mfa_token=x_mfa_token,
            x_refresh_token=x_refresh_token,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            deps=deps,
        )

    async def _logout(
        *,
        response: Any,
        payload: Dict[str, Any],
        authorization: Optional[str] = None,
        pantheon_session: Optional[str] = None,
        x_mfa_token: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        x_idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await bff_logout(
            response=response,
            payload=payload,
            authorization=authorization,
            pantheon_session=pantheon_session,
            x_mfa_token=x_mfa_token,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
            deps=deps,
        )

    async def _switch_tenant(
        *,
        payload: Dict[str, Any],
        authorization: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await bff_switch_tenant(
            payload=payload,
            authorization=authorization,
            deps=deps,
        )

    async def _update_locale(
        *,
        payload: Dict[str, Any],
        authorization: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await bff_update_locale(
            payload=payload,
            authorization=authorization,
            deps=deps,
        )

    return {
        "bff_auth_dev_login": _dev_login,
        "bff_me": _me,
        "bff_auth_readiness": _readiness,
        "bff_auth_refresh": _refresh,
        "bff_logout": _logout,
        "bff_switch_tenant": _switch_tenant,
        "bff_update_locale": _update_locale,
    }

