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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from services.evolution.program_qualification import (
    EXPRESSION_REPLAY_POLICY_CONTROLS,
    expression_replay_gate,
)
from services.registry.strategy_artifact import (
    FORBIDDEN_MUTATION_CONTROL_KEYS,
    REQUIRED_EXPRESSION_POLICY_CONTROLS,
    StrategyArtifactValidationError,
    mutate_strategy_artifact,
    validate_strategy_artifact,
)

log = logging.getLogger(__name__)

EVOLUTION_ALLOWED_EXPRESSION_CONTROLS: Tuple[str, ...] = tuple(
    REQUIRED_EXPRESSION_POLICY_CONTROLS.keys()
)
EVOLUTION_FORBIDDEN_CONTROLS: Tuple[str, ...] = tuple(
    sorted(FORBIDDEN_MUTATION_CONTROL_KEYS)
)
_PREFER_WHEN_KEY_RE = re.compile(r"^prefer_.*_when_.*$")


def validate_evolution_child_artifact(
    parent_artifact: Mapping[str, Any],
    child_artifact: Mapping[str, Any],
    *,
    parameter_changes: Optional[Mapping[str, Any]] = None,
    replay_records: Optional[Sequence[Mapping[str, Any]]] = None,
    require_replay: bool = False,
) -> Dict[str, Any]:
    """Validate an evolution child strategy artifact against its parent and mutation surface.

    Accepts the expression policy controls (strength_scale_k, strength_floor_momentum,
    lever_min_strength, notional_fraction_at_full_strength) when expression_policy is
    present, and rejects forbidden controls or immutable parameter mutations.

    A child may never raise a risk limit: any risk-limit key (``max_leverage``,
    ``gross_limit``, ``net_limit`` ...) that differs from the parent, in the
    parameters or the expression policy, is rejected.  When the change touches
    a control that decides the instrument and ``replay_records`` are given, the
    proposal is replayed on those recorded decisions against the parent and
    rejected if it does not pass; ``require_replay=True`` (the pre-qualification
    path) also rejects such a change when no records are supplied.
    """
    validate_strategy_artifact(parent_artifact)
    validate_strategy_artifact(child_artifact)
    parent = dict(parent_artifact)
    child = dict(child_artifact)

    for section_name, getter in (
        ("parameters", lambda a: a.get("parameters") or {}),
        ("expression_policy.parameters", lambda a: (a.get("expression_policy") or {}).get("parameters") or {}),
    ):
        parent_section, child_section = getter(parent), getter(child)
        for risk_key in sorted(FORBIDDEN_MUTATION_CONTROL_KEYS):
            if risk_key in child_section and child_section[risk_key] != parent_section.get(risk_key):
                raise StrategyArtifactValidationError(
                    f"evolution child cannot raise or change risk limit {risk_key!r} in {section_name}"
                )

    if child["strategy_id"] != parent["strategy_id"]:
        raise StrategyArtifactValidationError(
            "evolution child strategy_id must match parent strategy_id"
        )
    parent_ids = child.get("lineage", {}).get("parent_registry_ids") or []
    if parent["artifact_id"] not in parent_ids:
        raise StrategyArtifactValidationError(
            "evolution child lineage.parent_registry_ids must include parent artifact_id"
        )

    parent_controls = {
        ctrl["parameter_key"]: ctrl
        for ctrl in parent["mutation_surface"]["controls"]
    }
    child_controls = {
        ctrl["parameter_key"]: ctrl
        for ctrl in child["mutation_surface"]["controls"]
    }
    if set(parent_controls) != set(child_controls):
        raise StrategyArtifactValidationError(
            "evolution child mutation_surface.controls keys must match parent"
        )

    inferred_changes: Dict[str, Any] = {}
    for key, val in child["parameters"].items():
        if parent["parameters"].get(key) != val:
            inferred_changes[key] = val
    for key in parent["parameters"]:
        if key not in child["parameters"]:
            raise StrategyArtifactValidationError(
                f"evolution child removed parent parameter {key!r}"
            )

    if "expression_policy" in parent or "expression_policy" in child:
        if "expression_policy" not in parent or "expression_policy" not in child:
            raise StrategyArtifactValidationError(
                "evolution child cannot add or remove expression_policy section"
            )
        parent_ep = parent["expression_policy"]
        child_ep = child["expression_policy"]
        if (
            parent_ep.get("version") != child_ep.get("version")
            or parent_ep.get("kind") != child_ep.get("kind")
        ):
            raise StrategyArtifactValidationError(
                "evolution child cannot change expression_policy version or kind"
            )
        for key, val in child_ep["parameters"].items():
            if parent_ep["parameters"].get(key) != val:
                inferred_changes[key] = val

    changes_to_check = (
        dict(parameter_changes) if parameter_changes is not None else inferred_changes
    )
    if not changes_to_check:
        raise StrategyArtifactValidationError(
            "evolution child must mutate at least one declared control"
        )

    for key in changes_to_check:
        if key in FORBIDDEN_MUTATION_CONTROL_KEYS or _PREFER_WHEN_KEY_RE.match(str(key)):
            raise StrategyArtifactValidationError(
                f"evolution child cannot mutate forbidden control {key!r}"
            )
        if key not in parent_controls:
            raise StrategyArtifactValidationError(
                f"evolution child mutated undeclared control {key!r}"
            )

    expected_child = mutate_strategy_artifact(
        parent,
        parameter_updates=changes_to_check,
        new_artifact_id=child["artifact_id"],
        new_version=child["version"],
        source_run_ids=child["lineage"]["source_run_ids"],
        parent_registry_id=parent["artifact_id"],
    )
    if expected_child != child:
        raise StrategyArtifactValidationError(
            "evolution child artifact does not match governed mutation of parent"
        )
    result: Dict[str, Any] = {
        "valid": True,
        "parent_artifact_id": parent["artifact_id"],
        "child_artifact_id": child["artifact_id"],
        "mutated_controls": sorted(changes_to_check.keys()),
    }
    replay_controls = sorted(set(changes_to_check) & set(EXPRESSION_REPLAY_POLICY_CONTROLS))
    if replay_controls and (replay_records or require_replay):
        gate = expression_replay_gate(
            parent["expression_policy"]["parameters"],
            child["expression_policy"]["parameters"],
            list(replay_records or []),
        )
        if not gate["passed"]:
            raise StrategyArtifactValidationError(
                f"evolution child expression replay failed: {gate['reason']}"
            )
        result["expression_replay"] = {**gate, "evaluated_controls": replay_controls}
    elif replay_controls:
        result["expression_replay"] = {"status": "not_evaluated", "evaluated_controls": replay_controls}
    return result

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

_EVOLUTION_JOURNAL_THRESHOLD_SIGNAL_TYPES = frozenset(
    {
        "performance_degradation",
        "execution_drift",
        "feature_drift",
        "human_correction",
    }
)

_EVOLUTION_JOURNAL_ALLOWED_VIEWS = frozenset(
    {
        "all",
        "history",
        "auto_proposals",
    }
)

_EVOLUTION_JOURNAL_NON_DRAFT_POSTMORTEM_STATUSES = (
    "review,approved,published,resolved,completed"
)

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


def evolution_journal_decision_evidence_class(decision: Dict[str, Any]) -> str:
    """Classify an EvolutionDecision (and its derived mutation_review) by evidence class."""
    if not isinstance(decision, dict):
        return "unclassified"
    explicit = str(decision.get("evidence_class") or "").strip().lower()
    if explicit in {"threshold_signal", "incident_followup", "incident_severity"}:
        return explicit

    raw_snapshots = decision.get("threshold_snapshots")
    snapshots = (
        [s for s in raw_snapshots if isinstance(s, dict)]
        if isinstance(raw_snapshots, list)
        else []
    )
    for snap in snapshots:
        breached_raw = snap.get("breached")
        is_breached = True if breached_raw is None else bool(breached_raw)
        sig = str(snap.get("signal_type") or snap.get("signal") or "").strip().lower()
        if is_breached and sig in _EVOLUTION_JOURNAL_THRESHOLD_SIGNAL_TYPES:
            return "threshold_signal"

    metrics: Set[str] = set()
    for snap in snapshots:
        metric = str(snap.get("metric_name") or snap.get("metric") or "").strip().lower()
        if metric:
            metrics.add(metric)

    if not snapshots:
        top_breached_raw = decision.get("breached")
        top_breached = True if top_breached_raw is None else bool(top_breached_raw)
        top_sig = str(decision.get("signal_type") or decision.get("signal") or "").strip().lower()
        if top_breached and top_sig in _EVOLUTION_JOURNAL_THRESHOLD_SIGNAL_TYPES:
            return "threshold_signal"
        top_metric = str(decision.get("metric_name") or decision.get("metric") or "").strip().lower()
        if top_metric:
            metrics.add(top_metric)

    if metrics == {"incident_followup_required"}:
        return "incident_followup"
    if metrics == {"severity1_incident_count"}:
        return "incident_severity"

    risk_assessment = decision.get("risk_assessment")
    if isinstance(risk_assessment, dict) and not snapshots and not metrics:
        triggers = risk_assessment.get("threshold_triggers")
        if isinstance(triggers, list):
            for trig in triggers:
                if isinstance(trig, dict):
                    trig_type = str(trig.get("trigger_type") or trig.get("signal_type") or "").strip().lower()
                    if trig_type in _EVOLUTION_JOURNAL_THRESHOLD_SIGNAL_TYPES:
                        return "threshold_signal"

    return "unclassified"


def evolution_journal_decision_item(decision: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    decision_id = _management_record_id(decision, "decision_id", "id", "evolution_decision_id")
    if not decision_id:
        return None
    status = evolution_journal_status(decision)
    ev_class = evolution_journal_decision_evidence_class(decision)
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
    item["evidence_class"] = ev_class
    item["decision"] = json.loads(json.dumps(decision))
    item["decision"]["evidence_class"] = ev_class
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
        # A mutation review is carried inside its decision row, so the
        # mutation_review source type selects decision rows that carry one.
        filtered = [
            item for item in filtered
            if str(item.get("entry_type") or item.get("entryType") or "").lower() in source_types
            or (
                "mutation_review" in source_types
                and isinstance(item.get("mutation_review"), dict)
            )
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


def evolution_journal_item_is_history(item: Dict[str, Any]) -> bool:
    """Return True if a journal item belongs in view=history (vs view=auto_proposals)."""
    entry_type = str(item.get("entry_type") or item.get("entryType") or "").strip().lower()
    if entry_type in {"freeze_order", "rollback"}:
        return True
    if entry_type == "postmortem":
        return str(item.get("status") or "").strip().lower() != "draft"
    if entry_type == "evolution_decision":
        status = str(item.get("status") or "").strip().lower()
        if status != "proposed":
            return True
        ev_class = str(item.get("evidence_class") or "").strip().lower()
        return ev_class == "threshold_signal"
    return True


def evolution_journal_filter_by_view(
    items: List[Dict[str, Any]],
    *,
    view: str = "all",
    has_explicit_target_filter: bool = False,
) -> List[Dict[str, Any]]:
    """Filter journal items by view (all | history | auto_proposals), bypassed on explicit deep links."""
    if has_explicit_target_filter or view == "all":
        return items
    if view == "history":
        return [it for it in items if evolution_journal_item_is_history(it)]
    if view == "auto_proposals":
        return [it for it in items if not evolution_journal_item_is_history(it)]
    return items


def evolution_journal_auto_proposal_counts(
    decisions: Sequence[Dict[str, Any]],
    *,
    postmortem_draft_count: int,
) -> Dict[str, int]:
    """Compute auto_proposal_counts per record so a decision and its mutation_review count once."""
    incident_followup = 0
    incident_severity = 0
    other_proposed = 0
    seen_ids: Set[str] = set()
    for dec in decisions:
        if not isinstance(dec, dict):
            continue
        dec_id = _management_record_id(dec, "decision_id", "id", "evolution_decision_id")
        if not dec_id or dec_id in seen_ids:
            continue
        seen_ids.add(dec_id)
        if evolution_journal_status(dec) != "proposed":
            continue
        ev_class = evolution_journal_decision_evidence_class(dec)
        if ev_class == "threshold_signal":
            continue
        if ev_class == "incident_followup":
            incident_followup += 1
        elif ev_class == "incident_severity":
            incident_severity += 1
        else:
            other_proposed += 1
    draft_count = max(0, int(postmortem_draft_count))
    total = incident_followup + incident_severity + other_proposed + draft_count
    return {
        "incident_followup": incident_followup,
        "incident_severity": incident_severity,
        "postmortem_draft": draft_count,
        "total": total,
    }


def _store_supports_bounded_postmortem_page_read(read_store: Any) -> bool:
    instance_dict = getattr(read_store, "__dict__", None)
    if isinstance(instance_dict, dict):
        if "list_postmortems_page" in instance_dict:
            return True
        if "list_postmortems" in instance_dict:
            return False
    cls = type(read_store)
    if "list_postmortems_page" in cls.__dict__:
        return True
    if "list_postmortems" in cls.__dict__:
        return False
    return callable(getattr(read_store, "list_postmortems_page", None))


def evolution_journal_read_postmortems(
    read_store: Any,
    *,
    view: str = "all",
    page_size: int = 20,
    page_token: Optional[str] = None,
    has_persona_filter: bool = False,
    has_decision_or_mr_filter: bool = False,
) -> Tuple[List[Dict[str, Any]], int, int]:
    """Read postmortems using bounded owner page reads when supported.

    Returns (postmortem_rows_for_items, postmortem_draft_total, unfetched_draft_count).
    """
    try:
        start_offset = max(0, int(page_token)) if page_token and str(page_token).strip() else 0
    except (TypeError, ValueError):
        start_offset = 0
    bounded_limit = min(200, max(int(page_size), start_offset + int(page_size)))

    if _store_supports_bounded_postmortem_page_read(read_store):
        list_page_fn = getattr(read_store, "list_postmortems_page")
        if has_decision_or_mr_filter and not has_persona_filter:
            draft_page = list_page_fn(status="draft", page_size=1) or {}
            draft_total = int(draft_page.get("total", len(draft_page.get("items") or [])))
            return [], draft_total, 0
        if view == "history" and not has_persona_filter:
            non_draft_page = (
                list_page_fn(status=_EVOLUTION_JOURNAL_NON_DRAFT_POSTMORTEM_STATUSES, page_size=200) or {}
            )
            draft_page = list_page_fn(status="draft", page_size=1) or {}
            non_draft_items = list(non_draft_page.get("items") or [])
            draft_total = int(draft_page.get("total", len(draft_page.get("items") or [])))
            return non_draft_items, draft_total, 0
        if view == "auto_proposals" and not has_persona_filter:
            draft_page = list_page_fn(status="draft", page_size=bounded_limit) or {}
            draft_items = list(draft_page.get("items") or [])
            draft_total = int(draft_page.get("total", len(draft_items)))
            unfetched_draft = max(0, draft_total - len(draft_items))
            return draft_items, draft_total, unfetched_draft
        non_draft_page = (
            list_page_fn(status=_EVOLUTION_JOURNAL_NON_DRAFT_POSTMORTEM_STATUSES, page_size=200) or {}
        )
        draft_page = list_page_fn(status="draft", page_size=bounded_limit) or {}
        non_draft_items = list(non_draft_page.get("items") or [])
        draft_items = list(draft_page.get("items") or [])
        draft_total = int(draft_page.get("total", len(draft_items)))
        unfetched_draft = 0 if has_persona_filter else max(0, draft_total - len(draft_items))
        return non_draft_items + draft_items, draft_total, unfetched_draft

    all_pms = [
        pm
        for pm in (getattr(read_store, "list_postmortems", lambda: [])() or [])
        if isinstance(pm, dict)
    ]
    draft_pms = [pm for pm in all_pms if evolution_journal_status(pm) == "draft"]
    non_draft_pms = [pm for pm in all_pms if evolution_journal_status(pm) != "draft"]
    draft_total = len(draft_pms)
    if has_decision_or_mr_filter and not has_persona_filter:
        return [], draft_total, 0
    if view == "history" and not has_persona_filter:
        return non_draft_pms, draft_total, 0
    if not has_persona_filter and len(draft_pms) > bounded_limit:
        draft_pms_sorted = sorted(
            draft_pms,
            key=lambda r: (str(r.get("created_at") or ""), str(r.get("postmortem_id") or r.get("id") or "")),
            reverse=True,
        )
        sliced_drafts = draft_pms_sorted[:bounded_limit]
        unfetched_draft = draft_total - len(sliced_drafts)
        if view == "auto_proposals":
            return sliced_drafts, draft_total, unfetched_draft
        return non_draft_pms + sliced_drafts, draft_total, unfetched_draft
    if view == "auto_proposals" and not has_persona_filter:
        return draft_pms, draft_total, 0
    return all_pms, draft_total, 0


def evolution_journal_summary(items: List[Dict[str, Any]], returned_count: int) -> Dict[str, Any]:
    by_type = _management_count_by(items, "entry_type")
    by_status = _management_count_by(items, "status")
    # Decision rows that carry a mutation_review projection (one row per decision).
    review_items = [
        item for item in items
        if item.get("entry_type") == "evolution_decision"
        and isinstance(item.get("mutation_review"), dict)
    ]
    by_risk_level = _management_count_by(items, "risk_level")
    latest_at = max(
        [str(item.get("occurred_at") or "") for item in items if item.get("occurred_at")],
        default=None,
    )
    return {
        "total_items": len(items),
        "returned_items": returned_count,
        "decision_count": by_type.get("evolution_decision", 0),
        "mutation_review_count": len(review_items),
        "postmortem_count": by_type.get("postmortem", 0),
        "rollback_count": by_type.get("rollback", 0),
        "freeze_order_count": by_type.get("freeze_order", 0),
        "pending_review_count": len([
            item for item in review_items
            if str(item["mutation_review"].get("decision_state") or "").lower()
            in {"pending", "reviewed", "under_review", "in_review"}
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

    # Composed overall journal surface across the core aggregate sources.
    journal_statuses = [
        source_surfaces["evolution_decisions"].get("status", "ok"),
        source_surfaces["postmortems"].get("status", "ok"),
        source_surfaces["freeze_orders"].get("status", "ok"),
        source_surfaces["rollbacks"].get("status", "ok"),
    ]
    if all(s == "ok" for s in journal_statuses):
        journal_surface = {"status": "ok", "source": "bff_composed", "snapshot_at": snapshot_at}
    elif all(s == "unavailable" for s in journal_statuses):
        journal_surface = {
            "status": "unavailable",
            "source": "bff_composed",
            "message": "Evolution journal data unavailable",
            "snapshot_at": snapshot_at,
        }
    else:
        journal_surface = {
            "status": "degraded",
            "source": "bff_composed",
            "message": "Evolution journal partially degraded",
            "snapshot_at": snapshot_at,
        }
    source_surfaces["management_evolution_journal"] = journal_surface

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
        edges = [{k: v for k, v in e.items() if k not in ("from_node", "to_node")} for e in edges]
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

    def validate_child_artifact(
        self,
        parent_artifact: Mapping[str, Any],
        child_artifact: Mapping[str, Any],
        *,
        parameter_changes: Optional[Mapping[str, Any]] = None,
        replay_records: Optional[Sequence[Mapping[str, Any]]] = None,
        require_replay: bool = False,
    ) -> Dict[str, Any]:
        return validate_evolution_child_artifact(
            parent_artifact,
            child_artifact,
            parameter_changes=parameter_changes,
            replay_records=replay_records,
            require_replay=require_replay,
        )

    def propose_child_artifact(
        self,
        parent_artifact: Mapping[str, Any],
        child_artifact: Mapping[str, Any],
        *,
        replay_records: Sequence[Mapping[str, Any]],
        parameter_changes: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Pre-qualification path: instrument-affecting changes must replay first."""
        return validate_evolution_child_artifact(
            parent_artifact,
            child_artifact,
            parameter_changes=parameter_changes,
            replay_records=replay_records,
            require_replay=True,
        )
