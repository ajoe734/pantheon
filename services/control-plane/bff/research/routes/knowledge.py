"""Research knowledge, workbench, institutional memory, and synthesis routes."""
from __future__ import annotations

import inspect
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request

from .common import (
    ResearchRouteContext,
    _ENTITY_TYPE_EVIDENCE_KIND,
    _KW03_CREDIBILITY_TIERS,
    _KW03_LINK_TYPES,
    _KW03_LINKED_ENTITY_TYPES,
    _KW04_LINKED_ENTITY_TYPES,
    _KW04_RECENCY_VALUES,
    _KW04_STATUSES,
    _KW05_LIFECYCLE_STATES,
    _authorization,
    _body_parameter,
    _path,
    _signature,
    _signature_query,
)

try:
    from services.control_plane.bff.models import (
        ErrorCode,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
        redact_evidence_refs,
    )
except (ImportError, ValueError):
    from ..models import (
        ErrorCode,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
        redact_evidence_refs,
    )

_KW02_ATTACHMENT_TYPES = {"research_ticket", "persona", "strategy_spec", "free_standing"}
_KW02_ATTACHMENT_ID_PATTERNS = {
    "research_ticket": re.compile(r"^tkt-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "persona": re.compile(r"^persona-[A-Za-z0-9][A-Za-z0-9_-]*$"),
    "strategy_spec": re.compile(r"^strat-[A-Za-z0-9-]+$"),
}
_KW02_MEMORY_ANCHOR_PATTERN = re.compile(
    r"^mem-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def build_knowledge_router(ctx: ResearchRouteContext) -> APIRouter:
    router = APIRouter()

    def _kw02_bad_request(message: str, reason: str, field: str) -> None:
        raise ctx.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            message,
            reason,
            precondition_failed=field,
        )

    def _kw02_optional_title(payload: Dict[str, Any]) -> Optional[str]:
        title = payload.get("title")
        if title in (None, ""):
            return None
        normalized = str(title).strip()
        if not normalized:
            return None
        if len(normalized) > 256:
            _kw02_bad_request(
                "Invalid title",
                "title must be 256 characters or fewer",
                "title",
            )
        return normalized

    def _kw02_required_body(payload: Dict[str, Any]) -> str:
        body = payload.get("body")
        if body is None or not str(body).strip():
            _kw02_bad_request(
                "Missing required field: body",
                "body must be a non-empty string",
                "body",
            )
        return str(body).strip()

    def _kw02_validate_string_list(value: Any, field: str) -> List[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            _kw02_bad_request(
                f"Invalid {field}",
                f"{field} must be an array of strings",
                field,
            )
        normalized: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text:
                _kw02_bad_request(
                    f"Invalid {field} entry",
                    f"{field} entries must be non-empty strings",
                    field,
                )
            normalized.append(text)
        return normalized

    def _kw02_validate_attachment_type(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _KW02_ATTACHMENT_TYPES:
            _kw02_bad_request(
                "Invalid attachment_type",
                f"attachment_type must be one of {sorted(_KW02_ATTACHMENT_TYPES)}",
                "attachment_type",
            )
        return normalized

    def _kw02_validate_attachment_ref(attachment_type: str, value: Any) -> Optional[str]:
        if attachment_type == "free_standing":
            if value not in (None, ""):
                _kw02_bad_request(
                    "Invalid attachment_ref",
                    "attachment_ref must be null when attachment_type is free_standing",
                    "attachment_ref",
                )
            return None

        ref = str(value or "").strip()
        if not ref:
            _kw02_bad_request(
                "Missing attachment_ref",
                "attachment_ref is required unless attachment_type is free_standing",
                "attachment_ref",
            )
        pattern = _KW02_ATTACHMENT_ID_PATTERNS.get(attachment_type)
        if pattern is not None and not pattern.match(ref):
            _kw02_bad_request(
                "Invalid attachment_ref",
                f"attachment_ref does not match the identity format for {attachment_type}",
                "attachment_ref",
            )
        return ref

    def _kw02_validate_memory_anchors(port: Any, anchor_ids: List[str]) -> List[str]:
        validated: List[str] = []
        for entry_id in anchor_ids:
            if not _KW02_MEMORY_ANCHOR_PATTERN.match(entry_id):
                _kw02_bad_request(
                    "Invalid linked_memory_anchors entry",
                    "linked_memory_anchors items must use the mem-{UUID} format",
                    "linked_memory_anchors",
                )
            if ctx.call_port(port, "get_institutional_memory_entry", entry_id) is None:
                _kw02_bad_request(
                    "Unknown linked_memory_anchors entry",
                    f"linked_memory_anchors entry {entry_id} does not resolve to a known institutional memory entry",
                    "linked_memory_anchors",
                )
            validated.append(entry_id)
        return validated

    def _kw02_attachment_exists(port: Any, attachment_type: str, attachment_ref: Optional[str]) -> bool:
        if attachment_type == "free_standing":
            return True
        method = {
            "research_ticket": "get_research_ticket",
            "persona": "get_persona",
            "strategy_spec": "get_strategy_spec",
        }[attachment_type]
        return ctx.call_port(port, method, attachment_ref) is not None

    def _kw02_surface_state(port: Any, *, snapshot_at: str, has_data: bool) -> str:
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_notes") or "missing") if callable(source_fn) else "missing"
        surface = ctx.dataset_surface_status(
            "research_notes",
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
        )
        if surface.get("status") == "unavailable":
            return "unavailable"
        if surface.get("status") == "degraded" or surface.get("source") == "local_snapshot":
            return "degraded"
        return "ok"

    def _kw02_operator_display_name(operator_id: str) -> str:
        if operator_id == "op-001":
            return "Alice Chen"
        token = str(operator_id or "").strip()
        if not token:
            return "Operator"
        if token.startswith("op-"):
            return f"Operator {token}"
        return " ".join(part.capitalize() for part in re.split(r"[-_]+", token) if part)

    def _kw02_strip_markdown(text: str) -> str:
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        plain = re.sub(r"[`*_>#]", " ", plain)
        return re.sub(r"\s+", " ", plain).strip()

    def _kw02_resolve_attachment_target(
        port: Any,
        attachment_type: str,
        attachment_ref: Optional[str],
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        if attachment_type == "free_standing":
            return True, None, None
        if attachment_type == "research_ticket":
            ticket = ctx.call_port(port, "get_research_ticket", attachment_ref)
            if not ticket:
                return False, None, None
            return True, ticket.get("title"), f"/research/tickets/{attachment_ref}"
        if attachment_type == "persona":
            persona = ctx.call_port(port, "get_persona", attachment_ref)
            if not persona:
                return False, None, None
            return True, persona.get("name"), f"/personas/{attachment_ref}"
        strategy_spec = ctx.call_port(port, "get_strategy_spec", attachment_ref)
        if not strategy_spec:
            return False, None, None
        label = strategy_spec.get("title") or strategy_spec.get("name") or attachment_ref
        return True, label, f"/knowledge/strategy-specs/{attachment_ref}"

    def _kw02_note_list_item(port: Any, note: Dict[str, Any]) -> Dict[str, Any]:
        attachment_type = str(note.get("attachment_type") or "free_standing")
        attachment_ref = note.get("attachment_ref")
        attachment_exists, attachment_label, _ = _kw02_resolve_attachment_target(
            port,
            attachment_type,
            attachment_ref,
        )
        return {
            "note_id": note.get("note_id"),
            "title": note.get("title"),
            "excerpt": _kw02_strip_markdown(str(note.get("body") or ""))[:280],
            "owner_ref": json.loads(json.dumps(note.get("owner_ref") or {})),
            "attachment": {
                "type": attachment_type,
                "ref": attachment_ref,
                "display_label": attachment_label if attachment_exists else None,
            },
            "tags": list(note.get("tags") or []),
            "created_at": note.get("created_at"),
            "updated_at": note.get("updated_at"),
            "route_href": f"/knowledge/notes/{note.get('note_id')}",
        }

    def _kw02_attachment_payload(
        port: Any,
        note: Dict[str, Any],
        *,
        include_route: bool,
    ) -> Dict[str, Any]:
        attachment_type = str(note.get("attachment_type") or "free_standing")
        attachment_ref = note.get("attachment_ref")
        exists, display_label, route_href = _kw02_resolve_attachment_target(
            port,
            attachment_type,
            attachment_ref,
        )
        payload = {
            "type": attachment_type,
            "ref": attachment_ref,
            "display_label": display_label if exists else None,
        }
        if include_route:
            payload["route_href"] = route_href if exists else None
        return payload

    def _kw02_resolve_evidence_links(
        port: Any,
        ref_ids: List[str],
        *,
        snapshot_at: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        surface_state = _knowledge_surface_state(
            "evidence_refs", snapshot_at=snapshot_at, has_data=True,
        )
        items: List[Dict[str, Any]] = []
        for ref_id in ref_ids:
            if surface_state == "unavailable":
                items.append({
                    "ref_id": ref_id,
                    "resolution_state": "unavailable",
                    "display_label": None,
                    "route_href": None,
                })
                continue
            evidence_ref = ctx.call_port(port, "get_evidence_ref", ref_id)
            if evidence_ref:
                items.append({
                    "ref_id": ref_id,
                    "resolution_state": "resolved",
                    "display_label": evidence_ref.get("display_label"),
                    "route_href": evidence_ref.get("route_href") or f"/knowledge/evidence/{ref_id}",
                })
                continue
            items.append({
                "ref_id": ref_id,
                "resolution_state": "unresolved",
                "display_label": None,
                "route_href": None,
            })
        return items, surface_state

    def _kw02_resolve_memory_anchors(
        port: Any,
        entry_ids: List[str],
        *,
        snapshot_at: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        surface_state = _knowledge_surface_state(
            "institutional_memory_entries", snapshot_at=snapshot_at, has_data=True,
        )
        items: List[Dict[str, Any]] = []
        missing_entries = False
        for entry_id in entry_ids:
            entry = ctx.call_port(port, "get_institutional_memory_entry", entry_id)
            if not entry:
                missing_entries = True
                continue
            content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
            lifecycle = entry.get("lifecycle") if isinstance(entry.get("lifecycle"), dict) else {}
            items.append({
                "entry_id": entry_id,
                "headline": content.get("headline") or entry.get("headline"),
                "knowledge_type": entry.get("knowledge_type"),
                "lifecycle_status": lifecycle.get("status"),
                "route_href": f"/knowledge/memory/{entry_id}",
            })
        if missing_entries and surface_state == "ok":
            surface_state = "degraded"
        return items, surface_state

    def _research_note_detail_payload(
        port: Any,
        note: Dict[str, Any],
        *,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        evidence_links, evidence_surface = _kw02_resolve_evidence_links(
            port,
            list(note.get("linked_evidence_refs") or []),
            snapshot_at=snapshot_at,
        )
        memory_anchors, memory_surface = _kw02_resolve_memory_anchors(
            port,
            list(note.get("linked_memory_anchors") or []),
            snapshot_at=snapshot_at,
        )
        return {
            "note_id": note.get("note_id"),
            "title": note.get("title"),
            "body": note.get("body"),
            "owner_ref": json.loads(json.dumps(note.get("owner_ref") or {})),
            "attachment": _kw02_attachment_payload(port, note, include_route=True),
            "tags": list(note.get("tags") or []),
            "linked_evidence_refs": evidence_links,
            "linked_memory_anchors": memory_anchors,
            "created_at": note.get("created_at"),
            "updated_at": note.get("updated_at"),
            "meta": {
                **ctx.snapshot_meta(snapshot_at),
                "surfaces": {
                    "research_note_detail": _knowledge_surface_state(
                        "research_notes", snapshot_at=snapshot_at, has_data=True,
                    ),
                    "evidence_links": evidence_surface,
                    "memory_anchors": memory_surface,
                },
            },
        }

    def _kw05_bad_request(message: str, reason: str, field: str) -> None:
        raise ctx.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            message,
            reason,
            precondition_failed=field,
        )

    def _kw05_surface_state(*, snapshot_at: str, has_data: bool) -> str:
        return _knowledge_surface_state(
            "strategy_specs", snapshot_at=snapshot_at, has_data=has_data,
        )

    def _kw05_validate_lifecycle_state(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _KW05_LIFECYCLE_STATES:
            _kw05_bad_request(
                "Invalid lifecycle_state",
                f"lifecycle_state must be one of {sorted(_KW05_LIFECYCLE_STATES)}",
                "lifecycle_state",
            )
        return normalized

    def _kw05_compare_selectors(
        *,
        left_version: Optional[str],
        right_version: Optional[str],
        base_version: Optional[str],
        target_version: Optional[str],
    ) -> Tuple[str, str]:
        left = str(left_version or base_version or "").strip()
        right = str(right_version or target_version or "").strip()
        if not left or not right:
            _kw05_bad_request(
                "Missing compare versions",
                "Provide either left_version/right_version or base_version/target_version",
                "left_version",
            )
        if left_version and base_version and str(left_version).strip() != str(base_version).strip():
            _kw05_bad_request(
                "Conflicting compare aliases",
                "left_version and base_version must reference the same version when both are provided",
                "left_version",
            )
        if right_version and target_version and str(right_version).strip() != str(target_version).strip():
            _kw05_bad_request(
                "Conflicting compare aliases",
                "right_version and target_version must reference the same version when both are provided",
                "right_version",
            )
        return left, right

    def _knowledge_bad_request(message: str, reason: str, field: str) -> None:
        raise ctx.bff_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            message,
            reason,
            precondition_failed=field,
        )

    def _validate_knowledge_choice(value: Any, *, field: str, allowed: set[str]) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in allowed:
            _knowledge_bad_request(
                f"Invalid {field}",
                f"{field} must be one of {sorted(allowed)}",
                field,
            )
        return normalized

    def _knowledge_surface_state(
        dataset: str,
        *,
        snapshot_at: str,
        has_data: bool,
        missing_message: Optional[str] = None,
    ) -> str:
        port = ctx.get_read_store()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn(dataset) or "missing") if callable(source_fn) else "missing"
        surface = ctx.dataset_surface_status(
            dataset,
            snapshot_at=snapshot_at,
            source=source,
            has_data=has_data,
            missing_message=missing_message,
        )
        if isinstance(surface, str):
            return surface
        status = str((surface or {}).get("status") or "")
        if status == "unavailable" or source == "missing":
            return "unavailable"
        if status == "degraded" or (surface or {}).get("source") == "local_snapshot":
            return "degraded"
        return "ok"

    def _evidence_detail_payload(
        evidence_ref: Dict[str, Any],
        *,
        ref_id: str,
        identity: Any,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        detail_surface = _knowledge_surface_state(
            "evidence_refs", snapshot_at=snapshot_at, has_data=True,
        )
        capabilities = _capabilities(identity)

        evidence_kind = str(evidence_ref.get("evidence_type") or "").strip()
        if not evidence_kind:
            source_document = evidence_ref.get("source_document")
            if isinstance(source_document, dict):
                evidence_kind = SOURCE_TYPE_TO_EVIDENCE_KIND.get(
                    str(source_document.get("source_type") or "").strip(), "",
                )
        if evidence_kind:
            [processed_self], _ = redact_evidence_refs(
                identity,
                [{"ref_id": ref_id, "evidence_type": evidence_kind}],
                capabilities=capabilities,
            )
            if isinstance(processed_self, dict) and processed_self.get("redacted"):
                return {
                    **processed_self,
                    "meta": {
                        **ctx.snapshot_meta(snapshot_at),
                        "surfaces": {
                            "evidence_ref_detail": detail_surface,
                            "resolved_link": detail_surface,
                            "linked_decisions": detail_surface,
                        },
                        "redacted_evidence_count": 1,
                    },
                }

        raw_linked_decisions = json.loads(
            json.dumps(evidence_ref.get("linked_decisions") or [])
        )
        annotated_decisions: List[Any] = []
        for decision in raw_linked_decisions:
            if not isinstance(decision, dict):
                annotated_decisions.append(decision)
                continue
            evidence_kind = _ENTITY_TYPE_EVIDENCE_KIND.get(
                str(decision.get("entity_type") or "").strip(),
            )
            if not evidence_kind:
                annotated_decisions.append(decision)
                continue
            annotated = dict(decision)
            annotated["evidence_type"] = evidence_kind
            if not annotated.get("ref_id") and not annotated.get("id"):
                annotated["ref_id"] = annotated.get("entity_ref") or ""
            annotated_decisions.append(annotated)
        processed_decisions, redacted_count = redact_evidence_refs(
            identity, annotated_decisions, capabilities=capabilities,
        )
        linked_decisions = [
            processed if isinstance(processed, dict) and processed.get("redacted") else original
            for original, processed in zip(raw_linked_decisions, processed_decisions)
        ]

        return {
            "ref_id": evidence_ref.get("ref_id"),
            "source_document": json.loads(json.dumps(evidence_ref.get("source_document") or {})),
            "link_type": evidence_ref.get("link_type"),
            "credibility": json.loads(json.dumps(evidence_ref.get("credibility") or {})),
            "resolved_link": json.loads(json.dumps(evidence_ref.get("resolved_link") or {})),
            "linked_object_summary": json.loads(
                json.dumps(evidence_ref.get("linked_object_summary") or {})
            ),
            "linked_decisions": linked_decisions,
            "source_note_context": json.loads(json.dumps(evidence_ref.get("source_note_context"))),
            "source_memory_context": json.loads(json.dumps(evidence_ref.get("source_memory_context"))),
            "created_at": evidence_ref.get("created_at"),
            "meta": {
                **ctx.snapshot_meta(snapshot_at),
                "surfaces": {
                    "evidence_ref_detail": detail_surface,
                    "resolved_link": detail_surface,
                    "linked_decisions": detail_surface,
                },
                "redacted_evidence_count": redacted_count,
            },
        }

    def _insight_supporting_evidence_surface(
        supporting_evidence_refs: List[Dict[str, Any]], *, snapshot_at: str,
    ) -> str:
        surface_state = _knowledge_surface_state(
            "evidence_refs", snapshot_at=snapshot_at, has_data=True,
        )
        if surface_state != "ok":
            return surface_state
        if any(
            not item.get("ref_id") or not isinstance(item.get("resolved_link"), dict)
            for item in supporting_evidence_refs
        ):
            return "degraded"
        return "ok"

    def _insight_linked_sources_surface(
        linked_sources: List[Dict[str, Any]], *, snapshot_at: str,
    ) -> str:
        dataset_map = {
            "memory_entry": "institutional_memory_entries",
            "research_note": "research_notes",
            "evidence_ref": "evidence_refs",
            "strategy_spec": "strategy_specs",
            "experiment": "research_experiments",
        }
        overall = "ok"
        for item in linked_sources:
            dataset = dataset_map.get(str(item.get("entity_type") or "").strip())
            if not dataset:
                return "degraded"
            surface_state = _knowledge_surface_state(
                dataset, snapshot_at=snapshot_at, has_data=True,
            )
            if surface_state == "unavailable":
                return "unavailable"
            if surface_state == "degraded":
                overall = "degraded"
            if not item.get("display_label") or "route_href" not in item:
                overall = "degraded"
        return overall

    def _insight_detail_payload(
        insight_card: Dict[str, Any], *, snapshot_at: str,
    ) -> Dict[str, Any]:
        supporting_evidence_refs = list(insight_card.get("supporting_evidence_refs") or [])
        linked_sources = list(insight_card.get("linked_sources") or [])
        return {
            "insight_id": insight_card.get("insight_id"),
            "summary": insight_card.get("summary"),
            "scope": insight_card.get("scope"),
            "scope_context": json.loads(json.dumps(insight_card.get("scope_context") or {})),
            "status": insight_card.get("status"),
            "superseded_by": json.loads(json.dumps(insight_card.get("superseded_by") or {})),
            "confidence": json.loads(json.dumps(insight_card.get("confidence") or {})),
            "tags": list(insight_card.get("tags") or []),
            "source_ref": insight_card.get("source_ref"),
            "supporting_evidence_refs": json.loads(json.dumps(supporting_evidence_refs)),
            "linked_sources": json.loads(json.dumps(linked_sources)),
            "aggregation_provenance": json.loads(
                json.dumps(insight_card.get("aggregation_provenance") or {})
            ),
            "created_at": insight_card.get("created_at"),
            "updated_at": insight_card.get("updated_at"),
            "meta": {
                **ctx.snapshot_meta(snapshot_at),
                "surfaces": {
                    "insight_card_detail": _knowledge_surface_state(
                        "insight_cards", snapshot_at=snapshot_at, has_data=True,
                    ),
                    "supporting_evidence_refs": _insight_supporting_evidence_surface(
                        supporting_evidence_refs, snapshot_at=snapshot_at,
                    ),
                    "linked_sources": _insight_linked_sources_surface(
                        linked_sources, snapshot_at=snapshot_at,
                    ),
                },
            },
        }

    def _memory_detail_payload(entry: Dict[str, Any], *, snapshot_at: str) -> Dict[str, Any]:
        source_event = entry.get("source_event") if isinstance(entry.get("source_event"), dict) else {}
        source_context_available = bool(source_event.get("type")) and bool(source_event.get("id"))
        return {
            **entry,
            "meta": {
                **ctx.snapshot_meta(snapshot_at),
                "surfaces": {
                    "entry_detail": _knowledge_surface_state(
                        "institutional_memory_entries", snapshot_at=snapshot_at, has_data=True,
                    ),
                    "source_context": _knowledge_surface_state(
                        "institutional_memory_entries",
                        snapshot_at=snapshot_at,
                        has_data=source_context_available,
                        missing_message="Institutional memory source context is unavailable.",
                    ),
                },
            },
        }

    def _capabilities(identity: Any) -> Optional[List[str]]:
        if ctx.get_capabilities is None:
            return None
        try:
            return ctx.get_capabilities(identity)
        except Exception:
            return None

    def _evidence_list_item(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ref_id": item.get("ref_id"),
            "source_document": json.loads(json.dumps(item.get("source_document") or {})),
            "link_type": item.get("link_type"),
            "credibility": json.loads(json.dumps(item.get("credibility") or {})),
            "linked_object_summary": json.loads(json.dumps(item.get("linked_object_summary") or {})),
            "resolved_link": json.loads(json.dumps(item.get("resolved_link") or {})),
            "route_href": item.get("route_href"),
        }

    def _insight_list_item(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "insight_id": item.get("insight_id"),
            "summary": item.get("summary"),
            "scope": item.get("scope"),
            "scope_ref": item.get("scope_ref"),
            "status": item.get("status"),
            "superseded_by_id": item.get("superseded_by_id"),
            "confidence": json.loads(json.dumps(item.get("confidence") or {})),
            "tags": list(item.get("tags") or []),
            "evidence_count": item.get("evidence_count"),
            "primary_evidence_count": item.get("primary_evidence_count"),
            "aggregated_at": item.get("aggregated_at")
            or (item.get("aggregation_provenance") or {}).get("aggregated_at"),
            "route_href": item.get("route_href")
            or (f"/knowledge/insights/{item.get('insight_id')}" if item.get("insight_id") else None),
        }

    def _insight_filter_metadata(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
        tag_counts: Dict[str, int] = {}
        entity_counts: Dict[str, int] = {}
        for card in cards:
            seen_tags: set[str] = set()
            for raw_tag in card.get("tags") or []:
                tag = str(raw_tag or "").strip()
                if tag and tag not in seen_tags:
                    tag_counts[tag] = tag_counts.get(tag, 0) + 1
                    seen_tags.add(tag)
            seen_entities: set[str] = set()
            for source in card.get("linked_sources") or []:
                if not isinstance(source, dict):
                    continue
                entity_type = str(source.get("entity_type") or "").strip()
                if entity_type and entity_type not in seen_entities:
                    entity_counts[entity_type] = entity_counts.get(entity_type, 0) + 1
                    seen_entities.add(entity_type)
        labels = {
            "memory_entry": "Institutional Memory",
            "research_note": "Research Note",
            "evidence_ref": "Evidence Reference",
            "strategy_spec": "Strategy Spec",
            "experiment": "Experiment",
        }
        return {
            "tags": [
                {"value": tag, "display_label": tag.replace("-", " ").title(), "count": count}
                for tag, count in sorted(tag_counts.items(), key=lambda value: (-value[1], value[0]))
            ],
            "linked_entity_types": [
                {
                    "value": entity,
                    "display_label": labels.get(entity, entity.replace("_", " ").title()),
                    "count": count,
                }
                for entity, count in sorted(entity_counts.items(), key=lambda value: (-value[1], value[0]))
            ],
            "recency_options": [
                {"value": value, "display_label": {"7d": "Last 7 days", "30d": "Last 30 days", "90d": "Last 90 days", "all": "All time"}[value]}
                for value in ("7d", "30d", "90d", "all")
            ],
            "total_active_count": sum(1 for card in cards if str(card.get("status") or "") == "active"),
        }

    def _within_recency(value: Any, recency: str, snapshot_at: str) -> bool:
        if recency == "all":
            return True
        try:
            raw = str(value or "").replace("Z", "+00:00")
            aggregated = datetime.fromisoformat(raw)
            if aggregated.tzinfo is None:
                aggregated = aggregated.replace(tzinfo=timezone.utc)
            snapshot = datetime.fromisoformat(str(snapshot_at).replace("Z", "+00:00"))
            if snapshot.tzinfo is None:
                snapshot = snapshot.replace(tzinfo=timezone.utc)
            return aggregated >= snapshot - timedelta(days={"7d": 7, "30d": 30, "90d": 90}[recency])
        except (TypeError, ValueError, KeyError):
            return False

    def _conflict_view(log: Dict[str, Any]) -> Dict[str, Any]:
        raw = json.loads(json.dumps(log))
        log_id = str(raw.get("log_id") or raw.get("id") or raw.get("conflict_resolution_log_id") or "").strip()
        proposal_ids = [str(value) for value in raw.get("proposal_ids") or [] if str(value).strip()]
        vetoes = {
            str(value.get("proposal_id")): value
            for value in raw.get("vetoed_proposals") or []
            if isinstance(value, dict) and value.get("proposal_id")
        }
        for proposal_id in vetoes:
            if proposal_id not in proposal_ids:
                proposal_ids.append(proposal_id)
        inputs = raw.get("weighting_inputs") if isinstance(raw.get("weighting_inputs"), dict) else {}
        outputs = raw.get("weighting_outputs") if isinstance(raw.get("weighting_outputs"), dict) else {}
        rows = []
        for proposal_id in proposal_ids:
            veto = vetoes.get(proposal_id)
            output = outputs.get(proposal_id)
            state = "vetoed" if veto else ("selected" if output not in (None, 0, "0") else "not_selected")
            row = {
                "proposal_id": proposal_id,
                "state": state,
                "input_weight": inputs.get(proposal_id),
                "output_share": output,
                "is_vetoed": bool(veto),
            }
            if veto:
                row.update({"persona_id": veto.get("persona_id"), "veto_reason": veto.get("reason"), "veto_detail": veto.get("detail")})
            rows.append(row)
        resolution_state = "rejected" if raw.get("rejected_reason") else ("committee_required" if raw.get("committee_ref") else ("resolved_with_veto" if vetoes else "resolved"))
        raw["id"] = log_id
        raw["resolution_state"] = resolution_state
        artifact_id = raw.get("allocation_policy_artifact_id") or raw.get("artifact_id")
        artifact_href = raw.get("allocation_policy_artifact_href") or raw.get("artifact_href")
        governance_approval_id = raw.get("governance_approval_id")
        raw["view"] = {
            "title": f"Synthesis conflict log {log_id}",
            "resolution_state": resolution_state,
            "summary": {
                "proposal_count": len(rows),
                "selected_count": sum(1 for row in rows if row["state"] == "selected"),
                "veto_count": sum(1 for row in rows if row["is_vetoed"]),
                "committee_required": bool(raw.get("committee_ref")),
                "sponsor_persona_id": raw.get("sponsor_persona_id"),
                "synthesis_method": raw.get("synthesis_method"),
                "capital_pool_id": raw.get("capital_pool_id"),
                "scope_ref": raw.get("scope_ref"),
            },
            "proposal_rows": rows,
            "governance": {
                "committee_ref": raw.get("committee_ref"),
                "rejected_reason": raw.get("rejected_reason"),
                "approval_id": governance_approval_id,
                "decision": raw.get("governance_decision"),
                "decision_state": raw.get("governance_decision_state"),
                "can_proceed": raw.get("governance_can_proceed"),
            },
            "links": {
                "allocation_policy_artifact": (
                    {"id": artifact_id, "href": artifact_href} if artifact_id else None
                ),
                "governance_approval": (
                    {"id": governance_approval_id, "href": f"/bff/approvals/{governance_approval_id}"}
                    if governance_approval_id
                    else None
                ),
            },
        }
        return raw

    # Endpoints
    async def endpoint_knowledge_workbench(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        if ctx.build_knowledge_workbench is not None:
            result = ctx.build_knowledge_workbench()
            return await result if inspect.isawaitable(result) else result
        records = list(ctx.call_port(port, "list_research_notes") or [])
        return {"data": records, "meta": ctx.meta(snapshot_at, "knowledge_workbench", "research_notes", bool(records))}

    async def endpoint_create_note(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        body = await ctx.body(request)
        if "owner_ref" in body:
            _kw02_bad_request(
                "Invalid owner_ref",
                "owner_ref is server-assigned and must not be supplied by the caller",
                "owner_ref",
            )
        identity = ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        title = _kw02_optional_title(body)
        note_body = _kw02_required_body(body)
        attachment_type = _kw02_validate_attachment_type(body.get("attachment_type"))
        attachment_ref = _kw02_validate_attachment_ref(attachment_type, body.get("attachment_ref"))
        tags = _kw02_validate_string_list(body.get("tags"), "tags")
        linked_evidence_refs = _kw02_validate_string_list(
            body.get("linked_evidence_refs"), "linked_evidence_refs"
        )
        linked_memory_anchors = _kw02_validate_memory_anchors(
            port,
            _kw02_validate_string_list(
                body.get("linked_memory_anchors"), "linked_memory_anchors"
            ),
        )
        if not _kw02_attachment_exists(port, attachment_type, attachment_ref):
            raise ctx.bff_error(
                422,
                ErrorCode.PRECONDITION_FAILED,
                "Attachment target does not exist",
                f"{attachment_type} target {attachment_ref} could not be resolved",
                precondition_failed="attachment_ref",
            )

        operator_id = str(getattr(identity, "operator_id", "") or "")
        note_id = f"note-{uuid.uuid4()}"
        note = {
            "note_id": note_id,
            "title": title,
            "body": note_body,
            "attachment_type": attachment_type,
            "attachment_ref": attachment_ref,
            "owner_ref": {
                "owner_type": "operator",
                "owner_id": operator_id,
                "display_name": _kw02_operator_display_name(operator_id),
            },
            "tags": tags,
            "linked_evidence_refs": linked_evidence_refs,
            "linked_memory_anchors": linked_memory_anchors,
            "created_at": snapshot_at,
            "updated_at": snapshot_at,
        }
        created = ctx.call_port(port, "create_research_note", note)
        if created is None:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Research note store unavailable",
                "Research note creation store is unavailable.",
            )
        return {
            "note_id": note_id,
            "created_at": snapshot_at,
            "route_href": f"/knowledge/notes/{note_id}",
        }

    async def endpoint_list_notes(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        attachment_type = ctx.query(request, "attachment_type")
        attachment_ref = ctx.query(request, "attachment_ref")
        validated_attachment_type = (
            _kw02_validate_attachment_type(attachment_type)
            if attachment_type is not None
            else None
        )
        if attachment_ref is not None and validated_attachment_type is None:
            _kw02_bad_request(
                "Invalid attachment_ref filter",
                "attachment_ref requires attachment_type to be set",
                "attachment_ref",
            )
        validated_attachment_ref = (
            _kw02_validate_attachment_ref(validated_attachment_type, attachment_ref)
            if validated_attachment_type is not None and attachment_ref is not None
            else None
        )
        notes = list(ctx.call_port(port, "list_research_notes") or [])
        notes_dataset_available = (
            getattr(port, "dataset_source", lambda _dataset: "missing")("research_notes")
            != "missing"
        )
        owner_ref = ctx.query(request, "owner_ref")
        if owner_ref:
            notes = [
                note
                for note in notes
                if str(((note.get("owner_ref") or {}).get("owner_id")) or "") == owner_ref
            ]
        if validated_attachment_type:
            notes = [
                note
                for note in notes
                if str(note.get("attachment_type") or "") == validated_attachment_type
            ]
        if validated_attachment_type == "free_standing" or validated_attachment_ref is not None:
            notes = [
                note
                for note in notes
                if note.get("attachment_ref") == validated_attachment_ref
            ]
        tags = ctx.query(request, "tags")
        if tags:
            requested_tags = {value.strip() for value in tags.split(",") if value.strip()}
            notes = [
                note
                for note in notes
                if requested_tags.intersection(set(note.get("tags") or []))
            ]
        surface_state = _kw02_surface_state(
            port,
            snapshot_at=snapshot_at,
            has_data=notes_dataset_available,
        )
        if surface_state == "unavailable":
            page_items, next_token, has_more = [], None, False
        else:
            page_items, next_token = ctx.page(notes, request)
            has_more = next_token is not None
        meta = ctx.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"research_note_list": surface_state}
        return {
            "notes": [_kw02_note_list_item(port, note) for note in page_items],
            "pagination": {
                "page_size": int(ctx.query(request, "page_size", "20") or 20),
                "next_page_token": next_token,
                "has_more": has_more,
            },
            "meta": meta,
        }

    async def endpoint_get_note(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        identifier = str(request.path_params.get("note_id") or "")
        record = ctx.call_port(port, "get_research_note", identifier)
        if not record:
            ctx.not_found("Research record", identifier)
        return _research_note_detail_payload(port, record, snapshot_at=snapshot_at)

    async def endpoint_list_evidence(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        linked_entity_type = ctx.query(request, "linked_entity_type")
        linked_entity_ref = ctx.query(request, "linked_entity_ref")
        link_type = ctx.query(request, "link_type")
        credibility_tier = ctx.query(request, "credibility_tier")
        verified_raw = ctx.query(request, "verified")
        validated_entity_type = (
            _validate_knowledge_choice(
                linked_entity_type,
                field="linked_entity_type",
                allowed=_KW03_LINKED_ENTITY_TYPES,
            )
            if linked_entity_type is not None
            else None
        )
        if linked_entity_ref is not None and validated_entity_type is None:
            _knowledge_bad_request(
                "Invalid linked_entity_ref filter",
                "linked_entity_ref requires linked_entity_type to be set",
                "linked_entity_ref",
            )
        validated_link_type = (
            _validate_knowledge_choice(link_type, field="link_type", allowed=_KW03_LINK_TYPES)
            if link_type is not None
            else None
        )
        validated_tier = (
            _validate_knowledge_choice(credibility_tier, field="credibility_tier", allowed=_KW03_CREDIBILITY_TIERS)
            if credibility_tier is not None
            else None
        )
        verified: Optional[bool] = None
        if verified_raw is not None:
            normalized_verified = str(verified_raw).strip().lower()
            if normalized_verified not in {"true", "false"}:
                _knowledge_bad_request("Invalid verified", "verified must be a boolean", "verified")
            verified = normalized_verified == "true"

        records = list(ctx.call_port(port, "list_evidence_refs") or [])
        if validated_entity_type:
            records = [
                item
                for item in records
                if str(((item.get("linked_object_summary") or {}).get("entity_type")) or "").lower()
                == validated_entity_type
            ]
        if linked_entity_ref is not None:
            records = [
                item
                for item in records
                if str(((item.get("linked_object_summary") or {}).get("entity_ref")) or "")
                == str(linked_entity_ref)
            ]
        if validated_link_type:
            records = [item for item in records if str(item.get("link_type") or "").lower() == validated_link_type]
        if validated_tier:
            records = [
                item
                for item in records
                if str(((item.get("credibility") or {}).get("tier")) or "").lower() == validated_tier
            ]
        if verified is not None:
            records = [item for item in records if bool((item.get("credibility") or {}).get("verified")) is verified]

        available = getattr(port, "dataset_source", lambda _dataset: "missing")("evidence_refs") != "missing"
        surface_state = _knowledge_surface_state(
            "evidence_refs", snapshot_at=snapshot_at, has_data=available,
        )
        if surface_state == "unavailable":
            page_items, next_token, has_more = [], None, False
        else:
            page_items, next_token = ctx.page(records, request)
            has_more = next_token is not None
        processed, redacted_count = redact_evidence_refs(
            identity, page_items, capabilities=_capabilities(identity)
        )
        response_items = [
            item if isinstance(item, dict) and item.get("redacted") else _evidence_list_item(item)
            for item in processed
        ]
        meta = ctx.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"evidence_refs_list": surface_state}
        meta["redacted_evidence_count"] = redacted_count
        return {
            "evidence_refs": response_items,
            "pagination": {
                "page_size": int(ctx.query(request, "page_size", "20") or 20),
                "next_page_token": next_token,
                "has_more": has_more,
            },
            "meta": meta,
        }

    async def endpoint_get_evidence(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        identifier = str(request.path_params.get("ref_id") or "")
        record = ctx.call_port(port, "get_evidence_ref_detail", identifier)
        if not record:
            ctx.not_found("Research record", identifier)
        return _evidence_detail_payload(record, ref_id=identifier, identity=identity, snapshot_at=snapshot_at)

    async def endpoint_list_insights(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        status = _validate_knowledge_choice(
            ctx.query(request, "status", "active"), field="status", allowed=_KW04_STATUSES,
        )
        recency = _validate_knowledge_choice(
            ctx.query(request, "recency", "all"), field="recency", allowed=_KW04_RECENCY_VALUES,
        )
        linked_entity_type = ctx.query(request, "linked_entity_type")
        linked_entity_ref = ctx.query(request, "linked_entity_ref")
        validated_entity_type = (
            _validate_knowledge_choice(linked_entity_type, field="linked_entity_type", allowed=_KW04_LINKED_ENTITY_TYPES)
            if linked_entity_type is not None
            else None
        )
        if linked_entity_ref is not None and validated_entity_type is None:
            _knowledge_bad_request(
                "Invalid linked_entity_ref filter",
                "linked_entity_ref requires linked_entity_type to be set",
                "linked_entity_ref",
            )
        confidence_raw = ctx.query(request, "confidence_min")
        confidence_min: Optional[float] = None
        if confidence_raw is not None:
            try:
                confidence_min = float(confidence_raw)
            except (TypeError, ValueError):
                _knowledge_bad_request("Invalid confidence_min", "confidence_min must be a number between 0.0 and 1.0", "confidence_min")
            if confidence_min < 0.0 or confidence_min > 1.0:
                _knowledge_bad_request("Invalid confidence_min", "confidence_min must be a number between 0.0 and 1.0", "confidence_min")
        include_inactive = str(ctx.query(request, "include_inactive", "false") or "false").lower() == "true"
        records = list(ctx.call_port(port, "list_insight_cards") or [])
        filter_metadata = _insight_filter_metadata(records)
        filtered = list(records)
        if not include_inactive and status != "all":
            filtered = [item for item in filtered if str(item.get("status") or "") == status]
        if ctx.query(request, "tag") is not None:
            tag = str(ctx.query(request, "tag") or "")
            filtered = [item for item in filtered if tag in set(item.get("tags") or [])]
        if validated_entity_type:
            filtered = [
                item for item in filtered if any(
                    str((source or {}).get("entity_type") or "") == validated_entity_type
                    for source in item.get("linked_sources") or []
                )
            ]
        if linked_entity_ref is not None:
            filtered = [
                item for item in filtered if any(
                    str((source or {}).get("entity_type") or "") == validated_entity_type
                    and str((source or {}).get("entity_ref") or "") == str(linked_entity_ref)
                    for source in item.get("linked_sources") or []
                )
            ]
        if recency != "all":
            filtered = [
                item for item in filtered if _within_recency(
                    item.get("aggregated_at") or (item.get("aggregation_provenance") or {}).get("aggregated_at"),
                    recency,
                    snapshot_at,
                )
            ]
        if confidence_min is not None:
            filtered = [
                item for item in filtered if float(((item.get("confidence") or {}).get("score")) or 0.0) >= confidence_min
            ]
        available = getattr(port, "dataset_source", lambda _dataset: "missing")("insight_cards") != "missing"
        surface_state = _knowledge_surface_state("insight_cards", snapshot_at=snapshot_at, has_data=available)
        if surface_state == "unavailable":
            page_items, next_token, has_more = [], None, False
        else:
            page_items, next_token = ctx.page(filtered, request)
            has_more = next_token is not None
        meta = ctx.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"insight_cards": surface_state}
        return {
            "insight_cards": [_insight_list_item(item) for item in page_items],
            "filter_metadata": filter_metadata if available else {
                "tags": [], "linked_entity_types": [],
                "recency_options": _insight_filter_metadata([])["recency_options"],
                "total_active_count": 0,
            },
            "pagination": {
                "page_size": int(ctx.query(request, "page_size", "20") or 20),
                "next_page_token": next_token,
                "has_more": has_more,
            },
            "meta": meta,
        }

    async def endpoint_get_insight(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        identifier = str(request.path_params.get("insight_id") or "")
        record = ctx.call_port(port, "get_insight_card_detail", identifier)
        if not record:
            ctx.not_found("Research record", identifier)
        return _insight_detail_payload(record, snapshot_at=snapshot_at)

    async def endpoint_list_strategy_specs(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        lifecycle_state = _kw05_validate_lifecycle_state(
            ctx.query(request, "lifecycle_state", "all")
        )
        kwargs = {
            "lifecycle_state": lifecycle_state,
            "source_kind": ctx.query(request, "source_kind"),
            "persona_id": ctx.query(request, "persona_id"),
            "include_retired": ctx.query(request, "include_retired", "false") == "true",
            "include_fixture_pack": False,
        }
        records = list(ctx.call_port(port, "list_strategy_specs", **kwargs) or [])
        source_fn = getattr(port, "dataset_source", None)
        dataset_available = (
            str(source_fn("strategy_specs") or "missing") != "missing"
            if callable(source_fn)
            else bool(records)
        )
        surface_state = _kw05_surface_state(
            snapshot_at=snapshot_at,
            has_data=dataset_available,
        )
        if surface_state == "unavailable":
            items, next_token, has_more = [], None, False
        else:
            items, next_token = ctx.page(records, request)
            has_more = next_token is not None
        meta = ctx.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"strategy_spec_list": surface_state}
        return {
            "items": items,
            "page_info": {
                "next_page_token": next_token,
                "page_size": int(ctx.query(request, "page_size", "20") or 20),
                "has_more": has_more,
            },
            "meta": meta,
        }

    async def endpoint_strategy_versions(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        strategy_id = str(request.path_params.get("strategy_id") or "")
        records = list(ctx.call_port(port, "list_strategy_spec_versions", strategy_id) or [])
        if not records and not ctx.call_port(port, "get_strategy_spec", strategy_id):
            ctx.not_found("Strategy spec", strategy_id)
        return {
            "strategy_id": strategy_id,
            "versions": records,
            "meta": {
                **ctx.snapshot_meta(snapshot_at),
                "surfaces": {
                    "version_history": _kw05_surface_state(
                        snapshot_at=snapshot_at,
                        has_data=True,
                    )
                },
            },
        }

    async def endpoint_strategy_compare(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        strategy_id = str(request.path_params.get("strategy_id") or "")
        left, right = _kw05_compare_selectors(
            left_version=ctx.query(request, "left_version"),
            right_version=ctx.query(request, "right_version"),
            base_version=ctx.query(request, "base_version"),
            target_version=ctx.query(request, "target_version"),
        )
        if left == right:
            raise ctx.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Compare requires two distinct versions",
                "left_version and right_version must identify different versions",
                precondition_failed="left_version",
            )
        left_detail = ctx.call_port(
            port,
            "get_strategy_spec_detail",
            strategy_id,
            version_selector=left,
        )
        right_detail = ctx.call_port(
            port,
            "get_strategy_spec_detail",
            strategy_id,
            version_selector=right,
        )
        if not left_detail or not right_detail:
            ctx.not_found("Strategy spec version", strategy_id)
        if not (left_detail.get("allowedActions") or {}).get("canCompare") or not (
            right_detail.get("allowedActions") or {}
        ).get("canCompare"):
            raise ctx.bff_error(
                422,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "One or more versions cannot be compared",
                "Compare accepts only candidate, approved, or retired strategy spec versions",
                precondition_failed="lifecycle_state",
            )
        comparison = ctx.call_port(port, "compare_strategy_spec_versions", strategy_id, left_selector=left, right_selector=right)
        if not comparison:
            ctx.not_found("Strategy spec version", strategy_id)
        payload = dict(comparison)
        payload["meta"] = {
            **ctx.snapshot_meta(snapshot_at),
            "surfaces": {
                "strategy_spec_compare": _kw05_surface_state(
                    snapshot_at=snapshot_at,
                    has_data=True,
                )
            },
        }
        return payload

    async def endpoint_get_strategy_spec(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        identifier = str(request.path_params.get("strategy_id") or "")
        if not ctx.call_port(port, "get_strategy_spec", identifier):
            ctx.not_found("Strategy spec", identifier)
        kwargs = {"version_selector": ctx.query(request, "version", "current")}
        record = ctx.call_port(port, "get_strategy_spec_detail", identifier, **kwargs)
        if not record:
            ctx.not_found("Strategy spec version", identifier)
        detail_surface = _kw05_surface_state(
            snapshot_at=snapshot_at,
            has_data=True,
        )
        citation_bundle = json.loads(json.dumps(record.get("citation_bundle") or {}))
        citation_surface = "partial" if not any(citation_bundle.values()) else detail_surface
        ancestry_surface = (
            "degraded"
            if record.get("parent_spec_version_id") is None
            and str(ctx.query(request, "version", "current") or "").strip() not in {"", "current"}
            else detail_surface
        )
        return {
            "object_ref": json.loads(json.dumps(record.get("object_ref") or {})),
            "strategy_id": record.get("strategy_id"),
            "spec_version_id": record.get("spec_version_id"),
            "spec_version": record.get("spec_version"),
            "parent_spec_version_id": record.get("parent_spec_version_id"),
            "derived_from_source_refs": list(record.get("derived_from_source_refs") or []),
            "lifecycle_state": record.get("lifecycle_state"),
            "title": record.get("title"),
            "hypothesis": record.get("hypothesis"),
            "objective": record.get("objective"),
            "market_scope": json.loads(json.dumps(record.get("market_scope") or {})),
            "execution_profile": json.loads(json.dumps(record.get("execution_profile") or {})),
            "evaluation_plan": json.loads(json.dumps(record.get("evaluation_plan") or {})),
            "governance": json.loads(json.dumps(record.get("governance") or {})),
            "citation_bundle": citation_bundle,
            "allowedActions": json.loads(json.dumps(record.get("allowedActions") or {})),
            "meta": {
                **ctx.snapshot_meta(snapshot_at),
                "surfaces": {
                    "strategy_spec_detail": detail_surface,
                    "citation_bundle": citation_surface,
                    "version_ancestry": ancestry_surface,
                },
            },
        }

    async def endpoint_list_memory(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        records = list(ctx.call_port(port, "list_institutional_memory_entries") or [])
        knowledge_type = ctx.query(request, "knowledge_type")
        scope = ctx.query(request, "scope")
        scope_filter = ctx.query(request, "scope_filter")
        tags = ctx.query(request, "tags")
        if knowledge_type:
            records = [item for item in records if str(item.get("knowledge_type") or "") == knowledge_type]
        if scope:
            records = [
                item for item in records
                if (
                    str(item.get("scope") or "") == scope
                    if not isinstance(item.get("scope"), dict)
                    else str((item.get("scope") or {}).get("type") or "") == scope
                )
            ]
        if scope_filter:
            records = [
                item for item in records
                if str(item.get("scope_filter") or ((item.get("scope") or {}).get("filter") if isinstance(item.get("scope"), dict) else "")) == scope_filter
            ]
        if tags:
            requested_tags = {value.strip() for value in str(tags).split(",") if value.strip()}
            records = [item for item in records if requested_tags.intersection(set(item.get("tags") or []))]
        try:
            page_number = int(ctx.query(request, "page", "1") or 1)
            page_size = int(ctx.query(request, "page_size", "20") or 20)
        except (TypeError, ValueError):
            _knowledge_bad_request("Invalid pagination", "page and page_size must be integers", "page")
        page_size = max(1, min(page_size, 200))
        total_count = len(records)
        start = (page_number - 1) * page_size
        page_items = records[start : start + page_size]
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        available = getattr(port, "dataset_source", lambda _dataset: "missing")("institutional_memory_entries") != "missing"
        surface_state = _knowledge_surface_state(
            "institutional_memory_entries", snapshot_at=snapshot_at, has_data=available,
            missing_message="Institutional memory list is unavailable.",
        )
        if surface_state == "unavailable":
            page_items, total_count, total_pages = [], 0, 0
        entries = []
        for item in page_items:
            if "headline" in item:
                entries.append(item)
                continue
            content = item.get("content") if isinstance(item.get("content"), dict) else {}
            scope_value = item.get("scope") if isinstance(item.get("scope"), dict) else {}
            lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else {}
            usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
            entries.append({
                "entry_id": item.get("entry_id") or item.get("id"),
                "headline": content.get("headline"),
                "knowledge_type": item.get("knowledge_type"),
                "scope": scope_value.get("type"),
                "scope_filter": scope_value.get("filter"),
                "tags": list(content.get("tags") or item.get("tags") or []),
                "reuse_count": int(usage.get("reuse_count") or 0),
                "is_superseded": bool(lifecycle.get("superseded_by")),
                "written_at": item.get("written_at"),
                "write_authority": item.get("write_authority"),
                "route_href": f"/knowledge/memory/{item.get('entry_id') or item.get('id')}",
            })
        meta = ctx.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"memory_list": surface_state}
        return {
            "entries": entries,
            "pagination": {
                "total_count": total_count,
                "page": page_number,
                "page_size": page_size,
                "total_pages": total_pages,
            },
            "meta": meta,
        }

    async def endpoint_get_memory(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        identifier = str(request.path_params.get("entry_id") or "")
        record = ctx.call_port(port, "get_institutional_memory_entry", identifier)
        if not record:
            ctx.not_found("Research record", identifier)
        return _memory_detail_payload(record, snapshot_at=snapshot_at)

    async def endpoint_synthesis_conflict_logs(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        raw_flag = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
        if raw_flag is not None and raw_flag.strip().lower() in {"0", "false", "no", "off", "disabled"}:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict log view disabled",
                "PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED is disabled for this BFF instance.",
                precondition_failed="synthesis_conflict_log_feature_flag",
            )
        list_reader = ctx.list_synthesis_conflict_logs
        if list_reader is not None:
            try:
                records = list(list_reader(
                    capital_pool_id=ctx.query(request, "capital_pool_id"),
                    scope_ref=ctx.query(request, "scope_ref"),
                    proposal_id=ctx.query(request, "proposal_id"),
                    sponsor_persona_id=ctx.query(request, "sponsor_persona_id"),
                    synthesis_method=ctx.query(request, "synthesis_method"),
                    committee_ref=ctx.query(request, "committee_ref"),
                ) or [])
            except TypeError:
                records = list(list_reader() or [])
        elif callable(getattr(port, "list_synthesis_conflict_logs", None)):
            records = list(ctx.call_port(
                port,
                "list_synthesis_conflict_logs",
                capital_pool_id=ctx.query(request, "capital_pool_id"),
                scope_ref=ctx.query(request, "scope_ref"),
                proposal_id=ctx.query(request, "proposal_id"),
                sponsor_persona_id=ctx.query(request, "sponsor_persona_id"),
                synthesis_method=ctx.query(request, "synthesis_method"),
                committee_ref=ctx.query(request, "committee_ref"),
            ) or [])
        else:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict logs are unavailable",
                "Inject list_synthesis_conflict_logs from the synthesis read adapter",
            )
        items, next_token = ctx.page(records, request)
        projected = [_conflict_view(item) for item in items]
        return {
            "data": projected,
            "items": projected,
            "page_info": {"next_page_token": next_token, "total": len(records)},
            "meta": ctx.meta(snapshot_at, "synthesis_conflict_logs", "synthesis_conflict_logs", bool(records)),
        }

    async def endpoint_synthesis_conflict_log(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        raw_flag = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
        if raw_flag is not None and raw_flag.strip().lower() in {"0", "false", "no", "off", "disabled"}:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict log view disabled",
                "PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED is disabled for this BFF instance.",
                precondition_failed="synthesis_conflict_log_feature_flag",
            )
        get_reader = ctx.get_synthesis_conflict_log
        log_id = str(request.path_params.get("log_id") or "")
        if get_reader is not None:
            record = get_reader(log_id)
        elif callable(getattr(port, "get_synthesis_conflict_log", None)):
            record = ctx.call_port(port, "get_synthesis_conflict_log", log_id)
        else:
            raise ctx.bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict logs are unavailable",
                "Inject get_synthesis_conflict_log from the synthesis read adapter",
            )
        if not record:
            ctx.not_found("Synthesis conflict log", log_id)
        return {
            "data": _conflict_view(record),
            "meta": ctx.meta(snapshot_at, "synthesis_conflict_log", "synthesis_conflict_logs", True),
        }

    async def endpoint_bff_search(request: Request, **_kwargs: Any) -> Dict[str, Any]:
        identity = ctx.identity(request)
        port = ctx.get_read_store()
        snapshot_at = ctx.utc_now()
        query = str(ctx.query(request, "q", "") or "").strip()
        types_raw = ctx.query(request, "types")
        requested_types = {item.strip().lower() for item in str(types_raw).split(",") if item.strip()} if types_raw else None
        effective_page_size = int(ctx.query(request, "limit") or ctx.query(request, "page_size", "20") or 20)
        effective_page_size = max(1, min(effective_page_size, 100))
        if ctx.cross_entity_search is not None:
            result = ctx.cross_entity_search(
                query=query,
                types=requested_types,
                page_size=effective_page_size,
                page_token=ctx.query(request, "page_token"),
                identity=identity,
            )
            result = await result if inspect.isawaitable(result) else result
            if isinstance(result, dict):
                return result
            records = list(result or [])
        else:
            records = []
            needle = query.lower()

            def _matches(value: Any) -> bool:
                return not needle or needle in str(value or "").lower()

            if not requested_types or "strategy" in requested_types:
                strategy_reader = getattr(port, "list_strategies", None) or getattr(port, "list_strategy_summaries", None)
                if callable(strategy_reader):
                    for raw in strategy_reader() or []:
                        item_id = str(raw.get("strategy_id") or raw.get("id") or "")
                        name_value = raw.get("title") or raw.get("name") or item_id
                        if _matches(item_id) or _matches(name_value):
                            records.append({"id": item_id, "type": "strategy", "name": str(name_value), "state": raw.get("lifecycle_state") or raw.get("status"), "owner": raw.get("owner") or "pantheon-bff", "risk": "medium", "updatedAt": raw.get("updated_at") or raw.get("last_modified_at") or snapshot_at})
            if not requested_types or "persona" in requested_types:
                persona_reader = getattr(port, "list_personas", None)
                if callable(persona_reader):
                    for raw in persona_reader() or []:
                        item_id = str(raw.get("persona_id") or raw.get("id") or "")
                        name_value = raw.get("name") or item_id
                        if _matches(item_id) or _matches(name_value):
                            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
                            records.append({"id": item_id, "type": "persona", "name": str(name_value), "state": raw.get("lifecycle_state") or raw.get("status"), "owner": metadata.get("owner") or raw.get("owner") or "pantheon-bff", "risk": metadata.get("risk_level") or "medium", "updatedAt": raw.get("updated_at") or raw.get("created_at") or snapshot_at})
            if not requested_types or "capital_pool" in requested_types or "capitalpool" in requested_types:
                pool_reader = getattr(port, "list_capital_pools", None)
                if callable(pool_reader):
                    for raw in pool_reader() or []:
                        item_id = str(raw.get("pool_id") or raw.get("id") or "")
                        name_value = raw.get("name") or item_id
                        if _matches(item_id) or _matches(name_value):
                            records.append({"id": item_id, "type": "capital_pool", "name": str(name_value), "state": raw.get("status"), "owner": raw.get("owner") or "pantheon-bff", "risk": raw.get("risk_level") or "medium", "updatedAt": raw.get("updated_at") or raw.get("created_at") or snapshot_at})
        items, next_token = ctx.page(records, request)
        return {"data": items, "items": items, "page_info": {"next_page_token": next_token, "total": len(records), "returned": len(items)}, "meta": ctx.meta(snapshot_at, "search", "personas", bool(records))}

    auth = _authorization()

    endpoint_knowledge_workbench.__signature__ = _signature(auth)
    endpoint_create_note.__signature__ = _signature(_body_parameter(), auth)
    endpoint_list_notes.__signature__ = _signature(
        _signature_query("owner_ref"), _signature_query("attachment_type"), _signature_query("attachment_ref"),
        _signature_query("tags"), _signature_query("page_token"),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
    )
    endpoint_get_note.__signature__ = _signature(_path("note_id"), auth)
    endpoint_list_evidence.__signature__ = _signature(
        _signature_query("linked_entity_type"), _signature_query("linked_entity_ref"),
        _signature_query("link_type"), _signature_query("credibility_tier"),
        _signature_query("verified", annotation=Optional[bool]), _signature_query("page_token"),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
    )
    endpoint_get_evidence.__signature__ = _signature(_path("ref_id"), auth)
    endpoint_list_insights.__signature__ = _signature(
        _signature_query("status", annotation=str, default="active"), _signature_query("tag"),
        _signature_query("linked_entity_type"), _signature_query("linked_entity_ref"),
        _signature_query("recency", annotation=str, default="all"), _signature_query("confidence_min", annotation=Optional[float]),
        _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100),
        _signature_query("include_inactive", annotation=bool, default=False), auth,
    )
    endpoint_get_insight.__signature__ = _signature(_path("insight_id"), auth)
    endpoint_list_strategy_specs.__signature__ = _signature(
        _signature_query("lifecycle_state", annotation=str, default="all"), _signature_query("source_kind"),
        _signature_query("persona_id"), _signature_query("include_retired", annotation=bool, default=False),
        _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=100), auth,
    )
    endpoint_strategy_versions.__signature__ = _signature(_path("strategy_id"), auth)
    endpoint_strategy_compare.__signature__ = _signature(
        _path("strategy_id"), _signature_query("left_version"), _signature_query("right_version"),
        _signature_query("base_version"), _signature_query("target_version"), auth,
    )
    endpoint_get_strategy_spec.__signature__ = _signature(_path("strategy_id"), _signature_query("version", annotation=str, default="current"), auth)
    endpoint_list_memory.__signature__ = _signature(
        _signature_query("knowledge_type"), _signature_query("scope"), _signature_query("scope_filter"),
        _signature_query("tags"), _signature_query("page", annotation=int, default=1, ge=1),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=200), auth,
    )
    endpoint_get_memory.__signature__ = _signature(_path("entry_id"), auth)
    endpoint_synthesis_conflict_logs.__signature__ = _signature(
        _signature_query("capital_pool_id"), _signature_query("scope_ref"), _signature_query("proposal_id"),
        _signature_query("sponsor_persona_id"), _signature_query("synthesis_method"), _signature_query("committee_ref"),
        _signature_query("page_token"), _signature_query("page_size", annotation=int, default=20, ge=1, le=200), auth,
    )
    endpoint_synthesis_conflict_log.__signature__ = _signature(_path("log_id"), auth)
    endpoint_bff_search.__signature__ = _signature(
        _signature_query("q", annotation=str, default=""), _signature_query("types"),
        _signature_query("page_size", annotation=int, default=20, ge=1, le=100),
        _signature_query("limit", annotation=Optional[int], default=None, ge=1, le=100),
        _signature_query("page_token"), auth,
    )

    router.add_api_route("/api/v1/workbench/knowledge", endpoint_knowledge_workbench, methods=["GET"], name="knowledge_workbench")
    router.add_api_route("/api/v1/knowledge/notes", endpoint_create_note, methods=["POST"], name="create_note", status_code=201)
    router.add_api_route("/api/v1/knowledge/notes", endpoint_list_notes, methods=["GET"], name="list_notes")
    router.add_api_route("/api/v1/knowledge/notes/{note_id}", endpoint_get_note, methods=["GET"], name="get_note")
    router.add_api_route("/api/v1/knowledge/evidence", endpoint_list_evidence, methods=["GET"], name="list_evidence")
    router.add_api_route("/api/v1/knowledge/evidence/{ref_id}", endpoint_get_evidence, methods=["GET"], name="get_evidence")
    router.add_api_route("/api/v1/knowledge/insights", endpoint_list_insights, methods=["GET"], name="list_insights")
    router.add_api_route("/api/v1/knowledge/insights/{insight_id}", endpoint_get_insight, methods=["GET"], name="get_insight")
    router.add_api_route("/api/v1/knowledge/strategy-specs", endpoint_list_strategy_specs, methods=["GET"], name="list_strategy_specs")
    router.add_api_route("/api/v1/knowledge/strategy-specs/{strategy_id}/versions", endpoint_strategy_versions, methods=["GET"], name="strategy_versions")
    router.add_api_route("/api/v1/knowledge/strategy-specs/{strategy_id}/compare", endpoint_strategy_compare, methods=["GET"], name="strategy_compare")
    router.add_api_route("/api/v1/knowledge/strategy-specs/{strategy_id}", endpoint_get_strategy_spec, methods=["GET"], name="get_strategy_spec")
    router.add_api_route("/api/v1/knowledge/memory", endpoint_list_memory, methods=["GET"], name="list_memory")
    router.add_api_route("/api/v1/knowledge/memory/{entry_id}", endpoint_get_memory, methods=["GET"], name="get_memory")
    router.add_api_route("/bff/synthesis/conflict-logs", endpoint_synthesis_conflict_logs, methods=["GET"], name="synthesis_conflict_logs")
    router.add_api_route("/bff/synthesis/conflict-logs/{log_id}", endpoint_synthesis_conflict_log, methods=["GET"], name="synthesis_conflict_log")
    router.add_api_route("/bff/search", endpoint_bff_search, methods=["GET"], name="bff_search")

    return router
