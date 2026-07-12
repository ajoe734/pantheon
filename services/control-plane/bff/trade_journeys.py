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

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from services.trade_journey.materializer import (
    IDENTIFIER_FIELDS,
    STAGES,
    TERMINAL_STATUSES,
    JourneyMaterializer,
    JourneyProjection,
    MaterializationError,
)

EVENTS_STORE_ENV = "PANTHEON_BFF_TRADE_JOURNEY_EVENTS_STORE"
_ALLOWED_ENVIRONMENTS = {"paper", "broker_sandbox", "canary", "live"}
_ANOMALY_DIAGNOSTIC_CODES = {"identifier_conflict", "conflicting_terminal_states", "orphan_identifier"}
_ALLOWED_SORTS = {"updated_at_desc", "updated_at_asc", "created_at_desc", "created_at_asc"}
_RESOLVABLE_IDENTIFIER_TYPES: Tuple[str, ...] = ("journey_id",) + IDENTIFIER_FIELDS
_STALLED_AFTER_SECONDS = 900  # 15 minutes; see gap-spec section 13 SLO discussion.
# Stages 1-6 (research_rationale .. deployment_runtime) precede signal
# generation and, per contract.md section 5.1, never carry a `journey_id` —
# they belong to the ResearchJourney/StrategyLifecycle event streams, not
# this TradeJourney's own history. The materializer's `missing_stages` is
# computed uniformly across all 14 stages, so a TradeJourney's own
# completeness must only be judged against the stages it could ever
# observe under its own journey_id (this set); pre-signal absence is
# structural, not a data-quality gap in this journey's execution truth.
_OBSERVABLE_STAGES = frozenset(STAGES[STAGES.index("signal_generation"):])
_LIVE_CAPITAL_FIELDS = ("quantity", "price")
_LIVE_CAPITAL_VISIBLE_ROLES = {"operator", "approver", "admin", "reviewer"}

ReadState = Literal["formal", "partial", "degraded", "unavailable"]


# --------------------------------------------------------------------------- #
# Response DTOs
# --------------------------------------------------------------------------- #


class TradeJourneyFreshness(BaseModel):
    materializer_revision: int
    rebuild_status: str
    source_watermarks: Dict[str, str] = Field(default_factory=dict)

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
            self._cache_key = None
            return False
        try:
            mtime = path.stat().st_mtime
        except OSError:
            self._materializer = None
            self._events = []
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
            self._cache_key = None
            return False
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


EVENT_STORE = TradeJourneyEventStore()


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
    scoped = claims.get("tenant_ids") or claims.get("tenantIds")
    if isinstance(scoped, str):
        scoped = [item.strip() for item in scoped.split(",") if item.strip()]
    if not scoped:
        return True
    return tenant_id in scoped


def _mask_live_capital(row: Dict[str, Any], identity: Any, environment: str) -> Dict[str, Any]:
    if environment != "live":
        return row
    roles = set(getattr(identity, "roles", None) or [])
    if roles & _LIVE_CAPITAL_VISIBLE_ROLES:
        return row
    masked = dict(row)
    changed = False
    for key in _LIVE_CAPITAL_FIELDS:
        if masked.get(key) is not None:
            masked[key] = "***"
            changed = True
    masked["live_capital_masked"] = changed
    return masked


def _meta(snapshot_at: str, read_state: str, materializer: JourneyMaterializer) -> Dict[str, Any]:
    return {
        "snapshot_at": snapshot_at,
        "read_state": read_state,
        "freshness": {
            "materializer_revision": materializer.revision,
            "rebuild_status": materializer.rebuild_status,
            "source_watermarks": dict(materializer.source_watermarks),
        },
    }


def _unavailable_meta(snapshot_at: str) -> Dict[str, Any]:
    return {
        "snapshot_at": snapshot_at,
        "read_state": "unavailable",
        "freshness": {"materializer_revision": 0, "rebuild_status": "idle", "source_watermarks": {}},
    }


def _unavailable_envelope(snapshot_at: str, *, entity_id: str) -> Dict[str, Any]:
    return {
        "data": {"id": entity_id, "status": "unavailable"},
        "meta": _unavailable_meta(snapshot_at),
    }


def _unavailable_list_envelope(snapshot_at: str, page_size: int, *, entity_id: str) -> Dict[str, Any]:
    return {
        "data": {"id": entity_id, "items": [], "status": "unavailable"},
        "page_info": {"next_page_token": None, "total": 0, "page_size": page_size, "returned": 0, "has_more": False},
        "meta": _unavailable_meta(snapshot_at),
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


# --------------------------------------------------------------------------- #
# Router factory
# --------------------------------------------------------------------------- #


def create_trade_journeys_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[[Any], None],
    get_event_store: Callable[[], TradeJourneyEventStore] = lambda: EVENT_STORE,
    utc_now: Callable[[], str] = _default_utc_now,
) -> APIRouter:
    """Factory for the `/bff/management/trade-journeys` route family.

    Dependencies are injected so tests can replace the event store without
    circular imports (main.py holds the live singletons), matching the
    `console_gap`/`trade_journal.py` factory-router convention.
    """
    router = APIRouter()

    def _identity(authorization: Optional[str]) -> Any:
        return extract_identity(authorization)

    # NOTE: `resolve` and `metrics` are static siblings of the `{journey_id}`
    # path segment and MUST be registered before the `{journey_id}` routes
    # below — Starlette matches routes in registration order, so a param
    # route registered first would shadow these literal paths (the exact bug
    # class `test_route_resolution_no_shadowing.py` guards against).

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
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")
        if identifier_type and identifier_type not in _RESOLVABLE_IDENTIFIER_TYPES:
            return _err(400, "VALIDATION_FAILED", f"identifier_type must be one of {list(_RESOLVABLE_IDENTIFIER_TYPES)}")
        if not _tenant_allowed(identity, tenant_id):
            return _err(403, "FORBIDDEN", "Cross-tenant access denied")

        snapshot_at = utc_now()
        materializer = get_event_store().materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id="trade-journey-resolve")

        types_to_try = (identifier_type,) if identifier_type else _RESOLVABLE_IDENTIFIER_TYPES
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
        meta = _meta(snapshot_at, "formal", materializer)
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
        materializer = get_event_store().materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id="trade-journey-metrics")

        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        projections = [p for p in materializer.projections if p.tenant_id == tenant_id and p.environment == environment]
        metrics = _metrics(projections, now=now)
        data = {"id": "trade-journey-metrics", "tenant_id": tenant_id, "environment": environment, **metrics}
        read_state = "formal" if projections else "partial"
        meta = _meta(snapshot_at, read_state, materializer)
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
        materializer = get_event_store().materializer()
        if materializer is None:
            return _unavailable_list_envelope(snapshot_at, page_size, entity_id="trade-journeys")

        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        date_from_norm = _normalize_bound(date_from)
        date_to_norm = _normalize_bound(date_to)
        scoped = [p for p in materializer.projections if p.tenant_id == tenant_id and p.environment == environment]
        rows = [(p, _list_row(p, now=now)) for p in scoped]
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
        items = [_mask_live_capital(row, identity, p.environment) for p, row in page]

        read_states = {row.get("read_state") for _, row in filtered}
        if not read_states or read_states == {"formal"}:
            overall_state = "formal"
        elif "degraded" in read_states:
            overall_state = "degraded"
        else:
            overall_state = "partial"

        data = {"id": "trade-journeys", "tenant_id": tenant_id, "environment": environment, "items": items}
        meta = _meta(snapshot_at, overall_state, materializer)
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
        identity = _identity(authorization)
        require_read_role(identity)
        if environment not in _ALLOWED_ENVIRONMENTS:
            return _err(400, "VALIDATION_FAILED", f"environment must be one of {sorted(_ALLOWED_ENVIRONMENTS)}")

        snapshot_at = utc_now()
        materializer = get_event_store().materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id=journey_id)

        projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        detail = _mask_live_capital(_detail(projection, now=now), identity, projection.environment)
        meta = _meta(snapshot_at, detail.get("read_state", "formal"), materializer)
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
        materializer = get_event_store().materializer()
        if materializer is None:
            return _unavailable_list_envelope(snapshot_at, page_size, entity_id=f"{journey_id}-timeline")

        projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        events = [_timeline_event(event) for event in projection.timeline]
        total = len(events)
        start = _decode_page_token(page_token)
        page = events[start : start + page_size]
        next_page_token = str(start + page_size) if start + page_size < total else None

        data = {"id": f"{journey_id}-timeline", "journey_id": journey_id, "items": page}
        read_state = _read_state(projection.snapshot, projection.diagnostics)
        meta = _meta(snapshot_at, read_state, materializer)
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
        materializer = get_event_store().materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id=f"{journey_id}-graph")

        projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        data = _graph(projection)
        read_state = _read_state(projection.snapshot, projection.diagnostics)
        meta = _meta(snapshot_at, read_state, materializer)
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
        materializer = get_event_store().materializer()
        if materializer is None:
            return _unavailable_envelope(snapshot_at, entity_id=f"{journey_id}-evidence")

        projection = (
            materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
            if _tenant_allowed(identity, tenant_id)
            else None
        )
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        data = _evidence(projection)
        read_state = _read_state(projection.snapshot, projection.diagnostics)
        meta = _meta(snapshot_at, read_state, materializer)
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
            return _unavailable_envelope(snapshot_at, entity_id=f"{journey_id}-replay")

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
            meta = _meta(snapshot_at, "formal", materializer)
            return {"data": data, "meta": meta}

        detail = _mask_live_capital(_detail(projection, now=now), identity, projection.environment)
        detail["as_of"] = as_of
        detail["exists_at_as_of"] = True
        meta = _meta(snapshot_at, detail.get("read_state", "formal"), materializer)
        return {"data": detail, "meta": meta}

    return router
