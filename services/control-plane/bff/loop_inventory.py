from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional


_REPO_ROOT = Path(__file__).resolve().parents[3]
_REGISTRY_PATH = _REPO_ROOT / "docs" / "deployment" / "loop-catalog.registry.json"
_REGISTRY_REF = "docs/deployment/loop-catalog.registry.json"
_LIVE_EVIDENCE_LEVELS = ("reconciled_live_proof", "proven_live_evidence")
_SNAPSHOT_TRUTH_LEVEL = "snapshot_fallback"
_TRUTH_LEVEL_ORDER = (
    "seed_fixture",
    _SNAPSHOT_TRUTH_LEVEL,
    "registry_metadata",
    "scheduled_tick",
    "reconciled_live_proof",
    "proven_live_evidence",
)
_TRUTH_LEVEL_RANKS = {
    "seed_fixture": 0,
    _SNAPSHOT_TRUTH_LEVEL: 0,
    "registry_metadata": 1,
    "scheduled_tick": 2,
    "reconciled_live_proof": 3,
    "proven_live_evidence": 4,
}
_TRUTH_SOURCE_TYPES = {
    "seed_fixture": "seed_fixture",
    _SNAPSHOT_TRUTH_LEVEL: "snapshot",
    "registry_metadata": "registry",
    "scheduled_tick": "scheduled",
    "reconciled_live_proof": "live_truth",
    "proven_live_evidence": "live_truth",
}


def _load_registry() -> Dict[str, Any]:
    return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))


def _evidence_statuses(evidence_profile: Dict[str, Any]) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    for truth_level, evidence in evidence_profile.items():
        if isinstance(evidence, dict):
            statuses[truth_level] = str(evidence.get("status") or "missing")
    return statuses


def _dedupe_strings(values: List[Any]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for value in values:
        cleaned = str(value or "").strip()
        if cleaned and cleaned not in seen:
            deduped.append(cleaned)
            seen.add(cleaned)
    return deduped


def _dict_or_empty(value: Any) -> Dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _health_record_truth_level(record: Dict[str, Any]) -> Optional[str]:
    packet = record.get("evidence_packet") if isinstance(record.get("evidence_packet"), dict) else {}
    raw = (
        record.get("truth_level")
        or record.get("truth_source_level")
        or packet.get("truth_level")
        or packet.get("highest_truth_level")
    )
    if not raw and isinstance(record.get("truth_source"), dict):
        raw = record["truth_source"].get("level")
    clean = str(raw or "").strip()
    if clean == "live_truth":
        return "reconciled_live_proof"
    if clean in _TRUTH_LEVEL_RANKS:
        return clean
    return None


def _health_record_refs(record: Dict[str, Any]) -> List[str]:
    refs: List[Any] = []
    packet = record.get("evidence_packet") if isinstance(record.get("evidence_packet"), dict) else {}
    for key in ("refs", "evidence_refs", "artifacts"):
        raw_refs = packet.get(key)
        if isinstance(raw_refs, list):
            refs.extend(raw_refs)
    for key in ("refs", "evidence_refs", "artifacts"):
        raw_refs = record.get(key)
        if isinstance(raw_refs, list):
            refs.extend(raw_refs)
    return _dedupe_strings(refs)


def _truth_source_from_profile(
    level: str,
    evidence_profile: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
) -> Dict[str, Any]:
    evidence = evidence_profile.get(level) if isinstance(evidence_profile.get(level), dict) else {}
    health_truth_level = _health_record_truth_level(health_record)
    health_refs = _health_record_refs(health_record)
    status = str(evidence.get("status") or "missing")
    refs = _dedupe_strings(list(evidence.get("refs") or []))
    note = evidence.get("note")
    source = "bff_local_registry" if evidence else "missing"

    if level == _SNAPSHOT_TRUTH_LEVEL:
        status = "present" if health_source == "local_snapshot" and health_record else "missing"
        refs = []
        note = (
            "Loop health snapshot fallback is being served from the BFF local snapshot."
            if status == "present"
            else "No BFF local snapshot fallback is present for this loop health record."
        )
        source = "local_snapshot" if status == "present" else "missing"

    if health_truth_level == level:
        status = str(health_record.get("truth_status") or "present")
        refs = _dedupe_strings(refs + health_refs)
        note = health_record.get("truth_note") or note
        source = health_source or "service_store"

    return {
        "truth_level": level,
        "truth_bucket": _TRUTH_SOURCE_TYPES[level],
        "source_type": _TRUTH_SOURCE_TYPES[level],
        "rank": _TRUTH_LEVEL_RANKS[level],
        "status": status,
        "source": source,
        "refs": refs,
        "note": note,
        "accepted_as_live": (
            level in _LIVE_EVIDENCE_LEVELS
            and status == "present"
            and source != "local_snapshot"
        ),
    }


def _truth_sources(
    evidence_profile: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
) -> List[Dict[str, Any]]:
    return [
        _truth_source_from_profile(level, evidence_profile, health_record, health_source)
        for level in _TRUTH_LEVEL_ORDER
    ]


def _highest_present_truth_source(truth_sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    present = [
        source
        for source in truth_sources
        if source.get("status") == "present"
    ]
    if not present:
        return {
            "truth_level": "missing",
            "rank": -1,
            "source": "missing",
            "accepted_as_live": False,
        }
    return max(
        present,
        key=lambda source: (
            int(source.get("rank") or 0),
            str(source.get("truth_level") or ""),
        ),
    )


def _has_present_live_evidence(evidence_profile: Dict[str, Any]) -> bool:
    return any(
        isinstance(evidence_profile.get(level), dict)
        and evidence_profile[level].get("status") == "present"
        for level in _LIVE_EVIDENCE_LEVELS
    )


def _is_proven_live(loop: Dict[str, Any]) -> bool:
    maturity = loop.get("maturity") if isinstance(loop.get("maturity"), dict) else {}
    controller = loop.get("controller_contract") if isinstance(loop.get("controller_contract"), dict) else {}
    evidence_profile = loop.get("evidence_profile") if isinstance(loop.get("evidence_profile"), dict) else {}
    return (
        maturity.get("current") == "proven-live"
        and controller.get("status") == "proven_live"
        and isinstance(evidence_profile.get("proven_live_evidence"), dict)
        and evidence_profile["proven_live_evidence"].get("status") == "present"
    )


def _is_reconciled_with_evidence(loop: Dict[str, Any]) -> bool:
    maturity = loop.get("maturity") if isinstance(loop.get("maturity"), dict) else {}
    controller = loop.get("controller_contract") if isinstance(loop.get("controller_contract"), dict) else {}
    evidence_profile = loop.get("evidence_profile") if isinstance(loop.get("evidence_profile"), dict) else {}
    return (
        maturity.get("current") in {"reconciled", "proven-live"}
        and controller.get("status") in {"implemented", "proven_live"}
        and isinstance(evidence_profile.get("reconciled_live_proof"), dict)
        and evidence_profile["reconciled_live_proof"].get("status") == "present"
    )


def loop_inventory_meta() -> Dict[str, Any]:
    registry = _load_registry()
    return {
        "schema_version": registry.get("schema_version"),
        "catalog_id": registry.get("catalog_id"),
        "created_at": registry.get("created_at"),
        "source_documents": list(registry.get("source_documents") or []),
        "catalog_decisions": deepcopy(registry.get("catalog_decisions") or {}),
        "maturity_levels": deepcopy(registry.get("maturity_levels") or []),
        "truth_levels": deepcopy(registry.get("truth_levels") or []),
        "registry_ref": _REGISTRY_REF,
    }


def _project_loop(loop: Dict[str, Any]) -> Dict[str, Any]:
    loop_id = str(loop.get("loop_id") or "")
    maturity = deepcopy(loop.get("maturity") or {})
    owner = deepcopy(loop.get("owner") or {})
    controller = deepcopy(loop.get("controller_contract") or {})
    evidence_profile = deepcopy(loop.get("evidence_profile") or {})
    live = _is_proven_live(loop)
    reconciled = _is_reconciled_with_evidence(loop)
    evidence_statuses = _evidence_statuses(evidence_profile)

    return {
        "id": loop_id,
        "loop_id": loop_id,
        "name": loop.get("name"),
        "policy_ref": deepcopy(loop.get("policy_ref") or {}),
        "owner": owner,
        "trigger_model": deepcopy(loop.get("trigger_model") or {}),
        "desired_state": deepcopy(loop.get("desired_state") or {}),
        "actual_state": deepcopy(loop.get("actual_state") or {}),
        "maturity": maturity,
        "current_maturity": maturity.get("current"),
        "target_maturity": maturity.get("target"),
        "controller": controller,
        "evidence": evidence_profile,
        "evidence_statuses": evidence_statuses,
        "execution_tasks": deepcopy(loop.get("execution_tasks") or []),
        "truth_source": {
            "level": "registry_metadata",
            "source": "static_json_registry",
            "registry_ref": _REGISTRY_REF,
            "live_truth_levels": list(_LIVE_EVIDENCE_LEVELS),
        },
        "live_status": {
            "is_live": live,
            "is_reconciled": reconciled,
            "has_live_evidence": _has_present_live_evidence(evidence_profile),
            "reason": (
                "proven live evidence is present in the loop catalog"
                if live
                else "catalog metadata is not live liveness proof"
            ),
        },
    }


def _normalize_health_records(health_records: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    records_by_loop: Dict[str, Dict[str, Any]] = {}
    for record in health_records or []:
        if not isinstance(record, dict):
            continue
        loop_id = str(record.get("loop_id") or record.get("id") or "").strip()
        if not loop_id:
            continue
        records_by_loop[loop_id] = deepcopy(record)
    return records_by_loop


def _event_from_health_record(
    health_record: Dict[str, Any],
    *,
    event_key: str,
    fallback_source: str,
) -> Optional[Dict[str, Any]]:
    raw = health_record.get(event_key)
    if raw in (None, ""):
        timestamp_key = f"{event_key}_at"
        reason_key = f"{event_key}_reason"
        if health_record.get(timestamp_key) or health_record.get(reason_key):
            raw = {
                "at": health_record.get(timestamp_key),
                "reason": health_record.get(reason_key),
            }
    if raw in (None, ""):
        return None
    if isinstance(raw, str):
        raw = {"at": raw}
    if not isinstance(raw, dict):
        return None

    evidence_refs = raw.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        evidence_refs = raw.get("refs") if isinstance(raw.get("refs"), list) else []
    truth_level = str(raw.get("truth_level") or _health_record_truth_level(health_record) or "").strip() or None
    return {
        "at": raw.get("at") or raw.get("timestamp") or raw.get("captured_at"),
        "status": raw.get("status"),
        "reason": raw.get("reason") or raw.get("failure_reason"),
        "summary": raw.get("summary") or raw.get("message"),
        "truth_level": truth_level,
        "source": raw.get("source") or fallback_source,
        "evidence_refs": _dedupe_strings(list(evidence_refs)),
    }


def _project_controller_health(
    controller: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
) -> Dict[str, Any]:
    raw_health = _dict_or_empty(
        health_record.get("controller_health")
        or health_record.get("controller")
    )
    contract_status = str(controller.get("status") or "unknown")
    status = (
        raw_health.get("status")
        or health_record.get("controller_status")
        or ("not_implemented" if contract_status == "not_implemented" else "unobserved")
    )
    source = health_source if raw_health or health_record.get("controller_status") else "registry_metadata"
    return {
        "status": status,
        "source": source,
        "controller_contract_status": contract_status,
        "controller_name": (
            raw_health.get("controller_name")
            or raw_health.get("name")
            or controller.get("controller_name")
        ),
        "last_heartbeat_at": raw_health.get("last_heartbeat_at") or health_record.get("last_heartbeat_at"),
        "liveness_metric": raw_health.get("liveness_metric") or controller.get("liveness_metric"),
        "desired_state_query": controller.get("desired_state_query"),
        "actual_state_query": controller.get("actual_state_query"),
        "desired_state_query_configured": bool(controller.get("desired_state_query")),
        "actual_state_query_configured": bool(controller.get("actual_state_query")),
        "restart_behavior": controller.get("restart_behavior"),
    }


def _project_downstream_actual_state(
    actual_state: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
) -> Dict[str, Any]:
    downstream = _dict_or_empty(
        health_record.get("downstream_actual_state")
        or health_record.get("downstream_status")
    )
    if downstream:
        return {
            "status": downstream.get("status") or "unknown",
            "source": downstream.get("source") or health_source,
            "summary": downstream.get("summary") or downstream.get("message"),
            "checked_at": downstream.get("checked_at") or downstream.get("captured_at"),
            "sources": deepcopy(downstream.get("sources") or []),
        }
    return {
        "status": actual_state.get("query_status") or "unknown",
        "source": "registry_metadata",
        "summary": actual_state.get("query"),
        "checked_at": None,
        "sources": deepcopy(actual_state.get("sources") or []),
    }


def _project_evidence_packet(
    loop_id: str,
    maturity: Dict[str, Any],
    evidence_profile: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
) -> Dict[str, Any]:
    packet = _dict_or_empty(health_record.get("evidence_packet"))
    truth_sources = _truth_sources(evidence_profile, health_record, health_source)
    highest = _highest_present_truth_source(truth_sources)
    profile_refs: List[Any] = []
    for evidence in evidence_profile.values():
        if isinstance(evidence, dict) and isinstance(evidence.get("refs"), list):
            profile_refs.extend(evidence["refs"])
    refs = _dedupe_strings(profile_refs + _health_record_refs(health_record))
    accepted_live_liveness = any(source.get("accepted_as_live") for source in truth_sources)
    return {
        "id": packet.get("id") or packet.get("packet_id") or f"loop-health-{loop_id}",
        "packet_id": packet.get("packet_id") or packet.get("id") or f"loop-health-{loop_id}",
        "loop_id": loop_id,
        "source": health_source if health_record else "bff_local_registry",
        "registry_ref": _REGISTRY_REF,
        "current_maturity": maturity.get("current"),
        "target_maturity": maturity.get("target"),
        "highest_truth_level": highest.get("truth_level"),
        "highest_truth_rank": highest.get("rank"),
        "accepted_live_liveness": accepted_live_liveness,
        "can_claim_reconciled": (
            accepted_live_liveness
            and int(highest.get("rank") or -1) >= _TRUTH_LEVEL_RANKS["reconciled_live_proof"]
        ),
        "can_claim_proven_live": (
            accepted_live_liveness
            and highest.get("truth_level") == "proven_live_evidence"
        ),
        "captured_at": packet.get("captured_at") or health_record.get("captured_at") or health_record.get("updated_at"),
        "refs": refs,
        "truth_sources": truth_sources,
    }


def _project_loop_health(
    loop: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
) -> Dict[str, Any]:
    projected = _project_loop(loop)
    loop_id = str(projected.get("loop_id") or "")
    maturity = projected.get("maturity") if isinstance(projected.get("maturity"), dict) else {}
    controller = projected.get("controller") if isinstance(projected.get("controller"), dict) else {}
    actual_state = projected.get("actual_state") if isinstance(projected.get("actual_state"), dict) else {}
    evidence_profile = projected.get("evidence") if isinstance(projected.get("evidence"), dict) else {}
    evidence_packet = _project_evidence_packet(
        loop_id,
        maturity,
        evidence_profile,
        health_record,
        health_source,
    )
    projected.update(
        {
            "read_model": "loop_health",
            "controller_health": _project_controller_health(controller, health_record, health_source),
            "last_success": _event_from_health_record(
                health_record,
                event_key="last_success",
                fallback_source=health_source,
            ),
            "last_failure": _event_from_health_record(
                health_record,
                event_key="last_failure",
                fallback_source=health_source,
            ),
            "downstream_actual_state": _project_downstream_actual_state(actual_state, health_record, health_source),
            "evidence_packet": evidence_packet,
        }
    )
    projected["truth_source"] = {
        "level": evidence_packet["highest_truth_level"],
        "source": evidence_packet["source"],
        "registry_ref": _REGISTRY_REF,
        "live_truth_levels": list(_LIVE_EVIDENCE_LEVELS),
        "truth_sources": evidence_packet["truth_sources"],
    }
    has_live_evidence = bool(evidence_packet["accepted_live_liveness"])
    projected["live_status"] = {
        "is_live": bool(evidence_packet["can_claim_proven_live"]),
        "is_reconciled": bool(evidence_packet["can_claim_reconciled"]),
        "has_live_evidence": has_live_evidence,
        "reason": (
            "live evidence is present in the loop health evidence packet"
            if has_live_evidence
            else "no reconciled or proven-live evidence packet is present"
        ),
    }
    return projected


def list_loop_inventory_entries() -> List[Dict[str, Any]]:
    registry = _load_registry()
    loops = registry.get("loops") if isinstance(registry.get("loops"), list) else []
    return [_project_loop(loop) for loop in loops if isinstance(loop, dict)]


def get_loop_inventory_entry(loop_id: str) -> Optional[Dict[str, Any]]:
    clean_id = str(loop_id or "").strip()
    for item in list_loop_inventory_entries():
        if item.get("loop_id") == clean_id:
            return item
    return None


def list_loop_health_entries(
    health_records: Optional[List[Dict[str, Any]]] = None,
    *,
    health_source: str = "missing",
) -> List[Dict[str, Any]]:
    registry = _load_registry()
    loops = registry.get("loops") if isinstance(registry.get("loops"), list) else []
    records_by_loop = _normalize_health_records(health_records)
    return [
        _project_loop_health(
            loop,
            records_by_loop.get(str(loop.get("loop_id") or ""), {}),
            health_source if str(loop.get("loop_id") or "") in records_by_loop else "missing",
        )
        for loop in loops
        if isinstance(loop, dict)
    ]


def get_loop_health_entry(
    loop_id: str,
    health_records: Optional[List[Dict[str, Any]]] = None,
    *,
    health_source: str = "missing",
) -> Optional[Dict[str, Any]]:
    clean_id = str(loop_id or "").strip()
    for item in list_loop_health_entries(health_records, health_source=health_source):
        if item.get("loop_id") == clean_id:
            return item
    return None
