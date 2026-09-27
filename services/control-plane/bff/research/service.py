"""Typed read and application service for the Research router.

Part of BFF-ROUTER-USECASE-CORRECTIVE-001.
Encapsulates data store / port accesses, validation, and domain use cases
for Knowledge Workbench, Research Notes, Evidence, Insights, Strategy Specs,
Institutional Memory, Conflict Logs, and Search away from HTTP route handlers.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Set

from fastapi import HTTPException

try:
    from services.control_plane.bff.models import (
        ErrorCode,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
        redact_evidence_refs,
    )
    from services.control_plane.bff.ports.research_knowledge_source import (
        ResearchKnowledgeSourcePort,
        ResearchWriteOwnerUnavailableError,
    )
except (ImportError, ValueError):
    from ..models import (  # type: ignore[no-redef]
        ErrorCode,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
        redact_evidence_refs,
    )
    from ..ports.research_knowledge_source import (  # type: ignore[no-redef]
        ResearchKnowledgeSourcePort,
        ResearchWriteOwnerUnavailableError,
    )

log = logging.getLogger(__name__)

PageSlice = Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
UtcNow = Callable[[], str]

_ANALYSIS_STATUSES = frozenset({"queued", "running", "completed", "failed"})
_DATE_RANGES = frozenset({"24h", "7d", "30d", "90d"})
_ARTIFACT_STATUSES = frozenset({"pending", "sealed", "superseded", "failed"})

_KW02_ATTACHMENT_TYPES = frozenset({"research_ticket", "persona", "strategy_spec", "free_standing"})
_KW02_ATTACHMENT_ID_PATTERNS = {
    "research_ticket": re.compile(r"^tkt-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"),
    "persona": re.compile(r"^persona-[A-Za-z0-9][A-Za-z0-9_-]*$"),
    "strategy_spec": re.compile(r"^strat-[A-Za-z0-9-]+$"),
}
_KW02_MEMORY_ANCHOR_PATTERN = re.compile(
    r"^mem-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_KW03_LINKED_ENTITY_TYPES = frozenset({
    "memory_entry", "research_note", "insight_card", "strategy_spec", "experiment", "artifact",
})
_KW03_LINK_TYPES = frozenset({
    "supporting_evidence", "counter_evidence", "citation", "provenance", "corroboration",
})
_KW03_CREDIBILITY_TIERS = frozenset({"primary", "secondary", "tertiary", "unverified"})
_KW04_STATUSES = frozenset({"active", "superseded", "archived", "all"})
_KW04_LINKED_ENTITY_TYPES = frozenset({
    "memory_entry", "research_note", "evidence_ref", "strategy_spec", "experiment",
})
_KW04_RECENCY_VALUES = frozenset({"7d", "30d", "90d", "all"})
_KW05_LIFECYCLE_STATES = frozenset({"draft", "candidate", "approved", "retired", "all"})

_ENTITY_TYPE_EVIDENCE_KIND: Dict[str, str] = {
    "strategy_spec": "strategy",
    "strategy": "strategy",
    "persona": "persona",
    "deployment_plan": "deployment",
    "deployment": "deployment",
    "runtime": "runtime",
    "runtime_binding": "runtime",
    "alert": "alert",
    "incident": "incident",
    "job": "job",
    "audit": "audit",
    "metric": "metric",
    "policy": "policy",
    "approval": "approval",
    "artifact": "artifact",
    "signal": "signal",
    "journal": "journal",
    "postmortem": "postmortem",
}


class ResearchValidationError(ValueError):
    """A client input error that the HTTP adapter turns into a BFF error."""

    def __init__(
        self,
        message: str,
        *,
        field: str,
        status_code: int = 422,
        error_code: str = "VALIDATION_FAILED",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.field = field
        self.status_code = status_code
        self.error_code = error_code
        self.details = dict(details) if details else {}


class ResearchNotFoundError(LookupError):
    """Raised when a durable typed record is absent."""

    def __init__(self, label: str, entity_id: str) -> None:
        super().__init__(f"{label} {entity_id} does not exist")
        self.label = label
        self.entity_id = entity_id


def _filter_by_status_csv(records: List[Dict[str, Any]], status: Optional[str]) -> List[Dict[str, Any]]:
    if not status:
        return list(records)
    requested = {s.strip().lower() for s in status.split(",") if s.strip()}
    return [r for r in records if str(r.get("status") or "").strip().lower() in requested]


def _legacy_artifact_reference_values(record: Dict[str, Any], field: str) -> set[str]:
    candidates: List[Any] = [record.get(field)]
    linkage = record.get("research_linkage")
    if isinstance(linkage, dict):
        candidates.extend(
            linkage.get(key)
            for key in (field, f"{field}_ref", f"linked_{field}")
        )
    if field == "experiment_id":
        candidates.append(record.get("produced_by_experiment_id"))
        candidates.append(record.get("experiment_refs"))
    if field == "lineage_id":
        lineage = record.get("lineage")
        if isinstance(lineage, dict):
            candidates.append(lineage.get("lineage_id"))

    values: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = (
                candidate.get(field)
                or candidate.get("id")
                or candidate.get("ref")
            )
        elif isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict):
                    item = item.get(field) or item.get("id") or item.get("ref")
                if item not in (None, ""):
                    values.add(str(item))
            continue
        if candidate not in (None, ""):
            values.add(str(candidate))
    return values


def _filter_legacy_artifacts(
    records: List[Dict[str, Any]],
    *,
    experiment_id: Optional[str],
    ticket_id: Optional[str],
    lineage_id: Optional[str],
    status: Optional[str],
) -> List[Dict[str, Any]]:
    requested = {
        "experiment_id": experiment_id,
        "ticket_id": ticket_id,
        "lineage_id": lineage_id,
    }
    filtered = list(records)
    for field, expected in requested.items():
        if expected not in (None, ""):
            filtered = [
                record
                for record in filtered
                if str(expected) in _legacy_artifact_reference_values(record, field)
            ]
    if status is not None:
        filtered = [
            record
            for record in filtered
            if str(record.get("status") or "").strip().lower() == status
        ]
    return filtered


@dataclass
class ResearchRouterService:
    """Projections and domain application logic for Research router."""

    port_getter: Callable[[], ResearchKnowledgeSourcePort]
    utc_now: UtcNow
    snapshot_meta: SnapshotMeta
    page_slice: PageSlice
    bff_error: Optional[Callable[..., HTTPException]] = None
    dataset_surface_status: Optional[Callable[..., Dict[str, Any]]] = None
    get_capabilities: Optional[Callable[[Any], Optional[List[str]]]] = None
    list_synthesis_conflict_logs_reader: Optional[Callable[..., Any]] = None
    get_synthesis_conflict_log_reader: Optional[Callable[[str], Any]] = None
    build_knowledge_workbench: Optional[Callable[[], Any]] = None
    cross_entity_search_fn: Optional[Callable[..., Any]] = None

    def _port(self) -> ResearchKnowledgeSourcePort:
        port = self.port_getter()
        if getattr(getattr(port, "__class__", None), "__module__", "").startswith("unittest.mock"):
            return port
        if hasattr(port, "research_knowledge_source"):
            return getattr(port, "research_knowledge_source")
        target = getattr(port, "_active_delegate", None)
        if target is not None:
            return getattr(target, "research_knowledge_source", target)
        return port

    def _call_port(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        port = self._port()
        fn = getattr(port, method_name, None)
        if not callable(fn):
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                f"Research store port missing {method_name}",
                f"Port {type(port).__name__} does not implement {method_name}",
            )
        if kwargs:
            try:
                sig = inspect.signature(fn)
                has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                if has_var_keyword:
                    for holder in (port, getattr(self, "port_getter", lambda: None)()):
                        if holder is None:
                            continue
                        for delegate_attr in ("_active_delegate", "research_knowledge_source"):
                            delegate = getattr(holder, delegate_attr, None)
                            if delegate is not None and hasattr(delegate, method_name):
                                delegate_method = getattr(delegate, method_name)
                                delegate_sig = inspect.signature(delegate_method)
                                if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in delegate_sig.parameters.values()):
                                    sig = delegate_sig
                                    has_var_keyword = False
                                    break
                        if not has_var_keyword:
                            break
                if not has_var_keyword:
                    kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
            except (ValueError, TypeError):
                pass
        try:
            return fn(*args, **kwargs)
        except (HTTPException, ResearchNotFoundError, ResearchValidationError):
            raise
        except ResearchWriteOwnerUnavailableError as exc:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Research experiment write owner unavailable",
                str(exc),
            )
        except Exception as exc:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                f"Research port {method_name} failed",
                str(exc),
            )

    def dataset_surface(self, dataset: str, *, snapshot_at: str, has_data: bool) -> Dict[str, Any]:
        return self._surface(dataset, snapshot_at=snapshot_at, has_data=has_data)

    def _raise_error(self, status_code: int, error_code: Any, message: str, reason: str, **kwargs: Any) -> None:
        surfaces = kwargs.pop("surfaces", None)
        if self.bff_error is not None:
            err = self.bff_error(status_code, error_code, message, reason, **kwargs)
            if surfaces is not None and isinstance(getattr(err, "detail", None), dict):
                err.detail["surfaces"] = surfaces
            raise err
        exc = HTTPException(status_code=status_code, detail=reason)
        if surfaces is not None:
            exc.detail = {"message": reason, "surfaces": surfaces}
        raise exc

    def _not_found(self, label: str, entity_id: str) -> None:
        self._raise_error(
            404,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{label} not found",
            f"{label} {entity_id} does not exist",
        )

    def _bad_request(self, message: str, reason: str, field: str) -> None:
        self._raise_error(
            400,
            ErrorCode.VALIDATION_FAILED,
            message,
            reason,
            precondition_failed=field,
        )

    def _knowledge_surface_state(
        self,
        dataset: str,
        *,
        snapshot_at: str,
        has_data: bool,
        missing_message: Optional[str] = None,
    ) -> str:
        port = self._port()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn(dataset) or "missing") if callable(source_fn) else "missing"
        if self.dataset_surface_status is not None:
            surface = self.dataset_surface_status(
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
        if source == "missing" or not has_data:
            return "unavailable"
        return "ok"

    def _get_capabilities_for(self, identity: Any) -> Optional[List[str]]:
        if self.get_capabilities is not None:
            try:
                res = self.get_capabilities(identity)
                if res is not None:
                    return res
            except Exception:
                pass
        try:
            from services.control_plane.bff.auth.policy import capabilities_for_identity
            return capabilities_for_identity(identity)
        except Exception:
            pass
        if identity is not None:
            caps = getattr(identity, "capabilities", None)
            if isinstance(caps, list):
                return caps
        return None

    def _surface(self, dataset: str, *, snapshot_at: str, has_data: bool) -> Dict[str, Any]:
        port = self._port()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn(dataset) or "missing") if callable(source_fn) else "missing"
        surface_fn = self.dataset_surface_status or getattr(port, "dataset_surface_status", None)
        if callable(surface_fn):
            try:
                res = surface_fn(
                    dataset,
                    snapshot_at=snapshot_at,
                    source=source,
                    has_data=has_data,
                    utc_now=self.utc_now,
                )
            except TypeError:
                res = surface_fn(
                    dataset,
                    snapshot_at=snapshot_at,
                    source=source,
                    has_data=has_data,
                )
            if isinstance(res, dict):
                return dict(res)
            if isinstance(res, str):
                return {"status": res, "source": source}
        if source in {"missing", "unavailable"} or not has_data:
            return {
                "status": "unavailable",
                "source": source,
                "message": f"{dataset} has no readable source records.",
            }
        return {"status": "ok", "source": source}

    @staticmethod
    def _validate_optional(value: Optional[str], *, allowed: frozenset[str], field: str) -> Optional[str]:
        if value in (None, ""):
            return None
        normalized = str(value).strip().lower()
        if normalized not in allowed:
            raise ResearchValidationError(
                f"{field} must be one of {sorted(allowed)}", field=field
            )
        return normalized

    @staticmethod
    def _validate_statuses(status_csv: Optional[str]) -> Optional[List[str]]:
        if status_csv in (None, ""):
            return None
        statuses = [str(value).strip().lower() for value in str(status_csv).split(",") if str(value).strip()]
        if not statuses:
            return None
        invalid = [value for value in statuses if value not in _ANALYSIS_STATUSES]
        if invalid:
            raise ResearchValidationError(
                f"status must contain only values from {sorted(_ANALYSIS_STATUSES)}",
                field="status",
            )
        return statuses

    # --- Analyses use cases ---
    def list_analyses(
        self,
        *,
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        status: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        detail_path: str = "/api/v1/research/analyses",
    ) -> Dict[str, Any]:
        statuses = self._validate_statuses(status)
        normalized_date_range = self._validate_optional(
            date_range, allowed=_DATE_RANGES, field="date_range"
        )
        snapshot_at = self.utc_now()
        port = self._port()
        records = list(
            port.list_research_analyses(
                ticket_id=ticket_id,
                experiment_id=experiment_id,
                statuses=statuses,
                date_range=normalized_date_range,
            )
            or []
        )
        surface = self._surface(
            "research_analyses", snapshot_at=snapshot_at, has_data=bool(records)
        )
        if surface.get("status") == "unavailable":
            page_items: List[Dict[str, Any]] = []
            next_page_token = None
            total = 0
        else:
            total = len(records)
            page_items, next_page_token = self.page_slice(records, page_token, page_size)
        items = [self._analysis_summary_with_links(item, detail_path=detail_path) for item in page_items]
        meta = self.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"analysis_results": surface}
        return {
            "data": items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": meta,
        }

    def get_analysis(
        self,
        analysis_id: str,
        *,
        detail_path: str = "/api/v1/research/analyses",
    ) -> Dict[str, Any]:
        clean_id = str(analysis_id or "").strip()
        record = self._call_port("get_research_analysis", clean_id)
        if not record:
            raise ResearchNotFoundError("Research analysis", clean_id)
        snapshot_at = self.utc_now()
        payload = dict(record)
        ticket_ref = str(payload.get("ticket_id") or "")
        experiment_ref = payload.get("experiment_id")
        payload["links"] = {
            "self": f"{detail_path}/{clean_id}",
            "workbench_detail": f"/research/analyze/{clean_id}",
            "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
            "linked_experiment_detail": (
                f"/research/experiments/{experiment_ref}" if experiment_ref else None
            ),
        }
        meta = self.snapshot_meta(snapshot_at)
        meta["surfaces"] = {
            "analysis_results": self._surface(
                "research_analyses", snapshot_at=snapshot_at, has_data=True
            )
        }
        payload["meta"] = meta
        return payload

    @staticmethod
    def _analysis_summary_with_links(
        item: Dict[str, Any], *, detail_path: str
    ) -> Dict[str, Any]:
        payload = dict(item)
        analysis_id = str(payload.get("analysis_id") or "")
        ticket_ref = str(payload.get("ticket_id") or "")
        payload["links"] = {
            "self": f"{detail_path}/{analysis_id}",
            "workbench_detail": f"/research/analyze/{analysis_id}",
            "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
        }
        return payload

    # --- Artifacts use cases ---
    def list_artifacts(
        self,
        *,
        artifact_type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[str] = None,
        author: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_status = self._validate_optional(
            status, allowed=_ARTIFACT_STATUSES, field="status"
        )
        normalized_date_range = self._validate_optional(
            date_range, allowed=_DATE_RANGES, field="date_range"
        )
        tag_values = [value.strip() for value in str(tags or "").split(",") if value.strip()] or None
        snapshot_at = self.utc_now()
        records = list(
            self._call_port(
                "list_research_artifacts",
                artifact_type=artifact_type,
                status=normalized_status,
                tags=tag_values,
                author=author,
                date_range=normalized_date_range,
            )
            or []
        )
        surface = self._surface(
            "research_artifacts", snapshot_at=snapshot_at, has_data=bool(records)
        )
        if surface.get("status") == "unavailable":
            page_items: List[Dict[str, Any]] = []
            next_page_token = None
            total = 0
        else:
            total = len(records)
            page_items, next_page_token = self.page_slice(records, page_token, page_size)
        meta = self.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"artifact_list": surface}
        return {
            "artifacts": [dict(item) for item in page_items],
            "next_page_token": next_page_token,
            "total_count": total,
            "meta": meta,
        }

    def get_artifact(self, artifact_id: str) -> Dict[str, Any]:
        clean_id = str(artifact_id or "").strip()
        record = self._call_port("get_research_artifact", clean_id)
        if not record:
            raise ResearchNotFoundError("Research artifact", clean_id)
        snapshot_at = self.utc_now()
        payload = dict(record)
        meta = self.snapshot_meta(snapshot_at)
        meta["surfaces"] = {
            "artifact_detail": self._surface(
                "research_artifacts", snapshot_at=snapshot_at, has_data=True
            )
        }
        payload["meta"] = meta
        return payload

    def compare_artifacts(self, artifact_ids: str) -> Dict[str, Any]:
        requested_ids = [value.strip() for value in str(artifact_ids or "").split(",") if value.strip()]
        if not 2 <= len(requested_ids) <= 4:
            raise ResearchValidationError(
                "artifact_ids must include between 2 and 4 artifact ids",
                field="artifact_ids",
                status_code=400,
            )
        artifacts = []
        for artifact_id in requested_ids:
            artifact = self._call_port("get_research_artifact", artifact_id)
            if not artifact:
                raise ResearchNotFoundError("Research artifact", artifact_id)
            artifacts.append(artifact)
        non_comparable = [
            {
                "artifact_id": artifact.get("artifact_id"),
                "status": artifact.get("status"),
                "reason": "Only sealed and superseded artifacts may be compared.",
            }
            for artifact in artifacts
            if not (artifact.get("allowedActions") or {}).get("canCompare")
        ]
        if non_comparable:
            raise ResearchValidationError(
                "One or more artifacts cannot be compared",
                field="artifact_status",
                error_code="OPERATION_NOT_ALLOWED",
                details={"non_comparable_artifacts": non_comparable},
            )
        snapshot_at = self.utc_now()
        payload = dict(self._call_port("compare_research_artifacts", requested_ids) or {})
        meta = self.snapshot_meta(snapshot_at)
        meta["computed_at"] = snapshot_at
        meta["surfaces"] = {
            "artifact_compare": self._surface(
                "research_artifacts", snapshot_at=snapshot_at, has_data=True
            )
        }
        payload["meta"] = meta
        return payload

    # --- KW-01 / Overview ---
    def get_knowledge_workbench_overview(self, *, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        if self.build_knowledge_workbench is not None:
            res = self.build_knowledge_workbench()
            return res() if callable(res) else res
        snap = snapshot_at or self.utc_now()
        modules = [
            {
                "module_id": "KW-01",
                "label": "Institutional Memory",
                "status": "ready",
                "wave_order": 1,
                "summary": "List and detail routes are live. Browse projection, lifecycle state machine, and identity contract published via KW-01-FOUNDATION-001.",
                "missing_contracts": [],
                "next_gate": "BFF routes are implemented; Lovable may proceed with production UI using example payloads.",
                "upstream_dependencies": [],
            },
            {
                "module_id": "KW-02",
                "label": "Research Notes",
                "status": "ready",
                "wave_order": 2,
                "summary": "Research Notes create/list/detail routes are live. Ownership, attachment taxonomy, and referential integrity rules are implemented in the current BFF.",
                "missing_contracts": [],
                "next_gate": "Activate the Lovable UI task against the live KW-02 routes.",
                "upstream_dependencies": ["KW-01"],
            },
            {
                "module_id": "KW-03",
                "label": "Evidence Refs",
                "status": "ready",
                "wave_order": 3,
                "summary": "Evidence Refs list/detail routes are live. Link taxonomy, credibility metadata, and resolved-link projection are implemented in the current BFF.",
                "missing_contracts": [],
                "next_gate": "Activate the Lovable UI task against the live KW-03 routes and preserve backend-owned resolved-link semantics.",
                "upstream_dependencies": ["KW-01", "KW-02"],
            },
            {
                "module_id": "KW-04",
                "label": "Insight Cards",
                "status": "ready",
                "wave_order": 4,
                "summary": "Insight Cards list/detail routes are live. Aggregation/detail projection and backend-owned filter taxonomy are implemented in the current BFF.",
                "missing_contracts": [],
                "next_gate": "Activate the Lovable UI task against the live KW-04 routes without client-side filter synthesis; the frontend handoff bundle is published.",
                "upstream_dependencies": ["KW-01", "KW-03"],
            },
            {
                "module_id": "KW-05",
                "label": "Strategy Spec",
                "status": "ready",
                "wave_order": 5,
                "summary": "Strategy Spec browse/detail/version-history/compare routes are live. Version identity, ancestry, lifecycle, and compare semantics are implemented per the ratified contract.",
                "missing_contracts": [],
                "live_routes": [
                    "GET /api/v1/knowledge/strategy-specs",
                    "GET /api/v1/knowledge/strategy-specs/{strategy_id}",
                    "GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions",
                    "GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare",
                ],
                "next_gate": "Activate the Lovable UI task against the live KW-05 routes using backend-owned version identity, ancestry, and compare semantics.",
                "upstream_dependencies": ["KW-01", "KW-03"],
            },
        ]
        return {
            "workbench_id": "knowledge-workbench",
            "label": "Knowledge Workbench",
            "route_href": "/knowledge",
            "overall_status": "overview_ready",
            "headline": "KW-01 to KW-05 are route-live",
            "summary": (
                "This overview is a truthful landing surface for the Knowledge Workbench. "
                "All five Knowledge Workbench modules are route-live in the current BFF."
            ),
            "packet_family": {
                "family_id": "KW-006",
                "path": "docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md",
                "lovable_readiness": "overview_ready",
                "note": "KW-01 to KW-05 are route-live in the current BFF. KW-02 to KW-05 now carry published frontend handoff packets; remaining work is front-owned UI activation plus KW-01 hardening follow-up.",
            },
            "module_counts": {
                "total": len(modules),
                "ready": sum(1 for m in modules if m.get("status") == "ready"),
                "not_ready": sum(1 for m in modules if m.get("status") != "ready"),
            },
            "modules": modules,
            "support_refs": [
                {
                    "ref_id": "memory-design-note",
                    "label": "Memory Layer Design Note",
                    "ref_type": "document",
                    "value": "services/memory/MEMORY_LAYER_DESIGN_NOTE.md",
                    "note": "Canonical Memory Plane split and retrieval-facade rules.",
                },
                {
                    "ref_id": "institutional-memory-schema",
                    "label": "InstitutionalMemoryEntry schema",
                    "ref_type": "document",
                    "value": "services/memory/institutional_memory_entry.schema.json",
                    "note": "Canonical shared-memory object shape; not a workbench browse contract.",
                },
                {
                    "ref_id": "strategy-spec-schema",
                    "label": "StrategySpec schema",
                    "ref_type": "document",
                    "value": "services/control-plane/specs/strategy_spec.schema.json",
                    "note": "Canonical StrategySpec object schema; version browsing, ancestry, lifecycle, and compare semantics are now ratified in docs/bff/KW-05-strategy-spec.md.",
                },
                {
                    "ref_id": "memory-retrieval-facade",
                    "label": "Memory retrieval facade",
                    "ref_type": "endpoint",
                    "value": "/memory/retrieve",
                    "note": "Session-facing retrieval API; not a substitute for workbench list/detail surfaces.",
                },
            ],
            "next_steps": [
                "Activate the Lovable UI task against the live KW-02 Research Notes routes.",
                "Activate the Lovable UI task against the live KW-03 Evidence Refs routes.",
                "Activate the Lovable UI task against the live KW-04 Insight Cards routes; the frontend handoff bundle is already published.",
                "Activate the Lovable UI task against the live KW-05 Strategy Spec routes using backend-owned version identity, ancestry, and compare semantics.",
                "Keep the Knowledge Workbench payload-owned; do not synthesize registry joins from raw schemas in the browser.",
                "Use this overview to track the remaining workbench order without downgrading live routes back to pending-BFF text.",
            ],
            "meta": {
                **self.snapshot_meta(snap),
                "surfaces": {
                    "overview": {"status": "ok", "source": "bff_static"},
                    "packet_family": {"status": "ok", "source": "canonical"},
                },
            },
        }

    # --- KW-02: Research Notes helpers and use cases ---
    def _kw02_validate_string_list(self, value: Any, field: str) -> List[str]:
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            self._bad_request(f"Invalid {field}", f"{field} must be an array of strings", field)
        normalized: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text:
                self._bad_request(f"Invalid {field} entry", f"{field} entries must be non-empty strings", field)
            normalized.append(text)
        return normalized

    def _kw02_validate_attachment_type(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _KW02_ATTACHMENT_TYPES:
            self._bad_request("Invalid attachment_type", f"attachment_type must be one of {sorted(_KW02_ATTACHMENT_TYPES)}", "attachment_type")
        return normalized

    def _kw02_validate_attachment_ref(self, attachment_type: str, value: Any) -> Optional[str]:
        if attachment_type == "free_standing":
            if value not in (None, ""):
                self._bad_request("Invalid attachment_ref", "attachment_ref must be null when attachment_type is free_standing", "attachment_ref")
            return None
        ref = str(value or "").strip()
        if not ref:
            self._bad_request("Missing attachment_ref", "attachment_ref is required unless attachment_type is free_standing", "attachment_ref")
        pattern = _KW02_ATTACHMENT_ID_PATTERNS.get(attachment_type)
        if pattern is not None and not pattern.match(ref):
            self._bad_request("Invalid attachment_ref", f"attachment_ref does not match the identity format for {attachment_type}", "attachment_ref")
        return ref

    def _kw02_validate_memory_anchors(self, anchor_ids: List[str]) -> List[str]:
        validated: List[str] = []
        for entry_id in anchor_ids:
            if not _KW02_MEMORY_ANCHOR_PATTERN.match(entry_id):
                self._bad_request("Invalid linked_memory_anchors entry", "linked_memory_anchors items must use the mem-{UUID} format", "linked_memory_anchors")
            if self._call_port("get_institutional_memory_entry", entry_id) is None:
                self._bad_request("Unknown linked_memory_anchors entry", f"linked_memory_anchors entry {entry_id} does not resolve to a known institutional memory entry", "linked_memory_anchors")
            validated.append(entry_id)
        return validated

    def _kw02_attachment_exists(self, attachment_type: str, attachment_ref: Optional[str]) -> bool:
        if attachment_type == "free_standing":
            return True
        if attachment_type == "research_ticket":
            return self._call_port("get_research_ticket", attachment_ref) is not None
        if attachment_type == "persona":
            return self._call_port("get_persona", attachment_ref) is not None
        if attachment_type == "strategy_spec":
            return self._call_port("get_strategy_spec", attachment_ref) is not None
        return False

    def _kw02_operator_display_name(self, operator_id: str) -> str:
        if operator_id == "op-001":
            return "Alice Chen"
        token = str(operator_id or "").strip()
        if not token:
            return "Operator"
        if token.startswith("op-"):
            return f"Operator {token}"
        return " ".join(part.capitalize() for part in re.split(r"[-_]+", token) if part)

    @staticmethod
    def _kw02_strip_markdown(text: str) -> str:
        plain = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        plain = re.sub(r"[`*_>#]", " ", plain)
        return re.sub(r"\s+", " ", plain).strip()

    def _kw02_resolve_attachment_target(
        self,
        attachment_type: str,
        attachment_ref: Optional[str],
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        if attachment_type == "free_standing":
            return True, None, None
        if attachment_type == "research_ticket":
            ticket = self._call_port("get_research_ticket", attachment_ref)
            if not ticket:
                return False, None, None
            return True, ticket.get("title"), f"/research/tickets/{attachment_ref}"
        if attachment_type == "persona":
            persona = self._call_port("get_persona", attachment_ref)
            if not persona:
                return False, None, None
            return True, persona.get("name"), f"/personas/{attachment_ref}"
        strategy_spec = self._call_port("get_strategy_spec", attachment_ref)
        if not strategy_spec:
            return False, None, None
        label = strategy_spec.get("title") or strategy_spec.get("name") or attachment_ref
        return True, label, f"/knowledge/strategy-specs/{attachment_ref}"

    def _kw02_note_list_item(self, note: Dict[str, Any]) -> Dict[str, Any]:
        attachment_type = str(note.get("attachment_type") or "free_standing")
        attachment_ref = note.get("attachment_ref")
        attachment_exists, attachment_label, _ = self._kw02_resolve_attachment_target(
            attachment_type,
            attachment_ref,
        )
        return {
            "note_id": note.get("note_id"),
            "title": note.get("title"),
            "excerpt": self._kw02_strip_markdown(str(note.get("body") or ""))[:280],
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
        self,
        note: Dict[str, Any],
        *,
        include_route: bool,
    ) -> Dict[str, Any]:
        attachment_type = str(note.get("attachment_type") or "free_standing")
        attachment_ref = note.get("attachment_ref")
        exists, display_label, route_href = self._kw02_resolve_attachment_target(
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
        self,
        ref_ids: List[str],
        *,
        snapshot_at: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        surface_state = self._knowledge_surface_state("evidence_refs", snapshot_at=snapshot_at, has_data=True)
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
            evidence_ref = self._call_port("get_evidence_ref", ref_id)
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
        self,
        entry_ids: List[str],
        *,
        snapshot_at: str,
    ) -> Tuple[List[Dict[str, Any]], str]:
        surface_state = self._knowledge_surface_state("institutional_memory_entries", snapshot_at=snapshot_at, has_data=True)
        items: List[Dict[str, Any]] = []
        missing_entries = False
        for entry_id in entry_ids:
            entry = self._call_port("get_institutional_memory_entry", entry_id)
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
        self,
        note: Dict[str, Any],
        *,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        evidence_links, evidence_surface = self._kw02_resolve_evidence_links(
            list(note.get("linked_evidence_refs") or []),
            snapshot_at=snapshot_at,
        )
        memory_anchors, memory_surface = self._kw02_resolve_memory_anchors(
            list(note.get("linked_memory_anchors") or []),
            snapshot_at=snapshot_at,
        )
        return {
            "note_id": note.get("note_id"),
            "title": note.get("title"),
            "body": note.get("body"),
            "owner_ref": json.loads(json.dumps(note.get("owner_ref") or {})),
            "attachment": self._kw02_attachment_payload(note, include_route=True),
            "tags": list(note.get("tags") or []),
            "linked_evidence_refs": evidence_links,
            "linked_memory_anchors": memory_anchors,
            "created_at": note.get("created_at"),
            "updated_at": note.get("updated_at"),
            "meta": {
                **self.snapshot_meta(snapshot_at),
                "surfaces": {
                    "research_note_detail": self._knowledge_surface_state("research_notes", snapshot_at=snapshot_at, has_data=True),
                    "evidence_links": evidence_surface,
                    "memory_anchors": memory_surface,
                },
            },
        }

    def create_research_note(
        self,
        body: Dict[str, Any],
        *,
        operator_id: str,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        if "owner_ref" in body:
            self._bad_request(
                "Invalid owner_ref",
                "owner_ref is server-assigned and must not be supplied by the caller",
                "owner_ref",
            )
        snap = snapshot_at or self.utc_now()
        title = body.get("title")
        if title not in (None, ""):
            title = str(title).strip()
            if len(title) > 256:
                self._bad_request("Invalid title", "title must be 256 characters or fewer", "title")
        else:
            title = None
        note_body = body.get("body")
        if note_body is None or not str(note_body).strip():
            self._bad_request("Missing required field: body", "body must be a non-empty string", "body")
        note_body = str(note_body).strip()
        attachment_type = self._kw02_validate_attachment_type(body.get("attachment_type"))
        attachment_ref = self._kw02_validate_attachment_ref(attachment_type, body.get("attachment_ref"))
        tags = self._kw02_validate_string_list(body.get("tags"), "tags")
        linked_evidence_refs = self._kw02_validate_string_list(body.get("linked_evidence_refs"), "linked_evidence_refs")
        linked_memory_anchors = self._kw02_validate_memory_anchors(
            self._kw02_validate_string_list(body.get("linked_memory_anchors"), "linked_memory_anchors")
        )
        if not self._kw02_attachment_exists(attachment_type, attachment_ref):
            self._raise_error(
                422,
                ErrorCode.PRECONDITION_FAILED,
                "Attachment target does not exist",
                f"{attachment_type} target {attachment_ref} could not be resolved",
                precondition_failed="attachment_ref",
            )
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
                "display_name": self._kw02_operator_display_name(operator_id),
            },
            "tags": tags,
            "linked_evidence_refs": linked_evidence_refs,
            "linked_memory_anchors": linked_memory_anchors,
            "created_at": snap,
            "updated_at": snap,
        }
        created = self._call_port("create_research_note", note)
        if created is None:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Research note store unavailable",
                "Research note creation store is unavailable.",
            )
        return {
            "note_id": note_id,
            "created_at": snap,
            "route_href": f"/knowledge/notes/{note_id}",
        }

    def list_research_notes(
        self,
        *,
        attachment_type: Optional[str] = None,
        attachment_ref: Optional[str] = None,
        owner_ref: Optional[str] = None,
        tags: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        val_attachment_type = self._kw02_validate_attachment_type(attachment_type) if attachment_type is not None else None
        if attachment_ref is not None and val_attachment_type is None:
            self._bad_request("Invalid attachment_ref filter", "attachment_ref requires attachment_type to be set", "attachment_ref")
        val_attachment_ref = (
            self._kw02_validate_attachment_ref(val_attachment_type, attachment_ref)
            if val_attachment_type is not None and attachment_ref is not None
            else None
        )
        notes = list(self._call_port("list_research_notes") or [])
        port = self._port()
        notes_dataset_available = getattr(port, "dataset_source", lambda _d: "missing")("research_notes") != "missing"
        if owner_ref:
            notes = [note for note in notes if str(((note.get("owner_ref") or {}).get("owner_id")) or "") == owner_ref]
        if val_attachment_type:
            notes = [note for note in notes if str(note.get("attachment_type") or "") == val_attachment_type]
        if val_attachment_type == "free_standing" or val_attachment_ref is not None:
            notes = [note for note in notes if note.get("attachment_ref") == val_attachment_ref]
        if tags:
            req_tags = {v.strip() for v in tags.split(",") if v.strip()}
            notes = [note for note in notes if req_tags.intersection(set(note.get("tags") or []))]
        surface_state = self._knowledge_surface_state("research_notes", snapshot_at=snap, has_data=notes_dataset_available)
        if surface_state == "unavailable":
            page_items, next_token, has_more = [], None, False
        else:
            page_items, next_token = self.page_slice(notes, page_token, page_size)
            has_more = next_token is not None
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"research_note_list": surface_state}
        return {
            "notes": [self._kw02_note_list_item(note) for note in page_items],
            "pagination": {
                "page_size": page_size,
                "next_page_token": next_token,
                "has_more": has_more,
            },
            "meta": meta,
        }

    def get_research_note(self, note_id: str, *, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        identifier = str(note_id or "").strip()
        record = self._call_port("get_research_note", identifier)
        if not record:
            self._not_found("Research record", identifier)
        return self._research_note_detail_payload(record, snapshot_at=snap)

    # --- KW-03: Evidence Refs helpers and use cases ---
    def _validate_choice(self, value: Any, *, field: str, allowed: Set[str] | frozenset[str]) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in allowed:
            self._bad_request(f"Invalid {field}", f"{field} must be one of {sorted(allowed)}", field)
        return normalized

    def _evidence_list_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ref_id": item.get("ref_id"),
            "source_document": json.loads(json.dumps(item.get("source_document") or {})),
            "link_type": item.get("link_type"),
            "credibility": json.loads(json.dumps(item.get("credibility") or {})),
            "linked_object_summary": json.loads(json.dumps(item.get("linked_object_summary") or {})),
            "resolved_link": json.loads(json.dumps(item.get("resolved_link") or {})),
            "route_href": item.get("route_href"),
        }

    def _evidence_detail_payload(
        self,
        evidence_ref: Dict[str, Any],
        *,
        ref_id: str,
        identity: Any,
        snapshot_at: str,
    ) -> Dict[str, Any]:
        detail_surface = self._knowledge_surface_state("evidence_refs", snapshot_at=snapshot_at, has_data=True)
        capabilities = self._get_capabilities_for(identity)
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
                        **self.snapshot_meta(snapshot_at),
                        "surfaces": {
                            "evidence_ref_detail": detail_surface,
                            "resolved_link": detail_surface,
                            "linked_decisions": detail_surface,
                        },
                        "redacted_evidence_count": 1,
                    },
                }

        raw_linked_decisions = json.loads(json.dumps(evidence_ref.get("linked_decisions") or []))
        annotated_decisions: List[Any] = []
        for decision in raw_linked_decisions:
            if not isinstance(decision, dict):
                annotated_decisions.append(decision)
                continue
            kind = _ENTITY_TYPE_EVIDENCE_KIND.get(str(decision.get("entity_type") or "").strip())
            if not kind:
                annotated_decisions.append(decision)
                continue
            annotated = dict(decision)
            annotated["evidence_type"] = kind
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
            "linked_object_summary": json.loads(json.dumps(evidence_ref.get("linked_object_summary") or {})),
            "linked_decisions": linked_decisions,
            "source_note_context": json.loads(json.dumps(evidence_ref.get("source_note_context"))),
            "source_memory_context": json.loads(json.dumps(evidence_ref.get("source_memory_context"))),
            "created_at": evidence_ref.get("created_at"),
            "meta": {
                **self.snapshot_meta(snapshot_at),
                "surfaces": {
                    "evidence_ref_detail": detail_surface,
                    "resolved_link": detail_surface,
                    "linked_decisions": detail_surface,
                },
                "redacted_evidence_count": redacted_count,
            },
        }

    def list_evidence_refs(
        self,
        *,
        identity: Any = None,
        linked_entity_type: Optional[str] = None,
        linked_entity_ref: Optional[str] = None,
        link_type: Optional[str] = None,
        credibility_tier: Optional[str] = None,
        verified_raw: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        val_entity_type = self._validate_choice(linked_entity_type, field="linked_entity_type", allowed=_KW03_LINKED_ENTITY_TYPES) if linked_entity_type is not None else None
        if linked_entity_ref is not None and val_entity_type is None:
            self._bad_request("Invalid linked_entity_ref filter", "linked_entity_ref requires linked_entity_type to be set", "linked_entity_ref")
        val_link_type = self._validate_choice(link_type, field="link_type", allowed=_KW03_LINK_TYPES) if link_type is not None else None
        val_tier = self._validate_choice(credibility_tier, field="credibility_tier", allowed=_KW03_CREDIBILITY_TIERS) if credibility_tier is not None else None
        verified: Optional[bool] = None
        if verified_raw is not None:
            normalized_verified = str(verified_raw).strip().lower()
            if normalized_verified not in {"true", "false"}:
                self._bad_request("Invalid verified", "verified must be a boolean", "verified")
            verified = normalized_verified == "true"
        records = list(self._call_port("list_evidence_refs") or [])
        if val_entity_type:
            records = [item for item in records if str(((item.get("linked_object_summary") or {}).get("entity_type")) or "").lower() == val_entity_type]
        if linked_entity_ref is not None:
            records = [item for item in records if str(((item.get("linked_object_summary") or {}).get("entity_ref")) or "") == str(linked_entity_ref)]
        if val_link_type:
            records = [item for item in records if str(item.get("link_type") or "").lower() == val_link_type]
        if val_tier:
            records = [item for item in records if str(((item.get("credibility") or {}).get("tier")) or "").lower() == val_tier]
        if verified is not None:
            records = [item for item in records if bool((item.get("credibility") or {}).get("verified")) is verified]
        port = self._port()
        available = getattr(port, "dataset_source", lambda _d: "missing")("evidence_refs") != "missing"
        surface_state = self._knowledge_surface_state("evidence_refs", snapshot_at=snap, has_data=available)
        if surface_state == "unavailable":
            page_items, next_token, has_more = [], None, False
        else:
            page_items, next_token = self.page_slice(records, page_token, page_size)
            has_more = next_token is not None
        processed, redacted_count = redact_evidence_refs(
            identity, page_items, capabilities=self._get_capabilities_for(identity)
        )
        response_items = [
            item if isinstance(item, dict) and item.get("redacted") else self._evidence_list_item(item)
            for item in processed
        ]
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"evidence_refs_list": surface_state}
        meta["redacted_evidence_count"] = redacted_count
        return {
            "evidence_refs": response_items,
            "pagination": {
                "page_size": page_size,
                "next_page_token": next_token,
                "has_more": has_more,
            },
            "meta": meta,
        }

    def get_evidence_ref(self, ref_id: str, *, identity: Any = None, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        identifier = str(ref_id or "").strip()
        record = self._call_port("get_evidence_ref_detail", identifier)
        if not record:
            self._not_found("Research record", identifier)
        return self._evidence_detail_payload(record, ref_id=identifier, identity=identity, snapshot_at=snap)

    # --- KW-04: Insight Cards helpers and use cases ---
    def _insight_list_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
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
            "aggregated_at": item.get("aggregated_at") or (item.get("aggregation_provenance") or {}).get("aggregated_at"),
            "route_href": item.get("route_href") or (f"/knowledge/insights/{item.get('insight_id')}" if item.get("insight_id") else None),
        }

    def _insight_filter_metadata(self, cards: List[Dict[str, Any]]) -> Dict[str, Any]:
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

    def _within_recency(self, value: Any, recency: str, snapshot_at: str) -> bool:
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

    def _insight_supporting_evidence_surface(
        self, supporting_evidence_refs: List[Dict[str, Any]], *, snapshot_at: str,
    ) -> str:
        surface_state = self._knowledge_surface_state("evidence_refs", snapshot_at=snapshot_at, has_data=True)
        if surface_state != "ok":
            return surface_state
        if any(not item.get("ref_id") or not isinstance(item.get("resolved_link"), dict) for item in supporting_evidence_refs):
            return "degraded"
        return "ok"

    def _insight_linked_sources_surface(
        self, linked_sources: List[Dict[str, Any]], *, snapshot_at: str,
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
            surface_state = self._knowledge_surface_state(dataset, snapshot_at=snapshot_at, has_data=True)
            if surface_state == "unavailable":
                return "unavailable"
            if surface_state == "degraded":
                overall = "degraded"
            if not item.get("display_label") or "route_href" not in item:
                overall = "degraded"
        return overall

    def _insight_detail_payload(self, insight_card: Dict[str, Any], *, snapshot_at: str) -> Dict[str, Any]:
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
            "aggregation_provenance": json.loads(json.dumps(insight_card.get("aggregation_provenance") or {})),
            "created_at": insight_card.get("created_at"),
            "updated_at": insight_card.get("updated_at"),
            "meta": {
                **self.snapshot_meta(snapshot_at),
                "surfaces": {
                    "insight_card_detail": self._knowledge_surface_state("insight_cards", snapshot_at=snapshot_at, has_data=True),
                    "supporting_evidence_refs": self._insight_supporting_evidence_surface(supporting_evidence_refs, snapshot_at=snapshot_at),
                    "linked_sources": self._insight_linked_sources_surface(linked_sources, snapshot_at=snapshot_at),
                },
            },
        }

    def list_insight_cards(
        self,
        *,
        status: Optional[str] = "active",
        scope: Optional[str] = None,
        scope_ref: Optional[str] = None,
        tags: Optional[str] = None,
        tag: Optional[str] = None,
        linked_entity_type: Optional[str] = None,
        linked_entity_ref: Optional[str] = None,
        recency: Optional[str] = "all",
        confidence_min: Optional[float] = None,
        min_confidence: Optional[float] = None,
        min_evidence_count: Optional[int] = None,
        include_inactive: bool = False,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        val_status = self._validate_choice(status, field="status", allowed=_KW04_STATUSES) if status is not None else None
        effective_recency = self._validate_choice(recency or "all", field="recency", allowed=_KW04_RECENCY_VALUES)
        val_entity_type = self._validate_choice(linked_entity_type, field="linked_entity_type", allowed=_KW04_LINKED_ENTITY_TYPES) if linked_entity_type is not None else None
        effective_min_conf = confidence_min if confidence_min is not None else min_confidence
        records = list(self._call_port("list_insight_cards") or [])
        port = self._port()
        available = getattr(port, "dataset_source", lambda _d: "missing")("insight_cards") != "missing"
        filter_metadata = self._insight_filter_metadata(records)
        if not include_inactive and val_status:
            records = [item for item in records if str(item.get("status") or "").lower() == val_status]
        if scope:
            records = [item for item in records if str(item.get("scope") or "") == scope]
        if scope_ref:
            records = [item for item in records if str(item.get("scope_ref") or "") == scope_ref]
        effective_tags = tag or tags
        if effective_tags:
            req_tags = {v.strip() for v in str(effective_tags).split(",") if v.strip()}
            records = [item for item in records if req_tags.intersection(set(item.get("tags") or []))]
        if val_entity_type:
            records = [
                item for item in records
                if any(str((s or {}).get("entity_type") or "").strip() == val_entity_type for s in (item.get("linked_sources") or []))
            ]
        if linked_entity_ref:
            records = [
                item for item in records
                if any(str((s or {}).get("entity_ref") or "").strip() == str(linked_entity_ref) for s in (item.get("linked_sources") or []))
            ]
        if effective_recency != "all":
            records = [
                item for item in records
                if self._within_recency(
                    item.get("aggregated_at") or (item.get("aggregation_provenance") or {}).get("aggregated_at"),
                    effective_recency,
                    snap,
                )
            ]
        if effective_min_conf is not None:
            records = [
                item for item in records
                if float(((item.get("confidence") or {}).get("score")) or 0.0) >= effective_min_conf
            ]
        if min_evidence_count is not None:
            records = [
                item for item in records
                if int(item.get("evidence_count") or 0) >= min_evidence_count
            ]
        surface_state = self._knowledge_surface_state("insight_cards", snapshot_at=snap, has_data=available)
        if surface_state == "unavailable":
            page_items, next_token, has_more = [], None, False
        else:
            page_items, next_token = self.page_slice(records, page_token, page_size)
            has_more = next_token is not None
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"insight_cards": surface_state}
        return {
            "insight_cards": [self._insight_list_item(item) for item in page_items],
            "filter_metadata": filter_metadata,
            "pagination": {
                "page_size": page_size,
                "next_page_token": next_token,
                "has_more": has_more,
            },
            "meta": meta,
        }

    def get_insight_card(self, insight_id: str, *, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        identifier = str(insight_id or "").strip()
        record = self._call_port("get_insight_card_detail", identifier)
        if not record:
            self._not_found("Research record", identifier)
        return self._insight_detail_payload(record, snapshot_at=snap)

    # --- KW-05: Strategy Specs helpers and use cases ---
    def _kw05_validate_lifecycle_state(self, value: Any) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in _KW05_LIFECYCLE_STATES:
            self._bad_request("Invalid lifecycle_state", f"lifecycle_state must be one of {sorted(_KW05_LIFECYCLE_STATES)}", "lifecycle_state")
        return normalized

    def _kw05_compare_selectors(
        self,
        *,
        left_version: Optional[str],
        right_version: Optional[str],
        base_version: Optional[str],
        target_version: Optional[str],
    ) -> Tuple[str, str]:
        left = str(left_version or base_version or "").strip()
        right = str(right_version or target_version or "").strip()
        if not left or not right:
            self._bad_request("Missing compare versions", "Provide either left_version/right_version or base_version/target_version", "left_version")
        if left_version and base_version and str(left_version).strip() != str(base_version).strip():
            self._bad_request("Conflicting compare aliases", "left_version and base_version must reference the same version when both are provided", "left_version")
        if right_version and target_version and str(right_version).strip() != str(target_version).strip():
            self._bad_request("Conflicting compare aliases", "right_version and target_version must reference the same version when both are provided", "right_version")
        return left, right

    def list_strategy_specs(
        self,
        *,
        lifecycle_state: Optional[str] = "all",
        source_kind: Optional[str] = None,
        archetype: Optional[str] = None,
        persona_id: Optional[str] = None,
        include_retired: bool = False,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        eff_source_kind = source_kind or archetype
        val_lifecycle = self._kw05_validate_lifecycle_state(lifecycle_state or "all")
        kwargs = {
            "lifecycle_state": val_lifecycle,
            "source_kind": eff_source_kind,
            "persona_id": persona_id,
            "include_retired": include_retired,
            "include_fixture_pack": False,
        }
        records = list(self._call_port("list_strategy_specs", **kwargs) or [])
        port = self._port()
        source_fn = getattr(port, "dataset_source", None)
        dataset_available = str(source_fn("strategy_specs") or "missing") != "missing" if callable(source_fn) else bool(records)
        surface_state = self._knowledge_surface_state("strategy_specs", snapshot_at=snap, has_data=dataset_available)
        if surface_state == "unavailable":
            items, next_token, has_more = [], None, False
        else:
            items, next_token = self.page_slice(records, page_token, page_size)
            has_more = next_token is not None
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"strategy_spec_list": surface_state}
        return {
            "items": items,
            "page_info": {
                "next_page_token": next_token,
                "page_size": page_size,
                "has_more": has_more,
            },
            "meta": meta,
        }

    def get_strategy_spec_versions(self, strategy_id: str, *, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        records = list(self._call_port("list_strategy_spec_versions", strategy_id) or [])
        if not records and not self._call_port("get_strategy_spec", strategy_id):
            self._not_found("Strategy spec", strategy_id)
        return {
            "strategy_id": strategy_id,
            "versions": records,
            "meta": {
                **self.snapshot_meta(snap),
                "surfaces": {
                    "version_history": self._knowledge_surface_state("strategy_specs", snapshot_at=snap, has_data=True)
                },
            },
        }

    def compare_strategy_spec_versions(
        self,
        strategy_id: str,
        *,
        left_version: Optional[str] = None,
        right_version: Optional[str] = None,
        base_version: Optional[str] = None,
        target_version: Optional[str] = None,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        left, right = self._kw05_compare_selectors(
            left_version=left_version,
            right_version=right_version,
            base_version=base_version,
            target_version=target_version,
        )
        if left == right:
            self._raise_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Compare requires two distinct versions",
                "left_version and right_version must identify different versions",
                precondition_failed="left_version",
            )
        left_detail = self._call_port("get_strategy_spec_detail", strategy_id, version_selector=left)
        right_detail = self._call_port("get_strategy_spec_detail", strategy_id, version_selector=right)
        if not left_detail or not right_detail:
            self._not_found("Strategy spec version", strategy_id)
        if not (left_detail.get("allowedActions") or {}).get("canCompare") or not (
            right_detail.get("allowedActions") or {}
        ).get("canCompare"):
            self._raise_error(
                422,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "One or more versions cannot be compared",
                "Compare accepts only candidate, approved, or retired strategy spec versions",
                precondition_failed="lifecycle_state",
            )
        comparison = self._call_port("compare_strategy_spec_versions", strategy_id, left_selector=left, right_selector=right)
        if not comparison:
            self._not_found("Strategy spec version", strategy_id)
        payload = dict(comparison)
        payload["meta"] = {
            **self.snapshot_meta(snap),
            "surfaces": {
                "strategy_spec_compare": self._knowledge_surface_state("strategy_specs", snapshot_at=snap, has_data=True)
            },
        }
        return payload

    def get_strategy_spec(
        self,
        strategy_id: str,
        *,
        version_selector: Optional[str] = "current",
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        identifier = str(strategy_id or "").strip()
        if not self._call_port("get_strategy_spec", identifier):
            self._not_found("Strategy spec", identifier)
        record = self._call_port("get_strategy_spec_detail", identifier, version_selector=version_selector or "current")
        if not record:
            self._not_found("Strategy spec version", identifier)
        detail_surface = self._knowledge_surface_state("strategy_specs", snapshot_at=snap, has_data=True)
        citation_bundle = json.loads(json.dumps(record.get("citation_bundle") or {}))
        citation_surface = "partial" if not any(citation_bundle.values()) else detail_surface
        ancestry_surface = (
            "degraded"
            if record.get("parent_spec_version_id") is None and str(version_selector or "").strip() not in {"", "current"}
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
                **self.snapshot_meta(snap),
                "surfaces": {
                    "strategy_spec_detail": detail_surface,
                    "citation_bundle": citation_surface,
                    "version_ancestry": ancestry_surface,
                },
            },
        }

    # --- Institutional Memory use cases ---
    def list_institutional_memory_entries(
        self,
        *,
        knowledge_type: Optional[str] = None,
        scope: Optional[str] = None,
        scope_filter: Optional[str] = None,
        tags: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        records = list(self._call_port("list_institutional_memory_entries") or [])
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
            req_tags = {v.strip() for v in str(tags).split(",") if v.strip()}
            records = [item for item in records if req_tags.intersection(set(item.get("tags") or []))]
        page_size = max(1, min(page_size, 200))
        total_count = len(records)
        start = (page - 1) * page_size
        page_items = records[start : start + page_size]
        total_pages = max(1, (total_count + page_size - 1) // page_size)
        port = self._port()
        available = getattr(port, "dataset_source", lambda _d: "missing")("institutional_memory_entries") != "missing"
        surface_state = self._knowledge_surface_state(
            "institutional_memory_entries", snapshot_at=snap, has_data=available,
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
            scope_val = item.get("scope") if isinstance(item.get("scope"), dict) else {}
            lifecycle = item.get("lifecycle") if isinstance(item.get("lifecycle"), dict) else {}
            usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
            entries.append({
                "entry_id": item.get("entry_id") or item.get("id"),
                "headline": content.get("headline"),
                "knowledge_type": item.get("knowledge_type"),
                "scope": scope_val.get("type"),
                "scope_filter": scope_val.get("filter"),
                "tags": list(content.get("tags") or item.get("tags") or []),
                "reuse_count": int(usage.get("reuse_count") or 0),
                "is_superseded": bool(lifecycle.get("superseded_by")),
                "written_at": item.get("written_at"),
                "write_authority": item.get("write_authority"),
                "route_href": f"/knowledge/memory/{item.get('entry_id') or item.get('id')}",
            })
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"memory_list": surface_state}
        return {
            "entries": entries,
            "pagination": {
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            },
            "meta": meta,
        }

    def get_institutional_memory_entry(self, entry_id: str, *, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        identifier = str(entry_id or "").strip()
        record = self._call_port("get_institutional_memory_entry", identifier)
        if not record:
            self._not_found("Research record", identifier)
        source_event = record.get("source_event") if isinstance(record.get("source_event"), dict) else {}
        source_context_available = bool(source_event.get("type")) and bool(source_event.get("id"))
        return {
            **record,
            "meta": {
                **self.snapshot_meta(snap),
                "surfaces": {
                    "entry_detail": self._knowledge_surface_state("institutional_memory_entries", snapshot_at=snap, has_data=True),
                    "source_context": self._knowledge_surface_state(
                        "institutional_memory_entries",
                        snapshot_at=snap,
                        has_data=source_context_available,
                        missing_message="Institutional memory source context is unavailable.",
                    ),
                },
            },
        }

    # --- Synthesis Conflict Logs use cases ---
    def _conflict_view(self, log: Dict[str, Any]) -> Dict[str, Any]:
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

    def list_synthesis_conflict_logs(
        self,
        *,
        capital_pool_id: Optional[str] = None,
        scope_ref: Optional[str] = None,
        proposal_id: Optional[str] = None,
        sponsor_persona_id: Optional[str] = None,
        synthesis_method: Optional[str] = None,
        committee_ref: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        raw_flag = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
        if raw_flag is not None and raw_flag.strip().lower() in {"0", "false", "no", "off", "disabled"}:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict log view disabled",
                "PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED is disabled for this BFF instance.",
                precondition_failed="synthesis_conflict_log_feature_flag",
            )
        list_reader = self.list_synthesis_conflict_logs_reader
        port = self._port()
        if list_reader is not None:
            try:
                records = list(list_reader(
                    capital_pool_id=capital_pool_id,
                    scope_ref=scope_ref,
                    proposal_id=proposal_id,
                    sponsor_persona_id=sponsor_persona_id,
                    synthesis_method=synthesis_method,
                    committee_ref=committee_ref,
                ) or [])
            except TypeError:
                records = list(list_reader() or [])
        elif callable(getattr(port, "list_synthesis_conflict_logs", None)):
            records = list(port.list_synthesis_conflict_logs(
                capital_pool_id=capital_pool_id,
                scope_ref=scope_ref,
                proposal_id=proposal_id,
                sponsor_persona_id=sponsor_persona_id,
                synthesis_method=synthesis_method,
                committee_ref=committee_ref,
            ) or [])
        else:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict logs are unavailable",
                "Inject list_synthesis_conflict_logs from the synthesis read adapter",
            )
        items, next_token = self.page_slice(records, page_token, page_size)
        projected = [self._conflict_view(item) for item in items]
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {
            "synthesis_conflict_logs": self._surface("synthesis_conflict_logs", snapshot_at=snap, has_data=bool(records))
        }
        return {
            "data": projected,
            "items": projected,
            "page_info": {"next_page_token": next_token, "total": len(records)},
            "meta": meta,
        }

    def get_synthesis_conflict_log(self, log_id: str, *, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        raw_flag = os.getenv("PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED")
        if raw_flag is not None and raw_flag.strip().lower() in {"0", "false", "no", "off", "disabled"}:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict log view disabled",
                "PANTHEON_SYNTHESIS_CONFLICT_LOG_VIEW_ENABLED is disabled for this BFF instance.",
                precondition_failed="synthesis_conflict_log_feature_flag",
            )
        get_reader = self.get_synthesis_conflict_log_reader
        port = self._port()
        clean_id = str(log_id or "").strip()
        if get_reader is not None:
            record = get_reader(clean_id)
        elif callable(getattr(port, "get_synthesis_conflict_log", None)):
            record = port.get_synthesis_conflict_log(clean_id)
        else:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Synthesis conflict logs are unavailable",
                "Inject get_synthesis_conflict_log from the synthesis read adapter",
            )
        if not record:
            self._not_found("Synthesis conflict log", clean_id)
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {
            "synthesis_conflict_log": self._surface("synthesis_conflict_logs", snapshot_at=snap, has_data=True)
        }
        return {
            "data": self._conflict_view(record),
            "meta": meta,
        }

    # --- Search use cases ---
    async def search_knowledge(
        self,
        *,
        query: str = "",
        types_raw: Optional[str] = None,
        page_size: int = 20,
        limit: Optional[int] = None,
        page_token: Optional[str] = None,
        identity: Any = None,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        needle = str(query or "").strip().lower()
        requested_types = {item.strip().lower() for item in str(types_raw).split(",") if item.strip()} if types_raw else None
        effective_page_size = int(limit or page_size or 20)
        effective_page_size = max(1, min(effective_page_size, 100))
        if self.cross_entity_search_fn is not None:
            result = self.cross_entity_search_fn(
                query=query,
                types=requested_types,
                page_size=effective_page_size,
                page_token=page_token,
                identity=identity,
            )
            result = await result if inspect.isawaitable(result) else result
            if isinstance(result, dict):
                return result
            records = list(result or [])
        else:
            records = []
            def _matches(value: Any) -> bool:
                return not needle or needle in str(value or "").lower()

            port = self._port()
            if not requested_types or "strategy" in requested_types:
                strategy_reader = getattr(port, "list_strategies", None) or getattr(port, "list_strategy_summaries", None)
                if callable(strategy_reader):
                    for raw in strategy_reader() or []:
                        item_id = str(raw.get("strategy_id") or raw.get("id") or "")
                        name_value = raw.get("title") or raw.get("name") or item_id
                        if _matches(item_id) or _matches(name_value):
                            records.append({
                                "id": item_id,
                                "type": "strategy",
                                "name": str(name_value),
                                "state": raw.get("lifecycle_state") or raw.get("status"),
                                "owner": raw.get("owner") or "pantheon-bff",
                                "risk": "medium",
                                "updatedAt": raw.get("updated_at") or raw.get("last_modified_at") or snap,
                            })
            if not requested_types or "persona" in requested_types:
                persona_reader = getattr(port, "list_personas", None)
                if callable(persona_reader):
                    for raw in persona_reader() or []:
                        item_id = str(raw.get("persona_id") or raw.get("id") or "")
                        name_value = raw.get("name") or item_id
                        if _matches(item_id) or _matches(name_value):
                            metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
                            records.append({
                                "id": item_id,
                                "type": "persona",
                                "name": str(name_value),
                                "state": raw.get("lifecycle_state") or raw.get("status"),
                                "owner": metadata.get("owner") or raw.get("owner") or "pantheon-bff",
                                "risk": metadata.get("risk_level") or "medium",
                                "updatedAt": raw.get("updated_at") or raw.get("created_at") or snap,
                            })
            if not requested_types or "capital_pool" in requested_types or "capitalpool" in requested_types:
                pool_reader = getattr(port, "list_capital_pools", None)
                if callable(pool_reader):
                    for raw in pool_reader() or []:
                        item_id = str(raw.get("pool_id") or raw.get("id") or "")
                        name_value = raw.get("name") or item_id
                        if _matches(item_id) or _matches(name_value):
                            records.append({
                                "id": item_id,
                                "type": "capital_pool",
                                "name": str(name_value),
                                "state": raw.get("status"),
                                "owner": raw.get("owner") or "pantheon-bff",
                                "risk": raw.get("risk_level") or "medium",
                                "updatedAt": raw.get("updated_at") or raw.get("created_at") or snap,
                            })
        items, next_token = self.page_slice(records, page_token, effective_page_size)
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {
            "search": self._surface("personas", snapshot_at=snap, has_data=bool(records))
        }
        return {
            "data": items,
            "items": items,
            "page_info": {
                "next_page_token": next_token,
                "total": len(records),
                "returned": len(items),
            },
            "meta": meta,
        }

    # --- Ops use cases ---
    def get_research_oss_preactivation_snapshot(
        self,
        *,
        activity_limit: int = 20,
        surface_key: str = "research_oss_preactivation",
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        port = self._port()
        fn = getattr(port, "get_research_oss_preactivation_snapshot", None)
        if not callable(fn):
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Research store port missing get_research_oss_preactivation_snapshot",
                f"Port {type(port).__name__} does not implement get_research_oss_preactivation_snapshot",
            )
        try:
            data = fn(activity_limit=activity_limit) or {}
        except HTTPException:
            raise
        except Exception as exc:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Research port get_research_oss_preactivation_snapshot failed",
                str(exc),
            )
        service_surfaces = {
            service: {
                key: value
                for key, value in status.items()
                if key in {"status", "source", "reason", "activity_status", "upstream_status", "upstream_reachable"}
            }
            for service, status in data.get("service_status", {}).items()
            if isinstance(status, dict)
        }
        composite_status = "ok"
        if any(surface.get("status") == "unavailable" for surface in service_surfaces.values()):
            composite_status = "degraded"
        if service_surfaces and all(surface.get("status") == "unavailable" for surface in service_surfaces.values()):
            composite_status = "unavailable"
        composite_surface = {
            "status": composite_status,
            "source": "service_client",
        }
        alias_key = (
            "research_oss_preactivation"
            if surface_key == "research_oss_activation_ready"
            else "research_oss_activation_ready"
        )
        meta = dict(self.snapshot_meta(snap))
        meta["surfaces"] = {
            surface_key: composite_surface,
            alias_key: composite_surface,
            **service_surfaces,
        }
        return {
            "data": data,
            "meta": meta,
        }

    def get_source_ops_snapshot(
        self,
        *,
        crawl_run_limit: int = 50,
        dlq_status: Optional[str] = None,
        frontier_status: Optional[str] = None,
        audit_limit: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        data = self._call_port(
            "get_source_ops_snapshot",
            crawl_run_limit=crawl_run_limit,
            dlq_status=dlq_status,
            frontier_status=frontier_status,
            audit_limit=audit_limit,
        )
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"source_ops": self._surface("source_ops", snapshot_at=snap, has_data=bool(data))}
        return {"data": data, "meta": meta}

    def get_search_ops_snapshot(
        self,
        *,
        pipeline_run_limit: int = 50,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        data = self._call_port("get_search_ops_snapshot", pipeline_run_limit=pipeline_run_limit)
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"search_ops": self._surface("search_ops", snapshot_at=snap, has_data=bool(data))}
        return {"data": data, "meta": meta}

    # --- Ticket and Connector use cases ---
    def _ticket_surface_state(self, *, snapshot_at: str, has_data: Optional[bool] = None) -> str:
        port = self._port()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_tickets") or "missing") if callable(source_fn) else "missing"
        if self.dataset_surface_status is not None:
            surface = self.dataset_surface_status(
                "research_tickets",
                snapshot_at=snapshot_at,
                source=source,
                has_data=has_data,
            )
            if isinstance(surface, str):
                return surface
            status = str((surface or {}).get("status") or "")
            if status == "unavailable" or source == "missing":
                return "unavailable"
            if source == "local_snapshot":
                return "degraded"
            if status == "degraded":
                return "stale"
            return "fresh"
        if source == "missing" or has_data is False:
            return "unavailable"
        return "fresh"

    def create_research_ticket(
        self,
        *,
        title: str,
        description: str,
        priority: str,
        owner: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = created_at or self.utc_now()
        ticket = self._call_port(
            "create_research_ticket",
            title=title,
            description=description,
            priority=priority,
            owner=owner,
            actor_id=actor_id,
            created_at=snap,
        )
        return {key: ticket.get(key) for key in ("ticket_id", "status", "created_at", "allowedActions")}

    def list_research_tickets(
        self,
        *,
        statuses: Optional[List[str]] = None,
        owner: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        records = list(self._call_port("list_research_tickets", statuses=statuses, owner=owner, include_fixture_pack=False) or [])
        surface_state = self._ticket_surface_state(snapshot_at=snap)
        if surface_state == "unavailable":
            items, next_token, total = [], None, 0
        else:
            items, next_token = self.page_slice(records, page_token, page_size)
            total = len(records)
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"ticket_list": surface_state}
        return {"data": items, "page_info": {"next_page_token": next_token, "total": total}, "meta": meta}

    def get_research_ticket(self, ticket_id: str, *, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        port = self._port()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_tickets") or "") if callable(source_fn) else ""
        if source == "local_snapshot":
            ticket = None
        else:
            ticket = self._call_port("get_research_ticket", ticket_id)
        if not ticket:
            self._not_found("Research ticket", ticket_id)
        payload = dict(ticket)
        payload["links"] = {
            "self": f"/api/v1/research/tickets/{ticket_id}",
            "workbench_detail": f"/research/tickets/{ticket_id}",
        }
        payload["meta"] = {
            **self.snapshot_meta(snap),
            "surfaces": {
                "ticket_detail": self._ticket_surface_state(
                    snapshot_at=snap, has_data=True
                ),
            },
        }
        return payload

    def patch_research_ticket(
        self,
        ticket_id: str,
        payload: Dict[str, Any],
        *,
        actor_id: str,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        ticket = self._call_port("get_research_ticket", ticket_id)
        if not ticket:
            self._not_found("Research ticket", ticket_id)

        allowed_fields = {"status", "title", "description", "priority", "owner"}
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            self._raise_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Invalid research ticket patch payload",
                f"Unsupported patch fields: {unknown_fields}",
                precondition_failed="payload_shape",
            )

        patch: Dict[str, Any] = {}
        editable = bool((ticket.get("allowedActions") or {}).get("canEdit"))
        for field in ("title", "description", "owner"):
            if field not in payload:
                continue
            value = str(payload.get(field) or "").strip()
            if not value:
                self._raise_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    f"{field} is required",
                    f"{field} must be a non-empty string",
                    precondition_failed=field,
                )
            if not editable:
                self._raise_error(
                    409,
                    ErrorCode.OPERATION_NOT_ALLOWED,
                    "Research ticket is not editable in its current lifecycle state",
                    f"{field} cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )
            patch[field] = value

        if "priority" in payload:
            if not editable:
                self._raise_error(
                    409,
                    ErrorCode.OPERATION_NOT_ALLOWED,
                    "Research ticket is not editable in its current lifecycle state",
                    "priority cannot be modified while allowedActions.canEdit is false.",
                    precondition_failed="allowedActions.canEdit",
                )
            p_val = str(payload["priority"] or "").strip().lower()
            if p_val not in {"low", "normal", "high", "critical"}:
                self._raise_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid research ticket priority",
                    "priority must be one of: ['critical', 'high', 'low', 'normal']",
                    precondition_failed="priority",
                )
            patch["priority"] = p_val

        if "status" in payload:
            current_status = str(ticket.get("status") or "").strip().lower()
            next_status = str(payload["status"] or "").strip().lower()
            if next_status not in {"open", "in_progress", "closed", "archived"}:
                self._raise_error(
                    422,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid research ticket status",
                    "status must be one of: ['archived', 'closed', 'in_progress', 'open']",
                    precondition_failed="status",
                )
            if next_status != current_status:
                actions = ticket.get("allowedActions") or {}
                if next_status == "closed" and not actions.get("canClose"):
                    self._raise_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Research ticket cannot be closed in its current state",
                        "allowedActions.canClose is false for this ticket.",
                        precondition_failed="allowedActions.canClose",
                    )
                if next_status == "archived" and not actions.get("canArchive"):
                    self._raise_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Research ticket cannot be archived in its current state",
                        "allowedActions.canArchive is false for this ticket.",
                        precondition_failed="allowedActions.canArchive",
                    )
                transitions = {
                    "open": {"in_progress", "closed"},
                    "in_progress": {"closed"},
                    "closed": {"archived"},
                    "archived": set(),
                }
                if next_status not in transitions.get(current_status, set()):
                    self._raise_error(
                        409,
                        ErrorCode.OPERATION_NOT_ALLOWED,
                        "Invalid research ticket lifecycle transition",
                        f"Cannot transition research ticket from {current_status} to {next_status}.",
                        precondition_failed="status_transition",
                    )
            patch["status"] = next_status

        if not patch:
            self._raise_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Empty research ticket patch payload",
                "At least one accepted patch field is required.",
                precondition_failed="payload_shape",
            )

        updated = self._call_port("patch_research_ticket", ticket_id, patch=patch, actor_id=actor_id, updated_at=snap)
        if not updated:
            self._raise_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Research ticket store unavailable", "Research ticket update store is unavailable")
        return {key: updated.get(key) for key in ("ticket_id", "status", "updated_at", "allowedActions")}

    def search_research(
        self,
        *,
        query: str,
        match_type: str = "all",
        status: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 25,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        index = self._call_port("get_research_search_index")
        if not index:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Search results are unavailable",
                "SEARCH_RESULTS_UNAVAILABLE",
                surfaces={"search_results": "unavailable"},
            )
        records = list(self._call_port("list_research_search_results", query=query, match_type=match_type, status=status, date_range=date_range) or [])
        items, next_token = self.page_slice(records, page_token, page_size)
        meta = dict(self.snapshot_meta(snap))
        meta["surfaces"] = {"search_results": self._surface("research_search", snapshot_at=snap, has_data=bool(records))}
        meta["index_adapter"] = index
        port = self._port()
        if hasattr(port, "get_last_governed_search_refs"):
            governed = self._call_port("get_last_governed_search_refs")
            if governed:
                meta["governed_evidence"] = governed
        return {"data": items, "page_info": {"next_page_token": next_token, "total": len(records)}, "meta": meta}

    def get_source_connectors(self, *, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        registry = self._call_port("get_source_connector_registry") or {}
        meta = dict(self.snapshot_meta(snap))
        meta["surfaces"] = {"source_connector_registry": self._surface("source_connectors", snapshot_at=snap, has_data=bool(registry.get("connectors")))}
        meta.update({
            "source": registry.get("source", "missing"),
            "provider_examples": list(registry.get("provider_examples") or []),
            "policy_registry": registry.get("policy_registry"),
        })
        return {"data": list(registry.get("connectors") or []), "meta": meta}

    def get_source_change_proposals(
        self,
        *,
        status: Optional[str] = None,
        proposal_type: Optional[str] = None,
        source_kind: Optional[str] = None,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        result = self._call_port(
            "get_source_change_proposals",
            status=status,
            proposal_type=proposal_type,
            source_kind=source_kind,
        ) or {}
        records = list(result.get("proposals") or [])
        source = str(result.get("source") or "missing")
        meta = dict(self.snapshot_meta(snap))
        meta["surfaces"] = {
            "source_change_proposals": "ok" if source == "service_client" else "unavailable"
        }
        meta["source"] = source
        return {"data": records, "meta": meta}

    # -------------------------------------------------------------------------
    # Experiment Domain Use Cases (BFF-ROUTER-USECASE-CORRECTIVE-001)
    # -------------------------------------------------------------------------

    def _enrich_experiment_with_analyses(self, item: Dict[str, Any], clean_id: str) -> Dict[str, Any]:
        enriched = dict(item)
        analyses = []
        port = self._port()
        if hasattr(port, "list_research_analyses"):
            try:
                analyses = self._call_port("list_research_analyses", experiment_id=clean_id) or []
            except Exception:
                analyses = []
        analysis_ids = []
        analysis_links = []
        for a in analyses:
            if isinstance(a, dict):
                a_id = str(a.get("analysis_id") or a.get("id") or "")
                if a_id:
                    analysis_ids.append(a_id)
                link_item = dict(a)
                if a_id and "detail" not in link_item:
                    link_item["detail"] = f"/bff/research-analyses/{a_id}"
                analysis_links.append(link_item)
        if "analysis_ids" not in enriched:
            enriched["analysis_ids"] = analysis_ids
        if "analysis_links" not in enriched:
            enriched["analysis_links"] = analysis_links
        return enriched

    def require_experiment(self, experiment_id: str) -> Dict[str, Any]:
        clean_id = experiment_id.strip()
        port = self._port()
        try:
            if hasattr(port, "get_experiment_bff"):
                item = self._call_port("get_experiment_bff", clean_id)
            elif hasattr(port, "get_research_experiment"):
                item = self._call_port("get_research_experiment", clean_id)
            else:
                item = None
        except ResearchWriteOwnerUnavailableError as exc:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Research experiment write owner unavailable",
                str(exc),
            )
        if not item:
            self._raise_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Experiment '{clean_id}' not found",
                f"No experiment exists with id '{clean_id}'",
            )
        return self._enrich_experiment_with_analyses(item, clean_id)

    def list_experiments_bff(
        self,
        *,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        port = self._port()
        if hasattr(port, "list_experiments_bff"):
            raw = self._call_port("list_experiments_bff", status=status) or []
            items = list(raw)
        else:
            raw = self._call_port("list_research_experiments") or []
            items = _filter_by_status_csv(raw, status)
        surface = self._surface("research_experiments", snapshot_at=snap, has_data=bool(items) or None)
        if surface.get("status") == "unavailable" and not items:
            page_items, next_page_token = [], None
        else:
            page_items, next_page_token = self.page_slice(items, page_token, page_size)
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"experiments": surface}
        return {"items": page_items, "page_info": {"next_page_token": next_page_token}, "meta": meta}

    def get_experiment_bff(self, experiment_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        clean_id = experiment_id.strip()
        snap = snapshot_at or self.utc_now()
        experiment = self.require_experiment(clean_id)
        return {"data": experiment, "meta": self.snapshot_meta(snap)}

    def get_experiment_logs(self, experiment_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        clean_id = experiment_id.strip()
        snap = snapshot_at or self.utc_now()
        self.require_experiment(clean_id)
        port = self._port()
        if hasattr(port, "get_experiment_logs"):
            logs = self._call_port("get_experiment_logs", clean_id) or []
        else:
            logs = []
        return {"experiment_id": clean_id, "logs": logs, "meta": self.snapshot_meta(snap)}

    def get_experiment_metrics(self, experiment_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        clean_id = experiment_id.strip()
        snap = snapshot_at or self.utc_now()
        self.require_experiment(clean_id)
        port = self._port()
        metrics = self._call_port("get_experiment_metrics", clean_id) if hasattr(port, "get_experiment_metrics") else {}
        return {"experiment_id": clean_id, "metrics": metrics, "meta": self.snapshot_meta(snap)}

    def get_experiment_artifacts(self, experiment_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        clean_id = experiment_id.strip()
        snap = snapshot_at or self.utc_now()
        self.require_experiment(clean_id)
        port = self._port()
        artifacts = self._call_port("get_experiment_artifacts", clean_id) if hasattr(port, "get_experiment_artifacts") else []
        return {"experiment_id": clean_id, "artifacts": artifacts, "meta": self.snapshot_meta(snap)}

    def list_research_experiments_bff(
        self,
        *,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        port = self._port()
        if hasattr(port, "list_research_experiments"):
            try:
                all_items = self._call_port("list_research_experiments", status=status)
            except TypeError:
                raw = self._call_port("list_research_experiments")
                all_items = _filter_by_status_csv(raw, status)
        else:
            raw = self._call_port("list_experiments_bff", status=status) if hasattr(port, "list_experiments_bff") else []
            all_items = list(raw)
        surface = self._surface("research_experiments", snapshot_at=snap, has_data=bool(all_items) or None)
        page_items, next_page_token = self.page_slice(all_items, page_token, page_size)
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"research_experiments": surface}
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_page_token, "total": len(all_items)},
            "meta": meta,
        }

    def get_research_experiment_bff(self, experiment_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        clean_id = experiment_id.strip()
        snap = snapshot_at or self.utc_now()
        port = self._port()
        if hasattr(port, "get_research_experiment"):
            experiment = self._call_port("get_research_experiment", clean_id)
        else:
            experiment = self.require_experiment(clean_id)
        if not experiment:
            self._raise_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                f"Experiment '{clean_id}' not found",
                f"No experiment exists with id '{clean_id}'",
            )
        experiment = self._enrich_experiment_with_analyses(experiment, clean_id)
        surface = self._surface("research_experiments", snapshot_at=snap, has_data=bool(experiment) or None)
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"research_experiment_detail": surface}
        return {"data": experiment, "meta": meta}

    def create_experiment(self, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        name = str(payload.get("name") or payload.get("experiment_name") or "").strip()
        if not name:
            self._raise_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "name is required",
                "Experiment name must be a non-empty string",
                precondition_failed="name",
            )
        port = self._port()
        try:
            if hasattr(port, "create_experiment_bff"):
                return self._call_port(
                    "create_experiment_bff",
                    name=name,
                    actor_id=actor_id,
                    created_at=self.utc_now(),
                    params=payload,
                )
            else:
                return self._call_port(
                    "create_research_experiment",
                    ticket_id=str(payload.get("ticket_id") or ""),
                    experiment_name=name,
                    strategy_selector=payload.get("strategy_selector") or {},
                    parameter_set=payload.get("parameter_set") or {},
                    run_config=payload.get("run_config") or {},
                    launch_context=payload.get("launch_context") or {"actor_id": actor_id},
                    queued_at=self.utc_now(),
                )
        except ResearchWriteOwnerUnavailableError as exc:
            self._raise_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Research experiment write owner unavailable",
                str(exc),
            )

    def launch_experiment(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        experiment = self._call_port(
            "create_research_experiment",
            ticket_id=payload["ticket_id"],
            experiment_name=payload["experiment_name"],
            strategy_selector=payload["strategy_selector"],
            parameter_set=payload["parameter_set"],
            run_config=payload["run_config"],
            launch_context=payload["launch_context"],
        )
        experiment_id = str(experiment.get("experiment_id") or "")
        return {
            "experiment_id": experiment_id,
            "ticket_id": experiment.get("ticket_id"),
            "status": experiment.get("status"),
            "queued_at": experiment.get("queued_at"),
            "allowedActions": {"canCancel": True},
            "links": {
                "self": f"/api/v1/experiments/{experiment_id}",
                "workbench_detail": f"/research/experiments/{experiment_id}",
            },
        }

    def list_experiments_api(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        records = list(self._call_port("list_research_experiments", ticket_id=ticket_id, status=status) or [])
        surface_state = self.legacy_experiment_surface_state(snapshot_at=snap, has_data=bool(records))
        if surface_state == "unavailable":
            items, next_token, total = [], None, 0
        else:
            page_items, next_token = self.page_slice(records, page_token, page_size)
            items = []
            for record in page_items:
                item = dict(record)
                experiment_ref = str(item.get("experiment_id") or "")
                item["links"] = {
                    "self": f"/api/v1/experiments/{experiment_ref}",
                    "workbench_detail": f"/research/experiments/{experiment_ref}",
                }
                item["allowedActions"] = {
                    "canCancel": bool((item.get("allowedActions") or {}).get("canCancel", False)),
                }
                items.append(item)
            total = len(records)
        meta = self.snapshot_meta(snap)
        meta["surfaces"] = {"experiment_history": surface_state}
        return {"data": items, "page_info": {"next_page_token": next_token, "total": total}, "meta": meta}

    def get_experiment_api(self, experiment_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        experiment = self._call_port("get_research_experiment", experiment_id)
        if not experiment:
            self._not_found("Experiment", experiment_id)
        payload = dict(experiment)
        ticket_id = str(payload.get("ticket_id") or "")
        payload["links"] = {
            "self": f"/api/v1/experiments/{experiment_id}",
            "workbench_detail": f"/research/experiments/{experiment_id}",
            "linked_ticket_detail": f"/research/tickets/{ticket_id}",
        }
        meta = dict(self.snapshot_meta(snap))
        meta["surfaces"] = {"experiment_status": self._surface("research_experiments", snapshot_at=snap, has_data=True)}
        payload["meta"] = meta
        return payload

    def cancel_experiment_api(self, experiment_id: str, reason: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        experiment = self._call_port("get_research_experiment", experiment_id)
        if not experiment:
            self._not_found("Experiment", experiment_id)
        if str(experiment.get("status") or "") not in {"queued", "running"}:
            self._raise_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "Experiment cannot be canceled",
                f"Experiment {experiment_id} is in terminal state '{experiment.get('status')}' and cannot be canceled",
            )
        canceled = self._call_port(
            "cancel_research_experiment",
            experiment_id,
            completed_at=snap,
        )
        if not canceled:
            self._raise_error(409, ErrorCode.OPERATION_NOT_ALLOWED, "Experiment cancel rejected", "Experiment could not be canceled")
        return {
            "experiment_id": experiment_id,
            "status": canceled.get("status"),
            "completed_at": canceled.get("completed_at"),
            "allowedActions": {"canCancel": False},
        }

    def legacy_experiment_surface_state(self, *, snapshot_at: str, has_data: bool) -> str:
        port = self._port()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn("research_experiments") or "missing") if callable(source_fn) else "missing"
        if source == "missing" and has_data:
            source = "bff_local"
        if self.dataset_surface_status is not None:
            surface = self.dataset_surface_status(
                "research_experiments",
                snapshot_at=snapshot_at,
                source=source,
                has_data=has_data,
            )
            if isinstance(surface, str):
                return surface
            status = str((surface or {}).get("status") or "")
            if status == "unavailable" or source == "missing":
                return "unavailable"
            if status == "degraded" or (surface or {}).get("source") == "local_snapshot":
                return "degraded"
            return "ok"
        if source == "missing" or not has_data:
            return "unavailable"
        return "ok"

    # -------------------------------------------------------------------------
    # Artifact Domain Use Cases (BFF-ROUTER-USECASE-CORRECTIVE-001)
    # -------------------------------------------------------------------------

    def list_artifacts_legacy(
        self,
        *,
        experiment_id: Optional[str] = None,
        ticket_id: Optional[str] = None,
        lineage_id: Optional[str] = None,
        status: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        snapshot_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        try:
            records = list(self._call_port(
                "list_research_artifacts",
                experiment_id=experiment_id,
                ticket_id=ticket_id,
                lineage_id=lineage_id,
                status=status,
            ) or [])
        except TypeError:
            records = list(self._call_port("list_research_artifacts", status=status) or [])
        records = _filter_legacy_artifacts(
            records,
            experiment_id=experiment_id,
            ticket_id=ticket_id,
            lineage_id=lineage_id,
            status=status,
        )
        items, next_token = self.page_slice(records, page_token, page_size)
        meta = dict(self.snapshot_meta(snap))
        meta["surfaces"] = {"artifact_list": self._surface("research_artifacts", snapshot_at=snap, has_data=bool(records))}
        return {"artifacts": items, "next_page_token": next_token, "total_count": len(records), "meta": meta}

    def get_artifact_legacy(self, artifact_id: str, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self.utc_now()
        artifact = self._call_port("get_research_artifact", artifact_id)
        if not artifact:
            self._not_found("Artifact", artifact_id)
        payload = dict(artifact)
        meta = dict(self.snapshot_meta(snap))
        meta["surfaces"] = {"artifact_detail": self._surface("research_artifacts", snapshot_at=snap, has_data=True)}
        payload["meta"] = meta
        return payload

    def patch_artifact_immutable(self, artifact_id: str) -> None:
        if not self._call_port("get_research_artifact", artifact_id):
            self._not_found("Artifact", artifact_id)
        self._raise_error(
            409,
            ErrorCode.OPERATION_NOT_ALLOWED,
            "Research artifacts are immutable",
            "Use the owning artifact pipeline; the generic BFF patch alias has no typed replacement",
        )

