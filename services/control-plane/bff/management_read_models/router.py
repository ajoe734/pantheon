"""BFF: Management Read Models & Management System Router.

Consolidates all 17 Management domain HTTP routes into a dedicated router:
- Shell summary (/bff/management/shell-summary)
- Operator home (/api/v1/operator/home)
- Management cockpit aggregate (/bff/management/cockpit)
- Trading pulse card aggregate (/bff/management/trading-pulse)
- Trading pulse rankings (/bff/management/trading-pulse/rankings)
- Sentinel pulse (/bff/management/sentinel-pulse)
- Operator health status (/api/v1/operator/health-status)
- Loop throughput metrics (/bff/management/loop-throughput)
- Risk radar indicators (/bff/management/risk-radar)
- Incident timeline (/bff/management/incident-timeline)
- Human review inbox (/bff/management/human-inbox)
- Human review inbox detail (/bff/management/human-inbox/{item_id})
- HIQ backlog (/bff/management/hiq-backlog)
- Intervention stream (/bff/management/intervention-stream)
- Evidence explorer (/bff/management/evidence)
- Operations read model (/bff/management/operations-read-model/{persona_id})
- Degraded control guidance (/api/v1/operator/degraded-control-guidance)

Also preserves the 5 composed Management read models:
- Formula jobs read model (/bff/management/formula-jobs)
- Consolidated activity read model (/bff/management/activity)
- Paper execution telemetry read model (/bff/management/paper-telemetry)
- Postmortem incident analysis read model (/bff/management/postmortems)
- Postmortem detail read model (/bff/management/postmortems/{postmortem_id})
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
from fastapi import APIRouter, Cookie, Header, HTTPException, Query, Response
from starlette.responses import JSONResponse

from .models import (
    ActivityEnvelope,
    FormulaJobsEnvelope,
    PaperTelemetryEnvelope,
    PostmortemDetailEnvelope,
    PostmortemsEnvelope,
)
from .service import ManagementService

try:
    from models import ErrorCode
except ImportError:
    class ErrorCode:
        VALIDATION_FAILED = "VALIDATION_FAILED"
        AUTH_REQUIRED = "AUTH_REQUIRED"
        FORBIDDEN = "FORBIDDEN"
        RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
        RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
        DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
        INTERNAL_ERROR = "INTERNAL_ERROR"

log = logging.getLogger(__name__)


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_snapshot_meta(snapshot_at: Optional[str] = None) -> Dict[str, Any]:
    now = snapshot_at or _utc_now_rfc3339()
    return {
        "snapshot_at": now,
        "version": "v1",
    }


def _default_extract_identity(
    authorization: Optional[str] = None,
    mfa_token: Optional[str] = None,
    session_cookie: Optional[str] = None,
) -> Any:
    class DummyIdentity:
        operator_id = "op-user"
        roles = {"operator", "viewer", "admin"}
        session_kind = "bearer"
        mfa_verified = False
        display_name = "Operator"

    ident = DummyIdentity()
    if authorization:
        token = authorization.replace("Bearer ", "").strip()
        parts = token.split(":")
        ident.operator_id = parts[0]
        if len(parts) > 1:
            ident.roles = set(parts[1].split(","))
    return ident


def _default_require_read_role(identity: Any) -> None:
    pass


def _default_bff_error(
    status_code: int,
    code: str,
    message: str,
    reason: Optional[str] = None,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    detail: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "reason": reason or message,
            "status_code": status_code,
        }
    }
    if precondition_failed:
        detail["error"]["details"] = {"precondition_failed": precondition_failed}
    if suggestion:
        detail["error"]["suggestion"] = suggestion
    if details_extra:
        detail["error"].setdefault("details", {}).update(details_extra)
    return HTTPException(status_code=status_code, detail=detail)


# ---------------------------------------------------------------------------
# Composed Read Model Standalone Functions (Preserved for compatibility)
# ---------------------------------------------------------------------------

def get_formula_jobs_read_model(
    *,
    status: Optional[str] = None,
    formula_id: Optional[str] = None,
    jobs_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    formula_jobs_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    store: Optional[Any] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    now_fn = utc_now or _utc_now_rfc3339
    contributing_sources: List[str] = []
    raw_items: List[Dict[str, Any]] = []

    jobs_avail, jobs_records = False, []
    fj_avail, fj_records = False, []

    if jobs_reader is not None:
        try:
            jobs_avail, jobs_records = jobs_reader()
        except Exception:
            jobs_avail, jobs_records = False, []
    elif store is not None:
        try:
            if hasattr(store, "_service") and hasattr(store._service, "list_records"):
                jobs_avail, jobs_records = store._service.list_records("jobs", include_snapshot_fallback=False)
            elif hasattr(store, "list_records"):
                jobs_avail, jobs_records = store.list_records("jobs")
        except Exception:
            jobs_avail, jobs_records = False, []

    if formula_jobs_reader is not None:
        try:
            fj_avail, fj_records = formula_jobs_reader()
        except Exception:
            fj_avail, fj_records = False, []
    elif store is not None:
        try:
            if hasattr(store, "_service") and hasattr(store._service, "list_records"):
                fj_avail, fj_records = store._service.list_records("formula_jobs", include_snapshot_fallback=False)
            elif hasattr(store, "list_records"):
                fj_avail, fj_records = store.list_records("formula_jobs")
        except Exception:
            fj_avail, fj_records = False, []

    if jobs_avail:
        contributing_sources.append("service")
        raw_items.extend(jobs_records or [])
    if fj_avail:
        contributing_sources.append("service")
        raw_items.extend(fj_records or [])

    if not jobs_avail and not fj_avail:
        if store is not None and hasattr(store, "get_formula_jobs_read_model"):
            try:
                return store.get_formula_jobs_read_model(status=status, formula_id=formula_id)
            except Exception:
                pass
        return {
            "source": "unavailable",
            "items": [],
        }

    filtered: List[Dict[str, Any]] = []
    seen_job_ids = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        job_id = str(item.get("job_id") or item.get("run_id") or item.get("id") or "")
        if not job_id or job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)

        item_status = str(item.get("status") or "admitted")
        item_formula_id = str(
            item.get("formula_id")
            or item.get("target_id")
            or item.get("entity_id")
            or item.get("name")
            or "formula-default"
        )

        if status and item_status != status:
            continue
        if formula_id and item_formula_id != formula_id:
            continue

        item_copy = {
            "job_id": job_id,
            "formula_id": item_formula_id,
            "formula_version": str(item.get("formula_version") or item.get("version") or "1.0.0"),
            "owner_id": str(
                item.get("owner_id")
                or item.get("actor_id")
                or item.get("user_id")
                or item.get("owner")
                or "formula_job_executor"
            ),
            "status": item_status,
            "submitted_at": str(item.get("submitted_at") or item.get("created_at") or now_fn()),
            "started_at": item.get("started_at"),
            "finished_at": item.get("finished_at") or item.get("completed_at"),
            "metrics": (
                item.get("metrics")
                or (item.get("result", {}).get("metrics") if isinstance(item.get("result"), dict) else {})
                or {}
            ),
            "chart_lineage": item.get("chart_lineage") or item.get("lineage") or [],
            "source_identity": str(item.get("source_identity") or "formula_job_executor"),
            "freshness": str(item.get("freshness") or item.get("submitted_at") or item.get("created_at") or now_fn()),
        }
        filtered.append(item_copy)

    filtered.sort(key=lambda x: str(x.get("submitted_at") or ""), reverse=True)
    source = "service"
    return {
        "source": source,
        "items": filtered,
    }


def get_activity_read_model(
    *,
    event_type: Optional[str] = None,
    actor_id: Optional[str] = None,
    activity_audit_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    governance_audit_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    telemetry_events_reader: Optional[Callable[[], Tuple[str, List[Dict[str, Any]]]]] = None,
    store: Optional[Any] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    now_fn = utc_now or _utc_now_rfc3339
    raw_items: List[Dict[str, Any]] = []
    contributing_sources: List[str] = []
    surfaces: Dict[str, Any] = {}

    act_avail, act_records = False, []
    if activity_audit_reader is not None:
        try:
            act_avail, act_records = activity_audit_reader()
        except Exception:
            act_avail, act_records = False, []
    elif store is not None:
        try:
            if hasattr(store, "_service") and hasattr(store._service, "list_records"):
                act_avail, act_records = store._service.list_records("activity_audit", include_snapshot_fallback=False)
            elif hasattr(store, "list_records"):
                act_avail, act_records = store.list_records("activity_audit")
        except Exception:
            act_avail, act_records = False, []

    if act_avail and act_records:
        contributing_sources.append("audit")
        surfaces["activity_audit"] = {"status": "ok", "source": "audit"}
        raw_items.extend(act_records)
    elif act_avail:
        surfaces["activity_audit"] = {"status": "degraded", "source": "audit"}
    else:
        surfaces["activity_audit"] = {"status": "unavailable", "source": "missing"}

    gov_avail, gov_records = False, []
    if governance_audit_reader is not None:
        try:
            gov_avail, gov_records = governance_audit_reader()
        except Exception:
            gov_avail, gov_records = False, []
    elif store is not None:
        try:
            if hasattr(store, "_service") and hasattr(store._service, "list_records"):
                gov_avail, gov_records = store._service.list_records("governance_audit_events", include_snapshot_fallback=False)
            elif hasattr(store, "list_records"):
                gov_avail, gov_records = store.list_records("governance_audit_events")
        except Exception:
            gov_avail, gov_records = False, []

    if gov_avail and gov_records:
        contributing_sources.append("audit")
        surfaces["governance_audit"] = {"status": "ok", "source": "audit"}
        for gev in gov_records:
            if not isinstance(gev, dict):
                continue
            eid = str(gev.get("entry_id") or gev.get("event_id") or gev.get("auditId") or gev.get("audit_id") or gev.get("id") or "")
            raw_items.append({
                "event_id": eid,
                "entry_id": eid,
                "event_type": str(gev.get("action_type") or gev.get("event_type") or "governance.audit"),
                "aggregate_id": str(gev.get("target_id") or gev.get("aggregate_id") or ""),
                "actor_id": str(gev.get("actor") or gev.get("actor_id") or "system"),
                "timestamp": str(gev.get("timestamp") or now_fn()),
                "summary": str(gev.get("summary") or f"Governance action {gev.get('action_type', '')}"),
                "details": gev.get("details") or gev.get("payload") or gev.get("audit_context") or {},
                "source_identity": str(gev.get("source_identity") or "governance_audit_store"),
                "freshness": str(gev.get("timestamp") or now_fn()),
            })
    elif gov_avail:
        surfaces["governance_audit"] = {"status": "degraded", "source": "audit"}
    else:
        surfaces["governance_audit"] = {"status": "unavailable", "source": "missing"}

    tel_src, tel_events = "missing", []
    if telemetry_events_reader is not None:
        try:
            tel_src, tel_events = telemetry_events_reader()
        except Exception:
            tel_src, tel_events = "missing", []
    elif store is not None:
        try:
            if hasattr(store, "list_telemetry_events_with_source"):
                tel_src, tel_events = store.list_telemetry_events_with_source()
            elif hasattr(store, "list_telemetry_events"):
                tel_events = store.list_telemetry_events()
                tel_src = "telemetry" if tel_events else "store"
        except Exception:
            tel_src, tel_events = "missing", []

    if tel_src not in ("missing", "unavailable") and tel_events:
        contributing_sources.append(tel_src)
        surfaces["telemetry_events"] = {"status": "ok", "source": tel_src}
        for tev in tel_events:
            if not isinstance(tev, dict):
                continue
            tev_id = str(tev.get("id") or tev.get("event_id") or tev.get("telemetry_event_id") or "")
            tev_type = str(tev.get("type") or tev.get("event_type") or "telemetry")
            tev_actor = str(tev.get("actor_id") or tev.get("persona_id") or "telemetry_ingest")
            raw_items.append({
                "event_id": tev_id,
                "entry_id": tev_id,
                "event_type": tev_type,
                "aggregate_id": str(tev.get("runtime_id") or tev.get("aggregate_id") or ""),
                "actor_id": tev_actor,
                "timestamp": str(tev.get("timestamp") or now_fn()),
                "summary": str(tev.get("summary") or f"Telemetry event for runtime {tev.get('runtime_id', '')}"),
                "details": tev.get("details") or tev.get("metrics") or {},
                "source_identity": str(tev.get("source_identity") or "telemetry_event_store"),
                "freshness": str(tev.get("timestamp") or now_fn()),
            })
    elif tel_src not in ("missing", "unavailable"):
        surfaces["telemetry_events"] = {"status": "degraded", "source": tel_src}
    else:
        surfaces["telemetry_events"] = {"status": "unavailable", "source": "missing"}

    if not act_avail and not gov_avail and (tel_src in ("missing", "unavailable")):
        if store is not None and hasattr(store, "get_activity_read_model"):
            try:
                return store.get_activity_read_model(event_type=event_type, actor_id=actor_id)
            except Exception:
                pass
        return {
            "source": "unavailable",
            "items": [],
            "surfaces": surfaces,
        }

    filtered: List[Dict[str, Any]] = []
    seen_event_ids = set()
    has_audit_items = False
    has_telemetry_items = False

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        eid = str(item.get("entry_id") or item.get("event_id") or item.get("auditId") or item.get("audit_id") or item.get("id") or "")
        if not eid or eid in seen_event_ids:
            continue
        seen_event_ids.add(eid)

        itype = str(item.get("event_type") or item.get("action_type") or item.get("type") or "activity")
        iactor = str(item.get("actor_id") or item.get("actor") or "system")

        if event_type and itype != event_type:
            continue
        if actor_id and iactor != actor_id:
            continue

        src_ident = str(item.get("source_identity") or "activity_audit_store")
        if "telemetry" in src_ident:
            has_telemetry_items = True
        else:
            has_audit_items = True

        item_copy = {
            "event_id": eid,
            "entry_id": eid,
            "event_type": itype,
            "aggregate_id": str(item.get("aggregate_id") or item.get("target_id") or item.get("runtime_id") or ""),
            "actor_id": iactor,
            "timestamp": str(item.get("timestamp") or item.get("occurred_at") or now_fn()),
            "summary": str(item.get("summary") or item.get("description") or f"Activity {itype}"),
            "details": item.get("details") or item.get("payload") or item.get("audit_context") or {},
            "source_identity": src_ident,
            "freshness": str(item.get("freshness") or item.get("timestamp") or now_fn()),
        }
        filtered.append(item_copy)

    filtered.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)

    if has_audit_items:
        source = "audit"
    elif has_telemetry_items:
        source = "telemetry"
    elif contributing_sources:
        source = contributing_sources[0]
    else:
        source = "audit"

    return {
        "source": source,
        "items": filtered,
        "surfaces": surfaces,
    }


def get_paper_telemetry_read_model(
    *,
    strategy_id: Optional[str] = None,
    persona_id: Optional[str] = None,
    paper_telemetry_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    runtime_bindings_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    telemetry_events_reader: Optional[Callable[[], Tuple[str, List[Dict[str, Any]]]]] = None,
    store: Optional[Any] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    now_fn = utc_now or _utc_now_rfc3339
    raw_items: List[Dict[str, Any]] = []
    contributing_sources: List[str] = []

    pt_avail, pt_records = False, []
    if paper_telemetry_reader is not None:
        try:
            pt_avail, pt_records = paper_telemetry_reader()
        except Exception:
            pt_avail, pt_records = False, []
    elif store is not None:
        try:
            if hasattr(store, "_service") and hasattr(store._service, "list_records"):
                pt_avail, pt_records = store._service.list_records("paper_telemetry", include_snapshot_fallback=False)
            elif hasattr(store, "list_records"):
                pt_avail, pt_records = store.list_records("paper_telemetry")
        except Exception:
            pt_avail, pt_records = False, []

    if pt_avail and pt_records:
        contributing_sources.append("service")
        raw_items.extend(pt_records)

    bindings_avail = False
    bindings: List[Dict[str, Any]] = []
    if runtime_bindings_reader is not None:
        try:
            bindings_avail, bindings = runtime_bindings_reader()
        except Exception:
            bindings_avail, bindings = False, []
    elif store is not None:
        try:
            if hasattr(store, "_service") and hasattr(store._service, "list_records"):
                bindings_avail, bindings = store._service.list_records("runtime_bindings", include_snapshot_fallback=False)
            if (not bindings_avail or not bindings) and hasattr(store, "list_runtime_bindings"):
                bindings = store.list_runtime_bindings()
                if bindings:
                    bindings_avail = True
        except Exception:
            bindings_avail, bindings = False, []

    tel_events: List[Dict[str, Any]] = []
    if telemetry_events_reader is not None:
        try:
            _, tel_events = telemetry_events_reader()
        except Exception:
            tel_events = []
    elif store is not None and hasattr(store, "list_telemetry_events"):
        try:
            tel_events = store.list_telemetry_events()
        except Exception:
            tel_events = []

    if bindings_avail and bindings:
        contributing_sources.append("service")
        for b in bindings:
            if not isinstance(b, dict):
                continue
            b_strat = str(b.get("strategy_id") or b.get("id") or "")
            b_persona = b.get("persona_id")
            b_ledger = str(b.get("paper_ledger_id") or f"ledger-{b.get('binding_id') or b.get('id') or b_strat or 'default'}")

            matching_events = [
                e for e in tel_events
                if isinstance(e, dict) and str(e.get("runtime_id") or e.get("strategy_id") or "") in (
                    b_strat,
                    str(b.get("binding_id") or b.get("id")),
                    str(b.get("runtime_id") or ""),
                )
            ]
            series: List[Dict[str, Any]] = []
            for me in matching_events:
                ts = str(me.get("timestamp") or me.get("occurred_at") or now_fn())
                m = me.get("metrics") or me.get("details") or me
                if isinstance(m, dict) and any(k in m for k in ("equity", "drawdown_pct", "open_positions", "daily_pnl")):
                    series.append({
                        "timestamp": ts,
                        "equity": float(m.get("equity") or 0.0),
                        "drawdown_pct": float(m.get("drawdown_pct") or 0.0),
                        "open_positions": int(m.get("open_positions") or 0),
                        "daily_pnl": float(m.get("daily_pnl") or 0.0),
                    })
            last_sig = matching_events[-1].get("timestamp") if matching_events else b.get("last_signal_at")
            raw_items.append({
                "strategy_id": b_strat,
                "persona_id": b_persona,
                "paper_ledger_id": b_ledger,
                "status": str(b.get("status") or "active"),
                "last_signal_at": last_sig,
                "series": series,
                "metrics": b.get("metrics") or (matching_events[-1].get("metrics") if matching_events else {}),
                "source_identity": "paper_telemetry_store",
                "freshness": str(last_sig or b.get("created_at") or now_fn()),
            })

    if not pt_avail and not bindings_avail:
        if store is not None and hasattr(store, "get_paper_telemetry_read_model"):
            try:
                return store.get_paper_telemetry_read_model(strategy_id=strategy_id, persona_id=persona_id)
            except Exception:
                pass
        return {
            "source": "unavailable",
            "items": [],
        }

    filtered: List[Dict[str, Any]] = []
    seen_strat_ids = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        strat = str(item.get("strategy_id") or item.get("id") or "")
        if not strat or strat in seen_strat_ids:
            continue
        seen_strat_ids.add(strat)

        p_id = item.get("persona_id")
        if strategy_id and strat != strategy_id:
            continue
        if persona_id and p_id != persona_id:
            continue

        raw_series = item.get("series") or []
        norm_series = []
        for pt in raw_series:
            if isinstance(pt, dict):
                norm_series.append({
                    "timestamp": str(pt.get("timestamp") or now_fn()),
                    "equity": float(pt.get("equity") or 0.0),
                    "drawdown_pct": float(pt.get("drawdown_pct") or 0.0),
                    "open_positions": int(pt.get("open_positions") or 0),
                    "daily_pnl": float(pt.get("daily_pnl") or 0.0),
                })

        item_copy = {
            "strategy_id": strat,
            "persona_id": p_id,
            "paper_ledger_id": str(item.get("paper_ledger_id") or f"ledger-{strat}"),
            "status": str(item.get("status") or "active"),
            "last_signal_at": item.get("last_signal_at"),
            "series": norm_series,
            "metrics": item.get("metrics") or {},
            "source_identity": str(item.get("source_identity") or "paper_telemetry_store"),
            "freshness": str(item.get("freshness") or item.get("last_signal_at") or now_fn()),
        }
        filtered.append(item_copy)

    source = "service"
    return {
        "source": source,
        "items": filtered,
    }


def get_postmortems_read_model(
    *,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    postmortems_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    store: Optional[Any] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    now_fn = utc_now or _utc_now_rfc3339
    available, service_records = False, []

    if postmortems_reader is not None:
        try:
            available, service_records = postmortems_reader()
        except Exception:
            available, service_records = False, []
    elif store is not None:
        try:
            if hasattr(store, "_service") and hasattr(store._service, "list_records"):
                available, service_records = store._service.list_records("postmortems", include_snapshot_fallback=False)
            elif hasattr(store, "list_records"):
                available, service_records = store.list_records("postmortems")
        except Exception:
            available, service_records = False, []

    if not available:
        if store is not None and hasattr(store, "get_postmortems_read_model"):
            try:
                return store.get_postmortems_read_model(severity=severity, status=status)
            except Exception:
                pass
        return {
            "source": "unavailable",
            "items": [],
        }

    source = "store"
    raw_items = service_records or []
    filtered: List[Dict[str, Any]] = []
    seen_pm_ids = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        item_copy = json.loads(json.dumps(item))
        pm_id = str(item_copy.get("postmortem_id") or item_copy.get("id") or item_copy.get("report_id") or "")
        if not pm_id or pm_id in seen_pm_ids:
            continue
        seen_pm_ids.add(pm_id)
        item_copy["postmortem_id"] = pm_id
        item_copy["incident_id"] = str(item_copy.get("incident_id") or "")
        item_copy["title"] = str(item_copy.get("title") or "Postmortem Analysis")
        item_copy["status"] = str(item_copy.get("status") or "resolved")
        item_copy["created_at"] = str(item_copy.get("created_at") or now_fn())

        if "impact_summary" not in item_copy and "incident_evidence_summary" in item_copy:
            item_copy["impact_summary"] = item_copy.get("incident_evidence_summary")
        if "severity" not in item_copy or not item_copy.get("severity"):
            item_copy["severity"] = "medium"
        if "action_items" in item_copy and isinstance(item_copy["action_items"], list):
            norm_actions = []
            for idx, act in enumerate(item_copy["action_items"]):
                if isinstance(act, str):
                    norm_actions.append({"id": f"act-{idx+1}", "desc": act})
                elif isinstance(act, dict):
                    norm_actions.append(act)
            item_copy["action_items"] = norm_actions

        if severity and item_copy.get("severity") != severity:
            continue
        if status and item_copy.get("status") != status:
            continue

        if "source_identity" not in item_copy:
            item_copy["source_identity"] = "postmortem_store"
        if "freshness" not in item_copy:
            item_copy["freshness"] = item_copy.get("created_at") or now_fn()
        filtered.append(item_copy)

    return {
        "source": source,
        "items": filtered,
    }


def get_postmortem_detail_read_model(
    *,
    postmortem_id: str,
    postmortems_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    store: Optional[Any] = None,
    utc_now: Optional[Callable[[], str]] = None,
) -> Dict[str, Any]:
    res = get_postmortems_read_model(
        postmortems_reader=postmortems_reader,
        store=store,
        utc_now=utc_now,
    )
    if res.get("source") == "unavailable":
        return {
            "source": "unavailable",
            "item": None,
        }
    items = res.get("items") or []
    for item in items:
        if item.get("postmortem_id") == postmortem_id:
            return {
                "source": res.get("source") or "store",
                "item": item,
            }
    return {
        "source": res.get("source") or "missing",
        "item": None,
    }


# ---------------------------------------------------------------------------
# Router Factory
# ---------------------------------------------------------------------------

def create_management_read_models_router(
    *,
    get_read_store: Optional[Callable] = None,
    extract_identity: Optional[Callable] = None,
    require_read_role: Optional[Callable] = None,
    snapshot_meta: Optional[Callable] = None,
    utc_now: Optional[Callable] = None,
    bff_error: Optional[Callable] = None,
    raise_if_session_logged_out: Optional[Callable] = None,
    service: Optional[ManagementService] = None,
    jobs_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    formula_jobs_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    activity_audit_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    governance_audit_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    telemetry_events_reader: Optional[Callable[[], Tuple[str, List[Dict[str, Any]]]]] = None,
    paper_telemetry_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    runtime_bindings_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
    postmortems_reader: Optional[Callable[[], Tuple[bool, List[Dict[str, Any]]]]] = None,
) -> APIRouter:
    """Create the APIRouter for Management read models and system operations."""
    router = APIRouter()

    _extract_id = extract_identity or _default_extract_identity
    _req_read = require_read_role or _default_require_read_role
    _snap_meta = snapshot_meta or _default_snapshot_meta
    _now = utc_now or _utc_now_rfc3339
    _err = bff_error or _default_bff_error

    svc = service or ManagementService(get_read_store=get_read_store, utc_now=_now)

    def _resolve_store() -> Optional[Any]:
        if get_read_store is not None:
            try:
                return get_read_store()
            except Exception:
                return None
        return None

    # -----------------------------------------------------------------------
    # 1. Shell Summary
    # -----------------------------------------------------------------------
    @router.get("/bff/management/shell-summary")
    def bff_management_shell_summary(
        authorization: Optional[str] = Header(default=None),
        pantheon_session: Optional[str] = Cookie(default=None),
        x_mfa_token: Optional[str] = Header(default=None, alias="X-MFA-Token"),
    ) -> Dict[str, Any]:
        """Cheap management shell summary for first-mount badges and session chrome."""
        identity = _extract_id(
            authorization,
            mfa_token=x_mfa_token,
            session_cookie=pantheon_session,
        )
        _req_read(identity)
        if raise_if_session_logged_out:
            raise_if_session_logged_out(identity)
        return svc.get_shell_summary(identity)

    # -----------------------------------------------------------------------
    # 2. Operator Home
    # -----------------------------------------------------------------------
    @router.get("/api/v1/operator/home")
    async def get_operator_home(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Overview cards and dispatch shortcuts for operator home dashboard."""
        identity = _extract_id(authorization)
        _req_read(identity)
        snap = _now()
        return svc.get_operator_home(snapshot_at=snap)

    # -----------------------------------------------------------------------
    # 3. Management Cockpit Aggregate
    # -----------------------------------------------------------------------
    @router.get("/bff/management/cockpit")
    async def bff_management_cockpit(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF-B3-001: Pantheon Management cockpit aggregate."""
        identity = _extract_id(authorization)
        _req_read(identity)
        snap = _now()
        return svc.get_management_cockpit(snapshot_at=snap)

    # -----------------------------------------------------------------------
    # 4. Trading Pulse
    # -----------------------------------------------------------------------
    @router.get("/bff/management/trading-pulse")
    async def bff_management_trading_pulse(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF-B3-004: Management Trading Pulse card aggregate."""
        identity = _extract_id(authorization)
        _req_read(identity)
        snap = _now()
        return svc.get_trading_pulse(snapshot_at=snap)

    # -----------------------------------------------------------------------
    # 5. Trading Pulse Rankings
    # -----------------------------------------------------------------------
    @router.get("/bff/management/trading-pulse/rankings")
    async def bff_management_trading_pulse_rankings(
        limit: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF-B3-004: Management Trading Pulse ranking blocks."""
        identity = _extract_id(authorization)
        _req_read(identity)
        snap = _now()
        return svc.get_trading_pulse_rankings(limit=limit, snapshot_at=snap)

    # -----------------------------------------------------------------------
    # 6. Sentinel Pulse
    # -----------------------------------------------------------------------
    @router.get("/bff/management/sentinel-pulse")
    async def bff_management_sentinel_pulse(
        kind: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        severity: Optional[str] = Query(default=None),
        q: str = Query(default=""),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Management Sentinel Pulse composed from v5 sentinel read surfaces."""
        identity = _extract_id(authorization)
        _req_read(identity)
        return svc.get_sentinel_pulse(
            kind=kind,
            status=status,
            severity=severity,
            q=q,
            page_token=page_token,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # 7. Operator Health Status
    # -----------------------------------------------------------------------
    @router.get("/api/v1/operator/health-status")
    async def get_operator_health_status(
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Grouped control plane health status and secondary control path."""
        identity = _extract_id(authorization)
        _req_read(identity)
        snap = _now()
        return svc.get_operator_health_status(snapshot_at=snap)

    # -----------------------------------------------------------------------
    # 8. Loop Throughput
    # -----------------------------------------------------------------------
    @router.get("/bff/management/loop-throughput")
    async def bff_management_loop_throughput(
        loop_type: Optional[str] = Query(default=None),
        window_minutes: int = Query(default=60, ge=5, le=1440),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: read-only loop execution throughput & metrics."""
        identity = _extract_id(authorization)
        _req_read(identity)
        return svc.get_loop_throughput(
            loop_type=loop_type,
            window_minutes=window_minutes,
            page_token=page_token,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # 9. Risk Radar
    # -----------------------------------------------------------------------
    @router.get("/bff/management/risk-radar")
    async def bff_management_risk_radar(
        persona_id: Optional[str] = Query(default=None),
        strategy_id: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None),
        risk_state: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: read-only cross-persona and strategy risk indicators."""
        identity = _extract_id(authorization)
        _req_read(identity)
        return svc.get_risk_radar(
            persona_id=persona_id,
            strategy_id=strategy_id,
            capital_pool_id=capital_pool_id,
            risk_state=risk_state,
            page_token=page_token,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # 10. Incident Timeline
    # -----------------------------------------------------------------------
    @router.get("/bff/management/incident-timeline")
    async def bff_management_incident_timeline(
        status: Optional[str] = Query(default=None),
        severity: Optional[str] = Query(default=None),
        capital_pool_id: Optional[str] = Query(default=None),
        affected_pool_id: Optional[str] = Query(default=None),
        runtime_id: Optional[str] = Query(default=None),
        sort_order: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: read-only Management Console incident chronology."""
        identity = _extract_id(authorization)
        _req_read(identity)
        return svc.get_incident_timeline(
            status=status,
            severity=severity,
            capital_pool_id=capital_pool_id,
            affected_pool_id=affected_pool_id,
            runtime_id=runtime_id,
            sort_order=sort_order,
            page_token=page_token,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # 11. Human Review Inbox
    # -----------------------------------------------------------------------
    @router.get("/bff/management/human-inbox")
    async def bff_management_human_inbox(
        source_type: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        priority: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: compose human-action inbox rows from governed human-review sources."""
        identity = _extract_id(authorization)
        _req_read(identity)
        return svc.get_human_inbox(
            source_type=source_type,
            status=status,
            priority=priority,
            page_token=page_token,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # 12. Human Review Inbox Detail
    # -----------------------------------------------------------------------
    @router.get("/bff/management/human-inbox/{item_id}")
    async def bff_management_human_inbox_detail(
        item_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: detail for one composed human-action inbox row."""
        identity = _extract_id(authorization)
        _req_read(identity)
        detail = svc.get_human_inbox_detail(item_id)
        if detail is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Human inbox item not found",
                f"Human inbox item '{item_id}' does not exist",
            )
        return detail

    # -----------------------------------------------------------------------
    # 13. HIQ Backlog
    # -----------------------------------------------------------------------
    @router.get("/bff/management/hiq-backlog")
    async def bff_management_hiq_backlog(
        source_type: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        kind: Optional[str] = Query(default=None),
        priority: Optional[str] = Query(default=None),
        q: str = Query(default=""),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: read-only HIQ backlog aggregate for sentinel and intervention review."""
        identity = _extract_id(authorization)
        _req_read(identity)
        return svc.get_hiq_backlog(
            source_type=source_type,
            status=status,
            kind=kind,
            priority=priority,
            q=q,
            page_token=page_token,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # 14. Intervention Stream
    # -----------------------------------------------------------------------
    @router.get("/bff/management/intervention-stream")
    async def bff_management_intervention_stream(
        persona_id: Optional[str] = Query(default=None),
        personaId: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        kind: Optional[str] = Query(default=None),
        q: str = Query(default=""),
        window_hours: int = Query(default=24, ge=1, le=720),
        windowHours: Optional[int] = Query(default=None, ge=1, le=720),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: read-only intervention event stream for Management Console review."""
        identity = _extract_id(authorization)
        _req_read(identity)
        return svc.get_intervention_stream(
            persona_id=persona_id or personaId,
            status=status,
            kind=kind,
            q=q,
            window_hours=windowHours or window_hours,
            page_token=page_token,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # 15. Evidence Explorer
    # -----------------------------------------------------------------------
    @router.get("/bff/management/evidence")
    async def bff_management_evidence(
        ref_id: Optional[str] = Query(default=None),
        linked_entity_type: Optional[str] = Query(default=None),
        linked_entity_ref: Optional[str] = Query(default=None),
        link_type: Optional[str] = Query(default=None),
        credibility_tier: Optional[str] = Query(default=None),
        verified: Optional[bool] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: adapt knowledge evidence refs into the Management Evidence Explorer."""
        identity = _extract_id(authorization)
        _req_read(identity)
        return svc.get_evidence(
            ref_id=ref_id,
            linked_entity_type=linked_entity_type,
            linked_entity_ref=linked_entity_ref,
            link_type=link_type,
            credibility_tier=credibility_tier,
            verified=verified,
            page_token=page_token,
            page_size=page_size,
        )

    # -----------------------------------------------------------------------
    # 16. Operations Read Model
    # -----------------------------------------------------------------------
    @router.get("/bff/management/operations-read-model/{persona_id}")
    async def bff_management_operations_read_model(
        persona_id: str,
        period: str = Query(default="latest"),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """MGMT-OPS-001: shared identity/source-confidence read model for one persona."""
        identity = _extract_id(authorization)
        _req_read(identity)
        res = svc.get_operations_read_model(persona_id=persona_id, period=period)
        if res is None:
            raise _err(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Persona not found",
                f"Persona '{persona_id}' does not exist",
            )
        return res

    # -----------------------------------------------------------------------
    # 17. Degraded Control Guidance
    # -----------------------------------------------------------------------
    @router.get("/api/v1/operator/degraded-control-guidance")
    async def degraded_control_guidance() -> Response:
        """Return guidance for operators when the BFF is degraded or unavailable."""
        res = svc.get_degraded_control_guidance()
        return JSONResponse(
            status_code=res["status_code"],
            content=res["payload"],
        )

    # -----------------------------------------------------------------------
    # 18. Formula Jobs Read Model (Composed)
    # -----------------------------------------------------------------------
    @router.get(
        "/bff/management/formula-jobs",
        response_model=FormulaJobsEnvelope,
    )
    async def bff_management_formula_jobs(
        status: Optional[str] = Query(default=None),
        formula_id: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Real Formula execution/evaluation jobs read model."""
        identity = _extract_id(authorization)
        _req_read(identity)

        snapshot_at = _now()
        store = _resolve_store()
        raw_res = get_formula_jobs_read_model(
            status=status,
            formula_id=formula_id,
            jobs_reader=jobs_reader,
            formula_jobs_reader=formula_jobs_reader,
            store=store,
            utc_now=_now,
        )

        source: str = str(raw_res.get("source") or "missing")
        items: List[Dict[str, Any]] = list(raw_res.get("items") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Formula job executor read store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Formula job read store is readable but currently empty."

        meta: Dict[str, Any] = {
            **_snap_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "formula_jobs": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Formula jobs read model is currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start: start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        summary = {
            "total_items": len(items),
            "returned_items": len(page_items),
            "status": surface_state,
            "source": source,
            "freshness": snapshot_at,
        }

        return {
            "data": {
                "id": "management-formula-jobs",
                "items": page_items,
                "summary": summary,
                "status": surface_state,
                "source": source,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
            },
            "meta": meta,
        }

    # -----------------------------------------------------------------------
    # 19. Consolidated Activity Read Model (Composed)
    # -----------------------------------------------------------------------
    @router.get(
        "/bff/management/activity",
        response_model=ActivityEnvelope,
    )
    async def bff_management_activity(
        event_type: Optional[str] = Query(default=None),
        actor_id: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Consolidated Management system activity read model."""
        identity = _extract_id(authorization)
        _req_read(identity)

        snapshot_at = _now()
        store = _resolve_store()
        raw_res = get_activity_read_model(
            event_type=event_type,
            actor_id=actor_id,
            activity_audit_reader=activity_audit_reader,
            governance_audit_reader=governance_audit_reader,
            telemetry_events_reader=telemetry_events_reader,
            store=store,
            utc_now=_now,
        )

        source: str = str(raw_res.get("source") or "missing")
        items: List[Dict[str, Any]] = list(raw_res.get("items") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Activity audit event store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Activity read store is readable but currently empty."

        meta: Dict[str, Any] = {
            **_snap_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "activity": surface,
                **(raw_res.get("surfaces") or {}),
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Activity read model is currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start: start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        summary = {
            "total_items": len(items),
            "returned_items": len(page_items),
            "status": surface_state,
            "source": source,
            "freshness": snapshot_at,
        }

        return {
            "data": {
                "id": "management-activity",
                "items": page_items,
                "summary": summary,
                "status": surface_state,
                "source": source,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
            },
            "meta": meta,
        }

    # -----------------------------------------------------------------------
    # 20. Paper Telemetry Read Model (Composed)
    # -----------------------------------------------------------------------
    @router.get(
        "/bff/management/paper-telemetry",
        response_model=PaperTelemetryEnvelope,
    )
    async def bff_management_paper_telemetry(
        strategy_id: Optional[str] = Query(default=None),
        persona_id: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Real strategy paper execution telemetry & series read model."""
        identity = _extract_id(authorization)
        _req_read(identity)

        snapshot_at = _now()
        store = _resolve_store()
        raw_res = get_paper_telemetry_read_model(
            strategy_id=strategy_id,
            persona_id=persona_id,
            paper_telemetry_reader=paper_telemetry_reader,
            runtime_bindings_reader=runtime_bindings_reader,
            telemetry_events_reader=telemetry_events_reader,
            store=store,
            utc_now=_now,
        )

        source: str = str(raw_res.get("source") or "missing")
        items: List[Dict[str, Any]] = list(raw_res.get("items") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Paper execution telemetry store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Paper telemetry store is readable but currently empty."

        meta: Dict[str, Any] = {
            **_snap_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "paper_telemetry": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Paper telemetry read model is currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start: start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        summary = {
            "total_items": len(items),
            "returned_items": len(page_items),
            "status": surface_state,
            "source": source,
            "freshness": snapshot_at,
        }

        return {
            "data": {
                "id": "management-paper-telemetry",
                "items": page_items,
                "summary": summary,
                "status": surface_state,
                "source": source,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
            },
            "meta": meta,
        }

    # -----------------------------------------------------------------------
    # 21. Postmortems Read Model (Composed)
    # -----------------------------------------------------------------------
    @router.get(
        "/bff/management/postmortems",
        response_model=PostmortemsEnvelope,
    )
    async def bff_management_postmortems(
        severity: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        page_token: Optional[str] = Query(default=None),
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Postmortem incident analysis list read model."""
        identity = _extract_id(authorization)
        _req_read(identity)

        snapshot_at = _now()
        store = _resolve_store()
        raw_res = get_postmortems_read_model(
            severity=severity,
            status=status,
            postmortems_reader=postmortems_reader,
            store=store,
            utc_now=_now,
        )

        source: str = str(raw_res.get("source") or "missing")
        items: List[Dict[str, Any]] = list(raw_res.get("items") or [])

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        else:
            surface_state = "ok" if items else "degraded"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Postmortem analysis store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }
        elif surface_state == "degraded":
            surface["message"] = "Postmortem analysis store is readable but currently empty."

        meta: Dict[str, Any] = {
            **_snap_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "postmortems": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Postmortems read model is currently unavailable.",
            }

        start = 0
        if page_token:
            try:
                start = int(page_token)
            except (TypeError, ValueError):
                start = 0
        page_items = items[start: start + page_size]
        next_page_token = str(start + page_size) if start + page_size < len(items) else None

        summary = {
            "total_items": len(items),
            "returned_items": len(page_items),
            "status": surface_state,
            "source": source,
            "freshness": snapshot_at,
        }

        return {
            "data": {
                "id": "management-postmortems",
                "items": page_items,
                "summary": summary,
                "status": surface_state,
                "source": source,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(items),
            },
            "meta": meta,
        }

    # -----------------------------------------------------------------------
    # 22. Postmortem Detail Read Model (Composed)
    # -----------------------------------------------------------------------
    @router.get(
        "/bff/management/postmortems/{postmortem_id}",
        response_model=PostmortemDetailEnvelope,
    )
    async def bff_management_postmortem_detail(
        postmortem_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: Postmortem detail read model."""
        identity = _extract_id(authorization)
        _req_read(identity)

        snapshot_at = _now()
        store = _resolve_store()
        raw_res = get_postmortem_detail_read_model(
            postmortem_id=postmortem_id,
            postmortems_reader=postmortems_reader,
            store=store,
            utc_now=_now,
        )

        source: str = str(raw_res.get("source") or "missing")
        item: Optional[Dict[str, Any]] = raw_res.get("item")

        if source in ("missing", "unavailable"):
            surface_state = "unavailable"
        elif not item:
            raise HTTPException(status_code=404, detail=f"Postmortem '{postmortem_id}' not found")
        else:
            surface_state = "ok"

        surface = {
            "status": surface_state,
            "source": source,
        }
        if surface_state == "unavailable":
            surface["message"] = "Postmortem analysis store is unavailable or unconfigured."
            surface["staleness"] = {
                "served_from": "unverifiable" if source == "missing" else source,
                "last_known_at": snapshot_at,
            }

        meta: Dict[str, Any] = {
            **_snap_meta(snapshot_at),
            "status": surface_state,
            "source": source,
            "surfaces": {
                "postmortem_detail": surface,
            },
        }
        if surface_state == "unavailable":
            meta["degradation"] = {
                "reason": "Postmortem detail read model is currently unavailable.",
            }

        return {
            "data": item,
            "meta": meta,
        }

    return router


create_management_router = create_management_read_models_router

