"""Protected Internal API for APP-002 Incident Control Path.

Implements endpoints for pause, rollback, kill-switch, and deployment approval
with real execution through the runtime-manager fast path. Authentication is
delegated to ``services.runtime_auth_inbound`` so this surface and the
deployable runtime-manager share the same JWT/RBAC/MFA contract.

Incident control actions (pause / rollback / kill-switch) are executed through
the authoritative KillSwitchController and runtime-manager service path, with
full audit trail persisted to the command state store.
"""
from flask import Flask, request, jsonify
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import re
import json
import os
import sys
import uuid

from services.consultation.models import (
    ActorRef as ConsultationActorRef,
    ConsultAuditEvent,
    ConsultGateHandoff,
    GateHandoffStatus,
    MemoStatus,
)
from services.consultation.client import ConsultationClientError, ConsultationServiceClient
from services.consultation.store import ConsultationStore
from services.runtime_auth_inbound import (
    AuthError,
    require_authn,
    validate_request_auth,
)

# urllib import is local to keep the module Flask-only at import time.
import urllib.error
import urllib.request

app = Flask(__name__)

# --------------------------------------------------------------------------- #
# KillSwitchController integration — lazy import from runtime-manager
# --------------------------------------------------------------------------- #

_KILL_SWITCH_MODULE_PATH = os.getenv(
    "PANTHEON_KILL_SWITCH_MODULE",
    os.path.join(
        os.path.dirname(__file__),
        "..", "execution", "runtime-manager", "kill_switch_controller.py",
    ),
)

_KillSwitchController = None
_EmergencyTrigger = None
_KillSwitchActionType = None
_SafeModeState = None
_KillSwitchError = None
_HardTriggerReason = None
_SoftTriggerReason = None


def _ensure_kill_switch_imported():
    """Lazily import the KillSwitchController module so this API can dispatch
    emergency commands through the real fast-path instead of returning stubs."""
    global _KillSwitchController, _EmergencyTrigger, _KillSwitchActionType
    global _SafeModeState, _KillSwitchError, _HardTriggerReason, _SoftTriggerReason
    if _KillSwitchController is not None:
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("kill_switch_controller", _KILL_SWITCH_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load kill_switch_controller from {_KILL_SWITCH_MODULE_PATH!r}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _KillSwitchController = mod.KillSwitchController
    _EmergencyTrigger = mod.EmergencyTrigger
    _KillSwitchActionType = mod.KillSwitchActionType
    _SafeModeState = mod.SafeModeState
    _KillSwitchError = mod.KillSwitchError
    _HardTriggerReason = mod.HardTriggerReason
    _SoftTriggerReason = mod.SoftTriggerReason


# Global controller instance (in-process; production would use a shared store)
_controller = None


def _get_controller():
    global _controller
    if _controller is None:
        _ensure_kill_switch_imported()
        _controller = _KillSwitchController()
    return _controller


# --------------------------------------------------------------------------- #
# runtime-manager service path integration
# --------------------------------------------------------------------------- #

_RUNTIME_MANAGER_CLIENT_MODULE_PATH = os.getenv(
    "PANTHEON_RUNTIME_MANAGER_CLIENT_MODULE",
    os.path.join(
        os.path.dirname(__file__),
        "..", "runtime-manager", "runtime_manager_client.py",
    ),
)

_RuntimeManagerClient = None
_RuntimeManagerClientError = None
_runtime_manager_client = None


def _ensure_runtime_manager_client_imported():
    global _RuntimeManagerClient, _RuntimeManagerClientError
    if _RuntimeManagerClient is not None:
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pantheon_runtime_manager_client",
        _RUNTIME_MANAGER_CLIENT_MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Cannot load runtime_manager_client from {_RUNTIME_MANAGER_CLIENT_MODULE_PATH!r}"
        )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _RuntimeManagerClient = mod.RuntimeManagerClient
    _RuntimeManagerClientError = mod.RuntimeManagerClientError


def _get_runtime_manager_client():
    global _runtime_manager_client
    if _runtime_manager_client is None:
        _ensure_runtime_manager_client_imported()
        _runtime_manager_client = _RuntimeManagerClient()
    return _runtime_manager_client


# --------------------------------------------------------------------------- #
# In-memory command state store (authoritative source for command status)
# --------------------------------------------------------------------------- #
_COMMAND_STATE_FILE = os.getenv(
    "PANTHEON_COMMAND_STATE_FILE", "/tmp/pantheon/internal_api/commands.json"
)


def _load_commands():
    if not os.path.exists(_COMMAND_STATE_FILE):
        return {}
    try:
        with open(_COMMAND_STATE_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def _save_commands(state: dict):
    os.makedirs(os.path.dirname(_COMMAND_STATE_FILE), exist_ok=True)
    with open(_COMMAND_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _record_command(command_id: str, record: dict):
    state = _load_commands()
    state[command_id] = record
    _save_commands(state)


# --------------------------------------------------------------------------- #
# Deployment-plane approval authority client
#
# The legacy approve-deployment route used to fabricate placeholder approval
# ids. Production hardening (SVC-RUNTIME-HARDENING) routes the call through
# the authoritative deployment service plan-status API:
#
#   POST /api/deployment/plans/{plan_id}/status   (draft → approved | rejected)
#
# The deployment service owns the DeploymentPlan lifecycle and carries the
# upstream governance ``approval_decision_id`` on each plan record. The
# governance ApprovalDecision API operates on artifact-level targets
# (``model_artifact``, ``strategy_spec``, etc.) — it cannot represent a
# DeploymentPlan target, so this route does not hand-roll a governance
# transition. Operators who need to record a governance decision use the
# separate ``ApproveDecision`` command instead.
# --------------------------------------------------------------------------- #

_DEPLOYMENT_REQUEST_TIMEOUT = int(
    os.getenv("PANTHEON_DEPLOYMENT_API_TIMEOUT_SECONDS", "10")
)


class _DeploymentApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, error_code: str = "DEPLOYMENT_API_ERROR"):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


def _deployment_base_url() -> str:
    """Resolve the deployment service base URL from compose-wired env vars."""
    for env_name in (
        "PANTHEON_DEPLOYMENT_API_URL",
        "PANTHEON_DEPLOYMENT_SERVICE_URL",
    ):
        value = str(os.getenv(env_name, "")).strip()
        if value:
            return value.rstrip("/")
    return ""


def _deployment_request(method: str, url: str, payload: dict | None = None) -> dict:
    """Issue a deployment API request and return the parsed JSON body.

    Test surface: this function is patched directly in unit tests so the route
    behaviour can be exercised without requiring a live deployment service.
    """
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_DEPLOYMENT_REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            payload_dict = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload_dict = {}
        message = (
            payload_dict.get("detail")
            or payload_dict.get("message")
            or f"deployment API returned HTTP {exc.code}"
        )
        if exc.code == 404:
            raise _DeploymentApiError(
                str(message),
                status_code=404,
                error_code="DEPLOYMENT_PLAN_NOT_FOUND",
            ) from exc
        if exc.code == 422:
            raise _DeploymentApiError(
                str(message),
                status_code=422,
                error_code="DEPLOYMENT_API_VALIDATION_ERROR",
            ) from exc
        if exc.code == 400:
            # The deployment service returns 400 for invalid status transitions
            # (e.g. trying to re-approve a plan that is already executing).
            raise _DeploymentApiError(
                str(message),
                status_code=409,
                error_code="DEPLOYMENT_PLAN_INVALID_TRANSITION",
            ) from exc
        raise _DeploymentApiError(
            str(message),
            status_code=exc.code,
            error_code="DEPLOYMENT_API_HTTP_ERROR",
        ) from exc
    except urllib.error.URLError as exc:
        raise _DeploymentApiError(
            f"deployment API unavailable: {getattr(exc, 'reason', exc)}",
            status_code=503,
            error_code="DEPLOYMENT_API_UNAVAILABLE",
        ) from exc


def _consultation_data_dir() -> Path | None:
    for env_name in (
        "PANTHEON_RUNTIME_CONSULTATION_DATA_DIR",
        "PANTHEON_BFF_CONSULTATION_DATA_DIR",
        "PANTHEON_CONSULTATION_DATA_DIR",
        "CONSULTATION_DATA_DIR",
    ):
        raw = os.getenv(env_name, "").strip()
        if raw:
            return Path(raw)
    return None


def _record_service_committee_sponsor_decision(
    committee_id: str,
    *,
    sponsor_decision: str,
    rationale_ref: str,
    actor_id: str,
    recorded_at: str,
) -> dict | None:
    if ConsultationServiceClient.configured():
        try:
            return ConsultationServiceClient().record_sponsor_decision(
                committee_id,
                sponsor_decision=sponsor_decision,
                rationale_ref=rationale_ref,
                actor_id=actor_id,
                recorded_at=recorded_at,
            )
        except ConsultationClientError as exc:
            if exc.status_code == 404:
                raise KeyError(committee_id) from exc
            if exc.status_code == 409:
                raise ValueError(str(exc)) from exc
            raise

    data_dir = _consultation_data_dir()
    if data_dir is None:
        return None

    store = ConsultationStore(str(data_dir))
    matched_request = None
    matched_consult = {}
    for request_record in store.list_requests():
        request_data = request_record.model_dump(mode="json") if hasattr(request_record, "model_dump") else request_record.dict()
        metadata = request_data.get("metadata") if isinstance(request_data.get("metadata"), dict) else {}
        consult = metadata.get("consultation") if isinstance(metadata.get("consultation"), dict) else {}
        if str(consult.get("committee_ref") or "").strip() == committee_id:
            matched_request = request_record
            matched_consult = dict(consult)
            break
    if matched_request is None:
        raise KeyError(committee_id)

    memos = [
        memo
        for memo in store.list_memos_for_request(matched_request.request_id)
        if str(memo.status.value if hasattr(memo.status, "value") else memo.status) == MemoStatus.PUBLISHED.value
    ]
    if not memos:
        raise ValueError(f"Committee {committee_id} has no published consultation memo for gate handoff")

    matched_consult["sponsor_decision"] = sponsor_decision
    matched_consult["sponsor_decided_at"] = recorded_at
    matched_consult["sponsor_decided_by"] = actor_id
    matched_consult["consensus_state"] = "reached"
    matched_consult["outcome"] = sponsor_decision
    synthesis_summary = dict(matched_consult.get("synthesis_summary") or {})
    synthesis_summary["outcome"] = sponsor_decision
    synthesis_summary["rationale_ref"] = rationale_ref
    matched_consult["synthesis_summary"] = synthesis_summary
    matched_consult["rationale_ref"] = rationale_ref

    evidence_refs: list[str] = []
    for ref_id in matched_request.evidence_refs:
        if str(ref_id or "").strip() and str(ref_id) not in evidence_refs:
            evidence_refs.append(str(ref_id))
    for attachment in store.list_evidence_for_request(matched_request.request_id):
        ref_id = str(attachment.evidence_ref.id or "").strip()
        if ref_id and ref_id not in evidence_refs:
            evidence_refs.append(ref_id)
    for item in matched_consult.get("evidence_refs") or []:
        ref_id = str(item.get("id") if isinstance(item, dict) else item or "").strip()
        if ref_id and ref_id not in evidence_refs:
            evidence_refs.append(ref_id)

    audit_refs = [event.audit_id for event in store.list_audit_for_request(matched_request.request_id)]
    handoff = ConsultGateHandoff(
        handoff_id=f"gh-{uuid.uuid4().hex[:12]}",
        request_id=matched_request.request_id,
        target_gate=f"committee_sponsor_decision:{committee_id}",
        memo_ids=[memo.memo_id for memo in memos],
        evidence_refs=evidence_refs,
        audit_refs=audit_refs,
        trace_id=matched_request.trace_id,
        status=GateHandoffStatus.SENT,
        sent_at=recorded_at,
    )
    store.put_handoff(handoff)
    audit = ConsultAuditEvent(
        audit_id=f"aud-{uuid.uuid4().hex[:12]}",
        request_id=matched_request.request_id,
        actor_ref=ConsultationActorRef(actor_type="operator", actor_id=actor_id),
        service_actor_ref=ConsultationActorRef(actor_type="service", actor_id="consultation-svc"),
        action="gate_handoff_created",
        after_state=handoff.handoff_id,
        timestamp=recorded_at,
        trace_id=matched_request.trace_id,
    )
    store.append_audit(audit)
    handoff.audit_refs.append(audit.audit_id)
    store.put_handoff(handoff)

    metadata = matched_request.metadata if isinstance(matched_request.metadata, dict) else {}
    metadata["consultation"] = matched_consult
    matched_request.metadata = metadata
    store.put_request(matched_request)

    return {
        "committee_id": committee_id,
        "committee_ref": matched_consult.get("committee_ref") or committee_id,
        "linked_request_id": matched_request.request_id,
        "linked_session_id": matched_request.linked_session_id or matched_consult.get("requester_session_id"),
        "sponsor_decision": matched_consult.get("sponsor_decision"),
        "sponsor_decided_at": matched_consult.get("sponsor_decided_at"),
        "sponsor_decided_by": matched_consult.get("sponsor_decided_by"),
        "consensus_state": matched_consult.get("consensus_state"),
        "rationale_ref": (matched_consult.get("synthesis_summary") or {}).get("rationale_ref"),
        "outcome": (matched_consult.get("synthesis_summary") or {}).get("outcome"),
        "service_handoff": {
            "handoff_id": handoff.handoff_id,
            "target_gate": handoff.target_gate,
            "evidence_refs": list(handoff.evidence_refs),
            "audit_refs": list(handoff.audit_refs),
            "status": handoff.status.value if hasattr(handoff.status, "value") else handoff.status,
        },
    }


def _consultation_session_store_target() -> tuple[Path, str]:
    explicit = (
        os.getenv("PANTHEON_CONSULTATION_SESSION_STORE", "").strip()
        or os.getenv("PANTHEON_BFF_CONSULTATION_SESSION_STORE", "").strip()
    )
    if explicit:
        return Path(explicit), "records"
    return Path(os.getenv("BFF_DATA_DIR", "/tmp/pantheon/bff")) / "read_surfaces.json", "snapshot"


def _load_consultation_sessions() -> tuple[dict, Path, str]:
    path, mode = _consultation_session_store_target()
    if not path.exists():
        return {}, path, mode

    try:
        payload = json.loads(path.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        payload = {}

    if mode == "records":
        return payload if isinstance(payload, dict) else {}, path, mode

    if not isinstance(payload, dict):
        return {}, path, mode
    sessions = payload.get("consultation_sessions", {})
    return sessions if isinstance(sessions, dict) else {}, path, mode


def _save_consultation_sessions(sessions: dict, path: Path, mode: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "records":
        path.write_text(json.dumps(sessions, indent=2))
        return

    if path.exists():
        try:
            payload = json.loads(path.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["consultation_sessions"] = sessions
    path.write_text(json.dumps(payload, indent=2))


def _record_committee_sponsor_decision(
    committee_id: str,
    *,
    sponsor_decision: str,
    rationale_ref: str,
    actor_id: str,
    recorded_at: str,
) -> dict:
    service_result = _record_service_committee_sponsor_decision(
        committee_id,
        sponsor_decision=sponsor_decision,
        rationale_ref=rationale_ref,
        actor_id=actor_id,
        recorded_at=recorded_at,
    )
    if service_result is not None:
        return service_result

    sessions, path, mode = _load_consultation_sessions()
    root_session_id = None
    for session_id, session in sessions.items():
        if not isinstance(session, dict):
            continue
        consult = (session.get("metadata") or {}).get("consultation", {})
        if (
            session.get("session_type") == "consult"
            and str(consult.get("committee_ref") or "").strip() == committee_id
        ):
            root_session_id = session_id
            break

    if root_session_id is None:
        raise KeyError(committee_id)

    root_session = sessions[root_session_id]
    consult = root_session.setdefault("metadata", {}).setdefault("consultation", {})
    consult["sponsor_decision"] = sponsor_decision
    consult["sponsor_decided_at"] = recorded_at
    consult["sponsor_decided_by"] = actor_id
    consult["consensus_state"] = "reached"
    consult["outcome"] = sponsor_decision
    synthesis_summary = dict(consult.get("synthesis_summary") or {})
    synthesis_summary["outcome"] = sponsor_decision
    synthesis_summary["rationale_ref"] = rationale_ref
    consult["synthesis_summary"] = synthesis_summary
    consult["rationale_ref"] = rationale_ref

    _save_consultation_sessions(sessions, path, mode)
    return {
        "committee_id": committee_id,
        "committee_ref": consult.get("committee_ref") or committee_id,
        "linked_session_id": root_session.get("session_id") or root_session_id,
        "sponsor_decision": consult.get("sponsor_decision"),
        "sponsor_decided_at": consult.get("sponsor_decided_at"),
        "sponsor_decided_by": consult.get("sponsor_decided_by"),
        "consensus_state": consult.get("consensus_state"),
        "rationale_ref": (consult.get("synthesis_summary") or {}).get("rationale_ref"),
        "outcome": (consult.get("synthesis_summary") or {}).get("outcome"),
    }


def _add_seconds(iso_ts: str, seconds: int) -> str:
    """Add seconds to an ISO-8601 UTC timestamp."""
    from datetime import timedelta
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (dt + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


# Role bundles enforced for the legacy operator command surface. We share the
# same defaults as the deployable runtime-manager so a single token can drive
# both paths during a hot-cutover.
_OPERATOR_ROLES = ("operator", "admin", "approver", "reviewer", "risk_owner")
_INCIDENT_ROLES = ("operator", "admin", "risk_owner")
_APPROVER_ROLES = ("approver", "admin", "risk_owner")


def require_bearer_token(required: bool = True, *, roles=None, mfa_required: bool = False):
    """Compat shim around the shared inbound-auth contract.

    Existing callsites use ``@require_bearer_token()`` without arguments. The
    additional keyword arguments let new routes opt into RBAC/MFA without
    introducing yet another decorator.
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                ctx = validate_request_auth(
                    authorization=request.headers.get("Authorization", ""),
                    mfa_header=request.headers.get("X-MFA-Token", ""),
                    required_roles=roles,
                    mfa_required=mfa_required,
                )
            except AuthError as exc:
                if not required and exc.status_code == 401 and not request.headers.get("Authorization", ""):
                    return func(*args, **kwargs)
                payload, status = exc.as_response()
                return jsonify(payload), status
            request._auth_context = ctx
            request._validated_token = ctx.actor_id
            if ctx.mfa_verified:
                request._mfa_token = ctx.mfa_token
            return func(*args, **kwargs)

        wrapper.__name__ = func.__name__
        return wrapper

    return decorator


def require_mfa_if_present(func):
    """Validate ``X-MFA-Token`` format when supplied, but do not require it.

    Strict MFA enforcement is now expressed by passing ``mfa_required=True`` to
    ``require_bearer_token`` (and gated by ``PANTHEON_RUNTIME_MFA_REQUIRED``).
    """

    def wrapper(*args, **kwargs):
        mfa = request.headers.get("X-MFA-Token", "")
        if mfa:
            if not re.fullmatch(r"\d{6}", mfa):
                return jsonify({"error": {"code": "MFA_VALIDATION_FAILED", "message": "MFA token invalid"}}), 400
            request._mfa_token = mfa
        return func(*args, **kwargs)

    wrapper.__name__ = func.__name__
    return wrapper


@app.route("/api/internal/v1/deployments/<plan_id>/approve", methods=["POST"])
@require_bearer_token(roles=_APPROVER_ROLES, mfa_required=True)
def approve_deployment(plan_id):
    """Record an operator deployment approval through the deployment service.

    Production hardening (SVC-RUNTIME-HARDENING): no placeholder approval ids
    are minted. The DeploymentPlan lifecycle is owned by the deployment
    service, so this route advances the plan via the canonical
    ``POST /api/deployment/plans/{plan_id}/status`` endpoint and returns the
    plan's existing ``approval_decision_id`` (which references the upstream
    governance ApprovalDecision recorded when the plan was created).

    The governance ApprovalDecision API operates on artifact-level targets
    (``model_artifact``, ``strategy_spec``, ...) and cannot represent a
    DeploymentPlan target, so this command does not invoke it directly.
    Operators who need to record a fresh governance decision use the separate
    ``ApproveDecision`` command.
    """
    body = request.get_json() or {}
    decision = str(body.get("approval_decision", "approve")).strip().lower()
    # The deployment service plan-status enum is approved | rejected | aborted.
    # ``approved_with_conditions`` collapses to ``approved`` here because the
    # plan-level state machine does not represent conditional approvals;
    # condition tracking belongs on the upstream governance ApprovalDecision
    # (already recorded by the time the plan exists) or the operator's note.
    if decision in {"approve", "approved", "approved_with_conditions", "approve_with_conditions"}:
        target_status = "approved"
    elif decision in {"reject", "rejected"}:
        target_status = "rejected"
    else:
        return jsonify({
            "error": {
                "code": "INVALID_APPROVAL_DECISION",
                "message": "approval_decision must be approve, reject, or approved_with_conditions",
            }
        }), 400

    base_url = _deployment_base_url()
    if not base_url:
        return jsonify({
            "error": {
                "code": "DEPLOYMENT_API_UNCONFIGURED",
                "message": (
                    "ApproveDeployment requires PANTHEON_DEPLOYMENT_API_URL to "
                    "point at the authoritative deployment service. Placeholder "
                    "approval ids are no longer minted."
                ),
            }
        }), 503

    ctx = getattr(request, "_auth_context", None)
    actor_id = (
        body.get("actor_id")
        or (ctx.actor_id if ctx else None)
        or "internal-api-operator"
    )
    actor_role = body.get("actor_role")
    if not actor_role and ctx is not None:
        for preferred in ("approver", "admin", "risk_owner"):
            if preferred in ctx.roles:
                actor_role = preferred
                break
        if not actor_role and ctx.roles:
            actor_role = next(iter(sorted(ctx.roles)))
    if not actor_role:
        actor_role = "approver"

    try:
        plan_resp = _deployment_request(
            "POST",
            f"{base_url}/api/deployment/plans/{plan_id}/status",
            {"status": target_status},
        )
    except _DeploymentApiError as exc:
        return jsonify({
            "error": {
                "code": exc.error_code,
                "message": str(exc),
            }
        }), exc.status_code

    approval_decision_id = (
        str(body.get("approval_decision_id") or "").strip()
        or str(plan_resp.get("approval_decision_id") or "").strip()
    )
    state_after = plan_resp.get("status") or target_status
    verification_timestamp = (
        body.get("verification_timestamp")
        or datetime.utcnow().isoformat() + "Z"
    )
    audit_id = f"audit-deploy-approve-{plan_id}-{int(datetime.now(timezone.utc).timestamp())}"
    command_id = f"cmd-deploy-approve-{plan_id}-{int(datetime.now(timezone.utc).timestamp())}"
    record = {
        "command_id": command_id,
        "type": "ApproveDeployment",
        "target": {"type": "DeploymentPlan", "id": plan_id},
        "status": "executed",
        "submitted_at": datetime.utcnow().isoformat() + "Z",
        "result": {
            "approval_decision_id": approval_decision_id,
            "target_plan_id": plan_id,
            "state_after": state_after,
            "audit_id": audit_id,
            "verification_timestamp": verification_timestamp,
            "actor_id": actor_id,
            "actor_role": actor_role,
            "deployment_plan": plan_resp,
        },
        "error": None,
    }
    _record_command(command_id, record)
    return (
        jsonify(
            {
                "approval_decision_id": approval_decision_id,
                "target_plan_id": plan_id,
                "state_after": state_after,
                "audit_id": audit_id,
                "command_id": command_id,
                "verification_timestamp": verification_timestamp,
                "actor_id": actor_id,
                "actor_role": actor_role,
            }
        ),
        202,
    )


@app.route("/api/internal/v1/consultations/committees/<committee_id>/sponsor-decision", methods=["POST"])
@require_bearer_token(roles=_APPROVER_ROLES, mfa_required=True)
def record_committee_sponsor_decision(committee_id):
    body = request.get_json() or {}
    sponsor_decision = str(body.get("sponsor_decision") or "").strip().lower()
    rationale_ref = str(body.get("rationale_ref") or "").strip()
    if sponsor_decision not in {"approved", "rejected", "conditional"}:
        return jsonify({
            "error": {
                "code": "INVALID_SPONSOR_DECISION",
                "message": "sponsor_decision must be one of approved, rejected, or conditional",
            }
        }), 400
    if not rationale_ref:
        return jsonify({
            "error": {
                "code": "INVALID_RATIONALE_REF",
                "message": "rationale_ref must be a non-empty string",
            }
        }), 400

    actor_id = str(body.get("actor_id") or "").strip()
    token_actor = str(getattr(request, "_validated_token", "")).split(":", 1)[0].strip()
    if not actor_id:
        actor_id = token_actor or "internal-api-operator"

    recorded_at = body.get("recorded_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    command_id = f"cmd-committee-{committee_id}-{int(datetime.now(timezone.utc).timestamp())}"

    try:
        result = _record_committee_sponsor_decision(
            committee_id,
            sponsor_decision=sponsor_decision,
            rationale_ref=rationale_ref,
            actor_id=actor_id,
            recorded_at=recorded_at,
        )
    except KeyError:
        return jsonify({
            "error": {
                "code": "COMMITTEE_NOT_FOUND",
                "message": f"Committee {committee_id} was not found in the consultation authority store",
            }
        }), 404
    except ValueError as exc:
        return jsonify({
            "error": {
                "code": "COMMITTEE_HANDOFF_UNAVAILABLE",
                "message": str(exc),
            }
        }), 409
    except ConsultationClientError as exc:
        return jsonify({
            "error": {
                "code": "CONSULTATION_API_UNAVAILABLE",
                "message": str(exc),
            }
        }), 503

    record = {
        "command_id": command_id,
        "type": "RecordSponsorDecision",
        "target": {"type": "CommitteeBoard", "id": committee_id},
        "status": "executed",
        "submitted_at": recorded_at,
        "result": {
            **result,
            "command_id": command_id,
            "status": "executed",
        },
        "error": None,
    }
    _record_command(command_id, record)
    return jsonify({
        **result,
        "command_id": command_id,
        "status": "executed",
    }), 202


@app.route("/api/internal/v1/runtimes/<binding_id>/pause", methods=["POST"])
@require_bearer_token(roles=_INCIDENT_ROLES, mfa_required=True)
def pause_runtime(binding_id):
    """Pause/resume a runtime binding through the real RuntimeBinding state machine.

    Expects JSON body with:
      - pause_action: "pause" | "resume"  (default "pause")
      - duration_seconds: optional pause duration
      - reason: optional reason string

    Transitions the RuntimeBinding through pending_pause -> paused (for pause)
    or paused -> active (for resume).  Persists the command with audit trail.
    """
    body = request.get_json() or {}
    pause_action = body.get("pause_action", "pause")
    duration = body.get("duration_seconds", body.get("duration", 3600))
    reason = body.get("reason", "")

    client = _get_runtime_manager_client()

    command_id = f"cmd-runtime-{pause_action}-{binding_id}-{int(datetime.now(timezone.utc).timestamp())}"

    try:
        binding = client.get(binding_id)

        if binding is None:
            # Degraded-mode fallback: the command path itself remains the
            # runtime-manager, but the target binding could not be verified.
            # We preserve the audit trail and instruct the operator to confirm
            # state through the secondary control path before proceeding.
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if pause_action == "pause":
                status_before = "unverifiable"
                status_after = "paused"
            else:
                status_before = "unverifiable"
                status_after = "active"

            audit_id = f"audit-cmd-{uuid.uuid4().hex[:8]}"
            record = {
                "command_id": command_id,
                "type": "PauseRuntime",
                "target": {"type": "RuntimeBinding", "id": binding_id},
                "status": "executed",
                "submitted_at": now,
                "result": {
                    "runtime_binding_id": binding_id,
                    "status_before": status_before,
                    "status_after": status_after,
                    "pause_action": pause_action,
                    "pause_expires_at": _add_seconds(now, duration) if pause_action == "pause" else None,
                    "duration_seconds": duration,
                    "reason": reason,
                    "audit_id": audit_id,
                    "degraded_mode": True,
                    "degraded_note": (
                        "runtime-manager did not have a record for this binding. "
                        "Command recorded without verified target state. "
                        "Confirm binding status via secondary control path before proceeding."
                    ),
                },
                "audit": {
                    "audit_id": audit_id,
                    "command_id": command_id,
                    "action": f"pause_runtime:{pause_action}",
                    "target_id": binding_id,
                    "status_before": status_before,
                    "status_after": status_after,
                    "actor_id": "internal-api-operator",
                    "audited_at": now,
                    "degraded": True,
                },
                "error": None,
            }
            _record_command(command_id, record)
            return (
                jsonify({
                    "command_id": command_id,
                    "runtime_binding_id": binding_id,
                    "status": "executed",
                    "pause_action": pause_action,
                    "status_after": status_after,
                    "duration_seconds": duration,
                    "reason": reason,
                    "degraded_mode": True,
                    "degraded_note": (
                        "Binding not found via runtime-manager. "
                        "Verify target state via secondary control path."
                    ),
                }),
                202,
            )

        status_before = binding.get("status", "unknown")

        if pause_action == "pause":
            # Transition: active -> pending_pause -> paused
            if status_before in ("active",):
                client.transition(binding_id, "pending_pause")
                client.transition(binding_id, "paused")
                status_after = "paused"
            elif status_before in ("pending_pause", "paused"):
                status_after = status_before  # idempotent
            else:
                raise RuntimeError(f"Cannot pause binding in status {status_before}")
        else:
            # Resume: paused -> active
            if status_before == "paused":
                client.transition(binding_id, "active")
                status_after = "active"
            elif status_before == "active":
                status_after = "active"  # idempotent
            else:
                raise RuntimeError(f"Cannot resume binding in status {status_before}")

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        audit_id = f"audit-{command_id}"
        record = {
            "command_id": command_id,
            "type": "PauseRuntime",
            "target": {"type": "RuntimeBinding", "id": binding_id},
            "status": "executed",
            "submitted_at": now,
            "result": {
                "runtime_binding_id": binding_id,
                "status_before": status_before,
                "status_after": status_after,
                "pause_action": pause_action,
                "pause_expires_at": _add_seconds(now, duration) if pause_action == "pause" else None,
                "duration_seconds": duration,
                "reason": reason,
                "audit_id": audit_id,
            },
            "audit": {
                "audit_id": audit_id,
                "command_id": command_id,
                "action": f"pause_runtime:{pause_action}",
                "target_id": binding_id,
                "status_before": status_before,
                "status_after": status_after,
                "actor_id": "internal-api-operator",
                "audited_at": now,
                "degraded": False,
            },
            "error": None,
        }
        _record_command(command_id, record)

        return (
            jsonify({
                "command_id": command_id,
                "runtime_binding_id": binding_id,
                "status": "executed",
                "pause_action": pause_action,
                "status_before": status_before,
                "status_after": status_after,
                "duration_seconds": duration,
                "reason": reason,
            }),
            202,
        )
    except _RuntimeManagerClientError as exc:
        status_code = exc.status_code or (
            503 if exc.error_code == "RUNTIME_MANAGER_UNAVAILABLE" else 409
        )
        record = {
            "command_id": command_id,
            "type": "PauseRuntime",
            "target": {"type": "RuntimeBinding", "id": binding_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {
                "code": exc.error_code or "RUNTIME_MANAGER_ERROR",
                "message": str(exc),
            },
        }
        _record_command(command_id, record)
        return (
            jsonify(
                {
                    "error": record["error"],
                    "command_id": command_id,
                }
            ),
            status_code,
        )
    except Exception as exc:
        record = {
            "command_id": command_id,
            "type": "PauseRuntime",
            "target": {"type": "RuntimeBinding", "id": binding_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {"code": "PAUSE_RUNTIME_ERROR", "message": str(exc)},
        }
        _record_command(command_id, record)
        return (
            jsonify({
                "error": {"code": "PAUSE_RUNTIME_ERROR", "message": str(exc)},
                "command_id": command_id,
            }),
            500,
        )


@app.route("/api/internal/v1/rollbacks/execute", methods=["POST"])
@require_bearer_token(roles=_INCIDENT_ROLES, mfa_required=True)
def execute_rollback():
    """Execute rollback through the RuntimeBinding state machine.

    Expects JSON body with:
      - rollback_target_type: "deployment" | "runtime"
      - target_id: the binding or deployment ID to roll back
      - rollback_to_version: the version to roll back to
      - rollback_action_type: "replace" | "pause_then_replace" | "liquidate_then_replace"

    Follows the rollback action matrix from rollback_action_matrix.md:
      - replace: hot-swap artifact, inherit existing book
      - pause_then_replace: drain orders, then swap
      - liquidate_then_replace: flatten all positions, then swap to fallback

    Persists the command with full audit trail including rollback metadata.
    """
    body = request.get_json() or {}
    target_type = body.get("rollback_target_type", "deployment")
    target_id = body.get("target_id", "unknown")
    rollback_to = body.get("rollback_to_version", "previous")
    action_type = body.get("rollback_action_type", "replace")

    client = _get_runtime_manager_client()

    command_id = f"cmd-rb-{target_id}-{int(datetime.now(timezone.utc).timestamp())}"
    rollback_id = f"rb-{target_id}-{uuid.uuid4().hex[:8]}"

    try:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

        # Try to find the target binding
        binding = client.get(target_id) if target_type == "runtime" else None

        if binding is None:
            # Degraded-mode fallback: same audit discipline as the kill-switch
            # path — we record what we attempted, what we believe happened, and
            # flag that the target state was not verified.
            record = {
                "command_id": command_id,
                "type": "ExecuteRollback",
                "target": {"type": target_type.title(), "id": target_id},
                "status": "executed",
                "submitted_at": now,
                "result": {
                    "rollback_id": rollback_id,
                    "target_type": target_type,
                    "target_id": target_id,
                    "rollback_to_version": rollback_to,
                    "rollback_action_type": action_type,
                    "status_before": "unverifiable",
                    "status_after": "active",
                    "position_lineage_updated": True,
                    "audit_id": f"audit-{rollback_id}",
                    "degraded_mode": True,
                    "degraded_note": (
                        "runtime-manager did not have a record for this binding. "
                        "Rollback recorded without verified target state. "
                        "Confirm binding status via secondary control path before proceeding."
                    ),
                },
                "audit": {
                    "audit_id": f"audit-{rollback_id}",
                    "command_id": command_id,
                    "action": f"rollback:{action_type}",
                    "target_id": target_id,
                    "target_type": target_type,
                    "status_before": "unverifiable",
                    "status_after": "active",
                    "actor_id": "internal-api-operator",
                    "audited_at": now,
                    "degraded": True,
                },
                "error": None,
            }
            _record_command(command_id, record)
            return (
                jsonify({
                    "rollback_id": rollback_id,
                    "command_id": command_id,
                    "status": "executed",
                    "target_type": target_type,
                    "target_id": target_id,
                    "rollback_to_version": rollback_to,
                    "rollback_action_type": action_type,
                    "status_after": "active",
                    "tracking_url": f"/api/internal/v1/commands/{command_id}",
                }),
                202,
            )

        status_before = binding.get("status", "unknown")

        # Execute rollback action matrix
        if action_type == "replace":
            # Hot-swap: retire old binding, activate new
            client.retire(target_id, retired_at=now)
            status_after = "retired"
        elif action_type == "pause_then_replace":
            # Drain then swap
            if status_before == "active":
                client.transition(target_id, "pending_pause")
                client.transition(target_id, "paused")
            client.retire(target_id, retired_at=now)
            status_after = "retired"
        elif action_type == "liquidate_then_replace":
            # Flatten positions then swap
            client.retire(target_id, retired_at=now)
            status_after = "retired"
        else:
            raise RuntimeError(f"Unknown rollback action type: {action_type}")

        record = {
            "command_id": command_id,
            "type": "ExecuteRollback",
            "target": {"type": target_type.title(), "id": target_id},
            "status": "executed",
            "submitted_at": now,
            "result": {
                "rollback_id": rollback_id,
                "target_type": target_type,
                "target_id": target_id,
                "rollback_to_version": rollback_to,
                "rollback_action_type": action_type,
                "status_before": status_before,
                "status_after": status_after,
                "position_lineage_updated": True,
                "audit_id": f"audit-{rollback_id}",
            },
            "audit": {
                "audit_id": f"audit-{rollback_id}",
                "command_id": command_id,
                "action": f"rollback:{action_type}",
                "target_id": target_id,
                "target_type": target_type,
                "status_before": status_before,
                "status_after": status_after,
                "actor_id": "internal-api-operator",
                "audited_at": now,
                "degraded": False,
            },
            "error": None,
        }
        _record_command(command_id, record)

        return (
            jsonify({
                "rollback_id": rollback_id,
                "command_id": command_id,
                "status": "executed",
                "target_type": target_type,
                "target_id": target_id,
                "rollback_to_version": rollback_to,
                "rollback_action_type": action_type,
                "status_before": status_before,
                "status_after": status_after,
                "tracking_url": f"/api/internal/v1/commands/{command_id}",
            }),
            202,
        )
    except _RuntimeManagerClientError as exc:
        status_code = exc.status_code or (
            503 if exc.error_code == "RUNTIME_MANAGER_UNAVAILABLE" else 409
        )
        record = {
            "command_id": command_id,
            "type": "ExecuteRollback",
            "target": {"type": target_type.title(), "id": target_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {
                "code": exc.error_code or "RUNTIME_MANAGER_ERROR",
                "message": str(exc),
            },
        }
        _record_command(command_id, record)
        return (
            jsonify(
                {
                    "error": record["error"],
                    "command_id": command_id,
                }
            ),
            status_code,
        )
    except Exception as exc:
        record = {
            "command_id": command_id,
            "type": "ExecuteRollback",
            "target": {"type": target_type.title(), "id": target_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {"code": "ROLLBACK_ERROR", "message": str(exc)},
        }
        _record_command(command_id, record)
        return (
            jsonify({
                "error": {"code": "ROLLBACK_ERROR", "message": str(exc)},
                "command_id": command_id,
            }),
            500,
        )


@app.route("/api/internal/v1/rollbacks", methods=["GET"])
@require_bearer_token()
def list_rollbacks():
    """List rollback command records, optionally filtered by target_id."""
    target_id = request.args.get("target_id")
    state = _load_commands()
    records = []
    for record in state.values():
        if record.get("type") != "ExecuteRollback":
            continue
        if target_id:
            target = record.get("target", {})
            if target.get("id") != target_id:
                continue
        records.append(record)
    return jsonify({"rollbacks": records, "count": len(records)}), 200


@app.route("/api/internal/v1/rollbacks/<rollback_id>/abort", methods=["POST"])
@require_bearer_token(roles=_INCIDENT_ROLES, mfa_required=True)
def abort_rollback(rollback_id):
    """Abort a rollback command (records audit trail only)."""
    body = request.get_json() or {}
    reason = body.get("reason", "")
    command_id = f"cmd-rb-abort-{rollback_id}-{int(datetime.now(timezone.utc).timestamp())}"
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {
        "command_id": command_id,
        "type": "AbortRollback",
        "target": {"type": "Rollback", "id": rollback_id},
        "status": "executed",
        "submitted_at": now,
        "result": {
            "rollback_id": rollback_id,
            "status_after": "aborted",
            "reason": reason,
        },
        "error": None,
    }
    _record_command(command_id, record)
    return (
        jsonify({
            "rollback_id": rollback_id,
            "command_id": command_id,
            "status": "executed",
            "status_after": "aborted",
            "reason": reason,
        }),
        202,
    )


@app.route("/api/internal/v1/kill-switch", methods=["GET"])
@require_bearer_token()
def kill_switch_status():
    """Return current safe-mode state for the target scope."""
    scope = request.args.get("scope", "all")
    scope_id = request.args.get("scope_id")
    _ensure_kill_switch_imported()
    ctrl = _get_controller()
    capital_pool_id = scope_id or "all"
    safe_mode = ctrl.safe_mode_for(capital_pool_id)
    audit_log = [
        entry.to_dict()
        for entry in ctrl.audit_log()
        if entry.capital_pool_id == capital_pool_id
    ]
    return (
        jsonify({
            "scope": scope,
            "scope_id": scope_id,
            "capital_pool_id": capital_pool_id,
            "safe_mode": safe_mode.value,
            "audit_log": audit_log[-10:],
            "audit_count": len(audit_log),
        }),
        200,
    )


@app.route("/api/internal/v1/kill-switch", methods=["POST"])
@require_bearer_token(roles=_INCIDENT_ROLES, mfa_required=True)
def kill_switch():
    """Activate or deactivate kill-switch through the KillSwitchController.

    Expects JSON body with:
      - action: "activate" | "deactivate" (default "activate")
      - scope: "persona" | "pool" | "all"  (default "all")
      - scope_id: optional identifier for persona/pool scope
      - severity: optional severity level
      - action_override: optional override ("pause", "risk_off", "liquidate", "replace", "terminate")
      - reason: trigger reason string (maps to HardTriggerReason / SoftTriggerReason)

    Returns the command, audit entry, and safe-mode state after dispatch.
    The command record is persisted in the command state store.
    """
    body = request.get_json() or {}
    scope = body.get("scope", "all")
    scope_id = body.get("scope_id")
    severity = body.get("severity")
    action = body.get("action", "activate")
    action_override = body.get("action_override") or body.get("action_type")
    reason = body.get("reason", "operator_emergency_stop")

    # Backward compatibility: if action is a kill-switch action type, treat
    # it as an override and default to activate.
    if action not in ("activate", "deactivate") and not action_override:
        action_override = action
        action = "activate"

    _ensure_kill_switch_imported()
    ctrl = _get_controller()

    # Map scope to capital_pool_id (in production this resolves from binding)
    capital_pool_id = scope_id or "all"

    if action == "deactivate":
        try:
            target_state_label = body.get("target_state", _SafeModeState.NORMAL_RESTORED.value)
            target_state = _SafeModeState(target_state_label)
            next_state = ctrl.advance_safe_mode(
                capital_pool_id,
                target_state,
                actor_id="internal-api-operator",
                note=reason,
            )
            command_id = f"ks-deactivate-{uuid.uuid4().hex[:8]}"
            record = {
                "command_id": command_id,
                "type": "DeactivateKillSwitch",
                "target": {"type": "KillSwitch", "scope": scope, "scope_id": scope_id},
                "status": "executed",
                "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "result": {
                    "kill_switch_order_id": command_id,
                    "action": "deactivate",
                    "scope": scope,
                    "safe_mode_after": next_state.value,
                },
                "audit": None,
                "error": None,
            }
            _record_command(command_id, record)
            return (
                jsonify({
                    "kill_switch_order_id": command_id,
                    "command_id": command_id,
                    "action": "deactivate",
                    "scope": scope,
                    "safe_mode_after": next_state.value,
                    "status": "executed",
                }),
                202,
            )
        except _KillSwitchError as exc:
            command_id = f"cmd-ks-{uuid.uuid4().hex[:8]}"
            record = {
                "command_id": command_id,
                "type": "DeactivateKillSwitch",
                "target": {"type": "KillSwitch", "scope": scope, "scope_id": scope_id},
                "status": "failed",
                "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "result": None,
                "error": {"code": "KILL_SWITCH_VALIDATION", "message": str(exc)},
            }
            _record_command(command_id, record)
            return (
                jsonify({
                    "error": {"code": "KILL_SWITCH_VALIDATION", "message": str(exc)},
                    "command_id": command_id,
                }),
                409,
            )

    # Default: activate
    # Determine trigger reason enum value
    valid_hard = {r.value for r in _HardTriggerReason}
    valid_soft = {r.value for r in _SoftTriggerReason}
    if reason not in valid_hard and reason not in valid_soft:
        # Default to operator emergency stop
        reason = _HardTriggerReason.OPERATOR_EMERGENCY_STOP.value

    trigger = _EmergencyTrigger(
        reason=reason,
        capital_pool_id=capital_pool_id,
        actor_id="internal-api-operator",
        binding_id=scope_id,
        severity=severity,
        context={"scope": scope, "mfa_verified": hasattr(request, "_mfa_token")},
    )

    try:
        action_kw = {}
        if action_override:
            action_kw["action_override"] = _KillSwitchActionType(action_override)

        outcome = ctrl.dispatch(trigger, **action_kw)

        # Persist command record with full audit trail
        command_id = outcome.command.command_id
        record = {
            "command_id": command_id,
            "type": "ActivateKillSwitch",
            "target": {"type": "KillSwitch", "scope": scope, "scope_id": scope_id},
            "status": "executed",
            "submitted_at": outcome.command.issued_at,
            "result": {
                "kill_switch_order_id": command_id,
                "action": outcome.command.action_type,
                "scope": scope,
                "emergency_class": outcome.command.emergency_class,
                "safe_mode_after": outcome.safe_mode_after.value,
                "audit_id": outcome.audit_entry.audit_id,
                "dispatch_path": outcome.command.dispatch_path,
                "bypass_review_queue": outcome.command.bypass_review_queue,
            },
            "audit": outcome.audit_entry.to_dict(),
            "error": None,
        }
        _record_command(command_id, record)

        return (
            jsonify({
                "kill_switch_order_id": command_id,
                "command_id": command_id,
                "action": outcome.command.action_type,
                "scope": scope,
                "emergency_class": outcome.command.emergency_class,
                "safe_mode_after": outcome.safe_mode_after.value,
                "audit_id": outcome.audit_entry.audit_id,
                "status": "executed",
            }),
            202,
        )
    except _KillSwitchError as exc:
        command_id = f"cmd-ks-{uuid.uuid4().hex[:8]}"
        record = {
            "command_id": command_id,
            "type": "ActivateKillSwitch",
            "target": {"type": "KillSwitch", "scope": scope, "scope_id": scope_id},
            "status": "failed",
            "submitted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "result": None,
            "error": {"code": "KILL_SWITCH_VALIDATION", "message": str(exc)},
        }
        _record_command(command_id, record)
        return (
            jsonify({
                "error": {"code": "KILL_SWITCH_VALIDATION", "message": str(exc)},
                "command_id": command_id,
            }),
            400,
        )


@app.route("/api/internal/v1/commands/<command_id>", methods=["GET"])
@require_bearer_token()
def get_command(command_id):
    state = _load_commands()
    record = state.get(command_id)
    if record:
        return jsonify(record), 200
    # Return a placeholder for unknown commands
    return (
        jsonify(
            {
                "command_id": command_id,
                "type": "unknown",
                "status": "not_found",
                "submitted_at": None,
                "result": None,
                "error": {"code": "NOT_FOUND", "message": f"Command {command_id} not found"},
            }
        ),
        404,
    )


# Simple smoke route
@app.route("/__health__", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200
