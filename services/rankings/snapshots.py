"""Pure PM-12 snapshot identity and scheduled-owner admission into Rankings."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, List

from .store import RankingSnapshotRecord, RankingConflictError, utc_now

FORMULA_VERSION = "pm12-default-v1"


def _stable_json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


# --- _pm12_ranking_snapshot_helpers ---
_PM12_RANKING_SNAPSHOT_ITEM_FIELDS = (
    'persona_id', 'name', 'owner', 'state',
    'owner_lifecycle_state', 'archetype', 'risk', 'rank',
    'score', 'overall_score', 'tier', 'tier_id',
    'tier_label', 'formula_version', 'allocation_policy_input', 'components',
    'metrics', 'stage', 'deployment_stage', 'capital_mode',
    'capital_scope', 'capital_scope_id', 'capital_pool_id', 'capital_sleeve_id',
    'paper_ledger_id', 'current_weight', 'target_weight', 'delta',
    'current_weight_source', 'binding_state', 'binding_resolution', 'runtime_resolution',
    'session_resolution', 'session_id', 'session_authority', 'telemetry_resolution',
    'binding_ids', 'runtime_ids', 'strategy_ids', 'capital_pool_ids',
    'sleeve_ids', 'artifact_ids', 'broker_ids', 'eligible',
    'exclusion_codes', 'exclusion_reasons', 'exclusion_reason', 'evidence_coverage',
    'evidence_ref_ids', 'source_confidence',
)


def _pm12_ranking_snapshot_payload_items(
    items: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    set_like_fields = {
        "binding_ids",
        "runtime_ids",
        "strategy_ids",
        "capital_pool_ids",
        "sleeve_ids",
        "artifact_ids",
        "broker_ids",
        "exclusion_codes",
        "exclusion_reasons",
    }
    payload_items: List[Dict[str, Any]] = []
    for item in items:
        payload_item: Dict[str, Any] = {}
        for field in _PM12_RANKING_SNAPSHOT_ITEM_FIELDS:
            if field not in item:
                continue
            if field == "evidence_ref_ids":
                payload_item[field] = sorted(
                    str(value).strip()
                    for value in (
                        item.get("_snapshot_evidence_ref_ids")
                        or item.get(field)
                        or []
                    )
                    if str(value).strip()
                )
            elif field in set_like_fields and isinstance(item.get(field), list):
                payload_item[field] = sorted(
                    item.get(field) or [],
                    key=lambda value: json.dumps(
                        value,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                )
            elif field == "metrics" and isinstance(item.get(field), dict):
                metrics = json.loads(json.dumps(item.get(field)))
                for nested_field in ("runtime_ids", "telemetry_evidence_refs"):
                    if isinstance(metrics.get(nested_field), list):
                        metrics[nested_field] = sorted(
                            metrics[nested_field],
                            key=lambda value: json.dumps(
                                value,
                                sort_keys=True,
                                separators=(",", ":"),
                                ensure_ascii=True,
                            ),
                        )
                payload_item[field] = metrics
            else:
                payload_item[field] = item.get(field)
        payload_items.append(payload_item)
    payload_items.sort(
        key=lambda item: (
            (
                int(item.get("rank"))
                if isinstance(item.get("rank"), int)
                or str(item.get("rank") or "").isdigit()
                else 10**9
            ),
            str(item.get("persona_id") or ""),
        )
    )
    return payload_items


# --- _pm12_ranking_snapshot_content ---
def _pm12_ranking_snapshot_content(
    items: List[Dict[str, Any]],
    *,
    surface: str,
    period: str,
) -> Dict[str, Any]:
    return {
        "surface": surface,
        "period": period,
        "formula_version": FORMULA_VERSION,
        "items": _pm12_ranking_snapshot_payload_items(items),
    }


def snapshot_record(items, *, surface, period, created_at=None):
    content = _pm12_ranking_snapshot_content(items, surface=surface, period=period)
    digest = _stable_json_hash(content)
    clean_period = re.sub(r"[^a-z0-9]+", "-", str(period or "current").strip().lower()).strip("-")
    assertions = {}
    for item in items:
        persona_id = str(item.get("persona_id") or "").strip()
        if persona_id:
            assertions.setdefault(persona_id, []).append(_stable_json_hash(item.get("evidence_refs") or []))
    return RankingSnapshotRecord(
        ranking_snapshot_id=f"ranking-{surface}-{clean_period or 'current'}-{digest[:24]}",
        **content, content_digest=digest, evidence_assertion_digests=assertions,
        created_at=created_at or utc_now(),
    )


def admit_snapshot(store, snapshot):
    """Atomic owner admission; replay preserves the first timestamp and all content."""
    from dataclasses import replace
    try:
        return store.create_ranking_snapshot(snapshot)
    except RankingConflictError:
        existing = store.get_ranking_snapshot(snapshot.ranking_snapshot_id)
        if existing is None or replace(snapshot, created_at=existing.created_at) != existing:
            raise
        return existing
