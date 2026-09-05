"""
Governance Service — ApprovalDecision API

Deployable FastAPI service that exposes the canonical ApprovalDecision
governance API, write-authority matrix, and audit log read path.

Promotion, deployment, and evolution flows cite this service as the single
canonical approval surface instead of maintaining local fallbacks.

Depends on
----------
  services/control-plane/governance/approval_decision.py   (platform objects)

Routes
------
  POST   /api/governance/approvals                          propose
  GET    /api/governance/approvals                          list (filterable)
  GET    /api/governance/approvals/latest-approved          latest approved for a target
  GET    /api/governance/approvals/{decision_id}            get single decision
  POST   /api/governance/approvals/{decision_id}/review     accept review
  POST   /api/governance/approvals/{decision_id}/decide     record outcome
  POST   /api/governance/approvals/{decision_id}/revoke     revoke decided decision
  POST   /api/governance/human-gates                        create promotion gate
  GET    /api/governance/human-gates/{decision_id}          read promotion gate
  POST   /api/governance/human-gates/{decision_id}/signatures append MFA-bound signature
  POST   /api/governance/human-gates/{decision_id}/revoke     revoke promotion gate
  GET    /api/governance/freeze-orders                      list freeze orders
  GET    /api/governance/freeze-orders/{freeze_order_id}    get freeze order
  GET    /api/governance/rollbacks                          list rollback records
  GET    /api/governance/rollbacks/{rollback_id}            get rollback record
  GET    /api/governance/write-authority                    write-authority matrix
  GET    /api/governance/audit                              audit log read path
  GET    /health                                            liveness probe
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import threading
from datetime import datetime, timezone
from fastapi import FastAPI, Header, HTTPException, Query, Body, Response
from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from services.runtime_auth_inbound import (
    AuthContext,
    AuthError,
    has_claim_bound_mfa,
    validate_request_auth,
)
from services.governance.human_gate.decision_model import HumanGateDecisionError
from services.governance.promotion_readiness.signoff_api import (
    SignoffAPI,
    SignoffApiError,
)

# ---------------------------------------------------------------------------
# Platform objects — resolve relative to repo layout
# ---------------------------------------------------------------------------
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from approval_decision import (  # type: ignore
    ActorRole,
    ApprovalDecision,
    ApprovalDecisionStore,
    DecisionOutcome,
    DecisionState,
    EvidenceRef,
    OwnerMatrix,
    RiskLevel,
    TargetType,
)

# ---------------------------------------------------------------------------
# Local modules
# ---------------------------------------------------------------------------
try:
    from .models import (
        AcceptReviewRequest,
        ApprovalDecisionResponse,
        AuthzCheckRequest,
        AuthzCheckResponse,
        DecideRequest,
        ProposeApprovalRequest,
        RevokeRequest,
        WriteAuthorityEntry,
        WriteAuthorityResponse,
    )
    from .authz import evaluate_authz_request
    from .pg_store import build_approval_decision_store, build_governance_audit_store
    from .record_store import GovernanceRecordStore, build_governance_record_store
    from .human_gate_store import GovernanceHumanGateDecisionStore
    from .write_authority import is_authorized_to_decide, matrix_as_list
except ImportError:
    from models import (  # type: ignore
        AcceptReviewRequest,
        ApprovalDecisionResponse,
        AuthzCheckRequest,
        AuthzCheckResponse,
        DecideRequest,
        ProposeApprovalRequest,
        RevokeRequest,
        WriteAuthorityEntry,
        WriteAuthorityResponse,
    )
    from authz import evaluate_authz_request  # type: ignore
    from pg_store import build_approval_decision_store, build_governance_audit_store  # type: ignore
    from record_store import GovernanceRecordStore, build_governance_record_store  # type: ignore
    from human_gate_store import GovernanceHumanGateDecisionStore  # type: ignore
    from write_authority import is_authorized_to_decide, matrix_as_list  # type: ignore

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = os.getenv("GOVERNANCE_DATA_DIR", "/tmp/pantheon/governance")
os.makedirs(DATA_DIR, exist_ok=True)

AUDIT_LOG_PATH = os.path.join(DATA_DIR, "audit.jsonl")
STORE_PATH     = os.path.join(DATA_DIR, "approval_decisions.json")
FREEZE_ORDER_STORE_PATH = os.path.join(DATA_DIR, "freeze_orders.json")
ROLLBACK_STORE_PATH = os.path.join(DATA_DIR, "rollbacks.json")
HUMAN_GATE_STORE_PATH = os.path.join(DATA_DIR, "human_gate_decisions.json")
CONSULTATION_HANDOFF_STORE_PATH = os.path.join(
    DATA_DIR, "consultation_handoffs.json"
)

STORE_BACKEND = os.getenv("GOVERNANCE_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("governance")
store = build_approval_decision_store(STORE_PATH)
audit_store = build_governance_audit_store(AUDIT_LOG_PATH)
freeze_order_store: GovernanceRecordStore = build_governance_record_store(
    FREEZE_ORDER_STORE_PATH,
    table=os.getenv("GOVERNANCE_FREEZE_ORDER_STORE_TABLE", "governance.freeze_orders"),
    id_fields=("freeze_order_id", "id"),
)
rollback_store: GovernanceRecordStore = build_governance_record_store(
    ROLLBACK_STORE_PATH,
    table=os.getenv("GOVERNANCE_ROLLBACK_STORE_TABLE", "governance.rollbacks"),
    id_fields=("rollback_id", "id"),
)
human_gate_record_store: GovernanceRecordStore = build_governance_record_store(
    HUMAN_GATE_STORE_PATH,
    table=os.getenv(
        "GOVERNANCE_HUMAN_GATE_STORE_TABLE",
        "governance.human_gate_decisions",
    ),
    id_fields=("decision_id",),
)
consultation_handoff_store: GovernanceRecordStore = build_governance_record_store(
    CONSULTATION_HANDOFF_STORE_PATH,
    table=os.getenv(
        "GOVERNANCE_CONSULTATION_HANDOFF_STORE_TABLE",
        "governance.consultation_handoffs",
    ),
    id_fields=("handoff_id",),
)
human_gate_api = SignoffAPI(
    store=GovernanceHumanGateDecisionStore(human_gate_record_store)
)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pantheon Governance Service",
    version="0.1.0",
    description=(
        "Canonical ApprovalDecision governance API.  "
        "Promotion, deployment, and evolution flows reference this service "
        "instead of local fallbacks."
    ),
)
register_fastapi_health_routes(
    app,
    "governance",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    metrics=lambda: {
        "approval_count": len(store.list_all()),
        "freeze_order_count": len(freeze_order_store.list_all()),
        "rollback_count": len(rollback_store.list_all()),
        "human_gate_count": len(human_gate_record_store.list_all()),
        "consultation_handoff_count": len(consultation_handoff_store.list_all()),
    },
    details=lambda: {
        "data_dir": DATA_DIR,
        "store_path": STORE_PATH,
        "freeze_order_store_path": FREEZE_ORDER_STORE_PATH,
        "rollback_store_path": ROLLBACK_STORE_PATH,
        "human_gate_store_path": HUMAN_GATE_STORE_PATH,
        "consultation_handoff_store_path": CONSULTATION_HANDOFF_STORE_PATH,
        "store_backend": STORE_BACKEND,
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_response(d: ApprovalDecision) -> ApprovalDecisionResponse:
    return ApprovalDecisionResponse(**d.to_dict())


def _emit(
    event_type: str,
    decision: ApprovalDecision,
    detail: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        audit_store.append_event(
            event_type=event_type,
            decision_id=decision.decision_id,
            actor_id=decision.actor_id,
            actor_role=(
                decision.actor_role.value
                if isinstance(decision.actor_role, ActorRole)
                else decision.actor_role
            ),
            target_type=(
                decision.target_type.value
                if isinstance(decision.target_type, TargetType)
                else decision.target_type
            ),
            target_id=decision.target_id,
            detail=detail,
        )
    except Exception as exc:
        log.warning("Audit write failed: %s", exc)


def _get_or_404(decision_id: str) -> ApprovalDecision:
    decision = store.get(decision_id)
    if not decision:
        raise HTTPException(status_code=404, detail=f"Decision '{decision_id}' not found")
    return decision


def _record_or_404(
    record_store: GovernanceRecordStore,
    record_id: str,
    *,
    label: str,
) -> Dict[str, Any]:
    record = record_store.get(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"{label} '{record_id}' not found")
    return record


def _record_time(record: Dict[str, Any], *fields: str) -> str:
    for field in fields:
        value = record.get(field)
        if value not in (None, ""):
            return str(value)
    return ""


# ---------------------------------------------------------------------------
# Routes — freeze-order and rollback read models
# ---------------------------------------------------------------------------

@app.get(
    "/api/governance/freeze-orders",
    response_model=List[Dict[str, Any]],
    summary="List canonical freeze orders",
)
def list_freeze_orders(
    status: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    """List governance quarantine records, most-recent first."""
    records = freeze_order_store.list_all()
    if status:
        records = [record for record in records if record.get("status") == status]
    if scope:
        records = [record for record in records if record.get("scope") == scope]
    return sorted(
        records,
        key=lambda record: _record_time(record, "created_at", "issued_at", "updated_at"),
        reverse=True,
    )


@app.get(
    "/api/governance/freeze-orders/{freeze_order_id}",
    response_model=Dict[str, Any],
    summary="Get a canonical freeze order",
)
def get_freeze_order(freeze_order_id: str) -> Dict[str, Any]:
    return _record_or_404(freeze_order_store, freeze_order_id, label="Freeze order")


@app.get(
    "/api/governance/rollbacks",
    response_model=List[Dict[str, Any]],
    summary="List canonical rollback records",
)
def list_rollbacks(
    runtime_id: Optional[str] = Query(None),
    action_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
) -> List[Dict[str, Any]]:
    """List rollback request/outcome read models, most-recent first."""
    records = rollback_store.list_all()
    if runtime_id:
        records = [record for record in records if record.get("runtime_id") == runtime_id]
    if action_type:
        records = [record for record in records if record.get("action_type") == action_type]
    if status:
        records = [record for record in records if record.get("status") == status]
    return sorted(
        records,
        key=lambda record: _record_time(record, "initiated_at", "requested_at", "created_at", "updated_at"),
        reverse=True,
    )


@app.get(
    "/api/governance/rollbacks/{rollback_id}",
    response_model=Dict[str, Any],
    summary="Get a canonical rollback record",
)
def get_rollback(rollback_id: str) -> Dict[str, Any]:
    return _record_or_404(rollback_store, rollback_id, label="Rollback")


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Routes — Consultation advisory handoff intake
# ---------------------------------------------------------------------------

@app.post(
    "/api/governance/consultation-handoffs",
    response_model=Dict[str, Any],
    summary="Durably acknowledge a reviewed Consultation handoff",
)
def accept_consultation_handoff(
    response: Response,
    body: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(None),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    service_actor: Optional[str] = Header(
        None,
        alias="X-Pantheon-Service-Actor",
    ),
    tenant_header: Optional[str] = Header(None, alias="X-Pantheon-Tenant-Id"),
) -> Dict[str, Any]:
    actor, authorized_tenant = _authenticate_consultation_handoff_service(
        authorization=authorization,
        service_actor=service_actor,
        tenant_id=tenant_header,
    )
    tenant_id = _required_string(body.get("tenant_id"), "tenant_id")
    request_id = _required_string(body.get("request_id"), "request_id")
    handoff = body.get("handoff")
    if tenant_id != authorized_tenant:
        raise HTTPException(
            status_code=403,
            detail="Consultation handoff payload tenant does not match authority",
        )
    if not isinstance(handoff, dict):
        raise HTTPException(status_code=422, detail="handoff must be an object")
    handoff_id = _required_string(handoff.get("handoff_id"), "handoff.handoff_id")
    handoff_request_id = _required_string(
        handoff.get("request_id"),
        "handoff.request_id",
    )
    handoff_tenant = str(handoff.get("tenant_id") or tenant_id).strip()
    target_gate = _required_string(handoff.get("target_gate"), "handoff.target_gate")
    if (
        handoff_request_id != request_id
        or handoff_tenant != tenant_id
    ):
        raise HTTPException(status_code=422, detail="Invalid Consultation handoff identity")
    if not (
        target_gate.startswith("consultation.")
        and target_gate.endswith(".reviewed")
    ):
        raise HTTPException(
            status_code=422,
            detail="handoff.target_gate must be a reviewed Consultation gate",
        )
    expected_idempotency_key = (
        f"consultation-handoff:{tenant_id}:{handoff_id}"
    )
    if str(idempotency_key or "") != expected_idempotency_key:
        raise HTTPException(
            status_code=422,
            detail="Consultation handoff idempotency key is invalid",
        )

    request_digest = hashlib.sha256(
        json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    record = {
        "handoff_id": handoff_id,
        "tenant_id": tenant_id,
        "request_id": request_id,
        "source_actor": actor,
        "idempotency_key": expected_idempotency_key,
        "request_digest": request_digest,
        "target_gate": target_gate,
        "memo_ids": _string_list(
            handoff.get("memo_ids"),
            "handoff.memo_ids",
            required=True,
        ),
        "evidence_refs": _string_list(
            handoff.get("evidence_refs", []),
            "handoff.evidence_refs",
        ),
        "audit_refs": _string_list(
            handoff.get("audit_refs", []),
            "handoff.audit_refs",
        ),
        "trace_id": _required_string(handoff.get("trace_id"), "handoff.trace_id"),
        "handoff": dict(handoff),
        "acknowledged_at": _utc_now(),
        "consumer_ref": "governance-consultation-handoff",
    }
    inserted, canonical = consultation_handoff_store.insert_if_absent(record)
    if not inserted and (
        canonical.get("request_digest") != request_digest
        or canonical.get("idempotency_key") != expected_idempotency_key
        or canonical.get("tenant_id") != tenant_id
        or canonical.get("source_actor") != actor
    ):
        raise HTTPException(
            status_code=409,
            detail="Consultation handoff identity already has different content",
        )
    response.status_code = 201 if inserted else 200
    if inserted:
        _emit_consultation_handoff_event(canonical)
    return {
        "acknowledged": True,
        "handoff_id": handoff_id,
        "consumer_ref": str(canonical["consumer_ref"]),
        "idempotent": not inserted,
    }


# ---------------------------------------------------------------------------
# Routes — freeze-order and rollback write models
# ---------------------------------------------------------------------------

_freeze_order_lock = threading.Lock()
_rollback_lock = threading.Lock()
_human_gate_lock = threading.Lock()

# Any authenticated caller holding one of these roles may write a freeze
# order / rollback record at all (create or transition). Statuses that carry
# real authority (approve/reject/activate) are further restricted below to
# _GOVERNANCE_AUTHORITY_ROLES. Test-only aliases ("approver-role",
# "rejecter-role") that used to satisfy the self-declared check are removed —
# real callers authenticate with the roles they actually hold.
_GOVERNANCE_WRITE_ROLES = (
    "operator",
    "admin",
    "approver",
    "governance_reviewer",
    "risk_owner",
    "governance_committee",
)
_GOVERNANCE_AUTHORITY_ROLES = frozenset(
    {"governance_reviewer", "risk_owner", "governance_committee", "admin", "approver"}
)

# Freeze orders have a legitimate first-class "create as active" path (kill
# switch, evolution-mutation freeze) that the BFF already gates at operator
# level (see _MUTATION_EXECUTION_ROLES / ActivateKillSwitch's admin check) —
# unlike an approve/reject *transition*, which always requires a
# _GOVERNANCE_AUTHORITY_ROLES-level role regardless of who created the order.
_FREEZE_CREATE_AUTHORITY_ROLES = _GOVERNANCE_AUTHORITY_ROLES | {"operator"}
_FREEZE_AUTHORITY_STATUSES = frozenset({"approved", "rejected", "active"})
_ROLLBACK_AUTHORITY_STATUSES = frozenset({"approved", "rejected"})
_PROMOTION_HUMAN_GATE_ROLES = frozenset(
    {"approver", "risk_owner", "operator"}
)
_PROMOTION_HUMAN_GATE_EVIDENCE = {
    "canary": frozenset(
        {
            "promotion_readiness_packet",
            "paper_observation",
            "paper_performance",
            "reconciliation",
            "incident_clearance",
            "rollback_target",
            "broker_sandbox_smoke",
            "broker_entitlement",
            "capital_authorization",
            "kill_switch_drill",
        }
    ),
    "live": frozenset(
        {
            "promotion_readiness_packet",
            "canary_observation",
            "canary_performance",
            "execution_quality",
            "reconciliation",
            "incident_clearance",
            "rollback_target",
            "broker_sandbox_smoke",
            "broker_entitlement",
            "capital_authorization",
            "kill_switch_drill",
        }
    ),
}


def _governance_auth_env() -> Dict[str, str]:
    return {
        "PANTHEON_RUNTIME_AUTH_MODE": os.getenv("PANTHEON_GOVERNANCE_AUTH_MODE") or os.getenv("PANTHEON_BFF_AUTH_MODE") or os.getenv("PANTHEON_RUNTIME_AUTH_MODE") or "permissive",
        "PANTHEON_RUNTIME_JWT_SECRET": os.getenv("PANTHEON_GOVERNANCE_JWT_SECRET") or os.getenv("PANTHEON_BFF_JWT_SECRET") or os.getenv("PANTHEON_RUNTIME_JWT_SECRET", ""),
        "PANTHEON_RUNTIME_JWT_ISSUER": os.getenv("PANTHEON_GOVERNANCE_JWT_ISSUER") or os.getenv("PANTHEON_BFF_JWT_ISSUER") or os.getenv("PANTHEON_RUNTIME_JWT_ISSUER", ""),
        "PANTHEON_RUNTIME_JWT_AUDIENCE": os.getenv("PANTHEON_GOVERNANCE_JWT_AUDIENCE") or os.getenv("PANTHEON_BFF_JWT_AUDIENCE") or os.getenv("PANTHEON_RUNTIME_JWT_AUDIENCE", ""),
        "PANTHEON_RUNTIME_DEFAULT_ROLE": os.getenv("PANTHEON_GOVERNANCE_DEFAULT_ROLE") or os.getenv("PANTHEON_BFF_DEFAULT_ROLE") or os.getenv("PANTHEON_RUNTIME_DEFAULT_ROLE", "operator"),
        "PANTHEON_RUNTIME_MFA_REQUIRED": os.getenv("PANTHEON_GOVERNANCE_MFA_REQUIRED") or os.getenv("PANTHEON_BFF_MFA_REQUIRED") or os.getenv("PANTHEON_RUNTIME_MFA_REQUIRED", "false"),
        "PANTHEON_RUNTIME_JWKS_URI": os.getenv("PANTHEON_GOVERNANCE_JWKS_URI") or os.getenv("PANTHEON_BFF_JWKS_URI") or os.getenv("PANTHEON_RUNTIME_JWKS_URI", ""),
        "PANTHEON_RUNTIME_OIDC_DISCOVERY_URL": os.getenv("PANTHEON_GOVERNANCE_OIDC_DISCOVERY_URL") or os.getenv("PANTHEON_BFF_OIDC_DISCOVERY_URL") or os.getenv("PANTHEON_RUNTIME_OIDC_DISCOVERY_URL", ""),
        "PANTHEON_RUNTIME_OIDC_ISSUER": os.getenv("PANTHEON_GOVERNANCE_OIDC_ISSUER") or os.getenv("PANTHEON_BFF_OIDC_ISSUER") or os.getenv("PANTHEON_RUNTIME_OIDC_ISSUER", ""),
        "PANTHEON_RUNTIME_OIDC_AUDIENCE": os.getenv("PANTHEON_GOVERNANCE_OIDC_AUDIENCE") or os.getenv("PANTHEON_BFF_OIDC_AUDIENCE") or os.getenv("PANTHEON_RUNTIME_OIDC_AUDIENCE", ""),
        "PANTHEON_RUNTIME_ROLE_CLAIMS": os.getenv("PANTHEON_GOVERNANCE_ROLE_CLAIMS") or os.getenv("PANTHEON_BFF_ROLE_CLAIMS") or os.getenv("PANTHEON_RUNTIME_ROLE_CLAIMS", ""),
        "PANTHEON_RUNTIME_ROLE_MAP": os.getenv("PANTHEON_GOVERNANCE_ROLE_MAP") or os.getenv("PANTHEON_BFF_ROLE_MAP") or os.getenv("PANTHEON_RUNTIME_ROLE_MAP", ""),
        "PANTHEON_RUNTIME_ROLE_MAP_MODE": os.getenv("PANTHEON_GOVERNANCE_ROLE_MAP_MODE") or os.getenv("PANTHEON_BFF_ROLE_MAP_MODE") or os.getenv("PANTHEON_RUNTIME_ROLE_MAP_MODE", ""),
        "PANTHEON_RUNTIME_MFA_CLAIMS": os.getenv("PANTHEON_GOVERNANCE_MFA_CLAIMS") or os.getenv("PANTHEON_BFF_MFA_CLAIMS") or os.getenv("PANTHEON_RUNTIME_MFA_CLAIMS", ""),
        "PANTHEON_RUNTIME_MFA_VALUES": os.getenv("PANTHEON_GOVERNANCE_MFA_VALUES") or os.getenv("PANTHEON_BFF_MFA_VALUES") or os.getenv("PANTHEON_RUNTIME_MFA_VALUES", ""),
    }


def _authenticate_governance_write(
    authorization: Optional[str], mfa_token: Optional[str]
) -> AuthContext:
    """Resolve and authenticate the caller for a freeze/rollback write.

    Previously these endpoints trusted self-declared ``actor``/``identity``
    body fields with no bearer-token check at all, so an unauthenticated
    caller could POST a brand-new rollback with ``status: "approved"`` (or a
    freeze order with ``status: "active"``) and get a 201. Require a valid
    bearer token here and derive identity/roles from it instead.
    """
    gov_env = _governance_auth_env()
    mfa_required = gov_env["PANTHEON_RUNTIME_MFA_REQUIRED"].lower() == "true"
    try:
        return validate_request_auth(
            authorization=authorization,
            mfa_header=mfa_token,
            required_roles=_GOVERNANCE_WRITE_ROLES,
            mfa_required=mfa_required,
            env=gov_env,
        )
    except AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


def _authenticate_consultation_handoff_service(
    *,
    authorization: Optional[str],
    service_actor: Optional[str],
    tenant_id: Optional[str],
) -> tuple[str, str]:
    """Authenticate the narrow machine-to-machine handoff boundary.

    Consultation delivery is not a browser/user operation and must not depend
    on BFF or Governance user JWT configuration.  The dedicated credential is
    accepted only on this intake route and is additionally bound to an exact
    service actor and an explicit tenant allowlist.
    """

    expected_token = str(os.getenv("CONSULTATION_HANDOFF_TOKEN") or "").strip()
    expected_actor = str(
        os.getenv("CONSULTATION_HANDOFF_ALLOWED_SERVICE_ACTOR")
        or "consultation-workflow-executor"
    ).strip()
    allowed_tenants = {
        item.strip()
        for item in str(
            os.getenv("CONSULTATION_HANDOFF_ALLOWED_TENANTS") or ""
        ).split(",")
        if item.strip()
    }
    if (
        not expected_token
        or not expected_actor
        or not allowed_tenants
        or (
            PERSISTENCE_POSTURE.enforced
            and expected_token
            in {
                "pantheon-local-consultation-handoff-token",
                "replace-me-consultation-handoff-token",
            }
        )
    ):
        raise HTTPException(
            status_code=503,
            detail="Consultation handoff service boundary is not safely configured",
        )
    scheme, _, supplied_token = str(authorization or "").partition(" ")
    if (
        scheme.lower() != "bearer"
        or not supplied_token
        or not hmac.compare_digest(supplied_token, expected_token)
    ):
        raise HTTPException(
            status_code=401,
            detail="Consultation handoff service credential is invalid",
        )
    actor = str(service_actor or "").strip()
    if actor != expected_actor:
        raise HTTPException(
            status_code=403,
            detail="Consultation handoff service actor is outside authority",
        )
    tenant = str(tenant_id or "").strip()
    if not tenant or tenant not in allowed_tenants:
        raise HTTPException(
            status_code=403,
            detail="Consultation handoff tenant is outside service authority",
        )
    return actor, tenant


def _resolve_trusted_actor(
    body: Dict[str, Any],
    ctx: AuthContext,
    *,
    actor_field: str,
    identity_field: str,
) -> None:
    """Overwrite actor/identity fields in-place with server-trusted values.

    ``identity`` always comes from the authenticated token — a caller cannot
    claim to be someone else. ``actor`` (the role label) may be declared by
    the caller, but only if it is one of the roles the token actually
    carries; otherwise it is rejected rather than silently trusted.
    """
    declared_actor = body.get(actor_field)
    if declared_actor:
        if declared_actor not in ctx.roles:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Authenticated role set {sorted(ctx.roles)} does not include "
                    f"declared role '{declared_actor}' for '{actor_field}'."
                ),
            )
    else:
        for candidate in (
            "governance_committee",
            "risk_owner",
            "approver",
            "governance_reviewer",
            "admin",
            "operator",
        ):
            if candidate in ctx.roles:
                declared_actor = candidate
                break
        else:
            declared_actor = sorted(ctx.roles)[0] if ctx.roles else None
    body[actor_field] = declared_actor
    body[identity_field] = ctx.actor_id


def _emit_human_gate_event(
    event_type: str,
    decision: Dict[str, Any],
    *,
    actor_id: str,
    actor_role: str,
) -> None:
    try:
        audit_store.append_event(
            event_type=event_type,
            decision_id=str(decision["decision_id"]),
            actor_id=actor_id,
            actor_role=actor_role,
            target_type=str(decision["target_type"]),
            target_id=str(decision["target_id"]),
            detail={
                "target_environment": decision["target_environment"],
                "target_stage": decision.get("target_stage"),
                "source_binding_id": decision.get("source_binding_id"),
                "status": decision["status"],
                "evidence_hash": decision["evidence_hash"],
            },
        )
    except Exception as exc:
        log.warning("Human-gate audit write failed: %s", exc)


def _required_string(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise HTTPException(status_code=422, detail=f"{field} is required")
    return result


def _string_list(value: Any, field: str, *, required: bool = False) -> List[str]:
    if not isinstance(value, list):
        raise HTTPException(status_code=422, detail=f"{field} must be a list")
    result = [str(item).strip() for item in value]
    if any(not item for item in result):
        raise HTTPException(
            status_code=422,
            detail=f"{field} cannot contain empty values",
        )
    if required and not result:
        raise HTTPException(status_code=422, detail=f"{field} must not be empty")
    return result


def _emit_consultation_handoff_event(record: Dict[str, Any]) -> None:
    try:
        audit_store.append_event(
            event_type="consultation_handoff_acknowledged",
            decision_id=str(record["handoff_id"]),
            actor_id=str(record["source_actor"]),
            actor_role="consultation_handoff",
            target_type="consultation_gate",
            target_id=str(record["target_gate"]),
            detail={
                "tenant_id": record["tenant_id"],
                "request_id": record["request_id"],
                "memo_ids": record["memo_ids"],
                "evidence_refs": record["evidence_refs"],
                "audit_refs": record["audit_refs"],
                "trace_id": record["trace_id"],
            },
        )
    except Exception as exc:
        log.warning("Consultation handoff audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# Routes — target-bound promotion human gates
# ---------------------------------------------------------------------------

@app.post(
    "/api/governance/human-gates",
    response_model=Dict[str, Any],
    status_code=201,
    summary="Create a target-bound runtime promotion human gate",
)
def create_human_gate(
    body: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
) -> Dict[str, Any]:
    ctx = _authenticate_governance_write(authorization, x_mfa_token)
    target_type = str(body.get("target_type") or "").strip()
    if target_type != "runtime_binding_promotion":
        raise HTTPException(
            status_code=422,
            detail="target_type must be 'runtime_binding_promotion'",
        )

    metadata = dict(body.get("metadata") or {}) if isinstance(body.get("metadata"), dict) else {}
    unsupported_metadata = sorted(
        set(metadata).difference({"target_stage", "source_binding_id"})
    )
    if unsupported_metadata:
        raise HTTPException(
            status_code=422,
            detail=(
                "unsupported human-gate metadata fields: "
                + ", ".join(unsupported_metadata)
            ),
        )
    target_stage = str(metadata.get("target_stage") or "").strip().lower()
    required_evidence = _PROMOTION_HUMAN_GATE_EVIDENCE.get(target_stage)
    if required_evidence is None:
        raise HTTPException(
            status_code=422,
            detail="metadata.target_stage must be 'canary' or 'live'",
        )
    if not str(metadata.get("source_binding_id") or "").strip():
        raise HTTPException(
            status_code=422,
            detail="metadata.source_binding_id is required",
        )

    requested_roles = body.get("required_roles")
    if requested_roles is not None and (
        not isinstance(requested_roles, list)
        or frozenset(str(role) for role in requested_roles) != _PROMOTION_HUMAN_GATE_ROLES
        or len(requested_roles) != len(_PROMOTION_HUMAN_GATE_ROLES)
    ):
        raise HTTPException(
            status_code=422,
            detail="required_roles must contain exactly approver, risk_owner, and operator",
        )

    can_proceed_input = body.get("can_proceed_input")
    if not isinstance(can_proceed_input, dict):
        raise HTTPException(status_code=422, detail="can_proceed_input must be an object")
    declared_evidence_items = can_proceed_input.get("required_evidence", [])
    declared_evidence = frozenset(
        str(key) for key in declared_evidence_items
    ) if isinstance(declared_evidence_items, list) else frozenset()
    if (
        not isinstance(declared_evidence_items, list)
        or declared_evidence != required_evidence
        or len(declared_evidence_items) != len(required_evidence)
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"can_proceed_input.required_evidence for {target_stage} must contain exactly "
                + ", ".join(sorted(required_evidence))
            ),
        )

    reviewed_evidence = body.get("evidence_reviewed")
    reviewed_keys = (
        [str(item.get("key") or "") for item in reviewed_evidence]
        if isinstance(reviewed_evidence, list)
        and all(isinstance(item, dict) for item in reviewed_evidence)
        else []
    )
    if (
        frozenset(reviewed_keys) != required_evidence
        or len(reviewed_keys) != len(required_evidence)
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"evidence_reviewed for {target_stage} must contain exactly "
                + ", ".join(sorted(required_evidence))
            ),
        )

    decision_id = str(body.get("decision_id") or f"hgd-{uuid.uuid4().hex[:12]}").strip()
    metadata["target_stage"] = target_stage
    metadata["created_by_actor_id"] = ctx.actor_id
    payload = {
        "decision_id": decision_id,
        "target_type": target_type,
        "target_id": body.get("target_id"),
        "target_environment": body.get("target_environment"),
        "required_roles": sorted(_PROMOTION_HUMAN_GATE_ROLES),
        "evidence_reviewed": body.get("evidence_reviewed"),
        "can_proceed_input": can_proceed_input,
    }
    payload.update(metadata)
    try:
        with _human_gate_lock:
            created = human_gate_api.create_decision(payload).to_dict()
    except SignoffApiError as exc:
        code = 409 if "already exists" in str(exc) else 422
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except HumanGateDecisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    acting_role = next(
        (role for role in ("admin", "approver", "risk_owner", "operator") if role in ctx.roles),
        sorted(ctx.roles)[0],
    )
    _emit_human_gate_event(
        "human_gate_created",
        created,
        actor_id=ctx.actor_id,
        actor_role=acting_role,
    )
    return created


@app.get(
    "/api/governance/human-gates/{decision_id}",
    response_model=Dict[str, Any],
    summary="Read a canonical promotion human gate",
)
def get_human_gate(decision_id: str) -> Dict[str, Any]:
    try:
        return human_gate_api.read_decision(decision_id).to_dict()
    except SignoffApiError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/api/governance/human-gates/{decision_id}/signatures",
    response_model=Dict[str, Any],
    summary="Append an MFA-bound human-gate signature",
)
def sign_human_gate(
    decision_id: str,
    body: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
) -> Dict[str, Any]:
    ctx = _authenticate_governance_write(authorization, x_mfa_token)
    if not has_claim_bound_mfa(ctx, env=_governance_auth_env()):
        raise HTTPException(
            status_code=401,
            detail="A verified JWT with claim-bound MFA is required to sign a human gate",
        )

    requested_role = str(body.get("role") or "").strip()
    eligible_roles = _PROMOTION_HUMAN_GATE_ROLES.intersection(ctx.roles)
    if not requested_role and len(eligible_roles) == 1:
        requested_role = next(iter(eligible_roles))
    if requested_role not in eligible_roles:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Authenticated role set {sorted(ctx.roles)} cannot sign required role "
                f"{requested_role!r}"
            ),
        )

    meaning = str(body.get("meaning") or "approved").strip().lower()
    if meaning not in {"approved", "rejected"}:
        raise HTTPException(
            status_code=422,
            detail="human-gate signature meaning must be approved or rejected",
        )
    if body.get("conditions"):
        raise HTTPException(
            status_code=422,
            detail="conditional signatures are not admitted for runtime promotion",
        )

    try:
        with _human_gate_lock:
            current = human_gate_api.read_decision(decision_id)
            if any(signature.actor_id == ctx.actor_id for signature in current.signatures):
                raise HTTPException(
                    status_code=409,
                    detail="one authenticated actor may sign only one role on a human gate",
                )
            signature_payload = {
                "role": requested_role,
                "actor_id": ctx.actor_id,
                "meaning": meaning,
                "authn_token_kind": ctx.token_kind,
                "mfa_proof": "jwt_claim",
            }
            if body.get("signature_id"):
                signature_payload["signature_id"] = body["signature_id"]
            if body.get("source_ref"):
                signature_payload["source_ref"] = body["source_ref"]
            signed = human_gate_api.append_signature(
                decision_id, signature_payload
            ).to_dict()
    except HTTPException:
        raise
    except SignoffApiError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except HumanGateDecisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _emit_human_gate_event(
        "human_gate_signed",
        signed,
        actor_id=ctx.actor_id,
        actor_role=requested_role,
    )
    return signed


@app.post(
    "/api/governance/human-gates/{decision_id}/revoke",
    response_model=Dict[str, Any],
    summary="Revoke a promotion human gate",
)
def revoke_human_gate(
    decision_id: str,
    body: Dict[str, Any] = Body(...),
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
) -> Dict[str, Any]:
    ctx = _authenticate_governance_write(authorization, x_mfa_token)
    revoke_roles = frozenset({"approver", "risk_owner", "admin"})
    eligible_roles = revoke_roles.intersection(ctx.roles)
    if not eligible_roles:
        raise HTTPException(
            status_code=403,
            detail="human-gate revocation requires approver, risk_owner, or admin",
        )
    if not has_claim_bound_mfa(ctx, env=_governance_auth_env()):
        raise HTTPException(
            status_code=401,
            detail="A verified JWT with claim-bound MFA is required to revoke a human gate",
        )
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")
    acting_role = next(
        role for role in ("admin", "risk_owner", "approver") if role in eligible_roles
    )
    try:
        with _human_gate_lock:
            revoked = human_gate_api.revoke_decision(
                decision_id,
                reason=reason,
                metadata={
                    "revoked_by_actor_id": ctx.actor_id,
                    "revoked_by_role": acting_role,
                    "revocation_mfa_proof": "jwt_claim",
                },
            ).to_dict()
    except SignoffApiError as exc:
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except HumanGateDecisionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _emit_human_gate_event(
        "human_gate_revoked",
        revoked,
        actor_id=ctx.actor_id,
        actor_role=acting_role,
    )
    return revoked


@app.post(
    "/api/governance/freeze-orders",
    response_model=Dict[str, Any],
    status_code=201,
    summary="Record or update a canonical freeze order",
)
def record_freeze_order(
    body: Dict[str, Any] = Body(...),
    response: Response = None,
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
) -> Dict[str, Any]:
    """Create or update a FreezeOrder in the canonical store."""
    ctx = _authenticate_governance_write(authorization, x_mfa_token)

    freeze_order_id = body.get("freeze_order_id") or body.get("id")
    if not freeze_order_id:
        freeze_order_id = f"freeze-{uuid.uuid4().hex[:12]}"
        body["freeze_order_id"] = freeze_order_id
        body["id"] = freeze_order_id

    if not body.get("created_at") and not body.get("issued_at"):
        body["created_at"] = _utc_now()
        body["issued_at"] = body["created_at"]

    with _freeze_order_lock:
        existing = freeze_order_store.get(freeze_order_id)
        is_transition = False
        if existing:
            is_transition = True
            if response:
                response.status_code = 200

            # Enforce legal status transitions
            existing_status = existing.get("status")
            new_status = body.get("status")
            if existing_status in ("released", "rejected"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot transition from terminal freeze order status '{existing_status}'.",
                )
            if existing_status == "active" and new_status not in ("released", "active"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid transition from active to '{new_status}'.",
                )
            if existing_status == "requested" and new_status not in ("active", "rejected", "requested"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid transition from requested to '{new_status}'.",
                )

            inc_source_cmd = body.get("transition_source_command_id") or body.get("source_command_id")
            if not inc_source_cmd:
                raise HTTPException(
                    status_code=400,
                    detail="Missing required transition audit field: source_command_id",
                )
            if not body.get("transition_actor") and body.get("actor"):
                body["transition_actor"] = body.get("actor")
            _resolve_trusted_actor(
                body, ctx, actor_field="transition_actor", identity_field="transition_identity"
            )
            inc_actor = body["transition_actor"]
            inc_identity = body["transition_identity"]

            merged = dict(existing)
            for k, v in body.items():
                if v is not None:
                    merged[k] = v

            # Set transition audit fields explicitly
            merged["transition_actor"] = inc_actor
            merged["transition_identity"] = inc_identity
            merged["transition_source_command_id"] = inc_source_cmd

            # Preserve original fields and audit origin on transitions
            for field in ["scope", "target_id", "created_at", "issued_at", "actor", "identity", "source_command_id"]:
                if field in existing and existing[field] not in (None, ""):
                    merged[field] = existing[field]
            body = merged
        else:
            _resolve_trusted_actor(body, ctx, actor_field="actor", identity_field="identity")

        body["updated_at"] = _utc_now()

        # Enforce required audit fields on final payload
        for field in ["status", "actor", "identity", "source_command_id", "scope", "target_id"]:
            if not body.get(field):
                raise HTTPException(status_code=400, detail=f"Missing required audit field: {field}")

        # Enforce write authority for authority-gated statuses on BOTH create and
        # transition — a brand-new record can no longer walk straight in as
        # "active" without an authority role, same as a transition can't.
        new_status = body.get("status")
        if new_status in _FREEZE_AUTHORITY_STATUSES:
            acting_role = body.get("transition_actor") if is_transition else body.get("actor")
            allowed_roles = _GOVERNANCE_AUTHORITY_ROLES if is_transition else _FREEZE_CREATE_AUTHORITY_ROLES
            if acting_role not in allowed_roles:
                raise HTTPException(
                    status_code=403,
                    detail=f"Role '{acting_role}' is not authorized to set freeze order status to '{new_status}'.",
                )

        freeze_order_store.put(body)

    log.info("Recorded freeze order %s: %s", freeze_order_id, body)

    # Append audit event
    try:
        audit_store.append_event(
            event_type="freeze_order_state_changed" if is_transition else "freeze_order_created",
            decision_id=freeze_order_id,
            actor_id=body.get("transition_identity") or body.get("identity"),
            actor_role=body.get("transition_actor") or body.get("actor"),
            target_type="freeze_scope",
            target_id=body.get("scope"),
            detail={
                "status": body.get("status"),
                "target_id": body.get("target_id"),
                "source_command_id": body.get("source_command_id"),
                "transition_source_command_id": body.get("transition_source_command_id"),
            }
        )
    except Exception as exc:
        log.warning("Audit write failed: %s", exc)

    return body


@app.post(
    "/api/governance/rollbacks",
    response_model=Dict[str, Any],
    status_code=201,
    summary="Record or update a canonical rollback record",
)
def record_rollback(
    body: Dict[str, Any] = Body(...),
    response: Response = None,
    authorization: Optional[str] = Header(default=None),
    x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
) -> Dict[str, Any]:
    """Create or update a Rollback record in the canonical store."""
    ctx = _authenticate_governance_write(authorization, x_mfa_token)

    rollback_id = body.get("rollback_id") or body.get("id")
    if not rollback_id:
        rollback_id = f"rollback-{uuid.uuid4().hex[:12]}"
        body["rollback_id"] = rollback_id
        body["id"] = rollback_id

    if not body.get("created_at") and not body.get("initiated_at"):
        body["created_at"] = _utc_now()
        body["initiated_at"] = body["created_at"]
        body["requested_at"] = body["created_at"]

    with _rollback_lock:
        existing = rollback_store.get(rollback_id)
        is_transition = False
        if existing:
            is_transition = True
            if response:
                response.status_code = 200

            # Enforce legal status transitions
            existing_status = existing.get("status")
            new_status = body.get("status")
            if existing_status in ("completed", "rejected", "aborted"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Cannot transition from terminal rollback status '{existing_status}'.",
                )
            if existing_status == "approved" and new_status not in ("completed", "failed", "aborted", "approved", "rejected"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid transition from approved to '{new_status}'.",
                )
            if existing_status == "initiated" and new_status not in ("approved", "rejected", "completed", "failed", "aborted", "initiated"):
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid transition from initiated to '{new_status}'.",
                )

            inc_source_cmd = body.get("transition_source_command_id") or body.get("source_command_id")
            if not inc_source_cmd:
                raise HTTPException(
                    status_code=400,
                    detail="Missing required transition audit field: source_command_id",
                )
            if not body.get("transition_actor") and body.get("actor"):
                body["transition_actor"] = body.get("actor")
            _resolve_trusted_actor(
                body, ctx, actor_field="transition_actor", identity_field="transition_identity"
            )
            inc_actor = body["transition_actor"]
            inc_identity = body["transition_identity"]

            merged = dict(existing)
            for k, v in body.items():
                if v is not None:
                    merged[k] = v

            # Set transition audit fields explicitly
            merged["transition_actor"] = inc_actor
            merged["transition_identity"] = inc_identity
            merged["transition_source_command_id"] = inc_source_cmd

            # Preserve original fields and audit origin on transitions
            for field in ["runtime_id", "runtime_binding_id", "action_type", "target_artifact_id", "created_at", "initiated_at", "requested_at", "actor", "identity", "source_command_id"]:
                if field in existing and existing[field] not in (None, ""):
                    merged[field] = existing[field]
            body = merged
        else:
            _resolve_trusted_actor(body, ctx, actor_field="actor", identity_field="identity")

        body["updated_at"] = _utc_now()

        # Enforce required audit fields on final payload
        for field in ["status", "actor", "identity", "source_command_id", "runtime_id", "action_type"]:
            if not body.get(field):
                raise HTTPException(status_code=400, detail=f"Missing required audit field: {field}")

        # Enforce write authority for authority-gated statuses on BOTH create and
        # transition — a brand-new record can no longer walk straight in as
        # "approved" without an authority role, same as a transition can't.
        new_status = body.get("status")
        if new_status in _ROLLBACK_AUTHORITY_STATUSES:
            acting_role = body.get("transition_actor") if is_transition else body.get("actor")
            if acting_role not in _GOVERNANCE_AUTHORITY_ROLES:
                raise HTTPException(
                    status_code=403,
                    detail=f"Role '{acting_role}' is not authorized to set rollback status to '{new_status}'.",
                )

        rollback_store.put(body)

    log.info("Recorded rollback %s: %s", rollback_id, body)

    # Append audit event
    try:
        audit_store.append_event(
            event_type="rollback_state_changed" if is_transition else "rollback_created",
            decision_id=rollback_id,
            actor_id=body.get("transition_identity") or body.get("identity"),
            actor_role=body.get("transition_actor") or body.get("actor"),
            target_type="runtime_binding",
            target_id=body.get("runtime_binding_id") or body.get("runtime_id"),
            detail={
                "status": body.get("status"),
                "action_type": body.get("action_type"),
                "source_command_id": body.get("source_command_id"),
                "transition_source_command_id": body.get("transition_source_command_id"),
            }
        )
    except Exception as exc:
        log.warning("Audit write failed: %s", exc)

    return body


# ---------------------------------------------------------------------------
# Routes — proposals
# ---------------------------------------------------------------------------

@app.post(
    "/api/governance/approvals",
    response_model=ApprovalDecisionResponse,
    status_code=201,
    summary="Propose a new ApprovalDecision",
)
def propose_approval(body: ProposeApprovalRequest) -> ApprovalDecisionResponse:
    """Create a new ApprovalDecision in the *proposed* state.

    Called by: promotion plane, evolution controller, registry pipeline.
    """
    decision_id = body.decision_id or f"apv-{uuid.uuid4().hex[:12]}"
    if store.get(decision_id):
        raise HTTPException(
            status_code=409,
            detail=f"Decision '{decision_id}' already exists",
        )

    decision = ApprovalDecision.create_proposed(
        decision_id=decision_id,
        target_type=body.target_type.value,
        target_id=body.target_id,
        target_version=body.target_version,
        risk_level=body.risk_level.value,
        capital_pool_id=body.capital_pool_id,
        persona_id=body.persona_id,
        tenant_id=body.tenant_id,
        owner_user_id=body.owner_user_id,
        proposal_id=body.proposal_id,
        proposal_revision=body.proposal_revision,
        proposal_content_digest=body.proposal_content_digest,
        validation_result_digest=body.validation_result_digest,
        session_id=body.session_id,
        candidate_digest=body.candidate_digest,
        proof_digest=body.proof_digest,
        expires_at=body.expires_at,
    )

    errors = decision.validate()
    if errors:
        raise HTTPException(status_code=422, detail={"validation_errors": errors})

    store.put(decision)
    _emit("approval_decision_created", decision)
    log.info("Proposed %s for %s/%s", decision_id, body.target_type, body.target_id)
    return _to_response(decision)


# ---------------------------------------------------------------------------
# Routes — list / lookup
# ---------------------------------------------------------------------------

@app.get(
    "/api/governance/approvals/latest-approved",
    response_model=Optional[ApprovalDecisionResponse],
    summary="Latest approved decision for a target",
)
def get_latest_approved(
    target_type: str = Query(..., description="TargetType value"),
    target_id: str  = Query(..., description="Target artifact / object ID"),
) -> Optional[ApprovalDecisionResponse]:
    """Return the most recent *decided* + *approved* decision for a target.

    Returns null (HTTP 200 with null body) when no approved decision exists.

    Used by deployment planner, runtime manager, and evolution controller to
    verify canonical approval before proceeding.
    """
    decision = store.find_latest_approved(target_type, target_id)
    if not decision:
        return None
    return _to_response(decision)


@app.get(
    "/api/governance/approvals",
    response_model=List[ApprovalDecisionResponse],
    summary="List approval decisions",
)
def list_approvals(
    target_type:    Optional[str] = Query(None),
    target_id:      Optional[str] = Query(None),
    decision_state: Optional[str] = Query(None),
    risk_level:     Optional[str] = Query(None),
) -> List[ApprovalDecisionResponse]:
    """List all decisions with optional filters.  Most-recent first."""
    decisions = store.list_all()

    def _match_str(val, expected: str) -> bool:
        return val == expected or (hasattr(val, "value") and val.value == expected)

    if target_type:
        decisions = [d for d in decisions if _match_str(d.target_type, target_type)]
    if target_id:
        decisions = [d for d in decisions if d.target_id == target_id]
    if decision_state:
        decisions = [d for d in decisions if _match_str(d.decision_state, decision_state)]
    if risk_level:
        decisions = [d for d in decisions if _match_str(d.risk_level, risk_level)]

    decisions.sort(key=lambda d: d.created_at, reverse=True)
    return [_to_response(d) for d in decisions]


@app.get(
    "/api/governance/approvals/{decision_id}",
    response_model=ApprovalDecisionResponse,
    summary="Get a single approval decision",
)
def get_approval(decision_id: str) -> ApprovalDecisionResponse:
    return _to_response(_get_or_404(decision_id))


# ---------------------------------------------------------------------------
# Routes — lifecycle transitions
# ---------------------------------------------------------------------------

@app.post(
    "/api/governance/approvals/{decision_id}/review",
    response_model=ApprovalDecisionResponse,
    summary="Accept review (proposed → under_review)",
)
def accept_review(
    decision_id: str,
    body: AcceptReviewRequest,
) -> ApprovalDecisionResponse:
    """Transition a proposed decision to *under_review*.

    Authorization: role must be permitted for the decision's risk_level per
    the write-authority matrix.
    """
    decision = _get_or_404(decision_id)
    try:
        decision.accept_review(actor_role=body.actor_role.value, actor_id=body.actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    store.put(decision)
    _emit("approval_decision_state_changed", decision, {"new_state": "under_review"})
    return _to_response(decision)


@app.post(
    "/api/governance/approvals/{decision_id}/decide",
    response_model=ApprovalDecisionResponse,
    summary="Record outcome (under_review → decided)",
)
def record_decision(
    decision_id: str,
    body: DecideRequest,
) -> ApprovalDecisionResponse:
    """Record the final outcome: *approved*, *rejected*, or *approved_with_conditions*.

    An *approved* decision here is what deployment planner and evolution
    controller cite when constructing a DeploymentPlan or executing a
    follow-through action.
    """
    decision = _get_or_404(decision_id)

    # Enforce write-authority matrix: caller must hold a role authorized to
    # decide at the decision's risk level.
    risk_level_str = (
        decision.risk_level.value
        if hasattr(decision.risk_level, "value")
        else decision.risk_level
    )
    if not is_authorized_to_decide(body.actor_role.value, risk_level_str):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Role '{body.actor_role.value}' is not authorized to decide "
                f"at risk level '{risk_level_str}'"
            ),
        )

    evidence_refs = None
    if body.evidence_refs:
        evidence_refs = [
            EvidenceRef(
                ref_type=e.ref_type,
                ref_id=e.ref_id,
                storage_ref=e.storage_ref,
            )
            for e in body.evidence_refs
        ]

    try:
        decision.decide(
            outcome=body.outcome.value,
            rationale=body.rationale,
            actor_role=body.actor_role.value,
            actor_id=body.actor_id,
            conditions=body.conditions,
            evidence_refs=evidence_refs,
            session_id=body.session_id,
            candidate_digest=body.candidate_digest,
            proof_digest=body.proof_digest,
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    store.put(decision)
    _emit(
        "approval_decision_decided",
        decision,
        {"outcome": body.outcome.value, "rationale": body.rationale},
    )
    log.info("Decision %s → %s", decision_id, body.outcome)
    return _to_response(decision)


@app.post(
    "/api/governance/approvals/{decision_id}/revoke",
    response_model=ApprovalDecisionResponse,
    summary="Revoke a decided decision",
)
def revoke_decision(
    decision_id: str,
    body: RevokeRequest,
) -> ApprovalDecisionResponse:
    """Revoke a decided approval.  Requires risk_owner or governance_committee role."""
    decision = _get_or_404(decision_id)
    try:
        decision.revoke(actor_role=body.actor_role.value, actor_id=body.actor_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    store.put(decision)
    _emit("approval_decision_revoked", decision)
    log.info("Decision %s revoked by %s (%s)", decision_id, body.actor_id, body.actor_role)
    return _to_response(decision)


# ---------------------------------------------------------------------------
# Routes — write-authority matrix
# ---------------------------------------------------------------------------

@app.get(
    "/api/governance/write-authority",
    response_model=WriteAuthorityResponse,
    summary="Write-authority matrix",
)
def get_write_authority() -> WriteAuthorityResponse:
    """Return the canonical write-authority matrix.

    Specifies which actor roles may write an ApprovalDecision at each risk
    level.  Callers can consult this before submitting a decision to verify
    they hold a permitted role.
    """
    return WriteAuthorityResponse(
        matrix=[WriteAuthorityEntry(**entry) for entry in matrix_as_list()],
        description=(
            "Risk-level → authorized_roles: only these roles may record "
            "an ApprovalDecision at the given risk level.  "
            "revoke_roles may revoke any decided decision."
        ),
    )


# ---------------------------------------------------------------------------
# Routes — AuthZ check
# ---------------------------------------------------------------------------

@app.post(
    "/api/governance/authz/check",
    response_model=AuthzCheckResponse,
    summary="Evaluate a narrow service authorization request",
)
def check_authz(body: AuthzCheckRequest) -> AuthzCheckResponse:
    """Return allow/deny for service-to-service policy checks.

    The first consumer is memory retrieval. Unsupported actions deny instead
    of falling back to implicit allow.
    """
    decision = evaluate_authz_request(
        action=body.action,
        actor_id=body.actor_id,
        actor_roles=body.actor_roles,
        resource=body.resource,
        context=body.context,
    )
    return AuthzCheckResponse(
        allowed=bool(decision.get("allowed")),
        reason=str(decision.get("reason") or "unknown"),
        policy_version="governance-authz.v1",
    )


# ---------------------------------------------------------------------------
# Routes — audit log read path
# ---------------------------------------------------------------------------

@app.get(
    "/api/governance/audit",
    summary="Audit log read path",
)
def get_audit_events(
    decision_id: Optional[str] = Query(None, description="Filter by decision_id"),
    limit:       int           = Query(100, ge=1, le=1000),
) -> List[Dict[str, Any]]:
    """Return recent audit events from the governance audit log.

    Events are returned most-recent first.  Filter by decision_id to trace a
    single approval object through its lifecycle.
    """
    return audit_store.list_events(decision_id=decision_id, limit=limit)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", summary="Liveness probe")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": "governance"}
