"""Public Workshop readiness projection module.

Computes the AG-BE-SW readiness assessment (preliminary_research /
full_validation / trading_room gates) from a workshop session, its events
and its latest StrategyCompleteness snapshot. Moved out of router.py so
other Agora routers (Trading Room) import one public readiness
implementation instead of a router-private helper (ACG-06-003).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ._common import _clean_optional, _safe_card_id

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


# Public API consumed outside this package (e.g. Trading Room's readiness
# fallback projector). The underscore-prefixed name above is retained as
# the module's own internal implementation name; this is the stable public
# entry point callers should import.
build_readiness_assessment = _build_readiness_assessment
