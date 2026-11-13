"""Shared admission and failure envelopes for restored owner-backed views."""
from __future__ import annotations

import logging
from typing import Any, Mapping

from fastapi import HTTPException, Request

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope

log = logging.getLogger(__name__)


def unavailable(owner: str, reason: str, status: int = 503) -> HTTPException:
    log.warning("Agora %s unavailable: %s", owner, reason)
    return HTTPException(status, detail={"status": "unavailable", "owner": owner, "reason": reason,
                                        "fallback_used": False})


def admit(request: Request, *, extract_identity, require_role, utc_now):
    identity = extract_identity(request.headers.get("Authorization"),
                                session_cookie=request.cookies.get("pantheon_session"))
    require_role(identity)
    try:
        scope = resolve_agora_user_scope(identity, utc_now=utc_now,
                                        requested_tenant_id=request.headers.get("X-Tenant-Id"))
    except AgoraScopeResolutionError as exc:
        raise unavailable("identity", exc.reason, exc.status_code) from exc
    return identity, scope


def envelope(data: Any, *, owner: str, empty_reason: str = "owner_has_no_records") -> dict:
    empty = data == []
    return {"status": "unavailable" if empty else "ok", "reason": empty_reason if empty else None,
            "owner": owner, "data": data, "fallback_used": False,
            "environment_ceiling": "paper", "execution_authority": "none"}

# ---- Case-file projection -----------------------------------------------------------
# Pure projection of owner-persisted decision case files (shared by the OODA packet
# detail and the committee views). No synthetic items, no model call.

_PRIVATE = "persona_private"


def unavailable_case(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason, "case_file": None}


def _private(item: Mapping[str, Any]) -> bool:
    return item.get("relevance_scope") == _PRIVATE or item.get("memory_layer") == _PRIVATE


def project_case_file(case: Any, *, tenant_id: str, private_persona_ids: frozenset = frozenset()) -> dict:
    """Project one case file. Private memory only for its own subject persona."""
    if (not isinstance(case, Mapping) or not case.get("case_file_id") or not isinstance(case.get("groups"), Mapping)
            or (case.get("subject") or {}).get("tenant_id") != tenant_id):
        return unavailable_case("case_file_invalid")
    subject = case["subject"]
    may_see_private = bool(subject.get("persona_id")) and private_persona_ids == {subject["persona_id"]}
    groups, withheld = {}, 0
    for name, group in case["groups"].items():
        if not isinstance(group, Mapping):
            continue
        items = []
        for item in group.get("items") or []:
            if not isinstance(item, Mapping) or not item.get("item_id"):
                continue
            if _private(item) and not may_see_private:
                withheld += 1
                continue
            items.append({"item_id": item["item_id"], "kind": item.get("kind"), "excerpt": item.get("excerpt"),
                          "source_owner": item.get("source_owner"), "source_id": item.get("source_id"),
                          "retrieval_mode": item.get("retrieval_mode"),
                          "retrieval_score": item.get("retrieval_score")})
        groups[name] = {"status": group.get("status"), "reason": group.get("reason"), "items": items,
                        "unavailable": list(group.get("unavailable") or []),
                        "truncated": bool(group.get("truncated"))}
    return {"status": "ok", "reason": None, "case_file": {
        "case_file_id": case["case_file_id"], "digest": case.get("digest"),
        "decision_type": subject.get("decision_type"), "persona_id": subject.get("persona_id"),
        "composed_at": case.get("composed_at"), "groups": groups, "private_items_withheld": withheld}}


def item_ids(view: Mapping[str, Any]) -> set:
    case = view.get("case_file") or {}
    return {i["item_id"] for g in case.get("groups", {}).values() for i in g["items"]}


def _citations(refs: Any, known: set) -> dict:
    refs = [r for r in refs if isinstance(r, str)] if isinstance(refs, list) else []
    return {"cited_item_ids": [r for r in refs if r in known],
            "unresolved_item_ids": [r for r in refs if r not in known]}


def project_packet(packet: Mapping[str, Any]) -> dict:
    """Case file(s) of an OODA packet plus the decisions that cite them."""
    cycle = packet.get("persona_cycle")
    if not isinstance(cycle, Mapping):
        return unavailable_case("packet_has_no_persona_cycle")
    cases = cycle.get("case_files")
    if not isinstance(cases, Mapping) or not cases:
        return unavailable_case(str(cycle.get("failure_reason") or "no_case_file_recorded"))
    tenant, personas = packet.get("tenant_id"), frozenset(packet.get("persona_ids") or [])
    views = {cid: project_case_file(c, tenant_id=tenant, private_persona_ids=personas) for cid, c in cases.items()}
    out = []
    for cid, view in views.items():
        if view["status"] == "ok" and view["case_file"]["case_file_id"] == cid:
            out.append(view["case_file"])
    if not out:
        return unavailable_case("case_file_invalid")
    known = {i["item_id"] for c in out for g in c["groups"].values() for i in g["items"]}
    decisions = [{"kind": d.get("kind"), "status": d.get("status"), "case_file_id": d.get("case_file_id"),
                  "case_file_scope": d.get("case_file_scope"),
                  "none_applicable_reason": d.get("none_applicable_reason"),
                  **_citations(d.get("cited_case_file_item_ids"), known)}
                 for d in cycle.get("decisions") or [] if isinstance(d, Mapping)]
    return {"status": "ok", "reason": None, "case_files": out, "decisions": decisions}


def project_panel(transcript: Any, *, request_id: str, tenant_id: str) -> dict:
    """Case file, per-claim citations and rejections of one persona panel."""
    if not isinstance(transcript, Mapping) or transcript.get("request_id") != request_id:
        return unavailable_case("invalid_transcript_response")
    events = [e for e in transcript.get("events") or [] if isinstance(e, Mapping)]
    attached = [e for e in events if e.get("event_type") == "panel_case_file_attached"]
    if not attached:
        return unavailable_case("no_case_file_recorded")
    content = attached[-1].get("content") or {}
    view = project_case_file(content.get("case_file"), tenant_id=tenant_id)
    if view["status"] != "ok" or view["case_file"]["case_file_id"] != content.get("case_file_id"):
        return unavailable_case("case_file_invalid")
    case_id, known = content["case_file_id"], item_ids(view)
    contributions, rejected = [], []
    for e in events:
        c, actor = e.get("content") or {}, e.get("actor") or {}
        if c.get("case_file_id") != case_id:
            continue
        if e.get("event_type") == "panel_contribution_rejected":
            rejected.append({"persona_id": c.get("persona_id"), "contribution_id": c.get("contribution_id"),
                             "reason": c.get("reason"), "cited": False})
        elif actor.get("actor_type") == "persona" and isinstance(c.get("claims"), list):
            contributions.append({"persona_id": actor.get("actor_id"), "event_id": e.get("event_id"),
                "recommendation": c.get("recommendation"), "role": c.get("role"),
                "claims": [{"claim": cl.get("claim"), **_citations(cl.get("evidence_refs"), known)}
                           for cl in c["claims"] if isinstance(cl, Mapping)],
                **{"summary_" + k: v for k, v in _citations(c.get("summary_evidence_refs"), known).items()}})
    return {"status": "ok", "reason": None, **view, "contributions": contributions,
            "rejected_contributions": rejected}
