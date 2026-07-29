"""BFF boundary for protected product-closeout verdict issuance and revocation.

The router deliberately accepts an already-authenticated ``OperatorIdentity``
dependency from the host BFF.  It never trusts actor identifiers or roles from
the request body, never exposes private-key material, and rejects dev auth
stubs, automation principals, unclassified principals, missing MFA, and
non-approver roles.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field


def _load_governance_module() -> Any:
    module_name = "_pantheon_product_closeout_verdict_governance"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    path = (
        Path(__file__).resolve().parents[1]
        / "governance"
        / "product_closeout_verdict.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load product closeout governance module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_governance = _load_governance_module()
CloseoutBinding = _governance.CloseoutBinding
HumanOpsIdentity = _governance.HumanOpsIdentity
ProductCloseoutVerdictService = _governance.ProductCloseoutVerdictService
VerdictAuthorizationError = _governance.VerdictAuthorizationError
VerdictConflictError = _governance.VerdictConflictError
VerdictError = _governance.VerdictError

_AUTHORIZED_BFF_ROLES = frozenset({"admin", "approver", "human_ops", "ops"})
_AUTOMATION_PRINCIPALS = frozenset(
    {"automation", "fleet", "service", "service_account", "workload"}
)
_TRUSTED_HUMAN_PRINCIPALS = frozenset({"human", "human_ops", "user"})
_PRINCIPAL_TYPE_CLAIMS = ("principal_type", "principalType", "actor_type", "actorType")
_PROTECTED_SESSION_KINDS = frozenset({"cookie", "jwt"})


class CloseoutVerdictIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    program_id: str = Field(min_length=1)
    catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_id: str = Field(min_length=1)
    closeout_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    target_environment: str = Field(min_length=1)
    frontend_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    bff_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    decision: str = Field(pattern=r"^(approved|rejected)$")
    ttl_seconds: int = Field(default=900, ge=1, le=3600)


class CloseoutVerdictRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


def _identity_value(identity: Any, field: str, default: Any = None) -> Any:
    if isinstance(identity, dict):
        return identity.get(field, default)
    return getattr(identity, field, default)


def _require_trusted_human_principal(claims: dict[str, Any]) -> str:
    """Require the session to classify itself as a trusted human principal.

    Classification is never inferred: a session that carries no principal
    classification claim, an unknown one, or two claims that disagree is
    refused rather than defaulted, so an unclassified admin token cannot pass
    as a human closeout authority.
    """

    declared = {
        str(claims[claim]).strip().lower()
        for claim in _PRINCIPAL_TYPE_CLAIMS
        if claim in claims and str(claims[claim] or "").strip()
    }
    if not declared:
        raise VerdictAuthorizationError(
            "protected closeout verdicts require an explicit human principal "
            "classification claim; an unclassified session is not trusted"
        )
    if len(declared) > 1:
        raise VerdictAuthorizationError(
            "conflicting principal classification claims cannot be trusted: "
            + ", ".join(sorted(declared))
        )
    principal_type = declared.pop()
    if principal_type in _AUTOMATION_PRINCIPALS:
        raise VerdictAuthorizationError(
            "automation and workload identities cannot issue closeout verdicts"
        )
    if principal_type not in _TRUSTED_HUMAN_PRINCIPALS:
        raise VerdictAuthorizationError(
            f"principal classification {principal_type!r} is not a trusted human "
            "principal for closeout verdicts"
        )
    return principal_type


def human_ops_identity_from_operator(identity: Any) -> Any:
    """Project the authenticated BFF identity into the protected issuer shape."""

    operator_id = str(_identity_value(identity, "operator_id", "") or "").strip()
    token_kind = str(_identity_value(identity, "token_kind", "") or "").strip().lower()
    raw_roles = _identity_value(identity, "roles", []) or []
    roles = {str(role).strip().lower() for role in raw_roles}
    claims = _identity_value(identity, "claims", {}) or {}
    if not isinstance(claims, dict):
        claims = {}

    if token_kind not in _PROTECTED_SESSION_KINDS:
        raise VerdictAuthorizationError(
            "protected closeout verdicts require a verified JWT or JWT-backed "
            "cookie session"
        )
    _require_trusted_human_principal(claims)
    authorized_roles = sorted(roles.intersection(_AUTHORIZED_BFF_ROLES))
    if not authorized_roles:
        raise VerdictAuthorizationError(
            "protected closeout verdicts require approver or admin authority"
        )
    actor_role = "human_ops" if "human_ops" in authorized_roles else "ops"
    projected = HumanOpsIdentity(
        actor_id=operator_id,
        actor_role=actor_role,
        authenticated=True,
        mfa_verified=(
            _identity_value(identity, "mfa_verified", False) is True
        ),
        principal_type="human",
    )
    projected.authorize()
    return projected


def _http_error(exc: VerdictError) -> HTTPException:
    if isinstance(exc, VerdictAuthorizationError):
        status_code = 403
        code = "PRODUCT_CLOSEOUT_AUTHORITY_REQUIRED"
    elif isinstance(exc, VerdictConflictError):
        status_code = 409
        code = "PRODUCT_CLOSEOUT_CONFLICT"
    else:
        status_code = 422
        code = "PRODUCT_CLOSEOUT_VERDICT_INVALID"
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": str(exc)},
    )


def create_product_closeout_verdict_router(
    *,
    service: Any,
    identity_dependency: Callable[..., Any],
) -> APIRouter:
    """Create the protected router without importing the monolithic BFF app."""

    router = APIRouter(
        prefix="/bff/governance/product-closeout-verdicts",
        tags=["product-closeout-verdicts"],
    )

    @router.post("", status_code=201)
    def issue_verdict(
        request: CloseoutVerdictIssueRequest,
        identity: Any = Depends(identity_dependency),
    ) -> dict[str, Any]:
        try:
            actor = human_ops_identity_from_operator(identity)
            binding = CloseoutBinding(
                program_id=request.program_id,
                catalog_sha256=request.catalog_sha256,
                task_id=request.task_id,
                closeout_manifest_sha256=request.closeout_manifest_sha256,
                target_environment=request.target_environment,
                frontend_sha=request.frontend_sha,
                bff_sha=request.bff_sha,
            )
            verdict = service.issue(
                binding,
                actor,
                decision=request.decision,
                ttl_seconds=request.ttl_seconds,
            )
        except VerdictError as exc:
            raise _http_error(exc) from exc
        return {
            "data": verdict,
            "meta": {
                "protected_authority": "Human/Ops",
                "replay_policy": "verify-at-review; consume-exactly-once-at-done",
            },
        }

    @router.post("/{verdict_id}/revoke")
    def revoke_verdict(
        verdict_id: str,
        request: CloseoutVerdictRevokeRequest,
        identity: Any = Depends(identity_dependency),
    ) -> dict[str, Any]:
        try:
            actor = human_ops_identity_from_operator(identity)
            record = service.revoke(verdict_id, actor, reason=request.reason)
        except VerdictError as exc:
            raise _http_error(exc) from exc
        return {"data": record}

    @router.get("/{verdict_id}")
    def inspect_verdict(
        verdict_id: str,
        identity: Any = Depends(identity_dependency),
    ) -> dict[str, Any]:
        try:
            human_ops_identity_from_operator(identity)
            result = service.inspect(verdict_id)
        except VerdictError as exc:
            raise _http_error(exc) from exc
        return {"data": result}

    return router
