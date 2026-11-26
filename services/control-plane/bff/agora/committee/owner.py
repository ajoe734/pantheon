"""Shared admission and failure envelopes for restored owner-backed views."""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

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
_PRIVATE_SCOPES = frozenset({_PRIVATE, "private_user", "user_private", "private"})


def unavailable_case(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason, "case_file": None}


def _private(item: Mapping[str, Any]) -> bool:
    for key in ("relevance_scope", "memory_layer", "scope", "memory_scope", "visibility_scope", "owner_scope"):
        value = str(item.get(key) or "").strip().lower()
        if value in _PRIVATE_SCOPES:
            return True
    return False


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


def project_panel_rounds(transcript: Any = None, *, case_file_view: Optional[Mapping[str, Any]] = None) -> list[dict]:
    """Project the single independent round of a gate panel for unified session reads."""
    positions: list[dict] = []
    if isinstance(case_file_view, Mapping):
        for contrib in case_file_view.get("contributions") or []:
            if not isinstance(contrib, Mapping):
                continue
            claims = contrib.get("claims") or []
            citations = [
                cid
                for cl in claims if isinstance(cl, Mapping)
                for cid in (cl.get("cited_item_ids") or [])
                if isinstance(cid, str)
            ]
            positions.append({
                "label": str(contrib.get("role") or contrib.get("persona_id") or "panel_reviewer"),
                "persona_id": contrib.get("persona_id"),
                "role": contrib.get("role") or "panel_reviewer",
                "style": "independent_gate_panel",
                "stance": contrib.get("recommendation") or "review",
                "confidence": None,
                "citations": list(dict.fromkeys(citations)),
                "changed": False,
            })
    if not positions and isinstance(transcript, Mapping):
        for event in transcript.get("events") or []:
            if not isinstance(event, Mapping):
                continue
            actor = event.get("actor") if isinstance(event.get("actor"), Mapping) else {}
            content = event.get("content") if isinstance(event.get("content"), Mapping) else {}
            if actor.get("actor_type") == "persona" or event.get("event_type") == "persona_contribution":
                positions.append({
                    "label": str(content.get("role") or actor.get("actor_id") or "panel_reviewer"),
                    "persona_id": actor.get("actor_id") or content.get("persona_id"),
                    "role": content.get("role") or "panel_reviewer",
                    "style": "independent_gate_panel",
                    "stance": content.get("recommendation") or content.get("claim") or "submitted",
                    "confidence": content.get("confidence"),
                    "citations": [r for r in (content.get("evidence_refs") or []) if isinstance(r, str)],
                    "changed": False,
                })
    return [{"round": 1, "mode": "independent", "positions": positions}]


def project_meeting_proposal(proposal: Any, *, meeting_id: Optional[str] = None) -> dict:
    """Normalize a meeting proposal and mark unsponsored proposals for Agora candidate decisions."""
    if not isinstance(proposal, Mapping):
        raise unavailable("consultation", "invalid_meeting_proposal_response")
    pid = str(proposal.get("proposal_id") or proposal.get("id") or "").strip()
    if not pid:
        raise unavailable("consultation", "invalid_meeting_proposal_response")
    sponsor = proposal.get("sponsor_persona_id")
    sponsor_clean = str(sponsor).strip() if sponsor is not None and str(sponsor).strip() else None
    kind = str(proposal.get("kind") or ("agora_candidate_decision" if sponsor_clean is None else "research_demand")).strip()
    is_unsponsored = sponsor_clean is None or kind == "agora_candidate_decision"
    downstream = proposal.get("downstream") if isinstance(proposal.get("downstream"), Mapping) else {}
    target = str(
        downstream.get("target")
        or proposal.get("downstream_target")
        or ("agora_candidate_decision" if is_unsponsored else kind)
    )
    downstream_id = downstream.get("id") or proposal.get("downstream_id") or (pid if is_unsponsored else None)
    downstream_status = str(downstream.get("status") or proposal.get("status") or "draft")
    return {
        **{k: v for k, v in proposal.items() if not _private({k: v})},
        "proposal_id": pid,
        "meeting_id": str(proposal.get("meeting_id") or meeting_id or ""),
        "kind": "agora_candidate_decision" if is_unsponsored and kind == "agora_candidate_decision" else kind,
        "sponsor_persona_id": None if is_unsponsored else sponsor_clean,
        "approval_path": "agora_candidate_decision" if is_unsponsored else "governed_owner_entry_point",
        "downstream": {
            "target": target,
            "id": downstream_id,
            "status": downstream_status,
        },
        "execution_authority": "none",
    }


def project_meeting_case_file(
    meeting: Mapping[str, Any],
    *,
    tenant_id: str,
    meeting_id: Optional[str] = None,
) -> dict:
    """Case file, round position citations, and rejected positions of one deliberative meeting."""
    raw_case = meeting.get("case_file")
    if raw_case is None or (isinstance(raw_case, Mapping) and not raw_case):
        return unavailable_case("no_case_file_recorded")
    view = project_case_file(raw_case, tenant_id=tenant_id)
    if view["status"] != "ok":
        return view
    mid = str(meeting_id or meeting.get("meeting_id") or meeting.get("request_id") or "").strip()
    known = item_ids(view)
    contributions: list[dict[str, Any]] = []
    raw_rounds = meeting.get("rounds")
    if isinstance(raw_rounds, list):
        for r in raw_rounds:
            if not isinstance(r, Mapping) or not isinstance(r.get("positions"), list):
                continue
            round_no = r.get("round")
            for idx, pos in enumerate(r["positions"]):
                if not isinstance(pos, Mapping) or _private(pos):
                    continue
                pid = pos.get("persona_id")
                event_id = pos.get("event_id") or (
                    f"evt-{mid}-r{round_no}-{pid or idx + 1}"
                    if mid and round_no is not None
                    else f"r{round_no}-{pid or idx + 1}"
                )
                recommendation = pos.get("recommendation") or pos.get("stance")
                pos_citations = (
                    pos.get("citations")
                    if isinstance(pos.get("citations"), list)
                    else pos.get("evidence_refs")
                )
                raw_claims = pos.get("claims")
                if isinstance(raw_claims, list) and raw_claims:
                    claims = [
                        {
                            "claim": cl.get("claim") if cl.get("claim") is not None else cl.get("summary"),
                            **_citations(
                                cl.get("evidence_refs")
                                if cl.get("evidence_refs") is not None
                                else cl.get("citations"),
                                known,
                            ),
                        }
                        for cl in raw_claims
                        if isinstance(cl, Mapping) and not _private(cl)
                    ]
                else:
                    claim_text = (
                        pos.get("summary")
                        or pos.get("claim")
                        or pos.get("stance")
                        or pos.get("recommendation")
                        or pos.get("label")
                    )
                    claims = [{"claim": claim_text, **_citations(pos_citations, known)}]
                summary_refs = (
                    pos.get("summary_evidence_refs")
                    if pos.get("summary_evidence_refs") is not None
                    else pos_citations
                )
                contributions.append({
                    "persona_id": pid,
                    "event_id": event_id,
                    "recommendation": recommendation,
                    "role": pos.get("role"),
                    "claims": claims,
                    **{"summary_" + k: v for k, v in _citations(summary_refs, known).items()},
                })
    rejected: list[dict[str, Any]] = []
    raw_rejected = meeting.get("rejected_positions")
    if isinstance(raw_rejected, list):
        for idx, rej in enumerate(raw_rejected):
            if not isinstance(rej, Mapping) or _private(rej):
                continue
            pid = rej.get("persona_id")
            r_no = rej.get("round")
            contrib_id = (
                rej.get("contribution_id")
                or rej.get("event_id")
                or (
                    f"evt-{mid}-r{r_no}-{pid or idx + 1}-rejected"
                    if mid and r_no is not None
                    else f"rej-{mid or 'meeting'}-{idx + 1}"
                )
            )
            rejected.append({
                "persona_id": pid,
                "contribution_id": contrib_id,
                "reason": rej.get("reason"),
                "cited": False,
            })
    return {
        "status": "ok",
        "reason": None,
        **view,
        "contributions": contributions,
        "rejected_contributions": rejected,
    }


def project_meeting(
    item: Any,
    *,
    tenant_id: str,
    user_id: Optional[str] = None,
    detail: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Validate and project a deliberative persona meeting from the consultation owner.

    Enforces tenant and private-servant audience boundaries and strips any
    persona_private or user_private memory items before leaving the BFF.
    """
    if not isinstance(item, Mapping):
        raise unavailable("consultation", "invalid_meeting_response")
    merged: dict[str, Any] = dict(item)
    if isinstance(detail, Mapping):
        merged.update(detail)
    meeting_id = str(merged.get("meeting_id") or merged.get("request_id") or "").strip()
    meeting_type = str(merged.get("meeting_type") or "").strip()
    if not meeting_id or not meeting_type:
        raise unavailable("consultation", "invalid_meeting_response")
    item_tenant = merged.get("tenant_id")
    if item_tenant is not None and str(item_tenant).strip() and str(item_tenant).strip() != tenant_id:
        raise unavailable("consultation", "meeting_tenant_mismatch", 404)

    convened_by = merged.get("convened_by") if isinstance(merged.get("convened_by"), Mapping) else None
    if (
        isinstance(convened_by, Mapping)
        and str(convened_by.get("kind") or "").strip() == "agora_user"
        and user_id
        and str(convened_by.get("id") or "").strip()
        and str(convened_by.get("id") or "").strip() != user_id
    ):
        raise unavailable("consultation", "meeting_not_found", 404)

    raw_participants = merged.get("participants")
    participants: list[dict[str, Any]] = []
    if isinstance(raw_participants, list):
        for p in raw_participants:
            if isinstance(p, str):
                participants.append({"persona_id": p, "role": "participant", "origin": "institutional"})
                continue
            if not isinstance(p, Mapping) or not p.get("persona_id"):
                raise unavailable("consultation", "invalid_meeting_response")
            origin = str(p.get("origin") or "institutional").strip()
            servant_user = str(p.get("agora_user_id") or "").strip() or None
            if (origin == "private_servant" or servant_user) and user_id and servant_user and servant_user != user_id:
                raise unavailable("consultation", "meeting_not_found", 404)
            entry: dict[str, Any] = {
                "persona_id": str(p["persona_id"]),
                "role": str(p.get("role") or "participant"),
                "origin": origin,
            }
            if servant_user is not None:
                entry["agora_user_id"] = servant_user
            participants.append(entry)

    private_withheld = 0
    withheld_case_item_ids: set[str] = set()
    case_file_items: list[dict[str, Any]] = []
    for citem in merged.get("case_file_items") or []:
        if not isinstance(citem, Mapping):
            continue
        if _private(citem):
            private_withheld += 1
            if citem.get("item_id"):
                withheld_case_item_ids.add(str(citem["item_id"]))
            continue
        case_file_items.append({
            k: v for k, v in citem.items()
            if k not in {"private_memory", "persona_private_memory"}
        })

    raw_case = merged.get("case_file")
    if isinstance(raw_case, Mapping) and isinstance(raw_case.get("groups"), Mapping):
        for grp in raw_case["groups"].values():
            if not isinstance(grp, Mapping):
                continue
            for gitem in grp.get("items") or []:
                if isinstance(gitem, Mapping) and _private(gitem):
                    gid = str(gitem.get("item_id") or "")
                    if not gid or gid not in withheld_case_item_ids:
                        private_withheld += 1
                        if gid:
                            withheld_case_item_ids.add(gid)

    pre_read_raw = merged.get("pre_read")
    if isinstance(pre_read_raw, list):
        pre_read: Any = []
        for pitem in pre_read_raw:
            if isinstance(pitem, Mapping):
                if _private(pitem):
                    private_withheld += 1
                    continue
                pre_read.append({
                    k: v for k, v in pitem.items()
                    if k not in {"private_memory", "persona_private_memory"}
                })
    elif isinstance(pre_read_raw, Mapping):
        pre_read = {}
        for k, v in pre_read_raw.items():
            if isinstance(v, Mapping) and _private(v):
                private_withheld += 1
                continue
            if isinstance(v, list):
                filtered_list = []
                for elem in v:
                    if isinstance(elem, Mapping) and _private(elem):
                        private_withheld += 1
                        continue
                    filtered_list.append(elem)
                pre_read[k] = filtered_list
            else:
                pre_read[k] = v
    else:
        pre_read = pre_read_raw

    raw_rounds = merged.get("rounds")
    rounds: list[dict[str, Any]] = []
    if isinstance(raw_rounds, list):
        for r in raw_rounds:
            if not isinstance(r, Mapping) or r.get("round") is None or not isinstance(r.get("positions"), list):
                raise unavailable("consultation", "invalid_meeting_rounds_response")
            positions = []
            for pos in r["positions"]:
                if not isinstance(pos, Mapping):
                    raise unavailable("consultation", "invalid_meeting_rounds_response")
                if _private(pos):
                    private_withheld += 1
                    continue
                if isinstance(pos.get("claims"), list):
                    for cl in pos["claims"]:
                        if isinstance(cl, Mapping) and _private(cl):
                            private_withheld += 1
                positions.append({
                    "label": pos.get("label"),
                    "persona_id": pos.get("persona_id"),
                    "role": pos.get("role"),
                    "style": pos.get("style"),
                    "stance": pos.get("stance"),
                    "confidence": pos.get("confidence"),
                    "citations": list(pos.get("citations") or []),
                    "changed": bool(pos.get("changed", False)),
                })
            rounds.append({
                "round": int(r["round"]),
                "positions": positions,
            })

    if isinstance(merged.get("rejected_positions"), list):
        for rej in merged["rejected_positions"]:
            if isinstance(rej, Mapping) and _private(rej):
                private_withheld += 1

    synthesis_raw = merged.get("synthesis")
    synthesis: Optional[dict[str, Any]] = None
    projected_proposals: list[dict[str, Any]] = []
    if isinstance(synthesis_raw, Mapping):
        raw_props = synthesis_raw.get("proposals") or []
        if isinstance(raw_props, list):
            projected_proposals = [project_meeting_proposal(p, meeting_id=meeting_id) for p in raw_props]
        synthesis = {
            **dict(synthesis_raw),
            "proposals": projected_proposals,
        }
    elif isinstance(merged.get("proposals"), list):
        projected_proposals = [project_meeting_proposal(p, meeting_id=meeting_id) for p in merged["proposals"]]

    round_count = merged.get("round")
    if round_count is None:
        round_count = len(rounds)

    case_file_view = project_meeting_case_file(merged, tenant_id=tenant_id, meeting_id=meeting_id)

    return {
        **{
            k: v
            for k, v in merged.items()
            if k not in {"case_file", "case_file_items", "pre_read", "rounds", "synthesis", "rejected_positions"}
        },
        "session_id": meeting_id,
        "meeting_id": meeting_id,
        "request_id": meeting_id,
        "meeting_type": meeting_type,
        "consultation_type": "persona_meeting",
        "session_class": "deliberative_meeting",
        "status": str(merged.get("status") or "open"),
        "outcome": merged.get("outcome") or (synthesis.get("outcome") if isinstance(synthesis, Mapping) else None),
        "chair_persona_id": merged.get("chair_persona_id"),
        "participants": participants,
        "round": round_count,
        "rounds": rounds,
        "agenda": merged.get("agenda") or merged.get("topic"),
        "case_file_items": case_file_items,
        "case_file_view": case_file_view,
        "pre_read": pre_read,
        "synthesis": synthesis,
        "proposals": projected_proposals,
        "writebacks": list(merged.get("writebacks") or []),
        "usage": merged.get("usage"),
        "private_items_withheld": private_withheld,
        "environment_ceiling": "paper",
        "execution_authority": "none",
    }
