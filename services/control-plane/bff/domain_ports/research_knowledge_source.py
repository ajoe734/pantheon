"""Typed domain ports and read models for Research, Knowledge, Memory, Search, and Source.

This module provides the `ResearchKnowledgeSourcePort` protocol and its default
implementation `DefaultResearchKnowledgeSourcePort`. It connects BFF read routes
directly to canonical domain stores and typed service clients, eliminating process-local
monkey patches, experiment overlays, and fixture-pack loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:
    from services.knowledge.evidence.repository import (
        InMemoryEvidenceRepository,
        JsonlEvidenceRepository,
    )
except ImportError:  # pragma: no cover
    InMemoryEvidenceRepository = None  # type: ignore[assignment,misc]
    JsonlEvidenceRepository = None  # type: ignore[assignment,misc]

try:
    from services.memory.institutional_memory_store import (
        InstitutionalMemoryEntry,
        InstitutionalMemoryStore,
    )
except ImportError:  # pragma: no cover
    InstitutionalMemoryEntry = None  # type: ignore[assignment,misc]
    InstitutionalMemoryStore = None  # type: ignore[assignment,misc]

try:
    from services.search.gateway import SearchAccessContext, SearchGateway, SearchRequest
    from services.search.index_store import JsonlSearchIndexStore
except ImportError:  # pragma: no cover
    SearchAccessContext = None  # type: ignore[assignment,misc]
    SearchGateway = None  # type: ignore[assignment,misc]
    SearchRequest = None  # type: ignore[assignment,misc]
    JsonlSearchIndexStore = None  # type: ignore[assignment,misc]

try:
    from services.source_ingestion.registry.data_source_registry import DataSourceRegistry
except ImportError:  # pragma: no cover
    DataSourceRegistry = None  # type: ignore[assignment,misc]


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        normalized = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
        dt = datetime.fromisoformat(normalized)
        return dt
    except (ValueError, TypeError):
        return None


def _naive_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.min
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class ResearchKnowledgeSourcePort:
    """Typed domain port interface for Research, Knowledge, Memory, Search, and Source reads."""

    # -------------------------------------------------------------------------
    # Surface & Dataset metadata
    # -------------------------------------------------------------------------
    def dataset_source(self, dataset: str) -> str:
        raise NotImplementedError

    def dataset_surface_status(
        self,
        dataset: str,
        *,
        snapshot_at: str,
        source: Optional[str] = None,
        has_data: bool = True,
        missing_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Knowledge & Evidence (KW-02, KW-03, KW-04, KW-05)
    # -------------------------------------------------------------------------
    def list_research_notes(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_research_note(self, note_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_evidence_refs(
        self,
        *,
        tenant_id: Optional[str] = None,
        include_tenant_agnostic: bool = True,
        linked_entities: Optional[set[tuple[str, str]]] = None,
        source_types: Optional[set[str]] = None,
        include_scope_metadata: bool = False,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_evidence_ref(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_evidence_ref_detail(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_insight_cards(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_insight_card(self, insight_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_insight_card_detail(self, insight_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_strategy_specs(
        self,
        *,
        lifecycle_state: Optional[str] = None,
        source_kind: Optional[str] = None,
        persona_id: Optional[str] = None,
        include_retired: bool = False,
        include_fixture_pack: bool = False,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_strategy_spec(self, strategy_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_strategy_spec_detail(
        self,
        strategy_id: Optional[str],
        *,
        version_selector: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def list_strategy_spec_versions(self, strategy_id: Optional[str]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def compare_strategy_spec_versions(
        self,
        strategy_id: Optional[str],
        *,
        left_selector: str,
        right_selector: str,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Institutional Memory
    # -------------------------------------------------------------------------
    def list_institutional_memory_entries(self) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_institutional_memory_entry(self, entry_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Research Tickets (RW-01)
    # -------------------------------------------------------------------------
    def list_research_tickets(
        self,
        *,
        statuses: Optional[List[str]] = None,
        owner: Optional[str] = None,
        include_fixture_pack: bool = False,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_research_ticket(self, ticket_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

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
        raise NotImplementedError

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Research Analyses (RW-03)
    # -------------------------------------------------------------------------
    def list_research_analyses(
        self,
        *,
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_research_analysis(self, analysis_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Research Experiments (RW-04)
    # -------------------------------------------------------------------------
    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def create_research_experiment(
        self,
        *,
        ticket_id: str,
        experiment_name: str,
        strategy_selector: Dict[str, Any],
        parameter_set: Dict[str, Any],
        run_config: Dict[str, Any],
        launch_context: Dict[str, Any],
        queued_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Research Artifacts (RW-05)
    # -------------------------------------------------------------------------
    def list_research_artifacts(
        self,
        *,
        artifact_type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
        author: Optional[str] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_research_artifact(self, artifact_id: Optional[str]) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def compare_research_artifacts(self, artifact_ids: List[str]) -> Dict[str, Any]:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Search & Governed Search (RW-02)
    # -------------------------------------------------------------------------
    def get_research_search_index(self) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_last_governed_search_refs(self) -> Dict[str, Dict[str, Any]]:
        raise NotImplementedError

    def list_research_search_results(
        self,
        *,
        query: str,
        match_type: str = "all",
        status: Optional[str] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_search_ops_snapshot(
        self,
        *,
        pipeline_run_limit: int = 50,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Source Ingestion & Ops (SVC-SOURCE-SEARCH-OPS-BFF)
    # -------------------------------------------------------------------------
    def get_source_connector_registry(self) -> Dict[str, Any]:
        raise NotImplementedError

    def get_source_change_proposals(
        self,
        *,
        status: Optional[str] = None,
        proposal_type: Optional[str] = None,
        source_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def get_source_ops_snapshot(
        self,
        *,
        crawl_run_limit: int = 50,
        dlq_status: Optional[str] = None,
        frontier_status: Optional[str] = None,
        audit_limit: int = 20,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def get_source_health_usage_snapshot(self) -> Dict[str, Any]:
        raise NotImplementedError


class DefaultResearchKnowledgeSourcePort(ResearchKnowledgeSourcePort):
    """Default typed implementation of ResearchKnowledgeSourcePort.

    Connects to typed stores and clients when provided. When an underlying
    owner is missing or unconfigured, it returns typed degraded/unavailable
    indicators without recreating process-local experiment/job overlays.
    """

    _RW04_CANCELABLE_STATUSES = frozenset({"queued", "running"})
    _RW05_COMPARABLE_STATUSES = frozenset({"sealed", "superseded"})
    _RW05_FIELD_SPECS = (
        ("metrics.sharpe_ratio", "Sharpe Ratio", "performance", "higher_is_better"),
        ("metrics.sortino_ratio", "Sortino Ratio", "performance", "higher_is_better"),
        ("metrics.max_drawdown", "Max Drawdown", "risk", "higher_is_better"),
        ("metrics.annualized_return", "Annualized Return", "performance", "higher_is_better"),
        ("metrics.win_rate", "Win Rate", "performance", "higher_is_better"),
        ("metrics.avg_trade_duration_days", "Avg Trade Duration", "performance", "lower_is_better"),
        ("metrics.total_trades", "Total Trades", "metadata", "neutral"),
        ("parameters.fast_period", "Fast Period", "parameters", "neutral"),
        ("parameters.slow_period", "Slow Period", "parameters", "neutral"),
        ("parameters.signal_period", "Signal Period", "parameters", "neutral"),
        ("parameters.position_sizing", "Position Sizing", "parameters", "neutral"),
        ("parameters.risk_per_trade", "Risk Per Trade", "parameters", "lower_is_better"),
        ("name", "Artifact Name", "metadata", "neutral"),
        ("produced_by_experiment_id", "Experiment Run", "metadata", "neutral"),
    )

    def __init__(
        self,
        *,
        evidence_repository: Optional[Any] = None,
        institutional_memory_store: Optional[Any] = None,
        search_gateway: Optional[Any] = None,
        search_index_store: Optional[Any] = None,
        search_service_url: Optional[str] = None,
        source_ingest_service_url: Optional[str] = None,
        data_source_registry: Optional[Any] = None,
        source_management_client: Optional[Any] = None,
        research_notes_store: Optional[Dict[str, Any]] = None,
        insight_cards_store: Optional[Dict[str, Any]] = None,
        strategy_specs_store: Optional[Dict[str, Any]] = None,
        research_tickets_store: Optional[Dict[str, Any]] = None,
        research_analyses_store: Optional[Dict[str, Any]] = None,
        research_experiments_store: Optional[Dict[str, Any]] = None,
        research_artifacts_store: Optional[Dict[str, Any]] = None,
        evidence_refs_store: Optional[Dict[str, Any]] = None,
        search_documents_store: Optional[List[Dict[str, Any]]] = None,
        http_get_fn: Optional[Callable[[str, str], Tuple[bool, Any]]] = None,
        http_post_fn: Optional[Callable[[str, str, Dict[str, Any]], Tuple[bool, Any]]] = None,
    ) -> None:
        self._evidence_repo = evidence_repository
        self._institutional_memory_store = institutional_memory_store
        self._search_gateway = search_gateway
        self._search_index_store = search_index_store
        self._search_service_url = search_service_url or os.getenv("PANTHEON_SEARCH_API_URL") or os.getenv("PANTHEON_SEARCH_SERVICE_URL")
        self._source_ingest_service_url = (
            source_ingest_service_url
            or os.getenv("PANTHEON_SOURCE_INGEST_API_URL")
            or os.getenv("PANTHEON_SOURCE_INGEST_URL")
            or os.getenv("SOURCE_INGEST_URL")
        )
        self._data_source_registry = data_source_registry
        self._source_management_client = source_management_client

        # In-memory stores for testing or isolated deployment
        self._notes: Dict[str, Any] = dict(research_notes_store or {})
        self._insights: Dict[str, Any] = dict(insight_cards_store or {})
        self._strategy_specs: Dict[str, Any] = dict(strategy_specs_store or {})
        self._tickets: Dict[str, Any] = dict(research_tickets_store or {})
        self._analyses: Dict[str, Any] = dict(research_analyses_store or {})
        self._experiments: Dict[str, Any] = dict(research_experiments_store or {})
        self._artifacts: Dict[str, Any] = dict(research_artifacts_store or {})
        self._evidence_refs: Dict[str, Any] = dict(evidence_refs_store or {})
        self._search_documents: List[Dict[str, Any]] = list(search_documents_store or [])
        self._last_governed_search_refs: Dict[str, Dict[str, Any]] = {}
        self._http_get = http_get_fn or self._default_http_get
        self._http_post = http_post_fn or self._default_http_post

    # -------------------------------------------------------------------------
    # HTTP helper stubs
    # -------------------------------------------------------------------------
    @staticmethod
    def _default_http_get(base_url: str, path: str) -> Tuple[bool, Any]:
        return False, None

    @staticmethod
    def _default_http_post(base_url: str, path: str, body: Dict[str, Any]) -> Tuple[bool, Any]:
        return False, None

    # -------------------------------------------------------------------------
    # Surface & Dataset metadata
    # -------------------------------------------------------------------------
    def dataset_source(self, dataset: str) -> str:
        if dataset == "institutional_memory_entries":
            return "typed_store" if self._institutional_memory_store is not None else ("bff_composed" if self._notes else "missing")
        if dataset == "evidence_refs":
            return "typed_store" if (self._evidence_repo is not None or self._evidence_refs) else "missing"
        if dataset in ("research_notes", "insight_cards", "strategy_specs", "research_tickets", "research_analyses", "research_experiments", "research_artifacts"):
            store_map = {
                "research_notes": self._notes,
                "insight_cards": self._insights,
                "strategy_specs": self._strategy_specs,
                "research_tickets": self._tickets,
                "research_analyses": self._analyses,
                "research_experiments": self._experiments,
                "research_artifacts": self._artifacts,
            }
            items = store_map.get(dataset, {})
            return "typed_store" if items else "missing"
        if dataset == "data_sources":
            return "service_client" if (self._source_ingest_service_url or self._source_management_client or self._data_source_registry) else "missing"
        if dataset == "search_ops":
            return "service_client" if (self._search_service_url or self._search_gateway) else "missing"
        return "missing"

    def dataset_surface_status(
        self,
        dataset: str,
        *,
        snapshot_at: str,
        source: Optional[str] = None,
        has_data: bool = True,
        missing_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        src = source or self.dataset_source(dataset)
        if src in ("missing", "unavailable") or not has_data:
            return {
                "status": "unavailable",
                "source": src,
                "message": missing_message or f"{dataset} has no readable source records.",
                "staleness": {
                    "served_from": "unverifiable" if src == "missing" else src,
                    "last_known_at": snapshot_at,
                },
            }
        return {
            "status": "ok",
            "source": src,
        }

    # -------------------------------------------------------------------------
    # Knowledge & Evidence: Research Notes (KW-02)
    # -------------------------------------------------------------------------
    def list_research_notes(self) -> List[Dict[str, Any]]:
        notes = list(self._notes.values())
        notes.sort(
            key=lambda note: _naive_utc(_parse_rfc3339(note.get("updated_at")) or _parse_rfc3339(note.get("created_at"))),
            reverse=True,
        )
        return [json.loads(json.dumps(note)) for note in notes if isinstance(note, dict)]

    def get_research_note(self, note_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not note_id:
            return None
        note = self._notes.get(str(note_id))
        return json.loads(json.dumps(note)) if isinstance(note, dict) else None

    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        note_id = str(note.get("note_id") or "").strip()
        if not note_id:
            return None
        payload = json.loads(json.dumps(note))
        self._notes[note_id] = payload
        return json.loads(json.dumps(payload))

    # -------------------------------------------------------------------------
    # Knowledge & Evidence: Evidence Refs (KW-03)
    # -------------------------------------------------------------------------
    @staticmethod
    def _kw03_route_href(ref_id: Optional[str]) -> Optional[str]:
        ref = str(ref_id or "").strip()
        return f"/knowledge/evidence/{ref}" if ref else None

    @staticmethod
    def _kw03_entity_route_href(entity_type: Optional[str], entity_ref: Optional[str]) -> Optional[str]:
        entity = str(entity_type or "").strip()
        ref = str(entity_ref or "").strip()
        if not entity or not ref:
            return None
        route_map = {
            "memory_entry": "/knowledge/memory",
            "research_note": "/knowledge/notes",
            "insight_card": "/knowledge/insights",
            "strategy_spec": "/knowledge/strategy-specs",
            "experiment": "/research/experiments",
            "artifact": "/research/artifacts",
        }
        base = route_map.get(entity)
        return f"{base}/{ref}" if base else None

    def _kw03_entity_display_label(self, entity_type: Optional[str], entity_ref: Optional[str]) -> Optional[str]:
        entity = str(entity_type or "").strip()
        ref = str(entity_ref or "").strip()
        if not entity or not ref:
            return None
        if entity == "memory_entry":
            entry = self.get_institutional_memory_entry(ref) or {}
            content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
            return content.get("headline")
        if entity == "research_note":
            note = self.get_research_note(ref) or {}
            return note.get("title")
        if entity == "evidence_ref":
            evidence_ref = self.get_evidence_ref(ref) or {}
            source_document = (
                evidence_ref.get("source_document")
                if isinstance(evidence_ref.get("source_document"), dict)
                else {}
            )
            return evidence_ref.get("display_label") or source_document.get("title")
        if entity == "insight_card":
            insight = self.get_insight_card(ref) or {}
            return insight.get("summary")
        if entity == "strategy_spec":
            spec = self.get_strategy_spec(ref) or {}
            return spec.get("title") or spec.get("name")
        if entity == "experiment":
            experiment = self.get_research_experiment(ref) or {}
            return experiment.get("experiment_name")
        if entity == "artifact":
            artifact = self.get_research_artifact(ref) or {}
            return artifact.get("name")
        return None

    @staticmethod
    def _kw03_normalize_credibility(raw: Any, *, include_detail: bool) -> Dict[str, Any]:
        credibility = raw if isinstance(raw, dict) else {}
        payload: Dict[str, Any] = {
            "tier": credibility.get("tier") or "unverified",
            "verified": bool(credibility.get("verified")),
        }
        if include_detail:
            payload["last_verified_at"] = credibility.get("last_verified_at")
            payload["verification_method"] = credibility.get("verification_method")
        return payload

    @staticmethod
    def _kw03_normalize_resolved_link(raw: Any) -> Dict[str, Any]:
        link = raw if isinstance(raw, dict) else {}
        availability = str(link.get("availability") or "").strip().lower()
        if availability not in {"available", "unavailable", "external"}:
            availability = "unavailable"
        route_href = link.get("route_href")
        if availability == "unavailable":
            route_href = None
        open_in_new_tab = bool(link.get("open_in_new_tab")) if availability != "unavailable" else False
        if availability == "external" and route_href:
            open_in_new_tab = True if link.get("open_in_new_tab") is None else bool(link.get("open_in_new_tab"))
        return {
            "availability": availability,
            "route_href": route_href,
            "display_label": link.get("display_label") or "Source unavailable",
            "open_in_new_tab": open_in_new_tab,
        }

    @staticmethod
    def _tenant_scope_values(value: Any) -> List[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            return [part.strip() for part in re.split(r"[\s,]+", value) if part.strip()]
        if isinstance(value, dict):
            values: List[str] = []
            for key in ("id", "tenant_id", "tenantId", "value", "name"):
                if value.get(key) not in (None, ""):
                    values.extend(DefaultResearchKnowledgeSourcePort._tenant_scope_values(value.get(key)))
            return values
        if isinstance(value, (list, tuple, set)):
            values = []
            for item in value:
                values.extend(DefaultResearchKnowledgeSourcePort._tenant_scope_values(item))
            return values
        return [str(value).strip()]

    @classmethod
    def _record_tenant_ids(cls, record: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        direct_keys = (
            "tenant_id",
            "tenantId",
            "tenant",
            "tenant_ref",
            "tenantRef",
            "org_id",
            "orgId",
            "organization_id",
            "organizationId",
            "workspace_id",
            "workspaceId",
        )
        for key in direct_keys:
            if key in record:
                values.extend(cls._tenant_scope_values(record.get(key)))
        for key in ("metadata", "scope", "source_document", "linked_object_summary"):
            nested = record.get(key)
            if isinstance(nested, dict):
                values.extend(cls._record_tenant_ids(nested))
        seen = set()
        result: List[str] = []
        for val in values:
            clean = str(val or "").strip()
            if clean and clean not in seen:
                seen.add(clean)
                result.append(clean)
        return result

    @classmethod
    def _record_matches_tenant(
        cls,
        record: Dict[str, Any],
        tenant_id: Optional[str],
        *,
        include_tenant_agnostic: bool,
    ) -> bool:
        clean_tenant = str(tenant_id or "").strip()
        if not clean_tenant:
            return True
        record_tenants = cls._record_tenant_ids(record)
        if not record_tenants:
            return include_tenant_agnostic
        return "*" in record_tenants or clean_tenant in record_tenants

    @staticmethod
    def _evidence_linked_entity_pairs(evidence_ref: Dict[str, Any]) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()

        def add_pair(entity_type: Any, entity_ref: Any) -> None:
            clean_type = str(entity_type or "").strip().lower()
            clean_ref = str(entity_ref or "").strip()
            if clean_type and clean_ref:
                pairs.add((clean_type, clean_ref))

        linked_summary = evidence_ref.get("linked_object_summary")
        if isinstance(linked_summary, dict):
            add_pair(linked_summary.get("entity_type"), linked_summary.get("entity_ref"))
        add_pair(evidence_ref.get("linked_entity_type"), evidence_ref.get("linked_entity_ref"))
        add_pair(evidence_ref.get("target_type"), evidence_ref.get("target_id"))
        for key in ("linked_decisions", "linked_entities", "related_entities"):
            for item in evidence_ref.get(key) or []:
                if isinstance(item, dict):
                    add_pair(
                        item.get("entity_type") or item.get("type"),
                        item.get("entity_ref") or item.get("ref") or item.get("id"),
                    )
        return pairs

    @staticmethod
    def _evidence_source_type(evidence_ref: Dict[str, Any]) -> str:
        source_document = (
            evidence_ref.get("source_document")
            if isinstance(evidence_ref.get("source_document"), dict)
            else {}
        )
        return str(
            evidence_ref.get("source_type")
            or source_document.get("source_type")
            or evidence_ref.get("evidence_type")
            or evidence_ref.get("type")
            or ""
        ).strip().lower()

    @classmethod
    def _evidence_matches_scope(
        cls,
        evidence_ref: Dict[str, Any],
        *,
        linked_entities: Optional[set[tuple[str, str]]],
        source_types: Optional[set[str]],
    ) -> bool:
        normalized_entities = {
            (str(entity_type or "").strip().lower(), str(entity_ref or "").strip())
            for entity_type, entity_ref in (linked_entities or set())
            if str(entity_type or "").strip() and str(entity_ref or "").strip()
        }
        normalized_source_types = {
            str(source_type or "").strip().lower()
            for source_type in (source_types or set())
            if str(source_type or "").strip()
        }
        if not normalized_entities and not normalized_source_types:
            return True
        ref_entities = cls._evidence_linked_entity_pairs(evidence_ref)
        if ref_entities:
            return bool(normalized_entities and ref_entities.intersection(normalized_entities))
        ref_source_type = cls._evidence_source_type(evidence_ref)
        if ref_source_type and ref_source_type in normalized_source_types:
            return True
        return False

    def _project_evidence_ref_list_item(
        self,
        evidence_ref: Dict[str, Any],
        *,
        include_scope_metadata: bool = False,
    ) -> Dict[str, Any]:
        ref_id = evidence_ref.get("ref_id")
        source_document = evidence_ref.get("source_document") if isinstance(evidence_ref.get("source_document"), dict) else {}
        linked_summary = (
            evidence_ref.get("linked_object_summary")
            if isinstance(evidence_ref.get("linked_object_summary"), dict)
            else {}
        )
        if not linked_summary and isinstance(evidence_ref.get("linked_decisions"), list) and evidence_ref.get("linked_decisions"):
            first = evidence_ref["linked_decisions"][0]
            if isinstance(first, dict):
                linked_summary = {
                    "entity_type": first.get("entity_type"),
                    "entity_ref": first.get("entity_ref"),
                    "display_label": first.get("display_label"),
                }

        linked_entity_type = linked_summary.get("entity_type")
        linked_entity_ref = linked_summary.get("entity_ref")
        linked_display_label = (
            linked_summary.get("display_label")
            or self._kw03_entity_display_label(linked_entity_type, linked_entity_ref)
        )
        route_href = evidence_ref.get("route_href") or self._kw03_route_href(ref_id)
        payload = {
            "ref_id": ref_id,
            "evidence_type": evidence_ref.get("evidence_type") or evidence_ref.get("type") or None,
            "display_label": evidence_ref.get("display_label") or source_document.get("title") or linked_display_label or ref_id,
            "route_href": route_href,
            "source_document": {
                "title": source_document.get("title") or evidence_ref.get("display_label") or ref_id,
                "source_type": source_document.get("source_type"),
                "source_ref": source_document.get("source_ref"),
                "captured_at": source_document.get("captured_at") or evidence_ref.get("created_at"),
            },
            "link_type": evidence_ref.get("link_type"),
            "credibility": self._kw03_normalize_credibility(
                evidence_ref.get("credibility"),
                include_detail=False,
            ),
            "linked_object_summary": {
                "entity_type": linked_entity_type,
                "entity_ref": linked_entity_ref,
                "display_label": linked_display_label,
            },
            "resolved_link": self._kw03_normalize_resolved_link(evidence_ref.get("resolved_link")),
        }
        artifact_manifest = evidence_ref.get("artifact_manifest")
        if isinstance(artifact_manifest, dict):
            payload["artifact_manifest"] = json.loads(json.dumps(artifact_manifest))
        criteria = evidence_ref.get("criteria")
        if isinstance(criteria, dict):
            payload["criteria"] = json.loads(json.dumps(criteria))
        if "overall" in evidence_ref:
            payload["overall"] = evidence_ref.get("overall")
        if include_scope_metadata:
            tenant_ids = self._record_tenant_ids(evidence_ref)
            if tenant_ids:
                payload["tenant_id"] = tenant_ids[0]
                payload["tenantId"] = tenant_ids[0]
            linked_decisions = [
                {
                    "entity_type": item.get("entity_type") or item.get("type"),
                    "entity_ref": item.get("entity_ref") or item.get("ref") or item.get("id"),
                }
                for item in evidence_ref.get("linked_decisions") or []
                if isinstance(item, dict)
            ]
            if linked_decisions:
                payload["linked_decisions"] = linked_decisions
        return payload

    def _project_evidence_ref_detail(self, evidence_ref: Dict[str, Any]) -> Dict[str, Any]:
        projected = self._project_evidence_ref_list_item(evidence_ref)
        source_document = evidence_ref.get("source_document") if isinstance(evidence_ref.get("source_document"), dict) else {}
        storage_preview = (
            source_document.get("storage_preview")
            if isinstance(source_document.get("storage_preview"), dict)
            else {}
        )
        linked_decisions: List[Dict[str, Any]] = []
        for item in evidence_ref.get("linked_decisions") or []:
            if not isinstance(item, dict):
                continue
            entity_type = item.get("entity_type")
            entity_ref = item.get("entity_ref")
            linked_decisions.append(
                {
                    "entity_type": entity_type,
                    "entity_ref": entity_ref,
                    "display_label": item.get("display_label")
                    or self._kw03_entity_display_label(entity_type, entity_ref),
                    "route_href": item.get("route_href")
                    or self._kw03_entity_route_href(entity_type, entity_ref),
                    "link_type": item.get("link_type") or evidence_ref.get("link_type"),
                    "relationship_note": item.get("relationship_note"),
                }
            )

        source_note_context = (
            evidence_ref.get("source_note_context")
            if isinstance(evidence_ref.get("source_note_context"), dict)
            else None
        )
        source_memory_context = (
            evidence_ref.get("source_memory_context")
            if isinstance(evidence_ref.get("source_memory_context"), dict)
            else None
        )

        return {
            "ref_id": projected.get("ref_id"),
            "evidence_type": projected.get("evidence_type"),
            "display_label": projected.get("display_label"),
            "route_href": projected.get("route_href"),
            "source_document": {
                "title": projected["source_document"].get("title"),
                "source_type": projected["source_document"].get("source_type"),
                "excerpt": source_document.get("excerpt"),
                "source_ref": projected["source_document"].get("source_ref"),
                "storage_preview": {
                    "available": bool(storage_preview.get("available")),
                    "preview_type": storage_preview.get("preview_type") or "unavailable",
                    "preview_token": storage_preview.get("preview_token"),
                },
                "captured_at": projected["source_document"].get("captured_at"),
                "captured_by": source_document.get("captured_by"),
            },
            "link_type": projected.get("link_type"),
            "credibility": self._kw03_normalize_credibility(
                evidence_ref.get("credibility"),
                include_detail=True,
            ),
            "resolved_link": projected.get("resolved_link"),
            "linked_object_summary": projected.get("linked_object_summary"),
            "linked_decisions": linked_decisions,
            "source_note_context": json.loads(json.dumps(source_note_context)),
            "source_memory_context": json.loads(json.dumps(source_memory_context)),
            "created_at": evidence_ref.get("created_at") or projected["source_document"].get("captured_at"),
        }

    def _collect_raw_evidence_refs(self) -> List[Dict[str, Any]]:
        if self._evidence_repo is not None:
            # Reconstruct from evidence repository if present
            refs: List[Dict[str, Any]] = []
            for item in self._evidence_repo.list_evidence_items():
                src = self._evidence_repo.get_source_record(item.source_id)
                refs.append({
                    "ref_id": item.evidence_item_id,
                    "display_label": item.citation_label or (src.title if src else item.evidence_item_id),
                    "source_document": {
                        "title": src.title if src else item.citation_label,
                        "source_type": src.source_type if src else "internal",
                        "source_ref": src.content_ref if src else None,
                        "captured_at": item.metadata.get("captured_at") or _utc_now_rfc3339(),
                    },
                    "link_type": "supporting",
                    "credibility": {"tier": "verified" if item.confidence >= 0.8 else "unverified", "verified": item.confidence >= 0.8},
                    "metadata": dict(item.metadata),
                })
            return refs
        return list(self._evidence_refs.values())

    def list_evidence_refs(
        self,
        *,
        tenant_id: Optional[str] = None,
        include_tenant_agnostic: bool = True,
        linked_entities: Optional[set[tuple[str, str]]] = None,
        source_types: Optional[set[str]] = None,
        include_scope_metadata: bool = False,
    ) -> List[Dict[str, Any]]:
        raw_refs = self._collect_raw_evidence_refs()
        filtered = [
            ref
            for ref in raw_refs
            if self._record_matches_tenant(
                ref,
                tenant_id,
                include_tenant_agnostic=include_tenant_agnostic,
            )
            and self._evidence_matches_scope(
                ref,
                linked_entities=linked_entities,
                source_types=source_types,
            )
        ]
        filtered.sort(
            key=lambda ref: (
                _naive_utc(
                    _parse_rfc3339(((ref.get("source_document") or {}).get("captured_at")) or ref.get("created_at"))
                ),
                str(ref.get("ref_id") or ""),
            ),
            reverse=True,
        )
        return [
            self._project_evidence_ref_list_item(ref, include_scope_metadata=include_scope_metadata)
            for ref in filtered
        ]

    def get_evidence_ref(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ref_id:
            return None
        raw_refs = {str(r.get("ref_id") or ""): r for r in self._collect_raw_evidence_refs()}
        ref = raw_refs.get(str(ref_id))
        return self._project_evidence_ref_list_item(ref) if ref else None

    def get_evidence_ref_detail(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ref_id:
            return None
        raw_refs = {str(r.get("ref_id") or ""): r for r in self._collect_raw_evidence_refs()}
        ref = raw_refs.get(str(ref_id))
        return self._project_evidence_ref_detail(ref) if ref else None

    # -------------------------------------------------------------------------
    # Knowledge & Evidence: Insight Cards (KW-04)
    # -------------------------------------------------------------------------
    @staticmethod
    def _kw04_route_href(insight_id: Optional[str]) -> Optional[str]:
        ref = str(insight_id or "").strip()
        return f"/knowledge/insights/{ref}" if ref else None

    def _kw04_scope_context(self, scope: Optional[str], scope_ref: Optional[str]) -> Dict[str, Any]:
        normalized_scope = str(scope or "").strip().lower()
        ref = str(scope_ref or "").strip()
        if normalized_scope == "global" or not ref:
            return {
                "scope_ref": None,
                "display_label": None,
                "route_href": None,
            }
        if normalized_scope == "strategy":
            strategy_spec = self.get_strategy_spec(ref) or {}
            title = strategy_spec.get("title") or strategy_spec.get("name")
            display = f"{title} — Strategy Spec" if title else None
            return {
                "scope_ref": ref,
                "display_label": display,
                "route_href": f"/knowledge/strategy-specs/{ref}",
            }
        if normalized_scope == "experiment":
            experiment = self.get_research_experiment(ref) or {}
            return {
                "scope_ref": ref,
                "display_label": experiment.get("experiment_name"),
                "route_href": f"/research/experiments/{ref}",
            }
        return {
            "scope_ref": ref,
            "display_label": None,
            "route_href": None,
        }

    def _project_kw04_supporting_evidence_ref(self, item: Dict[str, Any]) -> Dict[str, Any]:
        ref_id = str(item.get("ref_id") or "").strip()
        evidence_ref = self.get_evidence_ref(ref_id) if ref_id else None
        source_document = (
            evidence_ref.get("source_document")
            if isinstance((evidence_ref or {}).get("source_document"), dict)
            else {}
        )
        credibility = (
            evidence_ref.get("credibility")
            if isinstance((evidence_ref or {}).get("credibility"), dict)
            else {}
        )
        return {
            "ref_id": ref_id,
            "source_document_title": (
                item.get("source_document_title")
                or source_document.get("title")
                or (evidence_ref or {}).get("display_label")
                or ref_id
            ),
            "link_type": item.get("link_type") or (evidence_ref or {}).get("link_type"),
            "credibility_tier": item.get("credibility_tier") or credibility.get("tier") or "unverified",
            "resolved_link": self._kw03_normalize_resolved_link(
                item.get("resolved_link") or (evidence_ref or {}).get("resolved_link")
            ),
        }

    def _project_kw04_linked_source(self, item: Dict[str, Any]) -> Dict[str, Any]:
        entity_type = item.get("entity_type")
        entity_ref = item.get("entity_ref")
        return {
            "entity_type": entity_type,
            "entity_ref": entity_ref,
            "display_label": item.get("display_label")
            or self._kw03_entity_display_label(entity_type, entity_ref),
            "route_href": item.get("route_href")
            or self._kw03_entity_route_href(entity_type, entity_ref),
            "relationship_note": item.get("relationship_note"),
        }

    def _project_insight_card_list_item(self, insight_card: Dict[str, Any]) -> Dict[str, Any]:
        confidence = insight_card.get("confidence") if isinstance(insight_card.get("confidence"), dict) else {}
        provenance = (
            insight_card.get("aggregation_provenance")
            if isinstance(insight_card.get("aggregation_provenance"), dict)
            else {}
        )
        supporting_evidence_refs = [
            self._project_kw04_supporting_evidence_ref(item)
            for item in insight_card.get("supporting_evidence_refs") or []
            if isinstance(item, dict)
        ]
        linked_sources = [
            self._project_kw04_linked_source(item)
            for item in insight_card.get("linked_sources") or []
            if isinstance(item, dict)
        ]
        return {
            "insight_id": insight_card.get("insight_id"),
            "summary": insight_card.get("summary"),
            "scope": insight_card.get("scope"),
            "scope_ref": insight_card.get("scope_ref"),
            "status": insight_card.get("status") or "active",
            "superseded_by_id": insight_card.get("superseded_by_id"),
            "confidence": {
                "score": confidence.get("score"),
                "label": confidence.get("label"),
            },
            "tags": list(insight_card.get("tags") or []),
            "evidence_count": len(supporting_evidence_refs),
            "primary_evidence_count": provenance.get("primary_evidence_count")
            if provenance.get("primary_evidence_count") is not None
            else len(
                [
                    item
                    for item in supporting_evidence_refs
                    if str(item.get("credibility_tier") or "") == "primary"
                ]
            ),
            "aggregated_at": provenance.get("aggregated_at"),
            "route_href": insight_card.get("route_href") or self._kw04_route_href(insight_card.get("insight_id")),
            "linked_sources": linked_sources,
        }

    def _project_insight_card_detail(self, insight_card: Dict[str, Any]) -> Dict[str, Any]:
        projected = self._project_insight_card_list_item(insight_card)
        superseded_by_id = projected.get("superseded_by_id")
        superseded_card = self.get_insight_card(superseded_by_id) if superseded_by_id else None
        confidence = insight_card.get("confidence") if isinstance(insight_card.get("confidence"), dict) else {}
        return {
            "insight_id": projected.get("insight_id"),
            "summary": projected.get("summary"),
            "scope": projected.get("scope"),
            "scope_context": self._kw04_scope_context(
                projected.get("scope"),
                projected.get("scope_ref"),
            ),
            "status": projected.get("status"),
            "superseded_by": {
                "insight_id": superseded_by_id,
                "summary": (superseded_card or {}).get("summary") if superseded_by_id else None,
                "route_href": (superseded_card or {}).get("route_href") if superseded_by_id else None,
            },
            "confidence": {
                "score": confidence.get("score"),
                "label": confidence.get("label"),
                "basis": confidence.get("basis"),
            },
            "tags": projected.get("tags"),
            "source_ref": insight_card.get("source_ref"),
            "supporting_evidence_refs": [
                self._project_kw04_supporting_evidence_ref(item)
                for item in insight_card.get("supporting_evidence_refs") or []
                if isinstance(item, dict)
            ],
            "linked_sources": [
                self._project_kw04_linked_source(item)
                for item in insight_card.get("linked_sources") or []
                if isinstance(item, dict)
            ],
            "aggregation_provenance": json.loads(
                json.dumps(insight_card.get("aggregation_provenance") or {})
            ),
            "created_at": insight_card.get("created_at"),
            "updated_at": insight_card.get("updated_at"),
        }

    def list_insight_cards(self) -> List[Dict[str, Any]]:
        cards = list(self._insights.values())
        cards.sort(
            key=lambda card: (
                _naive_utc(
                    _parse_rfc3339(((card.get("aggregation_provenance") or {}).get("aggregated_at")))
                    or _parse_rfc3339(card.get("updated_at"))
                    or _parse_rfc3339(card.get("created_at"))
                ),
                str(card.get("insight_id") or ""),
            ),
            reverse=True,
        )
        return [self._project_insight_card_list_item(card) for card in cards if isinstance(card, dict)]

    def get_insight_card(self, insight_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not insight_id:
            return None
        card = self._insights.get(str(insight_id))
        return self._project_insight_card_list_item(card) if isinstance(card, dict) else None

    def get_insight_card_detail(self, insight_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not insight_id:
            return None
        card = self._insights.get(str(insight_id))
        return self._project_insight_card_detail(card) if isinstance(card, dict) else None

    # -------------------------------------------------------------------------
    # Knowledge & Evidence: Strategy Specs (KW-05)
    # -------------------------------------------------------------------------
    @staticmethod
    def _kw05_lifecycle_state(value: Any) -> str:
        normalized = str(value or "").strip().lower()
        mapping = {
            "draft": "draft",
            "candidate": "candidate",
            "approved": "approved",
            "retired": "retired",
            "active": "approved",
        }
        return mapping.get(normalized, "draft")

    @staticmethod
    def _kw05_hypothesis_excerpt(value: Any, limit: int = 180) -> Optional[str]:
        text = " ".join(str(value or "").split())
        if not text:
            return None
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"

    @staticmethod
    def _kw05_strategy_route_href(strategy_id: str, version_id: Optional[str] = None) -> str:
        base = f"/knowledge/strategy-specs/{strategy_id}"
        return f"{base}?version={version_id}" if version_id else base

    @classmethod
    def _kw05_normalize_citation_bundle(cls, raw: Any) -> Dict[str, Any]:
        bundle = raw if isinstance(raw, dict) else {}
        return {
            "evidence_refs": [
                json.loads(json.dumps(item))
                for item in bundle.get("evidence_refs") or []
                if isinstance(item, dict)
            ],
            "memory_anchors": [
                json.loads(json.dumps(item))
                for item in bundle.get("memory_anchors") or []
                if isinstance(item, dict)
            ],
            "insight_citations": [
                json.loads(json.dumps(item))
                for item in bundle.get("insight_citations") or []
                if isinstance(item, dict)
            ],
        }

    @classmethod
    def _kw05_allowed_actions(cls, version: Dict[str, Any]) -> Dict[str, bool]:
        lifecycle_state = cls._kw05_lifecycle_state(version.get("lifecycle_state"))
        return {
            "canSubmitForApproval": lifecycle_state == "draft",
            "canRetire": lifecycle_state in {"candidate", "approved"},
            "canCompare": lifecycle_state in {"candidate", "approved", "retired"},
        }

    @classmethod
    def _kw05_sort_versions(cls, versions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(
            versions,
            key=lambda version: (
                _naive_utc(_parse_rfc3339(version.get("created_at"))),
                str(version.get("spec_version_id") or ""),
            ),
            reverse=True,
        )

    @classmethod
    def _kw05_versions(cls, strategy_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        strategy_id = str(strategy_spec.get("strategy_id") or strategy_spec.get("id") or "").strip()
        if not strategy_id:
            return []

        raw_versions = strategy_spec.get("versions")
        candidates = raw_versions if isinstance(raw_versions, list) and raw_versions else [strategy_spec]
        versions: List[Dict[str, Any]] = []
        for index, raw_version in enumerate(candidates, start=1):
            if not isinstance(raw_version, dict):
                continue
            provenance = (
                raw_version.get("provenance")
                if isinstance(raw_version.get("provenance"), dict)
                else {}
            )
            spec_version_id = str(
                raw_version.get("spec_version_id")
                or (
                    strategy_spec.get("current_spec_version_id")
                    if index == 1 and len(candidates) == 1
                    else ""
                )
                or raw_version.get("id")
                or strategy_id
            ).strip()
            spec_version = str(
                raw_version.get("spec_version")
                or (
                    strategy_spec.get("current_spec_version")
                    if index == 1 and len(candidates) == 1
                    else ""
                )
                or f"v{index}"
            ).strip()
            title = (
                raw_version.get("title")
                or strategy_spec.get("title")
                or strategy_spec.get("name")
            )
            version = {
                "object_ref": {
                    "type": "StrategySpec",
                    "id": spec_version_id,
                },
                "strategy_id": strategy_id,
                "spec_version_id": spec_version_id,
                "spec_version": spec_version,
                "parent_spec_version_id": raw_version.get("parent_spec_version_id"),
                "derived_from_source_refs": list(
                    raw_version.get("derived_from_source_refs")
                    or provenance.get("source_refs")
                    or []
                ),
                "lifecycle_state": cls._kw05_lifecycle_state(
                    raw_version.get("lifecycle_state")
                    or raw_version.get("status")
                    or strategy_spec.get("lifecycle_state")
                    or strategy_spec.get("status")
                ),
                "title": title,
                "hypothesis": raw_version.get("hypothesis") or strategy_spec.get("hypothesis"),
                "objective": raw_version.get("objective") or strategy_spec.get("objective"),
                "market_scope": json.loads(
                    json.dumps(raw_version.get("market_scope") or strategy_spec.get("market_scope") or {})
                ),
                "execution_profile": json.loads(
                    json.dumps(
                        raw_version.get("execution_profile")
                        or strategy_spec.get("execution_profile")
                        or {}
                    )
                ),
                "evaluation_plan": json.loads(
                    json.dumps(
                        raw_version.get("evaluation_plan")
                        or strategy_spec.get("evaluation_plan")
                        or {}
                    )
                ),
                "governance": json.loads(
                    json.dumps(raw_version.get("governance") or strategy_spec.get("governance") or {})
                ),
                "citation_bundle": cls._kw05_normalize_citation_bundle(
                    raw_version.get("citation_bundle") or strategy_spec.get("citation_bundle")
                ),
                "source_kind": (
                    raw_version.get("source_kind")
                    or provenance.get("source_kind")
                    or strategy_spec.get("source_kind")
                ),
                "persona_ids": list(raw_version.get("persona_ids") or strategy_spec.get("persona_ids") or []),
                "created_at": (
                    raw_version.get("created_at")
                    or provenance.get("created_at")
                    or strategy_spec.get("created_at")
                    or strategy_spec.get("updated_at")
                ),
                "created_by": raw_version.get("created_by") or provenance.get("created_by"),
                "last_modified_at": (
                    raw_version.get("updated_at")
                    or raw_version.get("last_modified_at")
                    or strategy_spec.get("updated_at")
                    or raw_version.get("created_at")
                    or provenance.get("created_at")
                ),
            }
            version["allowedActions"] = cls._kw05_allowed_actions(version)
            versions.append(version)

        return cls._kw05_sort_versions(versions)

    @classmethod
    def _kw05_current_version(
        cls,
        strategy_spec: Dict[str, Any],
        versions: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        current_spec_version_id = str(strategy_spec.get("current_spec_version_id") or "").strip()
        if current_spec_version_id:
            for version in versions:
                if str(version.get("spec_version_id") or "") == current_spec_version_id:
                    return version
        return versions[0] if versions else None

    @classmethod
    def _kw05_find_version(
        cls,
        strategy_spec: Dict[str, Any],
        selector: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        versions = cls._kw05_versions(strategy_spec)
        if not versions:
            return None
        normalized = str(selector or "current").strip()
        if normalized in {"", "current"}:
            return cls._kw05_current_version(strategy_spec, versions)
        for version in versions:
            if normalized in {
                str(version.get("spec_version_id") or ""),
                str(version.get("spec_version") or ""),
            }:
                return version
        return None

    @classmethod
    def _kw05_compare_section(
        cls,
        left: Dict[str, Any],
        right: Dict[str, Any],
        field: str,
        label: str,
        *,
        breaking: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if json.dumps(left.get(field), sort_keys=True) == json.dumps(right.get(field), sort_keys=True):
            return None
        summary = f"{label} changed from {left.get('spec_version')} to {right.get('spec_version')}."
        if field == "execution_profile":
            left_mode = (left.get(field) or {}).get("execution_mode_hint")
            right_mode = (right.get(field) or {}).get("execution_mode_hint")
            if left_mode != right_mode and left_mode and right_mode:
                summary = f"Execution mode hint changed from {left_mode} to {right_mode}."
        elif field == "evaluation_plan":
            summary = "Evaluation gates or metrics changed."
        elif field == "market_scope":
            summary = "Market scope changed."
        elif field == "governance":
            summary = "Governance policy or approval requirements changed."
        elif field == "hypothesis":
            summary = "Hypothesis changed."
        elif field == "objective":
            summary = "Objective changed."
        payload = {
            "section": field,
            "summary": summary,
        }
        if breaking:
            payload["severity"] = "breaking"
        return payload

    def list_strategy_specs(
        self,
        *,
        lifecycle_state: Optional[str] = None,
        source_kind: Optional[str] = None,
        persona_id: Optional[str] = None,
        include_retired: bool = False,
        include_fixture_pack: bool = False,
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for strategy_spec in self._strategy_specs.values():
            if not isinstance(strategy_spec, dict):
                continue
            versions = self._kw05_versions(strategy_spec)
            current_version = self._kw05_current_version(strategy_spec, versions)
            if current_version is None:
                continue

            current_lifecycle_state = str(current_version.get("lifecycle_state") or "")
            current_source_kind = str(current_version.get("source_kind") or "")
            persona_ids = {
                str(value)
                for value in (current_version.get("persona_ids") or [])
                if str(value).strip()
            }

            if lifecycle_state and lifecycle_state != "all" and current_lifecycle_state != lifecycle_state:
                continue
            if not include_retired and lifecycle_state in {None, "", "all"} and current_lifecycle_state == "retired":
                continue
            if source_kind and current_source_kind != source_kind:
                continue
            if persona_id and str(persona_id) not in persona_ids:
                continue

            strategy_id = str(current_version.get("strategy_id") or "")
            items.append(
                {
                    "object_ref": json.loads(json.dumps(current_version.get("object_ref") or {})),
                    "strategy_id": strategy_id,
                    "current_spec_version_id": current_version.get("spec_version_id"),
                    "current_spec_version": current_version.get("spec_version"),
                    "title": current_version.get("title"),
                    "lifecycle_state": current_lifecycle_state,
                    "source_kind": current_source_kind,
                    "hypothesis_excerpt": self._kw05_hypothesis_excerpt(current_version.get("hypothesis")),
                    "version_count": len(versions),
                    "last_modified_at": current_version.get("last_modified_at"),
                    "route_href": self._kw05_strategy_route_href(strategy_id),
                }
            )

        items.sort(
            key=lambda item: (
                _naive_utc(_parse_rfc3339(item.get("last_modified_at"))),
                str(item.get("strategy_id") or ""),
            ),
            reverse=True,
        )
        return items

    def get_strategy_spec(self, strategy_id: Optional[str]) -> Optional[Dict[str, Any]]:
        detail = self.get_strategy_spec_detail(strategy_id, version_selector="current")
        if not detail:
            return None
        return {
            "strategy_id": detail.get("strategy_id"),
            "title": detail.get("title"),
            "name": detail.get("title"),
            "spec_version_id": detail.get("spec_version_id"),
            "spec_version": detail.get("spec_version"),
            "lifecycle_state": detail.get("lifecycle_state"),
        }

    def get_strategy_spec_detail(
        self,
        strategy_id: Optional[str],
        *,
        version_selector: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not strategy_id:
            return None
        strategy_spec = self._strategy_specs.get(str(strategy_id))
        if not isinstance(strategy_spec, dict):
            return None
        version = self._kw05_find_version(strategy_spec, version_selector)
        return json.loads(json.dumps(version)) if version else None

    def list_strategy_spec_versions(self, strategy_id: Optional[str]) -> List[Dict[str, Any]]:
        if not strategy_id:
            return []
        strategy_spec = self._strategy_specs.get(str(strategy_id))
        if not isinstance(strategy_spec, dict):
            return []
        return [
            {
                "spec_version_id": version.get("spec_version_id"),
                "spec_version": version.get("spec_version"),
                "lifecycle_state": version.get("lifecycle_state"),
                "created_at": version.get("created_at"),
                "created_by": version.get("created_by"),
                "parent_spec_version_id": version.get("parent_spec_version_id"),
                "route_href": self._kw05_strategy_route_href(
                    str(version.get("strategy_id") or ""),
                    str(version.get("spec_version_id") or ""),
                ),
            }
            for version in self._kw05_versions(strategy_spec)
        ]

    def compare_strategy_spec_versions(
        self,
        strategy_id: Optional[str],
        *,
        left_selector: str,
        right_selector: str,
    ) -> Optional[Dict[str, Any]]:
        if not strategy_id:
            return None
        strategy_spec = self._strategy_specs.get(str(strategy_id))
        if not isinstance(strategy_spec, dict):
            return None
        left = self._kw05_find_version(strategy_spec, left_selector)
        right = self._kw05_find_version(strategy_spec, right_selector)
        if not left or not right:
            return None

        changed_sections = [
            item
            for item in [
                self._kw05_compare_section(left, right, "hypothesis", "Hypothesis"),
                self._kw05_compare_section(left, right, "objective", "Objective"),
                self._kw05_compare_section(left, right, "market_scope", "Market scope"),
                self._kw05_compare_section(left, right, "evaluation_plan", "Evaluation plan"),
                self._kw05_compare_section(left, right, "governance", "Governance"),
            ]
            if item is not None
        ]
        breaking_changes = [
            item
            for item in [
                self._kw05_compare_section(
                    left,
                    right,
                    "execution_profile",
                    "Execution profile",
                    breaking=True,
                )
            ]
            if item is not None
        ]

        evidence_refs = sorted(
            {
                str(item.get("ref_id") or "")
                for item in (
                    (left.get("citation_bundle") or {}).get("evidence_refs") or []
                ) + (
                    (right.get("citation_bundle") or {}).get("evidence_refs") or []
                )
                if isinstance(item, dict) and str(item.get("ref_id") or "").strip()
            }
        )

        return {
            "strategy_id": str(strategy_id),
            "left_spec_version_id": left.get("spec_version_id"),
            "right_spec_version_id": right.get("spec_version_id"),
            "changed_sections": changed_sections,
            "breaking_changes": breaking_changes,
            "evidence_refs": evidence_refs,
        }

    # -------------------------------------------------------------------------
    # Institutional Memory
    # -------------------------------------------------------------------------
    @staticmethod
    def _institutional_memory_scope(entry: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
        scope = entry.get("scope")
        if isinstance(scope, dict):
            return (
                scope.get("type") or scope.get("scope_type") or scope.get("value"),
                scope.get("filter") or scope.get("scope_filter") or scope.get("scope_ref"),
            )
        return scope, entry.get("scope_filter")

    @staticmethod
    def _institutional_memory_lifecycle(entry: Dict[str, Any]) -> Dict[str, Any]:
        lifecycle = entry.get("lifecycle") if isinstance(entry.get("lifecycle"), dict) else {}
        superseded_by = lifecycle.get("superseded_by") or entry.get("superseded_by")
        status = lifecycle.get("status") or lifecycle.get("state")
        if not status:
            if superseded_by:
                status = "superseded"
            elif entry.get("archived_at"):
                status = "archived"
            else:
                status = "active"
        return {"status": status, "superseded_by": superseded_by}

    @staticmethod
    def _institutional_memory_usage(entry: Dict[str, Any]) -> Dict[str, Any]:
        usage = entry.get("usage") if isinstance(entry.get("usage"), dict) else {}
        return {
            **usage,
            "reuse_count": usage.get("reuse_count") if "reuse_count" in usage else entry.get("reuse_count", 0),
        }

    @staticmethod
    def _institutional_memory_source_event(entry: Dict[str, Any]) -> Dict[str, Any]:
        source_event = entry.get("source_event") if isinstance(entry.get("source_event"), dict) else {}
        event_type = source_event.get("type") or entry.get("source_event_type")
        event_id = source_event.get("id") or entry.get("source_event_id")
        if not event_type and not event_id:
            return json.loads(json.dumps(source_event))
        projected = {**source_event, "type": event_type, "id": event_id}
        return json.loads(json.dumps({k: v for k, v in projected.items() if v is not None}))

    def _project_institutional_memory_summary(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        entry_id = str(entry.get("entry_id") or entry.get("id") or "")
        content = entry.get("content") if isinstance(entry.get("content"), dict) else {}
        scope, scope_filter = self._institutional_memory_scope(entry)
        lifecycle = self._institutional_memory_lifecycle(entry)
        usage = self._institutional_memory_usage(entry)
        return {
            "entry_id": entry_id,
            "knowledge_type": entry.get("knowledge_type"),
            "headline": content.get("headline"),
            "scope": scope,
            "scope_filter": scope_filter,
            "written_at": entry.get("written_at"),
            "write_authority": entry.get("write_authority"),
            "tags": list(content.get("tags") or []),
            "reuse_count": usage.get("reuse_count") or 0,
            "is_superseded": str(lifecycle.get("status") or "").strip().lower() == "superseded",
            "route_href": f"/knowledge/memory/{entry_id}",
        }

    def _project_institutional_memory_detail(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        scope, scope_filter = self._institutional_memory_scope(entry)
        lifecycle = self._institutional_memory_lifecycle(entry)
        usage = self._institutional_memory_usage(entry)
        return {
            "entry_id": entry.get("entry_id") or entry.get("id"),
            "knowledge_type": entry.get("knowledge_type"),
            "content": json.loads(json.dumps(entry.get("content") or {})),
            "source_event": self._institutional_memory_source_event(entry),
            "contributing_persona_ids": list(entry.get("contributing_persona_ids") or []),
            "written_at": entry.get("written_at"),
            "write_authority": entry.get("write_authority"),
            "scope": {"type": scope, "filter": scope_filter},
            "lifecycle": lifecycle,
            "usage": usage,
        }

    def list_institutional_memory_entries(self) -> List[Dict[str, Any]]:
        if self._institutional_memory_store is not None:
            entries = [
                entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
                for entry in self._institutional_memory_store.list(active_only=False)
            ]
        else:
            entries = []
        entries.sort(
            key=lambda entry: (
                _naive_utc(_parse_rfc3339(entry.get("written_at"))),
                int(self._institutional_memory_usage(entry).get("reuse_count") or 0),
            ),
            reverse=True,
        )
        return [self._project_institutional_memory_summary(entry) for entry in entries]

    def get_institutional_memory_entry(self, entry_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not entry_id:
            return None
        if self._institutional_memory_store is not None:
            raw = self._institutional_memory_store.get(entry_id)
            if raw is not None:
                entry = raw.to_dict() if hasattr(raw, "to_dict") else dict(raw)
                return self._project_institutional_memory_detail(entry)
        return None

    # -------------------------------------------------------------------------
    # Research Tickets (RW-01)
    # -------------------------------------------------------------------------
    @staticmethod
    def _research_ticket_allowed_actions(status: Optional[str]) -> Dict[str, bool]:
        normalized = str(status or "").strip().lower()
        if normalized == "archived":
            return {"canEdit": False, "canClose": False, "canArchive": False}
        if normalized == "closed":
            return {"canEdit": False, "canClose": False, "canArchive": True}
        if normalized in {"open", "in_progress"}:
            return {"canEdit": True, "canClose": True, "canArchive": False}
        return {"canEdit": False, "canClose": False, "canArchive": False}

    @classmethod
    def _project_research_ticket_summary(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": ticket.get("ticket_id"),
            "title": ticket.get("title"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "owner": ticket.get("owner"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "allowedActions": cls._research_ticket_allowed_actions(ticket.get("status")),
        }

    @classmethod
    def _project_research_ticket_detail(cls, ticket: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ticket_id": ticket.get("ticket_id"),
            "title": ticket.get("title"),
            "description": ticket.get("description"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "owner": ticket.get("owner"),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "closed_at": ticket.get("closed_at"),
            "archived_at": ticket.get("archived_at"),
            "lifecycle_history": json.loads(json.dumps(ticket.get("lifecycle_history") or [])),
            "linked_experiments": list(ticket.get("linked_experiments") or []),
            "linked_artifacts": list(ticket.get("linked_artifacts") or []),
            "allowedActions": cls._research_ticket_allowed_actions(ticket.get("status")),
        }

    def list_research_tickets(
        self,
        *,
        statuses: Optional[List[str]] = None,
        owner: Optional[str] = None,
        include_fixture_pack: bool = False,
    ) -> List[Dict[str, Any]]:
        tickets = list(self._tickets.values())
        if statuses:
            req_statuses = {str(s).strip().lower() for s in statuses if str(s).strip()}
            tickets = [t for t in tickets if str(t.get("status") or "").strip().lower() in req_statuses]
        if owner:
            req_owner = str(owner).strip()
            tickets = [t for t in tickets if str(t.get("owner") or "").strip() == req_owner]
        tickets.sort(
            key=lambda t: _naive_utc(_parse_rfc3339(t.get("updated_at")) or _parse_rfc3339(t.get("created_at"))),
            reverse=True,
        )
        return [self._project_research_ticket_summary(t) for t in tickets if isinstance(t, dict)]

    def get_research_ticket(self, ticket_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ticket_id:
            return None
        ticket = self._tickets.get(str(ticket_id))
        return self._project_research_ticket_detail(ticket) if isinstance(ticket, dict) else None

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
        timestamp = created_at or _utc_now_rfc3339()
        ticket_id = f"rt-{timestamp[:10].replace('-', '')}-{len(self._tickets) + 1:03d}"
        while ticket_id in self._tickets:
            ticket_id = f"rt-{timestamp[:10].replace('-', '')}-{len(self._tickets) + 2:03d}"

        ticket = {
            "ticket_id": ticket_id,
            "title": title,
            "description": description,
            "status": "open",
            "priority": priority,
            "owner": owner,
            "created_at": timestamp,
            "updated_at": timestamp,
            "closed_at": None,
            "archived_at": None,
            "lifecycle_history": [
                {
                    "from_status": None,
                    "to_status": "open",
                    "transitioned_at": timestamp,
                    "transitioned_by": actor_id,
                }
            ],
            "linked_experiments": [],
            "linked_artifacts": [],
        }
        self._tickets[ticket_id] = ticket
        return self._project_research_ticket_detail(ticket)

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        ticket = self._tickets.get(str(ticket_id))
        if ticket is None or not isinstance(ticket, dict):
            return None

        timestamp = updated_at or _utc_now_rfc3339()
        editable = {"title", "description", "priority", "owner"}
        for f in editable:
            if f in patch:
                ticket[f] = patch[f]

        next_status = patch.get("status")
        if next_status is not None and next_status != ticket.get("status"):
            prev_status = ticket.get("status")
            ticket["status"] = next_status
            if next_status == "closed":
                ticket["closed_at"] = timestamp
                ticket["archived_at"] = None
            elif next_status == "archived":
                ticket["archived_at"] = timestamp
                if ticket.get("closed_at") is None:
                    ticket["closed_at"] = timestamp
            else:
                if next_status in {"open", "in_progress"}:
                    ticket["closed_at"] = None
                if next_status != "archived":
                    ticket["archived_at"] = None
            ticket.setdefault("lifecycle_history", []).append(
                {
                    "from_status": prev_status,
                    "to_status": next_status,
                    "transitioned_at": timestamp,
                    "transitioned_by": actor_id,
                }
            )
        ticket["updated_at"] = timestamp
        return self._project_research_ticket_detail(ticket)

    # -------------------------------------------------------------------------
    # Research Analyses (RW-03)
    # -------------------------------------------------------------------------
    @staticmethod
    def _project_research_analysis_summary(analysis: Dict[str, Any]) -> Dict[str, Any]:
        metric_groups = list(analysis.get("metric_groups") or [])
        return {
            "analysis_id": analysis.get("analysis_id"),
            "ticket_id": analysis.get("ticket_id"),
            "experiment_id": analysis.get("experiment_id"),
            "status": analysis.get("status"),
            "run_at": analysis.get("run_at"),
            "summary": {
                "headline": ((analysis.get("summary") or {}).get("headline")),
                "verdict": ((analysis.get("summary") or {}).get("verdict")),
            },
            "metric_group_refs": [
                str(group.get("group_key") or "")
                for group in metric_groups
                if str(group.get("group_key") or "").strip()
            ],
        }

    @staticmethod
    def _project_research_analysis_detail(analysis: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "analysis_id": analysis.get("analysis_id"),
            "ticket_id": analysis.get("ticket_id"),
            "experiment_id": analysis.get("experiment_id"),
            "status": analysis.get("status"),
            "run_at": analysis.get("run_at"),
            "completed_at": analysis.get("completed_at"),
            "summary": json.loads(json.dumps(analysis.get("summary") or {})),
            "metric_groups": json.loads(json.dumps(analysis.get("metric_groups") or [])),
            "comparative_summary": json.loads(json.dumps(analysis.get("comparative_summary") or {})),
        }

    @staticmethod
    def _date_range_cutoff_days(date_range: Optional[str]) -> Optional[int]:
        mapping = {"24h": 1, "7d": 7, "30d": 30, "90d": 90}
        return mapping.get(str(date_range or "").strip().lower())

    def list_research_analyses(
        self,
        *,
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        statuses: Optional[List[str]] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        analyses = list(self._analyses.values())
        if ticket_id:
            analyses = [a for a in analyses if str(a.get("ticket_id") or "") == str(ticket_id)]
        if experiment_id:
            analyses = [a for a in analyses if str(a.get("experiment_id") or "") == str(experiment_id)]
        if statuses:
            req_statuses = {str(s).strip().lower() for s in statuses if str(s).strip()}
            analyses = [a for a in analyses if str(a.get("status") or "").strip().lower() in req_statuses]
        cutoff_days = self._date_range_cutoff_days(date_range)
        if cutoff_days is not None:
            ref_now = datetime.now(timezone.utc).replace(tzinfo=None)
            analyses = [
                a for a in analyses
                if (parsed := _parse_rfc3339(a.get("run_at"))) is not None
                and (ref_now - _naive_utc(parsed)).days < cutoff_days
            ]
        analyses.sort(
            key=lambda a: _naive_utc(_parse_rfc3339(a.get("run_at"))),
            reverse=True,
        )
        return [self._project_research_analysis_summary(a) for a in analyses if isinstance(a, dict)]

    def get_research_analysis(self, analysis_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not analysis_id:
            return None
        analysis = self._analyses.get(str(analysis_id))
        return self._project_research_analysis_detail(analysis) if isinstance(analysis, dict) else None

    # -------------------------------------------------------------------------
    # Research Experiments (RW-04) - NO process-local monkey patch overlays
    # -------------------------------------------------------------------------
    @classmethod
    def _rw04_can_cancel(cls, status: Optional[str]) -> bool:
        return str(status or "").strip().lower() in cls._RW04_CANCELABLE_STATUSES

    @classmethod
    def _project_research_experiment_summary(cls, exp: Dict[str, Any]) -> Dict[str, Any]:
        status = str(exp.get("status") or "")
        strategy_selector = exp.get("strategy_selector") or {}
        strategy_id = (
            exp.get("linked_strategy_id")
            or exp.get("strategy_id")
            or strategy_selector.get("strategy_id")
        )
        run_config = exp.get("run_config") or {}
        return {
            "experiment_id": exp.get("experiment_id"),
            "ticket_id": exp.get("ticket_id"),
            "experiment_name": exp.get("experiment_name"),
            "status": status,
            "stage": exp.get("stage"),
            "framework": exp.get("framework") or run_config.get("backend"),
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "strategy_id": strategy_id,
            "linked_strategy_id": strategy_id,
            "dataset_ref": exp.get("dataset_ref") or run_config.get("dataset_ref"),
            "dataset_manifest_id": exp.get("dataset_manifest_id") or run_config.get("dataset_manifest_id"),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "registry_admission_status": exp.get("registry_admission_status"),
            "can_deploy": bool(exp.get("can_deploy", True)),
            "allowedActions": {"canCancel": cls._rw04_can_cancel(status)},
        }

    @classmethod
    def _project_research_experiment_detail(cls, exp: Dict[str, Any]) -> Dict[str, Any]:
        status = str(exp.get("status") or "")
        failure = exp.get("failure") or {}
        progress = exp.get("progress") or {}
        strategy_selector = exp.get("strategy_selector") or {}
        run_config = exp.get("run_config") or {}
        time_range = run_config.get("time_range") or {}
        launch_context = exp.get("launch_context") or {}
        return {
            "experiment_id": exp.get("experiment_id"),
            "ticket_id": exp.get("ticket_id"),
            "experiment_name": exp.get("experiment_name"),
            "status": status,
            "stage": exp.get("stage"),
            "queued_at": exp.get("queued_at"),
            "started_at": exp.get("started_at"),
            "completed_at": exp.get("completed_at"),
            "progress": {
                "percent": progress.get("percent"),
                "phase": progress.get("phase"),
                "message": progress.get("message"),
            },
            "strategy_selector": {
                "strategy_id": strategy_selector.get("strategy_id"),
                "variant_id": strategy_selector.get("variant_id"),
            },
            "parameter_set": json.loads(json.dumps(exp.get("parameter_set") or {})),
            "run_config": {
                "backend": run_config.get("backend"),
                "dataset_ref": run_config.get("dataset_ref"),
                "dataset_manifest_id": run_config.get("dataset_manifest_id"),
                "time_range": {
                    "start_at": time_range.get("start_at"),
                    "end_at": time_range.get("end_at"),
                },
                "execution_mode": run_config.get("execution_mode"),
                "priority": run_config.get("priority"),
                "requested_by": run_config.get("requested_by"),
            },
            "launch_context": {
                "analysis_refs": (
                    list(launch_context["analysis_refs"])
                    if isinstance(launch_context.get("analysis_refs"), list)
                    else None
                ),
            },
            "validation_warnings": json.loads(json.dumps(exp.get("validation_warnings") or [])),
            "artifact_ids": list(exp.get("artifact_ids") or []),
            "artifact_refs": json.loads(json.dumps(exp.get("artifact_refs") or [])),
            "framework": exp.get("framework") or run_config.get("backend"),
            "dataset_ref": exp.get("dataset_ref") or run_config.get("dataset_ref"),
            "dataset_manifest_id": exp.get("dataset_manifest_id") or run_config.get("dataset_manifest_id"),
            "research_linkage": json.loads(json.dumps(exp.get("research_linkage") or {})),
            "evidence_refs": json.loads(json.dumps(exp.get("evidence_refs") or [])),
            "safety_assertions": json.loads(json.dumps(exp.get("safety_assertions") or {})),
            "registry_admission_status": exp.get("registry_admission_status"),
            "can_deploy": bool(exp.get("can_deploy", True)),
            "deployment_stage": exp.get("deployment_stage"),
            "failure": {
                "reason_code": failure.get("reason_code"),
                "message": failure.get("message"),
            },
            "allowedActions": {"canCancel": cls._rw04_can_cancel(status)},
        }

    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        experiments = list(self._experiments.values())
        if ticket_id:
            experiments = [e for e in experiments if str(e.get("ticket_id") or "") == str(ticket_id)]
        if status:
            req_status = str(status).strip().lower()
            experiments = [e for e in experiments if str(e.get("status") or "").strip().lower() == req_status]
        experiments.sort(
            key=lambda e: _naive_utc(_parse_rfc3339(e.get("queued_at"))),
            reverse=True,
        )
        return [self._project_research_experiment_summary(e) for e in experiments if isinstance(e, dict)]

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not experiment_id:
            return None
        exp = self._experiments.get(str(experiment_id))
        return self._project_research_experiment_detail(exp) if isinstance(exp, dict) else None

    def create_research_experiment(
        self,
        *,
        ticket_id: str,
        experiment_name: str,
        strategy_selector: Dict[str, Any],
        parameter_set: Dict[str, Any],
        run_config: Dict[str, Any],
        launch_context: Dict[str, Any],
        queued_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = queued_at or _utc_now_rfc3339()
        date_part = timestamp[:10].replace("-", "")
        experiment_id = f"exp-{date_part}-{len(self._experiments) + 1:03d}"
        while experiment_id in self._experiments:
            experiment_id = f"exp-{date_part}-{len(self._experiments) + 2:03d}"

        record: Dict[str, Any] = {
            "experiment_id": experiment_id,
            "ticket_id": ticket_id,
            "experiment_name": experiment_name,
            "status": "queued",
            "queued_at": timestamp,
            "started_at": None,
            "completed_at": None,
            "progress": {"percent": None, "phase": None, "message": None},
            "strategy_selector": json.loads(json.dumps(strategy_selector)),
            "parameter_set": json.loads(json.dumps(parameter_set)),
            "run_config": json.loads(json.dumps(run_config)),
            "launch_context": json.loads(json.dumps(launch_context)),
            "validation_warnings": [],
            "artifact_ids": [],
            "failure": {"reason_code": None, "message": None},
            "allowedActions": {"canCancel": True},
        }
        self._experiments[experiment_id] = record
        return self._project_research_experiment_detail(record)

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        record = self._experiments.get(str(experiment_id))
        if record is None or not isinstance(record, dict):
            return None
        status = str(record.get("status") or "").strip().lower()
        if status not in self._RW04_CANCELABLE_STATUSES:
            return None
        record["status"] = "canceled"
        record["completed_at"] = completed_at or _utc_now_rfc3339()
        record["allowedActions"] = {"canCancel": False}
        return self._project_research_experiment_detail(record)

    # -------------------------------------------------------------------------
    # Research Artifacts (RW-05)
    # -------------------------------------------------------------------------
    @classmethod
    def _rw05_can_compare(cls, status: Optional[str]) -> bool:
        return str(status or "").strip().lower() in cls._RW05_COMPARABLE_STATUSES

    @classmethod
    def _rw05_metric_summary(cls, artifact: Dict[str, Any]) -> Dict[str, Any]:
        metrics = artifact.get("metrics") or {}
        return {
            "sharpe_ratio": metrics.get("sharpe_ratio"),
            "max_drawdown": metrics.get("max_drawdown"),
            "annualized_return": metrics.get("annualized_return"),
        }

    def _rw05_lineage_versions(self, lineage_id: Optional[str]) -> List[Dict[str, Any]]:
        artifacts = list(self._artifacts.values())
        chain = [a for a in artifacts if str(a.get("lineage_id") or "") == str(lineage_id or "")]
        chain.sort(key=lambda a: (int(a.get("version") or 0), str(a.get("created_at") or "")))
        return chain

    def _rw05_is_current_version(self, artifact: Dict[str, Any]) -> bool:
        lineage_id = artifact.get("lineage_id")
        if not lineage_id:
            return True
        chain = self._rw05_lineage_versions(lineage_id)
        return bool(chain and chain[-1].get("artifact_id") == artifact.get("artifact_id"))

    def _project_research_artifact_summary(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        status = str(artifact.get("status") or "")
        return {
            "artifact_id": artifact.get("artifact_id"),
            "name": artifact.get("name"),
            "artifact_type": artifact.get("artifact_type"),
            "status": status,
            "version": artifact.get("version"),
            "lineage_id": artifact.get("lineage_id"),
            "is_current_version": self._rw05_is_current_version(artifact),
            "produced_by_experiment_id": artifact.get("produced_by_experiment_id"),
            "created_at": artifact.get("created_at"),
            "author": artifact.get("author"),
            "tags": list(artifact.get("tags") or []),
            "metric_summary": self._rw05_metric_summary(artifact),
            "allowedActions": {"canCompare": self._rw05_can_compare(status)},
        }

    def _project_research_artifact_detail(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        status = str(artifact.get("status") or "")
        lineage_id = artifact.get("lineage_id")
        lineage_chain = self._rw05_lineage_versions(lineage_id) if lineage_id else []
        versions = [
            {
                "artifact_id": v.get("artifact_id"),
                "version": v.get("version"),
                "status": v.get("status"),
                "created_at": v.get("created_at"),
                "metrics": self._rw05_metric_summary(v),
            }
            for v in lineage_chain
        ]
        return {
            "artifact_id": artifact.get("artifact_id"),
            "name": artifact.get("name"),
            "artifact_type": artifact.get("artifact_type"),
            "status": status,
            "version": artifact.get("version"),
            "lineage_id": lineage_id,
            "is_current_version": self._rw05_is_current_version(artifact),
            "produced_by_experiment_id": artifact.get("produced_by_experiment_id"),
            "created_at": artifact.get("created_at"),
            "author": artifact.get("author"),
            "tags": list(artifact.get("tags") or []),
            "metrics": json.loads(json.dumps(artifact.get("metrics") or {})),
            "parameters": json.loads(json.dumps(artifact.get("parameters") or {})),
            "lineage": {
                "lineage_id": lineage_id,
                "version_count": len(versions),
                "versions": versions,
            },
            "research_linkage": json.loads(json.dumps(artifact.get("research_linkage") or {})),
            "evidence_refs": json.loads(json.dumps(artifact.get("evidence_refs") or [])),
            "allowedActions": {"canCompare": self._rw05_can_compare(status)},
        }

    def list_research_artifacts(
        self,
        *,
        artifact_type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None,
        author: Optional[str] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        artifacts = list(self._artifacts.values())
        if artifact_type:
            artifacts = [a for a in artifacts if str(a.get("artifact_type") or "").lower() == artifact_type.lower()]
        if status:
            artifacts = [a for a in artifacts if str(a.get("status") or "").lower() == status.lower()]
        if author:
            artifacts = [a for a in artifacts if str(a.get("author") or "") == author]
        if tags:
            tag_set = {str(t).strip().lower() for t in tags if str(t).strip()}
            artifacts = [
                a for a in artifacts
                if tag_set.intersection({str(t).strip().lower() for t in (a.get("tags") or [])})
            ]
        cutoff_days = self._date_range_cutoff_days(date_range)
        if cutoff_days is not None:
            ref_now = datetime.now(timezone.utc).replace(tzinfo=None)
            artifacts = [
                a for a in artifacts
                if (parsed := _parse_rfc3339(a.get("created_at"))) is not None
                and (ref_now - _naive_utc(parsed)).days < cutoff_days
            ]
        artifacts.sort(
            key=lambda a: _naive_utc(_parse_rfc3339(a.get("created_at"))),
            reverse=True,
        )
        return [self._project_research_artifact_summary(a) for a in artifacts if isinstance(a, dict)]

    def get_research_artifact(self, artifact_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not artifact_id:
            return None
        artifact = self._artifacts.get(str(artifact_id))
        return self._project_research_artifact_detail(artifact) if isinstance(artifact, dict) else None

    @staticmethod
    def _rw05_field_value(artifact: Dict[str, Any], field_key: str) -> Any:
        parts = field_key.split(".")
        val: Any = artifact
        for p in parts:
            if not isinstance(val, dict):
                return None
            val = val.get(p)
        return val

    @classmethod
    def _rw05_delta_display(cls, val_a: Any, val_b: Any, direction: str) -> Tuple[Optional[float], Optional[str], str]:
        if not isinstance(val_a, (int, float)) or not isinstance(val_b, (int, float)):
            return None, None, "neutral"
        delta = round(float(val_b) - float(val_a), 4)
        pct_delta = round((delta / abs(float(val_a))) * 100, 2) if val_a != 0 else None
        polarity = "neutral"
        if direction == "higher_is_better":
            polarity = "better" if delta > 0 else ("worse" if delta < 0 else "neutral")
        elif direction == "lower_is_better":
            polarity = "better" if delta < 0 else ("worse" if delta > 0 else "neutral")
        return delta, (f"{pct_delta:+.2f}%" if pct_delta is not None else None), polarity

    def compare_research_artifacts(self, artifact_ids: List[str]) -> Dict[str, Any]:
        artifacts = [self.get_research_artifact(aid) for aid in artifact_ids if aid]
        artifacts = [a for a in artifacts if a is not None]
        if len(artifacts) < 2:
            return {"artifacts": artifacts, "comparisons": []}

        art_a, art_b = artifacts[0], artifacts[1]
        comparisons: List[Dict[str, Any]] = []
        for field_key, label, category, direction in self._RW05_FIELD_SPECS:
            va = self._rw05_field_value(art_a, field_key)
            vb = self._rw05_field_value(art_b, field_key)
            delta, pct_str, polarity = self._rw05_delta_display(va, vb, direction)
            comparisons.append({
                "field_key": field_key,
                "label": label,
                "category": category,
                "value_a": va,
                "value_b": vb,
                "delta": delta,
                "percent_delta": pct_str,
                "polarity": polarity,
            })
        return {"artifacts": [art_a, art_b], "comparisons": comparisons}

    # -------------------------------------------------------------------------
    # Search & Governed Search (RW-02)
    # -------------------------------------------------------------------------
    def get_research_search_index(self) -> Optional[Dict[str, Any]]:
        docs = self._search_documents
        if not docs:
            return None
        indexed_match_types = sorted(
            {str(d.get("match_type") or "").strip() for d in docs if str(d.get("match_type") or "").strip()}
        )
        return {
            "adapter_id": "rw02-search-index",
            "snapshot_at": _utc_now_rfc3339(),
            "adapter_state": "fresh",
            "indexed_match_types": indexed_match_types,
            "source_watermarks": {"tickets": None, "experiments": None, "artifacts": None},
        }

    def get_last_governed_search_refs(self) -> Dict[str, Dict[str, Any]]:
        return json.loads(json.dumps(self._last_governed_search_refs))

    def list_research_search_results(
        self,
        *,
        query: str,
        match_type: str = "all",
        status: Optional[str] = None,
        date_range: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        docs = self._search_documents
        if not docs:
            self._last_governed_search_refs = {}
            return []

        ticket_statuses = {
            str(t.get("ticket_id") or ""): str(t.get("status") or "")
            for t in self._tickets.values()
            if isinstance(t, dict) and t.get("ticket_id")
        }
        cutoff_days = self._date_range_cutoff_days(date_range)
        ref_now = datetime.now(timezone.utc).replace(tzinfo=None)

        eligible: List[Dict[str, Any]] = []
        for doc in docs:
            d_type = str(doc.get("match_type") or "").strip().lower()
            if d_type not in {"ticket", "experiment", "artifact"}:
                continue
            if match_type != "all" and d_type != match_type:
                continue

            linked_ticket_id = str(doc.get("linked_ticket_id") or "").strip()
            ticket_status = ticket_statuses.get(linked_ticket_id) or str(doc.get("linked_ticket_status") or "")
            if status and ticket_status.lower() != status.lower():
                continue

            updated_at = _parse_rfc3339(doc.get("updated_at") or doc.get("created_at"))
            if cutoff_days is not None:
                if updated_at is None:
                    continue
                if (ref_now - _naive_utc(updated_at)).days >= cutoff_days:
                    continue
            eligible.append(doc)

        # In-memory query match if no external service
        q_lower = query.lower()
        matched: List[Dict[str, Any]] = []
        for doc in eligible:
            text = f"{doc.get('title', '')} {doc.get('excerpt', '')} {doc.get('search_text', '')}".lower()
            if not query or q_lower in text:
                res_id = str(doc.get("result_id") or "")
                d_type = str(doc.get("match_type") or "document").strip().lower()
                links = doc.get("links") if isinstance(doc.get("links"), dict) else {}
                matched.append({
                    "result_id": res_id,
                    "match_type": d_type,
                    "title": str(doc.get("title") or ""),
                    "excerpt": str(doc.get("excerpt") or ""),
                    "linked_ticket_id": str(doc.get("linked_ticket_id") or ""),
                    "relevance_score": 1.0 if q_lower in text else 0.5,
                    "links": {
                        "result_detail": str(
                            links.get("result_detail")
                            or (f"/research/tickets/{res_id}" if d_type == "ticket" else f"/research/{d_type}s/{res_id}")
                        ),
                        "linked_ticket_detail": str(
                            links.get("linked_ticket_detail") or f"/research/tickets/{doc.get('linked_ticket_id')}"
                        ),
                    },
                })
        return matched

    def get_search_ops_snapshot(
        self,
        *,
        pipeline_run_limit: int = 50,
    ) -> Dict[str, Any]:
        if not self._search_service_url and not self._search_gateway:
            return {
                "source": "missing",
                "index_freshness": None,
                "pipeline_runs": [],
                "pipeline_retention_runs": None,
                "materialized_index": None,
                "summary": {
                    "pipeline_run_count": 0,
                    "pipeline_retention_runs": None,
                    "freshness_ok": False,
                    "freshness_status": "unknown",
                },
            }

        # Query search service if configured
        avail_fresh, payload_fresh = self._http_get(self._search_service_url or "", "/api/search/index/freshness")
        freshness = json.loads(json.dumps(payload_fresh)) if avail_fresh and isinstance(payload_fresh, dict) else None

        runs_path = f"/api/search/index/pipeline-runs?limit={pipeline_run_limit}"
        avail_pipe, payload_pipe = self._http_get(self._search_service_url or "", runs_path)
        pipeline_runs = list(payload_pipe.get("runs") or []) if avail_pipe and isinstance(payload_pipe, dict) else []
        pipeline_total = int(payload_pipe.get("total") or len(pipeline_runs)) if avail_pipe and isinstance(payload_pipe, dict) else 0

        avail_mat, payload_mat = self._http_get(self._search_service_url or "", "/api/search/index/materialize")
        materialized = json.loads(json.dumps(payload_mat)) if avail_mat and isinstance(payload_mat, dict) else None

        service_available = any([avail_fresh, avail_pipe, avail_mat])
        freshness_ok = bool(
            freshness
            and (
                freshness.get("within_sla") is True
                or freshness.get("is_fresh") is True
                or str(freshness.get("status") or "").lower() == "fresh"
            )
        )
        return {
            "source": "service_client" if service_available else "unavailable",
            "index_freshness": freshness,
            "pipeline_runs": pipeline_runs,
            "pipeline_run_total": pipeline_total,
            "pipeline_retention_runs": None,
            "materialized_index": materialized,
            "summary": {
                "pipeline_run_count": pipeline_total,
                "pipeline_retention_runs": None,
                "freshness_ok": freshness_ok,
                "freshness_status": "ok" if freshness_ok else ("stale" if freshness else "unknown"),
            },
        }

    # -------------------------------------------------------------------------
    # Source Ingestion & Ops (SVC-SOURCE-SEARCH-OPS-BFF)
    # -------------------------------------------------------------------------
    def get_source_connector_registry(self) -> Dict[str, Any]:
        if not self._source_ingest_service_url and not self._data_source_registry and not self._source_management_client:
            return {
                "source": "missing",
                "connectors": [],
                "provider_examples": [],
                "financial_data_source_catalog": None,
                "active_universe_policy": None,
            }

        if self._source_ingest_service_url:
            avail, payload = self._http_get(self._source_ingest_service_url, "/api/source-ingest/registry")
            if avail and isinstance(payload, dict):
                return {
                    "source": "service_client",
                    "schema_version": payload.get("schema_version"),
                    "connectors": list(payload.get("connectors") or []),
                    "provider_examples": list(payload.get("provider_examples") or []),
                    "policy_registry": payload.get("policy_registry"),
                    "financial_data_source_catalog": payload.get("financial_data_source_catalog"),
                    "active_universe_policy": payload.get("active_universe_policy"),
                }
            return {
                "source": "unavailable",
                "connectors": [],
                "provider_examples": [],
                "financial_data_source_catalog": None,
                "active_universe_policy": None,
            }

        return {
            "source": "service_client",
            "connectors": [],
            "provider_examples": [],
            "financial_data_source_catalog": None,
            "active_universe_policy": None,
        }

    def get_source_change_proposals(
        self,
        *,
        status: Optional[str] = None,
        proposal_type: Optional[str] = None,
        source_kind: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self._source_ingest_service_url:
            return {"source": "missing", "proposals": []}
        path = "/api/source-change-proposals"
        params: list[str] = []
        if status:
            params.append(f"status={status}")
        if proposal_type:
            params.append(f"proposal_type={proposal_type}")
        if source_kind:
            params.append(f"source_kind={source_kind}")
        if params:
            path = path + "?" + "&".join(params)
        avail, payload = self._http_get(self._source_ingest_service_url, path)
        if avail and isinstance(payload, dict):
            return {"source": "service_client", "proposals": list(payload.get("proposals") or [])}
        return {"source": "unavailable", "proposals": []}

    def get_source_ops_snapshot(
        self,
        *,
        crawl_run_limit: int = 50,
        dlq_status: Optional[str] = None,
        frontier_status: Optional[str] = None,
        audit_limit: int = 20,
    ) -> Dict[str, Any]:
        if not self._source_ingest_service_url:
            return {
                "source": "missing",
                "connector_health": [],
                "policy_registry": None,
                "financial_data_source_catalog": None,
                "active_universe_policy": None,
                "crawl_runs": [],
                "dlq": [],
                "frontier": [],
                "audit": [],
                "summary": {
                    "connector_count": 0,
                    "recent_run_count": 0,
                    "dlq_count": 0,
                    "frontier_count": 0,
                    "audit_count": 0,
                    "scheduled_connector_count": 0,
                    "due_connector_count": 0,
                    "degraded_connector_count": 0,
                    "connector_policy_count": 0,
                    "external_allowlist_policy_count": 0,
                    "pit_policy_count": 0,
                    "scheduled_policy_count": 0,
                    "financial_data_source_count": 0,
                    "financial_data_source_template_count": 0,
                    "active_universe_rule_count": 0,
                    "search_refresh_notification_configured": False,
                },
            }

        avail_reg, payload_reg = self._http_get(self._source_ingest_service_url, "/api/source-ingest/registry")
        connectors = list(payload_reg.get("connectors") or []) if avail_reg and isinstance(payload_reg, dict) else []
        avail_runs, payload_runs = self._http_get(self._source_ingest_service_url, "/api/source-ingest/jobs")
        crawl_runs = list(payload_runs.get("runs") or [])[-crawl_run_limit:] if avail_runs and isinstance(payload_runs, dict) else []
        avail_dlq, payload_dlq = self._http_get(self._source_ingest_service_url, "/api/source-ingest/dlq")
        dlq = list(payload_dlq.get("entries") or []) if avail_dlq and isinstance(payload_dlq, dict) else []
        avail_fr, payload_fr = self._http_get(self._source_ingest_service_url, "/api/source-ingest/frontier")
        frontier = list(payload_fr.get("frontier") or []) if avail_fr and isinstance(payload_fr, dict) else []
        avail_audit, payload_audit = self._http_get(self._source_ingest_service_url, "/api/source-ingest/audit")
        audit = list(payload_audit.get("actions") or [])[-audit_limit:] if avail_audit and isinstance(payload_audit, dict) else []

        service_avail = any([avail_reg, avail_runs, avail_dlq, avail_fr, avail_audit])
        return {
            "source": "service_client" if service_avail else "unavailable",
            "connector_health": connectors,
            "policy_registry": payload_reg.get("policy_registry") if avail_reg and isinstance(payload_reg, dict) else None,
            "financial_data_source_catalog": payload_reg.get("financial_data_source_catalog") if avail_reg and isinstance(payload_reg, dict) else None,
            "active_universe_policy": payload_reg.get("active_universe_policy") if avail_reg and isinstance(payload_reg, dict) else None,
            "crawl_runs": crawl_runs,
            "dlq": dlq,
            "frontier": frontier,
            "audit": audit,
            "summary": {
                "connector_count": len(connectors),
                "recent_run_count": len(crawl_runs),
                "dlq_count": len(dlq),
                "frontier_count": len(frontier),
                "audit_count": len(audit),
                "scheduled_connector_count": 0,
                "due_connector_count": 0,
                "degraded_connector_count": 0,
                "connector_policy_count": 0,
                "external_allowlist_policy_count": 0,
                "pit_policy_count": 0,
                "scheduled_policy_count": 0,
                "financial_data_source_count": 0,
                "financial_data_source_template_count": 0,
                "active_universe_rule_count": 0,
                "search_refresh_notification_configured": False,
            },
        }

    def get_source_health_usage_snapshot(self) -> Dict[str, Any]:
        if not self._source_ingest_service_url:
            return {
                "source": "missing",
                "source_count": 0,
                "sources": [],
                "recommendation_summary": {},
            }
        avail, payload = self._http_get(self._source_ingest_service_url, "/api/source-ingest/health-usage-snapshot")
        if avail and isinstance(payload, dict):
            sources = list(payload.get("sources") or [])
            return {
                "source": "service_client",
                "source_count": len(sources),
                "sources": sources,
                "recommendation_summary": dict(payload.get("recommendation_summary") or {}),
            }
        return {
            "source": "unavailable",
            "source_count": 0,
            "sources": [],
            "recommendation_summary": {},
        }
