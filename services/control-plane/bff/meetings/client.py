"""Consultation-owner client and console projections for deliberative persona meetings.

The BFF keeps no meeting store: all reads and the operator convene action
forward to the consultation service (`services/consultation`).
"""
from __future__ import annotations

import re
import json
import os
import time
from contextlib import contextmanager
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from datetime import datetime, timezone
from urllib.parse import quote

from services.consultation.client import ConsultationClientError, ConsultationServiceClient
from services.control_plane.bff.core.display_names import current_resolver
from services.control_plane.bff.core.owner_reads import (
    remaining_management_read_budget,
    management_read_deadline,
    selected_tenant,
)

MEETING_OPERATOR_REVIEW_NOTE_MAX_CHARS = 2000

_PRIVATE_SCOPES = frozenset({
    "persona_private",
    "private",
    "private_user",
    "user_private",
})
_PRIVATE_FIELD_KEYS = frozenset({
    "private_memory",
    "private_memories",
    "private_memory_text",
    "private_text",
    "private_content",
    "private_interpretation",
})
_TRADER_IDENTITY_KEYS = frozenset({
    "agora_user_id",
    "agoraUserId",
    "trader_id",
    "traderId",
    "user_id",
    "userId",
    "account_id",
    "accountId",
    "broker_ids",
    "brokerIds",
    "broker_account",
    "brokerAccount",
})
_WRITEBACK_ALLOWED_KEYS = frozenset({
    "persona_id",
    "entry_id",
    "status",
    "written_at",
})
_DOWNSTREAM_TARGET_HREFS = {
    "research_demand": "/management/experiments",
    "research_ticket": "/management/experiments",
    "evolution_proposal": "/management/evolution",
    "harness_patch": "/management/personas",
    "lesson_candidate": "/management/governance/memory",
    "lesson_rule": "/management/personas",
    "pool_decision": "/management/capital",
    "governance_approval": "/management/approvals",
    "agora_candidate_decision": "/agora/committee",
}
_TERMINAL_PROPOSAL_STATUSES = frozenset({
    "approved",
    "rejected",
    "completed",
    "applied",
    "endorsed",
    "canceled",
    "cancelled",
    "closed",
    "done",
    "dispatched_terminal",
})
_OPERATOR_WAITING_PROPOSAL_STATUSES = frozenset({
    "pending",
    "pending_operator",
    "waiting_for_operator",
    "awaiting_operator",
    "proposed",
    "in_review",
    "under_review",
    "pending_approval",
    "pending_review",
    "pending_human_gate",
    "requires_operator",
    "operator_review",
    "submitted",
})


class MeetingsOwnerUnavailableError(RuntimeError):
    """Raised when the consultation owner is unconfigured, unreachable, or returns invalid data."""

    def __init__(
        self,
        reason: str,
        *,
        status_code: int = 503,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code
        self.payload = payload or {}


class MeetingsOwnerNotFoundError(RuntimeError):
    """Raised when a requested meeting_id does not exist on the consultation owner."""

    def __init__(self, meeting_id: str, *, reason: str = "meeting_not_found") -> None:
        super().__init__(reason)
        self.meeting_id = meeting_id
        self.reason = reason
        self.status_code = 404


class MeetingsOwnerValidationError(RuntimeError):
    """Raised when the consultation owner rejects a convene or query payload as invalid."""

    def __init__(
        self,
        reason: str,
        *,
        status_code: int = 422,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code
        self.payload = payload or {}


def _is_private_memory_item(item: Mapping[str, Any]) -> bool:
    for key in ("relevance_scope", "memory_layer", "scope", "visibility_scope", "memory_scope"):
        val = str(item.get(key) or "").strip().lower()
        if val in _PRIVATE_SCOPES:
            return True
    if item.get("is_private") is True or item.get("private") is True:
        return True
    for key in ("kind", "memory_type", "type", "owner"):
        val = str(item.get(key) or "").strip().lower()
        if val in {"private_memory", "persona_private", "persona_private_memory", "memory:persona_private"}:
            return True
    return False


def _strip_private_fields(obj: Any) -> Any:
    if isinstance(obj, Mapping):
        if _is_private_memory_item(obj):
            return None
        cleaned: Dict[str, Any] = {}
        for k, v in obj.items():
            if str(k) in _PRIVATE_FIELD_KEYS:
                continue
            stripped = _strip_private_fields(v)
            if stripped is not None or v is None:
                cleaned[str(k)] = stripped
        return cleaned
    if isinstance(obj, list):
        out = []
        for elem in obj:
            if isinstance(elem, Mapping) and _is_private_memory_item(elem):
                continue
            stripped = _strip_private_fields(elem)
            if stripped is not None or elem is None:
                out.append(stripped)
        return out
    return obj


def _is_agora_or_trader_record(item: Mapping[str, Any]) -> bool:
    if str(item.get("source_owner") or "").strip().lower() == "agora":
        return True
    if str(item.get("origin") or "").strip().lower() in {"agora", "agora_advisor", "private_servant"}:
        return True
    if str(item.get("provenance") or "").strip().lower() == "agora":
        return True
    if str(item.get("kind") or "").strip().lower().startswith("agora"):
        return True
    if str(item.get("subject_kind") or "").strip().lower() == "trader":
        return True
    if any(k in item for k in ("agora_user_id", "agoraUserId", "trader_id", "traderId", "account_id", "broker_ids", "raw_text")):
        return True
    return False


def _extract_trader_id(item: Mapping[str, Any], default_trader_id: Optional[str] = None) -> Optional[str]:
    for key in ("agora_user_id", "agoraUserId", "trader_id", "traderId", "user_id", "userId"):
        val = str(item.get(key) or "").strip()
        if val:
            return val
    if str(item.get("subject_kind") or "").strip().lower() in {"trader", "agora_user"}:
        val = str(item.get("subject_id") or "").strip()
        if val:
            return val
    subject_ref = item.get("subject_ref")
    if isinstance(subject_ref, Mapping):
        if str(subject_ref.get("kind") or "").strip().lower() in {"trader", "agora_user"}:
            val = str(
                subject_ref.get("id")
                or subject_ref.get("agora_user_id")
                or subject_ref.get("trader_id")
                or ""
            ).strip()
            if val:
                return val
    convened_by = item.get("convened_by")
    if isinstance(convened_by, Mapping):
        if str(convened_by.get("kind") or "").strip().lower() in {"trader", "agora_user"}:
            val = str(
                convened_by.get("id")
                or convened_by.get("agora_user_id")
                or convened_by.get("trader_id")
                or ""
            ).strip()
            if val:
                return val
    return default_trader_id


def _can_view_trader(
    trader_id: Optional[str],
    *,
    is_operator: bool,
    caller_user_id: Optional[str],
) -> bool:
    if is_operator:
        return True
    if trader_id and caller_user_id and str(trader_id).strip() == str(caller_user_id).strip():
        return True
    return False


def _shape_participant(
    participant: Mapping[str, Any],
    *,
    is_operator: bool,
    caller_user_id: Optional[str],
    default_trader_id: Optional[str] = None,
) -> Dict[str, Any]:
    shaped = _strip_private_fields(dict(participant)) or {}
    origin = str(shaped.get("origin") or "institutional").strip()
    shaped["origin"] = origin
    shaped["servant"] = origin == "private_servant"
    trader_id = _extract_trader_id(shaped, default_trader_id=default_trader_id)
    if not _can_view_trader(trader_id, is_operator=is_operator, caller_user_id=caller_user_id):
        for k in list(shaped.keys()):
            if k in _TRADER_IDENTITY_KEYS:
                shaped.pop(k, None)
    return shaped


def _shape_convened_by(
    convened_by: Any,
    *,
    is_operator: bool,
    caller_user_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    if not isinstance(convened_by, Mapping):
        return None
    out = dict(convened_by)
    kind = str(out.get("kind") or "").strip().lower()
    if kind in {"agora_user", "trader"}:
        trader_id = str(out.get("id") or out.get("agora_user_id") or out.get("trader_id") or "").strip() or None
        if not _can_view_trader(trader_id, is_operator=is_operator, caller_user_id=caller_user_id):
            out.pop("id", None)
            for k in list(out.keys()):
                if k in _TRADER_IDENTITY_KEYS:
                    out.pop(k, None)
            out["redacted"] = True
    return out


def _shape_subject_ref(
    subject_ref: Any,
    *,
    is_operator: bool,
    caller_user_id: Optional[str],
) -> Any:
    if not isinstance(subject_ref, Mapping):
        return subject_ref
    out = dict(subject_ref)
    kind = str(out.get("kind") or out.get("subject_kind") or "").strip().lower()
    trader_id = _extract_trader_id(out)
    if kind in {"agora_user", "trader"} or trader_id:
        eff_trader = trader_id or str(out.get("id") or "").strip() or None
        if not _can_view_trader(eff_trader, is_operator=is_operator, caller_user_id=caller_user_id):
            out.pop("id", None)
            out.pop("subject_id", None)
            for k in list(out.keys()):
                if k in _TRADER_IDENTITY_KEYS or k == "raw_text":
                    out.pop(k, None)
            out["redacted"] = True
    return out


def _downstream_href(downstream: Mapping[str, Any], sponsor_persona_id: Optional[str] = None) -> Optional[str]:
    target = str(downstream.get("target") or "").strip()
    target_id = str(downstream.get("id") or "").strip()
    if target == "harness_patch" and sponsor_persona_id:
        return f"/management/personas/{quote(str(sponsor_persona_id), safe='')}"
    if target == "lesson_rule" and sponsor_persona_id:
        return f"/management/personas/{quote(str(sponsor_persona_id), safe='')}"
    if target == "evolution_proposal" and target_id:
        return f"/management/evolution/{quote(target_id, safe='')}"
    if target == "governance_approval" and target_id:
        return f"/management/approvals?id={quote(target_id, safe='')}"
    return _DOWNSTREAM_TARGET_HREFS.get(target)


def _shape_proposal_item(raw: Mapping[str, Any], default_meeting_id: Optional[str] = None) -> Dict[str, Any]:
    proposal_id = str(raw.get("proposal_id") or raw.get("id") or "").strip()
    meeting_id = str(raw.get("meeting_id") or default_meeting_id or "").strip() or None
    sponsor = raw.get("sponsor_persona_id")
    downstream_raw = raw.get("downstream") if isinstance(raw.get("downstream"), Mapping) else {}
    target = downstream_raw.get("target") or raw.get("downstream_target")
    ds_id = downstream_raw.get("id") or raw.get("downstream_id")
    ds_status = downstream_raw.get("status") or raw.get("status") or "pending"
    downstream: Dict[str, Any] = {
        "target": target,
        "id": ds_id,
        "status": ds_status,
    }
    href = downstream_raw.get("href") or _downstream_href(downstream, sponsor)
    if href:
        downstream["href"] = href
    shaped = _strip_private_fields(dict(raw)) or {}
    shaped.update({
        "id": proposal_id,
        "proposal_id": proposal_id,
        "meeting_id": meeting_id,
        "meeting_href": f"/management/meetings/{quote(meeting_id, safe='')}" if meeting_id else None,
        "kind": raw.get("kind"),
        "sponsor_persona_id": sponsor,
        "downstream": downstream,
    })
    return shaped


_POOL_EVOLUTION_TOPIC_RE = re.compile(
    r"^Pool evolution review for (?P<persona>persona-\S+?) \((?P<period>\d{4}-W\d{2})\)$"
)
_OPTIMISATION_TOPIC_RE = re.compile(
    r"^Optimisation review for (?P<persona>persona-\S+?): lessons:(?P<lessons>\d+)$"
)


def parse_topic_summary(topic: Any) -> Optional[Dict[str, Any]]:
    """Parse machine-generated topic templates; None when topic is free text."""
    text = str(topic or "").strip()
    m = _POOL_EVOLUTION_TOPIC_RE.match(text)
    if m:
        return {
            "kind": "pool_evolution_review",
            "subject_persona_id": m.group("persona"),
            "period": m.group("period"),
            "lesson_count": None,
        }
    m = _OPTIMISATION_TOPIC_RE.match(text)
    if m:
        return {
            "kind": "optimisation_review",
            "subject_persona_id": m.group("persona"),
            "period": None,
            "lesson_count": int(m.group("lessons")),
        }
    return None


def _execution_fields(raw: Mapping[str, Any]) -> Dict[str, Any]:
    es = raw.get("execution_state")
    es_out = dict(es) if isinstance(es, Mapping) else None
    effective = raw.get("effective_status") or raw.get("status")
    return {"execution_state": es_out, "effective_status": effective}


def _resolve_meeting_review_cause(raw: Mapping[str, Any]) -> Optional[str]:
    """Return the stored specific synthesis review cause when present, or 'unknown' on legacy REVIEW rows."""
    synth = raw.get("synthesis") if isinstance(raw.get("synthesis"), Mapping) else {}
    outcome = str(
        raw.get("outcome")
        or synth.get("outcome")
        or ""
    ).strip().upper()
    status = str(raw.get("status") or "").strip().upper()
    is_review = outcome == "REVIEW" or status == "REVIEW"
    for candidate in (
        synth.get("unreadable_reason"),
        raw.get("synthesis_unreadable_reason"),
        raw.get("unreadable_reason"),
        (raw.get("operator_review") or {}).get("cause")
        if isinstance(raw.get("operator_review"), Mapping)
        else None,
    ):
        val = str(candidate or "").strip()
        if val and val.lower() not in {"none", "null", "unknown"}:
            return val
    if is_review:
        return "unknown"
    return None


def _extract_item_ref_ids(item: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for k in ("item_id", "id", "source_id", "ref_id", "entry_id", "record_id"):
        v = str(item.get(k) or "").strip()
        if v and v not in out:
            out.append(v)
    return out


def _collect_meeting_withheld_and_visible_refs(
    raw: Mapping[str, Any],
    *,
    is_operator: bool = True,
    caller_user_id: Optional[str] = None,
) -> Tuple[List[str], set[str]]:
    """Return ``(evidence_refs, withheld_ids)`` after private and trader visibility filtering."""
    meeting_trader_id = _extract_trader_id(raw)

    def _is_withheld(item: Mapping[str, Any]) -> bool:
        if _is_private_memory_item(item):
            return True
        if _is_agora_or_trader_record(item):
            rec_trader = _extract_trader_id(item, default_trader_id=meeting_trader_id)
            if not _can_view_trader(rec_trader, is_operator=is_operator, caller_user_id=caller_user_id):
                return True
        return False

    visible_item_ids: set[str] = set()
    withheld_ids: set[str] = set()
    visible_case_refs: List[str] = []
    visible_citation_refs: List[str] = []
    withheld_citation_refs: set[str] = set()

    for item in raw.get("case_file_items") or []:
        if not isinstance(item, Mapping):
            continue
        ids = _extract_item_ref_ids(item)
        if _is_withheld(item):
            withheld_ids.update(ids)
        else:
            visible_item_ids.update(ids)
            primary = str(item.get("item_id") or item.get("id") or item.get("source_id") or "").strip()
            if primary:
                visible_case_refs.append(primary)

    raw_case_file = raw.get("case_file")
    if isinstance(raw_case_file, Mapping):
        cf_lists: List[Any] = [raw_case_file.get("items")]
        groups = raw_case_file.get("groups")
        if isinstance(groups, Mapping):
            for grp in groups.values():
                if isinstance(grp, Mapping):
                    cf_lists.append(grp.get("items"))
        for cf_items in cf_lists:
            if not isinstance(cf_items, list):
                continue
            for item in cf_items:
                if not isinstance(item, Mapping):
                    continue
                ids = _extract_item_ref_ids(item)
                if _is_withheld(item):
                    withheld_ids.update(ids)
                else:
                    visible_item_ids.update(ids)

    raw_pre_read = raw.get("pre_read")
    pre_read_entries: List[Mapping[str, Any]] = []
    if isinstance(raw_pre_read, list):
        pre_read_entries.extend(x for x in raw_pre_read if isinstance(x, Mapping))
    elif isinstance(raw_pre_read, Mapping):
        if _is_withheld(raw_pre_read):
            withheld_ids.update(_extract_item_ref_ids(raw_pre_read))
        for list_key in ("interpretations", "items"):
            sub = raw_pre_read.get(list_key)
            if isinstance(sub, list):
                pre_read_entries.extend(x for x in sub if isinstance(x, Mapping))
    for entry in pre_read_entries:
        ids = _extract_item_ref_ids(entry)
        if _is_withheld(entry):
            withheld_ids.update(ids)
            for cit in entry.get("citations") or []:
                if isinstance(cit, Mapping):
                    withheld_ids.update(_extract_item_ref_ids(cit))
                else:
                    s = str(cit or "").strip()
                    if s:
                        withheld_citation_refs.add(s)
        else:
            visible_item_ids.update(ids)

    attr_rec = raw.get("attribution_record")
    if isinstance(attr_rec, Mapping) and _is_withheld(attr_rec):
        withheld_ids.update(_extract_item_ref_ids(attr_rec))
        if raw.get("attribution_record_id"):
            withheld_ids.add(str(raw["attribution_record_id"]).strip())

    for rec in raw.get("agora_records") or []:
        if isinstance(rec, Mapping) and _is_withheld(rec):
            withheld_ids.update(_extract_item_ref_ids(rec))

    for rnd in raw.get("rounds") or []:
        if not isinstance(rnd, Mapping):
            continue
        rnd_withheld = _is_withheld(rnd)
        for pos in rnd.get("positions") or []:
            if not isinstance(pos, Mapping):
                continue
            if rnd_withheld or _is_withheld(pos):
                withheld_ids.update(_extract_item_ref_ids(pos))
                for cit in pos.get("citations") or []:
                    if isinstance(cit, Mapping):
                        withheld_ids.update(_extract_item_ref_ids(cit))
                    else:
                        s = str(cit or "").strip()
                        if s:
                            withheld_citation_refs.add(s)
                continue
            for cit in pos.get("citations") or []:
                if isinstance(cit, Mapping):
                    if _is_withheld(cit):
                        withheld_ids.update(_extract_item_ref_ids(cit))
                        continue
                    s = str(cit.get("item_id") or cit.get("id") or cit.get("source_id") or "").strip()
                else:
                    s = str(cit or "").strip()
                if s:
                    visible_citation_refs.append(s)

    for cit_ref in withheld_citation_refs:
        if cit_ref not in visible_item_ids and cit_ref not in visible_citation_refs:
            withheld_ids.add(cit_ref)

    refs: List[str] = []
    seen: set[str] = set()

    def _add(val: Any) -> None:
        if isinstance(val, Mapping):
            if _is_withheld(val):
                withheld_ids.update(_extract_item_ref_ids(val))
                return
            s = str(val.get("item_id") or val.get("id") or val.get("source_id") or val.get("ref_id") or "").strip()
        else:
            s = str(val or "").strip()
        if s and s not in withheld_ids and s not in seen:
            seen.add(s)
            refs.append(s)

    synth = raw.get("synthesis") if isinstance(raw.get("synthesis"), Mapping) else {}
    minutes = raw.get("minutes") if isinstance(raw.get("minutes"), Mapping) else {}
    memo_refs = [
        str(x).strip()
        for x in (
            synth.get("dissent_memo_id"),
            minutes.get("memo_id"),
            minutes.get("dissent_memo_id"),
        )
        if x and str(x).strip()
    ]
    memo_ref_set = set(memo_refs)

    for r in raw.get("evidence_refs") or []:
        if isinstance(r, str) and r.strip() in memo_ref_set:
            continue
        _add(r)
    for r in visible_case_refs:
        _add(r)
    for r in visible_citation_refs:
        _add(r)
    for r in memo_refs:
        _add(r)
    return refs, withheld_ids


def _collect_meeting_evidence_refs(
    raw: Mapping[str, Any],
    *,
    is_operator: bool = True,
    caller_user_id: Optional[str] = None,
) -> List[str]:
    """Collect deduplicated non-private, caller-visible evidence/citation refs from a meeting record."""
    refs, _ = _collect_meeting_withheld_and_visible_refs(
        raw,
        is_operator=is_operator,
        caller_user_id=caller_user_id,
    )
    return refs


def _normalize_operator_review(
    raw_review: Any,
    *,
    default_cause: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_review, Mapping):
        return None
    status = str(raw_review.get("status") or "").strip().lower()
    if status != "reviewed":
        return None
    note = str(raw_review.get("note") or raw_review.get("rationale") or "").strip()
    reviewed_by = str(raw_review.get("reviewed_by") or "").strip()
    reviewed_at = str(raw_review.get("reviewed_at") or "").strip()
    stored_cause = str(
        raw_review.get("cause")
        or raw_review.get("unreadable_reason")
        or ""
    ).strip()
    if stored_cause and stored_cause.lower() not in {"none", "null", "unknown"}:
        cause = stored_cause
    else:
        cause = str(default_cause or stored_cause or "unknown").strip() or "unknown"
    out: Dict[str, Any] = {
        "status": "reviewed",
        "disposition": str(raw_review.get("disposition") or "closed_no_proposal"),
        "note": note,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "cause": cause,
        "unreadable_reason": cause,
    }
    if raw_review.get("idempotency_key"):
        out["idempotency_key"] = str(raw_review["idempotency_key"])
    return out


def shape_meeting_list_item(
    raw: Mapping[str, Any],
    *,
    is_operator: bool,
    caller_user_id: Optional[str],
) -> Dict[str, Any]:
    meeting_id = str(raw.get("meeting_id") or raw.get("id") or "").strip()
    meeting_trader_id = _extract_trader_id(raw)
    review_cause = _resolve_meeting_review_cause(raw)
    operator_review = _normalize_operator_review(raw.get("operator_review"), default_cause=review_cause)
    evidence_refs = _collect_meeting_evidence_refs(
        raw,
        is_operator=is_operator,
        caller_user_id=caller_user_id,
    )
    participants_raw = raw.get("participants") if isinstance(raw.get("participants"), list) else []
    participants = [
        _shape_participant(
            p,
            is_operator=is_operator,
            caller_user_id=caller_user_id,
            default_trader_id=meeting_trader_id,
        )
        for p in participants_raw
        if isinstance(p, Mapping)
    ]
    shaped = _strip_private_fields(dict(raw)) or {}
    if not _can_view_trader(meeting_trader_id, is_operator=is_operator, caller_user_id=caller_user_id):
        for k in list(shaped.keys()):
            if k in _TRADER_IDENTITY_KEYS:
                shaped.pop(k, None)
    if "convened_by" in shaped:
        shaped["convened_by"] = _shape_convened_by(
            raw.get("convened_by"),
            is_operator=is_operator,
            caller_user_id=caller_user_id,
        )
    if "subject_ref" in shaped:
        shaped["subject_ref"] = _shape_subject_ref(
            raw.get("subject_ref"),
            is_operator=is_operator,
            caller_user_id=caller_user_id,
        )
    shaped.update({
        "id": meeting_id,
        "meeting_id": meeting_id,
        "meeting_type": raw.get("meeting_type") or raw.get("type"),
        "status": raw.get("status"),
        "outcome": raw.get("outcome"),
        "synthesis_unreadable_reason": review_cause,
        "unreadable_reason": review_cause,
        "operator_review": operator_review,
        "evidence_refs": evidence_refs,
        "chair_persona_id": raw.get("chair_persona_id"),
        "participants": participants,
        "participant_count": len(participants),
        "has_agora_participants": any(
            p.get("origin") in {"agora_advisor", "private_servant"} for p in participants
        ),
        "has_private_servant": any(p.get("origin") == "private_servant" for p in participants),
        "round": raw.get("round"),
        "created_at": raw.get("created_at"),
        "closed_at": raw.get("closed_at"),
        "topic_summary": parse_topic_summary(raw.get("topic")),
        **_execution_fields(raw),
        "href": f"/management/meetings/{quote(meeting_id, safe='')}" if meeting_id else None,
    })
    return shaped


def shape_meeting_list(
    owner_payload: Mapping[str, Any],
    *,
    is_operator: bool,
    caller_user_id: Optional[str],
    snapshot_at: str,
    limit: int = 50,
) -> Dict[str, Any]:
    owner_summary = owner_payload.get("summary") if isinstance(owner_payload.get("summary"), Mapping) else {}
    try:
        dead_letter_count = int(owner_summary.get("dead_letter_count") or 0)
    except (TypeError, ValueError):
        dead_letter_count = 0
    raw_items = owner_payload.get("items") if isinstance(owner_payload.get("items"), list) else []
    next_cursor = owner_payload.get("next_cursor")
    items = [
        shape_meeting_list_item(item, is_operator=is_operator, caller_user_id=caller_user_id)
        for item in raw_items
        if isinstance(item, Mapping)
    ]
    open_count = sum(
        1
        for m in items
        if str(m.get("effective_status") or m.get("status") or "").strip().lower()
        not in {"closed", "completed", "refused", "cancelled", "canceled", "execution_failed"}
    )
    closed_count = sum(
        1
        for m in items
        if str(m.get("status") or "").strip().lower() in {"closed", "completed"}
    )
    review_count = sum(
        1
        for m in items
        if str(m.get("outcome") or "").strip().upper() == "REVIEW"
        or str(m.get("status") or "").strip().upper() == "REVIEW"
    )
    return {
        "status": "ok",
        "owner": "consultation",
        "reason": None,
        "empty": len(items) == 0,
        "fallback_used": False,
        "items": items,
        "next_cursor": next_cursor,
        "data": {
            "id": "management-meetings",
            "status": "ok",
            "source": "consultation",
            "empty": len(items) == 0,
            "items": items,
            "next_cursor": next_cursor,
            "summary": {
                "total_items": len(items),
                "returned_items": len(items),
                "open_count": open_count,
                "closed_count": closed_count,
                "review_count": review_count,
                "dead_letter_count": dead_letter_count,
                "status": "ok",
                "source": "consultation",
            },
        },
        "page_info": {
            "next_page_token": next_cursor,
            "next_cursor": next_cursor,
            "total": len(items),
            "page_size": limit,
            "returned": len(items),
            "has_more": next_cursor is not None,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "status": "ok",
            "source": "consultation",
            "surfaces": {
                "meetings": {
                    "status": "ok",
                    "source": "consultation",
                    "snapshot_at": snapshot_at,
                }
            },
        },
    }


def _visible_action_data(
    value: Any, *, tenant_id: Optional[str], is_operator: bool,
    caller_user_id: Optional[str], withheld_ids: Optional[set[str]] = None,
) -> Any:
    """Apply the meeting visibility boundary to new owner action subtrees too."""
    if isinstance(value, str) and value.strip() in (withheld_ids or set()):
        return None
    if isinstance(value, Mapping):
        # A withheld reference's payload is private too; dropping only its ID
        # would leave excerpts or metadata behind as an anonymous citation.
        if withheld_ids and withheld_ids.intersection(_extract_item_ref_ids(value)):
            return None
        if _is_private_memory_item(value):
            return None
        if value.get("tenant_id") and tenant_id and value["tenant_id"] != tenant_id:
            return None
        kind = str(value.get("kind") or value.get("subject_kind") or "").strip().lower()
        if kind in {"trader", "agora_user"}:
            # Typed refs use the ordinary subject-ref redaction semantics,
            # including when nested in action evidence or synthesis fallback.
            value = _shape_subject_ref(value, is_operator=is_operator, caller_user_id=caller_user_id)
        elif _is_agora_or_trader_record(value) and not _can_view_trader(
            _extract_trader_id(value), is_operator=is_operator, caller_user_id=caller_user_id
        ):
            return None
        return {str(k): cleaned for k, v in value.items() if str(k) not in _PRIVATE_FIELD_KEYS
                and ((cleaned := _visible_action_data(v, tenant_id=tenant_id,
                    is_operator=is_operator, caller_user_id=caller_user_id, withheld_ids=withheld_ids)) is not None or v is None)}
    if isinstance(value, list):
        return [cleaned for v in value if (cleaned := _visible_action_data(v,
            tenant_id=tenant_id, is_operator=is_operator, caller_user_id=caller_user_id, withheld_ids=withheld_ids)) is not None]
    return value


def shape_meeting_detail(
    raw: Mapping[str, Any],
    *,
    is_operator: bool,
    caller_user_id: Optional[str],
    snapshot_at: str,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    meeting_id = str(raw.get("meeting_id") or raw.get("id") or "").strip()
    meeting_trader_id = _extract_trader_id(raw)

    # 1. Participants
    participants_raw = raw.get("participants") if isinstance(raw.get("participants"), list) else []
    participants = [
        _shape_participant(
            p,
            is_operator=is_operator,
            caller_user_id=caller_user_id,
            default_trader_id=meeting_trader_id,
        )
        for p in participants_raw
        if isinstance(p, Mapping)
    ]

    # 2. Case file items: never return private memory; show Agora/trader records only to operators or owning trader
    raw_case_items = raw.get("case_file_items") if isinstance(raw.get("case_file_items"), list) else []
    case_file_items: List[Dict[str, Any]] = []
    private_items_withheld = 0
    for item in raw_case_items:
        if not isinstance(item, Mapping):
            continue
        if _is_private_memory_item(item):
            private_items_withheld += 1
            continue
        if _is_agora_or_trader_record(item):
            rec_trader = _extract_trader_id(item, default_trader_id=meeting_trader_id)
            if not _can_view_trader(rec_trader, is_operator=is_operator, caller_user_id=caller_user_id):
                continue
        cleaned_item = _strip_private_fields(dict(item))
        if cleaned_item is not None:
            case_file_items.append(cleaned_item)

    # 3. Pre-read: never return private memory; filter Agora/trader records for non-owning non-operators
    raw_pre_read = raw.get("pre_read")
    pre_read: Any
    if isinstance(raw_pre_read, list):
        pre_read_list: List[Any] = []
        for entry in raw_pre_read:
            if isinstance(entry, Mapping):
                if _is_private_memory_item(entry):
                    private_items_withheld += 1
                    continue
                if _is_agora_or_trader_record(entry):
                    rec_trader = _extract_trader_id(entry, default_trader_id=meeting_trader_id)
                    if not _can_view_trader(rec_trader, is_operator=is_operator, caller_user_id=caller_user_id):
                        continue
                cleaned_entry = _strip_private_fields(dict(entry))
                if cleaned_entry is not None:
                    pre_read_list.append(cleaned_entry)
        pre_read = pre_read_list
    elif isinstance(raw_pre_read, Mapping):
        if _is_private_memory_item(raw_pre_read):
            private_items_withheld += 1
            pre_read = {}
        else:
            for list_key in ("interpretations", "items"):
                raw_sub = raw_pre_read.get(list_key)
                if isinstance(raw_sub, list):
                    private_items_withheld += sum(
                        1 for x in raw_sub if isinstance(x, Mapping) and _is_private_memory_item(x)
                    )
            pre_read = _strip_private_fields(dict(raw_pre_read)) or {}
            if isinstance(pre_read.get("interpretations"), list):
                filtered_interps = []
                for interp in pre_read["interpretations"]:
                    if isinstance(interp, Mapping) and _is_agora_or_trader_record(interp):
                        rec_trader = _extract_trader_id(interp, default_trader_id=meeting_trader_id)
                        if not _can_view_trader(rec_trader, is_operator=is_operator, caller_user_id=caller_user_id):
                            continue
                    filtered_interps.append(interp)
                pre_read["interpretations"] = filtered_interps
            if isinstance(pre_read.get("items"), list):
                filtered_pr_items = []
                for pr_item in pre_read["items"]:
                    if isinstance(pr_item, Mapping) and _is_agora_or_trader_record(pr_item):
                        rec_trader = _extract_trader_id(pr_item, default_trader_id=meeting_trader_id)
                        if not _can_view_trader(rec_trader, is_operator=is_operator, caller_user_id=caller_user_id):
                            continue
                    filtered_pr_items.append(pr_item)
                pre_read["items"] = filtered_pr_items
    else:
        pre_read = raw_pre_read

    # 4. Rounds & anonymised labels reveal (only to operators)
    evidence_refs, withheld_ids = _collect_meeting_withheld_and_visible_refs(
        raw,
        is_operator=is_operator,
        caller_user_id=caller_user_id,
    )
    raw_rounds = raw.get("rounds") if isinstance(raw.get("rounds"), list) else []
    rounds: List[Dict[str, Any]] = []
    label_reveal: Dict[str, str] = {}
    for r in raw_rounds:
        if not isinstance(r, Mapping):
            continue
        round_no = r.get("round")
        raw_positions = r.get("positions") if isinstance(r.get("positions"), list) else []
        positions: List[Dict[str, Any]] = []
        for pos in raw_positions:
            if not isinstance(pos, Mapping):
                continue
            if _is_private_memory_item(pos):
                private_items_withheld += 1
                continue
            p_copy = _strip_private_fields(dict(pos)) or {}
            if isinstance(p_copy.get("citations"), list):
                p_copy["citations"] = [
                    c
                    for c in p_copy["citations"]
                    if not (
                        (isinstance(c, str) and c.strip() in withheld_ids)
                        or (
                            isinstance(c, Mapping)
                            and any(cid in withheld_ids for cid in _extract_item_ref_ids(c))
                        )
                    )
                ]
            label = p_copy.get("label")
            persona_id = p_copy.get("persona_id")
            if is_operator:
                if label and persona_id:
                    label_reveal[str(label)] = str(persona_id)
                if persona_id:
                    p_copy["revealed_persona_id"] = persona_id
            else:
                p_copy.pop("persona_id", None)
                p_copy.pop("revealed_persona_id", None)
                pos_trader = _extract_trader_id(p_copy, default_trader_id=meeting_trader_id)
                if not _can_view_trader(pos_trader, is_operator=is_operator, caller_user_id=caller_user_id):
                    for k in list(p_copy.keys()):
                        if k in _TRADER_IDENTITY_KEYS:
                            p_copy.pop(k, None)
            positions.append(p_copy)
        r_copy = _strip_private_fields({k: v for k, v in r.items() if k != "positions"}) or {}
        r_copy["round"] = round_no
        r_copy["positions"] = positions
        rounds.append(r_copy)

    # 5. Synthesis & proposals
    raw_synthesis = raw.get("synthesis") if isinstance(raw.get("synthesis"), Mapping) else {}
    raw_proposals = raw_synthesis.get("proposals") if isinstance(raw_synthesis.get("proposals"), list) else []
    shaped_proposals = [
        _shape_proposal_item(p, default_meeting_id=meeting_id)
        for p in raw_proposals
        if isinstance(p, Mapping)
    ]
    review_cause = _resolve_meeting_review_cause(raw)
    operator_review = _normalize_operator_review(raw.get("operator_review"), default_cause=review_cause)
    synthesis: Dict[str, Any] = _strip_private_fields(dict(raw_synthesis)) or {}
    synthesis.update({
        "proposals": shaped_proposals,
        "tally": raw_synthesis.get("tally"),
        "dissent_memo_id": raw_synthesis.get("dissent_memo_id"),
        "outcome": raw_synthesis.get("outcome") or raw.get("outcome"),
    })
    if review_cause is not None:
        synthesis["unreadable_reason"] = review_cause
        synthesis["cause"] = review_cause

    action_fields: Dict[str, Any] = {}
    for key in ("actions", "prior_actions", "participant_capabilities", "prior_action_reviews", "series_subject_ref", "action_read", "action_page_info"):
        value = raw.get(key)
        # Explicit null still uses the synthesis fallback; every returned
        # action subtree must pass the same viewer visibility filter.
        if value is None:
            value = raw_synthesis.get(key)
        if value is not None:
            action_fields[key] = _visible_action_data(value, tenant_id=tenant_id or raw.get("tenant_id"),
                is_operator=is_operator, caller_user_id=caller_user_id, withheld_ids=withheld_ids)
            if key in synthesis:
                synthesis[key] = action_fields[key]
    action_fields.setdefault("actions", [])
    action_fields.setdefault("prior_actions", [])
    action_fields.setdefault("participant_capabilities", [])
    has_actions = bool(action_fields["actions"] or action_fields["prior_actions"])
    action_fields.setdefault("action_read", {
        "status": "unavailable" if has_actions else "not_recorded", "source": "consultation",
        "reason": "live_action_owner_status_not_recorded" if has_actions else "meeting_actions_not_recorded",
    })

    # 6. Writebacks: never expose private memory text, only ids/status
    raw_writebacks = raw.get("writebacks") if isinstance(raw.get("writebacks"), list) else []
    writebacks: List[Dict[str, Any]] = []
    for wb in raw_writebacks:
        if not isinstance(wb, Mapping):
            continue
        wb_clean = {k: v for k, v in wb.items() if k in _WRITEBACK_ALLOWED_KEYS}
        writebacks.append(wb_clean)

    # 7. Attribution record / Agora records on MT-4 or other meetings
    attribution_record = raw.get("attribution_record")
    if isinstance(attribution_record, Mapping):
        attr_trader = _extract_trader_id(attribution_record, default_trader_id=meeting_trader_id)
        if _is_agora_or_trader_record(attribution_record) and not _can_view_trader(
            attr_trader, is_operator=is_operator, caller_user_id=caller_user_id
        ):
            attribution_record = None
        else:
            attribution_record = _strip_private_fields(dict(attribution_record))

    agora_records_raw = raw.get("agora_records")
    agora_records: Optional[List[Dict[str, Any]]] = None
    if isinstance(agora_records_raw, list):
        agora_records = []
        for rec in agora_records_raw:
            if not isinstance(rec, Mapping):
                continue
            if _is_private_memory_item(rec):
                private_items_withheld += 1
                continue
            rec_trader = _extract_trader_id(rec, default_trader_id=meeting_trader_id)
            if _can_view_trader(rec_trader, is_operator=is_operator, caller_user_id=caller_user_id):
                cleaned_rec = _strip_private_fields(dict(rec))
                if cleaned_rec is not None:
                    agora_records.append(cleaned_rec)

    detail: Dict[str, Any] = {
        "id": meeting_id,
        "meeting_id": meeting_id,
        "meeting_type": raw.get("meeting_type") or raw.get("type"),
        "topic": raw.get("topic"),
        "topic_summary": parse_topic_summary(raw.get("topic")),
        "status": raw.get("status"),
        **_execution_fields(raw),
        "outcome": raw.get("outcome") or synthesis.get("outcome"),
        "synthesis_unreadable_reason": review_cause,
        "unreadable_reason": review_cause,
        "operator_review": operator_review,
        "evidence_refs": evidence_refs,
        "chair_persona_id": raw.get("chair_persona_id"),
        "convened_by": _shape_convened_by(
            raw.get("convened_by"),
            is_operator=is_operator,
            caller_user_id=caller_user_id,
        ),
        "subject_ref": _shape_subject_ref(
            raw.get("subject_ref"),
            is_operator=is_operator,
            caller_user_id=caller_user_id,
        ),
        "attribution_record_id": raw.get("attribution_record_id"),
        "attribution_record": attribution_record,
        "participants": participants,
        "agenda": _strip_private_fields(raw.get("agenda")),
        "case_file_items": case_file_items,
        "private_items_withheld": private_items_withheld,
        "pre_read": pre_read,
        "rounds": rounds,
        "label_reveal": label_reveal if is_operator else None,
        "anonymised_labels_revealed": bool(is_operator),
        "synthesis": synthesis,
        **action_fields,
        "proposals": shaped_proposals,
        "writebacks": writebacks,
        "usage": _strip_private_fields(raw.get("usage")),
        "created_at": raw.get("created_at"),
        "closed_at": raw.get("closed_at"),
    }
    if agora_records is not None:
        detail["agora_records"] = agora_records
    if _can_view_trader(meeting_trader_id, is_operator=is_operator, caller_user_id=caller_user_id):
        for k in ("agora_user_id", "trader_id", "account_id", "broker_ids"):
            if k in raw:
                detail[k] = raw[k]

    return {
        "status": "ok",
        "owner": "consultation",
        "reason": None,
        "fallback_used": False,
        **detail,
        "data": {
            "status": "ok",
            "source": "consultation",
            **detail,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "status": "ok",
            "source": "consultation",
            "surfaces": {
                "meeting_detail": {
                    "status": "ok",
                    "source": "consultation",
                    "snapshot_at": snapshot_at,
                }
            },
        },
    }


def shape_meeting_proposals(
    owner_payload: Mapping[str, Any],
    *,
    snapshot_at: str,
) -> Dict[str, Any]:
    raw_items = owner_payload.get("items") if isinstance(owner_payload.get("items"), list) else []
    items = [
        _shape_proposal_item(item)
        for item in raw_items
        if isinstance(item, Mapping)
    ]
    return {
        "status": "ok",
        "owner": "consultation",
        "reason": None,
        "empty": len(items) == 0,
        "fallback_used": False,
        "items": items,
        "data": {
            "id": "management-meeting-proposals",
            "status": "ok",
            "source": "consultation",
            "empty": len(items) == 0,
            "items": items,
            "summary": {
                "total_items": len(items),
                "returned_items": len(items),
                "status": "ok",
                "source": "consultation",
            },
        },
        "page_info": {
            "next_page_token": None,
            "total": len(items),
            "page_size": len(items),
            "returned": len(items),
            "has_more": False,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "status": "ok",
            "source": "consultation",
            "surfaces": {
                "meeting_proposals": {
                    "status": "ok",
                    "source": "consultation",
                    "snapshot_at": snapshot_at,
                }
            },
        },
    }


def shape_meeting_catalogue(
    owner_payload: Mapping[str, Any],
    *,
    snapshot_at: str,
    persona_id: Optional[str] = None,
) -> Dict[str, Any]:
    raw_types = owner_payload.get("types") if isinstance(owner_payload.get("types"), list) else []
    raw_schedule = owner_payload.get("schedule") if isinstance(owner_payload.get("schedule"), list) else []
    raw_refusals = owner_payload.get("refusals") if isinstance(owner_payload.get("refusals"), list) else []

    types = [_strip_private_fields(dict(t)) for t in raw_types if isinstance(t, Mapping)]
    schedule = [
        _strip_private_fields(dict(s))
        for s in raw_schedule
        if isinstance(s, Mapping)
        and (not persona_id or str(s.get("persona_id") or "") == persona_id)
    ]
    refusals = [
        _strip_private_fields(dict(r))
        for r in raw_refusals
        if isinstance(r, Mapping)
        and (not persona_id or str(r.get("persona_id") or "") == persona_id)
    ]

    return {
        "status": "ok",
        "owner": "consultation",
        "reason": None,
        "empty": len(types) == 0 and len(schedule) == 0 and len(refusals) == 0,
        "fallback_used": False,
        "types": types,
        "schedule": schedule,
        "refusals": refusals,
        "data": {
            "id": "management-meeting-catalogue",
            "status": "ok",
            "source": "consultation",
            "types": types,
            "schedule": schedule,
            "refusals": refusals,
            "summary": {
                "type_count": len(types),
                "schedule_count": len(schedule),
                "refusal_count": len(refusals),
                "insufficient_participants_count": sum(
                    1
                    for r in refusals
                    if isinstance(r, Mapping) and str(r.get("reason") or "") == "insufficient_participants"
                ),
            },
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "status": "ok",
            "source": "consultation",
            "surfaces": {
                "meeting_catalogue": {
                    "status": "ok",
                    "source": "consultation",
                    "snapshot_at": snapshot_at,
                }
            },
        },
    }


def unavailable_meetings_envelope(
    surface_key: str,
    reason: str,
    *,
    snapshot_at: str,
) -> Dict[str, Any]:
    """Explicit unavailable state when the consultation owner is unconfigured or unreachable."""
    surface = {
        "status": "unavailable",
        "source": "consultation",
        "reason": reason,
        "message": f"Consultation meetings owner is unavailable ({reason}).",
        "snapshot_at": snapshot_at,
        "staleness": {
            "served_from": "unverifiable",
            "last_known_at": snapshot_at,
        },
    }
    data: Dict[str, Any] = {
        "id": surface_key.replace("_", "-"),
        "status": "unavailable",
        "source": "consultation",
        "reason": reason,
        "empty": False,
        "items": [],
    }
    if surface_key == "meeting_catalogue":
        data["types"] = []
        data["schedule"] = []
        data["refusals"] = []
    elif surface_key == "meeting_detail":
        data["meeting"] = None

    return {
        "status": "unavailable",
        "owner": "consultation",
        "reason": reason,
        "empty": False,
        "fallback_used": False,
        "items": [],
        **({"types": [], "schedule": [], "refusals": []} if surface_key == "meeting_catalogue" else {}),
        "data": data,
        "page_info": {
            "next_page_token": None,
            "next_cursor": None,
            "total": None,
            "page_size": 0,
            "returned": 0,
            "has_more": False,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "status": "unavailable",
            "source": "consultation",
            "reason": reason,
            "surfaces": {
                surface_key: surface,
            },
            "degradation": {
                "reason": reason,
            },
        },
    }


class MeetingsOwnerClient:
    """Thin BFF adapter forwarding meetings reads and operator convening to Consultation."""

    def __init__(
        self,
        tenant_id: str,
        actor_id: str,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        budget = remaining_management_read_budget(timeout_seconds or 5.0)
        eff_timeout = float(budget if budget is not None else (timeout_seconds or 5.0))
        self.tenant_id = str(tenant_id or "").strip()
        self.actor_id = str(actor_id or "operator").strip()
        self.client = ConsultationServiceClient(
            base_url=base_url,
            timeout_seconds=max(0.05, eff_timeout),
            token=token,
            service_actor=f"bff:{self.actor_id}",
            tenant_id=self.tenant_id,
        )

    def _ensure_ready(self) -> None:
        rem = remaining_management_read_budget()
        if rem is not None and rem <= 0.0:
            raise MeetingsOwnerUnavailableError("read_timeout", status_code=504)
        if rem is not None:
            self.client._timeout_seconds = min(self.client._timeout_seconds, max(0.01, rem))
        if not self.client.is_configured():
            raise MeetingsOwnerUnavailableError("consultation_owner_unconfigured")
        if not self.client._token and os.getenv("CONSULTATION_AUTH_REQUIRED", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }:
            raise MeetingsOwnerUnavailableError("consultation_service_credential_unconfigured")

    def _call(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        self._ensure_ready()
        if not extra_headers:
            try:
                return self.client._request_json(
                    method,
                    path,
                    payload,
                    tenant_id=self.tenant_id,
                    service_actor=f"bff:{self.actor_id}",
                )
            except ConsultationClientError as exc:
                self._raise_mapped_error(exc, path=path)

        # Custom headers path (e.g. Idempotency-Key on POST /api/consult/meetings)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers: Dict[str, str] = {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(
            f"{self.client._base_url}/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method,
        )
        if self.tenant_id:
            request.add_unredirected_header("X-Pantheon-Tenant-Id", self.tenant_id)
        if self.client._token:
            request.add_unredirected_header("Authorization", f"Bearer {self.client._token}")
        request.add_unredirected_header("X-Pantheon-Service-Actor", f"bff:{self.actor_id}")
        try:
            with urllib.request.urlopen(request, timeout=self.client._timeout_seconds) as response:
                text = response.read().decode("utf-8").strip()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                error_payload = {}
            detail = error_payload.get("detail") if isinstance(error_payload, dict) else None
            message = detail if isinstance(detail, str) else f"consultation API returned HTTP {exc.code}"
            c_err = ConsultationClientError(
                message,
                status_code=exc.code,
                payload=error_payload if isinstance(error_payload, dict) else {},
            )
            self._raise_mapped_error(c_err, path=path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise MeetingsOwnerUnavailableError("consultation_owner_unavailable") from exc

    def _raise_mapped_error(self, exc: ConsultationClientError, *, path: str) -> None:
        if exc.status_code == 404:
            clean_path = path.rstrip("/")
            for action_suffix in ("/review", "/evidence", "/sync"):
                if clean_path.endswith(action_suffix):
                    clean_path = clean_path[: -len(action_suffix)]
                    break
            meeting_id = clean_path.rsplit("/", 1)[-1]
            raise MeetingsOwnerNotFoundError(meeting_id) from exc
        if exc.status_code in {400, 409, 422} or (
            exc.status_code == 403 and isinstance(exc.payload, Mapping) and exc.payload.get("reason")
        ):
            reason = str(
                (exc.payload or {}).get("reason")
                or (exc.payload or {}).get("detail")
                or exc
            )
            raise MeetingsOwnerValidationError(
                reason,
                status_code=int(exc.status_code),
                payload=exc.payload,
            ) from exc
        reason = (
            f"consultation_owner_http_{exc.status_code}"
            if exc.status_code
            else "consultation_owner_unavailable"
        )
        raise MeetingsOwnerUnavailableError(
            reason,
            status_code=exc.status_code if exc.status_code in {401, 403} else 503,
            payload=exc.payload,
        ) from exc

    def list_meetings(
        self,
        *,
        status: Optional[str] = None,
        meeting_type: Optional[str] = None,
        persona_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        include_failed: bool = False,
    ) -> Dict[str, Any]:
        query: Dict[str, str] = {}
        if include_failed:
            query["include_failed"] = "true"
        if status:
            query["status"] = status
        if meeting_type:
            query["type"] = meeting_type
        if persona_id:
            query["persona_id"] = persona_id
        if cursor:
            query["cursor"] = cursor
        if limit is not None:
            query["limit"] = str(limit)
        raw = self._call("GET", ConsultationServiceClient._with_query("/api/consult/meetings", query))
        if isinstance(raw, list):
            items = raw
            next_cursor = None
        elif isinstance(raw, Mapping) and isinstance(raw.get("items"), list):
            items = raw["items"]
            next_cursor = raw.get("next_cursor")
            summary = raw.get("summary") if isinstance(raw.get("summary"), Mapping) else None
        else:
            raise MeetingsOwnerUnavailableError("invalid_meetings_list_response")
        if isinstance(raw, list):
            summary = None

        for item in items:
            if not isinstance(item, Mapping) or not (item.get("meeting_id") or item.get("id")):
                raise MeetingsOwnerUnavailableError("invalid_meetings_list_response")
            item_tenant = item.get("tenant_id")
            if item_tenant and self.tenant_id and str(item_tenant) != self.tenant_id:
                raise MeetingsOwnerUnavailableError("meeting_tenant_mismatch")
        result: Dict[str, Any] = {"items": items, "next_cursor": next_cursor}
        if summary is not None:
            result["summary"] = dict(summary)
        return result

    def get_meeting(self, meeting_id: str) -> Dict[str, Any]:
        clean_id = str(meeting_id or "").strip()
        if not clean_id:
            raise MeetingsOwnerNotFoundError("")
        raw = self._call("GET", f"/api/consult/meetings/{quote(clean_id, safe='')}")
        if not isinstance(raw, Mapping):
            raise MeetingsOwnerUnavailableError("invalid_meeting_detail_response")
        returned_id = str(raw.get("meeting_id") or raw.get("id") or "").strip()
        if returned_id != clean_id:
            raise MeetingsOwnerUnavailableError("invalid_meeting_detail_response")
        item_tenant = raw.get("tenant_id")
        if item_tenant and self.tenant_id and str(item_tenant) != self.tenant_id:
            raise MeetingsOwnerNotFoundError(clean_id)
        return dict(raw)

    def list_proposals(
        self,
        *,
        status: Optional[str] = None,
        persona_id: Optional[str] = None,
        meeting_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        query: Dict[str, str] = {}
        if status:
            query["status"] = status
        if persona_id:
            query["persona_id"] = persona_id
        if meeting_id:
            query["meeting_id"] = meeting_id
        raw = self._call(
            "GET",
            ConsultationServiceClient._with_query("/api/consult/meetings/proposals", query),
        )
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, Mapping) and isinstance(raw.get("items"), list):
            items = raw["items"]
        else:
            raise MeetingsOwnerUnavailableError("invalid_meeting_proposals_response")
        for item in items:
            if not isinstance(item, Mapping) or not (item.get("proposal_id") or item.get("id")):
                raise MeetingsOwnerUnavailableError("invalid_meeting_proposals_response")
        return {"items": items}

    def get_catalogue(self) -> Dict[str, Any]:
        raw = self._call("GET", "/api/consult/meetings/catalogue")
        if (
            not isinstance(raw, Mapping)
            or not isinstance(raw.get("types"), list)
            or not isinstance(raw.get("schedule"), list)
            or not isinstance(raw.get("refusals"), list)
        ):
            raise MeetingsOwnerUnavailableError("invalid_meeting_catalogue_response")
        return {
            "types": raw["types"],
            "schedule": raw["schedule"],
            "refusals": raw["refusals"],
        }

    def convene_meeting(
        self,
        *,
        meeting_type: str,
        topic: str,
        convened_by: Dict[str, Any],
        idempotency_key: str,
        subject_ref: Optional[Any] = None,
        participants: Optional[List[str]] = None,
        evidence_refs: Optional[List[Any]] = None,
        attribution_record_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "meeting_type": meeting_type,
            "topic": topic,
            "convened_by": convened_by,
        }
        if subject_ref is not None:
            payload["subject_ref"] = subject_ref
        if participants is not None:
            payload["participants"] = list(participants)
        if evidence_refs is not None:
            payload["evidence_refs"] = list(evidence_refs)
        if attribution_record_id is not None:
            payload["attribution_record_id"] = attribution_record_id

        raw = self._call(
            "POST",
            "/api/consult/meetings",
            payload,
            extra_headers={"Idempotency-Key": idempotency_key},
        )
        if not isinstance(raw, Mapping) or not raw.get("meeting_id"):
            raise MeetingsOwnerUnavailableError("invalid_meeting_convene_response")
        return dict(raw)

    def review_meeting(
        self,
        *,
        meeting_id: str,
        note: str,
        reviewed_by: str,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        clean_id = str(meeting_id or "").strip()
        if not clean_id:
            raise MeetingsOwnerNotFoundError("")
        payload: Dict[str, Any] = {
            "note": note,
            "reviewed_by": reviewed_by,
        }
        extra_headers: Optional[Dict[str, str]] = None
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
            extra_headers = {"Idempotency-Key": idempotency_key}
        raw = self._call(
            "POST",
            f"/api/consult/meetings/{quote(clean_id, safe='')}/review",
            payload,
            extra_headers=extra_headers,
        )
        if not isinstance(raw, Mapping) or not isinstance(raw.get("operator_review"), Mapping):
            raise MeetingsOwnerUnavailableError("invalid_meeting_review_response")
        returned_id = str(raw.get("meeting_id") or raw.get("id") or "").strip()
        if returned_id != clean_id:
            raise MeetingsOwnerUnavailableError("invalid_meeting_review_response")
        item_tenant = raw.get("tenant_id")
        if item_tenant and self.tenant_id and str(item_tenant) != self.tenant_id:
            raise MeetingsOwnerNotFoundError(clean_id)
        if str(raw["operator_review"].get("status") or "").strip().lower() != "reviewed":
            raise MeetingsOwnerUnavailableError("invalid_meeting_review_response")
        return dict(raw)


def _proposal_waiting_for_operator(prop: Mapping[str, Any]) -> bool:
    if prop.get("waiting_for_operator") is True or prop.get("requires_operator") is True:
        return True
    downstream = prop.get("downstream") if isinstance(prop.get("downstream"), Mapping) else {}
    ds_status = str(
        downstream.get("status")
        or prop.get("status")
        or ""
    ).strip().lower()
    if ds_status in _TERMINAL_PROPOSAL_STATUSES:
        return False
    if ds_status in _OPERATOR_WAITING_PROPOSAL_STATUSES:
        return True
    target = str(downstream.get("target") or prop.get("kind") or "").strip().lower()
    if target in {"governance_approval", "agora_candidate_decision"} and ds_status not in _TERMINAL_PROPOSAL_STATUSES:
        return True
    return False


def project_human_inbox_meeting_items(
    *,
    snapshot_at: str,
    meetings: Sequence[Mapping[str, Any]] = (),
    proposals: Sequence[Mapping[str, Any]] = (),
    catalogue: Optional[Mapping[str, Any]] = None,
    include_reviewed: bool = False,
    is_operator: bool = True,
    caller_user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Project human-actionable meeting items into the Management Human Inbox shape.

    Includes:
    1. Meetings ending in REVIEW (`outcome == "REVIEW"` or `status == "REVIEW"`) that
       have not yet been resolved by an operator review (`operator_review.status == "reviewed"`).
    2. Due meetings refused for too few participants (`reason == "insufficient_participants"`).
    3. Proposals waiting for an operator.
    """
    items: List[Dict[str, Any]] = []
    seen_refusal_keys: set[str] = set()

    # 1. Meetings ending in REVIEW (or refused for insufficient_participants)
    for m in meetings:
        if not isinstance(m, Mapping):
            continue
        meeting_id = str(m.get("meeting_id") or m.get("id") or "").strip()
        if not meeting_id:
            continue
        m_type = str(m.get("meeting_type") or m.get("type") or "persona_meeting").strip()
        outcome = str(
            m.get("outcome")
            or ((m.get("synthesis") or {}).get("outcome") if isinstance(m.get("synthesis"), Mapping) else "")
            or ""
        ).strip()
        status = str(m.get("status") or "").strip()
        reason = str(m.get("reason") or m.get("refusal_reason") or "").strip()
        chair = m.get("chair_persona_id")
        created_at = str(m.get("closed_at") or m.get("created_at") or snapshot_at)
        href = f"/management/meetings/{quote(meeting_id, safe='')}"

        if outcome.upper() == "REVIEW" or status.upper() == "REVIEW":
            review_cause = _resolve_meeting_review_cause(m) or "unknown"
            op_review = _normalize_operator_review(m.get("operator_review"), default_cause=review_cause)
            if op_review is not None and not include_reviewed:
                continue
            evidence_refs = _collect_meeting_evidence_refs(
                m,
                is_operator=is_operator,
                caller_user_id=caller_user_id,
            )
            inbox_id = f"meeting_review:{meeting_id}"
            is_reviewed = op_review is not None
            decision_history = (
                [
                    {
                        "decided_at": op_review["reviewed_at"],
                        "decided_by": op_review["reviewed_by"],
                        "decision": "approve",
                        "note": op_review["note"],
                    }
                ]
                if op_review is not None
                else []
            )
            items.append({
                "id": inbox_id,
                "item_id": meeting_id,
                "inbox_id": inbox_id,
                "kind": "meeting_review",
                "inboxType": "meeting",
                "inbox_type": "meeting",
                "source_type": "meeting",
                "source_dataset": "consultation_meetings",
                "source_id": meeting_id,
                "meeting_id": meeting_id,
                "meeting_type": m_type,
                "persona_id": chair,
                "title": f"Meeting synthesis requires operator review ({m_type})",
                "summary": f"Meeting {meeting_id} ({m_type}) ended with REVIEW synthesis and requires operator attention.",
                "status": "reviewed" if is_reviewed else "pending",
                "action_state": "resolved" if is_reviewed else "pending",
                "outcome": "REVIEW",
                "reason": "synthesis_review_required",
                "unreadable_reason": review_cause,
                "synthesis_unreadable_reason": review_cause,
                "cause": review_cause,
                "blocking_reasons": [] if is_reviewed else ["synthesis_review_required"],
                "evidence_refs": evidence_refs,
                "operator_review": op_review,
                "decision_history": decision_history,
                "priority": "high",
                "risk_level": "high",
                "required_role": "operator",
                "can_decide": not is_reviewed,
                "can_proceed": is_reviewed,
                "created_at": created_at,
                "updated_at": op_review["reviewed_at"] if op_review is not None else created_at,
                "href": href,
                "route": href,
                "detail_href": href,
                "decision_href": href,
                "links": {
                    "manageHref": href,
                    "primaryObjectHref": href,
                    "meeting": href,
                    "decisionHref": href,
                },
            })
        elif (
            reason in {"insufficient_participants", "too_few_participants"}
            or outcome in {"insufficient_participants", "too_few_participants"}
        ):
            ref_key = f"{chair or 'cross'}:{m_type}:{created_at}"
            seen_refusal_keys.add(ref_key)
            inbox_id = f"meeting_refusal:{meeting_id}"
            items.append({
                "id": inbox_id,
                "item_id": meeting_id,
                "inbox_id": inbox_id,
                "kind": "meeting_refusal",
                "inboxType": "meeting",
                "inbox_type": "meeting",
                "source_type": "meeting",
                "source_dataset": "consultation_meetings",
                "source_id": meeting_id,
                "meeting_id": meeting_id,
                "meeting_type": m_type,
                "persona_id": chair,
                "title": f"Due meeting {m_type} refused: insufficient participants",
                "summary": f"Due meeting {meeting_id} ({m_type}) was refused due to insufficient participants.",
                "status": "pending",
                "action_state": "pending",
                "reason": "insufficient_participants",
                "blocking_reasons": ["insufficient_participants"],
                "priority": "medium",
                "risk_level": "medium",
                "required_role": "operator",
                "can_decide": True,
                "can_proceed": False,
                "created_at": created_at,
                "updated_at": created_at,
                "href": href,
                "route": href,
                "detail_href": href,
                "links": {
                    "manageHref": href,
                    "primaryObjectHref": href,
                    "meeting": href,
                },
            })

    # 2. Due meetings refused for too few participants from catalogue.refusals
    refusals = (catalogue or {}).get("refusals") if isinstance(catalogue, Mapping) else None
    if isinstance(refusals, list):
        for idx, ref in enumerate(refusals):
            if not isinstance(ref, Mapping):
                continue
            reason = str(ref.get("reason") or "").strip()
            if reason not in {"insufficient_participants", "too_few_participants"}:
                continue
            persona_id = str(ref.get("persona_id") or "").strip() or None
            m_type = str(ref.get("meeting_type") or ref.get("type") or "persona_meeting").strip()
            at = str(ref.get("at") or snapshot_at).strip()
            ref_meeting_id = str(ref.get("meeting_id") or "").strip() or None
            ref_key = f"{persona_id or 'cross'}:{m_type}:{at}"
            if ref_key in seen_refusal_keys:
                continue
            seen_refusal_keys.add(ref_key)
            item_id = ref_meeting_id or f"refusal-{persona_id or 'cross'}-{m_type}-{idx}"
            inbox_id = f"meeting_refusal:{item_id}"
            href = (
                f"/management/meetings/{quote(ref_meeting_id, safe='')}"
                if ref_meeting_id
                else "/management/meetings?tab=budget"
            )
            items.append({
                "id": inbox_id,
                "item_id": item_id,
                "inbox_id": inbox_id,
                "kind": "meeting_refusal",
                "inboxType": "meeting",
                "inbox_type": "meeting",
                "source_type": "meeting",
                "source_dataset": "consultation_meetings",
                "source_id": item_id,
                "meeting_id": ref_meeting_id,
                "meeting_type": m_type,
                "persona_id": persona_id,
                "title": f"Due meeting {m_type} refused: insufficient participants",
                "summary": f"Due meeting {m_type} for {persona_id or 'cross-persona'} was refused (insufficient_participants).",
                "status": "pending",
                "action_state": "pending",
                "reason": "insufficient_participants",
                "blocking_reasons": ["insufficient_participants"],
                "priority": "medium",
                "risk_level": "medium",
                "required_role": "operator",
                "can_decide": True,
                "can_proceed": False,
                "created_at": at,
                "updated_at": at,
                "href": href,
                "route": href,
                "detail_href": href,
                "links": {
                    "manageHref": href,
                    "primaryObjectHref": href,
                    "meeting": href,
                },
            })

    # 3. Proposals waiting for an operator
    for prop in proposals:
        if not isinstance(prop, Mapping):
            continue
        if not _proposal_waiting_for_operator(prop):
            continue
        proposal_id = str(prop.get("proposal_id") or prop.get("id") or "").strip()
        if not proposal_id:
            continue
        meeting_id = str(prop.get("meeting_id") or "").strip() or None
        sponsor = prop.get("sponsor_persona_id")
        p_kind = str(prop.get("kind") or "meeting_proposal").strip()
        shaped_prop = _shape_proposal_item(prop, default_meeting_id=meeting_id)
        downstream = shaped_prop.get("downstream") or {}
        created_at = str(prop.get("created_at") or snapshot_at)
        href = (
            f"/management/meetings/{quote(meeting_id, safe='')}"
            if meeting_id
            else "/management/meetings?tab=proposals"
        )
        inbox_id = f"meeting_proposal:{proposal_id}"
        items.append({
            "id": inbox_id,
            "item_id": proposal_id,
            "inbox_id": inbox_id,
            "kind": "meeting_proposal",
            "inboxType": "meeting",
            "inbox_type": "meeting",
            "source_type": "meeting",
            "source_dataset": "consultation_meetings",
            "source_id": proposal_id,
            "proposal_id": proposal_id,
            "proposal_kind": p_kind,
            "meeting_id": meeting_id,
            "persona_id": sponsor,
            "sponsor_persona_id": sponsor,
            "downstream": downstream,
            "title": f"Meeting proposal {proposal_id} ({p_kind}) waiting for operator",
            "summary": f"Proposal {proposal_id} from meeting {meeting_id or 'unknown'} requires operator review.",
            "status": "pending",
            "action_state": "pending",
            "reason": "proposal_waiting_for_operator",
            "blocking_reasons": ["proposal_waiting_for_operator"],
            "priority": "high",
            "risk_level": "medium",
            "required_role": "operator",
            "can_decide": True,
            "can_proceed": False,
            "created_at": created_at,
            "updated_at": created_at,
            "href": href,
            "route": href,
            "detail_href": href,
            "links": {
                "manageHref": href,
                "primaryObjectHref": href,
                "meeting": href,
                **({"downstream": downstream["href"]} if downstream.get("href") else {}),
            },
        })

    names = current_resolver()
    for item in items:
        names.annotate(
            item,
            persona_keys=("persona_id", "sponsor_persona_id"),
            strategy_keys=(),
        )
    return items


def _action_is_overdue(due_at: Any, snapshot_at: str) -> Optional[bool]:
    if not due_at:
        return None
    try:
        due = datetime.fromisoformat(str(due_at).replace("Z", "+00:00"))
        now = datetime.fromisoformat(snapshot_at.replace("Z", "+00:00"))
        if due.tzinfo is None or now.tzinfo is None:
            return None
        return due.astimezone(timezone.utc) < now.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _can_respond_to_human_ticket(ticket: Mapping[str, Any], identity: Any) -> bool:
    """UI hint for the existing PATCH, including Research's narrower actor rules.

    This never authorizes a write: Research rechecks actor, tenant, state,
    revision and evidence on every command. Missing caller/state stays false.
    """
    from fastapi import HTTPException
    from services.control_plane.bff.auth.policy import require_operator_role

    actor = getattr(identity, "operator_id", None)
    roles = getattr(identity, "roles", None)
    if not actor or not roles:
        return False
    try:
        require_operator_role(identity)  # Same BFF gate as original ticket PATCH.
    except HTTPException:
        return False
    action = ticket.get("meeting_action")
    if not isinstance(action, Mapping) or action.get("assignee_kind") not in {"human", "persona"}:
        return False
    privileged = bool(set(roles) & {"operator", "admin"})
    state = ticket.get("action_state")
    if action.get("assignee_kind") == "human" and state in {"open", "in_progress", "returned"}:
        return privileged or actor == ticket.get("owner")
    if state == "waiting_for_input":
        return privileged or (actor == ticket.get("required_actor")
                              and bool(set(roles) & {"operator", "approver", "human"}))
    return False


def _human_input_ticket_items(page: Mapping[str, Any], *, tenant_id: str, snapshot_at: str,
                             identity: Any = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for ticket in page.get("data") or []:
        if not isinstance(ticket, Mapping) or ticket.get("tenant_id") != tenant_id:
            continue
        action = ticket.get("meeting_action")
        if not isinstance(action, Mapping):
            continue
        state = ticket.get("action_state")
        direct = action.get("assignee_kind") == "human" and state in {"open", "in_progress", "returned"}
        if not direct and state != "waiting_for_input":
            continue
        tid = str(ticket.get("ticket_id") or "")
        meeting_id = str(action.get("meeting_id") or "")
        if not tid or not meeting_id or tid in seen:
            continue
        seen.add(tid)
        href = f"/management/meetings/{quote(meeting_id, safe='')}?action={quote(tid, safe='')}"
        request = ticket.get("input_request") or {}
        review = ticket.get("review_decision") or {}
        # The chair's human-only requirement is stored by Research in the
        # existing description and included in its authorized human page.
        # A deliverable alone does not explain why human help is required.
        requirement = ticket.get("description") if direct else None
        requirement = (requirement.strip() or None) if isinstance(requirement, str) else None
        if direct:
            reason = ((review.get("reason") if state == "returned" else None)
                      or requirement or "Human-specific requirement was not recorded.")
        else:
            reason = request.get("reason")
        items.append({
            "id": f"meeting_action:{tid}", "inbox_id": f"meeting_action:{tid}", "item_id": tid,
            "kind": "meeting_action", "inboxType": "meeting", "inbox_type": "meeting",
            "source_type": "meeting", "source_dataset": "research_tickets", "source_id": tid,
            "action_id": tid, "ticket_id": tid, "meeting_id": meeting_id,
            "title": ticket.get("title") or action.get("deliverable"),
            "summary": reason, "reason": reason,
            "human_requirement": requirement,
            "requested_material": action.get("deliverable"),
            "acceptance_criterion": action.get("acceptance_criterion"),
            "owner": ticket.get("owner"), "assignee": ticket.get("assignee") or ticket.get("owner"),
            "assignee_kind": action.get("assignee_kind"),
            "response_action": "submit" if direct else "provide_input",
            "can_respond": _can_respond_to_human_ticket(ticket, identity),
            "resumes_with": ticket.get("reviewer_id") if direct else ticket.get("owner"),
            "reviewer_id": ticket.get("reviewer_id"), "review_decision": review,
            "evidence_refs": ticket.get("evidence_refs") or [],
            "required_actor": ticket.get("required_actor"), "input_request": request,
            "status": ticket.get("action_state"), "action_state": "pending",
            "owner_status": ticket.get("status"), "revision": ticket.get("revision"),
            "due_at": action.get("due_at"), "next_review": action.get("next_review"),
            "overdue": _action_is_overdue(action.get("due_at"), snapshot_at),
            "created_at": ticket.get("created_at"), "updated_at": ticket.get("updated_at"),
            "priority": ticket.get("priority") or "medium", "risk_level": "medium",
            "can_decide": False, "can_proceed": False,
            "href": href, "route": href, "detail_href": href,
            "links": {"meeting": href, "manageHref": href, "primaryObjectHref": href,
                      "ticket": f"/api/v1/research/tickets/{quote(tid, safe='')}"},
        })
    return items


@contextmanager
def _inbox_owner_budget():
    # Each independent owner gets part of the existing contributor deadline;
    # one slow source cannot consume the other source's entire read allowance.
    from services.control_plane.bff.governance.service import human_inbox_surface_timeout_seconds
    allowance = human_inbox_surface_timeout_seconds() * 0.4
    remaining = remaining_management_read_budget(allowance)
    budget = min(allowance, remaining) if remaining is not None else allowance
    token = management_read_deadline.set(time.monotonic() + max(0, budget))
    try:
        yield
    finally:
        management_read_deadline.reset(token)


def read_human_inbox_meeting_items(
    snapshot_at: str,
    *,
    store: Any = None,
    tenant_id: Optional[str] = None,
    identity: Any = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Contributor reader for ManagementService.get_human_inbox."""
    if store is not None and hasattr(store, "list_meetings"):
        meetings = list(store.list_meetings() or [])
        proposals = list(store.list_meeting_proposals() or []) if hasattr(store, "list_meeting_proposals") else []
        catalogue = store.get_meetings_catalogue() if hasattr(store, "get_meetings_catalogue") else {}
        items = project_human_inbox_meeting_items(
            snapshot_at=snapshot_at,
            meetings=meetings,
            proposals=proposals,
            catalogue=catalogue if isinstance(catalogue, Mapping) else {},
        )
        return items, {"status": "ok", "source": "consultation", "snapshot_at": snapshot_at}

    eff_tenant = (
        tenant_id
        or selected_tenant.get()
        or os.getenv("PANTHEON_BFF_TENANT_ID")
        or os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID")
        or os.getenv("PANTHEON_TENANT_ID")
        or "tenant-dev"
    )
    from services.control_plane.bff.core.owner_reads import verified_caller_tenant
    from services.control_plane.bff.research.client import ResearchServiceClient, ResearchCommandError, resolve_orchestrator_base_url

    # Resolve from the authenticated caller, never the contributor service actor.
    if resolve_orchestrator_base_url():
        eff_tenant = verified_caller_tenant(tenant_id=tenant_id)
    client = MeetingsOwnerClient(tenant_id=eff_tenant, actor_id="human-inbox")
    surfaces: Dict[str, Any] = {}
    items: List[Dict[str, Any]] = []
    # A partial owner failure must not hide work from a different owner.
    with _inbox_owner_budget():
        try:
            meetings_res = client.list_meetings(limit=100)
            proposals_res = client.list_proposals()
            catalogue_res = client.get_catalogue()
            items.extend(project_human_inbox_meeting_items(
                snapshot_at=snapshot_at, meetings=meetings_res.get("items") or [],
                proposals=proposals_res.get("items") or [], catalogue=catalogue_res))
            surfaces["consultation"] = {
                "status": "degraded" if meetings_res.get("next_cursor") else "ok",
                "reason": "owner_page_has_more" if meetings_res.get("next_cursor") else None,
                "next_cursor": meetings_res.get("next_cursor"),
            }
        except (MeetingsOwnerUnavailableError, MeetingsOwnerValidationError, MeetingsOwnerNotFoundError) as exc:
            surfaces["consultation"] = {"status": "denied" if exc.status_code in (401, 403) else "unavailable",
                                        "reason": exc.reason}
    if resolve_orchestrator_base_url():
        with _inbox_owner_budget():
            try:
                page = ResearchServiceClient().list_research_ticket_page(human_actionable=True, page_size=100)
                items.extend(_human_input_ticket_items(page, tenant_id=eff_tenant, snapshot_at=snapshot_at,
                                                      identity=identity))
                surfaces["research"] = {
                    "status": "degraded" if page["page_info"].get("next_page_token") else "ok",
                    "reason": "owner_page_has_more" if page["page_info"].get("next_page_token") else None,
                    "page_info": page["page_info"],
                    "href": "/api/v1/research/tickets?meeting_linked=true&human_actionable=true&page_size=100"}
            except ResearchCommandError as exc:
                surfaces["research"] = {"status": "denied" if exc.status_code in (401, 403) else "unavailable",
                                        "reason": str(exc), "status_code": exc.status_code}
    else:
        surfaces["research"] = {"status": "unavailable", "reason": "research_owner_unconfigured"}
    states = [s["status"] for s in surfaces.values()]
    return items, {"status": "ok" if all(s == "ok" for s in states) else "degraded" if any(s in {"ok", "degraded"} for s in states) else "unavailable",
                   "source": "bff_composed", "snapshot_at": snapshot_at, "surfaces": surfaces}
