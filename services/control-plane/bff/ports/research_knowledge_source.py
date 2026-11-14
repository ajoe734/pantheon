"""Typed domain ports and read models for Research, Knowledge, Memory, Search, and Source.

This module provides the `ResearchKnowledgeSourcePort` protocol and its default
implementation `DefaultResearchKnowledgeSourcePort`. It connects BFF read routes
directly to canonical domain stores and typed service clients, eliminating process-local
monkey patches, experiment overlays, and fixture-pack loading.
"""

from __future__ import annotations

from urllib.parse import quote

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

log = logging.getLogger(__name__)


class _EvidenceOwnerReadError(RuntimeError):
    """The evidence owner read failed or timed out."""


class ResearchWriteOwnerUnavailableError(RuntimeError):
    """Raised when the Postgres-backed ResearchWriteOwner is not configured.

    Under BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001, experiment reads and
    mutations delegate exclusively to ``services.research.write_owner.ResearchWriteOwner``.
    There is no in-memory fallback: if the owner cannot be built (missing
    ``DATABASE_URL``/``RESEARCH_STORE_DSN``) or a DB round-trip fails, callers
    must surface a 500/503, never a silent empty/fake success.
    """


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


EVIDENCE_OWNER_PAGE_SIZE = 200
EVIDENCE_OWNER_READ_TIMEOUT_SECONDS = 4.0


def evidence_owner_read_timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_EVIDENCE_OWNER_READ_TIMEOUT_SECONDS", "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return EVIDENCE_OWNER_READ_TIMEOUT_SECONDS


@dataclass
class EvidenceReadResult:
    """Outcome of ONE evidence read. Availability belongs to the read that
    produced the rows, never to shared instance state."""

    items: List[Dict[str, Any]]
    total: Optional[int]
    status: str  # "ok" | "unavailable"
    source: str  # "typed_store" | "service_client" | "unavailable" | "missing"
    next_page_token: Optional[str] = None
    summary: Optional[Dict[str, Any]] = None
    summary_unavailable: bool = False

    @property
    def available(self) -> bool:
        return self.status == "ok"


def _normalize_owner_evidence_summary(
    raw_summary: Any,
    *,
    default_total: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_summary, Mapping):
        return None
    verified_raw = (
        raw_summary.get("verified_count")
        if raw_summary.get("verified_count") is not None
        else raw_summary.get("verified_evidence")
    )
    if not isinstance(verified_raw, int) or isinstance(verified_raw, bool) or verified_raw < 0:
        return None
    buckets: Dict[str, Dict[str, int]] = {}
    for key in ("by_source_type", "by_link_type", "by_credibility_tier"):
        raw_bucket = raw_summary.get(key)
        if not isinstance(raw_bucket, Mapping):
            return None
        clean_bucket: Dict[str, int] = {}
        for k, v in raw_bucket.items():
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                return None
            clean_key = str(k or "").strip() or "unknown"
            clean_bucket[clean_key] = clean_bucket.get(clean_key, 0) + int(v)
        buckets[key] = clean_bucket
    total_raw = raw_summary.get("total")
    total_val = (
        int(total_raw)
        if isinstance(total_raw, int) and not isinstance(total_raw, bool) and total_raw >= 0
        else default_total
    )
    return {
        "total": total_val,
        "verified_count": int(verified_raw),
        "verified_evidence": int(verified_raw),
        "by_source_type": buckets["by_source_type"],
        "by_link_type": buckets["by_link_type"],
        "by_credibility_tier": buckets["by_credibility_tier"],
    }


@dataclass
class ArtifactOwnerReadResult:
    """Outcome of a single artifact owner read (registry execution bundles or research run outputs)."""

    items: List[Dict[str, Any]]
    status: str  # "ok" | "failed" | "unconfigured"
    source: str  # "typed_store" | "service_client" | "unavailable" | "missing"
    error: Optional[str] = None


def normalize_registry_catalog_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Project a Registry strategy-artifact execution bundle row into the /bff/artifacts catalog shape."""
    entry = row.get("entry") if isinstance(row.get("entry"), Mapping) else row
    eval_summary = entry.get("evaluation_summary") if isinstance(entry.get("evaluation_summary"), Mapping) else {}
    reg_id = entry.get("registry_id") if entry.get("registry_id") is not None else entry.get("id")
    strat_id = entry.get("strategy_id") if entry.get("strategy_id") is not None else entry.get("name")
    version = entry.get("version")
    state = entry.get("artifact_state") if "artifact_state" in entry else entry.get("state")
    risk = entry.get("risk_level") if "risk_level" in entry else eval_summary.get("risk_level")
    checksum = entry.get("checksum") if "checksum" in entry else entry.get("hash")
    approver = entry.get("approver") if "approver" in entry else entry.get("owner")
    created_at = entry.get("created_at") if "created_at" in entry else entry.get("updatedAt")
    return {
        "id": reg_id,
        "name": strat_id,
        "kind": "execution_bundle",
        "version": version,
        "state": state,
        "risk": risk,
        "hash": checksum,
        "owner": approver,
        "updatedAt": created_at,
        "source_kind": "registry",
    }


def project_registry_catalog_detail(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Project a Registry strategy-artifact execution bundle into the /bff/artifacts/{id} detail shape."""
    entry = row.get("entry") if isinstance(row.get("entry"), Mapping) else row
    catalog_row = normalize_registry_catalog_row(entry)
    detail = dict(entry)
    detail.update(catalog_row)
    if "registry_id" not in detail and catalog_row.get("id") is not None:
        detail["registry_id"] = catalog_row["id"]
    if "artifact_id" not in detail and catalog_row.get("id") is not None:
        detail["artifact_id"] = catalog_row["id"]
    return detail


def normalize_research_catalog_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Project a Research artifact / run output row into the /bff/artifacts catalog shape.

    Research run outputs have no owner-backed version, state, or risk; keep them None.
    """
    saved = row.get("saved_output") if isinstance(row.get("saved_output"), Mapping) else {}
    run = row.get("run") if isinstance(row.get("run"), Mapping) else {}
    item_id = row.get("artifact_id") if row.get("artifact_id") is not None else row.get("id")
    name = row.get("name") if row.get("name") is not None else row.get("title")
    kind = row.get("artifact_type") if row.get("artifact_type") is not None else row.get("kind")
    checksum = (
        row.get("hash")
        if row.get("hash") is not None
        else (
            row.get("checksum")
            if row.get("checksum") is not None
            else (saved.get("checksum") if saved.get("checksum") is not None else run.get("artifact_digest"))
        )
    )
    owner = (
        row.get("owner")
        if row.get("owner") is not None
        else (row.get("author") if row.get("author") is not None else row.get("created_by"))
    )
    updated_at = (
        row.get("updatedAt")
        if row.get("updatedAt") is not None
        else (row.get("created_at") if row.get("created_at") is not None else row.get("updated_at"))
    )
    is_run_output = bool(
        saved
        or run
        or row.get("source_kind") == "research_run_output"
        or "created_by" in row
        or "title" in row
        or row.get("status") in (None, "")
    )
    version = None if is_run_output else row.get("version")
    state = None if is_run_output else (row.get("state") if row.get("state") is not None else row.get("status"))
    risk = None if is_run_output else (row.get("risk") if row.get("risk") is not None else row.get("risk_level"))
    return {
        "id": item_id,
        "name": name,
        "kind": kind,
        "version": version,
        "state": state,
        "risk": risk,
        "hash": checksum,
        "owner": owner,
        "updatedAt": updated_at,
        "source_kind": str(row.get("source_kind") or "research_run_output"),
    }


def normalize_source_ingest_evidence_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    """The one mapping from a source-ingest evidence item to a BFF evidence ref.

    Never fills a field with the current time: ``captured_at`` is
    ``available_time`` then ``event_time`` and stays ``None`` when neither
    exists.
    """
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    ref_id = item.get("evidence_item_id") or item.get("ref_id")
    label = item.get("citation_label") or ref_id
    try:
        confidence = float(item.get("confidence") if item.get("confidence") is not None else 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    verified = confidence >= 0.8
    body = item.get("body") or None
    return {
        "ref_id": ref_id,
        "evidence_origin": "source_ingest",
        "evidence_type": item.get("item_type") or None,
        "display_label": label,
        "summary": body,
        "source_document": {
            "title": label,
            "source_type": metadata.get("source_class") or item.get("item_type") or "internal",
            "source_ref": item.get("content_ref"),
            "captured_at": item.get("available_time") or item.get("event_time") or None,
            "excerpt": body,
        },
        "link_type": "supporting",
        "credibility": {"tier": "verified" if verified else "unverified", "verified": verified},
        "metadata": dict(metadata),
    }


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


def evidence_ref_sort_key(ref: Mapping[str, Any]) -> Tuple[datetime, str]:
    """Newest-first ordering key shared by the full evidence read and callers
    that reassemble owner pages, so both produce identical row order."""
    return (
        _naive_utc(_parse_rfc3339(((ref.get("source_document") or {}).get("captured_at")) or ref.get("created_at"))),
        str(ref.get("ref_id") or ""),
    )


def _is_valid_health_usage_envelope(payload: Any) -> bool:
    """Match services/source_ingestion get_health_usage_snapshot; zero sources is valid."""
    if not isinstance(payload, dict):
        return False
    sources = payload.get("sources")
    summary = payload.get("recommendation_summary")
    if not isinstance(sources, list) or not isinstance(summary, dict):
        return False
    if payload.get("source_count") != len(sources):
        return False
    if not all(
        isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)
        for k, v in summary.items()
    ):
        return False
    for row in sources:
        if not isinstance(row, dict):
            return False
        health = row.get("health")
        if not isinstance(health, dict) or not isinstance(health.get("source_id"), str) or not health["source_id"]:
            return False
        if not isinstance(row.get("usage_aggregate_30d"), dict):
            return False
        if row.get("recommendation") is not None and not isinstance(row["recommendation"], dict):
            return False
    return True


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

    def read_evidence_refs(self, **kwargs: Any) -> "EvidenceReadResult":
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

    def list_strategy_specs_page(
        self,
        *,
        state: Optional[str] = None,
        exclude_state: Optional[str] = None,
        persona_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def get_strategy_labels(self) -> Dict[str, Dict[str, str]]:
        """``{strategy_id: {"title", "name"}}`` of bound strategies' current versions, without spec bodies."""
        raise NotImplementedError

    def count_strategy_persona_bindings(self) -> Dict[str, int]:
        """Per-persona count of non-retired strategies whose current version binds it."""
        counts: Dict[str, int] = {}
        for item in self.list_strategy_specs() or []:
            if not isinstance(item, dict):
                continue
            for persona_id in {str(v).strip() for v in item.get("persona_ids") or []}:
                if persona_id:
                    counts[persona_id] = counts.get(persona_id, 0) + 1
        return counts

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

    def read_registry_strategy_artifacts(self) -> ArtifactOwnerReadResult:
        raise NotImplementedError

    def read_registry_strategy_artifact_detail(
        self, artifact_id: Optional[str]
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        raise NotImplementedError

    def read_registry_strategy_artifacts_for_strategy(
        self, strategy_id: Optional[str]
    ) -> ArtifactOwnerReadResult:
        raise NotImplementedError

    def read_research_artifact_catalog_rows(self) -> ArtifactOwnerReadResult:
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
        research_write_owner: Optional[Any] = None,
        research_artifacts_store: Optional[Dict[str, Any]] = None,
        registry_service_url: Optional[str] = None,
        registry_strategy_artifacts_store: Optional[Dict[str, Any]] = None,
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
        self._has_injected_strategy_specs: bool = bool(strategy_specs_store)
        self._tickets: Dict[str, Any] = dict(research_tickets_store or {})
        self._analyses: Dict[str, Any] = dict(research_analyses_store or {})
        if research_experiments_store:
            # ACG-02-003 (REMOVE): the in-memory experiments overlay is retired.
            # BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001 deletes it entirely;
            # this constructor kwarg is accepted-but-ignored only so unrelated
            # fixtures that still pass it do not hard-crash at construction
            # time. Experiment reads/writes now go exclusively through
            # ResearchWriteOwner (Postgres), never this dict.
            log.warning(
                "DefaultResearchKnowledgeSourcePort: research_experiments_store is "
                "deprecated and ignored; experiments now delegate exclusively to "
                "ResearchWriteOwner (Postgres). See "
                "docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md."
            )
        self._research_write_owner: Optional[Any] = research_write_owner
        self._research_write_owner_resolved: bool = research_write_owner is not None
        self._artifacts: Dict[str, Any] = dict(research_artifacts_store or {})
        self._registry_service_url: Optional[str] = registry_service_url
        self._registry_strategy_artifacts: Dict[str, Any] = dict(registry_strategy_artifacts_store or {})
        self._has_injected_registry_strategy_artifacts: bool = registry_strategy_artifacts_store is not None
        self._evidence_refs: Dict[str, Any] = dict(evidence_refs_store or {})
        self._search_documents: List[Dict[str, Any]] = list(search_documents_store or [])
        self._last_governed_search_refs: Dict[str, Dict[str, Any]] = {}
        self._has_custom_http_get: bool = http_get_fn is not None
        self._http_get = http_get_fn or self._default_http_get
        self._http_post = http_post_fn or self._default_http_post

    # -------------------------------------------------------------------------
    # HTTP helper stubs
    # -------------------------------------------------------------------------
    @staticmethod
    def _default_http_get(base_url: str, path: str) -> Tuple[bool, Any]:
        if not base_url:
            return False, None
        try:
            import urllib.request
            from ..core.owner_reads import authorization, remaining_management_read_budget, selected_tenant

            timeout = evidence_owner_read_timeout_seconds()
            budget = remaining_management_read_budget(timeout)
            if budget is not None:
                if budget <= 0.0:
                    return False, None
                timeout = max(0.05, float(budget))
            h = {k: v for k, v in (("Authorization", authorization.get()), ("X-Tenant-Id", selected_tenant.get())) if v}
            with urllib.request.urlopen(
                urllib.request.Request(f"{base_url.rstrip('/')}/{path.lstrip('/')}", headers=h),
                timeout=timeout,
            ) as resp:
                return True, json.loads(raw.decode("utf-8")) if (raw := resp.read()) else None
        except Exception:
            return False, None

    @staticmethod
    def _default_http_post(base_url: str, path: str, body: Dict[str, Any]) -> Tuple[bool, Any]:
        return False, None

    # -------------------------------------------------------------------------
    # ResearchWriteOwner binding (RW-04 Experiments)
    # -------------------------------------------------------------------------
    def _get_research_write_owner(self) -> Optional[Any]:
        """Lazily resolve the canonical Research write owner or Research service client.

        Returns ``None`` (never raises) when the owner cannot be resolved, so
        callers can decide whether to fail closed (mutations, detail reads)
        or report a degraded surface (list reads). Resolution is attempted at
        most once per instance to avoid retry storms against a misconfigured
        URL on every request.
        """
        if self._research_write_owner is not None:
            return self._research_write_owner
        if self._research_write_owner_resolved:
            return None
        self._research_write_owner_resolved = True
        try:
            try:
                from ..research.client import ResearchServiceClient, resolve_orchestrator_base_url
            except (ImportError, ValueError):
                from services.control_plane.bff.research.client import ResearchServiceClient, resolve_orchestrator_base_url
            url = resolve_orchestrator_base_url()
            self._research_write_owner = ResearchServiceClient(base_url=url) if url else None
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any failure means "unavailable"
            log.warning("Research service unavailable for research experiments: %s", exc)
            self._research_write_owner = None
        return self._research_write_owner

    # -------------------------------------------------------------------------
    # Surface & Dataset metadata
    # -------------------------------------------------------------------------
    def dataset_source(self, dataset: str) -> str:
        if dataset == "institutional_memory_entries":
            return "typed_store" if self._institutional_memory_store is not None else ("bff_composed" if self._notes else "missing")
        if dataset in ("evidence_refs", "knowledge_evidence"):
            # Configuration only. Whether a particular read succeeded is the
            # EvidenceReadResult of that read, not instance state.
            if self._evidence_repo is not None or self._evidence_refs:
                return "typed_store"
            if not self._source_ingest_service_url:
                return "missing"
            # A fresh one-row probe on every call: nothing is cached on the
            # instance, so one request's failure can never label another's.
            try:
                next(self._iter_owner_evidence_pages(page_size=1))
            except (_EvidenceOwnerReadError, StopIteration):
                return "unavailable"
            return "service_client"
        if dataset == "research_experiments":
            return "typed_store" if self._get_research_write_owner() is not None else "missing"
        if dataset == "strategy_specs":
            if self._has_injected_strategy_specs:
                return "typed_store"
            return "service_client" if (os.getenv("PANTHEON_REGISTRY_API_URL") or os.getenv("PANTHEON_REGISTRY_URL")) else "missing"
        if dataset == "research_notes":
            # Configuration only, like research_artifacts: whether a read succeeded
            # is the outcome of that read, and the in-process dict is not the owner.
            owner = self._get_research_write_owner()
            if owner is not None and hasattr(owner, "list_research_notes"):
                return "service_client"
            return "typed_store" if self._notes else "missing"
        if dataset in ("insight_cards", "research_tickets", "research_analyses", "research_artifacts"):
            store_map = {
                "research_notes": self._notes,
                "insight_cards": self._insights,
                "research_tickets": self._tickets,
                "research_analyses": self._analyses,
                "research_artifacts": self._artifacts,
            }
            items = store_map.get(dataset, {})
            if dataset == "research_artifacts" and not items:
                owner = self._get_research_write_owner()
                return "service_client" if owner is not None and hasattr(owner, "list_research_artifacts") else "missing"
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
        owner = self._get_research_write_owner()
        if owner is not None and hasattr(owner, "list_research_notes"):
            return owner.list_research_notes()
        notes = list(self._notes.values())
        notes.sort(
            key=lambda note: _naive_utc(_parse_rfc3339(note.get("updated_at")) or _parse_rfc3339(note.get("created_at"))),
            reverse=True,
        )
        return [json.loads(json.dumps(note)) for note in notes if isinstance(note, dict)]

    def get_research_note(self, note_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not note_id:
            return None
        owner = self._get_research_write_owner()
        if owner is not None and hasattr(owner, "get_research_note"):
            return owner.get_research_note(note_id)
        note = self._notes.get(str(note_id))
        return json.loads(json.dumps(note)) if isinstance(note, dict) else None

    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        owner = self._get_research_write_owner()
        if owner is None or not hasattr(owner, "create_research_note"):
            raise ResearchWriteOwnerUnavailableError(
                "Research write owner is not configured; cannot create a research note."
            )
        result = owner.create_research_note(note)
        if isinstance(result, dict) and "note_id" in result:
            self._notes[str(result["note_id"])] = dict(result)
        return result

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
            try:
                spec = self.get_strategy_spec(ref) or {}
            except Exception:
                spec = {}
            return spec.get("title") or spec.get("name")
        if entity == "experiment":
            try:
                experiment = self.get_research_experiment(ref) or {}
            except ResearchWriteOwnerUnavailableError:
                experiment = {}
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
        if evidence_ref.get("summary"):
            payload["summary"] = evidence_ref["summary"]
        if evidence_ref.get("evidence_origin"):
            payload["evidence_origin"] = evidence_ref["evidence_origin"]
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

        detail = {
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
        if projected.get("summary"):
            detail["summary"] = projected["summary"]
        if projected.get("evidence_origin"):
            detail["evidence_origin"] = projected["evidence_origin"]
        return detail

    def _read_owner_evidence_page(
        self,
        *,
        start: int = 0,
        page_size: int = EVIDENCE_OWNER_PAGE_SIZE,
        captured_from: Optional[str] = None,
        captured_before: Optional[str] = None,
        linked_entity_types: Optional[tuple[str, ...]] = None,
        include_summary: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int, Optional[Dict[str, Any]]]:
        """Read one bounded page and its tenant summary from the owner service."""
        try:
            from ..core.owner_reads import remaining_management_read_budget

            budget = remaining_management_read_budget()
            if budget is not None and budget <= 0.0:
                raise _EvidenceOwnerReadError("source-ingest evidence owner read budget exhausted")
        except _EvidenceOwnerReadError:
            raise
        except Exception:
            pass
        path = f"/api/source-ingest/evidence/items?page_size={page_size}&page_token={start}"
        if captured_from is not None:
            path += f"&captured_from={quote(str(captured_from), safe='')}"
        if captured_before is not None:
            path += f"&captured_before={quote(str(captured_before), safe='')}"
        expected_filters: Dict[str, Any] = {}
        if linked_entity_types is not None:
            types = sorted(set(linked_entity_types))
            path += f"&linked_entity_types={quote(','.join(types), safe='')}"
            expected_filters["linked_entity_types"] = types
        if not include_summary:
            path += "&include_summary=false"
            expected_filters["include_summary"] = False
        try:
            avail, payload = self._http_get(self._source_ingest_service_url, path)
        except Exception as exc:  # noqa: BLE001 - any transport failure means unavailable
            raise _EvidenceOwnerReadError("source-ingest evidence owner read failed") from exc
        items = payload.get("items") if avail and isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise _EvidenceOwnerReadError("source-ingest evidence owner read failed")
        filters = payload.get("filters") or {}
        if expected_filters and (
            not isinstance(filters, dict)
            or any(filters.get(k) != v for k, v in expected_filters.items())
        ):
            # A mixed-version owner may implement only part of the hints. Retry
            # the complete legacy population, never trust a partially filtered
            # total or retain per-instance capability state across requests.
            return self._read_owner_evidence_page(start=start, page_size=page_size)
        total = payload.get("total")
        total = int(total) if isinstance(total, int) and not isinstance(total, bool) else start + len(items)
        summary = _normalize_owner_evidence_summary(payload.get("summary"), default_total=total)
        return items, total, summary

    def _iter_owner_evidence_pages(self, *, start: int = 0, page_size: int = EVIDENCE_OWNER_PAGE_SIZE):
        """Yield ``(items, total)`` per owner page; raise ``_EvidenceOwnerReadError``."""
        offset = start
        while True:
            items, total, _summary = self._read_owner_evidence_page(start=offset, page_size=page_size)
            yield items, total
            offset += len(items)
            if not items or offset >= total:
                return

    def read_evidence_refs(
        self,
        *,
        tenant_id: Optional[str] = None,
        include_tenant_agnostic: bool = True,
        linked_entities: Optional[set[tuple[str, str]]] = None,
        source_types: Optional[set[str]] = None,
        include_scope_metadata: bool = False,
        page_token: Optional[str] = None,
        page_size: Optional[int] = None,
        captured_from: Optional[str] = None,
        captured_before: Optional[str] = None,
        linked_entity_types: Optional[tuple[str, ...]] = None,
        include_summary: bool = True,
    ) -> EvidenceReadResult:
        """One evidence read. With ``page_size`` and no scope filter against the
        owner service, exactly one owner page is fetched; otherwise every owner
        page is streamed and filtered so totals stay correct.

        ``captured_from`` / ``captured_before`` ask the owner to page only items
        captured in ``[captured_from, captured_before)`` (a population hint for
        the paged branches). An older owner ignores them and returns the
        unfiltered population, so callers must still apply the window to rows."""
        from services.knowledge.evidence.models import summarize_evidence_items_collection

        unscoped = not linked_entities and not source_types
        effective_tenant = str(tenant_id or "").strip() or None
        if effective_tenant is None:
            try:
                from ..core.owner_reads import selected_tenant

                effective_tenant = str(selected_tenant.get() or "").strip() or None
            except Exception:
                effective_tenant = None

        if (
            page_size
            and unscoped
            and self._evidence_repo is not None
            and not self._evidence_refs
            and hasattr(self._evidence_repo, "list_evidence_items_page")
        ):
            try:
                start = int(page_token) if page_token else 0
            except ValueError:
                start = 0
            try:
                repo_kwargs: Dict[str, Any] = {"limit": page_size, "offset": start}
                if effective_tenant is not None:
                    repo_kwargs["tenant_id"] = effective_tenant
                window_kwargs = {
                    k: v
                    for k, v in (("captured_from", captured_from), ("captured_before", captured_before))
                    if v is not None
                }
                if linked_entity_types is not None:
                    window_kwargs["linked_entity_types"] = linked_entity_types
                try:
                    repo_items, total = self._evidence_repo.list_evidence_items_page(
                        **repo_kwargs, **window_kwargs
                    )
                except TypeError:
                    if not window_kwargs:
                        raise
                    # Repository without window support: unfiltered page, the
                    # caller applies the window to rows.
                    repo_items, total = self._evidence_repo.list_evidence_items_page(**repo_kwargs)
            except Exception:
                return EvidenceReadResult([], None, "unavailable", "unavailable", summary_unavailable=True)
            repo_summary: Optional[Dict[str, Any]] = None
            summarize_fn = getattr(self._evidence_repo, "summarize_evidence_items", None)
            if include_summary and callable(summarize_fn):
                try:
                    raw_summary = (
                        summarize_fn(tenant_id=effective_tenant)
                        if effective_tenant is not None
                        else summarize_fn()
                    )
                    repo_summary = _normalize_owner_evidence_summary(raw_summary, default_total=total)
                except Exception:
                    repo_summary = None
            rows = [
                self._project_evidence_ref_list_item(
                    normalize_source_ingest_evidence_item(it.to_dict()),
                    include_scope_metadata=include_scope_metadata,
                )
                for it in repo_items
            ]
            nxt = start + len(repo_items)
            return EvidenceReadResult(
                rows,
                total,
                "ok",
                "typed_store",
                str(nxt) if nxt < total else None,
                summary=repo_summary,
                summary_unavailable=repo_summary is None,
            )

        if (
            page_size
            and unscoped
            and self._evidence_repo is None
            and not self._evidence_refs
            and self._source_ingest_service_url
        ):
            try:
                start = int(page_token) if page_token else 0
            except ValueError:
                start = 0
            tenant_tok = None
            if effective_tenant is not None:
                try:
                    from ..core.owner_reads import selected_tenant

                    if selected_tenant.get() is None:
                        tenant_tok = selected_tenant.set(effective_tenant)
                except Exception:
                    tenant_tok = None
            try:
                items, total, owner_summary = self._read_owner_evidence_page(
                    start=start,
                    page_size=page_size,
                    captured_from=captured_from,
                    captured_before=captured_before,
                    linked_entity_types=linked_entity_types,
                    include_summary=include_summary,
                )
            except (_EvidenceOwnerReadError, StopIteration):
                return EvidenceReadResult([], None, "unavailable", "unavailable", summary_unavailable=True)
            finally:
                if tenant_tok is not None:
                    try:
                        from ..core.owner_reads import selected_tenant

                        selected_tenant.reset(tenant_tok)
                    except Exception:
                        pass
            rows = [
                self._project_evidence_ref_list_item(
                    normalize_source_ingest_evidence_item(it), include_scope_metadata=include_scope_metadata
                )
                for it in items
                if self._record_matches_tenant(it, effective_tenant, include_tenant_agnostic=include_tenant_agnostic)
            ]
            nxt = start + len(items)
            return EvidenceReadResult(
                rows,
                total,
                "ok",
                "service_client",
                str(nxt) if nxt < total else None,
                summary=owner_summary,
                summary_unavailable=owner_summary is None,
            )

        tenant_tok = None
        if effective_tenant is not None:
            try:
                from ..core.owner_reads import selected_tenant

                if selected_tenant.get() is None:
                    tenant_tok = selected_tenant.set(effective_tenant)
            except Exception:
                tenant_tok = None
        try:
            raw_refs, source = self._collect_raw_evidence_refs(tenant_id=effective_tenant)
        except _EvidenceOwnerReadError:
            return EvidenceReadResult([], None, "unavailable", "unavailable", summary_unavailable=True)
        finally:
            if tenant_tok is not None:
                try:
                    from ..core.owner_reads import selected_tenant

                    selected_tenant.reset(tenant_tok)
                except Exception:
                    pass
        filtered = [
            ref
            for ref in raw_refs
            if self._record_matches_tenant(ref, effective_tenant, include_tenant_agnostic=include_tenant_agnostic)
            and self._evidence_matches_scope(ref, linked_entities=linked_entities, source_types=source_types)
        ]
        filtered.sort(key=evidence_ref_sort_key, reverse=True)
        total = len(filtered)
        nxt_token: Optional[str] = None
        if page_size:
            try:
                start = int(page_token) if page_token else 0
            except ValueError:
                start = 0
            window = filtered[start: start + page_size]
            nxt_token = str(start + page_size) if start + page_size < total else None
        else:
            window = filtered
        rows = [
            self._project_evidence_ref_list_item(ref, include_scope_metadata=include_scope_metadata)
            for ref in window
        ]
        if source == "missing":
            # Nothing configured to read from: unknown, not an empty "ok".
            return EvidenceReadResult([], None, "unavailable", "missing", summary_unavailable=True)
        population_summary = _normalize_owner_evidence_summary(
            summarize_evidence_items_collection(filtered), default_total=total
        )
        return EvidenceReadResult(
            rows,
            total,
            "ok",
            source,
            nxt_token,
            summary=population_summary,
            summary_unavailable=population_summary is None,
        )

    def _collect_raw_evidence_refs(
        self, *, tenant_id: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], str]:
        """Return ``(raw_refs, source)``; raise on owner read failure."""
        if self._evidence_repo is not None:
            try:
                items = (
                    self._evidence_repo.list_evidence_items(tenant_id=tenant_id)
                    if tenant_id is not None
                    else self._evidence_repo.list_evidence_items()
                )
            except Exception as exc:
                raise _EvidenceOwnerReadError("evidence repository read failed") from exc
            return [normalize_source_ingest_evidence_item(it.to_dict()) for it in items], "typed_store"
        if self._source_ingest_service_url and not self._evidence_refs:
            refs: List[Dict[str, Any]] = []
            for items, _total in self._iter_owner_evidence_pages():
                refs.extend(normalize_source_ingest_evidence_item(it) for it in items)
            return refs, "service_client"
        return list(self._evidence_refs.values()), "typed_store" if self._evidence_refs else "missing"

    def list_evidence_refs(
        self,
        *,
        tenant_id: Optional[str] = None,
        include_tenant_agnostic: bool = True,
        linked_entities: Optional[set[tuple[str, str]]] = None,
        source_types: Optional[set[str]] = None,
        include_scope_metadata: bool = False,
    ) -> List[Dict[str, Any]]:
        return self.read_evidence_refs(
            tenant_id=tenant_id,
            include_tenant_agnostic=include_tenant_agnostic,
            linked_entities=linked_entities,
            source_types=source_types,
            include_scope_metadata=include_scope_metadata,
        ).items

    def _read_raw_evidence_ref_checked(
        self, ref_id: Optional[str]
    ) -> Tuple[Optional[Dict[str, Any]], str, str]:
        """Read ONE evidence item (owner item route / repo get), never the list.

        Returns ``(raw_or_None, status, source)``; ``status`` is ``ok`` (found
        or genuinely absent) or ``unavailable`` (the owner read failed).
        """
        clean = str(ref_id or "").strip()
        if not clean:
            return None, "ok", "typed_store"
        effective_tenant: Optional[str] = None
        try:
            from ..core.owner_reads import selected_tenant

            effective_tenant = str(selected_tenant.get() or "").strip() or None
        except Exception:
            effective_tenant = None
        if self._evidence_repo is not None:
            try:
                item = (
                    self._evidence_repo.get_evidence_item(clean, tenant_id=effective_tenant)
                    if effective_tenant is not None
                    else self._evidence_repo.get_evidence_item(clean)
                )
            except Exception:
                return None, "unavailable", "unavailable"
            raw = normalize_source_ingest_evidence_item(item.to_dict()) if item is not None else None
            if raw is not None and not self._record_matches_tenant(raw, effective_tenant, include_tenant_agnostic=True):
                raw = None
            return raw, "ok", "typed_store"
        if self._evidence_refs:
            ref = next((r for r in self._evidence_refs.values() if str(r.get("ref_id") or "") == clean), None)
            if ref is not None and not self._record_matches_tenant(ref, effective_tenant, include_tenant_agnostic=True):
                ref = None
            return (dict(ref) if ref else None), "ok", "typed_store"
        if self._source_ingest_service_url:
            from urllib.parse import quote

            try:
                avail, payload = self._http_get(
                    self._source_ingest_service_url, f"/api/source-ingest/evidence/items/{quote(clean, safe='')}"
                )
            except Exception:  # noqa: BLE001
                return None, "unavailable", "unavailable"
            record = payload.get("item") if avail and isinstance(payload, dict) else None
            if isinstance(record, dict):
                raw = normalize_source_ingest_evidence_item(record)
                if not self._record_matches_tenant(raw, effective_tenant, include_tenant_agnostic=True):
                    return None, "ok", "service_client"
                return raw, "ok", "service_client"
            if avail:
                return None, "ok", "service_client"
            # The helper reports 404 and transport failure alike; a one-row
            # list probe tells "absent" apart from "owner down".
            try:
                next(self._iter_owner_evidence_pages(page_size=1))
            except (_EvidenceOwnerReadError, StopIteration):
                return None, "unavailable", "unavailable"
            return None, "ok", "service_client"
        return None, "unavailable", "missing"

    def _read_raw_evidence_ref(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._read_raw_evidence_ref_checked(ref_id)[0]

    def read_evidence_ref_item(self, ref_id: Optional[str]) -> "EvidenceReadResult":
        """One owner item read; availability belongs to this read."""
        raw, status, source = self._read_raw_evidence_ref_checked(ref_id)
        if status != "ok":
            return EvidenceReadResult([], None, "unavailable", source)
        rows = [self._project_evidence_ref_list_item(raw)] if raw else []
        return EvidenceReadResult(rows, len(rows), "ok", source)

    def read_evidence_ref_detail(
        self, ref_id: Optional[str]
    ) -> Tuple[Optional[Dict[str, Any]], "EvidenceReadResult"]:
        """One owner item read returning the detail record plus that read's
        own availability (no separate dataset probe)."""
        raw, status, source = self._read_raw_evidence_ref_checked(ref_id)
        if status != "ok":
            return None, EvidenceReadResult([], None, "unavailable", source)
        detail = self._project_evidence_ref_detail(raw) if raw else None
        return detail, EvidenceReadResult([detail] if detail else [], 1 if detail else 0, "ok", source)

    def get_evidence_ref(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ref_id:
            return None
        ref = self._read_raw_evidence_ref(ref_id)
        return self._project_evidence_ref_list_item(ref) if ref else None

    def get_evidence_ref_detail(self, ref_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ref_id:
            return None
        ref = self._read_raw_evidence_ref(ref_id)
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
            try:
                strategy_spec = self.get_strategy_spec(ref) or {}
            except Exception:
                strategy_spec = {}
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
                copy.deepcopy(item)
                for item in bundle.get("evidence_refs") or []
                if isinstance(item, dict)
            ],
            "memory_anchors": [
                copy.deepcopy(item)
                for item in bundle.get("memory_anchors") or []
                if isinstance(item, dict)
            ],
            "insight_citations": [
                copy.deepcopy(item)
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
    def _kw05_version_sort_key(
        cls,
        *,
        spec_version: Any,
        created_at: Any,
        spec_version_id: Any,
    ) -> Tuple[Tuple[int, ...], datetime, str]:
        return (
            tuple(int(p) for p in str(spec_version or "").lstrip("v").split(".") if p.isdigit()),
            _naive_utc(_parse_rfc3339(created_at)),
            str(spec_version_id or ""),
        )

    @classmethod
    def _kw05_sort_versions(cls, versions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(
            versions,
            key=lambda version: cls._kw05_version_sort_key(
                spec_version=version.get("spec_version"),
                created_at=version.get("created_at"),
                spec_version_id=version.get("spec_version_id"),
            ),
            reverse=True,
        )

    @classmethod
    def _kw05_normalize_version_candidate(
        cls,
        strategy_spec: Dict[str, Any],
        raw_version: Dict[str, Any],
        *,
        strategy_id: str,
        index: int,
        candidate_count: int,
        include_detail_bundles: bool = True,
    ) -> Dict[str, Any]:
        provenance = (
            raw_version.get("provenance")
            if isinstance(raw_version.get("provenance"), dict)
            else {}
        )
        spec_version_id = str(
            raw_version.get("spec_version_id")
            or (
                strategy_spec.get("current_spec_version_id")
                if index == 1 and candidate_count == 1
                else ""
            )
            or raw_version.get("id")
            or strategy_id
        ).strip()
        spec_version = str(
            raw_version.get("spec_version")
            or (
                strategy_spec.get("current_spec_version")
                if index == 1 and candidate_count == 1
                else ""
            )
            or f"v{index}"
        ).strip()
        title = (
            raw_version.get("title")
            or raw_version.get("name")
            or strategy_spec.get("title")
            or strategy_spec.get("name")
        )
        version: Dict[str, Any] = {
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
            "market_scope": copy.deepcopy(
                raw_version.get("market_scope") or strategy_spec.get("market_scope") or {}
            ),
            "execution_profile": copy.deepcopy(
                raw_version.get("execution_profile")
                or strategy_spec.get("execution_profile")
                or {}
            ),
            "governance": copy.deepcopy(
                raw_version.get("governance") or strategy_spec.get("governance") or {}
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
        if include_detail_bundles:
            version["evaluation_plan"] = copy.deepcopy(
                raw_version.get("evaluation_plan")
                or strategy_spec.get("evaluation_plan")
                or {}
            )
            version["citation_bundle"] = cls._kw05_normalize_citation_bundle(
                raw_version.get("citation_bundle") or strategy_spec.get("citation_bundle")
            )
        risk_val = raw_version.get("risk") or strategy_spec.get("risk")
        if risk_val:
            version["risk"] = risk_val
            if isinstance(version.get("governance"), dict) and "risk_level" not in version["governance"]:
                version["governance"]["risk_level"] = risk_val
        for extra_key in ("strategy_spec_registry_id", "tenant_id", "semantic_version"):
            extra_val = raw_version.get(extra_key) or strategy_spec.get(extra_key)
            if extra_val:
                version[extra_key] = str(extra_val)
        raw_meta = raw_version.get("metadata") if isinstance(raw_version.get("metadata"), dict) else {}
        spec_meta = strategy_spec.get("metadata") if isinstance(strategy_spec.get("metadata"), dict) else {}
        merged_meta = {**spec_meta, **{k: v for k, v in raw_meta.items() if v is not None or k not in spec_meta}}
        if isinstance(spec_meta.get("traits"), dict) and isinstance(raw_meta.get("traits"), dict):
            merged_meta["traits"] = {
                **spec_meta["traits"],
                **{k: v for k, v in raw_meta["traits"].items() if v is not None},
            }
        if merged_meta:
            version["metadata"] = copy.deepcopy(merged_meta)
        top_cap_pool_id = (
            raw_version.get("capital_pool_id")
            or raw_version.get("capitalPoolId")
            or strategy_spec.get("capital_pool_id")
            or strategy_spec.get("capitalPoolId")
        )
        if top_cap_pool_id:
            version["capital_pool_id"] = str(top_cap_pool_id).strip()
        version["allowedActions"] = cls._kw05_allowed_actions(version)
        return version

    @classmethod
    def _kw05_versions(cls, strategy_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
        strategy_id = str(strategy_spec.get("strategy_id") or strategy_spec.get("id") or "").strip()
        if not strategy_id:
            return []

        raw_versions = strategy_spec.get("versions")
        candidates = raw_versions if isinstance(raw_versions, list) and raw_versions else [strategy_spec]
        candidate_count = len(candidates)
        versions: List[Dict[str, Any]] = []
        for index, raw_version in enumerate(candidates, start=1):
            if not isinstance(raw_version, dict):
                continue
            versions.append(
                cls._kw05_normalize_version_candidate(
                    strategy_spec,
                    raw_version,
                    strategy_id=strategy_id,
                    index=index,
                    candidate_count=candidate_count,
                    include_detail_bundles=True,
                )
            )

        return cls._kw05_sort_versions(versions)

    @classmethod
    def _kw05_select_current_version_candidate(
        cls,
        strategy_spec: Dict[str, Any],
    ) -> Optional[Tuple[str, int, Dict[str, Any], int, int]]:
        strategy_id = str(strategy_spec.get("strategy_id") or strategy_spec.get("id") or "").strip()
        if not strategy_id:
            return None

        raw_versions = strategy_spec.get("versions")
        candidates = raw_versions if isinstance(raw_versions, list) and raw_versions else [strategy_spec]
        candidate_count = len(candidates)
        if candidate_count == 1:
            only = candidates[0]
            if not isinstance(only, dict):
                return None
            return strategy_id, 1, only, 1, 1

        ranked: List[Tuple[Tuple[Tuple[int, ...], datetime, str], str, int, Dict[str, Any]]] = []
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
                or raw_version.get("id")
                or strategy_id
            ).strip()
            spec_version = str(raw_version.get("spec_version") or f"v{index}").strip()
            created_at = (
                raw_version.get("created_at")
                or provenance.get("created_at")
                or strategy_spec.get("created_at")
                or strategy_spec.get("updated_at")
            )
            ranked.append(
                (
                    cls._kw05_version_sort_key(
                        spec_version=spec_version,
                        created_at=created_at,
                        spec_version_id=spec_version_id,
                    ),
                    spec_version_id,
                    index,
                    raw_version,
                )
            )
        if not ranked:
            return None
        ranked.sort(key=lambda entry: entry[0], reverse=True)
        version_count = len(ranked)
        current_spec_version_id = str(strategy_spec.get("current_spec_version_id") or "").strip()
        if current_spec_version_id:
            for _sort_key, spec_version_id, index, raw_version in ranked:
                if spec_version_id == current_spec_version_id:
                    return strategy_id, index, raw_version, candidate_count, version_count
        _, _, best_index, best_raw = ranked[0]
        return strategy_id, best_index, best_raw, candidate_count, version_count

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
                str(version.get("strategy_spec_registry_id") or ""),
                str(version.get("semantic_version") or ""),
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
        use_injected = self._has_injected_strategy_specs and not (
            os.getenv("PANTHEON_REGISTRY_API_URL") or os.getenv("PANTHEON_REGISTRY_URL")
        )
        if (
            not use_injected
            and not include_retired
            and not include_fixture_pack
            and lifecycle_state not in {"retired", "all"}
        ):
            from services.control_plane.bff.core.owner_reads import cached_owner_call, selected_tenant

            base_items = cached_owner_call(
                ("rks_normalized_strategy_specs", id(self), selected_tenant.get()),
                lambda: self._list_strategy_specs_uncached(
                    use_injected=False,
                    lifecycle_state=None,
                    source_kind=None,
                    persona_id=None,
                    include_retired=False,
                    include_fixture_pack=False,
                ),
            )
            if lifecycle_state is None and source_kind is None and persona_id is None:
                return copy.deepcopy(list(base_items))
            target_pid = str(persona_id).strip() if persona_id else None
            filtered: List[Dict[str, Any]] = []
            for item in base_items:
                if lifecycle_state and item.get("lifecycle_state") != lifecycle_state:
                    continue
                if source_kind and item.get("source_kind") != source_kind:
                    continue
                if target_pid and target_pid not in {
                    str(v).strip() for v in (item.get("persona_ids") or ()) if str(v).strip()
                }:
                    continue
                filtered.append(item)
            return copy.deepcopy(filtered)

        return self._list_strategy_specs_uncached(
            use_injected=use_injected,
            lifecycle_state=lifecycle_state,
            source_kind=source_kind,
            persona_id=persona_id,
            include_retired=include_retired,
            include_fixture_pack=include_fixture_pack,
        )

    def _list_strategy_specs_uncached(
        self,
        *,
        use_injected: bool,
        lifecycle_state: Optional[str] = None,
        source_kind: Optional[str] = None,
        persona_id: Optional[str] = None,
        include_retired: bool = False,
        include_fixture_pack: bool = False,
    ) -> List[Dict[str, Any]]:
        source_specs = (
            list(self._strategy_specs.values())
            if use_injected
            else self._fetch_canonical_strategy_specs()
        )
        items: List[Dict[str, Any]] = []
        for strategy_spec in source_specs:
            if not isinstance(strategy_spec, dict):
                continue
            selected = self._kw05_select_current_version_candidate(strategy_spec)
            if selected is None:
                continue
            strategy_id, selected_index, raw_current, candidate_count, version_count = selected
            provenance = (
                raw_current.get("provenance")
                if isinstance(raw_current.get("provenance"), dict)
                else {}
            )
            raw_meta = raw_current.get("metadata") if isinstance(raw_current.get("metadata"), dict) else {}
            spec_meta = strategy_spec.get("metadata") if isinstance(strategy_spec.get("metadata"), dict) else {}
            current_lifecycle_state = str(
                self._kw05_lifecycle_state(
                    raw_current.get("lifecycle_state")
                    or raw_current.get("artifact_state")
                    or raw_current.get("status")
                    or raw_meta.get("lifecycle_state")
                    or raw_meta.get("state")
                    or raw_meta.get("status")
                    or strategy_spec.get("lifecycle_state")
                    or strategy_spec.get("artifact_state")
                    or strategy_spec.get("status")
                    or spec_meta.get("lifecycle_state")
                    or spec_meta.get("state")
                    or spec_meta.get("status")
                )
                or ""
            )
            current_source_kind = str(
                raw_current.get("source_kind")
                or provenance.get("source_kind")
                or strategy_spec.get("source_kind")
                or ""
            )
            raw_persona_ids = (
                raw_current.get("persona_ids")
                or strategy_spec.get("persona_ids")
                or raw_meta.get("personaIds")
                or raw_meta.get("persona_ids")
                or ([raw_meta["persona_id"]] if raw_meta.get("persona_id") else [])
                or spec_meta.get("personaIds")
                or spec_meta.get("persona_ids")
                or ([spec_meta["persona_id"]] if spec_meta.get("persona_id") else [])
                or []
            )
            persona_list = [str(value).strip() for value in raw_persona_ids if str(value).strip()]
            persona_ids = set(persona_list)

            if lifecycle_state and lifecycle_state != "all" and current_lifecycle_state != lifecycle_state:
                continue
            if not include_retired and lifecycle_state in {None, "", "all"} and current_lifecycle_state == "retired":
                continue
            if source_kind and current_source_kind != source_kind:
                continue
            if persona_id and str(persona_id).strip() not in persona_ids:
                continue

            current_version = self._kw05_normalize_version_candidate(
                strategy_spec,
                raw_current,
                strategy_id=strategy_id,
                index=selected_index,
                candidate_count=candidate_count,
                include_detail_bundles=False,
            )
            item_dict: Dict[str, Any] = {
                "object_ref": dict(current_version.get("object_ref") or {}),
                "strategy_id": strategy_id,
                "current_spec_version_id": current_version.get("spec_version_id"),
                "current_spec_version": current_version.get("spec_version"),
                "title": current_version.get("title"),
                "lifecycle_state": current_lifecycle_state,
                "source_kind": current_source_kind,
                "hypothesis_excerpt": self._kw05_hypothesis_excerpt(current_version.get("hypothesis")),
                "version_count": version_count,
                "last_modified_at": current_version.get("last_modified_at"),
                "route_href": self._kw05_strategy_route_href(strategy_id),
                "persona_ids": persona_list,
                "governance": current_version.get("governance"),
                "market_scope": current_version.get("market_scope"),
                "execution_profile": current_version.get("execution_profile"),
                "allowedActions": current_version.get("allowedActions"),
                "risk": current_version.get("risk"),
                "created_at": current_version.get("created_at"),
                "tenant_id": current_version.get("tenant_id"),
                "strategy_spec_registry_id": current_version.get("strategy_spec_registry_id"),
            }
            if current_version.get("capital_pool_id"):
                item_dict["capital_pool_id"] = current_version.get("capital_pool_id")
            if current_version.get("metadata"):
                item_dict["metadata"] = current_version.get("metadata")
            items.append(item_dict)

        items.sort(
            key=lambda item: (
                _naive_utc(_parse_rfc3339(item.get("last_modified_at"))),
                str(item.get("strategy_id") or ""),
            ),
            reverse=True,
        )
        return items

    _STRATEGY_DTO_LIFECYCLE_MAP: Dict[str, str] = {
        "draft": "draft",
        "candidate": "review",
        "review": "review",
        "approved": "approved",
        "active": "deployed",
        "deployed": "deployed",
        "paused": "paused",
        "retired": "retired",
        "archived": "retired",
        "rejected": "retired",
        "superseded": "retired",
        "paper": "paper_running",
        "paper_running": "paper_running",
        "canary": "canary_running",
        "canary_running": "canary_running",
        "canary_authorized_not_started": "canary_authorized_not_started",
        "live": "live_running",
        "live_running": "live_running",
        "needs_human_approval": "needs_human_approval",
        "rollback_required": "rollback_required",
        "stopped": "stopped",
        "failed": "failed",
        "provisioning": "provisioning",
        "provisioning_failed": "failed",
        "research_only": "research_only",
        "consultable": "consultable",
        "paper_owner": "paper_owner",
        "live_owner": "live_owner",
        "frozen": "frozen",
        "suspended": "suspended",
    }

    _STRATEGY_DEFAULT_STATE_COUNT_KEYS: Tuple[str, ...] = (
        "draft",
        "review",
        "approved",
        "paper_running",
        "canary_running",
        "live_running",
        "deployed",
        "paused",
        "retired",
    )

    def list_strategy_specs_page(
        self,
        *,
        state: Optional[str] = None,
        exclude_state: Optional[str] = None,
        persona_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Optional[Dict[str, Any]]:
        norm_state = str(state).strip().lower() if state and str(state).strip() else None
        norm_exclude_state = (
            str(exclude_state).strip().lower()
            if exclude_state and str(exclude_state).strip()
            else None
        )
        if norm_state and norm_exclude_state:
            raise self._strategy_owner_error(
                400,
                "VALIDATION_ERROR",
                "state and exclude_state cannot be combined",
                "conflicting_state_filters",
            )
        use_injected = self._has_injected_strategy_specs and not (
            os.getenv("PANTHEON_REGISTRY_API_URL") or os.getenv("PANTHEON_REGISTRY_URL")
        )
        if not use_injected:
            return self._fetch_canonical_strategy_specs_page(
                state=state,
                exclude_state=exclude_state,
                persona_id=persona_id,
                page_token=page_token,
                page_size=page_size,
            )

        offset = 0
        if page_token is not None and str(page_token).strip() != "":
            try:
                offset = int(str(page_token).strip())
            except ValueError as exc:
                raise self._strategy_owner_error(
                    400, "VALIDATION_ERROR", "Invalid page_token", "invalid_page_token",
                ) from exc
            if offset < 0:
                raise self._strategy_owner_error(
                    400, "VALIDATION_ERROR", "Invalid page_token", "invalid_page_token",
                )
        if not self._strategy_specs:
            return None

        effective_tenant: Optional[str] = None
        try:
            from services.control_plane.bff.core.owner_reads import selected_tenant

            effective_tenant = str(selected_tenant.get() or "").strip() or None
        except Exception:
            effective_tenant = None

        norm_persona = str(persona_id).strip() if persona_id and str(persona_id).strip() else None
        state_counts: Optional[Dict[str, int]] = {
            k: 0 for k in self._STRATEGY_DEFAULT_STATE_COUNT_KEYS
        }
        items: List[Dict[str, Any]] = []
        for strategy_spec in self._strategy_specs.values():
            if not isinstance(strategy_spec, dict):
                continue
            selected = self._kw05_select_current_version_candidate(strategy_spec)
            if selected is None:
                continue
            strategy_id, selected_index, raw_current, candidate_count, version_count = selected
            if effective_tenant is not None and (
                not self._record_matches_tenant(
                    strategy_spec, effective_tenant, include_tenant_agnostic=True
                )
                or not self._record_matches_tenant(
                    raw_current, effective_tenant, include_tenant_agnostic=True
                )
            ):
                continue
            raw_meta = (
                raw_current.get("metadata")
                if isinstance(raw_current.get("metadata"), dict)
                else {}
            )
            spec_meta = (
                strategy_spec.get("metadata")
                if isinstance(strategy_spec.get("metadata"), dict)
                else {}
            )
            raw_lifecycle = (
                str(
                    raw_current.get("lifecycle_state")
                    or raw_current.get("state")
                    or raw_current.get("status")
                    or strategy_spec.get("lifecycle_state")
                    or strategy_spec.get("state")
                    or strategy_spec.get("status")
                    or raw_meta.get("lifecycle_state")
                    or raw_meta.get("state")
                    or raw_meta.get("status")
                    or spec_meta.get("lifecycle_state")
                    or spec_meta.get("state")
                    or spec_meta.get("status")
                    or "draft"
                ).strip()
                or "draft"
            )
            raw_clean = raw_lifecycle.lower()
            dto_state = self._STRATEGY_DTO_LIFECYCLE_MAP.get(raw_clean, "draft")
            if state_counts is not None:
                state_counts[dto_state] = state_counts.get(dto_state, 0) + 1

            is_retired = raw_clean == "retired" or dto_state == "retired"
            if norm_state != "retired" and is_retired:
                continue
            raw_persona_ids = (
                raw_current.get("persona_ids") or strategy_spec.get("persona_ids") or []
            )
            persona_list = [str(value) for value in raw_persona_ids if str(value).strip()]
            if norm_persona and norm_persona not in set(persona_list):
                continue
            if norm_state:
                wanted_dto = self._STRATEGY_DTO_LIFECYCLE_MAP.get(norm_state, norm_state)
                if (
                    raw_clean != norm_state
                    and dto_state != norm_state
                    and dto_state != wanted_dto
                ):
                    continue
            if norm_exclude_state:
                if norm_exclude_state == "draft":
                    if raw_clean in {"", "draft"}:
                        continue
                else:
                    excluded_dto = self._STRATEGY_DTO_LIFECYCLE_MAP.get(
                        norm_exclude_state, norm_exclude_state
                    )
                    if (
                        raw_clean == norm_exclude_state
                        or dto_state == norm_exclude_state
                        or dto_state == excluded_dto
                    ):
                        continue

            provenance = (
                raw_current.get("provenance")
                if isinstance(raw_current.get("provenance"), dict)
                else {}
            )
            current_source_kind = str(
                raw_current.get("source_kind")
                or provenance.get("source_kind")
                or strategy_spec.get("source_kind")
                or ""
            )
            current_version = self._kw05_normalize_version_candidate(
                strategy_spec,
                raw_current,
                strategy_id=strategy_id,
                index=selected_index,
                candidate_count=candidate_count,
                include_detail_bundles=False,
            )
            item_dict: Dict[str, Any] = {
                "object_ref": dict(current_version.get("object_ref") or {}),
                "strategy_id": strategy_id,
                "current_spec_version_id": current_version.get("spec_version_id"),
                "current_spec_version": current_version.get("spec_version"),
                "title": current_version.get("title"),
                "lifecycle_state": raw_lifecycle,
                "source_kind": current_source_kind,
                "hypothesis_excerpt": self._kw05_hypothesis_excerpt(
                    current_version.get("hypothesis")
                ),
                "version_count": version_count,
                "last_modified_at": current_version.get("last_modified_at"),
                "route_href": self._kw05_strategy_route_href(strategy_id),
                "persona_ids": persona_list,
                "governance": current_version.get("governance"),
                "market_scope": current_version.get("market_scope"),
                "execution_profile": current_version.get("execution_profile"),
                "allowedActions": current_version.get("allowedActions"),
                "risk": current_version.get("risk"),
                "created_at": current_version.get("created_at"),
                "tenant_id": current_version.get("tenant_id"),
                "strategy_spec_registry_id": current_version.get(
                    "strategy_spec_registry_id"
                ),
            }
            if current_version.get("capital_pool_id"):
                item_dict["capital_pool_id"] = current_version.get("capital_pool_id")
            if current_version.get("metadata"):
                item_dict["metadata"] = current_version.get("metadata")
            items.append(item_dict)

        items.sort(
            key=lambda item: (
                _naive_utc(_parse_rfc3339(item.get("last_modified_at"))),
                str(item.get("strategy_id") or ""),
            ),
            reverse=True,
        )
        total = len(items)
        sliced = items[offset : offset + page_size]
        next_token = str(offset + page_size) if (offset + page_size) < total else None
        return {
            "items": sliced,
            "total": total,
            "next_page_token": next_token,
            "state_counts": state_counts,
        }

    def count_strategy_persona_bindings(self) -> Dict[str, int]:
        # Same source, current-version selection, lifecycle filter and persona
        # extraction as list_strategy_specs(), without per-version normalization.
        if not (self._has_injected_strategy_specs and not (os.getenv("PANTHEON_REGISTRY_API_URL") or os.getenv("PANTHEON_REGISTRY_URL"))):
            return self._fetch_canonical_persona_binding_counts()
        source_specs = list(self._strategy_specs.values())
        counts: Dict[str, int] = {}
        for strategy_spec in source_specs:
            if not isinstance(strategy_spec, dict):
                continue
            selected = self._kw05_select_current_version_candidate(strategy_spec)
            if selected is None:
                continue
            _strategy_id, _index, raw_current, _candidates, _versions = selected
            raw_meta = raw_current.get("metadata") if isinstance(raw_current.get("metadata"), dict) else {}
            spec_meta = strategy_spec.get("metadata") if isinstance(strategy_spec.get("metadata"), dict) else {}
            lifecycle = str(
                self._kw05_lifecycle_state(
                    raw_current.get("lifecycle_state")
                    or raw_current.get("artifact_state")
                    or raw_current.get("status")
                    or raw_meta.get("lifecycle_state")
                    or raw_meta.get("state")
                    or raw_meta.get("status")
                    or strategy_spec.get("lifecycle_state")
                    or strategy_spec.get("artifact_state")
                    or strategy_spec.get("status")
                    or spec_meta.get("lifecycle_state")
                    or spec_meta.get("state")
                    or spec_meta.get("status")
                )
                or ""
            )
            if lifecycle == "retired":
                continue
            raw_ids = (
                raw_current.get("persona_ids")
                or strategy_spec.get("persona_ids")
                or raw_meta.get("personaIds")
                or raw_meta.get("persona_ids")
                or ([raw_meta["persona_id"]] if raw_meta.get("persona_id") else [])
                or spec_meta.get("personaIds")
                or spec_meta.get("persona_ids")
                or ([spec_meta["persona_id"]] if spec_meta.get("persona_id") else [])
                or []
            )
            for persona_id in {str(v).strip() for v in raw_ids if str(v).strip()}:
                counts[persona_id] = counts.get(persona_id, 0) + 1
        return counts

    def _labels_from_persona_binding_rows(self, raw: Any) -> Dict[str, Dict[str, str]]:
        """Current-version title/name per family from flat registry ``persona_binding`` rows.

        Same current-version rule as the full list (highest semver, created_at,
        registry id) and the canonical title path (``strategy_spec.title`` then
        metadata ``title`` then ``name`` then the strategy id).
        """
        def invalid() -> Any:
            return self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry label payload", "strategy_store_unavailable")

        if not isinstance(raw, list):
            raise invalid()
        best: Dict[str, Tuple[Any, str]] = {}
        for item in raw:
            entry = item.get("entry") if isinstance(item, dict) and isinstance(item.get("entry"), dict) else item
            if not isinstance(entry, dict) or entry.get("artifact_type") not in (None, "strategy_spec"):
                raise invalid()
            meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            inner = meta.get("strategy_spec") if isinstance(meta.get("strategy_spec"), dict) else {}
            sid = str(entry.get("strategy_id") or inner.get("strategy_id") or "").strip()
            reg_id = str(entry.get("registry_id") or meta.get("spec_version_id") or "").strip()
            semver = str(entry.get("version") or inner.get("spec_version") or meta.get("spec_version") or meta.get("version") or "").strip()
            if not sid or not reg_id or not semver:
                raise invalid()
            prov = inner.get("provenance") if isinstance(inner.get("provenance"), dict) else (meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {})
            key = self._kw05_version_sort_key(
                spec_version=semver,
                created_at=entry.get("created_at") or prov.get("created_at"),
                spec_version_id=reg_id,
            )
            if sid not in best or key > best[sid][0]:
                best[sid] = (key, str(inner.get("title") or meta.get("title") or meta.get("name") or sid))
        return {sid: {"title": title, "name": title} for sid, (_key, title) in best.items()}

    def get_strategy_labels(self) -> Dict[str, Dict[str, str]]:
        # Bounded label read: no per-version normalization and no deepcopy of spec bodies.
        if not (self._has_injected_strategy_specs and not (os.getenv("PANTHEON_REGISTRY_API_URL") or os.getenv("PANTHEON_REGISTRY_URL"))):
            return self._labels_from_persona_binding_rows(self._fetch_canonical_persona_binding_view("persona_binding"))
        labels = {}
        for strategy_spec in list(self._strategy_specs.values()):
            if not isinstance(strategy_spec, dict):
                continue
            selected = self._kw05_select_current_version_candidate(strategy_spec)
            if selected is None:
                continue
            strategy_id, _index, raw_current, _candidates, _versions = selected
            title = (
                raw_current.get("title") or raw_current.get("name")
                or strategy_spec.get("title") or strategy_spec.get("name")
            )
            if title:
                labels[strategy_id] = {"title": str(title), "name": str(title)}
        return labels

    def get_strategy_spec(self, strategy_id: Optional[str]) -> Optional[Dict[str, Any]]:
        d = self.get_strategy_spec_detail(strategy_id, version_selector="current")
        if not d:
            return None
        spec: Dict[str, Any] = {
            "strategy_id": d.get("strategy_id"), "tenant_id": d.get("tenant_id"),
            "title": d.get("title"), "name": d.get("title"),
            "spec_version_id": d.get("spec_version_id"),
            "strategy_spec_registry_id": d.get("strategy_spec_registry_id") or d.get("spec_version_id"),
            "spec_version": d.get("spec_version"), "lifecycle_state": d.get("lifecycle_state"),
            "risk": d.get("risk"), "last_modified_at": d.get("last_modified_at"),
        }
        if d.get("capital_pool_id"):
            spec["capital_pool_id"] = d.get("capital_pool_id")
        if d.get("metadata"):
            spec["metadata"] = d.get("metadata")
        return spec

    @staticmethod
    def _strategy_owner_error(status: int, code: str, message: str, reason: str) -> Any:
        from fastapi import HTTPException
        return HTTPException(status_code=status, detail={"error": {"code": code, "message": message, "reason": reason, "status_code": status}})

    def _fetch_canonical_persona_binding_counts(self) -> Dict[str, int]:
        """Registry-owned per-persona counts; the Registry applies tenant scope and current-version selection."""
        raw = self._fetch_canonical_persona_binding_view("persona_binding_counts")
        if not isinstance(raw, dict):
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry count payload", "strategy_store_unavailable")
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in raw.values()):
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry count payload", "strategy_store_unavailable")
        return {str(k): int(v) for k, v in raw.items()}

    def _fetch_canonical_persona_binding_view(self, view: str) -> Any:
        """Registry flat owner view (counts / labels): caller authorization and selected tenant forwarded as-is."""
        import urllib.error
        from services.control_plane.bff.command_adapters.base import ActionUnavailableError, http_request_json, registry_url
        from services.control_plane.bff.core.owner_reads import authorization, selected_tenant, verified_caller_tenant

        auth = authorization.get()
        if not auth:
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Canonical strategy registry readback requires caller authorization", "strategy_store_unavailable")
        try:
            caller_tenant = verified_caller_tenant(tenant_id=selected_tenant.get())
            raw = http_request_json(registry_url(f"/api/registry/strategy-specs?view={view}"), auth_token=auth.removeprefix("Bearer ").strip(), tenant_id=caller_tenant)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise self._strategy_owner_error(403, "FORBIDDEN", "Strategy reference is outside the caller tenant audience", "strategy_audience_mismatch") from exc
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Canonical strategy registry readback is unavailable", f"strategy_registry_http_{exc.code}") from exc
        except ActionUnavailableError as exc:
            if exc.status_code == 403:
                raise self._strategy_owner_error(403, "FORBIDDEN", str(exc.detail), "strategy_audience_mismatch") from exc
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", str(exc.detail), "strategy_store_unavailable") from exc
        except Exception as exc:
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Canonical strategy registry readback is unavailable", str(exc) or "strategy_store_unavailable") from exc
        return raw

    def _fetch_canonical_strategy_specs_page(
        self,
        *,
        state: Optional[str] = None,
        exclude_state: Optional[str] = None,
        persona_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        from services.control_plane.bff.core.owner_reads import cached_owner_call, selected_tenant

        norm_state = str(state).strip().lower() if state and str(state).strip() else None
        norm_exclude_state = (
            str(exclude_state).strip().lower()
            if exclude_state and str(exclude_state).strip()
            else None
        )
        if norm_state and norm_exclude_state:
            raise self._strategy_owner_error(
                400,
                "VALIDATION_ERROR",
                "state and exclude_state cannot be combined",
                "conflicting_state_filters",
            )

        if page_token is not None and str(page_token).strip() != "":
            try:
                if int(str(page_token).strip()) < 0:
                    raise ValueError("negative")
            except ValueError as exc:
                raise self._strategy_owner_error(
                    400, "VALIDATION_ERROR", "Invalid page_token", "invalid_page_token",
                ) from exc

        norm_persona = str(persona_id).strip() if persona_id and str(persona_id).strip() else None
        norm_token = str(page_token).strip() if page_token is not None and str(page_token).strip() != "" else None
        return cached_owner_call(
            (
                "rks_canonical_strategy_specs_page",
                id(self),
                selected_tenant.get(),
                norm_state,
                norm_exclude_state,
                norm_persona,
                norm_token,
                int(page_size),
            ),
            lambda: self._fetch_canonical_strategy_specs_page_uncached(
                state=norm_state,
                exclude_state=norm_exclude_state,
                persona_id=norm_persona,
                page_token=norm_token,
                page_size=int(page_size),
            ),
        )

    def _fetch_canonical_strategy_specs_page_uncached(
        self,
        *,
        state: Optional[str] = None,
        exclude_state: Optional[str] = None,
        persona_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        import urllib.error, urllib.parse
        from services.control_plane.bff.command_adapters.base import ActionUnavailableError, http_request_json, registry_url
        from services.control_plane.bff.core.owner_reads import authorization, selected_tenant, verified_caller_tenant

        auth = authorization.get()
        if not auth:
            raise self._strategy_owner_error(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "Canonical strategy registry readback requires caller authorization",
                "strategy_store_unavailable",
            )
        query_params: list[tuple[str, str]] = [
            ("view", "strategy_summary_page"),
            ("page_size", str(page_size)),
        ]
        if state:
            query_params.append(("state", state))
        if exclude_state:
            query_params.append(("exclude_state", exclude_state))
        if persona_id:
            query_params.append(("persona_id", persona_id))
        if page_token:
            query_params.append(("page_token", page_token))
        path = "/api/registry/strategy-specs?" + urllib.parse.urlencode(query_params)
        try:
            caller_tenant = verified_caller_tenant(tenant_id=selected_tenant.get())
            raw = http_request_json(
                registry_url(path),
                auth_token=auth.removeprefix("Bearer ").strip(),
                tenant_id=caller_tenant,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                if state and exclude_state:
                    raise self._strategy_owner_error(
                        400,
                        "VALIDATION_ERROR",
                        "state and exclude_state cannot be combined",
                        "conflicting_state_filters",
                    ) from exc
                raise self._strategy_owner_error(
                    400, "VALIDATION_ERROR", "Invalid page_token", "invalid_page_token",
                ) from exc
            if exc.code in (401, 403):
                raise self._strategy_owner_error(
                    403,
                    "FORBIDDEN",
                    "Strategy reference is outside the caller tenant audience",
                    "strategy_audience_mismatch",
                ) from exc
            raise self._strategy_owner_error(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "Canonical strategy registry readback is unavailable",
                f"strategy_registry_http_{exc.code}",
            ) from exc
        except ActionUnavailableError as exc:
            if exc.status_code == 403:
                raise self._strategy_owner_error(
                    403, "FORBIDDEN", str(exc.detail), "strategy_audience_mismatch",
                ) from exc
            raise self._strategy_owner_error(
                503, "DEPENDENCY_UNAVAILABLE", str(exc.detail), "strategy_store_unavailable",
            ) from exc
        except Exception as exc:
            raise self._strategy_owner_error(
                503,
                "DEPENDENCY_UNAVAILABLE",
                "Canonical strategy registry readback is unavailable",
                str(exc) or "strategy_store_unavailable",
            ) from exc

        if not isinstance(raw, dict):
            raise self._strategy_owner_error(
                503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry payload", "strategy_store_unavailable",
            )
        raw_items = raw.get("items")
        total = raw.get("total")
        next_page_token = raw.get("next_page_token")
        if (
            not isinstance(raw_items, list)
            or not isinstance(total, int)
            or isinstance(total, bool)
            or total < 0
            or (next_page_token is not None and not isinstance(next_page_token, str))
        ):
            raise self._strategy_owner_error(
                503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry payload", "strategy_store_unavailable",
            )
        validated_items: List[Dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                raise self._strategy_owner_error(
                    503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry entry", "strategy_store_unavailable",
                )
            sid = str(item.get("strategy_id") or "").strip()
            ver_id = str(item.get("current_spec_version_id") or item.get("strategy_spec_registry_id") or "").strip()
            semver = str(item.get("current_spec_version") or "").strip()
            if not sid or not ver_id or not semver:
                raise self._strategy_owner_error(
                    503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry entry", "strategy_store_unavailable",
                )
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            item_tenant = str(item.get("tenant_id") or meta.get("tenant_id") or "").strip()
            if item_tenant and item_tenant not in {caller_tenant, "*", "__builtin__"}:
                raise self._strategy_owner_error(
                    403,
                    "FORBIDDEN",
                    "Strategy reference is outside the caller tenant audience",
                    "strategy_audience_mismatch",
                )
            row = dict(item)
            resolved_tenant = item_tenant or caller_tenant
            row["tenant_id"] = resolved_tenant
            obj_ref = row.get("object_ref")
            if isinstance(obj_ref, dict) and not obj_ref.get("tenant_id"):
                row["object_ref"] = {**obj_ref, "tenant_id": resolved_tenant}
            validated_items.append(row)
        raw_state_counts = raw.get("state_counts")
        validated_state_counts: Optional[Dict[str, int]] = None
        if (
            isinstance(raw_state_counts, dict)
            and raw_state_counts
            and all(
                isinstance(k, str)
                and k.strip()
                and isinstance(v, int)
                and not isinstance(v, bool)
                and v >= 0
                for k, v in raw_state_counts.items()
            )
        ):
            validated_state_counts = {
                str(k).strip(): int(v) for k, v in raw_state_counts.items()
            }
        return {
            "items": validated_items,
            "total": total,
            "next_page_token": next_page_token,
            "state_counts": validated_state_counts,
        }

    def _fetch_canonical_strategy_specs(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sid = str(strategy_id).strip() if strategy_id is not None else ""
        from services.control_plane.bff.core.owner_reads import cached_owner_call, selected_tenant

        key = (
            ("rks_canonical_strategy_specs", id(self), selected_tenant.get())
            if not sid
            else ("rks_canonical_strategy_specs", id(self), selected_tenant.get(), sid)
        )
        return cached_owner_call(
            key,
            lambda: self._fetch_canonical_strategy_specs_uncached(sid),
        )

    def _fetch_canonical_strategy_specs_uncached(self, sid: str = "") -> List[Dict[str, Any]]:
        import urllib.error, urllib.parse
        from services.control_plane.bff.command_adapters.base import ActionUnavailableError, http_request_json, registry_url
        from services.control_plane.bff.core.owner_reads import authorization, selected_tenant, verified_caller_tenant

        auth = authorization.get()
        if not auth:
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Canonical strategy registry readback requires caller authorization", "strategy_store_unavailable")
        try:
            caller_tenant = verified_caller_tenant(tenant_id=selected_tenant.get())
            path = f"/api/registry/strategies/{urllib.parse.quote(sid, safe='')}/strategy-specs" if sid else "/api/registry/strategy-specs"
            raw_entries = http_request_json(registry_url(path), auth_token=auth.removeprefix("Bearer ").strip(), tenant_id=caller_tenant)
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise self._strategy_owner_error(403, "FORBIDDEN", "Strategy reference is outside the caller tenant audience", "strategy_audience_mismatch") from exc
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Canonical strategy registry readback is unavailable", f"strategy_registry_http_{exc.code}") from exc
        except ActionUnavailableError as exc:
            if exc.status_code == 403:
                raise self._strategy_owner_error(403, "FORBIDDEN", str(exc.detail), "strategy_audience_mismatch") from exc
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", str(exc.detail), "strategy_store_unavailable") from exc
        except Exception as exc:
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Canonical strategy registry readback is unavailable", str(exc) or "strategy_store_unavailable") from exc

        if not isinstance(raw_entries, list):
            raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry payload", "strategy_store_unavailable")
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in raw_entries:
            if not isinstance(item, dict) or ("entry" in item and not isinstance(item.get("entry"), dict)):
                raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry entry", "strategy_store_unavailable")
            entry = item["entry"] if isinstance(item.get("entry"), dict) else item
            if entry.get("artifact_type") not in (None, "strategy_spec"):
                raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry entry", "strategy_store_unavailable")
            meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
            inner = meta.get("strategy_spec") if isinstance(meta.get("strategy_spec"), dict) else {}
            inner_meta = inner.get("metadata") if isinstance(inner.get("metadata"), dict) else {}
            entry_tenant = str(entry.get("owner_tenant") or meta.get("tenant_id") or inner_meta.get("tenant_id") or "").strip()
            if entry_tenant and entry_tenant not in {caller_tenant, "*", "__builtin__"}:
                raise self._strategy_owner_error(403, "FORBIDDEN", "Strategy reference is outside the caller tenant audience", "strategy_audience_mismatch")
            entry_sid = str(entry.get("strategy_id") or inner.get("strategy_id") or "").strip()
            reg_id = str(entry.get("registry_id") or meta.get("spec_version_id") or "").strip()
            semver = str(entry.get("version") or inner.get("spec_version") or meta.get("spec_version") or meta.get("version") or "").strip()
            if not entry_sid or (sid and entry_sid != sid) or not reg_id or not semver:
                raise self._strategy_owner_error(503, "DEPENDENCY_UNAVAILABLE", "Invalid strategy registry entry", "strategy_store_unavailable")
            lineage = entry.get("lineage") if isinstance(entry.get("lineage"), dict) else {}
            parents = lineage.get("parent_registry_ids") if isinstance(lineage.get("parent_registry_ids"), list) else []
            parent_id = (parents[0] if parents else None) or lineage.get("source_strategy_spec_id") or inner.get("parent_spec_version_id") or meta.get("parent_spec_version_id")
            eval_summary = entry.get("evaluation_summary") if isinstance(entry.get("evaluation_summary"), dict) else {}
            prov = inner.get("provenance") if isinstance(inner.get("provenance"), dict) else (meta.get("provenance") if isinstance(meta.get("provenance"), dict) else {})
            persona_ids = list(inner.get("persona_ids") or meta.get("personaIds") or meta.get("persona_ids") or ([meta["persona_id"]] if meta.get("persona_id") else ([inner_meta["persona_id"]] if inner_meta.get("persona_id") else [])))
            state = entry.get("artifact_state") or inner.get("lifecycle_state") or meta.get("lifecycle_state") or meta.get("state") or meta.get("status") or "draft"
            title = inner.get("title") or meta.get("title") or meta.get("name") or entry_sid
            risk = meta.get("risk") or inner.get("risk") or eval_summary.get("risk_level") or (inner.get("governance") or {}).get("risk_level") or (meta.get("governance") or {}).get("risk_level")
            merged_meta = {**inner_meta, **{k: v for k, v in meta.items() if v is not None or k not in inner_meta}}
            if isinstance(inner_meta.get("traits"), dict) and isinstance(meta.get("traits"), dict):
                merged_meta["traits"] = {
                    **meta["traits"],
                    **{k: v for k, v in inner_meta["traits"].items() if v is not None},
                }
            # Replication-gate admission-time mechanism record: stored on the entry's
            # replication_proof (and entry top level), projected so candidate readers see it.
            proof = entry.get("replication_proof") if isinstance(entry.get("replication_proof"), dict) else {}
            for mkey in ("mechanism", "mechanism_status", "mechanism_support"):
                if merged_meta.get(mkey) is not None:
                    continue
                for holder in (entry, proof):
                    if holder.get(mkey) is not None:
                        merged_meta[mkey] = holder[mkey]
                        break
            ver_dict: Dict[str, Any] = {
                **inner, "strategy_id": entry_sid, "tenant_id": entry_tenant or caller_tenant,
                "strategy_spec_registry_id": reg_id, "spec_version_id": reg_id,
                "spec_version": semver, "semantic_version": str(inner.get("spec_version") or semver),
                "parent_spec_version_id": parent_id, "lifecycle_state": state, "title": title, "name": title,
                "persona_ids": persona_ids, "provenance": prov,
                "created_at": entry.get("created_at") or prov.get("created_at"),
                "updated_at": entry.get("updated_at") or entry.get("created_at"),
                "created_by": prov.get("created_by") or meta.get("requested_by") or (entry.get("last_actor") or {}).get("actor_id"),
            }
            if merged_meta:
                ver_dict["metadata"] = merged_meta
            if risk:
                ver_dict["risk"] = risk
            grouped.setdefault(entry_sid, []).append(ver_dict)

        specs: List[Dict[str, Any]] = []
        for entry_sid, versions in grouped.items():
            ordered = self._kw05_sort_versions(versions)
            latest = ordered[0]
            spec_entry: Dict[str, Any] = {
                "strategy_id": entry_sid, "tenant_id": latest.get("tenant_id"),
                "title": latest.get("title"), "name": latest.get("name"),
                "lifecycle_state": latest.get("lifecycle_state"), "risk": latest.get("risk"),
                "current_spec_version_id": latest.get("spec_version_id"),
                "current_spec_version": latest.get("spec_version"), "versions": ordered,
            }
            if latest.get("metadata"):
                spec_entry["metadata"] = latest.get("metadata")
            specs.append(spec_entry)
        return specs

    def _resolve_canonical_strategy_spec(self, strategy_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not strategy_id:
            return None
        sid = str(strategy_id).strip()
        if self._has_injected_strategy_specs and not (os.getenv("PANTHEON_REGISTRY_API_URL") or os.getenv("PANTHEON_REGISTRY_URL")):
            cached = self._strategy_specs.get(sid)
            return cached if isinstance(cached, dict) else None
        specs = self._fetch_canonical_strategy_specs(sid)
        return specs[0] if specs else None

    def get_strategy_spec_detail(
        self,
        strategy_id: Optional[str],
        *,
        version_selector: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not strategy_id:
            return None
        strategy_spec = self._resolve_canonical_strategy_spec(strategy_id)
        if not isinstance(strategy_spec, dict):
            return None
        version = self._kw05_find_version(strategy_spec, version_selector)
        return json.loads(json.dumps(version)) if version else None

    def list_strategy_spec_versions(self, strategy_id: Optional[str]) -> List[Dict[str, Any]]:
        if not strategy_id:
            return []
        strategy_spec = self._resolve_canonical_strategy_spec(strategy_id)
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
        strategy_spec = self._resolve_canonical_strategy_spec(strategy_id)
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
        write_owner = self._get_research_write_owner()
        if write_owner is not None and hasattr(write_owner, "list_research_tickets"):
            return write_owner.list_research_tickets(statuses=statuses, owner=owner)
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
        write_owner = self._get_research_write_owner()
        if write_owner is not None and hasattr(write_owner, "get_research_ticket"):
            return write_owner.get_research_ticket(ticket_id)
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
        write_owner = self._get_research_write_owner()
        if write_owner is None or not hasattr(write_owner, "create_research_ticket"):
            raise ResearchWriteOwnerUnavailableError(
                "Research write owner is not configured; cannot create a research ticket."
            )
        result = write_owner.create_research_ticket(
            title=title,
            description=description,
            priority=priority,
            owner=owner,
            actor_id=actor_id,
            created_at=created_at,
        )
        if isinstance(result, dict) and "ticket_id" in result:
            self._tickets[str(result["ticket_id"])] = dict(result)
            return self._project_research_ticket_detail(result)
        return result

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str,
        updated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        write_owner = self._get_research_write_owner()
        if write_owner is None or not hasattr(write_owner, "patch_research_ticket"):
            raise ResearchWriteOwnerUnavailableError(
                f"Research write owner is not configured; cannot patch research ticket {ticket_id!r}."
            )
        result = write_owner.patch_research_ticket(ticket_id, patch=patch, actor_id=actor_id, updated_at=updated_at)
        if isinstance(result, dict) and "ticket_id" in result:
            self._tickets[str(result["ticket_id"])] = dict(result)
            return self._project_research_ticket_detail(result)
        return result

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
    # Research Experiments (RW-04) - delegate exclusively to ResearchWriteOwner
    # -------------------------------------------------------------------------
    # ACG-02-003 (REMOVE), BFF-RESEARCH-JOBS-OWNER-BINDING-CORRECTIVE-001:
    # the in-memory ``self._experiments`` dictionary and its bff-local
    # projection helpers are deleted. `ResearchExperiment` is durably owned by
    # `services.research.write_owner.ResearchWriteOwner`
    # (`research.research_experiments` in Postgres); this port is a thin,
    # fail-closed pass-through. There is no fallback to memory: if the owner
    # cannot be resolved, every method below raises
    # ``ResearchWriteOwnerUnavailableError`` for the caller to map to a 500/503.
    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        owner = self._get_research_write_owner()
        if owner is None:
            raise ResearchWriteOwnerUnavailableError(
                "Research write owner (Postgres) is not configured; cannot list research experiments."
            )
        return owner.list_research_experiments(ticket_id=ticket_id, status=status)

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not experiment_id:
            return None
        owner = self._get_research_write_owner()
        if owner is None:
            raise ResearchWriteOwnerUnavailableError(
                "Research write owner (Postgres) is not configured; cannot read research experiment "
                f"{experiment_id!r}."
            )
        return owner.get_research_experiment(experiment_id)

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
        owner = self._get_research_write_owner()
        if owner is None:
            raise ResearchWriteOwnerUnavailableError(
                "Research write owner (Postgres) is not configured; cannot create a research experiment."
            )
        return owner.create_research_experiment(
            ticket_id=ticket_id,
            experiment_name=experiment_name,
            strategy_selector=strategy_selector,
            parameter_set=parameter_set,
            run_config=run_config,
            launch_context=launch_context,
            queued_at=queued_at,
        )

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        owner = self._get_research_write_owner()
        if owner is None:
            raise ResearchWriteOwnerUnavailableError(
                "Research write owner (Postgres) is not configured; cannot cancel research experiment "
                f"{experiment_id!r}."
            )
        return owner.cancel_research_experiment(experiment_id, completed_at=completed_at)

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
        if not lineage_id:
            return []
        artifacts = list(self._artifacts.values())
        chain = [a for a in artifacts if str(a.get("lineage_id") or "") == str(lineage_id or "")]
        chain.sort(key=lambda a: (int(a.get("version") or 0), str(a.get("created_at") or "")))
        return chain

    def _rw05_is_current_version(self, artifact: Dict[str, Any]) -> Optional[bool]:
        if artifact.get("saved_output"):
            return None  # a saved run output is not a versioned RW-05 record
        lineage_id = artifact.get("lineage_id")
        if not lineage_id:
            return True
        chain = self._rw05_lineage_versions(lineage_id)
        return bool(chain and chain[-1].get("artifact_id") == artifact.get("artifact_id"))

    @staticmethod
    def _saved_output_fields(artifact: Dict[str, Any]) -> Dict[str, Any]:
        saved = artifact.get("saved_output")
        if not isinstance(saved, dict):
            return {}
        return {
            "source_kind": "research_run_output",
            "saved_output": json.loads(json.dumps(saved)),
            "compare_unavailable_reason": "Saved Research run output is not a sealed or superseded versioned artifact.",
        }

    @staticmethod
    def _saved_output_artifact(row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize an owner run-output row; version/lineage/status/metrics stay absent."""
        run = row.get("run") if isinstance(row.get("run"), dict) else {}
        return {
            "artifact_id": row.get("artifact_id"),
            "name": row.get("title"),
            "artifact_type": row.get("artifact_type"),
            "status": None,
            "created_at": row.get("created_at"),
            "author": row.get("created_by"),
            "tags": [],
            "research_linkage": {k: row.get(k) for k in ("run_id", "task_id", "stage_id") if row.get(k)},
            "saved_output": {
                "run_id": row.get("run_id"),
                "task_id": row.get("task_id"),
                "receipt_id": run.get("receipt_id"),
                "checksum": row.get("checksum") or run.get("artifact_digest"),
                "storage_ref": row.get("storage_ref"),
                "provenance": row.get("provenance") or run.get("provenance"),
                "run_status": run.get("status"),
                "completed_at": run.get("completed_at"),
                "artifact_family": row.get("artifact_family"),
                "stage_type": row.get("stage_type"),
                "tenant_id": row.get("tenant_id"),
            },
        }

    def _owner_artifact_rows(self) -> Optional[List[Dict[str, Any]]]:
        """Saved Research owner outputs; ``None`` when no owner read is configured. Owner failures raise."""
        if self._artifacts:
            return None
        owner = self._get_research_write_owner()
        if owner is None or not hasattr(owner, "list_research_artifacts"):
            return None
        return [self._saved_output_artifact(r) for r in owner.list_research_artifacts() if isinstance(r, dict)]

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
            **self._saved_output_fields(artifact),
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
            **self._saved_output_fields(artifact),
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
        owner_rows = self._owner_artifact_rows()
        artifacts = owner_rows if owner_rows is not None else list(self._artifacts.values())
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
        if artifact is None and not self._artifacts:
            owner = self._get_research_write_owner()
            if owner is not None and hasattr(owner, "get_research_artifact"):
                row = owner.get_research_artifact(str(artifact_id))
                artifact = self._saved_output_artifact(row) if isinstance(row, dict) else None
        return self._project_research_artifact_detail(artifact) if isinstance(artifact, dict) else None

    def _resolve_registry_base_url(self) -> str:
        return (
            self._registry_service_url
            or os.getenv("PANTHEON_REGISTRY_API_URL")
            or os.getenv("PANTHEON_REGISTRY_URL")
            or ""
        ).strip().rstrip("/")

    def _registry_http_get(self, base_url: str, path: str) -> Tuple[int, Any]:
        if self._has_custom_http_get:
            ok, payload = self._http_get(base_url, path)
            return (200 if ok else 503), payload
        if not base_url:
            return 0, None
        import urllib.error
        import urllib.request
        from ..core.owner_reads import authorization, selected_tenant

        auth_val = authorization.get()
        if not auth_val:
            svc_tok = (
                os.getenv("PANTHEON_REGISTRY_SERVICE_TOKEN")
                or os.getenv("PANTHEON_SERVICE_TOKEN")
                or ""
            ).strip()
            if svc_tok:
                auth_val = svc_tok if svc_tok.lower().startswith("bearer ") else f"Bearer {svc_tok}"
        headers = {
            k: v
            for k, v in (
                ("Authorization", auth_val),
                ("X-Tenant-Id", selected_tenant.get()),
            )
            if v
        }
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=5.0) as resp:
                raw = resp.read()
                payload = json.loads(raw.decode("utf-8")) if raw else None
                return int(getattr(resp, "status", 200) or 200), payload
        except urllib.error.HTTPError as exc:
            return int(exc.code or 500), None
        except Exception:
            return 0, None

    def read_registry_strategy_artifacts(self) -> ArtifactOwnerReadResult:
        base_url = self._resolve_registry_base_url()
        if self._has_injected_registry_strategy_artifacts and not base_url:
            items = [
                normalize_registry_catalog_row(row)
                for row in self._registry_strategy_artifacts.values()
                if isinstance(row, Mapping)
                and str(
                    (row.get("entry") if isinstance(row.get("entry"), Mapping) else row).get("artifact_type")
                    or "execution_bundle"
                )
                == "execution_bundle"
            ]
            return ArtifactOwnerReadResult(items, "ok", "typed_store")
        if not base_url:
            return ArtifactOwnerReadResult([], "unconfigured", "missing")
        items: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        seen_tokens: set[str] = set()
        from urllib.parse import quote

        while True:
            path = "/api/registry/strategy-artifacts?page_size=500"
            if page_token is not None:
                path = f"{path}&page_token={quote(str(page_token), safe='')}"
            status_code, payload = self._registry_http_get(base_url, path)
            if status_code != 200 or not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                return ArtifactOwnerReadResult(
                    [],
                    "failed",
                    "unavailable",
                    f"Registry strategy-artifacts read failed (status={status_code})",
                )
            for raw_item in payload["items"]:
                if not isinstance(raw_item, Mapping):
                    continue
                entry = raw_item.get("entry") if isinstance(raw_item.get("entry"), Mapping) else raw_item
                if entry.get("artifact_type") not in (None, "execution_bundle"):
                    continue
                items.append(normalize_registry_catalog_row(entry))
            next_token = payload.get("next_page_token")
            if next_token is None and isinstance(payload.get("page_info"), Mapping):
                next_token = payload["page_info"].get("next_page_token")
            if not next_token or str(next_token) in seen_tokens:
                break
            seen_tokens.add(str(next_token))
            page_token = str(next_token)
        return ArtifactOwnerReadResult(items, "ok", "service_client")

    def read_registry_strategy_artifact_detail(
        self, artifact_id: Optional[str]
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        clean_id = str(artifact_id or "").strip()
        if not clean_id:
            return None, "ok"
        base_url = self._resolve_registry_base_url()
        if self._has_injected_registry_strategy_artifacts and not base_url:
            row = self._registry_strategy_artifacts.get(clean_id)
            if not isinstance(row, Mapping):
                return None, "ok"
            entry = row.get("entry") if isinstance(row.get("entry"), Mapping) else row
            if entry.get("artifact_type") not in (None, "execution_bundle"):
                return None, "ok"
            return project_registry_catalog_detail(entry), "ok"
        if not base_url:
            return None, "unconfigured"
        from urllib.parse import quote

        status_code, payload = self._registry_http_get(
            base_url,
            f"/api/registry/entries/{quote(clean_id, safe='')}",
        )
        if status_code in (404, 403):
            return None, "ok"
        if status_code != 200 or not isinstance(payload, dict):
            return None, "failed"
        entry = payload.get("entry") if isinstance(payload.get("entry"), Mapping) else payload
        if not isinstance(entry, Mapping) or entry.get("artifact_type") not in (None, "execution_bundle"):
            return None, "ok"
        return project_registry_catalog_detail(entry), "ok"

    def read_registry_strategy_artifacts_for_strategy(
        self, strategy_id: Optional[str]
    ) -> ArtifactOwnerReadResult:
        """Approved execution bundles of one strategy, as raw registry entries.

        Uses the owner route ``GET /api/registry/strategies/{strategy_id}/strategy-artifacts``
        (one targeted SQL predicate on ``strategy_id`` in the registry store,
        then a state filter), so lineage and metadata stay intact and the read
        never loads the whole artifact catalog.
        """
        clean_id = str(strategy_id or "").strip()
        if not clean_id:
            return ArtifactOwnerReadResult([], "ok", "missing")
        base_url = self._resolve_registry_base_url()
        if self._has_injected_registry_strategy_artifacts and not base_url:
            items: List[Dict[str, Any]] = []
            for row in self._registry_strategy_artifacts.values():
                if not isinstance(row, Mapping):
                    continue
                entry = row.get("entry") if isinstance(row.get("entry"), Mapping) else row
                if str(entry.get("strategy_id") or "").strip() != clean_id:
                    continue
                if entry.get("artifact_type") not in (None, "execution_bundle"):
                    continue
                items.append(dict(entry))
            return ArtifactOwnerReadResult(items, "ok", "typed_store")
        if not base_url:
            return ArtifactOwnerReadResult([], "unconfigured", "missing")
        from urllib.parse import quote

        status_code, payload = self._registry_http_get(
            base_url,
            f"/api/registry/strategies/{quote(clean_id, safe='')}/strategy-artifacts"
            "?artifact_state=approved",
        )
        if status_code != 200 or not isinstance(payload, list):
            return ArtifactOwnerReadResult(
                [],
                "failed",
                "unavailable",
                f"Registry strategy-artifacts read for strategy failed (status={status_code})",
            )
        items = []
        for raw_item in payload:
            if not isinstance(raw_item, Mapping):
                continue
            entry = raw_item.get("entry") if isinstance(raw_item.get("entry"), Mapping) else raw_item
            items.append(dict(entry))
        return ArtifactOwnerReadResult(items, "ok", "service_client")

    def read_research_artifact_catalog_rows(self) -> ArtifactOwnerReadResult:
        if self._artifacts:
            return ArtifactOwnerReadResult(
                [normalize_research_catalog_row(a) for a in self._artifacts.values() if isinstance(a, Mapping)],
                "ok",
                "typed_store",
            )
        owner = self._get_research_write_owner()
        if owner is None or not hasattr(owner, "list_research_artifacts"):
            return ArtifactOwnerReadResult([], "unconfigured", "missing")
        try:
            raw_rows = owner.list_research_artifacts()
        except Exception as exc:
            return ArtifactOwnerReadResult([], "failed", "unavailable", str(exc))
        if not isinstance(raw_rows, list):
            return ArtifactOwnerReadResult([], "failed", "unavailable", "invalid research artifact list")
        items = [
            normalize_research_catalog_row(
                self._saved_output_artifact(r) if ("title" in r or "run" in r or "created_by" in r) else r
            )
            for r in raw_rows
            if isinstance(r, Mapping)
        ]
        return ArtifactOwnerReadResult(items, "ok", "service_client")

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
        if avail and _is_valid_health_usage_envelope(payload):
            sources = list(payload["sources"])
            return {
                "source": "service_client",
                "source_count": len(sources),
                "sources": sources,
                "recommendation_summary": dict(payload["recommendation_summary"]),
            }
        return {
            "source": "unavailable",
            "source_count": 0,
            "sources": [],
            "recommendation_summary": {},
        }
