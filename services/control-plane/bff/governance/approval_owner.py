"""Single BFF forwarding path to the Governance approval owner."""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

_TIMEOUT = float(os.getenv("PANTHEON_GOVERNANCE_APPROVAL_TIMEOUT_SECONDS", "15"))
_ACTOR_ROLES = ("governance_reviewer", "risk_owner", "governance_committee", "automated_gate")
_OUTCOMES = {
    "approve": "approved",
    "approved": "approved",
    "approvedecision": "approved",
    "reject": "rejected",
    "rejected": "rejected",
    "rejectdecision": "rejected",
    "approved_with_conditions": "approved_with_conditions",
    "approvedwithconditions": "approved_with_conditions",
    "approve_with_conditions": "approved_with_conditions",
    "approvewithconditions": "approved_with_conditions",
}
_PENDING = {"proposed", "under_review"}
OWNER_PENDING_DECISION_STATES: tuple[str, ...] = ("proposed", "under_review")
OWNER_DECISION_STATES = {"proposed", "under_review", "decided", "superseded", "revoked"}


def map_owner_decision_states(states: Iterable[str]) -> tuple[str, ...]:
    mapped: List[str] = []
    seen: set[str] = set()
    for raw in states or ():
        for part in str(raw or "").split(","):
            val = part.strip().lower()
            if not val:
                continue
            if val in ("pending", "in_review", "reviewed", "proposed", "under_review"):
                candidates: Iterable[str] = (
                    OWNER_PENDING_DECISION_STATES if val in ("pending", "in_review", "reviewed") else (val,)
                )
            elif val in OWNER_DECISION_STATES:
                candidates = (val,)
            else:
                continue
            for candidate in candidates:
                if candidate not in seen:
                    seen.add(candidate)
                    mapped.append(candidate)
    return tuple(mapped)


class UnsupportedApprovalAction(ValueError):
    """Raised when an approval action has no valid governance transition."""


class RetiredApprovalAction(ValueError):
    """Raised when a retired approval action is attempted."""


class InvalidApprovalRequest(ValueError):
    """Raised when approval request parameters are invalid."""


def owner_url(path: str) -> str:
    from ..command_adapters.base import governance_approval_url

    return governance_approval_url(path)


def bearer(authorization: Optional[str]) -> str:
    t = str(authorization or "").strip()
    return t if t.lower().startswith("bearer ") else f"Bearer {t}"


def call_owner(
    method: str,
    path: str,
    authorization: Optional[str],
    *,
    body: Optional[Mapping[str, Any]] = None,
    idempotency_key: Optional[str] = None,
    query: Optional[Mapping[str, Any]] = None,
    timeout: Optional[float] = None,
) -> Any:
    params = {k: v for k, v in (query or {}).items() if v not in (None, "")}
    url = owner_url(path) + (("?" + urllib.parse.urlencode(params, safe=",")) if params else "")
    headers = {"Accept": "application/json", "Authorization": bearer(authorization)}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    effective_timeout = _TIMEOUT if timeout is None else min(float(_TIMEOUT), float(timeout))
    if effective_timeout <= 0.0:
        raise TimeoutError("Governance approval owner read budget exhausted")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _claims(authorization: Optional[str]) -> Dict[str, Any]:
    try:
        segment = bearer(authorization).split(" ", 1)[1].split(".")[1]
        return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except Exception:
        return {}


def project(decision: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(decision, Mapping):
        raise ValueError("Malformed decision item in owner response")
    state = str(decision.get("decision_state") or decision.get("state") or "")
    dec_id = decision.get("decision_id") or decision.get("id")
    target_type = decision.get("decision_type") or decision.get("target_type") or "ApprovalDecision"
    status = "pending" if state in _PENDING else state
    can_decide = state in {"under_review", "reviewed", "in_review", "proposed", "pending"}
    res = {"id": dec_id, "outcome": decision.get("decision"), "status": status, "state": status}
    res.update(decision)
    res["decision_id"] = dec_id
    res["decision_type"] = target_type
    res.setdefault("risk_level", decision.get("risk_level"))
    res.setdefault("submitted_at", decision.get("created_at"))
    res.setdefault("submitted_by", decision.get("owner_user_id") or decision.get("actor_id") or decision.get("created_by"))
    if state:
        res.setdefault("decision_state", state)
    res.setdefault("allowedActions", {"canApprove": can_decide, "canReject": can_decide, "canRequestRevision": False})
    context = {
        "risk_summary": decision.get("rationale"),
        "evidence_refs": list(decision.get("evidence_refs") or []),
        "governance_chain": {
            "target_type": target_type,
            "target_id": decision.get("target_id"),
            "target_version": decision.get("target_version"),
        },
    }
    if decision.get("required_approvals") is not None:
        context["required_approvals"] = decision["required_approvals"]
    res.setdefault("decision_context", context)
    return res


def list_decisions(
    authorization: Optional[str],
    *,
    state: Optional[str] = None,
    outcome: Optional[str] = None,
    pending_only: bool = False,
    timeout: Optional[float] = None,
    owner_decision_states: Optional[Sequence[str]] = None,
    decision_ids: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    ws = {p.strip().lower() for p in str(state or "").split(",") if p.strip()}
    wo = {p.strip().lower() for p in str(outcome or "").split(",") if p.strip()}
    clean_ids: Optional[tuple[str, ...]] = None
    clean_id_set: Optional[set[str]] = None
    if decision_ids is not None:
        clean_ids = tuple(
            dict.fromkeys(
                clean
                for raw in decision_ids
                for part in str(raw or "").split(",")
                if (clean := part.strip())
            )
        )
        if not clean_ids:
            return []
        clean_id_set = set(clean_ids)
    mapped_states: Optional[tuple[str, ...]] = None
    if owner_decision_states is not None:
        mapped_states = map_owner_decision_states(owner_decision_states)
        if not mapped_states:
            return []
    if timeout is None:
        from ..core.owner_reads import remaining_management_read_budget

        timeout = remaining_management_read_budget()
    query: Dict[str, str] = {}
    if mapped_states:
        query["decision_state"] = ",".join(mapped_states)
    if clean_ids:
        query["decision_id"] = ",".join(clean_ids)
    call_kwargs: Dict[str, Any] = {}
    if timeout is not None:
        call_kwargs["timeout"] = timeout
    if query:
        call_kwargs["query"] = query
    raw_decisions = call_owner(
        "GET",
        "/api/governance/approvals",
        authorization,
        **call_kwargs,
    )
    if not isinstance(raw_decisions, list):
        raise ValueError("Governance owner returned non-list decisions response")
    results = []
    for x in raw_decisions:
        it = project(x)
        if clean_id_set is not None:
            dec_id = str(x.get("decision_id") or x.get("id") or it.get("decision_id") or "").strip()
            if dec_id not in clean_id_set:
                continue
        raw_state = str(x.get("decision_state") or x.get("state") or it.get("decision_state") or "").lower()
        status = "pending" if raw_state in _PENDING or raw_state == "pending" else raw_state
        raw_outcome = str(x.get("decision") or x.get("outcome") or it.get("outcome") or "").lower()
        if pending_only and status != "pending":
            continue
        if ws and raw_state not in ws and status not in ws:
            continue
        if wo and raw_outcome not in wo:
            continue
        results.append(it)
    return results


def get_decision(authorization: Optional[str], decision_id: str) -> Dict[str, Any]:
    return project(call_owner("GET", f"/api/governance/approvals/{urllib.parse.quote(decision_id, safe='')}", authorization))


def propose(authorization: Optional[str], payload: Mapping[str, Any], idempotency_key: str) -> Dict[str, Any]:
    if not payload.get("target_type") or not payload.get("target_id"):
        raise UnsupportedApprovalAction("legacy incomplete approval create is unsupported; submit a complete proposal")
    return project(call_owner("POST", "/api/governance/approvals", authorization, body=payload, idempotency_key=idempotency_key))


def decide(authorization: Optional[str], decision_id: str, params: Mapping[str, Any], idempotency_key: str) -> Dict[str, Any]:
    if any(params.get(k) not in (None, "") for k in ("stage_name", "stageName", "stage_id", "stageId", "stage")):
        raise UnsupportedApprovalAction("named stage approvals are unsupported; votes must target whole approval")
    for k in ("outcome", "decision", "action", "verb", "action_id", "actionId"):
        v_check = params.get(k)
        if v_check is not None and not isinstance(v_check, str):
            raise InvalidApprovalRequest(f"Invalid {k} carrier type: must be a string")
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
    rationale = next(
        (str(params[k]).strip() for k in ("rationale", "memo", "approval_notes", "rejection_reason", "notes") if str(params.get(k) or "").strip()),
        "",
    )
    if not rationale:
        raise InvalidApprovalRequest("rationale")
    claims = _claims(authorization)
    roles = claims.get("roles") if isinstance(claims.get("roles"), list) else []
    held = [r for r in _ACTOR_ROLES if r in roles]
    role = params.get("actor_role") or (held[0] if len(held) == 1 else None)
    if not role or role not in _ACTOR_ROLES:
        raise InvalidApprovalRequest("actor_role")
    optional_fields = {
        k: params[k]
        for k in ("conditions", "evidence_refs", "session_id", "candidate_digest", "proof_digest", "expires_at")
        if params.get(k) is not None
    }
    body = {
        "expected_version": version,
        "actor_role": role,
        "actor_id": claims.get("sub"),
        "outcome": verb,
        "rationale": rationale,
        **optional_fields,
    }
    return project(call_owner("POST", f"/api/governance/approvals/{urllib.parse.quote(decision_id, safe='')}/decide", authorization, body=body, idempotency_key=idempotency_key))
