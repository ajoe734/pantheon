"""Single BFF forwarding path to the Governance approval owner.

The BFF keeps no approval state. Every approval entry point (REST, ``/bff``,
and the ApproveDecision / RejectDecision / ReviewAction commands) forwards the
caller's original verified JWT to the Governance owner; the owner alone decides
authority, tenant scope and CAS. Unsupported verbs fail explicitly.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional

_TIMEOUT = float(os.getenv("PANTHEON_GOVERNANCE_APPROVAL_TIMEOUT_SECONDS", "15"))
_ACTOR_ROLES = ("governance_reviewer", "risk_owner", "governance_committee")
_OUTCOMES = {"approve": "approved", "approved": "approved", "reject": "rejected", "rejected": "rejected",
             "approved_with_conditions": "approved_with_conditions"}
_PENDING = {"proposed", "under_review"}


class UnsupportedApprovalAction(ValueError):
    """The verb has no Governance owner transition and is never reinterpreted as a vote."""


class InvalidApprovalRequest(ValueError):
    """The request is missing a field the owner contract requires."""


def owner_url(path: str) -> str:
    from ..command_adapters.base import governance_approval_url

    return governance_approval_url(path)


def bearer(authorization: Optional[str]) -> str:
    token = str(authorization or "").strip()
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def call_owner(
    method: str,
    path: str,
    authorization: Optional[str],
    *,
    body: Optional[Mapping[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    query: Optional[Mapping[str, Any]] = None,
) -> Any:
    """Forward one call; ``urllib.error.HTTPError`` carries the owner's status/body."""
    url = owner_url(path)
    params = {key: value for key, value in (query or {}).items() if value not in (None, "")}
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "Authorization": bearer(authorization)}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _claims(authorization: Optional[str]) -> Dict[str, Any]:
    """Unverified claims, used only to fill body defaults; the owner verifies the JWT."""
    try:
        segment = bearer(authorization).split(" ", 1)[1].split(".")[1]
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except Exception:
        return {}


def project(decision: Mapping[str, Any]) -> Dict[str, Any]:
    """Thin FE aliases over the owner DTO; first vote (under_review) reads as pending."""
    state = str(decision.get("decision_state") or "")
    return {
        "id": decision.get("decision_id"),
        "outcome": decision.get("decision"),
        "status": "pending" if state in _PENDING else state,
        "state": "pending" if state in _PENDING else state,
        **decision,
    }


def list_decisions(authorization: Optional[str], *, state: Optional[str] = None, outcome: Optional[str] = None,
                   pending_only: bool = False) -> List[Dict[str, Any]]:
    wanted_states = {part.strip().lower() for part in str(state or "").split(",") if part.strip()}
    wanted_outcomes = {part.strip().lower() for part in str(outcome or "").split(",") if part.strip()}
    items = [project(item) for item in call_owner("GET", "/api/governance/approvals", authorization)]
    return [
        item for item in items
        if (not pending_only or item["status"] == "pending")
        and (not wanted_states or str(item.get("decision_state")).lower() in wanted_states or item["status"] in wanted_states)
        and (not wanted_outcomes or str(item.get("outcome") or "").lower() in wanted_outcomes)
    ]


def get_decision(authorization: Optional[str], decision_id: str) -> Dict[str, Any]:
    return project(call_owner("GET", f"/api/governance/approvals/{urllib.parse.quote(decision_id, safe='')}", authorization))


def propose(authorization: Optional[str], payload: Mapping[str, Any], idempotency_key: str) -> Dict[str, Any]:
    if not payload.get("target_type") or not payload.get("target_id"):
        raise UnsupportedApprovalAction("legacy incomplete approval create is unsupported; submit a complete proposal")
    return project(call_owner("POST", "/api/governance/approvals", authorization, body=payload, idempotency_key=idempotency_key))


def decide(authorization: Optional[str], decision_id: str, params: Mapping[str, Any], idempotency_key: str) -> Dict[str, Any]:
    """Forward one human vote; the owner moves PROPOSED→UNDER_REVIEW→DECIDED inside one CAS."""
    verbs = {_OUTCOMES.get(v, v) for v in (str(params.get(k) or "").strip().lower() for k in ("outcome", "decision")) if v}
    if len(verbs) > 1:
        raise InvalidApprovalRequest("outcome")
    verb = next(iter(verbs), "rejected" if params.get("rejection_reason") else "approved")
    if verb not in _OUTCOMES.values():
        raise UnsupportedApprovalAction(f"approval action {verb!r} has no Governance owner transition")
    version = params.get("expected_version", params.get("expectedVersion"))
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise InvalidApprovalRequest("expected_version")
    rationale = next((str(params[key]).strip() for key in ("rationale", "memo", "approval_notes", "rejection_reason", "notes")
                      if str(params.get(key) or "").strip()), "")
    if not rationale:
        raise InvalidApprovalRequest("rationale")
    claims = _claims(authorization)
    roles = claims.get("roles") if isinstance(claims.get("roles"), list) else []
    held = [role for role in _ACTOR_ROLES if role in roles]
    role = params.get("actor_role") or (held[0] if len(held) == 1 else None)
    if not role:
        raise InvalidApprovalRequest("actor_role")
    body = {
        "expected_version": version, "actor_role": role, "actor_id": claims.get("sub"),
        "outcome": verb, "rationale": rationale,
    }
    body.update({key: params[key] for key in ("conditions", "evidence_refs", "session_id", "candidate_digest",
                                              "proof_digest", "expires_at") if params.get(key) is not None})
    path = f"/api/governance/approvals/{urllib.parse.quote(decision_id, safe='')}/decide"
    return project(call_owner("POST", path, authorization, body=body, idempotency_key=idempotency_key))
