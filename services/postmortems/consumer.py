"""Resolved incident consumer for the Postmortem Evidence Service."""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from services.incident.incident import (
    IncidentCase,
    IncidentError,
    IncidentStatus,
    IncidentStore,
    Postmortem,
    PostmortemStatus,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class PostmortemDraftConsumerError(ValueError):
    """Raised when a resolved incident event cannot produce a draft."""


@dataclass(frozen=True)
class ResolvedIncidentDraftResult:
    postmortem: Postmortem
    created: bool
    updated: bool


class ResolvedIncidentPostmortemDraftConsumer:
    """Create or refresh a draft Postmortem from a resolved IncidentCase."""

    def __init__(self, *, incident_store: IncidentStore) -> None:
        self._store = incident_store

    def consume(self, payload: Mapping[str, Any]) -> ResolvedIncidentDraftResult:
        incident = self._incident_from_payload(payload)
        if incident.status not in {IncidentStatus.RESOLVED.value, IncidentStatus.CLOSED.value}:
            raise PostmortemDraftConsumerError(
                f"IncidentCase {incident.incident_id!r} is not resolved or closed"
            )

        draft = build_postmortem_draft_from_incident(incident)
        existing = self._store.find_postmortem_for_incident(incident.incident_id)
        if existing is None:
            try:
                created = self._store.create_postmortem(draft)
            except IncidentError as exc:
                raise PostmortemDraftConsumerError(str(exc)) from exc
            return ResolvedIncidentDraftResult(postmortem=created, created=True, updated=False)

        if existing.status != PostmortemStatus.DRAFT.value:
            return ResolvedIncidentDraftResult(postmortem=existing, created=False, updated=False)

        merged = merge_postmortem_draft(existing, draft)
        if merged.to_dict() == existing.to_dict():
            return ResolvedIncidentDraftResult(postmortem=existing, created=False, updated=False)

        try:
            updated = self._store.update_postmortem_draft(merged)
        except IncidentError as exc:
            raise PostmortemDraftConsumerError(str(exc)) from exc
        return ResolvedIncidentDraftResult(postmortem=updated, created=False, updated=True)

    def _incident_from_payload(self, payload: Mapping[str, Any]) -> IncidentCase:
        if not isinstance(payload, Mapping):
            raise PostmortemDraftConsumerError("resolved incident payload must be an object")

        incident_id = str(payload.get("incident_id") or "").strip()
        if not incident_id and isinstance(payload.get("incident"), Mapping):
            incident_id = str(payload["incident"].get("incident_id") or "").strip()
        if not incident_id:
            raise PostmortemDraftConsumerError("incident_id is required")

        incident = self._store.get_incident(incident_id)
        if incident is None:
            raise PostmortemDraftConsumerError(f"IncidentCase not found: {incident_id}")
        return incident


def build_postmortem_draft_from_incident(
    incident: IncidentCase,
    *,
    created_at: str | None = None,
) -> Postmortem:
    """Build the canonical draft content for a resolved IncidentCase."""
    created = created_at or _utc_now()
    timeline = [
        {
            "ts": incident.created_at,
            "description": f"Incident {incident.incident_id} opened",
            "actor": "incident-service",
        }
    ]
    if incident.resolved_at:
        timeline.append(
            {
                "ts": incident.resolved_at,
                "description": f"Incident {incident.incident_id} resolved",
                "actor": "incident-service",
            }
        )
    timeline.append(
        {
            "ts": created,
            "description": "Postmortem draft generated from resolved incident evidence",
            "actor": "postmortem-service",
        }
    )

    contributing_factors = [f"incident_severity={incident.severity}"]
    if incident.incident_cluster_id:
        contributing_factors.append(f"incident_cluster_id={incident.incident_cluster_id}")
    if incident.evidence_summary:
        contributing_factors.append(f"incident_evidence_summary={incident.evidence_summary}")

    return Postmortem(
        postmortem_id=_postmortem_id_for_incident(incident.incident_id),
        title=f"Postmortem draft: {incident.title}",
        status=PostmortemStatus.DRAFT.value,
        created_at=created,
        incident_id=incident.incident_id,
        binding_id=incident.binding_id,
        deployment_stage=incident.deployment_stage,
        deployment_plan_id=incident.deployment_plan_id,
        capital_pool_id=incident.capital_pool_id,
        persona_capital_binding_id=incident.persona_capital_binding_id,
        artifact_id=incident.artifact_id,
        artifact_version=incident.artifact_version,
        runtime_id=incident.runtime_id,
        trace_id=incident.trace_id,
        root_cause=f"Draft generated from resolved incident {incident.incident_id}; root cause pending analysis.",
        contributing_factors=contributing_factors,
        timeline=timeline,
        action_items=[
            "Complete root cause analysis",
            "Validate remediation against linked telemetry and reconciliation evidence",
        ],
        author_ids=["postmortem-draft-worker"],
        telemetry_event_ids=list(incident.telemetry_event_ids),
        reconciliation_ids=list(incident.reconciliation_ids),
        incident_cluster_id=incident.incident_cluster_id,
        incident_evidence_summary=incident.evidence_summary,
        lineage_ref=incident.lineage_ref,
    )


def merge_postmortem_draft(existing: Postmortem, incoming: Postmortem) -> Postmortem:
    """Merge generated incident evidence into an existing draft."""
    updates = {
        "telemetry_event_ids": _merge_unique(existing.telemetry_event_ids, incoming.telemetry_event_ids),
        "reconciliation_ids": _merge_unique(existing.reconciliation_ids, incoming.reconciliation_ids),
        "contributing_factors": _merge_unique(existing.contributing_factors, incoming.contributing_factors),
        "timeline": _merge_timeline(existing.timeline, incoming.timeline),
        "action_items": _merge_unique(existing.action_items, incoming.action_items),
        "author_ids": _merge_unique(existing.author_ids, incoming.author_ids),
        "incident_cluster_id": existing.incident_cluster_id or incoming.incident_cluster_id,
        "incident_evidence_summary": _merge_summary(
            existing.incident_evidence_summary,
            incoming.incident_evidence_summary,
        ),
        "lineage_ref": existing.lineage_ref or incoming.lineage_ref,
    }
    return Postmortem(**{**existing.to_dict(), **updates})


def _postmortem_id_for_incident(incident_id: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9_.:-]+", "-", incident_id).strip("-")
    return f"pm-{suffix or 'incident'}"


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for group in (left, right):
        for value in group or []:
            item = str(value).strip()
            if item:
                seen[item] = None
    return list(seen)


def _merge_timeline(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    merged: list[dict[str, Any]] = []
    for group in (left, right):
        for event in group or []:
            if not isinstance(event, Mapping):
                continue
            normalized = dict(event)
            key = (
                str(normalized.get("ts") or ""),
                str(normalized.get("description") or ""),
                str(normalized.get("actor") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(normalized)
    return merged


def _merge_summary(left: str | None, right: str | None) -> str | None:
    left = (left or "").strip()
    right = (right or "").strip()
    if not left:
        return right or None
    if right.startswith(left):
        return right
    if not right or right in left:
        return left
    return f"{left}; {right}"
