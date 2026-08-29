from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


_REPO_ROOT = Path(__file__).resolve().parents[3]
_REGISTRY_PATH = _REPO_ROOT / "docs" / "deployment" / "loop-catalog.registry.json"
_REGISTRY_REF = "docs/deployment/loop-catalog.registry.json"
_LIVE_EVIDENCE_LEVELS = ("reconciled_live_proof", "proven_live_evidence")
_ACCEPTED_RUNTIME_HEALTH_SOURCES = {
    "controller_store",
    "service_store",
    "target_runtime",
    "bff_downstream_health_monitor",
}
_ACCEPTED_RUNTIME_EVIDENCE_BASES = {
    "controller_runtime",
    "target_runtime",
}
_ACCEPTED_CONTROLLER_HEALTH_STATUSES = {
    "healthy",
    "ok",
    "observed",
    "running",
}
_CONTROLLER_RECORD_MAX_AGE_SECONDS = 900
_CONTROLLER_RECORD_MAX_FUTURE_SKEW_SECONDS = 60
_IMPLEMENTED_CONTROLLER_STATUSES = {"implemented", "proven_live"}
_CONTROLLER_CONTRACT_REQUIRED_FIELDS = (
    "controller_name",
    "desired_state_query",
    "actual_state_query",
    "restart_behavior",
    "liveness_metric",
)
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
_TRUTH_SOURCE_LABELS = {
    "seed_fixture": "Seed / fixture",
    _SNAPSHOT_TRUTH_LEVEL: "Snapshot fallback",
    "registry_metadata": "Registry metadata",
    "scheduled_tick": "Scheduled tick",
    "reconciled_live_proof": "Reconciled live truth",
    "proven_live_evidence": "Proven live truth",
}
_TRUTH_SOURCE_DESCRIPTIONS = {
    "seed_fixture": "Seed or fixture data; never accepted as liveness proof.",
    _SNAPSHOT_TRUTH_LEVEL: "BFF local snapshot fallback; visible for inspection but not live proof.",
    "registry_metadata": "Durable static loop catalog metadata.",
    "scheduled_tick": "Scheduler or worker tick evidence without reconciliation proof.",
    "reconciled_live_proof": "Desired-vs-actual reconciliation evidence from a non-snapshot source.",
    "proven_live_evidence": "Target-environment live evidence with liveness, recovery, and readback.",
}


class LoopInventoryEntry(BaseModel):
    """Stable catalog contract for one loop while preserving extensions.

    The inventory describes only stable loop/spec/owner declarations.  It must
    not turn a catalog's historical maturity or task references into a current
    runtime statement; ``LoopHealthEntry`` owns that projection.
    """

    model_config = ConfigDict(extra="allow")

    loop_id: str
    classification: Literal["canonical", "composite_overlay"]
    live_status: Dict[str, Any] = Field(default_factory=dict)


class LoopHealthEntry(LoopInventoryEntry):
    """One composed, current-record-backed operator health row."""

    read_model: Literal["loop_health"]
    runtime_maturity: Dict[str, Any] = Field(default_factory=dict)
    controller_health: Dict[str, Any] = Field(default_factory=dict)
    evidence_packet: Dict[str, Any] = Field(default_factory=dict)


class LoopInventoryListEnvelope(BaseModel):
    data: List[LoopInventoryEntry]
    items: List[LoopInventoryEntry]
    page_info: Dict[str, Any]
    meta: Dict[str, Any]


class LoopInventoryDetailEnvelope(BaseModel):
    data: LoopInventoryEntry
    meta: Dict[str, Any]


class LoopHealthListEnvelope(BaseModel):
    data: List[LoopHealthEntry]
    items: List[LoopHealthEntry]
    page_info: Dict[str, Any]
    meta: Dict[str, Any]


class LoopHealthDetailEnvelope(BaseModel):
    data: LoopHealthEntry
    meta: Dict[str, Any]


def truth_label_payload() -> Dict[str, Dict[str, Any]]:
    return {
        level: {
            "truth_level": level,
            "truth_bucket": _TRUTH_SOURCE_TYPES[level],
            "source_type": _TRUTH_SOURCE_TYPES[level],
            "rank": _TRUTH_LEVEL_RANKS[level],
            "label": _TRUTH_SOURCE_LABELS[level],
            "description": _TRUTH_SOURCE_DESCRIPTIONS[level],
            "accepted_as_live": level in _LIVE_EVIDENCE_LEVELS,
        }
        for level in _TRUTH_LEVEL_ORDER
    }


def _load_registry() -> Dict[str, Any]:
    return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))


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


def _health_record_evidence_bases(record: Dict[str, Any]) -> List[str]:
    packet = record.get("evidence_packet") if isinstance(record.get("evidence_packet"), dict) else {}
    truth_source = record.get("truth_source") if isinstance(record.get("truth_source"), dict) else {}
    bases: List[str] = []
    for container in (record, packet, truth_source):
        for key in ("evidence_basis", "truth_basis", "provenance_type"):
            clean = str(container.get(key) or "").strip()
            if clean and clean not in bases:
                bases.append(clean)
    return bases


def _health_record_evidence_basis(record: Dict[str, Any]) -> str:
    bases = _health_record_evidence_bases(record)
    if len(bases) > 1:
        return "conflicting"
    if bases:
        return bases[0]
    return "missing"


def _is_archived_task_ref(value: Any) -> bool:
    clean = str(value or "").strip().lower().replace("\\", "/")
    return bool(
        clean.startswith("ai-task-archive/")
        or "/ai-task-archive/" in clean
    )


def _health_record_runtime_refs(record: Dict[str, Any]) -> List[str]:
    return [ref for ref in _health_record_refs(record) if not _is_archived_task_ref(ref)]


def _controller_heartbeat_is_current(value: Any) -> bool:
    clean = str(value or "").strip()
    if not clean:
        return False
    try:
        parsed = datetime.fromisoformat(clean.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age_seconds = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    return (
        -_CONTROLLER_RECORD_MAX_FUTURE_SKEW_SECONDS
        <= age_seconds
        <= _CONTROLLER_RECORD_MAX_AGE_SECONDS
    )


def _runtime_controller_record_qualified(
    record: Dict[str, Any],
    health_source: str,
    expected_controller_name: Any,
    *,
    classification: str = "canonical",
) -> bool:
    if not record or health_source not in _ACCEPTED_RUNTIME_HEALTH_SOURCES:
        return False
    if classification == "composite_overlay":
        return False
    evidence_bases = _health_record_evidence_bases(record)
    if (
        len(evidence_bases) != 1
        or evidence_bases[0] not in _ACCEPTED_RUNTIME_EVIDENCE_BASES
    ):
        return False
    if not _health_record_runtime_refs(record):
        return False
    raw_health = _dict_or_empty(
        record.get("controller_health")
        or record.get("controller")
    )
    reported_status = str(
        raw_health.get("status")
        or record.get("controller_status")
        or ""
    ).strip().lower()
    controller_name = str(
        raw_health.get("controller_name")
        or raw_health.get("name")
        or ""
    ).strip()
    expected_name = str(expected_controller_name or "").strip()
    heartbeat = str(
        raw_health.get("last_heartbeat_at")
        or record.get("last_heartbeat_at")
        or ""
    ).strip()

    # Worker functional health overrides process readiness
    worker_functional_health = _dict_or_empty(
        record.get("worker_health")
        or record.get("functional_health")
        or raw_health.get("worker_health")
        or raw_health.get("functional_health")
    )
    if worker_functional_health:
        if worker_functional_health.get("ready") is False or worker_functional_health.get("ok") is False:
            return False
        wf_status = str(worker_functional_health.get("status") or "").strip().lower()
        if wf_status in {"degraded", "unhealthy", "failed", "down", "error", "unavailable"}:
            return False
    if record.get("ready") is False or raw_health.get("ready") is False or record.get("functional_ready") is False:
        return False

    # A blank catalog controller identity can never be satisfied by any
    # reported name; accepting "any nonblank name" here was a second,
    # permissive validator layered on top of the catalog contract.
    name_matches = bool(expected_name) and controller_name == expected_name

    desired_state_presence = _dict_or_empty(record.get("desired_state_presence"))
    downstream_actual_state = _dict_or_empty(record.get("downstream_actual_state"))
    authoritative_desired_and_actual = bool(
        desired_state_presence.get("authoritative")
        and downstream_actual_state.get("authoritative")
    )

    return bool(
        reported_status in _ACCEPTED_CONTROLLER_HEALTH_STATUSES
        and controller_name
        and name_matches
        and authoritative_desired_and_actual
        and _controller_heartbeat_is_current(heartbeat)
        and _health_record_refs(record)
    )


def _truth_source_from_profile(
    level: str,
    health_record: Dict[str, Any],
    health_source: str,
    expected_controller_name: Any,
    *,
    classification: str = "canonical",
) -> Dict[str, Any]:
    health_truth_level = _health_record_truth_level(health_record)
    health_refs = _health_record_refs(health_record)
    status = "present" if level == "registry_metadata" else "missing"
    refs: List[str] = [_REGISTRY_REF] if level == "registry_metadata" else []
    note = None
    source = "bff_local_registry" if level == "registry_metadata" else "missing"

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

    runtime_record_qualified = _runtime_controller_record_qualified(
        health_record,
        health_source,
        expected_controller_name,
        classification=classification,
    )
    evidence_basis = _health_record_evidence_basis(health_record)
    accepted_as_live = (
        level in _LIVE_EVIDENCE_LEVELS
        and status == "present"
        and health_truth_level == level
        and runtime_record_qualified
    )
    if accepted_as_live:
        operator_note = "Accepted as live liveness proof."
    elif level == "seed_fixture":
        operator_note = "Seed or fixture data is not live proof."
    elif level == _SNAPSHOT_TRUTH_LEVEL or source == "local_snapshot":
        operator_note = "Local snapshot fallback is not live proof."
    elif level == "registry_metadata":
        operator_note = "Registry metadata identifies the loop but does not prove runtime liveness."
    elif level == "scheduled_tick":
        operator_note = "Scheduled tick evidence does not prove desired-vs-actual reconciliation."
    elif level in _LIVE_EVIDENCE_LEVELS and status == "present" and not runtime_record_qualified:
        operator_note = (
            "Live truth is not accepted without a current controller or target-runtime "
            "record; task archive completion is reference-only."
        )
    else:
        operator_note = "Live proof is missing or not present from an accepted source."

    return {
        "truth_level": level,
        "truth_bucket": _TRUTH_SOURCE_TYPES[level],
        "source_type": _TRUTH_SOURCE_TYPES[level],
        "label": _TRUTH_SOURCE_LABELS[level],
        "description": _TRUTH_SOURCE_DESCRIPTIONS[level],
        "rank": _TRUTH_LEVEL_RANKS[level],
        "status": status,
        "source": source,
        "evidence_basis": evidence_basis,
        "evidence_bases": _health_record_evidence_bases(health_record),
        "runtime_controller_record_qualified": runtime_record_qualified,
        "runtime_record_required": level in _LIVE_EVIDENCE_LEVELS,
        "refs": refs,
        "note": note,
        "accepted_as_live": accepted_as_live,
        "is_live_truth_level": level in _LIVE_EVIDENCE_LEVELS,
        "operator_note": operator_note,
        "operator_visibility": "live_proof" if accepted_as_live else "not_live_proof",
    }


def _truth_sources(
    health_record: Dict[str, Any],
    health_source: str,
    expected_controller_name: Any,
    *,
    classification: str = "canonical",
) -> List[Dict[str, Any]]:
    return [
        _truth_source_from_profile(
            level,
            health_record,
            health_source,
            expected_controller_name,
            classification=classification,
        )
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


def _operator_truth_source(
    truth_sources: List[Dict[str, Any]],
    highest: Dict[str, Any],
) -> Dict[str, Any]:
    accepted_live = [
        source
        for source in truth_sources
        if source.get("status") == "present" and source.get("accepted_as_live")
    ]
    if accepted_live:
        selected = max(
            accepted_live,
            key=lambda source: (
                int(source.get("rank") or 0),
                str(source.get("truth_level") or ""),
            ),
        )
        degraded_reason = None
    else:
        snapshot = next(
            (
                source
                for source in truth_sources
                if source.get("truth_level") == _SNAPSHOT_TRUTH_LEVEL
                and source.get("status") == "present"
            ),
            None,
        )
        if snapshot is not None:
            selected = snapshot
        else:
            selected = highest
        degraded_reason = selected.get("operator_note") or "No accepted live truth is present."

    truth_level = str(selected.get("truth_level") or "missing")
    return {
        "truth_level": truth_level,
        "truth_bucket": selected.get("truth_bucket"),
        "source_type": selected.get("source_type"),
        "source": selected.get("source"),
        "rank": selected.get("rank"),
        "status": selected.get("status"),
        "label": selected.get("label") or truth_level.replace("_", " "),
        "description": selected.get("description"),
        "accepted_as_live": bool(selected.get("accepted_as_live")),
        "is_live_truth": bool(selected.get("accepted_as_live")),
        "degraded": not bool(selected.get("accepted_as_live")),
        "degraded_reason": degraded_reason,
        "highest_available_truth_level": highest.get("truth_level"),
        "highest_available_source": highest.get("source"),
        "highest_available_label": highest.get("label"),
        "truth_labels": truth_label_payload(),
    }


def _controller_contract_declaration(loop: Dict[str, Any]) -> Dict[str, Any]:
    """Report what the catalog claims about one loop's controller, nothing more.

    A declared controller contract is an implementation claim, not liveness.
    ``contract_complete`` only says the catalog filled in every field a
    reconciled claim would later need; it never admits runtime truth.
    """

    controller = loop.get("controller_contract") if isinstance(loop.get("controller_contract"), dict) else {}
    status = str(controller.get("status") or "unknown")
    implemented = status in _IMPLEMENTED_CONTROLLER_STATUSES
    missing_fields = [
        field
        for field in _CONTROLLER_CONTRACT_REQUIRED_FIELDS
        if not str(controller.get(field) or "").strip()
    ]
    return {
        "status": status,
        "controller_implemented": implemented,
        "controller_name": controller.get("controller_name"),
        "contract_complete": implemented and not missing_fields,
        "missing_contract_fields": missing_fields,
        "declared_only": True,
        "note": (
            "Catalog declares an implemented controller; liveness still requires a "
            "current accepted controller runtime record."
            if implemented
            else "Catalog declares no implemented controller for this loop."
        ),
    }


def _controller_contract_coverage(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    declarations = [
        (str(entry.get("loop_id") or ""), _controller_contract_declaration(entry))
        for entry in entries
    ]
    implemented_ids = [
        loop_id
        for loop_id, declaration in declarations
        if declaration["controller_implemented"]
    ]
    incomplete_ids = [
        loop_id
        for loop_id, declaration in declarations
        if declaration["controller_implemented"] and not declaration["contract_complete"]
    ]
    return {
        "declared_controller_loop_ids": implemented_ids,
        "declared_controller_count": len(implemented_ids),
        "no_declared_controller_count": len(declarations) - len(implemented_ids),
        "incomplete_contract_loop_ids": incomplete_ids,
        "controller_names": {
            loop_id: declaration["controller_name"]
            for loop_id, declaration in declarations
            if declaration["controller_implemented"]
        },
        "note": (
            "A declared controller contract is an implementation claim only; the "
            "loop-health read model still requires a current accepted controller "
            "record before any live truth is shown."
        ),
    }


def _registry_entries(registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    canonical = registry.get("loops") if isinstance(registry.get("loops"), list) else []
    overlays = (
        registry.get("composite_overlays")
        if isinstance(registry.get("composite_overlays"), list)
        else []
    )
    return [
        entry
        for entry in [*canonical, *overlays]
        if isinstance(entry, dict)
    ]


def _canonical_registry_entries(registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The twelve stable canonical loops only; excludes composite overlays.

    Loop-health truth is scoped to these twelve rows.  A composite overlay
    such as ``per_persona_ooda`` is catalog inventory, not current
    controller-runtime truth, and is surfaced separately by the inventory
    read model instead.
    """

    canonical = registry.get("loops") if isinstance(registry.get("loops"), list) else []
    return [entry for entry in canonical if isinstance(entry, dict)]


def loop_inventory_meta() -> Dict[str, Any]:
    registry = _load_registry()
    canonical = registry.get("loops") if isinstance(registry.get("loops"), list) else []
    overlays = (
        registry.get("composite_overlays")
        if isinstance(registry.get("composite_overlays"), list)
        else []
    )
    entries = _registry_entries(registry)
    return {
        "schema_version": registry.get("schema_version"),
        "catalog_id": registry.get("catalog_id"),
        "created_at": registry.get("created_at"),
        "source_documents": list(registry.get("source_documents") or []),
        "catalog_decisions": deepcopy(registry.get("catalog_decisions") or {}),
        "inventory_counts": {
            "canonical_loop_count": len(canonical),
            "composite_overlay_count": len(overlays),
            "inventory_entry_count": len(entries),
        },
        "continuous_resident_execution_loop_ids": [
            str(entry.get("loop_id") or "")
            for entry in entries
            if isinstance(entry.get("trigger_model"), dict)
            and entry["trigger_model"].get("continuous") is True
        ],
        "runtime_projection_policy": {
            "catalog_role": "stable_loop_spec_and_owner_contract_only",
            "accepted_live_evidence_bases": sorted(_ACCEPTED_RUNTIME_EVIDENCE_BASES),
            "archived_task_completion": "reference_only",
            "archived_task_completion_accepted_as_liveness": False,
            "controller_record_max_age_seconds": _CONTROLLER_RECORD_MAX_AGE_SECONDS,
        },
        "controller_contract_coverage": _controller_contract_coverage(entries),
        "registry_ref": _REGISTRY_REF,
    }


def _project_loop(loop: Dict[str, Any]) -> Dict[str, Any]:
    loop_id = str(loop.get("loop_id") or "")
    owner = deepcopy(loop.get("owner") or {})
    controller = deepcopy(loop.get("controller_contract") or {})

    return {
        "id": loop_id,
        "loop_id": loop_id,
        "classification": loop.get("classification"),
        "composed_of": deepcopy(loop.get("composed_of") or []),
        "name": loop.get("name"),
        "policy_ref": deepcopy(loop.get("policy_ref") or {}),
        "owner": owner,
        "trigger_model": deepcopy(loop.get("trigger_model") or {}),
        "desired_state": deepcopy(loop.get("desired_state") or {}),
        "actual_state": deepcopy(loop.get("actual_state") or {}),
        "controller": controller,
        "controller_contract_declaration": _controller_contract_declaration(loop),
        "truth_source": {
            "level": "registry_metadata",
            "source": "static_json_registry",
            "registry_ref": _REGISTRY_REF,
            "live_truth_levels": list(_LIVE_EVIDENCE_LEVELS),
        },
        "live_status": {
            "is_live": False,
            "is_reconciled": False,
            "has_live_evidence": False,
            "reason": "catalog metadata is not live liveness proof",
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
    *,
    classification: str = "canonical",
) -> Dict[str, Any]:
    raw_health = _dict_or_empty(
        health_record.get("controller_health")
        or health_record.get("controller")
    )
    contract_status = str(controller.get("status") or "unknown")
    reported_status = (
        raw_health.get("status")
        or health_record.get("controller_status")
    )
    runtime_record_qualified = _runtime_controller_record_qualified(
        health_record,
        health_source,
        controller.get("controller_name"),
        classification=classification,
    )
    heartbeat = raw_health.get("last_heartbeat_at") or health_record.get("last_heartbeat_at")
    current_record_accepted = bool(runtime_record_qualified)
    status = (
        reported_status
        if current_record_accepted
        else ("not_implemented" if contract_status == "not_implemented" and not health_record else "unobserved")
    )
    source = health_source if current_record_accepted else "registry_metadata"
    evidence_bases = _health_record_evidence_bases(health_record)
    evidence_refs = _health_record_refs(health_record)
    runtime_evidence_refs = _health_record_runtime_refs(health_record)
    reported_controller_name = str(
        raw_health.get("controller_name")
        or raw_health.get("name")
        or ""
    ).strip()
    expected_controller_name = str(controller.get("controller_name") or "").strip()
    worker_functional_health = _dict_or_empty(
        health_record.get("worker_health")
        or health_record.get("functional_health")
        or raw_health.get("worker_health")
        or raw_health.get("functional_health")
    )
    functional_degraded = bool(
        worker_functional_health.get("ready") is False
        or worker_functional_health.get("ok") is False
        or str(worker_functional_health.get("status") or "").strip().lower() in {
            "degraded",
            "unhealthy",
            "failed",
            "down",
            "error",
            "unavailable",
        }
        or health_record.get("ready") is False
        or raw_health.get("ready") is False
        or health_record.get("functional_ready") is False
    )
    if current_record_accepted:
        rejection_reason = None
    elif classification == "composite_overlay":
        rejection_reason = "composite overlay loops do not accept direct controller runtime records"
    elif functional_degraded:
        rejection_reason = "worker functional health is degraded despite process readiness"
    elif len(evidence_bases) > 1:
        rejection_reason = "record declares conflicting evidence provenance"
    elif evidence_refs and not runtime_evidence_refs:
        rejection_reason = "task archive completion is reference-only, not runtime evidence"
    elif (
        reported_controller_name
        and expected_controller_name
        and reported_controller_name != expected_controller_name
    ):
        rejection_reason = "runtime controller identity does not match catalog contract"
    elif not reported_controller_name and health_record:
        rejection_reason = "record lacks accepted current controller-runtime provenance"
    else:
        rejection_reason = "record lacks accepted current controller-runtime provenance"
    return {
        "status": status,
        "source": source,
        "reported_status": reported_status,
        "reported_source": health_source if health_record else "missing",
        "evidence_basis": _health_record_evidence_basis(health_record),
        "evidence_bases": evidence_bases,
        "runtime_evidence_refs": runtime_evidence_refs,
        "runtime_record_qualified": runtime_record_qualified,
        "freshness": {
            "last_heartbeat_at": heartbeat,
            "current": _controller_heartbeat_is_current(heartbeat),
            "max_age_seconds": _CONTROLLER_RECORD_MAX_AGE_SECONDS,
        },
        "current_record_accepted": current_record_accepted,
        "rejection_reason": rejection_reason,
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
        checked_at = downstream.get("checked_at") or downstream.get("captured_at")
        authoritative = bool(
            checked_at
            and health_source in _ACCEPTED_RUNTIME_HEALTH_SOURCES
            and str(downstream.get("status") or "").strip().lower()
            not in {"", "unknown", "unobserved"}
        )
        return {
            "status": downstream.get("status") if authoritative else "unobserved",
            "source": downstream.get("source") or health_source,
            "authoritative": authoritative,
            "reported_status": downstream.get("status"),
            "summary": downstream.get("summary") or downstream.get("message"),
            "checked_at": checked_at if authoritative else None,
            "sources": deepcopy(downstream.get("sources") or []),
            "query": downstream.get("query") or actual_state.get("query"),
        }
    return {
        "status": "unobserved",
        "source": "registry_metadata",
        "authoritative": False,
        "reported_status": actual_state.get("query_status"),
        "summary": actual_state.get("query"),
        "checked_at": None,
        "sources": deepcopy(actual_state.get("sources") or []),
        "query": actual_state.get("query"),
    }


def _project_desired_state_presence(
    desired_state: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
) -> Dict[str, Any]:
    observed = _dict_or_empty(health_record.get("desired_state_presence"))
    checked_at = observed.get("checked_at") or observed.get("captured_at")
    if observed:
        present = observed.get("present")
        authoritative = bool(
            isinstance(present, bool)
            and checked_at
            and health_source in _ACCEPTED_RUNTIME_HEALTH_SOURCES
        )
        return {
            "status": (
                ("present" if present else "absent")
                if authoritative
                else "configured_unobserved"
            ),
            "present": present if authoritative else None,
            "authoritative": authoritative,
            "source": observed.get("source") or health_source,
            "summary": observed.get("summary"),
            "checked_at": checked_at if authoritative else None,
            "sources": deepcopy(observed.get("sources") or []),
            "query": observed.get("query") or desired_state.get("query"),
        }
    return {
        "status": (
            "declared_unobserved" if desired_state.get("query") else "missing"
        ),
        "present": None,
        "authoritative": False,
        "source": "registry_metadata",
        "summary": desired_state.get("query"),
        "checked_at": None,
        "sources": deepcopy(desired_state.get("sources") or []),
        "query": desired_state.get("query"),
    }


def _project_evidence_packet(
    loop_id: str,
    controller: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
    *,
    classification: str = "canonical",
) -> Dict[str, Any]:
    packet = _dict_or_empty(health_record.get("evidence_packet"))
    truth_sources = _truth_sources(
        health_record,
        health_source,
        controller.get("controller_name"),
        classification=classification,
    )
    highest = _highest_present_truth_source(truth_sources)
    operator_truth = _operator_truth_source(truth_sources, highest)
    refs = _dedupe_strings([_REGISTRY_REF, *_health_record_refs(health_record)])
    accepted_live_sources = [
        source
        for source in truth_sources
        if source.get("status") == "present" and source.get("accepted_as_live")
    ]
    accepted_live_levels = {
        str(source.get("truth_level") or "")
        for source in accepted_live_sources
    }
    accepted_live_liveness = bool(accepted_live_sources)
    return {
        "id": packet.get("id") or packet.get("packet_id") or f"loop-health-{loop_id}",
        "packet_id": packet.get("packet_id") or packet.get("id") or f"loop-health-{loop_id}",
        "loop_id": loop_id,
        "source": health_source if health_record else "bff_local_registry",
        "registry_ref": _REGISTRY_REF,
        "runtime_record_evidence_basis": _health_record_evidence_basis(health_record),
        "runtime_record_evidence_bases": _health_record_evidence_bases(health_record),
        "runtime_evidence_refs": _health_record_runtime_refs(health_record),
        "runtime_controller_record_qualified": _runtime_controller_record_qualified(
            health_record,
            health_source,
            controller.get("controller_name"),
            classification=classification,
        ),
        "archived_task_completion_accepted": False,
        "highest_truth_level": highest.get("truth_level"),
        "highest_truth_rank": highest.get("rank"),
        "accepted_live_liveness": accepted_live_liveness,
        "operator_truth": operator_truth,
        "can_claim_reconciled": (
            any(
                int(source.get("rank") or -1)
                >= _TRUTH_LEVEL_RANKS["reconciled_live_proof"]
                for source in accepted_live_sources
            )
        ),
        "can_claim_proven_live": (
            "proven_live_evidence" in accepted_live_levels
        ),
        "captured_at": packet.get("captured_at") or health_record.get("captured_at") or health_record.get("updated_at"),
        "refs": refs,
        "truth_sources": truth_sources,
    }


def _runtime_maturity(
    controller_health: Dict[str, Any],
    evidence_packet: Dict[str, Any],
) -> Dict[str, Any]:
    """Derive runtime maturity from a current accepted record only.

    A catalog's old maturity/task fields are intentionally not an input here.
    This makes an absent, stale, malformed, or functionally degraded record
    visibly unobserved instead of preserving a historical implementation claim.
    """

    accepted = bool(controller_health.get("current_record_accepted"))
    truth_level = str(evidence_packet.get("highest_truth_level") or "missing")
    if not accepted:
        state = "unobserved"
    elif truth_level == "proven_live_evidence":
        state = "proven-live"
    elif truth_level == "reconciled_live_proof":
        state = "reconciled"
    else:
        state = "observed"
    return {
        "state": state,
        "source": controller_health.get("source") if accepted else "missing",
        "truth_level": truth_level,
        "current_record_accepted": accepted,
        "reason": (
            "derived from the current accepted controller-runtime record"
            if accepted
            else controller_health.get("rejection_reason")
            or "no current accepted controller-runtime record"
        ),
    }


def _project_loop_health(
    loop: Dict[str, Any],
    health_record: Dict[str, Any],
    health_source: str,
) -> Dict[str, Any]:
    projected = _project_loop(loop)
    loop_id = str(projected.get("loop_id") or "")
    classification = str(loop.get("classification") or "canonical")
    controller = _dict_or_empty(loop.get("controller_contract"))
    desired_state = _dict_or_empty(loop.get("desired_state"))
    actual_state = _dict_or_empty(loop.get("actual_state"))
    evidence_packet = _project_evidence_packet(
        loop_id,
        controller,
        health_record,
        health_source,
        classification=classification,
    )
    controller_health = _project_controller_health(
        controller,
        health_record,
        health_source,
        classification=classification,
    )
    projected.update(
        {
            "read_model": "loop_health",
            "controller_health": controller_health,
            "runtime_maturity": _runtime_maturity(controller_health, evidence_packet),
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
            "desired_state_presence": _project_desired_state_presence(
                desired_state,
                health_record,
                health_source,
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
        "operator_truth": evidence_packet["operator_truth"],
    }
    has_live_evidence = bool(evidence_packet["accepted_live_liveness"])
    operator_truth = evidence_packet["operator_truth"]
    projected["live_status"] = {
        "is_live": bool(evidence_packet["can_claim_proven_live"]),
        "is_reconciled": bool(evidence_packet["can_claim_reconciled"]),
        "has_live_evidence": has_live_evidence,
        "reason": (
            "live evidence is present in the loop health evidence packet"
            if has_live_evidence
            else operator_truth.get("degraded_reason")
            or "no reconciled or proven-live evidence packet is present"
        ),
        "operator_truth": operator_truth,
    }
    return projected


def list_loop_inventory_entries() -> List[Dict[str, Any]]:
    registry = _load_registry()
    return [_project_loop(loop) for loop in _registry_entries(registry)]


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
    """Return exactly the twelve canonical loop-health rows.

    The composite overlay is catalog inventory, not current controller
    truth, and is intentionally excluded here.
    """

    registry = _load_registry()
    loops = _canonical_registry_entries(registry)
    records_by_loop = _normalize_health_records(health_records)
    projected_list = []
    for loop in loops:
        loop_id = str(loop.get("loop_id") or "")
        if loop_id in records_by_loop:
            rec = records_by_loop[loop_id]
            src = rec.get("_health_source") or health_source
        else:
            rec = {}
            src = "missing"
        projected_list.append(_project_loop_health(loop, rec, src))
    return projected_list


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
