"""Owner-side allocation lineage (PPL-ALLOC-009/012): targets are recomputed from the admitted
ranking snapshot, never trusted from caller rows or weights.  The BFF only forwards."""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List

from services.control_plane.bff.ports.rankings import create_ranking_reader
from services.control_plane.bff.persona_allocation_policy import (
    _ALLOCATION_POLICY_VERSION,
    _PAPER_SIMULATION_POLICY_VERSION,
    calculate_paper_simulation_allocations,
    calculate_target_allocations,
)

try:
    from .allocation_store import allocation_line_digest, stable_payload_hash
except ImportError:  # pragma: no cover - flat-module import path
    from allocation_store import allocation_line_digest, stable_payload_hash  # type: ignore

PAPER_AUTHORITY_MODE = "governed_paper_simulation"
# Caller-supplied context the snapshot cannot know; everything else must equal the snapshot item.
_CONTEXT_FIELDS = frozenset({
    "current_weight", "capital_pool_id", "capital_sleeve_id", "capital_scope",
    "paper_ledger_id", "binding_id", "evidence_refs", "ranking_snapshot_id",
})


class AllocationLineageError(ValueError):
    def __init__(self, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.status_code = status_code


def _load_snapshot(snapshot_id: str) -> Dict[str, Any]:
    try:  # the Rankings store is a separate owner; any failure to read it is unavailability
        snapshot = create_ranking_reader().get_ranking_snapshot(snapshot_id)
    except Exception as exc:
        raise AllocationLineageError(f"Ranking snapshot store is unavailable: {exc}", 503) from exc
    if snapshot is None or snapshot.get("surface") != "quarterly":
        raise AllocationLineageError(f"ranking snapshot {snapshot_id!r} is unknown or not allocation eligible")
    return snapshot


def _canonical_rows(snapshot: Dict[str, Any], rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise AllocationLineageError("rows must be a non-empty list")
    items = {str(item.get("persona_id") or ""): item for item in snapshot["items"]}
    canonical: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        persona_id = str((row or {}).get("persona_id") or "")
        item = items.get(persona_id)
        if item is None:
            raise AllocationLineageError(f"rows[{index}].persona_id is not part of the snapshot")
        if persona_id in seen:
            raise AllocationLineageError(f"rows[{index}].persona_id appears more than once")
        seen.add(persona_id)
        if row.get("ranking_snapshot_id") != snapshot["ranking_snapshot_id"]:
            raise AllocationLineageError(f"rows[{index}].ranking_snapshot_id must match the snapshot")
        for field, value in row.items():
            if field in item and field not in _CONTEXT_FIELDS and item[field] != value:
                raise AllocationLineageError(f"rows[{index}].{field} does not match the snapshot")
        context = {field: row[field] for field in _CONTEXT_FIELDS if field in row}
        canonical.append({
            **item, **context,
            "ranking_snapshot_id": snapshot["ranking_snapshot_id"],
            "evidence_refs": list(item.get("evidence_ref_ids") or []),
        })
    return canonical


def evaluate_allocation(payload: Dict[str, Any], *, paper: bool = False) -> Dict[str, Any]:
    snapshot = _load_snapshot(str(payload.get("ranking_snapshot_id") or "").strip())
    policy_version = _PAPER_SIMULATION_POLICY_VERSION if paper else _ALLOCATION_POLICY_VERSION
    requested = str(payload.get("allocation_policy_version") or "").strip()
    if requested and requested != policy_version:
        raise AllocationLineageError(f"allocation_policy_version must be {policy_version!r}")
    calculate = calculate_paper_simulation_allocations if paper else calculate_target_allocations
    rows = _canonical_rows(snapshot, payload.get("rows"))
    try:
        raw_lines = calculate(rows)
    except (KeyError, TypeError, ValueError) as exc:
        raise AllocationLineageError(f"ranking snapshot cannot be evaluated: {exc}") from exc
    snapshot_id = snapshot["ranking_snapshot_id"]
    evaluation_id = "allocation-evaluation-" + stable_payload_hash(
        {"ranking_snapshot_id": snapshot_id, "allocation_policy_version": policy_version, "lines": raw_lines}
    )[:24]
    lines = []
    for raw in raw_lines:
        line = {**raw, "ranking_snapshot_id": snapshot_id,
                "allocation_evaluation_id": evaluation_id, "allocation_policy_version": policy_version}
        lines.append({**line, "allocation_line_digest": allocation_line_digest(line)})
    return {
        "ranking_snapshot_id": snapshot_id, "allocation_evaluation_id": evaluation_id,
        "allocation_policy_version": policy_version, "lines": lines
    }


def verify_rebalance_lineage(proposal: Dict[str, Any]) -> None:
    """Reject a proposal whose snapshot, evaluation id or weights the owner cannot reproduce."""
    lines = proposal["lines"]
    paper = any(line.get("capital_scope") == "paper_ledger" for line in lines)
    evaluated = evaluate_allocation({
        "ranking_snapshot_id": proposal["ranking_snapshot_id"],
        "allocation_policy_version": proposal["allocation_policy_version"],
        "rows": [{**line, "ranking_snapshot_id": proposal["ranking_snapshot_id"]} for line in lines],
    }, paper=paper)
    if evaluated["allocation_evaluation_id"] != proposal["allocation_evaluation_id"]:
        raise AllocationLineageError("allocation_evaluation_id does not match the owner evaluation of the snapshot")
    for line, expected in zip(lines, evaluated["lines"]):
        if not math.isclose(float(line["target_weight"]), float(expected["target_weight"]), abs_tol=1e-9):
            raise AllocationLineageError(f"line for {line['persona_id']!r} does not match the snapshot evaluation")


def paper_environment_allowed() -> bool:
    live_flags = ("PANTHEON_LIVE_BROKER_ENABLED", "PANTHEON_CANARY_EXECUTION_ENABLED")
    return os.getenv("PANTHEON_ENV", "").strip().lower() == "dev" and not any(
        os.getenv(name, "").strip().lower() in ("1", "true", "yes") for name in live_flags
    )
