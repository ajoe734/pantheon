"""Canonical BFF read API for Trade Journeys (TJ-E2E-005).

Server-composed list/detail/timeline/graph/resolve/evidence/replay/metrics
under ``/bff/management/trade-journeys``, built directly on the TJ-E2E-004
``JourneyMaterializer`` read model (``services/trade_journey/materializer.py``).
The frontend must not join across domains: every response here is already
fully composed, row-level scoped, and carries an explicit
``formal | partial | degraded | unavailable`` read-state plus source
freshness, per the TJ-E2E-005 task packet and the Trade Journey E2E gap spec
(``docs/04/pantheon_trade_journey_e2e_observability_gap_2026-07-11/``).

Identifier-existence protection: a journey that does not exist and a journey
that exists but is outside the caller's tenant scope both resolve to an
identical 404 ``RESOURCE_NOT_FOUND`` — the caller cannot distinguish "wrong
id" from "not yours" (gap spec section 12).
"""
from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from services.trade_journey.alert_transport import DataQualityAlertTransport, SLO_ALERT_TRANSPORT
from services.trade_journey.action_ledger import ActionLedger, MemoryActionLedger, make_action_ledger
from services.trade_journey.api_latency_recorder import ApiLatencyRecorder
from services.trade_journey.dashboard import load_dashboard, render_dashboard_snapshot
from services.trade_journey.materializer import (
    IDENTIFIER_FIELDS,
    STAGES,
    TERMINAL_STATUSES,
    JourneyMaterializer,
    JourneyProjection,
    MaterializationError,
)
from services.trade_journey.telemetry_bridge import (
    load_store_events,
    merge_with_store,
    write_store_atomic,
)
from services.trade_journey.slo_data_quality import (
    compute_data_quality_metrics,
    evaluate_data_quality,
    incident_to_dict,
    load_slo_targets,
    metrics_to_dict,
)
from trade_journey_projection_store import (
    InvalidPageToken,
    ProjectionReadError,
    ProjectionReadUnavailable,
    TradeJourneyProjectionStore,
)

EVENTS_STORE_ENV = "PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE"
PROJECTOR_STORE_SCHEMA = "pantheon.trade-journey-projection.v1"
PROJECTOR_CONTROLLER_ID = "canonical-lifecycle-projector"
_ALLOWED_ENVIRONMENTS = {"paper", "broker_sandbox", "canary", "live"}
_ANOMALY_DIAGNOSTIC_CODES = {"identifier_conflict", "conflicting_terminal_states", "orphan_identifier"}
_ALLOWED_SORTS = {"updated_at_desc", "updated_at_asc", "created_at_desc", "created_at_asc"}
_RESOLVABLE_IDENTIFIER_TYPES: Tuple[str, ...] = ("journey_id",) + IDENTIFIER_FIELDS
_STALLED_AFTER_SECONDS = 900  # 15 minutes; see gap-spec section 13 SLO discussion.
_STALLED_THRESHOLDS_ENV = "PANTHEON_BFF_TRADE_JOURNEY_STALLED_THRESHOLDS"
_DEFAULT_STALLED_THRESHOLDS = {
    "paper": {"default": 900, "broker_acknowledgement": 120, "reconciliation": 600},
    "broker_sandbox": {"default": 600, "broker_acknowledgement": 90, "reconciliation": 300},
    "canary": {"default": 300, "broker_acknowledgement": 60, "reconciliation": 180},
    "live": {"default": 180, "broker_acknowledgement": 30, "reconciliation": 120},
}
# Stages 1-6 (research_rationale .. deployment_runtime) precede signal
# generation and, per contract.md section 5.1, never carry a `journey_id` —
# they belong to the ResearchJourney/StrategyLifecycle event streams, not
# this TradeJourney's own history. The materializer's `missing_stages` is
# computed uniformly across all 14 stages, so a TradeJourney's own
# completeness must only be judged against the stages it could ever
# observe under its own journey_id (this set); pre-signal absence is
# structural, not a data-quality gap in this journey's execution truth.
_OBSERVABLE_STAGES = frozenset(STAGES[STAGES.index("signal_generation"):])
_LIVE_SENSITIVE_FIELDS = frozenset(
    {
        "account_id",
        "capital_account_id",
        "order_id",
        "client_order_id",
        "broker_order_id",
        "quantity",
        "price",
    }
)
_LIVE_SENSITIVE_IDENTIFIER_TYPES = frozenset(
    {"order_id", "client_order_id", "broker_order_id"}
)
_LIVE_SENSITIVE_VISIBLE_ROLES = {"operator", "approver", "admin", "reviewer"}

ReadState = Literal["formal", "partial", "degraded", "unavailable"]

_JOURNEY_ACTIONS = {
    "escalate", "human_review", "pause", "cancel",
    "reconciliation_retry", "incident_acknowledge",
}
_LIVE_ACTIONS = {"pause", "cancel", "reconciliation_retry"}


def _projector_store_metadata(raw: Any) -> Dict[str, Any]:
    """Return truth metadata only for the lifecycle-projector-owned wrapper."""
    if not isinstance(raw, Mapping):
        return {}
    schema_version = str(raw.get("schema_version") or "")
    raw_controller = raw.get("controller")
    controller = dict(raw_controller) if isinstance(raw_controller, Mapping) else {}
    controller_id = str(
        controller.get("controller_id") or controller.get("controller_name") or ""
    )
    projector_owned = (
        schema_version == PROJECTOR_STORE_SCHEMA
        or controller_id == PROJECTOR_CONTROLLER_ID
    )
    if not projector_owned:
        return {}
    return {
        "schema_version": schema_version or PROJECTOR_STORE_SCHEMA,
        "generation": raw.get("generation"),
        "projector_owned": True,
        "controller": controller,
    }


class TradeJourneyActionRequest(BaseModel):
    action: str
    reason: str = Field(min_length=3, max_length=1000)
    expected_revision: int = Field(ge=0)
    confirm_token: str = Field(min_length=1)
    incident_id: Optional[str] = None
    return_to: Optional[str] = None


class TradeJourneyActionEnvelope(BaseModel):
    data: Dict[str, Any]
    meta: Dict[str, Any]


JourneyActionLedger = MemoryActionLedger  # compatibility for focused tests
ACTION_LEDGER = make_action_ledger()


def _unconfigured_action_dispatcher(command: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "partial_failure",
        "code": "ACTION_DISPATCH_UNAVAILABLE",
        "message": "Canonical governed command dispatcher is unavailable",
        "readback": None,
    }


# --------------------------------------------------------------------------- #
# Response DTOs
# --------------------------------------------------------------------------- #


class TradeJourneyFreshness(BaseModel):
    materializer_revision: int
    rebuild_status: str
    source_watermarks: Dict[str, str] = Field(default_factory=dict)
    projection_schema_version: Optional[str] = None
    generation: Optional[int] = None
    projector_owned: bool = False
    projection_mode: Optional[str] = None
    truth_level: Optional[str] = None
    accepted_live: Optional[bool] = None
    controller: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


class TradeJourneyMeta(BaseModel):
    snapshot_at: str
    read_state: ReadState
    freshness: TradeJourneyFreshness

    model_config = ConfigDict(extra="allow")


class TradeJourneyPageInfo(BaseModel):
    next_page_token: Optional[str] = None
    total: int = 0
    page_size: Optional[int] = None
    returned: Optional[int] = None
    has_more: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class TradeJourneyListEnvelope(BaseModel):
    data: Dict[str, Any]
    page_info: TradeJourneyPageInfo
    meta: TradeJourneyMeta

    model_config = ConfigDict(extra="allow")


class TradeJourneyDetailEnvelope(BaseModel):
    data: Dict[str, Any]
    meta: TradeJourneyMeta

    model_config = ConfigDict(extra="allow")


# --------------------------------------------------------------------------- #
# Event store adapter
# --------------------------------------------------------------------------- #


class TradeJourneyEventStore:
    """Loads raw journey events from a JSON file and materializes on demand.

    The materializer is cached by ``(path, mtime)`` so repeated reads within a
    request burst do not re-run ``rebuild()``; a changed source file
    invalidates the cache automatically. Tests may monkeypatch
    ``materializer()``/``events()`` directly instead of writing a fixture
    file (see ``test_bff_lineage_contract.py`` for the pattern this mirrors).
    """

    def __init__(self, path_env: str = EVENTS_STORE_ENV) -> None:
        self._path_env = path_env
        self._cache_key: Optional[Tuple[str, float]] = None
        self._materializer: Optional[JourneyMaterializer] = None
        self._events: List[Dict[str, Any]] = []
        self._projection_metadata: Dict[str, Any] = {}

    def _source_path(self) -> Optional[Path]:
        raw = os.getenv(self._path_env, "").strip()
        if not raw:
            return None
        path = Path(raw)
        return path if path.is_file() else None

    def _refresh(self) -> bool:
        path = self._source_path()
        if path is None:
            self._materializer = None
            self._events = []
            self._projection_metadata = {}
            self._cache_key = None
            return False
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._materializer = None
            self._events = []
            self._projection_metadata = {}
            self._cache_key = None
            return False
        key = (str(path), mtime)
        if key == self._cache_key and self._materializer is not None:
            return True
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._materializer = None
            self._events = []
            self._projection_metadata = {}
            self._cache_key = None
            return False
        self._projection_metadata = _projector_store_metadata(raw)
        if isinstance(raw, list):
            events = [dict(item) for item in raw if isinstance(item, Mapping)]
        elif isinstance(raw, Mapping) and isinstance(raw.get("events"), list):
            events = [dict(item) for item in raw["events"] if isinstance(item, Mapping)]
        else:
            events = []
        materializer = JourneyMaterializer()
        try:
            materializer.rebuild(events)
        except MaterializationError:
            self._materializer = None
            self._events = []
            self._cache_key = None
            return False
        self._materializer = materializer
        self._events = events
        self._cache_key = key
        return True

    def materializer(self) -> Optional[JourneyMaterializer]:
        return self._materializer if self._refresh() else None

    def events(self) -> List[Dict[str, Any]]:
        self._refresh()
        return list(self._events)

    def projection_metadata(self) -> Dict[str, Any]:
        """Expose cached projector/controller truth for response composition."""
        self._refresh()
        return json.loads(json.dumps(self._projection_metadata))

    def projector_owned(self) -> bool:
        return bool(self.projection_metadata().get("projector_owned"))


EVENT_STORE = TradeJourneyEventStore()

# TJ-E2E-011: real request-latency samples for the `detail`/`resolve` p95
# SLO targets (gap-spec section 13), and the governed alert transport that
# `slo` publishes every emitted `DataQualityIncident` through.
API_LATENCY_RECORDER = ApiLatencyRecorder()


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _err(status: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "retryable": retryable}})


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _normalize_bound(value: Optional[str]) -> Optional[str]:
    """Reformat a caller-supplied timestamp bound to the same fixed-width UTC
    form the materializer normalizes stored `occurred_at`/`updated_at`
    values to, so plain string comparison stays monotonic (see the
    TJ-E2E-004 mixed-precision ordering fix this deliberately avoids
    re-breaking)."""
    parsed = _parse_iso(value)
    if parsed is None:
        return None
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decode_page_token(token: Optional[str]) -> int:
    if not token:
        return 0
    try:
        value = int(token)
    except (TypeError, ValueError):
        return 0
    return max(0, value)


def _tenant_allowed(identity: Any, tenant_id: str) -> bool:
    roles = set(getattr(identity, "roles", None) or [])
    if "admin" in roles:
        return True
    claims = getattr(identity, "claims", None) or {}
    # Product OIDC and the server-bound dev-login exchange use the canonical
    # ``allowed_tenants``/``tenant_id`` claim family.  Keep accepting the
    # legacy ``tenant_ids`` aliases, but never treat a real dev-login token as
    # unscoped merely because it does not carry that legacy spelling.
    scoped = (
        claims.get("allowed_tenants")
        or claims.get("allowedTenants")
        or claims.get("tenant_ids")
        or claims.get("tenantIds")
        or claims.get("tenant_id")
        or claims.get("tenantId")
    )
    if isinstance(scoped, str):
        scoped = [item.strip() for item in scoped.split(",") if item.strip()]
    if not scoped:
        return True
    return tenant_id in scoped


def _can_view_live_sensitive(identity: Any, environment: str) -> bool:
    if environment != "live":
        return True
    roles = set(getattr(identity, "roles", None) or [])
    return bool(roles & _LIVE_SENSITIVE_VISIBLE_ROLES)


def _mask_live_sensitive(value: Any, identity: Any, environment: str) -> Any:
    """Recursively hide live account, order, and capital values from viewers."""
    if _can_view_live_sensitive(identity, environment):
        return value

    def redact(item: Any) -> Any:
        if item in (None, ""):
            return item
        if isinstance(item, list):
            return ["***"] if item else []
        if isinstance(item, tuple):
            return ("***",) if item else ()
        return "***"

    def mask(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {
                key: redact(child) if key in _LIVE_SENSITIVE_FIELDS else mask(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [mask(child) for child in item]
        if isinstance(item, tuple):
            return tuple(mask(child) for child in item)
        return item

    masked = mask(value)
    if isinstance(masked, dict):
        masked["live_capital_masked"] = True
    return masked


def _mask_live_graph(data: Dict[str, Any], identity: Any, environment: str) -> Dict[str, Any]:
    """Remove order nodes and their edges when live identifiers are not visible."""
    if _can_view_live_sensitive(identity, environment):
        return data
    sensitive_node_types = {
        "account",
        "account_id",
        "capital_account_id",
        "order",
        *_LIVE_SENSITIVE_IDENTIFIER_TYPES,
    }
    hidden_ids = {
        str(node.get("id"))
        for node in data.get("nodes", [])
        if isinstance(node, Mapping) and node.get("type") in sensitive_node_types
    }
    masked = dict(data)
    masked["nodes"] = [
        node
        for node in data.get("nodes", [])
        if not isinstance(node, Mapping) or str(node.get("id")) not in hidden_ids
    ]
    masked["edges"] = [
        edge
        for edge in data.get("edges", [])
        if not isinstance(edge, Mapping)
        or (str(edge.get("from")) not in hidden_ids and str(edge.get("to")) not in hidden_ids)
    ]
    masked["live_capital_masked"] = True
    return masked


def _store_projection_metadata(store: Optional[TradeJourneyEventStore]) -> Dict[str, Any]:
    getter = getattr(store, "projection_metadata", None)
    if not callable(getter):
        return {}
    metadata = getter()
    return metadata if isinstance(metadata, dict) else {}


def _projector_freshness(metadata: Mapping[str, Any]) -> Dict[str, Any]:
    if not metadata.get("projector_owned"):
        return {}
    controller = metadata.get("controller")
    controller = dict(controller) if isinstance(controller, Mapping) else {}
    generation = metadata.get("generation")
    if generation is None:
        generation = controller.get("generation")
    return {
        "projection_schema_version": metadata.get("schema_version"),
        "generation": generation,
        "projector_owned": True,
        "projection_mode": controller.get("mode"),
        "truth_level": controller.get("truth_level"),
        "accepted_live": bool(controller.get("accepted_live")),
        "controller": controller,
    }


def _effective_projection_read_state(read_state: str, metadata: Mapping[str, Any]) -> str:
    if not metadata.get("projector_owned"):
        return read_state
    controller = metadata.get("controller")
    canonical_live = bool(
        isinstance(controller, Mapping)
        and controller.get("accepted_live") is True
        and controller.get("status") == "ready"
        and controller.get("mode") == "live"
        and controller.get("truth_level") == "canonical_live"
    )
    if not canonical_live and read_state != "unavailable":
        return "degraded"
    return read_state


def _meta(
    snapshot_at: str,
    read_state: str,
    materializer: JourneyMaterializer,
    *,
    store: Optional[TradeJourneyEventStore] = None,
) -> Dict[str, Any]:
    projection_metadata = _store_projection_metadata(store)
    return {
        "snapshot_at": snapshot_at,
        "read_state": _effective_projection_read_state(read_state, projection_metadata),
        "freshness": {
            "materializer_revision": materializer.revision,
            "rebuild_status": materializer.rebuild_status,
            "source_watermarks": dict(materializer.source_watermarks),
            **_projector_freshness(projection_metadata),
        },
    }


def _unavailable_meta(
    snapshot_at: str,
    *,
    store: Optional[TradeJourneyEventStore] = None,
) -> Dict[str, Any]:
    projection_metadata = _store_projection_metadata(store)
    return {
        "snapshot_at": snapshot_at,
        "read_state": "unavailable",
        "freshness": {
            "materializer_revision": 0,
            "rebuild_status": "idle",
            "source_watermarks": {},
            **_projector_freshness(projection_metadata),
        },
    }


def _unavailable_envelope(
    snapshot_at: str,
    *,
    entity_id: str,
    store: Optional[TradeJourneyEventStore] = None,
) -> Dict[str, Any]:
    return {
        "data": {"id": entity_id, "status": "unavailable"},
        "meta": _unavailable_meta(snapshot_at, store=store),
    }


def _unavailable_list_envelope(
    snapshot_at: str,
    page_size: int,
    *,
    entity_id: str,
    store: Optional[TradeJourneyEventStore] = None,
) -> Dict[str, Any]:
    return {
        "data": {"id": entity_id, "items": [], "status": "unavailable"},
        "page_info": {"next_page_token": None, "total": 0, "page_size": page_size, "returned": 0, "has_more": False},
        "meta": _unavailable_meta(snapshot_at, store=store),
    }


def _projection_reader_meta(
    snapshot_at: str,
    read_state: str,
    reader: TradeJourneyProjectionStore,
    *,
    tenant_id: str,
    environment: str,
) -> Dict[str, Any]:
    """Build the existing metadata DTO from Postgres controller truth.

    A configured Postgres reader never falls back to JSON and therefore cannot
    accidentally claim current projection truth from the legacy store.
    """
    controller = reader.controller_freshness(
        tenant_id=tenant_id,
        environment=environment,
    ) or {}
    formal = (
        controller.get("accepted_live") is True
        and controller.get("status") == "ready"
        and controller.get("mode") == "live"
    )
    return {
        "snapshot_at": snapshot_at,
        "read_state": read_state if formal or read_state == "unavailable" else "degraded",
        "freshness": {
            "materializer_revision": int(controller.get("generation") or 0),
            "rebuild_status": "postgres_projection_reader",
            "source_watermarks": {"postgres": str(controller.get("checkpoint") or 0)},
            "projection_schema_version": "pantheon.trade-journey-projection.v1",
            "generation": controller.get("generation"),
            "projector_owned": True,
            "projection_mode": controller.get("mode"),
            "truth_level": "canonical_live" if formal else "not_accepted_live",
            "accepted_live": bool(controller.get("accepted_live")),
            "controller": controller,
        },
    }


def _read_state(snapshot: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]) -> str:
    codes = {item.get("code") for item in diagnostics}
    if codes & _ANOMALY_DIAGNOSTIC_CODES:
        return "degraded"
    missing = (snapshot.get("completeness") or {}).get("missing_stages") or []
    if any(stage in _OBSERVABLE_STAGES for stage in missing):
        return "partial"
    return "formal"


def _current_stage(snapshot: Mapping[str, Any]) -> Optional[str]:
    stages = snapshot.get("stages") or {}
    for stage in reversed(STAGES):
        if stage in stages:
            return stage
    return None


def _flags(snapshot: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]) -> Dict[str, bool]:
    stages = snapshot.get("stages") or {}
    statuses = {info.get("status") for info in stages.values()}
    codes = {item.get("code") for item in diagnostics}
    return {
        "waiting_human": snapshot.get("status") == "waiting_human" or "waiting_human" in statuses,
        "blocked": snapshot.get("status") == "blocked" or "blocked" in statuses,
        "rejected": bool({"rejected", "failed"} & statuses),
        "mismatch": snapshot.get("status") == "completed_with_variance",
        "orphan": "orphan_identifier" in codes,
    }


def _severity(snapshot: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]) -> str:
    status = snapshot.get("status")
    codes = {item.get("code") for item in diagnostics}
    if status in {"failed", "incomplete"} or (codes & _ANOMALY_DIAGNOSTIC_CODES):
        return "critical"
    if status in {"blocked", "waiting_human", "completed_with_variance"}:
        return "warning"
    return "info"


def _is_stalled(snapshot: Mapping[str, Any], now: datetime) -> bool:
    if snapshot.get("status") in TERMINAL_STATUSES:
        return False
    updated = _parse_iso(snapshot.get("updated_at"))
    if updated is None:
        return False
    return (now - updated).total_seconds() > _STALLED_AFTER_SECONDS


_ROW_IDENTIFIER_KEYS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("persona_id", ("persona_id",)),
    ("strategy_id", ("strategy_id",)),
    ("strategy_version", ("strategy_version",)),
    ("artifact_id", ("artifact_id",)),
    ("candidate_id", ("candidate_id",)),
    ("decision_id", ("trade_decision_id", "decision_id")),
    ("capital_pool_id", ("capital_pool_id",)),
    ("runtime_id", ("runtime_id",)),
    ("symbol", ("symbol", "instrument_id")),
    ("side", ("side",)),
    ("quantity", ("quantity", "target_quantity")),
    ("price", ("price",)),
    ("order_id", ("order_id",)),
    ("client_order_id", ("client_order_id",)),
    ("broker_order_id", ("broker_order_id",)),
)


def _first_value(events: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> Any:
    for event in events:
        for key in keys:
            value = event.get(key)
            if value not in (None, ""):
                return value
    return None


def _compose_summary_fields(timeline: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {field: _first_value(timeline, keys) for field, keys in _ROW_IDENTIFIER_KEYS}


def _list_row(projection: JourneyProjection, *, now: datetime) -> Dict[str, Any]:
    snapshot = projection.snapshot
    diagnostics = projection.diagnostics
    summary = _compose_summary_fields(projection.timeline)
    current_stage = _current_stage(snapshot)
    stages = snapshot.get("stages") or {}
    stage_info = stages.get(current_stage, {}) if current_stage else {}
    stage_updated_dt = _parse_iso(stage_info.get("updated_at"))
    created_dt = _parse_iso(snapshot.get("created_at"))
    status = snapshot.get("status")
    flags = _flags(snapshot, diagnostics)
    flags["stalled"] = _is_stalled(snapshot, now)
    return {
        "journey_id": projection.journey_id,
        "tenant_id": projection.tenant_id,
        "environment": projection.environment,
        "status": status,
        "current_stage": current_stage,
        "severity": _severity(snapshot, diagnostics),
        **summary,
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "terminal_at": snapshot.get("updated_at") if status in TERMINAL_STATUSES else None,
        "stage_elapsed_seconds": (now - stage_updated_dt).total_seconds() if stage_updated_dt else None,
        "total_elapsed_seconds": (now - created_dt).total_seconds() if created_dt else None,
        "flags": flags,
        "completeness": snapshot.get("completeness"),
        "read_state": _read_state(snapshot, diagnostics),
        "revision": snapshot.get("revision"),
    }


def _stage_events(timeline: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for event in timeline:
        stage = event.get("stage")
        if stage:
            latest[stage] = dict(event)
    return latest


def _detail(projection: JourneyProjection, *, now: datetime) -> Dict[str, Any]:
    row = _list_row(projection, now=now)
    snapshot = projection.snapshot
    return {
        **row,
        "identifiers": snapshot.get("identifiers"),
        "stages": snapshot.get("stages"),
        "stage_events": _stage_events(projection.timeline),
        "graph_edges": projection.graph_edges,
        "diagnostics": projection.diagnostics,
        "event_count": len(projection.timeline),
    }


def _graph(projection: JourneyProjection) -> Dict[str, Any]:
    identifiers = projection.snapshot.get("identifiers") or {}
    nodes: Dict[str, Dict[str, Any]] = {projection.journey_id: {"id": projection.journey_id, "type": "journey_id"}}
    edges: List[Dict[str, str]] = [dict(edge) for edge in projection.graph_edges]
    for edge in projection.graph_edges:
        for key in ("from", "to"):
            node_id = edge.get(key)
            if node_id:
                nodes.setdefault(node_id, {"id": node_id, "type": "order"})
    research_ids = identifiers.get("research_journey_id") or []
    lifecycle_ids = identifiers.get("strategy_lifecycle_id") or []
    for research_id in research_ids:
        nodes.setdefault(research_id, {"id": research_id, "type": "research_journey_id"})
    for lifecycle_id in lifecycle_ids:
        nodes.setdefault(lifecycle_id, {"id": lifecycle_id, "type": "strategy_lifecycle_id"})
        for research_id in research_ids:
            edges.append({"from": research_id, "to": lifecycle_id, "type": "produced"})
        edges.append({"from": lifecycle_id, "to": projection.journey_id, "type": "deployed_to"})
    return {
        "journey_id": projection.journey_id,
        "nodes": sorted(nodes.values(), key=lambda node: node["id"]),
        "edges": edges,
    }


def _evidence(projection: JourneyProjection) -> Dict[str, Any]:
    by_stage: Dict[str, Dict[str, Any]] = {}
    for event in projection.timeline:
        stage = event.get("stage") or "unstaged"
        bucket = by_stage.setdefault(
            stage,
            {"evidence_refs": [], "input_refs": [], "output_refs": [], "policy_refs": [], "event_ids": []},
        )
        bucket["event_ids"].append(event.get("event_id"))
        for ref_key in ("evidence_refs", "input_refs", "output_refs", "policy_refs"):
            values = event.get(ref_key)
            if isinstance(values, list):
                bucket[ref_key].extend(str(item) for item in values)
    return {
        "journey_id": projection.journey_id,
        "by_stage": by_stage,
        "completeness": projection.snapshot.get("completeness"),
        "diagnostics": projection.diagnostics,
    }


_TIMELINE_FIELDS = (
    "event_id", "journey_id", "stage", "event_type", "stage_status", "status", "occurred_at", "recorded_at",
    "actor", "summary", "reason_code", "input_refs", "output_refs", "evidence_refs", "policy_refs",
    "causation_id", "correlation_id", "payload_redaction", "schema_version",
)


def _timeline_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    row = {key: event[key] for key in _TIMELINE_FIELDS if key in event}
    row.setdefault("status", event.get("stage_status"))
    row.setdefault("payload_redaction", "none")
    row.setdefault("schema_version", event.get("schema_version", "1"))
    return row


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(pct * (len(ordered) - 1))))
    return ordered[idx]


def _metrics(projections: Sequence[JourneyProjection], *, now: datetime) -> Dict[str, Any]:
    by_status: Dict[str, int] = {}
    by_current_stage: Dict[str, int] = {}
    diagnostics_counts: Dict[str, int] = {}
    stage_latencies_ms: Dict[str, List[float]] = {stage: [] for stage in STAGES}
    stalled_count = 0
    for projection in projections:
        snapshot = projection.snapshot
        status = snapshot.get("status", "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        stage = _current_stage(snapshot) or "none"
        by_current_stage[stage] = by_current_stage.get(stage, 0) + 1
        for item in projection.diagnostics:
            code = item.get("code", "unknown")
            diagnostics_counts[code] = diagnostics_counts.get(code, 0) + 1
        if _is_stalled(snapshot, now):
            stalled_count += 1
        for event in projection.timeline:
            stage_name = event.get("stage")
            if stage_name not in stage_latencies_ms:
                continue
            occurred = _parse_iso(event.get("occurred_at"))
            recorded = _parse_iso(event.get("recorded_at"))
            if occurred and recorded:
                stage_latencies_ms[stage_name].append((recorded - occurred).total_seconds() * 1000.0)
    total = len(projections)
    stage_latency_ms = {
        stage: {"p50_ms": _percentile(values, 0.5), "p95_ms": _percentile(values, 0.95), "sample_count": len(values)}
        for stage, values in stage_latencies_ms.items()
        if values
    }
    diagnostics_rate = {code: (count / total if total else 0.0) for code, count in diagnostics_counts.items()}
    return {
        "total_journeys": total,
        "by_status": by_status,
        "by_current_stage": by_current_stage,
        "stalled_count": stalled_count,
        "diagnostics_counts": diagnostics_counts,
        "diagnostics_rate": diagnostics_rate,
        "stage_latency_ms": stage_latency_ms,
    }


def _matches_filters(
    projection: JourneyProjection,
    row: Mapping[str, Any],
    *,
    q: Optional[str],
    persona_id: Optional[str],
    strategy_id: Optional[str],
    decision_id: Optional[str],
    order_id: Optional[str],
    broker_order_id: Optional[str],
    stage: Optional[str],
    status: Optional[str],
    stalled: Optional[bool],
    waiting_human: Optional[bool],
    reconciliation_state: Optional[str],
    date_from_norm: Optional[str],
    date_to_norm: Optional[str],
) -> bool:
    if q:
        haystack = " ".join(str(value) for value in row.values() if isinstance(value, (str, int, float)))
        if q.lower() not in haystack.lower():
            return False
    if persona_id and row.get("persona_id") != persona_id:
        return False
    if strategy_id and row.get("strategy_id") != strategy_id:
        return False
    if decision_id and row.get("decision_id") != decision_id:
        return False
    if order_id and row.get("order_id") != order_id:
        return False
    if broker_order_id and row.get("broker_order_id") != broker_order_id:
        return False
    if stage and stage not in (projection.snapshot.get("stages") or {}):
        return False
    if status and row.get("status") != status:
        return False
    if stalled is not None and bool(row.get("flags", {}).get("stalled")) != stalled:
        return False
    if waiting_human is not None and bool(row.get("flags", {}).get("waiting_human")) != waiting_human:
        return False
    if reconciliation_state:
        recon_status = (projection.snapshot.get("stages") or {}).get("reconciliation", {}).get("status")
        if recon_status != reconciliation_state:
            return False
    updated_at = row.get("updated_at") or ""
    if date_from_norm and updated_at < date_from_norm:
        return False
    if date_to_norm and updated_at > date_to_norm:
        return False
    return True


def _default_utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stalled_thresholds() -> Dict[str, Dict[str, int]]:
    """Return validated environment/stage thresholds, falling back safely."""
    configured = os.getenv(_STALLED_THRESHOLDS_ENV, "").strip()
    if not configured:
        return {env: dict(values) for env, values in _DEFAULT_STALLED_THRESHOLDS.items()}
    try:
        raw = json.loads(configured)
        result = {env: dict(values) for env, values in _DEFAULT_STALLED_THRESHOLDS.items()}
        for env, values in raw.items():
            if env not in _ALLOWED_ENVIRONMENTS or not isinstance(values, Mapping):
                continue
            for stage, seconds in values.items():
                if (stage == "default" or stage in STAGES) and isinstance(seconds, int) and seconds > 0:
                    result[env][stage] = seconds
        return result
    except (TypeError, ValueError):
        return {env: dict(values) for env, values in _DEFAULT_STALLED_THRESHOLDS.items()}


def _attention_item(projection: JourneyProjection, *, now: datetime) -> Optional[Dict[str, Any]]:
    snapshot = projection.snapshot
    if snapshot.get("status") in TERMINAL_STATUSES:
        return None
    stages = snapshot.get("stages") or {}
    latest_stage = max(stages, key=lambda name: stages[name].get("updated_at", ""), default="signal_generation")
    updated = _parse_iso(snapshot.get("updated_at"))
    age = max(0, int((now - updated).total_seconds())) if updated else 0
    threshold = _stalled_thresholds()[projection.environment].get(
        latest_stage, _stalled_thresholds()[projection.environment]["default"]
    )
    diagnostics = [item.get("code") for item in projection.diagnostics]
    stalled = bool(updated and age >= threshold)
    if not stalled and not diagnostics:
        return None
    severity = "critical" if stalled and age >= threshold * 3 else "high" if stalled else "medium"
    return {
        "id": f"attention-{projection.journey_id}", "journey_id": projection.journey_id,
        "stage": latest_stage, "severity": severity, "stalled": stalled,
        "age_seconds": age, "threshold_seconds": threshold, "reason_codes": diagnostics + (["stage_stalled"] if stalled else []),
        "owner_role": "operator", "allowed_actions": ["open_journey", "inspect_evidence"],
        "updated_at": snapshot.get("updated_at"), "revision": snapshot.get("revision", 0),
    }


def _sse_frame(*, event_id: int, event: str, data: Mapping[str, Any]) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(dict(data), separators=(',', ':'), sort_keys=True)}\n\n"


def _normalize_event(event: dict) -> dict:
    result = dict(event)
    envelope = result.pop("correlation_envelope", None)
    if isinstance(envelope, dict):
        for name in ("journey_id", "tenant_id", "environment", "correlation_id", "trace_id", "research_journey_id", "strategy_lifecycle_id"):
            result.setdefault(name, envelope.get(name))
    for field in ("occurred_at", "recorded_at"):
        val = result.get(field)
        if val:
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
                result[field] = dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return result


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((key, _canonical(item)) for key, item in sorted(value.items()))
    if isinstance(value, list):
        return tuple(_canonical(item) for item in value)
    return value


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #


def create_trade_journeys_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[[Any], None],
    require_operator_role: Optional[Callable[[Any], None]] = None,
    get_event_store: Callable[[], TradeJourneyEventStore] = lambda: EVENT_STORE,
    get_projection_reader: Callable[[], Optional[TradeJourneyProjectionStore]] = lambda: None,
    dispatch_action: Callable[[Dict[str, Any]], Dict[str, Any]] = _unconfigured_action_dispatcher,
    action_ledger: ActionLedger = ACTION_LEDGER,
    utc_now: Callable[[], str] = _default_utc_now,
    get_slo_alert_transport: Callable[[], DataQualityAlertTransport] = lambda: SLO_ALERT_TRANSPORT,
    latency_recorder: ApiLatencyRecorder = API_LATENCY_RECORDER,
) -> APIRouter:
    """Factory for the `/bff/management/trade-journeys` route family.

    Dependencies are injected so tests can replace the event store without
    circular imports (main.py holds the live singletons), matching the
    `console_gap`/`trade_journal.py` factory-router convention.
    """
    router = APIRouter()

    def _identity(authorization: Optional[str]) -> Any:
        return extract_identity(authorization)

    @router.post(
        "/bff/management/trade-journeys/{journey_id}/actions",
        response_model=TradeJourneyActionEnvelope,
    )
    async def bff_trade_journey_action(
        journey_id: str,
        request: TradeJourneyActionRequest,
        tenant_id: str = Query(...),
        environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ):
        identity = _identity(authorization)
        (require_operator_role or require_read_role)(identity)
        if request.action not in _JOURNEY_ACTIONS:
            return _err(400, "VALIDATION_FAILED", f"action must be one of {sorted(_JOURNEY_ACTIONS)}")
        if not idempotency_key or len(idempotency_key.strip()) < 8:
            return _err(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key must contain at least 8 characters")
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        if environment == "live" and request.action in _LIVE_ACTIONS and os.getenv("PANTHEON_TRADE_JOURNEY_LIVE_ACTIONS", "false").lower() != "true":
            return _err(403, "LIVE_ACTION_DISABLED", "Live-capital journey actions are feature-flagged off")
        materializer = get_event_store().materializer()
        projection = materializer.get(journey_id, tenant_id=tenant_id, environment=environment) if materializer and _tenant_allowed(identity, tenant_id) else None
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
        if request.expected_revision != projection.snapshot.get("revision"):
            return _err(409, "STALE_JOURNEY_REVISION", "Canonical journey changed; refetch before retry")

        body = request.model_dump()
        request_hash = hashlib.sha256(json.dumps({"journey_id": journey_id, "tenant_id": tenant_id, "environment": environment, **body}, sort_keys=True).encode()).hexdigest()
        disposition, cached = action_ledger.reserve(idempotency_key.strip(), request_hash)
        if disposition == "conflict":
            return _err(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key was already used for a different command")
        if disposition == "pending":
            return _err(409, "ACTION_IN_PROGRESS", "An action with this idempotency key is already in progress")
        if cached is not None:
            cached["idempotent_replay"] = True
            return {"data": cached, "meta": {"snapshot_at": utc_now(), "refetch_required": True}}

        command = {
            "command_type": "trade_journey_action",
            "journey_id": journey_id,
            "tenant_id": tenant_id,
            "environment": environment,
            "idempotency_key": idempotency_key.strip(),
            **body,
            "human_inbox": {
                "href": "/bff/management/human-inbox",
                "return_to": request.return_to or f"/management/trade-journeys/{journey_id}",
            },
        }
        result = dict(dispatch_action(command) or {})
        status = str(result.get("status") or "partial_failure")
        receipt = {
            "receipt_id": str(result.get("receipt_id") or f"tja-{idempotency_key.strip()}"),
            "journey_id": journey_id,
            "action": request.action,
            "status": status,
            "readback": result.get("readback"),
            "human_inbox": command["human_inbox"],
            "idempotent_replay": False,
        }
        if status == "succeeded" and receipt["readback"] is None:
            receipt["status"] = "partial_failure"
            receipt["code"] = "READBACK_REQUIRED"
        if result.get("code"):
            receipt["code"] = result["code"]
        action_ledger.complete(idempotency_key.strip(), request_hash, receipt)
        response_status = 200 if receipt["status"] == "succeeded" else 502
        return JSONResponse(status_code=response_status, content={"data": receipt, "meta": {"snapshot_at": utc_now(), "refetch_required": True}})

    # NOTE: `resolve` and `metrics` are static siblings of the `{journey_id}`
    # path segment and MUST be registered before the `{journey_id}` routes
    # below — Starlette matches routes in registration order, so a param
    # route registered first would shadow these literal paths (the exact bug
    # class `test_route_resolution_no_shadowing.py` guards against).

    @router.get("/bff/management/trade-journeys/events")
    async def bff_trade_journeys_events(
        tenant_id: str = Query(...), environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
        last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    ):
        """Revisioned SSE cursor. Payloads are invalidation hints, never snapshots."""
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        if not _tenant_allowed(identity, tenant_id):
            return _err(403, "FORBIDDEN", "Cross-tenant access denied")
        materializer = get_event_store().materializer()
        if materializer is None:
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Trade journey materializer unavailable", retryable=True)
        try:
            cursor = int(last_event_id or 0)
            if cursor < 0:
                raise ValueError
        except ValueError:
            return _err(400, "VALIDATION_FAILED", "Last-Event-ID must be a non-negative integer")
        revision = materializer.revision

        async def stream():
            event = "snapshot_refetch_required" if cursor and cursor != revision - 1 else "journeys_changed"
            yield _sse_frame(event_id=revision, event=event, data={
                "revision": revision, "previous_revision": cursor,
                "gap": bool(cursor and cursor != revision - 1), "snapshot_refetch": True,
                "tenant_id": tenant_id, "environment": environment,
            })
            await asyncio.sleep(0)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={
            "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
        })

    @router.get("/bff/management/trade-journeys/attention", response_model=TradeJourneyListEnvelope)
    async def bff_trade_journeys_attention(
        tenant_id: str = Query(...), environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ):
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        if not _tenant_allowed(identity, tenant_id):
            return _err(403, "FORBIDDEN", "Cross-tenant access denied")
        snapshot_at = utc_now()
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_list_envelope(
                snapshot_at, 200, entity_id="trade-journey-attention", store=store
            )
        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        items = [
            _mask_live_sensitive(item, identity, projection.environment)
            for projection in materializer.projections
            if projection.tenant_id == tenant_id and projection.environment == environment
            if (item := _attention_item(projection, now=now)) is not None
        ]
        items.sort(key=lambda item: ({"critical": 0, "high": 1, "medium": 2}[item["severity"]], -item["age_seconds"]))
        return {"data": {"id": "trade-journey-attention", "tenant_id": tenant_id,
                         "environment": environment, "items": items},
                "page_info": {"total": len(items), "returned": len(items), "has_more": False},
                "meta": _meta(snapshot_at, "formal", materializer, store=store)}

    @router.get("/bff/management/trade-journeys/resolve", response_model=TradeJourneyDetailEnvelope)
    async def bff_trade_journeys_resolve(
        q: str = Query(..., min_length=1),
        identifier_type: Optional[str] = Query(default=None),
        tenant_id: str = Query(...),
        environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-005: ambiguity-aware resolve of any known identifier to journeys.

        Returns every matching (identifier_type, journey_ids) candidate
        instead of silently picking the first match; `ambiguous=true` when
        more than one distinct journey matches.
        """
        _slo_timer_start = time.perf_counter()
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        if identifier_type and identifier_type not in _RESOLVABLE_IDENTIFIER_TYPES:
            return _err(400, "VALIDATION_FAILED", f"identifier_type must be one of {list(_RESOLVABLE_IDENTIFIER_TYPES)}")
        if not _tenant_allowed(identity, tenant_id):
            return _err(403, "FORBIDDEN", "Cross-tenant access denied")

        snapshot_at = utc_now()
        reader = get_projection_reader()
        if reader is not None:
            try:
                types_to_try = (identifier_type,) if identifier_type else _RESOLVABLE_IDENTIFIER_TYPES
                if not _can_view_live_sensitive(identity, environment):
                    types_to_try = tuple(id_type for id_type in types_to_try if id_type not in _LIVE_SENSITIVE_IDENTIFIER_TYPES)
                candidates: List[Dict[str, Any]] = []
                all_ids: set[str] = set()
                for id_type in types_to_try:
                    matches = reader.resolve(tenant_id=tenant_id, environment=environment, identifier_type=id_type, identifier_value=q)
                    if matches:
                        candidates.append({"identifier_type": id_type, "identifier": q, "journey_ids": matches, "match_count": len(matches)})
                        all_ids.update(matches)
                data = {"id": "trade-journey-resolve", "query": q, "tenant_id": tenant_id, "environment": environment, "candidates": candidates, "journey_ids": sorted(all_ids), "ambiguous": len(all_ids) > 1}
                latency_recorder.record("resolve", (time.perf_counter() - _slo_timer_start) * 1000.0)
                return {"data": data, "meta": _projection_reader_meta(snapshot_at, "formal", reader, tenant_id=tenant_id, environment=environment)}
            except ProjectionReadUnavailable:
                return _unavailable_envelope(snapshot_at, entity_id="trade-journey-resolve")
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id="trade-journey-resolve", store=store)

        types_to_try = (identifier_type,) if identifier_type else _RESOLVABLE_IDENTIFIER_TYPES
        if not _can_view_live_sensitive(identity, environment):
            types_to_try = tuple(
                id_type
                for id_type in types_to_try
                if id_type not in _LIVE_SENSITIVE_IDENTIFIER_TYPES
            )
        candidates: List[Dict[str, Any]] = []
        all_ids: set = set()
        for id_type in types_to_try:
            matches = materializer.resolve(id_type, q, tenant_id=tenant_id, environment=environment)
            if matches:
                candidates.append(
                    {"identifier_type": id_type, "identifier": q, "journey_ids": matches, "match_count": len(matches)}
                )
                all_ids.update(matches)

        data = {
            "id": "trade-journey-resolve",
            "query": q,
            "tenant_id": tenant_id,
            "environment": environment,
            "candidates": candidates,
            "journey_ids": sorted(all_ids),
            "ambiguous": len(all_ids) > 1,
        }
        meta = _meta(snapshot_at, "formal", materializer, store=store)
        latency_recorder.record("resolve", (time.perf_counter() - _slo_timer_start) * 1000.0)
        return {"data": data, "meta": meta}

    @router.get("/bff/management/trade-journeys/metrics", response_model=TradeJourneyDetailEnvelope)
    async def bff_trade_journeys_metrics(
        tenant_id: str = Query(...),
        environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-005: stage latency, stalled and diagnostics aggregates.

        Percentile latency is computed only from timeline events that carry
        both `occurred_at` and `recorded_at`; stages with no such samples are
        omitted rather than reported with a fabricated value.
        """
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        if not _tenant_allowed(identity, tenant_id):
            return _err(403, "FORBIDDEN", "Cross-tenant access denied")

        snapshot_at = utc_now()
        reader = get_projection_reader()
        if reader is not None:
            try:
                metrics = reader.metrics(tenant_id=tenant_id, environment=environment)
                data = {"id": "trade-journey-metrics", "tenant_id": tenant_id, "environment": environment, **metrics}
                read_state = "formal" if metrics["total_journeys"] else "partial"
                return {"data": data, "meta": _projection_reader_meta(snapshot_at, read_state, reader, tenant_id=tenant_id, environment=environment)}
            except ProjectionReadUnavailable:
                return _unavailable_envelope(snapshot_at, entity_id="trade-journey-metrics")
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id="trade-journey-metrics", store=store)

        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        projections = [p for p in materializer.projections if p.tenant_id == tenant_id and p.environment == environment]
        metrics = _metrics(projections, now=now)
        data = {"id": "trade-journey-metrics", "tenant_id": tenant_id, "environment": environment, **metrics}
        read_state = "formal" if projections else "partial"
        meta = _meta(snapshot_at, read_state, materializer, store=store)
        return {"data": data, "meta": meta}

    @router.get("/bff/management/trade-journeys/slo", response_model=TradeJourneyDetailEnvelope)
    async def bff_trade_journeys_slo(
        tenant_id: str = Query(...),
        environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-011: SLO/data-quality metrics, incidents, and dashboard snapshot.

        This is the runtime/materializer-health integration the TJ-E2E-011
        review required: it computes `slo_data_quality.DataQualityMetrics`
        from the live materializer (not a synthetic drill), evaluates the
        gap-spec section 13 targets against real request latency recorded by
        `API_LATENCY_RECORDER`, and forwards every emitted `DataQualityIncident`
        to the governed alert transport (`alert_transport.py`) so it reaches
        the same durable outbox other Pantheon services publish through.
        """
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        if not _tenant_allowed(identity, tenant_id):
            return _err(403, "FORBIDDEN", "Cross-tenant access denied")

        snapshot_at = utc_now()
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id="trade-journey-slo", store=store)

        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        projections = [p for p in materializer.projections if p.tenant_id == tenant_id and p.environment == environment]
        targets = load_slo_targets(environment)
        metrics = compute_data_quality_metrics(
            projections,
            environment=environment,
            source_watermarks=materializer.source_watermarks,
            now=now,
            stalled_after_seconds=targets.stalled_after_seconds,
            detail_api_latencies_ms=latency_recorder.samples("detail"),
            resolve_api_latencies_ms=latency_recorder.samples("resolve"),
        )
        incidents = evaluate_data_quality(metrics, targets, projections, now=now)
        published = get_slo_alert_transport().publish_incidents(incidents)
        dashboard_snapshot = render_dashboard_snapshot(load_dashboard(), metrics, targets)

        data = {
            "id": "trade-journey-slo",
            "tenant_id": tenant_id,
            "environment": environment,
            "metrics": metrics_to_dict(metrics),
            "incidents": [incident_to_dict(incident) for incident in incidents],
            "alerts_published": len(published),
            "dashboard": dashboard_snapshot,
        }
        read_state = "formal" if projections else "partial"
        meta = _meta(snapshot_at, read_state, materializer, store=store)
        return {"data": data, "meta": meta}

    @router.get(
        "/bff/management/trade-journeys",
        response_model=TradeJourneyListEnvelope,
    )
    async def bff_trade_journeys_list(
        tenant_id: str = Query(...),
        environment: str = Query(...),
        q: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None),
        decision_id: Optional[str] = Query(default=None),
        order_id: Optional[str] = Query(default=None),
        broker_order_id: Optional[str] = Query(default=None),
        stage: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        stalled: Optional[bool] = Query(default=None),
        waiting_human: Optional[bool] = Query(default=None),
        reconciliation_state: Optional[str] = Query(default=None),
        date_from: Optional[str] = Query(default=None, alias="from"),
        date_to: Optional[str] = Query(default=None, alias="to"),
        sort: str = Query(default="updated_at_desc"),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-005: server-composed, cursor-paginated, filterable journey list.

        Every row is fully composed from the materializer projection — no
        client-side join across persona/strategy/execution domains required.
        """
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        if sort not in _ALLOWED_SORTS:
            return _err(400, "VALIDATION_FAILED", f"sort must be one of {sorted(_ALLOWED_SORTS)}")
        if not _tenant_allowed(identity, tenant_id):
            return _err(403, "FORBIDDEN", "Cross-tenant access denied")

        snapshot_at = utc_now()
        reader = get_projection_reader()
        if reader is not None:
            filters = {
                "q": q, "persona_id": persona_id, "strategy_id": strategy_id,
                "decision_id": decision_id, "order_id": order_id,
                "broker_order_id": broker_order_id, "stage": stage, "status": status,
                "waiting_human": waiting_human, "reconciliation_state": reconciliation_state,
                "date_from": _normalize_bound(date_from), "date_to": _normalize_bound(date_to),
            }
            try:
                page_result = reader.page_journeys(tenant_id=tenant_id, environment=environment, filters=filters, sort=sort, page_size=page_size, page_token=page_token)
            except InvalidPageToken:
                return _err(400, "VALIDATION_FAILED", "page_token is invalid for this scope, filter, or sort")
            except ProjectionReadUnavailable:
                return _unavailable_list_envelope(snapshot_at, page_size, entity_id="trade-journeys")
            now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
            items = [_mask_live_sensitive(_list_row(projection, now=now), identity, projection.environment) for projection in page_result.items]
            states = {item.get("read_state") for item in items}
            overall_state = "formal" if not states or states == {"formal"} else "degraded" if "degraded" in states else "partial"
            return {"data": {"id": "trade-journeys", "tenant_id": tenant_id, "environment": environment, "items": items}, "page_info": {"next_page_token": page_result.next_page_token, "total": page_result.total, "page_size": page_size, "returned": len(items), "has_more": page_result.next_page_token is not None}, "meta": _projection_reader_meta(snapshot_at, overall_state, reader, tenant_id=tenant_id, environment=environment)}
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_list_envelope(
                snapshot_at, page_size, entity_id="trade-journeys", store=store
            )

        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        date_from_norm = _normalize_bound(date_from)
        date_to_norm = _normalize_bound(date_to)
        scoped = [p for p in materializer.projections if p.tenant_id == tenant_id and p.environment == environment]
        rows = [
            (p, _mask_live_sensitive(_list_row(p, now=now), identity, p.environment))
            for p in scoped
        ]
        filtered = [
            (p, row)
            for p, row in rows
            if _matches_filters(
                p,
                row,
                q=q,
                persona_id=persona_id,
                strategy_id=strategy_id,
                decision_id=decision_id,
                order_id=order_id,
                broker_order_id=broker_order_id,
                stage=stage,
                status=status,
                stalled=stalled,
                waiting_human=waiting_human,
                reconciliation_state=reconciliation_state,
                date_from_norm=date_from_norm,
                date_to_norm=date_to_norm,
            )
        ]
        sort_field = "created_at" if sort.startswith("created_at") else "updated_at"
        filtered.sort(key=lambda pair: pair[1].get(sort_field) or "", reverse=sort.endswith("_desc"))

        total = len(filtered)
        start = _decode_page_token(page_token)
        page = filtered[start : start + page_size]
        next_page_token = str(start + page_size) if start + page_size < total else None
        items = [row for _, row in page]

        read_states = {row.get("read_state") for _, row in filtered}
        if not read_states or read_states == {"formal"}:
            overall_state = "formal"
        elif "degraded" in read_states:
            overall_state = "degraded"
        else:
            overall_state = "partial"

        data = {"id": "trade-journeys", "tenant_id": tenant_id, "environment": environment, "items": items}
        meta = _meta(snapshot_at, overall_state, materializer, store=store)
        return {
            "data": data,
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
                "returned": len(items),
                "has_more": next_page_token is not None,
            },
            "meta": meta,
        }

    @router.get("/bff/management/trade-journeys/{journey_id}", response_model=TradeJourneyDetailEnvelope)
    async def bff_trade_journeys_detail(
        journey_id: str,
        tenant_id: str = Query(...),
        environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-005: canonical detail snapshot (gap-spec section 7.2)."""
        _slo_timer_start = time.perf_counter()
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")

        snapshot_at = utc_now()
        reader = get_projection_reader()
        if reader is not None:
            try:
                projection = reader.get_journey(tenant_id=tenant_id, environment=environment, journey_id=journey_id) if _tenant_allowed(identity, tenant_id) else None
            except ProjectionReadUnavailable:
                return _unavailable_envelope(snapshot_at, entity_id=journey_id)
            if projection is None:
                return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
            now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
            detail = _mask_live_sensitive(_detail(projection, now=now), identity, projection.environment)
            latency_recorder.record("detail", (time.perf_counter() - _slo_timer_start) * 1000.0)
            return {"data": detail, "meta": _projection_reader_meta(snapshot_at, detail.get("read_state", "formal"), reader, tenant_id=tenant_id, environment=environment)}
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id=journey_id, store=store)

        projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        detail = _mask_live_sensitive(_detail(projection, now=now), identity, projection.environment)
        meta = _meta(
            snapshot_at, detail.get("read_state", "formal"), materializer, store=store
        )
        latency_recorder.record("detail", (time.perf_counter() - _slo_timer_start) * 1000.0)
        return {"data": detail, "meta": meta}

    @router.get(
        "/bff/management/trade-journeys/{journey_id}/timeline",
        response_model=TradeJourneyListEnvelope,
    )
    async def bff_trade_journeys_timeline(
        journey_id: str,
        tenant_id: str = Query(...),
        environment: str = Query(...),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-005: cursor-paginated append-only timeline."""
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")

        snapshot_at = utc_now()
        reader = get_projection_reader()
        if reader is not None:
            try:
                projection = reader.get_journey(tenant_id=tenant_id, environment=environment, journey_id=journey_id) if _tenant_allowed(identity, tenant_id) else None
                if projection is None:
                    return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
                page_result = reader.page_timeline(tenant_id=tenant_id, environment=environment, journey_id=journey_id, page_size=page_size, page_token=page_token)
            except InvalidPageToken:
                return _err(400, "VALIDATION_FAILED", "page_token is invalid for this journey scope")
            except ProjectionReadUnavailable:
                return _unavailable_list_envelope(snapshot_at, page_size, entity_id=f"{journey_id}-timeline")
            events = [_mask_live_sensitive(_timeline_event(event), identity, projection.environment) for event in page_result.items]
            return {"data": {"id": f"{journey_id}-timeline", "journey_id": journey_id, "items": events}, "page_info": {"next_page_token": page_result.next_page_token, "total": page_result.total, "page_size": page_size, "returned": len(events), "has_more": page_result.next_page_token is not None}, "meta": _projection_reader_meta(snapshot_at, _read_state(projection.snapshot, projection.diagnostics), reader, tenant_id=tenant_id, environment=environment)}
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_list_envelope(
                snapshot_at,
                page_size,
                entity_id=f"{journey_id}-timeline",
                store=store,
            )

        projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        events = [
            _mask_live_sensitive(_timeline_event(event), identity, projection.environment)
            for event in projection.timeline
        ]
        total = len(events)
        start = _decode_page_token(page_token)
        page = events[start : start + page_size]
        next_page_token = str(start + page_size) if start + page_size < total else None

        data = {"id": f"{journey_id}-timeline", "journey_id": journey_id, "items": page}
        read_state = _read_state(projection.snapshot, projection.diagnostics)
        meta = _meta(snapshot_at, read_state, materializer, store=store)
        return {
            "data": data,
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
                "returned": len(page),
                "has_more": next_page_token is not None,
            },
            "meta": meta,
        }

    @router.get("/bff/management/trade-journeys/{journey_id}/graph", response_model=TradeJourneyDetailEnvelope)
    async def bff_trade_journeys_graph(
        journey_id: str,
        tenant_id: str = Query(...),
        environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-005: research/strategy/trade relationship graph."""
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")

        snapshot_at = utc_now()
        reader = get_projection_reader()
        if reader is not None:
            try:
                projection = reader.get_journey(tenant_id=tenant_id, environment=environment, journey_id=journey_id) if _tenant_allowed(identity, tenant_id) else None
            except ProjectionReadUnavailable:
                return _unavailable_envelope(snapshot_at, entity_id=f"{journey_id}-graph")
            if projection is None:
                return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
            return {"data": _mask_live_graph(_graph(projection), identity, projection.environment), "meta": _projection_reader_meta(snapshot_at, _read_state(projection.snapshot, projection.diagnostics), reader, tenant_id=tenant_id, environment=environment)}
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_envelope(
                snapshot_at, entity_id=f"{journey_id}-graph", store=store
            )

        projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        data = _mask_live_graph(_graph(projection), identity, projection.environment)
        read_state = _read_state(projection.snapshot, projection.diagnostics)
        meta = _meta(snapshot_at, read_state, materializer, store=store)
        return {"data": data, "meta": meta}

    @router.get("/bff/management/trade-journeys/{journey_id}/evidence", response_model=TradeJourneyDetailEnvelope)
    async def bff_trade_journeys_evidence(
        journey_id: str,
        tenant_id: str = Query(...),
        environment: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-005: evidence completeness and refs, grouped by stage."""
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")

        snapshot_at = utc_now()
        reader = get_projection_reader()
        if reader is not None:
            try:
                projection = reader.get_journey(tenant_id=tenant_id, environment=environment, journey_id=journey_id) if _tenant_allowed(identity, tenant_id) else None
            except ProjectionReadUnavailable:
                return _unavailable_envelope(snapshot_at, entity_id=f"{journey_id}-evidence")
            if projection is None:
                return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
            return {"data": _evidence(projection), "meta": _projection_reader_meta(snapshot_at, _read_state(projection.snapshot, projection.diagnostics), reader, tenant_id=tenant_id, environment=environment)}
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_envelope(
                snapshot_at, entity_id=f"{journey_id}-evidence", store=store
            )

        projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        data = _evidence(projection)
        read_state = _read_state(projection.snapshot, projection.diagnostics)
        meta = _meta(snapshot_at, read_state, materializer, store=store)
        return {"data": data, "meta": meta}

    @router.get("/bff/management/trade-journeys/{journey_id}/replay", response_model=TradeJourneyDetailEnvelope)
    async def bff_trade_journeys_replay(
        journey_id: str,
        tenant_id: str = Query(...),
        environment: str = Query(...),
        as_of: str = Query(...),
        authorization: Optional[str] = Header(default=None),
    ):
        """TJ-E2E-005: read-only `as_of` historical snapshot.

        Rebuilds a scratch materializer from only the events that had
        occurred by `as_of` (compared as parsed datetimes, not raw strings,
        to avoid the mixed whole/fractional-second lexicographic-ordering
        bug TJ-E2E-004 fixed). Never mutates the shared cached materializer
        and never triggers a command — replay is strictly read-only.
        """
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        as_of_dt = _parse_iso(as_of)
        if as_of_dt is None:
            return _err(400, "VALIDATION_FAILED", "as_of must be a timezone-aware ISO-8601 timestamp")

        snapshot_at = utc_now()
        store = get_event_store()
        materializer = store.materializer()
        if materializer is None:
            return _unavailable_envelope(
                snapshot_at, entity_id=f"{journey_id}-replay", store=store
            )

        current_projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if current_projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        historical_events = [
            event
            for event in store.events()
            if (parsed := _parse_iso(event.get("occurred_at"))) is not None and parsed <= as_of_dt
        ]
        replay_materializer = JourneyMaterializer()
        try:
            replay_materializer.rebuild(historical_events)
        except MaterializationError:
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Replay could not rebuild a consistent snapshot", retryable=True)

        projection = replay_materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        if projection is None:
            data = {"id": f"{journey_id}-replay", "journey_id": journey_id, "as_of": as_of, "exists_at_as_of": False}
            meta = _meta(snapshot_at, "formal", materializer, store=store)
            return {"data": data, "meta": meta}

        detail = _mask_live_sensitive(_detail(projection, now=now), identity, projection.environment)
        detail["as_of"] = as_of
        detail["exists_at_as_of"] = True
        meta = _meta(
            snapshot_at, detail.get("read_state", "formal"), materializer, store=store
        )
        return {"data": detail, "meta": meta}

    @router.post(
        "/bff/management/trade-journeys/events",
        response_model=dict,
    )
    async def bff_publish_trade_journey_events(
        events: List[dict],
        authorization: Optional[str] = Header(default=None),
    ):
        """Append first-class journey events to the events store in real-time."""
        identity = _identity(authorization)
        if require_operator_role:
            require_operator_role(identity)
        else:
            require_read_role(identity)

        if not events:
            return _err(400, "VALIDATION_FAILED", "Event batch cannot be empty")

        normalized_batch = []
        batch_ids = {}

        for event in events:
            if not isinstance(event, dict):
                return _err(400, "VALIDATION_FAILED", "Each event must be a JSON object")

            # 1. Required fields validation
            for field in ("event_id", "journey_id", "tenant_id", "environment", "occurred_at"):
                val = event.get(field)
                if not isinstance(val, str) or not val.strip():
                    return _err(400, "VALIDATION_FAILED", f"Event missing required field: {field}")

            # 2. Tenant scope check
            if not _tenant_allowed(identity, event["tenant_id"]):
                return _err(403, "FORBIDDEN", f"Tenant access denied for tenant: {event['tenant_id']}")

            # 3. Timezone-aware ISO-8601 validation
            for ts_field in ("occurred_at", "recorded_at"):
                val = event.get(ts_field)
                if val:
                    try:
                        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            return _err(400, "VALIDATION_FAILED", f"{ts_field} must be timezone-aware")
                    except Exception:
                        return _err(400, "VALIDATION_FAILED", f"Invalid ISO-8601 format for {ts_field}")

            # 4. stage validation (if stage present)
            stage = event.get("stage")
            if stage is not None and stage not in STAGES:
                return _err(400, "VALIDATION_FAILED", f"Unknown stage: {stage}")

            # Normalize event to ensure consistent key sorting/comparison
            try:
                norm_event = _normalize_event(event)
            except Exception as exc:
                return _err(400, "VALIDATION_FAILED", f"Failed to normalize event: {exc}")

            event_id = norm_event["event_id"]
            fingerprint = repr(_canonical(norm_event))

            # Check duplicate within the batch
            if event_id in batch_ids:
                if batch_ids[event_id] != fingerprint:
                    return _err(400, "CONFLICTING_DUPLICATE", f"Conflicting duplicate event_id within batch: {event_id}")
            else:
                batch_ids[event_id] = fingerprint
                normalized_batch.append(norm_event)

        store_path_str = os.getenv(EVENTS_STORE_ENV, "")
        if not store_path_str:
            return _err(503, "STORE_UNCONFIGURED", "Events store path is not configured")
        store_path = Path(store_path_str)
        lock_path = store_path.with_suffix(".lock")

        try:
            # Ensure the directory exists
            store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_path, "w") as lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

                # Now we hold the lock!
                if store_path.is_file():
                    try:
                        raw_store = json.loads(store_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        return _err(
                            503,
                            "STORE_UNAVAILABLE",
                            "Events store cannot be read safely",
                            retryable=True,
                        )
                    if _projector_store_metadata(raw_store).get("projector_owned"):
                        return _err(
                            409,
                            "PROJECTOR_OWNED_STORE",
                            (
                                "Canonical lifecycle projector owns this read-model file; "
                                "publish through the canonical telemetry append path"
                            ),
                        )

                existing = load_store_events(store_path)

                # Check conflict with existing store events
                existing_normalized = {}
                for ev in existing:
                    if isinstance(ev, dict) and "event_id" in ev:
                        try:
                            norm_ev = _normalize_event(ev)
                            existing_normalized[norm_ev["event_id"]] = repr(_canonical(norm_ev))
                        except Exception:
                            # Skip unnormalizable existing events
                            pass

                for norm_event in normalized_batch:
                    event_id = norm_event["event_id"]
                    fingerprint = repr(_canonical(norm_event))
                    if event_id in existing_normalized:
                        if existing_normalized[event_id] != fingerprint:
                            return _err(409, "CONFLICTING_DUPLICATE", f"Conflicting duplicate event_id: {event_id}")

                # Merge/append to preserve telemetry_backfill events!
                merged_dict = {}
                for ev in existing:
                    if isinstance(ev, dict) and "event_id" in ev:
                        merged_dict[ev["event_id"]] = ev
                for ev in normalized_batch:
                    merged_dict[ev["event_id"]] = ev

                merged_list = sorted(
                    merged_dict.values(),
                    key=lambda event: (event.get("occurred_at") or "", event.get("event_id") or "")
                )

                write_store_atomic(store_path, merged_list)
        except Exception as exc:
            return _err(500, "WRITE_FAILED", f"Failed to write events to store: {exc}")
        return {"status": "ok", "count": len(normalized_batch)}

    return router
