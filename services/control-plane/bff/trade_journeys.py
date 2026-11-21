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
import hashlib
import json
import math
import os
import threading
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
from services.trade_journey.slo_data_quality import (
    compute_data_quality_metrics,
    evaluate_data_quality,
    incident_to_dict,
    load_slo_targets,
    metrics_to_dict,
)
from .persona_paper_enrollment import is_probe_or_test_persona
from .trade_journey_projection_store import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    InvalidPageToken,
    ProjectionReadError,
    ProjectionReadUnavailable,
    TradeJourneyProjectionStore,
    configured_projection_reader,
)

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
        "filled_quantity",
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


def _unavailable_meta(
    snapshot_at: str,
) -> Dict[str, Any]:
    return {
        "snapshot_at": snapshot_at,
        "read_state": "unavailable",
        "freshness": {
            "materializer_revision": 0,
            "rebuild_status": "idle",
            "source_watermarks": {},
        },
    }


def _unavailable_envelope(
    snapshot_at: str,
    *,
    entity_id: str,
) -> Dict[str, Any]:
    return {
        "data": {"id": entity_id, "status": "unavailable"},
        "meta": _unavailable_meta(snapshot_at),
    }


def _unavailable_list_envelope(
    snapshot_at: str,
    page_size: int,
    *,
    entity_id: str,
) -> Dict[str, Any]:
    return {
        "data": {"id": entity_id, "items": [], "status": "unavailable"},
        "page_info": {"next_page_token": None, "total": 0, "page_size": page_size, "returned": 0, "has_more": False},
        "meta": _unavailable_meta(snapshot_at),
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


def _is_nonfill_unsubmitted_event(
    item: Mapping[str, Any], *, check_quantity_zero: bool = True
) -> bool:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), Mapping) else {}
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    for flag in (
        item.get("submitted_to_broker"),
        metrics.get("submitted_to_broker"),
        metadata.get("submitted_to_broker"),
    ):
        if flag is not None and flag is not False:
            return False
    for field in (
        "broker_order_id",
        "fill_id",
        "broker_trade_id",
        "ledger_entry_id",
        "reconciliation_id",
    ):
        if (
            item.get(field) not in (None, "", [], {})
            or metadata.get(field) not in (None, "", [], {})
            or metrics.get(field) not in (None, "", [], {})
        ):
            return False
    for raw_qty in (
        item.get("fill_quantity"),
        item.get("filled_quantity"),
        metrics.get("fill_quantity"),
        metrics.get("filled_quantity"),
        metadata.get("fill_quantity"),
        metadata.get("filled_quantity"),
    ):
        if raw_qty is None:
            continue
        if isinstance(raw_qty, bool):
            return False
        try:
            parsed_qty = float(raw_qty)
        except (TypeError, ValueError):
            return False
        if parsed_qty != 0.0 or parsed_qty != parsed_qty or parsed_qty in (float("inf"), float("-inf")):
            return False
    for raw_qty in (
        item.get("quantity"),
        metrics.get("quantity"),
        metadata.get("quantity"),
    ):
        if raw_qty is None:
            continue
        if isinstance(raw_qty, bool):
            return False
        try:
            parsed_qty = float(raw_qty)
        except (TypeError, ValueError):
            return False
        if parsed_qty != parsed_qty or parsed_qty in (float("inf"), float("-inf")):
            return False
        if check_quantity_zero and parsed_qty != 0.0:
            return False
    return True


def _source_event_id(event: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not isinstance(event, Mapping):
        return None
    for key in ("source_event_id", "canonical_event_id"):
        val = str(event.get(key) or "").strip()
        if val:
            return val
    raw_event_id = str(event.get("event_id") or "").strip()
    if raw_event_id:
        return raw_event_id.split(":", 1)[0]
    return None


def _is_risk_gate_rejection(event: Mapping[str, Any]) -> bool:
    """An order_rejection stopped before the broker by the risk gate.

    Recorded shape: stage failed, submitted_to_broker false, no broker ids (the
    caller already proved the latter). Explicit risk_gate_evaluated or
    risk_status rejected also qualify.
    """
    if event.get("event_type") not in {"order_rejection", "order_rejection_simulated"}:
        return False
    if event.get("risk_gate_evaluated") is True:
        return True
    if str(event.get("risk_status") or "").strip().lower() == "rejected":
        return True
    # Recorded shape needs a stored reason or expression reason; a reasonless rejection stays partial.
    return event.get("submitted_to_broker") is False and any(
        bool(str(event.get(field) or "").strip())
        for field in (
            "reason_code",
            "expression_reason_code",
            "reject_reason",
            "risk_reason",
            "reason",
            "broker_submission_status",
        )
    )


def _effective_snapshot_and_diagnostics(
    snapshot: Mapping[str, Any],
    diagnostics: Sequence[Mapping[str, Any]],
    timeline: Sequence[Mapping[str, Any]] = (),
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    raw_stages = snapshot.get("stages")
    stages: Dict[str, Any] = (
        {str(k): dict(v) if isinstance(v, Mapping) else {"status": str(v)} for k, v in raw_stages.items()}
        if isinstance(raw_stages, Mapping)
        else {}
    )
    diag_list: List[Dict[str, Any]] = [
        dict(item) for item in diagnostics if isinstance(item, Mapping)
    ]
    if "reconciliation" in stages and (stages.get("reconciliation") or {}).get("status") == "failed":
        recon_event = _event_for_stage(timeline, "reconciliation")
        recon_analysis = _reconciliation_analysis(recon_event)
        recon_stage = dict(stages["reconciliation"])
        recon_stage["reason_code"] = recon_analysis["reason_code"]
        recon_stage["classification"] = recon_analysis["classification"]
        recon_stage["accounting_variance"] = recon_analysis["accounting_variance"]
        recon_stage["comparison_completed"] = recon_analysis["comparison_completed"]
        if recon_analysis["reason_detail"] is not None:
            recon_stage["reason_detail"] = recon_analysis["reason_detail"]
        if recon_analysis["discrepancies"]:
            recon_stage["discrepancies"] = recon_analysis["discrepancies"]
        if recon_analysis["missing_evidence"]:
            recon_stage["missing_evidence"] = recon_analysis["missing_evidence"]
        if recon_analysis["source_event_id"]:
            recon_stage["source_event_id"] = recon_analysis["source_event_id"]
        stages["reconciliation"] = recon_stage
    has_explicit_risk = "risk_evaluation" in stages
    if has_explicit_risk and not (
        (stages.get("risk_evaluation") or {}).get("status") == "rejected"
        and (stages.get("order_submission") or {}).get("status") in {"failed", "rejected"}
    ):
        return {**snapshot, "stages": stages}, diag_list
    if stages.get("signal_generation", {}).get("status") != "succeeded":
        return {**snapshot, "stages": stages}, diag_list
    if stages.get("trade_decision", {}).get("status") not in {"succeeded", "skipped"}:
        return {**snapshot, "stages": stages}, diag_list
    if {"broker_acknowledgement", "fill_management", "ledger_booking", "reconciliation"} & set(stages):
        return {**snapshot, "stages": stages}, diag_list
    identifiers = snapshot.get("identifiers") if isinstance(snapshot.get("identifiers"), Mapping) else {}
    if any(
        identifiers.get(field)
        for field in (
            "broker_order_id",
            "fill_id",
            "broker_trade_id",
            "ledger_entry_id",
            "reconciliation_id",
        )
    ):
        return {**snapshot, "stages": stages}, diag_list
    if not all(
        isinstance(item, Mapping)
        and _is_nonfill_unsubmitted_event(
            item, check_quantity_zero=(item.get("stage") == "order_submission")
        )
        for item in (timeline or ())
    ):
        return {**snapshot, "stages": stages}, diag_list
    order_stage = stages.get("order_submission")
    if not isinstance(order_stage, Mapping):
        return {**snapshot, "stages": stages}, diag_list
    order_status = order_stage.get("status")
    if order_status not in {"failed", "rejected", "skipped", "not_submitted"}:
        return {**snapshot, "stages": stages}, diag_list
    submission_events = [
        item
        for item in (timeline or ())
        if isinstance(item, Mapping) and item.get("stage") == "order_submission"
    ]
    inferred_status: Optional[str] = None
    gate_evidence: Optional[Dict[str, Any]] = None
    if submission_events:
        if (
            order_status == "skipped"
            and all(item.get("event_type") == "paper_order_simulated" for item in submission_events)
            and (
                stages.get("trade_decision", {}).get("status") == "skipped"
                or any(
                    str(item.get("decision_status") or "").strip().lower() == "no_order"
                    or str(item.get("order_status") or "").strip().lower() == "not_submitted"
                    or str(item.get("risk_status") or "").strip().lower() == "skipped"
                    or bool(item.get("noop_reason") or item.get("reason_code"))
                    for item in submission_events
                )
            )
        ):
            inferred_status = "skipped"
        elif order_status in {"failed", "rejected", "not_submitted"}:
            gate_event = next(
                (
                    item
                    for item in submission_events
                    if _is_risk_gate_rejection(item)
                    or (
                        item.get("event_type") in {"order_rejection", "order_rejection_simulated"}
                        and str(
                            item.get("reason_code")
                            or item.get("reject_reason")
                            or item.get("reason")
                            or item.get("risk_reason")
                            or item.get("broker_submission_status")
                            or ""
                        ).strip()
                        == "sell_without_holdings"
                    )
                ),
                None,
            )
            if gate_event is not None:
                inferred_status = "rejected"
                gate_evidence = _rejection_evidence(gate_event)
    if inferred_status is None:
        return {**snapshot, "stages": stages}, diag_list
    risk_stage_info: Dict[str, Any] = {
        "status": inferred_status,
        "updated_at": order_stage.get("updated_at"),
        "revision": order_stage.get("revision", 1),
    }
    if gate_evidence is not None:
        risk_stage_info["reason_code"] = gate_evidence["reason_code"]
        if gate_evidence["reason_detail"] is not None:
            risk_stage_info["reason_detail"] = gate_evidence["reason_detail"]
        if gate_evidence["source_event_id"]:
            risk_stage_info["source_event_id"] = gate_evidence["source_event_id"]
        if gate_evidence["expression_reason_code"]:
            risk_stage_info["expression_reason_code"] = gate_evidence["expression_reason_code"]
        if gate_evidence["missing_evidence"]:
            risk_stage_info["missing_evidence"] = gate_evidence["missing_evidence"]
        stages["order_submission"] = {
            "status": "not_submitted",
            "order_status": "not_submitted",
            "submitted_to_broker": False,
            "updated_at": order_stage.get("updated_at"),
            "revision": order_stage.get("revision", 1),
        }
    stages["risk_evaluation"] = risk_stage_info
    raw_completeness = snapshot.get("completeness")
    completeness = dict(raw_completeness) if isinstance(raw_completeness, Mapping) else {}
    missing_stages = [
        stage
        for stage in (completeness.get("missing_stages") or [])
        if stage != "risk_evaluation"
    ]
    completeness["missing_stages"] = missing_stages
    completeness["complete"] = not missing_stages
    normalized_diagnostics: List[Dict[str, Any]] = []
    for item in diag_list:
        if item.get("code") == "missing_stages":
            if missing_stages:
                normalized_diagnostics.append({**item, "stages": missing_stages})
        else:
            normalized_diagnostics.append(item)
    return {**snapshot, "stages": stages, "completeness": completeness}, normalized_diagnostics


def _read_state(
    snapshot: Mapping[str, Any],
    diagnostics: Sequence[Mapping[str, Any]],
    timeline: Sequence[Mapping[str, Any]] = (),
) -> str:
    effective_snapshot, effective_diagnostics = _effective_snapshot_and_diagnostics(
        snapshot, diagnostics, timeline
    )
    codes = {item.get("code") for item in effective_diagnostics}
    if codes & _ANOMALY_DIAGNOSTIC_CODES:
        return "degraded"
    missing = (effective_snapshot.get("completeness") or {}).get("missing_stages") or []
    if any(stage in _OBSERVABLE_STAGES for stage in missing):
        return "partial"
    return "formal"


def _current_stage(snapshot: Mapping[str, Any]) -> Optional[str]:
    stages = snapshot.get("stages") or {}
    for stage in reversed(STAGES):
        if stage in stages:
            info = stages.get(stage)
            if isinstance(info, Mapping) and info.get("status") == "not_submitted":
                continue
            return stage
    for stage in reversed(STAGES):
        if stage in stages:
            return stage
    return None


_REJECTION_STAGES = ("risk_evaluation", "order_submission", "broker_acknowledgement")


def _flags(snapshot: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]) -> Dict[str, bool]:
    stages = snapshot.get("stages") or {}
    statuses = {info.get("status") for info in stages.values() if isinstance(info, Mapping)}
    codes = {item.get("code") for item in diagnostics}
    recon_info = stages.get("reconciliation") if isinstance(stages.get("reconciliation"), Mapping) else {}
    recon_deferred = (
        recon_info.get("classification") in {"runtime_health", "broker_unavailable"}
        or recon_info.get("reason_code") in _DEFERRED_RECON_REASONS
    )
    return {
        "waiting_human": snapshot.get("status") == "waiting_human" or "waiting_human" in statuses,
        "blocked": snapshot.get("status") == "blocked" or "blocked" in statuses,
        "rejected": any(
            (stages.get(name) or {}).get("status") in {"rejected", "failed"}
            for name in _REJECTION_STAGES
        ),
        "mismatch": (
            snapshot.get("status") == "completed_with_variance"
            or recon_info.get("status") == "failed"
        )
        and not recon_deferred,
        "orphan": "orphan_identifier" in codes,
    }


def _severity(snapshot: Mapping[str, Any], diagnostics: Sequence[Mapping[str, Any]]) -> str:
    status = snapshot.get("status")
    codes = {item.get("code") for item in diagnostics}
    if status in {"failed", "incomplete"} or (codes & _ANOMALY_DIAGNOSTIC_CODES):
        return "critical"
    if status in {"blocked", "waiting_human", "completed_with_variance", "unreconciled"}:
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


def _compose_summary_fields(timeline: Sequence[Mapping[str, Any]], identifiers: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    summary = {field: _first_value(timeline, keys) for field, keys in _ROW_IDENTIFIER_KEYS}
    for field, keys in _ROW_IDENTIFIER_KEYS:
        if summary[field] is not None or not isinstance(identifiers, Mapping):
            continue
        for key in keys:
            value = identifiers.get(key)
            if isinstance(value, (list, tuple)):
                value = next((item for item in value if item not in (None, "")), None)
            if value not in (None, ""):
                summary[field] = value
                break
    return summary


_ATTENTION_VALUES = ("attention", "risk_reject", "broker_reject", "partial_fill", "recon_mismatch")
_UPSTREAM_STAGES = tuple(STAGES[: STAGES.index("signal_generation")])
# Expression choice codes describe how an order was expressed, never why it was
# rejected; a rejection that only carries one predates the reason fields.
_EXPRESSION_REASON_CODES = frozenset({"long_cash", "long_lever", "short_via_future", "close_leg"})
_DEFERRED_RECON_REASONS = frozenset(
    {
        "runtime_unhealthy_at_reconciliation",
        "broker_unavailable_at_reconciliation",
        "reconciliation_deferred_runtime_unhealthy",
        "reconciliation_deferred_broker_unavailable",
    }
)
_REASON_NOT_RECORDED = "reason_not_recorded"
_CASH_REJECT_REASONS = frozenset({"insufficient_cash", "insufficient_buying_power", "insufficient_initial_margin"})
_POSITION_REJECT_REASONS = frozenset({"insufficient_position", "sell_without_holdings"})
_RUNTIME_HEALTH_RECON_CHECKS = frozenset(
    {
        "runtime_state",
        "runtime_health_summary",
        "queue_lag_ms",
        "event_delivery_lag_ms",
        "actual_metrics_presence",
    }
)
_TRADE_COMPARISON_CHECKS = frozenset(
    {
        "broker_record",
        "broker_fill_qty",
        "broker_fill_price",
        "broker_status",
        "broker_side",
        "broker_instrument_id",
        "position_change",
        "committed_snapshot_position",
        "cash_change",
        "committed_snapshot_cash",
    }
)
_NUMERIC_COMPARISON_CHECKS = frozenset(
    {
        "broker_fill_qty",
        "broker_fill_price",
        "position_change",
        "committed_snapshot_position",
        "cash_change",
        "committed_snapshot_cash",
    }
)
_ACCOUNTING_VARIANCE_REASONS = frozenset(
    {
        "cash_variance",
        "position_variance",
        "broker_fill_mismatch",
        "broker_record_missing",
    }
)
_COMPARISON_TOLERANCE = 1e-6


_PERSONA_DIRECTORY_TTL_SECONDS = 60.0
_PERSONA_DIRECTORY_STALE_SECONDS = 300.0
_persona_directory_cache: Dict[Callable[[], Any], Tuple[float, Dict[str, Dict[str, Any]]]] = {}
_persona_directory_lock = threading.Lock()


def _read_persona_directory(list_personas: Callable[[], Any]) -> Dict[str, Dict[str, Any]]:
    directory: Dict[str, Dict[str, Any]] = {}
    for record in list_personas() or []:
        if not isinstance(record, Mapping):
            continue
        persona_id = str(record.get("persona_id") or record.get("id") or "").strip()
        if not persona_id:
            continue
        name = record.get("name") or record.get("display_name")
        directory[persona_id] = {
            "name": str(name) if name else None,
            "is_probe": bool(is_probe_or_test_persona(record)),
        }
    return directory


def _persona_directory(
    list_personas: Optional[Callable[[], Any]],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Dict[str, Dict[str, Any]]:
    """id -> {name, is_probe}, read at most once per 60s per process.

    Shared by list, detail and evidence reads. A failed read keeps the previous
    directory for up to 300s, then an empty one so rows keep a null name.
    """
    if list_personas is None:
        return {}
    key = list_personas
    with _persona_directory_lock:
        now = clock()
        cached = _persona_directory_cache.get(key)
        if cached is not None and now - cached[0] < _PERSONA_DIRECTORY_TTL_SECONDS:
            return cached[1]
        try:
            directory = _read_persona_directory(list_personas)
        except Exception:
            if cached is not None and now - cached[0] < _PERSONA_DIRECTORY_STALE_SECONDS:
                return cached[1]
            _persona_directory_cache.pop(key, None)
            return {}
        _persona_directory_cache[key] = (now, directory)
        return directory


def _event_for_stage(timeline: Sequence[Mapping[str, Any]], stage: str) -> Optional[Mapping[str, Any]]:
    for event in timeline:
        if event.get("stage") == stage:
            return event
    return None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _order_quantities(timeline: Sequence[Mapping[str, Any]], summary_quantity: Any) -> Tuple[Any, Optional[float]]:
    """Requested order quantity and filled quantity, never the post-fill position."""
    requested = None
    for event in timeline:
        value = _float_or_none(event.get("requested_quantity"))
        if value is not None:
            requested = value
            break
    fill_event = _event_for_stage(timeline, "fill_management")
    filled = None
    if fill_event is not None:
        filled = _float_or_none(fill_event.get("fill_quantity"))
        if filled is None:
            filled = _float_or_none(fill_event.get("filled_quantity"))
        if filled is None:
            filled = _float_or_none(fill_event.get("quantity"))
    if requested is None:
        order_event = _event_for_stage(timeline, "order_submission")
        requested = _float_or_none((order_event or {}).get("target_quantity"))
    if requested is None:
        requested = summary_quantity
    return requested, filled


def _rejection_evidence(event: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(event, Mapping):
        return {
            "reason_code": _REASON_NOT_RECORDED,
            "reason_detail": None,
            "expression_reason_code": None,
            "source_event_id": None,
            "missing_evidence": ["reject_reason"],
            "submitted_to_broker": None,
            "order_status": None,
        }
    source_event_id = _source_event_id(event)
    expression_code: Optional[str] = None
    for candidate in (
        event.get("expression_reason_code"),
        event.get("reason_code"),
        event.get("reject_reason"),
        event.get("risk_reason"),
        event.get("reason"),
        event.get("broker_submission_status"),
    ):
        text = str(candidate or "").strip()
        if text in _EXPRESSION_REASON_CODES:
            expression_code = text
            break
    chosen_code: Optional[str] = None
    for candidate in (
        event.get("reject_reason"),
        event.get("risk_reason"),
        event.get("reason"),
        event.get("broker_submission_status"),
        event.get("reason_code"),
    ):
        text = str(candidate or "").strip()
        if text and text not in _EXPRESSION_REASON_CODES and text != _REASON_NOT_RECORDED:
            chosen_code = text
            break
    reason_code = chosen_code or _REASON_NOT_RECORDED
    raw_detail = event.get("reason_detail")
    detail_map: Dict[str, Any] = dict(raw_detail) if isinstance(raw_detail, Mapping) else {}
    for detail_key in ("required", "available", "limit"):
        if detail_key not in detail_map and event.get(detail_key) is not None:
            detail_map[detail_key] = event.get(detail_key)
    if reason_code != _REASON_NOT_RECORDED or detail_map:
        for qty_key in ("holding_quantity", "requested_quantity"):
            if qty_key not in detail_map and event.get(qty_key) is not None:
                detail_map[qty_key] = event.get(qty_key)
    reason_detail: Any = (
        detail_map
        if detail_map
        else (raw_detail if raw_detail not in (None, {}, []) else None)
    )
    missing_evidence: List[str] = [
        str(item)
        for item in (event.get("missing_evidence") or [])
        if isinstance(event.get("missing_evidence"), Sequence)
        and not isinstance(event.get("missing_evidence"), (str, bytes))
        and str(item).strip()
    ]
    if reason_code == _REASON_NOT_RECORDED and "reject_reason" not in missing_evidence:
        missing_evidence.append("reject_reason")
    elif reason_code in _CASH_REJECT_REASONS:
        for req_key in ("required", "available"):
            if detail_map.get(req_key) is None and req_key not in missing_evidence:
                missing_evidence.append(req_key)
    elif reason_code in _POSITION_REJECT_REASONS:
        for req_key in ("holding_quantity", "requested_quantity"):
            if detail_map.get(req_key) is None and req_key not in missing_evidence:
                missing_evidence.append(req_key)
    submitted = event.get("submitted_to_broker") if isinstance(event.get("submitted_to_broker"), bool) else None
    order_status = (
        "not_submitted"
        if submitted is False
        else (str(event.get("order_status")).strip() if event.get("order_status") else None)
    )
    return {
        "reason_code": reason_code,
        "reason_detail": reason_detail,
        "expression_reason_code": expression_code,
        "source_event_id": source_event_id,
        "missing_evidence": missing_evidence,
        "submitted_to_broker": submitted,
        "order_status": order_status,
    }


def _rejected_reason(event: Optional[Mapping[str, Any]]) -> Tuple[str, Any]:
    ev = _rejection_evidence(event)
    return ev["reason_code"], ev["reason_detail"]


def _infer_variance_reason_code(discrepancies: Sequence[Mapping[str, Any]]) -> str:
    checks = {str(item.get("check") or "") for item in discrepancies}
    if "broker_record" in checks:
        return "broker_record_missing"
    if checks & {"broker_fill_qty", "broker_fill_price", "broker_status", "broker_side", "broker_instrument_id"}:
        return "broker_fill_mismatch"
    if checks & {"position_change", "committed_snapshot_position"}:
        return "position_variance"
    if checks & {"cash_change", "committed_snapshot_cash"}:
        return "cash_variance"
    return "reconciliation_check_failed"


def _reconciliation_analysis(recon: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    source_event_id = _source_event_id(recon)
    raw_code = str((recon or {}).get("reason_code") or "").strip()
    raw_detail = (recon or {}).get("reason_detail")
    normalized_detail: Any = None
    discrepancies: List[Dict[str, Any]] = []
    unverified_missing: List[str] = []
    check_names: set[str] = set()
    has_broker_unavailable = False

    if isinstance(raw_detail, Sequence) and not isinstance(raw_detail, (str, bytes)):
        normalized_checks: List[Any] = []
        for item in raw_detail:
            if not isinstance(item, Mapping):
                normalized_checks.append(item)
                continue
            entry = dict(item)
            check_name = str(entry.get("check") or "").strip()
            if check_name:
                check_names.add(check_name)
            status_val = str(entry.get("status") or "").strip().lower()
            if check_name == "broker_record" and status_val == "unavailable":
                has_broker_unavailable = True
            if check_name in _NUMERIC_COMPARISON_CHECKS:
                exp_num = _float_or_none(entry.get("expected"))
                act_num = _float_or_none(entry.get("actual"))
                if exp_num is not None and act_num is not None:
                    diff = _float_or_none(entry.get("delta"))
                    if diff is None:
                        diff = _float_or_none(entry.get("difference"))
                    if diff is None:
                        diff = act_num - exp_num
                    entry["difference"] = diff
                    entry["delta"] = diff
                    if status_val == "failed" and abs(diff) > _COMPARISON_TOLERANCE:
                        discrepancies.append(
                            {
                                "check": check_name,
                                "status": "failed",
                                "expected": exp_num,
                                "actual": act_num,
                                "difference": diff,
                                "delta": diff,
                            }
                        )
                else:
                    if status_val == "failed":
                        entry["status"] = "unverified"
                    entry["difference"] = None
                    entry["delta"] = None
                    if exp_num is None:
                        unverified_missing.append(f"{check_name}:expected")
                    if act_num is None:
                        unverified_missing.append(f"{check_name}:actual")
            elif check_name in _TRADE_COMPARISON_CHECKS:
                exp_val = entry.get("expected")
                act_val = entry.get("actual")
                if status_val in {"unverified", "missing_evidence"}:
                    for side_name, side_val in (("expected", exp_val), ("actual", act_val)):
                        if side_val in (None, ""):
                            unverified_missing.append(f"{check_name}:{side_name}")
                    for m_item in (entry.get("missing_evidence") or []):
                        if str(m_item).strip() and str(m_item) not in unverified_missing:
                            unverified_missing.append(str(m_item))
                elif status_val == "failed":
                    if exp_val not in (None, "") and act_val not in (None, "") and exp_val != act_val:
                        disc: Dict[str, Any] = {
                            "check": check_name,
                            "status": "failed",
                            "expected": exp_val,
                            "actual": act_val,
                        }
                        if entry.get("broker_order_id"):
                            disc["broker_order_id"] = entry["broker_order_id"]
                        discrepancies.append(disc)
                    else:
                        entry["status"] = "unverified"
                        if exp_val in (None, ""):
                            unverified_missing.append(f"{check_name}:expected")
                        if act_val in (None, ""):
                            unverified_missing.append(f"{check_name}:actual")
            normalized_checks.append(entry)
        normalized_detail = normalized_checks
    elif raw_detail is not None:
        normalized_detail = raw_detail

    explicit_missing: List[str] = [
        str(item)
        for item in ((recon or {}).get("missing_evidence") or [])
        if isinstance((recon or {}).get("missing_evidence"), Sequence)
        and not isinstance((recon or {}).get("missing_evidence"), (str, bytes))
        and str(item).strip()
    ]

    if raw_code in {"runtime_unhealthy_at_reconciliation", "reconciliation_deferred_runtime_unhealthy"} or (
        check_names and check_names <= _RUNTIME_HEALTH_RECON_CHECKS
    ):
        return {
            "kind": "reconciliation_deferred",
            "classification": "runtime_health",
            "accounting_variance": False,
            "comparison_completed": False,
            "reason_code": "runtime_unhealthy_at_reconciliation",
            "reason_detail": normalized_detail,
            "discrepancies": [],
            "missing_evidence": explicit_missing,
            "source_event_id": source_event_id,
        }
    if raw_code in {"broker_unavailable_at_reconciliation", "reconciliation_deferred_broker_unavailable"} or has_broker_unavailable:
        return {
            "kind": "reconciliation_deferred",
            "classification": "broker_unavailable",
            "accounting_variance": False,
            "comparison_completed": False,
            "reason_code": "broker_unavailable_at_reconciliation",
            "reason_detail": normalized_detail,
            "discrepancies": [],
            "missing_evidence": explicit_missing,
            "source_event_id": source_event_id,
        }
    if discrepancies:
        effective_code = (
            raw_code
            if (raw_code and raw_code != _REASON_NOT_RECORDED)
            else _infer_variance_reason_code(discrepancies)
        )
        return {
            "kind": "variance",
            "classification": "accounting_variance",
            "accounting_variance": True,
            "comparison_completed": True,
            "reason_code": effective_code,
            "reason_detail": normalized_detail,
            "discrepancies": discrepancies,
            "missing_evidence": explicit_missing,
            "source_event_id": source_event_id,
        }

    # Unverified or missing reconciliation evidence — never report a proven accounting variance.
    unverified_reason_code: Optional[str] = None
    if raw_code in _ACCOUNTING_VARIANCE_REASONS:
        unverified_reason_code = raw_code
        effective_code = "reconciliation_evidence_incomplete"
    elif raw_code:
        effective_code = raw_code
    else:
        effective_code = _REASON_NOT_RECORDED

    missing_evidence = list(explicit_missing)
    for item in unverified_missing:
        if item not in missing_evidence:
            missing_evidence.append(item)
    if not missing_evidence:
        if effective_code == _REASON_NOT_RECORDED:
            missing_evidence = ["reconciliation_reason", "booking_comparison"]
        elif effective_code == "reconciliation_identity_unverified":
            missing_evidence = ["authoritative_actual_identity"]
        else:
            missing_evidence = ["booking_comparison"]

    result: Dict[str, Any] = {
        "kind": "variance",
        "classification": "unverified_evidence",
        "accounting_variance": False,
        "comparison_completed": False,
        "reason_code": effective_code,
        "reason_detail": normalized_detail,
        "discrepancies": [],
        "missing_evidence": missing_evidence,
        "source_event_id": source_event_id,
    }
    if unverified_reason_code:
        result["unverified_reason_code"] = unverified_reason_code
    return result


def _outcome(snapshot: Mapping[str, Any], timeline: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    status = snapshot.get("status")
    stages = snapshot.get("stages") or {}

    def stage_status(name: str) -> Optional[str]:
        info = stages.get(name)
        return info.get("status") if isinstance(info, Mapping) else None

    order_event = _event_for_stage(timeline, "order_submission")
    if status == "no_order" or (
        order_event is not None
        and str(order_event.get("decision_status") or "").lower() == "no_order"
    ):
        code = (order_event or {}).get("reason_code") or _REASON_NOT_RECORDED
        return {"kind": "no_trade", "reason_code": str(code), "reason_detail": (order_event or {}).get("reason_detail")}
    if status == "completed_with_variance" or stage_status("reconciliation") == "failed":
        recon = _event_for_stage(timeline, "reconciliation")
        analysis = _reconciliation_analysis(recon)
        out: Dict[str, Any] = {
            "kind": analysis["kind"],
            "classification": analysis["classification"],
            "accounting_variance": analysis["accounting_variance"],
            "comparison_completed": analysis["comparison_completed"],
            "reason_code": analysis["reason_code"],
            "reason_detail": analysis["reason_detail"],
            "discrepancies": analysis["discrepancies"],
        }
        if analysis.get("source_event_id"):
            out["source_event_id"] = analysis["source_event_id"]
        if analysis.get("missing_evidence"):
            out["missing_evidence"] = analysis["missing_evidence"]
        if analysis.get("unverified_reason_code"):
            out["unverified_reason_code"] = analysis["unverified_reason_code"]
        return out
    if status == "failed" or any(
        stage_status(name) in {"failed", "rejected"}
        for name in ("risk_evaluation", "order_submission", "broker_acknowledgement")
    ):
        for name in ("broker_acknowledgement", "risk_evaluation", "order_submission"):
            if stage_status(name) in {"failed", "rejected"}:
                event = _event_for_stage(timeline, name)
                if event is None and name == "risk_evaluation":
                    event = _event_for_stage(timeline, "order_submission")  # inferred stage
                ev = _rejection_evidence(event)
                out = {
                    "kind": "rejected",
                    "reason_code": ev["reason_code"],
                    "reason_detail": ev["reason_detail"],
                    "rejected_stage": name,
                }
                if ev["source_event_id"]:
                    out["source_event_id"] = ev["source_event_id"]
                if ev["submitted_to_broker"] is not None:
                    out["submitted_to_broker"] = ev["submitted_to_broker"]
                if ev["order_status"]:
                    out["order_status"] = ev["order_status"]
                if ev["expression_reason_code"]:
                    out["expression_reason_code"] = ev["expression_reason_code"]
                if ev["missing_evidence"]:
                    out["missing_evidence"] = ev["missing_evidence"]
                return out
        return {"kind": "rejected", "reason_code": _REASON_NOT_RECORDED, "reason_detail": None, "missing_evidence": ["reject_reason"]}
    if status == "partially_filled":
        return {"kind": "open", "reason_code": "partially_filled", "reason_detail": None}
    if status in TERMINAL_STATUSES:
        return {"kind": "normal", "reason_code": None, "reason_detail": None}
    return {"kind": "open", "reason_code": None, "reason_detail": None}


def _next_step(
    outcome: Mapping[str, Any],
    persona_id: Any,
    binding_id: Any,
    open_incident_ids: Mapping[str, str],
) -> Optional[Dict[str, Any]]:
    """A link for an operator, never an action: the governed action route is separate."""
    kind = outcome.get("kind")
    if kind not in {"rejected", "variance"}:
        return None
    incident_id = open_incident_ids.get(str(binding_id)) if binding_id else None
    if incident_id:
        return {"action_needed": "review_open_incident", "href": f"/incidents/{incident_id}"}
    if persona_id:
        action = "review_cash_and_risk_limits" if kind == "rejected" else "review_reconciliation_variance"
        return {"action_needed": action, "href": f"/personas/{persona_id}"}
    return None


def _first_identifier(snapshot: Mapping[str, Any], key: str) -> Any:
    identifiers = snapshot.get("identifiers")
    if not isinstance(identifiers, Mapping):
        return None
    value = identifiers.get(key)
    if isinstance(value, (list, tuple)):
        value = next((item for item in value if item not in (None, "")), None)
    return value or None


def _list_row(
    projection: JourneyProjection,
    *,
    now: datetime,
    personas: Optional[Mapping[str, Mapping[str, Any]]] = None,
    open_incident_ids: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    snapshot, diagnostics = _effective_snapshot_and_diagnostics(
        projection.snapshot, projection.diagnostics, projection.timeline
    )
    summary = _compose_summary_fields(projection.timeline, snapshot.get("identifiers"))
    current_stage = _current_stage(snapshot)
    stages = snapshot.get("stages") or {}
    stage_info = stages.get(current_stage, {}) if current_stage else {}
    stage_updated_dt = _parse_iso(stage_info.get("updated_at"))
    created_dt = _parse_iso(snapshot.get("created_at"))
    status = snapshot.get("status")
    flags = _flags(snapshot, diagnostics)
    flags["stalled"] = _is_stalled(snapshot, now)
    quantity, filled_quantity = _order_quantities(projection.timeline, summary.get("quantity"))
    summary["quantity"] = quantity
    persona = (personas or {}).get(str(summary.get("persona_id") or "")) or {}
    outcome = _outcome(snapshot, projection.timeline)
    next_step = _next_step(outcome, summary.get("persona_id"), _first_identifier(snapshot, "binding_id"), open_incident_ids or {})
    return {
        "journey_id": projection.journey_id,
        "tenant_id": projection.tenant_id,
        "environment": projection.environment,
        "status": status,
        "current_stage": current_stage,
        "severity": _severity(snapshot, diagnostics),
        **summary,
        "filled_quantity": filled_quantity,
        "persona_name": persona.get("name"),
        "persona_is_probe": bool(persona.get("is_probe", False)),
        "outcome": outcome,
        "next_step": next_step,
        "created_at": snapshot.get("created_at"),
        "updated_at": snapshot.get("updated_at"),
        "terminal_at": snapshot.get("updated_at") if status in TERMINAL_STATUSES else None,
        "stage_elapsed_seconds": (now - stage_updated_dt).total_seconds() if stage_updated_dt else None,
        "total_elapsed_seconds": (now - created_dt).total_seconds() if created_dt else None,
        "flags": flags,
        "completeness": snapshot.get("completeness"),
        "read_state": _read_state(snapshot, diagnostics, projection.timeline),
        "revision": snapshot.get("revision"),
    }


def _stage_events(
    timeline: Sequence[Mapping[str, Any]],
    snapshot: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for event in timeline:
        stage = event.get("stage")
        if stage:
            latest[stage] = dict(event)
            src_id = _source_event_id(event)
            if src_id:
                latest[stage].setdefault("source_event_id", src_id)
    stages = (snapshot or {}).get("stages") if isinstance((snapshot or {}).get("stages"), Mapping) else {}
    if "reconciliation" in latest and (
        latest["reconciliation"].get("stage_status") == "failed"
        or latest["reconciliation"].get("event_type") == "reconciliation_failed"
    ):
        recon_analysis = _reconciliation_analysis(latest["reconciliation"])
        recon_ev = dict(latest["reconciliation"])
        recon_ev["reason_code"] = recon_analysis["reason_code"]
        recon_ev["classification"] = recon_analysis["classification"]
        recon_ev["accounting_variance"] = recon_analysis["accounting_variance"]
        recon_ev["comparison_completed"] = recon_analysis["comparison_completed"]
        if recon_analysis["reason_detail"] is not None:
            recon_ev["reason_detail"] = recon_analysis["reason_detail"]
        if recon_analysis["discrepancies"]:
            recon_ev["discrepancies"] = recon_analysis["discrepancies"]
        if recon_analysis["missing_evidence"]:
            recon_ev["missing_evidence"] = recon_analysis["missing_evidence"]
        if recon_analysis["source_event_id"]:
            recon_ev["source_event_id"] = recon_analysis["source_event_id"]
        latest["reconciliation"] = recon_ev

    order_ev = latest.get("order_submission")
    is_pre_broker_risk_reject = bool(
        isinstance(order_ev, Mapping)
        and (
            (
                isinstance(stages, Mapping)
                and (stages.get("risk_evaluation") or {}).get("status") == "rejected"
                and (stages.get("order_submission") or {}).get("status") == "not_submitted"
            )
            or (
                "risk_evaluation" not in latest
                and _is_risk_gate_rejection(order_ev)
                and _is_nonfill_unsubmitted_event(order_ev, check_quantity_zero=True)
            )
        )
    )
    if is_pre_broker_risk_reject and isinstance(order_ev, Mapping):
        ev = _rejection_evidence(order_ev)
        risk_ev = dict(order_ev)
        risk_ev["stage"] = "risk_evaluation"
        risk_ev["stage_status"] = "rejected"
        risk_ev["reason_code"] = ev["reason_code"]
        if ev["reason_detail"] is not None:
            risk_ev["reason_detail"] = ev["reason_detail"]
        if ev["expression_reason_code"]:
            risk_ev["expression_reason_code"] = ev["expression_reason_code"]
        if ev["source_event_id"]:
            risk_ev["source_event_id"] = ev["source_event_id"]
        if ev["missing_evidence"]:
            risk_ev["missing_evidence"] = ev["missing_evidence"]
        risk_ev["submitted_to_broker"] = False
        risk_ev["order_status"] = "not_submitted"
        latest["risk_evaluation"] = risk_ev

        sub_ev = dict(order_ev)
        sub_ev["stage"] = "order_submission"
        sub_ev["stage_status"] = "not_submitted"
        sub_ev["order_status"] = "not_submitted"
        sub_ev["submitted_to_broker"] = False
        sub_ev.pop("reason_code", None)
        sub_ev.pop("reason_detail", None)
        if ev["expression_reason_code"]:
            sub_ev["expression_reason_code"] = ev["expression_reason_code"]
        if ev["source_event_id"]:
            sub_ev["source_event_id"] = ev["source_event_id"]
        latest["order_submission"] = sub_ev
    return latest


def _upstream_lineage_diagnostics(diagnostics: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """Fold never-linked upstream research stages into one non-attention item."""
    result: List[Dict[str, Any]] = []
    unlinked: List[str] = []
    for item in diagnostics:
        if item.get("code") != "missing_stages":
            result.append(dict(item))
            continue
        stages = [str(stage) for stage in item.get("stages") or []]
        unlinked.extend(stage for stage in stages if stage in _UPSTREAM_STAGES and stage not in unlinked)
        rest = [stage for stage in stages if stage not in _UPSTREAM_STAGES]
        if rest:
            result.append({**item, "stages": rest})
    if unlinked:
        result.append({"code": "upstream_lineage_unlinked", "stages": unlinked, "attention": False})
    return result


def _detail(
    projection: JourneyProjection,
    *,
    now: datetime,
    personas: Optional[Mapping[str, Mapping[str, Any]]] = None,
    open_incident_ids: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    row = _list_row(projection, now=now, personas=personas, open_incident_ids=open_incident_ids)
    snapshot, diagnostics = _effective_snapshot_and_diagnostics(
        projection.snapshot, projection.diagnostics, projection.timeline
    )
    return {
        **row,
        "identifiers": snapshot.get("identifiers"),
        "stages": snapshot.get("stages"),
        "stage_events": _stage_events(projection.timeline, snapshot),
        "graph_edges": projection.graph_edges,
        "diagnostics": _upstream_lineage_diagnostics(diagnostics),
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
    snapshot, diagnostics = _effective_snapshot_and_diagnostics(
        projection.snapshot, projection.diagnostics, projection.timeline
    )
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
    stages = snapshot.get("stages") or {}
    if "risk_evaluation" in stages and "risk_evaluation" not in by_stage and "order_submission" in by_stage:
        by_stage["risk_evaluation"] = {
            "evidence_refs": list(by_stage["order_submission"]["evidence_refs"]),
            "input_refs": list(by_stage["order_submission"]["input_refs"]),
            "output_refs": list(by_stage["order_submission"]["output_refs"]),
            "policy_refs": list(by_stage["order_submission"]["policy_refs"]),
            "event_ids": list(by_stage["order_submission"]["event_ids"]),
        }
    return {
        "journey_id": projection.journey_id,
        "by_stage": by_stage,
        "completeness": snapshot.get("completeness"),
        "diagnostics": diagnostics,
    }


_TIMELINE_FIELDS = (
    "event_id", "journey_id", "stage", "event_type", "stage_status", "status", "occurred_at", "recorded_at",
    "actor", "summary", "reason_code", "reason_detail", "expression_reason_code",
    "submitted_to_broker", "order_status", "risk_status", "risk_gate_evaluated",
    "canonical_event_id", "source_event_id", "missing_evidence",
    "input_refs", "output_refs", "evidence_refs", "policy_refs",
    "causation_id", "correlation_id", "payload_redaction", "schema_version",
)


def _timeline_event(event: Mapping[str, Any]) -> Dict[str, Any]:
    row = {key: event[key] for key in _TIMELINE_FIELDS if key in event}
    row.setdefault("status", event.get("stage_status"))
    row.setdefault("payload_redaction", "none")
    row.setdefault("schema_version", event.get("schema_version", "1"))
    src_id = _source_event_id(event)
    if src_id:
        row.setdefault("source_event_id", src_id)
    if (
        _is_risk_gate_rejection(event)
        and _is_nonfill_unsubmitted_event(event, check_quantity_zero=True)
        and event.get("submitted_to_broker") is False
    ):
        ev = _rejection_evidence(event)
        row["stage"] = "risk_evaluation"
        row["stage_status"] = "rejected"
        row["status"] = "rejected"
        row["rejected_stage"] = "risk_evaluation"
        row["reason_code"] = ev["reason_code"]
        if ev["reason_detail"] is not None:
            row["reason_detail"] = ev["reason_detail"]
        if ev["expression_reason_code"]:
            row["expression_reason_code"] = ev["expression_reason_code"]
        if ev["source_event_id"]:
            row["source_event_id"] = ev["source_event_id"]
        if ev["missing_evidence"]:
            row["missing_evidence"] = ev["missing_evidence"]
        row["submitted_to_broker"] = False
        row["order_status"] = "not_submitted"
    elif event.get("event_type") == "reconciliation_failed" or (
        event.get("stage") == "reconciliation" and event.get("stage_status") == "failed"
    ):
        recon_analysis = _reconciliation_analysis(event)
        row["reason_code"] = recon_analysis["reason_code"]
        row["classification"] = recon_analysis["classification"]
        row["accounting_variance"] = recon_analysis["accounting_variance"]
        row["comparison_completed"] = recon_analysis["comparison_completed"]
        if recon_analysis["reason_detail"] is not None:
            row["reason_detail"] = recon_analysis["reason_detail"]
        if recon_analysis["discrepancies"]:
            row["discrepancies"] = recon_analysis["discrepancies"]
        if recon_analysis["missing_evidence"]:
            row["missing_evidence"] = recon_analysis["missing_evidence"]
        if recon_analysis["source_event_id"]:
            row["source_event_id"] = recon_analysis["source_event_id"]
    else:
        code = str(row.get("reason_code") or "").strip()
        if code in _EXPRESSION_REASON_CODES:
            row["reason_code"] = _REASON_NOT_RECORDED
            row["expression_reason_code"] = code
    envelope = event.get("correlation_envelope")
    producer = envelope.get("producer") if isinstance(envelope, Mapping) else None
    if producer:
        row["source"] = str(producer)
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
    snapshot, eff_diagnostics = _effective_snapshot_and_diagnostics(
        projection.snapshot, projection.diagnostics, projection.timeline
    )
    if snapshot.get("status") in TERMINAL_STATUSES:
        return None
    stages = snapshot.get("stages") or {}
    latest_stage = max(stages, key=lambda name: stages[name].get("updated_at", ""), default="signal_generation")
    updated = _parse_iso(snapshot.get("updated_at"))
    age = max(0, int((now - updated).total_seconds())) if updated else 0
    threshold = _stalled_thresholds()[projection.environment].get(
        latest_stage, _stalled_thresholds()[projection.environment]["default"]
    )
    diagnostics = [item.get("code") for item in eff_diagnostics]
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
    get_projection_reader: Callable[[], Optional[TradeJourneyProjectionStore]] = lambda: configured_projection_reader(),
    dispatch_action: Callable[[Dict[str, Any]], Dict[str, Any]] = _unconfigured_action_dispatcher,
    action_ledger: ActionLedger = ACTION_LEDGER,
    utc_now: Callable[[], str] = _default_utc_now,
    get_slo_alert_transport: Callable[[], DataQualityAlertTransport] = lambda: SLO_ALERT_TRANSPORT,
    latency_recorder: ApiLatencyRecorder = API_LATENCY_RECORDER,
    list_personas: Optional[Callable[[], Any]] = None,
    list_incidents: Optional[Callable[[], Any]] = None,
) -> APIRouter:
    """Factory for the `/bff/management/trade-journeys` route family.

    Dependencies are injected so tests can replace the event store without
    circular imports (main.py holds the live singletons), matching the
    `console_gap`/`trade_journal.py` factory-router convention.
    """
    router = APIRouter()

    def _identity(authorization: Optional[str]) -> Any:
        return extract_identity(authorization)

    def _open_incident_ids(binding_ids: Sequence[str]) -> Dict[str, str]:
        """binding_id -> id of an open incident that names it (one read, only when needed)."""
        wanted = [item for item in binding_ids if item]
        if list_incidents is None or not wanted:
            return {}
        try:
            incidents = list_incidents() or []
        except Exception:
            return {}
        found: Dict[str, str] = {}
        for incident in incidents:
            if not isinstance(incident, Mapping):
                continue
            if str(incident.get("status") or "open").lower() in {"resolved", "closed", "cancelled"}:
                continue
            incident_id = incident.get("incident_id") or incident.get("id")
            if not incident_id:
                continue
            text = json.dumps(incident, default=str)
            for binding_id in wanted:
                if binding_id in text and binding_id not in found:
                    found[binding_id] = str(incident_id)
        return found

    def _incident_map(projections: Sequence[JourneyProjection]) -> Dict[str, str]:
        binding_ids: List[str] = []
        for projection in projections:
            if projection.snapshot.get("status") == "completed_with_variance":
                value = _first_identifier(projection.snapshot, "binding_id")
                if value:
                    binding_ids.append(str(value))
        return _open_incident_ids(binding_ids)

    @router.post(
        "/bff/management/trade-journeys/{journey_id}/actions",
        response_model=TradeJourneyActionEnvelope,
    )
    def bff_trade_journey_action(
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
        reader = get_projection_reader()
        try:
            projection = reader.get_journey(tenant_id=tenant_id, environment=environment, journey_id=journey_id) if _tenant_allowed(identity, tenant_id) else None
        except ProjectionReadUnavailable:
            return _err(503, "SERVICE_UNAVAILABLE", "Postgres projection reader unavailable")
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
    def bff_trade_journeys_events(
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
        reader = get_projection_reader()
        try:
            freshness = reader.controller_freshness(tenant_id=tenant_id, environment=environment)
        except ProjectionReadUnavailable:
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Trade journey projection reader unavailable", retryable=True)
        if freshness is None:
            revision = 0
        else:
            revision = int(freshness.get("generation") or freshness.get("checkpoint") or 0)
        try:
            cursor = int(last_event_id or 0)
            if cursor < 0:
                raise ValueError
        except ValueError:
            return _err(400, "VALIDATION_FAILED", "Last-Event-ID must be a non-negative integer")

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
    def bff_trade_journeys_attention(
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
        reader = get_projection_reader()
        try:
            page_result = reader.page_journeys(
                tenant_id=tenant_id, environment=environment, page_size=MAX_PAGE_SIZE
            )
            now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
            items = [
                _mask_live_sensitive(item, identity, projection.environment)
                for projection in page_result.items
                if (item := _attention_item(projection, now=now)) is not None
            ]
            items.sort(key=lambda item: ({"critical": 0, "high": 1, "medium": 2}[item["severity"]], -item["age_seconds"]))
            return {
                "data": {
                    "id": "trade-journey-attention",
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "items": items,
                },
                "page_info": {"total": len(items), "returned": len(items), "has_more": False},
                "meta": _projection_reader_meta(
                    snapshot_at, "formal", reader, tenant_id=tenant_id, environment=environment
                ),
            }
        except ProjectionReadUnavailable:
            return _unavailable_list_envelope(
                snapshot_at, 200, entity_id="trade-journey-attention"
            )

    @router.get("/bff/management/trade-journeys/resolve", response_model=TradeJourneyDetailEnvelope)
    def bff_trade_journeys_resolve(
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

    @router.get("/bff/management/trade-journeys/metrics", response_model=TradeJourneyDetailEnvelope)
    def bff_trade_journeys_metrics(
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
        try:
            metrics = reader.metrics(tenant_id=tenant_id, environment=environment)
            data = {"id": "trade-journey-metrics", "tenant_id": tenant_id, "environment": environment, **metrics}
            read_state = "formal" if metrics["total_journeys"] else "partial"
            return {"data": data, "meta": _projection_reader_meta(snapshot_at, read_state, reader, tenant_id=tenant_id, environment=environment)}
        except ProjectionReadUnavailable:
            return _unavailable_envelope(snapshot_at, entity_id="trade-journey-metrics")

    @router.get("/bff/management/trade-journeys/slo", response_model=TradeJourneyDetailEnvelope)
    def bff_trade_journeys_slo(
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
        reader = get_projection_reader()
        try:
            page_result = reader.page_journeys(
                tenant_id=tenant_id, environment=environment, page_size=MAX_PAGE_SIZE
            )
            controller = reader.controller_freshness(tenant_id=tenant_id, environment=environment) or {}
            projections = page_result.items
            now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
            targets = load_slo_targets(environment)
            source_watermarks = {"postgres": str(controller.get("checkpoint") or 0)}
            metrics = compute_data_quality_metrics(
                projections,
                environment=environment,
                source_watermarks=source_watermarks,
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
            meta = _projection_reader_meta(snapshot_at, read_state, reader, tenant_id=tenant_id, environment=environment)
            return {"data": data, "meta": meta}
        except ProjectionReadUnavailable:
            return _unavailable_envelope(snapshot_at, entity_id="trade-journey-slo")

    @router.get(
        "/bff/management/trade-journeys",
        response_model=TradeJourneyListEnvelope,
    )
    def bff_trade_journeys_list(
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
        attention: Optional[str] = Query(default=None),
        include_probe: bool = Query(default=False),
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
        if attention is not None and attention not in _ATTENTION_VALUES:
            return _err(400, "VALIDATION_FAILED", f"attention must be one of {list(_ATTENTION_VALUES)}")
        if not _tenant_allowed(identity, tenant_id):
            return _err(403, "FORBIDDEN", "Cross-tenant access denied")

        snapshot_at = utc_now()
        reader = get_projection_reader()
        personas = _persona_directory(list_personas)
        can_view_sensitive = _can_view_live_sensitive(identity, environment)
        filters = {
            "q": q,
            "persona_id": persona_id,
            "strategy_id": strategy_id,
            "decision_id": decision_id if can_view_sensitive else ("__FORBIDDEN_PROBE__" if decision_id is not None else None),
            "order_id": order_id if can_view_sensitive else ("__FORBIDDEN_PROBE__" if order_id is not None else None),
            "broker_order_id": broker_order_id if can_view_sensitive else ("__FORBIDDEN_PROBE__" if broker_order_id is not None else None),
            "stage": stage,
            "status": status,
            "stalled": stalled,
            "waiting_human": waiting_human,
            "reconciliation_state": reconciliation_state,
            "attention": attention,
            "exclude_persona_ids": None
            if include_probe
            else (sorted(pid for pid, info in personas.items() if info["is_probe"]) or None),
            "date_from": _normalize_bound(date_from),
            "date_to": _normalize_bound(date_to),
        }
        if not can_view_sensitive:
            filters["q_journey_only"] = True
        try:
            page_result = reader.page_journeys(
                tenant_id=tenant_id,
                environment=environment,
                filters=filters,
                sort=sort,
                page_size=page_size,
                page_token=page_token,
            )
        except InvalidPageToken:
            return _err(400, "VALIDATION_FAILED", "page_token is invalid for this scope, filter, or sort")
        except ProjectionReadUnavailable:
            return _unavailable_list_envelope(snapshot_at, page_size, entity_id="trade-journeys")

        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        incident_map = _incident_map(page_result.items)
        items = [
            _mask_live_sensitive(
                _list_row(projection, now=now, personas=personas, open_incident_ids=incident_map),
                identity,
                projection.environment,
            )
            for projection in page_result.items
        ]
        states = {item.get("read_state") for item in items}
        overall_state = "formal" if not states or states == {"formal"} else "degraded" if "degraded" in states else "partial"
        return {
            "data": {
                "id": "trade-journeys",
                "tenant_id": tenant_id,
                "environment": environment,
                "items": items,
            },
            "page_info": {
                "next_page_token": page_result.next_page_token,
                "total": page_result.total,
                "page_size": page_size,
                "returned": len(items),
                "has_more": page_result.next_page_token is not None,
            },
            "meta": _projection_reader_meta(
                snapshot_at,
                overall_state,
                reader,
                tenant_id=tenant_id,
                environment=environment,
            ),
        }

    @router.get("/bff/management/trade-journeys/{journey_id}", response_model=TradeJourneyDetailEnvelope)
    def bff_trade_journeys_detail(
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
        try:
            projection = (
                reader.get_journey(
                    tenant_id=tenant_id,
                    environment=environment,
                    journey_id=journey_id,
                )
                if _tenant_allowed(identity, tenant_id)
                else None
            )
        except ProjectionReadUnavailable:
            return _unavailable_envelope(snapshot_at, entity_id=journey_id)
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        detail = _mask_live_sensitive(
            _detail(
                projection,
                now=now,
                personas=_persona_directory(list_personas),
                open_incident_ids=_incident_map([projection]),
            ),
            identity,
            projection.environment,
        )
        latency_recorder.record(
            "detail", (time.perf_counter() - _slo_timer_start) * 1000.0
        )
        return {
            "data": detail,
            "meta": _projection_reader_meta(
                snapshot_at,
                detail.get("read_state", "formal"),
                reader,
                tenant_id=tenant_id,
                environment=environment,
            ),
        }

    @router.get(
        "/bff/management/trade-journeys/{journey_id}/timeline",
        response_model=TradeJourneyListEnvelope,
    )
    def bff_trade_journeys_timeline(
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
        try:
            projection = (
                reader.get_journey(
                    tenant_id=tenant_id,
                    environment=environment,
                    journey_id=journey_id,
                )
                if _tenant_allowed(identity, tenant_id)
                else None
            )
            if projection is None:
                return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
            page_result = reader.page_timeline(
                tenant_id=tenant_id,
                environment=environment,
                journey_id=journey_id,
                page_size=page_size,
                page_token=page_token,
            )
        except InvalidPageToken:
            return _err(400, "VALIDATION_FAILED", "page_token is invalid for this journey scope")
        except ProjectionReadUnavailable:
            return _unavailable_list_envelope(
                snapshot_at, page_size, entity_id=f"{journey_id}-timeline"
            )

        events = [
            _mask_live_sensitive(_timeline_event(event), identity, projection.environment)
            for event in page_result.items
        ]
        return {
            "data": {
                "id": f"{journey_id}-timeline",
                "journey_id": journey_id,
                "items": events,
            },
            "page_info": {
                "next_page_token": page_result.next_page_token,
                "total": page_result.total,
                "page_size": page_size,
                "returned": len(events),
                "has_more": page_result.next_page_token is not None,
            },
            "meta": _projection_reader_meta(
                snapshot_at,
                _read_state(projection.snapshot, projection.diagnostics, projection.timeline),
                reader,
                tenant_id=tenant_id,
                environment=environment,
            ),
        }

    @router.get("/bff/management/trade-journeys/{journey_id}/graph", response_model=TradeJourneyDetailEnvelope)
    def bff_trade_journeys_graph(
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
        try:
            projection = (
                reader.get_journey(
                    tenant_id=tenant_id,
                    environment=environment,
                    journey_id=journey_id,
                )
                if _tenant_allowed(identity, tenant_id)
                else None
            )
        except ProjectionReadUnavailable:
            return _unavailable_envelope(snapshot_at, entity_id=f"{journey_id}-graph")
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
        return {
            "data": _mask_live_graph(
                _graph(projection), identity, projection.environment
            ),
            "meta": _projection_reader_meta(
                snapshot_at,
                _read_state(projection.snapshot, projection.diagnostics, projection.timeline),
                reader,
                tenant_id=tenant_id,
                environment=environment,
            ),
        }

    @router.get("/bff/management/trade-journeys/{journey_id}/evidence", response_model=TradeJourneyDetailEnvelope)
    def bff_trade_journeys_evidence(
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
        try:
            projection = (
                reader.get_journey(
                    tenant_id=tenant_id,
                    environment=environment,
                    journey_id=journey_id,
                )
                if _tenant_allowed(identity, tenant_id)
                else None
            )
        except ProjectionReadUnavailable:
            return _unavailable_envelope(snapshot_at, entity_id=f"{journey_id}-evidence")
        if projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")
        return {
            "data": _evidence(projection),
            "meta": _projection_reader_meta(
                snapshot_at,
                _read_state(projection.snapshot, projection.diagnostics, projection.timeline),
                reader,
                tenant_id=tenant_id,
                environment=environment,
            ),
        }

    @router.get("/bff/management/trade-journeys/{journey_id}/replay", response_model=TradeJourneyDetailEnvelope)
    def bff_trade_journeys_replay(
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
        reader = get_projection_reader()
        try:
            current_projection = (
                reader.get_journey(
                    tenant_id=tenant_id,
                    environment=environment,
                    journey_id=journey_id,
                )
                if _tenant_allowed(identity, tenant_id)
                else None
            )
        except ProjectionReadUnavailable:
            return _unavailable_envelope(snapshot_at, entity_id=f"{journey_id}-replay")
        if current_projection is None:
            return _err(404, "RESOURCE_NOT_FOUND", "Trade journey not found")

        historical_events = [
            event
            for event in current_projection.timeline
            if (parsed := _parse_iso(event.get("occurred_at"))) is not None and parsed <= as_of_dt
        ]
        if not historical_events:
            data = {
                "id": f"{journey_id}-replay",
                "journey_id": journey_id,
                "as_of": as_of,
                "exists_at_as_of": False,
            }
            meta = _projection_reader_meta(
                snapshot_at, "formal", reader, tenant_id=tenant_id, environment=environment
            )
            return {"data": data, "meta": meta}

        replay_materializer = JourneyMaterializer()
        try:
            replay_materializer.rebuild(historical_events)
        except MaterializationError:
            return _err(503, "DEPENDENCY_UNAVAILABLE", "Replay could not rebuild a consistent snapshot", retryable=True)

        projection = replay_materializer.get(journey_id, tenant_id=tenant_id, environment=environment)
        now = _parse_iso(snapshot_at) or datetime.now(timezone.utc)
        if projection is None:
            data = {
                "id": f"{journey_id}-replay",
                "journey_id": journey_id,
                "as_of": as_of,
                "exists_at_as_of": False,
            }
            meta = _projection_reader_meta(
                snapshot_at, "formal", reader, tenant_id=tenant_id, environment=environment
            )
            return {"data": data, "meta": meta}

        detail = _mask_live_sensitive(_detail(projection, now=now), identity, projection.environment)
        detail["as_of"] = as_of
        detail["exists_at_as_of"] = True
        meta = _projection_reader_meta(
            snapshot_at,
            detail.get("read_state", "formal"),
            reader,
            tenant_id=tenant_id,
            environment=environment,
        )
        return {"data": detail, "meta": meta}

    return router
