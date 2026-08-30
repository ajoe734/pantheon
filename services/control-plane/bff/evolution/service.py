"""Evolution domain service and projection helpers.

Encapsulates:
- Evolution decisions, freeze orders, and rollback projections
- Inspiration and lineage graph projections
- Telemetry summary and performance projections
- Evolution journal aggregation, filtering, summary metrics, and persona lineage traversal
- OODA packet list helpers and feature flag controls
- Evolution programs core operations
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

log = logging.getLogger(__name__)

_EW04_ALLOWED_SURFACE_STATES = {"fresh", "stale", "unavailable"}

_EVOLUTION_JOURNAL_REGISTERED_SEED_EXACT_IDS = {
    "87c655c3e3c9", "inc-87c655c3e3c9", "rb-001", "fo-001", "btc-drift",
    "inc-20260410-001", "inc-20260409-002", "pm-20260409-002",
    "plan-f-042", "artifact-042", "runtime-042", "binding-042",
}
_EVOLUTION_JOURNAL_REGISTERED_SEED_PREFIXES = ("evo-vslice-", "ev-seed-")

_EVOLUTION_JOURNAL_TYPE_ALIASES = {
    "decision": "evolution_decision",
    "evolution": "evolution_decision",
    "evolution_decision": "evolution_decision",
    "evolution_decisions": "evolution_decision",
    "mutation": "mutation_review",
    "mutation_review": "mutation_review",
    "mutation_reviews": "mutation_review",
    "postmortem": "postmortem",
    "postmortems": "postmortem",
    "rollback": "rollback",
    "rollbacks": "rollback",
    "freeze": "freeze_order",
    "freeze_order": "freeze_order",
    "freeze_orders": "freeze_order",
}

_EVOLUTION_JOURNAL_REFERENCE_FIELD_CATEGORY = {
    "artifact_id": "artifact",
    "persona_id": "persona",
    "runtime_id": "runtime",
    "runtime_binding_id": "binding",
    "persona_capital_binding_id": "binding",
    "incident_id": "incident",
    "incident_ref": "incident",
    "linked_incident_id": "incident",
    "capital_pool_id": "pool",
    "pool_id": "pool",
    "plan_id": "plan",
    "deployment_plan_id": "plan",
}

_EVOLUTION_JOURNAL_TARGET_TYPE_CATEGORY = {
    "persona": "persona",
    "runtime": "runtime",
    "binding": "binding",
    "runtime_binding": "binding",
    "persona_capital_binding": "binding",
    "plan": "plan",
    "deployment_plan": "plan",
    "pool": "pool",
    "capital_pool": "pool",
    "candidate_artifact": "artifact",
    "artifact": "artifact",
    "incident": "incident",
}

_MANAGEMENT_CAMEL_KEY_RE = re.compile(r"[A-Z]")


def _management_first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _management_record_id(record: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _management_count_by(records: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _management_camel_to_snake_key(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return value.lower()


def _management_prune_camel_aliases(value: Any) -> Any:
    """Keep snake_case when a dict carries both snake_case and camelCase aliases."""
    if isinstance(value, list):
        return [_management_prune_camel_aliases(item) for item in value]
    if not isinstance(value, dict):
        return value
    keys = {key for key in value if isinstance(key, str)}
    pruned: Dict[str, Any] = {}
    for key, nested in value.items():
        if isinstance(key, str) and _MANAGEMENT_CAMEL_KEY_RE.search(key):
            snake_key = _management_camel_to_snake_key(key)
            if snake_key in keys:
                continue
        pruned[key] = _management_prune_camel_aliases(nested)
    return pruned


# --------------------------------------------------------------------------- #
# Contract Projections
# --------------------------------------------------------------------------- #

def project_evolution_decision_contract(decision: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(decision)
    payload["updated_at"] = decision.get("updated_at")
    payload["notes"] = decision.get("notes")
    return payload


def project_freeze_order_contract(order: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(order)
    payload["freeze_order_id"] = order.get("freeze_order_id") or order.get("id")
    payload["issued_at"] = order.get("issued_at") or order.get("created_at")
    return payload


def project_rollback_contract(rollback: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(rollback)
    payload["rollback_id"] = rollback.get("rollback_id") or rollback.get("id")
    payload["executed_at"] = rollback.get("executed_at") or rollback.get("initiated_at")
    return payload


# --------------------------------------------------------------------------- #
# Inspiration Graph Helpers
# --------------------------------------------------------------------------- #

def ew04_inspiration_surface_state(
    projection: Optional[Dict[str, Any]],
    *,
    artifact_exists: bool,
    source: str = "ok",
    base_status: str = "ok",
) -> str:
    explicit_state = (
        projection.get("meta", {})
        .get("surfaces", {})
        .get("inspiration")
        if projection
        else None
    )
    explicit_state = str(explicit_state or "").strip().lower()
    if explicit_state in _EW04_ALLOWED_SURFACE_STATES:
        return explicit_state

    if source == "missing" or base_status == "unavailable":
        return "unavailable"
    if source == "local_snapshot" or base_status == "degraded":
        return "stale"
    if artifact_exists:
        return "fresh"
    return "unavailable"


def ew04_inspiration_payload(
    artifact_id: str,
    projection: Optional[Dict[str, Any]],
    *,
    snapshot_at: str,
    artifact_exists: bool,
    source: str = "ok",
    base_status: str = "ok",
) -> Dict[str, Any]:
    if projection:
        payload = json.loads(json.dumps(projection))
    else:
        payload = {
            "artifact_id": artifact_id,
            "inspiration_edges": [],
            "strategy_tags": [],
            "meta": {
                "snapshot_at": snapshot_at,
                "surfaces": {},
            },
        }

    payload["artifact_id"] = artifact_id
    payload["inspiration_edges"] = list(payload.get("inspiration_edges") or [])
    if "strategy_tags" in payload:
        payload["strategy_tags"] = list(payload.get("strategy_tags") or [])
    else:
        payload["strategy_tags"] = []

    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        payload["meta"] = meta
    meta["snapshot_at"] = str(meta.get("snapshot_at") or snapshot_at)
    surfaces = meta.get("surfaces")
    if not isinstance(surfaces, dict):
        surfaces = {}
        meta["surfaces"] = surfaces
    surfaces["inspiration"] = ew04_inspiration_surface_state(
        projection,
        artifact_exists=artifact_exists,
        source=source,
        base_status=base_status,
    )
    return payload


def ew04_inspiration_projection_from_lineage_edges(
    artifact_id: str,
    read_store: Any,
    *,
    utc_now: Callable[[], str],
) -> Optional[Dict[str, Any]]:
    source = getattr(read_store, "dataset_source", lambda ds: "ok")("lineage_edges")
    if source == "missing":
        return None
    lineage_edges = getattr(read_store, "list_lineage_edges", lambda **kw: [])(artifact_id=artifact_id)
    if not lineage_edges:
        return None
    surface_state = "fresh"
    if source in {"missing", "local_snapshot"}:
        surface_state = "stale"

    inspiration_edges: List[Dict[str, Any]] = []
    strategy_tags = set()
    for edge in lineage_edges:
        from_artifact_id = str(edge.get("from_artifact_id") or "").strip()
        to_artifact_id = str(edge.get("to_artifact_id") or "").strip()
        source_artifact_id = from_artifact_id if to_artifact_id == artifact_id else to_artifact_id
        relationship_type = str(edge.get("edge_type") or edge.get("relationship") or "").strip()
        if not source_artifact_id or not relationship_type:
            continue
        strategy_id = str(edge.get("strategy_id") or "").strip()
        if strategy_id:
            strategy_tags.add(strategy_id)
        raw_influence = edge.get("influence_weight")
        influence_weight = float(raw_influence) if raw_influence is not None else None
        influence_state = str(edge.get("influence_state") or ("confirmed_influence" if influence_weight is not None else "influence_unknown"))
        inspiration_edges.append(
            {
                "lineage_edge_id": edge.get("id"),
                "source_artifact_id": source_artifact_id,
                "relationship_type": relationship_type,
                "influence_weight": influence_weight,
                "influence_state": influence_state,
            }
        )
    return {
        "artifact_id": artifact_id,
        "inspiration_edges": inspiration_edges,
        "strategy_tags": sorted(strategy_tags),
        "meta": {
            "snapshot_at": utc_now(),
            "surfaces": {"inspiration": surface_state},
        },
    }


# --------------------------------------------------------------------------- #
# Evolution Journal Helpers
# --------------------------------------------------------------------------- #

def evolution_journal_is_registered_seed_id(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False
    if normalized in _EVOLUTION_JOURNAL_REGISTERED_SEED_EXACT_IDS:
        return True
    return any(
        normalized.startswith(prefix)
        for prefix in _EVOLUTION_JOURNAL_REGISTERED_SEED_PREFIXES
    )


def evolution_journal_csv_filter(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    requested = {part.strip().lower() for part in value.split(",") if part.strip()}
    return requested or None


def evolution_journal_type_filter(value: Optional[str]) -> Optional[Set[str]]:
    requested = evolution_journal_csv_filter(value)
    if not requested:
        return None
    return {
        _EVOLUTION_JOURNAL_TYPE_ALIASES.get(entry_type, entry_type)
        for entry_type in requested
    }


def evolution_journal_status(record: Dict[str, Any]) -> str:
    return str(
        _management_first_non_empty(
            record.get("status"),
            record.get("decision_state"),
            record.get("state"),
            "unknown",
        )
        or "unknown"
    ).strip().lower()


def evolution_journal_timestamp(record: Dict[str, Any]) -> str:
    return str(
        _management_first_non_empty(
            record.get("updated_at"),
            record.get("updatedAt"),
            record.get("published_at"),
            record.get("completed_at"),
            record.get("executed_at"),
            record.get("initiated_at"),
            record.get("issued_at"),
            record.get("created_at"),
            record.get("createdAt"),
            record.get("triggered_at"),
        )
        or ""
    )


def evolution_journal_target(
    *,
    target_type: Any = None,
    target_id: Any = None,
    target_version: Any = None,
    incident_id: Any = None,
    runtime_id: Any = None,
    artifact_id: Any = None,
) -> Dict[str, Any]:
    resolved_type = str(
        _management_first_non_empty(
            target_type,
            "incident" if incident_id else None,
            "runtime" if runtime_id else None,
            "artifact" if artifact_id else None,
        )
        or ""
    ).strip()
    resolved_id = _management_first_non_empty(target_id, incident_id, runtime_id, artifact_id)
    return {
        "type": resolved_type or None,
        "id": resolved_id,
        "version": target_version,
    }


def evolution_journal_base_item(
    *,
    entry_type: str,
    source_id: str,
    title: str,
    summary: str,
    status: str,
    created_at: Any = None,
    updated_at: Any = None,
    occurred_at: Any = None,
    risk_level: Any = None,
    action_type: Any = None,
    target: Optional[Dict[str, Any]] = None,
    route: Optional[str] = None,
    bff_detail_path: Optional[str] = None,
) -> Dict[str, Any]:
    journal_id = f"{entry_type}:{source_id}"
    return {
        "id": journal_id,
        "journal_id": journal_id,
        "entryType": entry_type,
        "entry_type": entry_type,
        "source_id": source_id,
        "title": title,
        "summary": summary,
        "status": status,
        "risk_level": risk_level,
        "action_type": action_type,
        "target": target or {},
        "created_at": created_at,
        "updated_at": updated_at,
        "occurred_at": occurred_at or updated_at or created_at,
        "route": route,
        "bff_detail_path": bff_detail_path,
    }


def evolution_journal_decision_item(decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    decision_id = _management_record_id(decision, "decision_id", "id", "evolution_decision_id")
    if not decision_id:
        return None
    status = evolution_journal_status(decision)
    title = str(
        _management_first_non_empty(
            decision.get("title"),
            f"{str(decision.get('action_type') or 'Evolution').replace('_', ' ').title()} decision",
        )
    )
    summary = str(
        _management_first_non_empty(
            decision.get("summary"),
            decision.get("notes"),
            decision.get("rationale"),
            (decision.get("risk_assessment") or {}).get("risk_summary")
            if isinstance(decision.get("risk_assessment"), dict)
            else None,
            "Evolution decision recorded.",
        )
    )
    item = evolution_journal_base_item(
        entry_type="evolution_decision",
        source_id=decision_id,
        title=title,
        summary=summary,
        status=status,
        created_at=decision.get("created_at"),
        updated_at=decision.get("updated_at"),
        occurred_at=evolution_journal_timestamp(decision),
        risk_level=decision.get("risk_level"),
        action_type=decision.get("action_type"),
        target=evolution_journal_target(
            target_type=decision.get("target_type"),
            target_id=_management_first_non_empty(decision.get("target_id"), decision.get("artifact_id")),
            target_version=decision.get("target_version") or decision.get("artifact_version"),
            incident_id=decision.get("incident_ref") or decision.get("linked_incident_id"),
        ),
        route=f"/management/evolution-journal?decision={decision_id}",
        bff_detail_path=f"/api/v1/evolution-decisions/{decision_id}",
    )
    item["decision"] = json.loads(json.dumps(decision))
    item["record"] = item["decision"]
    return item


def evolution_journal_postmortem_item(postmortem: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    postmortem_id = _management_record_id(postmortem, "postmortem_id", "report_id", "id")
    if not postmortem_id:
        return None
    summary = str(
        _management_first_non_empty(
            postmortem.get("summary"),
            postmortem.get("root_cause"),
            postmortem.get("title"),
            "Postmortem record published.",
        )
    )
    item = evolution_journal_base_item(
        entry_type="postmortem",
        source_id=postmortem_id,
        title=str(postmortem.get("title") or f"Postmortem {postmortem_id}"),
        summary=summary,
        status=evolution_journal_status(postmortem),
        created_at=postmortem.get("created_at"),
        updated_at=postmortem.get("published_at") or postmortem.get("updated_at"),
        occurred_at=evolution_journal_timestamp(postmortem),
        action_type="postmortem",
        target=evolution_journal_target(
            target_type="incident",
            target_id=postmortem.get("incident_id"),
            runtime_id=postmortem.get("runtime_id"),
            artifact_id=postmortem.get("artifact_id"),
        ),
        route=f"/management/evolution-journal?postmortem={postmortem_id}",
        bff_detail_path=f"/api/v1/postmortems/{postmortem_id}",
    )
    item["postmortem"] = json.loads(json.dumps(postmortem))
    item["record"] = item["postmortem"]
    return item


def evolution_journal_freeze_order_item(order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    freeze_order_id = _management_record_id(order, "freeze_order_id", "id")
    if not freeze_order_id:
        return None
    projected = project_freeze_order_contract(order)
    item = evolution_journal_base_item(
        entry_type="freeze_order",
        source_id=freeze_order_id,
        title=str(order.get("title") or f"Freeze order {freeze_order_id}"),
        summary=str(order.get("reason") or "Freeze order recorded."),
        status=evolution_journal_status(order),
        created_at=order.get("created_at"),
        updated_at=order.get("updated_at") or order.get("issued_at"),
        occurred_at=evolution_journal_timestamp(projected),
        action_type="freeze",
        target=evolution_journal_target(
            target_type=order.get("scope") or "freeze_scope",
            target_id=order.get("target_id"),
            incident_id=order.get("incident_ref"),
        ),
        route=f"/management/evolution-journal?freeze_order={freeze_order_id}",
        bff_detail_path=f"/api/v1/freeze-orders?status={order.get('status') or ''}",
    )
    item["freezeOrder"] = json.loads(json.dumps(projected))
    item["freeze_order"] = item["freezeOrder"]
    item["record"] = item["freezeOrder"]
    return item


def evolution_journal_rollback_item(rollback: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rollback_id = _management_record_id(rollback, "rollback_id", "id")
    if not rollback_id:
        return None
    projected = project_rollback_contract(rollback)
    item = evolution_journal_base_item(
        entry_type="rollback",
        source_id=rollback_id,
        title=str(rollback.get("title") or f"Rollback {rollback_id}"),
        summary=str(rollback.get("reason") or "Rollback record completed."),
        status=evolution_journal_status(rollback),
        created_at=rollback.get("initiated_at") or rollback.get("created_at"),
        updated_at=rollback.get("completed_at") or rollback.get("executed_at"),
        occurred_at=evolution_journal_timestamp(projected),
        action_type=rollback.get("action_type") or "rollback",
        target=evolution_journal_target(
            target_type="runtime",
            target_id=rollback.get("runtime_id"),
            target_version=rollback.get("to_version"),
            incident_id=rollback.get("incident_ref"),
        ),
        route=f"/management/evolution-journal?rollback={rollback_id}",
        bff_detail_path="/api/v1/rollbacks",
    )
    item["rollback"] = json.loads(json.dumps(projected))
    item["record"] = item["rollback"]
    return item


def evolution_journal_filter_items(
    items: List[Dict[str, Any]],
    *,
    source_type: Optional[str] = None,
    status: Optional[str] = None,
    action_type: Optional[str] = None,
    risk_level: Optional[str] = None,
) -> List[Dict[str, Any]]:
    source_types = evolution_journal_type_filter(source_type)
    statuses = evolution_journal_csv_filter(status)
    action_types = evolution_journal_csv_filter(action_type)
    risk_levels = evolution_journal_csv_filter(risk_level)
    filtered = items
    if source_types:
        filtered = [
            item for item in filtered
            if str(item.get("entry_type") or item.get("entryType") or "").lower() in source_types
        ]
    if statuses:
        filtered = [
            item for item in filtered
            if str(item.get("status") or "").lower() in statuses
        ]
    if action_types:
        filtered = [
            item for item in filtered
            if str(item.get("action_type") or "").lower() in action_types
        ]
    if risk_levels:
        filtered = [
            item for item in filtered
            if str(item.get("risk_level") or "").lower() in risk_levels
        ]
    return filtered


def evolution_journal_summary(items: List[Dict[str, Any]], returned_count: int) -> Dict[str, Any]:
    by_type = _management_count_by(items, "entry_type")
    by_status = _management_count_by(items, "status")
    by_risk_level = _management_count_by(items, "risk_level")
    latest_at = max(
        [str(item.get("occurred_at") or "") for item in items if item.get("occurred_at")],
        default=None,
    )
    return {
        "total_items": len(items),
        "returned_items": returned_count,
        "decision_count": by_type.get("evolution_decision", 0),
        "mutation_review_count": by_type.get("mutation_review", 0),
        "postmortem_count": by_type.get("postmortem", 0),
        "rollback_count": by_type.get("rollback", 0),
        "freeze_order_count": by_type.get("freeze_order", 0),
        "pending_review_count": len([
            item for item in items
            if item.get("entry_type") == "mutation_review"
            and str(item.get("status") or "").lower() in {"pending", "reviewed", "under_review", "in_review"}
        ]),
        "active_freeze_count": len([
            item for item in items
            if item.get("entry_type") == "freeze_order"
            and str(item.get("status") or "").lower() == "active"
        ]),
        "completed_rollback_count": len([
            item for item in items
            if item.get("entry_type") == "rollback"
            and str(item.get("status") or "").lower() == "completed"
        ]),
        "latest_at": latest_at,
        "byType": by_type,
        "by_type": by_type,
        "byStatus": by_status,
        "by_status": by_status,
        "byRiskLevel": by_risk_level,
        "by_risk_level": by_risk_level,
    }


def evolution_journal_surfaces(
    *,
    snapshot_at: str,
    dataset_surface_status_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    source_surfaces = {
        "evolution_decisions": dataset_surface_status_fn("evolution_decisions", snapshot_at=snapshot_at),
        "postmortems": dataset_surface_status_fn("postmortems", snapshot_at=snapshot_at),
        "freeze_orders": dataset_surface_status_fn("freeze_orders", snapshot_at=snapshot_at),
        "rollbacks": dataset_surface_status_fn("all_rollbacks", snapshot_at=snapshot_at),
        "approval_decisions": dataset_surface_status_fn("approval_decisions", snapshot_at=snapshot_at),
        "personas": dataset_surface_status_fn("personas", snapshot_at=snapshot_at),
        "persona_bindings": dataset_surface_status_fn("persona_bindings", snapshot_at=snapshot_at),
        "runtime_bindings": dataset_surface_status_fn("runtime_bindings", snapshot_at=snapshot_at),
        "incidents": dataset_surface_status_fn("incidents", snapshot_at=snapshot_at),
    }
    # Composed mutation_review surface
    mutation_statuses = [
        source_surfaces["evolution_decisions"].get("status", "ok"),
        source_surfaces["approval_decisions"].get("status", "ok"),
    ]
    if all(s == "ok" for s in mutation_statuses):
        mutation_surface = {"status": "ok", "source": "bff_composed", "snapshot_at": snapshot_at}
    elif all(s == "unavailable" for s in mutation_statuses):
        mutation_surface = {"status": "unavailable", "source": "bff_composed", "message": "Mutation review data unavailable", "snapshot_at": snapshot_at}
    else:
        mutation_surface = {"status": "degraded", "source": "bff_composed", "message": "Mutation review partially degraded", "snapshot_at": snapshot_at}

    source_surfaces["mutation_review"] = mutation_surface
    return source_surfaces


# --------------------------------------------------------------------------- #
# OODA Packet Feature Flag & Helpers
# --------------------------------------------------------------------------- #

def ooda_packet_routes_enabled() -> bool:
    raw = os.getenv("PANTHEON_OODA_PACKET_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def ooda_packet_list_payload(
    packets: List[Dict[str, Any]],
    *,
    surface_key: str,
    page_token: Optional[str] = None,
    page_size: int = 20,
    related: Optional[Dict[str, Any]] = None,
    snapshot_at: str,
    page_slice_fn: Callable[..., Tuple[List[Dict[str, Any]], Optional[str]]],
    read_surface_meta_fn: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    total = len(packets)
    page_items, next_page_token = page_slice_fn(packets, page_token, page_size)
    meta = read_surface_meta_fn(
        "ooda_packets",
        surface_key,
        snapshot_at=snapshot_at,
        total=total,
    )
    if related:
        meta["related"] = related
    return {
        "data": page_items,
        "items": page_items,
        "page_info": {"next_page_token": next_page_token, "total": total},
        "meta": meta,
    }


# --------------------------------------------------------------------------- #
# Evolution Service Class
# --------------------------------------------------------------------------- #

class EvolutionService:
    """Core domain service for Evolution Engine business logic and readback."""

    def __init__(self, read_store: Any) -> None:
        self.read_store = read_store

    def list_evolution_decisions(
        self,
        *,
        action_type: Optional[str] = None,
        risk_level: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self.read_store
        decisions = getattr(store, "list_evolution_decisions", lambda **kw: [])(
            action_type=action_type,
            risk_level=risk_level,
            status=status,
        ) or []
        return [project_evolution_decision_contract(d) for d in decisions]

    def get_evolution_decision(self, decision_id: str) -> Optional[Dict[str, Any]]:
        store = self.read_store
        decision = getattr(store, "get_evolution_decision_by_id", lambda did: None)(decision_id)
        if not decision:
            return None
        return project_evolution_decision_contract(decision)

    def list_freeze_orders(
        self,
        *,
        status: Optional[str] = None,
        scope: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self.read_store
        orders = getattr(store, "list_freeze_orders", lambda **kw: [])(
            status=status,
            scope=scope,
        ) or []
        return [project_freeze_order_contract(o) for o in orders]

    def list_rollbacks(
        self,
        *,
        runtime_id: Optional[str] = None,
        action_type: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self.read_store
        rollbacks = getattr(store, "list_all_rollbacks", lambda **kw: [])(
            runtime_id=runtime_id,
            action_type=action_type,
            time_range=time_range,
        ) or []
        return [project_rollback_contract(r) for r in rollbacks]

    def list_lineage(
        self,
        *,
        artifact_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self.read_store
        if hasattr(store, "list_lineage_records"):
            return store.list_lineage_records(artifact_id=artifact_id, include_fixture_pack=False) or []
        if hasattr(store, "list_lineage_edges"):
            return store.list_lineage_edges(artifact_id=artifact_id) or []
        return []

    def get_lineage_edge(self, edge_id: str) -> Optional[Dict[str, Any]]:
        store = self.read_store
        return getattr(store, "get_lineage_edge", lambda eid: None)(edge_id)

    def get_lineage_graph(
        self,
        *,
        root_type: Optional[str] = None,
        root_id: str,
        depth: int = 3,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        store = self.read_store
        edges = getattr(store, "get_lineage_graph", lambda **kw: [])(
            root_type=root_type,
            root_id=root_id,
            depth=depth,
        ) or []
        if hasattr(store, "get_lineage_graph_nodes"):
            nodes = store.get_lineage_graph_nodes(edges) or []
        else:
            node_map: Dict[str, Dict[str, Any]] = {}
            for edge in edges:
                for a_id in (edge.get("from_artifact_id"), edge.get("to_artifact_id")):
                    if a_id and a_id not in node_map:
                        node_map[a_id] = {"id": a_id, "label": a_id}
            nodes = list(node_map.values())
        return nodes, edges

    def get_inspiration_graph(
        self,
        artifact_id: str,
        *,
        snapshot_at: str,
        utc_now: Callable[[], str],
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        store = self.read_store
        projection = getattr(store, "get_inspiration_graph", lambda aid: None)(artifact_id)
        artifact_exists = getattr(store, "artifact_exists", lambda aid: True)(artifact_id)
        if projection is None and artifact_exists:
            projection = ew04_inspiration_projection_from_lineage_edges(artifact_id, store, utc_now=utc_now)
        return projection, artifact_exists

    def list_telemetry_events(
        self,
        *,
        pool_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        time_range: Optional[str] = None,
    ) -> Tuple[str, List[Dict[str, Any]]]:
        store = self.read_store
        if hasattr(store, "list_telemetry_events_with_source"):
            return store.list_telemetry_events_with_source(
                pool_id=pool_id,
                artifact_id=artifact_id,
                time_range=time_range,
            )
        if hasattr(store, "list_telemetry_events"):
            events = store.list_telemetry_events(
                pool_id=pool_id,
                artifact_id=artifact_id,
                time_range=time_range,
            ) or []
            return "live", events
        return "missing", []

    def get_telemetry_summary(self, runtime_id: str) -> Optional[Dict[str, Any]]:
        store = self.read_store
        return getattr(store, "get_telemetry_summary", lambda rid: None)(runtime_id)

    def get_telemetry_performance(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        store = self.read_store
        return getattr(store, "get_telemetry_performance", lambda aid: None)(artifact_id)

    def list_ooda_packets_for_evolution_program(self, program_id: str) -> List[Dict[str, Any]]:
        store = self.read_store
        return getattr(store, "list_ooda_packets_for_evolution_program", lambda pid: [])(program_id) or []
