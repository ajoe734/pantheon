"""Single BFF forwarding path to the Governance approval owner."""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional

_TIMEOUT = float(os.getenv("PANTHEON_GOVERNANCE_APPROVAL_TIMEOUT_SECONDS", "15"))
_ACTOR_ROLES = ("governance_reviewer", "risk_owner", "governance_committee", "automated_gate")
_OUTCOMES = {"approve": "approved", "approved": "approved", "approvedecision": "approved",
             "reject": "rejected", "rejected": "rejected", "rejectdecision": "rejected",
             "approved_with_conditions": "approved_with_conditions", "approvedwithconditions": "approved_with_conditions",
             "approve_with_conditions": "approved_with_conditions", "approvewithconditions": "approved_with_conditions"}
_PENDING = {"proposed", "under_review"}


class UnsupportedApprovalAction(ValueError): pass
class RetiredApprovalAction(ValueError): pass
class InvalidApprovalRequest(ValueError): pass


def owner_url(path: str) -> str:
    from ..command_adapters.base import governance_approval_url
    return governance_approval_url(path)


def bearer(authorization: Optional[str]) -> str:
    t = str(authorization or "").strip()
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


def call_owner(
    method: str, path: str, authorization: Optional[str], *,
    body: Optional[Mapping[str, Any]] = None, idempotency_key: Optional[str] = None,
    query: Optional[Mapping[str, Any]] = None,
) -> Any:
    params = {k: v for k, v in (query or {}).items() if v not in (None, "")}
    url = owner_url(path) + (("?" + urllib.parse.urlencode(params)) if params else "")
    headers = {"Accept": "application/json", "Authorization": bearer(authorization)}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None: headers["Content-Type"] = "application/json"
    if idempotency_key: headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _claims(authorization: Optional[str]) -> Dict[str, Any]:
    try:
        segment = bearer(authorization).split(" ", 1)[1].split(".")[1]
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except Exception:
        return {}


def project(decision: Mapping[str, Any]) -> Dict[str, Any]:
    state = str(decision.get("decision_state") or "")
    status = "pending" if state in _PENDING else state
    return {"id": decision.get("decision_id"), "outcome": decision.get("decision"), "status": status, "state": status, **decision}


def list_decisions(authorization: Optional[str], *, state: Optional[str] = None, outcome: Optional[str] = None, pending_only: bool = False) -> List[Dict[str, Any]]:
    ws = {p.strip().lower() for p in str(state or "").split(",") if p.strip()}
    wo = {p.strip().lower() for p in str(outcome or "").split(",") if p.strip()}
    return [it for it in (project(x) for x in call_owner("GET", "/api/governance/approvals", authorization))
            if (not pending_only or it["status"] == "pending")
            and (not ws or str(it.get("decision_state")).lower() in ws or it["status"] in ws)
            and (not wo or str(it.get("outcome") or "").lower() in wo)]


def get_decision(authorization: Optional[str], decision_id: str) -> Dict[str, Any]:
    return project(call_owner("GET", f"/api/governance/approvals/{urllib.parse.quote(decision_id, safe='')}", authorization))


def propose(authorization: Optional[str], payload: Mapping[str, Any], idempotency_key: str) -> Dict[str, Any]:
    if not payload.get("target_type") or not payload.get("target_id"):
        raise UnsupportedApprovalAction("legacy incomplete approval create is unsupported; submit a complete proposal")
    return project(call_owner("POST", "/api/governance/approvals", authorization, body=payload, idempotency_key=idempotency_key))


def decide(authorization: Optional[str], decision_id: str, params: Mapping[str, Any], idempotency_key: str) -> Dict[str, Any]:
    if any(params.get(k) not in (None, "") for k in ("stage_name", "stageName", "stage_id", "stageId", "stage")):
        raise UnsupportedApprovalAction("named stage approvals are unsupported; votes must target whole approval")
    raw_v = [re.sub(r"[^a-z0-9]", "", str(params.get(k) or "").lower()) for k in ("outcome", "decision", "action", "verb", "action_id", "actionId")]
    if any(v in {"requestrevision", "requestapprovalrevision", "requestchanges", "requestchange"} for v in raw_v if v) or params.get("revision_notes") or params.get("revisionNotes"):
        raise RetiredApprovalAction("RequestApprovalRevision is retired; use RejectDecision with notes")
    if any(v in {"stage", "freeze", "escalate"} for v in raw_v):
        raise UnsupportedApprovalAction("unsupported approval action")
    known_verbs = {_OUTCOMES[v] for v in raw_v if v in _OUTCOMES}
    unknown_verbs = {v for v in raw_v if v and v not in _OUTCOMES}
    if unknown_verbs:
        raise InvalidApprovalRequest(f"unknown approval action: {next(iter(unknown_verbs))}")
    if "rejected" in known_verbs and any(k.startswith("approved") for k in known_verbs):
        raise InvalidApprovalRequest("outcome")
    verb = "approved_with_conditions" if "approved_with_conditions" in known_verbs else (
        next(iter(known_verbs), None) or ("rejected" if params.get("rejection_reason") else "approved")
    )
    if len(known_verbs) > 1 and "approved_with_conditions" not in known_verbs:
        raise InvalidApprovalRequest("outcome")
    if verb not in _OUTCOMES.values():
        raise UnsupportedApprovalAction(f"approval action {verb!r} has no Governance owner transition")
    version = params.get("expected_version", params.get("expectedVersion"))
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise InvalidApprovalRequest("expected_version")
    rationale = next((str(params[k]).strip() for k in ("rationale", "memo", "approval_notes", "rejection_reason", "notes") if str(params.get(k) or "").strip()), "")
    if not rationale:
        raise InvalidApprovalRequest("rationale")
    claims = _claims(authorization)
    roles = claims.get("roles") if isinstance(claims.get("roles"), list) else []
    held = [r for r in _ACTOR_ROLES if r in roles]
    role = params.get("actor_role") or (held[0] if len(held) == 1 else None)
    if not role or role not in _ACTOR_ROLES:
        raise InvalidApprovalRequest("actor_role")
    body = {"expected_version": version, "actor_role": role, "actor_id": claims.get("sub"),
            "outcome": verb, "rationale": rationale,
            **{k: params[k] for k in ("conditions", "evidence_refs", "session_id", "candidate_digest", "proof_digest", "expires_at") if params.get(k) is not None}}
    return project(call_owner("POST", f"/api/governance/approvals/{urllib.parse.quote(decision_id, safe='')}/decide", authorization, body=body, idempotency_key=idempotency_key))
