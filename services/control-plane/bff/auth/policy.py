"""Standalone auth and session policy domain module for the BFF control-plane.

This module encapsulates BFF identity extraction, tenant scoping, role/capability
evaluation, dev-login policy, error mapping, and session lifecycle guards.
It can be imported independently without importing the BFF composition root (main.py).
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Set

from fastapi import HTTPException

from ..models import (
    BffErrorEnvelope,
    BffErrorPayload,
    ErrorCode,
    ErrorDetail,
    EVIDENCE_CAPABILITY_MAP,
    OperatorIdentity,
    utc_now as default_utc_now,
)

try:
    from services.foundation import AuditAction, ErrorEnvelope, PolicyDecision
except ImportError:
    AuditAction = None  # type: ignore[assignment,misc]
    ErrorEnvelope = None  # type: ignore[assignment,misc]
    PolicyDecision = None  # type: ignore[assignment,misc]

log = logging.getLogger(__name__)

# Constants and environments
_BFF_VALID_AUTH_MODES = frozenset({"strict", "permissive"})
_PRODUCTION_STRICT_ENVIRONMENTS = frozenset({
    "canary",
    "live",
    "prod",
    "production",
    "staging-live",
})
_BFF_AUTH_STUB_ENV = "PANTHEON_BFF_AUTH_STUB"
_BFF_STUB_LEGACY_BARE_TOKENS_ENV = "PANTHEON_BFF_STUB_LEGACY_BARE_TOKENS"
_BFF_STUB_CAPABILITY_ROLES = frozenset({"admin", "operator"})
_READ_ROLES = frozenset({"viewer", "view_only", "operator", "approver", "admin", "reviewer"})
_WRITE_ROLES = frozenset({"operator", "approver", "admin", "reviewer"})

_DEV_LOGIN_IDENTITY_DEFS = {
    "viewer": {"roles": ("viewer",), "subject_suffix": "viewer"},
    "operator": {"roles": ("operator",), "subject_suffix": "operator"},
    # Keep the UI role and the Governance owner's ordinary review role aligned
    # for this configured dev account; other dev identities stay distinct.
    "approver": {"roles": ("approver", "governance_reviewer"), "subject_suffix": "approver"},
    "risk_owner": {"roles": ("risk_owner",), "subject_suffix": "risk-owner"},
    "operator_a": {"roles": ("operator",), "subject_suffix": "operator-a"},
    "operator_b": {"roles": ("operator",), "subject_suffix": "operator-b"},
}

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
    "viewer": [
        "metric.read",
        "strategy.view",
        "persona.view",
    ],
}

_ERROR_CODE_BY_STATUS = {
    400: ErrorCode.VALIDATION_FAILED.value,
    401: ErrorCode.AUTH_REQUIRED.value,
    403: ErrorCode.FORBIDDEN.value,
    404: ErrorCode.RESOURCE_NOT_FOUND.value,
    409: ErrorCode.RESOURCE_CONFLICT.value,
    413: ErrorCode.REQUEST_TOO_LARGE.value,
    422: ErrorCode.VALIDATION_FAILED.value,
    428: ErrorCode.PRECONDITION_FAILED.value,
    429: ErrorCode.RATE_LIMITED.value,
    500: ErrorCode.INTERNAL_ERROR.value,
    502: ErrorCode.UPSTREAM_ERROR.value,
    503: ErrorCode.DEPENDENCY_UNAVAILABLE.value,
    504: ErrorCode.UPSTREAM_TIMEOUT.value,
}

_LEGACY_ERROR_CODE_ALIASES = {
    "INVALID_REQUEST": ErrorCode.VALIDATION_FAILED.value,
    "INVALID_PARAMS": ErrorCode.VALIDATION_FAILED.value,
    "MFA_VALIDATION_FAILED": ErrorCode.VALIDATION_FAILED.value,
    "INVALID_TOKEN": ErrorCode.AUTH_REQUIRED.value,
    "AUTH_TOKEN_FORMAT": ErrorCode.AUTH_REQUIRED.value,
    "AUTH_JWT_EXPIRED": ErrorCode.AUTH_EXPIRED.value,
    "INSUFFICIENT_ROLE": ErrorCode.FORBIDDEN.value,
    "PERMISSION_DENIED": ErrorCode.FORBIDDEN.value,
    "CAPABILITY_MISSING": ErrorCode.FORBIDDEN.value,
    "OBJECT_NOT_FOUND": ErrorCode.RESOURCE_NOT_FOUND.value,
    "NOT_FOUND": ErrorCode.RESOURCE_NOT_FOUND.value,
    "INVALID_STATE": ErrorCode.OPERATION_NOT_ALLOWED.value,
    "HIGH_RISK_QUERY_REFUSED": ErrorCode.OPERATION_NOT_ALLOWED.value,
    "CONCURRENT_MODIFICATION": ErrorCode.RESOURCE_CONFLICT.value,
    "STATE_CONFLICT": ErrorCode.RESOURCE_CONFLICT.value,
    "DOWNSTREAM_UNAVAILABLE": ErrorCode.DEPENDENCY_UNAVAILABLE.value,
    "DOWNSTREAM_TIMEOUT": ErrorCode.UPSTREAM_TIMEOUT.value,
    "COMMAND_TIMEOUT": ErrorCode.UPSTREAM_TIMEOUT.value,
    "DOWNSTREAM_ERROR": ErrorCode.UPSTREAM_ERROR.value,
    "PRECONDITION_NOT_MET": ErrorCode.PRECONDITION_FAILED.value,
    "CONFIRM_TOKEN_REQUIRED": ErrorCode.CONFIRMATION_REQUIRED.value,
    "APPROVAL_REQUIRED": ErrorCode.HUMAN_GATE_PENDING.value,
    "TWO_MAN_REQUIRED": ErrorCode.TWO_MAN_SIGNATURE_REQUIRED.value,
    "MFA_REQUIRED": ErrorCode.AUTH_REQUIRED.value,
    "SSE_REPLAY_UNAVAILABLE": ErrorCode.RESOURCE_CONFLICT.value,
}

_PACK_D_D21_ERROR_BEHAVIOR: Dict[str, Dict[str, bool]] = {
    ErrorCode.RESOURCE_NOT_FOUND.value: {"retryable": False, "userActionable": True},
    ErrorCode.AUTH_REQUIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.AUTH_EXPIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.FORBIDDEN.value: {"retryable": False, "userActionable": False},
    ErrorCode.RATE_LIMITED.value: {"retryable": True, "userActionable": True},
    ErrorCode.VALIDATION_FAILED.value: {"retryable": False, "userActionable": True},
    ErrorCode.BUSINESS_RULE_VIOLATION.value: {"retryable": False, "userActionable": True},
    ErrorCode.IDEMPOTENCY_CONFLICT.value: {"retryable": False, "userActionable": True},
    ErrorCode.PRECONDITION_FAILED.value: {"retryable": False, "userActionable": True},
    ErrorCode.CONFIRMATION_REQUIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.TWO_MAN_SIGNATURE_REQUIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.HUMAN_GATE_PENDING.value: {"retryable": False, "userActionable": True},
    ErrorCode.HUMAN_GATE_REJECTED.value: {"retryable": False, "userActionable": True},
    ErrorCode.HUMAN_GATE_EXPIRED.value: {"retryable": False, "userActionable": True},
    ErrorCode.RESOURCE_CONFLICT.value: {"retryable": False, "userActionable": True},
    ErrorCode.OPERATION_NOT_ALLOWED.value: {"retryable": False, "userActionable": True},
    ErrorCode.DEPENDENCY_UNAVAILABLE.value: {"retryable": True, "userActionable": True},
    ErrorCode.UPSTREAM_TIMEOUT.value: {"retryable": True, "userActionable": True},
    ErrorCode.UPSTREAM_ERROR.value: {"retryable": True, "userActionable": True},
    ErrorCode.INTERNAL_ERROR.value: {"retryable": False, "userActionable": False},
    ErrorCode.NOT_IMPLEMENTED.value: {"retryable": False, "userActionable": False},
    ErrorCode.MAINTENANCE_MODE.value: {"retryable": True, "userActionable": True},
    ErrorCode.KILL_SWITCH_ACTIVE.value: {"retryable": False, "userActionable": False},
    ErrorCode.SAFE_MODE_ACTIVE.value: {"retryable": False, "userActionable": False},
    ErrorCode.DEGRADED_READ_ONLY.value: {"retryable": False, "userActionable": False},
    ErrorCode.REQUEST_TOO_LARGE.value: {"retryable": False, "userActionable": True},
}


# Generic helpers
def bool_from_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def first_nonblank(*values: Any) -> Optional[str]:
    for value in values:
        clean = str(value or "").strip()
        if clean:
            return clean
    return None


def dedupe_nonblank_strings(values: List[Any]) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def split_claim_string(value: str) -> List[str]:
    clean = value.strip()
    if not clean:
        return []
    separator_pattern = r"[\s,]+" if "," not in clean else r"\s*,\s*"
    return [part.strip() for part in re.split(separator_pattern, clean) if part.strip()]


def env_csv(name: str) -> List[str]:
    return dedupe_nonblank_strings(split_claim_string(os.getenv(name, "")))


# Error formatting
def status_error_code(status_code: int) -> str:
    return _ERROR_CODE_BY_STATUS.get(status_code, ErrorCode.VALIDATION_FAILED.value)


def canonical_error_code_value(code: Any, *, status_code: Optional[int] = None) -> str:
    raw = str(getattr(code, "value", code) or "").strip()
    if not raw and status_code is not None:
        return status_error_code(status_code)
    candidate = _LEGACY_ERROR_CODE_ALIASES.get(raw, raw)
    try:
        return ErrorCode(candidate).value
    except ValueError:
        if status_code is not None:
            return status_error_code(status_code)
        return ErrorCode.INTERNAL_ERROR.value


def pack_d_error_metadata(code: Any, *, status_code: Optional[int] = None) -> Dict[str, Any]:
    code_value = canonical_error_code_value(code, status_code=status_code)
    behavior = _PACK_D_D21_ERROR_BEHAVIOR.get(
        code_value,
        _PACK_D_D21_ERROR_BEHAVIOR[ErrorCode.INTERNAL_ERROR.value],
    )
    return {
        "code": code_value,
        "i18nKey": f"errors.{code_value}",
        "retryable": behavior["retryable"],
        "userActionable": behavior["userActionable"],
    }


def bff_error(
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
    metadata = pack_d_error_metadata(code, status_code=status_code)
    body = BffErrorEnvelope(
        error=BffErrorPayload(
            code=ErrorCode(metadata["code"]),
            i18nKey=metadata["i18nKey"],
            message=message,
            retryable=metadata["retryable"],
            userActionable=metadata["userActionable"],
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


# Auth mode and commit helpers
def bff_auth_mode() -> str:
    raw = os.getenv("PANTHEON_BFF_AUTH_MODE", "strict").strip().lower() or "strict"
    if raw not in _BFF_VALID_AUTH_MODES:
        return "strict"
    return raw


def is_production_strict_mode() -> bool:
    env_name = os.getenv("PANTHEON_ENV", "").strip().lower()
    deployment_stage = os.getenv("PANTHEON_DEPLOYMENT_STAGE", "").strip().lower()
    return bff_auth_mode() == "strict" and (
        env_name in _PRODUCTION_STRICT_ENVIRONMENTS
        or deployment_stage in _PRODUCTION_STRICT_ENVIRONMENTS
    )


def bff_auth_stub_enabled() -> bool:
    return bool_from_env(_BFF_AUTH_STUB_ENV) and bff_auth_mode() != "strict"


def bff_source_commit() -> str:
    commit = os.environ.get("BFF_COMMIT") or os.environ.get("GIT_SHA")
    if not commit or commit == "unknown":
        git_dir = "/workspace/status-root/.git"
        if os.path.exists(git_dir):
            try:
                head_path = os.path.join(git_dir, "HEAD")
                if os.path.exists(head_path):
                    with open(head_path, "r") as f:
                        ref = f.read().strip()
                    if ref.startswith("ref: "):
                        ref_path = os.path.join(git_dir, ref[5:])
                        if os.path.exists(ref_path):
                            with open(ref_path, "r") as f:
                                commit = f.read().strip()
                        else:
                            packed_path = os.path.join(git_dir, "packed-refs")
                            if os.path.exists(packed_path):
                                ref_name = ref[5:]
                                with open(packed_path, "r") as f:
                                    for line in f:
                                        if line.startswith("#") or not line.strip():
                                            continue
                                        parts = line.strip().split()
                                        if len(parts) == 2 and parts[1] == ref_name:
                                            commit = parts[0]
                                            break
                    else:
                        commit = ref
            except Exception:
                pass
    return str(commit or "unknown")


# Dev login policy
def dev_login_forbidden_environment() -> bool:
    env_name = os.getenv("PANTHEON_ENV", "").strip().lower()
    deployment_stage = os.getenv("PANTHEON_DEPLOYMENT_STAGE", "").strip().lower()
    forbidden = _PRODUCTION_STRICT_ENVIRONMENTS | {"staging"}
    return env_name in forbidden or deployment_stage in forbidden


def dev_login_identity_registry() -> Dict[str, Dict[str, Any]]:
    registry: Dict[str, Dict[str, Any]] = {}
    for name, base in _DEV_LOGIN_IDENTITY_DEFS.items():
        env_prefix = f"PANTHEON_BFF_DEV_LOGIN_{name.upper()}"
        client_id = os.getenv(f"{env_prefix}_CLIENT_ID", "").strip()
        client_secret = os.getenv(f"{env_prefix}_CLIENT_SECRET", "").strip()
        if not (client_id and client_secret) and name == "operator":
            client_id = first_nonblank(
                os.getenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_ID"),
                os.getenv("PANTHEON_BFF_OIDC_CLIENT_ID"),
            ) or ""
            client_secret = first_nonblank(
                os.getenv("PANTHEON_BFF_DEV_LOGIN_CLIENT_SECRET"),
                os.getenv("PANTHEON_BFF_OIDC_CLIENT_SECRET"),
            ) or ""
        if not (client_id and client_secret):
            continue

        tenant_id = first_nonblank(
            os.getenv(f"{env_prefix}_TENANT_ID"),
            os.getenv("PANTHEON_BFF_TENANT_ID"),
            os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
            os.getenv("PANTHEON_TENANT_ID"),
            "tenant-dev",
        )
        allowed_tenants = env_csv(f"{env_prefix}_ALLOWED_TENANTS") or [tenant_id]
        if tenant_id not in allowed_tenants:
            allowed_tenants = [tenant_id] + list(allowed_tenants)

        registry[name] = {
            "identity": name,
            "client_id": client_id,
            "client_secret": client_secret,
            "roles": sorted(base["roles"]),
            "subject": f"pantheon-dev-{base['subject_suffix']}",
            "tenant_id": tenant_id,
            "allowed_tenants": allowed_tenants,
        }
    return registry


def dev_login_enabled() -> bool:
    if dev_login_forbidden_environment():
        return False
    return bool(dev_login_identity_registry())


# Role and capability policy
def require_read_role(identity: OperatorIdentity) -> None:
    if not _READ_ROLES.intersection(identity.roles):
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Read access requires viewer-level role",
            "Operator does not hold the required role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with viewer, operator, approver, admin, or reviewer role",
        )


def require_operator_role(identity: OperatorIdentity) -> None:
    if not _WRITE_ROLES.intersection(identity.roles):
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Operator command access requires operator-level role",
            "Operator does not hold the required command role",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator, approver, admin, or reviewer role",
        )


def stub_identity_capabilities(
    token_capabilities: List[str],
    roles: List[str],
) -> List[str]:
    normalized_roles = {str(role or "").strip().lower() for role in roles}
    if not normalized_roles.intersection(_BFF_STUB_CAPABILITY_ROLES):
        return []
    return dedupe_nonblank_strings(
        [
            *token_capabilities,
            *env_csv("PANTHEON_BFF_STUB_CAPABILITIES"),
        ]
    )


def with_structured_identity_capabilities(identity: OperatorIdentity) -> OperatorIdentity:
    if identity.token_kind != "structured":
        return identity
    claims = dict(identity.claims or {})
    raw_capabilities = claims.get("capabilities") or claims.get("capability") or []
    if isinstance(raw_capabilities, str):
        token_capabilities = split_claim_string(raw_capabilities)
    elif isinstance(raw_capabilities, list):
        token_capabilities = [str(cap) for cap in raw_capabilities]
    else:
        token_capabilities = []
    capabilities = stub_identity_capabilities(token_capabilities, identity.roles)
    if capabilities:
        claims["capabilities"] = capabilities
    else:
        claims.pop("capabilities", None)
        claims.pop("capability", None)
    try:
        return identity.model_copy(update={"claims": claims})
    except AttributeError:
        return OperatorIdentity(
            operator_id=identity.operator_id,
            roles=identity.roles,
            mfa_verified=identity.mfa_verified,
            claims=claims,
            token_kind=identity.token_kind,
        )


def capabilities_for_identity(identity: OperatorIdentity) -> List[str]:
    caps: List[str] = []
    for role in identity.roles:
        mapped = _ROLE_CAPABILITY_MAP.get(role)
        if mapped:
            caps.extend(mapped)
    seen = set()
    result: List[str] = []
    for c in caps:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


# Identity extraction
def extract_identity_stub(authorization: Optional[str]) -> OperatorIdentity:
    if not authorization or not authorization.startswith("Bearer "):
        raise bff_error(
            status_code=401,
            code=ErrorCode.AUTH_REQUIRED,
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
            suggestion="Re-authenticate and include a valid Bearer token",
        )
    token = authorization[len("Bearer "):].strip()
    if not token:
        raise bff_error(
            status_code=401,
            code=ErrorCode.AUTH_REQUIRED,
            message="Missing or invalid Authorization header",
            reason="Token is absent or not a Bearer token",
            suggestion="Re-authenticate and include a valid Bearer token",
        )
    if ":" not in token:
        allowed_bare_tokens = set(env_csv(_BFF_STUB_LEGACY_BARE_TOKENS_ENV))
        if token not in allowed_bare_tokens:
            raise bff_error(
                status_code=403,
                code=ErrorCode.FORBIDDEN,
                message="Stub bearer token must include explicit roles",
                reason="AUTH_STUB_TOKEN_NO_ROLES",
                suggestion="Use Bearer <operator_id>:<comma_roles> for dev stub auth",
            )
        lowered = token.lower()
        inferred_roles = ["operator"]
        if lowered.startswith("admin_"):
            inferred_roles = ["admin"]
        elif lowered.startswith("analyst_"):
            inferred_roles = ["analyst"]
        elif lowered.startswith("viewer_"):
            inferred_roles = ["viewer"]
        capabilities = stub_identity_capabilities([], inferred_roles)
        return OperatorIdentity(
            operator_id=token,
            roles=inferred_roles,
            mfa_verified="mfa" in lowered,
            claims={"sub": token, "roles": inferred_roles, "capabilities": capabilities},
            token_kind="stub",
        )
    parts = token.split(":")
    operator_id = parts[0] if parts else "unknown"
    roles = parts[1].split(",") if len(parts) > 1 else ["operator"]

    mfa_verified = False
    tenant_ids = None
    token_capabilities = []

    if len(parts) > 2:
        if parts[2] == "mfa":
            mfa_verified = True
            if len(parts) > 3 and parts[3]:
                token_capabilities = parts[3].split(",")
            if len(parts) > 4 and parts[4]:
                tenant_ids = parts[4].split(",")
        else:
            tenant_ids = parts[2].split(",")
            if len(parts) > 3 and parts[3]:
                token_capabilities = parts[3].split(",")

    capabilities = stub_identity_capabilities(token_capabilities, roles)
    claims = {"sub": operator_id, "roles": roles, "capabilities": capabilities}
    if tenant_ids:
        claims["tenant_ids"] = tenant_ids
        claims["tenantIds"] = tenant_ids

    return OperatorIdentity(
        operator_id=operator_id,
        roles=roles,
        mfa_verified=mfa_verified,
        claims=claims,
        token_kind="stub",
    )


def extract_identity_jwt(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
) -> OperatorIdentity:
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
        "PANTHEON_RUNTIME_JWKS_URI": os.getenv("PANTHEON_BFF_JWKS_URI", ""),
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": os.getenv("PANTHEON_BFF_OIDC_DISCOVERY_URL", ""),
        "PANTHEON_RUNTIME_OIDC_ISSUER": os.getenv("PANTHEON_BFF_OIDC_ISSUER", ""),
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": os.getenv("PANTHEON_BFF_OIDC_AUDIENCE", ""),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": os.getenv("PANTHEON_BFF_ROLE_CLAIMS", ""),
        "PANTHEON_RUNTIME_ROLE_MAP": os.getenv("PANTHEON_BFF_ROLE_MAP", ""),
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": os.getenv("PANTHEON_BFF_ROLE_MAP_MODE", ""),
        "PANTHEON_RUNTIME_MFA_CLAIMS": os.getenv("PANTHEON_BFF_MFA_CLAIMS", ""),
        "PANTHEON_RUNTIME_MFA_VALUES": os.getenv("PANTHEON_BFF_MFA_VALUES", ""),
        "PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED": os.getenv(
            "PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED",
            "false",
        ),
    }

    try:
        raw_token = str(authorization or "").split(None, 1)[1]
        header_segment = raw_token.split(".", 1)[0]
        header_segment += "=" * (-len(header_segment) % 4)
        unverified_alg = str(
            json.loads(base64.urlsafe_b64decode(header_segment).decode("utf-8")).get("alg")
            or ""
        ).upper()
    except Exception:
        unverified_alg = ""
    if unverified_alg == "HS256":
        bff_env["PANTHEON_RUNTIME_JWKS_URI"] = ""
        bff_env["PANTHEON_RUNTIME_OIDC_DISCOVERY_URL"] = ""
        bff_env["PANTHEON_RUNTIME_ROLE_CLAIMS"] = "roles,role"
        bff_env["PANTHEON_RUNTIME_ROLE_MAP"] = ""
        bff_env["PANTHEON_RUNTIME_ROLE_MAP_MODE"] = "passthrough"
        bff_env["PANTHEON_RUNTIME_REQUIRE_EMAIL_VERIFIED"] = "false"

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
            code = ErrorCode.FORBIDDEN
        elif exc.code == "AUTH_JWT_EXPIRED":
            code = ErrorCode.AUTH_EXPIRED
        elif exc.code in ("MFA_REQUIRED", "MFA_VALIDATION_FAILED"):
            code = ErrorCode.AUTH_REQUIRED
        else:
            code = ErrorCode.AUTH_REQUIRED
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
        raise bff_error(
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
        raise bff_error(
            status_code=401,
            code=ErrorCode.AUTH_REQUIRED,
            message="JWT subject claim is required",
            reason="AUTH_JWT_SUBJECT_MISSING",
            suggestion="Re-authenticate with a valid JWT bearer token",
        )
    identity = OperatorIdentity(
        operator_id=ctx.actor_id,
        roles=sorted(ctx.roles),
        mfa_verified=ctx.mfa_verified,
        claims=dict(ctx.claims),
        token_kind=ctx.token_kind,
    )
    return with_structured_identity_capabilities(identity)


def extract_identity(
    authorization: Optional[str],
    mfa_token: Optional[str] = None,
    session_cookie: Optional[str] = None,
) -> OperatorIdentity:
    if bff_auth_stub_enabled():
        if authorization and authorization.startswith("Bearer "):
            raw = authorization[len("Bearer "):].strip()
            if raw.count(".") == 2:
                try:
                    return extract_identity_jwt(authorization, mfa_token=mfa_token)
                except Exception:
                    pass
        if not authorization and session_cookie:
            try:
                identity = extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
                return identity.model_copy(update={"token_kind": "cookie"})
            except Exception:
                pass
        return extract_identity_stub(authorization)
    if not authorization and session_cookie:
        identity = extract_identity_jwt(f"Bearer {session_cookie}", mfa_token=mfa_token)
        return identity.model_copy(update={"token_kind": "cookie"})
    return extract_identity_jwt(authorization, mfa_token=mfa_token)


# Tenant policy
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
        return split_claim_string(value)
    if isinstance(value, dict):
        for key in ("id", "tenant_id", "tenantId", "value", "name"):
            if value.get(key):
                return [str(value[key]).strip()]
        return []
    if isinstance(value, (list, tuple, set)):
        collected: List[Any] = []
        for item in value:
            collected.extend(_claim_value_as_strings(item))
        return dedupe_nonblank_strings(collected)
    return [str(value).strip()]


def identity_claim_strings(identity: OperatorIdentity, paths: List[str]) -> List[str]:
    values: List[Any] = []
    claims = identity.claims if isinstance(identity.claims, dict) else {}
    for path in paths:
        values.extend(_claim_value_as_strings(_claim_path_value(claims, path)))
    return dedupe_nonblank_strings(values)


def bff_me_tenant_payload(
    identity: OperatorIdentity,
    *,
    requested_tenant: Optional[str] = None,
) -> Dict[str, Any]:
    claim_default = first_nonblank(
        *identity_claim_strings(
            identity,
            [
                "tenant_id",
                "tenantId",
                "tenant.id",
                "tid",
                "org_id",
                "organization.id",
                "tenant_ids",
                "tenantIds",
            ],
        )
    )
    default_tenant = first_nonblank(
        os.getenv("PANTHEON_BFF_TENANT_ID"),
        os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
        os.getenv("PANTHEON_TENANT_ID"),
        claim_default,
        "pantheon-dev",
    )
    claim_allowed = identity_claim_strings(
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
    allowed_tenants = claim_allowed or env_csv("PANTHEON_BFF_ALLOWED_TENANTS") or [default_tenant]
    effective_tenant = first_nonblank(requested_tenant, default_tenant) or "pantheon-dev"
    if "*" not in allowed_tenants and effective_tenant not in allowed_tenants:
        raise bff_error(
            403,
            ErrorCode.FORBIDDEN,
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


# Session key and session state derivation
def get_session_id(identity: Any) -> str:
    claims = getattr(identity, "claims", None)
    if not isinstance(claims, dict):
        claims = {}
    operator_id = getattr(identity, "operator_id", None) or getattr(identity, "actor_id", "unknown")
    return first_nonblank(
        claims.get("sid"),
        claims.get("session_id"),
        claims.get("jti"),
        os.getenv("PANTHEON_SESSION_ID"),
        f"bff-session-{operator_id}",
    ) or f"bff-session-{operator_id}"


def get_session_key(identity: Any) -> str:
    operator_id = getattr(identity, "operator_id", None) or getattr(identity, "actor_id", "unknown")
    return f"operator:{operator_id}:session:{get_session_id(identity)}"


def get_legacy_session_key(identity: Any) -> str:
    operator_id = getattr(identity, "operator_id", None) or getattr(identity, "actor_id", "unknown")
    return f"operator:{operator_id}"


def get_session_state(identity: Any, store: Any) -> Dict[str, Any]:
    if store is None:
        return {}
    session_key = get_session_key(identity)
    state = store.get_session(session_key)
    if state:
        return state
    legacy_key = get_legacy_session_key(identity)
    return store.get_session(legacy_key) or {}


def raise_if_session_logged_out(
    identity: Any,
    store: Optional[Any] = None,
    *,
    error_factory: Optional[Callable[..., HTTPException]] = None,
) -> None:
    if store is None:
        return
    state = get_session_state(identity, store)
    if state.get("state") != "logged_out":
        return
    err_fn = error_factory or bff_error
    raise err_fn(
        401,
        ErrorCode.AUTH_REQUIRED,
        "Session has been logged out",
        "SESSION_LOGGED_OUT",
        precondition_failed="session_state",
        suggestion="Re-authenticate before calling BFF session endpoints",
        details_extra={
            "sessionState": "logged_out",
            "loggedOutAt": state.get("logged_out_at"),
        },
    )


raise_if_session_logged_out._canonical_guard = True  # type: ignore[attr-defined]


class SessionLogoutGuard:
    """Callable session logout guard bound to a specific SessionLifecycleStore."""

    _canonical_guard: bool = True

    def __init__(self, store: Any, error_factory: Optional[Callable[..., HTTPException]] = None) -> None:
        self.store = store
        self.error_factory = error_factory
        self._canonical_guard = True
        self.__module__ = "services.control_plane.bff.auth.policy"

    def __call__(self, identity: Any) -> None:
        raise_if_session_logged_out(identity, self.store, error_factory=self.error_factory)

    def __repr__(self) -> str:
        return f"<SessionLogoutGuard store={self.store!r}>"


def create_session_logout_guard(
    store: Any,
    *,
    error_factory: Optional[Callable[..., HTTPException]] = None,
) -> Callable[[Any], None]:
    return SessionLogoutGuard(store=store, error_factory=error_factory)


# AuthDependencies and factory
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

    def __post_init__(self) -> None:
        if self.session_lifecycle_store is not None:
            guard = self.raise_if_session_logged_out
            is_canonical = (
                isinstance(guard, SessionLogoutGuard)
                or getattr(guard, "_canonical_guard", False)
                or guard is raise_if_session_logged_out
            )
            if is_canonical and getattr(guard, "store", None) is not self.session_lifecycle_store:
                new_guard = create_session_logout_guard(
                    self.session_lifecycle_store,
                    error_factory=self.bff_error,
                )
                object.__setattr__(self, "raise_if_session_logged_out", new_guard)


def create_auth_dependencies(
    *,
    session_lifecycle_store: Any = None,
    bff_error: Optional[Callable[..., HTTPException]] = None,
    dev_login_forbidden_environment: Optional[Callable[[], bool]] = None,
    dev_login_identity_registry: Optional[Callable[[], Dict[str, Any]]] = None,
    extract_identity: Optional[Callable[..., Any]] = None,
    require_read_role: Optional[Callable[[Any], None]] = None,
    raise_if_session_logged_out: Optional[Callable[[Any], None]] = None,
    bff_me_tenant_payload: Optional[Callable[..., Dict[str, Any]]] = None,
    capabilities_for_identity: Optional[Callable[[Any], List[str]]] = None,
    bff_auth_stub_enabled: Optional[Callable[[], bool]] = None,
    bff_auth_mode: Optional[Callable[[], str]] = None,
    bff_source_commit: Optional[Callable[[], str]] = None,
    write_roles: Optional[frozenset[str]] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> AuthDependencies:
    """Build typed AuthDependencies with canonical policy defaults."""
    effective_error = bff_error or default_bff_error
    effective_guard = raise_if_session_logged_out
    if effective_guard is None:
        effective_guard = create_session_logout_guard(
            session_lifecycle_store,
            error_factory=effective_error,
        )
    return AuthDependencies(
        bff_error=effective_error,
        dev_login_forbidden_environment=dev_login_forbidden_environment or default_dev_login_forbidden_environment,
        dev_login_identity_registry=dev_login_identity_registry or default_dev_login_identity_registry,
        extract_identity=extract_identity or default_extract_identity,
        require_read_role=require_read_role or default_require_read_role,
        raise_if_session_logged_out=effective_guard,
        session_lifecycle_store=session_lifecycle_store,
        bff_me_tenant_payload=bff_me_tenant_payload or default_bff_me_tenant_payload,
        capabilities_for_identity=capabilities_for_identity or default_capabilities_for_identity,
        bff_auth_stub_enabled=bff_auth_stub_enabled or default_bff_auth_stub_enabled,
        bff_auth_mode=bff_auth_mode or default_bff_auth_mode,
        bff_source_commit=bff_source_commit or default_bff_source_commit,
        write_roles=write_roles if write_roles is not None else frozenset(_WRITE_ROLES),
        utc_now=utc_now or default_utc_now,
    )


# Canonical aliases
default_bff_error = bff_error
default_dev_login_forbidden_environment = dev_login_forbidden_environment
default_dev_login_identity_registry = dev_login_identity_registry
default_extract_identity = extract_identity
default_require_read_role = require_read_role
default_bff_me_tenant_payload = bff_me_tenant_payload
default_capabilities_for_identity = capabilities_for_identity
default_bff_auth_stub_enabled = bff_auth_stub_enabled
default_bff_auth_mode = bff_auth_mode
default_bff_source_commit = bff_source_commit
