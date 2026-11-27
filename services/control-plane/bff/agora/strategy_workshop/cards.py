"""Workshop live-card projection helpers.

Builds the typed /cards feed (GET .../cards) by merging event, completeness,
next-question, and readiness cards. Moved out of router.py alongside the
route-group split (ACG-06-004) so both router.py (list_workshop_cards is not
itself moved out) and routes/session.py can import one implementation
without a circular import between router.py and routes/session.py.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from ._common import _clean_optional, _safe_card_id
from .events import _ws_utc_now
from .readiness import (
    _explicit_blockers,
    _readiness_helpers,
    _state_map_from_snapshot,
    build_next_high_value_decision,
    project_completeness_response,
)


def _card_sequence_base(events: List[Dict[str, Any]]) -> int:
    return max((int(event.get("sequence_no", 0)) for event in events), default=0)


def _event_card(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if event.get("event_type") not in {"message", "decision"}:
        return None
    event_id = event.get("event_id") or f"seq-{event.get('sequence_no')}"
    refs = event.get("payload_refs_json")
    is_decision = event.get("event_type") == "decision" or (
        isinstance(refs, list) and any(str(r).startswith(("kind:workshop_decision", "decision_id:", "decision:")) for r in refs)
    )
    title = "Workshop decision recorded" if is_decision else "Strategy description captured"
    return {
        "spec_version": "1.0",
        "card_id": f"card_{_safe_card_id('event', event_id)}",
        "card_type": "user_strategy_description",
        "workshop_id": event["workshop_id"],
        "sequence_no": int(event.get("sequence_no") or 1),
        "source_event_ids": [event_id],
        "status": "informational",
        "title": title,
        "summary": event.get("redacted_summary") or "Owner-private workshop message captured in the private content store.",
        "payload": {
            "owner_visible_content": event.get("redacted_summary") or "Owner-private workshop message captured.",
            "message_event_id": event_id,
            "created_at": event.get("created_at") or _ws_utc_now(),
        },
        "created_at": event.get("created_at") or _ws_utc_now(),
    }


def _completeness_card(
    *,
    session: Dict[str, Any],
    snapshot: Dict[str, Any],
    readiness: Dict[str, Any],
    sequence_no: int,
    events: Optional[List[Dict[str, Any]]] = None,
    reconstruction: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    _assess_blocking_items, _assess_readiness, compute_overall_grade = _readiness_helpers()
    state_map = _state_map_from_snapshot(snapshot, events=events)
    projected = project_completeness_response(
        snapshot=snapshot,
        session=session,
        events=events,
        reconstruction=reconstruction,
        readiness=readiness,
    ) or {}
    dimension_updates = [
        {
            "dimension": field,
            "prior_grade": "unknown",
            "current_grade": state,
            "gaps": [] if state in {"confirmed", "complete", "satisfied"} else [f"{field} is {state}"],
            "required_actions": [],
        }
        for field, state in state_map.items()
    ] or [
        {
            "dimension": "strategy_completeness",
            "prior_grade": "unknown",
            "current_grade": "missing",
            "gaps": ["No completeness state map is available"],
            "required_actions": ["Capture or recompute StrategyCompleteness before promotion"],
        }
    ]
    ready_gates = [
        gate["gate"]
        for gate in readiness.get("gates", [])
        if gate.get("state") == "ready"
    ]
    return {
        "spec_version": "1.0",
        "card_id": f"card_{_safe_card_id('completeness', snapshot.get('snapshot_id') or session['workshop_id'])}",
        "card_type": "completeness_update",
        "workshop_id": session["workshop_id"],
        "sequence_no": sequence_no,
        "source_event_ids": [],
        "workshop_version_id": snapshot.get("strategy_version_id"),
        "strategy_spec_registry_id": session.get("active_strategy_spec_registry_id") or session.get("strategy_id"),
        "status": "completed",
        "title": "Strategy completeness updated",
        "summary": "Latest StrategyCompleteness snapshot from scoped workshop store.",
        "payload": {
            "overall_grade": compute_overall_grade(state_map) if state_map else "incomplete",
            "dimension_updates": dimension_updates,
            "blockers": _explicit_blockers(snapshot),
            "research_ready": "preliminary_research" in ready_gates,
            "readiness_gates": ready_gates,
            "change_since_previous": "latest_snapshot",
            "strategy_family": projected.get("strategy_family", "generic"),
            "is_branch_strategy": bool(projected.get("is_branch_strategy", False)),
            "blocks": projected.get("blocks", []),
            "twelve_blocks": projected.get("twelve_blocks", []),
            "next_decision": projected.get("next_decision"),
        },
        "created_at": snapshot.get("created_at") or readiness.get("assessed_at") or _ws_utc_now(),
    }


def _next_question_card(
    *,
    session: Dict[str, Any],
    snapshot: Dict[str, Any],
    sequence_no: int,
    events: Optional[List[Dict[str, Any]]] = None,
    reconstruction: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    raw = snapshot.get("next_question_json")
    if not isinstance(raw, dict) or not raw:
        return None
    decision = build_next_high_value_decision(
        snapshot=snapshot,
        session=session,
        events=events,
        reconstruction=reconstruction,
    )
    question_id = str(
        decision.get("question_id")
        or raw.get("question_id")
        or raw.get("id")
        or f"q_{snapshot.get('snapshot_id') or sequence_no}"
    )
    question_text = str(
        decision.get("question")
        or raw.get("question")
        or raw.get("text")
        or raw.get("prompt")
        or "Clarify the next strategy decision."
    )
    return {
        "spec_version": "1.0",
        "card_id": f"card_{_safe_card_id('next', question_id)}",
        "card_type": "next_question",
        "workshop_id": session["workshop_id"],
        "sequence_no": sequence_no,
        "status": "action_required",
        "title": "Next workshop question",
        "summary": question_text,
        "payload": {
            "question_id": question_id,
            "decision_id": decision.get("decision_id") or question_id,
            "question": question_text,
            "why_now": str(decision.get("why_now") or raw.get("why_now") or raw.get("reason") or "This answer improves downstream readiness."),
            "score_total": float(raw.get("score_total") or raw.get("score") or decision.get("score_total") or 0),
            "answer_options": decision.get("answer_options") or raw.get("answer_options") or raw.get("options") or [],
            "options": decision.get("options") or [],
            "default_option_id": decision.get("default_option_id"),
            "servant_default": decision.get("servant_default"),
            "resolves": decision.get("resolves") or [],
            "target_blocks": decision.get("target_blocks") or [],
            "freeform_allowed": bool(raw.get("freeform_allowed", True)),
            "defer_allowed": bool(raw.get("defer_allowed", True)),
        },
        "created_at": snapshot.get("created_at") or _ws_utc_now(),
    }


def _readiness_card(
    *,
    session: Dict[str, Any],
    readiness: Dict[str, Any],
    sequence_no: int,
) -> Dict[str, Any]:
    hard_blockers: List[str] = []
    for gate in readiness.get("gates", []):
        for req in gate.get("requirements", []):
            if req.get("hardness") == "hard" and req.get("state") in {"missing", "partial", "stale"}:
                hard_blockers.append(str(req.get("title")))
    return {
        "spec_version": "1.0",
        "card_id": f"card_{_safe_card_id('readiness', readiness.get('assessment_id') or session['workshop_id'])}",
        "card_type": "readiness_gate",
        "workshop_id": session["workshop_id"],
        "sequence_no": sequence_no,
        "workshop_version_id": readiness.get("workshop_version_id"),
        "strategy_spec_registry_id": readiness.get("strategy_spec_registry_id"),
        "status": "completed" if readiness.get("highest_ready_gate") else "action_required",
        "title": "Strategy readiness gates",
        "summary": f"Highest ready gate: {readiness.get('highest_ready_gate') or 'none'}",
        "payload": _clean_optional({
            "gates": readiness.get("gates", []),
            "hard_blockers": hard_blockers,
            "temporary_assumptions": [],
            "staleness_reasons": readiness.get("staleness_reasons", []),
            "highest_ready_gate": readiness.get("highest_ready_gate"),
            "assessed_at": readiness.get("assessed_at"),
            "valid_until": readiness.get("valid_until"),
        }),
        "evidence_refs": readiness.get("evidence_refs", []),
        "allowed_actions": {"reassess_readiness": True},
        "created_at": readiness.get("assessed_at") or _ws_utc_now(),
    }


def _build_workshop_cards(
    *,
    session: Dict[str, Any],
    events: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
    readiness: Dict[str, Any],
    reconstruction: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    cards = [card for card in (_event_card(event) for event in events) if card is not None]
    next_seq = _card_sequence_base(events) + 1
    if snapshot:
        cards.append(_completeness_card(
            session=session,
            snapshot=snapshot,
            readiness=readiness,
            sequence_no=next_seq,
            events=events,
            reconstruction=reconstruction,
        ))
        next_seq += 1
        next_question = _next_question_card(
            session=session,
            snapshot=snapshot,
            sequence_no=next_seq,
            events=events,
            reconstruction=reconstruction,
        )
        if next_question:
            cards.append(next_question)
            next_seq += 1
    cards.append(_readiness_card(session=session, readiness=readiness, sequence_no=next_seq))
    return sorted((_clean_optional(card) for card in cards), key=lambda card: int(card.get("sequence_no", 0)))


def _merge_cards(*card_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for cards in card_lists:
        for card in cards:
            merged[str(card.get("card_id"))] = _clean_optional(dict(card))
    return sorted(merged.values(), key=lambda card: int(card.get("sequence_no", 0)))
