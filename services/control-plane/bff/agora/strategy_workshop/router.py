"""Agora strategy-workshop router — agora.workshop.v1.

Implements the /bff/agora/workshops/* route family per the AG-BE-SW-001
contract (docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/
03_servant_and_workshop_contracts.md §B).

Routes implemented in this module:
  GET  /bff/agora/workshops                     — list user-scoped sessions
  POST /bff/agora/workshops                     — create workshop session
  GET  /bff/agora/workshops/{workshop_id}       — get session (ETag)
  POST /bff/agora/workshops/{workshop_id}/messages   — append event (private_content_ref only)
  GET  /bff/agora/workshops/{workshop_id}/events     — list events
  GET  /bff/agora/workshops/{workshop_id}/completeness — latest completeness snapshot
  POST /bff/agora/workshops/{workshop_id}/completeness — persist completeness snapshot
  GET  /bff/agora/workshops/{workshop_id}/cards — typed live card projection
  GET  /bff/agora/workshops/{workshop_id}/readiness — latest readiness assessment
  POST /bff/agora/workshops/{workshop_id}/readiness/reassess — persist fresh readiness assessment
  GET  /bff/agora/workshops/{workshop_id}/stream — SSE aggregate (AG-BE-SW-004)

Routes still in main.py (migration pending — see router stub comment):
  GET  /bff/agora/training-examples
  POST /bff/agora/training-examples
  ...  (all the old committee/evaluation/persona-lab routes)

Canonical live operations:
  GET/POST /bff/agora/workshops/{id}/versions
  POST     /bff/agora/workshops/{id}/versions/{ver}/select
  POST     /bff/agora/workshops/{id}/research-runs
  POST     /bff/agora/workshops/{id}/consultations
  POST     /bff/agora/workshops/{id}/conclude
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import uuid
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Mapping, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .operations import CanonicalOperationError, WorkshopCanonicalOperations
from .reconstruction import StrategyReconstructionResult, reconstruct_strategy_from_events
from .store import WorkshopVersionProjectionConflict, make_workshop_store

_CONTROL_PLANE_DIR = Path(__file__).resolve().parents[3]
if str(_CONTROL_PLANE_DIR) not in sys.path:
    sys.path.insert(0, str(_CONTROL_PLANE_DIR))

from privacy.private_content_store import (  # noqa: E402
    EphemeralKeyProvider,
    MemoryPrivateContentStore,
)


class _StrategyVersionProjectionError(RuntimeError):
    def __init__(self, reason: str, *, status_code: int = 409) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# Module-level SSE state (per-workshop pub-sub)
#
# Each workshop gets its own bounded replay buffer and subscriber list.
# _ws_publish() is called from sync route handlers; asyncio.Queue.put_nowait()
# is thread-safe and requires no running event loop at the call site.
# --------------------------------------------------------------------------- #

_WS_SSE_BUFFER_SIZE = 500  # max events per workshop kept for reconnect replay

# workshop_id → deque[(event_id, event_dict)]
_workshop_sse_buffers: Dict[str, deque] = {}
# workshop_id → list[asyncio.Queue]
_workshop_sse_subscribers: Dict[str, List[asyncio.Queue]] = {}


def _ws_utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _ws_event_id() -> str:
    return f"wsevt-{uuid.uuid4().hex[:16]}"


def _ws_sse_format(event: dict) -> str:
    return (
        f"id: {event['id']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
    )


def _ws_get_buffer(workshop_id: str) -> deque:
    if workshop_id not in _workshop_sse_buffers:
        _workshop_sse_buffers[workshop_id] = deque(maxlen=_WS_SSE_BUFFER_SIZE)
    return _workshop_sse_buffers[workshop_id]


def _ws_get_subscribers(workshop_id: str) -> List[asyncio.Queue]:
    if workshop_id not in _workshop_sse_subscribers:
        _workshop_sse_subscribers[workshop_id] = []
    return _workshop_sse_subscribers[workshop_id]


def _ws_publish(
    workshop_id: str,
    event_type: str,
    data: dict,
    *,
    utc_now_fn: Optional[Callable[[], str]] = None,
    event_id: Optional[str] = None,
) -> str:
    """Publish an SSE event to the workshop buffer and all live subscribers.

    Safe to call from sync route handlers. Returns the new event_id.
    """
    event_id = event_id or _ws_event_id()
    if any(existing_id == event_id for existing_id, _ in _ws_get_buffer(workshop_id)):
        return event_id
    timestamp = utc_now_fn() if utc_now_fn else _ws_utc_now()
    event: Dict[str, Any] = {
        "id": event_id,
        "type": event_type,
        "timestamp": timestamp,
        "data": {"workshop_id": workshop_id, **data},
    }
    _ws_get_buffer(workshop_id).append((event_id, event))
    for q in list(_ws_get_subscribers(workshop_id)):
        try:
            q.put_nowait(event)
        except (asyncio.QueueFull, Exception):
            pass
    return event_id


def _ws_replay_after(
    workshop_id: str,
    last_event_id: str,
    store: Optional[Any] = None,
) -> List[dict]:
    """Return buffered events that came after *last_event_id*.

    Returns an empty list when last_event_id is not found (caller may treat
    this as a missed-event condition and reconnect from the top).
    """
    buf = _workshop_sse_buffers.get(workshop_id)
    found = False
    replayed: List[dict] = []
    if buf:
        for eid, evt in buf:
            if found:
                replayed.append(evt)
            elif eid == last_event_id:
                found = True
        if found:
            return replayed

    # Database fallback to ensure reconnect/readback does not lose durable cards/events
    if store and hasattr(store, "list_events"):
        try:
            db_events = store.list_events(workshop_id)
            idx = -1
            for i, ev in enumerate(db_events):
                if ev.get("event_id") == last_event_id:
                    idx = i
                    break
            if idx != -1:
                replayed = []
                for ev in db_events[idx + 1:]:
                    payload_refs = ev.get("payload_refs_json")
                    if isinstance(payload_refs, str) and payload_refs:
                        try:
                            payload_refs = json.loads(payload_refs)
                        except Exception:
                            pass
                    if isinstance(payload_refs, dict) and "event_type" in payload_refs:
                        # Reconstructed SSE event format from full DB event
                        evt = {
                            "id": ev["event_id"],
                            "type": ev["event_type"],
                            "timestamp": ev["created_at"],
                            "data": {
                                "workshop_id": workshop_id,
                                **payload_refs
                            }
                        }
                    else:
                        evt = {
                            "id": ev["event_id"],
                            "type": ev["event_type"],
                            "timestamp": ev["created_at"],
                            "data": {
                                "workshop_id": workshop_id,
                                "event_id": ev["event_id"],
                                "sequence_no": ev["sequence_no"],
                                "actor_type": ev["actor_type"],
                                "event_type": ev["event_type"],
                                "private_content_ref": ev.get("private_content_ref"),
                                "redacted_summary": ev.get("redacted_summary"),
                                "payload_refs_json": payload_refs,
                                "trace_id": ev.get("trace_id"),
                            }
                        }
                    replayed.append(evt)
                return replayed
        except Exception:
            pass
    return []



# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _parse_etag_lock_version(if_match: str, workshop_id: str) -> int:
    """Return the lock_version encoded in the ETag, or 0 if the header is malformed.

    Expected format: W/"workshop:{workshop_id}:v{N}"
    Returns 0 so a malformed header is guaranteed to conflict (lock_version >= 1).
    """
    prefix = f'W/"workshop:{workshop_id}:v'
    if if_match.startswith(prefix) and if_match.endswith('"'):
        try:
            return int(if_match[len(prefix):-1])
        except ValueError:
            pass
    return 0


def _raise_cross_user_forbidden(
    *,
    bff_error: Callable[..., HTTPException],
    resource: str,
    resource_id: str,
) -> None:
    from models import ErrorCode

    raise bff_error(
        403,
        ErrorCode.FORBIDDEN,
        "Agora resource is outside the current user scope",
        "CROSS_USER_ACCESS_FORBIDDEN",
        precondition_failed="agora_user_scope",
        details_extra={"resource": resource, "resource_id": resource_id},
    )


def _identity_for_scope(identity: Any) -> Any:
    """Normalize test-injected dict identities to the OperatorIdentity shape."""
    if not isinstance(identity, dict):
        return identity
    claims = identity.get("claims") if isinstance(identity.get("claims"), dict) else dict(identity)
    operator_id = (
        identity.get("operator_id")
        or identity.get("operatorId")
        or identity.get("sub")
        or claims.get("operator_id")
        or claims.get("operatorId")
        or claims.get("sub")
        or claims.get("user_id")
        or claims.get("userId")
        or ""
    )
    roles = identity.get("roles") or claims.get("roles") or ["operator"]
    if isinstance(roles, str):
        roles = [part.strip() for part in re.split(r"[\s,]+", roles) if part.strip()]
    return SimpleNamespace(
        operator_id=str(operator_id),
        roles=list(roles or []),
        claims=claims,
        token_kind=identity.get("token_kind", identity.get("tokenKind", "test")),
        mfa_verified=bool(
            identity.get("mfa_verified", identity.get("mfaVerified", False))
            or claims.get("mfa_verified", claims.get("mfaVerified", False))
        ),
    )


def _clean_optional(value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if item is not None and item != [] and item != {}
    }


def _safe_card_id(*parts: Any) -> str:
    raw = "_".join(str(part or "") for part in parts)
    clean = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return clean or uuid.uuid4().hex


def _project_winner_branch_state_map(state_map: Dict[str, str]) -> Dict[str, str]:
    winner_keys = {
        "market_scope",
        "insider_branch_mapping",
        "winner_branch_scoring",
        "migration_reverse_flow",
        "event_lead",
        "signal_formation",
        "entry_holding",
        "add_reduce_exit",
        "sizing_leverage",
        "cost_liquidity_capacity",
        "validation_backtest_refutation",
        "monitoring_update",
    }
    if not any(k in state_map for k in winner_keys):
        return state_map

    projected = dict(state_map)

    def to_generic_grade(grade: str) -> str:
        if grade in {"confirmed", "complete", "satisfied"}:
            return "complete"
        if grade in {"inferred_needs_confirmation", "weak", "conflicting"}:
            return "partial"
        return "missing"

    def to_specific_grade(grade: str) -> str:
        if grade == "confirmed":
            return "confirmed"
        if grade == "inferred_needs_confirmation":
            return "inferred_needs_confirmation"
        if grade == "weak":
            return "weak"
        if grade == "conflicting":
            return "conflicting"
        return "missing"

    # Map Winner Branch blocks directly to specific StrategySpec fields
    if "insider_branch_mapping" in state_map:
        projected["data_identity_mapping_role"] = to_specific_grade(state_map["insider_branch_mapping"])
        projected["identity_mapping_role"] = to_specific_grade(state_map["insider_branch_mapping"])
    if "migration_reverse_flow" in state_map:
        projected["exit"] = to_specific_grade(state_map["migration_reverse_flow"])
        projected["exit_invalidation"] = to_specific_grade(state_map["migration_reverse_flow"])
    if "event_lead" in state_map:
        projected["entry_signal"] = to_specific_grade(state_map["event_lead"])
    if "signal_formation" in state_map:
        projected["entry_signal_confirmation"] = to_specific_grade(state_map["signal_formation"])
    if "entry_holding" in state_map:
        projected["holding_period"] = to_specific_grade(state_map["entry_holding"])
    if "add_reduce_exit" in state_map:
        projected["exit_invalidation"] = to_specific_grade(state_map["add_reduce_exit"])
    if "sizing_leverage" in state_map:
        projected["risk_constraints"] = to_specific_grade(state_map["sizing_leverage"])
        projected["position_sizing"] = to_specific_grade(state_map["sizing_leverage"])
    if "cost_liquidity_capacity" in state_map:
        projected["execution_cost"] = to_specific_grade(state_map["cost_liquidity_capacity"])
        projected["liquidity"] = to_specific_grade(state_map["cost_liquidity_capacity"])
    if "validation_backtest_refutation" in state_map:
        projected["validation_oos"] = to_specific_grade(state_map["validation_backtest_refutation"])

    # Map Winner Branch blocks to 7 dimensions
    mapping = {
        "market_scope": ["market_scope"],
        "data_dependencies": ["insider_branch_mapping", "winner_branch_scoring", "migration_reverse_flow"],
        "hypothesis": ["event_lead", "signal_formation"],
        "evaluation_plan": ["entry_holding", "add_reduce_exit"],
        "risk_constraints": ["sizing_leverage"],
        "execution_profile": ["cost_liquidity_capacity", "validation_backtest_refutation"],
        "governance": ["monitoring_update"],
    }

    for dim, blocks in mapping.items():
        grades = [to_generic_grade(state_map[b]) for b in blocks if b in state_map]
        if not grades:
            if dim not in state_map:
                projected[dim] = "missing"
        elif all(g == "complete" for g in grades):
            projected[dim] = "complete"
        elif all(g == "missing" for g in grades):
            projected[dim] = "missing"
        else:
            projected[dim] = "partial"

    return projected


def _state_map_from_snapshot(snapshot: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not snapshot:
        return {}
    raw = snapshot.get("state_map_json") or {}
    if isinstance(raw, dict) and isinstance(raw.get("state_map"), dict):
        raw = raw["state_map"]
    if not isinstance(raw, dict):
        return {}
    result: Dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            state = value.get("state") or value.get("grade") or value.get("status")
        else:
            state = value
        if key and state is not None:
            result[str(key)] = str(state)
    return _project_winner_branch_state_map(result)


def _explicit_blockers(snapshot: Optional[Dict[str, Any]]) -> List[str]:
    if not snapshot:
        return []
    raw = snapshot.get("blocking_items_json") or []
    if not isinstance(raw, list):
        return [str(raw)]
    result: List[str] = []
    for item in raw:
        if isinstance(item, dict):
            result.append(str(item.get("reason") or item.get("field") or item))
        else:
            result.append(str(item))
    return [item for item in result if item]


def _readiness_helpers() -> tuple[Callable[[Any], Any], Callable[[Any], Any], Callable[[Any], str]]:
    try:
        from services.research.strategy_spec.completeness import (  # type: ignore
            assess_blocking_items,
            assess_readiness,
            compute_overall_grade,
        )
    except Exception:
        def assess_blocking_items(state_map: Dict[str, str]) -> List[Dict[str, str]]:
            return [
                {
                    "field": key,
                    "reason": f"Field '{key}' is {state}; blocks research gate.",
                    "gate": "research",
                }
                for key, state in state_map.items()
                if state in {"missing", "conflicting"}
            ]

        def assess_readiness(blocking_items: List[Any]) -> tuple[bool, bool, bool]:
            return (not blocking_items, not blocking_items, not blocking_items)

        def compute_overall_grade(state_map: Dict[str, str]) -> str:
            return "complete" if state_map and not assess_blocking_items(state_map) else "partial"

    return assess_blocking_items, assess_readiness, compute_overall_grade


def _blocking_attr(item: Any, attr: str) -> str:
    if isinstance(item, dict):
        return str(item.get(attr) or "")
    return str(getattr(item, attr, "") or "")


def _requirement(
    requirement_id: str,
    title: str,
    state: str,
    *,
    hardness: str = "hard",
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    return _clean_optional({
        "requirement_id": requirement_id,
        "title": title,
        "hardness": hardness,
        "state": state,
        "summary": summary,
    })


def _gate_payload(
    *,
    gate_name: str,
    gate_state: str,
    requirements: List[Dict[str, Any]],
    blockers: List[str],
    assessed_at: str,
) -> Dict[str, Any]:
    blocking_ids = [
        req["requirement_id"]
        for req in requirements
        if req.get("state") in {"missing", "partial", "stale"}
        and req.get("hardness") == "hard"
    ]
    return _clean_optional({
        "gate": gate_name,
        "state": gate_state,
        "requirements": requirements,
        "blocking_requirement_ids": blocking_ids,
        "conditional_assumptions": blockers if gate_state == "conditional" else [],
        "evaluated_at": assessed_at,
    })


def _build_evidence_refs(
    events: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for event in events:
        ref_id = event.get("private_content_ref") or event.get("event_id")
        if not ref_id:
            continue
        refs.append(_clean_optional({
            "ref_type": "evidence_item",
            "ref_id": ref_id,
            "summary": event.get("redacted_summary") or f"Workshop event {event.get('sequence_no')}",
            "data_cutoff": event.get("created_at"),
        }))
    if snapshot and snapshot.get("snapshot_id"):
        refs.append(_clean_optional({
            "ref_type": "evidence_bundle",
            "ref_id": snapshot["snapshot_id"],
            "summary": "Latest StrategyCompleteness snapshot",
            "data_cutoff": snapshot.get("created_at"),
        }))
    return refs


def _build_readiness_assessment(
    *,
    session: Dict[str, Any],
    events: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
    assessed_at: str,
    assessment_version: Optional[int] = None,
) -> Dict[str, Any]:
    assess_blocking_items, assess_readiness, _compute_overall_grade = _readiness_helpers()
    state_map = _state_map_from_snapshot(snapshot)
    hard_blockers = _explicit_blockers(snapshot)
    blocking_items = list(assess_blocking_items(state_map)) if state_map else []
    research_ready, validation_ready, trading_room_ready = assess_readiness(blocking_items)

    strategy_id = (
        session.get("strategy_id")
        or session.get("active_strategy_spec_registry_id")
        or f"unbound-{session['workshop_id']}"
    )
    workshop_version_id = (
        session.get("selected_version_id")
        or (snapshot or {}).get("strategy_version_id")
        or strategy_id
    )
    registry_id = (
        session.get("active_strategy_spec_registry_id")
        or session.get("strategy_id")
        or strategy_id
    )
    has_state = bool(state_map)
    has_events = bool(events)
    has_strategy_ref = bool(session.get("strategy_id") or session.get("active_strategy_spec_registry_id"))
    explicit_blocked = bool(hard_blockers)

    gate_blockers = {
        "preliminary_research": [
            _blocking_attr(item, "reason")
            for item in blocking_items
            if _blocking_attr(item, "gate") == "research"
        ],
        "full_validation": [
            _blocking_attr(item, "reason")
            for item in blocking_items
            if _blocking_attr(item, "gate") == "validation"
        ],
        "trading_room": [
            _blocking_attr(item, "reason")
            for item in blocking_items
            if _blocking_attr(item, "gate") == "trading_room"
        ],
    }

    if explicit_blocked and not blocking_items:
        gate_blockers = {key: list(hard_blockers) for key in gate_blockers}
        research_ready = validation_ready = trading_room_ready = False

    preliminary_state = "ready" if research_ready and has_state else (
        "conditional" if has_events or has_strategy_ref else "not_assessed"
    )
    if gate_blockers["preliminary_research"]:
        preliminary_state = "blocked"

    validation_state = "ready" if validation_ready and has_state else (
        "conditional" if preliminary_state == "ready" else "not_assessed"
    )
    if gate_blockers["full_validation"] or preliminary_state == "blocked":
        validation_state = "blocked"

    trading_state = "ready" if trading_room_ready and has_state and has_strategy_ref else (
        "conditional" if trading_room_ready and has_state else "not_assessed"
    )
    if gate_blockers["trading_room"] or validation_state == "blocked":
        trading_state = "blocked"

    gates = [
        _gate_payload(
            gate_name="preliminary_research",
            gate_state=preliminary_state,
            assessed_at=assessed_at,
            blockers=gate_blockers["preliminary_research"],
            requirements=[
                _requirement(
                    "state_map_present",
                    "Strategy completeness state map is present",
                    "satisfied" if has_state else "missing",
                    hardness="soft",
                ),
                _requirement(
                    "workshop_evidence_present",
                    "Workshop event evidence exists in scoped store",
                    "satisfied" if has_events else "missing",
                    hardness="hard",
                ),
                _requirement(
                    "research_blockers_clear",
                    "No research gate hard blockers",
                    "satisfied" if not gate_blockers["preliminary_research"] else "missing",
                    summary="; ".join(gate_blockers["preliminary_research"]) or None,
                ),
            ],
        ),
        _gate_payload(
            gate_name="full_validation",
            gate_state=validation_state,
            assessed_at=assessed_at,
            blockers=gate_blockers["full_validation"],
            requirements=[
                _requirement(
                    "preliminary_research_ready",
                    "Preliminary research gate is ready",
                    "satisfied" if preliminary_state == "ready" else "partial",
                ),
                _requirement(
                    "validation_blockers_clear",
                    "No validation gate hard blockers",
                    "satisfied" if not gate_blockers["full_validation"] else "missing",
                    summary="; ".join(gate_blockers["full_validation"]) or None,
                ),
            ],
        ),
        _gate_payload(
            gate_name="trading_room",
            gate_state=trading_state,
            assessed_at=assessed_at,
            blockers=gate_blockers["trading_room"],
            requirements=[
                _requirement(
                    "strategy_registry_ref_present",
                    "Strategy Registry reference exists in scoped workshop session",
                    "satisfied" if has_strategy_ref else "missing",
                ),
                _requirement(
                    "full_validation_ready",
                    "Full validation gate is ready",
                    "satisfied" if validation_state == "ready" else "partial",
                ),
                _requirement(
                    "trading_room_blockers_clear",
                    "No Trading Room hard blockers",
                    "satisfied" if not gate_blockers["trading_room"] else "missing",
                    summary="; ".join(gate_blockers["trading_room"]) or None,
                ),
            ],
        ),
    ]

    highest_ready_gate = None
    for gate_name in ("trading_room", "full_validation", "preliminary_research"):
        gate = next(item for item in gates if item["gate"] == gate_name)
        if gate["state"] == "ready":
            highest_ready_gate = gate_name
            break

    version = assessment_version or 1
    return _clean_optional({
        "spec_version": "1.0",
        "assessment_id": f"ready_{_safe_card_id(session['workshop_id'], version)}",
        "workshop_id": session["workshop_id"],
        "strategy_id": strategy_id,
        "workshop_version_id": workshop_version_id,
        "strategy_spec_registry_id": registry_id,
        "assessment_version": version,
        "gates": gates,
        "highest_ready_gate": highest_ready_gate,
        "staleness_reasons": [] if has_state else ["strategy_completeness_snapshot_missing"],
        "assessed_at": assessed_at,
        "evidence_refs": _build_evidence_refs(events, snapshot),
    })


def _card_sequence_base(events: List[Dict[str, Any]]) -> int:
    return max((int(event.get("sequence_no", 0)) for event in events), default=0)


def _event_card(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if event.get("event_type") != "message":
        return None
    event_id = event.get("event_id") or f"seq-{event.get('sequence_no')}"
    return {
        "spec_version": "1.0",
        "card_id": f"card_{_safe_card_id('event', event_id)}",
        "card_type": "user_strategy_description",
        "workshop_id": event["workshop_id"],
        "sequence_no": int(event.get("sequence_no") or 1),
        "source_event_ids": [event_id],
        "status": "informational",
        "title": "Strategy description captured",
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
) -> Dict[str, Any]:
    _assess_blocking_items, _assess_readiness, compute_overall_grade = _readiness_helpers()
    state_map = _state_map_from_snapshot(snapshot)
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
        },
        "created_at": snapshot.get("created_at") or readiness.get("assessed_at") or _ws_utc_now(),
    }


def _next_question_card(
    *,
    session: Dict[str, Any],
    snapshot: Dict[str, Any],
    sequence_no: int,
) -> Optional[Dict[str, Any]]:
    raw = snapshot.get("next_question_json")
    if not isinstance(raw, dict) or not raw:
        return None
    question_id = str(raw.get("question_id") or raw.get("id") or f"q_{snapshot.get('snapshot_id') or sequence_no}")
    question_text = str(raw.get("question") or raw.get("text") or raw.get("prompt") or "Clarify the next strategy decision.")
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
            "question": question_text,
            "why_now": str(raw.get("why_now") or raw.get("reason") or "This answer improves downstream readiness."),
            "score_total": float(raw.get("score_total") or raw.get("score") or 0),
            "answer_options": raw.get("answer_options") or raw.get("options") or [],
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
) -> List[Dict[str, Any]]:
    cards = [card for card in (_event_card(event) for event in events) if card is not None]
    next_seq = _card_sequence_base(events) + 1
    if snapshot:
        cards.append(_completeness_card(
            session=session,
            snapshot=snapshot,
            readiness=readiness,
            sequence_no=next_seq,
        ))
        next_seq += 1
        next_question = _next_question_card(session=session, snapshot=snapshot, sequence_no=next_seq)
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


# --------------------------------------------------------------------------- #
# Request / response models
# --------------------------------------------------------------------------- #

class WorkshopCreateRequest(BaseModel):
    model_config = {"extra": "forbid"}

    initial_message: str = Field(min_length=1)
    title: Optional[str] = None
    strategy_spec_ref: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class WorkshopMessageRequest(BaseModel):
    model_config = {"extra": "forbid"}

    content: str = Field(min_length=1)
    attachment_refs: List[str] = Field(default_factory=list)


class WorkshopReadinessReassessRequest(BaseModel):
    model_config = {"extra": "forbid"}

    force: bool = False


class WorkshopCompletenessSnapshotRequest(BaseModel):
    model_config = {"extra": "forbid"}

    strategy_version_id: Optional[str] = None
    state_map_json: Dict[str, Any] = Field(default_factory=dict)
    blocking_items_json: List[Any] = Field(default_factory=list)
    next_question_json: Dict[str, Any] = Field(default_factory=dict)
    persist_readiness: bool = True


class WorkshopVersionCreateRequest(BaseModel):
    """Create a Registry-owned immutable StrategySpec draft version."""

    model_config = {"extra": "forbid"}

    expected_workshop_version: Optional[int] = Field(default=None, ge=1)
    patch: List[Dict[str, Any]] = Field(min_length=1)
    base_document_sha256: Optional[str] = None
    reason: Optional[str] = None


class WorkshopResearchRunRequest(BaseModel):
    model_config = {"extra": "forbid"}

    research_context: str = Field(min_length=1)
    strategy_version_ref: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    approval_decision_id: str = Field(min_length=1)
    adapter: str = "handoff_only"
    requested_mode: str = "handoff_only"
    dispatch_mode: str = "handoff_only"


class WorkshopConsultationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    consultation_type: str
    subject: str = Field(min_length=1)
    context_refs: List[str] = Field(default_factory=list)


class WorkshopConcludeRequest(BaseModel):
    model_config = {"extra": "forbid"}

    final_version_id: Optional[str] = None
    conclusion_notes: Optional[str] = None
    approval_decision_id: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #

def create_strategy_workshop_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    workshop_store: Any = None,
    private_content_store: Any = None,
    canonical_operations: Any = None,
) -> APIRouter:
    """Build and return the strategy-workshop APIRouter.

    ``workshop_store`` may be injected (e.g. a MemoryWorkshopStore in tests).
    When omitted the store is constructed from AGORA_WORKSHOP_STORE_BACKEND env.
    """
    store = workshop_store if workshop_store is not None else make_workshop_store()
    canonical = (
        canonical_operations
        if canonical_operations is not None
        else WorkshopCanonicalOperations()
    )
    if private_content_store is None:
        private_content_store = MemoryPrivateContentStore(key_provider=EphemeralKeyProvider())
    router = APIRouter(tags=["agora-workshop"])

    # Stores with claim_resumable_command also accept resolve_compensation on
    # complete_command/fail_command, so adopting a lineage source and
    # resolving it happen atomically with the successor's terminal write.
    _supports_atomic_lineage = hasattr(store, "claim_resumable_command")

    def _source_resolution(
        resume: Mapping[str, Any],
        *,
        resolution: str,
        resolved_by: str,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Transactional resolution payload for an adopted lineage source."""
        return {
            "operation": (resume["receipt"] or {}).get("operation"),
            "idempotency_key": str(
                (resume["receipt"] or {}).get("idempotency_key") or ""
            ),
            "resolution": {
                "resolved_at": utc_now(),
                "resolution": resolution,
                "resolved_by_idempotency_key": resolved_by,
                **dict(extra or {}),
            },
        }

    def _source_claim_release(
        resume: Mapping[str, Any],
        *,
        released_by: str,
    ) -> Dict[str, Any]:
        """Release an adoption claim without resolving the source lineage."""
        return {
            "operation": (resume["receipt"] or {}).get("operation"),
            "idempotency_key": str(
                (resume["receipt"] or {}).get("idempotency_key") or ""
            ),
            "resolution": {
                "claimed_by_idempotency_key": None,
                "claimed_at": None,
                "claim_released_at": utc_now(),
                "claim_released_by_idempotency_key": released_by,
            },
        }

    # Lazy import to avoid circular import at module load time
    def _scope(
        authorization: Optional[str],
        x_tenant_id: Optional[str] = None,
        *,
        write: bool = False,
        x_mfa_token: Optional[str] = None,
        mfa_required: bool = False,
    ) -> Any:
        from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
        from ..models import AgoraErrorCode

        try:
            raw_identity = extract_identity(authorization, mfa_token=x_mfa_token)
        except TypeError:
            # Narrow test adapters written before the MFA-bearing factory
            # signature remain supported.  Production assembly accepts the
            # keyword and validates it in the shared auth facade.
            raw_identity = extract_identity(authorization)
        identity = _identity_for_scope(raw_identity)
        require_read_role(identity)
        if write:
            require_write_role(identity)
            if mfa_required and not bool(getattr(identity, "mfa_verified", False)):
                # The explicit header is accepted only for the dev auth stub.
                # Strict JWT/OIDC paths must set mfa_verified in the shared
                # inbound-auth facade after validating the token/claim.
                stub_mfa = bool(
                    str(x_mfa_token or "").strip()
                    and getattr(identity, "token_kind", "") in {"stub", "test"}
                )
                if not stub_mfa:
                    from models import ErrorCode
                    raise bff_error(
                        401,
                        ErrorCode.AUTH_REQUIRED,
                        "MFA verification is required for workshop commands",
                        "MFA_REQUIRED",
                        precondition_failed="mfa_verification",
                        suggestion="Supply a valid X-MFA-Token or MFA-verified identity",
                    )
        try:
            return resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=x_tenant_id,
            )
        except AgoraScopeResolutionError as exc:
            from models import ErrorCode  # BFF top-level models
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise bff_error(
                exc.status_code,
                code,
                exc.message,
                exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            )

    def _scoped_session(workshop_id: str, scope: Any) -> Dict[str, Any]:
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        return session

    def _etag(workshop_id: str, lock_version: int) -> str:
        return f'W/"workshop:{workshop_id}:v{lock_version}"'

    def _project_strategy_version(
        *,
        readback: Mapping[str, Any],
        registry_id: str,
        scope: Any,
        expected_strategy_id: Optional[str] = None,
        expected_workshop_id: Optional[str] = None,
    ) -> tuple[Dict[str, Any], str, str]:
        from services.research.strategy_spec.patching import compute_document_sha256

        entry = readback.get("entry")
        if not isinstance(entry, Mapping):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_READBACK_MISSING",
                status_code=502,
            )
        if str(entry.get("registry_id") or "") != registry_id:
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_REGISTRY_ID_MISMATCH",
                status_code=502,
            )
        strategy_id = str(entry.get("strategy_id") or "")
        if not strategy_id or (
            expected_strategy_id and strategy_id != expected_strategy_id
        ):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_STRATEGY_ID_MISMATCH"
            )
        metadata = entry.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        tenant_id = str(metadata.get("tenant_id") or "")
        owner_user_id = str(metadata.get("owner_user_id") or "")
        if (tenant_id and tenant_id != scope.tenant_id) or (
            owner_user_id and owner_user_id != scope.user_id
        ):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_SCOPE_MISMATCH",
                status_code=403,
            )
        linked_workshop_id = str(metadata.get("workshop_id") or "")
        if (
            linked_workshop_id
            and expected_workshop_id
            and linked_workshop_id != expected_workshop_id
        ):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_WORKSHOP_ID_MISMATCH"
            )
        document = metadata.get("strategy_spec")
        if not isinstance(document, Mapping):
            raise _StrategyVersionProjectionError(
                "STRATEGY_SPEC_DOCUMENT_REQUIRED",
                status_code=502,
            )
        return dict(readback), compute_document_sha256(document), strategy_id

    def _read_strategy_version(
        *,
        registry_id: str,
        scope: Any,
        expected_strategy_id: Optional[str] = None,
        expected_workshop_id: Optional[str] = None,
    ) -> tuple[Dict[str, Any], str, str]:
        readback = canonical.get_strategy_spec(registry_id)
        return _project_strategy_version(
            readback=readback,
            registry_id=registry_id,
            scope=scope,
            expected_strategy_id=expected_strategy_id,
            expected_workshop_id=expected_workshop_id,
        )

    def _request_hash(payload: Mapping[str, Any]) -> str:
        canonical_payload = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical_payload).hexdigest()

    def _require_command_headers(
        *,
        workshop_id: str,
        if_match: Optional[str],
        idempotency_key: Optional[str],
        request_id: Optional[str],
    ) -> tuple[int, str, str]:
        from models import ErrorCode

        if if_match is None:
            raise bff_error(
                428,
                ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for workshop mutations",
                "missing_if_match",
                precondition_failed="if_match",
                suggestion="GET the workshop and retry with its current ETag",
            )
        if not str(idempotency_key or "").strip():
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                precondition_failed="idempotency_key",
            )
        if not str(request_id or "").strip():
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "X-Request-Id header is required",
                "missing_request_id",
                precondition_failed="request_id",
            )
        return (
            _parse_etag_lock_version(if_match, workshop_id),
            str(idempotency_key).strip(),
            str(request_id).strip(),
        )

    def _public_receipt(receipt: Mapping[str, Any]) -> Dict[str, Any]:
        status = str(receipt.get("status") or "")
        result: Dict[str, Any] = {
            "receipt_id": receipt.get("receipt_id") or receipt.get("command_id"),
            "operation": receipt.get("operation"),
            "status": status,
            "command_terminal": status in {"completed", "failed"},
            "request_id": receipt.get("request_id"),
            "trace_id": receipt.get("trace_id"),
            "idempotency_key": receipt.get("idempotency_key"),
            "request_hash": receipt.get("request_hash"),
            "expected_lock_version": receipt.get("expected_lock_version"),
            "resulting_lock_version": (
                receipt.get("resulting_lock_version")
                or receipt.get("admitted_lock_version")
            ),
            "admitted_at": receipt.get("admitted_at"),
            "completed_at": receipt.get("completed_at"),
            "canonical_refs": receipt.get("canonical_refs")
            or receipt.get("canonical_refs_json")
            or {},
        }
        return {key: value for key, value in result.items() if value is not None}

    def _command_response(
        *,
        receipt: Mapping[str, Any],
        resource: Mapping[str, Any],
        session: Mapping[str, Any],
        scope: Any,
        canonical_authority: str,
        response: Response,
    ) -> Dict[str, Any]:
        lock_version = int(
            receipt.get("resulting_lock_version")
            or receipt.get("admitted_lock_version")
            or session.get("lock_version")
            or 1
        )
        value = _etag(str(session["workshop_id"]), lock_version)
        response.headers["ETag"] = value
        no_direct_action = {
            "deployment_triggered": False,
            "order_submitted": False,
            "live_capital_changed": False,
        }
        return {
            "data": {
                "command_receipt": _public_receipt(receipt),
                "resource": dict(resource),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": value,
                "canonical_authority": canonical_authority,
                "no_direct_action": no_direct_action,
            },
        }

    def _raise_admission_failure(
        *,
        workshop_id: str,
        result: Mapping[str, Any],
    ) -> None:
        from models import ErrorCode

        outcome = str(result.get("outcome") or "")
        current_version = int(result.get("current_lock_version") or 1)
        if outcome == "idempotency_conflict":
            raise bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency-Key was already used with a different request",
                "IDEMPOTENCY_REQUEST_HASH_MISMATCH",
                precondition_failed="idempotency_key",
            )
        if outcome in {"scope_mismatch"}:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        if outcome == "not_found":
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Workshop not found",
                workshop_id,
            )
        if outcome == "terminal":
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Workshop is in a terminal state",
                "WORKSHOP_TERMINAL_STATE",
                precondition_failed="workshop_status",
            )
        if outcome == "stale":
            current_etag = _etag(workshop_id, current_version)
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Concurrent modification: ETag mismatch",
                "CONCURRENT_MODIFICATION",
                precondition_failed="if_match",
                details_extra={
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/workshops/{workshop_id}",
                },
            )
        raise bff_error(
            409,
            ErrorCode.RESOURCE_CONFLICT,
            "Workshop command could not be admitted",
            outcome or "COMMAND_ADMISSION_FAILED",
        )

    def _admit_command(
        *,
        workshop_id: str,
        scope: Any,
        operation: str,
        expected_lock_version: int,
        idempotency_key: str,
        request_id: str,
        payload: Mapping[str, Any],
    ) -> tuple[Dict[str, Any], str]:
        payload_hash = _request_hash(payload)
        admission = store.admit_command(
            workshop_id=workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            expected_lock_version=expected_lock_version,
            request_payload=dict(payload),
            request_id=request_id,
            trace_id=request_id,
        )
        outcome = str(admission.get("outcome") or "")
        if outcome not in {"admitted", "replay"}:
            _raise_admission_failure(workshop_id=workshop_id, result=admission)
        receipt = admission.get("receipt")
        if not isinstance(receipt, dict):
            from models import ErrorCode
            raise bff_error(
                500,
                ErrorCode.UPSTREAM_ERROR,
                "Workshop command admission did not return a receipt",
                "COMMAND_RECEIPT_MISSING",
            )
        return receipt, payload_hash

    def _canonical_error(
        *,
        workshop_id: str,
        operation: str,
        scope: Any,
        idempotency_key: str,
        request_hash: str,
        error: CanonicalOperationError,
        compensation: Optional[Mapping[str, Any]] = None,
        resume_digest: Optional[str] = None,
        resume: Optional[Mapping[str, Any]] = None,
    ) -> None:
        from models import ErrorCode

        partial_effects = {
            key: value
            for key, value in {
                # An adopted source's recorded ids stay part of the lineage
                # even when the failing downstream call reports none itself.
                **dict((resume or {}).get("partial_effects") or {}),
                **dict(getattr(error, "partial_effects", None) or {}),
            }.items()
            if value
        }
        compensation_payload = dict(compensation or {})
        resumable = bool(resume_digest) and (bool(partial_effects) or error.retryable)
        if resumable:
            # Durable partial-effect lineage: a new-key retry of the same
            # request body resumes these downstream resources (and reuses the
            # recorded downstream idempotency digest) instead of duplicating.
            compensation_payload["resumable"] = True
            compensation_payload["downstream_idempotency_digest"] = resume_digest
            if partial_effects:
                compensation_payload["partial_effects"] = partial_effects
        fail_kwargs: Dict[str, Any] = {}
        if partial_effects:
            fail_kwargs["canonical_refs"] = partial_effects
        if resume is not None and _supports_atomic_lineage:
            if resumable:
                # The lineage moves onto this failed receipt in the same
                # transaction, so exactly one receipt stays resumable.
                fail_kwargs["resolve_compensation"] = _source_resolution(
                    resume,
                    resolution="superseded",
                    resolved_by=idempotency_key,
                )
            else:
                # This attempt cannot carry the lineage forward; release the
                # claim so the recorded source stays adoptable by a retry.
                fail_kwargs["resolve_compensation"] = _source_claim_release(
                    resume,
                    released_by=idempotency_key,
                )
        store.fail_command(
            workshop_id=workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            failure={
                "authority": error.authority,
                "reason": error.reason,
                "status_code": error.status_code,
                "retryable": error.retryable,
            },
            compensation=compensation_payload,
            **fail_kwargs,
        )
        status_code = 503 if error.retryable else 502
        if error.status_code == 404:
            status_code = 409
        raise bff_error(
            status_code,
            ErrorCode.DEPENDENCY_UNAVAILABLE if error.retryable else ErrorCode.UPSTREAM_ERROR,
            "Canonical downstream operation failed",
            error.reason,
            precondition_failed=error.authority,
            suggestion=(
                "Refetch the workshop and retry with a new command key; "
                "recorded partial downstream effects will be resumed, not duplicated"
                if resumable
                else "Refetch the workshop before retrying with a new command key"
            ),
        )

    def _fail_domain_command(
        *,
        workshop_id: str,
        operation: str,
        scope: Any,
        idempotency_key: str,
        request_hash: str,
        status_code: int,
        code: Any,
        message: str,
        reason: str,
        precondition_failed: str,
        resolve_source: Optional[Mapping[str, Any]] = None,
    ) -> None:
        fail_kwargs: Dict[str, Any] = {}
        if resolve_source is not None and _supports_atomic_lineage:
            fail_kwargs["resolve_compensation"] = dict(resolve_source)
        store.fail_command(
            workshop_id=workshop_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            operation=operation,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            failure={
                "reason": reason,
                "status_code": status_code,
                "precondition_failed": precondition_failed,
            },
            compensation={"workshop_effect": "none"},
            **fail_kwargs,
        )
        raise bff_error(
            status_code,
            code,
            message,
            reason,
            precondition_failed=precondition_failed,
        )

    @staticmethod
    def _receipt_result(receipt: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        value = receipt.get("result")
        if value is None:
            value = receipt.get("result_json")
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return None
        return dict(value) if isinstance(value, Mapping) else None

    def _require_replayable_receipt(
        *,
        receipt: Mapping[str, Any],
        workshop_id: str,
    ) -> Optional[Dict[str, Any]]:
        from models import ErrorCode

        status = str(receipt.get("status") or "")
        if status == "completed":
            result = _receipt_result(receipt)
            if result is None:
                raise bff_error(
                    500,
                    ErrorCode.UPSTREAM_ERROR,
                    "Completed command receipt is missing its canonical result",
                    "COMMAND_RESULT_MISSING",
                )
            return result
        if status == "failed":
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "The prior command attempt failed and was compensated",
                "COMMAND_PREVIOUSLY_FAILED",
                precondition_failed="idempotency_key",
                suggestion="GET the workshop and retry with its latest ETag and a new Idempotency-Key",
                details_extra={"latest_href": f"/bff/agora/workshops/{workshop_id}"},
            )
        return None

    def _complete_or_raise(
        *,
        workshop_id: str,
        operation: str,
        scope: Any,
        idempotency_key: str,
        request_hash: str,
        result: Mapping[str, Any],
        canonical_refs: Mapping[str, Any],
        version_link: Optional[Mapping[str, Any]] = None,
        session_updates: Optional[Mapping[str, Any]] = None,
        event: Optional[Mapping[str, Any]] = None,
        downstream_digest: Optional[str] = None,
        resume: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        from models import ErrorCode

        def _commit_failure_compensation() -> Dict[str, Any]:
            # The canonical downstream effect exists; only the local
            # projection commit failed.  With a downstream digest the
            # compensation is resumable: a new-key retry adopts the recorded
            # downstream resources instead of dispatching duplicates.
            compensation: Dict[str, Any] = {
                "required": True,
                "canonical_refs": dict(canonical_refs),
            }
            if downstream_digest:
                compensation["resumable"] = True
                compensation["downstream_idempotency_digest"] = downstream_digest
                compensation["partial_effects"] = {
                    key: value
                    for key, value in dict(canonical_refs).items()
                    if value
                }
            return compensation

        def _lineage_kwargs(resolution: str) -> Dict[str, Any]:
            # Adopted-source resolution rides the successor's terminal write
            # so the transaction commits both or neither.
            if resume is None or not _supports_atomic_lineage:
                return {}
            if resolution == "superseded" and not downstream_digest:
                # This failed receipt carries no resumable lineage of its
                # own; release the claim so the source stays adoptable.
                return {
                    "resolve_compensation": _source_claim_release(
                        resume,
                        released_by=idempotency_key,
                    )
                }
            return {
                "resolve_compensation": _source_resolution(
                    resume,
                    resolution=resolution,
                    resolved_by=idempotency_key,
                )
            }

        try:
            completed = store.complete_command(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                result=dict(result),
                canonical_refs=dict(canonical_refs),
                version_link=dict(version_link) if version_link else None,
                session_updates=dict(session_updates or {}),
                event=dict(event) if event else None,
                **_lineage_kwargs("resumed"),
            )
        except Exception as exc:
            try:
                store.fail_command(
                    workshop_id=workshop_id,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    failure={"reason": "WORKSHOP_COMMIT_EXCEPTION"},
                    compensation=_commit_failure_compensation(),
                    **_lineage_kwargs("superseded"),
                )
            except Exception:
                pass
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Workshop command store could not commit canonical readback",
                "WORKSHOP_COMMIT_EXCEPTION",
                precondition_failed="workshop_command_store",
            ) from exc
        if str(completed.get("outcome") or "") not in {"completed", "replay"}:
            store.fail_command(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                failure={"reason": "WORKSHOP_COMMIT_FAILED"},
                compensation=_commit_failure_compensation(),
                **_lineage_kwargs("superseded"),
            )
            raise bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Canonical effect was recorded but workshop projection could not commit",
                "WORKSHOP_COMMIT_FAILED",
                precondition_failed="workshop_command_store",
            )
        receipt = completed.get("receipt")
        if not isinstance(receipt, dict):
            raise bff_error(
                500,
                ErrorCode.UPSTREAM_ERROR,
                "Workshop command completion did not return a receipt",
                "COMMAND_RECEIPT_MISSING",
            )
        return receipt

    def _resume_context(
        *,
        workshop_id: str,
        scope: Any,
        operation: str,
        request_hash: str,
        claimed_by: str,
    ) -> Optional[Dict[str, Any]]:
        """Durable partial-effect lineage of the same logical request.

        Matching by ``request_hash`` restricts resume to a retry of the same
        request body; a genuinely different request never adopts another
        command's downstream resources.  With a claim-capable store the find
        and the claim are one atomic step, so concurrent new-key retries can
        never both adopt the same source: the loser opens fresh downstream
        resources under its own digest instead.
        """

        if _supports_atomic_lineage:
            receipt = store.claim_resumable_command(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                request_hash=request_hash,
                claimed_by_idempotency_key=claimed_by,
            )
        elif hasattr(store, "find_resumable_command"):
            receipt = store.find_resumable_command(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                request_hash=request_hash,
            )
        else:
            return None
        if not receipt:
            return None
        compensation = receipt.get("compensation") or {}
        return {
            "receipt": receipt,
            "digest": str(compensation.get("downstream_idempotency_digest") or ""),
            "partial_effects": {
                key: value
                for key, value in dict(
                    compensation.get("partial_effects") or {}
                ).items()
                if value
            },
        }

    def _resolve_resumed_compensation(
        *,
        workshop_id: str,
        scope: Any,
        operation: str,
        resume: Optional[Dict[str, Any]],
        resolved_by_idempotency_key: str,
    ) -> None:
        # Legacy best-effort fallback only: an atomic-lineage store resolves
        # the source inside the successor's completion transaction instead.
        if (
            resume is None
            or _supports_atomic_lineage
            or not hasattr(store, "resolve_command_compensation")
        ):
            return
        try:
            store.resolve_command_compensation(
                workshop_id=workshop_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                operation=operation,
                idempotency_key=str(resume["receipt"].get("idempotency_key") or ""),
                resolution={
                    "resolved_at": utc_now(),
                    "resolution": "resumed",
                    "resolved_by_idempotency_key": resolved_by_idempotency_key,
                },
            )
        except Exception:
            # Resolution is bookkeeping over an already-failed receipt; the
            # new completed receipt remains the authoritative delivery truth.
            pass

    @staticmethod
    def _approval_actor(record: Mapping[str, Any]) -> str:
        value = (
            record.get("reviewer")
            or record.get("approver")
            or record.get("decided_by")
            or record.get("actor_id")
        )
        if isinstance(value, Mapping):
            value = value.get("actor_id") or value.get("id")
        return str(value or "").strip()

    def _require_approval(
        *,
        approval_decision_id: str,
        workshop_id: str,
        version_id: str,
        session: Mapping[str, Any],
        scope: Any,
    ) -> Dict[str, Any]:
        from models import ErrorCode

        try:
            decision = canonical.get_approval_decision(approval_decision_id)
        except CanonicalOperationError as exc:
            status_code = 503 if exc.retryable else 409
            code = ErrorCode.DEPENDENCY_UNAVAILABLE if exc.retryable else ErrorCode.HUMAN_GATE_PENDING
            raise bff_error(
                status_code,
                code,
                "Authoritative approval is required",
                exc.reason,
                precondition_failed="approval_decision_id",
            ) from exc
        outcome_values = [
            decision[field]
            for field in ("decision", "outcome")
            if field in decision
        ]
        state_values = [
            decision[field]
            for field in ("decision_state", "state")
            if field in decision
        ]
        if not outcome_values or any(
            value not in ("approved", "approved_with_conditions")
            for value in outcome_values
        ):
            raise bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Approval decision is not approved",
                "APPROVAL_NOT_APPROVED",
                precondition_failed="approval_decision_id",
            )
        if not state_values or any(value != "decided" for value in state_values):
            raise bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Approval decision is not terminal",
                "APPROVAL_NOT_DECIDED",
                precondition_failed="approval_decision_id",
            )
        tenant_id = str(decision.get("tenant_id") or "").strip()
        owner_user_id = str(
            decision.get("owner_user_id") or decision.get("user_id") or ""
        ).strip()
        if not tenant_id or tenant_id != scope.tenant_id:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Approval decision is outside the workshop tenant",
                "APPROVAL_TENANT_MISMATCH",
                precondition_failed="approval_scope",
            )
        if not owner_user_id or owner_user_id != scope.user_id:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "Approval decision is outside the workshop user scope",
                "APPROVAL_USER_MISMATCH",
                precondition_failed="approval_scope",
            )
        target_id = str(decision.get("target_id") or "").strip()
        target_version = str(decision.get("target_version") or "").strip()
        target_type = str(decision.get("target_type") or "").strip().lower()
        if target_type != "strategy_workshop":
            raise bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Approval decision target type is not a Strategy Workshop",
                "APPROVAL_TARGET_TYPE_MISMATCH",
                precondition_failed="approval_binding",
            )
        if target_id != workshop_id or target_version != version_id:
            raise bff_error(
                409,
                ErrorCode.HUMAN_GATE_PENDING,
                "Approval decision is not bound to this workshop version",
                "APPROVAL_TARGET_MISMATCH",
                precondition_failed="approval_binding",
            )
        requester = str(session.get("user_id") or "").strip()
        approver = _approval_actor(decision)
        if not requester or not approver or requester == approver:
            raise bff_error(
                403,
                ErrorCode.FORBIDDEN,
                "A distinct approver is required",
                "APPROVAL_DISTINCT_ACTOR_REQUIRED",
                precondition_failed="distinct_approver",
            )
        return {
            "approval_decision_id": approval_decision_id,
            "requested_by": requester,
            "approved_by": approver,
            "distinct_actors": True,
            "decision": "approved",
        }

    def _readiness_from_store_or_state(session: Dict[str, Any]) -> Dict[str, Any]:
        latest = (
            store.get_latest_readiness_assessment(session["workshop_id"])
            if hasattr(store, "get_latest_readiness_assessment")
            else None
        )
        if latest is not None:
            return latest
        events = store.list_events(session["workshop_id"])
        snapshot = store.get_latest_completeness_snapshot(session["workshop_id"])
        return _build_readiness_assessment(
            session=session,
            events=events,
            snapshot=snapshot,
            assessed_at=utc_now(),
            assessment_version=1,
        )

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops — list user-scoped workshop sessions
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops")
    def list_workshops(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        status: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        sessions, next_cursor = store.list_sessions(
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
            status=status,
            cursor=cursor,
            limit=limit,
        )
        return {
            "data": sessions,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "next_cursor": next_cursor,
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops — create workshop session
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops", status_code=201)
    def create_workshop(
        body: WorkshopCreateRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id, write=True)
        initial_registry_id = str(body.strategy_spec_ref or "").strip()
        initial_strategy_id: Optional[str] = None
        if initial_registry_id:
            try:
                _, _, initial_strategy_id = _read_strategy_version(
                    registry_id=initial_registry_id,
                    scope=scope,
                )
            except CanonicalOperationError as exc:
                from models import ErrorCode

                if exc.status_code == 404:
                    status_code = 404
                    code = ErrorCode.RESOURCE_NOT_FOUND
                elif exc.retryable:
                    status_code = 503
                    code = ErrorCode.DEPENDENCY_UNAVAILABLE
                else:
                    status_code = 502
                    code = ErrorCode.UPSTREAM_ERROR
                raise bff_error(
                    status_code,
                    code,
                    "Referenced StrategySpec is unavailable",
                    exc.reason,
                    precondition_failed="strategy_spec_ref",
                ) from exc
            except _StrategyVersionProjectionError as exc:
                from models import ErrorCode

                code = (
                    ErrorCode.FORBIDDEN
                    if exc.status_code == 403
                    else ErrorCode.UPSTREAM_ERROR
                    if exc.status_code >= 500
                    else ErrorCode.RESOURCE_CONFLICT
                )
                raise bff_error(
                    exc.status_code,
                    code,
                    "Referenced StrategySpec projection is inconsistent",
                    exc.reason,
                    precondition_failed="strategy_spec_ref",
                ) from exc
        # Idempotency-Key is mandatory for all write operations on this endpoint.
        if idempotency_key is None:
            from models import ErrorCode
            raise bff_error(
                400, ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        # Reject duplicate keys for the same user+tenant+endpoint.
        idem_scope = f"{scope.user_id}:{scope.tenant_id}:POST:/bff/agora/workshops"
        if hasattr(store, "check_and_record_idempotency_key"):
            if store.check_and_record_idempotency_key(idem_scope, idempotency_key):
                from models import ErrorCode
                raise bff_error(
                    409, ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Duplicate Idempotency-Key", idempotency_key,
                )
        workshop_id = str(uuid.uuid4())
        session = store.create_session({
            "workshop_id": workshop_id,
            "tenant_id": scope.tenant_id,
            "user_id": scope.user_id,
            "strategy_id": initial_strategy_id,
            "active_strategy_spec_registry_id": initial_registry_id or None,
            "status": "open",
        })
        try:
            if private_content_store is None:
                raise bff_error(503, "PRIVATE_CONTENT_STORE_UNAVAILABLE",
                                "Private content store is not configured", "private_content_store")
            initial_event_id = str(uuid.uuid4())
            private = private_content_store.put(
                tenant_id=scope.tenant_id, owner_user_id=scope.user_id,
                workshop_id=workshop_id, event_id=initial_event_id,
                content_type="text/plain", plaintext=body.initial_message.encode("utf-8"),
                retention_class="workshop_default", idempotency_key=idempotency_key,
            )
            try:
                store.create_event({
                    "event_id": initial_event_id,
                    "workshop_id": workshop_id,
                    "actor_type": "operator",
                    "event_type": "message",
                    "private_content_ref": private.private_content_ref,
                    "redacted_summary": "Private workshop message",
                })
            except Exception:
                private_content_store.discard_failed_write(
                    private_content_ref=private.private_content_ref,
                    tenant_id=scope.tenant_id,
                    owner_user_id=scope.user_id,
                )
                raise
        except Exception:
            store.rollback_create_session(
                workshop_id,
                idempotency_scope=idem_scope,
                idempotency_key=idempotency_key,
            )
            raise
        return {
            "data": session,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id} — get workshop with ETag
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}")
    def get_workshop(
        workshop_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        # Verify ownership
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        lock_version = session.get("lock_version", 1)
        # ETag format: W/"workshop:{id}:v{lock_version}" per contract §B Concurrency
        etag = f'W/"workshop:{workshop_id}:v{lock_version}"'
        response.headers["ETag"] = etag
        return {
            "data": session,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": etag,
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops/{workshop_id}/messages — append event
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops/{workshop_id}/messages", status_code=202)
    def post_workshop_message(
        workshop_id: str,
        body: WorkshopMessageRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id, write=True)
        # If-Match is mandatory: mutations without a precondition are rejected (RFC 6585 §428).
        if if_match is None:
            from models import ErrorCode
            raise bff_error(
                428, ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for workshop mutations",
                "missing_if_match",
                suggestion="GET the workshop first and supply the returned ETag in If-Match",
            )
        # Idempotency-Key is mandatory for all write operations on this endpoint.
        if idempotency_key is None:
            from models import ErrorCode
            raise bff_error(
                400, ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        # Reject duplicate keys for the same user+workshop+endpoint.
        if hasattr(store, "check_and_record_idempotency_key"):
            idem_scope = (
                f"{scope.user_id}:{scope.tenant_id}:{workshop_id}"
                f":POST:/bff/agora/workshops/messages"
            )
            if store.check_and_record_idempotency_key(idem_scope, idempotency_key):
                from models import ErrorCode
                raise bff_error(
                    409, ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Duplicate Idempotency-Key", idempotency_key,
                )
        event_id = str(uuid.uuid4())
        private = private_content_store.put(
            tenant_id=scope.tenant_id, owner_user_id=scope.user_id,
            workshop_id=workshop_id, event_id=event_id, content_type="text/plain",
            plaintext=body.content.encode("utf-8"), retention_class="workshop_default",
            idempotency_key=idempotency_key,
        ) if private_content_store is not None else None
        if private is None:
            raise bff_error(503, "PRIVATE_CONTENT_STORE_UNAVAILABLE",
                            "Private content store is not configured", "private_content_store")
        expected_version = _parse_etag_lock_version(if_match, workshop_id)
        # Atomic CAS: compare expected lock_version, append event, bump version —
        # all in one store transaction so concurrent same-ETag writes both cannot succeed.
        event, _new_version = store.append_event_cas(workshop_id, expected_version, {
            "event_id": event_id,
            "workshop_id": workshop_id,
            "actor_type": "operator",
            "event_type": "message",
            "private_content_ref": private.private_content_ref,
            "redacted_summary": "Private workshop message",
            "payload_refs_json": body.attachment_refs or None,
        })
        if event is None:
            from models import ErrorCode
            private_content_store.discard_failed_write(
                private_content_ref=private.private_content_ref,
                tenant_id=scope.tenant_id,
                owner_user_id=scope.user_id,
            )
            # _new_version is the actual current lock_version (or None if not found)
            current_lock_version = _new_version if _new_version is not None else 1
            current_etag = f'W/"workshop:{workshop_id}:v{current_lock_version}"'
            raise bff_error(
                409, ErrorCode.RESOURCE_CONFLICT,
                "Concurrent modification: ETag mismatch",
                f"If-Match {if_match!r} does not match current ETag {current_etag!r}",
                details_extra={
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/workshops/{workshop_id}",
                },
            )
        # Push SSE ack to any open workshop streams (§8.2 audit: trace_id, sequence_no)
        _ws_publish(
            workshop_id,
            "workshop.message.ack",
            {
                "event_id": event["event_id"],
                "sequence_no": event["sequence_no"],
                "trace_id": event.get("trace_id"),
            },
            utc_now_fn=utc_now,
        )
        return {
            "data": {"event_id": event["event_id"], "sequence_no": event["sequence_no"]},
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id}/events — list events
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}/events")
    def list_workshop_events(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        after_sequence: Optional[int] = Query(default=None, ge=0),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        events = store.list_events(workshop_id, after_sequence=after_sequence)
        return {
            "data": events,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id}/completeness — latest snapshot
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}/completeness")
    def get_workshop_completeness(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )
        snapshot = store.get_latest_completeness_snapshot(workshop_id)
        return {
            "data": snapshot,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops/{workshop_id}/completeness — persist snapshot
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops/{workshop_id}/completeness", status_code=201)
    def create_workshop_completeness(
        workshop_id: str,
        response: Response,
        body: WorkshopCompletenessSnapshotRequest,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id, write=True)
        session = _scoped_session(workshop_id, scope)
        if if_match is None:
            from models import ErrorCode
            raise bff_error(
                428,
                ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for workshop completeness updates",
                "missing_if_match",
                suggestion="GET the workshop first and supply the returned ETag in If-Match",
            )
        if idempotency_key is None:
            from models import ErrorCode
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        expected_version = _parse_etag_lock_version(if_match, workshop_id)
        current_version = int(session.get("lock_version", 1))
        if expected_version != current_version:
            from models import ErrorCode
            current_etag = f'W/"workshop:{workshop_id}:v{current_version}"'
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Concurrent modification: ETag mismatch",
                f"If-Match {if_match!r} does not match current ETag {current_etag!r}",
                details_extra={
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/workshops/{workshop_id}",
                },
            )
        if hasattr(store, "check_and_record_idempotency_key"):
            idem_scope = (
                f"{scope.user_id}:{scope.tenant_id}:{workshop_id}"
                f":POST:/bff/agora/workshops/completeness"
            )
            if store.check_and_record_idempotency_key(idem_scope, idempotency_key):
                from models import ErrorCode
                raise bff_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Duplicate Idempotency-Key",
                    idempotency_key,
                )
        if not hasattr(store, "create_completeness_snapshot"):
            from models import ErrorCode
            raise bff_error(
                501,
                ErrorCode.NOT_IMPLEMENTED,
                "Workshop store does not support completeness snapshots",
                "completeness_store_unavailable",
            )

        strategy_version_id = (
            body.strategy_version_id
            or session.get("selected_version_id")
            or session.get("active_strategy_spec_registry_id")
            or session.get("strategy_id")
            or workshop_id
        )
        snapshot = store.create_completeness_snapshot({
            "workshop_id": workshop_id,
            "strategy_version_id": strategy_version_id,
            "state_map_json": body.state_map_json,
            "blocking_items_json": body.blocking_items_json,
            "next_question_json": body.next_question_json,
        })
        events = store.list_events(workshop_id)
        latest = (
            store.get_latest_readiness_assessment(workshop_id)
            if hasattr(store, "get_latest_readiness_assessment")
            else None
        )
        next_assessment_version = int((latest or {}).get("assessment_version", 0)) + 1
        assessment = _build_readiness_assessment(
            session=session,
            events=events,
            snapshot=snapshot,
            assessed_at=utc_now(),
            assessment_version=next_assessment_version,
        )
        stored_assessment = (
            store.create_readiness_assessment(assessment)
            if body.persist_readiness and hasattr(store, "create_readiness_assessment")
            else assessment
        )
        new_lock_version = store.update_session_lock_version(workshop_id)
        new_etag = f'W/"workshop:{workshop_id}:v{new_lock_version}"'
        response.headers["ETag"] = new_etag
        _ws_publish(
            workshop_id,
            "workshop.completeness.updated",
            {
                "snapshot_id": snapshot["snapshot_id"],
                "strategy_version_id": snapshot.get("strategy_version_id"),
            },
            utc_now_fn=utc_now,
        )
        _ws_publish(
            workshop_id,
            "workshop.readiness.updated",
            {
                "assessment_id": stored_assessment["assessment_id"],
                "assessment_version": stored_assessment["assessment_version"],
                "highest_ready_gate": stored_assessment.get("highest_ready_gate"),
            },
            utc_now_fn=utc_now,
        )
        return {
            "data": {
                "snapshot": snapshot,
                "readiness": stored_assessment,
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": new_etag,
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id}/cards — typed live cards
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}/cards")
    def list_workshop_cards(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        after_sequence: Optional[int] = Query(default=None, ge=0),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = _scoped_session(workshop_id, scope)
        events = store.list_events(workshop_id)
        snapshot = store.get_latest_completeness_snapshot(workshop_id)
        readiness = _readiness_from_store_or_state(session)
        projected_cards = _build_workshop_cards(
            session=session,
            events=events,
            snapshot=snapshot,
            readiness=readiness,
        )
        stored_cards = (
            store.list_workshop_cards(workshop_id)
            if hasattr(store, "list_workshop_cards")
            else []
        )
        cards = _merge_cards(stored_cards, projected_cards)
        if after_sequence is not None:
            cards = [card for card in cards if int(card.get("sequence_no", 0)) > after_sequence]
        return {
            "data": cards,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "total": len(cards),
            },
        }

    # ------------------------------------------------------------------ #
    # GET /bff/agora/workshops/{workshop_id}/readiness — latest readiness
    # ------------------------------------------------------------------ #
    @router.get("/bff/agora/workshops/{workshop_id}/readiness")
    def get_workshop_readiness(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = _scoped_session(workshop_id, scope)
        readiness = _readiness_from_store_or_state(session)
        return {
            "data": readiness,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops/{workshop_id}/readiness/reassess
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops/{workshop_id}/readiness/reassess", status_code=202)
    def reassess_workshop_readiness(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopReadinessReassessRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        del body
        scope = _scope(authorization, x_tenant_id, write=True)
        session = _scoped_session(workshop_id, scope)
        if if_match is None:
            from models import ErrorCode
            raise bff_error(
                428,
                ErrorCode.PRECONDITION_FAILED,
                "If-Match header is required for workshop readiness reassessment",
                "missing_if_match",
                suggestion="GET the workshop first and supply the returned ETag in If-Match",
            )
        if idempotency_key is None:
            from models import ErrorCode
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        expected_version = _parse_etag_lock_version(if_match, workshop_id)
        current_version = int(session.get("lock_version", 1))
        if expected_version != current_version:
            from models import ErrorCode
            current_etag = f'W/"workshop:{workshop_id}:v{current_version}"'
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Concurrent modification: ETag mismatch",
                f"If-Match {if_match!r} does not match current ETag {current_etag!r}",
                details_extra={
                    "current_etag": current_etag,
                    "latest_href": f"/bff/agora/workshops/{workshop_id}",
                },
            )
        if hasattr(store, "check_and_record_idempotency_key"):
            idem_scope = (
                f"{scope.user_id}:{scope.tenant_id}:{workshop_id}"
                f":POST:/bff/agora/workshops/readiness/reassess"
            )
            if store.check_and_record_idempotency_key(idem_scope, idempotency_key):
                from models import ErrorCode
                raise bff_error(
                    409,
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "Duplicate Idempotency-Key",
                    idempotency_key,
                )

        events = store.list_events(workshop_id)
        snapshot = store.get_latest_completeness_snapshot(workshop_id)
        latest = (
            store.get_latest_readiness_assessment(workshop_id)
            if hasattr(store, "get_latest_readiness_assessment")
            else None
        )
        next_assessment_version = int((latest or {}).get("assessment_version", 0)) + 1
        assessment = _build_readiness_assessment(
            session=session,
            events=events,
            snapshot=snapshot,
            assessed_at=utc_now(),
            assessment_version=next_assessment_version,
        )
        stored_assessment = (
            store.create_readiness_assessment(assessment)
            if hasattr(store, "create_readiness_assessment")
            else assessment
        )
        new_lock_version = store.update_session_lock_version(workshop_id)
        new_etag = f'W/"workshop:{workshop_id}:v{new_lock_version}"'
        response.headers["ETag"] = new_etag
        _ws_publish(
            workshop_id,
            "workshop.readiness.updated",
            {
                "assessment_id": stored_assessment["assessment_id"],
                "assessment_version": stored_assessment["assessment_version"],
                "highest_ready_gate": stored_assessment.get("highest_ready_gate"),
            },
            utc_now_fn=utc_now,
        )
        return {
            "data": stored_assessment,
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "etag": new_etag,
            },
        }

    # ------------------------------------------------------------------ #
    # POST /bff/agora/workshops/{workshop_id}/reconstruct
    # ------------------------------------------------------------------ #
    @router.post("/bff/agora/workshops/{workshop_id}/reconstruct")
    def reconstruct_workshop_strategy(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = _scoped_session(workshop_id, scope)
        events = store.list_events(workshop_id)
        sequence_no = max((int(e.get("sequence_no", 0)) for e in events), default=0)
        messages_content: List[str] = []
        for e in events:
            if e.get("event_type") == "message":
                msg = e.get("redacted_summary") or e.get("content") or ""
                if msg:
                    messages_content.append(str(msg))
        result = reconstruct_strategy_from_events(
            workshop_id=workshop_id,
            sequence_no=sequence_no,
            events=events,
            messages_content=messages_content,
        )
        return {
            "data": result.model_dump(),
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
            },
        }

    @router.get("/bff/agora/workshops/{workshop_id}/versions")
    def list_workshop_versions(
        workshop_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        scope = _scope(authorization, x_tenant_id)
        session = _scoped_session(workshop_id, scope)
        etag = _etag(workshop_id, int(session.get("lock_version") or 1))
        response.headers["ETag"] = etag
        readbacks: Dict[str, tuple[Dict[str, Any], str, str]] = {}
        active_registry_id = str(
            session.get("active_strategy_spec_registry_id") or ""
        )
        if active_registry_id:
            try:
                active_projection = _read_strategy_version(
                    registry_id=active_registry_id,
                    scope=scope,
                    expected_strategy_id=str(session.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
                readbacks[active_registry_id] = active_projection
                active_readback, active_digest, active_strategy_id = active_projection
                store.ensure_current_version_link(
                    workshop_id=workshop_id,
                    strategy_id=active_strategy_id,
                    strategy_spec_registry_id=active_registry_id,
                    document_sha256=active_digest,
                )
            except CanonicalOperationError as exc:
                from models import ErrorCode
                raise bff_error(
                    503 if exc.retryable else 502,
                    ErrorCode.DEPENDENCY_UNAVAILABLE if exc.retryable else ErrorCode.UPSTREAM_ERROR,
                    "StrategySpec version readback is unavailable",
                    exc.reason,
                    precondition_failed="strategy_registry",
                ) from exc
            except _StrategyVersionProjectionError as exc:
                from models import ErrorCode
                code = (
                    ErrorCode.FORBIDDEN
                    if exc.status_code == 403
                    else ErrorCode.UPSTREAM_ERROR
                    if exc.status_code >= 500
                    else ErrorCode.RESOURCE_CONFLICT
                )
                raise bff_error(
                    exc.status_code,
                    code,
                    "StrategySpec version projection is inconsistent",
                    exc.reason,
                    precondition_failed="strategy_version_projection",
                ) from exc
            except WorkshopVersionProjectionConflict as exc:
                from models import ErrorCode
                raise bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "Workshop version projection is inconsistent",
                    "WORKSHOP_VERSION_PROJECTION_CONFLICT",
                    precondition_failed="workshop_version_projection",
                ) from exc

        # Backfill is additive and leaves lock_version/ETag unchanged.  Refresh
        # the session so the response includes deterministic selected pointers.
        session = store.get_session(workshop_id) or session
        versions: List[Dict[str, Any]] = []
        for link in store.list_version_links(workshop_id):
            registry_id = str(link.get("strategy_spec_registry_id") or "")
            try:
                projected = readbacks.get(registry_id) or _read_strategy_version(
                    registry_id=registry_id,
                    scope=scope,
                    expected_strategy_id=str(link.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
                readback, digest, strategy_id = projected
                link = store.ensure_version_link_digest(
                    workshop_id=workshop_id,
                    workshop_version_id=str(link["workshop_version_id"]),
                    strategy_id=strategy_id,
                    document_sha256=digest,
                )
            except CanonicalOperationError as exc:
                from models import ErrorCode
                raise bff_error(
                    503 if exc.retryable else 502,
                    ErrorCode.DEPENDENCY_UNAVAILABLE if exc.retryable else ErrorCode.UPSTREAM_ERROR,
                    "StrategySpec version readback is unavailable",
                    exc.reason,
                    precondition_failed="strategy_registry",
                ) from exc
            except _StrategyVersionProjectionError as exc:
                from models import ErrorCode
                code = (
                    ErrorCode.FORBIDDEN
                    if exc.status_code == 403
                    else ErrorCode.UPSTREAM_ERROR
                    if exc.status_code >= 500
                    else ErrorCode.RESOURCE_CONFLICT
                )
                raise bff_error(
                    exc.status_code,
                    code,
                    "StrategySpec version projection is inconsistent",
                    exc.reason,
                    precondition_failed="strategy_version_projection",
                ) from exc
            except WorkshopVersionProjectionConflict as exc:
                from models import ErrorCode
                raise bff_error(
                    409,
                    ErrorCode.RESOURCE_CONFLICT,
                    "Workshop version projection is inconsistent",
                    "WORKSHOP_VERSION_PROJECTION_CONFLICT",
                    precondition_failed="workshop_version_projection",
                ) from exc
            versions.append({"version": link, "strategy_spec": readback})
        return {
            "data": {
                "versions": versions,
                "selected_version_id": session.get("selected_version_id"),
                "active_strategy_spec_registry_id": session.get(
                    "active_strategy_spec_registry_id"
                ),
            },
            "meta": {
                "snapshot_at": utc_now(),
                "capability": "agora.workshop.v1",
                "audience": f"tenant:{scope.tenant_id}:user:{scope.user_id}",
                "request_id": x_request_id,
                "canonical_authority": "strategy_registry",
                "etag": etag,
                "no_direct_action": {
                    "deployment_triggered": False,
                    "order_submitted": False,
                    "live_capital_changed": False,
                },
            },
        }

    @router.post("/bff/agora/workshops/{workshop_id}/versions", status_code=201)
    def create_workshop_version(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopVersionCreateRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from models import ErrorCode
        from services.research.strategy_spec.models import validate_strategy_spec_payload
        from services.research.strategy_spec.patching import PatchError, apply_patch_validated

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        if body is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "VersionCreateRequest body is required",
                "REQUEST_BODY_REQUIRED",
                precondition_failed="request_body",
            )
        session = _scoped_session(workshop_id, scope)
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        if (
            body.expected_workshop_version is not None
            and body.expected_workshop_version != expected_version
        ):
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                "Body and If-Match workshop versions disagree",
                "EXPECTED_WORKSHOP_VERSION_MISMATCH",
                precondition_failed="expected_workshop_version",
            )
        request_payload = body.model_dump(mode="json")
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="create_version",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="strategy_registry",
                response=response,
            )

        base_registry_id = str(session.get("active_strategy_spec_registry_id") or "")
        if not base_registry_id:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.PRECONDITION_FAILED,
                message="Workshop has no active StrategySpec",
                reason="ACTIVE_STRATEGY_SPEC_REQUIRED",
                precondition_failed="active_strategy_spec_registry_id",
            )
        try:
            base_readback, base_document_sha256, base_strategy_id = (
                _read_strategy_version(
                    registry_id=base_registry_id,
                    scope=scope,
                    expected_strategy_id=str(session.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
            )
            base_link = store.ensure_current_version_link(
                workshop_id=workshop_id,
                strategy_id=base_strategy_id,
                strategy_spec_registry_id=base_registry_id,
                document_sha256=base_document_sha256,
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
            )
        except _StrategyVersionProjectionError as exc:
            code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else ErrorCode.UPSTREAM_ERROR
                if exc.status_code >= 500
                else ErrorCode.RESOURCE_CONFLICT
            )
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=exc.status_code,
                code=code,
                message="StrategySpec version projection is inconsistent",
                reason=exc.reason,
                precondition_failed="strategy_version_projection",
            )
        except WorkshopVersionProjectionConflict:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Workshop version projection is inconsistent",
                reason="WORKSHOP_VERSION_PROJECTION_CONFLICT",
                precondition_failed="workshop_version_projection",
            )
        base_entry = dict(base_readback["entry"])
        base_doc = dict((base_entry.get("metadata") or {}).get("strategy_spec") or {})
        if not base_doc:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.PRECONDITION_FAILED,
                message="Active Registry entry has no inline StrategySpec document",
                reason="STRATEGY_SPEC_DOCUMENT_REQUIRED",
                precondition_failed="strategy_spec",
            )
        try:
            patched, patched_document_sha256 = apply_patch_validated(
                base_doc,
                body.patch,
                expected_base_sha256=body.base_document_sha256,
            )
            version_parts = [int(part) for part in str(base_entry.get("version") or "").split(".")]
            if len(version_parts) != 3:
                raise ValueError("Registry StrategySpec version must be semantic x.y.z")
            version_parts[2] += 1
            next_version = ".".join(str(part) for part in version_parts)
            # StrategySpec.spec_version is the document-schema version (currently
            # 1.0), not the Registry artifact semver.  The immutable Registry
            # entry's ``version`` below advances while the document contract
            # version remains unchanged.
            validate_strategy_spec_payload(patched)
        except (PatchError, ValueError) as exc:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=422,
                code=ErrorCode.VALIDATION_FAILED,
                message="StrategySpec patch is invalid",
                reason=getattr(exc, "error_code", "PATCH_VALIDATION_FAILED"),
                precondition_failed="patch",
            )
        digest = hashlib.sha256(
            f"{workshop_id}:{command_key}".encode("utf-8")
        ).hexdigest()[:20]
        registry_id = f"reg-ws-{digest}"
        workshop_version_id = f"wsv-{digest}"
        strategy_id = base_strategy_id
        try:
            registry_readback = canonical.create_strategy_spec(
                {
                    "registry_id": registry_id,
                    "strategy_id": strategy_id,
                    "version": next_version,
                    "artifact_state": "draft",
                    "lineage": {"parent_registry_ids": [base_registry_id]},
                    "metadata": {
                        "tenant_id": scope.tenant_id,
                        "owner_user_id": scope.user_id,
                        "workshop_id": workshop_id,
                        "workshop_request_id": request_id,
                        "reason": body.reason,
                    },
                    "strategy_spec": patched,
                }
            )
            (
                registry_readback,
                authoritative_document_sha256,
                authoritative_strategy_id,
            ) = _project_strategy_version(
                readback=registry_readback,
                registry_id=registry_id,
                scope=scope,
                expected_strategy_id=strategy_id,
                expected_workshop_id=workshop_id,
            )
            if (
                authoritative_strategy_id != strategy_id
                or authoritative_document_sha256 != patched_document_sha256
            ):
                raise _StrategyVersionProjectionError(
                    "STRATEGY_SPEC_AUTHORITATIVE_DIGEST_MISMATCH"
                )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
            )
        except _StrategyVersionProjectionError as exc:
            code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else ErrorCode.UPSTREAM_ERROR
                if exc.status_code >= 500
                else ErrorCode.RESOURCE_CONFLICT
            )
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="create_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=exc.status_code,
                code=code,
                message="Authoritative StrategySpec version is inconsistent",
                reason=exc.reason,
                precondition_failed="strategy_version_projection",
            )
        existing_links = store.list_version_links(workshop_id)
        link = {
            "workshop_version_id": workshop_version_id,
            "workshop_id": workshop_id,
            "strategy_id": strategy_id,
            "strategy_spec_registry_id": registry_id,
            "parent_workshop_version_id": base_link["workshop_version_id"],
            "source_event_id": f"wsevt-version-{digest}",
            "sequence_no": len(existing_links) + 1,
            "document_sha256": authoritative_document_sha256,
            "created_by": scope.user_id,
            "created_at": utc_now(),
        }
        resource = {"version": link, "strategy_spec": registry_readback}
        receipt = _complete_or_raise(
            workshop_id=workshop_id,
            operation="create_version",
            scope=scope,
            idempotency_key=command_key,
            request_hash=request_hash,
            result=resource,
            canonical_refs={
                "strategy_spec_registry_id": registry_id,
                "workshop_version_id": workshop_version_id,
            },
            version_link=link,
            session_updates={"strategy_id": strategy_id, "status": "in_review"},
            event={
                "event_id": link["source_event_id"],
                "actor_type": "operator",
                "event_type": "version_created",
                "redacted_summary": "StrategySpec draft version created",
                "payload_refs_json": {
                    "workshop_version_id": workshop_version_id,
                    "strategy_spec_registry_id": registry_id,
                },
                "trace_id": request_id,
            },
        )
        _ws_publish(
            workshop_id,
            "workshop.version.created",
            {
                "workshop_version_id": workshop_version_id,
                "strategy_spec_registry_id": registry_id,
            },
            utc_now_fn=utc_now,
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or session,
            scope=scope,
            canonical_authority="strategy_registry",
            response=response,
        )

    @router.post("/bff/agora/workshops/{workshop_id}/versions/{version_id}/select")
    def select_workshop_version(
        workshop_id: str,
        version_id: str,
        response: Response,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from models import ErrorCode

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        session = _scoped_session(workshop_id, scope)
        link = store.get_version_link(workshop_id, version_id)
        if link is None:
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Workshop version not found",
                version_id,
            )
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        request_payload = {"version_id": version_id}
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="select_version",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="strategy_registry",
                response=response,
            )
        registry_id = str(link["strategy_spec_registry_id"])
        try:
            registry_readback, document_sha256, strategy_id = (
                _read_strategy_version(
                    registry_id=registry_id,
                    scope=scope,
                    expected_strategy_id=str(link.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
            )
            link = store.ensure_version_link_digest(
                workshop_id=workshop_id,
                workshop_version_id=version_id,
                strategy_id=strategy_id,
                document_sha256=document_sha256,
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="select_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
            )
        except _StrategyVersionProjectionError as exc:
            code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else ErrorCode.UPSTREAM_ERROR
                if exc.status_code >= 500
                else ErrorCode.RESOURCE_CONFLICT
            )
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="select_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=exc.status_code,
                code=code,
                message="StrategySpec version projection is inconsistent",
                reason=exc.reason,
                precondition_failed="strategy_version_projection",
            )
        except WorkshopVersionProjectionConflict:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="select_version",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Workshop version projection is inconsistent",
                reason="WORKSHOP_VERSION_PROJECTION_CONFLICT",
                precondition_failed="workshop_version_projection",
            )
        selected_session = {
            **session,
            "strategy_id": link["strategy_id"],
            "selected_version_id": version_id,
            "active_workshop_version_id": version_id,
            "active_strategy_spec_registry_id": registry_id,
            "status": "in_review",
            "lock_version": receipt.get("admitted_lock_version"),
        }
        resource = {
            "workshop": selected_session,
            "version": link,
            "strategy_spec": registry_readback,
        }
        receipt = _complete_or_raise(
            workshop_id=workshop_id,
            operation="select_version",
            scope=scope,
            idempotency_key=command_key,
            request_hash=request_hash,
            result=resource,
            canonical_refs={
                "strategy_spec_registry_id": registry_id,
                "workshop_version_id": version_id,
            },
            session_updates={
                "strategy_id": link["strategy_id"],
                "selected_version_id": version_id,
                "active_workshop_version_id": version_id,
                "active_strategy_spec_registry_id": registry_id,
                "status": "in_review",
            },
            event={
                "event_id": f"wsevt-select-{hashlib.sha256(command_key.encode()).hexdigest()[:16]}",
                "actor_type": "operator",
                "event_type": "version_selected",
                "redacted_summary": "Workshop version selected",
                "payload_refs_json": {
                    "workshop_version_id": version_id,
                    "strategy_spec_registry_id": registry_id,
                },
                "trace_id": request_id,
            },
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or selected_session,
            scope=scope,
            canonical_authority="strategy_registry",
            response=response,
        )

    @router.post("/bff/agora/workshops/{workshop_id}/research-runs", status_code=202)
    def create_workshop_research_run(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopResearchRunRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from models import ErrorCode

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        if body is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "WorkshopResearchRunRequest body is required",
                "REQUEST_BODY_REQUIRED",
                precondition_failed="request_body",
            )
        session = _scoped_session(workshop_id, scope)
        version_id = body.strategy_version_ref or session.get("selected_version_id")
        link = store.get_version_link(workshop_id, str(version_id or ""))
        if link is None:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "A selected workshop version is required for research",
                "WORKSHOP_VERSION_REQUIRED",
                precondition_failed="strategy_version_ref",
            )
        approval = _require_approval(
            approval_decision_id=body.approval_decision_id,
            workshop_id=workshop_id,
            version_id=str(version_id),
            session=session,
            scope=scope,
        )
        safe_modes = {"stub", "handoff_only", "manual"}
        if (
            body.adapter not in safe_modes
            or body.requested_mode not in safe_modes
            or body.dispatch_mode not in safe_modes
        ):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Workshop research is restricted to non-live handoff modes",
                "RESEARCH_ENVIRONMENT_FORBIDDEN",
                precondition_failed="research_mode",
            )
        serialized_parameters = json.dumps(body.parameters, sort_keys=True).lower()
        if any(token in serialized_parameters for token in ('"live"', '"canary"', '"production"')):
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Workshop research cannot target canary, live, or production",
                "RESEARCH_ENVIRONMENT_FORBIDDEN",
                precondition_failed="parameters.environment",
            )
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        request_payload = body.model_dump(mode="json")
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="dispatch_research",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="research_orchestrator",
                response=response,
            )
        registry_id = str(link["strategy_spec_registry_id"])
        resume = _resume_context(
            workshop_id=workshop_id,
            scope=scope,
            operation="dispatch_research",
            request_hash=request_hash,
            claimed_by=command_key,
        )
        # Reusing the recorded downstream idempotency digest keeps the
        # downstream task/run idempotency keys stable across a new-key retry,
        # so an unacknowledged prior create is deduplicated downstream.
        digest = (resume or {}).get("digest") or hashlib.sha256(
            f"{workshop_id}:{command_key}".encode()
        ).hexdigest()[:20]
        dispatch_kwargs: Dict[str, Any] = {}
        if resume is not None and resume["partial_effects"]:
            dispatch_kwargs["resume"] = {
                "research_task_id": resume["partial_effects"].get("research_task_id"),
                "research_run_id": resume["partial_effects"].get("research_run_id"),
            }
        try:
            downstream = canonical.dispatch_research_run(
                **dispatch_kwargs,
                task_payload={
                    "title": f"Strategy Workshop research {workshop_id}",
                    "objective": body.research_context,
                    "source_refs": [
                        {"type": "strategy_workshop", "id": workshop_id},
                        {"type": "workshop_version", "id": str(version_id)},
                        {"type": "strategy_spec_registry", "id": registry_id},
                        {"type": "approval_decision", "id": body.approval_decision_id},
                    ],
                    "constraints": {
                        "tenant_id": scope.tenant_id,
                        "environment_ceiling": "research",
                        "no_live_capital": True,
                    },
                    "actor_id": scope.user_id,
                    "idempotency_key": f"workshop-{digest}-task",
                    "created_at": utc_now(),
                },
                run_payload={
                    "adapter": body.adapter,
                    "requested_mode": body.requested_mode,
                    "dispatch_mode": body.dispatch_mode,
                    "input_refs": [
                        {"type": "strategy_spec", "id": registry_id},
                        {"type": "workshop_version", "id": str(version_id)},
                    ],
                    "parameters": {
                        **body.parameters,
                        "workshop_id": workshop_id,
                        "tenant_id": scope.tenant_id,
                        "approval_decision_id": body.approval_decision_id,
                    },
                    "actor_id": scope.user_id,
                    "idempotency_key": f"workshop-{digest}-run",
                    "requested_at": utc_now(),
                },
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="dispatch_research",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
                resume_digest=digest,
                resume=resume,
            )
        run = dict(downstream["run"])
        run_id = str(run.get("run_id") or run.get("id") or "")
        downstream_status = str(run.get("status") or "").lower()
        if downstream_status == "rejected":
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="dispatch_research",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.PRECONDITION_FAILED,
                message="Research orchestrator rejected the run",
                reason="RESEARCH_RUN_REJECTED",
                precondition_failed="research_dispatch",
            )
        resource = {
            **downstream,
            "downstream_status": downstream_status,
            "downstream_terminal": downstream_status
            in {"completed", "succeeded", "failed", "cancelled", "timed_out"},
        }
        receipt = _complete_or_raise(
            workshop_id=workshop_id,
            operation="dispatch_research",
            scope=scope,
            idempotency_key=command_key,
            request_hash=request_hash,
            result=resource,
            canonical_refs={
                "research_task_id": downstream["task"].get("task_id"),
                "research_run_id": run_id,
                "workshop_version_id": str(version_id),
                "approval_decision_id": approval["approval_decision_id"],
                **(
                    {
                        "resumed_from_command_id": resume["receipt"].get("command_id"),
                        "resumed_from_idempotency_key": resume["receipt"].get(
                            "idempotency_key"
                        ),
                    }
                    if resume is not None
                    else {}
                ),
            },
            event={
                "event_id": f"wsevt-research-{digest}",
                "actor_type": "operator",
                "event_type": "research_dispatched",
                "redacted_summary": "Research run admitted by canonical orchestrator",
                "payload_refs_json": {
                    "research_run_id": run_id,
                    "workshop_version_id": str(version_id),
                },
                "trace_id": request_id,
            },
            downstream_digest=digest,
            resume=resume,
        )
        _resolve_resumed_compensation(
            workshop_id=workshop_id,
            scope=scope,
            operation="dispatch_research",
            resume=resume,
            resolved_by_idempotency_key=command_key,
        )
        _ws_publish(
            workshop_id,
            "research.run.progress",
            {"run_id": run_id, "status": downstream_status},
            utc_now_fn=utc_now,
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or session,
            scope=scope,
            canonical_authority="research_orchestrator",
            response=response,
        )

    @router.post("/bff/agora/workshops/{workshop_id}/consultations", status_code=201)
    def create_workshop_consultation(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopConsultationRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from models import ErrorCode

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        if body is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "WorkshopConsultationRequest body is required",
                "REQUEST_BODY_REQUIRED",
                precondition_failed="request_body",
            )
        session = _scoped_session(workshop_id, scope)
        if body.consultation_type not in {"committee", "advisory"}:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "consultation_type must be committee or advisory",
                "CONSULTATION_TYPE_INVALID",
            )
        version_id = str(session.get("selected_version_id") or "")
        link = store.get_version_link(workshop_id, version_id)
        if link is None:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "A selected workshop version is required for consultation",
                "WORKSHOP_VERSION_REQUIRED",
                precondition_failed="selected_version_id",
            )
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        request_payload = body.model_dump(mode="json")
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="open_consultation",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="consultation_service",
                response=response,
            )
        resume = _resume_context(
            workshop_id=workshop_id,
            scope=scope,
            operation="open_consultation",
            request_hash=request_hash,
            claimed_by=command_key,
        )
        # Reusing the recorded digest re-derives the same deterministic
        # consultation request id, so a new-key retry adopts the request a
        # failed attempt may already have created downstream.
        digest = (resume or {}).get("digest") or hashlib.sha256(
            f"{workshop_id}:{command_key}".encode()
        ).hexdigest()[:20]
        consultation_id = (
            str((resume or {}).get("partial_effects", {}).get("consultation_request_id") or "")
            or f"cr-ws-{digest}"
        )
        open_kwargs: Dict[str, Any] = {}
        if resume is not None:
            open_kwargs["resume"] = True
        try:
            consultation = canonical.open_consultation(
                **open_kwargs,
                request_id=consultation_id,
                payload={
                    "request_type": "strategy_review",
                    "requested_by": {
                        "actor_type": "operator",
                        "actor_id": scope.user_id,
                    },
                    "target_type": "strategy_workshop",
                    "target_id": workshop_id,
                    "task": body.subject,
                    "consultation_type": body.consultation_type,
                    "context_refs": [
                        {"type": "workshop_version", "id": version_id},
                        {
                            "type": "strategy_spec_registry",
                            "id": link["strategy_spec_registry_id"],
                        },
                        *[
                            {"type": "external_context", "id": ref}
                            for ref in body.context_refs
                        ],
                    ],
                    "priority": "normal",
                    "metadata": {
                        "tenant_id": scope.tenant_id,
                        "owner_user_id": scope.user_id,
                        "workshop_id": workshop_id,
                        "workshop_version_id": version_id,
                        "idempotency_key": command_key,
                    },
                    "trace_id": request_id,
                },
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="open_consultation",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
                resume_digest=digest,
                resume=resume,
            )
        downstream_status = str(consultation.get("status") or "").lower()
        if downstream_status == "cancelled":
            # A cancelled consultation is not a successful open.  Seal the
            # adopted lineage in the same transaction as this failure so no
            # later retry re-adopts the dead request; a new-key retry then
            # opens a fresh consultation under its own digest.
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="open_consultation",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Consultation request is cancelled downstream",
                reason="CONSULTATION_REQUEST_CANCELLED",
                precondition_failed="consultation_request",
                resolve_source=(
                    _source_resolution(
                        resume,
                        resolution="cancelled",
                        resolved_by=command_key,
                        extra={"consultation_request_id": consultation_id},
                    )
                    if resume is not None
                    else None
                ),
            )
        resource = {
            "consultation": consultation,
            "downstream_status": downstream_status,
            "downstream_terminal": downstream_status
            in {"published", "cancelled", "failed"},
        }
        try:
            receipt = _complete_or_raise(
                workshop_id=workshop_id,
                operation="open_consultation",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                result=resource,
                canonical_refs={
                    "consultation_request_id": consultation_id,
                    "workshop_version_id": version_id,
                    **(
                        {
                            "resumed_from_command_id": resume["receipt"].get(
                                "command_id"
                            ),
                            "resumed_from_idempotency_key": resume["receipt"].get(
                                "idempotency_key"
                            ),
                        }
                        if resume is not None
                        else {}
                    ),
                },
                event={
                    "event_id": f"wsevt-consult-{digest}",
                    "actor_type": "operator",
                    "event_type": "consultation_started",
                    "redacted_summary": "Consultation request opened",
                    "payload_refs_json": {
                        "consultation_request_id": consultation_id,
                        "workshop_version_id": version_id,
                    },
                    "trace_id": request_id,
                },
                downstream_digest=digest,
                resume=resume,
            )
        except HTTPException:
            # Compensation-or-resume with exclusive effect ownership: only a
            # consultation this attempt itself created (resume is None) may
            # be cancelled; on success the failed receipt's compensation is
            # sealed as cancelled so a retry opens a fresh consultation.  An
            # adopted request is shared lineage — cancelling it could destroy
            # a resource the prior receipt chain still references — so the
            # failed receipt keeps its resumable lineage instead (the adopted
            # source was resolved as superseded in the same failure write).
            # If cancellation itself fails, the receipt likewise keeps its
            # resumable lineage and a new-key retry adopts the recorded
            # request instead of opening a duplicate.
            if resume is None:
                cancelled = False
                try:
                    canonical.cancel_consultation(
                        consultation_id,
                        actor_id=scope.user_id,
                        trace_id=request_id,
                    )
                    cancelled = True
                except Exception:
                    pass
                if cancelled and hasattr(store, "resolve_command_compensation"):
                    try:
                        store.resolve_command_compensation(
                            workshop_id=workshop_id,
                            tenant_id=scope.tenant_id,
                            user_id=scope.user_id,
                            operation="open_consultation",
                            idempotency_key=command_key,
                            resolution={
                                "resolved_at": utc_now(),
                                "resolution": "cancelled",
                                "consultation_request_id": consultation_id,
                            },
                        )
                    except Exception:
                        pass
            raise
        _resolve_resumed_compensation(
            workshop_id=workshop_id,
            scope=scope,
            operation="open_consultation",
            resume=resume,
            resolved_by_idempotency_key=command_key,
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or session,
            scope=scope,
            canonical_authority="consultation_service",
            response=response,
        )

    @router.post("/bff/agora/workshops/{workshop_id}/conclude")
    def conclude_workshop(
        workshop_id: str,
        response: Response,
        body: Optional[WorkshopConcludeRequest] = Body(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
        if_match: Optional[str] = Header(default=None, alias="If-Match"),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-Id"),
    ) -> Dict[str, Any]:
        from models import ErrorCode

        scope = _scope(
            authorization,
            x_tenant_id,
            write=True,
            x_mfa_token=x_mfa_token,
            mfa_required=True,
        )
        if body is None:
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "WorkshopConcludeRequest body is required",
                "REQUEST_BODY_REQUIRED",
                precondition_failed="request_body",
            )
        session = _scoped_session(workshop_id, scope)
        if session.get("status") != "in_review":
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Workshop must be in_review before conclude",
                "WORKSHOP_NOT_IN_REVIEW",
                precondition_failed="workshop_status",
            )
        version_id = body.final_version_id or session.get("selected_version_id")
        link = store.get_version_link(workshop_id, str(version_id or ""))
        if link is None:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "A valid final workshop version is required",
                "WORKSHOP_VERSION_REQUIRED",
                precondition_failed="final_version_id",
            )
        selected_version_id = str(session.get("selected_version_id") or "")
        if str(version_id) != selected_version_id:
            raise bff_error(
                409,
                ErrorCode.PRECONDITION_FAILED,
                "Conclusion requires the currently selected workshop version",
                "WORKSHOP_FINAL_VERSION_NOT_SELECTED",
                precondition_failed="final_version_id",
                details_extra={"selected_version_id": selected_version_id},
            )
        two_person_proof = _require_approval(
            approval_decision_id=body.approval_decision_id,
            workshop_id=workshop_id,
            version_id=str(version_id),
            session=session,
            scope=scope,
        )
        expected_version, command_key, request_id = _require_command_headers(
            workshop_id=workshop_id,
            if_match=if_match,
            idempotency_key=idempotency_key,
            request_id=x_request_id,
        )
        request_payload = body.model_dump(mode="json")
        receipt, request_hash = _admit_command(
            workshop_id=workshop_id,
            scope=scope,
            operation="conclude",
            expected_lock_version=expected_version,
            idempotency_key=command_key,
            request_id=request_id,
            payload=request_payload,
        )
        replay = _require_replayable_receipt(receipt=receipt, workshop_id=workshop_id)
        if replay is not None:
            return _command_response(
                receipt=receipt,
                resource=replay,
                session=store.get_session(workshop_id) or session,
                scope=scope,
                canonical_authority="workshop_store+strategy_registry+approval_decision_store",
                response=response,
            )
        registry_id = str(link["strategy_spec_registry_id"])
        try:
            # The terminal transition demands the same authoritative projection
            # proof as version selection: Registry readback must expose a
            # complete StrategySpec document in the caller's scope whose digest
            # still matches the immutable durable link.
            registry_readback, document_sha256, final_strategy_id = (
                _read_strategy_version(
                    registry_id=registry_id,
                    scope=scope,
                    expected_strategy_id=str(link.get("strategy_id") or "") or None,
                    expected_workshop_id=workshop_id,
                )
            )
            link = store.ensure_version_link_digest(
                workshop_id=workshop_id,
                workshop_version_id=str(version_id),
                strategy_id=final_strategy_id,
                document_sha256=document_sha256,
            )
        except CanonicalOperationError as exc:
            _canonical_error(
                workshop_id=workshop_id,
                operation="conclude",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                error=exc,
            )
        except _StrategyVersionProjectionError as exc:
            code = (
                ErrorCode.FORBIDDEN
                if exc.status_code == 403
                else ErrorCode.UPSTREAM_ERROR
                if exc.status_code >= 500
                else ErrorCode.RESOURCE_CONFLICT
            )
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="conclude",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=exc.status_code,
                code=code,
                message="Final StrategySpec version projection is incomplete or inconsistent",
                reason=exc.reason,
                precondition_failed="strategy_version_projection",
            )
        except WorkshopVersionProjectionConflict:
            _fail_domain_command(
                workshop_id=workshop_id,
                operation="conclude",
                scope=scope,
                idempotency_key=command_key,
                request_hash=request_hash,
                status_code=409,
                code=ErrorCode.RESOURCE_CONFLICT,
                message="Final workshop version digest no longer matches its immutable link",
                reason="WORKSHOP_VERSION_PROJECTION_CONFLICT",
                precondition_failed="workshop_version_projection",
            )
        concluded_at = utc_now()
        concluded_session = {
            **session,
            "selected_version_id": str(version_id),
            "active_workshop_version_id": str(version_id),
            "active_strategy_spec_registry_id": registry_id,
            "final_workshop_version_id": str(version_id),
            "final_strategy_spec_registry_id": registry_id,
            "status": "concluded",
            "concluded_at": concluded_at,
            "lock_version": receipt.get("admitted_lock_version"),
        }
        no_direct_action = {
            "deployment_triggered": False,
            "order_submitted": False,
            "live_capital_changed": False,
        }
        resource = {
            "workshop": concluded_session,
            "version": {**link, "strategy_spec": registry_readback},
            "approval_decision_id": body.approval_decision_id,
            "two_person_proof": two_person_proof,
            "no_direct_action_proof": no_direct_action,
        }
        digest = hashlib.sha256(f"{workshop_id}:{command_key}".encode()).hexdigest()[:20]
        receipt = _complete_or_raise(
            workshop_id=workshop_id,
            operation="conclude",
            scope=scope,
            idempotency_key=command_key,
            request_hash=request_hash,
            result=resource,
            canonical_refs={
                "workshop_version_id": str(version_id),
                "strategy_spec_registry_id": registry_id,
                "approval_decision_id": body.approval_decision_id,
            },
            session_updates={
                "selected_version_id": str(version_id),
                "active_workshop_version_id": str(version_id),
                "active_strategy_spec_registry_id": registry_id,
                "final_workshop_version_id": str(version_id),
                "final_strategy_spec_registry_id": registry_id,
                "status": "concluded",
                "concluded_at": concluded_at,
            },
            event={
                "event_id": f"wsevt-conclude-{digest}",
                "actor_type": "operator",
                "event_type": "concluded",
                "redacted_summary": "Workshop concluded with approved final version",
                "payload_refs_json": {
                    "final_workshop_version_id": str(version_id),
                    "final_strategy_spec_registry_id": registry_id,
                    "approval_decision_id": body.approval_decision_id,
                },
                "trace_id": request_id,
            },
        )
        _ws_publish(
            workshop_id,
            "workshop.concluded",
            {
                "final_workshop_version_id": str(version_id),
                "final_strategy_spec_registry_id": registry_id,
            },
            utc_now_fn=utc_now,
        )
        return _command_response(
            receipt=receipt,
            resource=resource,
            session=store.get_session(workshop_id) or concluded_session,
            scope=scope,
            canonical_authority="workshop_store+strategy_registry+approval_decision_store",
            response=response,
        )

    @router.get("/bff/agora/workshops/{workshop_id}/stream")
    async def stream_workshop(
        workshop_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        """SSE aggregate of workshop events (AG-BE-SW-004).

        Streams workshop.connected (ack), workshop.message.ack, workshop.completeness.updated,
        research.run.progress, workshop.version.created, and workshop.openclaw.degraded
        events.  Supports reconnection via Last-Event-ID.  First event is always
        workshop.connected, delivered immediately (< 2 s guarantee).
        OpenClaw degradation is surfaced as OPENCLAW_UPSTREAM_DEGRADED in event data.
        """
        scope = _scope(authorization, x_tenant_id)
        session = store.get_session(workshop_id)
        if session is None:
            from models import ErrorCode
            raise bff_error(404, ErrorCode.RESOURCE_NOT_FOUND, "Workshop not found", workshop_id)
        if session["user_id"] != scope.user_id or session["tenant_id"] != scope.tenant_id:
            _raise_cross_user_forbidden(
                bff_error=bff_error,
                resource="strategy_workshop",
                resource_id=workshop_id,
            )

        async def _event_stream() -> AsyncGenerator[str, None]:
            q: asyncio.Queue = asyncio.Queue(maxsize=500)
            subs = _ws_get_subscribers(workshop_id)
            subs.append(q)
            try:
                # Replay missed events when client reconnects with Last-Event-ID
                if last_event_id:
                    for replayed_evt in _ws_replay_after(workshop_id, last_event_id, store=store):
                        yield _ws_sse_format(replayed_evt)

                # Immediate ack on connect — satisfies the < 2s first-acknowledgement requirement.
                # §8.2 audit: trace_id from the session's openclaw_session_id.
                ack_event_id = _ws_event_id()
                ack_event: Dict[str, Any] = {
                    "id": ack_event_id,
                    "type": "workshop.connected",
                    "timestamp": utc_now(),
                    "data": {
                        "workshop_id": workshop_id,
                        "status": session.get("status", "open"),
                        "lock_version": session.get("lock_version", 1),
                        "trace_id": session.get("openclaw_session_id"),
                    },
                }
                _ws_get_buffer(workshop_id).append((ack_event_id, ack_event))
                yield _ws_sse_format(ack_event)

                # Stream live events until the client disconnects
                while True:
                    try:
                        evt = await asyncio.wait_for(q.get(), timeout=30.0)
                        yield _ws_sse_format(evt)
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                if q in subs:
                    subs.remove(q)

        return StreamingResponse(
            _event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-SSE-Channel": f"workshop:{workshop_id}",
                "X-SSE-Replay-Supported": "true",
                "X-SSE-Replay-Window-Events": str(_WS_SSE_BUFFER_SIZE),
                "X-SSE-Resync-Routes": f"/bff/agora/workshops/{workshop_id}",
            },
        )

    return router
