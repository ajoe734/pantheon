"""BFF-AF-001: GET /bff/alpha-factory — replication admission & queue board."""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import APIRouter, Header, Query

try:
    from ..auth import policy as auth_policy
    from ..command_adapters.base import (
        ActionUnavailableError,
        bound_tenant,
        http_request_json_with_headers,
    )
    from ..core.owner_reads import authorization as owner_authorization, selected_tenant
except (ImportError, ValueError):
    try:
        from services.control_plane.bff.auth import policy as auth_policy
        from services.control_plane.bff.command_adapters.base import (
            ActionUnavailableError,
            bound_tenant,
            http_request_json_with_headers,
        )
        from services.control_plane.bff.core.owner_reads import authorization as owner_authorization, selected_tenant
    except ImportError:
        from auth import policy as auth_policy  # type: ignore
        from command_adapters.base import (  # type: ignore
            ActionUnavailableError,
            bound_tenant,
            http_request_json_with_headers,
        )
        from core.owner_reads import authorization as owner_authorization, selected_tenant  # type: ignore

try:
    from ..research import discovery_projection
except (ImportError, ValueError):
    from services.control_plane.bff.research import discovery_projection  # type: ignore

_SURFACE_KEY = "alpha_factory"
_STRATEGY_STATE_SURFACE_KEY = "alpha_factory_strategy_state"
_OWNER_SOURCE = "research_orchestrator"
_ORCHESTRATOR_URL_ENV = "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL"
_ORCHESTRATOR_ENTRIES_PATH = "/api/research-orchestrator/alpha-replication/entries"
_ORCHESTRATOR_TEMPLATES_PATH = "/api/research/alpha-templates/evaluations"
_TEMPLATES_SURFACE_KEY = "alpha_factory_templates"
_DEMANDS_PATH = "/api/research/persona-demands"
# Tenant-scoped, newest-first owner page of the run list (DISCOVERY-OWNER-SUMMARY-READ-20261125);
# the same route without ``limit`` is the unscoped whole list and is never requested.
_RUNS_PATH = "/api/research-orchestrator/runs"
_SUMMARY_PATH = "/api/source-ingest/management/discovery-summary"
_SUMMARY_SOURCE = "source_ingestion"
# The unbounded owner routes below must never be requested by the board:
# source-ingest source-records and management/strategy-seeds return every row
# of the tenant (hosted: 36,619 records / 148 MB, 13,514 seeds / 34 MB).
FORBIDDEN_UNBOUNDED_PATHS = (
    "/api/source-ingest/source-records",
    "/api/source-ingest/management/strategy-seeds",
)
# Owner run page size: the newest runs of the tenant (owner cap 200).
_MAX_RUNS_USED = 200
_SOURCE_INGEST_URL_ENVS = (
    "PANTHEON_SOURCE_INGEST_API_URL",
    "PANTHEON_SOURCE_INGEST_URL",
    "SOURCE_INGEST_SERVICE_URL",
    "SOURCE_INGEST_URL",
)
_DEMANDS_SURFACE_KEY = "alpha_factory_demands"
_RUNS_SURFACE_KEY = "alpha_factory_runs"
_CARDS_SURFACE_KEY = "alpha_factory_hypothesis_cards"
_RECORDS_SURFACE_KEY = "alpha_factory_source_records"
_PERSONA_SURFACE_KEY = "alpha_factory_persona_names"
_LLM_SURFACE_KEY = "alpha_factory_llm_calls"
_TEMPLATE_SEARCH_ACTION = "template_search"

_KNOWN_REASON_CODES = (
    "mandate_universe_missing",
    "survivorship_limited",
    "below_threshold",
    "dataset_unavailable",
)

_LANES: List[Dict[str, Any]] = [
    {"id": "discovered", "label": "Discovered"},
    {"id": "scaffolded", "label": "Scaffolded"},
    {"id": "replicated", "label": "Replicated"},
]


def _resolve_source_ingest_url() -> Optional[str]:
    for name in _SOURCE_INGEST_URL_ENVS:
        raw = (os.getenv(name) or "").strip().rstrip("/")
        if raw:
            return raw
    return None


def _resolve_orchestrator_url() -> Optional[str]:
    raw = (os.getenv(_ORCHESTRATOR_URL_ENV) or "").strip().rstrip("/")
    return raw or None


def _default_http_get(
    url: str,
    *,
    timeout: float = 3.0,
    auth_header: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> Tuple[int, Any]:
    auth = (auth_header if auth_header is not None else owner_authorization.get()) or ""
    auth = auth.strip()
    caller_token: Optional[str] = None
    if auth.lower().startswith("bearer "):
        caller_token = auth.split(" ", 1)[1].strip() or None
    elif auth:
        caller_token = auth
    if not caller_token:
        return 401, None
    eff_tenant = tenant_id if tenant_id is not None else selected_tenant.get()
    try:
        trusted_tenant = bound_tenant({}, tenant_id=eff_tenant, auth_token=caller_token)
        res = http_request_json_with_headers(
            url,
            method="GET",
            auth_token=caller_token,
            tenant_id=trusted_tenant,
            timeout=int(max(timeout, 1.0)),
        )
        if isinstance(res, tuple) and len(res) == 3:
            status_code, _headers, body = res
        else:
            status_code, body = res  # type: ignore[misc]
        return int(status_code), body
    except ActionUnavailableError:
        raise
    except Exception:
        return 503, None


def _resolve_verified_caller_tenant(
    identity: Any,
    requested_tenant: Optional[str],
) -> Optional[str]:
    clean_req = str(requested_tenant or "").strip() or None
    claim_tenants = auth_policy.identity_claim_strings(
        identity, auth_policy.TENANT_ALLOWED_CLAIM_PATHS
    )
    token_kind = getattr(identity, "token_kind", "stub")
    if claim_tenants or token_kind in ("jwt", "structured", "cookie"):
        tenant_payload = auth_policy.bff_me_tenant_payload(
            identity,
            requested_tenant=clean_req,
        )
        resolved = str(tenant_payload.get("id") or "").strip()
        return resolved or None
    return clean_req or auth_policy.first_nonblank(
        os.getenv("PANTHEON_BFF_TENANT_ID"),
        os.getenv("PANTHEON_BFF_DEFAULT_TENANT_ID"),
        os.getenv("PANTHEON_TENANT_ID"),
        "tenant-dev",
    )


def _extract_entries(payload: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("items", "entries", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
    return None


def _project_alpha_factory_card(entry: Dict[str, Any]) -> Dict[str, Any]:
    admission_status = (
        entry.get("admission_status")
        if entry.get("admission_status") is not None
        else entry.get("status")
    )
    queue_status = entry.get("queue_status")
    lane = (
        "replicated"
        if str(queue_status or "").strip().lower() == "completed"
        else "scaffolded"
    )
    raw_exp_runs = entry.get("experiment_run_ids")
    exp_run_ids = list(raw_exp_runs) if isinstance(raw_exp_runs, list) else None
    raw_auth_runs = entry.get("authority_run_ids")
    auth_run_ids = list(raw_auth_runs) if isinstance(raw_auth_runs, list) else None
    card_id = (
        entry.get("admission_id")
        or entry.get("id")
        or entry.get("strategy_spec_id")
    )
    return {
        "id": card_id,
        "admission_id": entry.get("admission_id"),
        "tenant_id": entry.get("tenant_id"),
        "lane": lane,
        "strategy_id": entry.get("strategy_id"),
        "strategy_spec_id": entry.get("strategy_spec_id"),
        "spec_version": entry.get("spec_version"),
        "checksum": entry.get("checksum"),
        "approval_decision_id": entry.get("approval_decision_id"),
        "approver": entry.get("approver"),
        "approved_at": entry.get("approved_at"),
        "requested_by": entry.get("requested_by"),
        "review_ref": entry.get("review_ref"),
        "mode": entry.get("mode"),
        "created_at": entry.get("created_at"),
        "status": admission_status,
        "admission_status": admission_status,
        "queue_status": queue_status,
        "enqueued_at": entry.get("enqueued_at"),
        "enqueued_by": entry.get("enqueued_by"),
        "attempt_count": entry.get("attempt_count"),
        "max_attempts": entry.get("max_attempts"),
        "experiment_task_id": entry.get("experiment_task_id"),
        "experiment_run_ids": exp_run_ids,
        "authority_task_id": entry.get("authority_task_id"),
        "authority_run_ids": auth_run_ids,
        "last_revalidation_at": entry.get("last_revalidation_at"),
        "last_error": entry.get("last_error"),
        "replay_count": entry.get("replay_count"),
        "last_replayed_at": entry.get("last_replayed_at"),
        "revalidation_count": entry.get("revalidation_count"),
        # Filled from the registry strategy reader after projection.
        "strategy_state": None,
        # Sample metrics are passed through only when the owner supplies them.
        "metrics": discovery_projection.project_sample_metrics(
            entry.get("metrics"),
            entry.get("backtest_summary"),
            entry.get("backtest_evaluation"),
        ),
        "lineage": {
            "strategy_spec_id": entry.get("strategy_spec_id"),
            "strategy_id": entry.get("strategy_id"),
            "approval_decision_id": entry.get("approval_decision_id"),
            "review_ref": entry.get("review_ref"),
            "experiment_task_id": entry.get("experiment_task_id"),
            "experiment_run_ids": exp_run_ids,
            "authority_run_ids": auth_run_ids,
            "source_records": discovery_projection.project_lineage(
                entry.get("lineage")
            )["source_records"],
        },
        "attribution": discovery_projection.attribution_block(
            entry.get("lineage"), entry.get("metrics"), entry.get("datasets")
        ),
    }


def _attach_strategy_state(
    cards: List[Dict[str, Any]],
    read_strategy_state: Optional[Callable[[str], Tuple[Optional[str], str]]],
) -> Dict[str, Any]:
    """Set ``strategy_state`` on each card from the registry strategy reader.

    One read per distinct strategy_id. A failed read leaves ``strategy_state``
    null on the affected cards and marks the surface unavailable; no card is
    hidden or moved because of the state.
    """
    source = "bff_strategy_registry"
    if read_strategy_state is None:
        return {"status": "unavailable", "source": source, "reason": "reader_unconfigured"}
    states: Dict[str, Tuple[Optional[str], str]] = {}
    failed = False
    for card in cards:
        strategy_id = str(card.get("strategy_id") or "").strip()
        if not strategy_id:
            continue
        if strategy_id not in states:
            try:
                states[strategy_id] = read_strategy_state(strategy_id)
            except Exception:  # noqa: BLE001 - degrade, never drop cards
                states[strategy_id] = (None, "unavailable")
        state, status = states[strategy_id]
        if status != "ok":
            failed = True
            card["strategy_state"] = None
        else:
            card["strategy_state"] = state
    if failed:
        return {"status": "unavailable", "source": source, "reason": "strategy_state_read_failed"}
    return {"status": "ok", "source": source}


def _owner_get(
    http_get: Callable[..., Tuple[Any, Any]],
    url: str,
    auth: Optional[str],
    tenant: Optional[str],
) -> Tuple[bool, Any]:
    try:
        try:
            status, payload = http_get(url, auth_header=auth, tenant_id=tenant)
        except TypeError:
            status, payload = http_get(url)
    except ActionUnavailableError:
        raise
    except Exception:  # noqa: BLE001
        return False, None
    ok = status is True if isinstance(status, bool) else int(status) == 200
    return ok, payload


def _unavailable(source: str, reason: str) -> Dict[str, Any]:
    return {"status": "unavailable", "source": source, "reason": reason}


def _read_templates(
    http_get: Callable[..., Tuple[Any, Any]],
    base_url: str,
    auth: Optional[str],
    tenant: Optional[str],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Templates with evaluation metrics from the research owner; degrade, never fail the board."""
    ok, payload = _owner_get(http_get, f"{base_url.rstrip('/')}{_ORCHESTRATOR_TEMPLATES_PATH}", auth, tenant)
    rows = _extract_entries(payload) if ok else None
    if rows is None:
        return [], _unavailable(_OWNER_SOURCE, "templates_read_failed")
    projected = [t for t in (discovery_projection.project_template_evaluation(r) for r in rows) if t]
    return projected, {"status": "ok", "source": _OWNER_SOURCE}


def _read_demands(
    http_get: Callable[..., Tuple[Any, Any]],
    base_url: str,
    auth: Optional[str],
    tenant: str,
) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """Research demands of the caller tenant (the same owner read the research task list uses)."""
    ok, payload = _owner_get(http_get, f"{base_url.rstrip('/')}{_DEMANDS_PATH}", auth, tenant)
    rows = payload.get("demands") if isinstance(payload, dict) else None
    if not ok or not isinstance(rows, list):
        return None, _unavailable(_OWNER_SOURCE, "owner_read_failed" if not ok else "demands_payload_invalid")
    if any(not isinstance(d, dict) or d.get("tenant_id") != tenant for d in rows):
        return None, _unavailable(_OWNER_SOURCE, "demand_scope_mismatch")
    return list(rows), {"status": "ok", "source": _OWNER_SOURCE}


def _read_runs(
    http_get: Callable[..., Tuple[Any, Any]],
    base_url: str,
    auth: Optional[str],
    tenant: str,
) -> Tuple[Optional[List[Dict[str, Any]]], Dict[str, Any]]:
    """Newest runs of the caller tenant from the owner's tenant-scoped page (one bounded page)."""
    url = f"{base_url.rstrip('/')}{_RUNS_PATH}?limit={_MAX_RUNS_USED}"
    ok, payload = _owner_get(http_get, url, auth, tenant)
    rows = _extract_entries(payload) if ok else None
    if rows is None:
        return None, _unavailable(_OWNER_SOURCE, "runs_read_failed")
    if any(str(r.get("tenant_id") or "").strip() != tenant for r in rows):
        return None, _unavailable(_OWNER_SOURCE, "run_scope_mismatch")
    # An owner that predates the page form ignores ``limit``; the cap still applies.
    mine = sorted(rows, key=_run_sort_key, reverse=True)
    surface: Dict[str, Any] = {"status": "ok", "source": _OWNER_SOURCE, "runs_used": min(len(mine), _MAX_RUNS_USED)}
    if len(mine) > _MAX_RUNS_USED or (isinstance(payload, dict) and payload.get("next_cursor")):
        surface["truncated"] = True
    return mine[:_MAX_RUNS_USED], surface


def _read_discovery_summary(
    http_get: Callable[..., Tuple[Any, Any]],
    auth: Optional[str],
    tenant: str,
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """The source-ingest owner's bounded discovery summary for the caller tenant."""
    base = _resolve_source_ingest_url()
    if not base:
        return None, _unavailable(_SUMMARY_SOURCE, "source_ingest_unconfigured")
    ok, payload = _owner_get(http_get, f"{base}{_SUMMARY_PATH}", auth, tenant)
    if not ok or not isinstance(payload, dict):
        return None, _unavailable(_SUMMARY_SOURCE, "summary_read_failed")
    if str(payload.get("tenant_id") or "").strip() != tenant:
        return None, _unavailable(_SUMMARY_SOURCE, "summary_scope_mismatch")
    return payload, {"status": "ok", "source": _SUMMARY_SOURCE}


def _summary_section(
    summary: Optional[Dict[str, Any]], key: str, summary_surface: Dict[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(section, surface): a section the owner marked unavailable keeps its own reason."""
    if summary is None:
        return {}, dict(summary_surface)
    section = summary.get(key)
    if not isinstance(section, dict):
        return {}, _unavailable(_SUMMARY_SOURCE, "summary_payload_invalid")
    if section.get("status") != "ok":
        return {}, _unavailable(_SUMMARY_SOURCE, str(section.get("reason") or "owner_section_unavailable"))
    return section, {"status": "ok", "source": _SUMMARY_SOURCE}


def _int_at(section: Dict[str, Any], *path: str) -> Optional[int]:
    value: Any = section
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def reason_code(raw: Any) -> str:
    """Typed reason code from an owner reason string; unknown text keeps only its leading token."""
    text = str(raw or "").strip()
    if not text:
        return "reason_not_recorded"
    low = text.lower()
    for known in _KNOWN_REASON_CODES:
        if known in low:
            return known
    if "dataset" in low and ("unavailable" in low or "missing" in low):
        return "dataset_unavailable"
    token = low.split(":", 1)[0].strip().replace(" ", "_")
    return token[:80] or "reason_not_recorded"


def _is_template_search_run(run: Dict[str, Any]) -> bool:
    params = run.get("parameters") if isinstance(run.get("parameters"), dict) else {}
    return params.get("action_type") == _TEMPLATE_SEARCH_ACTION and bool(str(params.get("demand_id") or "").strip())


def _run_reason(run: Dict[str, Any]) -> Optional[str]:
    rejection = run.get("rejection") if isinstance(run.get("rejection"), dict) else {}
    result = run.get("template_search") if isinstance(run.get("template_search"), dict) else {}
    for candidate in (rejection.get("detail"), result.get("reason")):
        if str(candidate or "").strip():
            return str(candidate).strip()
    return None


def _run_sort_key(run: Dict[str, Any]) -> Tuple[str, str]:
    return (str(run.get("created_at") or ""), str(run.get("run_id") or run.get("id") or ""))


def _demand_item(
    demand: Dict[str, Any], names: Dict[str, str]
) -> Dict[str, Any]:
    discovery = demand.get("discovery") if isinstance(demand.get("discovery"), dict) else {}
    template = discovery.get("template_search") if isinstance(discovery.get("template_search"), dict) else {}
    last_run = discovery.get("last_run") if isinstance(discovery.get("last_run"), dict) else {}
    persona_id = demand.get("persona_id")
    latest = template.get("reason") or last_run.get("reason")
    return {
        "id": demand.get("demand_id"),
        "lane": "discovered",
        "kind": "research_demand",
        "demand_id": demand.get("demand_id"),
        "persona_id": persona_id,
        "display_name": names.get(str(persona_id)) or None,
        "lacking": {
            "short_measure": demand.get("short_measure"),
            "missing_datasets": list(demand.get("missing_datasets") or []),
            "tried_families": list(demand.get("tried_families") or []),
        },
        "since": demand.get("created_at"),
        "status": demand.get("status"),
        "latest_outcome": {"reason_code": reason_code(latest) if latest else None, "reason": latest or None},
    }


def _card_item(card: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One newest card from the owner discovery summary (source, title, claim, time)."""
    source_id = str(card.get("source_id") or "").strip()
    if not source_id:
        return None
    digest = str(card.get("source_digest") or "").strip()
    return {
        "id": f"card:{source_id}:{digest}" if digest else f"card:{source_id}",
        "lane": "discovered",
        "kind": "hypothesis_card",
        "seed_id": card.get("seed_id"),
        "display_name": card.get("title") or source_id,
        "title": card.get("title"),
        "title_status": card.get("title_status"),
        "source_id": source_id,
        "source_family": card.get("source_family"),
        "claim": card.get("claim"),
        "claim_status": card.get("claim_status"),
        "since": card.get("carded_at"),
        "status": None,
        "latest_outcome": {"reason_code": None, "reason": None, "status": None},
    }


def _search_items(
    demands: List[Dict[str, Any]], runs: List[Dict[str, Any]], names: Dict[str, str]
) -> List[Dict[str, Any]]:
    """One item per demand that was searched: attempts, latest outcome and typed reason."""
    by_demand: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        if _is_template_search_run(run):
            by_demand.setdefault(str(run["parameters"]["demand_id"]).strip(), []).append(run)
    items: List[Dict[str, Any]] = []
    for demand in demands:
        demand_id = str(demand.get("demand_id") or "").strip()
        attempts = sorted(by_demand.get(demand_id, []), key=_run_sort_key)
        if not attempts:
            continue
        latest = attempts[-1]
        raw = _run_reason(latest)
        persona_id = demand.get("persona_id")
        items.append(
            {
                "id": f"template-search:{demand_id}",
                "lane": "scaffolded",
                "kind": "template_search",
                "demand_id": demand_id,
                "persona_id": persona_id,
                "display_name": names.get(str(persona_id)) or None,
                "attempt_count": len(attempts),
                "latest_run_id": latest.get("run_id") or latest.get("id"),
                "latest_at": latest.get("updated_at") or latest.get("created_at"),
                "outcome": latest.get("status"),
                "reason_code": reason_code(raw) if raw else None,
                "reason": raw,
            }
        )
    return items


def _gate_results(runs: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    """run_id -> recorded replication gate of a completed run."""
    out: Dict[str, Dict[str, Any]] = {}
    for run in runs or []:
        meta = run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
        gate = meta.get("replication_gate")
        if isinstance(gate, dict) and str(run.get("status") or "").lower() == "completed":
            out[str(run.get("run_id") or run.get("id") or "")] = gate
    return out


def _gate_passed(gate: Any) -> bool:
    return isinstance(gate, dict) and gate.get("passed") is True


def _scan_values(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value.lower()]
    if isinstance(value, dict):
        return [x for v in value.values() for x in _scan_values(v)]
    if isinstance(value, (list, tuple)):
        return [x for v in value for x in _scan_values(v)]
    return []


def _document_check_reason(entry: Dict[str, Any], card: Dict[str, Any], *, gate_known: bool) -> str:
    """Why a completed admission is only a document check, from owner facts."""
    if str(card.get("strategy_state") or "").lower() == "retired":
        return "retired"
    if str(entry.get("data_scope") or "").lower() == "test":
        return "test_scope"
    facts = _scan_values([entry.get("mode"), entry.get("datasets"), entry.get("lineage"), entry.get("source_kind")])
    if any(f == "simulation" or f == "raw_market" or f.startswith("raw_market:") for f in facts):
        return "simulation_or_raw_market_seed"
    return "no_passing_gate_result" if gate_known else "gate_result_unavailable"


def _classify_cards(
    cards: List[Dict[str, Any]],
    entries_by_id: Dict[str, Dict[str, Any]],
    runs: Optional[List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Sort admissions into lanes; returns the document-checked cards (they leave the lanes)."""
    gates = _gate_results(runs)
    document_checked: List[Dict[str, Any]] = []
    scaffolded: List[Dict[str, Any]] = []
    for card in cards:
        entry = entries_by_id.get(str(card.get("id"))) or {}
        if card.get("lane") != "replicated":
            if card.get("last_error"):
                card["reason_code"] = reason_code(card["last_error"])
            scaffolded.append(card)
            continue
        run_ids = [str(r) for r in (card.get("experiment_run_ids") or [])]
        passed = [gates[r] for r in run_ids if r in gates and _gate_passed(gates[r])]
        embedded = entry.get("replication_gate")
        if passed or _gate_passed(embedded):
            card["gate"] = {"passed": True, "source": "owner_run_gate"}
            continue
        card["lane"] = "document_checked"
        card["document_check_reason"] = _document_check_reason(entry, card, gate_known=runs is not None)
        document_checked.append(card)
    return document_checked


def _llm_per_source(llm: Dict[str, Any]) -> Dict[str, Any]:
    """Distinct sources and calls per source from the owner ledger read; null with a reason, never 0."""
    sources = _int_at(llm, "distinct_sources")
    if sources is None:
        reason = llm.get("distinct_sources_reason") if "distinct_sources" in llm else None
        return {
            "distinct_sources": None,
            "calls_per_source": None,
            "distinct_sources_reason": str(reason or "owner_distinct_sources_not_reported"),
        }
    raw = llm.get("calls_per_source")
    per_source = float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None
    out: Dict[str, Any] = {"distinct_sources": sources, "calls_per_source": per_source}
    if per_source is None:
        out["calls_per_source_reason"] = str(llm.get("calls_per_source_reason") or "owner_calls_per_source_not_reported")
    return out


def _count(value: Optional[int], surface: Dict[str, Any]) -> Dict[str, Any]:
    if value is None or surface.get("status") != "ok":
        return {"value": None, "status": "unavailable", "reason": surface.get("reason") or "owner_read_failed"}
    return {"value": int(value), "status": "ok"}


def _build_alpha_factory_payload(
    snapshot_at: str,
    *,
    all_cards: List[Dict[str, Any]],
    page: int,
    page_size: int,
    lane: Optional[str],
    surface: Dict[str, Any],
    strategy_state_surface: Optional[Dict[str, Any]] = None,
    templates: Optional[List[Dict[str, Any]]] = None,
    templates_surface: Optional[Dict[str, Any]] = None,
    discovered: Optional[List[Dict[str, Any]]] = None,
    searches: Optional[List[Dict[str, Any]]] = None,
    document_checked: Optional[List[Dict[str, Any]]] = None,
    counts: Optional[Dict[str, Any]] = None,
    extra_surfaces: Optional[Dict[str, Any]] = None,
    discovery_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    is_unavailable = surface.get("status") == "unavailable"
    effective_cards = [] if is_unavailable else list(all_cards)
    discovered_items = [] if is_unavailable else list(discovered or [])
    document_items = [] if is_unavailable else list(document_checked or [])
    scaffolded_items = ([] if is_unavailable else list(searches or [])) + [
        c for c in effective_cards if c.get("lane") == "scaffolded"
    ]
    replicated_items = [c for c in effective_cards if c.get("lane") == "replicated"]

    by_lane = {
        "discovered": discovered_items,
        "scaffolded": scaffolded_items,
        "replicated": replicated_items,
    }
    filtered = by_lane.get(lane) if lane in by_lane else (
        discovered_items + scaffolded_items + replicated_items
    )
    if lane == "document_checked":
        filtered = document_items

    total = len(filtered)
    start = (page - 1) * page_size
    page_items = filtered[start : start + page_size]
    next_page_token = str(page + 1) if (start + page_size) < total else None

    lanes_payload: List[Dict[str, Any]] = [
        {"id": "discovered", "label": "Discovered", "items": discovered_items},
        {"id": "scaffolded", "label": "Scaffolded", "items": scaffolded_items},
        {"id": "replicated", "label": "Replicated", "items": replicated_items},
    ]

    data: Dict[str, Any] = {
        "id": "alpha-factory",
        "snapshotAt": snapshot_at,
        "snapshot_at": snapshot_at,
        "lanes": lanes_payload,
        "discovered": discovered_items,
        "scaffolded": scaffolded_items,
        "replicated": replicated_items,
        "document_checked": document_items,
        "counts": counts or {},
        "items": page_items,
        "templates": list(templates or []),
        "discovery_summary": discovery_summary,
    }
    surfaces: Dict[str, Any] = {_SURFACE_KEY: surface}
    if templates_surface is not None:
        surfaces[_TEMPLATES_SURFACE_KEY] = templates_surface
    if strategy_state_surface is not None:
        surfaces[_STRATEGY_STATE_SURFACE_KEY] = strategy_state_surface
    surfaces.update(extra_surfaces or {})
    return {
        "data": data,
        "items": page_items,
        "page_info": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "next_page_token": next_page_token,
        },
        "meta": {
            "snapshot_at": snapshot_at,
            "surfaces": surfaces,
            "filters": {"lane": lane},
        },
    }


def create_alpha_factory_router(
    *,
    extract_identity: Callable,
    require_read_role: Callable,
    utc_now: Callable[[], str],
    http_get: Optional[Callable[..., Tuple[Any, Any]]] = None,
    orchestrator_url_resolver: Optional[Callable[[], Optional[str]]] = None,
    read_surface: Optional[Any] = None,
    get_read_store: Optional[Callable[[], Any]] = None,
    read_strategy_state: Optional[Callable[[str], Tuple[Optional[str], str]]] = None,
    read_persona_names: Optional[Callable[[str, str], Optional[Dict[str, str]]]] = None,
) -> APIRouter:
    del read_surface, get_read_store
    router = APIRouter()
    eff_http_get = http_get or _default_http_get
    eff_url_resolver = orchestrator_url_resolver or _resolve_orchestrator_url

    @router.get("/bff/alpha-factory")
    def get_alpha_factory(
        lane: Optional[str] = Query(
            default=None,
            description="Filter by lane id: discovered | scaffolded | replicated | document_checked",
        ),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        """BFF-AF-001: AlphaFactory pipeline board (who lacks what, what ran, what truly passed)."""
        eff_auth = authorization or owner_authorization.get()
        identity = extract_identity(eff_auth)
        require_read_role(identity)

        req_tenant = x_tenant_id if x_tenant_id is not None else selected_tenant.get()
        verified_tenant = _resolve_verified_caller_tenant(identity, req_tenant)

        snapshot_at = utc_now()
        base_url = eff_url_resolver()
        if not base_url or not verified_tenant:
            surface: Dict[str, Any] = {
                "status": "unavailable",
                "source": _OWNER_SOURCE,
                "reason": "unconfigured" if not base_url else "missing_tenant_scope",
                "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
            }
            return _build_alpha_factory_payload(
                snapshot_at,
                all_cards=[],
                page=page,
                page_size=page_size,
                lane=lane,
                surface=surface,
            )

        url = f"{base_url.rstrip('/')}{_ORCHESTRATOR_ENTRIES_PATH}"
        ok, raw_payload = _owner_get(eff_http_get, url, eff_auth, verified_tenant)
        entries = _extract_entries(raw_payload) if ok else None
        if not ok or entries is None:
            surface = {
                "status": "unavailable",
                "source": _OWNER_SOURCE,
                "reason": "upstream_error",
                "staleness": {"served_from": "unverifiable", "last_known_at": snapshot_at},
            }
            return _build_alpha_factory_payload(
                snapshot_at,
                all_cards=[],
                page=page,
                page_size=page_size,
                lane=lane,
                surface=surface,
            )

        cards: List[Dict[str, Any]] = []
        entries_by_id: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            entry_tenant = str(entry.get("tenant_id") or "").strip()
            if entry_tenant and entry_tenant != verified_tenant:
                continue
            card = _project_alpha_factory_card(entry)
            cards.append(card)
            entries_by_id[str(card.get("id"))] = entry

        strategy_state_surface = _attach_strategy_state(cards, read_strategy_state)
        templates, templates_surface = _read_templates(
            eff_http_get, base_url, eff_auth, verified_tenant
        )
        demands, demands_surface = _read_demands(eff_http_get, base_url, eff_auth, verified_tenant)
        runs, runs_surface = _read_runs(eff_http_get, base_url, eff_auth, verified_tenant)
        # Collected/closed/cards come from the owner's bounded discovery summary;
        # never fall back to a scan (see FORBIDDEN_UNBOUNDED_PATHS).
        summary, summary_surface = _read_discovery_summary(eff_http_get, eff_auth, verified_tenant)
        records, records_surface = _summary_section(summary, "collected_text_records", summary_surface)
        closed, closed_surface = _summary_section(summary, "closed_text_records", summary_surface)
        card_section, cards_surface = _summary_section(summary, "hypothesis_cards", summary_surface)
        llm, llm_surface = _summary_section(summary, "llm_calls", summary_surface)

        names: Dict[str, str] = {}
        persona_surface: Dict[str, Any] = {"status": "ok", "source": "persona_directory"}
        if read_persona_names is not None:
            try:
                resolved = read_persona_names(verified_tenant, snapshot_at)
            except Exception:  # noqa: BLE001 - names are display only
                resolved = None
            if resolved is None:
                persona_surface = _unavailable("persona_directory", "persona_directory_read_failed")
            else:
                names = dict(resolved)
        else:
            persona_surface = _unavailable("persona_directory", "reader_unconfigured")

        document_checked = _classify_cards(cards, entries_by_id, runs)
        open_demands = [d for d in (demands or []) if d.get("status") == "open"]
        discovered: List[Dict[str, Any]] = [_demand_item(d, names) for d in open_demands]
        newest_cards = card_section.get("newest") if isinstance(card_section.get("newest"), list) else []
        card_items = [c for c in (_card_item(x) for x in newest_cards if isinstance(x, dict)) if c is not None]
        discovered.extend(card_items)
        searches = _search_items(demands or [], runs or [], names) if demands is not None and runs is not None else []

        passed_count = sum(1 for c in cards if c.get("lane") == "replicated")
        search_runs = [r for r in (runs or []) if _is_template_search_run(r)]
        by_reason_24h = (closed.get("last_24h") or {}).get("by_reason") if closed else None
        llm_count = _count(_int_at(llm, "calls"), llm_surface)
        if llm_count["status"] == "ok":
            llm_count["cap"] = _int_at(llm, "daily_cap")
            llm_count["scope"] = llm.get("scope")
            llm_count.update(_llm_per_source(llm))
        counts = {
            "collected_text_records": _count(_int_at(records, "last_24h", "total"), records_surface),
            "closed_text_records": _count(_int_at(closed, "last_24h", "total"), closed_surface),
            "dropped_irrelevant": _count(
                int(by_reason_24h.get("l2_irrelevant", 0)) if isinstance(by_reason_24h, dict) else None,
                closed_surface,
            ),
            "hypothesis_cards": _count(_int_at(card_section, "total"), cards_surface),
            "hypothesis_cards_today": _count(_int_at(card_section, "today"), cards_surface),
            "llm_calls_today": llm_count,
            "templates_registered": _count(len(templates), templates_surface),
            "templates_evaluated": _count(
                sum(1 for t in templates if t.get("evaluation")), templates_surface
            ),
            "searches": _count(len(search_runs) if runs is not None else None, runs_surface),
            "passes": _count(passed_count if runs is not None else None, runs_surface),
        }

        surface = {
            "status": "ok",
            "source": _OWNER_SOURCE,
        }
        return _build_alpha_factory_payload(
            snapshot_at,
            all_cards=cards,
            page=page,
            page_size=page_size,
            lane=lane,
            surface=surface,
            strategy_state_surface=strategy_state_surface,
            templates=templates,
            templates_surface=templates_surface,
            discovered=discovered,
            searches=searches,
            document_checked=document_checked,
            counts=counts,
            extra_surfaces={
                _DEMANDS_SURFACE_KEY: demands_surface,
                _RUNS_SURFACE_KEY: runs_surface,
                _CARDS_SURFACE_KEY: cards_surface,
                _RECORDS_SURFACE_KEY: records_surface,
                _PERSONA_SURFACE_KEY: persona_surface,
                _LLM_SURFACE_KEY: llm_surface,
            },
            discovery_summary={
                "window": summary.get("window") if summary else None,
                "collected_by_source_family": (records.get("last_24h") or {}).get("by_source_family") if records else None,
                "closed_last_24h": closed.get("last_24h") if closed else None,
                "closed_total": closed.get("total") if closed else None,
                "llm_calls": llm or None,
                "bounds": summary.get("bounds") if summary else None,
            },
        )

    return router
