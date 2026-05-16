from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from fastapi import Body, Cookie, FastAPI, HTTPException, BackgroundTasks, Header, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from services.foundation import (  # noqa: E402
    ActorRef,
    ActorType,
    AuditAction,
    AuthorityScope,
    CommandEnvelope,
    EnvironmentName,
    EnvironmentScope,
    ErrorEnvelope,
    ErrorKind,
    FoundationValidationError,
    IdempotencyRecord,
    PolicyDecision,
    PolicyDecisionValue,
    TraceContext,
    foundation_id,
)
from services.foundation.health import register_fastapi_health_routes  # noqa: E402

from models import (
    ActionCommandStatus,
    ApproveMutationCommandPayload,
    AuditContext,
    SseEventEnvelope,
    BffActionCatalogResponse,
    BffErrorEnvelope,
    BffErrorPayload,
    CommandReceipt,
    CommandReceiptStatus,
    CommandResponse,
    CommandResultMeta,
    CommandRoutingPath,
    CommandStatus,
    CommandSubmissionResponse,
    CommandStatusResponse,
    CommandType,
    DecisionJournalEntryDTO,
    ErrorCode,
    ErrorDetail,
    InterventionKind,
    InterventionListResponse,
    InterventionRecord,
    InterventionStatus,
    JournalEntryMergePatch,
    McpImportedTool,
    McpRejectedTool,
    McpToolActionData,
    McpToolActionRequest,
    McpToolActionVerb,
    McpToolDescriptor,
    McpToolImportData,
    McpToolImportRequest,
    McpToolLifecycleStatus,
    ObjectType,
    OperatorCommand,
    OperatorIdentity,
    EVIDENCE_CAPABILITY_MAP,
    SOURCE_TYPE_TO_EVIDENCE_KIND,
    RecordSponsorDecisionCommandPayload,
    RejectMutationCommandPayload,
    StalenessWarning,
    TargetObject,
    utc_now,
)
from action_catalog import get_action_catalog, get_catalog_entry
from command_queue import CommandStore
from command_executor import execute_command_with_status
from session_lifecycle_store import SessionLifecycleStore
from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
from source_search_ops_client import (
    SearchIndexCommandClient,
    SourceIngestCommandClient,
    SourceSearchOpsClientError,
)
from read_store import ReadSurfaceStore, redact_evidence_refs
from settings_store import SettingsStore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def _bool_from_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_BFF_AUTH_STUB_ENV = "PANTHEON_BFF_AUTH_STUB"
_PRODUCTION_STRICT_ENVIRONMENTS = {
    "canary",
    "live",
    "prod",
    "production",
    "staging-live",
}
_DEFAULT_LOVABLE_CORS_ORIGINS = [
    # Lovable shared-preview and published URLs for the Pantheon UI lanes.
    "https://preview--pantheon-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-staging-live.lovable.app",
    "https://preview--pantheon.lovable.app",
    "https://preview--pantheon-ai-system-front.lovable.app",
    "https://pantheon-dev.lovable.app",
    "https://pantheon-ai-system-front-dev.lovable.app",
    "https://pantheon-ai-system-front-staging-live.lovable.app",
    "https://pantheon.lovable.app",
    "https://pantheon-ai-system-front.lovable.app",
    # BFF-CONSOL-022: Pantheon Frontend Lovable project preview URLs.
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
    "https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app",
]
_DEV_LOVABLE_CORS_ORIGINS = {
    "https://preview--pantheon-dev.lovable.app",
    "https://preview--pantheon-ai-system-front-dev.lovable.app",
    "https://pantheon-dev.lovable.app",
    "https://pantheon-ai-system-front-dev.lovable.app",
    # Pantheon Frontend Lovable project preview URLs (dev tier).
    "https://b75d3452-f667-4cf4-893a-1061de45b347.lovableproject.com",
    "https://id-preview--b75d3452-f667-4cf4-893a-1061de45b347.lovable.app",
}


def _normalized_origin(origin: str) -> str:
    return origin.strip().rstrip("/")


def _dedupe_origins(origins: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for origin in origins:
        cleaned = _normalized_origin(origin)
        if cleaned and cleaned not in seen:
            deduped.append(cleaned)
            seen.add(cleaned)
    return deduped


def _bff_auth_mode() -> str:
    return os.getenv("PANTHEON_BFF_AUTH_MODE", "strict").strip().lower() or "strict"


def _is_production_strict_mode() -> bool:
    env_name = os.getenv("PANTHEON_ENV", "").strip().lower()
    deployment_stage = os.getenv("PANTHEON_DEPLOYMENT_STAGE", "").strip().lower()
    return _bff_auth_mode() == "strict" and (
        env_name in _PRODUCTION_STRICT_ENVIRONMENTS
        or deployment_stage in _PRODUCTION_STRICT_ENVIRONMENTS
    )


def _bff_auth_stub_enabled() -> bool:
    return _bool_from_env(_BFF_AUTH_STUB_ENV) and _bff_auth_mode() != "strict"


def _cors_origins_from_env() -> List[str]:
    raw = os.getenv("PANTHEON_BFF_CORS_ORIGINS", "")
    origins = _dedupe_origins(raw.split(",")) if raw.strip() else list(_DEFAULT_LOVABLE_CORS_ORIGINS)
    if _is_production_strict_mode():
        origins = [
            origin
            for origin in origins
            if origin not in _DEV_LOVABLE_CORS_ORIGINS and origin != "*"
        ]
    return _dedupe_origins(origins)


def _cors_origin_allowed(origin: str) -> bool:
    return _normalized_origin(origin) in set(_cors_origins_from_env())


def _build_bff_app() -> FastAPI:
    cors_origins = _cors_origins_from_env()
    built_app = FastAPI(title="Pantheon Operator BFF", version="0.2.0")
    if cors_origins:
        built_app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=_CORS_ALLOW_HEADERS,
        )
    return built_app


_cors_origins = _cors_origins_from_env()
_CORS_ALLOW_HEADERS = [
    "Accept",
    "Accept-Language",
    "Authorization",
    "Cache-Control",
    "Content-Type",
    "X-BFF-Api-Version",
    "X-Confirm-Token",
    "Idempotency-Key",
    "Last-Event-ID",
    "X-Correlation-Id",
    "X-Idempotency-Key",
    "X-MFA-Token",
    "X-Request-Id",
    "X-Trace-Id",
]
app = _build_bff_app()

# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #

BFF_DATA_DIR = os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")
os.makedirs(BFF_DATA_DIR, exist_ok=True)
register_fastapi_health_routes(
    app,
    "operator-bff",
    dependencies=lambda: {
        "runtime_manager": {
            "status": "ok" if os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip() else "degraded",
            "url": os.getenv("PANTHEON_RUNTIME_MANAGER_URL", "").strip(),
        },
        "governance": {
            "status": "ok" if os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "").strip() else "degraded",
            "url": os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "").strip(),
        },
        "deployment": {
            "status": "ok" if os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip() else "degraded",
            "url": os.getenv("PANTHEON_DEPLOYMENT_API_URL", "").strip(),
        },
    },
    details=lambda: {"version": "0.2.0", "data_dir": BFF_DATA_DIR},
)
command_store = CommandStore(os.path.join(BFF_DATA_DIR, "commands.jsonl"))
session_lifecycle_store = SessionLifecycleStore(os.path.join(BFF_DATA_DIR, "session_lifecycle.json"))
read_store = ReadSurfaceStore(
    os.path.join(BFF_DATA_DIR, "read_surfaces.json"),
    allow_local_snapshot_fallback=_bool_from_env(
        "PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK",
        default=False,
    ),
)
settings_store = SettingsStore(os.path.join(BFF_DATA_DIR, "settings.json"))

_BFF_FOUNDATION_POLICY_VERSION = "2026-04-27"

# --------------------------------------------------------------------------- #
# Auth / identity helpers
# --------------------------------------------------------------------------- #

# Production default: validates HS256 JWT (issuer, audience, expiry, subject)
# via services.runtime_auth_inbound.validate_request_auth.
#
# Dev/test stub mode is available only when PANTHEON_BFF_AUTH_STUB=true and
# PANTHEON_BFF_AUTH_MODE is not strict.
# Stub accepts "Bearer <operator_id>:<comma_roles>[:mfa]" for local iteration.
#
# BFF-scoped env vars (mapped to runtime_auth_inbound key names internally):
#   PANTHEON_BFF_AUTH_MODE     - "strict" (default) or "permissive"
#   PANTHEON_BFF_JWT_SECRET    - HS256 signing secret (required for JWT validation)
#   PANTHEON_BFF_JWT_ISSUER    - optional expected iss claim
#   PANTHEON_BFF_JWT_AUDIENCE  - optional expected aud claim
#   PANTHEON_BFF_MFA_REQUIRED  - "true" to enforce X-MFA-Token on mfa_required routes
#   PANTHEON_BFF_DEFAULT_ROLE  - default role for JWT sub without explicit roles claim
#
# OIDC/JWKS mode (optional; activated when PANTHEON_BFF_JWKS_URI is non-empty):
#   PANTHEON_BFF_JWKS_URI      - JWKS endpoint URI (e.g. https://idp/.well-known/jwks.json)
#   PANTHEON_BFF_OIDC_DISCOVERY_URL - OIDC discovery metadata URL used to resolve jwks_uri
#   PANTHEON_BFF_OIDC_ISSUER   - expected iss claim for OIDC tokens (overrides JWT_ISSUER)
#   PANTHEON_BFF_OIDC_AUDIENCE - expected aud claim for OIDC tokens (overrides JWT_AUDIENCE)
#   PANTHEON_BFF_ROLE_CLAIMS   - comma-separated role claim paths (e.g. groups,roles)
#   PANTHEON_BFF_ROLE_MAP      - external=internal role map; semicolon-separated
#   PANTHEON_BFF_ROLE_MAP_MODE - passthrough (default) or strict
#   PANTHEON_BFF_MFA_CLAIMS    - comma-separated MFA claim paths (e.g. amr,acr)
#   PANTHEON_BFF_MFA_VALUES    - accepted MFA proof values (e.g. mfa,totp,webauthn)
#   When JWKS_URI is set, RS256/ES256 JWKS path is used instead of HS256.
#   Strict default still applies: stub tokens are not accepted in strict mode.


def _dev_login_client_id() -> str:
    return _first_nonblank(
        os.getenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_ID"),
        os.getenv("PANTHEON_BFF_OIDC_CLIENT_ID"),
    )


def _dev_login_client_secret() -> str:
    return _first_nonblank(
        os.getenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_SECRET"),
        os.getenv("PANTHEON_BFF_OIDC_CLIENT_SECRET"),
    )


def _dev_login_forbidden_environment() -> bool:
    env_name = os.getenv("PANTHEON_ENV", "").strip().lower()
    deployment_stage = os.getenv("PANTHEON_DEPLOYMENT_STAGE", "").strip().lower()
    return env_name in _PRODUCTION_STRICT_ENVIRONMENTS or deployment_stage in _PRODUCTION_STRICT_ENVIRONMENTS


def _dev_login_enabled() -> bool:
    if _dev_login_forbidden_environment():
        return False
    return bool(_dev_login_client_id() and _dev_login_client_secret())


def _dev_login_ttl_seconds() -> int:
    raw = os.getenv("PANTHEON_BFF_DEV_LOGIN_TTL_SECONDS", "900").strip()
    try:
        ttl = int(raw)
    except ValueError:
        ttl = 900
    return max(300, min(ttl, 3600))


def _dev_login_roles() -> List[str]:
    roles = _env_csv("PANTHEON_BFF_DEV_LOGIN_ROLES") or ["operator", "reviewer"]
    return sorted(set(role for role in roles if role in _READ_ROLES or role in _WRITE_ROLES))


def _dev_login_bool_env(name: str, *, default: bool) -> bool:
    return _bool_from_env(name, default=default)


def _issue_dev_login_jwt(client_id: str) -> Dict[str, Any]:
    try:
        from services.runtime_auth_inbound import encode_jwt_hs256
    except ImportError:
        from runtime_auth_inbound import encode_jwt_hs256  # type: ignore[no-redef]

    secret = os.getenv("PANTHEON_BFF_DEV_LOGIN_JWT_SECRET") or os.getenv("PANTHEON_BFF_JWT_SECRET", "")
    if not secret:
        raise _bff_error(
            500,
            ErrorCode.PRECONDITION_NOT_MET,
            "Dev login JWT signing secret is not configured",
            "PANTHEON_BFF_JWT_SECRET is required to issue dev-login JWTs",
            precondition_failed="jwt_secret",
            suggestion="Configure the dev BFF JWT secret before enabling /bff/auth/dev-login",
        )

    now = int(time.time())
    ttl = _dev_login_ttl_seconds()
    expires_at = now + ttl
    roles = _dev_login_roles() or ["operator", "reviewer"]
    subject = _first_nonblank(
        os.getenv("PANTHEON_BFF_DEV_LOGIN_SUBJECT"),
        f"pantheon-dev-{client_id}",
    )
    issuer = _first_nonblank(
        os.getenv("PANTHEON_BFF_JWT_ISSUER"),
        "pantheon-dev",
    )
    audience = _first_nonblank(
        os.getenv("PANTHEON_BFF_JWT_AUDIENCE"),
        "bff-operators",
    )
    tenant_id = _first_nonblank(
        os.getenv("PANTHEON_BFF_TENANT_ID"),
        os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
        os.getenv("PANTHEON_TENANT_ID"),
        "pantheon-dev",
    )
    allowed_tenants = _env_csv("PANTHEON_BFF_ALLOWED_TENANTS") or [tenant_id]
    mfa_verified = _dev_login_bool_env("PANTHEON_BFF_DEV_LOGIN_MFA_VERIFIED", default=False)
    claims: Dict[str, Any] = {
        "sub": subject,
        "roles": roles,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "nbf": now,
        "exp": expires_at,
        "jti": f"dev-login-{uuid.uuid4().hex}",
        "client_id": client_id,
        "token_use": "pantheon-bff-dev-login",
        "tenant_id": tenant_id,
        "allowed_tenants": allowed_tenants,
    }
    if mfa_verified:
        claims["mfa_verified"] = True

    token = encode_jwt_hs256(claims, secret=secret)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ttl,
        "issued_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": " ".join(roles),
    }


@app.post("/bff/auth/dev-login")
async def bff_auth_dev_login(payload: Dict[str, Any] = Body(default_factory=dict)):
    """Dev-only client-credentials exchange for short-lived BFF JWTs."""
    if not _dev_login_enabled():
        raise _bff_error(
            403,
            ErrorCode.PRECONDITION_NOT_MET,
            "Dev login is disabled for this BFF",
            "dev_login_disabled",
            precondition_failed="dev_login",
            suggestion="Use the dev BFF with configured client credentials; staging-live must use IdP OIDC/JWKS auth",
        )
    if str(payload.get("grant_type") or "client_credentials").strip() != "client_credentials":
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "Unsupported grant_type for dev login",
            "grant_type must be client_credentials",
            precondition_failed="grant_type",
        )

    client_id = str(payload.get("client_id") or payload.get("clientId") or "").strip()
    client_secret = str(payload.get("client_secret") or payload.get("clientSecret") or "").strip()
    expected_id = _dev_login_client_id()
    expected_secret = _dev_login_client_secret()
    if not (
        hmac.compare_digest(client_id, expected_id)
        and hmac.compare_digest(client_secret, expected_secret)
    ):
        raise _bff_error(
            401,
            ErrorCode.INVALID_TOKEN,
            "Invalid dev login client credentials",
            "AUTH_DEV_LOGIN_CLIENT_CREDENTIALS",
            suggestion="Use the configured PANTHEON_BFF_OIDC_CLIENT_ID and CLIENT_SECRET",
        )

    token_payload = _issue_dev_login_jwt(client_id)
    return {
        **token_payload,
        "meta": {
            "route": "POST /bff/auth/dev-login",
            "contract": "FE-INT-GATE-OIDC-DEV-LOGIN",
            "ttl_seconds": token_payload["expires_in"],
        },
    }


def _extract_identity(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
    session_cookie: Optional[str] = None,
) -> OperatorIdentity:
    if _bff_auth_stub_enabled():
        return _extract_identity_stub(authorization)
    # Cookie session: treat cookie value as a bearer token when no Authorization header present.
    if not authorization and session_cookie:
        identity = _extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
        identity = identity.model_copy(update={"token_kind": "cookie"})
        return identity
    return _extract_identity_jwt(authorization, mfa_token=mfa_token)


def _resolve_session_kind(identity: OperatorIdentity) -> str:
    """Return session_kind: cookie | bearer | stub based on how the identity was established."""
    if identity.token_kind == "stub":
        return "stub"
    if identity.token_kind == "cookie":
        return "cookie"
    return "bearer"


def _extract_identity_stub(authorization: Optional[str]) -> OperatorIdentity:
    """Legacy colon-format stub for PANTHEON_BFF_AUTH_STUB=true only."""
    if not authorization or not authorization.startswith("Bearer "):
        raise _bff_error(
            status_code=401,
            code=ErrorCode.INVALID_TOKEN,
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
            suggestion="Re-authenticate and include a valid Bearer token",
        )
    token = authorization[len("Bearer "):]
    if ":" not in token:
        lowered = token.lower()
        inferred_roles = ["operator"]
        if lowered.startswith("admin_"):
            inferred_roles = ["admin"]
        elif lowered.startswith("analyst_"):
            inferred_roles = ["analyst"]
        elif lowered.startswith("viewer_"):
            inferred_roles = ["viewer"]
        return OperatorIdentity(
            operator_id=token,
            roles=inferred_roles,
            mfa_verified="mfa" in lowered,
            claims={"sub": token, "roles": inferred_roles},
            token_kind="stub",
        )
    parts = token.split(":")
    operator_id = parts[0] if parts else "unknown"
    roles = parts[1].split(",") if len(parts) > 1 else ["operator"]
    mfa_verified = len(parts) > 2 and parts[2] == "mfa"
    return OperatorIdentity(
        operator_id=operator_id,
        roles=roles,
        mfa_verified=mfa_verified,
        claims={"sub": operator_id, "roles": roles},
        token_kind="stub",
    )


def _extract_identity_jwt(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
) -> OperatorIdentity:
    """JWT/RBAC auth facade for production. Validates issuer, audience, expiry, subject."""
    try:
        from services.runtime_auth_inbound import AuthError, validate_request_auth
    except ImportError:
        from runtime_auth_inbound import AuthError, validate_request_auth  # type: ignore[no-redef]

    bff_env = {
        "PANTHEON_RUNTIME_AUTH_MODE": os.getenv("PANTHEON_BFF_AUTH_MODE", "strict"),
        "PANTHEON_RUNTIME_JWT_SECRET": os.getenv("PANTHEON_BFF_JWT_SECRET", ""),
        "PANTHEON_RUNTIME_JWT_ISSUER": os.getenv("PANTHEON_BFF_JWT_ISSUER", ""),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": os.getenv("PANTHEON_BFF_JWT_AUDIENCE", ""),
        "PANTHEON_RUNTIME_DEFAULT_ROLE": os.getenv("PANTHEON_BFF_DEFAULT_ROLE", "operator"),
        "PANTHEON_RUNTIME_MFA_REQUIRED": os.getenv("PANTHEON_BFF_MFA_REQUIRED", "false"),
        # OIDC/JWKS optional path — active only when JWKS_URI is set.
        "PANTHEON_RUNTIME_JWKS_URI": os.getenv("PANTHEON_BFF_JWKS_URI", ""),
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": os.getenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", ""),
        "PANTHEON_RUNTIME_OIDC_ISSUER": os.getenv("PANTHEON_BFF_OIDC_ISSUER", ""),
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": os.getenv("PANTHEON_BFF_OIDC_AUDIENCE", ""),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": os.getenv("PANTHEON_BFF_ROLE_CLAIMS", ""),
        "PANTHEON_RUNTIME_ROLE_MAP": os.getenv("PANTHEON_BFF_ROLE_MAP", ""),
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": os.getenv("PANTHEON_BFF_ROLE_MAP_MODE", ""),
        "PANTHEON_RUNTIME_MFA_CLAIMS": os.getenv("PANTHEON_BFF_MFA_CLAIMS", ""),
        "PANTHEON_RUNTIME_MFA_VALUES": os.getenv("PANTHEON_BFF_MFA_VALUES", ""),
    }
    mfa_required = bff_env["PANTHEON_RUNTIME_MFA_REQUIRED"].lower() == "true"
    try:
        ctx = validate_request_auth(
            authorization=authorization or "",
            mfa_header=mfa_token or "",
            mfa_required=mfa_required,
            env=bff_env,
        )
    except AuthError as exc:
        if exc.status_code == 403:
            code = ErrorCode.INSUFFICIENT_ROLE
        elif exc.code in ("MFA_REQUIRED", "MFA_VALIDATION_FAILED"):
            code = ErrorCode.MFA_REQUIRED
        else:
            code = ErrorCode.INVALID_TOKEN
        # Sanitize codes that would leak server config details.
        _opaque_codes = {
            "AUTH_JWT_SECRET_MISSING",
            "JWKS_FETCH_FAILED",
            "JWKS_NO_MATCHING_KEY",
            "JWKS_INVALID_KEY",
            "JWKS_LIBRARY_UNAVAILABLE",
            "OIDC_DISCOVERY_FAILED",
        }
        if exc.code in _opaque_codes:
            effective_status = 401
            effective_message = "JWT bearer token cannot be verified"
            effective_reason = "AUTH_TOKEN_UNVERIFIED"
        else:
            effective_status = exc.status_code
            effective_message = exc.message
            effective_reason = exc.code
        raise _bff_error(
            status_code=effective_status,
            code=code,
            message=effective_message,
            reason=effective_reason,
            suggestion=(
                "Re-authenticate with a valid JWT bearer token"
                if effective_status == 401
                else None
            ),
        )
    if not str(ctx.claims.get("sub") or "").strip():
        raise _bff_error(
            status_code=401,
            code=ErrorCode.INVALID_TOKEN,
            message="JWT subject claim is required",
            reason="AUTH_JWT_SUBJECT_MISSING",
            suggestion="Re-authenticate with a valid JWT bearer token",
        )
    return OperatorIdentity(
        operator_id=ctx.actor_id,
        roles=sorted(ctx.roles),
        mfa_verified=ctx.mfa_verified,
        claims=dict(ctx.claims),
        token_kind=ctx.token_kind,
    )


def _bff_error(
    status_code: int,
    code: ErrorCode,
    message: str,
    reason: str,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
    foundation_error: Optional[ErrorEnvelope] = None,
    policy_decision: Optional[PolicyDecision] = None,
    audit_action: Optional[AuditAction] = None,
) -> HTTPException:
    body = BffErrorEnvelope(
        error=BffErrorPayload(
            code=code,
            message=message,
            details=ErrorDetail(
                reason=reason,
                precondition_failed=precondition_failed,
                suggestion=suggestion,
            ),
        )
    )
    detail = body.model_dump()
    error_payload = detail.get("error") if isinstance(detail.get("error"), dict) else {}
    error_details = error_payload.get("details") if isinstance(error_payload.get("details"), dict) else None
    if error_details is not None:
        if details_extra:
            for key, value in details_extra.items():
                if value is not None:
                    error_details[key] = value
        clean_correlation_id = str(correlation_id or "").strip()
        if clean_correlation_id:
            error_details["correlationId"] = clean_correlation_id
            detail["correlationId"] = clean_correlation_id
    if foundation_error is not None:
        detail["foundation_error"] = foundation_error.to_dict()
    if policy_decision is not None:
        detail["policy_decision"] = policy_decision.to_dict()
    if audit_action is not None:
        detail["audit_action"] = audit_action.to_dict()
    return HTTPException(status_code=status_code, detail=detail)


_FOUNDATION_COMMAND_ROUTE = "POST /api/v1/operator/commands"
_FINAL_COMMAND_ROUTE = "POST /bff/v1/commands"
_CANONICAL_ACTIONS_ROUTE = "POST /bff/actions/{type}/{id}/{action}"
_ACTIONS_TO_COMMANDS_SOURCE_ROUTE = "POST /bff/actions/{entityType}/{entityId}/{actionId}"
_ACTIONS_DEPRECATION_SINCE = "2026-05-14"
_ACTIONS_SUNSET_DATE = "2026-06-15"
_ACTIONS_SUNSET_HTTP_DATE = "Mon, 15 Jun 2026 00:00:00 GMT"
_ACTIONS_DEPRECATION_MESSAGE = (
    "/bff/actions/* is deprecated; submit the equivalent command envelope to "
    "/bff/v1/commands."
)


def _foundation_environment_scope() -> EnvironmentScope:
    raw = os.getenv("PANTHEON_ENV", "dev").strip().lower()
    if "live" in raw:
        name = EnvironmentName.LIVE
    elif "canary" in raw:
        name = EnvironmentName.CANARY
    elif "paper" in raw:
        name = EnvironmentName.PAPER
    elif "sandbox" in raw:
        name = EnvironmentName.SANDBOX
    else:
        name = EnvironmentName.DEV
    return EnvironmentScope(
        name=name,
        region=os.getenv("PANTHEON_REGION") or None,
        timezone=os.getenv("PANTHEON_TIMEZONE", "UTC"),
    )


def _foundation_actor_ref(identity: OperatorIdentity) -> ActorRef:
    return ActorRef(
        actor_type=ActorType.USER,
        actor_id=identity.operator_id,
        roles=identity.roles,
    )


def _foundation_request_payload(
    cmd: OperatorCommand,
    raw_payload: Dict[str, Any],
    *,
    route: str = _FOUNDATION_COMMAND_ROUTE,
    source_route: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {
        "route": route,
        "command": cmd.command.value,
        "target": cmd.target.model_dump(),
        "params": dict(cmd.params),
        "audit_context": cmd.audit_context.model_dump(),
        "raw_payload": raw_payload,
    }
    if source_route:
        payload["source_route"] = source_route
    return payload


def _foundation_route_metadata(route: str, source_route: Optional[str] = None) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {"route": route}
    if source_route:
        metadata["source_route"] = source_route
    return metadata


def _build_foundation_trace(
    *,
    environment: EnvironmentScope,
    actor_ref: ActorRef,
    trace_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    idempotency_key: Optional[str],
) -> TraceContext:
    clean_trace_id = str(trace_id or "").strip()
    if clean_trace_id:
        return TraceContext(
            trace_id=clean_trace_id,
            correlation_id=str(correlation_id or clean_trace_id).strip(),
            environment=environment,
            actor_ref=actor_ref,
            source_system="pantheon-bff",
            request_id=str(request_id or "").strip() or None,
            idempotency_key=str(idempotency_key or "").strip() or None,
        )
    return TraceContext.new(
        environment=environment,
        actor_ref=actor_ref,
        source_system="pantheon-bff",
        correlation_id=str(correlation_id or "").strip() or None,
        request_id=str(request_id or "").strip() or None,
        idempotency_key=str(idempotency_key or "").strip() or None,
    )


def _build_foundation_command_context(
    *,
    cmd: OperatorCommand,
    identity: OperatorIdentity,
    raw_payload: Dict[str, Any],
    trace_id: Optional[str],
    correlation_id: Optional[str],
    request_id: Optional[str],
    idempotency_key: Optional[str],
    route: str = _FOUNDATION_COMMAND_ROUTE,
    source_route: Optional[str] = None,
) -> Dict[str, Any]:
    environment = _foundation_environment_scope()
    actor_ref = _foundation_actor_ref(identity)
    route_metadata = _foundation_route_metadata(route, source_route)
    authority_scope = AuthorityScope(
        action=cmd.command.value,
        target_type=cmd.target.type.value,
        target_id=cmd.target.id,
        environment=environment,
        runtime_id=cmd.target.id if cmd.target.type == ObjectType.RUNTIME else None,
        attributes=route_metadata,
    )
    request_payload = _foundation_request_payload(
        cmd,
        raw_payload,
        route=route,
        source_route=source_route,
    )
    trace = _build_foundation_trace(
        environment=environment,
        actor_ref=actor_ref,
        trace_id=trace_id,
        correlation_id=correlation_id,
        request_id=request_id,
        idempotency_key=idempotency_key,
    )
    command_envelope = CommandEnvelope.new(
        command_type=cmd.command.value,
        actor_ref=actor_ref,
        authority_scope=authority_scope,
        payload=request_payload,
        trace=trace,
        idempotency_key=str(idempotency_key or "").strip() or None,
    )
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=command_envelope.idempotency_key,
        operation_type=f"bff.{cmd.command.value}",
        target_ref=authority_scope.target_ref,
        request_payload=request_payload,
        trace_id=command_envelope.trace.trace_id,
    )
    policy_decision = PolicyDecision.make(
        policy_id="bff.command.admission",
        policy_version=_BFF_FOUNDATION_POLICY_VERSION,
        decision=PolicyDecisionValue.ALLOW,
        actor_ref=actor_ref,
        action=cmd.command.value,
        target_ref=authority_scope.target_ref,
        environment=environment,
        trace_id=command_envelope.trace.trace_id,
    )
    audit_action = AuditAction.record(
        actor_ref=actor_ref,
        action_type="bff.command.accepted",
        target_ref=authority_scope.target_ref,
        environment=environment,
        reason=cmd.audit_context.reason or "operator command admission",
        trace=command_envelope.trace,
        payload=request_payload,
        policy_decision_ref=policy_decision.decision_id,
        metadata=route_metadata,
    )
    return {
        "admission_route": route,
        "source_route": source_route,
        "command_envelope": command_envelope,
        "trace_context": command_envelope.trace,
        "idempotency_record": idempotency_record,
        "policy_decision": policy_decision,
        "audit_action": audit_action,
        "request_payload": request_payload,
    }


def _serialize_foundation_context(context: Dict[str, Any]) -> Dict[str, Any]:
    serialized = {
        "admission_route": context.get("admission_route"),
        "trace_context": context["trace_context"].to_dict(),
        "command_envelope": context["command_envelope"].to_dict(),
        "idempotency_record": context["idempotency_record"].to_dict(),
        "policy_decision": context["policy_decision"].to_dict(),
        "audit_action": context["audit_action"].to_dict(),
    }
    if context.get("source_route"):
        serialized["source_route"] = context.get("source_route")
    return serialized


def _extract_error_fields(exc: HTTPException) -> Dict[str, Any]:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = detail.get("error") if isinstance(detail.get("error"), dict) else {}
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    details_extra = {
        key: value
        for key, value in details.items()
        if key not in {"reason", "precondition_failed", "suggestion"} and value is not None
    }
    code_value = error.get("code") or ErrorCode.INVALID_PARAMS.value
    try:
        code = ErrorCode(code_value)
    except ValueError:
        code = ErrorCode.INVALID_PARAMS
    return {
        "status_code": exc.status_code,
        "code": code,
        "message": error.get("message") or str(exc.detail),
        "reason": details.get("reason") or str(exc.detail),
        "precondition_failed": details.get("precondition_failed"),
        "suggestion": details.get("suggestion"),
        "details_extra": details_extra,
        "correlation_id": detail.get("correlationId") or details_extra.get("correlationId"),
    }


def _foundation_bff_error(
    exc: HTTPException,
    *,
    foundation_context: Dict[str, Any],
) -> HTTPException:
    fields = _extract_error_fields(exc)
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    admission_route = str(foundation_context.get("admission_route") or _FOUNDATION_COMMAND_ROUTE)
    source_route = str(foundation_context.get("source_route") or "").strip() or None
    route_metadata = _foundation_route_metadata(admission_route, source_route)
    if fields["status_code"] == 403:
        policy_decision = PolicyDecision.make(
            policy_id="bff.command.admission",
            policy_version=_BFF_FOUNDATION_POLICY_VERSION,
            decision=PolicyDecisionValue.DENY,
            actor_ref=command_envelope.actor_ref,
            action=command_envelope.command_type,
            target_ref=command_envelope.authority_scope.target_ref,
            environment=command_envelope.authority_scope.environment,
            trace_id=command_envelope.trace.trace_id,
            reasons=[fields["reason"]],
        )
        foundation_error = ErrorEnvelope.policy_denial(
            message=fields["message"],
            trace=command_envelope.trace,
            policy_decision_ref=policy_decision.decision_id,
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
        audit_action = AuditAction.record(
            actor_ref=command_envelope.actor_ref,
            action_type="bff.command.policy_denied",
            target_ref=command_envelope.authority_scope.target_ref,
            environment=command_envelope.authority_scope.environment,
            reason=fields["reason"],
            trace=command_envelope.trace,
            payload=foundation_context["request_payload"],
            policy_decision_ref=policy_decision.decision_id,
            metadata=route_metadata,
        )
        return _bff_error(
            fields["status_code"],
            fields["code"],
            fields["message"],
            fields["reason"],
            precondition_failed=fields["precondition_failed"],
            suggestion=fields["suggestion"],
            details_extra=fields["details_extra"],
            correlation_id=fields["correlation_id"],
            foundation_error=foundation_error,
            policy_decision=policy_decision,
            audit_action=audit_action,
        )

    if fields["status_code"] in {400, 422}:
        foundation_error = ErrorEnvelope.validation(
            message=fields["message"],
            trace=command_envelope.trace,
            error_code=fields["code"].value,
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
    else:
        foundation_error = ErrorEnvelope(
            error_id=foundation_id("err"),
            error_code=fields["code"].value,
            message=fields["message"],
            error_kind=ErrorKind.INVARIANT_VIOLATION,
            trace=command_envelope.trace,
            status_code=fields["status_code"],
            details={
                "reason": fields["reason"],
                "precondition_failed": fields["precondition_failed"],
                **fields["details_extra"],
            },
        )
    audit_action = AuditAction.record(
        actor_ref=command_envelope.actor_ref,
        action_type="bff.command.rejected",
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        reason=fields["reason"],
        trace=command_envelope.trace,
        payload=foundation_context["request_payload"],
        metadata=route_metadata,
    )
    return _bff_error(
        fields["status_code"],
        fields["code"],
        fields["message"],
        fields["reason"],
        precondition_failed=fields["precondition_failed"],
        suggestion=fields["suggestion"],
        details_extra=fields["details_extra"],
        correlation_id=fields["correlation_id"],
        foundation_error=foundation_error,
        audit_action=audit_action,
    )


def _foundation_idempotency_conflict_error(
    *,
    foundation_context: Dict[str, Any],
    existing_command_id: str,
) -> HTTPException:
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
    admission_route = str(foundation_context.get("admission_route") or _FOUNDATION_COMMAND_ROUTE)
    source_route = str(foundation_context.get("source_route") or "").strip() or None
    message = "Idempotency key was already used with a different command payload"
    reason = (
        f"idempotency_key={idempotency_record.idempotency_key} is already bound "
        f"to command {existing_command_id}"
    )
    foundation_error = ErrorEnvelope(
        error_id=foundation_id("err"),
        error_code=ErrorCode.IDEMPOTENCY_CONFLICT.value,
        message=message,
        error_kind=ErrorKind.IDEMPOTENCY_CONFLICT,
        trace=command_envelope.trace,
        status_code=409,
        details={
            "reason": reason,
            "existing_command_id": existing_command_id,
            "idempotency_key": idempotency_record.idempotency_key,
        },
    )
    audit_action = AuditAction.record(
        actor_ref=command_envelope.actor_ref,
        action_type="bff.command.idempotency_conflict",
        target_ref=command_envelope.authority_scope.target_ref,
        environment=command_envelope.authority_scope.environment,
        reason=reason,
        trace=command_envelope.trace,
        payload=foundation_context["request_payload"],
        metadata=_foundation_route_metadata(admission_route, source_route),
    )
    return _bff_error(
        409,
        ErrorCode.IDEMPOTENCY_CONFLICT,
        message,
        reason,
        precondition_failed="idempotency_conflict",
        suggestion="Reuse the original payload for this key or submit with a new X-Idempotency-Key",
        foundation_error=foundation_error,
        audit_action=audit_action,
    )


def _foundation_audit_for_command_record(
    *,
    identity: OperatorIdentity,
    command_type: CommandType,
    target_type: ObjectType,
    target_id: str,
    payload: Dict[str, Any],
    reason: str,
    command_id: str,
    idempotency_key: str,
    route: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> AuditAction:
    environment = _foundation_environment_scope()
    actor_ref = _foundation_actor_ref(identity)
    trace = _build_foundation_trace(
        environment=environment,
        actor_ref=actor_ref,
        trace_id=command_id,
        correlation_id=command_id,
        request_id=command_id,
        idempotency_key=idempotency_key,
    )
    audit_metadata = {
        "route": route,
        "command": command_type.value,
        "idempotency_key": idempotency_key,
    }
    if metadata:
        audit_metadata.update({key: value for key, value in metadata.items() if value is not None})
    return AuditAction.record(
        actor_ref=actor_ref,
        action_type="bff.command.accepted",
        target_ref=f"{target_type.value}:{target_id}",
        environment=environment,
        reason=reason,
        trace=trace,
        payload={
            "command": command_type.value,
            "target": {"type": target_type.value, "id": target_id},
            "payload": payload,
        },
        metadata=audit_metadata,
    )


def _command_audit_action_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    audit_action = foundation.get("audit_action") if isinstance(foundation.get("audit_action"), dict) else None
    if audit_action:
        return dict(audit_action)
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    audit_foundation = audit.get("foundation") if isinstance(audit.get("foundation"), dict) else {}
    audit_action = (
        audit_foundation.get("audit_action")
        if isinstance(audit_foundation.get("audit_action"), dict)
        else None
    )
    return dict(audit_action or {})


def _audit_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _project_command_record_audit_event(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    command_id = str(record.get("command_id") or "").strip()
    if not command_id:
        return None
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    audit = record.get("audit") if isinstance(record.get("audit"), dict) else {}
    foundation = record.get("foundation") if isinstance(record.get("foundation"), dict) else {}
    audit_action = _command_audit_action_from_record(record)
    idempotency_record = (
        foundation.get("idempotency_record")
        if isinstance(foundation.get("idempotency_record"), dict)
        else {}
    )
    audit_actor_ref = audit_action.get("actor_ref") if isinstance(audit_action.get("actor_ref"), dict) else {}
    metadata = audit_action.get("metadata") if isinstance(audit_action.get("metadata"), dict) else {}
    trace_context = foundation.get("trace_context") if isinstance(foundation.get("trace_context"), dict) else {}
    action_type = str(record.get("type") or metadata.get("command") or "").strip()
    target_type = str(target.get("type") or "").strip()
    target_id = str(target.get("id") or "").strip()
    timestamp = str(
        audit.get("timestamp")
        or audit_action.get("timestamp")
        or record.get("submitted_at")
        or utc_now()
    )
    reason = str(audit.get("reason") or audit_action.get("reason") or action_type or "operator command")
    event = {
        "entry_id": str(audit_action.get("action_id") or f"audit-{command_id}"),
        "actor": str(
            audit.get("operator_id")
            or audit.get("actor")
            or audit_actor_ref.get("actor_id")
            or "operator"
        ),
        "action_type": action_type,
        "target_type": target_type,
        "target_id": target_id,
        "timestamp": timestamp,
        "outcome": "accepted" if record.get("status") == CommandStatus.SUBMITTED.value else record.get("status"),
        "audit_context": {
            "reason": reason,
            "command_id": command_id,
            "receipt_id": command_id,
            "idempotency_key": (
                idempotency_record.get("idempotency_key")
                or metadata.get("idempotency_key")
                or audit.get("idempotency_key")
            ),
            "action_id": audit.get("action_id"),
            "foundation_action_type": audit_action.get("action_type"),
        },
        "evidence_refs": audit.get("evidence_refs") if isinstance(audit.get("evidence_refs"), list) else [],
        "command_ref": command_id,
        "trace_id": audit_action.get("trace_id") or trace_context.get("trace_id"),
        "correlation_id": (
            audit_action.get("correlation_id")
            or trace_context.get("correlation_id")
        ),
        "payload_checksum": audit_action.get("payload_checksum"),
        "audit_action": audit_action or None,
        "metadata": {
            "source": "command_store",
            "route": metadata.get("route"),
            "source_route": metadata.get("source_route"),
            "live_capital_side_effects": audit.get("live_capital_side_effects", False),
        },
    }
    return json.loads(json.dumps(event))


def _audit_event_matches(
    event: Dict[str, Any],
    *,
    actor: Optional[str] = None,
    action_types: Optional[List[str]] = None,
    target_type: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
) -> bool:
    if actor and event.get("actor") != actor:
        return False
    if action_types:
        allowed = {value for value in action_types if value}
        if event.get("action_type") not in allowed:
            return False
    if target_type and event.get("target_type") != target_type:
        return False
    event_dt = _audit_datetime(event.get("timestamp"))
    if from_ts is not None and (event_dt is None or event_dt < from_ts):
        return False
    if to_ts is not None and (event_dt is None or event_dt > to_ts):
        return False
    return True


def _list_governance_audit_events(
    *,
    actor: Optional[str] = None,
    action_types: Optional[List[str]] = None,
    target_type: Optional[str] = None,
    from_ts: Optional[datetime] = None,
    to_ts: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    events = read_store.list_governance_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    events_by_id: Dict[str, Dict[str, Any]] = {
        str(event.get("entry_id") or event.get("auditId") or event.get("id") or index): event
        for index, event in enumerate(events)
    }
    for record in command_store._get_all_commands():
        event = _project_command_record_audit_event(record)
        if not event or not _audit_event_matches(
            event,
            actor=actor,
            action_types=action_types,
            target_type=target_type,
            from_ts=from_ts,
            to_ts=to_ts,
        ):
            continue
        events_by_id.setdefault(str(event.get("entry_id")), event)
    merged = list(events_by_id.values())
    merged.sort(key=lambda event: str(event.get("timestamp") or ""), reverse=True)
    return json.loads(json.dumps(merged))


# --------------------------------------------------------------------------- #
# Command-specific precondition validators (§3 of contract)
# --------------------------------------------------------------------------- #

_APPROVE_DEPLOYMENT_REQUIRED = {"deployment_plan_id", "approval_decision"}
_VALID_APPROVAL_DECISIONS = {"approve", "reject"}
_APPROVE_DECISION_REQUIRED = {"decision_id"}
_REJECT_DECISION_REQUIRED = {"decision_id", "rejection_reason"}
_REQUEST_APPROVAL_REVISION_REQUIRED = {"decision_id", "revision_notes"}
_ESCALATE_DIFF_REQUIRED = {"plan_id", "escalation_reason"}

_PAUSE_RUNTIME_REQUIRED = {"runtime_binding_id", "pause_action"}
_VALID_PAUSE_ACTIONS = {"pause", "resume"}
_PAUSE_EXECUTION_REQUIRED = {"pause_new_entries", "cancel_open_orders"}

_ROLLBACK_REQUIRED = {"rollback_target_type", "target_id", "rollback_to_version"}
_VALID_ROLLBACK_TARGET_TYPES = {"deployment", "runtime"}
_APPROVE_ROLLBACK_REQUIRED = {"rollback_id"}
_REJECT_ROLLBACK_REQUIRED = {"rollback_id", "rejection_reason"}
_RISK_OFF_REQUIRED = {"reduce_exposure_pct"}
_SAFE_MODE_LEVELS = {"soft"}
_DRAWER_RUNTIME_COMMANDS = {
    CommandType.PAUSE_EXECUTION,
    CommandType.ISSUE_RISK_OFF,
    CommandType.LIQUIDATE_ALL,
    CommandType.HARD_ROLLBACK,
    CommandType.ISSUE_SAFE_MODE,
}
_LIVE_BROKER_SIGNAL_KEYS = {
    "account-mode",
    "account-type",
    "broker-mode",
    "broker-scope",
    "deployment-scope",
    "deployment-stage",
    "environment",
    "execution-mode",
    "order-mode",
    "runtime-mode",
    "scope",
    "target-env",
    "target-environment",
    "target-stage",
    "venue-mode",
}
_LIVE_BROKER_SIGNAL_VALUES = {
    "ibkr-live",
    "interactive-brokers-live",
    "live",
    "live-broker",
    "prod",
    "production",
    "staging-live",
}

_KILL_SWITCH_REQUIRED = {"scope", "activate"}
_VALID_SCOPES = {"persona", "pool", "all"}
_VALID_SEVERITIES = {"critical", "high", "medium"}

_APPROVE_EVO_REQUIRED = {"evolution_decision_id", "approval_action"}
_VALID_EVO_APPROVAL_ACTIONS = {"approve", "reject"}
_APPROVE_MUTATION_REQUIRED = {"decision_id"}
_REJECT_MUTATION_REQUIRED = {"decision_id"}
_RECORD_SPONSOR_DECISION_REQUIRED = {"committee_id", "sponsor_decision", "rationale_ref"}
_VALID_SPONSOR_DECISIONS = {"approved", "rejected", "conditional"}

_REMEDIATE_SENTINEL_REQUIRED = {"intervention_id", "remediation_action"}
_VALID_REMEDIATION_ACTIONS = {"resolve", "dismiss", "escalate"}

_EXECUTE_EVO_REQUIRED = {"evolution_decision_id", "action_type"}
_VALID_EVO_ACTION_TYPES = {"freeze", "retrain", "revalidate", "mutate", "retire"}

_OPERATOR_ALERTS_ROUTE = "/alerts"
_OPERATOR_INCIDENT_HOME_ROUTE = "/operator/incidents"
_OPERATOR_DEPLOYMENT_REVIEW_ROUTE = "/operator/deployment-review"
_OPERATOR_DEPLOYMENT_PLAN_ROUTE_PREFIX = "/operator/deployment-plans"
_OPERATOR_HEALTH_STATUS_ROUTE = "/operator/health-status"
_OPERATOR_POST_INCIDENT_REVIEW_ROUTE = "/operator/post-incident-review"
_OPERATOR_RUNTIME_STATE_ROUTE = "/operator/runtime-state"
_CONSULTATION_WORKBENCH_ROUTE = "/consultation"
_KNOWLEDGE_WORKBENCH_ROUTE = "/knowledge"
_TRAINER_WORKBENCH_ROUTE = "/trainer"
_GOVERNANCE_REVIEW_QUEUE_ROUTE = "/governance-review-queue"
_GOVERNANCE_APPROVAL_QUEUE_ROUTE = "/governance-approval-queue"
_MUTATION_REVIEW_ROUTE = "/operator/mutation-review"

_MUTATION_APPROVAL_ROLES = {
    "low": {"reviewer", "approver", "admin"},
    "medium": {"operator", "approver", "admin"},
    "high": {"approver", "admin"},
}

_MUTATION_REJECTION_ROLES = {
    "low": {"reviewer", "approver", "admin"},
    "medium": {"reviewer", "operator", "approver", "admin"},
    "high": {"approver", "admin"},
}


def _env_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def _value_contains_live_broker_signal(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_value_contains_live_broker_signal(child) for child in value.values())
    if isinstance(value, list):
        return any(_value_contains_live_broker_signal(child) for child in value)
    token = _env_token(value)
    if token in _LIVE_BROKER_SIGNAL_VALUES:
        return True
    return bool(
        re.search(r"(^|-)live($|-)", token)
        and ("broker" in token or "ibkr" in token or "interactive-brokers" in token)
    )


def _payload_has_live_broker_signal(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            key_token = _env_token(key)
            if (
                key_token in _LIVE_BROKER_SIGNAL_KEYS
                and _value_contains_live_broker_signal(child)
            ):
                return True
            if _payload_has_live_broker_signal(child):
                return True
    elif isinstance(value, list):
        return any(_payload_has_live_broker_signal(child) for child in value)
    return False


def _command_targets_live_runtime(cmd: OperatorCommand) -> bool:
    if cmd.target.type != ObjectType.RUNTIME:
        return False
    target_id = _env_token(cmd.target.id)
    return bool(re.search(r"(^|-)live($|-)", target_id))


def _ensure_live_broker_scope_allowed(cmd: OperatorCommand, payload: Dict[str, Any]) -> None:
    if _bool_from_env("PANTHEON_LIVE_BROKER_ENABLED", default=False):
        return
    if not (_command_targets_live_runtime(cmd) or _payload_has_live_broker_signal(payload)):
        return
    env_name = os.getenv("PANTHEON_ENV", "dev").strip() or "dev"
    raise _bff_error(
        403,
        ErrorCode.PRECONDITION_NOT_MET,
        "Live broker scope is disabled for this BFF",
        f"PANTHEON_ENV={env_name} has PANTHEON_LIVE_BROKER_ENABLED=false",
        precondition_failed="live_broker_scope",
        suggestion=(
            "Use the staging-live BFF only after operator auth, governance, "
            "runtime kill-switch, and broker rehearsal gates are verified"
        ),
    )


def _require_admin_mfa(identity: OperatorIdentity, command_name: str) -> None:
    if "admin" not in identity.roles:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            f"{command_name} requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )
    if not identity.mfa_verified:
        raise _bff_error(
            403,
            ErrorCode.MFA_REQUIRED,
            f"{command_name} requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
            suggestion="Provide a valid MFA token in your session",
        )


def _deployment_review_href(plan_id: str) -> str:
    return f"{_OPERATOR_DEPLOYMENT_REVIEW_ROUTE}?plan={plan_id}"


def _deployment_plan_href(plan_id: str) -> str:
    return f"{_OPERATOR_DEPLOYMENT_PLAN_ROUTE_PREFIX}/{plan_id}"


def _incident_detail_href(incident_id: str) -> str:
    return f"{_OPERATOR_INCIDENT_HOME_ROUTE}/{incident_id}"


def _post_incident_review_href(incident_id: str) -> str:
    return f"{_OPERATOR_POST_INCIDENT_REVIEW_ROUTE}?incident={incident_id}"


def _runtime_command_context(runtime_id: str, incident_id: Optional[str] = None) -> Dict[str, Optional[str]]:
    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    binding_id = None
    capital_pool_id = None
    artifact_id = None
    artifact_version = None
    plan_id = None

    if runtime_binding:
        binding_id = str(runtime_binding.get("id") or runtime_binding.get("binding_id") or runtime_id)
        capital_pool_id = runtime_binding.get("capital_pool_id")
        artifact_id = runtime_binding.get("artifact_id")
        artifact_version = runtime_binding.get("artifact_version")
        plan_id = runtime_binding.get("plan_id")

    if incident_id:
        incident = read_store.get_incident(incident_id)
        if incident and str(incident.get("runtime_id") or "") == runtime_id:
            capital_pool_id = capital_pool_id or incident.get("capital_pool_id")
            artifact_id = artifact_id or incident.get("artifact_id")
            artifact_version = artifact_version or incident.get("artifact_version")

    if plan_id and not capital_pool_id:
        plan = read_store.get_deployment_plan(plan_id)
        if plan:
            capital_pool_id = plan.get("capital_pool_id")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": binding_id or runtime_id,
        "capital_pool_id": capital_pool_id,
        "artifact_id": artifact_id,
        "artifact_version": artifact_version,
    }


def _validate_drawer_runtime_target(cmd: OperatorCommand) -> None:
    if cmd.command not in _DRAWER_RUNTIME_COMMANDS:
        return
    if cmd.target.type != ObjectType.RUNTIME:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"{cmd.command.value} requires target.type = Runtime",
            "Drawer commands only accept Runtime targets",
        )
    if not str(cmd.target.id or "").strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"{cmd.command.value} requires a runtime target id",
            "target.id must be a non-empty runtime id",
        )


def _validate_audit_context(cmd: OperatorCommand) -> None:
    if str(cmd.audit_context.reason or "").strip():
        return
    raise _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        "audit_context.reason is required",
        "audit_context.reason must be a non-empty string",
    )


def _require_operator_command_idempotency_key(value: Optional[str]) -> str:
    idempotency_key = str(value or "").strip()
    if idempotency_key:
        return idempotency_key
    raise _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        "X-Idempotency-Key is required for operator commands",
        (
            "Runtime, deployment, approval, and incident command admission "
            "requires a non-empty X-Idempotency-Key header"
        ),
        precondition_failed="idempotency_key",
        suggestion="Retry with X-Idempotency-Key set to a stable client retry key",
    )


def _resolve_final_idempotency_key(
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
) -> str:
    """Prefer Idempotency-Key (RFC); accept X-Idempotency-Key as a compatibility alias."""
    canonical = str(idempotency_key or "").strip()
    if canonical:
        return canonical
    alias = str(x_idempotency_key or "").strip()
    if alias:
        return alias
    raise _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        "Idempotency-Key is required for operator commands",
        (
            "Final contract routes require a non-empty Idempotency-Key header; "
            "X-Idempotency-Key is accepted as a temporary compatibility alias"
        ),
        precondition_failed="idempotency_key",
        suggestion="Retry with Idempotency-Key set to a stable client retry key",
    )


def _reject_body_idempotency_key(payload: Dict[str, Any]) -> None:
    """Reject final-contract payloads that carry idempotencyKey in the body."""
    body_key = "idempotencyKey" if "idempotencyKey" in payload else "idempotency_key" if "idempotency_key" in payload else None
    if body_key is not None:
        raise _bff_error(
            400,
            ErrorCode.INVALID_REQUEST,
            f"{body_key} must not appear in the request body",
            (
                "Final contract routes require idempotency via the Idempotency-Key header, "
                "not the request body"
            ),
            precondition_failed="body_idempotency_key",
            suggestion=f"Remove {body_key} from the body and set the Idempotency-Key header",
        )


_JOURNAL_MERGE_PATCH_CONTENT_TYPE = "application/merge-patch+json"
_JOURNAL_PATCH_FIELDS = {
    "title",
    "body",
    "tags",
    "linkedStrategyIds",
    "linkedPersonaIds",
    "visibility",
}
_JOURNAL_TAG_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
_JOURNAL_WRITE_ROLES = {"operator", "reviewer", "approver", "admin"}
_JOURNAL_VISIBILITY_CAPABILITY = {
    "private": "agora.journal.write.private",
    "team": "agora.journal.write.team",
    "committee": "agora.journal.write.committee",
    "public": "agora.journal.write.public",
}
_JOURNAL_VISIBILITY_ROLES = {
    "private": {"operator", "reviewer", "approver", "admin"},
    "team": {"operator", "reviewer", "approver", "admin"},
    "committee": {"reviewer", "approver", "admin"},
    "public": {"admin"},
}


def _stable_json_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_journal_write_role(identity: OperatorIdentity) -> None:
    if _JOURNAL_WRITE_ROLES.intersection(identity.roles):
        return
    raise _bff_error(
        403,
        ErrorCode.INSUFFICIENT_ROLE,
        "Agora journal patch requires operator-level role",
        "Operator does not hold a role allowed to patch journal entries",
        precondition_failed="role_check",
        suggestion="Escalate to an operator, reviewer, approver, or admin",
    )


def _require_merge_patch_content_type(content_type: Optional[str]) -> None:
    media_type = str(content_type or "").split(";", 1)[0].strip().lower()
    if media_type == _JOURNAL_MERGE_PATCH_CONTENT_TYPE:
        return
    raise _bff_error(
        415,
        ErrorCode.INVALID_REQUEST,
        "Agora journal patch requires application/merge-patch+json",
        "JSON Merge Patch endpoints reject non-merge-patch content types",
        precondition_failed="content_type",
        suggestion="Retry with Content-Type: application/merge-patch+json",
        details_extra={"requiredContentType": _JOURNAL_MERGE_PATCH_CONTENT_TYPE},
    )


def _journal_visibility_allowed(identity: OperatorIdentity, visibility: str) -> bool:
    return bool(_JOURNAL_VISIBILITY_ROLES.get(visibility, set()).intersection(identity.roles))


def _journal_validation_error(
    *,
    message: str,
    reason: str,
    field: str,
    status_code: int = 422,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    return _bff_error(
        status_code,
        ErrorCode.INVALID_PARAMS,
        message,
        reason,
        precondition_failed=f"journal_patch.{field}",
        suggestion="Submit a JSON Merge Patch body containing only valid journal fields",
        details_extra={"field": field, **(details_extra or {})},
    )


def _validate_journal_merge_patch_payload(
    payload: Dict[str, Any],
    identity: OperatorIdentity,
) -> Dict[str, Any]:
    unknown_fields = sorted(set(payload) - _JOURNAL_PATCH_FIELDS)
    if unknown_fields:
        raise _journal_validation_error(
            message="Agora journal patch contains unsupported fields",
            reason=f"Unsupported fields: {', '.join(unknown_fields)}",
            field="fields",
            status_code=400,
            details_extra={"unsupportedFields": unknown_fields},
        )
    if not any(field in payload for field in _JOURNAL_PATCH_FIELDS):
        raise _journal_validation_error(
            message="Agora journal patch must include at least one editable field",
            reason="The merge patch body did not contain any journal entry fields",
            field="fields",
            status_code=400,
        )

    try:
        patch_model = JournalEntryMergePatch(**payload)
    except ValidationError as exc:
        raise _journal_validation_error(
            message="Agora journal patch has invalid field types",
            reason=str(exc),
            field="payload",
        ) from exc

    patch = patch_model.model_dump(exclude_unset=True)

    if "title" in patch:
        title = patch["title"]
        if title is None or not str(title).strip():
            raise _journal_validation_error(
                message="Journal entry title is required when patched",
                reason="title must be a non-empty string",
                field="title",
            )
        title = str(title).strip()
        if len(title) > 160:
            raise _journal_validation_error(
                message="Journal entry title is too long",
                reason="title must be 1-160 characters",
                field="title",
                details_extra={"maxLength": 160},
            )
        patch["title"] = title

    if "body" in patch:
        body = "" if patch["body"] is None else str(patch["body"])
        if len(body) > 20000:
            raise _journal_validation_error(
                message="Journal entry body is too long",
                reason="body must be at most 20000 characters",
                field="body",
                details_extra={"maxLength": 20000},
            )
        patch["body"] = body

    for list_field in ("linkedStrategyIds", "linkedPersonaIds"):
        if list_field not in patch or patch[list_field] is None:
            continue
        cleaned = [str(item).strip() for item in patch[list_field]]
        if any(not item for item in cleaned):
            raise _journal_validation_error(
                message=f"{list_field} cannot contain empty ids",
                reason=f"{list_field} entries must be non-empty strings",
                field=list_field,
            )
        patch[list_field] = cleaned

    if "tags" in patch and patch["tags"] is not None:
        tags = [str(tag).strip() for tag in patch["tags"]]
        invalid_tags = [tag for tag in tags if not _JOURNAL_TAG_RE.fullmatch(tag)]
        if invalid_tags:
            raise _journal_validation_error(
                message="Journal entry tags must be lowercase slug or dot.case",
                reason="tags must match lowercase dot.case or slug form",
                field="tags",
                details_extra={"invalidTags": invalid_tags},
            )
        patch["tags"] = tags

    if "visibility" in patch:
        visibility = patch["visibility"]
        if visibility is None:
            raise _journal_validation_error(
                message="Journal entry visibility cannot be null",
                reason="visibility must be a supported scope",
                field="visibility",
            )
        visibility = str(visibility).strip().lower()
        required_capability = _JOURNAL_VISIBILITY_CAPABILITY.get(visibility)
        if not required_capability:
            raise _journal_validation_error(
                message="Journal entry visibility is unsupported",
                reason="visibility must be private, team, committee, or public",
                field="visibility",
                details_extra={"allowedValues": sorted(_JOURNAL_VISIBILITY_CAPABILITY)},
            )
        if not _journal_visibility_allowed(identity, visibility):
            raise _bff_error(
                403,
                ErrorCode.INSUFFICIENT_ROLE,
                "Operator lacks capability for requested journal visibility",
                f"visibility={visibility} requires {required_capability}",
                precondition_failed="journal_patch.visibility",
                suggestion="Choose a narrower visibility or escalate to an authorized operator",
                details_extra={
                    "field": "visibility",
                    "requiredCapability": required_capability,
                },
            )
        patch["visibility"] = visibility

    return patch


_CONFIRM_TOKEN_FIELDS = (
    "confirmToken",
    "confirm_token",
    "confirmationToken",
    "confirmation_token",
)
_APPROVAL_EVIDENCE_FIELDS = (
    "approvalId",
    "approval_id",
    "approvalDecisionId",
    "approval_decision_id",
)
_TWO_MAN_EVIDENCE_FIELDS = (
    "twoManSignatureId",
    "two_man_signature_id",
    "twoManApprovalId",
    "two_man_approval_id",
    "secondOperatorId",
    "second_operator_id",
    "secondOperatorSignature",
    "second_operator_signature",
)


def _precondition_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _precondition_field_present(
    payload: Dict[str, Any],
    params: Dict[str, Any],
    aliases: tuple[str, ...],
) -> bool:
    for source in (payload, params):
        for alias in aliases:
            if alias in source and _precondition_value_present(source.get(alias)):
                return True
    return False


def _final_precondition_details(
    *,
    cmd: OperatorCommand,
    kind: str,
) -> Dict[str, Any]:
    return {
        "actionId": cmd.command.value,
        "entityType": cmd.target.type.value,
        "entityId": cmd.target.id,
        "kind": kind,
    }


def _final_precondition_error(
    *,
    cmd: OperatorCommand,
    status_code: int,
    code: ErrorCode,
    message: str,
    reason: str,
    kind: str,
    correlation_id: Optional[str],
    suggestion: str,
) -> HTTPException:
    return _bff_error(
        status_code=status_code,
        code=code,
        message=message,
        reason=reason,
        precondition_failed=kind,
        suggestion=suggestion,
        details_extra=_final_precondition_details(cmd=cmd, kind=kind),
        correlation_id=correlation_id,
    )


def _require_final_command_preconditions(
    *,
    cmd: OperatorCommand,
    payload: Dict[str, Any],
    confirm_token: Optional[str],
    correlation_id: Optional[str],
) -> None:
    entry = get_catalog_entry(cmd.command.value)
    if entry is None:
        return

    params = dict(cmd.params)
    if getattr(entry, "requires_confirm_token", False):
        if not (
            _precondition_value_present(confirm_token)
            or _precondition_field_present(payload, params, _CONFIRM_TOKEN_FIELDS)
        ):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=428,
                code=ErrorCode.CONFIRM_TOKEN_REQUIRED,
                message="Confirmation token is required before this action can be accepted",
                reason="CONFIRM_TOKEN_MISSING",
                kind="confirm_token",
                correlation_id=correlation_id,
                suggestion="Retry with X-Confirm-Token or confirmToken after the operator confirmation step",
            )

    if getattr(entry, "requires_approval", False):
        if not _precondition_field_present(payload, params, _APPROVAL_EVIDENCE_FIELDS):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.APPROVAL_REQUIRED,
                message="Approval evidence is required before this action can be accepted",
                reason="APPROVAL_EVIDENCE_MISSING",
                kind="approval",
                correlation_id=correlation_id,
                suggestion="Attach approvalId from the governance approval flow before retrying",
            )

    if getattr(entry, "requires_two_man", False):
        if not _precondition_field_present(payload, params, _TWO_MAN_EVIDENCE_FIELDS):
            raise _final_precondition_error(
                cmd=cmd,
                status_code=409,
                code=ErrorCode.TWO_MAN_REQUIRED,
                message="Two-man authorization is required before this action can be accepted",
                reason="TWO_MAN_SIGNATURE_MISSING",
                kind="two_man",
                correlation_id=correlation_id,
                suggestion="Attach a second authorized operator signature before retrying",
            )


def _normalize_operator_command_payload(payload: Dict[str, Any]) -> OperatorCommand:
    command_type = payload.get("command_type")
    if command_type:
        try:
            if command_type == CommandType.APPROVE_MUTATION.value:
                mutation = ApproveMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params: Dict[str, Any] = {"decision_id": mutation.decision_id}
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.APPROVE_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="approve_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.REJECT_MUTATION.value:
                mutation = RejectMutationCommandPayload.model_validate(payload)
                note = str(mutation.note or "").strip() or None
                params = {"decision_id": mutation.decision_id}
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.REJECT_MUTATION,
                    target=TargetObject(type=ObjectType.EVOLUTION_DECISION, id=mutation.decision_id),
                    action="reject_mutation",
                    params=params,
                    audit_context=AuditContext(reason=note or mutation.command_type),
                )
            if command_type == CommandType.RECORD_SPONSOR_DECISION.value:
                decision = RecordSponsorDecisionCommandPayload.model_validate(payload)
                note = str(decision.note or "").strip() or None
                params = {
                    "committee_id": decision.committee_id,
                    "sponsor_decision": decision.sponsor_decision,
                    "rationale_ref": decision.rationale_ref,
                }
                if note:
                    params["note"] = note
                return OperatorCommand(
                    command=CommandType.RECORD_SPONSOR_DECISION,
                    target=TargetObject(type=ObjectType.COMMITTEE_BOARD, id=decision.committee_id),
                    action="record_sponsor_decision",
                    params=params,
                    audit_context=AuditContext(reason=note or decision.command_type),
                )
        except ValidationError as exc:
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                f"Invalid {command_type} payload",
                str(exc),
            ) from exc
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "Unknown command_type",
            f"Unsupported command_type: {command_type}",
        )

    try:
        return OperatorCommand.model_validate(payload)
    except ValidationError as exc:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid operator command payload",
            str(exc),
        ) from exc


def _validate_pause_execution(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _PAUSE_EXECUTION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for PauseExecution",
            f"Missing fields: {sorted(missing)}",
        )
    for field in sorted(_PAUSE_EXECUTION_REQUIRED):
        if not isinstance(params.get(field), bool):
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                f"Invalid {field} value",
                f"{field} must be a boolean",
            )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "PauseExecution requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_issue_risk_off(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _RISK_OFF_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for IssueRiskOff",
            f"Missing fields: {sorted(missing)}",
        )
    exposure_pct = params.get("reduce_exposure_pct")
    if not isinstance(exposure_pct, (int, float)) or exposure_pct <= 0 or exposure_pct > 100:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid reduce_exposure_pct value",
            "reduce_exposure_pct must be a number between 1 and 100",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "IssueRiskOff requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_liquidate_all(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    if params:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "LiquidateAll does not accept params",
            "params must be an empty object for LiquidateAll",
        )
    _require_admin_mfa(identity, "LiquidateAll")


def _validate_hard_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    target_artifact_id = str(params.get("target_artifact_id") or "").strip()
    if not target_artifact_id:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for HardRollback",
            "target_artifact_id must be a non-empty string",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "HardRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _validate_issue_safe_mode(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    safe_mode_level = str(params.get("safe_mode_level") or "").strip().lower()
    if safe_mode_level not in _SAFE_MODE_LEVELS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid safe_mode_level",
            f"safe_mode_level must be one of {sorted(_SAFE_MODE_LEVELS)}",
        )
    _require_admin_mfa(identity, "IssueSafeMode")


def _derive_drawer_execution_params(
    command: CommandType,
    runtime_id: str,
    params: Dict[str, Any],
    *,
    actor_id: Optional[str],
    reason: Optional[str],
    incident_id: Optional[str],
) -> Dict[str, Any]:
    context = _runtime_command_context(runtime_id, incident_id)
    base = {
        "runtime_id": runtime_id,
        "runtime_binding_id": context["runtime_binding_id"],
        "capital_pool_id": context["capital_pool_id"],
        "actor_id": actor_id or "operator-command",
        "reason": reason or "",
        "incident_id": incident_id,
    }

    if command == CommandType.PAUSE_EXECUTION:
        return {
            **base,
            "pause_action": "pause",
            "pause_new_entries": params["pause_new_entries"],
            "cancel_open_orders": params["cancel_open_orders"],
        }

    if command == CommandType.ISSUE_RISK_OFF:
        if not context["capital_pool_id"]:
            raise ValueError(
                f"Runtime {runtime_id} cannot be routed to a capital pool."
            )
        return {
            **base,
            "scope": "pool",
            "scope_id": context["capital_pool_id"],
            "action_override": "risk_off",
            "trigger_reason": "operator_emergency_stop",
            "reduce_exposure_pct": params["reduce_exposure_pct"],
        }

    if command == CommandType.LIQUIDATE_ALL:
        if not context["capital_pool_id"]:
            raise ValueError(
                f"Runtime {runtime_id} cannot be routed to a capital pool."
            )
        return {
            **base,
            "scope": "pool",
            "scope_id": context["capital_pool_id"],
            "action_override": "liquidate",
            "trigger_reason": "operator_emergency_stop",
        }

    if command == CommandType.HARD_ROLLBACK:
        return {
            **base,
            "rollback_target_type": "runtime",
            "target_id": context["runtime_binding_id"],
            "rollback_to_version": params["target_artifact_id"],
            "rollback_action_type": "pause_then_replace",
            "target_artifact_id": params["target_artifact_id"],
        }

    if not context["capital_pool_id"]:
        raise ValueError(
            f"Runtime {runtime_id} cannot be routed to a capital pool."
        )
    return {
        **base,
        "safe_mode_level": params["safe_mode_level"],
        "target_state": "guarded",
    }


def _stored_command_params(
    cmd: OperatorCommand,
    identity: OperatorIdentity,
    raw_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if cmd.command in _DRAWER_RUNTIME_COMMANDS:
        return dict(cmd.params)
    del identity
    params = dict(cmd.params)
    if cmd.command == CommandType.REMEDIATE_SENTINEL_INTERVENTION and raw_payload:
        # Normalize any top-level two-man alias from the raw payload into the
        # canonical params key so the executor always receives two_man_signature_id.
        if not str(params.get("two_man_signature_id") or "").strip():
            for alias in _TWO_MAN_EVIDENCE_FIELDS:
                val = str(raw_payload.get(alias) or "").strip()
                if val:
                    params["two_man_signature_id"] = val
                    break
    return params


def _resolve_execution_params_for_record(record: Dict[str, Any]) -> Dict[str, Any]:
    command_type = CommandType(record["type"])
    params = dict(record.get("params") or {})
    if command_type not in _DRAWER_RUNTIME_COMMANDS:
        return params

    target = record.get("target") or {}
    audit = record.get("audit") or {}
    runtime_id = str(target.get("id") or "").strip()
    if not runtime_id:
        raise ValueError(f"{command_type.value} is missing target.id.")

    return _derive_drawer_execution_params(
        command_type,
        runtime_id,
        params,
        actor_id=audit.get("operator_id"),
        reason=audit.get("reason"),
        incident_id=audit.get("incident_id"),
    )


def _validate_approve_deployment(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_DEPLOYMENT_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveDeployment",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_decision"] not in _VALID_APPROVAL_DECISIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid approval_decision value",
            f"Must be one of {_VALID_APPROVAL_DECISIONS}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ApproveDeployment requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_approve_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "ApproveDecision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_reject_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RejectDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("rejection_reason") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "RejectDecision requires a non-empty rejection_reason",
            "rejection_reason must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RejectDecision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_request_approval_revision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REQUEST_APPROVAL_REVISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RequestApprovalRevision",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("revision_notes") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "RequestApprovalRevision requires non-empty revision_notes",
            "revision_notes must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RequestApprovalRevision requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_pause_runtime(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _PAUSE_RUNTIME_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for PauseRuntime",
            f"Missing fields: {sorted(missing)}",
        )
    if params["pause_action"] not in _VALID_PAUSE_ACTIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid pause_action value",
            f"Must be one of {_VALID_PAUSE_ACTIONS}",
        )
    if not {"operator", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "PauseRuntime requires 'operator' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )


def _validate_execute_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ExecuteRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if params["rollback_target_type"] not in _VALID_ROLLBACK_TARGET_TYPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid rollback_target_type",
            f"Must be one of {_VALID_ROLLBACK_TARGET_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ExecuteRollback requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _validate_approve_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ApproveRollback requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_reject_rollback(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_ROLLBACK_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for RejectRollback",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("rejection_reason") or "").strip():
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "RejectRollback requires a non-empty rejection_reason",
            "rejection_reason must be a non-empty string",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "RejectRollback requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


def _validate_activate_kill_switch(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _KILL_SWITCH_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ActivateKillSwitch",
            f"Missing fields: {sorted(missing)}",
        )
    if params["scope"] not in _VALID_SCOPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid scope for ActivateKillSwitch",
            f"Must be one of {_VALID_SCOPES}",
        )
    severity = params.get("severity")
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid severity for ActivateKillSwitch",
            f"Must be one of {_VALID_SEVERITIES}",
        )
    # Admin role required
    if "admin" not in identity.roles:
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ActivateKillSwitch requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )
    # MFA required for kill-switch (§3.2.3)
    if not identity.mfa_verified:
        raise _bff_error(
            403, ErrorCode.MFA_REQUIRED,
            "ActivateKillSwitch requires MFA verification",
            "Admin action requires MFA validation",
            precondition_failed="mfa_check",
            suggestion="Provide a valid MFA token in your session",
        )


def _validate_escalate_diff(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _ESCALATE_DIFF_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for EscalateDiff",
            f"Missing fields: {sorted(missing)}",
        )
    if not str(params.get("escalation_reason") or "").strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "EscalateDiff requires a non-empty escalation_reason",
            "escalation_reason must be a non-empty string",
        )
    if not {"operator", "reviewer", "approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "EscalateDiff requires operator-level governance access",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, reviewer, approver, or admin role",
        )


def _validate_approve_evolution_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_EVO_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveEvolutionDecision",
            f"Missing fields: {sorted(missing)}",
        )
    if params["approval_action"] not in _VALID_EVO_APPROVAL_ACTIONS:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid approval_action",
            f"Must be one of {_VALID_EVO_APPROVAL_ACTIONS}",
        )
    if not {"reviewer", "admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ApproveEvolutionDecision requires 'reviewer', 'approver', or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with reviewer, approver, or admin role",
        )


def _validate_execute_evolution_action(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _EXECUTE_EVO_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Missing required params for ExecuteEvolutionAction",
            f"Missing fields: {sorted(missing)}",
        )
    if params["action_type"] not in _VALID_EVO_ACTION_TYPES:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS,
            "Invalid action_type for ExecuteEvolutionAction",
            f"Must be one of {_VALID_EVO_ACTION_TYPES}",
        )
    if not {"admin", "approver"}.intersection(identity.roles):
        raise _bff_error(
            403, ErrorCode.INSUFFICIENT_ROLE,
            "ExecuteEvolutionAction requires 'admin' or 'approver' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with admin or approver role",
        )


def _mutation_review_surface_state(
    decision: Dict[str, Any],
    approval_decision: Optional[Dict[str, Any]],
    linked_incident: Optional[Dict[str, Any]],
    linked_postmortem: Optional[Dict[str, Any]],
) -> str:
    required_sources_available = True
    decision_state = str(decision.get("decision_state") or decision.get("status") or "").lower()
    approval_decision_id = str(decision.get("approval_decision_id") or "").strip()
    linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
    linked_postmortem_id = str(decision.get("linked_postmortem_id") or "").strip()

    if read_store.dataset_source("evolution_decisions") == "missing":
        required_sources_available = False
    if decision_state in {"reviewed", "approved", "executed", "rejected", "superseded"}:
        if not approval_decision_id or approval_decision is None:
            required_sources_available = False
    if linked_incident_id and linked_incident is None:
        required_sources_available = False
    if linked_postmortem_id and linked_postmortem is None:
        required_sources_available = False

    read_surface_state = _read_surface_state()
    if read_surface_state == "unavailable" or not required_sources_available:
        return "unavailable"
    if read_surface_state in {"degraded", "stale"}:
        return "stale"

    dataset_names = ["evolution_decisions"]
    if approval_decision_id:
        dataset_names.append("approval_decisions")
    if linked_incident_id:
        dataset_names.append("incidents")
    if linked_postmortem_id:
        dataset_names.append("postmortems")
    if any(read_store.dataset_source(dataset) == "local_snapshot" for dataset in dataset_names):
        return "stale"
    return "fresh"


def _mutation_review_roles_for(
    risk_level: str,
    *,
    action: str,
) -> set[str]:
    normalized_risk = str(risk_level or "").lower()
    if action == "approve":
        return _MUTATION_APPROVAL_ROLES.get(normalized_risk, {"admin"})
    return _MUTATION_REJECTION_ROLES.get(normalized_risk, {"admin"})


def _mutation_review_allowed_actions(
    decision: Dict[str, Any],
    identity: OperatorIdentity,
    surface_state: str,
) -> Dict[str, bool]:
    if surface_state == "unavailable":
        return {
            "canApproveMutation": False,
            "canRejectMutation": False,
        }

    decision_state = str(decision.get("decision_state") or decision.get("status") or "").lower()
    risk_level = str(decision.get("risk_level") or "").lower()
    identity_roles = set(identity.roles)

    can_approve = (
        decision_state == "reviewed"
        and bool(identity_roles.intersection(_mutation_review_roles_for(risk_level, action="approve")))
    )
    can_reject = (
        decision_state in {"proposed", "reviewed"}
        and bool(identity_roles.intersection(_mutation_review_roles_for(risk_level, action="reject")))
    )
    return {
        "canApproveMutation": can_approve,
        "canRejectMutation": can_reject,
    }


def _mutation_threshold_triggers(decision: Dict[str, Any]) -> List[Dict[str, Any]]:
    risk_assessment = decision.get("risk_assessment") or {}
    explicit = risk_assessment.get("threshold_triggers")
    if isinstance(explicit, list):
        return explicit

    triggers: List[Dict[str, Any]] = []
    for snapshot in decision.get("threshold_snapshots") or []:
        if not isinstance(snapshot, dict):
            continue
        triggers.append(
            {
                "trigger_type": snapshot.get("signal_type"),
                "metric": snapshot.get("metric_name"),
                "observed_value": str(snapshot.get("observed_value")),
                "threshold_value": str(snapshot.get("threshold_value")),
                "threshold_source": snapshot.get("policy_source"),
            }
        )
    return triggers


def _mutation_required_approvals(decision: Dict[str, Any]) -> List[Dict[str, Any]]:
    explicit = decision.get("required_approvals")
    if isinstance(explicit, list):
        return explicit

    risk_level = str(decision.get("risk_level") or "").lower()
    if risk_level == "low":
        required_roles = ["reviewer_on_duty"]
    elif risk_level == "medium":
        required_roles = ["reviewer", "risk_owner"]
    elif risk_level == "high":
        required_roles = ["governance_committee"]
    else:
        required_roles = []

    approvals: List[Dict[str, Any]] = []
    review_chain = decision.get("review_chain") or []
    for role in required_roles:
        matched_step = next(
            (
                step for step in review_chain
                if isinstance(step, dict)
                and str(step.get("actor_role") or "").lower() == role
                and str(step.get("step_type") or step.get("action") or "").lower() in {"reviewed", "approved"}
            ),
            None,
        )
        approvals.append(
            {
                "role": role,
                "approved_by": matched_step.get("actor_id") if matched_step else None,
                "approved_at": matched_step.get("timestamp") if matched_step else None,
                "status": "approved" if matched_step else "pending",
            }
        )
    return approvals


def _mutation_review_projection(
    decision: Dict[str, Any],
    *,
    approval_decision: Optional[Dict[str, Any]],
    linked_incident: Optional[Dict[str, Any]],
    linked_postmortem: Optional[Dict[str, Any]],
    identity: OperatorIdentity,
    snapshot_at: str,
) -> Dict[str, Any]:
    surface_state = _mutation_review_surface_state(
        decision,
        approval_decision,
        linked_incident,
        linked_postmortem,
    )
    allowed_actions = _mutation_review_allowed_actions(decision, identity, surface_state)
    proposed_changes = dict(decision.get("proposed_changes") or {})
    risk_assessment = dict(decision.get("risk_assessment") or {})
    evidence_refs = list(decision.get("evidence_refs") or [])

    if linked_incident and not any(ref.get("ref_id") == linked_incident.get("incident_id") for ref in evidence_refs if isinstance(ref, dict)):
        evidence_refs.append(
            {
                "ref_type": "incident",
                "ref_id": linked_incident.get("incident_id"),
                "summary": linked_incident.get("evidence_summary") or linked_incident.get("title"),
            }
        )
    postmortem_id = (
        linked_postmortem.get("postmortem_id")
        or linked_postmortem.get("report_id")
        or linked_postmortem.get("id")
        if linked_postmortem
        else None
    )
    if linked_postmortem and not any(ref.get("ref_id") == postmortem_id for ref in evidence_refs if isinstance(ref, dict)):
        evidence_refs.append(
            {
                "ref_type": "postmortem",
                "ref_id": postmortem_id,
                "summary": linked_postmortem.get("summary") or linked_postmortem.get("title"),
            }
        )

    if "summary" not in proposed_changes:
        proposed_changes["summary"] = decision.get("rationale") or decision.get("notes") or ""
    proposed_changes.setdefault("target_stage", decision.get("target_stage"))
    proposed_changes.setdefault("downstream_plane", (decision.get("execution_result") or {}).get("plane"))
    proposed_changes.setdefault("change_details", [])

    # Apply evidence redaction based on derived capabilities for this identity.
    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None
    evidence_refs, redacted_count = redact_evidence_refs(identity, evidence_refs, capabilities=capabilities)

    risk_assessment.setdefault(
        "risk_summary",
        decision.get("notes") or decision.get("rationale") or "",
    )
    risk_assessment.setdefault("severity", None)
    risk_assessment["threshold_triggers"] = _mutation_threshold_triggers(decision)

    review_chain = [
        {
            "action": step.get("action") or step.get("step_type"),
            "actor_role": step.get("actor_role"),
            "actor_id": step.get("actor_id"),
            "acted_at": step.get("acted_at") or step.get("timestamp"),
            "note": step.get("note"),
        }
        for step in (decision.get("review_chain") or [])
        if isinstance(step, dict)
    ]

    rollback_followthrough = decision.get("rollback_followthrough")
    if rollback_followthrough is None:
        linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
        rollbacks = read_store.get_rollbacks_by_incident(linked_incident_id) if linked_incident_id else []
        if rollbacks:
            first_rollback = rollbacks[0]
            rollback_followthrough = {
                "rollback_request_ref": first_rollback.get("rollback_id") or first_rollback.get("id"),
                "rollback_action_type": first_rollback.get("action_type"),
                "followthrough_note": first_rollback.get("reason"),
            }

    meta = {**_snapshot_meta(snapshot_at), "surfaces": {"mutation_review": surface_state}}
    # Attach supporting_counts including redaction telemetry
    meta.setdefault("supporting_counts", {})
    meta["supporting_counts"]["redacted_evidence_count"] = redacted_count

    return {
        "decision_id": decision.get("decision_id") or decision.get("id"),
        "target_type": decision.get("target_type"),
        "target_id": decision.get("target_id") or decision.get("artifact_id"),
        "target_version": decision.get("target_version"),
        "action_type": decision.get("action_type"),
        "decision_state": decision.get("decision_state") or decision.get("status"),
        "risk_level": decision.get("risk_level"),
        "created_at": decision.get("created_at"),
        "approval_decision_id": decision.get("approval_decision_id"),
        "proposed_changes": proposed_changes,
        "risk_assessment": risk_assessment,
        "required_approvals": _mutation_required_approvals(decision),
        "review_chain": review_chain,
        "linked_incident_id": decision.get("linked_incident_id"),
        "linked_postmortem_id": decision.get("linked_postmortem_id"),
        "evidence_refs": evidence_refs,
        "rollback_followthrough": rollback_followthrough,
        "allowedActions": allowed_actions,
        "meta": meta,
    }


def _mutation_review_inputs(
    decision_id: str,
) -> tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    decision = read_store.get_evolution_decision_by_id(decision_id)
    if decision is None:
        return None, None, None, None

    approval_decision_id = str(decision.get("approval_decision_id") or "").strip()
    approval_decision = (
        read_store.get_approval_decision(approval_decision_id)
        if approval_decision_id
        else None
    )
    linked_incident_id = str(decision.get("linked_incident_id") or "").strip()
    linked_incident = read_store.get_incident(linked_incident_id) if linked_incident_id else None
    linked_postmortem_id = str(decision.get("linked_postmortem_id") or "").strip()
    linked_postmortem = (
        read_store.get_postmortem(linked_postmortem_id)
        if linked_postmortem_id
        else None
    )
    return decision, approval_decision, linked_incident, linked_postmortem


def _cw03_committee_surface_state(
    committee: Dict[str, Any],
    *,
    snapshot_at: str,
) -> str:
    committee_surface = _dataset_surface_status(
        "consultation_sessions",
        snapshot_at=snapshot_at,
        has_data=bool(committee),
        missing_message="Committee board state is unavailable.",
    )
    if committee_surface.get("status") == "unavailable":
        return "unavailable"
    if committee.get("surface_state") == "degraded":
        return "degraded"
    if committee_surface.get("source") == "local_snapshot":
        return "degraded"
    if committee_surface.get("status") == "degraded":
        return "stale"
    return "ok"


def _cw03_allowed_actions(
    committee: Dict[str, Any],
    *,
    identity: OperatorIdentity,
    surface_state: str,
) -> Dict[str, bool]:
    if surface_state == "unavailable":
        return {
            "canRecordSponsorDecision": False,
        }
    sponsor_decision = committee.get("sponsor_decision")
    consensus_state = str(committee.get("consensus_state") or "").strip().lower()
    roles = set(identity.roles)
    sponsor_assignment = committee.get("sponsor_assignment") or {}
    sponsor_participant_id = str(sponsor_assignment.get("participant_id") or "").strip()
    return {
        "canRecordSponsorDecision": (
            sponsor_decision in (None, "")
            and consensus_state == "sponsor_required"
            and bool(sponsor_participant_id)
            and bool(roles.intersection({"operator", "approver", "admin"}))
        )
    }


def _cw03_committee_projection(
    committee: Dict[str, Any],
    *,
    identity: OperatorIdentity,
    snapshot_at: str,
) -> Dict[str, Any]:
    surface_state = _cw03_committee_surface_state(committee, snapshot_at=snapshot_at)
    allowed_actions = _cw03_allowed_actions(committee, identity=identity, surface_state=surface_state)
    return {
        "committee_id": committee.get("committee_id"),
        "committee_ref": committee.get("committee_ref"),
        "linked_request_id": committee.get("linked_request_id"),
        "linked_session_id": committee.get("linked_session_id"),
        "started_at": committee.get("started_at"),
        "escalation_reason": json.loads(json.dumps(committee.get("escalation_reason") or {})),
        "quorum_state": committee.get("quorum_state"),
        "consensus_state": committee.get("consensus_state"),
        "participant_roster": json.loads(json.dumps(committee.get("participant_roster") or [])),
        "sponsor_assignment": json.loads(json.dumps(committee.get("sponsor_assignment") or {})),
        "sponsor_decision": committee.get("sponsor_decision"),
        "sponsor_decided_at": committee.get("sponsor_decided_at"),
        "sponsor_decided_by": committee.get("sponsor_decided_by"),
        "synthesis_summary": json.loads(json.dumps(committee.get("synthesis_summary") or {})),
        "linked_evidence": json.loads(json.dumps(committee.get("linked_evidence") or [])),
        "service_handoff": json.loads(json.dumps(committee.get("service_handoff") or {})),
        "allowedActions": allowed_actions,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "committee_board": surface_state,
            },
        },
    }


_CW04_ALLOWED_STATUSES = {"draft", "published"}
_CW04_GOVERNANCE_ROLES = {"reviewer", "approver", "admin", "governance_committee"}
_CW04_SUPPORTED_TARGET_TYPES = {"strategy", "artifact", "deployment_plan"}


def _cw04_collection_surface_state(snapshot_at: str) -> str:
    surface = _dataset_surface_status(
        "consult_memos",
        snapshot_at=snapshot_at,
        missing_message="Red-team memo list is unavailable.",
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("source") == "local_snapshot":
        return "degraded"
    if surface.get("status") == "degraded":
        return "degraded"
    return "ok"


def _cw04_memo_surface_state(
    memo: Dict[str, Any],
    *,
    snapshot_at: str,
) -> str:
    dataset_state = _cw04_collection_surface_state(snapshot_at)
    explicit_state = str(memo.get("surface_state") or "").strip().lower()
    if explicit_state == "unavailable":
        return "unavailable"
    if explicit_state == "degraded":
        return "degraded"
    return dataset_state


def _cw04_memo_staleness(
    surface_state: str,
    *,
    snapshot_at: str,
) -> Dict[str, Any]:
    return {
        "status": "fresh" if surface_state == "ok" else "stale",
        "as_of": snapshot_at,
    }


def _cw04_governance_target(
    memo: Dict[str, Any],
) -> tuple[str, str, bool]:
    target = memo.get("governance_target") if isinstance(memo.get("governance_target"), dict) else {}
    target_type = str(target.get("target_type") or "").strip().lower()
    target_id = str(target.get("target_id") or "").strip()

    strategy_id = str(target.get("strategy_id") or "").strip()
    artifact_id = str(target.get("artifact_id") or "").strip()
    deployment_plan_id = str(target.get("deployment_plan_id") or "").strip()

    if not target_type:
        if strategy_id:
            target_type = "strategy"
            target_id = strategy_id
        elif artifact_id:
            target_type = "artifact"
            target_id = artifact_id
        elif deployment_plan_id:
            target_type = "deployment_plan"
            target_id = deployment_plan_id

    has_valid_target = bool(strategy_id or artifact_id or deployment_plan_id or target_id)
    return target_type, target_id, has_valid_target


def _cw04_allowed_actions(
    memo: Dict[str, Any],
    *,
    identity: OperatorIdentity,
    surface_state: str,
) -> Dict[str, bool]:
    if surface_state != "ok":
        return {
            "canInitiateGovernanceReview": False,
        }

    lifecycle_state = str(memo.get("lifecycle_state") or memo.get("status") or "").strip().lower()
    target_type, _target_id, has_valid_target = _cw04_governance_target(memo)
    roles = set(identity.roles)
    has_authority = bool(roles.intersection(_CW04_GOVERNANCE_ROLES))
    has_active_review = bool(str(memo.get("active_governance_review_id") or "").strip())
    suppressed = bool(memo.get("suppressed"))
    withdrawn = bool(memo.get("withdrawn"))
    governance_accepts_target_type = target_type in _CW04_SUPPORTED_TARGET_TYPES

    return {
        "canInitiateGovernanceReview": (
            lifecycle_state == "published"
            and has_valid_target
            and has_authority
            and not has_active_review
            and not suppressed
            and not withdrawn
            and governance_accepts_target_type
        )
    }


def _cw04_memo_projection(
    memo: Dict[str, Any],
    *,
    identity: OperatorIdentity,
    snapshot_at: str,
) -> Dict[str, Any]:
    surface_state = _cw04_memo_surface_state(memo, snapshot_at=snapshot_at)
    allowed_actions = _cw04_allowed_actions(memo, identity=identity, surface_state=surface_state)
    hide_memo_content = surface_state == "unavailable"

    # Prepare evidence refs and redact where appropriate
    evidence_refs = [] if hide_memo_content else json.loads(json.dumps(memo.get("evidence_refs") or []))
    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None
    evidence_refs, redacted_count = redact_evidence_refs(identity, evidence_refs, capabilities=capabilities)

    meta = {
        "snapshot_at": snapshot_at,
        "staleness": _cw04_memo_staleness(surface_state, snapshot_at=snapshot_at),
        "surfaces": {
            "redteam_memo": {
                "state": surface_state,
            },
        },
    }
    meta.setdefault("supporting_counts", {})
    meta["supporting_counts"]["redacted_evidence_count"] = redacted_count

    return {
        "object_ref": json.loads(json.dumps(memo.get("object_ref") or {})),
        "memo_id": memo.get("memo_id"),
        "memo_type": memo.get("memo_type") or "red_team",
        "status": memo.get("status"),
        "lifecycle_state": memo.get("lifecycle_state"),
        "author_ref": memo.get("author_ref"),
        "linked_request_id": memo.get("linked_request_id"),
        "linked_session_id": memo.get("linked_session_id"),
        "session_to_memo_mapping": json.loads(json.dumps(memo.get("session_to_memo_mapping") or {})),
        "summary": None if hide_memo_content else memo.get("summary"),
        "recommendations": [] if hide_memo_content else list(memo.get("recommendations") or []),
        "evidence_refs": evidence_refs,
        "published_at": memo.get("published_at"),
        "created_at": memo.get("created_at"),
        "supersedes_memo_id": memo.get("supersedes_memo_id"),
        "superseded_by_memo_id": memo.get("superseded_by_memo_id"),
        "allowedActions": allowed_actions,
        "meta": meta,
    }


def _cw04_validate_status_filters(status_values: Optional[List[str]]) -> Optional[List[str]]:
    if not status_values:
        return None
    invalid = [
        value
        for value in status_values
        if str(value).strip().lower() not in _CW04_ALLOWED_STATUSES
    ]
    if invalid:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid memo status filter",
            "status must be draft or published",
            precondition_failed="status",
        )
    return [str(value).strip().lower() for value in status_values if str(value).strip()]


def _validate_record_sponsor_decision(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _RECORD_SPONSOR_DECISION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RecordSponsorDecision",
            f"Missing fields: {sorted(missing)}",
        )
    sponsor_decision = str(params.get("sponsor_decision") or "").strip().lower()
    if sponsor_decision not in _VALID_SPONSOR_DECISIONS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid sponsor_decision value",
            f"sponsor_decision must be one of {sorted(_VALID_SPONSOR_DECISIONS)}",
        )
    rationale_ref = str(params.get("rationale_ref") or "").strip()
    if not rationale_ref:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "RecordSponsorDecision requires a non-empty rationale_ref",
            "rationale_ref must be a non-empty string",
        )
    committee_id = str(params.get("committee_id") or "").strip()
    committee = read_store.get_committee(committee_id)
    if committee is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee board not found",
            f"Committee {committee_id} does not exist",
        )
    projection = _cw03_committee_projection(
        committee,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["committee_board"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "RecordSponsorDecision is blocked while the committee board is unavailable",
            "Committee evidence cannot be composed reliably",
            precondition_failed="committee_board_surface",
        )
    if not projection["allowedActions"]["canRecordSponsorDecision"]:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RecordSponsorDecision is not allowed for this operator and committee state",
            "allowedActions.canRecordSponsorDecision is false for the current read projection",
            precondition_failed="allowedActions.canRecordSponsorDecision",
        )


def _validate_approve_mutation(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _APPROVE_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for ApproveMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    projection = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "ApproveMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canApproveMutation"]:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "ApproveMutation is not allowed for this operator and decision state",
            "allowedActions.canApproveMutation is false for the current read projection",
            precondition_failed="allowedActions.canApproveMutation",
        )


def _validate_reject_mutation(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REJECT_MUTATION_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RejectMutation",
            f"Missing fields: {sorted(missing)}",
        )
    decision_id = str(params.get("decision_id") or "").strip()
    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Mutation review decision not found",
            f"Evolution decision {decision_id} does not exist",
        )
    projection = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )
    if projection["meta"]["surfaces"]["mutation_review"] == "unavailable":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "RejectMutation is blocked while the mutation-review surface is unavailable",
            "Mutation-review evidence cannot be composed reliably",
            precondition_failed="mutation_review_surface",
        )
    if not projection["allowedActions"]["canRejectMutation"]:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RejectMutation is not allowed for this operator and decision state",
            "allowedActions.canRejectMutation is false for the current read projection",
            precondition_failed="allowedActions.canRejectMutation",
        )


def _validate_remediate_sentinel_intervention(params: Dict[str, Any], identity: OperatorIdentity) -> None:
    missing = _REMEDIATE_SENTINEL_REQUIRED - params.keys()
    if missing:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required params for RemediateSentinelIntervention",
            f"Missing fields: {sorted(missing)}",
        )
    remediation_action = str(params.get("remediation_action") or "").strip()
    if remediation_action not in _VALID_REMEDIATION_ACTIONS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid remediation_action value",
            f"remediation_action must be one of {sorted(_VALID_REMEDIATION_ACTIONS)}",
        )
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "RemediateSentinelIntervention requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )


_VALIDATORS = {
    CommandType.APPROVE_DEPLOYMENT: _validate_approve_deployment,
    CommandType.APPROVE_DECISION: _validate_approve_decision,
    CommandType.REJECT_DECISION: _validate_reject_decision,
    CommandType.REQUEST_APPROVAL_REVISION: _validate_request_approval_revision,
    CommandType.PAUSE_RUNTIME: _validate_pause_runtime,
    CommandType.PAUSE_EXECUTION: _validate_pause_execution,
    CommandType.ESCALATE_DIFF: _validate_escalate_diff,
    CommandType.ISSUE_RISK_OFF: _validate_issue_risk_off,
    CommandType.LIQUIDATE_ALL: _validate_liquidate_all,
    CommandType.HARD_ROLLBACK: _validate_hard_rollback,
    CommandType.ISSUE_SAFE_MODE: _validate_issue_safe_mode,
    CommandType.EXECUTE_ROLLBACK: _validate_execute_rollback,
    CommandType.APPROVE_ROLLBACK: _validate_approve_rollback,
    CommandType.REJECT_ROLLBACK: _validate_reject_rollback,
    CommandType.ACTIVATE_KILL_SWITCH: _validate_activate_kill_switch,
    CommandType.APPROVE_EVOLUTION_DECISION: _validate_approve_evolution_decision,
    CommandType.EXECUTE_EVOLUTION_ACTION: _validate_execute_evolution_action,
    CommandType.APPROVE_MUTATION: _validate_approve_mutation,
    CommandType.REJECT_MUTATION: _validate_reject_mutation,
    CommandType.RECORD_SPONSOR_DECISION: _validate_record_sponsor_decision,
    CommandType.REMEDIATE_SENTINEL_INTERVENTION: _validate_remediate_sentinel_intervention,
}

# --------------------------------------------------------------------------- #
# Read surface helpers
# --------------------------------------------------------------------------- #

_READ_ROLES = {"operator", "approver", "admin", "reviewer"}
_WRITE_ROLES = {"operator", "approver", "admin", "reviewer"}


def _require_read_role(identity: OperatorIdentity) -> None:
    if not _READ_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Read access requires operator-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, admin, or reviewer role",
        )


def _require_operator_role(identity: OperatorIdentity) -> None:
    if not _WRITE_ROLES.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Operator command access requires operator-level role",
            "Operator does not hold the required command role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, admin, or reviewer role",
        )


# Role -> capability mapping (best-effort). In production, prefer capability snapshots
# supplied by the auth service. This map is intentionally conservative.
_ROLE_CAPABILITY_MAP = {
    "admin": list(EVIDENCE_CAPABILITY_MAP.values()),
    "approver": [
        "approval.read",
        "postmortem.read",
        "policy.read",
    ],
    "operator": [
        "runtime.read",
        "risk.incident.read",
        "risk.alert.read",
        "artifact.read",
    ],
    "reviewer": [
        "approval.read",
        "strategy.view",
        "persona.view",
    ],
    "analyst": [
        "metric.read",
        "job.read",
        "audit.read",
    ],
    "viewer": [],
}


_ENTITY_TYPE_EVIDENCE_KIND: Dict[str, str] = {
    "strategy_spec": "strategy",
    "strategy": "strategy",
    "persona": "persona",
    "deployment_plan": "deployment",
    "deployment": "deployment",
    "runtime": "runtime",
    "runtime_binding": "runtime",
    "alert": "alert",
    "incident": "incident",
    "job": "job",
    "audit": "audit",
    "metric": "metric",
    "policy": "policy",
    "approval": "approval",
    "artifact": "artifact",
    "signal": "signal",
    "journal": "journal",
    "postmortem": "postmortem",
}


def _capabilities_for_identity(identity: OperatorIdentity) -> List[str]:
    """Derive a best-effort capability set from operator roles.

    This is a fallback for deployments where explicit capability claims
    are not provided by upstream auth. It is intentionally permissive for
    admin and conservative for other roles.
    """
    caps: List[str] = []
    for role in identity.roles:
        mapped = _ROLE_CAPABILITY_MAP.get(role)
        if mapped:
            caps.extend(mapped)
    # Deduplicate while preserving order
    seen = set()
    result: List[str] = []
    for c in caps:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _dedupe_nonblank_strings(values: List[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _split_claim_string(value: str) -> List[str]:
    clean = value.strip()
    if not clean:
        return []
    separator_pattern = r"[\s,]+" if "," not in clean else r"\s*,\s*"
    return [part.strip() for part in re.split(separator_pattern, clean) if part.strip()]


def _claim_path_value(claims: Dict[str, Any], path: str) -> Any:
    current: Any = claims
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _claim_value_as_strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_claim_string(value)
    if isinstance(value, dict):
        for key in ("id", "tenant_id", "tenantId", "value", "name"):
            if value.get(key):
                return [str(value[key]).strip()]
        return []
    if isinstance(value, (list, tuple, set)):
        collected: List[Any] = []
        for item in value:
            collected.extend(_claim_value_as_strings(item))
        return _dedupe_nonblank_strings(collected)
    return [str(value).strip()]


def _identity_claim_strings(identity: OperatorIdentity, paths: List[str]) -> List[str]:
    values: List[Any] = []
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    for path in paths:
        values.extend(_claim_value_as_strings(_claim_path_value(claims, path)))
    return _dedupe_nonblank_strings(values)


def _first_nonblank(*values: Any) -> Optional[str]:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return None


def _env_csv(name: str) -> List[str]:
    return _dedupe_nonblank_strings(_split_claim_string(os.getenv(name, "")))


def _normalize_locale(raw: Any) -> Optional[str]:
    clean = str(raw or "").strip().replace("_", "-")
    if not clean:
        return None
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", clean):
        return None
    parts = clean.split("-")
    normalized: List[str] = []
    for idx, part in enumerate(parts):
        if idx == 0:
            normalized.append(part.lower())
        elif len(part) == 2:
            normalized.append(part.upper())
        elif len(part) == 4:
            normalized.append(part.title())
        else:
            normalized.append(part)
    return "-".join(normalized)


def _preferred_locale_from_accept_language(accept_language: Optional[str]) -> Optional[str]:
    for raw_part in str(accept_language or "").split(","):
        locale_part = raw_part.split(";", 1)[0].strip()
        resolved = _normalize_locale(locale_part)
        if resolved:
            return resolved
    return None


def _resolve_bff_me_locale(
    identity: OperatorIdentity,
    *,
    x_locale: Optional[str],
    accept_language: Optional[str],
) -> Dict[str, Any]:
    claim_locale = _first_nonblank(
        *_identity_claim_strings(identity, ["locale", "preferred_locale", "preferredLanguage"])
    )
    default_locale = (
        _normalize_locale(os.getenv("PANTHEON_BFF_DEFAULT_LOCALE"))
        or _normalize_locale(os.getenv("PANTHEON_LOCALE"))
        or "en-US"
    )
    requested = _normalize_locale(x_locale)
    accepted = _preferred_locale_from_accept_language(accept_language)
    resolved = requested or accepted or _normalize_locale(claim_locale) or default_locale
    return {
        "resolved": resolved,
        "requested": requested,
        "accept_language": accepted,
        "default": default_locale,
        "timezone": os.getenv("PANTHEON_TIMEZONE", "UTC"),
    }


def _flag_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    clean = str(value or "").strip()
    lowered = clean.lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "off", "disabled"}:
        return False
    return clean


def _parse_feature_flags(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return {str(key): _flag_value(value) for key, value in raw.items() if str(key).strip()}
    if isinstance(raw, (list, tuple, set)):
        return {str(item).strip(): True for item in raw if str(item).strip()}
    clean = str(raw or "").strip()
    if not clean:
        return {}
    if clean.startswith("{"):
        try:
            parsed = json.loads(clean)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return _parse_feature_flags(parsed)
    flags: Dict[str, Any] = {}
    for part in clean.split(","):
        item = part.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            flags[key.strip()] = _flag_value(value)
        else:
            flags[item] = True
    return {key: value for key, value in flags.items() if key}


def _bff_me_feature_flags(identity: OperatorIdentity) -> Dict[str, Any]:
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    flags = {
        "executePlansBff": True,
        "sessionAuthMe": True,
    }
    flags.update(_parse_feature_flags(_claim_path_value(claims, "feature_flags")))
    flags.update(_parse_feature_flags(_claim_path_value(claims, "features")))
    flags.update(_parse_feature_flags(os.getenv("PANTHEON_BFF_FEATURE_FLAGS")))
    return flags


def _epoch_claim_seconds(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch_to_iso(value: Any) -> Optional[str]:
    epoch = _epoch_claim_seconds(value)
    if epoch is None:
        clean = str(value or "").strip()
        return clean or None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bff_me_session_payload(identity: OperatorIdentity, *, checked_at: str) -> Dict[str, Any]:
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    exp = _epoch_claim_seconds(claims.get("exp"))
    now = time.time()
    freshness_seconds = max(0, int(exp - now)) if exp is not None else None
    session_id = _first_nonblank(
        claims.get("sid"),
        claims.get("session_id"),
        claims.get("jti"),
        os.getenv("PANTHEON_SESSION_ID"),
        f"bff-session-{identity.operator_id}",
    )
    return {
        "id": session_id,
        "authenticated": True,
        "auth_mode": identity.token_kind,
        "session_kind": _resolve_session_kind(identity),
        "fresh": exp is None or exp > now,
        "freshness_seconds_remaining": freshness_seconds,
        "issued_at": _epoch_to_iso(claims.get("iat")),
        "expires_at": _epoch_to_iso(claims.get("exp")),
        "mfa_verified": identity.mfa_verified,
        "checked_at": checked_at,
    }


def _bff_me_environment_payload() -> Dict[str, Any]:
    scope = _foundation_environment_scope()
    stub_auth = _bff_auth_stub_enabled()
    auth_mode = "stub" if stub_auth else _bff_auth_mode()
    return {
        "name": scope.name.value,
        "deployment_stage": os.getenv("PANTHEON_DEPLOYMENT_STAGE", scope.name.value),
        "region": scope.region,
        "timezone": scope.timezone,
        "auth_mode": auth_mode,
        "strict_auth": not stub_auth and auth_mode == "strict",
    }


def _bff_me_user_payload(identity: OperatorIdentity) -> Dict[str, Any]:
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    claim_caps = _identity_claim_strings(
        identity,
        ["capabilities", "permissions", "scp", "scope"],
    )
    capabilities = _dedupe_nonblank_strings([*claim_caps, *_capabilities_for_identity(identity)])
    display_name = _first_nonblank(
        claims.get("name"),
        claims.get("preferred_username"),
        claims.get("email"),
        identity.operator_id,
    )
    return {
        "id": identity.operator_id,
        "operator_id": identity.operator_id,
        "display_name": display_name,
        "roles": identity.roles,
        "capabilities": capabilities,
        "mfa_verified": identity.mfa_verified,
    }


def _bff_me_tenant_payload(
    identity: OperatorIdentity,
    *,
    requested_tenant: Optional[str],
) -> Dict[str, Any]:
    claim_default = _first_nonblank(
        *_identity_claim_strings(
            identity,
            ["tenant_id", "tenantId", "tenant.id", "tid", "org_id", "organization.id"],
        )
    )
    default_tenant = _first_nonblank(
        os.getenv("PANTHEON_BFF_TENANT_ID"),
        os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
        os.getenv("PANTHEON_TENANT_ID"),
        claim_default,
        "pantheon-dev",
    )
    claim_allowed = _identity_claim_strings(
        identity,
        [
            "allowed_tenants",
            "allowedTenants",
            "tenant_ids",
            "tenantIds",
            "tenants",
            "tenant_id",
            "tenantId",
            "tenant.id",
            "tid",
            "org_id",
        ],
    )
    allowed_tenants = claim_allowed or _env_csv("PANTHEON_BFF_ALLOWED_TENANTS") or [default_tenant]
    effective_tenant = _first_nonblank(requested_tenant, default_tenant) or "pantheon-dev"
    if "*" not in allowed_tenants and effective_tenant not in allowed_tenants:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Tenant access denied",
            "Requested tenant is outside the caller tenant scope",
            precondition_failed="tenant_scope",
            suggestion="Switch to an allowed tenant or request access from an administrator",
            details_extra={
                "tenantId": effective_tenant,
                "allowedTenantIds": allowed_tenants,
            },
        )
    return {
        "id": effective_tenant,
        "requested_id": str(requested_tenant or "").strip() or None,
        "default_id": default_tenant,
        "allowed_ids": allowed_tenants,
        "scope": "global" if "*" in allowed_tenants else "tenant",
    }


def _sem_session_key(identity: OperatorIdentity) -> str:
    return f"operator:{identity.operator_id}"


def _sem_session_state(identity: OperatorIdentity) -> Dict[str, Any]:
    return session_lifecycle_store.get_session(_sem_session_key(identity))


@app.get("/bff/me")
async def bff_me(
    tenant_id: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
    pantheon_session: Optional[str] = Cookie(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    x_locale: Optional[str] = Header(default=None, alias="X-Locale"),
    accept_language: Optional[str] = Header(default=None, alias="Accept-Language"),
):
    """BFF current-user/session DTO consumed by execute-plans."""
    identity = _extract_identity(authorization, mfa_token=x_mfa_token, session_cookie=pantheon_session)
    _require_read_role(identity)
    snapshot_at = utc_now()
    session_state = _sem_session_state(identity)
    requested_header_tenant = _first_nonblank(x_tenant_id, x_pantheon_tenant, tenant_id)
    requested_tenant = _first_nonblank(requested_header_tenant, session_state.get("tenant_id"))
    tenant = _bff_me_tenant_payload(identity, requested_tenant=requested_tenant)
    tenant["source"] = "request" if requested_header_tenant else ("session" if session_state.get("tenant_id") else "default")
    locale = _resolve_bff_me_locale(
        identity,
        x_locale=x_locale,
        accept_language=accept_language,
    )
    if not _first_nonblank(x_locale, accept_language) and session_state.get("locale"):
        locale["resolved"] = session_state["locale"]
        locale["source"] = "session"
    else:
        locale["source"] = "header" if x_locale else ("accept_language" if accept_language else "default")
    user = _bff_me_user_payload(identity)
    session = _bff_me_session_payload(identity, checked_at=snapshot_at)
    session["state"] = str(session_state.get("state") or "active")
    if session_state.get("state") == "logged_out":
        session["authenticated"] = False
        session["fresh"] = False
        session["logged_out_at"] = session_state.get("logged_out_at")
    data = {
        "user": user,
        "current_user": user,
        "currentUser": user,
        "tenant": tenant,
        "tenant_id": tenant["id"],
        "locale": locale,
        "environment": _bff_me_environment_payload(),
        "feature_flags": _bff_me_feature_flags(identity),
        "session": session,
        "roles": user["roles"],
        "capabilities": user["capabilities"],
    }
    return {
        "data": data,
        "meta": {
            "route": "GET /bff/me",
            "contract": "BFF-LUV-GAP-009",
            "snapshot_at": snapshot_at,
        },
    }


def _sem_session_current_response(
    identity: OperatorIdentity,
    *,
    operation_type: str,
    tenant: Optional[Dict[str, Any]] = None,
    locale: Optional[Dict[str, Any]] = None,
    session_patch: Optional[Dict[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    replayed: bool = False,
) -> Dict[str, Any]:
    now = utc_now()
    state = _sem_session_state(identity)
    if session_patch:
        state = session_lifecycle_store.upsert_session(_sem_session_key(identity), session_patch, now=now)
    user = _bff_me_user_payload(identity)
    session = _bff_me_session_payload(identity, checked_at=now)
    session["state"] = str(state.get("state") or "active")
    if state.get("state") == "logged_out":
        session["authenticated"] = False
        session["fresh"] = False
        session["logged_out_at"] = state.get("logged_out_at")
    selected_tenant = tenant or _bff_me_tenant_payload(identity, requested_tenant=state.get("tenant_id"))
    if state.get("tenant_id"):
        selected_tenant["source"] = "session"
    selected_locale = locale or _resolve_bff_me_locale(identity, x_locale=None, accept_language=None)
    if state.get("locale"):
        selected_locale["resolved"] = state["locale"]
        selected_locale["source"] = "session"
    data = {
        "operation": {
            "type": operation_type,
            "operation_id": f"{operation_type}-{uuid.uuid4().hex[:12]}",
            "performed_at": now,
        },
        "user": user,
        "currentUser": user,
        "current_user": user,
        "roles": user["roles"],
        "capabilities": user["capabilities"],
        "tenant": selected_tenant,
        "tenant_id": selected_tenant["id"],
        "locale": selected_locale,
        "environment": _bff_me_environment_payload(),
        "feature_flags": _bff_me_feature_flags(identity),
        "session": session,
    }
    meta: Dict[str, Any] = {
        "contract": "BFF-LUV-SEM-001",
        "snapshot_at": now,
        "idempotency": {"idempotencyKey": idempotency_key, "replayed": replayed},
    }
    return {"data": data, "meta": meta}


def _sem_session_idempotency_key(route: str, identity: OperatorIdentity, key: Optional[str]) -> Optional[str]:
    clean = str(key or "").strip()
    if not clean:
        return None
    return f"{route}:{identity.operator_id}:{clean}"


def _sem_optional_idempotency_key(
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
) -> Optional[str]:
    return _first_nonblank(idempotency_key, x_idempotency_key)


@app.post("/bff/auth/refresh")
async def bff_auth_refresh(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    pantheon_session: Optional[str] = Cookie(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization, mfa_token=x_mfa_token, session_cookie=pantheon_session)
    _require_read_role(identity)
    resolved_key = _sem_optional_idempotency_key(idempotency_key, x_idempotency_key)
    record_key = _sem_session_idempotency_key("POST /bff/auth/refresh", identity, resolved_key)
    request_hash = _stable_json_hash({"route": "POST /bff/auth/refresh", "payload": payload or {}})
    if record_key:
        cached = session_lifecycle_store.get_idempotency(record_key)
        if cached:
            if cached.get("request_hash") != request_hash:
                raise _bff_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key was reused with a different refresh payload",
                    "The idempotency key already belongs to another refresh payload",
                    precondition_failed="idempotency_key",
                )
            result = dict(cached["result"])
            result.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
            return result
    now = utc_now()
    session_lifecycle_store.upsert_session(
        _sem_session_key(identity),
        {"state": "active", "last_refreshed_at": now},
        now=now,
    )
    result = _sem_session_current_response(identity, operation_type="refresh", idempotency_key=resolved_key)
    if record_key:
        session_lifecycle_store.put_idempotency(record_key, request_hash=request_hash, result=result, now=now)
    return result


@app.post("/bff/logout")
async def bff_logout(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    pantheon_session: Optional[str] = Cookie(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization, mfa_token=x_mfa_token, session_cookie=pantheon_session)
    _require_read_role(identity)
    resolved_key = _sem_optional_idempotency_key(idempotency_key, x_idempotency_key)
    record_key = _sem_session_idempotency_key("POST /bff/logout", identity, resolved_key)
    request_hash = _stable_json_hash({"route": "POST /bff/logout", "payload": payload or {}})
    if record_key:
        cached = session_lifecycle_store.get_idempotency(record_key)
        if cached:
            if cached.get("request_hash") != request_hash:
                raise _bff_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Idempotency key was reused with a different logout payload",
                    "The idempotency key already belongs to another logout payload",
                    precondition_failed="idempotency_key",
                )
            result = dict(cached["result"])
            result.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
            return result
    now = utc_now()
    result = _sem_session_current_response(
        identity,
        operation_type="logout",
        session_patch={"state": "logged_out", "logged_out_at": now},
        idempotency_key=resolved_key,
    )
    if record_key:
        session_lifecycle_store.put_idempotency(record_key, request_hash=request_hash, result=result, now=now)
    return result


@app.post("/bff/switch-tenant")
async def bff_switch_tenant(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    tenant_id = str(payload.get("tenantId") or payload.get("tenant_id") or "").strip()
    tenant = _bff_me_tenant_payload(identity, requested_tenant=tenant_id)
    tenant["source"] = "session"
    session_lifecycle_store.upsert_session(_sem_session_key(identity), {"tenant_id": tenant["id"], "state": "active"}, now=utc_now())
    return _sem_session_current_response(identity, operation_type="switch_tenant", tenant=tenant)


@app.patch("/bff/me/locale")
async def bff_update_locale(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    locale_value = _normalize_locale(payload.get("locale"))
    if not locale_value:
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "locale is required",
            "locale must be a non-empty BCP-47-ish language tag",
            precondition_failed="locale",
        )
    locale = _resolve_bff_me_locale(identity, x_locale=locale_value, accept_language=None)
    locale["resolved"] = locale_value
    locale["source"] = "session"
    session_lifecycle_store.upsert_session(_sem_session_key(identity), {"locale": locale_value, "state": "active"}, now=utc_now())
    return _sem_session_current_response(identity, operation_type="update_locale", locale=locale)


def _read_surface_state() -> str:
    return os.getenv("BFF_READ_SURFACE_STATE", "fresh")


def _meta_staleness() -> Optional[Dict[str, Any]]:
    state = _read_surface_state()
    if state == "fresh":
        return None
    return {
        "served_from": "cache",
        "last_known_at": utc_now(),
    }


def _surface_status() -> Dict[str, Any]:
    state = _read_surface_state()
    if state == "fresh":
        return {"status": "ok"}
    if state in {"degraded", "stale"}:
        return {
            "status": "degraded",
            "staleness": _meta_staleness(),
        }
    if state == "unavailable":
        return {
            "status": "unavailable",
            "staleness": _meta_staleness(),
        }
    return {"status": "ok"}


def _dataset_surface_status(
    dataset: str,
    *,
    snapshot_at: Optional[str] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    surface = dict(_surface_status())
    source = source or read_store.dataset_source(dataset)
    surface["source"] = source

    if source == "local_snapshot":
        if surface.get("status") == "ok":
            surface["status"] = "degraded"
        surface["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
        surface["staleness"] = {
            "served_from": "local_snapshot",
            "last_known_at": snapshot_at or utc_now(),
        }
    elif source == "missing":
        surface["status"] = "unavailable"
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
        )

    if has_data is False:
        if surface.get("status") == "ok":
            surface["status"] = "unavailable"
        if missing_message:
            surface["message"] = missing_message
        surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
        )

    return surface


def _read_surface_meta(
    dataset: str,
    surface_key: str,
    *,
    snapshot_at: Optional[str] = None,
    total: Optional[int] = None,
    surface: Optional[Dict[str, Any]] = None,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    degraded_reason: Optional[str] = None,
    unavailable_reason: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot_at = snapshot_at or utc_now()
    surface = surface or _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
        missing_message=missing_message,
    )
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            surface_key: surface,
        },
    }
    if total is not None:
        meta["total"] = total
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    label = surface_key.replace("_", " ")
    reason = _surface_degradation_reason(
        surface,
        degraded_reason=degraded_reason or f"{label} is degraded and may be stale.",
        unavailable_reason=unavailable_reason or f"{label} is currently unavailable.",
    )
    if reason is not None:
        meta["degradation"] = {"reason": reason}
    return meta


def _raise_if_read_surface_unavailable(
    surface: Dict[str, Any],
    *,
    label: str,
) -> None:
    if surface.get("status") != "unavailable":
        return
    raise _bff_error(
        503,
        ErrorCode.DOWNSTREAM_UNAVAILABLE,
        f"{label} read surface unavailable",
        str(surface.get("message") or surface.get("note") or f"{label} downstream read source is unavailable."),
        precondition_failed="read_surface_unavailable",
        suggestion="Verify the owning service URL and health before retrying this read.",
    )


def _composed_surface_status(
    *,
    snapshot_at: Optional[str] = None,
    available: bool = True,
    missing_message: Optional[str] = None,
) -> Dict[str, Any]:
    surface = dict(_surface_status())
    surface["source"] = "bff_composed"
    if available:
        return surface

    if surface.get("status") == "ok":
        surface["status"] = "degraded"
    if missing_message:
        surface["message"] = missing_message
    surface.setdefault(
        "staleness",
        {"served_from": "unverifiable", "last_known_at": snapshot_at or utc_now()},
    )
    return surface


_INCIDENT_SEVERITY_MAP = {
    "critical": "sev1",
    "high": "sev1",
    "medium": "sev2",
    "low": "sev3",
    "sev1": "sev1",
    "sev2": "sev2",
    "sev3": "sev3",
}

_KILL_SWITCH_STATUS_MAP = {
    "armed": "armed",
    "off": "armed",
    "normal": "armed",
    "triggered": "triggered",
    "guarded": "triggered",
    "risk_off": "triggered",
    "cooling_down": "cooling_down",
    "cooldown": "cooling_down",
    "paused": "cooling_down",
}

_ACTION_DRAWER_PRIMARY_ALLOWED_ACTIONS = {
    "canPause": True,
    "canRiskOff": True,
    "canLiquidateAll": False,
    "canHardRollback": False,
    "canIssueSafeMode": True,
}


def _incident_home_severity(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return _INCIDENT_SEVERITY_MAP.get(str(value).strip().lower(), str(value))


def _project_incident_home_item(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id"),
        "title": incident.get("title"),
        "severity": _incident_home_severity(incident.get("severity")),
        "status": incident.get("status"),
        "artifact_id": incident.get("artifact_id"),
        "opened_at": incident.get("opened_at") or incident.get("created_at"),
        "resolved_at": incident.get("resolved_at"),
    }


def _project_incident_detail_incident(incident: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "incident_id": incident.get("incident_id"),
        "title": incident.get("title"),
        "severity": _incident_home_severity(incident.get("severity")),
        "status": incident.get("status"),
        "artifact_id": incident.get("artifact_id"),
        "artifact_version": incident.get("artifact_version"),
        "runtime_id": incident.get("runtime_id"),
        "trace_id": incident.get("trace_id"),
        "opened_at": incident.get("opened_at") or incident.get("created_at"),
    }


def _project_affected_binding(
    binding: Dict[str, Any],
    incident: Dict[str, Any],
    runtime_binding: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_stage = (
        incident.get("deployment_stage")
        or binding.get("stage")
        or binding.get("deployment_stage")
        or (runtime_binding or {}).get("deployment_stage")
        or binding.get("allowed_deployment_scope")
    )
    stage = str(raw_stage or "").strip().lower()
    if stage not in {"paper", "live"}:
        stage = "paper"

    return {
        "binding_id": binding.get("id") or binding.get("binding_id"),
        "persona_id": binding.get("persona_id"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "stage": stage,
        "binding_status": binding.get("binding_status") or binding.get("status"),
    }


def _project_affected_bindings(
    incident: Dict[str, Any],
    runtime_binding: Optional[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], bool]:
    candidate_ids: List[str] = []
    for value in [
        incident.get("persona_capital_binding_id"),
        (runtime_binding or {}).get("persona_capital_binding_id"),
    ]:
        if value in (None, ""):
            continue
        string_value = str(value)
        if string_value not in candidate_ids:
            candidate_ids.append(string_value)

    affected_bindings: List[Dict[str, Any]] = []
    for binding_id in candidate_ids:
        binding = read_store.get_binding(binding_id)
        if not binding:
            continue
        affected_bindings.append(
            _project_affected_binding(binding, incident, runtime_binding)
        )

    return affected_bindings, bool(candidate_ids)


def _default_incident_allowed_actions() -> Dict[str, bool]:
    return {
        "canPause": False,
        "canRiskOff": False,
        "canLiquidateAll": False,
        "canHardRollback": False,
        "canIssueSafeMode": False,
        "canOpenActionDrawer": False,
    }


def _derive_incident_allowed_actions(
    identity: OperatorIdentity,
    incident: Dict[str, Any],
) -> Dict[str, bool]:
    actions = _default_incident_allowed_actions()
    incident_status = str(incident.get("status") or "").lower()
    runtime_id = incident.get("runtime_id")
    if incident_status not in {"open", "in_progress"} or not runtime_id:
        return actions

    if not {"operator", "admin"}.intersection(identity.roles):
        return actions

    actions["canPause"] = True
    actions["canRiskOff"] = True
    actions["canIssueSafeMode"] = True
    actions["canOpenActionDrawer"] = True
    return actions


def _decode_page_token(page_token: Optional[str]) -> int:
    if page_token in (None, ""):
        return 0
    try:
        offset = int(page_token)
    except (TypeError, ValueError) as exc:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid page_token",
            "page_token must be a non-negative integer offset",
        ) from exc
    if offset < 0:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid page_token",
            "page_token must be a non-negative integer offset",
        )
    return offset


def _page_slice(items: List[Dict[str, Any]], page_token: Optional[str], page_size: int) -> tuple[List[Dict[str, Any]], Optional[str]]:
    start = _decode_page_token(page_token)
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token


_RUNTIME_STATE_SORT_FIELDS = {"last_updated_at", "runtime_id", "deployment_stage", "status"}
_RUNTIME_STATE_SORT_ORDERS = {"asc", "desc"}
_HEALTH_GROUP_LABELS = {
    "runtime": "Runtime",
    "telemetry": "Telemetry",
    "incident": "Incident",
    "governance": "Governance",
    "kill_switch": "Kill Switch",
}
_HEALTH_SURFACE_ORDER = ("runtime", "telemetry", "incident", "governance", "kill_switch")
_INCIDENT_SEVERITY_ORDER = {"sev1": 3, "sev2": 2, "sev3": 1}
_GOVERNANCE_RISK_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_ALERT_SEVERITY_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1}
_ALERT_CATEGORY_ORDER = {"incident": 4, "kill_switch": 3, "governance": 2, "runtime": 1}
_SECONDARY_CONTROL_PATH_ADVISORY_TARGETS = (
    {
        "operation": "Health diagnostics",
        "channel": "admin_cli",
        "command": "pantheon admin health",
        "api_path": "GET /admin/health",
        "required_role": "operator",
        "requires_mfa": False,
    },
    {
        "operation": "Runtime status",
        "channel": "admin_cli",
        "command": "pantheon admin runtime status --runtime={runtime_id}",
        "api_path": "GET /admin/runtimes/{runtime_id}/status",
        "required_role": "operator",
        "requires_mfa": False,
    },
    {
        "operation": "Kill-switch status",
        "channel": "admin_cli",
        "command": "pantheon admin kill-switch status",
        "api_path": "GET /admin/kill-switch/status",
        "required_role": "operator",
        "requires_mfa": False,
    },
)
_RUNTIME_STATUS_ALERT_SEVERITY = {
    "failed": "critical",
    "error": "critical",
    "degraded": "high",
    "paused": "medium",
}
_TELEMETRY_DRAWDOWN_THRESHOLDS = (
    (0.10, "critical"),
    (0.05, "high"),
)
_TELEMETRY_FILL_RATE_THRESHOLDS = (
    (0.90, "critical"),
    (0.95, "high"),
)
_TELEMETRY_SLIPPAGE_THRESHOLDS = (
    (4.0, "critical"),
    (3.0, "high"),
)
_SECONDARY_CONTROL_PATH_RECOMMENDED_TARGETS = _SECONDARY_CONTROL_PATH_ADVISORY_TARGETS + (
    {
        "operation": "Runtime pause",
        "channel": "admin_cli",
        "command": "pantheon admin runtime pause --runtime={runtime_id}",
        "api_path": "POST /admin/runtimes/{runtime_id}/pause",
        "required_role": "admin",
        "requires_mfa": True,
    },
    {
        "operation": "Runtime rollback",
        "channel": "admin_cli",
        "command": "pantheon admin runtime rollback --runtime={runtime_id} --target={version}",
        "api_path": "POST /admin/runtimes/{runtime_id}/rollback",
        "required_role": "admin",
        "requires_mfa": True,
    },
    {
        "operation": "Kill-switch activation",
        "channel": "admin_cli",
        "command": "pantheon admin kill-switch activate --runtime={runtime_id}",
        "api_path": "POST /admin/kill-switch/activate",
        "required_role": "admin",
        "requires_mfa": True,
    },
)


def _split_csv_query(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    tokens = [token.strip() for token in value.split(",") if token.strip()]
    return tokens or None


def _project_runtime_state_telemetry_summary(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not summary:
        return None
    projected = {
        "window": summary.get("window"),
        "collected_at": summary.get("collected_at"),
        "metrics": {
            "pnl": summary.get("pnl"),
            "drawdown": summary.get("drawdown"),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "fill_rate": summary.get("fill_rate"),
            "avg_slippage_bps": summary.get("avg_slippage_bps"),
            "total_trades": summary.get("total_trades"),
        },
    }
    for key in (
        "runtime_binding_id",
        "binding_id",
        "deployment_stage",
        "state",
        "last_heartbeat_at",
        "last_event_at",
        "last_event_type",
        "engine_bridge_repo",
        "engine_bridge_commit",
        "engine_bridge_path",
        "runtime_adapter_version",
        "health_summary",
        "projection_source",
        "projection_updated_at",
        "staleness",
    ):
        if key in summary:
            projected[key] = summary.get(key)
    return projected


def _project_runtime_state_latest_rollback(rollbacks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rollbacks:
        return None
    latest = max(
        rollbacks,
        key=lambda rollback: (
            rollback.get("completed_at")
            or rollback.get("executed_at")
            or rollback.get("initiated_at")
            or ""
        ),
    )
    return {
        "rollback_id": latest.get("rollback_id") or latest.get("id"),
        "action_type": latest.get("action_type"),
        "status": latest.get("status"),
        "from_version": latest.get("from_version"),
        "to_version": latest.get("to_version"),
        "initiated_at": latest.get("initiated_at"),
        "completed_at": latest.get("completed_at") or latest.get("executed_at"),
    }


def _derive_runtime_state_last_updated_at(
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
    latest_rollback: Optional[Dict[str, Any]],
) -> Optional[str]:
    candidates = [
        binding.get("last_updated_at"),
        binding.get("updated_at"),
        binding.get("started_at"),
        binding.get("created_at"),
        (telemetry_summary or {}).get("last_heartbeat_at"),
        (telemetry_summary or {}).get("last_event_at"),
        (telemetry_summary or {}).get("collected_at"),
        (latest_rollback or {}).get("completed_at"),
        (latest_rollback or {}).get("initiated_at"),
    ]
    values = [candidate for candidate in candidates if candidate]
    if not values:
        return None
    return max(values)


def _project_operator_runtime_state_row(binding: Dict[str, Any]) -> Dict[str, Any]:
    runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
    telemetry_summary = _project_runtime_state_telemetry_summary(
        read_store.get_telemetry_summary(runtime_id)
    )
    rollbacks = read_store.get_rollbacks(runtime_id)
    latest_rollback = _project_runtime_state_latest_rollback(rollbacks)
    artifact_id = binding.get("artifact_id")
    artifact_version = binding.get("artifact_version") or binding.get("version")
    plan_id = binding.get("plan_id")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": (
            binding.get("runtime_binding_id")
            or binding.get("binding_id")
            or binding.get("id")
        ),
        "deployment_stage": binding.get("deployment_stage") or binding.get("deployment_mode"),
        "status": binding.get("status"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "plan_ref": (
            {
                "plan_id": plan_id,
                "href": _deployment_review_href(str(plan_id)),
            }
            if plan_id
            else None
        ),
        "artifact_ref": (
            {
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
            }
            if artifact_id or artifact_version
            else None
        ),
        "telemetry_summary": telemetry_summary,
        "rollback_summary": {
            "count": len(rollbacks),
            "latest": latest_rollback,
            "href": f"/api/v1/runtimes/{runtime_id}/rollbacks",
        },
        "last_updated_at": _derive_runtime_state_last_updated_at(
            binding,
            telemetry_summary,
            latest_rollback,
        ),
    }


def _sort_runtime_state_rows(
    rows: List[Dict[str, Any]],
    *,
    sort_by: str,
    sort_order: str,
) -> List[Dict[str, Any]]:
    reverse = sort_order == "desc"

    def _sort_key(row: Dict[str, Any]) -> tuple[str, str]:
        primary = row.get(sort_by)
        if primary is None:
            primary = ""
        return (str(primary), str(row.get("runtime_id") or ""))

    ordered_rows = sorted(rows, key=_sort_key, reverse=reverse)
    present = [row for row in ordered_rows if row.get(sort_by)]
    missing = [row for row in ordered_rows if not row.get(sort_by)]
    return present + missing


def _highest_ranked_value(
    values: List[Optional[str]],
    order: Dict[str, int],
) -> Optional[str]:
    best_value: Optional[str] = None
    best_rank = -1
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip().lower()
        rank = order.get(normalized)
        if rank is None:
            continue
        if rank > best_rank:
            best_rank = rank
            best_value = normalized
    return best_value


def _aggregate_group_surface(
    surface_key: str,
    source_surfaces: List[Dict[str, Any]],
    *,
    snapshot_at: str,
    unavailable_message: str,
    degraded_message: str,
) -> Dict[str, Any]:
    surface = _composed_surface_status(snapshot_at=snapshot_at, available=True)
    surface["source"] = "bff_composed"
    statuses = [entry.get("status", "ok") for entry in source_surfaces]
    if statuses and all(status == "ok" for status in statuses):
        return surface
    if statuses and all(status == "unavailable" for status in statuses):
        surface["status"] = "unavailable"
        surface["message"] = unavailable_message
        return surface
    surface["status"] = "degraded"
    surface["message"] = degraded_message
    return surface


def _project_health_surface_ref(surface_key: str, surface: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "surface_key": surface_key,
        "status": surface.get("status"),
        "source": surface.get("source"),
    }
    if surface.get("message"):
        payload["message"] = surface.get("message")
    return payload


def _build_runtime_health_group(snapshot_at: str) -> tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    bindings = read_store.list_runtime_bindings()
    runtime_roster_surface = _dataset_surface_status(
        "runtime_bindings",
        snapshot_at=snapshot_at,
    )
    by_stage: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for binding in bindings:
        stage = str(binding.get("deployment_stage") or binding.get("deployment_mode") or "unknown")
        status = str(binding.get("status") or "unknown")
        by_stage[stage] = by_stage.get(stage, 0) + 1
        by_status[status] = by_status.get(status, 0) + 1

    group_surface = _aggregate_group_surface(
        "runtime",
        [runtime_roster_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Runtime roster unavailable.",
        degraded_message="Runtime roster degraded or stale.",
    )
    if runtime_roster_surface.get("status") == "unavailable":
        summary = "Runtime roster unavailable."
    elif not bindings:
        summary = "No runtimes reported."
    else:
        summary = f"{len(bindings)} runtime(s) tracked across {len(by_stage)} stage(s)."

    group = {
        "group_id": "runtime",
        "label": _HEALTH_GROUP_LABELS["runtime"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "total_runtime_count": len(bindings),
            "by_stage": by_stage,
            "by_status": by_status,
        },
        "surface_refs": [
            _project_health_surface_ref("runtime_roster", runtime_roster_surface),
        ],
        "target_refs": [
            {
                "label": "Runtime State Board",
                "href": _OPERATOR_RUNTIME_STATE_ROUTE,
            },
        ],
    }
    return group, group_surface, bindings


def _build_telemetry_health_group(
    snapshot_at: str,
    bindings: List[Dict[str, Any]],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    telemetry_surface = _dataset_surface_status(
        "telemetry_summaries",
        snapshot_at=snapshot_at,
    )
    covered_runtime_count = 0
    latest_collected_at: Optional[str] = None
    for binding in bindings:
        runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
        if not runtime_id:
            continue
        summary = read_store.get_telemetry_summary(runtime_id)
        if not summary:
            continue
        covered_runtime_count += 1
        collected_at = summary.get("collected_at")
        if collected_at and (latest_collected_at is None or collected_at > latest_collected_at):
            latest_collected_at = collected_at

    total_runtime_count = len(bindings)
    missing_runtime_count = max(total_runtime_count - covered_runtime_count, 0)
    if (
        total_runtime_count > 0
        and missing_runtime_count > 0
        and telemetry_surface.get("status") == "ok"
    ):
        telemetry_surface["status"] = "degraded"
        telemetry_surface["message"] = (
            "Telemetry summary missing for one or more runtimes."
        )
        telemetry_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    group_surface = _aggregate_group_surface(
        "telemetry",
        [telemetry_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Telemetry summary unavailable.",
        degraded_message="Telemetry summary coverage is degraded or stale.",
    )
    if telemetry_surface.get("status") == "unavailable":
        summary = "Telemetry summary unavailable."
    elif total_runtime_count == 0:
        summary = "No runtimes available for telemetry coverage."
    elif missing_runtime_count == 0:
        summary = f"Telemetry coverage available for all {covered_runtime_count} runtime(s)."
    else:
        summary = (
            f"Telemetry coverage available for {covered_runtime_count} of "
            f"{total_runtime_count} runtime(s)."
        )

    group = {
        "group_id": "telemetry",
        "label": _HEALTH_GROUP_LABELS["telemetry"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "total_runtime_count": total_runtime_count,
            "covered_runtime_count": covered_runtime_count,
            "missing_runtime_count": missing_runtime_count,
            "latest_collected_at": latest_collected_at,
        },
        "surface_refs": [
            _project_health_surface_ref("telemetry_summary", telemetry_surface),
        ],
        "target_refs": [
            {
                "label": "Runtime State Board",
                "href": _OPERATOR_RUNTIME_STATE_ROUTE,
            },
        ],
    }
    return group, group_surface


def _build_incident_health_group(snapshot_at: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    incident_surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    incidents = read_store.list_incidents()
    active_incidents = [
        incident
        for incident in incidents
        if str(incident.get("status") or "").lower() in {"open", "in_progress"}
    ]
    highest_severity = _highest_ranked_value(
        [_incident_home_severity(incident.get("severity")) for incident in active_incidents],
        _INCIDENT_SEVERITY_ORDER,
    )
    open_count = sum(
        1
        for incident in active_incidents
        if str(incident.get("status") or "").lower() == "open"
    )
    in_progress_count = sum(
        1
        for incident in active_incidents
        if str(incident.get("status") or "").lower() == "in_progress"
    )

    group_surface = _aggregate_group_surface(
        "incident",
        [incident_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Incident surface unavailable.",
        degraded_message="Incident surface degraded or stale.",
    )
    if incident_surface.get("status") == "unavailable":
        summary = "Incident surface unavailable."
    elif not active_incidents:
        summary = "No active incidents."
    else:
        summary = (
            f"{len(active_incidents)} active incident(s); highest severity "
            f"{highest_severity or 'unknown'}."
        )

    group = {
        "group_id": "incident",
        "label": _HEALTH_GROUP_LABELS["incident"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "total_incident_count": len(incidents),
            "active_incident_count": len(active_incidents),
            "open_count": open_count,
            "in_progress_count": in_progress_count,
            "highest_severity": highest_severity,
        },
        "surface_refs": [
            _project_health_surface_ref("incident_list", incident_surface),
        ],
        "target_refs": [
            {
                "label": "Incident Home",
                "href": _OPERATOR_INCIDENT_HOME_ROUTE,
            },
        ],
    }
    return group, group_surface


def _build_governance_health_group(snapshot_at: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    review_queue_surface = _dataset_surface_status(
        "governance_review_queue_items",
        snapshot_at=snapshot_at,
    )
    approval_queue_surface = _dataset_surface_status(
        "approval_queue_items",
        snapshot_at=snapshot_at,
    )
    review_items = read_store.list_governance_review_queue_items()
    approval_items = read_store.list_approval_queue_items()
    highest_risk_level = _highest_ranked_value(
        [item.get("risk_level") for item in review_items + approval_items],
        _GOVERNANCE_RISK_ORDER,
    )

    group_surface = _aggregate_group_surface(
        "governance",
        [review_queue_surface, approval_queue_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Governance health unavailable.",
        degraded_message="Governance review or approval surfaces are degraded.",
    )
    total_pending_items = len(review_items) + len(approval_items)
    if group_surface.get("status") == "unavailable":
        summary = "Governance health unavailable."
    elif total_pending_items == 0:
        summary = "No pending governance reviews or approvals."
    else:
        summary = f"{total_pending_items} governance item(s) pending review or approval."

    group = {
        "group_id": "governance",
        "label": _HEALTH_GROUP_LABELS["governance"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "review_queue_count": len(review_items),
            "approval_queue_count": len(approval_items),
            "total_pending_items": total_pending_items,
            "highest_risk_level": highest_risk_level,
        },
        "surface_refs": [
            _project_health_surface_ref("review_queue", review_queue_surface),
            _project_health_surface_ref("approval_queue", approval_queue_surface),
        ],
        "target_refs": [
            {
                "label": "Governance Review Queue",
                "href": _GOVERNANCE_REVIEW_QUEUE_ROUTE,
            },
            {
                "label": "Governance Approval Queue",
                "href": _GOVERNANCE_APPROVAL_QUEUE_ROUTE,
            },
        ],
    }
    return group, group_surface


def _build_kill_switch_health_group(
    snapshot_at: str,
) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)
    kill_switch = (
        read_store.get_kill_switch_status()
        if kill_switch_surface.get("status") != "unavailable"
        else {}
    )
    safe_mode_status = kill_switch.get("safe_mode_status")
    kill_switch_status = kill_switch.get("status")

    group_surface = _aggregate_group_surface(
        "kill_switch",
        [kill_switch_surface],
        snapshot_at=snapshot_at,
        unavailable_message="Kill-switch and safe-mode state unavailable.",
        degraded_message="Kill-switch or safe-mode state is degraded or stale.",
    )
    if kill_switch_surface.get("status") == "unavailable":
        summary = "Kill-switch and safe-mode state unavailable."
    else:
        summary = (
            f"Kill-switch {kill_switch_status or 'unknown'}; "
            f"safe mode {safe_mode_status or 'unknown'}."
        )

    safe_mode_state = {
        "status": None if kill_switch_surface.get("status") == "unavailable" else safe_mode_status,
        "kill_switch_status": None if kill_switch_surface.get("status") == "unavailable" else kill_switch_status,
        "active": None if kill_switch_surface.get("status") == "unavailable" else kill_switch.get("active"),
        "last_confirmed_at": None if kill_switch_surface.get("status") == "unavailable" else kill_switch.get("last_confirmed_at"),
        "last_triggered_at": None if kill_switch_surface.get("status") == "unavailable" else kill_switch.get("last_triggered_at"),
        "secondary_path_available": None if kill_switch_surface.get("status") == "unavailable" else kill_switch.get("secondary_path_available"),
    }

    group = {
        "group_id": "kill_switch",
        "label": _HEALTH_GROUP_LABELS["kill_switch"],
        "status": group_surface.get("status"),
        "summary": summary,
        "details": {
            "kill_switch_status": safe_mode_state["kill_switch_status"],
            "safe_mode_status": safe_mode_state["status"],
            "active_command_count": (
                len(kill_switch.get("active_commands") or [])
                if kill_switch_surface.get("status") != "unavailable"
                else None
            ),
            "last_confirmed_at": safe_mode_state["last_confirmed_at"],
            "last_triggered_at": safe_mode_state["last_triggered_at"],
            "secondary_path_available": safe_mode_state["secondary_path_available"],
        },
        "surface_refs": [
            _project_health_surface_ref("kill_switch", kill_switch_surface),
        ],
        "target_refs": [
            {
                "label": "Health Status Board",
                "href": _OPERATOR_HEALTH_STATUS_ROUTE,
            },
        ],
    }
    return group, group_surface, safe_mode_state


def _build_secondary_control_path(
    *,
    overall_status: str,
    safe_mode_state: Dict[str, Any],
) -> Dict[str, Any]:
    safe_mode_status = str(safe_mode_state.get("status") or "").lower()
    kill_switch_status = str(safe_mode_state.get("kill_switch_status") or "").lower()
    safe_mode_active = safe_mode_status not in {"", "off", "released", "none", "null"}
    if overall_status == "ok" and not safe_mode_active and kill_switch_status not in {"triggered", "cooling_down"}:
        return {
            "mode": "hidden",
            "reason": None,
            "targets": [],
        }

    if overall_status == "unavailable" or safe_mode_active or kill_switch_status in {"triggered", "cooling_down"}:
        mode = "recommended"
        reason = (
            "One or more critical health groups are unavailable or safe mode is active. "
            "Use the secondary control path for verification or intervention."
        )
        targets = _SECONDARY_CONTROL_PATH_RECOMMENDED_TARGETS
    else:
        mode = "advisory"
        reason = (
            "Some health groups are degraded. Use the secondary control path to verify "
            "current control-plane state before critical decisions."
        )
        targets = _SECONDARY_CONTROL_PATH_ADVISORY_TARGETS

    return {
        "mode": mode,
        "reason": reason,
        "targets": json.loads(json.dumps(list(targets))),
    }


def _health_status_headline(overall_status: str, safe_mode_state: Dict[str, Any]) -> str:
    safe_mode_status = str(safe_mode_state.get("status") or "").lower()
    kill_switch_status = str(safe_mode_state.get("kill_switch_status") or "").lower()
    if safe_mode_status not in {"", "off", "released", "none", "null"}:
        return "Safe mode active"
    if kill_switch_status == "cooling_down":
        return "Kill-switch cooling down"
    if kill_switch_status == "triggered":
        return "Kill-switch triggered"
    if overall_status == "ok":
        return "Control plane healthy"
    if overall_status == "degraded":
        return "Some services degraded"
    return "Control plane health unavailable"


def _alert_target_ref(
    *,
    surface_id: str,
    label: str,
    href: str,
    target_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "surface_id": surface_id,
        "label": label,
        "href": href,
    }
    if target_id not in (None, ""):
        payload["target_id"] = target_id
    return payload


def _max_alert_severity(values: List[Optional[str]]) -> Optional[str]:
    return _highest_ranked_value(values, _ALERT_SEVERITY_ORDER)


def _alert_sort_key(alert: Dict[str, Any]) -> tuple[str, int, int, str]:
    severity = str(alert.get("severity") or "").lower()
    category = str(alert.get("category") or "").lower()
    return (
        str(alert.get("raised_at") or ""),
        _ALERT_SEVERITY_ORDER.get(severity, 0),
        _ALERT_CATEGORY_ORDER.get(category, 0),
        str(alert.get("alert_id") or ""),
    )


def _alert_severity_for_incident(incident: Dict[str, Any]) -> str:
    normalized = _incident_home_severity(incident.get("severity"))
    if normalized == "sev1":
        return "critical"
    if normalized == "sev2":
        return "high"
    return "medium"


def _alert_severity_for_risk_level(
    risk_level: Optional[str],
    *,
    elevated: bool = False,
) -> str:
    mapping = {
        "critical": "critical",
        "high": "high",
        "medium": "medium",
        "low": "low",
    }
    severity = mapping.get(str(risk_level or "").strip().lower(), "medium")
    if elevated and _ALERT_SEVERITY_ORDER.get(severity, 0) < _ALERT_SEVERITY_ORDER["high"]:
        return "high"
    return severity


def _build_incident_alerts(snapshot_at: str) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    incident_surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    if incident_surface.get("status") == "unavailable":
        return [], incident_surface

    alerts: List[Dict[str, Any]] = []
    incidents = read_store.list_incidents()
    for incident in incidents:
        incident_status = str(incident.get("status") or "").lower()
        if incident_status not in {"open", "in_progress"}:
            continue
        incident_id = str(incident.get("incident_id") or "")
        severity = _alert_severity_for_incident(incident)
        title = str(incident.get("title") or incident_id or "Unnamed incident")
        status_prefix = "Active" if incident_status == "open" else "In-progress"
        alerts.append(
            {
                "alert_id": f"alert-incident-{incident_id}",
                "severity": severity,
                "category": "incident",
                "raised_at": incident.get("opened_at") or incident.get("created_at") or snapshot_at,
                "summary": f"{status_prefix} incident: {title}.",
                "target_ref": _alert_target_ref(
                    surface_id="PKT-002",
                    label="Open incident response",
                    href=_incident_detail_href(incident_id),
                    target_id=incident_id,
                ),
            }
        )
    return alerts, incident_surface


def _build_governance_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    review_queue_surface = _dataset_surface_status(
        "governance_review_queue_items",
        snapshot_at=snapshot_at,
    )
    approval_queue_surface = _dataset_surface_status(
        "approval_queue_items",
        snapshot_at=snapshot_at,
    )
    alerts: List[Dict[str, Any]] = []

    if review_queue_surface.get("status") != "unavailable":
        for item in read_store.list_governance_review_queue_items():
            item_id = str(item.get("item_id") or "")
            status = str(item.get("status") or "").lower()
            if status not in {"pending", "in_review", "escalated"}:
                continue
            severity = _alert_severity_for_risk_level(
                item.get("risk_level"),
                elevated=status == "escalated",
            )
            item_type = str(item.get("item_type") or "Governance item")
            if status == "escalated":
                summary = f"Escalated governance review: {item_type} {item_id}."
            elif status == "in_review":
                summary = f"Governance review in progress: {item_type} {item_id}."
            else:
                summary = f"Pending governance review: {item_type} {item_id}."
            alerts.append(
                {
                    "alert_id": f"alert-governance-review-{item_id}",
                    "severity": severity,
                    "category": "governance",
                    "raised_at": item.get("submitted_at") or snapshot_at,
                    "summary": summary,
                    "target_ref": _alert_target_ref(
                        surface_id="PKT-001",
                        label="Open governance review queue",
                        href=_GOVERNANCE_REVIEW_QUEUE_ROUTE,
                        target_id=item_id,
                    ),
                }
            )

    if approval_queue_surface.get("status") != "unavailable":
        for item in read_store.list_approval_queue_items():
            decision_id = str(item.get("decision_id") or "")
            decision_state = str(item.get("decision_state") or "").lower()
            if decision_state not in {"pending", "in_review"}:
                continue
            severity = _alert_severity_for_risk_level(
                item.get("risk_level"),
                elevated=decision_state == "in_review",
            )
            decision_type = str(item.get("decision_type") or "Approval item")
            if decision_state == "in_review":
                summary = f"Approval decision in review: {decision_type} {decision_id}."
            else:
                summary = f"Approval required: {decision_type} {decision_id}."
            alerts.append(
                {
                    "alert_id": f"alert-approval-{decision_id}",
                    "severity": severity,
                    "category": "governance",
                    "raised_at": item.get("submitted_at") or snapshot_at,
                    "summary": summary,
                    "target_ref": _alert_target_ref(
                        surface_id="GV-02",
                        label="Open approval queue",
                        href=_GOVERNANCE_APPROVAL_QUEUE_ROUTE,
                        target_id=decision_id,
                    ),
                }
            )

    return alerts, {
        "review_queue": review_queue_surface,
        "approval_queue": approval_queue_surface,
    }


def _build_kill_switch_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)
    if kill_switch_surface.get("status") == "unavailable":
        return [], kill_switch_surface, {}

    kill_switch = read_store.get_kill_switch_status()
    safe_mode_status = str(kill_switch.get("safe_mode_status") or "").lower()
    kill_switch_status = str(kill_switch.get("status") or "").lower()
    safe_mode_active = safe_mode_status not in {"", "off", "released", "none", "null"}
    alerts: List[Dict[str, Any]] = []

    if kill_switch.get("active") or kill_switch_status == "triggered":
        severity = "critical"
        summary = "Kill-switch active; operator intervention is required."
    elif kill_switch_status == "cooling_down":
        severity = "high"
        summary = "Kill-switch cooling down; verify runtime stability before resuming operations."
    elif safe_mode_active:
        severity = "high"
        summary = f"Safe mode active ({safe_mode_status}); use the health board to verify current restrictions."
    else:
        return [], kill_switch_surface, kill_switch

    alerts.append(
        {
            "alert_id": "alert-kill-switch-state",
            "severity": severity,
            "category": "kill_switch",
            "raised_at": kill_switch.get("last_triggered_at")
            or kill_switch.get("last_confirmed_at")
            or snapshot_at,
            "summary": summary,
            "target_ref": _alert_target_ref(
                surface_id="OC-03",
                label="Open health status board",
                href=_OPERATOR_HEALTH_STATUS_ROUTE,
                target_id=kill_switch_status or safe_mode_status or "kill-switch",
            ),
        }
    )
    return alerts, kill_switch_surface, kill_switch


def _runtime_anomaly_reasons(
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
) -> tuple[List[str], Optional[str]]:
    reasons: List[str] = []
    severities: List[Optional[str]] = []

    runtime_status = str(binding.get("status") or "").lower()
    runtime_status_severity = _RUNTIME_STATUS_ALERT_SEVERITY.get(runtime_status)
    if runtime_status_severity:
        severities.append(runtime_status_severity)
        reasons.append(f"runtime status is {runtime_status}")

    if telemetry_summary:
        drawdown = telemetry_summary.get("drawdown")
        if isinstance(drawdown, (int, float)):
            for threshold, severity in _TELEMETRY_DRAWDOWN_THRESHOLDS:
                if drawdown >= threshold:
                    severities.append(severity)
                    reasons.append(f"drawdown is {drawdown:.3f}")
                    break

        fill_rate = telemetry_summary.get("fill_rate")
        if isinstance(fill_rate, (int, float)):
            for threshold, severity in _TELEMETRY_FILL_RATE_THRESHOLDS:
                if fill_rate < threshold:
                    severities.append(severity)
                    reasons.append(f"fill rate dropped to {fill_rate:.2f}")
                    break

        avg_slippage_bps = telemetry_summary.get("avg_slippage_bps")
        if isinstance(avg_slippage_bps, (int, float)):
            for threshold, severity in _TELEMETRY_SLIPPAGE_THRESHOLDS:
                if avg_slippage_bps >= threshold:
                    severities.append(severity)
                    reasons.append(f"average slippage reached {avg_slippage_bps:.1f} bps")
                    break

    return reasons, _max_alert_severity(severities)


def _build_runtime_alerts(
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    runtime_roster_surface = _dataset_surface_status(
        "runtime_bindings",
        snapshot_at=snapshot_at,
    )
    telemetry_surface = _dataset_surface_status(
        "telemetry_summaries",
        snapshot_at=snapshot_at,
    )
    if runtime_roster_surface.get("status") == "unavailable":
        return [], {
            "runtime_roster": runtime_roster_surface,
            "telemetry_summary": telemetry_surface,
        }

    alerts: List[Dict[str, Any]] = []
    bindings = read_store.list_runtime_bindings()
    missing_telemetry = False
    for binding in bindings:
        runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
        telemetry_summary = None
        if runtime_id and telemetry_surface.get("status") != "unavailable":
            telemetry_summary = read_store.get_telemetry_summary(runtime_id)
            if telemetry_summary is None:
                missing_telemetry = True
        reasons, severity = _runtime_anomaly_reasons(binding, telemetry_summary)
        if not reasons or not severity:
            continue
        alerts.append(
            {
                "alert_id": f"alert-runtime-{runtime_id}",
                "severity": severity,
                "category": "runtime",
                "raised_at": (telemetry_summary or {}).get("collected_at")
                or binding.get("updated_at")
                or binding.get("last_updated_at")
                or binding.get("started_at")
                or snapshot_at,
                "summary": f"Runtime {runtime_id} anomaly: {'; '.join(reasons[:2])}.",
                "target_ref": _alert_target_ref(
                    surface_id="OC-04",
                    label="Open runtime state board",
                    href=_OPERATOR_RUNTIME_STATE_ROUTE,
                    target_id=runtime_id,
                ),
            }
        )

    if bindings and missing_telemetry and telemetry_surface.get("status") == "ok":
        telemetry_surface["status"] = "degraded"
        telemetry_surface["message"] = "Telemetry summary missing for one or more runtimes."
        telemetry_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    return alerts, {
        "runtime_roster": runtime_roster_surface,
        "telemetry_summary": telemetry_surface,
    }


def _build_alert_summary(alerts: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_severity = {key: 0 for key in _ALERT_SEVERITY_ORDER}
    by_category = {key: 0 for key in _ALERT_CATEGORY_ORDER}
    for alert in alerts:
        severity = str(alert.get("severity") or "").lower()
        category = str(alert.get("category") or "").lower()
        if severity in by_severity:
            by_severity[severity] += 1
        if category in by_category:
            by_category[category] += 1
    return {
        "total_active": len(alerts),
        "highest_severity": _max_alert_severity(
            [str(alert.get("severity") or "").lower() for alert in alerts]
        ),
        "by_severity": by_severity,
        "by_category": by_category,
    }


def _build_operator_alerts_payload(snapshot_at: str) -> Dict[str, Any]:
    incident_alerts, incident_surface = _build_incident_alerts(snapshot_at)
    governance_alerts, governance_surfaces = _build_governance_alerts(snapshot_at)
    kill_switch_alerts, kill_switch_surface, _ = _build_kill_switch_alerts(snapshot_at)
    runtime_alerts, runtime_surfaces = _build_runtime_alerts(snapshot_at)

    source_surfaces = [
        incident_surface,
        governance_surfaces["review_queue"],
        governance_surfaces["approval_queue"],
        kill_switch_surface,
        runtime_surfaces["runtime_roster"],
        runtime_surfaces["telemetry_summary"],
    ]
    alerts_surface = _aggregate_group_surface(
        "alerts",
        source_surfaces,
        snapshot_at=snapshot_at,
        unavailable_message="Operator alert feed unavailable.",
        degraded_message="Operator alert feed is available, but one or more contributing surfaces are degraded.",
    )

    alerts = sorted(
        incident_alerts + governance_alerts + kill_switch_alerts + runtime_alerts,
        key=_alert_sort_key,
        reverse=True,
    )
    if alerts_surface.get("status") == "unavailable":
        alerts = []

    meta = _snapshot_meta(snapshot_at)
    meta["acknowledgement_supported"] = False
    meta["surfaces"] = {
        "alerts": alerts_surface,
        "incident_feed": incident_surface,
        "review_queue": governance_surfaces["review_queue"],
        "approval_queue": governance_surfaces["approval_queue"],
        "kill_switch": kill_switch_surface,
        "runtime_roster": runtime_surfaces["runtime_roster"],
        "telemetry_summary": runtime_surfaces["telemetry_summary"],
    }
    return {
        "alerts": alerts,
        "summary": _build_alert_summary(alerts),
        "meta": meta,
    }


def _build_operator_health_status_payload(snapshot_at: str) -> Dict[str, Any]:
    runtime_group, runtime_surface, runtime_bindings = _build_runtime_health_group(snapshot_at)
    telemetry_group, telemetry_surface = _build_telemetry_health_group(
        snapshot_at,
        runtime_bindings,
    )
    incident_group, incident_surface = _build_incident_health_group(snapshot_at)
    governance_group, governance_surface = _build_governance_health_group(snapshot_at)
    kill_switch_group, kill_switch_surface, safe_mode_state = _build_kill_switch_health_group(
        snapshot_at
    )

    group_surfaces = {
        "runtime": runtime_surface,
        "telemetry": telemetry_surface,
        "incident": incident_surface,
        "governance": governance_surface,
        "kill_switch": kill_switch_surface,
    }
    overall_surface = _aggregate_group_surface(
        "health_status",
        list(group_surfaces.values()),
        snapshot_at=snapshot_at,
        unavailable_message="All health groups are unavailable.",
        degraded_message="One or more health groups are degraded or unavailable.",
    )
    overall_status = overall_surface.get("status", "ok")

    group_counts = {
        "ok": sum(1 for surface in group_surfaces.values() if surface.get("status") == "ok"),
        "degraded": sum(
            1 for surface in group_surfaces.values() if surface.get("status") == "degraded"
        ),
        "unavailable": sum(
            1 for surface in group_surfaces.values() if surface.get("status") == "unavailable"
        ),
    }
    secondary_control_path = _build_secondary_control_path(
        overall_status=overall_status,
        safe_mode_state=safe_mode_state,
    )

    if overall_status == "ok":
        message = "All health groups are responding normally."
    elif overall_status == "degraded":
        message = (
            f"{group_counts['degraded'] + group_counts['unavailable']} of "
            f"{len(group_surfaces)} health groups need attention."
        )
    else:
        message = "Primary health surfaces are unavailable; rely on the secondary control path."

    groups = [
        runtime_group,
        telemetry_group,
        incident_group,
        governance_group,
        kill_switch_group,
    ]
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "health_status": overall_surface,
        **group_surfaces,
    }
    return {
        "overall_status": overall_status,
        "headline": _health_status_headline(overall_status, safe_mode_state),
        "message": message,
        "group_counts": group_counts,
        "safe_mode_state": safe_mode_state,
        "secondary_control_path": secondary_control_path,
        "groups": groups,
        "meta": meta,
    }


def _build_home_card(
    *,
    card_id: str,
    label: str,
    status: str,
    summary: str,
    details: Dict[str, Any],
    target_refs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "card_id": card_id,
        "label": label,
        "status": status,
        "summary": summary,
        "details": details,
        "target_refs": target_refs,
    }


def _build_operator_home_payload(snapshot_at: str) -> Dict[str, Any]:
    alerts_payload = _build_operator_alerts_payload(snapshot_at)
    health_payload = _build_operator_health_status_payload(snapshot_at)
    groups_by_id = {
        str(group.get("group_id") or ""): group
        for group in health_payload["groups"]
    }
    alert_summary = alerts_payload["summary"]
    safe_mode_state = health_payload["safe_mode_state"]

    alert_surface = alerts_payload["meta"]["surfaces"]["alerts"]
    incident_group = groups_by_id["incident"]
    governance_group = groups_by_id["governance"]
    runtime_group = groups_by_id["runtime"]
    telemetry_group = groups_by_id["telemetry"]

    runtime_card_status = _aggregate_group_surface(
        "operator_home_runtime",
        [
            health_payload["meta"]["surfaces"]["runtime"],
            health_payload["meta"]["surfaces"]["telemetry"],
        ],
        snapshot_at=snapshot_at,
        unavailable_message="Runtime overview unavailable.",
        degraded_message="Runtime or telemetry coverage is degraded.",
    )["status"]

    cards = [
        _build_home_card(
            card_id="alerts",
            label="Alerts",
            status=alert_surface.get("status", "ok"),
            summary=(
                "Operator alert feed unavailable."
                if alert_surface.get("status") == "unavailable"
                else (
                    "No active operator alerts."
                    if alert_summary["total_active"] == 0
                    else f"{alert_summary['total_active']} active alert(s); highest severity {alert_summary['highest_severity']}."
                )
            ),
            details=alert_summary,
            target_refs=[
                _alert_target_ref(
                    surface_id="OC-02",
                    label="Open alerts rail",
                    href=_OPERATOR_ALERTS_ROUTE,
                )
            ],
        ),
        _build_home_card(
            card_id="incidents",
            label="Incidents",
            status=str(incident_group.get("status") or "ok"),
            summary=str(incident_group.get("summary") or "Incident summary unavailable."),
            details=dict(incident_group.get("details") or {}),
            target_refs=list(incident_group.get("target_refs") or []),
        ),
        _build_home_card(
            card_id="governance",
            label="Governance",
            status=str(governance_group.get("status") or "ok"),
            summary=str(governance_group.get("summary") or "Governance summary unavailable."),
            details=dict(governance_group.get("details") or {}),
            target_refs=list(governance_group.get("target_refs") or []),
        ),
        _build_home_card(
            card_id="runtime",
            label="Runtime",
            status=runtime_card_status,
            summary=(
                "Runtime overview unavailable."
                if runtime_card_status == "unavailable"
                else (
                    str(runtime_group.get("summary") or "Runtime overview unavailable.")
                    if telemetry_group.get("status") == "ok"
                    else f"{runtime_group.get('summary')} Telemetry: {telemetry_group.get('summary')}"
                )
            ),
            details={
                "runtime": dict(runtime_group.get("details") or {}),
                "telemetry": dict(telemetry_group.get("details") or {}),
            },
            target_refs=[
                _alert_target_ref(
                    surface_id="OC-04",
                    label="Open runtime state board",
                    href=_OPERATOR_RUNTIME_STATE_ROUTE,
                )
            ],
        ),
        _build_home_card(
            card_id="health",
            label="Health",
            status=str(health_payload.get("overall_status") or "ok"),
            summary=str(health_payload.get("message") or "Health summary unavailable."),
            details={
                "headline": health_payload.get("headline"),
                "group_counts": dict(health_payload.get("group_counts") or {}),
                "safe_mode_state": dict(safe_mode_state),
            },
            target_refs=[
                _alert_target_ref(
                    surface_id="OC-03",
                    label="Open health status board",
                    href=_OPERATOR_HEALTH_STATUS_ROUTE,
                )
            ],
        ),
    ]

    safe_mode_status = str(safe_mode_state.get("status") or "").lower()
    kill_switch_status = str(safe_mode_state.get("kill_switch_status") or "").lower()
    safe_mode_active = safe_mode_status not in {"", "off", "released", "none", "null"}

    home_surface = _aggregate_group_surface(
        "operator_home",
        [
            alert_surface,
            health_payload["meta"]["surfaces"]["health_status"],
        ],
        snapshot_at=snapshot_at,
        unavailable_message="Operator home summary unavailable.",
        degraded_message="Operator home summary is degraded because alerts or health inputs are degraded.",
    )
    overall_status = home_surface.get("status", "ok")

    if safe_mode_active or kill_switch_status in {"triggered", "cooling_down"}:
        headline = "Operator attention required"
        message = "Safe mode or kill-switch activity requires immediate review."
    elif overall_status == "unavailable":
        headline = "Operator home unavailable"
        message = "Primary operator summary surfaces are unavailable."
    elif alert_summary["total_active"] > 0:
        headline = f"{alert_summary['total_active']} active operator alert(s)"
        message = "Review the alerts rail before making deployment or runtime decisions."
    elif overall_status == "degraded":
        headline = "Operator home degraded"
        message = "One or more operator summary surfaces are degraded."
    else:
        headline = "Operator console stable"
        message = "No active incidents, governance bottlenecks, or runtime alerts require attention."

    escalation_shortcuts: List[Dict[str, Any]] = []
    if alert_summary["total_active"] > 0:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-alerts-rail",
                "label": "Open alerts rail",
                "reason": "There are active operator alerts that need triage.",
                "href": _OPERATOR_ALERTS_ROUTE,
                "priority": "high",
            }
        )
    if int((incident_group.get("details") or {}).get("active_incident_count") or 0) > 0:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-incident-home",
                "label": "Open incident home",
                "reason": "Active incidents are open and may require response.",
                "href": _OPERATOR_INCIDENT_HOME_ROUTE,
                "priority": "high",
            }
        )
    if health_payload.get("overall_status") != "ok" or safe_mode_active:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-health-status",
                "label": "Open health status board",
                "reason": "Health status or safe-mode state needs verification.",
                "href": _OPERATOR_HEALTH_STATUS_ROUTE,
                "priority": "high" if safe_mode_active else "medium",
            }
        )
    if int((governance_group.get("details") or {}).get("total_pending_items") or 0) > 0:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-approval-queue",
                "label": "Open approval queue",
                "reason": "Pending governance items may block execution changes.",
                "href": _GOVERNANCE_APPROVAL_QUEUE_ROUTE,
                "priority": "medium",
            }
        )
    if int((runtime_group.get("details") or {}).get("total_runtime_count") or 0) > 0:
        escalation_shortcuts.append(
            {
                "shortcut_id": "open-runtime-state",
                "label": "Open runtime state board",
                "reason": "Inspect current runtime and telemetry status.",
                "href": _OPERATOR_RUNTIME_STATE_ROUTE,
                "priority": "medium",
            }
        )

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "operator_home": home_surface,
        "alerts": alert_surface,
        "health_status": health_payload["meta"]["surfaces"]["health_status"],
        "incident": health_payload["meta"]["surfaces"]["incident"],
        "governance": health_payload["meta"]["surfaces"]["governance"],
        "runtime": health_payload["meta"]["surfaces"]["runtime"],
        "telemetry": health_payload["meta"]["surfaces"]["telemetry"],
        "kill_switch": health_payload["meta"]["surfaces"]["kill_switch"],
    }
    return {
        "overall_status": overall_status,
        "headline": headline,
        "message": message,
        "safe_mode_state": safe_mode_state,
        "cards": cards,
        "escalation_shortcuts": escalation_shortcuts,
        "meta": meta,
    }


def _unavailable_surface(
    dataset: str,
    *,
    snapshot_at: str,
    message: str,
) -> Dict[str, Any]:
    surface = _dataset_surface_status(dataset, snapshot_at=snapshot_at)
    surface["status"] = "unavailable"
    surface["message"] = message
    surface.setdefault(
        "staleness",
        {"served_from": "unverifiable", "last_known_at": snapshot_at},
    )
    return surface


def _browser_href_for_drift_evidence_ref(
    ref: Dict[str, Any],
    *,
    plan_id: Optional[str],
    primary_incident_id: Optional[str],
) -> Optional[str]:
    ref_type = str(ref.get("type") or "").strip().lower()
    ref_id = str(ref.get("ref_id") or "").strip()
    if ref_type == "approvaldecision":
        return _GOVERNANCE_APPROVAL_QUEUE_ROUTE
    if ref_type == "incidentcase" and ref_id:
        return _incident_detail_href(ref_id)
    if ref_type == "evolutiondecision" and primary_incident_id:
        return _post_incident_review_href(primary_incident_id)
    if ref_type == "drift_report":
        return None
    if plan_id and str(ref.get("href") or "").startswith("/api/v1/operator/deployment-review/"):
        return _deployment_review_href(plan_id)
    return ref.get("href")


def _normalize_drift_evidence_refs(
    refs: List[Dict[str, Any]],
    *,
    plan_id: Optional[str],
    primary_incident_id: Optional[str],
) -> List[Dict[str, Any]]:
    normalized = json.loads(json.dumps(refs))
    for ref in normalized:
        if not isinstance(ref, dict):
            continue
        ref["href"] = _browser_href_for_drift_evidence_ref(
            ref,
            plan_id=plan_id,
            primary_incident_id=primary_incident_id,
        )
    return normalized


def _normalize_drift_recommended_actions(
    actions: List[Dict[str, Any]],
    *,
    plan_id: Optional[str],
    primary_incident_id: Optional[str],
) -> List[Dict[str, Any]]:
    normalized = json.loads(json.dumps(actions))
    for action in normalized:
        if not isinstance(action, dict):
            continue
        target_ref = action.get("target_ref")
        if not isinstance(target_ref, dict):
            continue
        surface_id = str(target_ref.get("surface_id") or "").strip()
        target_id = str(target_ref.get("target_id") or "").strip()
        if surface_id == "PKT-001" and plan_id:
            target_ref["href"] = _deployment_review_href(plan_id)
        elif surface_id == "PKT-002" and target_id:
            target_ref["href"] = _incident_detail_href(target_id)
        elif surface_id == "PKT-003" and target_id:
            target_ref["href"] = _post_incident_review_href(target_id)
    return normalized


def _build_operator_paper_live_drift_payload(
    runtime_id: str,
    snapshot_at: str,
) -> Dict[str, Any]:
    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    report = read_store.get_paper_live_drift_report(runtime_id)
    plan_id = None
    artifact_id = None
    artifact_version = None

    if runtime_binding:
        plan_id = runtime_binding.get("plan_id")
        artifact_id = runtime_binding.get("artifact_id")
        artifact_version = runtime_binding.get("artifact_version")
    if report:
        plan_id = report.get("plan_id") or plan_id
        artifact_id = report.get("artifact_id") or artifact_id
        artifact_version = report.get("artifact_version") or artifact_version

    plan = read_store.get_deployment_plan(plan_id) if plan_id else None
    approval_decision = (
        read_store.get_approval_decision(plan.get("approval_decision_id"))
        if plan
        else None
    )
    telemetry_summary = read_store.get_telemetry_summary(runtime_id)
    telemetry_performance = (
        read_store.get_telemetry_performance(str(artifact_id))
        if artifact_id
        else None
    )
    incidents = [
        incident
        for incident in read_store.list_incidents()
        if str(incident.get("runtime_id") or "") == runtime_id
        and str(incident.get("status") or "").lower() in {"open", "in_progress"}
    ]
    evolution_decisions = []
    for incident in incidents:
        evolution_decisions.extend(
            read_store.get_evolution_decisions_by_incident(
                str(incident.get("incident_id") or "")
            )
        )
    primary_incident_id = (
        str(incidents[0].get("incident_id") or "").strip() if incidents else None
    )

    report_surface = (
        _dataset_surface_status("paper_live_drift_reports", snapshot_at=snapshot_at)
        if report is not None
        else _unavailable_surface(
            "paper_live_drift_reports",
            snapshot_at=snapshot_at,
            message="Paper/live drift report unavailable for this runtime.",
        )
    )
    runtime_surface = (
        _dataset_surface_status(
            "runtime_bindings",
            snapshot_at=snapshot_at,
            has_data=runtime_binding is not None,
            missing_message="Runtime binding unavailable for this drift view.",
        )
        if runtime_binding is not None
        else _unavailable_surface(
            "runtime_bindings",
            snapshot_at=snapshot_at,
            message="Runtime binding unavailable for this drift view.",
        )
    )
    telemetry_surface = (
        _dataset_surface_status(
            "telemetry_summaries",
            snapshot_at=snapshot_at,
            has_data=telemetry_summary is not None,
            missing_message="Observed telemetry summary unavailable for this drift view.",
        )
        if telemetry_summary is not None
        else _unavailable_surface(
            "telemetry_summaries",
            snapshot_at=snapshot_at,
            message="Observed telemetry summary unavailable for this drift view.",
        )
    )
    performance_surface = (
        _dataset_surface_status(
            "telemetry_performance",
            snapshot_at=snapshot_at,
            has_data=telemetry_performance is not None,
            missing_message="Paper baseline performance unavailable for this drift view.",
        )
        if telemetry_performance is not None
        else _unavailable_surface(
            "telemetry_performance",
            snapshot_at=snapshot_at,
            message="Paper baseline performance unavailable for this drift view.",
        )
    )
    approval_surface = (
        _dataset_surface_status(
            "approval_decisions",
            snapshot_at=snapshot_at,
            has_data=approval_decision is not None,
            missing_message="Approval decision unavailable for this drift view.",
        )
        if approval_decision is not None
        else _unavailable_surface(
            "approval_decisions",
            snapshot_at=snapshot_at,
            message="Approval decision unavailable for this drift view.",
        )
    )
    incident_surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    evolution_surface = _dataset_surface_status(
        "evolution_decisions",
        snapshot_at=snapshot_at,
    )

    source_surfaces = [
        report_surface,
        runtime_surface,
        telemetry_surface,
        performance_surface,
        approval_surface,
        incident_surface,
        evolution_surface,
    ]
    paper_live_drift_surface = _aggregate_group_surface(
        "paper_live_drift",
        source_surfaces,
        snapshot_at=snapshot_at,
        unavailable_message="Paper/live drift view unavailable.",
        degraded_message="Paper/live drift view is available, but one or more supporting surfaces are degraded.",
    )
    if report is None:
        paper_live_drift_surface["status"] = "unavailable"
        paper_live_drift_surface["message"] = "Paper/live drift view unavailable."
        paper_live_drift_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    recommended_actions = []
    if report:
        recommended_actions = _normalize_drift_recommended_actions(
            report.get("recommended_actions") or [],
            plan_id=str(plan_id) if plan_id else None,
            primary_incident_id=primary_incident_id,
        )
    elif plan_id:
        recommended_actions = [
            {
                "action_id": "open-deployment-review",
                "label": "Open deployment review",
                "reason": "A drift report is not yet available; inspect the current deployment context first.",
                "target_ref": _alert_target_ref(
                    surface_id="PKT-001",
                    label="Open deployment review",
                    href=_deployment_review_href(str(plan_id)),
                    target_id=plan_id,
                ),
            }
        ]

    return {
        "runtime_id": runtime_id,
        "plan_ref": (
            {
                "plan_id": plan_id,
                "href": _deployment_plan_href(str(plan_id)),
            }
            if plan_id
            else None
        ),
        "artifact_ref": (
            {
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
            }
            if artifact_id or artifact_version
            else None
        ),
        "paper_baseline": json.loads(json.dumps((report or {}).get("paper_baseline")))
        if report
        else None,
        "observed_state": json.loads(json.dumps((report or {}).get("observed_state")))
        if report
        else None,
        "drift_groups": json.loads(json.dumps((report or {}).get("drift_groups") or [])),
        "threshold_evaluation": json.loads(
            json.dumps(
                (report or {}).get("threshold_evaluation")
                or {
                    "overall_status": "unavailable",
                    "summary": "Paper/live drift report unavailable for this runtime.",
                    "breached_metric_ids": [],
                }
            )
        ),
        "evidence_refs": _normalize_drift_evidence_refs(
            (report or {}).get("evidence_refs") or [],
            plan_id=str(plan_id) if plan_id else None,
            primary_incident_id=primary_incident_id,
        ),
        "recommended_actions": recommended_actions,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "paper_live_drift": paper_live_drift_surface,
                "drift_report": report_surface,
                "runtime_binding": runtime_surface,
                "telemetry_summary": telemetry_surface,
                "telemetry_performance": performance_surface,
                "approval_decision": approval_surface,
                "incident": incident_surface,
                "evolution": evolution_surface,
            },
            "supporting_counts": {
                "active_incident_count": len(incidents),
                "evolution_decision_count": len(evolution_decisions),
            },
        },
    }


def _snapshot_meta(snapshot_at: str) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    return meta


_RW01_ALLOWED_PRIORITIES = {"low", "normal", "high", "critical"}
_RW01_ALLOWED_STATUSES = {"open", "in_progress", "closed", "archived"}
_RW01_STATUS_TRANSITIONS = {
    "open": {"in_progress", "closed"},
    "in_progress": {"closed"},
    "closed": {"archived"},
    "archived": set(),
}
_RW02_ALLOWED_MATCH_TYPES = {"all", "ticket", "experiment", "artifact"}
_RW02_ALLOWED_ADAPTER_STATES = {"fresh", "stale", "degraded", "unavailable"}
_RW03_ALLOWED_STATUSES = {"queued", "running", "completed", "failed"}
_RW03_ALLOWED_DATE_RANGES = {"24h", "7d", "30d", "90d"}
_RW05_ALLOWED_STATUSES = {"pending", "sealed", "superseded", "failed"}
_EW04_ALLOWED_SURFACE_STATES = {"fresh", "stale", "unavailable"}
_KW02_ATTACHMENT_TYPES = {"research_ticket", "persona", "strategy_spec", "free_standing"}
_KW02_ATTACHMENT_ID_PATTERNS = {
    "research_ticket": re.compile(r"^tkt-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "persona": re.compile(r"^persona-[A-Za-z0-9][A-Za-z0-9_-]*$"),
    "strategy_spec": re.compile(r"^strat-[A-Za-z0-9-]+$"),
}
_KW02_MEMORY_ANCHOR_PATTERN = re.compile(
    r"^mem-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_KW03_LINKED_ENTITY_TYPES = {
    "memory_entry",
    "research_note",
    "insight_card",
    "strategy_spec",
    "experiment",
    "artifact",
}
_KW03_LINK_TYPES = {
    "supporting_evidence",
    "counter_evidence",
    "citation",
    "provenance",
    "corroboration",
}
_KW03_CREDIBILITY_TIERS = {"primary", "secondary", "tertiary", "unverified"}
_KW04_STATUSES = {"active", "superseded", "archived", "all"}
_KW04_LINKED_ENTITY_TYPES = {
    "memory_entry",
    "research_note",
    "evidence_ref",
    "strategy_spec",
    "experiment",
}
_KW04_RECENCY_VALUES = {"7d", "30d", "90d", "all"}
_KW05_LIFECYCLE_STATES = {"draft", "candidate", "approved", "retired", "all"}


def _ew04_inspiration_surface_state(
    projection: Optional[Dict[str, Any]],
    *,
    artifact_exists: bool,
) -> str:
    source = read_store.dataset_source("inspiration_graphs")
    base_status = _surface_status().get("status")

    explicit_state = (
        projection.get("meta", {})
        .get("surfaces", {})
        .get("inspiration")
        if projection
        else None
    )
    explicit_state = str(explicit_state or "").strip().lower()
    if explicit_state in _EW04_ALLOWED_SURFACE_STATES:
        return explicit_state

    if source == "missing" or base_status == "unavailable":
        return "unavailable"
    if source == "local_snapshot" or base_status == "degraded":
        return "stale"
    if artifact_exists:
        return "fresh"
    return "unavailable"


def _ew04_inspiration_payload(
    artifact_id: str,
    projection: Optional[Dict[str, Any]],
    *,
    snapshot_at: str,
    artifact_exists: bool,
) -> Dict[str, Any]:
    if projection:
        payload = json.loads(json.dumps(projection))
    else:
        payload = {
            "artifact_id": artifact_id,
            "inspiration_edges": [],
            "strategy_tags": [],
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {},
            },
        }

    payload["artifact_id"] = artifact_id
    payload["inspiration_edges"] = list(payload.get("inspiration_edges") or [])
    if "strategy_tags" in payload:
        payload["strategy_tags"] = list(payload.get("strategy_tags") or [])
    else:
        payload["strategy_tags"] = []

    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        payload["meta"] = meta
    meta["snapshot_at"] = str(meta.get("snapshot_at") or snapshot_at)
    surfaces = meta.get("surfaces")
    if not isinstance(surfaces, dict):
        surfaces = {}
        meta["surfaces"] = surfaces
    surfaces["inspiration"] = _ew04_inspiration_surface_state(
        projection,
        artifact_exists=artifact_exists,
    )
    return payload


def _ew04_inspiration_projection_from_lineage_edges(artifact_id: str) -> Optional[Dict[str, Any]]:
    source = read_store.dataset_source("lineage_edges")
    if source == "missing":
        return None
    lineage_edges = read_store.list_lineage_edges(artifact_id=artifact_id)
    if not lineage_edges:
        return None
    surface_state = "fresh"
    if source in {"missing", "local_snapshot"} or _surface_status().get("status") == "degraded":
        surface_state = "stale"

    inspiration_edges: List[Dict[str, Any]] = []
    strategy_tags = set()
    for edge in lineage_edges:
        from_artifact_id = str(edge.get("from_artifact_id") or "").strip()
        to_artifact_id = str(edge.get("to_artifact_id") or "").strip()
        source_artifact_id = from_artifact_id if to_artifact_id == artifact_id else to_artifact_id
        relationship_type = str(edge.get("edge_type") or edge.get("relationship") or "").strip()
        if not source_artifact_id or not relationship_type:
            continue
        strategy_id = str(edge.get("strategy_id") or "").strip()
        if strategy_id:
            strategy_tags.add(strategy_id)
        inspiration_edges.append(
            {
                "lineage_edge_id": edge.get("id"),
                "source_artifact_id": source_artifact_id,
                "relationship_type": relationship_type,
                "influence_weight": 1.0,
            }
        )
    return {
        "artifact_id": artifact_id,
        "inspiration_edges": inspiration_edges,
        "strategy_tags": sorted(strategy_tags),
        "meta": {
            "snapshot_at": utc_now(),
            "surfaces": {"inspiration": surface_state},
        },
    }


def _rw01_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
    source: Optional[str] = None,
) -> str:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
        missing_message=missing_message,
        source=source,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("source") == "local_snapshot":
        return "degraded"
    if surface.get("status") == "degraded":
        return "stale"
    return "fresh"


_TW01_SESSION_STATUSES = {"active", "paused", "completed", "abandoned"}
_TW04_REPLAY_TERMINAL_STATUSES = {"completed", "abandoned"}
_TRN003_RAPID_EVAL_SCOPES = frozenset({"persona_patch", "strategy_patch", "feature_patch", "risk_patch"})
_TRN003_RAPID_EVAL_ACTIVE_STATUSES = frozenset({"active", "paused"})


def _tw04_get_candidate_snapshot_at(replay: Dict[str, Any]) -> Optional[str]:
    preview_events = sorted(
        [
            e for e in (replay.get("events") or [])
            if isinstance(e, dict) and e.get("event_type") == "preview_trigger"
        ],
        key=lambda e: int(e.get("sequence_number") or 0),
    )
    if not preview_events:
        return None
    return (preview_events[-1].get("eval_ref") or {}).get("candidate_snapshot_at")


def _tw04_required_text(payload: Dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if value is None or not str(value).strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"Missing required field: {field}",
            f"{field} must be a non-empty string",
            precondition_failed=field,
        )
    return str(value).strip()


def _tw01_validate_session_status(value: Optional[str]) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _TW01_SESSION_STATUSES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid trainer session status",
            f"status must be one of {sorted(_TW01_SESSION_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _tw01_required_text(payload: Dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if value is None or not str(value).strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"Missing required field: {field}",
            f"{field} must be a non-empty string",
            precondition_failed=field,
        )
    return str(value).strip()


def _tw01_validate_context_refs(value: Any) -> List[Dict[str, str]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid context_refs",
            "context_refs must be an array of { type, id } objects",
            precondition_failed="context_refs",
        )
    refs: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid context_refs entry",
                "Each context_refs entry must be an object",
                precondition_failed="context_refs",
            )
        refs.append(
            {
                "type": _tw01_required_text(item, "type"),
                "id": _tw01_required_text(item, "id"),
            }
        )
    return refs


def _tw01_trainer_dialog_surface_state(
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
) -> str:
    surface = _dataset_surface_status(
        "teaching_sessions",
        snapshot_at=snapshot_at,
        has_data=has_data,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("source") == "local_snapshot":
        return "degraded"
    if surface.get("status") == "degraded":
        return "stale"
    return "fresh"


def _tw03_validate_refresh_mode(payload: Dict[str, Any]) -> str:
    refresh_mode = str(payload.get("refresh_mode") or "").strip().lower()
    mode = str(payload.get("mode") or "").strip().lower()
    if refresh_mode and refresh_mode != "manual":
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid trainer preview refresh mode",
            "refresh_mode must equal 'manual' or mode must equal 'refresh'",
            precondition_failed="refresh_mode",
        )
    if mode and mode != "refresh":
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid trainer preview refresh mode",
            "refresh_mode must equal 'manual' or mode must equal 'refresh'",
            precondition_failed="mode",
        )
    if refresh_mode == "manual":
        return refresh_mode
    if mode == "refresh":
        return mode
    raise _bff_error(
        422,
        ErrorCode.INVALID_PARAMS,
        "Invalid trainer preview refresh mode",
        "refresh_mode must equal 'manual' or mode must equal 'refresh'",
        precondition_failed="refresh_mode",
    )


def _tw02_validate_patch_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    allowed_fields = {"patches"}
    unknown_fields = sorted(set(payload.keys()) - allowed_fields)
    if unknown_fields:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid trainer control patch payload",
            f"Unsupported top-level fields: {unknown_fields}",
            precondition_failed="payload_shape",
        )

    patches = payload.get("patches")
    if not isinstance(patches, list) or not patches:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid trainer control patch payload",
            "patches must be a non-empty array of { parameter_key, proposed_value } objects",
            precondition_failed="patches",
        )

    normalized: List[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    for index, patch in enumerate(patches):
        if not isinstance(patch, dict):
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid trainer control patch entry",
                "Each patches[] entry must be an object",
                precondition_failed=f"patches[{index}]",
            )

        unknown_patch_fields = sorted(set(patch.keys()) - {"parameter_key", "proposed_value"})
        if unknown_patch_fields:
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid trainer control patch entry",
                f"Unsupported patch fields: {unknown_patch_fields}",
                precondition_failed=f"patches[{index}]",
            )

        parameter_key = str(patch.get("parameter_key") or "").strip()
        if not parameter_key:
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid trainer control patch entry",
                "parameter_key must be a non-empty string",
                precondition_failed=f"patches[{index}].parameter_key",
            )
        if parameter_key in seen_keys:
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid trainer control patch payload",
                f"Duplicate parameter_key is not allowed: {parameter_key}",
                precondition_failed=f"patches[{index}].parameter_key",
            )

        normalized.append(
            {
                "parameter_key": parameter_key,
                "proposed_value": patch.get("proposed_value"),
            }
        )
        seen_keys.add(parameter_key)

    return normalized


_CW01_TARGET_TYPES = {"persona", "committee", "red_team"}
_CW01_PRIORITIES = {"low", "normal", "high", "critical"}
_CW01_CONSULTATION_TYPES = {
    "pre_deployment",
    "risk_review",
    "macro_regime_shift",
    "incident_response",
    "policy_change",
    "general",
}
_CW01_CONTEXT_REF_TYPES = {
    "artifact",
    "deployment_plan",
    "incident",
    "lineage_edge",
    "telemetry_ref",
    "note",
}


def _cw01_required_text(payload: Dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if value is None or not str(value).strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"Missing required field: {field}",
            f"{field} must be a non-empty string",
            precondition_failed=field,
        )
    return str(value).strip()


def _cw01_validate_target_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _CW01_TARGET_TYPES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid target_type",
            f"target_type must be one of {sorted(_CW01_TARGET_TYPES)}",
            precondition_failed="target_type",
        )
    return normalized


def _cw01_validate_priority(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _CW01_PRIORITIES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid priority",
            f"priority must be one of {sorted(_CW01_PRIORITIES)}",
            precondition_failed="priority",
        )
    return normalized


def _cw01_validate_consultation_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _CW01_CONSULTATION_TYPES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid consultation_type",
            f"consultation_type must be one of {sorted(_CW01_CONSULTATION_TYPES)}",
            precondition_failed="consultation_type",
        )
    return normalized


def _cw01_validate_context_refs(value: Any) -> List[Dict[str, str]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid context_refs",
            "context_refs must be an array of { type, id } objects",
            precondition_failed="context_refs",
        )
    refs: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid context_refs entry",
                "Each context_refs entry must be an object with type and id",
                precondition_failed="context_refs",
            )
        ref_type = str(item.get("type") or "").strip().lower()
        if ref_type not in _CW01_CONTEXT_REF_TYPES:
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid context_refs entry type",
                f"context_refs[].type must be one of {sorted(_CW01_CONTEXT_REF_TYPES)}",
                precondition_failed="context_refs",
            )
        ref_id = str(item.get("id") or "").strip()
        if not ref_id:
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Missing context_refs entry id",
                "context_refs[].id must be a non-empty string",
                precondition_failed="context_refs",
            )
        refs.append({"type": ref_type, "id": ref_id})
    return refs


def _cw01_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
) -> str:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("source") == "local_snapshot":
        return "degraded"
    if surface.get("status") == "degraded":
        return "stale"
    return "fresh"


def _rw01_validate_priority(priority: Any) -> str:
    normalized = str(priority or "").strip().lower()
    if normalized not in _RW01_ALLOWED_PRIORITIES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research ticket priority",
            f"priority must be one of {sorted(_RW01_ALLOWED_PRIORITIES)}",
            precondition_failed="priority",
        )
    return normalized


def _rw01_validate_status(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in _RW01_ALLOWED_STATUSES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research ticket status",
            f"status must be one of {sorted(_RW01_ALLOWED_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _kw02_bad_request(message: str, reason: str, field: str) -> HTTPException:
    return _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        message,
        reason,
        precondition_failed=field,
    )


def _kw02_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
) -> str:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("status") == "degraded" or surface.get("source") == "local_snapshot":
        return "degraded"
    return "ok"


def _kw02_optional_title(payload: Dict[str, Any]) -> Optional[str]:
    title = payload.get("title")
    if title in (None, ""):
        return None
    normalized = str(title).strip()
    if not normalized:
        return None
    if len(normalized) > 256:
        raise _kw02_bad_request(
            "Invalid title",
            "title must be 256 characters or fewer",
            "title",
        )
    return normalized


def _kw02_required_body(payload: Dict[str, Any]) -> str:
    body = payload.get("body")
    if body is None or not str(body).strip():
        raise _kw02_bad_request(
            "Missing required field: body",
            "body must be a non-empty string",
            "body",
        )
    return str(body).strip()


def _kw02_validate_string_list(value: Any, field: str) -> List[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise _kw02_bad_request(
            f"Invalid {field}",
            f"{field} must be an array of strings",
            field,
        )
    normalized: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if not text:
            raise _kw02_bad_request(
                f"Invalid {field} entry",
                f"{field} entries must be non-empty strings",
                field,
            )
        normalized.append(text)
    return normalized


def _kw02_validate_attachment_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW02_ATTACHMENT_TYPES:
        raise _kw02_bad_request(
            "Invalid attachment_type",
            f"attachment_type must be one of {sorted(_KW02_ATTACHMENT_TYPES)}",
            "attachment_type",
        )
    return normalized


def _kw02_validate_attachment_ref(attachment_type: str, value: Any) -> Optional[str]:
    if attachment_type == "free_standing":
        if value not in (None, ""):
            raise _kw02_bad_request(
                "Invalid attachment_ref",
                "attachment_ref must be null when attachment_type is free_standing",
                "attachment_ref",
            )
        return None

    ref = str(value or "").strip()
    if not ref:
        raise _kw02_bad_request(
            "Missing attachment_ref",
            "attachment_ref is required unless attachment_type is free_standing",
            "attachment_ref",
        )
    pattern = _KW02_ATTACHMENT_ID_PATTERNS.get(attachment_type)
    if pattern is not None and not pattern.match(ref):
        raise _kw02_bad_request(
            "Invalid attachment_ref",
            f"attachment_ref does not match the identity format for {attachment_type}",
            "attachment_ref",
        )
    return ref


def _kw02_resolve_attachment_target(
    attachment_type: str,
    attachment_ref: Optional[str],
) -> tuple[bool, Optional[str], Optional[str]]:
    if attachment_type == "free_standing":
        return True, None, None
    if attachment_type == "research_ticket":
        ticket = read_store.get_research_ticket(attachment_ref)
        if not ticket:
            return False, None, None
        return True, ticket.get("title"), f"/research/tickets/{attachment_ref}"
    if attachment_type == "persona":
        persona = read_store.get_persona(attachment_ref)
        if not persona:
            return False, None, None
        return True, persona.get("name"), f"/personas/{attachment_ref}"
    strategy_spec = read_store.get_strategy_spec(attachment_ref)
    if not strategy_spec:
        return False, None, None
    label = strategy_spec.get("title") or strategy_spec.get("name") or attachment_ref
    return True, label, f"/knowledge/strategy-specs/{attachment_ref}"


def _kw02_validate_memory_anchors(anchor_ids: List[str]) -> List[str]:
    validated: List[str] = []
    for entry_id in anchor_ids:
        if not _KW02_MEMORY_ANCHOR_PATTERN.match(entry_id):
            raise _kw02_bad_request(
                "Invalid linked_memory_anchors entry",
                "linked_memory_anchors items must use the mem-{UUID} format",
                "linked_memory_anchors",
            )
        if read_store.get_institutional_memory_entry(entry_id) is None:
            raise _kw02_bad_request(
                "Unknown linked_memory_anchors entry",
                f"linked_memory_anchors entry {entry_id} does not resolve to a known institutional memory entry",
                "linked_memory_anchors",
            )
        validated.append(entry_id)
    return validated


def _kw02_operator_display_name(operator_id: str) -> str:
    if operator_id == "op-001":
        return "Alice Chen"
    token = str(operator_id or "").strip()
    if not token:
        return "Operator"
    if token.startswith("op-"):
        return f"Operator {token}"
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", token) if part)


def _kw02_strip_markdown(text: str) -> str:
    plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    plain = re.sub(r"[`*_>#]", " ", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain


def _kw02_note_excerpt(body: str) -> str:
    return _kw02_strip_markdown(body)[:280]


def _kw02_attachment_payload(note: Dict[str, Any], *, include_route: bool) -> Dict[str, Any]:
    attachment_type = str(note.get("attachment_type") or "free_standing")
    attachment_ref = note.get("attachment_ref")
    exists, display_label, route_href = _kw02_resolve_attachment_target(attachment_type, attachment_ref)
    payload = {
        "type": attachment_type,
        "ref": attachment_ref,
        "display_label": display_label if exists else None,
    }
    if include_route:
        payload["route_href"] = route_href if exists else None
    return payload


def _kw02_note_list_item(note: Dict[str, Any]) -> Dict[str, Any]:
    body = str(note.get("body") or "")
    return {
        "note_id": note.get("note_id"),
        "title": note.get("title"),
        "excerpt": _kw02_note_excerpt(body),
        "owner_ref": json.loads(json.dumps(note.get("owner_ref") or {})),
        "attachment": _kw02_attachment_payload(note, include_route=False),
        "tags": list(note.get("tags") or []),
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
        "route_href": f"/knowledge/notes/{note.get('note_id')}",
    }


def _kw02_resolve_evidence_links(
    ref_ids: List[str],
    *,
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], str]:
    surface_state = _kw02_surface_state(
        "evidence_refs",
        snapshot_at=snapshot_at,
        has_data=True,
    )
    items: List[Dict[str, Any]] = []
    for ref_id in ref_ids:
        if surface_state == "unavailable":
            items.append(
                {
                    "ref_id": ref_id,
                    "resolution_state": "unavailable",
                    "display_label": None,
                    "route_href": None,
                }
            )
            continue
        evidence_ref = read_store.get_evidence_ref(ref_id)
        if evidence_ref:
            items.append(
                {
                    "ref_id": ref_id,
                    "resolution_state": "resolved",
                    "display_label": evidence_ref.get("display_label"),
                    "route_href": evidence_ref.get("route_href") or f"/knowledge/evidence/{ref_id}",
                }
            )
            continue
        items.append(
            {
                "ref_id": ref_id,
                "resolution_state": "unresolved",
                "display_label": None,
                "route_href": None,
            }
        )
    return items, surface_state


def _kw02_resolve_memory_anchors(
    entry_ids: List[str],
    *,
    snapshot_at: str,
) -> tuple[List[Dict[str, Any]], str]:
    surface_state = _kw02_surface_state(
        "institutional_memory_entries",
        snapshot_at=snapshot_at,
        has_data=True,
    )
    items: List[Dict[str, Any]] = []
    missing_entries = False
    for entry_id in entry_ids:
        entry = read_store.get_institutional_memory_entry(entry_id)
        if not entry:
            missing_entries = True
            continue
        content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
        lifecycle = entry.get("lifecycle") if isinstance(entry.get("lifecycle"), dict) else {}
        items.append(
            {
                "entry_id": entry_id,
                "headline": content.get("headline"),
                "knowledge_type": entry.get("knowledge_type"),
                "lifecycle_status": lifecycle.get("status"),
                "route_href": f"/knowledge/memory/{entry_id}",
            }
        )
    if missing_entries and surface_state == "ok":
        surface_state = "degraded"
    return items, surface_state


def _kw03_bad_request(message: str, reason: str, field: str) -> HTTPException:
    return _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        message,
        reason,
        precondition_failed=field,
    )


def _kw03_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
) -> str:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("status") == "degraded" or surface.get("source") == "local_snapshot":
        return "degraded"
    return "ok"


def _kw03_validate_linked_entity_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW03_LINKED_ENTITY_TYPES:
        raise _kw03_bad_request(
            "Invalid linked_entity_type",
            f"linked_entity_type must be one of {sorted(_KW03_LINKED_ENTITY_TYPES)}",
            "linked_entity_type",
        )
    return normalized


def _kw03_validate_link_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW03_LINK_TYPES:
        raise _kw03_bad_request(
            "Invalid link_type",
            f"link_type must be one of {sorted(_KW03_LINK_TYPES)}",
            "link_type",
        )
    return normalized


def _kw03_validate_credibility_tier(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW03_CREDIBILITY_TIERS:
        raise _kw03_bad_request(
            "Invalid credibility_tier",
            f"credibility_tier must be one of {sorted(_KW03_CREDIBILITY_TIERS)}",
            "credibility_tier",
        )
    return normalized


def _kw04_bad_request(message: str, reason: str, field: str) -> HTTPException:
    return _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        message,
        reason,
        precondition_failed=field,
    )


def _kw04_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
) -> str:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("status") == "degraded" or surface.get("source") == "local_snapshot":
        return "degraded"
    return "ok"


def _kw04_validate_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW04_STATUSES:
        raise _kw04_bad_request(
            "Invalid status",
            f"status must be one of {sorted(_KW04_STATUSES)}",
            "status",
        )
    return normalized


def _kw04_validate_linked_entity_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW04_LINKED_ENTITY_TYPES:
        raise _kw04_bad_request(
            "Invalid linked_entity_type",
            f"linked_entity_type must be one of {sorted(_KW04_LINKED_ENTITY_TYPES)}",
            "linked_entity_type",
        )
    return normalized


def _kw04_validate_recency(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW04_RECENCY_VALUES:
        raise _kw04_bad_request(
            "Invalid recency",
            f"recency must be one of {sorted(_KW04_RECENCY_VALUES)}",
            "recency",
        )
    return normalized


def _kw04_validate_confidence_min(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise _kw04_bad_request(
            "Invalid confidence_min",
            "confidence_min must be a number between 0.0 and 1.0",
            "confidence_min",
        )
    if parsed < 0.0 or parsed > 1.0:
        raise _kw04_bad_request(
            "Invalid confidence_min",
            "confidence_min must be a number between 0.0 and 1.0",
            "confidence_min",
        )
    return parsed


def _kw04_recency_display_label(value: str) -> str:
    labels = {
        "7d": "Last 7 days",
        "30d": "Last 30 days",
        "90d": "Last 90 days",
        "all": "All time",
    }
    return labels[value]


def _kw04_within_recency(aggregated_at: Optional[str], recency: str, snapshot_at: str) -> bool:
    if recency == "all":
        return True
    aggregated_dt = _parse_rfc3339(aggregated_at)
    snapshot_dt = _parse_rfc3339(snapshot_at)
    if aggregated_dt is None or snapshot_dt is None:
        return False
    days = {"7d": 7, "30d": 30, "90d": 90}[recency]
    return aggregated_dt >= snapshot_dt - timedelta(days=days)


def _kw04_filter_metadata(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    tag_counts: Dict[str, int] = {}
    linked_entity_counts: Dict[str, int] = {}
    for card in cards:
        seen_tags = set()
        for tag in card.get("tags") or []:
            tag_value = str(tag or "").strip()
            if not tag_value or tag_value in seen_tags:
                continue
            tag_counts[tag_value] = tag_counts.get(tag_value, 0) + 1
            seen_tags.add(tag_value)
        seen_entity_types = set()
        for source in card.get("linked_sources") or []:
            entity_type = str((source or {}).get("entity_type") or "").strip()
            if not entity_type or entity_type in seen_entity_types:
                continue
            linked_entity_counts[entity_type] = linked_entity_counts.get(entity_type, 0) + 1
            seen_entity_types.add(entity_type)

    linked_entity_labels = {
        "memory_entry": "Institutional Memory",
        "research_note": "Research Note",
        "evidence_ref": "Evidence Reference",
        "strategy_spec": "Strategy Spec",
        "experiment": "Experiment",
    }
    return {
        "tags": [
            {
                "value": tag,
                "display_label": tag.replace("-", " ").title(),
                "count": count,
            }
            for tag, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "linked_entity_types": [
            {
                "value": entity_type,
                "display_label": linked_entity_labels.get(entity_type, entity_type.replace("_", " ").title()),
                "count": count,
            }
            for entity_type, count in sorted(
                linked_entity_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "recency_options": [
            {
                "value": value,
                "display_label": _kw04_recency_display_label(value),
            }
            for value in ["7d", "30d", "90d", "all"]
        ],
        "total_active_count": len(
            [card for card in cards if str(card.get("status") or "") == "active"]
        ),
    }


def _kw05_bad_request(message: str, reason: str, field: str) -> HTTPException:
    return _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        message,
        reason,
        precondition_failed=field,
    )


def _kw05_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
) -> str:
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("status") == "degraded" or surface.get("source") == "local_snapshot":
        return "degraded"
    return "ok"


def _kw05_validate_lifecycle_state(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW05_LIFECYCLE_STATES:
        raise _kw05_bad_request(
            "Invalid lifecycle_state",
            f"lifecycle_state must be one of {sorted(_KW05_LIFECYCLE_STATES)}",
            "lifecycle_state",
        )
    return normalized


def _kw05_compare_selectors(
    *,
    left_version: Optional[str],
    right_version: Optional[str],
    base_version: Optional[str],
    target_version: Optional[str],
) -> tuple[str, str]:
    left = str(left_version or base_version or "").strip()
    right = str(right_version or target_version or "").strip()
    if not left or not right:
        raise _kw05_bad_request(
            "Missing compare versions",
            "Provide either left_version/right_version or base_version/target_version",
            "left_version",
        )
    if left_version and base_version and str(left_version).strip() != str(base_version).strip():
        raise _kw05_bad_request(
            "Conflicting compare aliases",
            "left_version and base_version must reference the same version when both are provided",
            "left_version",
        )
    if right_version and target_version and str(right_version).strip() != str(target_version).strip():
        raise _kw05_bad_request(
            "Conflicting compare aliases",
            "right_version and target_version must reference the same version when both are provided",
            "right_version",
        )
    return left, right


def _kw04_supporting_evidence_surface(
    supporting_evidence_refs: List[Dict[str, Any]],
    *,
    snapshot_at: str,
) -> str:
    surface_state = _kw04_surface_state(
        "evidence_refs",
        snapshot_at=snapshot_at,
        has_data=True,
    )
    if surface_state != "ok":
        return surface_state
    for item in supporting_evidence_refs:
        if not item.get("ref_id") or not isinstance(item.get("resolved_link"), dict):
            return "degraded"
    return "ok"


def _kw04_linked_sources_surface(
    linked_sources: List[Dict[str, Any]],
    *,
    snapshot_at: str,
) -> str:
    dataset_map = {
        "memory_entry": "institutional_memory_entries",
        "research_note": "research_notes",
        "evidence_ref": "evidence_refs",
        "strategy_spec": "strategy_specs",
        "experiment": "research_experiments",
    }
    if not linked_sources:
        return "ok"
    overall = "ok"
    for item in linked_sources:
        entity_type = str(item.get("entity_type") or "").strip()
        dataset = dataset_map.get(entity_type)
        if not dataset:
            return "degraded"
        state = _kw04_surface_state(dataset, snapshot_at=snapshot_at, has_data=True)
        if state == "unavailable":
            return "unavailable"
        if state == "degraded":
            overall = "degraded"
        if not item.get("display_label") or "route_href" not in item:
            overall = "degraded"
    return overall


def _rw03_validate_status(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in _RW03_ALLOWED_STATUSES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research analysis status",
            f"status must be one of {sorted(_RW03_ALLOWED_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _rw03_validate_date_range(date_range: Any) -> str:
    normalized = str(date_range or "").strip().lower()
    if normalized not in _RW03_ALLOWED_DATE_RANGES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research analysis date_range",
            f"date_range must be one of {sorted(_RW03_ALLOWED_DATE_RANGES)}",
            precondition_failed="date_range",
        )
    return normalized


def _rw02_invalid_query(detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": "invalid_search_query",
            "detail": detail,
        },
    )


def _rw02_validate_query(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("q is required and must be non-empty")
    return normalized


def _rw02_validate_match_type(value: Any) -> str:
    normalized = str(value or "all").strip().lower()
    if normalized not in _RW02_ALLOWED_MATCH_TYPES:
        raise ValueError(
            f"match_type must be one of {sorted(_RW02_ALLOWED_MATCH_TYPES)}"
        )
    return normalized


def _rw02_validate_status(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized not in _RW01_ALLOWED_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_RW01_ALLOWED_STATUSES)}"
        )
    return normalized


def _rw02_validate_date_range(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized not in _RW03_ALLOWED_DATE_RANGES:
        raise ValueError(
            f"date_range must be one of {sorted(_RW03_ALLOWED_DATE_RANGES)}"
        )
    return normalized


def _rw02_page_slice(
    items: List[Dict[str, Any]],
    page_token: Optional[str],
    page_size: int,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    if page_token in (None, ""):
        start = 0
    else:
        try:
            start = int(page_token)
        except (TypeError, ValueError) as exc:
            raise ValueError("page_token must be a non-negative integer offset") from exc
        if start < 0:
            raise ValueError("page_token must be a non-negative integer offset")
    end = start + page_size
    next_page_token = str(end) if end < len(items) else None
    return items[start:end], next_page_token


def _rw02_adapter_state(index_adapter: Optional[Dict[str, Any]], *, snapshot_at: str) -> str:
    derived_state = _rw01_surface_state("research_search_documents", snapshot_at=snapshot_at)
    if derived_state in {"unavailable", "degraded"}:
        return derived_state
    if isinstance(index_adapter, dict):
        state = str(index_adapter.get("adapter_state") or "").strip().lower()
        if state in _RW02_ALLOWED_ADAPTER_STATES:
            if derived_state == "stale" and state == "fresh":
                return "stale"
            return state
    return derived_state


def _rw01_required_text(payload: Dict[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"Missing required field: {field}",
            f"{field} is required and must be a non-empty string.",
            precondition_failed=field,
        )
    return value


def _rw01_validate_patch(
    ticket: Dict[str, Any],
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    allowed_patch_fields = {"status", "title", "description", "priority", "owner"}
    unknown_fields = sorted(set(payload.keys()) - allowed_patch_fields)
    if unknown_fields:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid research ticket patch payload",
            f"Unsupported patch fields: {unknown_fields}",
            precondition_failed="payload_shape",
        )

    patch: Dict[str, Any] = {}
    editable = bool((ticket.get("allowedActions") or {}).get("canEdit"))

    for field in ("title", "description", "owner"):
        if field in payload:
            value = str(payload.get(field) or "").strip()
            if not value:
                raise _bff_error(
                    422,
                    ErrorCode.INVALID_PARAMS,
                    f"Invalid research ticket field: {field}",
                    f"{field} must be a non-empty string when provided.",
                    precondition_failed=field,
                )
            if not editable:
                raise _bff_error(
                    409,
                    ErrorCode.INVALID_STATE,
                    "Research ticket is not editable in its current lifecycle state",
                    f"{field} cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )
            patch[field] = value

    if "priority" in payload:
        if not editable:
            raise _bff_error(
                409,
                ErrorCode.INVALID_STATE,
                "Research ticket is not editable in its current lifecycle state",
                "priority cannot be modified while allowedActions.canEdit is false.",
                precondition_failed="allowedActions.canEdit",
            )
        patch["priority"] = _rw01_validate_priority(payload.get("priority"))

    if "status" in payload:
        current_status = str(ticket.get("status") or "").strip().lower()
        next_status = _rw01_validate_status(payload.get("status"))
        if next_status != current_status:
            if next_status == "closed" and not (ticket.get("allowedActions") or {}).get("canClose"):
                raise _bff_error(
                    409,
                    ErrorCode.INVALID_STATE,
                    "Research ticket cannot be closed in its current state",
                    "allowedActions.canClose is false for this ticket.",
                    precondition_failed="allowedActions.canClose",
                )
            if next_status == "archived" and not (ticket.get("allowedActions") or {}).get("canArchive"):
                raise _bff_error(
                    409,
                    ErrorCode.INVALID_STATE,
                    "Research ticket cannot be archived in its current state",
                    "allowedActions.canArchive is false for this ticket.",
                    precondition_failed="allowedActions.canArchive",
                )
            allowed_targets = _RW01_STATUS_TRANSITIONS.get(current_status, set())
            if next_status not in allowed_targets:
                raise _bff_error(
                    409,
                    ErrorCode.INVALID_STATE,
                    "Invalid research ticket lifecycle transition",
                    f"Cannot transition research ticket from {current_status} to {next_status}.",
                    precondition_failed="status_transition",
                )
        patch["status"] = next_status

    if not patch:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Empty research ticket patch payload",
            "At least one accepted patch field is required.",
            precondition_failed="payload_shape",
        )
    return patch


def _build_consultation_workbench_overview(snapshot_at: str) -> Dict[str, Any]:
    modules = [
        {
            "module_id": "CW-01",
            "label": "Consult Request",
            "status": "ready",
            "wave_order": 1,
            "summary": "Request create/list/detail/cancel routes are live. Request-to-session lifecycle and linked_session_id contract are implemented.",
            "live_routes": [
                "POST /api/v1/consult/requests",
                "GET /api/v1/consult/requests",
                "GET /api/v1/consult/requests/{request_id}",
                "POST /api/v1/consult/requests/{request_id}/cancel",
            ],
            "next_gate": "CW-01 is live. CW-02 (Debate Transcript) may proceed.",
            "upstream_dependencies": [],
        },
        {
            "module_id": "CW-02",
            "label": "Debate Transcript",
            "status": "ready",
            "wave_order": 2,
            "summary": "Transcript route is live. Ordered TranscriptEvent stream with actor identity, sequence_no ordering, from_sequence_no filtering, and degradation semantics are implemented.",
            "live_routes": [
                "GET /api/v1/consultations/{session_id}/transcript",
            ],
            "next_gate": "Activate the CW-02 transcript UI against the published frontend packet; CW-03 and CW-04 may continue consuming the live transcript truth.",
            "upstream_dependencies": ["CW-01"],
        },
        {
            "module_id": "CW-03",
            "label": "Committee Board",
            "status": "ready",
            "wave_order": 3,
            "summary": "Committee list/detail routes, sponsor-decision authority, and transcript dependency (CW-02) are all live. Full production handoff is unblocked.",
            "live_routes": [
                "GET /api/v1/committees",
                "GET /api/v1/committees/{committee_id}",
                "POST /api/v1/operator/commands (RecordSponsorDecision)",
            ],
            "next_gate": "CW-03 is fully live. Proceed with CW-04 Red-team Memo implementation.",
            "upstream_dependencies": ["CW-01", "CW-02"],
        },
        {
            "module_id": "CW-04",
            "label": "Red-team Memo",
            "status": "ready",
            "wave_order": 4,
            "summary": "Memo list/detail routes are live. Backend-owned mapping, evidence links, degradation semantics, and governance handoff authority are implemented.",
            "live_routes": [
                "GET /api/v1/consult/memos",
                "GET /api/v1/consult/memos/{memo_id}",
            ],
            "next_gate": "Activate the CW-04 memo UI against the published frontend handoff packet and preserve backend-owned mapping, evidence, and governance semantics.",
            "upstream_dependencies": ["CW-01", "CW-02"],
        },
    ]
    return {
        "workbench_id": "consultation-workbench",
        "label": "Consultation Workbench",
        "route_href": _CONSULTATION_WORKBENCH_ROUTE,
        "overall_status": "partial_ready",
        "headline": "CW-01 through CW-04 are live in the BFF, and both CW-02 and CW-04 frontend packets are published",
        "summary": (
            "CW-01 request create/list/detail/cancel routes are live. "
            "CW-02 transcript route is live with ordered TranscriptEvent stream, actor identity, degradation semantics, and a published frontend activation packet. "
            "CW-03 committee list/detail and sponsor-decision routes are fully live. "
            "CW-04 memo list/detail routes are live with backend-owned mapping, evidence links, governance handoff gating, and a published module-local frontend handoff bundle. "
            "The remaining work is UI activation and truthful loop closeout, not backend implementation."
        ),
        "packet_family": {
            "family_id": "CW-008",
            "path": "docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md",
            "lovable_readiness": "partial_ready",
            "note": "All four consultation BFF modules are live. CW-02 and CW-04 both have published module-local frontend handoff bundles.",
        },
        "module_counts": {
            "total": len(modules),
            "ready": sum(1 for m in modules if m.get("status") == "ready"),
            "not_ready": sum(1 for m in modules if m.get("status") != "ready"),
        },
        "modules": modules,
        "support_refs": [
            {
                "ref_id": "consultation-surface-contract",
                "label": "Consultation Surface Contract",
                "ref_type": "document",
                "value": "services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md",
                "note": "Existing CS-01 to CS-06 persona-side read surfaces; not a workbench IA.",
            },
            {
                "ref_id": "persona-consultations",
                "label": "Persona consultation list",
                "ref_type": "endpoint",
                "value": "/api/v1/personas/{persona_id}/consultations",
                "note": "Existing persona-scoped consultation list and detail support.",
            },
            {
                "ref_id": "persona-consult-policy",
                "label": "Persona consult policy",
                "ref_type": "endpoint",
                "value": "/api/v1/personas/{persona_id}/consult-policy",
                "note": "Existing consult-policy read surface used by persona-side consultation flows.",
            },
            {
                "ref_id": "persona-runtime-model",
                "label": "Persona Runtime Model",
                "ref_type": "document",
                "value": "PERSONA_RUNTIME_MODEL.md",
                "note": "Canonical source for consultation roles, session metadata, and ConsultPolicy fields.",
            },
        ],
        "next_steps": [
            "Activate the CW-02 transcript UI against the published frontend packet now that the route family is live.",
            "Activate the CW-04 memo UI against the published frontend packet and keep governance CTA authority backend-owned.",
            "Keep this overview read-only; do not invent request forms or memo state in the browser.",
        ],
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "overview": {"status": "ok", "source": "bff_static"},
                "packet_family": {"status": "ok", "source": "canonical"},
            },
        },
    }


def _build_knowledge_workbench_overview(snapshot_at: str) -> Dict[str, Any]:
    modules = [
        {
            "module_id": "KW-01",
            "label": "Institutional Memory",
            "status": "ready",
            "wave_order": 1,
            "summary": "List and detail routes are live. Browse projection, lifecycle state machine, and identity contract published via KW-01-FOUNDATION-001.",
            "missing_contracts": [],
            "next_gate": "BFF routes are implemented; Lovable may proceed with production UI using example payloads.",
            "upstream_dependencies": [],
        },
        {
            "module_id": "KW-02",
            "label": "Research Notes",
            "status": "ready",
            "wave_order": 2,
            "summary": "Research Notes create/list/detail routes are live. Ownership, attachment taxonomy, and referential integrity rules are implemented in the current BFF.",
            "missing_contracts": [],
            "next_gate": "Activate the Lovable UI task against the live KW-02 routes.",
            "upstream_dependencies": ["KW-01"],
        },
        {
            "module_id": "KW-03",
            "label": "Evidence Refs",
            "status": "ready",
            "wave_order": 3,
            "summary": "Evidence Refs list/detail routes are live. Link taxonomy, credibility metadata, and resolved-link projection are implemented in the current BFF.",
            "missing_contracts": [],
            "next_gate": "Activate the Lovable UI task against the live KW-03 routes and preserve backend-owned resolved-link semantics.",
            "upstream_dependencies": ["KW-01", "KW-02"],
        },
        {
            "module_id": "KW-04",
            "label": "Insight Cards",
            "status": "ready",
            "wave_order": 4,
            "summary": "Insight Cards list/detail routes are live. Aggregation/detail projection and backend-owned filter taxonomy are implemented in the current BFF.",
            "missing_contracts": [],
            "next_gate": "Activate the Lovable UI task against the live KW-04 routes without client-side filter synthesis; the frontend handoff bundle is published.",
            "upstream_dependencies": ["KW-01", "KW-03"],
        },
        {
            "module_id": "KW-05",
            "label": "Strategy Spec",
            "status": "ready",
            "wave_order": 5,
            "summary": "Strategy Spec browse/detail/version-history/compare routes are live. Version identity, ancestry, lifecycle, and compare semantics are implemented per the ratified contract.",
            "missing_contracts": [],
            "live_routes": [
                "GET /api/v1/knowledge/strategy-specs",
                "GET /api/v1/knowledge/strategy-specs/{strategy_id}",
                "GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions",
                "GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare",
            ],
            "next_gate": "Activate the Lovable UI task against the live KW-05 routes using backend-owned version identity, ancestry, and compare semantics.",
            "upstream_dependencies": ["KW-01", "KW-03"],
        },
    ]
    return {
        "workbench_id": "knowledge-workbench",
        "label": "Knowledge Workbench",
        "route_href": _KNOWLEDGE_WORKBENCH_ROUTE,
        "overall_status": "overview_ready",
        "headline": "KW-01 to KW-05 are route-live",
        "summary": (
            "This overview is a truthful landing surface for the Knowledge Workbench. "
            "All five Knowledge Workbench modules are route-live in the current BFF."
        ),
        "packet_family": {
            "family_id": "KW-006",
            "path": "docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md",
            "lovable_readiness": "overview_ready",
            "note": "KW-01 to KW-05 are route-live in the current BFF. KW-02 to KW-05 now carry published frontend handoff packets; remaining work is front-owned UI activation plus KW-01 hardening follow-up.",
        },
        "module_counts": {
            "total": len(modules),
            "ready": sum(1 for m in modules if m.get("status") == "ready"),
            "not_ready": sum(1 for m in modules if m.get("status") != "ready"),
        },
        "modules": modules,
        "support_refs": [
            {
                "ref_id": "memory-design-note",
                "label": "Memory Layer Design Note",
                "ref_type": "document",
                "value": "services/memory/MEMORY_LAYER_DESIGN_NOTE.md",
                "note": "Canonical Memory Plane split and retrieval-facade rules.",
            },
            {
                "ref_id": "institutional-memory-schema",
                "label": "InstitutionalMemoryEntry schema",
                "ref_type": "document",
                "value": "services/memory/institutional_memory_entry.schema.json",
                "note": "Canonical shared-memory object shape; not a workbench browse contract.",
            },
            {
                "ref_id": "strategy-spec-schema",
                "label": "StrategySpec schema",
                "ref_type": "document",
                "value": "services/control-plane/specs/strategy_spec.schema.json",
                "note": "Canonical StrategySpec object schema; version browsing, ancestry, lifecycle, and compare semantics are now ratified in docs/bff/KW-05-strategy-spec.md.",
            },
            {
                "ref_id": "memory-retrieval-facade",
                "label": "Memory retrieval facade",
                "ref_type": "endpoint",
                "value": "/memory/retrieve",
                "note": "Session-facing retrieval API; not a substitute for workbench list/detail surfaces.",
            },
        ],
        "next_steps": [
            "Activate the Lovable UI task against the live KW-02 Research Notes routes.",
            "Activate the Lovable UI task against the live KW-03 Evidence Refs routes.",
            "Activate the Lovable UI task against the live KW-04 Insight Cards routes; the frontend handoff bundle is already published.",
            "Activate the Lovable UI task against the live KW-05 Strategy Spec routes using backend-owned version identity, ancestry, and compare semantics.",
            "Keep the Knowledge Workbench payload-owned; do not synthesize registry joins from raw schemas in the browser.",
            "Use this overview to track the remaining workbench order without downgrading live routes back to pending-BFF text.",
        ],
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "overview": {"status": "ok", "source": "bff_static"},
                "packet_family": {"status": "ok", "source": "canonical"},
            },
        },
    }


def _project_evolution_decision_contract(decision: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(decision)
    payload["updated_at"] = decision.get("updated_at")
    payload["notes"] = decision.get("notes")
    return payload


def _project_freeze_order_contract(order: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(order)
    payload["freeze_order_id"] = order.get("freeze_order_id") or order.get("id")
    payload["issued_at"] = order.get("issued_at") or order.get("created_at")
    return payload


def _project_rollback_contract(rollback: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(rollback)
    payload["rollback_id"] = rollback.get("rollback_id") or rollback.get("id")
    payload["executed_at"] = rollback.get("executed_at") or rollback.get("initiated_at")
    return payload


def _surface_degradation_reason(
    surface: Dict[str, Any],
    *,
    degraded_reason: str,
    unavailable_reason: str,
) -> Optional[str]:
    status = surface.get("status")
    if status == "ok":
        return None
    if status == "unavailable":
        return unavailable_reason
    if surface.get("message"):
        return str(surface["message"])
    if surface.get("note"):
        return str(surface["note"])
    return degraded_reason


def _last_triggered_at(ks: Dict[str, Any]) -> Optional[str]:
    explicit = ks.get("last_triggered_at")
    if explicit:
        return str(explicit)

    timestamps: List[str] = []
    for order in ks.get("active_freeze_orders", []):
        if not isinstance(order, dict):
            continue
        value = order.get("triggered_at") or order.get("created_at")
        if value:
            timestamps.append(str(value))
    return max(timestamps) if timestamps else None


def _kill_switch_status_value(ks: Dict[str, Any]) -> str:
    explicit = str(ks.get("status") or "").strip().lower()
    if explicit:
        mapped = _KILL_SWITCH_STATUS_MAP.get(explicit)
        if mapped:
            return mapped

    safe_mode_status = str(ks.get("safe_mode_status") or "").strip().lower()
    mapped = _KILL_SWITCH_STATUS_MAP.get(safe_mode_status)
    if mapped:
        if mapped == "armed" and ks.get("active"):
            return "triggered"
        return mapped

    return "triggered" if ks.get("active") else "armed"


def _kill_switch_active_commands(ks: Dict[str, Any]) -> List[str]:
    active_commands = ks.get("active_commands")
    if isinstance(active_commands, list):
        return [str(value) for value in active_commands if value not in (None, "")]

    derived: List[str] = []
    for order in ks.get("active_freeze_orders", []):
        if not isinstance(order, dict):
            continue
        value = order.get("command_id") or order.get("id") or order.get("target_id")
        if value not in (None, ""):
            derived.append(str(value))
    return derived


def _project_kill_switch_contract(ks: Dict[str, Any], surface: Dict[str, Any]) -> Dict[str, Any]:
    if surface.get("status") == "unavailable":
        return {
            "status": None,
            "last_triggered_at": None,
            "last_confirmed_at": None,
            "active_commands": [],
        }

    return {
        "status": _kill_switch_status_value(ks),
        "last_triggered_at": _last_triggered_at(ks),
        "last_confirmed_at": ks.get("last_confirmed_at") or ks.get("last_checked_at"),
        "active_commands": _kill_switch_active_commands(ks),
    }


def _action_drawer_allowed_actions_surface() -> Dict[str, Any]:
    if _read_surface_state() == "unavailable":
        return {
            "status": "unavailable",
            "message": "Action authority service is unavailable. All CTAs disabled for safety.",
        }
    return {"status": "ok"}


def _project_action_drawer_allowed_actions(
    kill_switch_surface: Dict[str, Any],
    allowed_actions_surface: Dict[str, Any],
) -> Dict[str, bool]:
    allowed_actions = {
        "canPause": False,
        "canRiskOff": False,
        "canLiquidateAll": False,
        "canHardRollback": False,
        "canIssueSafeMode": False,
        "secondaryPathAvailable": False,
    }

    if allowed_actions_surface.get("status") != "ok":
        return allowed_actions

    secondary_path_available = kill_switch_surface.get("status") != "unavailable"
    allowed_actions["secondaryPathAvailable"] = secondary_path_available

    if kill_switch_surface.get("status") == "ok":
        allowed_actions.update(_ACTION_DRAWER_PRIMARY_ALLOWED_ACTIONS)
        return allowed_actions

    if secondary_path_available:
        allowed_actions["canPause"] = True
        allowed_actions["canRiskOff"] = True

    return allowed_actions


_COMMAND_RECEIPT_STATUS_MAP = {
    CommandStatus.SUBMITTED.value: CommandReceiptStatus.ACCEPTED,
    CommandStatus.PROCESSING.value: CommandReceiptStatus.QUEUED,
    CommandStatus.EXECUTED.value: CommandReceiptStatus.QUEUED,
    CommandStatus.FAILED.value: CommandReceiptStatus.FAILED,
    CommandStatus.TIMEOUT.value: CommandReceiptStatus.FAILED,
}

_ACTION_COMMAND_STATUS_MAP = {
    CommandStatus.SUBMITTED.value: ActionCommandStatus.ACCEPTED,
    CommandStatus.PROCESSING.value: ActionCommandStatus.QUEUED,
    CommandStatus.EXECUTED.value: ActionCommandStatus.COMPLETED,
}


def _expected_completion_at(accepted_at: str, estimated_processing_time_ms: int) -> Optional[str]:
    if not accepted_at or estimated_processing_time_ms < 0:
        return None
    try:
        parsed = datetime.fromisoformat(accepted_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    completed_at = parsed + timedelta(milliseconds=estimated_processing_time_ms)
    return completed_at.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _project_command_submission_response(
    *,
    command_id: str,
    command: CommandType,
    accepted_at: str,
    status: CommandStatus,
    staleness_warning: Optional[StalenessWarning],
) -> CommandSubmissionResponse:
    receipt_status = _COMMAND_RECEIPT_STATUS_MAP.get(status.value, CommandReceiptStatus.FAILED)
    meta = CommandResultMeta()
    receipt = CommandReceipt(
        receipt_id=command_id,
        command_id=command_id,
        command=command.value,
        status=receipt_status,
        accepted_at=accepted_at,
        routing_path=CommandRoutingPath.DIRECT,
        expected_completion_at=_expected_completion_at(
            accepted_at,
            meta.estimated_processing_time_ms,
        ),
        error_message=None,
    )
    return CommandSubmissionResponse(
        receipt_id=command_id,
        command=command.value,
        status=receipt_status,
        accepted_at=accepted_at,
        routing_path=CommandRoutingPath.DIRECT,
        expected_completion_at=receipt.expected_completion_at,
        error_message=None,
        staleness_warning=staleness_warning,
        receipt=receipt,
    )


def _command_dual_write_receipts(
    *,
    command_id: str,
    command: str,
    status: str,
    accepted_at: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    tracking_url = f"/api/v1/operator/commands/{command_id}"
    action_receipt = {
        "receipt_type": "action",
        "id": command_id,
        "receipt_id": command_id,
        "command_id": command_id,
        "status": status,
        "trackingUrl": tracking_url,
        "tracking_url": tracking_url,
    }
    command_receipt = {
        "receipt_type": "command",
        "receipt_id": command_id,
        "command_id": command_id,
        "command": command,
        "status": status,
        "trackingUrl": tracking_url,
        "tracking_url": tracking_url,
    }
    if accepted_at:
        action_receipt["accepted_at"] = accepted_at
        command_receipt["accepted_at"] = accepted_at
    return {
        "action_receipt": action_receipt,
        "command_receipt": command_receipt,
    }


def _action_command_status_from_command_status(status: CommandStatus) -> ActionCommandStatus:
    try:
        return _ACTION_COMMAND_STATUS_MAP[status.value]
    except KeyError as exc:
        raise ValueError(
            f"Command status {status.value!r} cannot be projected as a successful CommandResponse"
        ) from exc


def _project_final_command_response(
    *,
    command_id: str,
    command: CommandType,
    accepted_at: str,
    status: CommandStatus,
    staleness_warning: Optional[StalenessWarning],
    meta: Optional[Dict[str, Any]] = None,
    deprecation: Optional[Dict[str, Any]] = None,
) -> CommandResponse[Dict[str, Any]]:
    final_status = _action_command_status_from_command_status(status)
    legacy_payload = _project_command_submission_response(
        command_id=command_id,
        command=command,
        accepted_at=accepted_at,
        status=status,
        staleness_warning=staleness_warning,
    ).model_dump()
    legacy_payload["status"] = final_status.value
    if isinstance(legacy_payload.get("receipt"), dict):
        legacy_payload["receipt"]["status"] = final_status.value
    receipts = _command_dual_write_receipts(
        command_id=command_id,
        command=command.value,
        status=final_status.value,
        accepted_at=accepted_at,
    )
    legacy_payload["receipt_dual_write"] = receipts
    legacy_payload["action_receipt"] = receipts["action_receipt"]
    legacy_payload["actionReceipt"] = receipts["action_receipt"]
    legacy_payload["command_receipt"] = receipts["command_receipt"]
    legacy_payload["commandReceipt"] = receipts["command_receipt"]
    final_meta = dict(meta or {})
    if deprecation:
        legacy_payload["deprecated"] = True
        legacy_payload["deprecation"] = dict(deprecation)
        if isinstance(legacy_payload.get("receipt"), dict):
            legacy_payload["receipt"]["deprecated"] = True
            legacy_payload["receipt"]["deprecation"] = dict(deprecation)
        final_meta["deprecated"] = True
        final_meta["deprecation"] = dict(deprecation)
    return CommandResponse[Dict[str, Any]](
        status=final_status,
        data=legacy_payload,
        meta=final_meta or None,
    )


def _legacy_action_deprecation_notice() -> Dict[str, Any]:
    return {
        "route": "/bff/actions/{type}/{id}/{action}",
        "replacement": "/bff/v1/commands",
        "deprecated_since": _ACTIONS_DEPRECATION_SINCE,
        "sunset": _ACTIONS_SUNSET_DATE,
        "message": _ACTIONS_DEPRECATION_MESSAGE,
    }


def _apply_legacy_action_deprecation_headers(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = _ACTIONS_SUNSET_HTTP_DATE
    response.headers["Link"] = '</bff/v1/commands>; rel="successor-version"'
    response.headers["Warning"] = f'299 - "{_ACTIONS_DEPRECATION_MESSAGE}"'
    response.headers["X-Pantheon-Deprecated-Route"] = "/bff/actions/*"


# --------------------------------------------------------------------------- #
# Degraded-mode helper
# --------------------------------------------------------------------------- #

def _check_read_surface_state() -> Optional[StalenessWarning]:
    """
    In production, query the BFF read surface health endpoint.
    Returns a StalenessWarning when the surface is degraded or unavailable,
    or None when fresh.
    """
    state = os.getenv("BFF_READ_SURFACE_STATE", "fresh")
    if state == "fresh":
        return None
    return StalenessWarning(
        read_surface_state=state,
        message=(
            "Command submitted against stale read surface data. "
            "Verify target state via secondary control path before confirming action."
        ),
    )


# --------------------------------------------------------------------------- #
# Control-path degraded-mode guidance (§BFF-HA §3.2)
# --------------------------------------------------------------------------- #

_CONTROL_PATH_DEGRADED_GUIDANCE: Dict[str, Dict[str, Any]] = {
    "pause_runtime": {
        "degraded_action": "pause or resume a runtime binding",
        "risk": "Binding status is unverifiable; pause may target a binding that is already "
                "retired, failed, or in an unknown state.",
        "mitigation": [
            "Check the runtime binding status via GET /api/v1/runtime-bindings/{binding_id}.",
            "If the binding is unavailable, use the CLI fallback path (pantheon-admin) "
            "to verify the runtime state directly.",
            "Proceed only if the operator has confirmed the target identity via a "
            "secondary control path.",
        ],
        "safe_mode_impact": "Pause actions may advance the safe-mode state to PAUSED. "
                           "Verify via GET /api/v1/kill-switch/status.",
    },
    "execute_rollback": {
        "degraded_action": "roll back a runtime binding to a previous version",
        "risk": "Rollback may target a binding whose status is unknown. Position lineage "
                "updates may be applied without verified current state.",
        "mitigation": [
            "Check the rollback target via GET /api/v1/runtimes/{runtime_id}/rollbacks.",
            "Verify the current artifact version and binding status before executing.",
            "If the target is unavailable, escalate to a Severity-1 incident and consider "
            "the kill-switch path.",
        ],
        "safe_mode_impact": "Rollback actions do not directly affect safe-mode state, but "
                           "may be followed by kill-switch activation if the target is unstable.",
    },
    "activate_kill_switch": {
        "degraded_action": "activate the kill-switch to halt all runtime activity",
        "risk": "Kill-switch activation is a destructive action that bypasses the normal "
                "review queue. In degraded mode, the operator cannot verify the current "
                "safe-mode state before dispatching.",
        "mitigation": [
            "Verify the current safe-mode state via GET /api/v1/kill-switch/status.",
            "Confirm MFA is verified (required for all kill-switch activations).",
            "If the kill-switch status endpoint is unavailable, escalate to an admin "
            "operator and use the CLI fallback path.",
        ],
        "safe_mode_impact": "Kill-switch dispatch advances the safe-mode state (NORMAL → "
                           "PAUSED/GUARDED/RISK_OFF depending on trigger severity).",
    },
}


def _get_control_path_guidance(action_type: str) -> Optional[Dict[str, Any]]:
    """Return degraded-mode guidance for a control-path action."""
    guidance = _CONTROL_PATH_DEGRADED_GUIDANCE.get(action_type)
    if guidance is None:
        return None
    state = _read_surface_state()
    if state == "fresh":
        return None  # No guidance needed when surfaces are fresh
    return {
        "action_type": action_type,
        **guidance,
        "read_surface_state": state,
        "warning": f"Control-path action '{action_type}' is being executed against a "
                   f"{state} read surface. Follow the mitigation steps before confirming.",
    }


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "operator-bff",
        "version": "0.2.0",
        "timestamp": utc_now(),
    }


@app.get("/api/v1/settings")
async def get_settings(
    authorization: Optional[str] = Header(default=None),
):
    _extract_identity(authorization)
    return settings_store.get()


@app.post("/api/v1/settings")
async def update_settings(
    body: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
):
    identity = _extract_identity(authorization, mfa_token=x_mfa_token)
    _require_admin_mfa(identity, "update_settings")
    patch = body.get("settings", body)
    try:
        settings = settings_store.update(patch)
    except ValueError as exc:
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "Settings update payload is invalid",
            str(exc),
            precondition_failed="settings_payload",
            suggestion="Submit a complete settings object or a valid partial patch",
        )
    return {"settings": settings}


@app.get("/api/v1/settings/export")
async def export_settings(
    authorization: Optional[str] = Header(default=None),
):
    _extract_identity(authorization)
    return {"jsonData": settings_store.export_json()}


@app.post("/api/v1/settings/import")
async def import_settings(
    body: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
):
    identity = _extract_identity(authorization, mfa_token=x_mfa_token)
    _require_admin_mfa(identity, "import_settings")
    json_data = body.get("jsonData")
    if not isinstance(json_data, str):
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "Settings import payload is invalid",
            "jsonData must be a string containing a settings JSON document",
            precondition_failed="settings_import_payload",
            suggestion="Send jsonData as a string",
        )
    try:
        settings = settings_store.import_json(json_data)
    except ValueError as exc:
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "Settings import payload is invalid",
            str(exc),
            precondition_failed="settings_import_payload",
            suggestion="Upload a valid JSON export from the Pantheon settings surface",
        )
    return {"settings": settings}


# --------------------------------------------------------------------------- #
# Read surfaces (Wave 4 - Remaining Catalog List/Detail)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/personas")
async def list_personas(
    lifecycle_state: Optional[str] = None,
    mandate: Optional[str] = None,
    strategy_family: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-01: Persona List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    personas = read_store.list_personas(
        lifecycle_state=lifecycle_state,
        mandate=mandate,
        strategy_family=strategy_family,
    )
    snapshot_at = utc_now()
    return {
        "data": personas,
        "meta": _read_surface_meta(
            "personas",
            "persona_list",
            snapshot_at=snapshot_at,
            total=len(personas),
        ),
    }


@app.get("/api/v1/personas/{persona_id}")
async def get_persona_detail(persona_id: str, authorization: Optional[str] = Header(default=None)):
    """PS-02: Persona Detail with bindings."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
    persona = read_store.get_persona(persona_id)
    if not persona:
        _raise_if_read_surface_unavailable(persona_surface, label="Persona")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    bindings = read_store.get_bindings_for_persona(persona_id) or []
    payload = dict(persona)
    payload["bindings"] = bindings

    return {
        "data": payload,
        "meta": _read_surface_meta(
            "personas",
            "persona_detail",
            snapshot_at=snapshot_at,
            surface=persona_surface,
        ),
    }


@app.get("/api/v1/personas/{persona_id}/sessions")
async def list_persona_sessions(
    persona_id: str,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-03: Persona Sessions list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
    persona = read_store.get_persona(persona_id)
    if not persona:
        _raise_if_read_surface_unavailable(persona_surface, label="Persona")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    sessions = read_store.list_sessions_for_persona(persona_id, status=status) or []
    return {
        "data": sessions,
        "meta": _read_surface_meta(
            "sessions",
            "persona_sessions",
            snapshot_at=snapshot_at,
            total=len(sessions),
        ),
    }


@app.get("/api/v1/sessions/{session_id}")
async def get_session_detail(session_id: str, authorization: Optional[str] = Header(default=None)):
    """PS-04: Session detail with capability snapshot."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    session_surface = _dataset_surface_status("sessions", snapshot_at=snapshot_at)
    session = read_store.get_session(session_id)
    if not session:
        _raise_if_read_surface_unavailable(session_surface, label="Session")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Session not found",
            f"Session {session_id} does not exist",
        )

    snapshot = read_store.get_capability_snapshot(session.get("capability_snapshot_id"))
    if snapshot is None:
        snapshot = read_store.get_capability_snapshot_for_persona(session.get("persona_id"))

    payload = dict(session)
    if snapshot:
        payload["capability_snapshot"] = snapshot

    return {
        "data": payload,
        "meta": _read_surface_meta(
            "sessions",
            "session_detail",
            snapshot_at=snapshot_at,
            surface=session_surface,
        ),
    }


@app.get("/api/v1/personas/{persona_id}/teaching")
async def list_persona_teaching_sessions(
    persona_id: str,
    status: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """PS-05: Teaching sessions list for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
    persona = read_store.get_persona(persona_id)
    if not persona:
        _raise_if_read_surface_unavailable(persona_surface, label="Persona")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    sessions = read_store.list_teaching_sessions_for_persona(persona_id, status=status) or []
    return {
        "data": sessions,
        "meta": _read_surface_meta(
            "teaching_sessions",
            "teaching_sessions",
            snapshot_at=snapshot_at,
            total=len(sessions),
        ),
    }


@app.get("/api/v1/personas/{persona_id}/capabilities")
async def get_persona_capabilities(
    persona_id: str, authorization: Optional[str] = Header(default=None),
):
    """PS-06: Capability snapshot for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
    persona = read_store.get_persona(persona_id)
    if not persona:
        _raise_if_read_surface_unavailable(persona_surface, label="Persona")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    capability_surface = _dataset_surface_status("capability_snapshots", snapshot_at=snapshot_at)
    snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
    if not snapshot:
        _raise_if_read_surface_unavailable(capability_surface, label="Capability snapshot")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Capability snapshot not found",
            f"Capability snapshot for persona {persona_id} does not exist",
        )

    return {
        "data": snapshot,
        "meta": _read_surface_meta(
            "capability_snapshots",
            "capability_snapshot",
            snapshot_at=snapshot_at,
            surface=capability_surface,
        ),
    }


@app.post("/api/v1/trainer/sessions")
async def create_trainer_session(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona_id = _tw01_required_text(payload, "persona_id")
    session_type = _tw01_required_text(payload, "session_type")
    objective = _tw01_required_text(payload, "objective")
    context_refs = _tw01_validate_context_refs(payload.get("context_refs"))

    if session_type != "trainer":
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid trainer session type",
            "session_type must equal 'trainer' for TW-01",
            precondition_failed="session_type",
        )

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    session = read_store.create_trainer_session(
        persona_id=persona_id,
        objective=objective,
        context_refs=context_refs,
        actor_id=identity.operator_id,
        created_at=utc_now(),
    )
    if session is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Trainer session store unavailable",
            "Trainer session creation store is unavailable.",
        )

    return {
        "session_id": session["session_id"],
        "persona_id": session["persona_id"],
        "session_type": session["session_type"],
        "objective": session["objective"],
        "status": session["status"],
        "started_at": session["started_at"],
        "allowedActions": session["allowedActions"],
        "links": session["links"],
    }


@app.get("/api/v1/trainer/sessions")
async def list_trainer_sessions(
    persona_id: str,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    snapshot_at = utc_now()
    normalized_status = _tw01_validate_session_status(status) if status is not None else None
    sessions = read_store.list_trainer_sessions(persona_id=persona_id, status=normalized_status) or []
    surface_state = _tw01_trainer_dialog_surface_state(snapshot_at=snapshot_at, has_data=sessions is not None)

    total = len(sessions)
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(sessions, page_token, page_size)

    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "trainer_dialog": surface_state,
            },
        },
    }


@app.get("/api/v1/trainer/sessions/{session_id}")
async def get_trainer_session_detail(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_trainer_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )

    snapshot_at = utc_now()
    payload = dict(session)
    payload["meta"] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "trainer_dialog": _tw01_trainer_dialog_surface_state(snapshot_at=snapshot_at, has_data=True),
        },
    }
    return payload


@app.get("/api/v1/trainer/sessions/{session_id}/controls")
async def get_trainer_controls(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    controls = read_store.get_trainer_controls(session_id, snapshot_at=utc_now())
    if not controls:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )
    return controls


@app.post("/api/v1/trainer/sessions/{session_id}/patch")
async def patch_trainer_controls(
    session_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    patches = _tw02_validate_patch_payload(payload)

    controls = read_store.get_trainer_controls(session_id, snapshot_at=utc_now())
    if not controls:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )
    if str(controls.get("status") or "").strip().lower() != "active":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Trainer session cannot patch controls",
            "POST /patch is only allowed while the trainer session status is active",
            precondition_failed="status",
        )
    if not (controls.get("allowedActions") or {}).get("canPatchControls"):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Trainer control patch unavailable",
            "allowedActions.canPatchControls is false for this trainer session",
            precondition_failed="allowedActions.canPatchControls",
        )

    result = read_store.patch_trainer_controls(
        session_id,
        patches=patches,
        patched_at=utc_now(),
    )
    if result is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Trainer control store unavailable",
            "Trainer control patch store is unavailable.",
        )
    return result


@app.post("/api/v1/trainer/sessions/{session_id}/message")
async def append_trainer_message(
    session_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_trainer_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )
    if session["status"] != "active":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Trainer session is not active",
            "POST /message is only allowed while the trainer session status is active",
            precondition_failed="status",
        )
    if not session["allowedActions"].get("canSendMessage"):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Trainer message submission unavailable",
            "allowedActions.canSendMessage is false for this trainer session",
            precondition_failed="allowedActions.canSendMessage",
        )

    message_body = _tw01_required_text(payload, "message_body")
    result = read_store.append_trainer_message(
        session_id,
        message_body=message_body,
        actor_id=identity.operator_id,
        accepted_at=utc_now(),
    )
    if result is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Trainer session store unavailable",
            "Trainer message append store is unavailable.",
        )

    updated = result["session"]
    return {
        "session_id": updated["session_id"],
        "status": updated["status"],
        "accepted_at": result["accepted_at"],
        "event": result["event"],
        "session_summary": updated["session_summary"],
        "allowedActions": updated["allowedActions"],
    }


@app.get("/api/v1/trainer/sessions/{session_id}/preview")
async def get_trainer_preview(
    session_id: str,
    eval_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_trainer_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )

    snapshot_at = utc_now()
    preview = read_store.get_trainer_preview(
        session_id,
        session_status=session.get("status"),
        eval_id=eval_id,
        snapshot_at=snapshot_at,
    )
    if preview is None and eval_id:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer preview evaluation not found",
            f"Trainer preview evaluation {eval_id} does not exist for session {session_id}",
        )
    if preview is None:
        preview = read_store.build_trainer_preview_unavailable(
            session_id,
            session_status=session.get("status"),
            snapshot_at=snapshot_at,
        )
    return preview


@app.post("/api/v1/trainer/sessions/{session_id}/preview")
async def refresh_trainer_preview(
    session_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _tw03_validate_refresh_mode(payload)

    session = read_store.get_trainer_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )

    preview = read_store.get_trainer_preview(
        session_id,
        session_status=session.get("status"),
        snapshot_at=utc_now(),
    ) or read_store.build_trainer_preview_unavailable(
        session_id,
        session_status=session.get("status"),
        snapshot_at=utc_now(),
    )
    if session.get("status") not in {"active", "paused"}:
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Trainer session cannot refresh preview",
            "POST /preview is only allowed while the trainer session status is active or paused",
            precondition_failed="status",
        )
    if preview.get("status") == "pending":
        return preview
    if not preview.get("allowedActions", {}).get("canRefreshPreview"):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Trainer preview refresh unavailable",
            "allowedActions.canRefreshPreview is false for this trainer preview",
            precondition_failed="allowedActions.canRefreshPreview",
        )

    refreshed = read_store.refresh_trainer_preview(
        session_id,
        session_status=session.get("status"),
        refreshed_at=utc_now(),
    )
    if refreshed is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Trainer preview store unavailable",
            "Trainer preview refresh store is unavailable.",
        )
    return refreshed


@app.get("/api/v1/trainer/replay")
async def list_trainer_replays(
    persona_id: str,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    if status is not None:
        normalized_status = str(status).strip().lower()
        if normalized_status not in _TW04_REPLAY_TERMINAL_STATUSES:
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid replay status filter",
                f"status must be one of {sorted(_TW04_REPLAY_TERMINAL_STATUSES)}",
                precondition_failed="status",
            )
    else:
        normalized_status = None

    snapshot_at = utc_now()
    items, surface_state = read_store.list_trainer_replays(
        persona_id=persona_id,
        status=normalized_status,
        snapshot_at=snapshot_at,
    )
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {
                "trainer_replay": surface_state,
            },
        },
    }


@app.get("/api/v1/trainer/replay/{session_id}")
async def get_trainer_replay_detail(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    replay = read_store.get_trainer_replay(session_id, snapshot_at=snapshot_at)
    if not replay:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer replay session not found",
            f"Trainer replay session {session_id} does not exist",
        )
    return replay


@app.post("/api/v1/trainer/sessions/{session_id}/commit")
async def commit_trainer_replay(
    session_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    expected_candidate_snapshot_at = _tw04_required_text(payload, "expected_candidate_snapshot_at")
    note = payload.get("note") or None

    replay = read_store.get_trainer_replay(session_id)
    if not replay:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer replay session not found",
            f"Trainer replay session {session_id} does not exist",
        )

    if str(replay.get("status") or "").strip().lower() != "completed":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Trainer session cannot be committed",
            "commit is only allowed when session status is completed",
            precondition_failed="status",
        )

    if not replay.get("allowedActions", {}).get("canCommit"):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Commit not allowed",
            "allowedActions.canCommit is false for this trainer replay session",
            precondition_failed="allowedActions.canCommit",
        )

    candidate_snapshot_at = _tw04_get_candidate_snapshot_at(replay)
    if candidate_snapshot_at != expected_candidate_snapshot_at:
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Candidate snapshot mismatch",
            "expected_candidate_snapshot_at does not match the current replayable candidate snapshot",
            precondition_failed="expected_candidate_snapshot_at",
        )

    result = read_store.commit_trainer_replay(
        session_id,
        expected_candidate_snapshot_at=expected_candidate_snapshot_at,
        note=note,
        actor_id=identity.operator_id,
        committed_at=utc_now(),
    )
    if result is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Trainer replay store unavailable",
            "Trainer replay commit store is unavailable.",
        )
    return result


@app.post("/api/v1/trainer/sessions/{session_id}/discard")
async def discard_trainer_replay(
    session_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    expected_candidate_snapshot_at = _tw04_required_text(payload, "expected_candidate_snapshot_at")
    note = payload.get("note") or None

    replay = read_store.get_trainer_replay(session_id)
    if not replay:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer replay session not found",
            f"Trainer replay session {session_id} does not exist",
        )

    if str(replay.get("status") or "").strip().lower() != "completed":
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Trainer session cannot be discarded",
            "discard is only allowed when session status is completed",
            precondition_failed="status",
        )

    if not replay.get("allowedActions", {}).get("canDiscard"):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Discard not allowed",
            "allowedActions.canDiscard is false for this trainer replay session",
            precondition_failed="allowedActions.canDiscard",
        )

    candidate_snapshot_at = _tw04_get_candidate_snapshot_at(replay)
    if candidate_snapshot_at != expected_candidate_snapshot_at:
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Candidate snapshot mismatch",
            "expected_candidate_snapshot_at does not match the current replayable candidate snapshot",
            precondition_failed="expected_candidate_snapshot_at",
        )

    result = read_store.discard_trainer_replay(
        session_id,
        expected_candidate_snapshot_at=expected_candidate_snapshot_at,
        note=note,
        actor_id=identity.operator_id,
        discarded_at=utc_now(),
    )
    if result is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Trainer replay store unavailable",
            "Trainer replay discard store is unavailable.",
        )
    return result


@app.post("/api/v1/trainer/sessions/{session_id}/rapid-eval")
async def create_rapid_eval(
    session_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    eval_scope = str(payload.get("eval_scope") or "").strip().lower()
    if not eval_scope or eval_scope not in _TRN003_RAPID_EVAL_SCOPES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid eval_scope",
            f"eval_scope must be one of {sorted(_TRN003_RAPID_EVAL_SCOPES)}",
            precondition_failed="eval_scope",
        )

    dataset_version_id_raw = payload.get("dataset_version_id")
    if not dataset_version_id_raw or not str(dataset_version_id_raw).strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required field: dataset_version_id",
            "dataset_version_id must be a non-empty string",
            precondition_failed="dataset_version_id",
        )
    dataset_version_id = str(dataset_version_id_raw).strip()

    max_runtime_seconds_raw = payload.get("max_runtime_seconds")
    try:
        max_runtime_seconds = int(max_runtime_seconds_raw)
        if max_runtime_seconds <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid max_runtime_seconds",
            "max_runtime_seconds must be a positive integer",
            precondition_failed="max_runtime_seconds",
        )

    patch_ref = str(payload["patch_ref"]).strip() if payload.get("patch_ref") else None
    persona_id = str(payload["persona_id"]).strip() if payload.get("persona_id") else None
    strategy_id = str(payload["strategy_id"]).strip() if payload.get("strategy_id") else None

    session = read_store.get_trainer_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )

    if str(session.get("status") or "").strip().lower() not in _TRN003_RAPID_EVAL_ACTIVE_STATUSES:
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Trainer session cannot submit rapid eval",
            "rapid-eval is only allowed while the trainer session status is active or paused",
            precondition_failed="status",
        )

    result = read_store.create_rapid_eval(
        session_id,
        persona_id=persona_id,
        strategy_id=strategy_id,
        eval_scope=eval_scope,
        patch_ref=patch_ref,
        dataset_version_id=dataset_version_id,
        max_runtime_seconds=max_runtime_seconds,
        requested_by=identity.operator_id or "unknown",
        requested_at=utc_now(),
    )
    if result is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Rapid eval store unavailable",
            "Rapid eval creation store is unavailable.",
        )
    return result


@app.get("/api/v1/trainer/sessions/{session_id}/rapid-eval/{eval_id}")
async def get_rapid_eval(
    session_id: str,
    eval_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_trainer_session(session_id)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Trainer session not found",
            f"Trainer session {session_id} does not exist",
        )

    record = read_store.get_rapid_eval(eval_id, snapshot_at=utc_now())
    if not record or record.get("session_id") != session_id:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Rapid eval not found",
            f"Rapid eval {eval_id} does not exist for trainer session {session_id}",
        )
    return record


@app.get("/api/v1/capital-pools")
async def list_capital_pools(
    status: Optional[str] = None,
    risk_policy_ref: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """CP-01: Capital pool list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    pools = read_store.list_capital_pools(status=status, risk_policy_ref=risk_policy_ref)
    snapshot_at = utc_now()
    return {
        "data": pools,
        "meta": _read_surface_meta(
            "capital_pools",
            "capital_pool_list",
            snapshot_at=snapshot_at,
            total=len(pools),
        ),
    }


@app.get("/api/v1/bindings")
async def list_bindings(
    persona_id: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    role: Optional[str] = None,
    validity: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """CP-03: Persona capital binding list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    bindings = read_store.list_bindings(
        persona_id=persona_id,
        capital_pool_id=capital_pool_id,
        role=role,
        validity=validity,
    )
    snapshot_at = utc_now()
    return {
        "data": bindings,
        "meta": _read_surface_meta(
            "persona_bindings",
            "binding_list",
            snapshot_at=snapshot_at,
            total=len(bindings),
        ),
    }


@app.get("/api/v1/deployment-plans")
async def list_deployment_plans(
    status: Optional[str] = None,
    capital_pool_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """DP-01: Deployment plan list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    plans = read_store.list_deployment_plans(
        status=status,
        capital_pool_id=capital_pool_id,
    )
    snapshot_at = utc_now()
    return {
        "data": plans,
        "meta": _read_surface_meta(
            "deployment_plans",
            "deployment_plan_list",
            snapshot_at=snapshot_at,
            total=len(plans),
        ),
    }


@app.get("/api/v1/approval-decisions")
async def list_approval_decisions(
    outcome: Optional[str] = None,
    state: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """DP-03: Approval decision list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decisions = read_store.list_approval_decisions(
        outcome=outcome,
        state=state,
    )
    snapshot_at = utc_now()
    return {
        "data": decisions,
        "meta": _read_surface_meta(
            "approval_decisions",
            "approval_decision_list",
            snapshot_at=snapshot_at,
            total=len(decisions),
        ),
    }


@app.get("/api/v1/approval-decisions/{decision_id}")
async def get_approval_decision_detail(
    decision_id: str, authorization: Optional[str] = Header(default=None),
):
    """DP-04: Approval decision detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    decision_surface = _dataset_surface_status("approval_decisions", snapshot_at=snapshot_at)
    decision = read_store.get_approval_decision(decision_id)
    if not decision:
        _raise_if_read_surface_unavailable(decision_surface, label="Approval decision")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Approval decision not found",
            f"Approval decision {decision_id} does not exist",
        )

    return {
        "data": decision,
        "meta": _read_surface_meta(
            "approval_decisions",
            "approval_decision_detail",
            snapshot_at=snapshot_at,
            surface=decision_surface,
        ),
    }


@app.get("/api/v1/runtime-bindings")
async def list_runtime_bindings(
    deployment_mode: Optional[str] = None,
    version: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """RT-01: Runtime binding list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    bindings = read_store.list_runtime_bindings(
        deployment_mode=deployment_mode,
        version=version,
    )
    snapshot_at = utc_now()
    return {
        "data": bindings,
        "meta": _read_surface_meta(
            "runtime_bindings",
            "runtime_binding_list",
            snapshot_at=snapshot_at,
            total=len(bindings),
        ),
    }


@app.get("/api/v1/runtimes/{runtime_id}/status")
async def get_runtime_status(
    runtime_id: str, authorization: Optional[str] = Header(default=None),
):
    """RT-03: Runtime status detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    runtime_surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    if not runtime_binding:
        _raise_if_read_surface_unavailable(runtime_surface, label="Runtime")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime not found",
            f"Runtime {runtime_id} does not exist",
        )

    return {
        "data": runtime_binding,
        "meta": _read_surface_meta(
            "runtime_bindings",
            "runtime_status",
            snapshot_at=snapshot_at,
            surface=runtime_surface,
        ),
    }


# --------------------------------------------------------------------------- #
# Read surfaces (Wave 1 - Promotion Review)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/deployment-plans/{plan_id}")
async def get_deployment_plan(plan_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    plan_surface = _dataset_surface_status("deployment_plans", snapshot_at=snapshot_at)
    plan = read_store.get_deployment_plan(plan_id)
    if not plan:
        _raise_if_read_surface_unavailable(plan_surface, label="Deployment plan")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment plan not found",
            f"Deployment plan {plan_id} does not exist",
        )

    decision = read_store.get_approval_decision(plan.get("approval_decision_id"))
    payload = dict(plan)
    if decision:
        payload["approval_decision"] = decision

    return {
        "data": payload,
        "meta": _read_surface_meta(
            "deployment_plans",
            "deployment_plan_detail",
            snapshot_at=snapshot_at,
            surface=plan_surface,
        ),
    }


@app.get("/api/v1/capital-pools/{pool_id}")
async def get_capital_pool(pool_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    pool_surface = _dataset_surface_status("capital_pools", snapshot_at=snapshot_at)
    pool = read_store.get_capital_pool(pool_id)
    if not pool:
        _raise_if_read_surface_unavailable(pool_surface, label="Capital pool")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Capital pool not found",
            f"Capital pool {pool_id} does not exist",
        )

    bindings = read_store.get_bindings_for_pool(pool_id)
    payload = dict(pool)
    payload["bindings"] = bindings

    return {
        "data": payload,
        "meta": _read_surface_meta(
            "capital_pools",
            "capital_pool_detail",
            snapshot_at=snapshot_at,
            surface=pool_surface,
        ),
    }


@app.get("/api/v1/bindings/{binding_id}")
async def get_binding(binding_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    binding_surface = _dataset_surface_status("persona_bindings", snapshot_at=snapshot_at)
    binding = read_store.get_binding(binding_id)
    if not binding:
        _raise_if_read_surface_unavailable(binding_surface, label="Binding")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Binding not found",
            f"Binding {binding_id} does not exist",
        )

    persona = read_store.get_persona(binding.get("persona_id"))
    payload = dict(binding)
    if persona:
        payload["persona"] = persona

    return {
        "data": payload,
        "meta": _read_surface_meta(
            "persona_bindings",
            "binding_detail",
            snapshot_at=snapshot_at,
            surface=binding_surface,
        ),
    }


@app.get("/api/v1/runtime-bindings/{binding_id}")
async def get_runtime_binding(binding_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    runtime_binding_surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
    runtime_binding = read_store.get_runtime_binding(binding_id)
    if not runtime_binding:
        _raise_if_read_surface_unavailable(runtime_binding_surface, label="Runtime binding")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime binding not found",
            f"Runtime binding {binding_id} does not exist",
        )

    plan = read_store.get_deployment_plan(runtime_binding.get("plan_id", ""))
    payload = dict(runtime_binding)
    if plan:
        payload["deployment_plan"] = plan

    return {
        "data": payload,
        "meta": _read_surface_meta(
            "runtime_bindings",
            "runtime_binding_detail",
            snapshot_at=snapshot_at,
            surface=runtime_binding_surface,
        ),
    }


@app.get("/api/v1/runtimes/{runtime_id}/rollbacks")
async def get_runtime_rollbacks(runtime_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    rollbacks = read_store.get_rollbacks(runtime_id)
    return {
        "data": rollbacks,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


_PKT001_DEPLOYMENT_PLAN_FILTER_STATUSES = {"pending_review", "approved", "rejected"}


def _pkt001_requested_plan_statuses(status: Optional[str]) -> Optional[set[str]]:
    requested = _split_csv_query(status)
    if requested is None:
        return None
    normalized = {token.lower() for token in requested}
    invalid = normalized - _PKT001_DEPLOYMENT_PLAN_FILTER_STATUSES
    if invalid:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid deployment plan status filter",
            f"status must be one of {sorted(_PKT001_DEPLOYMENT_PLAN_FILTER_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _pkt001_governance_outcome(
    plan: Dict[str, Any],
    approval_decision: Optional[Dict[str, Any]],
    review: Optional[Dict[str, Any]],
) -> str:
    raw_value = str(
        (review or {}).get("governanceOutcome")
        or (approval_decision or {}).get("outcome")
        or plan.get("status")
        or ""
    ).strip().lower()
    if raw_value in {"", "pending_review", "under_review", "in_review"}:
        return "pending"
    if raw_value in {"approve", "approved_with_conditions"}:
        return "approved"
    if raw_value in {"reject"}:
        return "rejected"
    return raw_value


def _pkt001_plan_filter_status(
    plan: Dict[str, Any],
    approval_decision: Optional[Dict[str, Any]],
    review: Optional[Dict[str, Any]],
) -> str:
    governance_outcome = _pkt001_governance_outcome(plan, approval_decision, review)
    if governance_outcome == "approved":
        return "approved"
    if governance_outcome == "rejected":
        return "rejected"
    return "pending_review"


def _pkt001_plan_list_item(
    plan: Dict[str, Any],
    approval_decision: Optional[Dict[str, Any]],
    review: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "plan_id": plan.get("plan_id") or plan.get("id"),
        "artifact_id": plan.get("artifact_id"),
        "target_stage": plan.get("target_stage") or plan.get("stage"),
        "risk_level": (approval_decision or {}).get("risk_level"),
        "governance_outcome": _pkt001_governance_outcome(plan, approval_decision, review),
        "submitted_at": (
            plan.get("submitted_at")
            or plan.get("created_at")
            or (approval_decision or {}).get("decided_at")
        ),
    }


def _pkt001_allowed_actions_present(allowed_actions: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(allowed_actions, dict):
        return False
    required_fields = ("canApprove", "canReject", "canPromoteToPaper")
    return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)


def _pkt001_degradation_meta(surfaces: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    reason_templates = {
        "deployment_plans": (
            "Deployment plan list is degraded and may be stale.",
            "Deployment plan list is currently unavailable.",
        ),
        "deployment_plan": (
            "Deployment plan detail is degraded and may be stale.",
            "Deployment plan detail is currently unavailable.",
        ),
        "approval_decision": (
            "Approval decision detail is degraded and may be stale.",
            "Approval decision detail is currently unavailable.",
        ),
        "capital_pool": (
            "Capital pool detail is degraded and may be stale.",
            "Capital pool detail is currently unavailable.",
        ),
        "bindings": (
            "Binding detail is degraded and may be stale.",
            "Binding detail is currently unavailable.",
        ),
        "runtime_binding": (
            "Runtime binding detail is degraded and may be stale.",
            "Runtime binding detail is currently unavailable.",
        ),
        "allowedActions": (
            "Action authority is degraded. All CTAs disabled for safety.",
            "Action authority service is unavailable. All CTAs disabled for safety.",
        ),
        "latestRun": (
            "Latest run progress is degraded and may be stale.",
            "Latest run progress is currently unavailable.",
        ),
        "review": (
            "Review summary is degraded and may be stale.",
            "Review summary is currently unavailable.",
        ),
    }
    degradation: Dict[str, Any] = {}
    for surface_name, surface in surfaces.items():
        templates = reason_templates.get(surface_name)
        if not templates:
            continue
        reason = _surface_degradation_reason(
            surface,
            degraded_reason=templates[0],
            unavailable_reason=templates[1],
        )
        if reason is not None:
            degradation[f"{surface_name}_reason"] = reason
    if degradation and "allowedActions" in surfaces:
        degradation["disable_ctas"] = surfaces["allowedActions"].get("status") != "ok"
    return degradation


@app.get("/api/v1/operator/deployment-plans")
async def list_operator_deployment_plans(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    requested_statuses = _pkt001_requested_plan_statuses(status)
    snapshot_at = utc_now()
    deployment_plans_surface = _dataset_surface_status(
        "deployment_plans",
        snapshot_at=snapshot_at,
    )

    matched_items: List[Dict[str, Any]] = []
    allowed_actions_complete = True
    for plan in read_store.list_deployment_plans():
        plan_id = str(plan.get("plan_id") or plan.get("id") or "")
        approval_decision = read_store.get_approval_decision(plan.get("approval_decision_id"))
        review = read_store.get_review_summary(plan_id)
        derived_status = _pkt001_plan_filter_status(plan, approval_decision, review)
        if requested_statuses and derived_status not in requested_statuses:
            continue
        matched_items.append(_pkt001_plan_list_item(plan, approval_decision, review))
        if not _pkt001_allowed_actions_present(read_store.get_allowed_actions(plan_id)):
            allowed_actions_complete = False

    matched_items.sort(
        key=lambda item: (item.get("submitted_at") or "", item.get("plan_id") or ""),
        reverse=True,
    )

    if deployment_plans_surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(matched_items, page_token, page_size)

    allowed_actions_surface = _dataset_surface_status(
        "approval_decisions",
        snapshot_at=snapshot_at,
        has_data=allowed_actions_complete if matched_items else None,
        missing_message="Deployment action authority unavailable for this deployment-plan snapshot.",
    )

    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "deployment_plans": deployment_plans_surface,
            "allowedActions": allowed_actions_surface,
        },
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    degradation = _pkt001_degradation_meta(meta["surfaces"])
    if degradation:
        meta["degradation"] = degradation

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/deployment-review/{plan_id}")
async def get_deployment_review(plan_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    plan = read_store.get_deployment_plan(plan_id)
    if not plan:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment plan not found",
            f"Deployment plan {plan_id} does not exist",
        )

    pool = read_store.get_capital_pool(plan.get("capital_pool_id"))
    bindings = read_store.get_bindings_for_pool(plan.get("capital_pool_id"))
    runtime_binding = read_store.get_runtime_binding(plan.get("runtime_binding_id"))
    approval_decision = read_store.get_approval_decision(plan.get("approval_decision_id"))
    rollbacks = read_store.get_rollbacks(
        runtime_binding.get("runtime_id") if runtime_binding else None
    )
    allowed_actions = read_store.get_allowed_actions(plan_id)
    latest_run = read_store.get_latest_run(plan_id)
    review = read_store.get_review_summary(plan_id)

    snapshot_at = utc_now()

    deployment_plan_payload = {
        "id": plan.get("id"),
        "stage": plan.get("stage"),
        "artifact_id": plan.get("artifact_id"),
        "approval_decision_id": plan.get("approval_decision_id"),
    }
    for optional_key in ["current_stage", "target_stage", "status", "artifact_version", "transition_type"]:
        if plan.get(optional_key) is not None:
            deployment_plan_payload[optional_key] = plan.get(optional_key)
    if approval_decision:
        deployment_plan_payload["approval_decision"] = approval_decision

    data = {
        "deployment_plan": deployment_plan_payload,
        "approval_decision": approval_decision or {},
        "capital_pool": pool or {},
        "bindings": bindings,
        "runtime_binding": runtime_binding or {},
        "rollbacks": rollbacks,
        "allowedActions": allowed_actions,
        "latestRun": latest_run,
        "review": review,
    }

    surfaces = {
        "deployment_plan": _dataset_surface_status(
            "deployment_plans",
            snapshot_at=snapshot_at,
            has_data=plan is not None,
        ),
        "approval_decision": _dataset_surface_status(
            "approval_decisions",
            snapshot_at=snapshot_at,
            has_data=approval_decision is not None,
            missing_message="Approval decision unavailable for this deployment plan.",
        ),
        "capital_pool": _dataset_surface_status(
            "capital_pools",
            snapshot_at=snapshot_at,
            has_data=pool is not None,
            missing_message="Capital pool detail unavailable for this deployment plan.",
        ),
        "bindings": _dataset_surface_status(
            "persona_bindings",
            snapshot_at=snapshot_at,
            has_data=bindings is not None,
        ),
        "runtime_binding": _dataset_surface_status(
            "runtime_bindings",
            snapshot_at=snapshot_at,
            has_data=runtime_binding is not None,
            missing_message="Runtime binding unavailable for this deployment plan.",
        ),
        "rollbacks": _dataset_surface_status("rollbacks", snapshot_at=snapshot_at),
        "allowedActions": _dataset_surface_status(
            "approval_decisions",
            snapshot_at=snapshot_at,
            has_data=_pkt001_allowed_actions_present(allowed_actions),
            missing_message="Deployment action authority unavailable for this deployment plan.",
        ),
        "latestRun": _dataset_surface_status(
            "latest_runs",
            snapshot_at=snapshot_at,
            has_data=latest_run is not None,
        ),
        "review": _dataset_surface_status(
            "review_summaries",
            snapshot_at=snapshot_at,
            has_data=review is not None,
        ),
    }

    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": surfaces,
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    degradation = _pkt001_degradation_meta(surfaces)
    if degradation:
        meta["degradation"] = degradation

    return {
        "data": data,
        "meta": meta,
    }


@app.get("/api/v1/operator/runtime-state")
async def list_operator_runtime_state(
    deployment_stage: Optional[str] = None,
    status: Optional[str] = None,
    sort_by: str = Query(default="last_updated_at"),
    sort_order: str = Query(default="desc"),
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    if sort_by not in _RUNTIME_STATE_SORT_FIELDS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid sort_by",
            f"sort_by must be one of {sorted(_RUNTIME_STATE_SORT_FIELDS)}",
        )
    if sort_order not in _RUNTIME_STATE_SORT_ORDERS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid sort_order",
            f"sort_order must be one of {sorted(_RUNTIME_STATE_SORT_ORDERS)}",
        )

    requested_stages = {
        value.lower() for value in (_split_csv_query(deployment_stage) or [])
    }
    requested_statuses = {
        value.lower() for value in (_split_csv_query(status) or [])
    }
    snapshot_at = utc_now()

    bindings = read_store.list_runtime_bindings()
    if requested_stages:
        bindings = [
            binding
            for binding in bindings
            if str(
                binding.get("deployment_stage") or binding.get("deployment_mode") or ""
            ).lower() in requested_stages
        ]
    if requested_statuses:
        bindings = [
            binding
            for binding in bindings
            if str(binding.get("status") or "").lower() in requested_statuses
        ]

    runtimes = [
        _project_operator_runtime_state_row(binding)
        for binding in bindings
    ]
    runtimes = _sort_runtime_state_rows(
        runtimes,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    runtime_roster_surface = _dataset_surface_status(
        "runtime_bindings",
        snapshot_at=snapshot_at,
    )
    telemetry_surface = _dataset_surface_status(
        "telemetry_summaries",
        snapshot_at=snapshot_at,
    )
    if runtimes and any(row.get("telemetry_summary") is None for row in runtimes):
        if telemetry_surface.get("status") == "ok":
            telemetry_surface["status"] = "degraded"
        telemetry_surface.setdefault(
            "message",
            "Telemetry summary unavailable for one or more runtimes on the runtime-state board.",
        )
        telemetry_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    rollback_history_surface = _dataset_surface_status(
        "rollbacks",
        snapshot_at=snapshot_at,
    )

    runtime_state_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=runtime_roster_surface.get("status") != "unavailable",
        missing_message="Runtime roster unavailable for the operator runtime-state board.",
    )
    if runtime_roster_surface.get("status") == "degraded":
        runtime_state_surface["status"] = "degraded"
    elif runtime_roster_surface.get("status") == "unavailable":
        runtime_state_surface["status"] = "unavailable"
    elif any(
        surface.get("status") != "ok"
        for surface in (telemetry_surface, rollback_history_surface)
    ):
        runtime_state_surface["status"] = "degraded"
        runtime_state_surface.setdefault(
            "message",
            "Runtime-state board is available, but one or more supporting surfaces are degraded or unavailable.",
        )
        runtime_state_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )

    total = len(runtimes)
    if runtime_state_surface.get("status") == "unavailable":
        runtimes = []
        next_page_token = None
    else:
        runtimes, next_page_token = _page_slice(runtimes, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["total"] = total
    meta["sort"] = {
        "sort_by": sort_by,
        "sort_order": sort_order,
    }
    meta["surfaces"] = {
        "runtime_state": runtime_state_surface,
        "runtime_roster": runtime_roster_surface,
        "telemetry_summary": telemetry_surface,
        "rollback_history": rollback_history_surface,
    }

    return {
        "runtimes": runtimes,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/alerts")
async def list_operator_alerts(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    return _build_operator_alerts_payload(snapshot_at)


@app.get("/api/v1/operator/home")
async def get_operator_home(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    return _build_operator_home_payload(snapshot_at)


@app.get("/api/v1/operator/paper-live-drift/{runtime_id}")
async def get_operator_paper_live_drift(
    runtime_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)
    report = read_store.get_paper_live_drift_report(runtime_id)
    if runtime_binding is None and report is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime drift view not found",
            f"Runtime {runtime_id} does not exist",
        )

    snapshot_at = utc_now()
    return _build_operator_paper_live_drift_payload(runtime_id, snapshot_at)


@app.get("/api/v1/operator/health-status")
async def get_operator_health_status(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    return _build_operator_health_status_payload(snapshot_at)


@app.get("/api/v1/workbench/consultation")
async def get_consultation_workbench_overview(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_consultation_workbench_overview(utc_now())


@app.post("/api/v1/consult/requests")
async def create_consult_request(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    """CW-01: Create a consult request."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    from_persona_id = _cw01_required_text(payload, "from_persona_id")
    target_type = _cw01_validate_target_type(payload.get("target_type"))
    target_ref = _cw01_required_text(payload, "target_ref")
    task = _cw01_required_text(payload, "task")
    context_refs = _cw01_validate_context_refs(payload.get("context_refs"))
    priority = _cw01_validate_priority(payload.get("priority"))
    consultation_type = _cw01_validate_consultation_type(payload.get("consultation_type"))

    req = read_store.create_consult_request(
        from_persona_id=from_persona_id,
        target_type=target_type,
        target_ref=target_ref,
        task=task,
        context_refs=context_refs,
        priority=priority,
        consultation_type=consultation_type,
        actor_id=identity.operator_id,
        created_at=utc_now(),
    )
    return {
        "request_id": req["request_id"],
        "status": req["status"],
        "created_at": req["created_at"],
        "linked_session_id": req["linked_session_id"],
        "request_to_session_status": req["request_to_session_status"],
        "allowedActions": req["allowedActions"],
    }


@app.get("/api/v1/consult/requests")
async def list_consult_requests(
    status: Optional[str] = None,
    target_type: Optional[str] = None,
    consultation_type: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """CW-01: List consult requests."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    statuses = _split_csv_query(status)
    items = read_store.list_consult_requests(
        statuses=statuses or None,
        target_type=target_type or None,
        consultation_type=consultation_type or None,
    )
    total = len(items)
    surface_state = _cw01_surface_state("consult_requests", snapshot_at=snapshot_at)
    if surface_state == "unavailable":
        page_items: List[Dict[str, Any]] = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(items, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"consult_request_list": surface_state}
    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/consult/requests/{request_id}")
async def get_consult_request(
    request_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CW-01: Get consult request detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    req = read_store.get_consult_request(request_id)
    if not req:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consult request not found",
            f"Consult request {request_id} does not exist",
        )

    snapshot_at = utc_now()
    payload = dict(req)
    payload["links"] = {
        "self": f"/api/v1/consult/requests/{request_id}",
        "workbench_detail": f"/consultation/requests/{request_id}",
    }
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "surfaces": {
            "consult_request_detail": _cw01_surface_state(
                "consult_requests",
                snapshot_at=snapshot_at,
                has_data=True,
            ),
        },
    }
    return payload


@app.post("/api/v1/consult/requests/{request_id}/cancel")
async def cancel_consult_request(
    request_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CW-01: Cancel a consult request."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    req = read_store.get_consult_request(request_id)
    if not req:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consult request not found",
            f"Consult request {request_id} does not exist",
        )

    if not req.get("allowedActions", {}).get("canCancel"):
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "Consult request cannot be canceled",
            f"allowedActions.canCancel is false for request {request_id}",
            precondition_failed="allowedActions.canCancel",
        )

    canceled = read_store.cancel_consult_request(
        request_id,
        actor_id=identity.operator_id,
        canceled_at=utc_now(),
    )
    if canceled is None:
        refreshed = read_store.get_consult_request(request_id)
        if refreshed and not refreshed.get("allowedActions", {}).get("canCancel"):
            raise _bff_error(
                409,
                ErrorCode.PRECONDITION_NOT_MET,
                "Consult request cannot be canceled",
                f"allowedActions.canCancel is false for request {request_id}",
                precondition_failed="allowedActions.canCancel",
            )
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Consult request store unavailable",
            "Cancel operation could not be persisted.",
        )
    return {
        "request_id": canceled["request_id"],
        "status": canceled["status"],
        "canceled_at": canceled["canceled_at"],
        "linked_session_id": canceled["linked_session_id"],
        "request_to_session_status": canceled["request_to_session_status"],
        "allowedActions": canceled["allowedActions"],
    }


@app.get("/api/v1/committees")
async def list_committees(
    quorum_state: Optional[str] = None,
    consensus_state: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    committees = read_store.list_committees(
        quorum_states=_split_csv_query(quorum_state),
        consensus_states=_split_csv_query(consensus_state),
    )
    surface_state = _dataset_surface_status(
        "consultation_sessions",
        snapshot_at=snapshot_at,
        missing_message="Committee board list is unavailable.",
    )

    if surface_state.get("status") == "unavailable":
        data = []
        total = 0
        next_page_token = None
    else:
        data, next_page_token = _page_slice(committees, page_token, page_size)
        total = len(committees)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "committee_board": (
            "degraded"
            if surface_state.get("source") == "local_snapshot"
            else surface_state.get("status")
        ),
    }

    return {
        "data": data,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/committees/{committee_id}")
async def get_committee(
    committee_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    committee = read_store.get_committee(committee_id)
    if committee is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee board not found",
            f"Committee {committee_id} does not exist",
        )

    return _cw03_committee_projection(
        committee,
        identity=identity,
        snapshot_at=utc_now(),
    )


@app.get("/api/v1/consult/memos")
async def list_consult_memos(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=25, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    requested_statuses = _cw04_validate_status_filters(_split_csv_query(status))
    memos = read_store.list_consult_memos(statuses=requested_statuses)
    surface_state = _cw04_collection_surface_state(snapshot_at)

    if surface_state == "unavailable":
        items = []
        total = 0
        next_page_token = None
    else:
        items, next_page_token = _page_slice(memos, page_token, page_size)
        total = len(memos)

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
            "page_size": page_size,
            "total": total,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "staleness": _cw04_memo_staleness(surface_state, snapshot_at=snapshot_at),
            "surfaces": {
                "redteam_memo": {
                    "state": surface_state,
                },
            },
        },
    }


@app.get("/api/v1/consult/memos/{memo_id}")
async def get_consult_memo(
    memo_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    memo = read_store.get_consult_memo(memo_id)
    if memo is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consult memo not found",
            f"Consult memo {memo_id} does not exist",
        )

    return _cw04_memo_projection(
        memo,
        identity=identity,
        snapshot_at=utc_now(),
    )


@app.get("/api/v1/workbench/knowledge")
async def get_knowledge_workbench_overview(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_knowledge_workbench_overview(utc_now())


def _build_research_oss_activation_ready_response(
    *,
    activity_limit: int,
    surface_key: str,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    data = read_store.get_research_oss_preactivation_snapshot(
        activity_limit=activity_limit,
    )
    service_surfaces = {
        service: {
            key: value
            for key, value in status.items()
            if key in {"status", "source", "reason", "activity_status", "upstream_status", "upstream_reachable"}
        }
        for service, status in data.get("service_status", {}).items()
        if isinstance(status, dict)
    }
    composite_status = "ok"
    if any(surface.get("status") == "unavailable" for surface in service_surfaces.values()):
        composite_status = "degraded"
    if service_surfaces and all(surface.get("status") == "unavailable" for surface in service_surfaces.values()):
        composite_status = "unavailable"

    composite_surface = {
        "status": composite_status,
        "source": "service_client",
    }
    alias_key = (
        "research_oss_preactivation"
        if surface_key == "research_oss_activation_ready"
        else "research_oss_activation_ready"
    )
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        surface_key: composite_surface,
        alias_key: composite_surface,
        **service_surfaces,
    }
    return {
        "data": data,
        "meta": meta,
    }


_OPENCLAW_COMMAND_ROLES = {"operator", "admin"}


def _require_openclaw_command_role(identity: OperatorIdentity) -> None:
    if _OPENCLAW_COMMAND_ROLES.intersection(identity.roles):
        return
    raise _bff_error(
        403,
        ErrorCode.INSUFFICIENT_ROLE,
        "OpenClaw operator commands require operator or admin role",
        "Operator does not hold the required OpenClaw command role",
        precondition_failed="role_check",
        suggestion="Escalate to a user with operator or admin role",
    )


def _require_openclaw_idempotency_key(value: Optional[str]) -> str:
    key = str(value or "").strip()
    if key:
        return key
    raise _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        "X-Idempotency-Key is required for OpenClaw operator commands",
        "OpenClaw session lifecycle commands must be idempotent at the BFF boundary",
        precondition_failed="idempotency_key",
        suggestion="Retry with a stable X-Idempotency-Key for this operator action",
    )


def _authorized_openclaw_operator_filter(
    identity: OperatorIdentity,
    operator_id: Optional[str],
) -> Optional[str]:
    clean = str(operator_id or "").strip() or None
    if clean and clean != identity.operator_id and "admin" not in identity.roles:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "OpenClaw operator filter is not authorized",
            "Non-admin operators may only filter OpenClaw sessions by their own operator id",
            precondition_failed="operator_filter",
            suggestion="Remove the operator_id filter or use an admin-role operator",
        )
    return clean


def _openclaw_ops_meta(snapshot_at: str, data: Dict[str, Any], surface_key: str) -> Dict[str, Any]:
    service_surfaces = {
        service: {
            key: value
            for key, value in status.items()
            if key in {"status", "source", "reason", "message", "http_status", "surface"}
        }
        for service, status in data.get("service_status", {}).items()
        if isinstance(status, dict)
    }
    overall = str(data.get("overall_status") or "degraded")
    alias_key = (
        "openclaw_tool_workflow_bridge"
        if surface_key == "openclaw_ops"
        else "openclaw_ops"
    )
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        surface_key: {"status": overall, "source": "service_client"},
        alias_key: {"status": overall, "source": "service_client"},
        **service_surfaces,
    }
    return meta


def _build_openclaw_ops_response(
    *,
    session_limit: int,
    audit_limit: int,
    state: Optional[str],
    operator_id: Optional[str],
    agent_id: Optional[str],
    effective_tools_session_id: Optional[str],
    requesting_operator_id: str,
    surface_key: str,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    data = read_store.get_openclaw_ops_snapshot(
        session_limit=session_limit,
        audit_limit=audit_limit,
        operator_id=operator_id,
        state=state,
        agent_id=agent_id,
        effective_tools_session_id=effective_tools_session_id,
        requesting_operator_id=requesting_operator_id,
    )
    return {
        "data": data,
        "meta": _openclaw_ops_meta(snapshot_at, data, surface_key),
    }


def _openclaw_client_error(exc: OpenClawOpsClientError) -> HTTPException:
    status_code = exc.status_code or 502
    if status_code == 404:
        code = ErrorCode.OBJECT_NOT_FOUND
    elif status_code == 409:
        code = ErrorCode.CONCURRENT_MODIFICATION
    elif status_code == 403:
        code = ErrorCode.PRECONDITION_NOT_MET
    elif status_code >= 500:
        code = ErrorCode.DOWNSTREAM_UNAVAILABLE
    else:
        code = ErrorCode.INVALID_PARAMS
    return _bff_error(
        status_code,
        code,
        exc.message,
        exc.error_code,
        precondition_failed="openclaw_adapter",
        suggestion="Inspect GET /api/v1/operator/openclaw/ops for current adapter degradation state",
    )


def _openclaw_command_payload(
    *,
    command: str,
    adapter_payload: Dict[str, Any],
    accepted_at: str,
) -> Dict[str, Any]:
    return {
        "data": {
            "command": command,
            "status": "accepted",
            "accepted_at": accepted_at,
            "adapter_status": adapter_payload.get("status"),
            "replayed": bool(adapter_payload.get("replayed")),
            "session": adapter_payload.get("session"),
        },
        "meta": {
            **_snapshot_meta(accepted_at),
            "surfaces": {
                "openclaw_command": {"status": "ok", "source": "service_client"},
            },
        },
    }


@app.get("/api/v1/operator/research/oss-activation-ready")
async def get_research_oss_activation_ready(
    activity_limit: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_research_oss_activation_ready_response(
        activity_limit=activity_limit,
        surface_key="research_oss_activation_ready",
    )


@app.get("/api/v1/operator/research/oss-preactivation")
async def get_research_oss_preactivation(
    activity_limit: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _build_research_oss_activation_ready_response(
        activity_limit=activity_limit,
        surface_key="research_oss_preactivation",
    )


@app.get("/api/v1/operator/openclaw/ops")
async def get_openclaw_ops(
    session_limit: int = Query(default=25, ge=1, le=100),
    audit_limit: int = Query(default=20, ge=1, le=100),
    state: Optional[str] = Query(default=None),
    operator_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    authorized_operator_id = _authorized_openclaw_operator_filter(identity, operator_id)
    return _build_openclaw_ops_response(
        session_limit=session_limit,
        audit_limit=audit_limit,
        state=state,
        operator_id=authorized_operator_id,
        agent_id=agent_id,
        effective_tools_session_id=session_id,
        requesting_operator_id=identity.operator_id,
        surface_key="openclaw_ops",
    )


@app.get("/api/v1/operator/openclaw/tool-workflow-bridge")
async def get_openclaw_tool_workflow_bridge(
    session_limit: int = Query(default=25, ge=1, le=100),
    audit_limit: int = Query(default=20, ge=1, le=100),
    state: Optional[str] = Query(default=None),
    operator_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
    session_id: Optional[str] = Query(default=None),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    authorized_operator_id = _authorized_openclaw_operator_filter(identity, operator_id)
    return _build_openclaw_ops_response(
        session_limit=session_limit,
        audit_limit=audit_limit,
        state=state,
        operator_id=authorized_operator_id,
        agent_id=agent_id,
        effective_tools_session_id=session_id,
        requesting_operator_id=identity.operator_id,
        surface_key="openclaw_tool_workflow_bridge",
    )


@app.post("/api/v1/operator/openclaw/sessions")
async def create_openclaw_session(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_openclaw_command_role(identity)
    idempotency_key = _require_openclaw_idempotency_key(x_idempotency_key)
    agent_id = str(payload.get("agent_id") or "").strip()
    session_type = str(payload.get("session_type") or "").strip()
    if not agent_id or not session_type:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "agent_id and session_type are required",
            "OpenClaw session create requires non-empty agent_id and session_type",
        )
    context_bundle = payload.get("context_bundle")
    if context_bundle is not None and not isinstance(context_bundle, dict):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "context_bundle must be an object when provided",
            "OpenClaw session context_bundle must be a JSON object",
        )
    try:
        adapter_payload = OpenClawOpsClient().create_session(
            agent_id=agent_id,
            session_type=session_type,
            operator_id=identity.operator_id,
            idempotency_key=idempotency_key,
            context_bundle=context_bundle,
        )
    except OpenClawOpsClientError as exc:
        raise _openclaw_client_error(exc) from exc
    accepted_at = utc_now()
    status_code = 200 if adapter_payload.get("replayed") else 202
    return JSONResponse(
        status_code=status_code,
        content=_openclaw_command_payload(
            command="OpenClawCreateSession",
            adapter_payload=adapter_payload,
            accepted_at=accepted_at,
        ),
    )


@app.post("/api/v1/operator/openclaw/sessions/{session_id}/cancel")
async def cancel_openclaw_session(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_openclaw_command_role(identity)
    idempotency_key = _require_openclaw_idempotency_key(x_idempotency_key)
    try:
        adapter_payload = OpenClawOpsClient().cancel_session(
            session_id=session_id,
            operator_id=identity.operator_id,
            idempotency_key=idempotency_key,
        )
    except OpenClawOpsClientError as exc:
        raise _openclaw_client_error(exc) from exc
    accepted_at = utc_now()
    return JSONResponse(
        status_code=202,
        content=_openclaw_command_payload(
            command="OpenClawCancelSession",
            adapter_payload=adapter_payload,
            accepted_at=accepted_at,
        ),
    )


# --------------------------------------------------------------------------- #
# OpenClaw Live Gate Operator Surface (SVC-OPENCLAW-LIVE-GATE-HARNESS)
# Read-only BFF projections: status and audit trail.
# Dry handoff and gate validate remain on the adapter (require X-Human-Approval-Token).
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/openclaw/live-gate/status")
async def get_openclaw_live_gate_status(
    authorization: Optional[str] = Header(default=None),
):
    """Return live gate capability and configuration status.

    Reflects whether the live gate harness is enabled and which gate checks are
    configured, without performing any gate evaluation.  Always fail-closed on
    the live path.
    """
    identity = _extract_identity(authorization)
    _require_openclaw_command_role(identity)
    try:
        payload = OpenClawOpsClient().get_live_gate_status()
    except OpenClawOpsClientError as exc:
        raise _openclaw_client_error(exc) from exc
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "surface": "openclaw_live_gate_status",
            "data": payload,
            "snapshot_at": utc_now(),
        },
    )


@app.get("/api/v1/operator/openclaw/live-gate/audit")
async def get_openclaw_live_gate_audit(
    capital_pool_id: Optional[str] = None,
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
):
    """Return the live gate intent and outcome audit trail.

    Scoped to the requesting operator when non-admin; admins may pass
    capital_pool_id to filter across pools.
    """
    identity = _extract_identity(authorization)
    _require_openclaw_command_role(identity)
    operator_id = None if "admin" in identity.roles else identity.operator_id
    try:
        payload = OpenClawOpsClient().list_live_gate_audit(
            operator_id=operator_id,
            capital_pool_id=capital_pool_id,
            limit=limit,
        )
    except OpenClawOpsClientError as exc:
        raise _openclaw_client_error(exc) from exc
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "surface": "openclaw_live_gate_audit",
            "data": payload,
            "snapshot_at": utc_now(),
        },
    )


@app.get(
    "/api/v1/operator/openclaw/broker-adapter-readiness",
    operation_id="get_openclaw_broker_adapter_readiness_legacy",
)
@app.get(
    "/api/v1/operator/openclaw/broker/adapter-readiness",
    operation_id="get_openclaw_broker_adapter_readiness",
)
async def get_openclaw_broker_adapter_readiness(
    authorization: Optional[str] = Header(default=None),
):
    """Return broker adapter capability states and gate reasons.

    Projects sandbox/paper/canary/live adapter states with the gate reason for
    each capability without claiming live activation.  Live and canary paths are
    always fail-closed.  The BFF does not expose activation commands; enablement
    requires explicit gate configuration outside the BFF.
    """
    identity = _extract_identity(authorization)
    _require_openclaw_command_role(identity)
    surface = read_store.get_openclaw_broker_adapter_readiness()
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "surface": "openclaw_broker_adapter_readiness",
            "data": surface,
            "snapshot_at": utc_now(),
        },
    )


# --------------------------------------------------------------------------- #
# Source / Search Operator Ops Surface (SVC-SOURCE-SEARCH-OPS-BFF)
# --------------------------------------------------------------------------- #

_SOURCE_SEARCH_COMMAND_ROLES = {"operator", "admin"}


def _require_source_search_command_role(identity: OperatorIdentity) -> None:
    if _SOURCE_SEARCH_COMMAND_ROLES.intersection(identity.roles):
        return
    raise _bff_error(
        403,
        ErrorCode.INSUFFICIENT_ROLE,
        "Source/search operator commands require operator or admin role",
        "Operator does not hold the required source/search command role",
        precondition_failed="role_check",
        suggestion="Escalate to a user with operator or admin role",
    )


def _require_source_search_idempotency_key(value: Optional[str]) -> str:
    key = str(value or "").strip()
    if key:
        return key
    raise _bff_error(
        400,
        ErrorCode.INVALID_PARAMS,
        "X-Idempotency-Key is required for source/search operator commands",
        "Source/search commands must be idempotent at the BFF boundary",
        precondition_failed="idempotency_key",
        suggestion="Retry with a stable X-Idempotency-Key for this operator action",
    )


def _source_search_client_error(exc: SourceSearchOpsClientError) -> HTTPException:
    status_code = exc.status_code or 502
    if status_code == 404:
        code = ErrorCode.OBJECT_NOT_FOUND
    elif status_code == 409:
        code = ErrorCode.CONCURRENT_MODIFICATION
    elif status_code == 403:
        code = ErrorCode.PRECONDITION_NOT_MET
    elif status_code >= 500:
        code = ErrorCode.DOWNSTREAM_UNAVAILABLE
    else:
        code = ErrorCode.INVALID_PARAMS
    return _bff_error(
        status_code,
        code,
        exc.message,
        exc.error_code,
        precondition_failed="source_search_service",
        suggestion="Inspect GET /api/v1/operator/source/ops or /api/v1/operator/search/ops for current state",
    )


def _source_search_command_payload(
    *,
    command: str,
    service_payload: Dict[str, Any],
    accepted_at: str,
) -> Dict[str, Any]:
    return {
        "data": {
            "command": command,
            "status": "accepted",
            "accepted_at": accepted_at,
            "service_result": service_payload,
        },
        "meta": {
            **_snapshot_meta(accepted_at),
            "surfaces": {
                "source_search_command": {"status": "ok", "source": "service_client"},
            },
        },
    }


@app.get("/api/v1/operator/source/ops")
async def get_source_ops(
    crawl_run_limit: int = Query(default=50, ge=1, le=200),
    dlq_status: Optional[str] = Query(default=None),
    frontier_status: Optional[str] = Query(default=None),
    audit_limit: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    """Source-ingestion operator ops surface.

    Returns connector health, recent crawl runs, DLQ state, crawl frontier,
    and audit summary.  The BFF never reads source volumes directly.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    data = read_store.get_source_ops_snapshot(
        crawl_run_limit=crawl_run_limit,
        dlq_status=dlq_status,
        frontier_status=frontier_status,
        audit_limit=audit_limit,
    )
    source_status = "ok" if data.get("source") == "service_client" else data.get("source", "unavailable")
    return {
        "data": data,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "source_ops": {"status": source_status, "source": data.get("source", "unavailable")},
            },
        },
    }


@app.get("/api/v1/operator/search/ops")
async def get_search_ops(
    pipeline_run_limit: int = Query(default=50, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """Search-index operator ops surface.

    Returns index freshness, recent pipeline runs, and materialized index snapshot.
    The BFF never reads search volumes directly.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    data = read_store.get_search_ops_snapshot(
        pipeline_run_limit=pipeline_run_limit,
    )
    source_status = "ok" if data.get("source") == "service_client" else data.get("source", "unavailable")
    freshness_ok = data.get("summary", {}).get("freshness_ok", False)
    if source_status == "ok" and not freshness_ok:
        source_status = "stale"
    return {
        "data": data,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "search_ops": {"status": source_status, "source": data.get("source", "unavailable")},
            },
        },
    }


@app.post("/api/v1/operator/source/dlq/replay", status_code=202)
async def replay_source_dlq(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """Replay pending source-ingest DLQ entries.

    Idempotent: pass the same X-Idempotency-Key to safely retry without
    double-processing.  Auth guarded to operator/admin role.
    """
    identity = _extract_identity(authorization)
    _require_source_search_command_role(identity)
    idempotency_key = _require_source_search_idempotency_key(x_idempotency_key)
    entry_ids = payload.get("entry_ids") if isinstance(payload.get("entry_ids"), list) else None
    tag = str(payload.get("tag") or "retry_exhausted")
    reason = str(payload.get("reason") or f"operator-approved BFF DLQ replay by {identity.operator_id}")
    try:
        result = SourceIngestCommandClient().replay_dlq(
            entry_ids=entry_ids,
            tag=tag,
            reason=reason,
            actor_id=identity.operator_id,
            idempotency_key=idempotency_key,
        )
    except SourceSearchOpsClientError as exc:
        raise _source_search_client_error(exc) from exc
    return JSONResponse(
        status_code=202,
        content=_source_search_command_payload(
            command="SourceDLQReplay",
            service_payload=result,
            accepted_at=utc_now(),
        ),
    )


@app.post("/api/v1/operator/source/frontier/{frontier_id}/replay", status_code=202)
async def replay_source_frontier(
    frontier_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """Replay a specific crawl frontier item.

    Idempotent: retrying the same frontier_id with the same X-Idempotency-Key
    is safe.  Auth guarded to operator/admin role.
    """
    identity = _extract_identity(authorization)
    _require_source_search_command_role(identity)
    idempotency_key = _require_source_search_idempotency_key(x_idempotency_key)
    if not frontier_id.strip():
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "frontier_id is required",
            "frontier_id must be a non-empty string",
        )
    trace_id = str(payload.get("trace_id") or "").strip() or None
    try:
        result = SourceIngestCommandClient().replay_frontier(
            frontier_id=frontier_id.strip(),
            trace_id=trace_id,
            idempotency_key=idempotency_key,
        )
    except SourceSearchOpsClientError as exc:
        raise _source_search_client_error(exc) from exc
    return JSONResponse(
        status_code=202,
        content=_source_search_command_payload(
            command="SourceFrontierReplay",
            service_payload=result,
            accepted_at=utc_now(),
        ),
    )


@app.post("/api/v1/operator/search/index/refresh", status_code=202)
async def trigger_search_index_refresh(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """Trigger an incremental (or full) search index refresh.

    Idempotent: same X-Idempotency-Key can be retried safely.
    Auth guarded to operator/admin role.
    """
    identity = _extract_identity(authorization)
    _require_source_search_command_role(identity)
    idempotency_key = _require_source_search_idempotency_key(x_idempotency_key)
    triggered_by = str(payload.get("triggered_by") or f"bff-operator:{identity.operator_id}")
    trigger_ref = str(payload.get("trigger_ref") or "").strip() or None
    force_full = bool(payload.get("force_full", False))
    try:
        result = SearchIndexCommandClient().trigger_refresh(
            triggered_by=triggered_by,
            trigger_ref=trigger_ref,
            force_full=force_full,
            idempotency_key=idempotency_key,
        )
    except SourceSearchOpsClientError as exc:
        raise _source_search_client_error(exc) from exc
    return JSONResponse(
        status_code=202,
        content=_source_search_command_payload(
            command="SearchIndexRefresh",
            service_payload=result,
            accepted_at=utc_now(),
        ),
    )


@app.post("/api/v1/operator/search/index/materialize", status_code=202)
async def trigger_search_index_materialize(
    authorization: Optional[str] = Header(default=None),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """Materialize a search index snapshot for recovery and audit.

    Idempotent: same X-Idempotency-Key can be retried safely.
    Auth guarded to operator/admin role.
    """
    identity = _extract_identity(authorization)
    _require_source_search_command_role(identity)
    idempotency_key = _require_source_search_idempotency_key(x_idempotency_key)
    try:
        result = SearchIndexCommandClient().trigger_materialize(
            idempotency_key=idempotency_key,
        )
    except SourceSearchOpsClientError as exc:
        raise _source_search_client_error(exc) from exc
    return JSONResponse(
        status_code=202,
        content=_source_search_command_payload(
            command="SearchIndexMaterialize",
            service_payload=result,
            accepted_at=utc_now(),
        ),
    )


@app.post("/api/v1/research/tickets")
async def create_research_ticket(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    title = _rw01_required_text(payload, "title")
    description = _rw01_required_text(payload, "description")
    owner = _rw01_required_text(payload, "owner")
    priority = _rw01_validate_priority(payload.get("priority"))

    ticket = read_store.create_research_ticket(
        title=title,
        description=description,
        priority=priority,
        owner=owner,
        actor_id=identity.operator_id,
        created_at=utc_now(),
    )
    return {
        "ticket_id": ticket["ticket_id"],
        "status": ticket["status"],
        "created_at": ticket["created_at"],
        "allowedActions": ticket["allowedActions"],
    }


@app.get("/api/v1/research/tickets")
async def list_research_tickets(
    status: Optional[str] = None,
    owner: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    statuses = _split_csv_query(status)
    if statuses:
        statuses = [_rw01_validate_status(value) for value in statuses]

    items = read_store.list_research_tickets(statuses=statuses, owner=owner)
    total = len(items)
    surface_state = _rw01_surface_state("research_tickets", snapshot_at=snapshot_at)
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(items, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "ticket_list": surface_state,
    }
    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/research/tickets/{ticket_id}")
async def get_research_ticket(
    ticket_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    ticket = read_store.get_research_ticket(
        ticket_id,
        include_snapshot_fallback=False,
        include_local_fallback=False,
    )
    if not ticket:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Research ticket not found",
            f"Research ticket {ticket_id} does not exist",
        )

    snapshot_at = utc_now()
    payload = dict(ticket)
    payload["links"] = {
        "self": f"/api/v1/research/tickets/{ticket_id}",
        "workbench_detail": f"/research/tickets/{ticket_id}",
    }
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "surfaces": {
            "ticket_detail": _rw01_surface_state(
                "research_tickets",
                snapshot_at=snapshot_at,
                has_data=True,
            ),
        },
    }
    return payload


@app.patch("/api/v1/research/tickets/{ticket_id}")
async def patch_research_ticket(
    ticket_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    ticket = read_store.get_research_ticket(ticket_id)
    if not ticket:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Research ticket not found",
            f"Research ticket {ticket_id} does not exist",
        )

    patch = _rw01_validate_patch(ticket, payload)
    updated = read_store.patch_research_ticket(
        ticket_id,
        patch=patch,
        actor_id=identity.operator_id,
        updated_at=utc_now(),
    )
    if updated is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Research ticket store unavailable",
            "Research ticket update store is unavailable.",
        )

    return {
        "ticket_id": updated["ticket_id"],
        "status": updated["status"],
        "updated_at": updated["updated_at"],
        "allowedActions": updated["allowedActions"],
    }


@app.get("/api/v1/research/search")
async def search_research_corpus(
    q: str,
    match_type: str = "all",
    status: Optional[str] = None,
    date_range: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=25, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    try:
        query = _rw02_validate_query(q)
        match_type = _rw02_validate_match_type(match_type)
        status = _rw02_validate_status(status)
        date_range = _rw02_validate_date_range(date_range)
    except ValueError as exc:
        return _rw02_invalid_query(str(exc))

    snapshot_at = utc_now()
    index_adapter = read_store.get_research_search_index()
    adapter_state = _rw02_adapter_state(index_adapter, snapshot_at=snapshot_at)
    if index_adapter is None or adapter_state == "unavailable":
        return JSONResponse(
            status_code=503,
            content={
                "error": "search_unavailable",
                "meta": {
                    "surfaces": {
                        "search_results": "unavailable",
                    }
                },
            },
        )

    items = read_store.list_research_search_results(
        query=query,
        match_type=match_type,
        status=status,
        date_range=date_range,
    )
    total = len(items)
    try:
        page_items, next_page_token = _rw02_page_slice(items, page_token, page_size)
    except ValueError as exc:
        return _rw02_invalid_query(str(exc))

    index_snapshot_at = str(index_adapter.get("snapshot_at") or snapshot_at)
    source_watermarks = index_adapter.get("source_watermarks")
    if not isinstance(source_watermarks, dict):
        source_watermarks = {}
    indexed_match_types = index_adapter.get("indexed_match_types")
    if not isinstance(indexed_match_types, list):
        indexed_match_types = []

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "search_results": adapter_state,
    }
    meta["index_adapter"] = {
        "snapshot_at": index_snapshot_at,
        "adapter_state": adapter_state,
        "indexed_match_types": [
            str(value)
            for value in indexed_match_types
            if str(value).strip()
        ],
        "source_watermarks": {
            "tickets": source_watermarks.get("tickets"),
            "experiments": source_watermarks.get("experiments"),
            "artifacts": source_watermarks.get("artifacts"),
        },
    }
    governed_evidence = read_store.get_last_governed_search_refs()
    if governed_evidence:
        meta["governed_evidence"] = governed_evidence
    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/research/source-connectors")
async def list_source_connectors(
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    registry = read_store.get_source_connector_registry()
    source = str(registry.get("source") or "missing")
    surface_state = "ok" if source == "service_client" else "unavailable"
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "source_connector_registry": surface_state,
    }
    meta["source"] = source
    meta["provider_examples"] = list(registry.get("provider_examples") or [])
    meta["policy_registry"] = registry.get("policy_registry")
    return {
        "data": list(registry.get("connectors") or []),
        "meta": meta,
    }


@app.get("/api/v1/research/analysis")
async def list_research_analysis(
    ticket_id: Optional[str] = None,
    experiment_id: Optional[str] = None,
    status: Optional[str] = None,
    date_range: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    statuses = _split_csv_query(status)
    if statuses:
        statuses = [_rw03_validate_status(value) for value in statuses]
    if date_range is not None:
        date_range = _rw03_validate_date_range(date_range)

    source = read_store.dataset_source(
        "research_analyses",
        include_snapshot_fallback=False,
        include_local_fallback=False,
    )
    items = read_store.list_research_analyses(
        ticket_id=ticket_id,
        experiment_id=experiment_id,
        statuses=statuses,
        date_range=date_range,
        include_snapshot_fallback=False,
        include_local_fallback=False,
    )
    total = len(items)
    surface_state = _rw01_surface_state(
        "research_analyses",
        snapshot_at=snapshot_at,
        source=source,
    )
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(items, page_token, page_size)

    for item in page_items:
        analysis_id = str(item.get("analysis_id") or "")
        ticket_ref = str(item.get("ticket_id") or "")
        item["links"] = {
            "self": f"/api/v1/research/analysis/{analysis_id}",
            "workbench_detail": f"/research/analyze/{analysis_id}",
            "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
        }

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "analysis_results": surface_state,
    }
    return {
        "data": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "total": total,
        },
        "meta": meta,
    }


@app.get("/api/v1/research/analysis/{analysis_id}")
async def get_research_analysis(
    analysis_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    source = read_store.dataset_source(
        "research_analyses",
        include_snapshot_fallback=False,
        include_local_fallback=False,
    )
    analysis = read_store.get_research_analysis(
        analysis_id,
        include_snapshot_fallback=False,
        include_local_fallback=False,
    )
    if not analysis:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Research analysis not found",
            f"Research analysis {analysis_id} does not exist",
        )

    snapshot_at = utc_now()
    ticket_ref = str(analysis.get("ticket_id") or "")
    experiment_ref = analysis.get("experiment_id")
    payload = dict(analysis)
    payload["links"] = {
        "self": f"/api/v1/research/analysis/{analysis_id}",
        "workbench_detail": f"/research/analyze/{analysis_id}",
        "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
        "linked_experiment_detail": (
            f"/research/experiments/{experiment_ref}" if experiment_ref else None
        ),
    }
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "surfaces": {
            "analysis_results": _rw01_surface_state(
                "research_analyses",
                snapshot_at=snapshot_at,
                has_data=True,
                source=source,
            ),
        },
    }
    return payload


# --------------------------------------------------------------------------- #
# RW-04 — Experiment Launch helpers
# --------------------------------------------------------------------------- #

_RW04_ALLOWED_STATUSES = {"queued", "running", "completed", "failed", "canceled"}
_RW04_ALLOWED_EXECUTION_MODES = {"paper", "backtest", "simulation"}
_RW04_ALLOWED_PRIORITIES = {"normal", "high"}


def _rw04_validate_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _RW04_ALLOWED_STATUSES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid experiment status",
            f"status must be one of {sorted(_RW04_ALLOWED_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _rw04_surface_state(
    snapshot_at: str,
    *,
    has_data: Optional[bool] = None,
    source: Optional[str] = None,
) -> str:
    return _rw01_surface_state(
        "research_experiments",
        snapshot_at=snapshot_at,
        has_data=has_data,
        source=source,
    )


def _rw05_surface_state(snapshot_at: str, *, has_data: Optional[bool] = None) -> str:
    surface = _dataset_surface_status(
        "research_artifacts",
        snapshot_at=snapshot_at,
        has_data=has_data,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("source") == "local_snapshot":
        return "degraded"
    if surface.get("status") == "degraded":
        return "degraded"
    return "ok"


def _rw04_required_text(payload: Dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if value is None or not str(value).strip():
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"Missing required field: {field}",
            f"{field} must be a non-empty string",
            precondition_failed=field,
        )
    return str(value).strip()


def _rw05_validate_status(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized not in _RW05_ALLOWED_STATUSES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid artifact status",
            f"status must be one of {sorted(_RW05_ALLOWED_STATUSES)}",
            precondition_failed="status",
        )
    return normalized


def _rw04_required_dict(payload: Dict[str, Any], field: str) -> Dict[str, Any]:
    value = payload.get(field)
    if not isinstance(value, dict):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            f"Missing or invalid field: {field}",
            f"{field} must be an object",
            precondition_failed=field,
        )
    return value


def _rw04_validate_run_config(run_config: Dict[str, Any]) -> Dict[str, Any]:
    dataset_ref = _rw04_required_text(run_config, "dataset_ref")
    time_range = _rw04_required_dict(run_config, "time_range")
    start_at = _rw04_required_text(time_range, "start_at")
    end_at = _rw04_required_text(time_range, "end_at")
    execution_mode = str(run_config.get("execution_mode") or "").strip().lower()
    if execution_mode not in _RW04_ALLOWED_EXECUTION_MODES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid execution_mode",
            f"execution_mode must be one of {sorted(_RW04_ALLOWED_EXECUTION_MODES)}",
            precondition_failed="execution_mode",
        )
    priority = str(run_config.get("priority") or "normal").strip().lower()
    if priority not in _RW04_ALLOWED_PRIORITIES:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid priority",
            f"priority must be one of {sorted(_RW04_ALLOWED_PRIORITIES)}",
            precondition_failed="priority",
        )
    requested_by = _rw04_required_text(run_config, "requested_by")
    return {
        "dataset_ref": dataset_ref,
        "time_range": {"start_at": start_at, "end_at": end_at},
        "execution_mode": execution_mode,
        "priority": priority,
        "requested_by": requested_by,
    }


@app.post("/api/v1/experiments/launch")
async def launch_experiment(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    ticket_id = _rw04_required_text(payload, "ticket_id")
    experiment_name = _rw04_required_text(payload, "experiment_name")
    strategy_selector = _rw04_required_dict(payload, "strategy_selector")
    parameter_set = _rw04_required_dict(payload, "parameter_set")
    run_config_raw = _rw04_required_dict(payload, "run_config")
    run_config = _rw04_validate_run_config(run_config_raw)
    launch_context_raw = payload.get("launch_context") or {}
    analysis_refs = launch_context_raw.get("analysis_refs")
    if analysis_refs is not None and not isinstance(analysis_refs, list):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Invalid launch_context.analysis_refs",
            "analysis_refs must be null or an array of strings",
            precondition_failed="launch_context.analysis_refs",
        )
    launch_context = {"analysis_refs": list(analysis_refs) if analysis_refs is not None else None}

    experiment = read_store.create_research_experiment(
        ticket_id=ticket_id,
        experiment_name=experiment_name,
        strategy_selector=strategy_selector,
        parameter_set=parameter_set,
        run_config=run_config,
        launch_context=launch_context,
    )

    experiment_id = experiment["experiment_id"]
    return {
        "experiment_id": experiment_id,
        "ticket_id": experiment["ticket_id"],
        "status": experiment["status"],
        "queued_at": experiment["queued_at"],
        "allowedActions": {"canCancel": True},
        "links": {
            "self": f"/api/v1/experiments/{experiment_id}",
            "workbench_detail": f"/research/experiments/{experiment_id}",
        },
    }


@app.get("/api/v1/experiments")
async def list_experiments(
    ticket_id: Optional[str] = None,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    validated_status: Optional[str] = None
    if status is not None:
        validated_status = _rw04_validate_status(status)

    snapshot_at = utc_now()
    items = read_store.list_research_experiments(
        ticket_id=ticket_id,
        status=validated_status,
    )
    total = len(items)
    experiment_source = read_store.dataset_source("research_experiments")
    if experiment_source == "missing" and items:
        experiment_source = "bff_local"
    surface_state = _rw04_surface_state(
        snapshot_at,
        has_data=bool(items),
        source=experiment_source,
    )
    if surface_state == "unavailable":
        page_items: List[Dict[str, Any]] = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(items, page_token, page_size)

    for item in page_items:
        exp_id = str(item.get("experiment_id") or "")
        ticket_ref = str(item.get("ticket_id") or "")
        item["links"] = {
            "self": f"/api/v1/experiments/{exp_id}",
            "workbench_detail": f"/research/experiments/{exp_id}",
        }
        item["allowedActions"] = {"canCancel": item.get("allowedActions", {}).get("canCancel", False)}
        _ = ticket_ref

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"experiment_history": surface_state}
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": meta,
    }


@app.get("/api/v1/experiments/{experiment_id}")
async def get_experiment(
    experiment_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    experiment = read_store.get_research_experiment(experiment_id)
    if not experiment:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experiment_id} does not exist",
        )

    snapshot_at = utc_now()
    ticket_ref = str(experiment.get("ticket_id") or "")
    payload = dict(experiment)
    payload["links"] = {
        "self": f"/api/v1/experiments/{experiment_id}",
        "workbench_detail": f"/research/experiments/{experiment_id}",
        "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
    }
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "surfaces": {
            "experiment_status": _rw04_surface_state(snapshot_at, has_data=True),
        },
    }
    return payload


@app.post("/api/v1/experiments/{experiment_id}/cancel")
async def cancel_experiment(
    experiment_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Missing required field: reason",
            "reason must be a non-empty string",
            precondition_failed="reason",
        )

    experiment = read_store.get_research_experiment(experiment_id)
    if not experiment:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experiment_id} does not exist",
        )

    status = str(experiment.get("status") or "")
    if status not in {"queued", "running"}:
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Experiment cannot be canceled",
            f"Experiment {experiment_id} is in terminal state '{status}' and cannot be canceled",
        )

    canceled = read_store.cancel_research_experiment(experiment_id)
    if not canceled:
        raise _bff_error(
            409,
            ErrorCode.INVALID_STATE,
            "Experiment cancel rejected",
            f"Experiment {experiment_id} could not be canceled; it may have already reached a terminal state",
        )

    return {
        "experiment_id": experiment_id,
        "status": canceled["status"],
        "completed_at": canceled["completed_at"],
        "allowedActions": {"canCancel": False},
    }


@app.get("/api/v1/artifacts")
async def list_artifacts(
    experiment_id: Optional[str] = None,
    ticket_id: Optional[str] = None,
    lineage_id: Optional[str] = None,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    validated_status = _rw05_validate_status(status)
    snapshot_at = utc_now()
    items = read_store.list_research_artifacts(
        experiment_id=experiment_id,
        ticket_id=ticket_id,
        lineage_id=lineage_id,
        status=validated_status,
    )
    total = len(items)
    surface_state = _rw05_surface_state(snapshot_at, has_data=bool(items))
    if surface_state == "unavailable":
        page_items: List[Dict[str, Any]] = []
        next_page_token = None
        total = 0
    else:
        page_items, next_page_token = _page_slice(items, page_token, page_size)

    return {
        "artifacts": page_items,
        "next_page_token": next_page_token,
        "total_count": total,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {"artifact_list": surface_state},
        },
    }


@app.get("/api/v1/artifacts/compare")
async def compare_artifacts(
    artifact_ids: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    requested_ids = [
        str(artifact_id).strip()
        for artifact_id in artifact_ids.split(",")
        if str(artifact_id).strip()
    ]
    if len(requested_ids) < 2:
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "At least two artifact_ids are required",
            "artifact_ids must include between 2 and 4 artifact ids",
            precondition_failed="artifact_ids",
        )
    if len(requested_ids) > 4:
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "Too many artifact_ids provided",
            "artifact_ids must include between 2 and 4 artifact ids",
            precondition_failed="artifact_ids",
        )

    artifacts: List[Dict[str, Any]] = []
    for artifact_id in requested_ids:
        artifact = read_store.get_research_artifact(artifact_id)
        if not artifact:
            raise _bff_error(
                404,
                ErrorCode.OBJECT_NOT_FOUND,
                "Artifact not found",
                f"Artifact {artifact_id} does not exist",
            )
        artifacts.append(artifact)

    non_comparable = [
        {
            "artifact_id": artifact.get("artifact_id"),
            "status": artifact.get("status"),
            "reason": "Only sealed and superseded artifacts may be compared.",
        }
        for artifact in artifacts
        if not (artifact.get("allowedActions") or {}).get("canCompare")
    ]
    if non_comparable:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": ErrorCode.INVALID_STATE.value,
                    "message": "One or more artifacts cannot be compared",
                    "details": {
                        "reason": "Compare accepts only sealed or superseded artifacts.",
                        "precondition_failed": "artifact_status",
                    },
                },
                "non_comparable_artifacts": non_comparable,
            },
        )

    snapshot_at = utc_now()
    payload = read_store.compare_research_artifacts(requested_ids)
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "computed_at": utc_now(),
        "surfaces": {"artifact_compare": _rw05_surface_state(snapshot_at, has_data=True)},
    }
    return payload


@app.get("/api/v1/artifacts/{artifact_id}")
async def get_artifact(
    artifact_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    artifact = read_store.get_research_artifact(artifact_id)
    if not artifact:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Artifact not found",
            f"Artifact {artifact_id} does not exist",
        )

    snapshot_at = utc_now()
    payload = dict(artifact)
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "surfaces": {"artifact_detail": _rw05_surface_state(snapshot_at, has_data=True)},
    }
    return payload


@app.post("/api/v1/knowledge/notes", status_code=201)
async def create_research_note(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    if "owner_ref" in payload:
        raise _kw02_bad_request(
            "Invalid owner_ref",
            "owner_ref is server-assigned and must not be supplied by the caller",
            "owner_ref",
        )

    title = _kw02_optional_title(payload)
    body = _kw02_required_body(payload)
    validated_attachment_type = _kw02_validate_attachment_type(payload.get("attachment_type"))
    validated_attachment_ref = _kw02_validate_attachment_ref(
        validated_attachment_type,
        payload.get("attachment_ref"),
    )
    tags = _kw02_validate_string_list(payload.get("tags"), "tags")
    linked_evidence_refs = _kw02_validate_string_list(
        payload.get("linked_evidence_refs"),
        "linked_evidence_refs",
    )
    linked_memory_anchors = _kw02_validate_memory_anchors(
        _kw02_validate_string_list(payload.get("linked_memory_anchors"), "linked_memory_anchors")
    )

    attachment_exists, _, _ = _kw02_resolve_attachment_target(
        validated_attachment_type,
        validated_attachment_ref,
    )
    if not attachment_exists:
        raise _bff_error(
            422,
            ErrorCode.PRECONDITION_NOT_MET,
            "Attachment target does not exist",
            f"{validated_attachment_type} target {validated_attachment_ref} could not be resolved",
            precondition_failed="attachment_ref",
        )

    timestamp = utc_now()
    note_id = f"note-{uuid.uuid4()}"
    note = {
        "note_id": note_id,
        "title": title,
        "body": body,
        "attachment_type": validated_attachment_type,
        "attachment_ref": validated_attachment_ref,
        "owner_ref": {
            "owner_type": "operator",
            "owner_id": identity.operator_id,
            "display_name": _kw02_operator_display_name(identity.operator_id),
        },
        "tags": tags,
        "linked_evidence_refs": linked_evidence_refs,
        "linked_memory_anchors": linked_memory_anchors,
        "created_at": timestamp,
        "updated_at": timestamp,
    }

    created = read_store.create_research_note(note)
    if created is None:
        raise _bff_error(
            503,
            ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Research note store unavailable",
            "Research note creation store is unavailable.",
        )

    return {
        "note_id": note_id,
        "created_at": timestamp,
        "route_href": f"/knowledge/notes/{note_id}",
    }


@app.get("/api/v1/knowledge/notes")
async def list_research_notes(
    owner_ref: Optional[str] = None,
    attachment_type: Optional[str] = None,
    attachment_ref: Optional[str] = None,
    tags: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    validated_attachment_type = None
    if attachment_type is not None:
        validated_attachment_type = _kw02_validate_attachment_type(attachment_type)
    if attachment_ref is not None and validated_attachment_type is None:
        raise _kw02_bad_request(
            "Invalid attachment_ref filter",
            "attachment_ref requires attachment_type to be set",
            "attachment_ref",
        )
    validated_attachment_ref = None
    if validated_attachment_type is not None and attachment_ref is not None:
        validated_attachment_ref = _kw02_validate_attachment_ref(
            validated_attachment_type,
            attachment_ref,
        )

    snapshot_at = utc_now()
    notes = read_store.list_research_notes()
    notes_dataset_available = read_store.dataset_source("research_notes") != "missing"
    if owner_ref:
        notes = [
            note
            for note in notes
            if str(((note.get("owner_ref") or {}).get("owner_id")) or "") == owner_ref
        ]
    if validated_attachment_type:
        notes = [
            note
            for note in notes
            if str(note.get("attachment_type") or "") == validated_attachment_type
        ]
    if validated_attachment_type == "free_standing" or validated_attachment_ref is not None:
        notes = [
            note
            for note in notes
            if note.get("attachment_ref") == validated_attachment_ref
        ]
    if tags:
        requested_tags = {value.strip() for value in tags.split(",") if value.strip()}
        notes = [
            note
            for note in notes
            if requested_tags.intersection(set(note.get("tags") or []))
        ]

    surface_state = _kw02_surface_state(
        "research_notes",
        snapshot_at=snapshot_at,
        has_data=notes_dataset_available,
    )
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        has_more = False
    else:
        page_items, next_page_token = _page_slice(notes, page_token, page_size)
        has_more = next_page_token is not None

    return {
        "notes": [_kw02_note_list_item(note) for note in page_items],
        "pagination": {
            "page_size": page_size,
            "next_page_token": next_page_token,
            "has_more": has_more,
        },
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {"research_note_list": surface_state},
        },
    }


@app.get("/api/v1/knowledge/notes/{note_id}")
async def get_research_note_detail(
    note_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    note = read_store.get_research_note(note_id)
    if not note:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Research note not found",
            f"Research note {note_id} does not exist",
        )

    snapshot_at = utc_now()
    evidence_links, evidence_surface = _kw02_resolve_evidence_links(
        list(note.get("linked_evidence_refs") or []),
        snapshot_at=snapshot_at,
    )
    memory_anchors, memory_surface = _kw02_resolve_memory_anchors(
        list(note.get("linked_memory_anchors") or []),
        snapshot_at=snapshot_at,
    )

    return {
        "note_id": note.get("note_id"),
        "title": note.get("title"),
        "body": note.get("body"),
        "owner_ref": json.loads(json.dumps(note.get("owner_ref") or {})),
        "attachment": _kw02_attachment_payload(note, include_route=True),
        "tags": list(note.get("tags") or []),
        "linked_evidence_refs": evidence_links,
        "linked_memory_anchors": memory_anchors,
        "created_at": note.get("created_at"),
        "updated_at": note.get("updated_at"),
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "research_note_detail": _kw02_surface_state(
                    "research_notes",
                    snapshot_at=snapshot_at,
                    has_data=True,
                ),
                "evidence_links": evidence_surface,
                "memory_anchors": memory_surface,
            },
        },
    }


@app.get("/api/v1/knowledge/evidence")
async def list_evidence_refs(
    linked_entity_type: Optional[str] = None,
    linked_entity_ref: Optional[str] = None,
    link_type: Optional[str] = None,
    credibility_tier: Optional[str] = None,
    verified: Optional[bool] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    validated_linked_entity_type = None
    if linked_entity_type is not None:
        validated_linked_entity_type = _kw03_validate_linked_entity_type(linked_entity_type)
    if linked_entity_ref is not None and validated_linked_entity_type is None:
        raise _kw03_bad_request(
            "Invalid linked_entity_ref filter",
            "linked_entity_ref requires linked_entity_type to be set",
            "linked_entity_ref",
        )

    validated_link_type = _kw03_validate_link_type(link_type) if link_type is not None else None
    validated_credibility_tier = (
        _kw03_validate_credibility_tier(credibility_tier)
        if credibility_tier is not None
        else None
    )

    snapshot_at = utc_now()
    evidence_refs = read_store.list_evidence_refs()
    evidence_dataset_available = read_store.dataset_source("evidence_refs") != "missing"

    if validated_linked_entity_type:
        evidence_refs = [
            item
            for item in evidence_refs
            if str(((item.get("linked_object_summary") or {}).get("entity_type")) or "")
            == validated_linked_entity_type
        ]
    if linked_entity_ref is not None:
        evidence_refs = [
            item
            for item in evidence_refs
            if str(((item.get("linked_object_summary") or {}).get("entity_ref")) or "")
            == str(linked_entity_ref)
        ]
    if validated_link_type:
        evidence_refs = [
            item
            for item in evidence_refs
            if str(item.get("link_type") or "") == validated_link_type
        ]
    if validated_credibility_tier:
        evidence_refs = [
            item
            for item in evidence_refs
            if str(((item.get("credibility") or {}).get("tier")) or "")
            == validated_credibility_tier
        ]
    if verified is not None:
        evidence_refs = [
            item
            for item in evidence_refs
            if bool((item.get("credibility") or {}).get("verified")) is verified
        ]

    surface_state = _kw03_surface_state(
        "evidence_refs",
        snapshot_at=snapshot_at,
        has_data=evidence_dataset_available,
    )
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        has_more = False
    else:
        page_items, next_page_token = _page_slice(evidence_refs, page_token, page_size)
        has_more = next_page_token is not None

    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None
    processed_items, redacted_count = redact_evidence_refs(identity, list(page_items), capabilities=capabilities)

    evidence_refs_response = []
    for item in processed_items:
        if item.get("redacted"):
            evidence_refs_response.append(item)
        else:
            evidence_refs_response.append({
                "ref_id": item.get("ref_id"),
                "source_document": json.loads(json.dumps(item.get("source_document") or {})),
                "link_type": item.get("link_type"),
                "credibility": json.loads(json.dumps(item.get("credibility") or {})),
                "linked_object_summary": json.loads(json.dumps(item.get("linked_object_summary") or {})),
                "resolved_link": json.loads(json.dumps(item.get("resolved_link") or {})),
                "route_href": item.get("route_href"),
            })

    return {
        "evidence_refs": evidence_refs_response,
        "pagination": {
            "page_size": page_size,
            "next_page_token": next_page_token,
            "has_more": has_more,
        },
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {"evidence_refs_list": surface_state},
            "redacted_evidence_count": redacted_count,
        },
    }


@app.get("/api/v1/knowledge/evidence/{ref_id}")
async def get_evidence_ref_detail(
    ref_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    evidence_ref = read_store.get_evidence_ref_detail(ref_id)
    if not evidence_ref:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evidence reference not found",
            f"Evidence reference {ref_id} does not exist",
        )

    snapshot_at = utc_now()
    detail_surface = _kw03_surface_state(
        "evidence_refs",
        snapshot_at=snapshot_at,
        has_data=True,
    )

    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None

    # Check if the evidence ref itself requires capability redaction before exposing any detail.
    ev_kind_raw = str(evidence_ref.get("evidence_type") or "").strip()
    # Fall back to source_document.source_type so refs without an explicit
    # evidence_type field are still capability-gated.
    if not ev_kind_raw:
        _src_doc = evidence_ref.get("source_document")
        if isinstance(_src_doc, dict):
            _source_type = str(_src_doc.get("source_type") or "").strip()
            ev_kind_raw = SOURCE_TYPE_TO_EVIDENCE_KIND.get(_source_type, "")
    if ev_kind_raw:
        [self_processed], _ = redact_evidence_refs(
            identity, [{"ref_id": ref_id, "evidence_type": ev_kind_raw}], capabilities=capabilities
        )
        if self_processed.get("redacted"):
            return {
                **self_processed,
                "meta": {
                    **_snapshot_meta(snapshot_at),
                    "surfaces": {
                        "evidence_ref_detail": detail_surface,
                        "resolved_link": detail_surface,
                        "linked_decisions": detail_surface,
                    },
                    "redacted_evidence_count": 1,
                },
            }

    raw_linked_decisions = json.loads(json.dumps(evidence_ref.get("linked_decisions") or []))
    # Annotate linked decisions with evidence_type derived from entity_type for redaction.
    # Pass-through decisions are restored to their original form (without annotation).
    annotated_decisions = []
    for dec in raw_linked_decisions:
        entity_type = str(dec.get("entity_type") or "").strip()
        ev_kind = _ENTITY_TYPE_EVIDENCE_KIND.get(entity_type)
        if ev_kind:
            dec_copy = dict(dec)
            dec_copy["evidence_type"] = ev_kind
            if not dec_copy.get("ref_id") and not dec_copy.get("id"):
                dec_copy["ref_id"] = dec_copy.get("entity_ref") or ""
            annotated_decisions.append(dec_copy)
        else:
            annotated_decisions.append(dec)
    processed_decisions, redacted_count = redact_evidence_refs(identity, annotated_decisions, capabilities=capabilities)
    linked_decisions = []
    for orig, proc in zip(raw_linked_decisions, processed_decisions):
        if proc.get("redacted"):
            linked_decisions.append(proc)
        else:
            linked_decisions.append(orig)

    return {
        "ref_id": evidence_ref.get("ref_id"),
        "source_document": json.loads(json.dumps(evidence_ref.get("source_document") or {})),
        "link_type": evidence_ref.get("link_type"),
        "credibility": json.loads(json.dumps(evidence_ref.get("credibility") or {})),
        "resolved_link": json.loads(json.dumps(evidence_ref.get("resolved_link") or {})),
        "linked_decisions": linked_decisions,
        "source_note_context": json.loads(json.dumps(evidence_ref.get("source_note_context"))),
        "source_memory_context": json.loads(json.dumps(evidence_ref.get("source_memory_context"))),
        "created_at": evidence_ref.get("created_at"),
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "evidence_ref_detail": detail_surface,
                "resolved_link": detail_surface,
                "linked_decisions": detail_surface,
            },
            "redacted_evidence_count": redacted_count,
        },
    }


@app.get("/api/v1/knowledge/insights")
async def list_insight_cards(
    status: str = Query(default="active"),
    tag: Optional[str] = None,
    linked_entity_type: Optional[str] = None,
    linked_entity_ref: Optional[str] = None,
    recency: str = Query(default="all"),
    confidence_min: Optional[float] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=100),
    include_inactive: bool = False,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    validated_status = _kw04_validate_status(status)
    validated_recency = _kw04_validate_recency(recency)
    validated_linked_entity_type = None
    if linked_entity_type is not None:
        validated_linked_entity_type = _kw04_validate_linked_entity_type(linked_entity_type)
    if linked_entity_ref is not None and validated_linked_entity_type is None:
        raise _kw04_bad_request(
            "Invalid linked_entity_ref filter",
            "linked_entity_ref requires linked_entity_type to be set",
            "linked_entity_ref",
        )
    validated_confidence_min = (
        _kw04_validate_confidence_min(confidence_min)
        if confidence_min is not None
        else None
    )

    snapshot_at = utc_now()
    cards = read_store.list_insight_cards()
    cards_dataset_available = read_store.dataset_source("insight_cards") != "missing"
    filter_metadata = _kw04_filter_metadata(cards) if cards_dataset_available else {
        "tags": [],
        "linked_entity_types": [],
        "recency_options": [
            {"value": value, "display_label": _kw04_recency_display_label(value)}
            for value in ["7d", "30d", "90d", "all"]
        ],
        "total_active_count": 0,
    }

    filtered_cards = list(cards)
    if not include_inactive and validated_status != "all":
        filtered_cards = [
            card
            for card in filtered_cards
            if str(card.get("status") or "") == validated_status
        ]
    elif not include_inactive and validated_status == "all":
        filtered_cards = filtered_cards
    elif include_inactive and validated_status != "all":
        filtered_cards = filtered_cards

    if tag is not None:
        filtered_cards = [
            card for card in filtered_cards if str(tag) in set(card.get("tags") or [])
        ]
    if validated_linked_entity_type is not None:
        filtered_cards = [
            card
            for card in filtered_cards
            if any(
                str((source or {}).get("entity_type") or "") == validated_linked_entity_type
                for source in card.get("linked_sources") or []
            )
        ]
    if linked_entity_ref is not None:
        filtered_cards = [
            card
            for card in filtered_cards
            if any(
                str((source or {}).get("entity_type") or "") == validated_linked_entity_type
                and str((source or {}).get("entity_ref") or "") == str(linked_entity_ref)
                for source in card.get("linked_sources") or []
            )
        ]
    if validated_recency != "all":
        filtered_cards = [
            card
            for card in filtered_cards
            if _kw04_within_recency(card.get("aggregated_at"), validated_recency, snapshot_at)
        ]
    if validated_confidence_min is not None:
        filtered_cards = [
            card
            for card in filtered_cards
            if float(((card.get("confidence") or {}).get("score")) or 0.0) >= validated_confidence_min
        ]

    surface_state = _kw04_surface_state(
        "insight_cards",
        snapshot_at=snapshot_at,
        has_data=cards_dataset_available,
    )
    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        has_more = False
    else:
        page_items, next_page_token = _page_slice(filtered_cards, page_token, page_size)
        has_more = next_page_token is not None

    return {
        "insight_cards": [
            {
                "insight_id": item.get("insight_id"),
                "summary": item.get("summary"),
                "scope": item.get("scope"),
                "scope_ref": item.get("scope_ref"),
                "status": item.get("status"),
                "superseded_by_id": item.get("superseded_by_id"),
                "confidence": json.loads(json.dumps(item.get("confidence") or {})),
                "tags": list(item.get("tags") or []),
                "evidence_count": item.get("evidence_count"),
                "primary_evidence_count": item.get("primary_evidence_count"),
                "aggregated_at": item.get("aggregated_at"),
                "route_href": item.get("route_href"),
            }
            for item in page_items
        ],
        "filter_metadata": filter_metadata,
        "pagination": {
            "page_size": page_size,
            "next_page_token": next_page_token,
            "has_more": has_more,
        },
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {"insight_cards": surface_state},
        },
    }


@app.get("/api/v1/knowledge/insights/{insight_id}")
async def get_insight_card_detail(
    insight_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    insight_card = read_store.get_insight_card_detail(insight_id)
    if not insight_card:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Insight card not found",
            f"Insight card {insight_id} does not exist",
        )

    snapshot_at = utc_now()
    detail_surface = _kw04_surface_state(
        "insight_cards",
        snapshot_at=snapshot_at,
        has_data=True,
    )
    supporting_evidence_surface = _kw04_supporting_evidence_surface(
        list(insight_card.get("supporting_evidence_refs") or []),
        snapshot_at=snapshot_at,
    )
    linked_sources_surface = _kw04_linked_sources_surface(
        list(insight_card.get("linked_sources") or []),
        snapshot_at=snapshot_at,
    )

    return {
        "insight_id": insight_card.get("insight_id"),
        "summary": insight_card.get("summary"),
        "scope": insight_card.get("scope"),
        "scope_context": json.loads(json.dumps(insight_card.get("scope_context") or {})),
        "status": insight_card.get("status"),
        "superseded_by": json.loads(json.dumps(insight_card.get("superseded_by") or {})),
        "confidence": json.loads(json.dumps(insight_card.get("confidence") or {})),
        "tags": list(insight_card.get("tags") or []),
        "source_ref": insight_card.get("source_ref"),
        "supporting_evidence_refs": json.loads(
            json.dumps(insight_card.get("supporting_evidence_refs") or [])
        ),
        "linked_sources": json.loads(json.dumps(insight_card.get("linked_sources") or [])),
        "aggregation_provenance": json.loads(
            json.dumps(insight_card.get("aggregation_provenance") or {})
        ),
        "created_at": insight_card.get("created_at"),
        "updated_at": insight_card.get("updated_at"),
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "insight_card_detail": detail_surface,
                "supporting_evidence_refs": supporting_evidence_surface,
                "linked_sources": linked_sources_surface,
            },
        },
    }


@app.get("/api/v1/knowledge/strategy-specs")
async def list_strategy_specs(
    lifecycle_state: str = Query(default="all"),
    source_kind: Optional[str] = None,
    persona_id: Optional[str] = None,
    include_retired: bool = False,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    validated_lifecycle_state = _kw05_validate_lifecycle_state(lifecycle_state)
    snapshot_at = utc_now()
    items = read_store.list_strategy_specs(
        lifecycle_state=validated_lifecycle_state,
        source_kind=source_kind,
        persona_id=persona_id,
        include_retired=include_retired,
    )
    dataset_available = read_store.dataset_source("strategy_specs") != "missing"
    surface_state = _kw05_surface_state(
        "strategy_specs",
        snapshot_at=snapshot_at,
        has_data=dataset_available,
    )

    if surface_state == "unavailable":
        page_items = []
        next_page_token = None
        has_more = False
    else:
        page_items, next_page_token = _page_slice(items, page_token, page_size)
        has_more = next_page_token is not None

    return {
        "items": page_items,
        "page_info": {
            "next_page_token": next_page_token,
            "page_size": page_size,
            "has_more": has_more,
        },
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {"strategy_spec_list": surface_state},
        },
    }


@app.get("/api/v1/knowledge/strategy-specs/{strategy_id}")
async def get_strategy_spec_detail(
    strategy_id: str,
    version: str = Query(default="current"),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    strategy_exists = read_store.get_strategy_spec(strategy_id)
    if not strategy_exists:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Strategy spec not found",
            f"Strategy spec family {strategy_id} does not exist",
        )

    detail = read_store.get_strategy_spec_detail(strategy_id, version_selector=version)
    if not detail:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Strategy spec version not found",
            f"Version {version} does not exist for strategy {strategy_id}",
        )

    snapshot_at = utc_now()
    detail_surface = _kw05_surface_state(
        "strategy_specs",
        snapshot_at=snapshot_at,
        has_data=True,
    )
    citation_bundle = json.loads(json.dumps(detail.get("citation_bundle") or {}))
    citation_surface = "partial" if not any(citation_bundle.values()) else detail_surface
    ancestry_surface = (
        "degraded"
        if detail.get("parent_spec_version_id") is None and str(version).strip() not in {"", "current"}
        else detail_surface
    )

    return {
        "object_ref": json.loads(json.dumps(detail.get("object_ref") or {})),
        "strategy_id": detail.get("strategy_id"),
        "spec_version_id": detail.get("spec_version_id"),
        "spec_version": detail.get("spec_version"),
        "parent_spec_version_id": detail.get("parent_spec_version_id"),
        "derived_from_source_refs": list(detail.get("derived_from_source_refs") or []),
        "lifecycle_state": detail.get("lifecycle_state"),
        "title": detail.get("title"),
        "hypothesis": detail.get("hypothesis"),
        "objective": detail.get("objective"),
        "market_scope": json.loads(json.dumps(detail.get("market_scope") or {})),
        "execution_profile": json.loads(json.dumps(detail.get("execution_profile") or {})),
        "evaluation_plan": json.loads(json.dumps(detail.get("evaluation_plan") or {})),
        "governance": json.loads(json.dumps(detail.get("governance") or {})),
        "citation_bundle": citation_bundle,
        "allowedActions": json.loads(json.dumps(detail.get("allowedActions") or {})),
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "strategy_spec_detail": detail_surface,
                "citation_bundle": citation_surface,
                "version_ancestry": ancestry_surface,
            },
        },
    }


@app.get("/api/v1/knowledge/strategy-specs/{strategy_id}/versions")
async def list_strategy_spec_versions(
    strategy_id: str,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    versions = read_store.list_strategy_spec_versions(strategy_id)
    if not versions and not read_store.get_strategy_spec(strategy_id):
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Strategy spec not found",
            f"Strategy spec family {strategy_id} does not exist",
        )

    snapshot_at = utc_now()
    return {
        "strategy_id": strategy_id,
        "versions": versions,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "version_history": _kw05_surface_state(
                    "strategy_specs",
                    snapshot_at=snapshot_at,
                    has_data=True,
                )
            },
        },
    }


@app.get("/api/v1/knowledge/strategy-specs/{strategy_id}/compare")
async def compare_strategy_spec_versions(
    strategy_id: str,
    left_version: Optional[str] = None,
    right_version: Optional[str] = None,
    base_version: Optional[str] = None,
    target_version: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    left_selector, right_selector = _kw05_compare_selectors(
        left_version=left_version,
        right_version=right_version,
        base_version=base_version,
        target_version=target_version,
    )
    if left_selector == right_selector:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Compare requires two distinct versions",
            "left_version and right_version must identify different versions",
            precondition_failed="left_version",
        )

    left_detail = read_store.get_strategy_spec_detail(strategy_id, version_selector=left_selector)
    right_detail = read_store.get_strategy_spec_detail(strategy_id, version_selector=right_selector)
    if not left_detail or not right_detail:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Strategy spec version not found",
            f"Cannot compare missing versions for strategy {strategy_id}",
        )
    if not (left_detail.get("allowedActions") or {}).get("canCompare") or not (
        right_detail.get("allowedActions") or {}
    ).get("canCompare"):
        raise _bff_error(
            422,
            ErrorCode.INVALID_STATE,
            "One or more versions cannot be compared",
            "Compare accepts only candidate, approved, or retired strategy spec versions",
            precondition_failed="lifecycle_state",
        )

    payload = read_store.compare_strategy_spec_versions(
        strategy_id,
        left_selector=left_selector,
        right_selector=right_selector,
    )
    if not payload:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Strategy spec version not found",
            f"Cannot compare missing versions for strategy {strategy_id}",
        )

    snapshot_at = utc_now()
    payload["meta"] = {
        **_snapshot_meta(snapshot_at),
        "surfaces": {
            "strategy_spec_compare": _kw05_surface_state(
                "strategy_specs",
                snapshot_at=snapshot_at,
                has_data=True,
            )
        },
    }
    return payload


def _kw01_surface_state(
    dataset: str,
    *,
    snapshot_at: str,
    has_data: Optional[bool] = None,
    missing_message: Optional[str] = None,
) -> str:
    source = read_store.dataset_source(
        dataset,
        include_snapshot_fallback=False,
        include_local_fallback=False,
    )
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=has_data,
        missing_message=missing_message,
        source=source,
    )
    if surface.get("status") == "unavailable":
        return "unavailable"
    if surface.get("status") == "degraded":
        return "degraded"
    return "ok"


@app.get("/api/v1/knowledge/memory")
async def list_institutional_memory(
    knowledge_type: Optional[str] = None,
    scope: Optional[str] = None,
    scope_filter: Optional[str] = None,
    tags: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    authorization: Optional[str] = Header(default=None),
):
    """KW-01: Paginated list of institutional memory entries."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    entries = read_store.list_institutional_memory_entries()

    if knowledge_type:
        entries = [e for e in entries if e["knowledge_type"] == knowledge_type]
    if scope:
        entries = [e for e in entries if e["scope"] == scope]
    if scope_filter:
        entries = [e for e in entries if e.get("scope_filter") == scope_filter]
    if tags:
        requested = {t.strip() for t in tags.split(",") if t.strip()}
        entries = [e for e in entries if requested.intersection(e.get("tags", []))]

    total_count = len(entries)
    start = (page - 1) * page_size
    page_entries = entries[start : start + page_size]
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    memory_list_surface = _kw01_surface_state(
        "institutional_memory_entries",
        snapshot_at=snapshot_at,
        has_data=bool(entries),
        missing_message="Institutional memory list is unavailable.",
    )
    if memory_list_surface == "unavailable":
        page_entries = []
        total_count = 0
        total_pages = 0

    return {
        "entries": page_entries,
        "pagination": {
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        },
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {"memory_list": memory_list_surface},
        },
    }


@app.get("/api/v1/knowledge/memory/{entry_id}")
async def get_institutional_memory_entry(
    entry_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """KW-01: Full detail view for one institutional memory entry."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    entry = read_store.get_institutional_memory_entry(
        entry_id,
        include_snapshot_fallback=False,
    )
    if entry is None:
        return JSONResponse(
            status_code=404,
            content={"error": "entry_not_found", "entry_id": entry_id},
        )

    source_event = entry.get("source_event") if isinstance(entry.get("source_event"), dict) else {}
    source_context_available = bool(source_event.get("type")) and bool(source_event.get("id"))

    return {
        **entry,
        "meta": {
            **_snapshot_meta(snapshot_at),
            "surfaces": {
                "entry_detail": _kw01_surface_state(
                    "institutional_memory_entries",
                    snapshot_at=snapshot_at,
                    has_data=True,
                ),
                "source_context": _kw01_surface_state(
                    "institutional_memory_entries",
                    snapshot_at=snapshot_at,
                    has_data=source_context_available,
                    missing_message="Institutional memory source context is unavailable.",
                ),
            },
        },
    }


def _governance_review_allowed_actions_present(item: Dict[str, Any]) -> bool:
    allowed_actions = item.get("allowedActions")
    if not isinstance(allowed_actions, dict):
        return False
    required_fields = (
        "canReview",
        "canForwardToApproval",
        "canRequestChanges",
        "canEscalate",
    )
    return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)


def _approval_queue_allowed_actions_present(item: Dict[str, Any]) -> bool:
    allowed_actions = item.get("allowedActions")
    if not isinstance(allowed_actions, dict):
        return False
    required_fields = (
        "canApprove",
        "canReject",
        "canRequestRevision",
    )
    return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)


_DEPLOYMENT_DIFF_CATEGORIES = (
    "parameters",
    "bindings",
    "capital_allocation",
    "risk_controls",
    "stage_transition",
)


def _default_deployment_diff_summary() -> Dict[str, Any]:
    return {
        "total_changes": 0,
        "by_category": {
            category: {"count": 0, "highest_risk_tier": None}
            for category in _DEPLOYMENT_DIFF_CATEGORIES
        },
    }


def _deployment_diff_allowed_actions_present(payload: Dict[str, Any]) -> bool:
    allowed_actions = payload.get("allowedActions")
    if not isinstance(allowed_actions, dict):
        return False
    required_fields = ("canProceedToApproval", "canEscalateDiff")
    return all(isinstance(allowed_actions.get(field), bool) for field in required_fields)


def _unavailable_deployment_diff_payload(plan_id: str, snapshot_at: str) -> Dict[str, Any]:
    deployment_diff_surface = _dataset_surface_status(
        "deployment_diffs",
        snapshot_at=snapshot_at,
        has_data=False,
        missing_message="Deployment diff unavailable for this plan.",
    )
    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=False,
        missing_message="Deployment diff authority unavailable.",
    )
    allowed_actions_surface["status"] = deployment_diff_surface.get("status")
    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "deployment_diff": deployment_diff_surface,
        "allowedActions": allowed_actions_surface,
    }
    return {
        "plan_id": plan_id,
        "artifact_id": None,
        "stage": None,
        "submitted_at": None,
        "submitted_by": None,
        "previous_plan_id": None,
        "first_deployment": False,
        "changes": [],
        "change_summary": _default_deployment_diff_summary(),
        "allowedActions": {
            "canProceedToApproval": False,
            "canEscalateDiff": False,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/governance/review-queue")
async def list_governance_review_queue(
    item_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    item_types = [value.strip() for value in item_type.split(",") if value.strip()] if item_type else None
    risk_levels = [value.strip() for value in risk_level.split(",") if value.strip()] if risk_level else None
    statuses = [value.strip() for value in status.split(",") if value.strip()] if status else None

    items = read_store.list_governance_review_queue_items(
        item_types=item_types,
        risk_levels=risk_levels,
        statuses=statuses,
    )
    review_queue_surface = _dataset_surface_status(
        "governance_review_queue_items",
        snapshot_at=snapshot_at,
    )

    if review_queue_surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=all(_governance_review_allowed_actions_present(item) for item in items),
        missing_message="Governance routing authority unavailable.",
    )
    if review_queue_surface.get("status") == "degraded":
        allowed_actions_surface["status"] = "degraded"
    elif review_queue_surface.get("status") == "unavailable":
        allowed_actions_surface["status"] = "unavailable"

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "review_queue": review_queue_surface,
        "allowedActions": allowed_actions_surface,
    }

    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None
    total_redacted = 0
    redacted_items = []
    for item in items:
        item_copy = json.loads(json.dumps(item))
        review_summary = item_copy.get("review_summary") or {}
        raw_refs = list(review_summary.get("evidence_refs") or [])
        if raw_refs:
            processed_refs, count = redact_evidence_refs(identity, raw_refs, capabilities=capabilities)
            review_summary["evidence_refs"] = processed_refs
            total_redacted += count
            item_copy["review_summary"] = review_summary
        redacted_items.append(item_copy)
    meta["redacted_evidence_count"] = total_redacted

    return {
        "items": redacted_items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/governance/approval-queue")
async def list_governance_approval_queue(
    decision_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    decision_state: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    decision_types = [value.strip() for value in decision_type.split(",") if value.strip()] if decision_type else None
    risk_levels = [value.strip() for value in risk_level.split(",") if value.strip()] if risk_level else None
    decision_states = [value.strip() for value in decision_state.split(",") if value.strip()] if decision_state else None

    items = read_store.list_approval_queue_items(
        decision_types=decision_types,
        risk_levels=risk_levels,
        decision_states=decision_states,
    )
    approval_queue_surface = _dataset_surface_status(
        "approval_queue_items",
        snapshot_at=snapshot_at,
    )

    if approval_queue_surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=all(_approval_queue_allowed_actions_present(item) for item in items),
        missing_message="Approval queue authority unavailable.",
    )
    if approval_queue_surface.get("status") == "degraded":
        allowed_actions_surface["status"] = "degraded"
    elif approval_queue_surface.get("status") == "unavailable":
        allowed_actions_surface["status"] = "unavailable"

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {
        "approval_queue": approval_queue_surface,
        "allowedActions": allowed_actions_surface,
    }

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/operator/deployment-diff/{plan_id}")
async def get_deployment_diff(plan_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    diff = read_store.get_deployment_diff(plan_id)
    diff_source = read_store.dataset_source("deployment_diffs")
    if not diff:
        if diff_source == "missing":
            return _unavailable_deployment_diff_payload(plan_id, utc_now())
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment diff not found",
            f"Deployment diff for plan {plan_id} does not exist",
        )

    snapshot_at = (((diff.get("meta") or {}).get("snapshot_at"))) or utc_now()
    payload = dict(diff)
    payload["plan_id"] = payload.get("plan_id") or plan_id
    payload["changes"] = list(payload.get("changes") or [])

    summary = dict(payload.get("change_summary") or {})
    summary["total_changes"] = int(summary.get("total_changes") or len(payload["changes"]))
    by_category = dict(summary.get("by_category") or {})
    for category in _DEPLOYMENT_DIFF_CATEGORIES:
        category_summary = dict(by_category.get(category) or {})
        category_summary.setdefault("count", 0)
        category_summary.setdefault("highest_risk_tier", None)
        by_category[category] = category_summary
    summary["by_category"] = by_category
    payload["change_summary"] = summary

    allowed_actions = dict(payload.get("allowedActions") or {})
    allowed_actions.setdefault("canProceedToApproval", False)
    allowed_actions.setdefault("canEscalateDiff", False)
    payload["allowedActions"] = allowed_actions

    deployment_diff_surface = _dataset_surface_status(
        "deployment_diffs",
        snapshot_at=snapshot_at,
        has_data=True,
    )
    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=_deployment_diff_allowed_actions_present(payload),
        missing_message="Deployment diff authority unavailable.",
    )
    if deployment_diff_surface.get("status") == "degraded":
        allowed_actions_surface["status"] = "degraded"
    elif deployment_diff_surface.get("status") == "unavailable":
        allowed_actions_surface["status"] = "unavailable"

    meta = dict(payload.get("meta") or {})
    meta["snapshot_at"] = snapshot_at
    meta["surfaces"] = {
        "deployment_diff": deployment_diff_surface,
        "allowedActions": allowed_actions_surface,
    }
    payload["meta"] = meta
    return payload


@app.get("/api/v1/operator/rollback-review/{rollback_id}")
async def get_rollback_review(rollback_id: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    review = read_store.get_rollback_review(rollback_id)
    if not review:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Rollback review not found",
            f"Rollback review {rollback_id} does not exist",
        )

    snapshot_at = (
        ((review.get("meta") or {}).get("snapshot_at"))
        or utc_now()
    )
    meta = dict(review.get("meta") or {})
    meta["snapshot_at"] = snapshot_at
    surfaces = dict(meta.get("surfaces") or {})
    surfaces.setdefault(
        "rollback_review",
        _composed_surface_status(snapshot_at=snapshot_at, available=True),
    )
    surfaces.setdefault(
        "position_data",
        _composed_surface_status(snapshot_at=snapshot_at, available=True),
    )
    surfaces.setdefault(
        "allowedActions",
        _composed_surface_status(
            snapshot_at=snapshot_at,
            available=review.get("allowedActions") is not None,
            missing_message="Rollback approval authority unavailable.",
        ),
    )
    meta["surfaces"] = surfaces

    payload = dict(review)
    payload["meta"] = meta
    return payload


@app.get("/api/v1/operator/governance/audit")
async def list_governance_audit_trail(
    actor: Optional[str] = None,
    action_type: Optional[str] = None,
    target_type: Optional[str] = None,
    from_: Optional[datetime] = Query(default=None, alias="from"),
    to: Optional[datetime] = Query(default=None),
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    action_types = None
    if action_type:
        action_types = [value.strip() for value in action_type.split(",") if value.strip()]

    entries = _list_governance_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
        from_ts=from_,
        to_ts=to,
    )
    audit_surface = _dataset_surface_status(
        "governance_audit_events",
        snapshot_at=snapshot_at,
    )
    if audit_surface.get("status") == "unavailable" and not entries:
        entries = []
        next_page_token = None
    else:
        entries, next_page_token = _page_slice(entries, page_token, page_size)

    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "audit_trail": audit_surface,
        },
    }

    return {
        "entries": entries,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


# --------------------------------------------------------------------------- #
# Incident Surfaces (Wave 2 - Incident Response: IN-01 – IN-05)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/incidents")
async def list_incidents(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    severity: Optional[str] = None,
    affected_pool_id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """IN-01: Incident List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    incidents = read_store.list_incidents(
        status=status, severity=severity, affected_pool_id=affected_pool_id,
    )
    items = [_project_incident_home_item(incident) for incident in incidents]
    if surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "incident_list": surface,
        },
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness

    degradation_reason = _surface_degradation_reason(
        surface,
        degraded_reason="Incident list is degraded and may be stale.",
        unavailable_reason="Incident list is currently unavailable.",
    )
    if degradation_reason is not None:
        meta["degradation"] = {"reason": degradation_reason}

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": meta,
    }


@app.get("/api/v1/incidents/{incident_id}")
async def get_incident(incident_id: str, authorization: Optional[str] = Header(default=None)):
    """IN-02: Incident Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    return {
        "data": incident,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/postmortems")
async def list_postmortems(
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """IN-03: Postmortem List."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    postmortems = read_store.list_postmortems(time_range=time_range)
    return {
        "data": postmortems,
        "meta": {
            "total": len(postmortems),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/postmortems/{report_id}")
async def get_postmortem(report_id: str, authorization: Optional[str] = Header(default=None)):
    """IN-04: Postmortem Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    postmortem = read_store.get_postmortem(report_id)
    if not postmortem:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Postmortem report not found",
            f"Postmortem {report_id} does not exist",
        )

    # Include linked incident if available
    incident_id = postmortem.get("incident_id")
    incident = read_store.get_incident(incident_id) if incident_id else None
    payload = dict(postmortem)
    if incident:
        payload["linked_incident"] = incident

    return {
        "data": payload,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/kill-switch/status")
async def get_kill_switch_status(authorization: Optional[str] = Header(default=None)):
    """IN-05: Kill Switch Status — requires admin role."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    if "admin" not in identity.roles:
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Kill-switch status requires 'admin' role",
            "Operator does not hold the admin role",
            precondition_failed="role_check",
            suggestion="Escalate to an admin-role operator",
        )

    snapshot_at = utc_now()
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)
    allowed_actions_surface = _action_drawer_allowed_actions_surface()
    ks = read_store.get_kill_switch_status()
    allowed_actions = _project_action_drawer_allowed_actions(
        kill_switch_surface,
        allowed_actions_surface,
    )
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "kill_switch": kill_switch_surface,
            "allowedActions": allowed_actions_surface,
        },
    }
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness

    degradation: Dict[str, Any] = {}
    kill_switch_reason = _surface_degradation_reason(
        kill_switch_surface,
        degraded_reason="Kill switch status is degraded and may be stale.",
        unavailable_reason="Kill switch status is currently unavailable.",
    )
    if kill_switch_reason is not None:
        degradation["kill_switch_reason"] = kill_switch_reason
    allowed_actions_reason = _surface_degradation_reason(
        allowed_actions_surface,
        degraded_reason="Action authority is degraded. All CTAs disabled for safety.",
        unavailable_reason="Action authority service is unavailable. All CTAs disabled for safety.",
    )
    if allowed_actions_reason is not None:
        degradation["allowedActions_reason"] = allowed_actions_reason
    if degradation:
        meta["degradation"] = degradation

    return {
        "kill_switch": _project_kill_switch_contract(ks, kill_switch_surface),
        "allowedActions": allowed_actions,
        "meta": meta,
    }


# --------------------------------------------------------------------------- #
# Composed Views — Incident Response
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/incident-response/{incident_id}")
async def get_incident_response(
    incident_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for PKT-002 Incident Detail.
    Composes: incident record, affected bindings, kill-switch state, and action authority.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # IN-02: Incident detail
    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    snapshot_at = utc_now()
    runtime_binding = None
    binding_id = incident.get("binding_id")
    if binding_id:
        runtime_binding = read_store.get_runtime_binding(binding_id)
    if runtime_binding is None:
        runtime_binding = read_store.get_runtime_binding_by_runtime_id(incident.get("runtime_id"))

    affected_bindings, binding_lookup_expected = _project_affected_bindings(
        incident,
        runtime_binding,
    )
    ks = read_store.get_kill_switch_status()

    incident_surface = _dataset_surface_status(
        "incidents",
        snapshot_at=snapshot_at,
        has_data=incident is not None,
    )
    affected_bindings_surface = _dataset_surface_status(
        "persona_bindings",
        snapshot_at=snapshot_at,
        has_data=(len(affected_bindings) > 0) if binding_lookup_expected else None,
        missing_message="Affected bindings unavailable for this incident.",
    )
    kill_switch_surface = _dataset_surface_status("kill_switch", snapshot_at=snapshot_at)

    action_derivation_available = bool(incident.get("runtime_id"))
    allowed_actions_surface = _composed_surface_status(
        snapshot_at=snapshot_at,
        available=action_derivation_available,
        missing_message="Action authority unavailable for this incident.",
    )
    if kill_switch_surface.get("status") == "unavailable":
        allowed_actions_surface["status"] = "unavailable"
        allowed_actions_surface.setdefault(
            "staleness",
            {"served_from": "unverifiable", "last_known_at": snapshot_at},
        )
    allowed_actions = (
        _derive_incident_allowed_actions(identity, incident)
        if allowed_actions_surface.get("status") == "ok"
        else _default_incident_allowed_actions()
    )

    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {
            "incident": incident_surface,
            "affected_bindings": affected_bindings_surface,
            "kill_switch": kill_switch_surface,
            "allowedActions": allowed_actions_surface,
        },
    }
    if snapshot == "preferred":
        staleness = _meta_staleness()
        if staleness is not None:
            meta["staleness"] = staleness

    degradation: Dict[str, str] = {}
    affected_bindings_reason = _surface_degradation_reason(
        affected_bindings_surface,
        degraded_reason="Affected bindings are degraded and may be incomplete.",
        unavailable_reason="Affected bindings are currently unavailable.",
    )
    if affected_bindings_reason is not None:
        degradation["affected_bindings_reason"] = affected_bindings_reason
    kill_switch_reason = _surface_degradation_reason(
        kill_switch_surface,
        degraded_reason="Kill switch status is degraded and may be stale.",
        unavailable_reason="Kill switch status is currently unavailable.",
    )
    if kill_switch_reason is not None:
        degradation["kill_switch_reason"] = kill_switch_reason
    allowed_actions_reason = _surface_degradation_reason(
        allowed_actions_surface,
        degraded_reason="Action authority is degraded; all CTAs are disabled for safety.",
        unavailable_reason="Action authority is currently unavailable; all CTAs are disabled.",
    )
    if allowed_actions_reason is not None:
        degradation["allowedActions_reason"] = allowed_actions_reason
    if degradation:
        meta["degradation"] = degradation

    return {
        "data": {
            "incident": _project_incident_detail_incident(incident),
            "affected_bindings": affected_bindings,
            "kill_switch": _project_kill_switch_contract(ks, kill_switch_surface),
        },
        "allowedActions": allowed_actions,
        "meta": meta,
    }


# --------------------------------------------------------------------------- #
# Persona Management composed view (Wave 4 — PS-02, CP-03, CP-04, PS-03, PS-05)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/persona-management/{persona_id}")
async def get_persona_management(
    persona_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for persona lifecycle management.
    Composes: PS-02 (persona detail + bindings), CP-03/CP-04 (capital pool bindings),
              PS-03 (persona sessions), PS-05 (teaching sessions).
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # PS-02: Persona detail
    persona = read_store.get_persona(persona_id)
    if not persona:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )

    snapshot_at = utc_now()
    surfaces = {}

    # PS-02: Persona bindings (bindings where this persona is the owner)
    persona_bindings = read_store.get_bindings_for_persona(persona_id)
    persona_bindings_available = persona_bindings is not None
    if persona_bindings is None:
        persona_bindings = []
    surfaces["persona_bindings"] = _dataset_surface_status(
        "persona_bindings",
        snapshot_at=snapshot_at,
        has_data=persona_bindings_available,
        missing_message="Persona bindings unavailable for this persona.",
    )

    # CP-04: Enrich each binding with its capital pool detail
    enriched_bindings = []
    for binding in persona_bindings:
        binding_detail = dict(binding)
        pool = read_store.get_capital_pool(binding.get("capital_pool_id"))
        if pool:
            binding_detail["capital_pool"] = pool
        enriched_bindings.append(binding_detail)

    capital_pool_surface = _dataset_surface_status(
        "capital_pools",
        snapshot_at=snapshot_at,
        has_data=bool(enriched_bindings),
        missing_message="No capital pool bindings available for this persona.",
    )
    if surfaces["persona_bindings"]["status"] != "ok":
        surfaces["capital_pool_bindings"] = dict(surfaces["persona_bindings"])
    else:
        surfaces["capital_pool_bindings"] = capital_pool_surface

    # PS-03: Active sessions for this persona
    sessions = read_store.get_sessions_for_persona(persona_id)
    sessions_available = sessions is not None
    if sessions is None:
        sessions = []
    surfaces["persona_sessions"] = _dataset_surface_status(
        "sessions",
        snapshot_at=snapshot_at,
        has_data=sessions_available,
        missing_message="Persona sessions unavailable for this persona.",
    )

    # PS-05: Teaching sessions for this persona
    teaching_sessions = read_store.get_teaching_sessions_for_persona(persona_id)
    teaching_sessions_available = teaching_sessions is not None
    if teaching_sessions is None:
        teaching_sessions = []
    surfaces["teaching_sessions"] = _dataset_surface_status(
        "teaching_sessions",
        snapshot_at=snapshot_at,
        has_data=teaching_sessions_available,
        missing_message="Teaching sessions unavailable for this persona.",
    )

    # Backend-shaped allowed actions (acceptance: backend_shaped_persona_actions)
    allowed_actions = read_store.get_persona_allowed_actions(persona_id)
    allowed_actions_available = allowed_actions is not None
    if allowed_actions is None:
        allowed_actions = {}
    surfaces["allowed_actions"] = _dataset_surface_status(
        "allowed_actions",
        snapshot_at=snapshot_at,
        has_data=allowed_actions_available,
        missing_message="Allowed actions unavailable for this persona.",
    )

    data = {
        "persona": persona,
        "bindings": enriched_bindings,
        "sessions": sessions,
        "teaching_sessions": teaching_sessions,
        "allowedActions": allowed_actions,
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        },
    }


# --------------------------------------------------------------------------- #
# Post-Incident Review composed view
# --------------------------------------------------------------------------- #


@app.get("/api/v1/operator/post-incident-review/{incident_id}")
async def get_post_incident_review(
    incident_id: str,
    snapshot: str = "preferred",
    authorization: Optional[str] = Header(default=None),
):
    """
    Composed view for post-incident analysis.
    Composes: IN-04, EV-01, EV-02, LN-01, TL-03
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    incident = read_store.get_incident(incident_id)
    if not incident:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    snapshot_at = utc_now()
    surfaces = {}

    # IN-04: Postmortem report
    postmortem = read_store.get_postmortem_by_incident(incident_id)
    surfaces["postmortem"] = _dataset_surface_status(
        "postmortems",
        snapshot_at=snapshot_at,
        has_data=postmortem is not None,
        missing_message="No postmortem report available yet",
    )

    # EV-01/EV-02: Evolution decisions
    evolution_decisions = read_store.get_evolution_decisions_by_incident(incident_id)
    surfaces["evolution_decisions"] = _dataset_surface_status(
        "evolution_decisions",
        snapshot_at=snapshot_at,
    )

    # LN-01: Lineage edges — fetch by artifact_id from incident
    artifact_id = incident.get("artifact_id")
    lineage_edges = read_store.list_lineage_edges(artifact_id=artifact_id) if artifact_id else []
    surfaces["lineage"] = _dataset_surface_status(
        "lineage_edges",
        snapshot_at=snapshot_at,
        has_data=bool(lineage_edges),
        missing_message="No lineage edges found for this artifact",
    )

    # TL-03: Telemetry performance — use artifact_id (not runtime_id or summary)
    telemetry_performance = None
    if artifact_id:
        telemetry_performance = read_store.get_telemetry_performance(artifact_id)
    surfaces["telemetry_performance"] = _dataset_surface_status(
        "telemetry_performance",
        snapshot_at=snapshot_at,
        has_data=telemetry_performance is not None,
        missing_message="Telemetry performance unavailable for this artifact.",
    )

    data = {
        "incident": incident,
        "postmortem": postmortem,
        "evolution_decisions": evolution_decisions,
        "lineage_edges": lineage_edges,
        "telemetry_performance": telemetry_performance,
    }

    return {
        "data": data,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
        },
    }


# --------------------------------------------------------------------------- #
# Evolution Surfaces (Wave 3 — EV-01 – EV-04)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/evolution-decisions")
async def list_evolution_decisions(
    action_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """EV-01: Evolution Decision List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    decisions = [
        _project_evolution_decision_contract(decision)
        for decision in read_store.list_evolution_decisions(
            action_type=action_type,
            risk_level=risk_level,
            status=status,
        )
    ]
    items, next_page_token = _page_slice(decisions, page_token, page_size)
    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/evolution-decisions/{decision_id}")
async def get_evolution_decision(
    decision_id: str, authorization: Optional[str] = Header(default=None),
):
    """EV-02: Evolution Decision Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decision = read_store.get_evolution_decision_by_id(decision_id)
    if not decision:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evolution decision not found",
            f"Evolution decision {decision_id} does not exist",
        )

    payload = _project_evolution_decision_contract(decision)
    payload["meta"] = _snapshot_meta(utc_now())
    return payload


@app.get("/api/v1/freeze-orders")
async def list_freeze_orders(
    status: Optional[str] = None,
    scope: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """EV-03: Freeze Order List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    orders = [
        _project_freeze_order_contract(order)
        for order in read_store.list_freeze_orders(status=status, scope=scope)
    ]
    return {
        "items": orders,
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/rollbacks")
async def list_rollbacks(
    runtime_id: Optional[str] = None,
    action_type: Optional[str] = None,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """EV-04: Global Rollback List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    rollbacks = [
        _project_rollback_contract(rollback)
        for rollback in read_store.list_all_rollbacks(
            runtime_id=runtime_id,
            action_type=action_type,
            time_range=time_range,
        )
    ]
    return {
        "items": rollbacks,
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/operator/mutation-review/{decision_id}")
async def get_mutation_review(
    decision_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """EW-05: Compose the operator mutation-review projection."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    decision, approval_decision, linked_incident, linked_postmortem = _mutation_review_inputs(decision_id)
    if decision is None:
        return JSONResponse(
            status_code=404,
            content={"error": "decision_not_found", "decision_id": decision_id},
        )

    payload = _mutation_review_projection(
        decision,
        approval_decision=approval_decision,
        linked_incident=linked_incident,
        linked_postmortem=linked_postmortem,
        identity=identity,
        snapshot_at=utc_now(),
    )

    if payload["meta"]["surfaces"]["mutation_review"] == "unavailable":
        return JSONResponse(
            status_code=503,
            content={
                "error": "evidence_unavailable",
                "meta": {
                    "surfaces": {
                        "mutation_review": "unavailable",
                    }
                },
            },
        )

    required_fields = (
        "decision_id",
        "target_type",
        "target_id",
        "target_version",
        "action_type",
        "decision_state",
        "risk_level",
        "created_at",
    )
    missing_fields = [field for field in required_fields if payload.get(field) in (None, "")]
    if missing_fields:
        return JSONResponse(
            status_code=503,
            content={
                "error": "evidence_unavailable",
                "detail": f"Mutation review projection is missing required fields: {missing_fields}",
                "meta": {
                    "surfaces": {
                        "mutation_review": "unavailable",
                    }
                },
            },
        )

    return payload


# --------------------------------------------------------------------------- #
# Lineage Surfaces (Wave 3 — LN-01 – LN-03)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/lineage")
async def list_lineage(
    artifact_id: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """LN-01: Aggregated lineage list with optional artifact filter."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    surface = _dataset_surface_status("lineage_edges", snapshot_at=snapshot_at)
    items = read_store.list_lineage_records(artifact_id=artifact_id)
    if surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    return {
        "items": items,
        "page_info": {
            "next_page_token": next_page_token,
        },
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/lineage/edges/{edge_id}")
async def get_lineage_edge(
    edge_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """LN-02: Lineage Edge Detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    edge = read_store.get_lineage_edge(edge_id)
    if not edge:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Lineage edge not found",
            f"Lineage edge {edge_id} does not exist",
        )

    payload = dict(edge)
    payload["meta"] = _snapshot_meta(utc_now())
    return payload


@app.get("/api/v1/lineage/graph")
async def get_lineage_graph(
    root_type: Optional[str] = None,
    root_id: str = Query(...),
    depth: int = 3,
    authorization: Optional[str] = Header(default=None),
):
    """LN-03: Lineage Graph from a root artifact with configurable depth."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # Clamp depth to allowed range (§4.3)
    depth = max(1, min(depth, 10))

    snapshot_at = utc_now()
    edges = read_store.get_lineage_graph(root_type=root_type, root_id=root_id, depth=depth)
    nodes = read_store.get_lineage_graph_nodes(edges)
    return {
        "nodes": nodes,
        "edges": [
            {
                "id": edge.get("id"),
                "from_artifact_id": edge.get("from_artifact_id"),
                "to_artifact_id": edge.get("to_artifact_id"),
                "relationship": edge.get("relationship"),
            }
            for edge in edges
        ],
        "meta": _snapshot_meta(snapshot_at),
    }


@app.get("/api/v1/lineage/inspiration/{artifact_id}")
async def get_inspiration_graph(
    artifact_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """EW-04: BFF-composed inspiration graph for a target artifact."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    projection = read_store.get_inspiration_graph(artifact_id)
    artifact_exists = read_store.artifact_exists(artifact_id)
    if projection is None and artifact_exists:
        projection = _ew04_inspiration_projection_from_lineage_edges(artifact_id)

    if projection is None and not artifact_exists:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Artifact not found",
            f"Artifact {artifact_id} does not exist",
        )

    return _ew04_inspiration_payload(
        artifact_id,
        projection,
        snapshot_at=snapshot_at,
        artifact_exists=artifact_exists or projection is not None,
    )


# --------------------------------------------------------------------------- #
# Telemetry Surfaces (Wave 3 — TL-01 – TL-03)
# --------------------------------------------------------------------------- #


@app.get("/api/v1/telemetry")
async def list_telemetry(
    pool_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-01: Telemetry Event List with optional filters."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    events = read_store.list_telemetry_events(
        pool_id=pool_id, artifact_id=artifact_id, time_range=time_range,
    )
    return {
        "data": events,
        "meta": {
            "total": len(events),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/telemetry/{runtime_id}/summary")
async def get_telemetry_summary(
    runtime_id: str,
    time_range: Optional[str] = None,
    aggregate_by: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-02: Telemetry Summary for a runtime."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    summary = read_store.get_telemetry_summary(runtime_id)
    if not summary:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Telemetry summary not found",
            f"No telemetry summary for runtime {runtime_id}",
        )

    return {
        "data": summary,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/telemetry/{artifact_id}/performance")
async def get_telemetry_performance(
    artifact_id: str,
    time_range: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """TL-03: Telemetry Performance Chart for an artifact."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    performance = read_store.get_telemetry_performance(artifact_id)
    if not performance:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Telemetry performance data not found",
            f"No performance data for artifact {artifact_id}",
        )

    return {
        "data": performance,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Consultation surfaces (CS-01 – CS-06)
# Derived from PERSONA_RUNTIME_MODEL.md §6, §13, §14 via
# CONSULTATION_SURFACE_CONTRACT.md.  All surfaces are GET-only —
# writes are the Persona Plane's responsibility.
# --------------------------------------------------------------------------- #


@app.get("/api/v1/personas/{persona_id}/consultations")
def list_consultations(
    persona_id: str,
    consultation_type: Optional[str] = Query(default=None, alias="filter.consultation_type"),
    status: Optional[str] = Query(default=None, alias="filter.status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    """CS-01: List consultation sessions for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if persona is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"No persona with id {persona_id}",
        )

    consultations = read_store.list_consultations_for_persona(
        persona_id,
        consultation_type=consultation_type,
        status=status,
        page=page,
        page_size=page_size,
    )
    if consultations is None:
        return {
            "data": [],
            "meta": {
                "total": 0,
                "page": page,
                "page_size": page_size,
                "staleness": {
                    "served_from": "unavailable",
                    "last_known_at": utc_now(),
                },
            },
        }

    start = (page - 1) * page_size
    page_data = consultations[start: start + page_size]
    return {
        "data": [
            {
                **s,
                "_links": {
                    "self": f"/api/v1/consultations/{s['session_id']}",
                    "participants": f"/api/v1/consultations/{s['session_id']}/participants",
                    "outcome": f"/api/v1/consultations/{s['session_id']}/outcome",
                },
            }
            for s in page_data
        ],
        "meta": {
            "total": len(consultations),
            "page": page,
            "page_size": page_size,
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}")
def get_consultation(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-02: Consultation session detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    session = read_store.get_consultation(session_id)
    if session is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": {
            **session,
            "_links": {
                "self": f"/api/v1/consultations/{session_id}",
                "participants": f"/api/v1/consultations/{session_id}/participants",
                "outcome": f"/api/v1/consultations/{session_id}/outcome",
                "evidence": f"/api/v1/consultations/{session_id}/evidence",
            },
        },
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/participants")
def get_consultation_participants(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-03: All participants in a consultation session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    participants = read_store.get_consultation_participants(session_id)
    if participants is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": [
            {
                **p,
                "_links": {
                    "self": f"/api/v1/sessions/{p['session_id']}",
                    "persona": f"/api/v1/personas/{p['persona_id']}",
                },
            }
            for p in participants
        ],
        "meta": {
            "total": len(participants),
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/outcome")
def get_consultation_outcome(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-04: Consultation outcome projection."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    outcome = read_store.get_consultation_outcome(session_id)
    if outcome is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return {
        "data": outcome,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


@app.get("/api/v1/consultations/{session_id}/evidence")
def get_consultation_evidence(
    session_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-05: Evidence refs attached to a consultation session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    evidence = read_store.get_consultation_evidence(session_id)
    if evidence is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None
    evidence, redacted_count = redact_evidence_refs(identity, evidence, capabilities=capabilities)

    return {
        "data": evidence,
        "meta": {
            "total": len(evidence),
            "staleness": _meta_staleness(),
            "supporting_counts": {"redacted_evidence_count": redacted_count},
        },
    }


@app.get("/api/v1/consultations/{session_id}/transcript")
def get_consultation_transcript(
    session_id: str,
    page_token: Optional[str] = None,
    page_size: int = 50,
    from_sequence_no: Optional[int] = None,
    authorization: Optional[str] = Header(default=None),
):
    """CW-02: Ordered debate transcript for a consultation session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    transcript = read_store.get_consult_transcript(
        session_id,
        from_sequence_no=from_sequence_no,
        page_size=page_size,
        page_token=page_token,
    )
    if transcript is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Consultation session not found",
            f"No consultation session with id {session_id}",
        )

    return transcript


@app.get("/api/v1/personas/{persona_id}/consult-policy")
def get_consult_policy(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """CS-06: Consult policy for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    persona = read_store.get_persona(persona_id)
    if persona is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"No persona with id {persona_id}",
        )

    policy = read_store.get_consult_policy(persona_id)
    if policy is None:
        # Policy may not exist yet — return a safe empty structure rather than 404
        # so operators always get a valid read (policy absence is itself informative)
        return {
            "data": {
                "id": None,
                "persona_id": persona_id,
                "required_reviewers": 0,
                "required_committees": [],
                "trigger_rules": [],
                "forbidden_solo_actions": [],
                "escalation_rules": [],
            },
            "meta": {
                "staleness": _meta_staleness(),
                "note": "No consult policy found for this persona. Defaulting to empty policy.",
            },
        }

    return {
        "data": policy,
        "meta": {
            "staleness": _meta_staleness(),
        },
    }


# --------------------------------------------------------------------------- #
# Command submission (write path — async execution)
# --------------------------------------------------------------------------- #

@app.post("/api/v1/operator/commands", response_model=CommandSubmissionResponse, status_code=202)
async def submit_command(
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
    x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """
    Submit an operator command for async execution.

    Returns 202 with a command receipt.  Poll GET /api/v1/operator/commands/{command_id}
    for status updates.
    """
    # 1. Authenticate
    identity = _extract_identity(authorization, mfa_token=x_mfa_token)
    cmd = _normalize_operator_command_payload(payload)

    foundation_context = _build_foundation_command_context(
        cmd=cmd,
        identity=identity,
        raw_payload=payload,
        trace_id=x_trace_id,
        correlation_id=x_correlation_id,
        request_id=x_request_id,
        idempotency_key=x_idempotency_key,
    )

    # 2. Command-specific precondition validation (role + params shape)
    try:
        x_idempotency_key = _require_operator_command_idempotency_key(x_idempotency_key)
        _validate_audit_context(cmd)
        _ensure_live_broker_scope_allowed(cmd, payload)
        _validate_drawer_runtime_target(cmd)
        validator = _VALIDATORS.get(cmd.command)
        if validator:
            validator(cmd.params, identity)
    except HTTPException as exc:
        raise _foundation_bff_error(exc, foundation_context=foundation_context) from exc
    stored_params = _stored_command_params(cmd, identity, raw_payload=payload)

    duplicate = command_store.get_command_by_idempotency_key(
        foundation_context["idempotency_record"].idempotency_key
    )
    if duplicate:
        duplicate_record = (duplicate.get("foundation") or {}).get("idempotency_record") or {}
        if duplicate_record.get("request_hash") != foundation_context["idempotency_record"].request_hash:
            conflict_error = _foundation_idempotency_conflict_error(
                foundation_context=foundation_context,
                existing_command_id=str(duplicate.get("command_id") or ""),
            )
            raise conflict_error
        return _project_command_submission_response(
            command_id=duplicate["command_id"],
            command=cmd.command,
            accepted_at=duplicate.get("submitted_at") or utc_now(),
            status=CommandStatus(duplicate.get("status") or CommandStatus.SUBMITTED.value),
            staleness_warning=None,
        )

    # 3. Concurrent modification check (§5.1)
    active = command_store.get_active_commands_for_target(cmd.target.type.value, cmd.target.id)
    if active:
        error = _bff_error(
            409, ErrorCode.CONCURRENT_MODIFICATION,
            "A command is already in flight for this target",
            f"Command {active[0]['command_id']} is currently {active[0]['status']}",
            precondition_failed="concurrent_safety",
            suggestion="Wait for the in-flight command to complete or time out before retrying",
        )
        raise _foundation_bff_error(error, foundation_context=foundation_context)

    # 4. Degraded mode check (§7.1)
    staleness_warning = _check_read_surface_state()

    # 5. Persist command with full audit record
    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
    idempotency_record = idempotency_record.with_status(
        "succeeded",
        result_ref=f"command:{command_envelope.command_id}",
    )
    foundation_context["idempotency_record"] = idempotency_record
    command_id = command_envelope.command_id
    submitted_at = utc_now()

    # Extract raw token from Authorization header for downstream propagation
    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[len("Bearer "):]

    # Use provided MFA token if present; otherwise stub a 6-digit token when MFA was verified.
    # This keeps the internal API scaffold happy while preserving the "mfa" flag semantics.
    mfa_token = x_mfa_token or ("000000" if identity.mfa_verified else None)

    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "mfa_verified": identity.mfa_verified,
        "reason": cmd.audit_context.reason,
        "incident_id": cmd.audit_context.incident_id,
        "preconditions_checked": [
            "authentication", "authorization", "params_shape", "concurrent_safety"
        ],
        "timestamp": submitted_at,
        "staleness_warning": staleness_warning.model_dump() if staleness_warning else None,
        "auth_token": raw_token,
        "mfa_token": mfa_token,
        "foundation": _serialize_foundation_context(foundation_context),
    }

    command_store.submit_command(
        command_id=command_id,
        command_type=cmd.command,
        target=cmd.target,
        submitted_at=submitted_at,
        params=stored_params,
        audit_context=audit_record,
        foundation_context=_serialize_foundation_context(foundation_context),
    )

    log.info(
        "Accepted command %s (%s) for %s:%s by operator %s",
        command_id, cmd.command.value, cmd.target.type.value, cmd.target.id, identity.operator_id,
    )

    # 6. Queue for async processing
    background_tasks.add_task(_process_command_stub, command_id)

    return _project_command_submission_response(
        command_id=command_id,
        command=cmd.command,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )


def _command_response_durable_meta(idempotency_key: str, *, replayed: bool) -> Dict[str, Any]:
    return {
        "durable": True,
        "liveCapitalSideEffects": False,
        "idempotency": {
            "key": idempotency_key,
            "idempotencyKey": idempotency_key,
            "replayed": replayed,
        },
    }


def _submit_final_command_admission(
    *,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any],
    authorization: Optional[str],
    x_mfa_token: Optional[str],
    x_trace_id: Optional[str],
    x_correlation_id: Optional[str],
    x_request_id: Optional[str],
    x_confirm_token: Optional[str],
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
    route: str = _FINAL_COMMAND_ROUTE,
    source_route: Optional[str] = None,
    audit_extra: Optional[Dict[str, Any]] = None,
    extra_precondition: Optional[Callable[[OperatorIdentity, OperatorCommand], None]] = None,
    enqueue: bool = True,
    include_durable_meta: bool = False,
    response_deprecation: Optional[Dict[str, Any]] = None,
) -> CommandResponse[Dict[str, Any]]:
    """Submit a final-contract command through the shared BFF command admission path."""
    identity = _extract_identity(authorization, mfa_token=x_mfa_token)
    cmd = _normalize_operator_command_payload(payload)

    # Resolve idempotency key before building foundation context so the key is
    # present in the trace from the start.
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)

    foundation_context = _build_foundation_command_context(
        cmd=cmd,
        identity=identity,
        raw_payload=payload,
        trace_id=x_trace_id,
        correlation_id=x_correlation_id,
        request_id=x_request_id,
        idempotency_key=resolved_key,
        route=route,
        source_route=source_route,
    )

    try:
        _reject_body_idempotency_key(payload)
        if extra_precondition is not None:
            extra_precondition(identity, cmd)
        _validate_audit_context(cmd)
        _ensure_live_broker_scope_allowed(cmd, payload)
        _validate_drawer_runtime_target(cmd)
        validator = _VALIDATORS.get(cmd.command)
        if validator:
            validator(cmd.params, identity)
        _require_final_command_preconditions(
            cmd=cmd,
            payload=payload,
            confirm_token=x_confirm_token,
            correlation_id=foundation_context["trace_context"].correlation_id,
        )
    except HTTPException as exc:
        raise _foundation_bff_error(exc, foundation_context=foundation_context) from exc

    stored_params = _stored_command_params(cmd, identity, raw_payload=payload)

    duplicate = command_store.get_command_by_idempotency_key(
        foundation_context["idempotency_record"].idempotency_key
    )
    if duplicate:
        duplicate_record = (duplicate.get("foundation") or {}).get("idempotency_record") or {}
        if duplicate_record.get("request_hash") != foundation_context["idempotency_record"].request_hash:
            raise _foundation_idempotency_conflict_error(
                foundation_context=foundation_context,
                existing_command_id=str(duplicate.get("command_id") or ""),
            )
        return _project_final_command_response(
            command_id=duplicate["command_id"],
            command=cmd.command,
            accepted_at=duplicate.get("submitted_at") or utc_now(),
            status=CommandStatus(duplicate.get("status") or CommandStatus.SUBMITTED.value),
            staleness_warning=None,
            meta=_command_response_durable_meta(resolved_key, replayed=True)
            if include_durable_meta
            else None,
            deprecation=response_deprecation,
        )

    active = command_store.get_active_commands_for_target(cmd.target.type.value, cmd.target.id)
    if active:
        error = _bff_error(
            409, ErrorCode.CONCURRENT_MODIFICATION,
            "A command is already in flight for this target",
            f"Command {active[0]['command_id']} is currently {active[0]['status']}",
            precondition_failed="concurrent_safety",
            suggestion="Wait for the in-flight command to complete or time out before retrying",
        )
        raise _foundation_bff_error(error, foundation_context=foundation_context)

    staleness_warning = _check_read_surface_state()

    command_envelope: CommandEnvelope = foundation_context["command_envelope"]
    idempotency_record: IdempotencyRecord = foundation_context["idempotency_record"]
    idempotency_record = idempotency_record.with_status(
        "succeeded",
        result_ref=f"command:{command_envelope.command_id}",
    )
    foundation_context["idempotency_record"] = idempotency_record
    command_id = command_envelope.command_id
    submitted_at = utc_now()
    receipt_dual_write = _command_dual_write_receipts(
        command_id=command_id,
        command=cmd.command.value,
        status=ActionCommandStatus.ACCEPTED.value,
        accepted_at=submitted_at,
    )

    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[len("Bearer "):]
    mfa_token = x_mfa_token or ("000000" if identity.mfa_verified else None)

    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "mfa_verified": identity.mfa_verified,
        "reason": cmd.audit_context.reason,
        "incident_id": cmd.audit_context.incident_id,
        "preconditions_checked": [
            "authentication", "authorization", "params_shape", "concurrent_safety"
        ],
        "timestamp": submitted_at,
        "staleness_warning": staleness_warning.model_dump() if staleness_warning else None,
        "auth_token": raw_token,
        "mfa_token": mfa_token,
        "foundation": _serialize_foundation_context(foundation_context),
        "receipt_dual_write": receipt_dual_write,
    }
    if audit_extra:
        audit_record.update({key: value for key, value in audit_extra.items() if value is not None})

    command_store.submit_command(
        command_id=command_id,
        command_type=cmd.command,
        target=cmd.target,
        submitted_at=submitted_at,
        params=stored_params,
        audit_context=audit_record,
        foundation_context=_serialize_foundation_context(foundation_context),
    )

    log.info(
        "Accepted final-contract command %s (%s) for %s:%s by operator %s",
        command_id, cmd.command.value, cmd.target.type.value, cmd.target.id, identity.operator_id,
    )

    if enqueue:
        background_tasks.add_task(_process_command_stub, command_id)

    return _project_final_command_response(
        command_id=command_id,
        command=cmd.command,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
        meta=_command_response_durable_meta(resolved_key, replayed=False)
        if include_durable_meta
        else None,
        deprecation=response_deprecation,
    )


@app.post("/bff/v1/commands", status_code=202)
async def submit_final_command(
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
    x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """
    Submit an operator command (final BFF contract).

    Idempotency-Key is the required header; X-Idempotency-Key is accepted as a
    temporary compatibility alias when Idempotency-Key is absent.
    idempotencyKey in the request body is rejected.
    Returns CommandResponse<T> wrapping the command receipt.
    """
    return _submit_final_command_admission(
        background_tasks=background_tasks,
        payload=payload,
        authorization=authorization,
        x_mfa_token=x_mfa_token,
        x_trace_id=x_trace_id,
        x_correlation_id=x_correlation_id,
        x_request_id=x_request_id,
        x_confirm_token=x_confirm_token,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
        route=_FINAL_COMMAND_ROUTE,
    )


@app.patch("/bff/agora/journal/{entry_id}")
async def patch_agora_journal_entry(
    entry_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
    x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    content_type: Optional[str] = Header(default=None, alias="Content-Type"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """Apply JSON Merge Patch to an Agora decision journal entry facade."""
    identity = _extract_identity(authorization, mfa_token=x_mfa_token)
    _require_journal_write_role(identity)
    _require_merge_patch_content_type(content_type)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    _reject_body_idempotency_key(payload)
    patch = _validate_journal_merge_patch_payload(payload, identity)

    correlation_id = str(x_correlation_id or x_trace_id or "").strip() or None
    request_hash = _stable_json_hash(
        {
            "route": "PATCH /bff/agora/journal/{id}",
            "entry_id": entry_id,
            "patch": patch,
        }
    )
    result = read_store.patch_decision_journal_entry(
        entry_id,
        patch=patch,
        actor_id=identity.operator_id,
        correlation_id=correlation_id,
        idempotency_key=resolved_key,
        request_hash=request_hash,
    )
    if result is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora journal entry not found",
            f"DecisionJournalEntry {entry_id} is not available in the journal store",
            precondition_failed="journal_entry",
            suggestion="Refresh the journal list and retry against an existing entry",
            details_extra={"entryId": entry_id},
            correlation_id=correlation_id,
        )
    if result.get("status") == "conflict":
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different journal patch",
            f"idempotency_key={resolved_key} is already bound to another journal patch",
            precondition_failed="idempotency_conflict",
            suggestion="Reuse the original payload for this key or submit with a new Idempotency-Key",
            details_extra={
                "idempotencyKey": resolved_key,
                "existingPatchId": result.get("existing_patch_id"),
            },
            correlation_id=correlation_id,
        )

    entry = DecisionJournalEntryDTO(**(result.get("entry") or {}))
    audit = dict(result.get("audit") or {})
    if result.get("status") == "updated":
        _publish_event(
            _sse_buffers["journal"],
            _sse_subscribers["journal"],
            "journal.entry.updated",
            {
                "entryId": entry.id,
                "auditId": audit.get("auditId"),
                "changedFields": ((audit.get("diff") or {}).get("changedFields") or []),
                "correlationId": correlation_id,
            },
        )

    return CommandResponse[DecisionJournalEntryDTO](
        status=ActionCommandStatus.COMPLETED,
        data=entry,
        meta={
            "audit": audit,
            "idempotency": {
                "idempotencyKey": resolved_key,
                "replayed": result.get("status") == "replayed",
            },
            "requestId": str(x_request_id or "").strip() or None,
            "canonicalWriteAuthority": "agora_journal_service",
            "persistenceMode": "bff_local_dev_store",
            "degraded": True,
        },
    )


# --------------------------------------------------------------------------- #
# Agora Core BFF Compatibility (BFF-LUV-GAP-006)
# --------------------------------------------------------------------------- #

_AGORA_CORE_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_AGORA_SIGNAL_DECISIONS = {"agree", "disagree", "flag_suspicious"}
_AGORA_EVIDENCE_ALLOWED_MIMES = {
    "application/pdf",
    "text/markdown",
    "text/plain",
    "text/csv",
    "image/png",
    "image/jpeg",
}
_AGORA_EVIDENCE_MAX_FILES = 12
_AGORA_EVIDENCE_MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
_AGORA_EVIDENCE_MAX_TOTAL_SIZE_BYTES = 100 * 1024 * 1024


def _agora_core_idempotency_check(
    resolved_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    existing = _AGORA_CORE_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different Agora request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")


def _agora_response_payload(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return json.loads(json.dumps(value))
    return {"data": json.loads(json.dumps(value))}


def _agora_list_response(
    *,
    dataset: str,
    surface_key: str,
    items: List[Dict[str, Any]],
    page_token: Optional[str],
    page_size: int,
    snapshot_at: str,
) -> Dict[str, Any]:
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(dataset, surface_key, snapshot_at=snapshot_at, total=total),
    }


def _agora_record_id(record: Dict[str, Any], fields: List[str]) -> str:
    for field in fields:
        clean = str(record.get(field) or "").strip()
        if clean:
            return clean
    return ""


def _agora_string_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return _dedupe_nonblank_strings(_split_claim_string(raw))
    if isinstance(raw, (list, tuple, set)):
        return _dedupe_nonblank_strings(list(raw))
    return _dedupe_nonblank_strings([raw])


def _agora_required_text(payload: Dict[str, Any], *fields: str) -> str:
    for field in fields:
        clean = str(payload.get(field) or "").strip()
        if clean:
            return clean
    label = fields[0] if fields else "value"
    raise _bff_error(
        422,
        ErrorCode.INVALID_PARAMS,
        f"{label} is required",
        f"Agora request requires a non-empty {label}",
        precondition_failed=label,
    )


def _agora_signal_feedback_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    decision = str(payload.get("decision") or "").strip()
    if decision not in _AGORA_SIGNAL_DECISIONS:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Signal feedback decision is invalid",
            "decision must be one of agree, disagree, or flag_suspicious",
            precondition_failed="signal_feedback.decision",
        )
    try:
        confidence = int(payload.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Signal feedback confidence is invalid",
            "confidence must be an integer from 1 to 5",
            precondition_failed="signal_feedback.confidence",
        ) from exc
    if confidence < 1 or confidence > 5:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Signal feedback confidence is invalid",
            "confidence must be an integer from 1 to 5",
            precondition_failed="signal_feedback.confidence",
        )
    reason = str(payload.get("reason") or "").strip() or None
    if (decision == "disagree" and confidence >= 4 and not reason) or (
        decision == "flag_suspicious" and not reason
    ):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Signal feedback reason is required",
            "reason is required for high-confidence disagree and flag_suspicious feedback",
            precondition_failed="signal_feedback.reason",
        )
    try:
        edit_window_seconds = int(payload.get("editWindowSeconds") or payload.get("edit_window_seconds") or 30)
    except (TypeError, ValueError) as exc:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Signal feedback edit window is invalid",
            "editWindowSeconds must be a positive integer",
            precondition_failed="signal_feedback.editWindowSeconds",
        ) from exc
    return {
        "decision": decision,
        "confidence": confidence,
        "reason": reason,
        "edit_window_seconds": max(1, edit_window_seconds),
    }


def _agora_evidence_files_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_files = payload.get("files")
    if raw_files is None and any(key in payload for key in ("fileName", "filename", "name")):
        raw_files = [payload]
    if not isinstance(raw_files, list):
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Evidence files are required",
            "Committee evidence upload requires a files array.",
            precondition_failed="committee_evidence.files",
            suggestion="Send {\"files\": [{fileName, mimeType, sizeBytes, metadata}]}",
        )
    return [item for item in raw_files if isinstance(item, dict)]


def _agora_validate_evidence_files(
    *,
    existing_files: List[Dict[str, Any]],
    incoming_files: List[Dict[str, Any]],
) -> None:
    violations: List[Dict[str, Any]] = []
    if not incoming_files:
        violations.append({"code": "missing_metadata", "field": "files"})
    if len(existing_files) + len(incoming_files) > _AGORA_EVIDENCE_MAX_FILES:
        violations.append({"code": "too_many_files"})

    total_size = 0
    for existing in existing_files:
        try:
            total_size += int(existing.get("sizeBytes") or existing.get("size_bytes") or 0)
        except (TypeError, ValueError):
            continue

    for item in incoming_files:
        file_name = str(item.get("fileName") or item.get("filename") or item.get("name") or "").strip()
        mime_type = str(item.get("mimeType") or item.get("mime_type") or "").strip()
        raw_size = item.get("sizeBytes")
        if raw_size is None:
            raw_size = item.get("size_bytes")
        try:
            size_bytes = int(raw_size)
        except (TypeError, ValueError):
            size_bytes = -1
        total_size += max(size_bytes, 0)

        if size_bytes < 0:
            violations.append({"code": "missing_metadata", "fileName": file_name, "field": "sizeBytes"})
        elif size_bytes > _AGORA_EVIDENCE_MAX_FILE_SIZE_BYTES:
            violations.append({"code": "file_too_large", "fileName": file_name})
        if mime_type not in _AGORA_EVIDENCE_ALLOWED_MIMES:
            violations.append({"code": "mime_not_allowed", "fileName": file_name})
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        missing = [
            key
            for key in ("source", "title", "uploadedBy", "createdAt")
            if not str(metadata.get(key) or "").strip()
        ]
        if missing:
            violations.append({"code": "missing_metadata", "fileName": file_name, "fields": missing})

    if total_size > _AGORA_EVIDENCE_MAX_TOTAL_SIZE_BYTES:
        violations.append({"code": "total_too_large"})

    if violations:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Committee evidence upload rejected",
            "One or more committee evidence files failed server-side validation.",
            precondition_failed="committee_evidence.files",
            suggestion="Check file count, file size, MIME type, and required metadata before retrying.",
            details_extra={"violations": violations},
        )


def _agora_persona_lab_commit_payload(draft_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    payload_draft = str(payload.get("personaDraftId") or payload.get("persona_draft_id") or draft_id).strip()
    if payload_draft and payload_draft != draft_id:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Persona draft id mismatch",
            "personaDraftId in the request body must match the draftId route parameter.",
            precondition_failed="personaDraftId",
        )
    raw_runs = payload.get("evaluationRunIds") or payload.get("evaluation_run_ids") or []
    if not isinstance(raw_runs, list):
        raw_runs = [raw_runs]
    evaluation_run_ids = _dedupe_nonblank_strings(raw_runs)
    if not evaluation_run_ids:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "evaluationRunIds are required",
            "Persona lab commit requires at least one evaluation run id before handoff.",
            precondition_failed="evaluationRunIds",
        )
    change_summary = str(payload.get("changeSummary") or payload.get("change_summary") or "").strip()
    if not change_summary:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "changeSummary is required",
            "Persona lab commit requires a concise change summary for management review.",
            precondition_failed="changeSummary",
        )
    priority = str(payload.get("priority") or "normal").strip().lower()
    if priority not in {"low", "normal", "high", "urgent"}:
        priority = "normal"
    return {
        "personaDraftId": draft_id,
        "basePersonaId": str(payload.get("basePersonaId") or payload.get("base_persona_id") or "").strip() or None,
        "evaluationRunIds": evaluation_run_ids,
        "changeSummary": change_summary,
        "requestedRoutePolicyId": (
            str(payload.get("requestedRoutePolicyId") or payload.get("requested_route_policy_id") or "").strip()
            or None
        ),
        "priority": priority,
    }


def _agora_get_insight(insight_id: str) -> Optional[Dict[str, Any]]:
    getter = getattr(read_store, "get_insight_card", None)
    if callable(getter):
        item = getter(insight_id)
        if item:
            return item
    for item in read_store.list_agora_insights():
        if _agora_record_id(item, ["insight_id", "id"]) == insight_id:
            return item
    return None


def _agora_submit_command(
    *,
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: OperatorIdentity,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    request_payload = {
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "action_id": action_id,
        "payload": payload,
    }
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=resolved_key,
        operation_type=f"bff.{command_type.value}",
        target_ref=f"{entity_type.value}:{entity_id}",
        request_payload=request_payload,
        trace_id=command_id,
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
        "idempotency_key": resolved_key,
        "command_id": command_id,
    }
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context={"idempotency_record": idempotency_record.to_dict()},
    )
    audit = read_store.record_agora_audit_event({
        "action": f"agora.{action_id}",
        "targetType": entity_type.value,
        "targetId": entity_id,
        "commandId": command_id,
        "actorId": identity.operator_id,
        "recordedAt": submitted_at,
        "idempotencyKey": resolved_key,
    })
    result = _agora_response_payload(
        _project_final_command_response(
            command_id=command_id,
            command=command_type,
            accepted_at=submitted_at,
            status=CommandStatus.SUBMITTED,
            staleness_warning=staleness_warning,
        )
    )
    meta = result.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        result["meta"] = meta
    meta["audit"] = audit
    return result


def _agora_action_command(
    *,
    route: str,
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: OperatorIdentity,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    request_hash = _stable_json_hash({
        "route": route,
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "action_id": action_id,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    result = _agora_submit_command(
        entity_type=entity_type,
        entity_id=entity_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=command_type,
    )
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/daily")
async def bff_agora_daily(
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora daily brief assembled from current read surfaces."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    signals = read_store.list_agora_signals()
    watchlist = read_store.list_agora_watchlist()
    journal = read_store.list_decision_journal_entries()
    research_tasks = read_store.list_research_tickets(statuses=["new", "triaged", "open", "in_progress"])
    pending_signals = [
        item for item in signals
        if str(item.get("reviewStatus") or item.get("review_status") or "").strip() == "pending_trader_review"
    ]
    brief = {
        "id": f"agora-daily-{snapshot_at[:10]}",
        "date": snapshot_at[:10],
        "generatedAt": snapshot_at,
        "kpis": {
            "watchlistMoveCount": len(watchlist),
            "signalReviewQueue": len(pending_signals),
            "personaBriefCount": len(journal),
            "researchQuestionCount": len(research_tasks),
        },
        "sections": {
            "signals": signals[:5],
            "watchlist": watchlist[:5],
            "journal": journal[:5],
            "research_tasks": research_tasks[:5],
        },
    }
    meta = _read_surface_meta("agora_signals", "agora_daily", snapshot_at=snapshot_at)
    meta["surfaces"]["watchlist"] = _dataset_surface_status("agora_watchlist", snapshot_at=snapshot_at)
    meta["surfaces"]["research_tasks"] = _dataset_surface_status("research_tickets", snapshot_at=snapshot_at)
    return {"data": brief, "items": [brief], "meta": meta}


@app.get("/bff/agora/signals")
async def bff_agora_signals(
    review_status: Optional[str] = Query(default=None, alias="reviewStatus"),
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora signal review queue."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = read_store.list_agora_signals(review_status=review_status or status)
    return _agora_list_response(
        dataset="agora_signals",
        surface_key="agora_signal_list",
        items=items,
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.get("/bff/agora/signals/{signalId}")
async def bff_agora_signal_detail(
    signalId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora signal detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    signal = read_store.get_agora_signal(signalId)
    if not signal:
        surface = _dataset_surface_status("agora_signals", snapshot_at=snapshot_at)
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora signal not found",
            f"Agora signal {signalId} does not exist",
            precondition_failed="signal_id",
        )
    return {
        "data": signal,
        "meta": _read_surface_meta("agora_signals", "agora_signal_detail", snapshot_at=snapshot_at),
    }


@app.post("/bff/agora/signals/{signalId}/feedback")
async def bff_agora_signal_feedback(
    signalId: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: record trader feedback for an Agora signal."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    feedback_payload = _agora_signal_feedback_payload(payload)
    request_hash = _stable_json_hash({
        "route": "POST /bff/agora/signals/{signalId}/feedback",
        "signalId": signalId,
        "payload": feedback_payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    feedback = read_store.record_agora_signal_feedback(
        signalId,
        decision=feedback_payload["decision"],
        confidence=feedback_payload["confidence"],
        reason=feedback_payload["reason"],
        actor_id=identity.operator_id,
        edit_window_seconds=feedback_payload["edit_window_seconds"],
        recorded_at=utc_now(),
    )
    if feedback is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora signal not found",
            f"Agora signal {signalId} does not exist",
            precondition_failed="signal_id",
        )
    command = _agora_submit_command(
        entity_type=ObjectType.AGORA_SIGNAL,
        entity_id=signalId,
        action_id="feedback",
        resolved_key=resolved_key,
        identity=identity,
        payload=feedback_payload,
        command_type=CommandType.AGORA_SIGNAL_FEEDBACK,
    )
    _publish_event(
        _sse_buffers["signal"],
        _sse_subscribers["signal"],
        "agora.signal.feedback_recorded",
        {"signalId": signalId, "feedbackId": feedback.get("feedbackId"), "decision": feedback.get("decision")},
    )
    result = {
        "status": ActionCommandStatus.COMPLETED.value,
        "data": {
            "feedback": feedback,
            "signal": read_store.get_agora_signal(signalId),
        },
        "meta": {
            "command": command.get("data"),
            "audit": (command.get("meta") or {}).get("audit"),
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/watchlist")
async def bff_agora_watchlist(
    page_token: Optional[str] = None,
    page_size: int = Query(default=50, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora watchlist assets for daily KPIs."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _agora_list_response(
        dataset="agora_watchlist",
        surface_key="agora_watchlist",
        items=read_store.list_agora_watchlist(),
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.get("/bff/agora/sessions")
async def bff_agora_sessions(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora ask/session list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _agora_list_response(
        dataset="agora_sessions",
        surface_key="agora_session_list",
        items=read_store.list_agora_sessions(status=status),
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.post("/bff/agora/sessions", status_code=201)
async def bff_create_agora_session(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create an Agora ask/session record."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/agora/sessions", "payload": payload})
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or f"agora-sess-{uuid.uuid4().hex[:10]}")
    title = str(payload.get("title") or "Untitled Agora session").strip()
    result = {
        "data": read_store.create_agora_session(
            session_id=session_id,
            title=title,
            actor_id=identity.operator_id,
            payload=payload,
            created_at=snapshot_at,
        ),
        "meta": {"snapshot_at": snapshot_at},
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/sessions/{sessionId}")
async def bff_agora_session_detail(
    sessionId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora ask/session detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    session = read_store.get_agora_session(sessionId)
    if not session:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora session not found",
            f"Agora session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    return {
        "data": session,
        "meta": _read_surface_meta("agora_sessions", "agora_session_detail", snapshot_at=snapshot_at),
    }


@app.get("/bff/agora/sessions/{sessionId}/messages")
async def bff_agora_session_messages(
    sessionId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: messages for an Agora session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    messages = read_store.list_agora_session_messages(sessionId)
    if messages is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora session not found",
            f"Agora session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    return {
        "data": messages,
        "items": messages,
        "page_info": {"next_page_token": None, "total": len(messages)},
        "meta": _read_surface_meta("agora_sessions", "agora_session_messages", snapshot_at=snapshot_at),
    }


@app.post("/bff/agora/sessions/{sessionId}/messages", status_code=201)
async def bff_create_agora_session_message(
    sessionId: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: append a message to an Agora session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    content = _agora_required_text(payload, "content", "body", "message")
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({
        "route": "POST /bff/agora/sessions/{sessionId}/messages",
        "sessionId": sessionId,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    message_id = str(payload.get("id") or payload.get("messageId") or f"agora-msg-{uuid.uuid4().hex[:10]}")
    message = read_store.append_agora_session_message(
        sessionId,
        message_id=message_id,
        content=content,
        actor_id=identity.operator_id,
        payload=payload,
        created_at=snapshot_at,
    )
    if message is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora session not found",
            f"Agora session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "agora.session.message_created",
        {"sessionId": sessionId, "messageId": message.get("id")},
    )
    result = {"data": message, "meta": {"snapshot_at": snapshot_at}}
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.post("/bff/agora/committee/{sessionId}/evidence-pack", status_code=201)
async def bff_create_agora_committee_evidence_pack(
    sessionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create or refresh a committee evidence pack for an Agora session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({
        "route": "POST /bff/agora/committee/{sessionId}/evidence-pack",
        "sessionId": sessionId,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    if not read_store.get_agora_session(sessionId):
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora committee session not found",
            f"Agora committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    snapshot_at = utc_now()
    pack = read_store.create_agora_committee_evidence_pack(
        sessionId,
        payload=payload,
        actor_id=identity.operator_id,
        created_at=snapshot_at,
    )
    audit = read_store.record_agora_audit_event({
        "action": "agora.committee.evidence_pack.created",
        "targetType": "AgoraCommitteeEvidencePack",
        "targetId": pack.get("id"),
        "sessionId": sessionId,
        "actorId": identity.operator_id,
        "recordedAt": snapshot_at,
        "idempotencyKey": resolved_key,
    })
    result = {
        "data": pack,
        "meta": {
            "snapshot_at": snapshot_at,
            "audit": audit,
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.post("/bff/agora/committee/{sessionId}/evidence-pack/files", status_code=201)
async def bff_upload_agora_committee_evidence_files(
    sessionId: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: attach validated file metadata to a committee evidence pack."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    files = _agora_evidence_files_from_payload(payload)
    request_hash = _stable_json_hash({
        "route": "POST /bff/agora/committee/{sessionId}/evidence-pack/files",
        "sessionId": sessionId,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    existing_pack = read_store.get_agora_committee_evidence_pack(sessionId)
    existing_files = list((existing_pack or {}).get("uploadedFiles") or [])
    _agora_validate_evidence_files(existing_files=existing_files, incoming_files=files)
    snapshot_at = utc_now()
    pack = read_store.append_agora_committee_evidence_files(
        sessionId,
        files=files,
        actor_id=identity.operator_id,
        uploaded_at=snapshot_at,
    )
    if pack is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora committee session not found",
            f"Agora committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    new_files = list(pack.pop("newFiles", []))
    audit = read_store.record_agora_audit_event({
        "action": "agora.committee.evidence_pack.files_uploaded",
        "targetType": "AgoraCommitteeEvidencePack",
        "targetId": pack.get("id"),
        "sessionId": sessionId,
        "fileIds": [item.get("id") for item in new_files],
        "actorId": identity.operator_id,
        "recordedAt": snapshot_at,
        "idempotencyKey": resolved_key,
    })
    result = {
        "data": pack,
        "items": new_files,
        "meta": {
            "snapshot_at": snapshot_at,
            "audit": audit,
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.post("/bff/agora/messages/{messageId}/actions/{actionId}", status_code=202)
async def bff_agora_message_action(
    messageId: str,
    actionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: route an Agora message action through command admission."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    if not read_store.get_agora_message(messageId):
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora message not found",
            f"Agora message {messageId} does not exist",
            precondition_failed="message_id",
        )
    return _agora_action_command(
        route="POST /bff/agora/messages/{messageId}/actions/{actionId}",
        entity_type=ObjectType.AGORA_MESSAGE,
        entity_id=messageId,
        action_id=actionId,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.AGORA_MESSAGE_ACTION,
    )


@app.get("/bff/agora/notes")
async def bff_agora_notes(
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora notebook/research notes."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _agora_list_response(
        dataset="research_notes",
        surface_key="agora_note_list",
        items=read_store.list_agora_notes(),
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.post("/bff/agora/notes", status_code=201)
async def bff_create_agora_note(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create an Agora notebook/research note."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    body = _agora_required_text(payload, "body", "content")
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/agora/notes", "payload": payload})
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    note_id = str(payload.get("id") or payload.get("note_id") or f"note-agora-{uuid.uuid4().hex[:10]}")
    result = {
        "data": read_store.create_agora_note(
            note_id=note_id,
            title=str(payload.get("title") or "").strip() or None,
            body=body,
            actor_id=identity.operator_id,
            payload=payload,
            created_at=snapshot_at,
        ),
        "meta": {"snapshot_at": snapshot_at},
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/journal")
async def bff_agora_journal(
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora decision journal list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _agora_list_response(
        dataset="decision_journal_entries",
        surface_key="agora_journal_list",
        items=read_store.list_decision_journal_entries(),
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.post("/bff/agora/journal", status_code=201)
async def bff_create_agora_journal_entry(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create an Agora decision journal entry."""
    identity = _extract_identity(authorization)
    _require_journal_write_role(identity)
    _reject_body_idempotency_key(payload)
    title = _agora_required_text(payload, "title")
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/agora/journal", "payload": payload})
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    body = str(payload.get("body") or "").strip()
    if not body:
        body_parts = [
            str(payload.get(field) or "").strip()
            for field in ("context", "decision", "rationale")
            if str(payload.get(field) or "").strip()
        ]
        body = "\n\n".join(body_parts)
    snapshot_at = utc_now()
    entry_id = str(payload.get("id") or payload.get("entryId") or f"journal-agora-{uuid.uuid4().hex[:10]}")
    visibility = str(payload.get("visibility") or "private").strip()
    if visibility not in _JOURNAL_VISIBILITY_ROLES:
        visibility = "private"
    if not _journal_visibility_allowed(identity, visibility):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Journal visibility is not allowed",
            f"Role set cannot create a {visibility} journal entry",
            precondition_failed="journal.visibility",
        )
    entry = read_store.create_decision_journal_entry(
        entry_id=entry_id,
        title=title,
        body=body,
        tags=_agora_string_list(payload.get("tags")),
        linked_strategy_ids=_agora_string_list(payload.get("linkedStrategyIds") or payload.get("linked_strategy_ids")),
        linked_persona_ids=_agora_string_list(payload.get("linkedPersonaIds") or payload.get("linked_persona_ids")),
        visibility=visibility,
        actor_id=identity.operator_id,
        created_at=snapshot_at,
    )
    audit = read_store.record_agora_audit_event({
        "action": "agora.journal.create",
        "targetType": "DecisionJournalEntry",
        "targetId": entry_id,
        "actorId": identity.operator_id,
        "recordedAt": snapshot_at,
        "idempotencyKey": resolved_key,
    })
    result = {"data": entry, "meta": {"snapshot_at": snapshot_at, "audit": audit}}
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/insights")
async def bff_agora_insights(
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora insight inbox."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _agora_list_response(
        dataset="insight_cards",
        surface_key="agora_insight_list",
        items=read_store.list_agora_insights(),
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.post("/bff/agora/insights", status_code=201)
async def bff_create_agora_insight(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create an Agora insight card."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    summary = _agora_required_text(payload, "summary", "title")
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/agora/insights", "payload": payload})
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    insight_id = str(payload.get("id") or payload.get("insight_id") or f"ins-agora-{uuid.uuid4().hex[:10]}")
    result = {
        "data": read_store.create_agora_insight(
            insight_id=insight_id,
            summary=summary,
            actor_id=identity.operator_id,
            payload=payload,
            created_at=snapshot_at,
        ),
        "meta": {"snapshot_at": snapshot_at},
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.post("/bff/agora/insights/{insightId}/actions/{actionId}", status_code=202)
async def bff_agora_insight_action(
    insightId: str,
    actionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: route an Agora insight action through command admission."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    if not _agora_get_insight(insightId):
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora insight not found",
            f"Agora insight {insightId} does not exist",
            precondition_failed="insight_id",
        )
    return _agora_action_command(
        route="POST /bff/agora/insights/{insightId}/actions/{actionId}",
        entity_type=ObjectType.AGORA_INSIGHT,
        entity_id=insightId,
        action_id=actionId,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.AGORA_INSIGHT_ACTION,
    )


@app.get("/bff/agora/memory")
async def bff_agora_memory(
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora institutional memory review list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _agora_list_response(
        dataset="institutional_memory_entries",
        surface_key="agora_memory_list",
        items=read_store.list_agora_memory(),
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.post("/bff/agora/memory/{memoryId}/actions/{actionId}", status_code=202)
async def bff_agora_memory_action(
    memoryId: str,
    actionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: route an Agora memory action through command admission."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    if not read_store.get_agora_memory_entry(memoryId):
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Agora memory entry not found",
            f"Agora memory entry {memoryId} does not exist",
            precondition_failed="memory_id",
        )
    return _agora_action_command(
        route="POST /bff/agora/memory/{memoryId}/actions/{actionId}",
        entity_type=ObjectType.AGORA_MEMORY,
        entity_id=memoryId,
        action_id=actionId,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.AGORA_MEMORY_ACTION,
    )


@app.get("/bff/agora/training-examples")
async def bff_agora_training_examples(
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora training examples."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _agora_list_response(
        dataset="agora_training_examples",
        surface_key="agora_training_example_list",
        items=read_store.list_agora_training_examples(),
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.post("/bff/agora/training-examples", status_code=201)
async def bff_create_agora_training_example(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create an Agora training example."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/agora/training-examples", "payload": payload})
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    example_id = str(payload.get("id") or payload.get("trainingExampleId") or f"trn-agora-{uuid.uuid4().hex[:10]}")
    result = {
        "data": read_store.create_agora_training_example(
            example_id=example_id,
            payload=payload,
            actor_id=identity.operator_id,
            created_at=snapshot_at,
        ),
        "meta": {"snapshot_at": snapshot_at},
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/research/tasks")
async def bff_agora_research_tasks(
    status: Optional[str] = None,
    owner: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora-compatible research task list backed by research tickets."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    statuses = _split_csv_query(status)
    tasks = read_store.list_research_tickets(statuses=statuses or None, owner=owner)
    return _agora_list_response(
        dataset="research_tickets",
        surface_key="research_task_list",
        items=tasks,
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.post("/bff/memory/{memoryId}/actions/quarantine", status_code=202)
async def bff_memory_quarantine_action(
    memoryId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: execute-plans compatibility alias for memory quarantine."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    if not read_store.get_agora_memory_entry(memoryId):
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Memory entry not found",
            f"Memory entry {memoryId} does not exist",
            precondition_failed="memory_id",
        )
    return _agora_action_command(
        route="POST /bff/memory/{memoryId}/actions/quarantine",
        entity_type=ObjectType.AGORA_MEMORY,
        entity_id=memoryId,
        action_id="quarantine",
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.AGORA_MEMORY_ACTION,
    )


@app.post("/bff/insights/{insightId}/actions/attach-strategy", status_code=202)
async def bff_insight_attach_strategy_action(
    insightId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: execute-plans compatibility alias for attaching an insight to a strategy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    if not _agora_get_insight(insightId):
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Insight not found",
            f"Insight {insightId} does not exist",
            precondition_failed="insight_id",
        )
    return _agora_action_command(
        route="POST /bff/insights/{insightId}/actions/attach-strategy",
        entity_type=ObjectType.AGORA_INSIGHT,
        entity_id=insightId,
        action_id="attach-strategy",
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.AGORA_INSIGHT_ACTION,
    )


@app.get("/bff/agora/handoffs")
async def bff_agora_handoffs(
    status: Optional[str] = None,
    handoff_type: Optional[str] = Query(default=None, alias="handoffType"),
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: Agora handoff queue records created by workbench actions."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = read_store.list_agora_handoffs(status=status, handoff_type=handoff_type)
    return _agora_list_response(
        dataset="agora_handoffs",
        surface_key="agora_handoff_list",
        items=items,
        page_token=page_token,
        page_size=page_size,
        snapshot_at=snapshot_at,
    )


@app.post("/bff/agora/persona-lab/{draftId}/actions/submit-commit", status_code=202)
async def bff_agora_persona_lab_submit_commit(
    draftId: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: submit a persona-lab sandbox draft as a management handoff."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    commit_payload = _agora_persona_lab_commit_payload(draftId, payload)
    request_hash = _stable_json_hash({
        "route": "POST /bff/agora/persona-lab/{draftId}/actions/submit-commit",
        "draftId": draftId,
        "payload": commit_payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    handoff_id = f"handoff-persona-{uuid.uuid4().hex[:12]}"
    base_persona_id = commit_payload.get("basePersonaId") or draftId
    handoff = read_store.create_agora_handoff(
        handoff_id=handoff_id,
        handoff_type="trainer_feedback_to_persona_update",
        source_route=f"/agora/persona-lab/{draftId}",
        source_entity={"type": "persona_draft", "id": draftId},
        destination_route=f"/personas/{base_persona_id}/management-review",
        destination_queue="persona",
        priority=str(commit_payload.get("priority") or "normal"),
        payload={
            "personaDraftId": commit_payload["personaDraftId"],
            "basePersonaId": commit_payload.get("basePersonaId"),
            "evaluationRunIds": commit_payload["evaluationRunIds"],
            "changeSummary": commit_payload["changeSummary"],
            "requestedRoutePolicyId": commit_payload.get("requestedRoutePolicyId"),
        },
        actor_id=identity.operator_id,
        created_at=snapshot_at,
    )
    command = _agora_submit_command(
        entity_type=ObjectType.PERSONA,
        entity_id=str(base_persona_id),
        action_id="submit-commit",
        resolved_key=resolved_key,
        identity=identity,
        payload=commit_payload,
        command_type=CommandType.PERSONA_ACTION,
    )
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "agora.persona_lab.handoff_submitted",
        {"draftId": draftId, "handoffId": handoff_id, "basePersonaId": base_persona_id},
    )
    result = {
        "status": ActionCommandStatus.ACCEPTED.value,
        "data": handoff,
        "meta": {
            "command": command.get("data"),
            "audit": (command.get("meta") or {}).get("audit"),
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/actions", response_model=BffActionCatalogResponse)
async def get_action_catalog_endpoint(
    authorization: Optional[str] = Header(default=None),
):
    """
    Return the canonical backend action catalog.

    The frontend maps each entry into an ActionDescriptor and never
    invents action truth independently.  High-risk entries carry
    approval / confirm_token / two_man / cooldown governance metadata.
    """
    _extract_identity(authorization)
    return get_action_catalog()


# --------------------------------------------------------------------------- #
# MCP server tool import and action admission (BFF-FINAL-006)
# --------------------------------------------------------------------------- #

_MCP_TOOL_WRITE_ROLES = {"operator", "admin"}
_MCP_IMPORT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_MCP_TOOL_ACTION_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_MCP_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _mcp_tool_registry_key(server_id: str, tool_id: str) -> str:
    return f"{server_id}:{tool_id}"


def _require_mcp_tool_write_role(identity: OperatorIdentity) -> None:
    if _MCP_TOOL_WRITE_ROLES.intersection(identity.roles):
        return
    raise _bff_error(
        403,
        ErrorCode.INSUFFICIENT_ROLE,
        "MCP tool import requires operator-level role",
        "Operator does not hold a role allowed to import or administer MCP tools",
        precondition_failed="role_check",
        suggestion="Escalate to an operator or admin",
    )


def _validate_mcp_server_id(server_id: str) -> str:
    clean = str(server_id or "").strip()
    if clean:
        return clean
    raise _bff_error(
        422,
        ErrorCode.INVALID_PARAMS,
        "MCP server id is required",
        "server_id path parameter must be a non-empty string",
        precondition_failed="server_id",
    )


def _parse_mcp_import_payload(payload: Dict[str, Any]) -> McpToolImportRequest:
    try:
        request = McpToolImportRequest.model_validate(payload)
    except ValidationError as exc:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "MCP tool import payload is invalid",
            str(exc),
            precondition_failed="payload_shape",
            suggestion="Submit server metadata and a non-empty tools array of MCP tool descriptors",
        ) from exc
    if not request.tools:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "MCP tool import requires at least one tool descriptor",
            "tools must contain one or more MCP tool descriptors",
            precondition_failed="tools",
        )
    return request


def _parse_mcp_tool_action_payload(payload: Dict[str, Any]) -> McpToolActionRequest:
    try:
        request = McpToolActionRequest.model_validate(payload)
    except ValidationError as exc:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "MCP tool action payload is invalid",
            str(exc),
            precondition_failed="payload_shape",
            suggestion="Submit a reason and optional scope for the tool action",
        ) from exc
    if not str(request.reason or "").strip():
        raise _bff_error(
            400,
            ErrorCode.INVALID_PARAMS,
            "MCP tool action reason is required",
            "reason must be a non-empty string for audit",
            precondition_failed="reason",
        )
    return request


def _approved_mcp_governance_flags(governance: Dict[str, Any]) -> set[str]:
    flags: set[str] = set()
    for field in ("approvedFlags", "approved_flags", "flags"):
        raw = governance.get(field)
        if isinstance(raw, list):
            flags.update(str(value).strip() for value in raw if str(value).strip())
    if governance.get("allowStandaloneCreate") is True or governance.get("allow_standalone_create") is True:
        flags.add("allow_standalone_create")
    return flags


def _mcp_tool_standalone_create_authorized(
    tool: McpToolDescriptor,
    approved_flags: set[str],
) -> bool:
    authorized = False
    for action in tool.actions:
        if not action.allow_standalone_create:
            continue
        if action.governance_flag and action.governance_flag in approved_flags:
            authorized = True
            continue
        if "allow_standalone_create" in approved_flags:
            authorized = True
            continue
        return False
    return authorized


def _mcp_import_replay_response(
    record: Dict[str, Any],
    request_hash: str,
    *,
    conflict_message: str,
) -> Optional[CommandResponse[McpToolImportData]]:
    if record.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            conflict_message,
            "The same Idempotency-Key is already bound to a different MCP import payload",
            precondition_failed="idempotency_conflict",
            suggestion="Reuse the original payload for this key or submit with a new Idempotency-Key",
        )
    response: CommandResponse[McpToolImportData] = record["response"]
    return CommandResponse[McpToolImportData](
        status=response.status,
        data=response.data.model_copy(update={"replayed": True}),
        meta={**(response.meta or {}), "replayed": True},
    )


def _mcp_action_replay_response(
    record: Dict[str, Any],
    request_hash: str,
    *,
    conflict_message: str,
) -> Optional[CommandResponse[McpToolActionData]]:
    if record.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            conflict_message,
            "The same Idempotency-Key is already bound to a different MCP tool action payload",
            precondition_failed="idempotency_conflict",
            suggestion="Reuse the original payload for this key or submit with a new Idempotency-Key",
        )
    response: CommandResponse[McpToolActionData] = record["response"]
    return CommandResponse[McpToolActionData](
        status=response.status,
        data=response.data.model_copy(update={"replayed": True}),
        meta={**(response.meta or {}), "replayed": True},
    )


def _mcp_tool_action_status(action: McpToolActionVerb) -> McpToolLifecycleStatus:
    if action == McpToolActionVerb.GRANT:
        return McpToolLifecycleStatus.GRANTED
    if action == McpToolActionVerb.REVOKE:
        return McpToolLifecycleStatus.REVOKED
    if action == McpToolActionVerb.DISABLE:
        return McpToolLifecycleStatus.DISABLED
    if action == McpToolActionVerb.TEST:
        return McpToolLifecycleStatus.TESTED
    raise _bff_error(
        422,
        ErrorCode.INVALID_PARAMS,
        "Unsupported MCP tool action",
        f"action={action!r} is not a supported MCP tool lifecycle action",
        precondition_failed="action",
    )


def _require_mcp_action_admitted(
    *,
    tool_record: Dict[str, Any],
    action: McpToolActionVerb,
    request: McpToolActionRequest,
) -> None:
    tool_class = str(tool_record.get("tool_class") or "")
    execution_context = str(
        request.scope.get("executionContext")
        or request.scope.get("execution_context")
        or ""
    ).strip().lower()
    if tool_class == "lean_direct" and action == McpToolActionVerb.GRANT and execution_context == "live":
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "lean_direct MCP tools cannot be granted for live execution",
            "OpenClaw tool permission contract denies direct LEAN tool access in live context",
            precondition_failed="lean_direct_live",
            suggestion="Use governed signal/artifact flow or restrict the grant scope to paper/backtest",
        )


@app.post(
    "/bff/v1/mcp/servers/{server_id}/import-tools",
    response_model=CommandResponse[McpToolImportData],
)
@app.post(
    "/bff/mcp-servers/{server_id}/import-tools",
    response_model=CommandResponse[McpToolImportData],
)
async def import_mcp_server_tools(
    server_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
):
    """
    Import MCP server-owned tool descriptors.

    This route is the only BFF-local tool registration path. It imports tools
    under a server id, rejects implicit standalone-create semantics, and stores
    imported descriptors for v1 lifecycle action admission.
    """
    clean_server_id = _validate_mcp_server_id(server_id)
    identity = _extract_identity(authorization)
    _require_mcp_tool_write_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    _reject_body_idempotency_key(payload)
    request = _parse_mcp_import_payload(payload)

    request_hash = _stable_json_hash(
        {
            "route": "POST /bff/v1/mcp/servers/{server_id}/import-tools",
            "server_id": clean_server_id,
            "payload": request.model_dump(mode="json", by_alias=True),
        }
    )
    existing = _MCP_IMPORT_IDEMPOTENCY.get(resolved_key)
    if existing is not None:
        return _mcp_import_replay_response(
            existing,
            request_hash,
            conflict_message="Idempotency key was already used with a different MCP tool import",
        )

    approved_flags = _approved_mcp_governance_flags(request.governance)
    imported: List[McpImportedTool] = []
    rejected: List[McpRejectedTool] = []
    seen_tool_ids: set[str] = set()

    for tool in request.tools:
        tool_id = str(tool.tool_id or "").strip()
        if not tool_id:
            rejected.append(
                McpRejectedTool(
                    toolId=None,
                    reason="toolId is required",
                    preconditionFailed="tool_id",
                )
            )
            continue
        if tool_id in seen_tool_ids:
            rejected.append(
                McpRejectedTool(
                    toolId=tool_id,
                    reason="Duplicate toolId in import payload",
                    preconditionFailed="duplicate_tool_id",
                )
            )
            continue
        seen_tool_ids.add(tool_id)
        if not str(tool.name or "").strip():
            rejected.append(
                McpRejectedTool(
                    toolId=tool_id,
                    reason="Tool name is required",
                    preconditionFailed="tool_name",
                )
            )
            continue
        standalone_create_enabled = _mcp_tool_standalone_create_authorized(tool, approved_flags)
        if any(action.allow_standalone_create for action in tool.actions) and not standalone_create_enabled:
            rejected.append(
                McpRejectedTool(
                    toolId=tool_id,
                    reason="Standalone tool create must be explicitly authorized by governance flags",
                    preconditionFailed="standalone_tool_create",
                )
            )
            continue

        registry_key = _mcp_tool_registry_key(clean_server_id, tool_id)
        _MCP_TOOL_REGISTRY[registry_key] = {
            "server_id": clean_server_id,
            "tool_id": tool_id,
            "name": tool.name,
            "tool_class": tool.tool_class.value,
            "descriptor": tool.model_dump(mode="json", by_alias=True),
            "schema_url": tool.schema_url or request.schema_url,
            "status": McpToolLifecycleStatus.IMPORTED.value,
            "standalone_create_enabled": standalone_create_enabled,
            "imported_by": identity.operator_id,
            "imported_at": utc_now(),
        }
        imported.append(
            McpImportedTool(
                toolId=tool_id,
                serverId=clean_server_id,
                name=tool.name,
                toolClass=tool.tool_class,
                status=McpToolLifecycleStatus.IMPORTED,
                schemaUrl=tool.schema_url or request.schema_url,
                actionCount=len(tool.actions),
                standaloneCreateEnabled=standalone_create_enabled,
            )
        )

    data = McpToolImportData(
        importId=f"mcp-import-{uuid.uuid4().hex[:12]}",
        serverId=clean_server_id,
        importedTools=imported,
        rejectedTools=rejected,
        replayed=False,
    )
    response = CommandResponse[McpToolImportData](
        status=ActionCommandStatus.COMPLETED,
        data=data,
        meta={
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
            "requestId": str(x_request_id or "").strip() or None,
            "canonicalWriteAuthority": "mcp_server_import",
            "standaloneToolCreateRoute": None,
        },
    )
    _MCP_IMPORT_IDEMPOTENCY[resolved_key] = {
        "request_hash": request_hash,
        "response": response,
    }
    return response


def _resolve_mcp_server_id_for_tool(
    clean_tool_id: str,
    request: McpToolActionRequest,
) -> str:
    explicit = str(
        request.scope.get("serverId")
        or request.scope.get("server_id")
        or ""
    ).strip()
    if explicit:
        return _validate_mcp_server_id(explicit)
    matches = sorted(
        {
            str(record.get("server_id") or "")
            for record in _MCP_TOOL_REGISTRY.values()
            if str(record.get("tool_id") or "") == clean_tool_id
        }
    )
    matches = [server_id for server_id in matches if server_id]
    if not matches:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "MCP tool is not imported",
            f"tool_id={clean_tool_id} has not been imported under any MCP server",
            precondition_failed="tool_import",
            suggestion="Import the server tool descriptors before admitting tool actions",
        )
    if len(matches) > 1:
        raise _bff_error(
            409,
            ErrorCode.PRECONDITION_NOT_MET,
            "MCP tool id is ambiguous across servers",
            f"tool_id={clean_tool_id} is imported under multiple MCP servers",
            precondition_failed="server_id",
            suggestion="Retry with scope.serverId or use the server-scoped v1 MCP tool action route",
        )
    return matches[0]


@app.post(
    "/bff/mcp-tools/{tool_id}/{action}",
    response_model=CommandResponse[McpToolActionData],
)
async def admit_mcp_tool_action_alias(
    tool_id: str,
    action: McpToolActionVerb,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
):
    """
    Frontend-facing compatibility alias for v1 MCP tool lifecycle actions.

    The server-scoped v1 route remains canonical for unambiguous routing. This
    alias resolves the server from scope.serverId or the imported registry.
    """
    clean_tool_id = str(tool_id or "").strip()
    if not clean_tool_id:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "MCP tool id is required",
            "tool_id path parameter must be a non-empty string",
            precondition_failed="tool_id",
        )
    _reject_body_idempotency_key(payload)
    request = _parse_mcp_tool_action_payload(payload)
    resolved_server_id = _resolve_mcp_server_id_for_tool(clean_tool_id, request)
    return await admit_mcp_tool_action(
        server_id=resolved_server_id,
        tool_id=clean_tool_id,
        action=action,
        payload=payload,
        authorization=authorization,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
        x_request_id=x_request_id,
    )


@app.post(
    "/bff/v1/mcp/servers/{server_id}/tools/{tool_id}/actions/{action}",
    response_model=CommandResponse[McpToolActionData],
)
async def admit_mcp_tool_action(
    server_id: str,
    tool_id: str,
    action: McpToolActionVerb,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
):
    """Admit a v1 lifecycle action against an imported MCP tool."""
    clean_server_id = _validate_mcp_server_id(server_id)
    clean_tool_id = str(tool_id or "").strip()
    if not clean_tool_id:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "MCP tool id is required",
            "tool_id path parameter must be a non-empty string",
            precondition_failed="tool_id",
        )
    identity = _extract_identity(authorization)
    _require_mcp_tool_write_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    _reject_body_idempotency_key(payload)
    request = _parse_mcp_tool_action_payload(payload)

    request_hash = _stable_json_hash(
        {
            "route": "POST /bff/v1/mcp/servers/{server_id}/tools/{tool_id}/actions/{action}",
            "server_id": clean_server_id,
            "tool_id": clean_tool_id,
            "action": action.value,
            "payload": request.model_dump(mode="json", by_alias=True),
        }
    )
    existing = _MCP_TOOL_ACTION_IDEMPOTENCY.get(resolved_key)
    if existing is not None:
        return _mcp_action_replay_response(
            existing,
            request_hash,
            conflict_message="Idempotency key was already used with a different MCP tool action",
        )

    registry_key = _mcp_tool_registry_key(clean_server_id, clean_tool_id)
    tool_record = _MCP_TOOL_REGISTRY.get(registry_key)
    if tool_record is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "MCP tool is not imported for this server",
            f"tool_id={clean_tool_id} has not been imported under server_id={clean_server_id}",
            precondition_failed="tool_import",
            suggestion="Import the server tool descriptors before admitting tool actions",
        )
    _require_mcp_action_admitted(
        tool_record=tool_record,
        action=action,
        request=request,
    )

    next_status = _mcp_tool_action_status(action)
    if not request.dry_run:
        tool_record["status"] = next_status.value
        tool_record["updated_at"] = utc_now()
        tool_record["updated_by"] = identity.operator_id

    data = McpToolActionData(
        toolId=clean_tool_id,
        serverId=clean_server_id,
        action=action,
        status=next_status if not request.dry_run else McpToolLifecycleStatus(tool_record["status"]),
        admitted=True,
        replayed=False,
    )
    response = CommandResponse[McpToolActionData](
        status=ActionCommandStatus.COMPLETED,
        data=data,
        meta={
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
            "requestId": str(x_request_id or "").strip() or None,
            "dryRun": request.dry_run,
            "canonicalWriteAuthority": "mcp_tool_action_admission",
        },
    )
    _MCP_TOOL_ACTION_IDEMPOTENCY[resolved_key] = {
        "request_hash": request_hash,
        "response": response,
    }
    return response


# --------------------------------------------------------------------------- #
# BFF SSE resync surfaces (BFF-FINAL-009)
# --------------------------------------------------------------------------- #

@app.get("/bff/approvals")
async def list_bff_approvals(
    authorization: Optional[str] = Header(default=None),
):
    """
    SSE resync surface: list pending approval-queue items for the approval channel.

    Clients reconnecting to the approval SSE channel must resync canonical state
    from this route and GET /bff/v5/interventions before re-streaming.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    try:
        items = read_store.list_approval_queue_items()
    except Exception:
        items = []
    pending = [
        item for item in items
        if str(item.get("decision_state") or "").lower() in {"pending", "in_review"}
    ]
    return {
        "items": pending,
        "count": len(pending),
        "generated_at": snapshot_at,
    }


# --------------------------------------------------------------------------- #
# BFF Capital, Ranking, and Rebalance Compatibility (BFF-LUV-GAP-003)
# --------------------------------------------------------------------------- #

# In-process idempotency ledger for create/patch operations on these surfaces.
# Maps idempotency_key -> {"request_hash": str, "result": dict}.
_CAPITAL_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}


def _capital_bff_idempotency_check(
    resolved_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    """Return cached result on replay or raise 409 on conflict."""
    existing = _CAPITAL_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")


def _capital_bff_action_command(
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: Any,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    """Submit a resource action through the command store and return the receipt."""
    request_hash = _stable_json_hash({
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "action_id": action_id,
        "payload": payload,
    })
    cached = _capital_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    catalog_entry = get_catalog_entry(command_type.value)
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={"action_id": action_id, **payload},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
    }
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=resolved_key,
        operation_type=f"bff.{command_type.value}",
        target_ref=f"{entity_type.value}:{entity_id}",
        request_payload={
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        },
        trace_id=command_id,
    )
    foundation_ctx = {
        "idempotency_record": idempotency_record.to_dict(),
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    _CAPITAL_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


# -- Capital pools BFF -------------------------------------------------------

@app.get("/bff/capital-pools")
async def bff_list_capital_pools(
    status: Optional[str] = None,
    risk_policy_ref: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: capital pool list (CP-01 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    pools = read_store.list_capital_pools(status=status, risk_policy_ref=risk_policy_ref)
    total = len(pools)
    page_items, next_page_token = _page_slice(pools, page_token, page_size)
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "capital_pools", "capital_pool_list",
            snapshot_at=snapshot_at, total=total,
        ),
    }


@app.post("/bff/capital-pools", status_code=201)
async def bff_create_capital_pool(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create capital pool — Idempotency-Key required."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/capital-pools", "payload": payload})
    cached = _capital_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "Capital pool name must be a non-empty string",
            precondition_failed="name",
        )
    snapshot_at = utc_now()
    pool_id = f"pool-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    result = read_store.create_capital_pool(
        pool_id=pool_id,
        name=name,
        actor_id=identity.operator_id,
        created_at=snapshot_at,
        risk_policy_ref=payload.get("risk_policy_ref"),
        params=payload.get("params") or {},
    )
    _CAPITAL_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/capital-pools/{pool_id}")
async def bff_get_capital_pool(
    pool_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: capital pool detail (CP-02 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    pool_surface = _dataset_surface_status("capital_pools", snapshot_at=snapshot_at)
    pool = read_store.get_capital_pool(pool_id)
    if not pool:
        _raise_if_read_surface_unavailable(pool_surface, label="Capital pool")
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Capital pool not found",
            f"Capital pool {pool_id} does not exist",
        )
    bindings = read_store.get_bindings_for_pool(pool_id)
    binding_surface = _dataset_surface_status("persona_bindings", snapshot_at=snapshot_at)
    data = dict(pool)
    data["bindings"] = bindings
    meta = _read_surface_meta(
        "capital_pools",
        "capital_pool_detail",
        snapshot_at=snapshot_at,
        surface=pool_surface,
    )
    meta.setdefault("surfaces", {})["persona_bindings"] = binding_surface
    binding_reason = _surface_degradation_reason(
        binding_surface,
        degraded_reason="persona bindings are degraded and may be stale.",
        unavailable_reason="persona bindings are currently unavailable.",
    )
    if binding_reason is not None:
        meta.setdefault("degradation", {})["persona_bindings_reason"] = binding_reason
    return {
        "data": data,
        "meta": meta,
    }


@app.patch("/bff/capital-pools/{pool_id}")
async def bff_patch_capital_pool(
    pool_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: patch capital pool — Idempotency-Key required."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash(
        {"route": "PATCH /bff/capital-pools/{pool_id}", "pool_id": pool_id, "payload": payload}
    )
    cached = _capital_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    pool = read_store.get_capital_pool(pool_id)
    if not pool:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Capital pool not found",
            f"Capital pool {pool_id} does not exist",
        )
    snapshot_at = utc_now()
    updated = read_store.patch_capital_pool(
        pool_id,
        patch={k: payload[k] for k in ("name", "status", "risk_policy_ref", "params") if k in payload},
        actor_id=identity.operator_id,
        updated_at=snapshot_at,
    ) or dict(pool)
    result = {"data": updated, "meta": {"snapshot_at": snapshot_at}}
    _CAPITAL_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.post("/bff/capital-pools/{pool_id}/actions/{action_id}", status_code=202)
async def bff_capital_pool_action(
    pool_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: capital pool action — routes through command/precondition machinery."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    pool = read_store.get_capital_pool(pool_id)
    if not pool:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Capital pool not found",
            f"Capital pool {pool_id} does not exist",
        )
    return _capital_bff_action_command(
        entity_type=ObjectType.CAPITAL_POOL,
        entity_id=pool_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.CAPITAL_POOL_ACTION,
    )


# -- Ranking formulas BFF ----------------------------------------------------

@app.get("/bff/ranking/formulas")
async def bff_list_ranking_formulas(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: ranking formula list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = read_store.list_ranking_formulas(status=status)
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "ranking_formulas", "ranking_formula_list",
            snapshot_at=snapshot_at, total=total,
        ),
    }


@app.post("/bff/ranking/formulas", status_code=201)
async def bff_create_ranking_formula(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create ranking formula — Idempotency-Key required."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/ranking/formulas", "payload": payload})
    cached = _capital_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "Ranking formula name must be a non-empty string",
            precondition_failed="name",
        )
    description = str(payload.get("description") or "").strip()
    result = read_store.create_ranking_formula(
        name=name,
        description=description,
        actor_id=identity.operator_id,
        params=payload.get("params"),
    )
    _CAPITAL_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/ranking/formulas/{formula_id}")
async def bff_get_ranking_formula(
    formula_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: ranking formula detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    formula = read_store.get_ranking_formula(formula_id)
    if not formula:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Ranking formula not found",
            f"Ranking formula {formula_id} does not exist",
        )
    return {
        "data": formula,
        "meta": _read_surface_meta(
            "ranking_formulas", "ranking_formula_detail",
            snapshot_at=snapshot_at,
        ),
    }


@app.patch("/bff/ranking/formulas/{formula_id}")
async def bff_patch_ranking_formula(
    formula_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: patch ranking formula — Idempotency-Key required."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash(
        {"route": "PATCH /bff/ranking/formulas/{formula_id}", "formula_id": formula_id, "payload": payload}
    )
    cached = _capital_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    formula = read_store.get_ranking_formula(formula_id)
    if not formula:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Ranking formula not found",
            f"Ranking formula {formula_id} does not exist",
        )
    updated = read_store.patch_ranking_formula(
        formula_id, patch=payload, actor_id=identity.operator_id,
    )
    if not updated:
        raise _bff_error(
            503, ErrorCode.DOWNSTREAM_UNAVAILABLE,
            "Ranking formula store unavailable",
            "Unable to patch ranking formula at this time",
        )
    snapshot_at = utc_now()
    result = {"data": updated, "meta": {"snapshot_at": snapshot_at}}
    _CAPITAL_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.post("/bff/ranking/formulas/{formula_id}/actions/{action_id}", status_code=202)
async def bff_ranking_formula_action(
    formula_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: ranking formula action — routes through command/precondition machinery."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    formula = read_store.get_ranking_formula(formula_id)
    if not formula:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Ranking formula not found",
            f"Ranking formula {formula_id} does not exist",
        )
    return _capital_bff_action_command(
        entity_type=ObjectType.RANKING_FORMULA,
        entity_id=formula_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.RANKING_FORMULA_ACTION,
    )


# -- Rebalances BFF ----------------------------------------------------------

@app.get("/bff/rebalances")
async def bff_list_rebalances(
    status: Optional[str] = None,
    pool_id: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: rebalance list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = read_store.list_rebalances(status=status, pool_id=pool_id)
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "rebalances", "rebalance_list",
            snapshot_at=snapshot_at, total=total,
        ),
    }


@app.post("/bff/rebalances", status_code=202)
async def bff_create_rebalance(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create rebalance command — Idempotency-Key required; produces command/audit metadata."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/rebalances", "payload": payload})
    cached = _capital_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    capital_pool_id = str(payload.get("capital_pool_id") or "").strip()
    if not capital_pool_id:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "capital_pool_id is required",
            "Rebalance must specify a capital_pool_id",
            precondition_failed="capital_pool_id",
        )
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=ObjectType.REBALANCE, id=capital_pool_id)
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
    }
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=resolved_key,
        operation_type=f"bff.{CommandType.REBALANCE_ACTION.value}",
        target_ref=f"{ObjectType.REBALANCE.value}:{capital_pool_id}",
        request_payload=payload,
        trace_id=command_id,
    )
    command_store.submit_command(
        command_id=command_id,
        command_type=CommandType.REBALANCE_ACTION,
        target=target,
        submitted_at=submitted_at,
        params={"capital_pool_id": capital_pool_id, **payload},
        audit_context=audit_record,
        foundation_context={"idempotency_record": idempotency_record.to_dict()},
    )
    rebalance = read_store.create_rebalance(
        capital_pool_id=capital_pool_id,
        actor_id=identity.operator_id,
        created_at=submitted_at,
        params=payload.get("params"),
        reason=payload.get("reason"),
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=CommandType.REBALANCE_ACTION,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    combined = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    combined["rebalance_id"] = rebalance["rebalance_id"]
    _CAPITAL_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": combined}
    return combined


@app.get("/bff/rebalances/{rebalance_id}")
async def bff_get_rebalance(
    rebalance_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: rebalance detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    rebalance = read_store.get_rebalance(rebalance_id)
    if not rebalance:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Rebalance not found",
            f"Rebalance {rebalance_id} does not exist",
        )
    return {
        "data": rebalance,
        "meta": _read_surface_meta(
            "rebalances", "rebalance_detail",
            snapshot_at=snapshot_at,
        ),
    }


@app.post("/bff/rebalances/{rebalance_id}/actions/{action_id}", status_code=202)
async def bff_rebalance_action(
    rebalance_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: rebalance action — routes through command/precondition machinery."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    rebalance = read_store.get_rebalance(rebalance_id)
    if not rebalance:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Rebalance not found",
            f"Rebalance {rebalance_id} does not exist",
        )
    return _capital_bff_action_command(
        entity_type=ObjectType.REBALANCE,
        entity_id=rebalance_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.REBALANCE_ACTION,
    )


# -- Rankings BFF (full-spec long tail) ---------------------------------------

@app.get("/bff/rankings")
async def bff_list_rankings(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: ranking list (full-spec long tail)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = read_store.list_rankings(status=status)
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "rankings", "ranking_list",
            snapshot_at=snapshot_at, total=total,
        ),
    }


@app.get("/bff/rankings/{ranking_id}")
async def bff_get_ranking(
    ranking_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: ranking detail (full-spec long tail)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    ranking = read_store.get_ranking(ranking_id)
    if not ranking:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Ranking not found",
            f"Ranking {ranking_id} does not exist",
        )
    return {
        "data": ranking,
        "meta": _read_surface_meta(
            "rankings", "ranking_detail",
            snapshot_at=snapshot_at,
        ),
    }


@app.post("/bff/rankings/{ranking_id}/actions/{action_id}", status_code=202)
async def bff_ranking_action(
    ranking_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: ranking action (full-spec long tail) — routes through command/precondition machinery."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    ranking = read_store.get_ranking(ranking_id)
    if not ranking:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Ranking not found",
            f"Ranking {ranking_id} does not exist",
        )
    return _capital_bff_action_command(
        entity_type=ObjectType.RANKING,
        entity_id=ranking_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.RANKING_ACTION,
    )


# -- Strategy and Persona BFF (BFF-LUV-GAP-002) -----------------------------
#
# Compatibility surfaces for execute-plans `Strategy` and `Persona` route
# families. These routes project canonical Pantheon read-store data into the
# DTO shape declared in execute-plans/src/lib/bff/types.ts. Action endpoints
# route through the same command/precondition machinery used by capital-pool
# actions (BFF-LUV-GAP-003) so high-risk operations honor the final BFF
# envelope. New persona records created via these routes are persisted through
# the BFF read store until the persona registry service owns the write path.
# Strategy creates still use the legacy in-process compatibility overlay.

_STRATEGY_BFF_LIFECYCLE_MAP = {
    "draft": "draft",
    "candidate": "review",
    "review": "review",
    "approved": "approved",
    "active": "deployed",
    "deployed": "deployed",
    "paused": "paused",
    "retired": "retired",
}

_STRATEGY_BFF_RISK_MAP = {
    "info": "info",
    "low": "low",
    "medium": "medium",
    "moderate": "medium",
    "high": "high",
    "critical": "critical",
}

_STRATEGY_PERSONA_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_STRATEGY_BFF_OVERLAY: Dict[str, Dict[str, Any]] = {}
_PERSONA_BFF_OVERLAY: Dict[str, Dict[str, Any]] = {}


def _strategy_persona_idempotency_check(
    resolved_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    existing = _STRATEGY_PERSONA_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")


def _strategy_persona_action_command(
    *,
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: OperatorIdentity,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    """Submit a strategy / persona resource action through the command store
    and return the final command envelope.

    The /bff/strategies/{id}/actions/{actionId} and /bff/personas/{id}/actions/{actionId}
    endpoints accept action ids declared in the canonical action catalog
    (see action_catalog.py). Idempotency is enforced through the
    `_STRATEGY_PERSONA_BFF_IDEMPOTENCY` ledger so callers receive a stable
    receipt on safe retries.
    """
    request_hash = _stable_json_hash(
        {
            "route": f"POST /bff/{entity_type.value.lower()}/{{id}}/actions",
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        }
    )
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached

    catalog_entry = get_catalog_entry(command_type.value)
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={"action_id": action_id, **payload},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
        "idempotency_key": resolved_key,
        "request_hash": request_hash,
        "catalog_entry": catalog_entry.action_id if catalog_entry else None,
    }
    foundation_ctx = {
        "idempotency_record": {
            "idempotency_key": resolved_key,
            "request_hash": request_hash,
            "operation_type": f"bff.{command_type.value}",
            "target_ref": f"{entity_type.value}:{entity_id}",
            "trace_id": audit_action.trace_id,
        },
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    payload_dump: Dict[str, Any]
    if hasattr(result, "model_dump"):
        payload_dump = result.model_dump(mode="json")
    elif isinstance(result, dict):
        payload_dump = result
    else:
        payload_dump = {"data": result}
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {
        "request_hash": request_hash,
        "result": payload_dump,
    }
    return payload_dump


def _normalize_lifecycle_state(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STRATEGY_BFF_LIFECYCLE_MAP.get(text, "draft")


def _normalize_risk_level(value: Any) -> str:
    text = str(value or "").strip().lower()
    return _STRATEGY_BFF_RISK_MAP.get(text, "medium")


def _project_strategy_dto(
    summary: Dict[str, Any],
    *,
    detail: Optional[Dict[str, Any]] = None,
    overlay: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Project canonical strategy_spec data into execute-plans Strategy DTO."""
    strategy_id = str(summary.get("strategy_id") or summary.get("id") or "")
    title = summary.get("title") or summary.get("name") or strategy_id
    lifecycle_raw = (detail or summary).get("lifecycle_state") or summary.get("lifecycle_state")
    governance = (detail or {}).get("governance") if detail else {}
    governance = governance if isinstance(governance, dict) else {}
    market_scope = (detail or {}).get("market_scope") if detail else {}
    market_scope = market_scope if isinstance(market_scope, dict) else {}
    execution_profile = (detail or {}).get("execution_profile") if detail else {}
    execution_profile = execution_profile if isinstance(execution_profile, dict) else {}
    persona_ids: List[str] = []
    if detail and isinstance(detail.get("persona_ids"), list):
        persona_ids = [str(p) for p in detail.get("persona_ids") or [] if str(p).strip()]
    capital_pool_id = str(
        execution_profile.get("capital_pool_id")
        or governance.get("capital_pool_id")
        or summary.get("capital_pool_id")
        or ""
    )
    alpha = str(
        market_scope.get("alpha")
        or summary.get("source_kind")
        or summary.get("hypothesis_excerpt")
        or ""
    )
    allowed = (detail or {}).get("allowedActions") or {}
    available_actions: List[str] = []
    if isinstance(allowed, dict):
        available_actions = sorted([k for k, v in allowed.items() if v])
    dto: Dict[str, Any] = {
        "id": strategy_id,
        "name": title,
        "owner": summary.get("owner") or governance.get("owner") or "pantheon-bff",
        "updatedAt": summary.get("last_modified_at")
        or summary.get("updated_at")
        or (detail or {}).get("created_at")
        or utc_now(),
        "state": _normalize_lifecycle_state(lifecycle_raw),
        "risk": _normalize_risk_level(governance.get("risk_level")),
        "alpha": alpha,
        "capitalPoolId": capital_pool_id,
        "personaIds": persona_ids,
        "pnl30d": 0.0,
        "sharpe": 0.0,
        "drawdown": 0.0,
        "availableActions": available_actions,
        "labelKey": f"strategy.{strategy_id}" if strategy_id else None,
        "lifecycleStatus": str(lifecycle_raw or ""),
    }
    if overlay:
        for k, v in overlay.items():
            if v is not None:
                dto[k] = v
    return dto


def _project_persona_dto(
    raw: Dict[str, Any],
    *,
    overlay: Optional[Dict[str, Any]] = None,
    routed_strategies: Optional[int] = None,
) -> Dict[str, Any]:
    """Project canonical persona data into execute-plans Persona DTO."""
    persona_id = str(raw.get("persona_id") or raw.get("id") or "")
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    archetype = str(
        metadata.get("archetype")
        or raw.get("strategy_family")
        or raw.get("mandate")
        or "generalist"
    )
    dto: Dict[str, Any] = {
        "id": persona_id,
        "name": raw.get("name") or persona_id,
        "owner": metadata.get("owner") or raw.get("owner") or "pantheon-bff",
        "updatedAt": raw.get("updated_at") or raw.get("created_at") or utc_now(),
        "state": _normalize_lifecycle_state(raw.get("lifecycle_state")),
        "risk": _normalize_risk_level(metadata.get("risk_level")),
        "archetype": archetype,
        "routedStrategies": int(routed_strategies if routed_strategies is not None else 0),
        "successRate": float(metadata.get("success_rate") or 0.0),
        "labelKey": f"persona.{persona_id}" if persona_id else None,
        "lifecycleStatus": str(raw.get("lifecycle_state") or ""),
    }
    if overlay:
        for k, v in overlay.items():
            if v is not None:
                dto[k] = v
    return dto


def _strategy_routed_persona_count(strategy_id: str) -> int:
    detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
    if not detail:
        return 0
    persona_ids = detail.get("persona_ids") or []
    if not isinstance(persona_ids, list):
        return 0
    return len([p for p in persona_ids if str(p).strip()])


def _routed_strategies_for_persona(persona_id: str) -> int:
    items = read_store.list_strategy_specs(persona_id=persona_id) or []
    return len(items)


def _list_strategy_summaries() -> List[Dict[str, Any]]:
    """Combine canonical strategy_specs with overlay records created via /bff."""
    items = list(read_store.list_strategy_specs() or [])
    seen = {str(item.get("strategy_id") or "") for item in items}
    for sid, overlay in _STRATEGY_BFF_OVERLAY.items():
        if sid in seen:
            continue
        items.append({
            "strategy_id": sid,
            "title": overlay.get("name"),
            "lifecycle_state": overlay.get("state") or "draft",
            "last_modified_at": overlay.get("updatedAt"),
            "owner": overlay.get("owner"),
        })
    return items


def _list_persona_records() -> List[Dict[str, Any]]:
    """Combine canonical personas with overlay records created via /bff."""
    items = list(read_store.list_personas() or [])
    seen = {str(item.get("id") or item.get("persona_id") or "") for item in items}
    for pid, overlay in _PERSONA_BFF_OVERLAY.items():
        if pid in seen:
            continue
        items.append({
            "id": pid,
            "persona_id": pid,
            "name": overlay.get("name"),
            "lifecycle_state": overlay.get("state") or "draft",
            "updated_at": overlay.get("updatedAt"),
            "metadata": {
                "archetype": overlay.get("archetype"),
                "owner": overlay.get("owner"),
                "risk_level": overlay.get("risk"),
            },
        })
    return items


# ---------------- /bff/strategies routes ----------------

@app.get("/bff/strategies")
async def bff_list_strategies(
    state: Optional[str] = None,
    persona_id: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: strategy list (execute-plans Strategy DTO compatibility)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    summaries = _list_strategy_summaries()
    if persona_id:
        summaries = [
            s for s in summaries
            if persona_id in (s.get("persona_ids") or [])
            or s.get("strategy_id") in _STRATEGY_BFF_OVERLAY
        ]
    items = []
    for summary in summaries:
        strategy_id = str(summary.get("strategy_id") or "")
        detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
        overlay = _STRATEGY_BFF_OVERLAY.get(strategy_id)
        items.append(_project_strategy_dto(summary, detail=detail, overlay=overlay))
    if state:
        items = [s for s in items if s.get("state") == state]
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "strategy_specs", "strategy_list",
            snapshot_at=snapshot_at, total=total,
        ),
    }


@app.post("/bff/strategies", status_code=201)
async def bff_create_strategy(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create strategy stub (execute-plans compatibility)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/strategies", "payload": payload})
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "Strategy name must be a non-empty string",
            precondition_failed="name",
        )
    snapshot_at = utc_now()
    strategy_id = f"strategy-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    overlay = {
        "id": strategy_id,
        "name": name,
        "owner": str(payload.get("owner") or identity.operator_id),
        "updatedAt": snapshot_at,
        "state": _normalize_lifecycle_state(payload.get("state") or "draft"),
        "risk": _normalize_risk_level(payload.get("risk")),
        "alpha": str(payload.get("alpha") or ""),
        "capitalPoolId": str(payload.get("capitalPoolId") or payload.get("capital_pool_id") or ""),
        "personaIds": list(payload.get("personaIds") or payload.get("persona_ids") or []),
        "pnl30d": float(payload.get("pnl30d") or 0.0),
        "sharpe": float(payload.get("sharpe") or 0.0),
        "drawdown": float(payload.get("drawdown") or 0.0),
        "availableActions": ["edit", "submit", "retire"],
        "labelKey": f"strategy.{strategy_id}",
    }
    _STRATEGY_BFF_OVERLAY[strategy_id] = overlay
    result = {
        "data": overlay,
        "meta": {"snapshot_at": snapshot_at},
    }
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/strategies/{strategy_id}")
async def bff_get_strategy(
    strategy_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: strategy detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    overlay = _STRATEGY_BFF_OVERLAY.get(strategy_id)
    summary = read_store.get_strategy_spec(strategy_id)
    if not summary and not overlay:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Strategy not found",
            f"Strategy {strategy_id} does not exist",
        )
    summary_for_dto = summary or {"strategy_id": strategy_id, "title": (overlay or {}).get("name")}
    detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
    dto = _project_strategy_dto(summary_for_dto, detail=detail, overlay=overlay)
    return {
        "data": dto,
        "meta": _read_surface_meta(
            "strategy_specs", "strategy_detail",
            snapshot_at=snapshot_at,
        ),
    }


@app.patch("/bff/strategies/{strategy_id}")
async def bff_patch_strategy(
    strategy_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: patch strategy overlay fields."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash(
        {"route": "PATCH /bff/strategies/{strategy_id}", "id": strategy_id, "payload": payload}
    )
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    summary = read_store.get_strategy_spec(strategy_id)
    overlay = _STRATEGY_BFF_OVERLAY.get(strategy_id)
    if not summary and not overlay:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Strategy not found",
            f"Strategy {strategy_id} does not exist",
        )
    snapshot_at = utc_now()
    base = dict(overlay) if overlay else {}
    if not base:
        detail = read_store.get_strategy_spec_detail(strategy_id, version_selector="current")
        base = _project_strategy_dto(summary or {"strategy_id": strategy_id}, detail=detail)
    for field in (
        "name", "owner", "state", "risk", "alpha",
        "capitalPoolId", "personaIds", "pnl30d", "sharpe", "drawdown",
        "availableActions",
    ):
        if field in payload:
            base[field] = payload[field]
    if "state" in payload:
        base["state"] = _normalize_lifecycle_state(payload["state"])
    if "risk" in payload:
        base["risk"] = _normalize_risk_level(payload["risk"])
    base["updatedAt"] = snapshot_at
    base["id"] = strategy_id
    _STRATEGY_BFF_OVERLAY[strategy_id] = base
    result = {"data": base, "meta": {"snapshot_at": snapshot_at}}
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


def _ensure_strategy_exists(strategy_id: str) -> None:
    if read_store.get_strategy_spec(strategy_id) or strategy_id in _STRATEGY_BFF_OVERLAY:
        return
    raise _bff_error(
        404, ErrorCode.OBJECT_NOT_FOUND,
        "Strategy not found",
        f"Strategy {strategy_id} does not exist",
    )


@app.get("/bff/strategies/{strategy_id}/specs")
async def bff_list_strategy_specs(
    strategy_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: spec versions for a strategy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_strategy_exists(strategy_id)
    snapshot_at = utc_now()
    versions = read_store.list_strategy_spec_versions(strategy_id) or []
    return {
        "data": versions,
        "items": versions,
        "page_info": {"next_page_token": None, "total": len(versions)},
        "meta": _read_surface_meta(
            "strategy_specs", "strategy_spec_versions",
            snapshot_at=snapshot_at, total=len(versions),
        ),
    }


@app.post("/bff/strategies/{strategy_id}/specs", status_code=201)
async def bff_create_strategy_spec(
    strategy_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create new spec version stub for a strategy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    _ensure_strategy_exists(strategy_id)
    request_hash = _stable_json_hash(
        {"route": "POST /bff/strategies/{id}/specs", "id": strategy_id, "payload": payload}
    )
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    spec_version_id = f"spec-{strategy_id}-{uuid.uuid4().hex[:8]}"
    result = {
        "data": {
            "strategy_id": strategy_id,
            "spec_version_id": spec_version_id,
            "spec_version": str(payload.get("version") or "draft"),
            "lifecycle_state": "draft",
            "created_at": snapshot_at,
            "created_by": identity.operator_id,
            "params": payload.get("params") or {},
        },
        "meta": {"snapshot_at": snapshot_at},
    }
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/strategies/{strategy_id}/experiments")
async def bff_list_strategy_experiments(
    strategy_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: experiments related to a strategy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_strategy_exists(strategy_id)
    snapshot_at = utc_now()
    raw = read_store.list_research_experiments() or []
    items = [e for e in raw if (e.get("linked_strategy_id") or e.get("strategy_id")) == strategy_id]
    return {
        "data": items,
        "items": items,
        "page_info": {"next_page_token": None, "total": len(items)},
        "meta": _read_surface_meta(
            "research_experiments", "strategy_experiments",
            snapshot_at=snapshot_at, total=len(items),
        ),
    }


@app.get("/bff/strategies/{strategy_id}/artifacts")
async def bff_list_strategy_artifacts(
    strategy_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: artifacts produced for a strategy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_strategy_exists(strategy_id)
    snapshot_at = utc_now()
    raw = read_store.list_research_artifacts() or []
    items = [a for a in raw if (a.get("linked_strategy_id") or a.get("strategy_id")) == strategy_id]
    return {
        "data": items,
        "items": items,
        "page_info": {"next_page_token": None, "total": len(items)},
        "meta": _read_surface_meta(
            "research_artifacts", "strategy_artifacts",
            snapshot_at=snapshot_at, total=len(items),
        ),
    }


@app.get("/bff/strategies/{strategy_id}/lineage")
async def bff_get_strategy_lineage(
    strategy_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: lineage subgraph rooted at a strategy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_strategy_exists(strategy_id)
    snapshot_at = utc_now()
    edges = read_store.list_lineage_edges() or []
    nodes_seen: set[str] = set()
    related = []
    for edge in edges:
        node_keys = (
            str(edge.get("from_artifact_id") or edge.get("source_id") or ""),
            str(edge.get("to_artifact_id") or edge.get("target_id") or ""),
            str(edge.get("strategy_id") or ""),
        )
        if strategy_id in node_keys:
            related.append(edge)
            for key in node_keys:
                if key:
                    nodes_seen.add(key)
    nodes_seen.add(strategy_id)
    return {
        "data": {
            "strategy_id": strategy_id,
            "edges": related,
            "node_ids": sorted(nodes_seen),
        },
        "meta": _read_surface_meta(
            "lineage_edges", "strategy_lineage",
            snapshot_at=snapshot_at, total=len(related),
        ),
    }


def _filter_audit_events_by_target(events: List[Dict[str, Any]], target_id: str) -> List[Dict[str, Any]]:
    return [
        event for event in events
        if str(event.get("target_id") or event.get("subject_id") or event.get("entity_id") or "") == target_id
    ]


@app.get("/bff/strategies/{strategy_id}/audit")
async def bff_get_strategy_audit(
    strategy_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: audit trail for a strategy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_strategy_exists(strategy_id)
    snapshot_at = utc_now()
    events = _list_governance_audit_events() or []
    filtered = _filter_audit_events_by_target(events, strategy_id)
    return {
        "data": filtered,
        "items": filtered,
        "page_info": {"next_page_token": None, "total": len(filtered)},
        "meta": _read_surface_meta(
            "governance_audit_events", "strategy_audit",
            snapshot_at=snapshot_at, total=len(filtered),
        ),
    }


def _ooda_packet_routes_enabled() -> bool:
    raw = os.getenv("PANTHEON_OODA_PACKET_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _require_ooda_packet_routes_enabled() -> None:
    if _ooda_packet_routes_enabled():
        return
    raise _bff_error(
        503,
        ErrorCode.DOWNSTREAM_UNAVAILABLE,
        "OODA packet read routes disabled",
        "PANTHEON_OODA_PACKET_ENABLED is disabled for this BFF instance.",
        precondition_failed="ooda_packet_feature_flag",
        suggestion="Re-enable the OODA packet read surface before retrying this route.",
    )


def _ooda_packet_list_payload(
    packets: List[Dict[str, Any]],
    *,
    surface_key: str,
    page_token: Optional[str] = None,
    page_size: int = 20,
    related: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    total = len(packets)
    page_items, next_page_token = _page_slice(packets, page_token, page_size)
    meta = _read_surface_meta(
        "ooda_packets",
        surface_key,
        snapshot_at=snapshot_at,
        total=total,
    )
    if related:
        meta["related"] = related
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": meta,
    }


def _synthesis_conflict_log_routes_enabled() -> bool:
    raw = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _require_synthesis_conflict_log_routes_enabled() -> None:
    if _synthesis_conflict_log_routes_enabled():
        return
    raise _bff_error(
        503,
        ErrorCode.DOWNSTREAM_UNAVAILABLE,
        "Synthesis conflict log view disabled",
        "PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED is disabled for this BFF instance.",
        precondition_failed="synthesis_conflict_log_feature_flag",
        suggestion="Re-enable the synthesis conflict log read surface before retrying this route.",
    )


def _synthesis_conflict_log_id(log: Dict[str, Any]) -> str:
    return str(log.get("log_id") or log.get("id") or log.get("conflict_resolution_log_id") or "").strip()


def _synthesis_conflict_resolution_state(log: Dict[str, Any]) -> str:
    if log.get("rejected_reason"):
        return "rejected"
    if log.get("committee_ref"):
        return "committee_required"
    if log.get("vetoed_proposals"):
        return "resolved_with_veto"
    return "resolved"


def _float_or_none(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _synthesis_conflict_proposal_rows(log: Dict[str, Any]) -> List[Dict[str, Any]]:
    proposal_ids = [str(item) for item in (log.get("proposal_ids") or []) if str(item).strip()]
    weighting_inputs = log.get("weighting_inputs") if isinstance(log.get("weighting_inputs"), dict) else {}
    weighting_outputs = log.get("weighting_outputs") if isinstance(log.get("weighting_outputs"), dict) else {}
    vetoes_by_proposal: Dict[str, Dict[str, Any]] = {}
    for veto in log.get("vetoed_proposals") or []:
        if not isinstance(veto, dict):
            continue
        proposal_id = str(veto.get("proposal_id") or "").strip()
        if proposal_id:
            vetoes_by_proposal[proposal_id] = veto
            if proposal_id not in proposal_ids:
                proposal_ids.append(proposal_id)

    rows: List[Dict[str, Any]] = []
    for proposal_id in proposal_ids:
        veto = vetoes_by_proposal.get(proposal_id)
        input_weight = _float_or_none(weighting_inputs.get(proposal_id))
        output_share = _float_or_none(weighting_outputs.get(proposal_id))
        state = "vetoed" if veto else "not_selected"
        if not veto and output_share is not None and output_share > 0:
            state = "selected"
        row: Dict[str, Any] = {
            "proposal_id": proposal_id,
            "state": state,
            "input_weight": input_weight,
            "output_share": output_share,
            "is_vetoed": bool(veto),
        }
        if veto:
            row["persona_id"] = veto.get("persona_id")
            row["veto_reason"] = veto.get("reason")
            row["veto_detail"] = veto.get("detail")
        rows.append(row)
    return rows


def _synthesis_conflict_log_view_payload(log: Dict[str, Any]) -> Dict[str, Any]:
    raw = json.loads(json.dumps(log))
    log_id = _synthesis_conflict_log_id(raw)
    proposal_rows = _synthesis_conflict_proposal_rows(raw)
    veto_count = sum(1 for row in proposal_rows if row.get("is_vetoed"))
    selected_count = sum(1 for row in proposal_rows if row.get("state") == "selected")
    resolution_state = _synthesis_conflict_resolution_state(raw)
    artifact_id = raw.get("allocation_policy_artifact_id") or raw.get("artifact_id")
    artifact_href = raw.get("allocation_policy_artifact_href") or raw.get("artifact_href")
    governance_approval_id = raw.get("governance_approval_id")
    view = {
        "title": f"Synthesis conflict log {log_id}",
        "resolution_state": resolution_state,
        "summary": {
            "proposal_count": len(proposal_rows),
            "selected_count": selected_count,
            "veto_count": veto_count,
            "committee_required": bool(raw.get("committee_ref")),
            "sponsor_persona_id": raw.get("sponsor_persona_id"),
            "synthesis_method": raw.get("synthesis_method"),
            "capital_pool_id": raw.get("capital_pool_id"),
            "scope_ref": raw.get("scope_ref"),
        },
        "proposal_rows": proposal_rows,
        "governance": {
            "committee_ref": raw.get("committee_ref"),
            "rejected_reason": raw.get("rejected_reason"),
            "approval_id": governance_approval_id,
            "decision": raw.get("governance_decision"),
            "decision_state": raw.get("governance_decision_state"),
            "can_proceed": raw.get("governance_can_proceed"),
        },
        "links": {
            "allocation_policy_artifact": (
                {"id": artifact_id, "href": artifact_href}
                if artifact_id
                else None
            ),
            "governance_approval": (
                {"id": governance_approval_id, "href": f"/bff/approvals/{governance_approval_id}"}
                if governance_approval_id
                else None
            ),
        },
    }
    raw["id"] = log_id
    raw["resolution_state"] = resolution_state
    raw["view"] = view
    return raw


def _synthesis_conflict_log_list_payload(
    logs: List[Dict[str, Any]],
    *,
    surface_key: str,
    page_token: Optional[str] = None,
    page_size: int = 20,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    total = len(logs)
    page_logs, next_page_token = _page_slice(logs, page_token, page_size)
    page_items = [_synthesis_conflict_log_view_payload(log) for log in page_logs]
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "synthesis_conflict_logs",
            surface_key,
            snapshot_at=snapshot_at,
            total=total,
        ),
    }


@app.get("/bff/synthesis/conflict-logs")
async def bff_list_synthesis_conflict_logs(
    capital_pool_id: Optional[str] = None,
    scope_ref: Optional[str] = None,
    proposal_id: Optional[str] = None,
    sponsor_persona_id: Optional[str] = None,
    synthesis_method: Optional[str] = None,
    committee_ref: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list Management-visible multi-persona synthesis conflict logs."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _require_synthesis_conflict_log_routes_enabled()
    logs = read_store.list_synthesis_conflict_logs(
        capital_pool_id=capital_pool_id,
        scope_ref=scope_ref,
        proposal_id=proposal_id,
        sponsor_persona_id=sponsor_persona_id,
        synthesis_method=synthesis_method,
        committee_ref=committee_ref,
    )
    return _synthesis_conflict_log_list_payload(
        logs,
        surface_key="synthesis_conflict_logs",
        page_token=page_token,
        page_size=page_size,
    )


@app.get("/bff/synthesis/conflict-logs/{log_id}")
async def bff_get_synthesis_conflict_log(
    log_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get one Management-visible synthesis conflict log view."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _require_synthesis_conflict_log_routes_enabled()
    clean_id = log_id.strip()
    log_record = read_store.get_synthesis_conflict_log(clean_id)
    source = read_store.dataset_source("synthesis_conflict_logs")
    if not log_record:
        if source == "missing":
            return _sem_final_degraded_detail(
                entity_id=clean_id,
                label="Synthesis conflict log",
                dataset="synthesis_conflict_logs",
                surface_key="synthesis_conflict_log_detail",
                source="missing",
            )
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Synthesis conflict log not found",
            f"Synthesis conflict log {log_id} does not exist",
        )
    snapshot_at = utc_now()
    return {
        "data": _synthesis_conflict_log_view_payload(log_record),
        "meta": _read_surface_meta(
            "synthesis_conflict_logs",
            "synthesis_conflict_log_detail",
            snapshot_at=snapshot_at,
        ),
    }


@app.get("/bff/ooda/packets")
async def bff_list_ooda_packets(
    status: Optional[str] = None,
    stage: Optional[str] = None,
    strategy_id: Optional[str] = None,
    runtime_id: Optional[str] = None,
    evolution_program_id: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list replayable Management OODA packets."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _require_ooda_packet_routes_enabled()
    packets = read_store.list_ooda_packets(
        status=status,
        stage=stage,
        strategy_id=strategy_id,
        runtime_id=runtime_id,
        evolution_program_id=evolution_program_id,
    )
    return _ooda_packet_list_payload(
        packets,
        surface_key="ooda_packets",
        page_token=page_token,
        page_size=page_size,
    )


@app.get("/bff/ooda/packets/{packet_id}")
async def bff_get_ooda_packet(
    packet_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a single replayable Management OODA packet."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _require_ooda_packet_routes_enabled()
    clean_id = packet_id.strip()
    packet = read_store.get_ooda_packet(clean_id)
    source = read_store.dataset_source("ooda_packets")
    if not packet:
        if source == "missing":
            return _sem_final_degraded_detail(
                entity_id=clean_id,
                label="OODA packet",
                dataset="ooda_packets",
                surface_key="ooda_packet_detail",
                source="missing",
            )
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "OODA packet not found",
            f"OODA packet {packet_id} does not exist",
        )
    snapshot_at = utc_now()
    return {
        "data": packet,
        "meta": _read_surface_meta(
            "ooda_packets",
            "ooda_packet_detail",
            snapshot_at=snapshot_at,
        ),
    }


@app.get("/bff/strategies/{strategy_id}/ooda")
async def bff_list_strategy_ooda_packets(
    strategy_id: str,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list OODA packets linked to a strategy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _require_ooda_packet_routes_enabled()
    clean_id = strategy_id.strip()
    packets = read_store.list_ooda_packets_for_strategy(clean_id)
    return _ooda_packet_list_payload(
        packets,
        surface_key="strategy_ooda_packets",
        page_token=page_token,
        page_size=page_size,
        related={"type": "Strategy", "id": clean_id},
    )


@app.get("/bff/runtimes/{runtime_id}/ooda")
async def bff_list_runtime_ooda_packets(
    runtime_id: str,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list OODA packets linked to a runtime or RuntimeBinding."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _require_ooda_packet_routes_enabled()
    clean_id = runtime_id.strip()
    packets = read_store.list_ooda_packets_for_runtime(clean_id)
    return _ooda_packet_list_payload(
        packets,
        surface_key="runtime_ooda_packets",
        page_token=page_token,
        page_size=page_size,
        related={"type": "Runtime", "id": clean_id},
    )


@app.get("/bff/evolution-programs/{program_id}/ooda")
async def bff_list_evolution_program_ooda_packets(
    program_id: str,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list OODA packets linked to an evolution program."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _require_ooda_packet_routes_enabled()
    clean_id = program_id.strip()
    packets = read_store.list_ooda_packets_for_evolution_program(clean_id)
    return _ooda_packet_list_payload(
        packets,
        surface_key="evolution_program_ooda_packets",
        page_token=page_token,
        page_size=page_size,
        related={"type": "EvolutionProgram", "id": clean_id},
    )


@app.post("/bff/strategies/{strategy_id}/actions/{action_id}", status_code=202)
async def bff_strategy_action(
    strategy_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: strategy action — routes through command/precondition machinery."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    _ensure_strategy_exists(strategy_id)
    return _strategy_persona_action_command(
        entity_type=ObjectType.STRATEGY,
        entity_id=strategy_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.STRATEGY_ACTION,
    )


@app.post("/bff/strategies/{strategy_id}/dry-run", status_code=202)
async def bff_strategy_dry_run(
    strategy_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: launch a strategy dry-run; returns a stub run handle."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    _ensure_strategy_exists(strategy_id)
    request_hash = _stable_json_hash(
        {"route": "POST /bff/strategies/{id}/dry-run", "id": strategy_id, "payload": payload}
    )
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    run_id = f"dryrun-{strategy_id}-{uuid.uuid4().hex[:8]}"
    result = {
        "data": {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "status": "queued",
            "started_at": snapshot_at,
            "params": payload.get("params") or payload,
            "requested_by": identity.operator_id,
        },
        "meta": {"snapshot_at": snapshot_at},
    }
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


# ---------------- /bff/personas routes ----------------

@app.get("/bff/personas")
async def bff_list_personas(
    state: Optional[str] = None,
    archetype: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: persona list (execute-plans Persona DTO compatibility)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    raw_personas = _list_persona_records()
    items = []
    for raw in raw_personas:
        persona_id = str(raw.get("persona_id") or raw.get("id") or "")
        overlay = _PERSONA_BFF_OVERLAY.get(persona_id)
        routed = _routed_strategies_for_persona(persona_id)
        dto = _project_persona_dto(raw, overlay=overlay, routed_strategies=routed)
        items.append(dto)
    if state:
        items = [p for p in items if p.get("state") == state]
    if archetype:
        items = [p for p in items if p.get("archetype") == archetype]
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "personas", "persona_list",
            snapshot_at=snapshot_at, total=total,
        ),
    }


@app.post("/bff/personas", status_code=201)
async def bff_create_persona(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create persona stub (execute-plans compatibility)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/personas", "payload": payload})
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "Persona name must be a non-empty string",
            precondition_failed="name",
        )
    snapshot_at = utc_now()
    persona_id = f"persona-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    owner = str(payload.get("owner") or identity.operator_id)
    archetype = str(payload.get("archetype") or "generalist")
    risk = _normalize_risk_level(payload.get("risk") or "low")
    lifecycle_state = _normalize_lifecycle_state(
        payload.get("state") or payload.get("lifecycleStatus") or "draft"
    )
    persona_record = read_store.create_persona(
        persona_id=persona_id,
        name=name,
        actor_id=owner,
        created_at=snapshot_at,
        archetype=archetype,
        lifecycle_state=lifecycle_state,
        risk_level=risk,
        metadata={
            "description": payload.get("description"),
            "memo": payload.get("memo"),
            "initial_mode": payload.get("initialMode"),
            "execution_mode": payload.get("executionMode") or payload.get("initialMode"),
            "success_rate": float(payload.get("successRate") or 0.0),
        },
    )
    overlay = _project_persona_dto(
        persona_record,
        overlay={
            "routedStrategies": int(payload.get("routedStrategies") or 0),
            "successRate": float(payload.get("successRate") or 0.0),
        },
        routed_strategies=0,
    )
    _PERSONA_BFF_OVERLAY[persona_id] = overlay
    result = {
        "data": overlay,
        "meta": {"snapshot_at": snapshot_at},
    }
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/personas/{persona_id}")
async def bff_get_persona(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: persona detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    overlay = _PERSONA_BFF_OVERLAY.get(persona_id)
    raw = read_store.get_persona(persona_id)
    if not raw and not overlay:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )
    base = raw or {"persona_id": persona_id, "name": (overlay or {}).get("name")}
    routed = _routed_strategies_for_persona(persona_id)
    dto = _project_persona_dto(base, overlay=overlay, routed_strategies=routed)
    return {
        "data": dto,
        "meta": _read_surface_meta(
            "personas", "persona_detail",
            snapshot_at=snapshot_at,
        ),
    }


@app.patch("/bff/personas/{persona_id}")
async def bff_patch_persona(
    persona_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: patch persona fields through the BFF read store."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash(
        {"route": "PATCH /bff/personas/{persona_id}", "id": persona_id, "payload": payload}
    )
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    raw = read_store.get_persona(persona_id)
    overlay = _PERSONA_BFF_OVERLAY.get(persona_id)
    if not raw and not overlay:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Persona not found",
            f"Persona {persona_id} does not exist",
        )
    snapshot_at = utc_now()
    base = dict(overlay) if overlay else {}
    if not base:
        routed = _routed_strategies_for_persona(persona_id)
        base = _project_persona_dto(raw or {"persona_id": persona_id}, routed_strategies=routed)
    for field in (
        "name", "owner", "state", "risk",
        "archetype", "routedStrategies", "successRate",
        "availableActions",
    ):
        if field in payload:
            base[field] = payload[field]
    if "state" in payload:
        base["state"] = _normalize_lifecycle_state(payload["state"])
    if "risk" in payload:
        base["risk"] = _normalize_risk_level(payload["risk"])
    base["updatedAt"] = snapshot_at
    base["id"] = persona_id
    persona_record = read_store.update_persona(
        persona_id,
        name=str(base.get("name") or persona_id),
        actor_id=str(base.get("owner") or identity.operator_id),
        updated_at=snapshot_at,
        archetype=str(base.get("archetype") or "generalist"),
        lifecycle_state=str(base.get("state") or "draft"),
        risk_level=str(base.get("risk") or "low"),
        metadata={
            "success_rate": float(base.get("successRate") or 0.0),
        },
    )
    if persona_record is not None:
        routed = _routed_strategies_for_persona(persona_id)
        base = _project_persona_dto(
            persona_record,
            overlay={
                "routedStrategies": int(base.get("routedStrategies") or routed),
                "successRate": float(base.get("successRate") or 0.0),
            },
            routed_strategies=routed,
        )
    _PERSONA_BFF_OVERLAY[persona_id] = base
    result = {"data": base, "meta": {"snapshot_at": snapshot_at}}
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


def _ensure_persona_exists(persona_id: str) -> None:
    if read_store.get_persona(persona_id) or persona_id in _PERSONA_BFF_OVERLAY:
        return
    raise _bff_error(
        404, ErrorCode.OBJECT_NOT_FOUND,
        "Persona not found",
        f"Persona {persona_id} does not exist",
    )


@app.get("/bff/personas/{persona_id}/route-policy")
async def bff_get_persona_route_policy(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: persona route policy."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_persona_exists(persona_id)
    snapshot_at = utc_now()
    policy = None
    fetcher = getattr(read_store, "get_route_policy_for_persona", None)
    if callable(fetcher):
        policy = fetcher(persona_id)
    if not policy:
        consult_policy = None
        consult_fetcher = getattr(read_store, "get_persona_consult_policy", None)
        if callable(consult_fetcher):
            consult_policy = consult_fetcher(persona_id)
        policy = {
            "personaId": persona_id,
            "version": "v1",
            "rules": [],
            "consult_policy": consult_policy,
        }
    return {
        "data": policy,
        "meta": _read_surface_meta(
            "personas", "persona_route_policy",
            snapshot_at=snapshot_at,
        ),
    }


@app.get("/bff/personas/{persona_id}/activity")
async def bff_get_persona_activity(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: persona activity (sessions + recent consultations)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_persona_exists(persona_id)
    snapshot_at = utc_now()
    sessions = read_store.get_sessions_for_persona(persona_id) or []
    consultations: List[Dict[str, Any]] = []
    consult_fetcher = getattr(read_store, "list_consultations_for_persona", None)
    if callable(consult_fetcher):
        consultations = consult_fetcher(persona_id) or []
    activity = {
        "personaId": persona_id,
        "sessions": sessions,
        "consultations": consultations,
    }
    return {
        "data": activity,
        "meta": _read_surface_meta(
            "personas", "persona_activity",
            snapshot_at=snapshot_at, total=len(sessions) + len(consultations),
        ),
    }


@app.get("/bff/personas/{persona_id}/evaluations")
async def bff_get_persona_evaluations(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: persona evaluations (teaching sessions stand in until evaluation runs ship)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_persona_exists(persona_id)
    snapshot_at = utc_now()
    teaching = read_store.get_teaching_sessions_for_persona(persona_id) or []
    return {
        "data": teaching,
        "items": teaching,
        "page_info": {"next_page_token": None, "total": len(teaching)},
        "meta": _read_surface_meta(
            "personas", "persona_evaluations",
            snapshot_at=snapshot_at, total=len(teaching),
        ),
    }


@app.get("/bff/personas/{persona_id}/memory")
async def bff_get_persona_memory(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: persona memory updates."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_persona_exists(persona_id)
    snapshot_at = utc_now()
    memory: List[Dict[str, Any]] = []
    fetcher = getattr(read_store, "list_memory_updates_for_persona", None)
    if callable(fetcher):
        memory = fetcher(persona_id) or []
    return {
        "data": memory,
        "items": memory,
        "page_info": {"next_page_token": None, "total": len(memory)},
        "meta": _read_surface_meta(
            "personas", "persona_memory",
            snapshot_at=snapshot_at, total=len(memory),
        ),
    }


@app.get("/bff/personas/{persona_id}/audit")
async def bff_get_persona_audit(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: audit trail for a persona."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_persona_exists(persona_id)
    snapshot_at = utc_now()
    events = _list_governance_audit_events() or []
    filtered = _filter_audit_events_by_target(events, persona_id)
    return {
        "data": filtered,
        "items": filtered,
        "page_info": {"next_page_token": None, "total": len(filtered)},
        "meta": _read_surface_meta(
            "governance_audit_events", "persona_audit",
            snapshot_at=snapshot_at, total=len(filtered),
        ),
    }


@app.get("/bff/personas/{persona_id}/skills")
async def bff_get_persona_skills(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: skills accessible to a persona, derived from capability snapshot (PER-002)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_persona_exists(persona_id)
    snapshot_at = utc_now()
    snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
    effective_skill_ids = list((snapshot or {}).get("effective_skills") or [])
    all_skills = _merged_skill_records()
    skill_by_id: Dict[str, Dict[str, Any]] = {
        str(s.get("skill_id") or s.get("id") or ""): s
        for s in all_skills
        if s.get("skill_id") or s.get("id")
    }
    items: List[Dict[str, Any]] = []
    for sid in effective_skill_ids:
        sid = str(sid)
        record = skill_by_id.get(sid)
        if record:
            items.append(dict(record))
        else:
            items.append({"skill_id": sid, "id": sid, "name": sid, "status": "active"})
    return {
        "data": items,
        "items": items,
        "page_info": {"next_page_token": None, "total": len(items)},
        "meta": _read_surface_meta(
            "capability_snapshots", "persona_skills",
            snapshot_at=snapshot_at, total=len(items),
        ),
    }


@app.get("/bff/personas/{persona_id}/tools")
async def bff_get_persona_tools(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: tools accessible to a persona, derived from capability snapshot (PER-002)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_persona_exists(persona_id)
    snapshot_at = utc_now()
    snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
    effective_tool_ids = list((snapshot or {}).get("effective_tools") or [])
    all_tools = _merged_tool_records()
    tool_by_id: Dict[str, Dict[str, Any]] = {
        str(t.get("tool_id") or t.get("id") or ""): t
        for t in all_tools
        if t.get("tool_id") or t.get("id")
    }
    items: List[Dict[str, Any]] = []
    for tid in effective_tool_ids:
        tid = str(tid)
        record = tool_by_id.get(tid)
        if record:
            items.append(dict(record))
        else:
            items.append({"tool_id": tid, "id": tid, "name": tid, "status": "active"})
    return {
        "data": items,
        "items": items,
        "page_info": {"next_page_token": None, "total": len(items)},
        "meta": _read_surface_meta(
            "capability_snapshots", "persona_tools",
            snapshot_at=snapshot_at, total=len(items),
        ),
    }


@app.get("/bff/personas/{persona_id}/capabilities")
async def bff_get_persona_capabilities_surface(
    persona_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: capability snapshot surface for a persona (PER-002 read surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _ensure_persona_exists(persona_id)
    snapshot_at = utc_now()
    snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
    data: Dict[str, Any] = {
        "personaId": persona_id,
        "effectiveSkills": (snapshot or {}).get("effective_skills") or [],
        "effectiveTools": (snapshot or {}).get("effective_tools") or [],
        "effectiveWorkflows": (snapshot or {}).get("effective_workflows") or [],
        "restrictions": (snapshot or {}).get("restrictions") or [],
        "generatedAt": (snapshot or {}).get("generated_at"),
        "sourceRefs": (snapshot or {}).get("source_refs") or [],
        "snapshotId": (snapshot or {}).get("snapshot_id"),
    }
    return {
        "data": data,
        "meta": _read_surface_meta(
            "capability_snapshots", "persona_capabilities",
            snapshot_at=snapshot_at,
        ),
    }


@app.post("/bff/personas/{persona_id}/actions/{action_id}", status_code=202)
async def bff_persona_action(
    persona_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: persona action — routes through command/precondition machinery."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    _ensure_persona_exists(persona_id)
    return _strategy_persona_action_command(
        entity_type=ObjectType.PERSONA,
        entity_id=persona_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.PERSONA_ACTION,
    )


@app.post("/bff/personas/{persona_id}/test-prompt", status_code=202)
async def bff_persona_test_prompt(
    persona_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: send a test prompt to a persona; returns a stub trial handle."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    _ensure_persona_exists(persona_id)
    request_hash = _stable_json_hash(
        {"route": "POST /bff/personas/{id}/test-prompt", "id": persona_id, "payload": payload}
    )
    cached = _strategy_persona_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "prompt is required",
            "Persona test-prompt requires a non-empty prompt",
            precondition_failed="prompt",
        )
    snapshot_at = utc_now()
    trial_id = f"prompt-{persona_id}-{uuid.uuid4().hex[:8]}"
    result = {
        "data": {
            "trial_id": trial_id,
            "persona_id": persona_id,
            "status": "queued",
            "queued_at": snapshot_at,
            "prompt": prompt,
            "params": payload.get("params") or {},
            "requested_by": identity.operator_id,
        },
        "meta": {"snapshot_at": snapshot_at},
    }
    _STRATEGY_PERSONA_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


# ---------------- Platform helpers ----------------

@app.get("/bff/search")
async def bff_search(
    q: str = Query(default=""),
    types: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: cross-entity search across strategies, personas, and capital pools."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    needle = q.strip().lower()
    requested_types: Optional[set[str]] = None
    if types:
        requested_types = {t.strip().lower() for t in types.split(",") if t.strip()}

    results: List[Dict[str, Any]] = []

    def _match(text: Any) -> bool:
        if not needle:
            return True
        return needle in str(text or "").lower()

    if not requested_types or "strategy" in requested_types:
        for summary in _list_strategy_summaries():
            strategy_id = str(summary.get("strategy_id") or "")
            name = summary.get("title") or strategy_id
            if _match(strategy_id) or _match(name):
                results.append({
                    "id": strategy_id,
                    "type": "strategy",
                    "name": str(name),
                    "state": _normalize_lifecycle_state(summary.get("lifecycle_state")),
                    "owner": str(summary.get("owner") or "pantheon-bff"),
                    "risk": "medium",
                    "updatedAt": summary.get("last_modified_at") or snapshot_at,
                })

    if not requested_types or "persona" in requested_types:
        for raw in _list_persona_records():
            persona_id = str(raw.get("persona_id") or raw.get("id") or "")
            name = raw.get("name") or persona_id
            if _match(persona_id) or _match(name):
                metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
                results.append({
                    "id": persona_id,
                    "type": "persona",
                    "name": str(name),
                    "state": _normalize_lifecycle_state(raw.get("lifecycle_state")),
                    "owner": str(metadata.get("owner") or raw.get("owner") or "pantheon-bff"),
                    "risk": _normalize_risk_level(metadata.get("risk_level")),
                    "updatedAt": raw.get("updated_at") or raw.get("created_at") or snapshot_at,
                })

    if not requested_types or "capital_pool" in requested_types or "capitalPool" in requested_types:
        for pool in (read_store.list_capital_pools() or []):
            pool_id = str(pool.get("id") or pool.get("pool_id") or "")
            name = pool.get("name") or pool_id
            if _match(pool_id) or _match(name):
                results.append({
                    "id": pool_id,
                    "type": "capital_pool",
                    "name": str(name),
                    "state": _normalize_lifecycle_state(pool.get("status")),
                    "owner": str(pool.get("owner") or "pantheon-bff"),
                    "risk": _normalize_risk_level(pool.get("risk_level")),
                    "updatedAt": pool.get("updated_at") or pool.get("created_at") or snapshot_at,
                })

    capped = results[:limit]
    return {
        "data": capped,
        "items": capped,
        "page_info": {"next_page_token": None, "total": len(results), "returned": len(capped)},
        "meta": _read_surface_meta(
            "personas", "search",
            snapshot_at=snapshot_at, total=len(capped),
        ),
    }


@app.get("/bff/types")
async def bff_types_compat(
    authorization: Optional[str] = Header(default=None),
):
    """
    Source-reference compatibility decision for /bff/types.

    The execute-plans repo declares its DTO universe in
    `src/lib/bff/types.ts`; the Pantheon BFF mirrors that shape for the
    surfaces it serves. This endpoint returns the canonical compatibility
    map so frontend tooling can validate that a Pantheon deployment
    advertises the expected entity DTOs without scraping route inventories.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return {
        "data": {
            "types_source": "execute-plans/src/lib/bff/types.ts",
            "exported_entities": [
                "Strategy", "Persona", "CapitalPool", "RankingFormula",
                "Rebalance", "Deployment", "Runtime", "EvolutionProgram",
                "ResearchExperiment", "Artifact", "Job", "Alert", "Incident",
                "ApprovalRequest", "AuditEvent", "SearchResult",
                "Tool", "McpServer", "McpTool", "Skill", "Channel",
                "RoutePolicy", "PolicyVersion", "PermissionMatrix",
                "MemoryUpdate", "EvolutionRun", "EvolutionCandidate",
                "FitnessFormula", "MutationRule", "AllocationSimulation",
                "PolicyViolation", "EvaluationRun", "ObjectVersion",
                "FeatureSet", "PerformanceSeries", "Watcher",
                "DecisionJournalEntry", "AllocationLimit", "PoolFreeze",
                "DeploymentStage", "McpSecret", "PromotionRecord",
                "MetricFreeze", "RebalanceOverride",
            ],
            "served_by": "pantheon-bff",
            "compatibility_decision": "execute-plans/src/lib/bff/types.ts is the canonical TypeScript declaration; the Pantheon BFF projects to it for /bff/* surfaces.",
        },
        "meta": {"snapshot_at": utc_now()},
    }


_V5_INTERVENTIONS_STORE: List[Dict[str, Any]] = []


def _v5_intervention_records(
    *,
    status: Optional[str] = None,
    kind: Optional[str] = None,
) -> List[Dict[str, Any]]:
    records_by_id: Dict[str, Dict[str, Any]] = {}
    store_lister = getattr(read_store, "list_v5_interventions", None)
    if callable(store_lister):
        for record in store_lister(status=status, kind=kind):
            if not isinstance(record, dict):
                continue
            record_id = str(record.get("intervention_id") or record.get("id") or "").strip()
            if record_id:
                records_by_id[record_id] = dict(record)

    for record in _V5_INTERVENTIONS_STORE:
        if not isinstance(record, dict):
            continue
        if status and str(record.get("status") or "") != status:
            continue
        if kind and str(record.get("kind") or "") != kind:
            continue
        record_id = str(record.get("intervention_id") or record.get("id") or "").strip()
        if record_id:
            records_by_id[record_id] = dict(record)

    return list(records_by_id.values())


@app.get("/bff/v5/interventions", response_model=InterventionListResponse)
async def list_v5_interventions(
    status: Optional[str] = Query(default=None, description="Filter by status: pending, remediated, dismissed, escalated"),
    kind: Optional[str] = Query(default=None, description="Filter by kind: hiq_sentinel, risk_breach, strategy_drift, loop_anomaly"),
    authorization: Optional[str] = Header(default=None),
):
    """
    HIQ Sentinel v5 interventions list.

    Returns pending and recent sentinel intervention records.  Clients
    reconnecting to the approval SSE channel should resync from this
    route alongside GET /bff/approvals.

    Two-man authorization is required to remediate any record returned here.
    Submit remediation via POST /bff/v5/interventions/{id}/remediate or via
    POST /bff/v1/commands with command RemediateSentinelIntervention.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()

    items = _v5_intervention_records(status=status, kind=kind)

    records = [InterventionRecord(**i) for i in items]
    return InterventionListResponse(
        items=records,
        count=len(records),
        generated_at=snapshot_at,
    )


@app.post("/bff/v5/interventions/{intervention_id}/remediate", status_code=202)
async def remediate_v5_intervention(
    intervention_id: str,
    background_tasks: BackgroundTasks,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
    x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """
    Submit a HIQ Sentinel remediation command for an intervention.

    This is a two-man guarded surface: the request must carry a second-operator
    signature (twoManSignatureId / secondOperatorId) or the command is rejected
    with TWO_MAN_REQUIRED (HTTP 409).  Approval evidence and a confirm token are
    also required because the risk level is CRITICAL.

    Internally this builds a RemediateSentinelIntervention OperatorCommand and
    routes it through the same admission, idempotency, and audit pipeline as all
    other governed commands.
    """
    identity = _extract_identity(authorization, mfa_token=x_mfa_token)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)

    merged_params = dict(payload)
    merged_params["intervention_id"] = intervention_id

    cmd = OperatorCommand(
        command=CommandType.REMEDIATE_SENTINEL_INTERVENTION,
        target=TargetObject(
            type=ObjectType.SENTINEL_INTERVENTION,
            id=intervention_id,
        ),
        action="remediate_sentinel_intervention",
        params=merged_params,
        audit_context=AuditContext(
            reason=str(payload.get("reason") or "HIQ Sentinel remediation"),
            incident_id=str(payload.get("incident_id") or "").strip() or None,
        ),
    )

    foundation_context = _build_foundation_command_context(
        cmd=cmd,
        identity=identity,
        raw_payload={**payload, "intervention_id": intervention_id},
        trace_id=x_trace_id,
        correlation_id=x_correlation_id,
        request_id=x_request_id,
        idempotency_key=resolved_key,
    )

    try:
        _reject_body_idempotency_key(payload)
        _validate_audit_context(cmd)
        _validate_remediate_sentinel_intervention(merged_params, identity)
        _require_final_command_preconditions(
            cmd=cmd,
            payload={**payload, "intervention_id": intervention_id},
            confirm_token=x_confirm_token,
            correlation_id=foundation_context["trace_context"].correlation_id,
        )
    except HTTPException as exc:
        raise _foundation_bff_error(exc, foundation_context=foundation_context) from exc

    stored_params = _stored_command_params(cmd, identity)

    duplicate = command_store.get_command_by_idempotency_key(
        foundation_context["idempotency_record"].idempotency_key
    )
    if duplicate:
        duplicate_record = (duplicate.get("foundation") or {}).get("idempotency_record") or {}
        if duplicate_record.get("request_hash") != foundation_context["idempotency_record"].request_hash:
            raise _foundation_idempotency_conflict_error(
                foundation_context=foundation_context,
                existing_command_id=str(duplicate.get("command_id") or ""),
            )
        return _project_final_command_response(
            command_id=duplicate["command_id"],
            command=cmd.command,
            accepted_at=duplicate.get("submitted_at") or utc_now(),
            status=CommandStatus(duplicate.get("status") or CommandStatus.SUBMITTED.value),
            staleness_warning=None,
        )

    active = command_store.get_active_commands_for_target(cmd.target.type.value, cmd.target.id)
    if active:
        error = _bff_error(
            409, ErrorCode.CONCURRENT_MODIFICATION,
            "A remediation command is already in flight for this intervention",
            f"Command {active[0]['command_id']} is currently {active[0]['status']}",
            precondition_failed="concurrent_safety",
            suggestion="Wait for the in-flight command to complete before retrying",
        )
        raise _foundation_bff_error(error, foundation_context=foundation_context)

    staleness_warning = _check_read_surface_state()

    command_envelope = foundation_context["command_envelope"]
    idempotency_record = foundation_context["idempotency_record"]
    idempotency_record = idempotency_record.with_status(
        "succeeded",
        result_ref=f"command:{command_envelope.command_id}",
    )
    foundation_context["idempotency_record"] = idempotency_record
    command_id = command_envelope.command_id
    submitted_at = utc_now()

    raw_token = None
    if authorization and authorization.startswith("Bearer "):
        raw_token = authorization[len("Bearer "):]
    mfa_token_val = x_mfa_token or ("000000" if identity.mfa_verified else None)

    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "mfa_verified": identity.mfa_verified,
        "reason": cmd.audit_context.reason,
        "incident_id": cmd.audit_context.incident_id,
        "preconditions_checked": [
            "authentication", "authorization", "two_man", "params_shape", "concurrent_safety"
        ],
        "timestamp": submitted_at,
        "staleness_warning": staleness_warning.model_dump() if staleness_warning else None,
        "auth_token": raw_token,
        "mfa_token": mfa_token_val,
        "foundation": _serialize_foundation_context(foundation_context),
    }

    command_store.submit_command(
        command_id=command_id,
        command_type=cmd.command,
        target=cmd.target,
        submitted_at=submitted_at,
        params=stored_params,
        audit_context=audit_record,
        foundation_context=_serialize_foundation_context(foundation_context),
    )
    background_tasks.add_task(_process_command_stub, command_id)

    return _project_final_command_response(
        command_id=command_id,
        command=cmd.command,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )


@app.get("/api/v1/operator/commands/{command_id}", response_model=CommandStatusResponse)
async def get_command_status(command_id: str, authorization: Optional[str] = Header(default=None)):
    """
    Poll for the status of a previously submitted command.
    """
    # Auth required to read command status (prevents polling by unauthenticated callers)
    _extract_identity(authorization)

    record = command_store.get_command(command_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Command {command_id} not found")

    return CommandStatusResponse(
        command_id=record["command_id"],
        type=record["type"],
        target=record["target"],
        submitted_at=record["submitted_at"],
        status=record["status"],
        result=record.get("result"),
        error=record.get("error"),
        audit=record.get("audit"),
    )


# --------------------------------------------------------------------------- #
# Background worker — real execution path
# --------------------------------------------------------------------------- #

async def _process_command(command_id: str):
    """
    Async command processor that dispatches to the Protected Internal API.
    Records authoritative status, result, and audit data for every execution.
    """
    import asyncio

    record = command_store.get_command(command_id)
    if not record:
        log.error("Worker: command %s not found in store", command_id)
        return

    command_type = CommandType(record["type"])
    params = record.get("params", {})
    audit = record.get("audit", {})

    # Extract auth tokens from submission audit context for downstream calls
    auth_token = audit.get("auth_token")
    mfa_token = audit.get("mfa_token")

    # Mark processing
    await asyncio.sleep(0.05)  # brief yield to event loop
    command_store.update_status(command_id, CommandStatus.PROCESSING)

    try:
        execution_params = _resolve_execution_params_for_record(record)
    except Exception as exc:
        failed_at = utc_now()
        error = {
            "code": "TARGET_CONTEXT_UNAVAILABLE",
            "message": f"Unable to route command {command_id}: {exc}",
            "started_at": failed_at,
            "failed_at": failed_at,
            "suggestion": (
                "Refresh Pantheon runtime/incident read surfaces or use the secondary control path "
                "until the runtime target can be resolved."
            ),
        }
        audit["execution_completed_at"] = failed_at
        audit["executor"] = "command_executor"
        audit["failure_reason"] = error["message"]
        audit["failure_suggestion"] = error["suggestion"]
        command_store.update_status(
            command_id,
            CommandStatus.FAILED,
            error=error,
            audit=audit,
        )
        log.warning("Worker: command %s failed during routing resolution: %s", command_id, exc)
        return

    if command_type == CommandType.RECORD_SPONSOR_DECISION:
        try:
            committee_id = str(execution_params.get("committee_id") or "").strip()
            updated = read_store.record_sponsor_decision(
                committee_id,
                sponsor_decision=str(execution_params.get("sponsor_decision") or "").strip().lower(),
                rationale_ref=str(execution_params.get("rationale_ref") or "").strip(),
                actor_id=str(audit.get("operator_id") or "operator-command"),
                recorded_at=utc_now(),
            )
            if updated is None:
                raise ValueError(f"Committee {committee_id} could not be updated.")
            result = {
                "command_id": command_id,
                "committee_id": updated.get("committee_id"),
                "committee_ref": updated.get("committee_ref"),
                "sponsor_decision": updated.get("sponsor_decision"),
                "sponsor_decided_at": updated.get("sponsor_decided_at"),
                "sponsor_decided_by": updated.get("sponsor_decided_by"),
                "consensus_state": updated.get("consensus_state"),
                "rationale_ref": (updated.get("synthesis_summary") or {}).get("rationale_ref"),
                "service_handoff": updated.get("service_handoff") or {},
                "execution_completed_at": utc_now(),
            }
            audit["execution_completed_at"] = result["execution_completed_at"]
            audit["executor"] = "bff_read_store"
            audit["downstream_verified"] = True
            command_store.update_status(
                command_id,
                CommandStatus.EXECUTED,
                result=result,
                audit=audit,
            )
            log.info("Worker: command %s completed with status=%s", command_id, CommandStatus.EXECUTED.value)
            return
        except Exception as exc:
            failed_at = utc_now()
            error = {
                "code": "COMMITTEE_UPDATE_FAILED",
                "message": f"Unable to record sponsor decision: {exc}",
                "started_at": failed_at,
                "failed_at": failed_at,
                "suggestion": "Refresh the committee board projection and retry once the committee surface is available.",
            }
            audit["execution_completed_at"] = failed_at
            audit["executor"] = "bff_read_store"
            audit["failure_reason"] = error["message"]
            audit["failure_suggestion"] = error["suggestion"]
            command_store.update_status(
                command_id,
                CommandStatus.FAILED,
                error=error,
                audit=audit,
            )
            log.warning("Worker: command %s failed during committee update: %s", command_id, exc)
            return

    # Execute via real executor with propagated auth headers
    status, result, error = execute_command_with_status(
        command_id, command_type, execution_params,
        auth_token=auth_token, mfa_token=mfa_token,
    )

    # Enrich audit with execution timeline
    audit["execution_completed_at"] = result.get("execution_completed_at") if result else error.get("failed_at") if error else None
    audit["executor"] = "command_executor"
    if result:
        audit["downstream_verified"] = True
    if error:
        audit["failure_reason"] = error.get("message", "")
        audit["failure_suggestion"] = error.get("suggestion", "")

    # Persist both result and enriched audit data
    command_store.update_status(
        command_id,
        status,
        result=result,
        error=error,
        audit=audit,
    )

    log.info(
        "Worker: command %s completed with status=%s",
        command_id, status.value,
    )


# Keep backward-compatible alias for existing tests
_process_command_stub = _process_command


# --------------------------------------------------------------------------- #
# Degraded Control Guidance (Wave 2 — Incident Response)
# --------------------------------------------------------------------------- #

@app.get("/api/v1/operator/degraded-control-guidance")
async def degraded_control_guidance():
    """Return guidance for operators when the BFF is degraded or unavailable.

    Provides actionable fallback instructions using the secondary control path
    (Admin CLI and Protected Internal API) so operators can still execute
    critical incident actions (pause, rollback, kill-switch) even when the
    primary BFF path is down.

    See BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §6 and
    APP-002-SECONDARY-CONTROL-PATH.md for full spec.
    """
    state = _read_surface_state()
    guidance = {
        "current_state": state,
        "command_backend_configured": bool(os.getenv("PANTHEON_INTERNAL_API_URL", "").strip()),
        "primary_path": {
            "url": "/api/v1/operator/commands",
            "status": "available" if state == "fresh" else "degraded",
            "note": (
                "Primary BFF command path. Submit operator commands for async execution."
                if state == "fresh"
                else "BFF read surface is degraded. Commands may execute but status queries could return stale data."
            ),
        },
        "secondary_path": {
            "admin_cli": {
                "description": "Local/SSH CLI with RBAC and MFA for destructive actions",
                "commands": {
                    "pause_runtime": "pantheon-admin runtime pause --binding-id <ID> --reason <REASON>",
                    "resume_runtime": "pantheon-admin runtime resume --binding-id <ID>",
                    "rollback": "pantheon-admin rollback --target-type <TYPE> --target-id <ID> --to-version <VER>",
                    "kill_switch": "pantheon-admin kill-switch activate --scope <SCOPE> --reason <REASON>",
                },
                "auth": "SSH key + RBAC role; MFA required for destructive actions",
            },
            "protected_internal_api": {
                "description": "Direct HTTP access to control-plane internal API",
                "base_url": os.getenv("PANTHEON_INTERNAL_API_URL", "").strip() or None,
                "endpoints": {
                    "pause_runtime": "POST /api/internal/v1/runtimes/{binding_id}/pause",
                    "execute_rollback": "POST /api/internal/v1/rollbacks/execute",
                    "activate_kill_switch": "POST /api/internal/v1/kill-switch",
                    "approve_deployment": "POST /api/internal/v1/deployments/{plan_id}/approve",
                    "check_command_status": "GET /api/internal/v1/commands/{command_id}",
                },
                "auth": "Bearer token + RBAC; X-MFA-Token header for destructive actions",
            },
        },
        "critical_actions_bypass_mfa": True,
        "reconciliation": {
            "description": "When BFF recovers, reconcile command history from internal API",
            "endpoint": "GET /api/internal/v1/commands",
            "note": "Both BFF and internal API persist command records; compare by command_id to detect gaps.",
        },
        "spec_reference": "support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md",
    }

    status_code = 200 if state == "fresh" else 206
    return JSONResponse(
        status_code=status_code,
        content={"data": guidance, "meta": {"staleness": _meta_staleness()}},
    )


# --------------------------------------------------------------------------- #
# SSE Real-Time Feeds (Wave 5 — APP-002-W5-SSE-LIVE)
# --------------------------------------------------------------------------- #

# In-process event buffers per stream type.
# Each buffer is a deque of (event_id, event_dict) tuples, keeping the last N events.
_MAX_EVENTS = 500

SSE_CHANNEL_CATALOG = (
    "approval",
    "ask",
    "artifact",
    "runtime",
    "mcp",
    "skill",
    "channel",
    "tool",
    "ranking",
    "rebalance",
    "evolution",
    "research",
    "signal",
    "inbox",
    "journal",
    "postmortem",
    "loop",
    "sentinel",
    "intervention",
    "audit",
    "system",
)
SSE_CHANNELS = set(SSE_CHANNEL_CATALOG)

SSE_APPROVAL_EVENT_TYPES = {
    "approval.created",
    "approval.stage.changed",
    "approval.decided",
    "approval.sla.escalated",
}
SSE_ASK_EVENT_TYPES = {
    "ask.session.started",
    "ask.message.delta",
    "ask.tool.called",
    "ask.message.completed",
    "ask.session.completed",
    "ask.session.failed",
}

_SSE_RESYNC_ROUTES: Dict[str, tuple[str, ...]] = {
    "approval": ("/bff/approvals", "/bff/v5/interventions"),
    "ask": ("/bff/agora/ask/sessions/{id}", "/bff/agora/committee/sessions/{id}"),
}


class SseReplayUnavailableError(Exception):
    pass


# Centralized SSE buffers and subscribers
_sse_buffers: Dict[str, deque] = {
    channel: deque(maxlen=_MAX_EVENTS) for channel in SSE_CHANNEL_CATALOG
}
_sse_subscribers: Dict[str, list[asyncio.Queue]] = {
    channel: [] for channel in SSE_CHANNEL_CATALOG
}

# Compatibility aliases for existing stream routes.
_runtime_events = _sse_buffers["runtime"]
_runtime_subscribers = _sse_subscribers["runtime"]
_incident_events = deque(maxlen=_MAX_EVENTS)
_incident_subscribers: list[asyncio.Queue] = []

_kill_switch_events = _sse_buffers["system"]
_kill_switch_subscribers = _sse_subscribers["system"]

_approval_events = _sse_buffers["approval"]
_approval_subscribers = _sse_subscribers["approval"]
_ask_events = _sse_buffers["ask"]
_ask_subscribers = _sse_subscribers["ask"]


def _make_event_id(prefix: str = "evt") -> str:
    return f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _sse_format(event: dict) -> str:
    """Format a full event dict as an SSE message block."""
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


def _sse_replay_headers(channel: str) -> Dict[str, str]:
    headers = {
        "X-SSE-Channel": channel,
        "X-SSE-Replay-Supported": "true",
        "X-SSE-Replay-Window-Events": str(_MAX_EVENTS),
        "X-SSE-Buffer-Size": str(_MAX_EVENTS),
        "X-SSE-Replay-Store": "in-memory",
    }
    resync_routes = _SSE_RESYNC_ROUTES.get(channel, ())
    if resync_routes:
        headers["X-SSE-Resync-Routes"] = ",".join(resync_routes)
    return headers


def _publish_event(buffer: deque, subscribers: list[asyncio.Queue], event_type: str, data: dict) -> str:
    """Publish an event to the buffer and notify all subscribers."""
    event_id = _make_event_id()
    event = SseEventEnvelope[Dict[str, Any]](
        id=event_id,
        type=event_type,
        data=dict(data or {}),
    ).model_dump(mode="json")
    buffer.append((event_id, event))
    # Notify subscribers
    for q in list(subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass
    return event_id


def _replay_from(buffer: deque, last_event_id: Optional[str]) -> list[dict]:
    """Replay events from the buffer starting after last_event_id."""
    if not last_event_id:
        return [evt for _, evt in buffer]
    found = False
    result = []
    for eid, evt in buffer:
        if found:
            result.append(evt)
        elif eid == last_event_id:
            found = True
    if not found:
        # Client requested an event ID we no longer have
        raise SseReplayUnavailableError(f"Event ID {last_event_id} is no longer in the buffer")
    return result


async def _sse_stream(
    buffer: deque,
    subscribers: list[asyncio.Queue],
    last_event_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    subscribers.append(q)
    try:
        # Replay historical events first
        for evt in _replay_from(buffer, last_event_id):
            yield _sse_format(evt)

        # Then stream new events as they arrive
        while True:
            try:
                evt = await asyncio.wait_for(q.get(), timeout=30.0)
                yield _sse_format(evt)
            except asyncio.TimeoutError:
                # Send a comment to keep the connection alive
                yield ": heartbeat\n\n"
    finally:
        # Unsubscribe on client disconnect
        if q in subscribers:
            subscribers.remove(q)


def _handle_sse_stream(
    channel: str,
    buffer: deque,
    subscribers: list[asyncio.Queue],
    last_event_id: Optional[str],
    extra_headers: Optional[Dict[str, str]] = None,
) -> StreamingResponse:
    """Helper to create a StreamingResponse with replay error handling."""
    try:
        # Check if replay is possible before starting the stream
        _replay_from(buffer, last_event_id)
    except SseReplayUnavailableError as exc:
        error = _bff_error(
            status_code=409,
            code=ErrorCode.SSE_REPLAY_UNAVAILABLE,
            message=str(exc),
            reason="SSE_REPLAY_HISTORY_MISSING",
            suggestion="Resync canonical state via GET routes before reconnecting to the stream",
            details_extra={
                "channel": channel,
                "lastEventId": last_event_id,
                "replaySupported": True,
                "replayWindowEvents": _MAX_EVENTS,
                "replayStore": "in-memory",
                "resyncRoutes": list(_SSE_RESYNC_ROUTES.get(channel, ())),
            },
        )
        error.headers = _sse_replay_headers(channel)
        raise error from exc

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        **_sse_replay_headers(channel),
    }
    if extra_headers:
        headers.update(extra_headers)

    return StreamingResponse(
        _sse_stream(buffer, subscribers, last_event_id),
        media_type="text/event-stream",
        headers=headers,
    )


_FRONTEND_SSE_SCHEMA_VERSION = 1


def _frontend_sse_event(
    *,
    channel: str,
    event_type: str,
    payload: Optional[Dict[str, Any]] = None,
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "schemaVersion": _FRONTEND_SSE_SCHEMA_VERSION,
        "id": event_id or _make_event_id("evt-bff"),
        "channel": channel,
        "type": event_type,
        "occurredAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": payload or {},
    }


def _frontend_sse_format(event: Dict[str, Any]) -> str:
    return f"id: {event['id']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _frontend_bff_event_stream(channels: tuple[str, ...]) -> AsyncGenerator[str, None]:
    """Compatibility event stream used by the Lovable operator shell.

    Browser EventSource cannot attach Authorization headers. Until the shell has
    a cookie-backed SSE auth path, this endpoint only emits non-sensitive BFF
    liveness events instead of replaying privileged domain event buffers.
    """
    channel_list = list(channels) if channels else ["system"]
    yield _frontend_sse_format(
        _frontend_sse_event(
            channel="system",
            event_type="system.connected",
            payload={"channels": channel_list, "transport": "sse"},
        )
    )

    while True:
        await asyncio.sleep(15.0)
        yield _frontend_sse_format(
            _frontend_sse_event(
                channel="system",
                event_type="system.heartbeat",
                payload={"channels": channel_list},
            )
        )


@app.get("/bff/events/stream")
async def stream_bff_events(
    channels: Optional[str] = Query(default=None),
    channel: Optional[str] = Query(default=None),
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    last_event_id_camel: Optional[str] = Query(default=None, alias="lastEventId"),
    last_event_id_header: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    pantheon_session: Optional[str] = Cookie(default=None),
):
    """BFF-wide SSE stream for the frontend shell.

    ``lastEventId`` is accepted for the browser client, but this transitional
    liveness stream only applies to unauthenticated callers. Authenticated
    cookie or Bearer callers use the real replay-capable SSE substrate.
    """
    channels_value = channels if isinstance(channels, str) else None
    channel_value = channel if isinstance(channel, str) else None
    last_event_id_value = last_event_id if isinstance(last_event_id, str) else None
    last_event_id_camel_value = last_event_id_camel if isinstance(last_event_id_camel, str) else None
    last_event_id_header_value = last_event_id_header if isinstance(last_event_id_header, str) else None
    authorization_value = authorization if isinstance(authorization, str) else None
    x_mfa_token_value = x_mfa_token if isinstance(x_mfa_token, str) else None
    pantheon_session_value = pantheon_session if isinstance(pantheon_session, str) else None

    resolved_last_event_id = (
        last_event_id_value or last_event_id_camel_value or last_event_id_header_value
    )

    requested = tuple(
        channel.strip()
        for channel in (channel_value or channels_value or "system").split(",")
        if channel.strip()
    )
    if authorization_value or pantheon_session_value:
        selected_channel = requested[0] if requested else "system"
        if selected_channel not in SSE_CHANNELS:
            raise _bff_error(
                400,
                ErrorCode.INVALID_REQUEST,
                f"Unknown SSE channel: {selected_channel}",
                f"Channel must be one of {sorted(list(SSE_CHANNELS))}",
            )
        identity = _extract_identity(
            authorization_value,
            mfa_token=x_mfa_token_value,
            session_cookie=pantheon_session_value,
        )
        _require_read_role(identity)
        return _handle_sse_stream(
            selected_channel,
            _sse_buffers[selected_channel],
            _sse_subscribers[selected_channel],
            resolved_last_event_id,
            extra_headers={"X-BFF-Session-Kind": _resolve_session_kind(identity)},
        )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "X-SSE-Channel": "bff",
        "X-SSE-Replay-Supported": "false",
        "X-SSE-Replay-Store": "liveness-only",
        "X-SSE-Resync-Routes": "/health,/readyz",
    }
    return StreamingResponse(
        _frontend_bff_event_stream(requested),
        media_type="text/event-stream",
        headers=headers,
    )


@app.get("/api/v1/runtime/{runtime_id}/events/stream")
async def stream_runtime_events(
    runtime_id: str,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """RT-SSE: Server-Sent Events stream for runtime state changes.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return _handle_sse_stream("runtime", _runtime_events, _runtime_subscribers, last_event_id)


@app.get("/api/v1/incidents/stream")
async def stream_incident_events(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """IN-SSE: Server-Sent Events stream for active incident events.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return _handle_sse_stream("incident", _incident_events, _incident_subscribers, last_event_id)


@app.get("/api/v1/kill-switch/updates")
async def stream_kill_switch_events(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """KS-SSE: Server-Sent Events stream for kill-switch state changes.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    BFF_API_CONTRACT.md §11.2
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return _handle_sse_stream("system", _kill_switch_events, _kill_switch_subscribers, last_event_id)


@app.get("/api/v1/approvals/stream")
async def stream_approval_events(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """AP-SSE: Server-Sent Events stream for approval decisions and lifecycle.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return _handle_sse_stream("approval", _approval_events, _approval_subscribers, last_event_id)


@app.get("/api/v1/agora/ask/stream")
async def stream_ask_events(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """AK-SSE: Server-Sent Events stream for Agora ask session messages and tool calls.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return _handle_sse_stream("ask", _ask_events, _ask_subscribers, last_event_id)


# --------------------------------------------------------------------------- #
# SSE Publish Helpers (for internal use / testing / admin injection)
# --------------------------------------------------------------------------- #

@app.get("/api/v1/stream/{channel}")
async def stream_generic_events(
    channel: str,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF-SSE: Generic Server-Sent Events stream for any channel in the catalog.

    Supports reconnection via ``?last_event_id=`` to replay missed events.
    """
    if channel not in SSE_CHANNELS:
        raise _bff_error(
            400,
            ErrorCode.INVALID_REQUEST,
            f"Unknown SSE channel: {channel}",
            f"Channel must be one of {sorted(list(SSE_CHANNELS))}",
        )

    identity = _extract_identity(authorization)
    _require_read_role(identity)

    return _handle_sse_stream(channel, _sse_buffers[channel], _sse_subscribers[channel], last_event_id)


# --------------------------------------------------------------------------- #
# Evolution Programs, Experiments, Jobs, Events (BFF-LUV-GAP-004)
# --------------------------------------------------------------------------- #

_EVOL_EXP_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}


def _evol_exp_bff_idempotency_check(
    resolved_key: str,
    request_hash: str,
) -> Optional[Dict[str, Any]]:
    existing = _EVOL_EXP_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")


def _evol_exp_bff_action_command(
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: Any,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    request_hash = _stable_json_hash({
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "action_id": action_id,
        "payload": payload,
    })
    cached = _evol_exp_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    catalog_entry = get_catalog_entry(command_type.value)
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={"action_id": action_id, **payload},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
    }
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=resolved_key,
        operation_type=f"bff.{command_type.value}",
        target_ref=f"{entity_type.value}:{entity_id}",
        request_payload={
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        },
        trace_id=command_id,
    )
    foundation_ctx = {
        "idempotency_record": idempotency_record.to_dict(),
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    _EVOL_EXP_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


# -- Evolution Programs ------------------------------------------------------

@app.get("/bff/evolution-programs")
async def bff_list_evolution_programs(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: evolution program list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    programs = read_store.list_evolution_programs(status=status)
    total = len(programs)
    page_items, next_page_token = _page_slice(programs, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "evolution_programs", "evolution_program_list",
            snapshot_at=snapshot_at, total=total,
        ),
    }


@app.post("/bff/evolution-programs", status_code=201)
async def bff_create_evolution_program(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create evolution program — Idempotency-Key required."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/evolution-programs", "payload": payload})
    cached = _evol_exp_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "Evolution program name must be a non-empty string",
            precondition_failed="name",
        )
    snapshot_at = utc_now()
    program_id = f"evp-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    result = read_store.create_evolution_program(
        program_id=program_id,
        name=name,
        actor_id=identity.operator_id,
        created_at=snapshot_at,
        params=payload.get("params") or {},
    )
    _EVOL_EXP_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/evolution-programs/{program_id}")
async def bff_get_evolution_program(
    program_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: evolution program detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    program = read_store.get_evolution_program(program_id)
    if not program:
        surface = _dataset_surface_status("evolution_programs", snapshot_at=snapshot_at)
        _raise_if_read_surface_unavailable(surface, label="Evolution program")
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {program_id} does not exist",
        )
    return {
        "data": program,
        "meta": _read_surface_meta("evolution_programs", "evolution_program_detail", snapshot_at=snapshot_at),
    }


@app.patch("/bff/evolution-programs/{program_id}")
async def bff_patch_evolution_program(
    program_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: patch evolution program — Idempotency-Key required."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash(
        {"route": "PATCH /bff/evolution-programs/{program_id}", "program_id": program_id, "payload": payload}
    )
    cached = _evol_exp_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    program = read_store.get_evolution_program(program_id)
    if not program:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {program_id} does not exist",
        )
    snapshot_at = utc_now()
    updated = read_store.patch_evolution_program(
        program_id,
        patch={k: payload[k] for k in ("name", "status", "params") if k in payload},
        actor_id=identity.operator_id,
        updated_at=snapshot_at,
    ) or dict(program)
    result = {"data": updated, "meta": {"snapshot_at": snapshot_at}}
    _EVOL_EXP_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/evolution-programs/{program_id}/runs")
async def bff_list_evolution_program_runs(
    program_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list runs for an evolution program."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    program = read_store.get_evolution_program(program_id)
    if not program:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {program_id} does not exist",
        )
    runs = read_store.list_evolution_program_runs(program_id)
    return {
        "data": runs,
        "page_info": {"total": len(runs)},
        "meta": _read_surface_meta("evolution_programs", "evolution_program_runs", snapshot_at=snapshot_at),
    }


@app.get("/bff/evolution-programs/{program_id}/candidates")
async def bff_list_evolution_program_candidates(
    program_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list candidates for an evolution program."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    program = read_store.get_evolution_program(program_id)
    if not program:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {program_id} does not exist",
        )
    candidates = read_store.list_evolution_program_candidates(program_id)
    return {
        "data": candidates,
        "page_info": {"total": len(candidates)},
        "meta": _read_surface_meta("evolution_programs", "evolution_program_candidates", snapshot_at=snapshot_at),
    }


@app.post("/bff/evolution-programs/{program_id}/actions/{action_id}", status_code=202)
async def bff_evolution_program_action(
    program_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: evolution program action."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    program = read_store.get_evolution_program(program_id)
    if not program:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {program_id} does not exist",
        )
    return _evol_exp_bff_action_command(
        entity_type=ObjectType.EVOLUTION_PROGRAM,
        entity_id=program_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.EVOLUTION_PROGRAM_ACTION,
    )


# -- Experiments -------------------------------------------------------------

@app.get("/bff/experiments")
async def bff_list_experiments(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: experiment list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = read_store.list_experiments_bff(status=status)
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta("experiments", "experiment_list", snapshot_at=snapshot_at, total=total),
    }


@app.post("/bff/experiments", status_code=201)
async def bff_create_experiment(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create experiment — Idempotency-Key required."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/experiments", "payload": payload})
    cached = _evol_exp_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or payload.get("experiment_name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "Experiment name must be a non-empty string",
            precondition_failed="name",
        )
    result = read_store.create_experiment_bff(
        name=name,
        actor_id=identity.operator_id,
        created_at=utc_now(),
        params=payload,
    )
    _EVOL_EXP_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/experiments/{experiment_id}")
async def bff_get_experiment(
    experiment_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: experiment detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    item = read_store.get_experiment_bff(experiment_id)
    if not item:
        surface = _dataset_surface_status("experiments", snapshot_at=snapshot_at)
        _raise_if_read_surface_unavailable(surface, label="Experiment")
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experiment_id} does not exist",
        )
    return {
        "data": item,
        "meta": _read_surface_meta("experiments", "experiment_detail", snapshot_at=snapshot_at),
    }


@app.post("/bff/experiments/{experiment_id}/actions/{action_id}", status_code=202)
async def bff_experiment_action(
    experiment_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: experiment action."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    item = read_store.get_experiment_bff(experiment_id)
    if not item:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experiment_id} does not exist",
        )
    return _evol_exp_bff_action_command(
        entity_type=ObjectType.EXPERIMENT,
        entity_id=experiment_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.EXPERIMENT_ACTION,
    )


@app.get("/bff/experiments/{experiment_id}/logs")
async def bff_get_experiment_logs(
    experiment_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: experiment logs."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    item = read_store.get_experiment_bff(experiment_id)
    if not item:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experiment_id} does not exist",
        )
    logs = read_store.get_experiment_logs(experiment_id)
    return {
        "data": logs,
        "meta": _read_surface_meta("experiments", "experiment_logs", snapshot_at=snapshot_at),
    }


@app.get("/bff/experiments/{experiment_id}/metrics")
async def bff_get_experiment_metrics(
    experiment_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: experiment metrics."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    item = read_store.get_experiment_bff(experiment_id)
    if not item:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experiment_id} does not exist",
        )
    metrics = read_store.get_experiment_metrics(experiment_id)
    return {
        "data": metrics,
        "meta": _read_surface_meta("experiments", "experiment_metrics", snapshot_at=snapshot_at),
    }


@app.get("/bff/experiments/{experiment_id}/artifacts")
async def bff_get_experiment_artifacts(
    experiment_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: experiment artifacts."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    item = read_store.get_experiment_bff(experiment_id)
    if not item:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experiment_id} does not exist",
        )
    artifacts = read_store.get_experiment_artifacts(experiment_id)
    return {
        "data": artifacts,
        "meta": _read_surface_meta("experiments", "experiment_artifacts", snapshot_at=snapshot_at),
    }


# -- Jobs --------------------------------------------------------------------

@app.get("/bff/jobs")
async def bff_list_jobs(
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: job list."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    jobs = read_store.list_jobs_bff(status=status, job_type=job_type)
    total = len(jobs)
    page_items, next_page_token = _page_slice(jobs, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta("jobs", "job_list", snapshot_at=snapshot_at, total=total),
    }


@app.get("/bff/jobs/{job_id}")
async def bff_get_job(
    job_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: job detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    job = read_store.get_job_bff(job_id)
    if not job:
        surface = _dataset_surface_status("jobs", snapshot_at=snapshot_at)
        _raise_if_read_surface_unavailable(surface, label="Job")
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Job not found",
            f"Job {job_id} does not exist",
        )
    return {
        "data": job,
        "meta": _read_surface_meta("jobs", "job_detail", snapshot_at=snapshot_at),
    }


@app.get("/bff/jobs/{job_id}/logs")
async def bff_get_job_logs(
    job_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: job logs."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    job = read_store.get_job_bff(job_id)
    if not job:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Job not found",
            f"Job {job_id} does not exist",
        )
    logs = read_store.get_job_logs_bff(job_id)
    return {
        "data": logs,
        "meta": _read_surface_meta("jobs", "job_logs", snapshot_at=snapshot_at),
    }


@app.post("/bff/jobs/{job_id}/actions/{action_id}", status_code=202)
async def bff_job_action(
    job_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: job action."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    job = read_store.get_job_bff(job_id)
    if not job:
        raise _bff_error(
            404, ErrorCode.OBJECT_NOT_FOUND,
            "Job not found",
            f"Job {job_id} does not exist",
        )
    return _evol_exp_bff_action_command(
        entity_type=ObjectType.JOB,
        entity_id=job_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.JOB_ACTION,
    )


# -- Events list -------------------------------------------------------------

@app.get("/bff/events")
async def bff_list_events(
    event_type: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=50, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: event list (paginated telemetry/audit feed)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    events = read_store.list_events_bff(event_type=event_type, page_size=page_size)
    total = len(events)
    page_items, next_page_token = _page_slice(events, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta("events", "event_list", snapshot_at=snapshot_at, total=total),
    }


# --------------------------------------------------------------------------- #
# Execute-Plans SSE Compatibility Aliases (BFF-LUV-GAP-010)
# --------------------------------------------------------------------------- #

@app.get("/bff/events/stream")
async def bff_events_stream_alias(
    channel: str = Query(default="system", description="SSE channel name"),
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for generic stream with Part 06 naming."""
    return await stream_generic_events(channel, last_event_id, authorization)


@app.get("/bff/sse/notifications")
async def bff_sse_notifications_alias(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for inbox channel notifications."""
    return await stream_generic_events("inbox", last_event_id, authorization)


@app.get("/bff/sse/command-center/kpi")
async def bff_sse_cc_kpi_alias(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for ranking channel KPI updates."""
    return await stream_generic_events("ranking", last_event_id, authorization)


@app.get("/bff/sse/command-center/events")
async def bff_sse_cc_events_alias(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for loop channel system events."""
    return await stream_generic_events("loop", last_event_id, authorization)


@app.get("/bff/sse/jobs/{jobId}/progress")
async def bff_sse_job_progress_alias(
    jobId: str,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for tool channel job progress updates."""
    # Note: substrate is channel-based; client filters by jobId in payload.
    return await stream_generic_events("tool", last_event_id, authorization)


@app.get("/bff/sse/alerts")
async def bff_sse_alerts_alias(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for sentinel channel alerts."""
    return await stream_generic_events("sentinel", last_event_id, authorization)


@app.get("/bff/sse/incidents/{incidentId}/timeline")
async def bff_sse_incident_timeline_alias(
    incidentId: str,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for journal channel incident timeline events."""
    # Maps to journal channel which aggregates incident events.
    return await stream_generic_events("journal", last_event_id, authorization)


@app.get("/bff/sse/deployment/events")
async def bff_sse_deployment_events_alias(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for artifact channel deployment events."""
    return await stream_generic_events("artifact", last_event_id, authorization)


@app.get("/bff/sse/review/updates")
async def bff_sse_review_updates_alias(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for approval channel review updates."""
    return await stream_approval_events(last_event_id, authorization)


@app.get("/bff/sse/agora/signals")
async def bff_sse_agora_signals_alias(
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for signal channel Agora signals."""
    return await stream_generic_events("signal", last_event_id, authorization)


@app.get("/bff/sse/agora/sessions/{sessionId}")
async def bff_sse_agora_session_alias(
    sessionId: str,
    last_event_id: Optional[str] = Query(default=None, alias="last_event_id"),
    authorization: Optional[str] = Header(default=None),
):
    """Alias for ask channel Agora session messages."""
    return await stream_ask_events(last_event_id, authorization)


@app.post("/api/v1/internal/sse/publish")
async def publish_sse_event(
    event_type: str = Query(..., description="Event type: runtime_state_changed, incident_created, etc."),
    channel: Optional[str] = Query(default=None, description="Optional channel name; inferred from event_type if missing"),
    runtime_id: Optional[str] = Query(default=None),
    incident_id: Optional[str] = Query(default=None),
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
):
    """Internal helper to publish SSE events for testing and integration.

    In production, events would be published by downstream services via
    an internal message bus. This endpoint is a convenience for smoke tests.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    # Infer channel if missing
    if not channel:
        if event_type.startswith("runtime"):
            channel = "runtime"
        elif event_type.startswith("incident"):
            channel = "journal"  # Map incident to journal
        elif event_type.startswith("kill_switch"):
            channel = "system"
        elif event_type.startswith("approval"):
            channel = "approval"
        elif event_type.startswith("ask"):
            channel = "ask"
        else:
            channel = "system"  # Default

    event_payload = dict(payload or {})

    if channel == "journal" and event_type.startswith("incident"):
        # Legacy incident support
        event_id = _publish_event(
            _incident_events, _incident_subscribers, event_type,
            {"incident_id": incident_id, **event_payload},
        )
        # Also publish to the journal channel if it's different
        if "journal" in _sse_buffers:
            _publish_event(
                _sse_buffers["journal"], _sse_subscribers["journal"], event_type,
                {"incident_id": incident_id, **event_payload},
            )
        return {"event_id": event_id, "status": "published"}

    if channel not in _sse_buffers:
        raise _bff_error(
            400,
            ErrorCode.INVALID_REQUEST,
            f"Unknown SSE channel: {channel}",
            f"Channel must be one of {list(SSE_CHANNEL_CATALOG)}",
        )

    # For runtime channel, we might want to include runtime_id in the payload
    if channel == "runtime" and runtime_id:
        event_payload["runtime_id"] = runtime_id

    event_id = _publish_event(
        _sse_buffers[channel], _sse_subscribers[channel], event_type,
        event_payload,
    )

    return {"event_id": event_id, "status": "published"}


# --------------------------------------------------------------------------- #
# Tools, MCP servers, and Skills BFF compatibility (BFF-LUV-GAP-008)
# --------------------------------------------------------------------------- #

_TOOLS_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_MCP_SERVER_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_SKILLS_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}

# In-memory registries for BFF-local dev/test state (not a durable store).
_TOOL_REGISTRY: Dict[str, Dict[str, Any]] = {}
_MCP_SERVER_REGISTRY: Dict[str, Dict[str, Any]] = {}
_SKILL_REGISTRY: Dict[str, Dict[str, Any]] = {}


def _read_store_fixture_records(dataset: str) -> List[Dict[str, Any]]:
    data = getattr(read_store, "_data", {})
    raw = data.get(dataset) if isinstance(data, dict) else None
    if isinstance(raw, dict):
        return [dict(record) for record in raw.values() if isinstance(record, dict)]
    if isinstance(raw, list):
        return [dict(record) for record in raw if isinstance(record, dict)]
    return []


def _merge_registry_records(
    fixture_records: List[Dict[str, Any]],
    registry_records: List[Dict[str, Any]],
    id_keys: tuple[str, ...],
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for record in fixture_records + registry_records:
        record_id = ""
        for key in id_keys:
            value = record.get(key)
            if value not in (None, ""):
                record_id = str(value)
                break
        if record_id:
            merged[record_id] = dict(record)
    return list(merged.values())


def _tool_fixture_records() -> List[Dict[str, Any]]:
    return _read_store_fixture_records("tools")


def _skill_fixture_records() -> List[Dict[str, Any]]:
    return _read_store_fixture_records("skills")


def _mcp_server_fixture_records() -> List[Dict[str, Any]]:
    return _read_store_fixture_records("mcp_servers")


def _mcp_tool_fixture_records() -> List[Dict[str, Any]]:
    return _read_store_fixture_records("mcp_tools")


def _merged_tool_records() -> List[Dict[str, Any]]:
    return _merge_registry_records(
        _tool_fixture_records(),
        [dict(record) for record in _TOOL_REGISTRY.values()],
        ("tool_id", "id"),
    )


def _merged_skill_records() -> List[Dict[str, Any]]:
    return _merge_registry_records(
        _skill_fixture_records(),
        [dict(record) for record in _SKILL_REGISTRY.values()],
        ("skill_id", "id"),
    )


def _merged_mcp_server_records() -> List[Dict[str, Any]]:
    return _merge_registry_records(
        _mcp_server_fixture_records(),
        [dict(record) for record in _MCP_SERVER_REGISTRY.values()],
        ("server_id", "id"),
    )


def _merged_mcp_tool_records() -> List[Dict[str, Any]]:
    return _merge_registry_records(
        _mcp_tool_fixture_records(),
        [dict(record) for record in _MCP_TOOL_REGISTRY.values()],
        ("tool_id", "id"),
    )


def _find_record_by_id(records: List[Dict[str, Any]], entity_id: str, id_keys: tuple[str, ...]) -> Optional[Dict[str, Any]]:
    clean_id = str(entity_id or "").strip()
    return next(
        (
            dict(record)
            for record in records
            if any(str(record.get(key) or "") == clean_id for key in id_keys)
        ),
        None,
    )


def _tools_bff_idempotency_check(resolved_key: str, request_hash: str) -> Optional[Dict[str, Any]]:
    existing = _TOOLS_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409, ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")


def _skills_bff_idempotency_check(resolved_key: str, request_hash: str) -> Optional[Dict[str, Any]]:
    existing = _SKILLS_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409, ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")


def _mcp_server_bff_idempotency_check(resolved_key: str, request_hash: str) -> Optional[Dict[str, Any]]:
    existing = _MCP_SERVER_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is None:
        return None
    if existing.get("request_hash") != request_hash:
        raise _bff_error(
            409, ErrorCode.IDEMPOTENCY_CONFLICT,
            "Idempotency key was already used with a different payload",
            f"Key {resolved_key!r} is bound to a different request hash",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )
    return existing.get("result")


def _tools_mcp_skills_action_command(
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: Any,
    payload: Dict[str, Any],
    command_type: CommandType,
    idempotency_store: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Submit a tool / MCP server / skill resource action through the command store."""
    request_hash = _stable_json_hash({
        "route": f"POST /bff/{entity_type.value.lower()}/{{id}}/actions",
        "entity_type": entity_type.value,
        "entity_id": entity_id,
        "action_id": action_id,
        "payload": payload,
    })
    existing = idempotency_store.get(resolved_key)
    if existing is not None:
        if existing.get("request_hash") != request_hash:
            raise _bff_error(
                409, ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return existing["result"]

    catalog_entry = get_catalog_entry(command_type.value)
    staleness_warning = _check_read_surface_state()
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={"action_id": action_id, **payload},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
        "idempotency_key": resolved_key,
        "request_hash": request_hash,
        "catalog_entry": catalog_entry.action_id if catalog_entry else None,
    }
    foundation_ctx = {
        "idempotency_record": {
            "idempotency_key": resolved_key,
            "request_hash": request_hash,
            "operation_type": f"bff.{command_type.value}",
            "target_ref": f"{entity_type.value}:{entity_id}",
            "trace_id": audit_action.trace_id,
        },
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    payload_dump: Dict[str, Any]
    if hasattr(result, "model_dump"):
        payload_dump = result.model_dump(mode="json")
    elif isinstance(result, dict):
        payload_dump = result
    else:
        payload_dump = {"data": result}
    idempotency_store[resolved_key] = {"request_hash": request_hash, "result": payload_dump}
    return payload_dump


# ---------------- /bff/tools routes ----------------

@app.get("/bff/tools")
async def bff_list_tools(
    status: Optional[str] = None,
    tool_class: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: tool registry list (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = _merged_tool_records()
    if status:
        items = [t for t in items if t.get("status") == status]
    if tool_class:
        items = [t for t in items if t.get("tool_class") == tool_class]
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta("tools", "tool_list", snapshot_at=snapshot_at, total=total),
    }


@app.post("/bff/tools", status_code=201)
async def bff_create_tool(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create generic tool metadata record (Part 06 compatibility surface).

    Note: MCP-sourced tools must be created via the import route
    (POST /bff/v1/mcp/servers/{id}/import-tools). This route is for
    non-MCP generic tool descriptors only; MCP tool standalone-create is
    superseded by the import contract.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/tools", "payload": payload})
    cached = _tools_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "Tool name must be a non-empty string",
            precondition_failed="name",
        )
    snapshot_at = utc_now()
    tool_id = f"tool-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    result = {
        "id": tool_id,
        "tool_id": tool_id,
        "name": name,
        "status": "draft",
        "tool_class": payload.get("tool_class") or "generic",
        "description": payload.get("description") or "",
        "input_schema": payload.get("input_schema") or {},
        "output_schema": payload.get("output_schema") or {},
        "mcp_sourced": False,
        "created_at": snapshot_at,
        "updated_at": snapshot_at,
        "created_by": identity.operator_id,
    }
    _TOOL_REGISTRY[tool_id] = result
    _TOOLS_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/tools/{tool_id}")
async def bff_get_tool(
    tool_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: tool detail (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    clean_id = str(tool_id or "").strip()
    if not clean_id:
        raise _bff_error(422, ErrorCode.INVALID_PARAMS, "tool_id is required", "tool_id path parameter must be a non-empty string", precondition_failed="tool_id")
    record = _find_record_by_id(_merged_tool_records(), clean_id, ("tool_id", "id"))
    if record is None:
        # Also search MCP tool registry for MCP-imported tools.
        for reg_record in _merged_mcp_tool_records():
            if reg_record.get("tool_id") == clean_id:
                record = {
                    "id": clean_id,
                    "tool_id": clean_id,
                    "name": reg_record.get("name", clean_id),
                    "status": reg_record.get("status", "imported"),
                    "tool_class": reg_record.get("tool_class", ""),
                    "mcp_sourced": True,
                    "server_id": reg_record.get("server_id"),
                    "updated_at": utc_now(),
                }
                break
    if record is None:
        raise _bff_error(404, ErrorCode.OBJECT_NOT_FOUND, "Tool not found", f"tool_id={clean_id!r} is not registered", precondition_failed="tool_id")
    return record


@app.patch("/bff/tools/{tool_id}")
async def bff_patch_tool(
    tool_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: patch generic tool metadata (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    clean_id = str(tool_id or "").strip()
    if not clean_id:
        raise _bff_error(422, ErrorCode.INVALID_PARAMS, "tool_id is required", "tool_id path parameter must be a non-empty string", precondition_failed="tool_id")
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "PATCH /bff/tools/{tool_id}", "id": clean_id, "payload": payload})
    cached = _tools_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    record = _TOOL_REGISTRY.get(clean_id)
    if record is None:
        raise _bff_error(404, ErrorCode.OBJECT_NOT_FOUND, "Tool not found", f"tool_id={clean_id!r} is not registered", precondition_failed="tool_id")
    allowed_patches = {"name", "description", "status", "input_schema", "output_schema", "tool_class"}
    for field in allowed_patches:
        if field in payload:
            record[field] = payload[field]
    record["updated_at"] = utc_now()
    _TOOLS_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": record}
    return record


@app.post("/bff/tools/{tool_id}/actions/{action_id}", status_code=202)
async def bff_tool_action(
    tool_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: tool action — routes through command/precondition machinery."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    clean_id = str(tool_id or "").strip()
    if not clean_id:
        raise _bff_error(422, ErrorCode.INVALID_PARAMS, "tool_id is required", "tool_id path parameter must be a non-empty string", precondition_failed="tool_id")
    return _tools_mcp_skills_action_command(
        entity_type=ObjectType.TOOL,
        entity_id=clean_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.TOOL_ACTION,
        idempotency_store=_TOOLS_BFF_IDEMPOTENCY,
    )


# ---------------- /bff/mcp/servers routes (Part 06 compatibility) ----------------
# These routes provide the Part 06 MCP server management surface.
# The final MCP import contract (/bff/v1/mcp/servers/{id}/import-tools and
# /bff/mcp-servers/{id}/import-tools) is preserved and unaffected.

@app.get("/bff/mcp/servers")
async def bff_list_mcp_servers(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: MCP server list (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = _merged_mcp_server_records()
    if status:
        items = [s for s in items if s.get("status") == status]
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta("mcp_servers", "mcp_server_list", snapshot_at=snapshot_at, total=total),
    }


@app.post("/bff/mcp/servers", status_code=201)
async def bff_create_mcp_server(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: register an MCP server connection record (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_mcp_tool_write_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/mcp/servers", "payload": payload})
    cached = _mcp_server_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "MCP server name must be a non-empty string",
            precondition_failed="name",
        )
    snapshot_at = utc_now()
    server_id = f"mcp-srv-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    result = {
        "id": server_id,
        "server_id": server_id,
        "name": name,
        "status": "registered",
        "endpoint": payload.get("endpoint") or "",
        "server_version": payload.get("server_version") or "",
        "governance": payload.get("governance") or {},
        "created_at": snapshot_at,
        "updated_at": snapshot_at,
        "created_by": identity.operator_id,
    }
    _MCP_SERVER_REGISTRY[server_id] = result
    _MCP_SERVER_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/mcp/servers/{server_id}")
async def bff_get_mcp_server(
    server_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: MCP server detail (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    clean_id = _validate_mcp_server_id(server_id)
    record = _find_record_by_id(_merged_mcp_server_records(), clean_id, ("server_id", "id"))
    if record is None:
        raise _bff_error(404, ErrorCode.OBJECT_NOT_FOUND, "MCP server not found", f"server_id={clean_id!r} is not registered", precondition_failed="server_id")
    return record


@app.post("/bff/mcp/servers/{server_id}/actions/{action_id}", status_code=202)
async def bff_mcp_server_action(
    server_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: MCP server action (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_mcp_tool_write_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    clean_id = _validate_mcp_server_id(server_id)
    return _tools_mcp_skills_action_command(
        entity_type=ObjectType.MCP_SERVER,
        entity_id=clean_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.MCP_SERVER_ACTION,
        idempotency_store=_MCP_SERVER_BFF_IDEMPOTENCY,
    )


@app.get("/bff/mcp/servers/{server_id}/tools")
async def bff_list_mcp_server_tools(
    server_id: str,
    authorization: Optional[str] = Header(default=None),
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
):
    """BFF: list imported tools for a given MCP server (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    clean_id = _validate_mcp_server_id(server_id)
    snapshot_at = utc_now()
    tools = [
        {
            "tool_id": rec["tool_id"],
            "name": rec.get("name", rec["tool_id"]),
            "tool_class": rec.get("tool_class", ""),
            "status": rec.get("status", "imported"),
            "server_id": rec.get("server_id", clean_id),
            "action_count": rec.get("action_count", 0),
            "schema_url": rec.get("schema_url"),
        }
        for rec in _merged_mcp_tool_records()
        if rec.get("server_id") == clean_id
    ]
    total = len(tools)
    page_items, next_page_token = _page_slice(tools, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta("mcp_tools", "mcp_server_tool_list", snapshot_at=snapshot_at, total=total),
    }


@app.post("/bff/mcp/tools/{tool_id}/actions/{action_id}", status_code=202)
async def bff_mcp_tool_action_compat(
    tool_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: Part 06 MCP tool action compatibility alias.

    Delegates to the final MCP tool action route
    (POST /bff/mcp-tools/{tool_id}/{action}) when action_id matches a
    lifecycle verb, otherwise routes through the generic command machinery.
    """
    identity = _extract_identity(authorization)
    _require_mcp_tool_write_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    clean_tool_id = str(tool_id or "").strip()
    if not clean_tool_id:
        raise _bff_error(422, ErrorCode.INVALID_PARAMS, "tool_id is required", "tool_id path parameter must be a non-empty string", precondition_failed="tool_id")
    # If action_id is a final MCP lifecycle verb, delegate to the final route handler.
    lifecycle_verbs = {v.value for v in McpToolActionVerb}
    if action_id in lifecycle_verbs:
        return await admit_mcp_tool_action_alias(
            tool_id=clean_tool_id,
            action=McpToolActionVerb(action_id),
            payload=payload,
            authorization=authorization,
            idempotency_key=idempotency_key,
            x_idempotency_key=x_idempotency_key,
        )
    # Generic command path for non-lifecycle actions.
    return _tools_mcp_skills_action_command(
        entity_type=ObjectType.TOOL,
        entity_id=clean_tool_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.TOOL_ACTION,
        idempotency_store=_TOOLS_BFF_IDEMPOTENCY,
    )


# ---------------- /bff/skills routes ----------------

@app.get("/bff/skills")
async def bff_list_skills(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: skill registry list (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    items = _merged_skill_records()
    if status:
        items = [s for s in items if s.get("status") == status]
    total = len(items)
    page_items, next_page_token = _page_slice(items, page_token, page_size)
    return {
        "data": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta("skills", "skill_list", snapshot_at=snapshot_at, total=total),
    }


@app.post("/bff/skills", status_code=201)
async def bff_create_skill(
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: create skill record (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/skills", "payload": payload})
    cached = _skills_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    name = str(payload.get("name") or "").strip()
    if not name:
        raise _bff_error(
            422, ErrorCode.INVALID_PARAMS, "name is required",
            "Skill name must be a non-empty string",
            precondition_failed="name",
        )
    snapshot_at = utc_now()
    skill_id = f"skill-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
    result = {
        "id": skill_id,
        "skill_id": skill_id,
        "name": name,
        "status": "draft",
        "description": payload.get("description") or "",
        "sandbox_enabled": bool(payload.get("sandbox_enabled", True)),
        "input_schema": payload.get("input_schema") or {},
        "output_schema": payload.get("output_schema") or {},
        "created_at": snapshot_at,
        "updated_at": snapshot_at,
        "created_by": identity.operator_id,
    }
    _SKILL_REGISTRY[skill_id] = result
    _SKILLS_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/skills/{skill_id}")
async def bff_get_skill(
    skill_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: skill detail (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    clean_id = str(skill_id or "").strip()
    if not clean_id:
        raise _bff_error(422, ErrorCode.INVALID_PARAMS, "skill_id is required", "skill_id path parameter must be a non-empty string", precondition_failed="skill_id")
    record = _find_record_by_id(_merged_skill_records(), clean_id, ("skill_id", "id"))
    if record is None:
        raise _bff_error(404, ErrorCode.OBJECT_NOT_FOUND, "Skill not found", f"skill_id={clean_id!r} is not registered", precondition_failed="skill_id")
    return record


@app.patch("/bff/skills/{skill_id}")
async def bff_patch_skill(
    skill_id: str,
    payload: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: patch skill record (Part 06 compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    clean_id = str(skill_id or "").strip()
    if not clean_id:
        raise _bff_error(422, ErrorCode.INVALID_PARAMS, "skill_id is required", "skill_id path parameter must be a non-empty string", precondition_failed="skill_id")
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "PATCH /bff/skills/{skill_id}", "id": clean_id, "payload": payload})
    cached = _skills_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    record = _SKILL_REGISTRY.get(clean_id)
    if record is None:
        raise _bff_error(404, ErrorCode.OBJECT_NOT_FOUND, "Skill not found", f"skill_id={clean_id!r} is not registered", precondition_failed="skill_id")
    allowed_patches = {"name", "description", "status", "sandbox_enabled", "input_schema", "output_schema"}
    for field in allowed_patches:
        if field in payload:
            record[field] = payload[field]
    record["updated_at"] = utc_now()
    _SKILLS_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": record}
    return record


@app.post("/bff/skills/{skill_id}/actions/{action_id}", status_code=202)
async def bff_skill_action(
    skill_id: str,
    action_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: skill action — routes through command/precondition machinery."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    clean_id = str(skill_id or "").strip()
    if not clean_id:
        raise _bff_error(422, ErrorCode.INVALID_PARAMS, "skill_id is required", "skill_id path parameter must be a non-empty string", precondition_failed="skill_id")
    return _tools_mcp_skills_action_command(
        entity_type=ObjectType.SKILL,
        entity_id=clean_id,
        action_id=action_id,
        resolved_key=resolved_key,
        identity=identity,
        payload=payload,
        command_type=CommandType.SKILL_ACTION,
        idempotency_store=_SKILLS_BFF_IDEMPOTENCY,
    )


@app.post("/bff/skills/{skill_id}/sandbox-eval", status_code=202)
async def bff_skill_sandbox_eval(
    skill_id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: submit a skill sandbox evaluation job.

    Returns a job/command envelope and audit record.  The sandbox eval
    executes the skill in an isolated context against the provided inputs
    without touching live execution paths.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    clean_id = str(skill_id or "").strip()
    if not clean_id:
        raise _bff_error(422, ErrorCode.INVALID_PARAMS, "skill_id is required", "skill_id path parameter must be a non-empty string", precondition_failed="skill_id")
    request_hash = _stable_json_hash({"route": "POST /bff/skills/{id}/sandbox-eval", "id": clean_id, "payload": payload})
    cached = _skills_bff_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    snapshot_at = utc_now()
    job_id = f"sandbox-eval-{clean_id}-{uuid.uuid4().hex[:10]}"
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": "sandbox-eval",
        "skill_id": clean_id,
        "timestamp": snapshot_at,
        "idempotency_key": resolved_key,
    }
    result = {
        "job_id": job_id,
        "skill_id": clean_id,
        "status": "queued",
        "sandbox_mode": True,
        "inputs": payload.get("inputs") or {},
        "submitted_at": snapshot_at,
        "submitted_by": identity.operator_id,
        "audit": audit_record,
        "meta": {
            "estimated_processing_time_ms": 3000,
            "next_poll_after_ms": 500,
        },
    }
    _SKILLS_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


# --------------------------------------------------------------------------- #
# BFF Governance, Runtime, Risk, Incident, Audit Compatibility (BFF-LUV-GAP-005)
# --------------------------------------------------------------------------- #

# In-process idempotency ledger for action operations on these surfaces.
_GOV_BFF_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}
_GOV_BFF_INCIDENT_OVERLAY: Dict[str, Dict[str, Any]] = {}

_INCIDENT_CASE_ALIAS_FIELDS = {
    "binding_id": ("binding_id", "runtime_binding_id"),
    "deployment_stage": ("deployment_stage", "deployment_mode"),
    "deployment_plan_id": ("deployment_plan_id", "plan_id"),
    "capital_pool_id": ("capital_pool_id", "affected_pool_id"),
    "persona_capital_binding_id": ("persona_capital_binding_id",),
    "artifact_id": ("artifact_id",),
    "artifact_version": ("artifact_version",),
    "runtime_id": ("runtime_id",),
    "trace_id": ("trace_id", "correlation_id"),
}


def _first_present(payload: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _project_bff_incident_case(incident: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(incident)
    incident_id = str(payload.get("incident_id") or payload.get("id") or "")
    if incident_id:
        payload["id"] = payload.get("id") or incident_id
        payload["incident_id"] = incident_id

    for field, aliases in _INCIDENT_CASE_ALIAS_FIELDS.items():
        value = _first_present(payload, aliases)
        if value is not None:
            payload[field] = value

    created_at = payload.get("created_at") or payload.get("opened_at")
    if created_at:
        payload["created_at"] = created_at
        payload["opened_at"] = payload.get("opened_at") or created_at

    if not payload.get("lineage_ref") and payload.get("artifact_id") and payload.get("artifact_version"):
        payload["lineage_ref"] = f"{payload['artifact_id']}@{payload['artifact_version']}"

    return payload


def _bff_incident_matches_filters(
    incident: Dict[str, Any],
    *,
    status: Optional[str],
    severity: Optional[str],
    affected_pool_id: Optional[str],
) -> bool:
    if status:
        requested_statuses = {token.strip().lower() for token in status.split(",") if token.strip()}
        if str(incident.get("status") or "").lower() not in requested_statuses:
            return False
    if severity and str(incident.get("severity") or "").lower() != severity.lower():
        return False
    if affected_pool_id and (incident.get("capital_pool_id") or incident.get("affected_pool_id")) != affected_pool_id:
        return False
    return True


def _list_bff_incidents(
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    affected_pool_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    incidents = [
        _project_bff_incident_case(incident)
        for incident in read_store.list_incidents(
            status=status,
            severity=severity,
            affected_pool_id=affected_pool_id,
        )
    ]
    seen = {str(item.get("incident_id") or item.get("id") or "") for item in incidents}
    for incident_id, incident in _GOV_BFF_INCIDENT_OVERLAY.items():
        if incident_id in seen:
            continue
        if _bff_incident_matches_filters(
            incident,
            status=status,
            severity=severity,
            affected_pool_id=affected_pool_id,
        ):
            incidents.append(_project_bff_incident_case(incident))
    return sorted(incidents, key=lambda item: str(item.get("created_at") or item.get("submitted_at") or ""), reverse=True)


def _get_bff_incident(incident_id: str) -> Optional[Dict[str, Any]]:
    incident = read_store.get_incident(incident_id)
    if incident:
        return _project_bff_incident_case(incident)
    overlay = _GOV_BFF_INCIDENT_OVERLAY.get(incident_id)
    return _project_bff_incident_case(overlay) if overlay else None


def _gov_bff_action_command(
    entity_type: ObjectType,
    entity_id: str,
    action_id: str,
    resolved_key: str,
    identity: Any,
    payload: Dict[str, Any],
    command_type: CommandType,
) -> Dict[str, Any]:
    """Submit a governance/risk/incident resource action through the command store."""
    _reject_body_idempotency_key(payload)
    request_hash = _stable_json_hash(
        {"entity_type": entity_type.value, "entity_id": entity_id, "action_id": action_id, "payload": payload}
    )
    existing = _GOV_BFF_IDEMPOTENCY.get(resolved_key)
    if existing is not None:
        if existing.get("request_hash") != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return existing["result"]

    staleness_warning = _check_read_surface_state()
    catalog_entry = get_catalog_entry(command_type.value)
    command_id = str(uuid.uuid4())
    submitted_at = utc_now()
    target = TargetObject(type=entity_type, id=entity_id)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=entity_type,
        target_id=entity_id,
        payload={"action_id": action_id, **payload},
        reason=str(payload.get("reason") or action_id or command_type.value),
        command_id=command_id,
        idempotency_key=resolved_key,
        route=f"POST /bff/{entity_type.value}/{entity_id}/actions/{action_id}",
        metadata={"action_id": action_id, "catalog_entry": catalog_entry.action_id if catalog_entry else None},
    )
    audit_record = {
        "operator_id": identity.operator_id,
        "roles_at_submission": identity.roles,
        "action_id": action_id,
        "preconditions_checked": ["authentication", "authorization", "idempotency"],
        "timestamp": submitted_at,
        "idempotency_key": resolved_key,
        "request_hash": request_hash,
        "catalog_entry": catalog_entry.action_id if catalog_entry else None,
    }
    idempotency_record = IdempotencyRecord.reserve(
        idempotency_key=resolved_key,
        operation_type=f"bff.{command_type.value}",
        target_ref=f"{entity_type.value}:{entity_id}",
        request_payload={
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "action_id": action_id,
            "payload": payload,
        },
        trace_id=command_id,
    )
    foundation_ctx = {
        "idempotency_record": idempotency_record.to_dict(),
        "audit_action": audit_action.to_dict(),
    }
    audit_record["foundation"] = foundation_ctx
    command_store.submit_command(
        command_id=command_id,
        command_type=command_type,
        target=target,
        submitted_at=submitted_at,
        params={"action_id": action_id, **payload},
        audit_context=audit_record,
        foundation_context=foundation_ctx,
    )
    result = _project_final_command_response(
        command_id=command_id,
        command=command_type,
        accepted_at=submitted_at,
        status=CommandStatus.SUBMITTED,
        staleness_warning=staleness_warning,
    )
    _GOV_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


# -- Governance reviews ------------------------------------------------------

@app.get("/bff/reviews")
async def bff_list_reviews(
    item_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: governance review queue list (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    item_types = [v.strip() for v in item_type.split(",") if v.strip()] if item_type else None
    risk_levels = [v.strip() for v in risk_level.split(",") if v.strip()] if risk_level else None
    statuses = [v.strip() for v in status.split(",") if v.strip()] if status else None

    items = read_store.list_governance_review_queue_items(
        item_types=item_types,
        risk_levels=risk_levels,
        statuses=statuses,
    )
    review_queue_surface = _dataset_surface_status("governance_review_queue_items", snapshot_at=snapshot_at)
    if review_queue_surface.get("status") == "unavailable":
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"review_queue": review_queue_surface}
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness

    return {
        "items": items,
        "page_info": {"next_page_token": next_page_token},
        "meta": meta,
    }


@app.post("/bff/reviews", status_code=202)
async def bff_create_review(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit a new governance review item."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    review_id = str(payload.get("review_id") or payload.get("id") or uuid.uuid4())
    return _gov_bff_action_command(
        ObjectType.REVIEW, review_id, "submit", resolved_key, identity, payload, CommandType.REVIEW_ACTION
    )


@app.get("/bff/reviews/{review_id}")
async def bff_get_review(
    review_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a specific governance review queue item by ID."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    items = read_store.list_governance_review_queue_items()
    clean_id = review_id.strip()
    match = next(
        (item for item in items if str(item.get("item_id") or item.get("id") or "") == clean_id),
        None,
    )
    if not match:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Review item not found",
            f"Review {review_id} does not exist in the governance review queue",
        )

    return {
        "data": match,
        "meta": {
            "snapshot_at": snapshot_at,
            "correlation_id": clean_id,
            "staleness": _meta_staleness(),
        },
    }


@app.post("/bff/reviews/{review_id}/actions/{action_id}", status_code=202)
async def bff_review_action(
    review_id: str,
    action_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit an action (approve/reject/escalate) against a governance review item."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    clean_id = review_id.strip()
    return _gov_bff_action_command(
        ObjectType.REVIEW, clean_id, action_id, resolved_key, identity, payload, CommandType.REVIEW_ACTION
    )


@app.get("/bff/reviews/{review_id}/validators")
async def bff_review_validators(
    review_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list validators assigned to a governance review item."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    items = read_store.list_governance_review_queue_items()
    clean_id = review_id.strip()
    match = next(
        (item for item in items if str(item.get("item_id") or item.get("id") or "") == clean_id),
        None,
    )
    validators: List[Dict[str, Any]] = []
    if match:
        review_summary = match.get("review_summary") or {}
        validators = review_summary.get("validators") or []

    return {
        "review_id": clean_id,
        "validators": validators,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.get("/bff/reviews/{review_id}/audit")
async def bff_review_audit(
    review_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get the audit trail for a governance review item."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    clean_id = review_id.strip()
    events = _list_governance_audit_events()
    item_events = [
        e for e in events
        if str(e.get("target_id") or e.get("item_id") or "") == clean_id
        and str(e.get("target_type") or "") in {"Review", "GovernanceReviewItem"}
    ]

    return {
        "review_id": clean_id,
        "events": item_events,
        "meta": {
            "snapshot_at": snapshot_at,
            "correlation_id": clean_id,
            "staleness": _meta_staleness(),
        },
    }


@app.get("/bff/approvals/{approval_id}/evidence")
async def bff_approval_evidence(
    approval_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get evidence records attached to a governance approval decision."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    clean_id = approval_id.strip()
    decision = read_store.get_approval_decision(clean_id)
    if not decision:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Approval decision not found",
            f"Approval {approval_id} does not exist",
        )

    raw_refs = list((decision.get("evidence_refs") or decision.get("evidence") or []))
    try:
        capabilities = _capabilities_for_identity(identity)
    except Exception:
        capabilities = None
    processed_refs, redacted_count = redact_evidence_refs(identity, raw_refs, capabilities=capabilities)

    return {
        "approval_id": clean_id,
        "evidence": processed_refs,
        "correlation_id": decision.get("correlation_id") or decision.get("decision_id") or decision.get("id") or clean_id,
        "audit_ref": decision.get("audit_ref") or {
            "target_type": "ApprovalDecision",
            "target_id": clean_id,
            "href": f"/bff/audit/entities/ApprovalDecision/{clean_id}",
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "redacted_count": redacted_count,
            "staleness": _meta_staleness(),
        },
    }


# -- Deployments -------------------------------------------------------------

@app.get("/bff/deployments")
async def bff_list_deployments(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list deployment plans (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    plans = read_store.list_deployment_plans()
    if status:
        requested_statuses = {v.strip().lower() for v in status.split(",") if v.strip()}
        plans = [p for p in plans if str(p.get("status") or "").lower() in requested_statuses]
    total = len(plans)

    surface = _dataset_surface_status("deployment_plans", snapshot_at=snapshot_at)
    if surface.get("status") == "unavailable":
        plans = []
        next_page_token = None
        total = 0
    else:
        plans, next_page_token = _page_slice(plans, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"deployments": surface}
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness

    return {
        "data": plans,
        "items": plans,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": meta,
    }


@app.get("/bff/deployments/{deployment_id}")
async def bff_get_deployment(
    deployment_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a deployment plan detail."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = deployment_id.strip()
    plan = read_store.get_deployment_plan(clean_id)
    if not plan:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment not found",
            f"Deployment plan {deployment_id} does not exist",
        )

    snapshot_at = utc_now()
    decision = read_store.get_approval_decision(plan.get("approval_decision_id"))
    review = read_store.get_review_summary(clean_id)

    return {
        "data": {
            **plan,
            "approval_decision": decision or {},
            "review": review or {},
        },
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.post("/bff/deployments/{deployment_id}/actions/{action_id}", status_code=202)
async def bff_deployment_action(
    deployment_id: str,
    action_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit an action against a deployment plan."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    clean_id = deployment_id.strip()
    plan = read_store.get_deployment_plan(clean_id)
    if not plan:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Deployment not found",
            f"Deployment plan {deployment_id} does not exist",
        )
    return _gov_bff_action_command(
        ObjectType.DEPLOYMENT, clean_id, action_id, resolved_key, identity, payload, CommandType.DEPLOYMENT_ACTION
    )


# -- Runtimes ----------------------------------------------------------------

@app.get("/bff/runtimes")
async def bff_list_runtimes(
    status: Optional[str] = None,
    deployment_stage: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list runtime bindings (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    bindings = read_store.list_runtime_bindings()
    if status:
        requested_statuses = {v.strip().lower() for v in status.split(",") if v.strip()}
        bindings = [b for b in bindings if str(b.get("status") or "").lower() in requested_statuses]
    if deployment_stage:
        requested_stages = {v.strip().lower() for v in deployment_stage.split(",") if v.strip()}
        bindings = [
            b for b in bindings
            if str(b.get("deployment_stage") or b.get("deployment_mode") or "").lower() in requested_stages
        ]

    surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
    if surface.get("status") == "unavailable":
        bindings = []
        next_page_token = None
    else:
        total = len(bindings)
        bindings, next_page_token = _page_slice(bindings, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["total"] = 0 if surface.get("status") == "unavailable" else total
    meta["surfaces"] = {"runtimes": surface}
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness

    return {
        "items": bindings,
        "page_info": {"next_page_token": next_page_token},
        "meta": meta,
    }


@app.get("/bff/runtimes/{runtime_id}")
async def bff_get_runtime(
    runtime_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a runtime binding detail by runtime_id."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    surface = _dataset_surface_status("runtime_bindings", snapshot_at=snapshot_at)
    clean_id = runtime_id.strip()
    binding = read_store.get_runtime_binding_by_runtime_id(clean_id)
    if not binding:
        # fall back to binding_id lookup
        binding = read_store.get_runtime_binding(clean_id)
    if not binding:
        _raise_if_read_surface_unavailable(surface, label="Runtime")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime not found",
            f"Runtime {runtime_id} does not exist",
        )

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"runtime": surface}
    return {
        "data": binding,
        "meta": meta,
    }


@app.post("/bff/runtimes/{runtime_id}/actions/{action_id}", status_code=202)
async def bff_runtime_action(
    runtime_id: str,
    action_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit an action against a runtime binding."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    clean_id = runtime_id.strip()
    binding = read_store.get_runtime_binding_by_runtime_id(clean_id)
    if not binding:
        binding = read_store.get_runtime_binding(clean_id)
    if not binding:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Runtime not found",
            f"Runtime {runtime_id} does not exist",
        )
    return _gov_bff_action_command(
        ObjectType.RUNTIME_BINDING, clean_id, action_id, resolved_key, identity, payload, CommandType.RUNTIME_ACTION
    )


# -- Risk alerts -------------------------------------------------------------

@app.get("/bff/risk/alerts")
async def bff_list_risk_alerts(
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list risk/operator alerts (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _build_operator_alerts_payload(snapshot_at)


@app.get("/bff/risk/alerts/{alert_id}")
async def bff_get_risk_alert(
    alert_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a specific risk alert by ID."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    payload = _build_operator_alerts_payload(snapshot_at)
    clean_id = alert_id.strip()
    match = next(
        (a for a in payload.get("alerts", []) if str(a.get("alert_id") or a.get("id") or "") == clean_id),
        None,
    )
    if not match:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Risk alert not found",
            f"Alert {alert_id} does not exist",
        )
    return {
        "data": match,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.post("/bff/risk/alerts/{alert_id}/actions/{action_id}", status_code=202)
async def bff_risk_alert_action(
    alert_id: str,
    action_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit an action against a risk alert."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    clean_id = alert_id.strip()
    return _gov_bff_action_command(
        ObjectType.RISK_ALERT, clean_id, action_id, resolved_key, identity, payload, CommandType.RISK_ALERT_ACTION
    )


# -- Incidents ---------------------------------------------------------------

@app.get("/bff/incidents")
async def bff_list_incidents(
    status: Optional[str] = None,
    severity: Optional[str] = None,
    affected_pool_id: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list incidents (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    incidents = _list_bff_incidents(status=status, severity=severity, affected_pool_id=affected_pool_id)
    total = len(incidents)
    if surface.get("status") == "unavailable":
        incidents = []
        next_page_token = None
        total = 0
    else:
        incidents, next_page_token = _page_slice(incidents, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"incidents": surface}
    meta["total"] = total
    staleness = _meta_staleness()
    if staleness is not None:
        meta["staleness"] = staleness
    degradation_reason = _surface_degradation_reason(
        surface,
        degraded_reason="Incident list is degraded and may be stale.",
        unavailable_reason="Incident list is currently unavailable.",
    )
    if degradation_reason is not None:
        meta["degradation"] = {"reason": degradation_reason}

    return {
        "data": incidents,
        "items": incidents,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": meta,
    }


@app.post("/bff/incidents", status_code=201)
async def bff_create_incident(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: create a new incident record."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    _reject_body_idempotency_key(payload)

    existing = _GOV_BFF_IDEMPOTENCY.get(resolved_key)
    req_hash = _stable_json_hash(payload)
    if existing is not None:
        if existing.get("request_hash") != req_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return existing["result"]

    incident_id = str(payload.get("incident_id") or payload.get("id") or uuid.uuid4())
    submitted_at = utc_now()
    result = _project_bff_incident_case({
        **payload,
        "id": incident_id,
        "incident_id": incident_id,
        "status": payload.get("status") or "open",
        "submitted_at": submitted_at,
        "created_at": payload.get("created_at") or payload.get("opened_at") or submitted_at,
        "updated_at": submitted_at,
        "submitted_by": identity.operator_id,
        "title": payload.get("title") or "Untitled Incident",
        "severity": payload.get("severity") or "medium",
        "capital_pool_id": payload.get("capital_pool_id") or payload.get("affected_pool_id"),
        "runtime_id": payload.get("runtime_id"),
        "correlation_id": payload.get("correlation_id") or incident_id,
        "trace_id": payload.get("trace_id") or payload.get("correlation_id") or incident_id,
        "audit_ref": {
            "target_type": "Incident",
            "target_id": incident_id,
            "href": f"/bff/audit/entities/Incident/{incident_id}",
        },
        "meta": {"idempotency_key": resolved_key},
    })
    _GOV_BFF_INCIDENT_OVERLAY[incident_id] = result
    _GOV_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": req_hash, "result": result}
    return result


@app.get("/bff/incidents/{incident_id}")
async def bff_get_incident(
    incident_id: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a specific incident by ID."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = incident_id.strip()
    snapshot_at = utc_now()
    surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    incident = _get_bff_incident(clean_id)
    if not incident:
        _raise_if_read_surface_unavailable(surface, label="Incident")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )

    return {
        "data": incident,
        "meta": _read_surface_meta("incidents", "incident", snapshot_at=snapshot_at, surface=surface),
    }


@app.post("/bff/incidents/{incident_id}/actions/{action_id}", status_code=202)
async def bff_incident_action(
    incident_id: str,
    action_id: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit an action against an incident."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    clean_id = incident_id.strip()
    snapshot_at = utc_now()
    surface = _dataset_surface_status("incidents", snapshot_at=snapshot_at)
    incident = _get_bff_incident(clean_id)
    if not incident:
        _raise_if_read_surface_unavailable(surface, label="Incident")
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Incident not found",
            f"Incident {incident_id} does not exist",
        )
    return _gov_bff_action_command(
        ObjectType.INCIDENT, clean_id, action_id, resolved_key, identity, payload, CommandType.INCIDENT_ACTION
    )


# -- Alerts source-reference alias -------------------------------------------

@app.get("/bff/alerts")
async def bff_list_alerts(
    authorization: Optional[str] = Header(default=None),
):
    """BFF: source-reference compatibility alias for /bff/risk/alerts."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    return _build_operator_alerts_payload(snapshot_at)


# -- Audit -------------------------------------------------------------------

@app.get("/bff/audit")
async def bff_list_audit(
    actor: Optional[str] = None,
    action_type: Optional[str] = None,
    target_type: Optional[str] = None,
    from_: Optional[datetime] = Query(default=None, alias="from"),
    to: Optional[datetime] = Query(default=None),
    page_token: Optional[str] = None,
    page_size: int = Query(default=50, ge=1, le=500),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: governance audit event list with actor/type/time filters and pagination."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    action_types = [v.strip() for v in action_type.split(",") if v.strip()] if action_type else None
    events = _list_governance_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
        from_ts=from_,
        to_ts=to,
    )
    total = len(events)
    page_items, next_page_token = _page_slice(events, page_token, page_size)
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": _read_surface_meta(
            "governance_audit_events", "audit_list",
            snapshot_at=snapshot_at, total=total,
        ),
    }


@app.get("/bff/audit/events")
async def bff_list_audit_events(
    actor: Optional[str] = None,
    action_type: Optional[str] = None,
    target_type: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=50, ge=1, le=500),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list governance audit events (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    action_types = [v.strip() for v in action_type.split(",") if v.strip()] if action_type else None

    from_dt = _parse_rfc3339_header(from_ts) if from_ts else None
    to_dt = _parse_rfc3339_header(to_ts) if to_ts else None

    events = _list_governance_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
        from_ts=from_dt,
        to_ts=to_dt,
    )

    events, next_page_token = _page_slice(events, page_token, page_size)
    return {
        "events": events,
        "page_info": {"next_page_token": next_page_token},
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.get("/bff/audit/entities/{entity_type}/{entity_id}")
async def bff_get_entity_audit(
    entity_type: str,
    entity_id: str,
    page_token: Optional[str] = None,
    page_size: int = Query(default=50, ge=1, le=500),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get audit trail for a specific entity."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    clean_type = entity_type.strip()
    clean_id = entity_id.strip()

    events = _list_governance_audit_events(target_type=clean_type)
    entity_events = [
        e for e in events
        if str(e.get("target_id") or e.get("entity_id") or "") == clean_id
    ]
    entity_events, next_page_token = _page_slice(entity_events, page_token, page_size)

    return {
        "entity_type": clean_type,
        "entity_id": clean_id,
        "events": entity_events,
        "page_info": {"next_page_token": next_page_token},
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.get("/bff/audit/export")
async def bff_audit_export(
    actor: Optional[str] = None,
    action_type: Optional[str] = None,
    target_type: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: export governance audit events as a structured payload."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    action_types = [v.strip() for v in action_type.split(",") if v.strip()] if action_type else None
    from_dt = _parse_rfc3339_header(from_ts) if from_ts else None
    to_dt = _parse_rfc3339_header(to_ts) if to_ts else None

    events = _list_governance_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
        from_ts=from_dt,
        to_ts=to_dt,
    )

    return {
        "events": events,
        "total": len(events),
        "exported_at": snapshot_at,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


# -- Command confirmations ---------------------------------------------------

@app.post("/bff/command-confirmations", status_code=202)
async def bff_command_confirmation(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """
    BFF: submit a command confirmation token.

    Clients that received a CONFIRM_TOKEN_REQUIRED precondition error must
    resubmit with a valid confirm_token in the body alongside this route to
    proceed past the confirmation gate.
    """
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass

    _reject_body_idempotency_key(payload)

    confirm_token = str(payload.get("confirm_token") or "").strip()
    if not confirm_token:
        raise _bff_error(
            400,
            ErrorCode.CONFIRM_TOKEN_REQUIRED,
            "confirm_token is required",
            "Command confirmation requires a non-empty confirm_token in the request body",
            precondition_failed="confirm_token_missing",
            suggestion="Include the confirm_token issued by the original precondition error response",
        )

    original_command_id = str(payload.get("command_id") or "").strip()
    if not original_command_id:
        raise _bff_error(
            400,
            ErrorCode.INVALID_REQUEST,
            "command_id is required",
            "Command confirmation requires the original command_id being confirmed",
            precondition_failed="command_id_missing",
            suggestion="Include the command_id from the original command submission",
        )

    existing = _GOV_BFF_IDEMPOTENCY.get(resolved_key)
    req_hash = _stable_json_hash({"command_id": original_command_id, "confirm_token": confirm_token})
    if existing is not None:
        if existing.get("request_hash") != req_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key already used with a different payload",
                f"Key {resolved_key!r} is bound to a different confirmation request",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original confirmation unchanged",
            )
        return existing["result"]

    staleness_warning = _check_read_surface_state()
    confirmation_id = str(uuid.uuid4())
    confirmed_at = utc_now()
    result = {
        "confirmation_id": confirmation_id,
        "command_id": original_command_id,
        "status": "accepted",
        "confirmed_at": confirmed_at,
        "confirmed_by": identity.operator_id,
    }
    if staleness_warning is not None:
        result["staleness_warning"] = {
            "read_surface_state": staleness_warning.read_surface_state,
            "message": staleness_warning.message,
        }
    _GOV_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": req_hash, "result": result}
    return result


# --------------------------------------------------------------------------- #
# BFF-LUV-GAP-004: Evolution Programs, Experiments, Jobs, Events
# --------------------------------------------------------------------------- #

_GOV_BFF_EVOLUTION_PROGRAM_OVERLAY: Dict[str, Dict[str, Any]] = {}
_GOV_BFF_EXPERIMENT_OVERLAY: Dict[str, Dict[str, Any]] = {}
_GOV_BFF_JOB_OVERLAY: Dict[str, Dict[str, Any]] = {}


def _get_bff_evolution_program(program_id: str) -> Optional[Dict[str, Any]]:
    overlay = _GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.get(program_id)
    if overlay is not None:
        return dict(overlay)
    program = read_store.get_evolution_program(program_id)
    return dict(program) if program else None


def _list_bff_evolution_programs(*, status: Optional[str] = None) -> List[Dict[str, Any]]:
    programs_by_id: Dict[str, Dict[str, Any]] = {}
    for program in read_store.list_evolution_programs():
        program_id = str(program.get("program_id") or program.get("id") or "").strip()
        if program_id:
            programs_by_id[program_id] = dict(program)
    for program_id, program in _GOV_BFF_EVOLUTION_PROGRAM_OVERLAY.items():
        programs_by_id[program_id] = dict(program)
    programs = list(programs_by_id.values())
    if status:
        requested = {s.strip().lower() for s in status.split(",") if s.strip()}
        programs = [p for p in programs if str(p.get("status") or "").lower() in requested]
    return sorted(programs, key=lambda p: str(p.get("created_at") or ""), reverse=True)


def _get_bff_experiment(experiment_id: str) -> Optional[Dict[str, Any]]:
    overlay = _GOV_BFF_EXPERIMENT_OVERLAY.get(experiment_id)
    if overlay:
        return _bff_experiment_with_analysis_links(overlay)
    experiment = read_store.get_research_experiment(experiment_id)
    if not experiment:
        return None
    return _bff_experiment_with_analysis_links(experiment)


def _bff_experiment_with_analysis_links(experiment: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(experiment)
    experiment_id = str(record.get("experiment_id") or record.get("id") or "").strip()
    if not experiment_id:
        return record
    analyses = read_store.list_research_analyses(experiment_id=experiment_id)
    analysis_links: List[Dict[str, Any]] = []
    for analysis in analyses:
        analysis_id = str(analysis.get("analysis_id") or analysis.get("id") or "").strip()
        if not analysis_id:
            continue
        analysis_links.append(
            {
                "analysis_id": analysis_id,
                "ticket_id": analysis.get("ticket_id"),
                "status": analysis.get("status"),
                "detail": f"/bff/research-analyses/{analysis_id}",
                "api_detail": f"/api/v1/research/analysis/{analysis_id}",
            }
        )
    record["analysis_links"] = analysis_links
    record["analysis_ids"] = [link["analysis_id"] for link in analysis_links]
    return record


def _list_bff_experiments(*, status: Optional[str] = None) -> List[Dict[str, Any]]:
    items = list(read_store.list_research_experiments())
    seen = {str(e.get("experiment_id") or e.get("id") or "") for e in items}
    for eid, exp in _GOV_BFF_EXPERIMENT_OVERLAY.items():
        if eid not in seen:
            items.append(dict(exp))
    if status:
        requested = {s.strip().lower() for s in status.split(",") if s.strip()}
        items = [e for e in items if str(e.get("status") or "").lower() in requested]
    return sorted(items, key=lambda e: str(e.get("created_at") or e.get("queued_at") or ""), reverse=True)


def _get_bff_job(job_id: str) -> Optional[Dict[str, Any]]:
    overlay = _GOV_BFF_JOB_OVERLAY.get(job_id)
    if overlay is not None:
        return dict(overlay)
    return _find_record_by_id(_read_store_fixture_records("jobs"), job_id, ("job_id", "id"))


def _list_bff_jobs(*, status: Optional[str] = None) -> List[Dict[str, Any]]:
    jobs = _merge_registry_records(
        _read_store_fixture_records("jobs"),
        [dict(record) for record in _GOV_BFF_JOB_OVERLAY.values()],
        ("job_id", "id"),
    )
    if status:
        requested = {s.strip().lower() for s in status.split(",") if s.strip()}
        jobs = [j for j in jobs if str(j.get("status") or "").lower() in requested]
    return sorted(jobs, key=lambda j: str(j.get("created_at") or j.get("submitted_at") or ""), reverse=True)


# -- Evolution Programs ------------------------------------------------------

@app.get("/bff/evolution-programs")
async def bff_list_evolution_programs(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list evolution programs (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    programs = _list_bff_evolution_programs(status=status)
    surface = _dataset_surface_status(
        "evolution_decisions",
        snapshot_at=snapshot_at,
        has_data=bool(programs) or None,
    )
    if surface.get("status") == "unavailable" and not programs:
        next_page_token = None
    else:
        programs, next_page_token = _page_slice(programs, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"evolution_programs": surface}
    return {
        "items": programs,
        "page_info": {"next_page_token": next_page_token},
        "meta": meta,
    }


@app.post("/bff/evolution-programs", status_code=201)
async def bff_create_evolution_program(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: create an evolution program record."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    _reject_body_idempotency_key(payload)

    existing = _GOV_BFF_IDEMPOTENCY.get(resolved_key)
    req_hash = _stable_json_hash(payload)
    if existing is not None:
        if existing.get("request_hash") != req_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return existing["result"]

    program_id = str(payload.get("program_id") or payload.get("id") or uuid.uuid4())
    created_at = utc_now()
    result = {
        "id": program_id,
        "program_id": program_id,
        "name": payload.get("name") or "Untitled Evolution Program",
        "status": "active",
        "created_at": created_at,
        "updated_at": created_at,
        "created_by": identity.operator_id,
        "description": payload.get("description") or "",
        "config": payload.get("config") or {},
        "artifact_links": [],
        "meta": {"idempotency_key": resolved_key},
    }
    _GOV_BFF_EVOLUTION_PROGRAM_OVERLAY[program_id] = result
    _GOV_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": req_hash, "result": result}
    return result


@app.get("/bff/evolution-programs/{programId}")
async def bff_get_evolution_program(
    programId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a specific evolution program by ID."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = programId.strip()
    program = _get_bff_evolution_program(clean_id)
    if not program:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {programId} does not exist",
        )

    snapshot_at = utc_now()
    return {
        "data": program,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.patch("/bff/evolution-programs/{programId}")
async def bff_patch_evolution_program(
    programId: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: update an evolution program."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)

    clean_id = programId.strip()
    program = _get_bff_evolution_program(clean_id)
    if not program:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {programId} does not exist",
        )

    patch: Dict[str, Any] = {}
    try:
        patch = await request.json()
    except Exception:
        pass

    allowed_fields = {"name", "description", "status", "config"}
    updated = dict(program)
    for field in allowed_fields:
        if field in patch:
            updated[field] = patch[field]
    updated["updated_at"] = utc_now()
    _GOV_BFF_EVOLUTION_PROGRAM_OVERLAY[clean_id] = updated

    snapshot_at = utc_now()
    return {
        "data": updated,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.get("/bff/evolution-programs/{programId}/runs")
async def bff_list_evolution_program_runs(
    programId: str,
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list evolution decision runs for a program."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = programId.strip()
    program = _get_bff_evolution_program(clean_id)
    if not program:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {programId} does not exist",
        )

    snapshot_at = utc_now()
    decisions = read_store.list_evolution_decisions(status=status)
    runs = [
        {
            "run_id": str(d.get("id") or d.get("decision_id") or ""),
            "program_id": clean_id,
            "status": d.get("status") or "pending",
            "action_type": d.get("action_type") or "",
            "risk_level": d.get("risk_level") or "",
            "created_at": d.get("created_at") or "",
            "artifact_links": d.get("artifact_links") or [],
        }
        for d in decisions
        if not d.get("program_id") or d.get("program_id") == clean_id
    ]

    surface = _dataset_surface_status("evolution_decisions", snapshot_at=snapshot_at)
    if surface.get("status") == "unavailable":
        runs = []
        next_page_token = None
    else:
        runs, next_page_token = _page_slice(runs, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"evolution_runs": surface}
    return {
        "items": runs,
        "page_info": {"next_page_token": next_page_token},
        "meta": meta,
    }


@app.get("/bff/evolution-programs/{programId}/candidates")
async def bff_list_evolution_program_candidates(
    programId: str,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list evolution candidates for a program."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = programId.strip()
    program = _get_bff_evolution_program(clean_id)
    if not program:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {programId} does not exist",
        )

    snapshot_at = utc_now()
    candidates: List[Dict[str, Any]] = []
    for cand in program.get("candidates") or []:
        candidates.append(dict(cand))

    candidates, next_page_token = _page_slice(candidates, page_token, page_size)
    meta = _snapshot_meta(snapshot_at)
    return {
        "items": candidates,
        "page_info": {"next_page_token": next_page_token},
        "meta": meta,
    }


@app.post("/bff/evolution-programs/{programId}/actions/{actionId}", status_code=202)
async def bff_evolution_program_action(
    programId: str,
    actionId: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit an action against an evolution program."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    clean_id = programId.strip()
    program = _get_bff_evolution_program(clean_id)
    if not program:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Evolution program not found",
            f"Evolution program {programId} does not exist",
        )
    return _gov_bff_action_command(
        ObjectType.EVOLUTION_PROGRAM, clean_id, actionId, resolved_key, identity, payload,
        CommandType.EVOLUTION_PROGRAM_ACTION,
    )


# -- Experiments -------------------------------------------------------------

@app.get("/bff/experiments")
async def bff_list_experiments_compat(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list experiments (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    items = _list_bff_experiments(status=status)
    source = read_store.dataset_source("research_experiments")
    surface = _dataset_surface_status("research_experiments", snapshot_at=snapshot_at, source=source)
    if surface.get("status") == "unavailable" and not _GOV_BFF_EXPERIMENT_OVERLAY:
        items = []
        next_page_token = None
    else:
        items, next_page_token = _page_slice(items, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"experiments": surface}
    return {
        "items": items,
        "page_info": {"next_page_token": next_page_token},
        "meta": meta,
    }


@app.post("/bff/experiments", status_code=201)
async def bff_create_experiment_compat(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: create an experiment (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    _reject_body_idempotency_key(payload)

    existing = _GOV_BFF_IDEMPOTENCY.get(resolved_key)
    req_hash = _stable_json_hash(payload)
    if existing is not None:
        if existing.get("request_hash") != req_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        return existing["result"]

    experiment_id = str(payload.get("experiment_id") or payload.get("id") or uuid.uuid4())
    created_at = utc_now()
    result = {
        "id": experiment_id,
        "experiment_id": experiment_id,
        "name": payload.get("name") or payload.get("experiment_name") or "Untitled Experiment",
        "status": "queued",
        "created_at": created_at,
        "queued_at": created_at,
        "updated_at": created_at,
        "created_by": identity.operator_id,
        "description": payload.get("description") or "",
        "config": payload.get("config") or {},
        "artifact_links": [],
        "logs": [],
        "metrics": {},
        "meta": {"idempotency_key": resolved_key},
    }
    _GOV_BFF_EXPERIMENT_OVERLAY[experiment_id] = result
    _GOV_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": req_hash, "result": result}
    return result


@app.get("/bff/experiments/{experimentId}")
async def bff_get_experiment_compat(
    experimentId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a specific experiment by ID."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = experimentId.strip()
    experiment = _get_bff_experiment(clean_id)
    if not experiment:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experimentId} does not exist",
        )

    snapshot_at = utc_now()
    return {
        "data": experiment,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.post("/bff/experiments/{experimentId}/actions/{actionId}", status_code=202)
async def bff_experiment_action(
    experimentId: str,
    actionId: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit an action against an experiment."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    clean_id = experimentId.strip()
    experiment = _get_bff_experiment(clean_id)
    if not experiment:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experimentId} does not exist",
        )
    return _gov_bff_action_command(
        ObjectType.EXPERIMENT, clean_id, actionId, resolved_key, identity, payload,
        CommandType.EXPERIMENT_ACTION,
    )


@app.get("/bff/experiments/{experimentId}/logs")
async def bff_get_experiment_logs(
    experimentId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get logs for a specific experiment."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = experimentId.strip()
    experiment = _get_bff_experiment(clean_id)
    if not experiment:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experimentId} does not exist",
        )

    snapshot_at = utc_now()
    logs = list(experiment.get("logs") or [])
    return {
        "experiment_id": clean_id,
        "logs": logs,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.get("/bff/experiments/{experimentId}/metrics")
async def bff_get_experiment_metrics(
    experimentId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get metrics for a specific experiment."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = experimentId.strip()
    experiment = _get_bff_experiment(clean_id)
    if not experiment:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experimentId} does not exist",
        )

    snapshot_at = utc_now()
    metrics = dict(experiment.get("metrics") or {})
    return {
        "experiment_id": clean_id,
        "metrics": metrics,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.get("/bff/experiments/{experimentId}/artifacts")
async def bff_get_experiment_artifacts(
    experimentId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get artifact links for a specific experiment."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = experimentId.strip()
    experiment = _get_bff_experiment(clean_id)
    if not experiment:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Experiment not found",
            f"Experiment {experimentId} does not exist",
        )

    snapshot_at = utc_now()
    artifacts = list(experiment.get("artifact_links") or [])
    return {
        "experiment_id": clean_id,
        "artifacts": artifacts,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


# -- Jobs --------------------------------------------------------------------

@app.get("/bff/jobs")
async def bff_list_jobs(
    status: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=20, ge=1, le=200),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list background jobs (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    jobs = _list_bff_jobs(status=status)
    surface = _dataset_surface_status(
        "jobs",
        snapshot_at=snapshot_at,
        has_data=bool(jobs) or None,
    )
    if surface.get("status") == "unavailable" and not jobs:
        next_page_token = None
    else:
        jobs, next_page_token = _page_slice(jobs, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"jobs": surface}
    return {
        "items": jobs,
        "page_info": {"next_page_token": next_page_token},
        "meta": meta,
    }


@app.get("/bff/jobs/{jobId}")
async def bff_get_job(
    jobId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get a specific job by ID."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = jobId.strip()
    job = _get_bff_job(clean_id)
    if not job:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Job not found",
            f"Job {jobId} does not exist",
        )

    snapshot_at = utc_now()
    return {
        "data": job,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.get("/bff/jobs/{jobId}/logs")
async def bff_get_job_logs(
    jobId: str,
    authorization: Optional[str] = Header(default=None),
):
    """BFF: get logs for a specific job."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    clean_id = jobId.strip()
    job = _get_bff_job(clean_id)
    if not job:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Job not found",
            f"Job {jobId} does not exist",
        )

    snapshot_at = utc_now()
    logs = list(job.get("logs") or [])
    return {
        "job_id": clean_id,
        "status": job.get("status") or "unknown",
        "progress": job.get("progress") or {},
        "logs": logs,
        "meta": {"snapshot_at": snapshot_at, "staleness": _meta_staleness()},
    }


@app.post("/bff/jobs/{jobId}/actions/{actionId}", status_code=202)
async def bff_job_action(
    jobId: str,
    actionId: str,
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: submit an action against a background job."""
    identity = _extract_identity(authorization)
    _require_operator_role(identity)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        pass
    clean_id = jobId.strip()
    job = _get_bff_job(clean_id)
    if not job:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Job not found",
            f"Job {jobId} does not exist",
        )
    return _gov_bff_action_command(
        ObjectType.JOB, clean_id, actionId, resolved_key, identity, payload,
        CommandType.JOB_ACTION,
    )


# -- Events list (non-stream) ------------------------------------------------

@app.get("/bff/events")
async def bff_list_events(
    event_type: Optional[str] = None,
    actor: Optional[str] = None,
    target_type: Optional[str] = None,
    page_token: Optional[str] = None,
    page_size: int = Query(default=50, ge=1, le=500),
    authorization: Optional[str] = Header(default=None),
):
    """BFF: list recent system/audit events (execute-plans compatibility surface)."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    snapshot_at = utc_now()
    action_types = [event_type] if event_type else None
    events = _list_governance_audit_events(
        actor=actor,
        action_types=action_types,
        target_type=target_type,
    )
    surface = _dataset_surface_status("audit_log", snapshot_at=snapshot_at)
    if surface.get("status") == "unavailable":
        events = []
        next_page_token = None
    else:
        events, next_page_token = _page_slice(events, page_token, page_size)

    meta = _snapshot_meta(snapshot_at)
    meta["surfaces"] = {"events": surface}
    return {
        "items": events,
        "page_info": {"next_page_token": next_page_token},
        "meta": meta,
    }


_BFF_GAP004_ROUTE_PATHS = {
    "/bff/evolution-programs",
    "/bff/evolution-programs/{}",
    "/bff/evolution-programs/{}/runs",
    "/bff/evolution-programs/{}/candidates",
    "/bff/evolution-programs/{}/actions/{}",
    "/bff/experiments",
    "/bff/experiments/{}",
    "/bff/experiments/{}/actions/{}",
    "/bff/experiments/{}/logs",
    "/bff/experiments/{}/metrics",
    "/bff/experiments/{}/artifacts",
    "/bff/jobs",
    "/bff/jobs/{}",
    "/bff/jobs/{}/logs",
    "/bff/jobs/{}/actions/{}",
    "/bff/events",
}


def _bff_gap004_route_path_key(path: str) -> str:
    return re.sub(r"\{[^}/]+\}", "{}", path)


def _bff_gap004_route_methods(route: Any) -> List[str]:
    return sorted(
        method
        for method in getattr(route, "methods", set())
        if method not in {"HEAD", "OPTIONS"}
    )


def _prefer_latest_bff_gap004_routes() -> None:
    """Keep the task-scoped GAP-004 handlers authoritative.

    Earlier execute-plans slices registered placeholder handlers for the same
    compatibility paths. FastAPI matches the first route, so prune the shadowed
    registrations and keep the latest GAP-004 route for each method/path key.
    """
    preferred: Dict[tuple[str, str], Any] = {}
    for route in app.router.routes:
        path_key = _bff_gap004_route_path_key(getattr(route, "path", ""))
        if path_key not in _BFF_GAP004_ROUTE_PATHS:
            continue
        for method in _bff_gap004_route_methods(route):
            preferred[(method, path_key)] = route

    if not preferred:
        return

    pruned = []
    for route in app.router.routes:
        path_key = _bff_gap004_route_path_key(getattr(route, "path", ""))
        methods = _bff_gap004_route_methods(route)
        route_keys = [(method, path_key) for method in methods if path_key in _BFF_GAP004_ROUTE_PATHS]
        if route_keys and all(preferred.get(key) is not route for key in route_keys):
            continue
        pruned.append(route)
    app.router.routes[:] = pruned


_prefer_latest_bff_gap004_routes()


# --------------------------------------------------------------------------- #
# Execute-plans semantic completion aliases (BFF-LUV-SEM-002/005/final catalog)
# --------------------------------------------------------------------------- #

_FINAL_CONTRACT_IDEMPOTENCY: Dict[str, Dict[str, Any]] = {}

_ACTION_ADAPTER_ENTITY_SPECS: Dict[str, Dict[str, Any]] = {
    "strategy": {
        "target_type": ObjectType.STRATEGY,
        "command_type": CommandType.STRATEGY_ACTION,
        "audit_namespace": "strategy",
    },
    "persona": {
        "target_type": ObjectType.PERSONA,
        "command_type": CommandType.PERSONA_ACTION,
        "audit_namespace": "persona",
    },
    "capital-pool": {
        "target_type": ObjectType.CAPITAL_POOL,
        "command_type": CommandType.CAPITAL_POOL_ACTION,
        "audit_namespace": "capitalpool",
    },
    "rebalance": {
        "target_type": ObjectType.REBALANCE,
        "command_type": CommandType.REBALANCE_ACTION,
        "audit_namespace": "rebalance",
    },
    "ranking-formula": {
        "target_type": ObjectType.RANKING_FORMULA,
        "command_type": CommandType.RANKING_FORMULA_ACTION,
        "audit_namespace": "rankingformula",
    },
    "ranking": {
        "target_type": ObjectType.RANKING,
        "command_type": CommandType.RANKING_ACTION,
        "audit_namespace": "ranking",
    },
    "deployment": {
        "target_type": ObjectType.DEPLOYMENT,
        "command_type": CommandType.DEPLOYMENT_ACTION,
        "audit_namespace": "deployment",
    },
    "runtime": {
        "target_type": ObjectType.RUNTIME,
        "command_type": CommandType.RUNTIME_ACTION,
        "audit_namespace": "runtime",
    },
    "review": {
        "target_type": ObjectType.REVIEW,
        "command_type": CommandType.REVIEW_ACTION,
        "audit_namespace": "review",
    },
    "approval": {
        "target_type": ObjectType.APPROVAL_DECISION,
        "command_type": CommandType.REVIEW_ACTION,
        "audit_namespace": "approval",
    },
    "alert": {
        "target_type": ObjectType.RISK_ALERT,
        "command_type": CommandType.RISK_ALERT_ACTION,
        "audit_namespace": "alert",
    },
    "incident": {
        "target_type": ObjectType.INCIDENT,
        "command_type": CommandType.INCIDENT_ACTION,
        "audit_namespace": "incident",
    },
    "evolution-program": {
        "target_type": ObjectType.EVOLUTION_PROGRAM,
        "command_type": CommandType.EVOLUTION_PROGRAM_ACTION,
        "audit_namespace": "evolution",
    },
    "research-experiment": {
        "target_type": ObjectType.EXPERIMENT,
        "command_type": CommandType.EXPERIMENT_ACTION,
        "audit_namespace": "research",
    },
    "experiment": {
        "target_type": ObjectType.EXPERIMENT,
        "command_type": CommandType.EXPERIMENT_ACTION,
        "audit_namespace": "research",
    },
    "job": {
        "target_type": ObjectType.JOB,
        "command_type": CommandType.JOB_ACTION,
        "audit_namespace": "job",
    },
    "tool": {
        "target_type": ObjectType.TOOL,
        "command_type": CommandType.TOOL_ACTION,
        "audit_namespace": "tool",
    },
    "mcp-server": {
        "target_type": ObjectType.MCP_SERVER,
        "command_type": CommandType.MCP_SERVER_ACTION,
        "audit_namespace": "mcpserver",
    },
    "mcp-tool": {
        "target_type": ObjectType.TOOL,
        "command_type": CommandType.TOOL_ACTION,
        "audit_namespace": "mcptool",
    },
    "skill": {
        "target_type": ObjectType.SKILL,
        "command_type": CommandType.SKILL_ACTION,
        "audit_namespace": "skill",
    },
    "artifact": {
        "target_type": ObjectType.REVIEW,
        "command_type": CommandType.REVIEW_ACTION,
        "audit_namespace": "artifact",
    },
    "channel": {
        "target_type": ObjectType.REVIEW,
        "command_type": CommandType.REVIEW_ACTION,
        "audit_namespace": "channel",
    },
}


def _normalize_action_adapter_entity_type(entity_type: str) -> str:
    return str(entity_type or "").strip().lower().replace("_", "-")


def _action_adapter_spec(entity_type: str) -> Dict[str, Any]:
    normalized = _normalize_action_adapter_entity_type(entity_type)
    spec = _ACTION_ADAPTER_ENTITY_SPECS.get(normalized)
    if spec is None:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Unsupported action entity type",
            f"/bff/actions does not admit entityType={entity_type!r}",
            precondition_failed="entity_type",
            suggestion="Submit a documented BFF action entity type from BFF_COMMAND_API_CONTRACT.md section 8",
        )
    return spec


def _action_adapter_audit_event(spec: Dict[str, Any], action_id: str) -> str:
    namespace = str(spec.get("audit_namespace") or "action").strip()
    action = str(action_id or "").strip()
    return f"{namespace}.{action}" if action else namespace


def _action_adapter_command_payload(
    *,
    entity_type: str,
    entity_id: str,
    action_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    normalized_entity_type = _normalize_action_adapter_entity_type(entity_type)
    clean_entity_id = str(entity_id or "").strip()
    clean_action_id = str(action_id or "").strip()
    if not clean_entity_id:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Action target id is required",
            "entityId must be a non-empty string",
            precondition_failed="entity_id",
        )
    if not clean_action_id:
        raise _bff_error(
            422,
            ErrorCode.INVALID_PARAMS,
            "Action id is required",
            "actionId must be a non-empty string",
            precondition_failed="action_id",
        )
    spec = _action_adapter_spec(normalized_entity_type)
    audit_event = _action_adapter_audit_event(spec, clean_action_id)
    body = dict(payload or {})
    reason = str(
        body.get("reason")
        or body.get("operator_note")
        or body.get("note")
        or audit_event
    ).strip()
    params = {
        **body,
        "action_id": clean_action_id,
        "entity_type": normalized_entity_type,
        "entity_id": clean_entity_id,
        "audit_event": audit_event,
        "adapter_source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
    }
    return {
        "command": spec["command_type"].value,
        "target": {
            "type": spec["target_type"].value,
            "id": clean_entity_id,
        },
        "action": clean_action_id,
        "params": params,
        "audit_context": {"reason": reason},
    }


def _sem_command_target_for(entity_type: str, entity_id: str) -> ObjectType:
    normalized = str(entity_type or "").strip().lower().replace("_", "-")
    return {
        "strategy": ObjectType.STRATEGY,
        "persona": ObjectType.PERSONA,
        "deployment": ObjectType.DEPLOYMENT,
        "rebalance": ObjectType.REBALANCE,
        "capital-pool": ObjectType.CAPITAL_POOL,
        "ranking-formula": ObjectType.RANKING_FORMULA,
        "alert": ObjectType.RISK_ALERT,
        "incident": ObjectType.INCIDENT,
        "job": ObjectType.JOB,
        "mcp-server": ObjectType.MCP_SERVER,
        "mcp-tool": ObjectType.TOOL,
        "skill": ObjectType.SKILL,
    }.get(normalized, ObjectType.REVIEW)


def _sem_command_payload_from_record(
    record: Dict[str, Any],
    *,
    idempotency_key: str,
    replayed: bool,
) -> Dict[str, Any]:
    command_id = str(record.get("command_id") or "")
    command_type = str(record.get("type") or "")
    receipts = _command_dual_write_receipts(
        command_id=command_id,
        command=command_type,
        status=ActionCommandStatus.ACCEPTED.value,
        accepted_at=str(record.get("submitted_at") or ""),
    )
    receipt = dict(receipts["command_receipt"])
    receipt["id"] = command_id
    return {
        "status": "accepted",
        "data": {
            "status": "accepted",
            "command": command_type,
            "commandId": command_id,
            "command_id": command_id,
            "receipt_id": command_id,
            "receipt": receipt,
            "receipt_dual_write": receipts,
            "action_receipt": receipts["action_receipt"],
            "actionReceipt": receipts["action_receipt"],
            "command_receipt": receipts["command_receipt"],
            "commandReceipt": receipts["command_receipt"],
        },
        "meta": {
            "durable": True,
            "liveCapitalSideEffects": False,
            "idempotency": {
                "key": idempotency_key,
                "idempotencyKey": idempotency_key,
                "replayed": replayed,
            },
        },
    }


def _sem_command_response(
    *,
    command_type: CommandType,
    target_type: ObjectType,
    target_id: str,
    payload: Dict[str, Any],
    identity: OperatorIdentity,
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str] = None,
    status_code: int = 202,
    server_generated_target: bool = False,
) -> JSONResponse:
    payload = dict(payload or {})
    _reject_body_idempotency_key(payload)
    clean_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    # For routes that generate the target_id server-side (CREATE without a client-supplied id),
    # exclude target_id from the idempotency hash so that retries with the same Idempotency-Key
    # replay correctly rather than conflicting due to a different random id per call.
    hash_body: Dict[str, Any] = {
        "command": command_type.value,
        "target_type": target_type.value,
        "payload": payload,
    }
    if not server_generated_target:
        hash_body["target_id"] = target_id
    request_hash = _stable_json_hash(hash_body)
    existing = _FINAL_CONTRACT_IDEMPOTENCY.get(clean_key)
    if existing:
        if existing.get("request_hash") != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was reused with a different command payload",
                "The idempotency key already belongs to another command payload",
                precondition_failed="idempotency_key",
            )
        replay = dict(existing["result"])
        replay.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
        return JSONResponse(status_code=status_code, content=replay)
    existing_record = command_store.get_command_by_idempotency_key(clean_key)
    if existing_record:
        stored_hash = (existing_record.get("foundation") or {}).get("idempotency_record", {}).get("request_hash")
        if stored_hash and stored_hash != request_hash:
            raise _bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was reused with a different command payload",
                "The idempotency key already belongs to another command payload",
                precondition_failed="idempotency_key",
            )
        response = _sem_command_payload_from_record(existing_record, idempotency_key=clean_key, replayed=True)
        return JSONResponse(status_code=status_code, content=response)

    now = utc_now()
    command_id = f"cmd-{uuid.uuid4().hex[:16]}"
    receipt_dual_write = _command_dual_write_receipts(
        command_id=command_id,
        command=command_type.value,
        status=ActionCommandStatus.ACCEPTED.value,
        accepted_at=now,
    )
    reason = str(payload.get("reason") or command_type.value)
    audit_action = _foundation_audit_for_command_record(
        identity=identity,
        command_type=command_type,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
        reason=reason,
        command_id=command_id,
        idempotency_key=clean_key,
        route="POST /bff/semantic-command",
    )
    foundation_ctx = {
        "idempotency_record": {
            "idempotency_key": clean_key,
            "request_hash": request_hash,
            "status": "succeeded",
            "trace_id": audit_action.trace_id,
        },
        "audit_action": audit_action.to_dict(),
    }
    record = command_store.submit_command(
        command_id,
        command_type,
        TargetObject(type=target_type, id=target_id),
        now,
        payload,
        {
            "actor": identity.operator_id,
            "reason": reason,
            "live_capital_side_effects": False,
            "receipt_dual_write": receipt_dual_write,
            "foundation": foundation_ctx,
        },
        foundation_ctx,
    )
    result = _sem_command_payload_from_record(record, idempotency_key=clean_key, replayed=False)
    _FINAL_CONTRACT_IDEMPOTENCY[clean_key] = {"request_hash": request_hash, "result": result}
    return JSONResponse(status_code=status_code, content=result)


def _submit_canonical_action_command(
    *,
    background_tasks: BackgroundTasks,
    response: Response,
    entity_type: str,
    entity_id: str,
    action_id: str,
    payload: Dict[str, Any],
    authorization: Optional[str],
    x_mfa_token: Optional[str],
    x_trace_id: Optional[str],
    x_correlation_id: Optional[str],
    x_request_id: Optional[str],
    x_confirm_token: Optional[str],
    idempotency_key: Optional[str],
    x_idempotency_key: Optional[str],
):
    deprecation = _legacy_action_deprecation_notice()
    _apply_legacy_action_deprecation_headers(response)
    payload = dict(payload or {})
    _reject_body_idempotency_key(payload)
    command_payload = _action_adapter_command_payload(
        entity_type=entity_type,
        entity_id=entity_id,
        action_id=action_id,
        payload=payload,
    )
    params = command_payload["params"]
    return _submit_final_command_admission(
        background_tasks=background_tasks,
        payload=command_payload,
        authorization=authorization,
        x_mfa_token=x_mfa_token,
        x_trace_id=x_trace_id,
        x_correlation_id=x_correlation_id,
        x_request_id=x_request_id,
        x_confirm_token=x_confirm_token,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
        route=_FINAL_COMMAND_ROUTE,
        source_route=_ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
        audit_extra={
            "action_id": params.get("action_id"),
            "entity_type": params.get("entity_type"),
            "entity_id": params.get("entity_id"),
            "audit_event": params.get("audit_event"),
            "adapter_source_route": _ACTIONS_TO_COMMANDS_SOURCE_ROUTE,
        },
        extra_precondition=lambda identity, _cmd: _require_operator_role(identity),
        enqueue=False,
        include_durable_meta=True,
        response_deprecation=deprecation,
    )


@app.post("/bff/actions/{type}/{id}/{action}", status_code=202)
async def sem_canonical_action_command(
    background_tasks: BackgroundTasks,
    response: Response,
    type: str,
    id: str,
    action: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
    x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    return _submit_canonical_action_command(
        background_tasks=background_tasks,
        response=response,
        entity_type=type,
        entity_id=id,
        action_id=action,
        payload=payload,
        authorization=authorization,
        x_mfa_token=x_mfa_token,
        x_trace_id=x_trace_id,
        x_correlation_id=x_correlation_id,
        x_request_id=x_request_id,
        x_confirm_token=x_confirm_token,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.post("/bff/actions/{entityType}/{entityId}/{actionId}", status_code=202, include_in_schema=False)
async def sem_legacy_named_action_command(
    background_tasks: BackgroundTasks,
    response: Response,
    entityType: str,
    entityId: str,
    actionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    x_trace_id: Optional[str] = Header(default=None, alias="X-Trace-Id"),
    x_correlation_id: Optional[str] = Header(default=None, alias="X-Correlation-Id"),
    x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    x_confirm_token: Optional[str] = Header(default=None, alias="X-Confirm-Token"),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    return _submit_canonical_action_command(
        background_tasks=background_tasks,
        response=response,
        entity_type=entityType,
        entity_id=entityId,
        action_id=actionId,
        payload=payload,
        authorization=authorization,
        x_mfa_token=x_mfa_token,
        x_trace_id=x_trace_id,
        x_correlation_id=x_correlation_id,
        x_request_id=x_request_id,
        x_confirm_token=x_confirm_token,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.post("/bff/deployments", status_code=201)
async def sem_create_deployment_command(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    client_provided_id = payload.get("deployment_id") or payload.get("deploymentId") or payload.get("id")
    deployment_id = str(client_provided_id or f"deployment-{uuid.uuid4().hex[:8]}")
    return _sem_command_response(
        command_type=CommandType.DEPLOYMENT_CREATE,
        target_type=ObjectType.DEPLOYMENT,
        target_id=deployment_id,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
        status_code=201,
        server_generated_target=not client_provided_id,
    )


@app.patch("/bff/deployments/{id}", status_code=202)
async def sem_patch_deployment_command(
    id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _sem_command_response(
        command_type=CommandType.DEPLOYMENT_PATCH,
        target_type=ObjectType.DEPLOYMENT,
        target_id=id,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.patch("/bff/rebalances/{id}", status_code=202)
async def sem_patch_rebalance_command(
    id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _sem_command_response(
        command_type=CommandType.REBALANCE_PATCH,
        target_type=ObjectType.REBALANCE,
        target_id=id,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.post("/bff/audit/export", status_code=202)
async def sem_audit_export_command(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _sem_command_response(
        command_type=CommandType.AUDIT_EXPORT,
        target_type=ObjectType.AUDIT_EXPORT,
        target_id=str(payload.get("target_type") or payload.get("targetType") or "audit-export"),
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.post("/bff/confirm-tokens", status_code=201)
async def sem_create_confirm_token_command(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    client_provided_id = str(payload.get("tokenId") or payload.get("token_id") or "").strip()
    token_id = client_provided_id or f"ct-{uuid.uuid4().hex[:12]}"
    server_generated = not bool(client_provided_id)
    # When tokenId is server-generated, exclude it from the payload and target_id hash
    # so that retries with the same Idempotency-Key replay correctly instead of
    # conflicting on a different randomly-generated id.
    hash_payload = dict(payload)
    if server_generated:
        hash_payload.pop("tokenId", None)
        hash_payload.pop("token_id", None)
    else:
        hash_payload["tokenId"] = token_id
    response = _sem_command_response(
        command_type=CommandType.CONFIRM_TOKEN_CREATE,
        target_type=ObjectType.CONFIRM_TOKEN,
        target_id=token_id,
        payload=hash_payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
        status_code=201,
        server_generated_target=server_generated,
    )
    content = json.loads(response.body.decode("utf-8"))
    # For server-generated token replays, recover the original tokenId from the
    # durable command record so the response is stable across retries.
    final_token_id = token_id
    if server_generated and content.get("meta", {}).get("idempotency", {}).get("replayed"):
        clean_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        stored = command_store.get_command_by_idempotency_key(clean_key)
        if stored:
            final_token_id = str(stored.get("target", {}).get("id") or token_id)
    content["data"]["tokenId"] = final_token_id
    content["data"]["id"] = final_token_id
    content["data"]["status"] = "created"
    return JSONResponse(status_code=201, content=content)


@app.get("/bff/confirm-tokens/{tokenId}")
async def sem_get_confirm_token(tokenId: str, authorization: Optional[str] = Header(default=None)):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    status = "available"
    for record in command_store._get_all_commands():
        target = record.get("target") if isinstance(record.get("target"), dict) else {}
        if target.get("type") != ObjectType.CONFIRM_TOKEN.value or target.get("id") != tokenId:
            continue
        status = {
            CommandType.CONFIRM_TOKEN_CREATE.value: "created",
            CommandType.CONFIRM_TOKEN_REDEEM.value: "redeemed",
            CommandType.CONFIRM_TOKEN_DELETE.value: "deleted",
        }.get(record.get("type"), status)
    return {"data": {"id": tokenId, "tokenId": tokenId, "status": status}, "meta": {"contract": "BFF-LUV-SEM-002", "snapshot_at": utc_now()}}


@app.post("/bff/confirm-tokens/{tokenId}/redeem", status_code=202)
async def sem_redeem_confirm_token_command(
    tokenId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _sem_command_response(
        command_type=CommandType.CONFIRM_TOKEN_REDEEM,
        target_type=ObjectType.CONFIRM_TOKEN,
        target_id=tokenId,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.delete("/bff/confirm-tokens/{tokenId}", status_code=202)
async def sem_delete_confirm_token_command(
    tokenId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _sem_command_response(
        command_type=CommandType.CONFIRM_TOKEN_DELETE,
        target_type=ObjectType.CONFIRM_TOKEN,
        target_id=tokenId,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.post("/bff/v5/interventions/{id}/claim", status_code=202)
@app.post("/bff/v5/interventions/{id}/decide", status_code=202)
@app.post("/bff/v5/interventions/{id}/escalate", status_code=202)
@app.post("/bff/v5/interventions/{id}/release", status_code=202)
@app.post("/bff/v5/interventions/{id}/two-man-sign", status_code=202)
async def sem_v5_intervention_command(
    id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _sem_command_response(
        command_type=CommandType.V5_INTERVENTION_ACTION,
        target_type=ObjectType.SENTINEL_INTERVENTION,
        target_id=id,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.post("/bff/v5/sentinel/findings/{id}/status", status_code=202)
async def sem_v5_sentinel_status_command(
    id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _sem_command_response(
        command_type=CommandType.SENTINEL_FINDING_STATUS,
        target_type=ObjectType.SENTINEL_FINDING,
        target_id=id,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.post("/bff/v5/sentinel/remediation/build", status_code=202)
async def sem_v5_sentinel_remediation_build_command(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    client_provided_finding = payload.get("finding_id") or payload.get("findingId")
    target_id = str(client_provided_finding or f"remediation-{uuid.uuid4().hex[:8]}")
    return _sem_command_response(
        command_type=CommandType.SENTINEL_REMEDIATION_BUILD,
        target_type=ObjectType.SENTINEL_REMEDIATION,
        target_id=target_id,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
        server_generated_target=not client_provided_finding,
    )


@app.post("/bff/v5/sentinel/remediation/{actionId}/execute", status_code=202)
async def sem_v5_sentinel_remediation_execute_command(
    actionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    return _sem_command_response(
        command_type=CommandType.SENTINEL_REMEDIATION_EXECUTE,
        target_type=ObjectType.SENTINEL_REMEDIATION,
        target_id=actionId,
        payload=payload,
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


_SENTINEL_FINDING_KINDS = {"hiq_sentinel", "risk_breach", "strategy_drift", "loop_anomaly"}
_SENTINEL_FINDING_STATUSES = {"open", "resolved", "dismissed", "escalated"}
_SENTINEL_FINDING_SEVERITIES = {"critical", "high", "medium", "low"}


@app.get("/bff/v5/sentinel/findings")
async def bff_v5_sentinel_findings_list(
    kind: Optional[str] = Query(default=None, description="Filter by kind: hiq_sentinel, risk_breach, strategy_drift, loop_anomaly"),
    status: Optional[str] = Query(default=None, description="Filter by status: open, resolved, dismissed, escalated"),
    severity: Optional[str] = Query(default=None, description="Filter by severity: critical, high, medium, low"),
    authorization: Optional[str] = Header(default=None),
):
    """
    GET /bff/v5/sentinel/findings — list sentinel findings from the read surface store.

    Optional query filters:
      ?kind=hiq_sentinel|risk_breach|strategy_drift|loop_anomaly
      ?status=open|resolved|dismissed|escalated
      ?severity=critical|high|medium|low

    Returns source-aware list response.  When the source is missing the items
    list is empty and meta.surfaces.sentinel_findings.source is 'missing'.
    """
    identity = _extract_identity(authorization)
    _require_read_role(identity)

    if kind is not None and kind.lower() not in _SENTINEL_FINDING_KINDS:
        raise _bff_error(
            400,
            ErrorCode.INVALID_REQUEST,
            f"Invalid kind '{kind}'. Must be one of: {', '.join(sorted(_SENTINEL_FINDING_KINDS))}",
            "Unknown sentinel finding kind filter value",
        )
    if status is not None and status.lower() not in _SENTINEL_FINDING_STATUSES:
        raise _bff_error(
            400,
            ErrorCode.INVALID_REQUEST,
            f"Invalid status '{status}'. Must be one of: {', '.join(sorted(_SENTINEL_FINDING_STATUSES))}",
            "Unknown sentinel finding status filter value",
        )
    if severity is not None and severity.lower() not in _SENTINEL_FINDING_SEVERITIES:
        raise _bff_error(
            400,
            ErrorCode.INVALID_REQUEST,
            f"Invalid severity '{severity}'. Must be one of: {', '.join(sorted(_SENTINEL_FINDING_SEVERITIES))}",
            "Unknown sentinel finding severity filter value",
        )

    available, records = read_store.list_sentinel_findings(
        kind=kind,
        status=status,
        severity=severity,
    )
    src_dataset = "sentinel_findings" if available and read_store.dataset_source("incidents") == "missing" else "incidents"
    source = None if available else "missing"
    return _sem_final_list_response(records, dataset=src_dataset, surface_key="sentinel_findings", source=source)


def _sem_local_records(dataset: str) -> tuple[str, List[Dict[str, Any]]]:
    data = getattr(read_store, "_data", {})
    raw = data.get(dataset) if isinstance(data, dict) else None
    if isinstance(raw, dict):
        return ("local_snapshot" if raw else "missing", [dict(item) for item in raw.values() if isinstance(item, dict)])
    if isinstance(raw, list):
        return ("local_snapshot" if raw else "missing", [dict(item) for item in raw if isinstance(item, dict)])
    return "missing", []


def _sem_read_records(dataset: str) -> tuple[str, List[Dict[str, Any]]]:
    reader = getattr(read_store, "_read_dataset_records", None)
    source_fn = getattr(read_store, "dataset_source", None)
    if not callable(reader):
        return _sem_local_records(dataset)

    records = [dict(item) for item in reader(dataset) if isinstance(item, dict)]
    source = source_fn(dataset) if callable(source_fn) else ("local_snapshot" if records else "missing")
    if source == "missing" and records:
        source = "local_snapshot"
    return source, records


def _sem_list_payload(dataset: str, surface_key: str, *, filter_mode: Optional[str] = None) -> Dict[str, Any]:
    source, records = _sem_read_records(dataset)
    if filter_mode:
        records = [record for record in records if str(record.get("mode") or "") == filter_mode]
    snapshot_at = utc_now()
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        source=source,
        has_data=(source != "missing"),
    )
    meta = _read_surface_meta(
        dataset,
        surface_key,
        snapshot_at=snapshot_at,
        total=len(records),
        surface=surface,
    )
    return {"items": records, "page_info": {"next_page_token": None}, "meta": meta}


@app.get("/bff/agora/inbox")
async def sem_agora_inbox(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return _sem_list_payload("insight_cards", "agora_inbox")


@app.get("/bff/agora/ask/sessions")
async def sem_agora_ask_sessions(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return _sem_list_payload("agora_sessions", "agora_ask_sessions", filter_mode="quick_ask")


_ASK_SESSIONS_IDEMPOTENCY = _AGORA_CORE_BFF_IDEMPOTENCY


@app.post("/bff/agora/ask/sessions", status_code=201)
async def sem_agora_ask_create_session(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """ASK-001: create an agora ask session explicitly."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/agora/ask/sessions", "payload": payload})
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    now = utc_now()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or f"ask-{uuid.uuid4().hex[:10]}")
    title = str(payload.get("title") or "Agora ask session").strip()
    session = read_store.create_agora_session(
        session_id=session_id,
        title=title,
        actor_id=identity.operator_id,
        payload={
            **dict(payload),
            "mode": "quick_ask",
            "participants": payload.get("participants") or [{"type": "operator", "id": identity.operator_id}],
        },
        created_at=now,
    )
    result = {
        "data": session,
        "meta": {
            "snapshot_at": now,
            "surfaces": {"agora_ask_session_detail": {"status": "ok", "source": "bff_local"}},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/ask/sessions/{sessionId}")
async def sem_agora_ask_session_detail(
    sessionId: str,
    authorization: Optional[str] = Header(default=None),
):
    """ASK-001: ask session detail — also serves as the SSE resync route for the ask channel."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    session = read_store.get_agora_session(sessionId)
    if session is None or str(session.get("mode") or "").strip() != "quick_ask":
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Ask session not found",
            f"Ask session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    return {
        "data": session,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {"agora_ask_session_detail": {"status": "ok", "source": "bff_local"}},
        },
    }


@app.post("/bff/agora/ask/sessions/{sessionId}/close", status_code=200)
async def sem_agora_ask_close_session(
    sessionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """ASK-001: close an agora ask session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({
        "route": f"POST /bff/agora/ask/sessions/{sessionId}/close",
        "sessionId": sessionId,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    now = utc_now()
    outcome = str(payload.get("outcome") or "").strip() or None
    existing = read_store.get_agora_session(sessionId)
    if existing is None or str(existing.get("mode") or "").strip() != "quick_ask":
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Ask session not found",
            f"Ask session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    session = read_store.close_agora_session(sessionId, closed_at=now, outcome=outcome)
    if session is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Ask session not found",
            f"Ask session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "ask.session.completed",
        {"sessionId": sessionId, "outcome": outcome},
    )
    result = {
        "data": session,
        "meta": {
            "snapshot_at": now,
            "surfaces": {"agora_ask_session_detail": {"status": "ok", "source": "bff_local"}},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


# ---- ASK-003: committee session lifecycle ----


@app.get("/bff/agora/committee/sessions")
async def sem_agora_committee_sessions(authorization: Optional[str] = Header(default=None)):
    """ASK-003: list committee sessions (mode=committee)."""
    _require_read_role(_extract_identity(authorization))
    return _sem_list_payload("agora_sessions", "agora_committee_sessions", filter_mode="committee")


@app.post("/bff/agora/committee/sessions", status_code=201)
async def sem_agora_committee_create_session(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """ASK-003: create a committee session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/agora/committee/sessions", "payload": payload})
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    now = utc_now()
    session_id = str(
        payload.get("sessionId") or payload.get("session_id") or f"committee-{uuid.uuid4().hex[:10]}"
    )
    title = str(payload.get("title") or "Committee session").strip()
    session = read_store.create_agora_session(
        session_id=session_id,
        title=title,
        actor_id=identity.operator_id,
        payload={
            **dict(payload),
            "mode": "committee",
            "status": "pending",
            "participants": payload.get("participants") or [],
            "quorumState": payload.get("quorumState") or "pending",
            "consensusState": payload.get("consensusState") or "open",
            "participantRoster": payload.get("participantRoster") or [],
            "linkedRequestId": payload.get("linkedRequestId") or payload.get("linked_request_id"),
        },
        created_at=now,
    )
    result = {
        "data": session,
        "meta": {
            "snapshot_at": now,
            "surfaces": {"agora_committee_session_detail": {"status": "ok", "source": "bff_local"}},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/committee/sessions/{sessionId}")
async def sem_agora_committee_session_detail(
    sessionId: str,
    authorization: Optional[str] = Header(default=None),
):
    """ASK-003: committee session detail — also serves as SSE resync route for ask channel."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    session = read_store.get_agora_session(sessionId)
    if session is None or str(session.get("mode") or "").strip() != "committee":
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee session not found",
            f"Committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    return {
        "data": session,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {"agora_committee_session_detail": {"status": "ok", "source": "bff_local"}},
        },
    }


@app.post("/bff/agora/committee/sessions/{sessionId}/open", status_code=200)
async def sem_agora_committee_open_session(
    sessionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """ASK-003: open a pending committee session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({
        "route": f"POST /bff/agora/committee/sessions/{sessionId}/open",
        "sessionId": sessionId,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    now = utc_now()
    session = read_store.open_committee_session(sessionId, opened_at=now)
    if session is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee session not found",
            f"Committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "ask.session.started",
        {"sessionId": sessionId, "mode": "committee"},
    )
    result = {
        "data": session,
        "meta": {
            "snapshot_at": now,
            "surfaces": {"agora_committee_session_detail": {"status": "ok", "source": "bff_local"}},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.post("/bff/agora/committee/sessions/{sessionId}/close", status_code=200)
async def sem_agora_committee_close_session(
    sessionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """ASK-003: close a committee session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({
        "route": f"POST /bff/agora/committee/sessions/{sessionId}/close",
        "sessionId": sessionId,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    now = utc_now()
    outcome = str(payload.get("outcome") or "").strip() or None
    memo_ids = payload.get("memoIds") or payload.get("memo_ids") or None
    session = read_store.close_committee_session(
        sessionId,
        closed_at=now,
        outcome=outcome,
        memo_ids=memo_ids,
    )
    if session is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee session not found",
            f"Committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    _publish_event(
        _sse_buffers["ask"],
        _sse_subscribers["ask"],
        "ask.session.completed",
        {"sessionId": sessionId, "mode": "committee", "outcome": outcome},
    )
    result = {
        "data": session,
        "meta": {
            "snapshot_at": now,
            "surfaces": {"agora_committee_session_detail": {"status": "ok", "source": "bff_local"}},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


# ---- ASK-004: committee session memo publish to registry / review ---- #


@app.get("/bff/agora/committee/sessions/{sessionId}/memos")
async def sem_agora_committee_session_memos(
    sessionId: str,
    authorization: Optional[str] = Header(default=None),
):
    """ASK-004: list memos linked to a committee session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    session = read_store.get_agora_session(sessionId)
    if session is None or str(session.get("mode") or "").strip() != "committee":
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee session not found",
            f"Committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    snapshot_at = utc_now()
    memos = read_store.list_committee_session_memos(sessionId)
    return {
        "items": memos,
        "page_info": {"next_page_token": None, "total": len(memos)},
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {"agora_committee_session_memos": {"status": "ok", "source": "bff_local"}},
        },
    }


@app.post("/bff/agora/committee/sessions/{sessionId}/memos", status_code=201)
async def sem_agora_committee_submit_memo(
    sessionId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """ASK-004: submit a draft memo for a committee session."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({
        "route": f"POST /bff/agora/committee/sessions/{sessionId}/memos",
        "sessionId": sessionId,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    now = utc_now()
    session = read_store.get_agora_session(sessionId)
    if session is None or str(session.get("mode") or "").strip() != "committee":
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee session not found",
            f"Committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    memo_id = str(payload.get("memoId") or payload.get("memo_id") or "").strip() or f"memo-{uuid.uuid4().hex[:12]}"
    if read_store.get_consult_memo(memo_id) is not None:
        raise _bff_error(
            409,
            ErrorCode.CONCURRENT_MODIFICATION,
            "Committee memo id already exists",
            f"Memo {memo_id} already exists in the consult memo registry",
            precondition_failed="memo_id",
            suggestion="Retry with a new memoId or replay the original request with the same Idempotency-Key",
        )
    memo = read_store.submit_committee_session_memo(
        sessionId,
        memo_id=memo_id,
        actor_id=identity.operator_id,
        payload=payload,
        created_at=now,
    )
    result = {
        "data": memo,
        "meta": {
            "snapshot_at": now,
            "surfaces": {"agora_committee_memo_detail": {"status": "ok", "source": "bff_local"}},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/committee/sessions/{sessionId}/memos/{memoId}")
async def sem_agora_committee_memo_detail(
    sessionId: str,
    memoId: str,
    authorization: Optional[str] = Header(default=None),
):
    """ASK-004: get a committee session memo for review."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    snapshot_at = utc_now()
    session = read_store.get_agora_session(sessionId)
    if session is None or str(session.get("mode") or "").strip() != "committee":
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee session not found",
            f"Committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    memo = read_store.get_committee_session_memo(sessionId, memoId)
    if memo is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee memo not found",
            f"Memo {memoId} for session {sessionId} does not exist",
            precondition_failed="memo_id",
        )
    return {
        "data": memo,
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": {"agora_committee_memo_detail": {"status": "ok", "source": "bff_local"}},
        },
    }


@app.post("/bff/agora/committee/sessions/{sessionId}/memos/{memoId}/publish", status_code=200)
async def sem_agora_committee_publish_memo(
    sessionId: str,
    memoId: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """ASK-004: publish a committee session memo to the consult memo registry."""
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({
        "route": f"POST /bff/agora/committee/sessions/{sessionId}/memos/{memoId}/publish",
        "sessionId": sessionId,
        "memoId": memoId,
        "payload": payload,
    })
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return cached
    now = utc_now()
    session = read_store.get_agora_session(sessionId)
    if session is None or str(session.get("mode") or "").strip() != "committee":
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee session not found",
            f"Committee session {sessionId} does not exist",
            precondition_failed="session_id",
        )
    existing_memo = read_store.get_committee_session_memo(sessionId, memoId)
    was_published = str((existing_memo or {}).get("status") or "").strip().lower() == "published"
    memo = read_store.publish_committee_session_memo(
        sessionId,
        memoId,
        actor_id=identity.operator_id,
        published_at=now,
    )
    if memo is None:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Committee memo not found",
            f"Memo {memoId} for session {sessionId} does not exist",
            precondition_failed="memo_id",
        )
    if not was_published:
        _publish_event(
            _sse_buffers["ask"],
            _sse_subscribers["ask"],
            "ask.memo.published",
            {"sessionId": sessionId, "memoId": memoId},
        )
    result = {
        "data": memo,
        "meta": {
            "snapshot_at": now,
            "surfaces": {"agora_committee_memo_detail": {"status": "ok", "source": "bff_local"}},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return result


@app.get("/bff/agora/skill-coaching/sessions")
async def sem_agora_skill_coaching_sessions(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return _sem_list_payload("agora_skill_coaching_sessions", "agora_skill_coaching_sessions")


@app.get("/bff/agora/persona-lab/runs")
async def sem_agora_persona_lab_runs(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return _sem_list_payload("agora_persona_lab_runs", "agora_persona_lab_runs")


@app.get("/bff/agora/postmortems")
async def sem_agora_postmortems(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return _sem_list_payload("postmortems", "agora_postmortems")


@app.get("/bff/agora/evaluation-suites")
async def sem_agora_evaluation_suites(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return _sem_list_payload("agora_evaluation_suites", "agora_evaluation_suites")


@app.get("/bff/agora/evaluation-runs")
async def sem_agora_evaluation_runs(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return _sem_list_payload("agora_evaluation_runs", "agora_evaluation_runs")


@app.post("/bff/agora/ask", status_code=202)
async def sem_agora_ask(
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    identity = _extract_identity(authorization)
    _require_read_role(identity)
    _reject_body_idempotency_key(payload)
    resolved_key = _resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
    request_hash = _stable_json_hash({"route": "POST /bff/agora/ask", "payload": payload})
    cached = _agora_core_idempotency_check(resolved_key, request_hash)
    if cached is not None:
        return JSONResponse(status_code=202, content=cached)
    now = utc_now()
    session_id = str(payload.get("sessionId") or payload.get("session_id") or f"ask-{uuid.uuid4().hex[:10]}")
    message_id = str(payload.get("messageId") or payload.get("message_id") or f"msg-{uuid.uuid4().hex[:10]}")
    prompt = str(payload.get("prompt") or payload.get("message") or payload.get("content") or "").strip()
    session = read_store.get_agora_session(session_id)
    if session is None:
        session = read_store.create_agora_session(
            session_id=session_id,
            title=prompt[:80] or "Agora ask",
            actor_id=identity.operator_id,
            payload={
                **dict(payload),
                "mode": "quick_ask",
                "participants": [{"type": "operator", "id": identity.operator_id}],
                "messages": [],
            },
            created_at=now,
        )
    messages = read_store.list_agora_session_messages(session_id) or []
    message = next(
        (item for item in messages if isinstance(item, dict) and str(item.get("id") or "") == message_id),
        None,
    )
    if message is None:
        message = read_store.append_agora_session_message(
            session_id,
            message_id=message_id,
            content=prompt,
            actor_id=identity.operator_id,
            payload={
                **dict(payload),
                "sender": {"type": "operator", "id": identity.operator_id},
                "role": "user",
            },
            created_at=now,
        )
    session = read_store.get_agora_session(session_id) or session
    command_id = f"cmd-{uuid.uuid4().hex[:16]}"
    command_store.submit_command(
        command_id,
        CommandType.AGORA_MESSAGE_ACTION,
        TargetObject(type=ObjectType.AGORA_MESSAGE, id=message_id),
        now,
        dict(payload),
        {"actor": identity.operator_id, "live_capital_side_effects": False},
        {"idempotency_record": {"idempotency_key": resolved_key, "request_hash": request_hash, "status": "succeeded"}},
    )
    result = {
        "status": "accepted",
        "data": {"session": session, "message": message},
        "meta": {
            "snapshot_at": now,
            "command": {"command": CommandType.AGORA_MESSAGE_ACTION.value, "commandId": command_id},
            "idempotency": {"idempotencyKey": resolved_key, "replayed": False},
        },
    }
    _AGORA_CORE_BFF_IDEMPOTENCY[resolved_key] = {"request_hash": request_hash, "result": result}
    return JSONResponse(status_code=202, content=result)


@app.get("/bff/healthz")
@app.get("/bff/readyz")
async def sem_bff_health_alias():
    return {"status": "ok", "service": "operator-bff", "version": "0.2.0"}


@app.get("/bff/capabilities")
@app.get("/bff/feature-flags")
async def sem_bff_capabilities(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return {
        "data": {
            "feature_flags": {
                "executePlansBff": True,
                "sessionAuthMe": True,
                "oodaPackets": _ooda_packet_routes_enabled(),
                "synthesisConflictLogs": _synthesis_conflict_log_routes_enabled(),
            }
        },
        "meta": {"snapshot_at": utc_now()},
    }


def _sem_empty_final_list(surface_key: str) -> Dict[str, Any]:
    return {
        "items": [],
        "page_info": {"next_page_token": None},
        "meta": {"snapshot_at": utc_now(), "surfaces": {surface_key: {"status": "unavailable", "source": "missing"}}},
    }


def _sem_final_registry_meta(surface_key: str, *, snapshot_at: Optional[str] = None, total: Optional[int] = None) -> Dict[str, Any]:
    snapshot_at = snapshot_at or utc_now()
    meta: Dict[str, Any] = {
        "snapshot_at": snapshot_at,
        "surfaces": {surface_key: {"status": "ok", "source": "bff_local_registry"}},
    }
    if total is not None:
        meta["total"] = total
    return meta


def _sem_final_list_response(
    items: List[Dict[str, Any]],
    *,
    dataset: str,
    surface_key: str,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    if source == "bff_local_registry":
        meta = _sem_final_registry_meta(surface_key, snapshot_at=snapshot_at, total=len(items))
    else:
        surface = _dataset_surface_status(dataset, snapshot_at=snapshot_at, source=source)
        meta = {
            "snapshot_at": snapshot_at,
            "surfaces": {surface_key: surface},
            "total": len(items),
        }
        reason = _surface_degradation_reason(
            surface,
            degraded_reason=f"{surface_key.replace('_', ' ')} is degraded and may be stale.",
            unavailable_reason=f"{surface_key.replace('_', ' ')} is currently unavailable.",
        )
        if reason is not None:
            meta["degradation"] = {"reason": reason}
    return {
        "data": items,
        "items": items,
        "page_info": {"next_page_token": None, "total": len(items)},
        "meta": meta,
    }


def _sem_final_degraded_detail(
    *,
    entity_id: str,
    label: str,
    dataset: str,
    surface_key: str,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    surface = _dataset_surface_status(
        dataset,
        snapshot_at=snapshot_at,
        has_data=False,
        missing_message=f"{label} read model source is unavailable.",
        source=source,
    )
    meta = _read_surface_meta(
        dataset,
        surface_key,
        snapshot_at=snapshot_at,
        surface=surface,
        unavailable_reason=f"{label} read model source is unavailable.",
    )
    return {
        "data": {
            "id": entity_id,
            "status": "degraded",
            "readSurface": surface,
            "message": f"{label} read model source is unavailable.",
        },
        "meta": meta,
    }


def _sem_final_read_model_detail(
    record: Optional[Dict[str, Any]],
    *,
    entity_id: str,
    label: str,
    dataset: str,
    surface_key: str,
    source: Optional[str] = None,
    source_available: Optional[bool] = None,
) -> Dict[str, Any]:
    snapshot_at = utc_now()
    surface = _dataset_surface_status(dataset, snapshot_at=snapshot_at, source=source)
    if record:
        return {
            "data": record,
            "meta": _read_surface_meta(dataset, surface_key, snapshot_at=snapshot_at, surface=surface),
        }
    if source_available is False or surface.get("status") == "unavailable":
        return _sem_final_degraded_detail(
            entity_id=entity_id,
            label=label,
            dataset=dataset,
            surface_key=surface_key,
            source=source,
        )
    raise _bff_error(
        404,
        ErrorCode.OBJECT_NOT_FOUND,
        f"{label} not found",
        f"{label} {entity_id} does not exist",
    )


def _sem_final_registry_detail(
    record: Optional[Dict[str, Any]],
    *,
    entity_id: str,
    label: str,
    surface_key: str,
) -> Dict[str, Any]:
    if not record:
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            f"{label} not found",
            f"{label} {entity_id} does not exist",
        )
    snapshot_at = utc_now()
    return {
        "data": record,
        "meta": _sem_final_registry_meta(surface_key, snapshot_at=snapshot_at),
    }


def _sem_final_mcp_tool_records() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for record in _merged_mcp_tool_records():
        tool_id = str(record.get("tool_id") or record.get("id") or "").strip()
        if not tool_id:
            continue
        records.append(
            {
                "id": tool_id,
                "tool_id": tool_id,
                "server_id": record.get("server_id"),
                "name": record.get("name") or tool_id,
                "status": record.get("status") or "imported",
                "tool_class": record.get("tool_class") or "",
                "schema_url": record.get("schema_url"),
                "action_count": record.get("action_count", 0),
            }
        )
    return sorted(records, key=lambda item: (str(item.get("server_id") or ""), str(item.get("tool_id") or "")))


def _sem_final_mcp_tool_record(tool_id: str) -> Optional[Dict[str, Any]]:
    clean_id = str(tool_id or "").strip()
    for record in _sem_final_mcp_tool_records():
        if str(record.get("tool_id") or record.get("id") or "") == clean_id:
            return record
    return None


def _sem_final_channel_records() -> List[Dict[str, Any]]:
    return [
        {
            "id": channel,
            "channel_id": channel,
            "name": channel,
            "status": "active",
            "replay_supported": channel in _SSE_RESYNC_ROUTES,
            "resync_routes": list(_SSE_RESYNC_ROUTES.get(channel, ())),
        }
        for channel in SSE_CHANNEL_CATALOG
    ]


def _sem_final_channel_record(channel_id: str) -> Optional[Dict[str, Any]]:
    clean_id = str(channel_id or "").strip()
    return next((record for record in _sem_final_channel_records() if record["id"] == clean_id), None)


def _sem_final_v5_intervention_record(intervention_id: str) -> Optional[Dict[str, Any]]:
    clean_id = str(intervention_id or "").strip()
    for record in _v5_intervention_records():
        if str(record.get("id") or record.get("intervention_id") or "") == clean_id:
            return dict(record)
    return None


def _sem_final_alert_detail(alert_id: str) -> Dict[str, Any]:
    snapshot_at = utc_now()
    payload = _build_operator_alerts_payload(snapshot_at)
    alerts = payload.get("alerts") if isinstance(payload, dict) else []
    alert = next(
        (
            item
            for item in alerts or []
            if str(item.get("alert_id") or item.get("id") or "") == str(alert_id)
        ),
        None,
    )
    if alert:
        meta = dict(payload.get("meta") or {})
        meta.setdefault("snapshot_at", snapshot_at)
        return {"data": alert, "meta": meta}
    surfaces = ((payload.get("meta") or {}).get("surfaces") or {}) if isinstance(payload, dict) else {}
    alerts_surface = surfaces.get("alerts") if isinstance(surfaces, dict) else None
    if isinstance(alerts_surface, dict) and alerts_surface.get("status") == "unavailable":
        return _sem_final_degraded_detail(
            entity_id=alert_id,
            label="Alert",
            dataset="incidents",
            surface_key="alert_detail",
            source=alerts_surface.get("source"),
        )
    raise _bff_error(
        404,
        ErrorCode.OBJECT_NOT_FOUND,
        "Alert not found",
        f"Alert {alert_id} does not exist",
    )


# MGMT-OODA-005: stage definitions for control-room OODA status card
_OODA_STAGE_DEFS = [
    ("observe", "Observe", "telemetry/source/search health"),
    ("orient", "Orient", "active signal/persona proposal count"),
    ("decide", "Decide", "pending approvals/interventions"),
    ("act", "Act", "paper runtime / sandbox broker state"),
    ("learn", "Learn", "evolution/postmortem/retrain state"),
]

# Stage-to-status mapping: which packet status values map to each stage card
_OODA_STAGE_STATUSES: Dict[str, List[str]] = {
    "observe": ["open", "observing"],
    "orient": ["oriented"],
    "decide": ["decided"],
    "act": ["acted"],
    "learn": ["evolving"],
}


def _build_ooda_control_room_status_card(snapshot_at: str) -> Dict[str, Any]:
    """Return the OODA stage summary card for the Control Room.

    Gated by PANTHEON_OODA_PACKET_ENABLED. Returns a fail-closed card when
    disabled. Each stage card carries an active_count (open loops at that
    stage) and a direct link to the filtered packet list.
    """
    if not _ooda_packet_routes_enabled():
        return {
            "enabled": False,
            "gate_state": "fail_closed",
            "open_loop_count": 0,
            "closed_loop_count": 0,
            "failed_loop_count": 0,
            "total_packet_count": 0,
            "stages": {
                stage: {
                    "label": label,
                    "description": desc,
                    "status": "fail_closed",
                    "active_count": 0,
                    "detail_link": f"/bff/ooda/packets?stage={stage}",
                }
                for stage, label, desc in _OODA_STAGE_DEFS
            },
            "live_capital_side_effects": False,
            "fail_closed_gate_posture": "fail_closed",
            "meta": {
                "snapshot_at": snapshot_at,
                "source": "fail_closed",
                "status": "fail_closed",
                "surface_key": "ooda_control_room_status",
            },
        }

    packets = read_store.list_ooda_packets()
    ooda_src = read_store.dataset_source("ooda_packets")

    open_statuses = {"open", "observing", "oriented", "decided", "acted", "evolving"}
    open_count = sum(
        1 for p in packets if str(p.get("status") or "").lower() in open_statuses
    )
    closed_count = sum(
        1 for p in packets if str(p.get("status") or "").lower() == "closed"
    )
    failed_count = sum(
        1 for p in packets if str(p.get("status") or "").lower() == "failed"
    )

    stage_counts: Dict[str, int] = {
        stage: sum(
            1
            for p in packets
            if str(p.get("status") or "").lower() in status_vals
        )
        for stage, status_vals in _OODA_STAGE_STATUSES.items()
    }

    # Safety assertion: no pre-activation packet should carry live capital side effects
    live_side_effects_detected = any(
        p.get("act", {}).get("live_capital_side_effects", False) is True
        for p in packets
        if str(p.get("environment") or "").lower() != "live"
    )

    surface_status = "ok" if ooda_src not in (None, "missing") else "unavailable"

    return {
        "enabled": True,
        "gate_state": "enabled",
        "open_loop_count": open_count,
        "closed_loop_count": closed_count,
        "failed_loop_count": failed_count,
        "total_packet_count": len(packets),
        "stages": {
            stage: {
                "label": label,
                "description": desc,
                "status": "ok",
                "active_count": stage_counts[stage],
                "detail_link": f"/bff/ooda/packets?stage={stage}",
            }
            for stage, label, desc in _OODA_STAGE_DEFS
        },
        "live_capital_side_effects": live_side_effects_detected,
        "fail_closed_gate_posture": "fail_closed",
        "meta": {
            "snapshot_at": snapshot_at,
            "source": ooda_src if ooda_src else "missing",
            "status": surface_status,
            "surface_key": "ooda_control_room_status",
        },
    }


def _sem_final_generic_list_for_path(path: str) -> Optional[Dict[str, Any]]:
    if path == "/bff/audit":
        return _sem_final_list_response(
            _list_governance_audit_events(),
            dataset="governance_audit_events",
            surface_key="audit",
        )
    if path == "/bff/artifacts":
        return _sem_final_list_response(
            read_store.list_research_artifacts(),
            dataset="research_artifacts",
            surface_key="artifacts",
        )
    if path == "/bff/mcp-servers":
        return _sem_final_list_response(
            _merged_mcp_server_records(),
            dataset="mcp_servers",
            surface_key="mcp_servers",
            source="bff_local_registry",
        )
    if path == "/bff/mcp-tools":
        return _sem_final_list_response(
            _sem_final_mcp_tool_records(),
            dataset="mcp_tools",
            surface_key="mcp_tools",
            source="bff_local_registry",
        )
    if path == "/bff/ranking-formulas":
        return _sem_final_list_response(
            read_store.list_ranking_formulas(),
            dataset="ranking_formulas",
            surface_key="ranking_formulas",
        )
    if path == "/bff/research-experiments":
        source = "bff_overlay" if _GOV_BFF_EXPERIMENT_OVERLAY else None
        return _sem_final_list_response(
            _list_bff_experiments(),
            dataset="research_experiments",
            surface_key="research_experiments",
            source=source,
        )
    if path == "/bff/research-analyses":
        return _sem_final_list_response(
            read_store.list_research_analyses(),
            dataset="research_analyses",
            surface_key="research_analyses",
        )
    if path == "/bff/channels":
        return _sem_final_list_response(
            _sem_final_channel_records(),
            dataset="channels",
            surface_key="channels",
            source="bff_local_registry",
        )
    if path == "/bff/v5/loop-runs":
        available, records = read_store.list_loop_runs()
        src_dataset = "loop_runs" if available and read_store.dataset_source("incidents") == "missing" else "incidents"
        source = None if available else "missing"
        return _sem_final_list_response(records, dataset=src_dataset, surface_key="loop_runs", source=source)
    if path == "/bff/v5/sentinel/findings":
        available, records = read_store.list_sentinel_findings()
        src_dataset = "sentinel_findings" if available and read_store.dataset_source("incidents") == "missing" else "incidents"
        source = None if available else "missing"
        return _sem_final_list_response(records, dataset=src_dataset, surface_key="sentinel_findings", source=source)
    if path == "/bff/v5/control-room":
        snapshot_at = utc_now()
        avail_lr, loop_runs = read_store.list_loop_runs()
        avail_sf, sentinel_findings = read_store.list_sentinel_findings()
        incidents_source = read_store.dataset_source("incidents")

        def _control_room_child_surface(dataset: str, available: bool) -> Dict[str, Any]:
            if incidents_source != "missing":
                return _dataset_surface_status("incidents", snapshot_at=snapshot_at)
            return _dataset_surface_status(
                dataset,
                snapshot_at=snapshot_at,
                source=None if available else "missing",
            )

        loop_surface = _control_room_child_surface("loop_runs", avail_lr)
        sentinel_surface = _control_room_child_surface("sentinel_findings", avail_sf)
        child_statuses = {
            str(loop_surface.get("status") or "ok"),
            str(sentinel_surface.get("status") or "ok"),
        }
        if child_statuses == {"ok"}:
            control_surface = {"status": "ok", "source": "composed_read_models"}
        elif child_statuses == {"unavailable"}:
            control_surface = {
                "status": "unavailable",
                "source": "missing",
                "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
            }
        else:
            control_surface = {
                "status": "degraded",
                "source": "composed_read_models",
                "staleness": {"served_from": "mixed", "last_known_at": snapshot_at},
            }
        ooda_card = _build_ooda_control_room_status_card(snapshot_at)
        return {
            "loops": {
                "items": loop_runs,
                "meta": {"snapshot_at": snapshot_at, "surfaces": {"loop_runs": loop_surface}},
            },
            "interventions": {
                "items": _v5_intervention_records(),
                "meta": {"snapshot_at": snapshot_at, "surfaces": {"interventions": {"status": "ok", "source": "bff_local_registry"}}},
            },
            "sentinel": {
                "items": sentinel_findings,
                "meta": {"snapshot_at": snapshot_at, "surfaces": {"sentinel_findings": sentinel_surface}},
            },
            "ooda_status": ooda_card,
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {
                    "control_room": control_surface,
                    "loop_runs": loop_surface,
                    "sentinel_findings": sentinel_surface,
                    "ooda_control_room_status": ooda_card["meta"],
                },
            },
        }
    if path == "/bff/v5/execution/persona-health":
        snapshot_at = utc_now()
        persona_surface = _dataset_surface_status("personas", snapshot_at=snapshot_at)
        personas = read_store.list_personas()
        health_items = [
            {
                "id": p.get("persona_id") or p.get("id"),
                "persona_id": p.get("persona_id") or p.get("id"),
                "name": p.get("name") or p.get("persona_id"),
                "health": "healthy" if p.get("lifecycle_state") == "active" else "degraded",
                "lifecycle_state": p.get("lifecycle_state"),
            }
            for p in personas
        ]
        return {
            "items": health_items,
            "meta": {"snapshot_at": snapshot_at, "surfaces": {"persona_health": persona_surface}},
        }
    if path == "/bff/v5/execution/strategy-health":
        snapshot_at = utc_now()
        strategy_surface = _dataset_surface_status("strategy_specs", snapshot_at=snapshot_at)
        strategies = read_store.list_strategy_specs()
        health_items = [
            {
                "id": s.get("strategy_id") or s.get("id"),
                "strategy_id": s.get("strategy_id") or s.get("id"),
                "name": s.get("name") or s.get("strategy_id"),
                "health": "healthy" if str(s.get("status") or "") == "active" else "degraded",
                "status": s.get("status"),
            }
            for s in strategies
        ]
        return {
            "items": health_items,
            "meta": {"snapshot_at": snapshot_at, "surfaces": {"strategy_health": strategy_surface}},
        }
    return None


def _sem_final_generic_detail_for_path(path: str, entity_id: str) -> Optional[Dict[str, Any]]:
    if path.startswith("/bff/alerts/"):
        return _sem_final_alert_detail(entity_id)
    if path.startswith("/bff/approvals/"):
        return _sem_final_read_model_detail(
            read_store.get_approval_decision(entity_id),
            entity_id=entity_id,
            label="Approval",
            dataset="approval_decisions",
            surface_key="approval_detail",
        )
    if path.startswith("/bff/artifacts/"):
        return _sem_final_read_model_detail(
            read_store.get_research_artifact(entity_id),
            entity_id=entity_id,
            label="Artifact",
            dataset="research_artifacts",
            surface_key="artifact_detail",
        )
    if path.startswith("/bff/channels/"):
        return _sem_final_registry_detail(
            _sem_final_channel_record(entity_id),
            entity_id=entity_id,
            label="Channel",
            surface_key="channel_detail",
        )
    if path.startswith("/bff/mcp-servers/"):
        return _sem_final_registry_detail(
            _find_record_by_id(_merged_mcp_server_records(), entity_id, ("server_id", "id")),
            entity_id=entity_id,
            label="MCP server",
            surface_key="mcp_server_detail",
        )
    if path.startswith("/bff/mcp-tools/"):
        return _sem_final_registry_detail(
            _sem_final_mcp_tool_record(entity_id),
            entity_id=entity_id,
            label="MCP tool",
            surface_key="mcp_tool_detail",
        )
    if path.startswith("/bff/ranking-formulas/"):
        return _sem_final_read_model_detail(
            read_store.get_ranking_formula(entity_id),
            entity_id=entity_id,
            label="Ranking formula",
            dataset="ranking_formulas",
            surface_key="ranking_formula_detail",
        )
    if path.startswith("/bff/research-experiments/"):
        source = "bff_overlay" if _GOV_BFF_EXPERIMENT_OVERLAY else None
        return _sem_final_read_model_detail(
            _get_bff_experiment(entity_id),
            entity_id=entity_id,
            label="Research experiment",
            dataset="research_experiments",
            surface_key="research_experiment_detail",
            source=source,
            source_available=True if _GOV_BFF_EXPERIMENT_OVERLAY else None,
        )
    if path.startswith("/bff/research-analyses/"):
        return _sem_final_read_model_detail(
            read_store.get_research_analysis(entity_id),
            entity_id=entity_id,
            label="Research analysis",
            dataset="research_analyses",
            surface_key="research_analysis_detail",
        )
    if path.startswith("/bff/v5/interventions/"):
        return _sem_final_registry_detail(
            _sem_final_v5_intervention_record(entity_id),
            entity_id=entity_id,
            label="Intervention",
            surface_key="intervention_detail",
        )
    if path.startswith("/bff/v5/loop-runs/"):
        available, record = read_store.get_loop_run(entity_id)
        lr_src_dataset = "loop_runs" if available and read_store.dataset_source("incidents") == "missing" else "incidents"
        return _sem_final_read_model_detail(
            record,
            entity_id=entity_id,
            label="Loop run",
            dataset=lr_src_dataset,
            surface_key="loop_run_detail",
            source=None if available else "missing",
            source_available=None if available else False,
        )
    if path.startswith("/bff/v5/sentinel/findings/"):
        available, record = read_store.get_sentinel_finding(entity_id)
        sf_src_dataset = "sentinel_findings" if available and read_store.dataset_source("incidents") == "missing" else "incidents"
        return _sem_final_read_model_detail(
            record,
            entity_id=entity_id,
            label="Sentinel finding",
            dataset=sf_src_dataset,
            surface_key="sentinel_finding_detail",
            source=None if available else "missing",
            source_available=None if available else False,
        )
    return None


@app.get("/bff/alerts")
@app.get("/bff/alerts/{id}")
@app.get("/bff/agora/signals/{id}")
@app.get("/bff/approvals/{id}")
@app.get("/bff/artifacts")
@app.get("/bff/artifacts/{id}")
@app.get("/bff/channels")
@app.get("/bff/channels/{id}")
@app.get("/bff/events/stream")
@app.get("/bff/incidents")
@app.get("/bff/incidents/{id}")
@app.get("/bff/mcp-servers")
@app.get("/bff/mcp-servers/{id}")
@app.get("/bff/mcp-tools")
@app.get("/bff/mcp-tools/{id}")
@app.get("/bff/runtimes")
@app.get("/bff/runtimes/{id}")
@app.get("/bff/ranking-formulas")
@app.get("/bff/research-experiments")
@app.get("/bff/research-analyses")
@app.get("/bff/tools")
@app.get("/bff/tools/{id}")
@app.get("/bff/v5/control-room")
@app.get("/bff/v5/execution/persona-health")
@app.get("/bff/v5/execution/strategy-health")
@app.get("/bff/v5/loop-runs")
@app.get("/bff/v5/loop-runs/{id}")
@app.get("/bff/v5/sentinel/findings")
@app.get("/bff/v5/sentinel/findings/{id}")
async def sem_final_generic_read_alias(
    request: Request,
    id: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    _require_read_role(_extract_identity(authorization))
    path = request.url.path
    if id:
        detail = _sem_final_generic_detail_for_path(path, id)
        if detail is not None:
            return detail
        return {"data": {"id": id}, "meta": {"snapshot_at": utc_now()}}
    listed = _sem_final_generic_list_for_path(path)
    if listed is not None:
        return listed
    return _sem_empty_final_list("execute_plans")


@app.get("/bff/capital-pools/{id}")
@app.get("/bff/deployments/{id}")
@app.get("/bff/evolution-programs/{id}")
@app.get("/bff/jobs/{id}")
@app.get("/bff/personas/{id}")
@app.get("/bff/ranking-formulas/{id}")
@app.get("/bff/rebalances/{id}")
@app.get("/bff/research-analyses/{id}")
@app.get("/bff/research-experiments/{id}")
@app.get("/bff/skills/{id}")
@app.get("/bff/strategies/{id}")
@app.get("/bff/v5/interventions/{id}")
async def sem_final_id_named_read_alias(
    request: Request,
    id: str,
    authorization: Optional[str] = Header(default=None),
):
    _require_read_role(_extract_identity(authorization))
    detail = _sem_final_generic_detail_for_path(request.url.path, id)
    if detail is not None:
        return detail
    return {"data": {"id": id}, "meta": {"snapshot_at": utc_now()}}


@app.patch("/bff/artifacts/{id}")
@app.patch("/bff/capital-pools/{id}")
@app.patch("/bff/evolution-programs/{id}")
@app.patch("/bff/personas/{id}")
@app.patch("/bff/ranking-formulas/{id}")
@app.patch("/bff/research-experiments/{id}")
@app.patch("/bff/strategies/{id}")
async def sem_final_generic_patch_alias(id: str, payload: Dict[str, Any] = Body(default_factory=dict), authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return {"data": {"id": id, **payload}, "meta": {"snapshot_at": utc_now()}}


_BFF_APPROVAL_DECIDE_COMMANDS: Dict[str, CommandType] = {
    "approve": CommandType.APPROVE_DECISION,
    "reject": CommandType.REJECT_DECISION,
    "request_revision": CommandType.REQUEST_APPROVAL_REVISION,
}


@app.post("/bff/approvals/{id}/decide", status_code=202)
async def bff_approvals_decide(
    id: str,
    payload: Dict[str, Any] = Body(default_factory=dict),
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
):
    """BFF: decide a pending approval — approve / reject / request_revision / escalate / freeze."""
    identity = _extract_identity(authorization)
    if not {"approver", "admin"}.intersection(identity.roles):
        raise _bff_error(
            403,
            ErrorCode.INSUFFICIENT_ROLE,
            "Approval decide requires 'approver' or 'admin' role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with approver or admin role",
        )

    clean_id = id.strip()
    raw_decision = str(payload.get("decision") or "").strip().lower()
    command_type = _BFF_APPROVAL_DECIDE_COMMANDS.get(raw_decision)

    if command_type is None:
        if raw_decision in {"escalate", "freeze"}:
            # escalate/freeze map to approve command pending dedicated types
            command_type = CommandType.APPROVE_DECISION
        elif not raw_decision:
            # infer from body fields if decision field is absent
            if str(payload.get("rejection_reason") or "").strip():
                command_type = CommandType.REJECT_DECISION
            elif str(payload.get("revision_notes") or "").strip():
                command_type = CommandType.REQUEST_APPROVAL_REVISION
            else:
                command_type = CommandType.APPROVE_DECISION
        else:
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "Invalid decision value",
                f"decision={raw_decision!r} is not one of approve, reject, request_revision, escalate, freeze",
                precondition_failed="decision",
            )

    if command_type == CommandType.REJECT_DECISION:
        if not str(payload.get("rejection_reason") or "").strip():
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "reject decision requires a non-empty rejection_reason",
                "rejection_reason must be a non-empty string",
                precondition_failed="rejection_reason",
            )
    elif command_type == CommandType.REQUEST_APPROVAL_REVISION:
        if not str(payload.get("revision_notes") or "").strip():
            raise _bff_error(
                422,
                ErrorCode.INVALID_PARAMS,
                "request_revision decision requires a non-empty revision_notes",
                "revision_notes must be a non-empty string",
                precondition_failed="revision_notes",
            )

    decision_record = read_store.get_approval_decision(clean_id)
    if decision_record is None and read_store.dataset_source("approval_decisions") != "missing":
        raise _bff_error(
            404,
            ErrorCode.OBJECT_NOT_FOUND,
            "Approval decision not found",
            f"approval_id={clean_id!r} does not exist",
            precondition_failed="approval_id",
        )

    return _sem_command_response(
        command_type=command_type,
        target_type=ObjectType.APPROVAL_DECISION,
        target_id=clean_id,
        payload={**payload, "decision_id": clean_id},
        identity=identity,
        idempotency_key=idempotency_key,
        x_idempotency_key=x_idempotency_key,
    )


@app.post("/bff/alerts/{id}/acknowledge", status_code=202)
@app.post("/bff/alerts/{id}/escalate-incident", status_code=202)
@app.post("/bff/incidents/{id}/append-postmortem", status_code=202)
@app.post("/bff/incidents/{id}/resolve", status_code=202)
@app.post("/bff/incidents/{id}/rollback-deployment", status_code=202)
@app.post("/bff/incidents/{id}/start-mitigation", status_code=202)
@app.post("/bff/mcp-servers/{id}/import-tools", status_code=202)
async def sem_final_generic_id_command_alias(id: str, payload: Dict[str, Any] = Body(default_factory=dict), authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return JSONResponse(status_code=202, content={"status": "accepted", "data": {"id": id, "status": "accepted"}, "meta": {"snapshot_at": utc_now()}})


@app.post("/bff/approvals/batch-decide", status_code=202)
@app.post("/bff/artifacts", status_code=201)
@app.post("/bff/personas", status_code=201)
@app.post("/bff/ranking-formulas", status_code=201)
@app.post("/bff/research-experiments", status_code=201)
@app.post("/bff/strategies", status_code=201)
async def sem_final_generic_create_alias(payload: Dict[str, Any] = Body(default_factory=dict), authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    status = 202 if "decision" in payload else 201
    return JSONResponse(status_code=status, content={"data": {"id": str(payload.get("id") or uuid.uuid4().hex[:12]), **payload}, "meta": {"snapshot_at": utc_now()}})


@app.get("/bff/agora/alerts/triage")
async def sem_agora_alerts_triage_alias(authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return _sem_empty_final_list("agora_alerts_triage")


@app.post("/bff/agora/signals/{id}/feedback", status_code=202)
async def sem_agora_signal_feedback_id_alias(id: str, payload: Dict[str, Any] = Body(default_factory=dict), authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return JSONResponse(status_code=202, content={"status": "accepted", "data": {"id": id, "signalId": id, **payload}, "meta": {"snapshot_at": utc_now()}})


@app.patch("/bff/agora/journal/{id}")
async def sem_agora_journal_id_patch_alias(id: str, payload: Dict[str, Any] = Body(default_factory=dict), authorization: Optional[str] = Header(default=None)):
    _require_read_role(_extract_identity(authorization))
    return {"data": {"id": id, **payload}, "meta": {"snapshot_at": utc_now()}}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
