"""Bi-temporal replay and confidence-labelled legacy journey backfill."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from services.trade_journey.materializer import JourneyMaterializer, MaterializationError


@dataclass(frozen=True)
class ReplayResult:
    projections: tuple[dict[str, Any], ...]
    evidence_hash: str
    included_event_ids: tuple[str, ...]


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise MaterializationError(f"{field} must be timezone-aware ISO-8601")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise MaterializationError(f"{field} must be timezone-aware ISO-8601") from exc


def replay_as_of(events: Iterable[Mapping[str, Any]], *, occurred_as_of: str,
                 recorded_as_of: str) -> ReplayResult:
    """Replay knowledge available at ``recorded_as_of`` through event time.

    Corrections must name ``corrects_event_id``.  They overlay the corrected
    event only after the correction was recorded, preserving the original
    occurred-at ordering without rewriting history.
    """
    occurred_cutoff = _timestamp(occurred_as_of, "occurred_as_of")
    recorded_cutoff = _timestamp(recorded_as_of, "recorded_as_of")
    eligible: list[dict[str, Any]] = []
    for raw in events:
        event = dict(raw)
        occurred = _timestamp(event.get("occurred_at"), "occurred_at")
        recorded = _timestamp(event.get("recorded_at", event.get("occurred_at")), "recorded_at")
        if occurred <= occurred_cutoff and recorded <= recorded_cutoff:
            event["recorded_at"] = recorded.isoformat(timespec="microseconds").replace("+00:00", "Z")
            eligible.append(event)

    by_id = {event.get("event_id"): event for event in eligible}
    overlaid: dict[str, dict[str, Any]] = {}
    corrections: list[dict[str, Any]] = []
    for event in eligible:
        target = event.get("corrects_event_id")
        if target:
            if target not in by_id:
                raise MaterializationError(f"correction target unavailable as-of: {target}")
            corrections.append(event)
        else:
            overlaid[event["event_id"]] = event
    for correction in sorted(corrections, key=lambda item: (item["recorded_at"], item["event_id"])):
        target = correction["corrects_event_id"]
        replacement = dict(overlaid.get(target, by_id[target]))
        replacement.update(dict(correction.get("overlay", {})))
        replacement["event_id"] = target
        replacement["correction_event_id"] = correction["event_id"]
        overlaid[target] = replacement

    materializer = JourneyMaterializer()
    materializer.rebuild(overlaid.values())
    projections = tuple(projection.snapshot for projection in materializer.projections)
    payload = {"included_event_ids": sorted(event["event_id"] for event in eligible), "projections": projections}
    evidence_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ReplayResult(projections, evidence_hash, tuple(payload["included_event_ids"]))


def backfill_legacy(records: Iterable[Mapping[str, Any]], *, confidence_threshold: float = 0.8) -> dict[str, Any]:
    """Map legacy records without promoting inference to authoritative truth."""
    mappings: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []
    conflicts = 0
    records = [dict(item) for item in records]
    before_mapped = sum(bool(item.get("journey_id")) for item in records)
    for record in sorted(records, key=lambda item: str(item.get("legacy_id", ""))):
        legacy_id = record.get("legacy_id")
        candidates = sorted(set(record.get("candidate_journey_ids", [])))
        explicit = record.get("journey_id")
        if explicit:
            mappings.append({"legacy_id": legacy_id, "journey_id": explicit, "confidence": 1.0, "basis": "explicit"})
            continue
        confidence = float(record.get("confidence", 0.0))
        if len(candidates) == 1 and confidence >= confidence_threshold:
            mappings.append({"legacy_id": legacy_id, "journey_id": candidates[0], "confidence": confidence, "basis": "inferred"})
        else:
            reason = "conflict" if len(candidates) > 1 else "low_confidence" if candidates else "unmapped"
            conflicts += reason == "conflict"
            orphans.append({"legacy_id": legacy_id, "reason": reason, "candidates": candidates, "confidence": confidence})
    return {
        "mappings": mappings,
        "orphan_queue": orphans,
        "evidence": {
            "total": len(records), "before_mapped": before_mapped,
            "after_mapped": len(mappings), "conflicts": conflicts, "orphans": len(orphans),
            "confidence_threshold": confidence_threshold,
        },
    }
