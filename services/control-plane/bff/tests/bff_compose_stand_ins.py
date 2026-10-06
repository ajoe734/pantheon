"""Test-only stand-ins for BFF composition dependencies.

Production composition (``core.app_factory``) raises ``UnresolvedBffDependency``
for any dependency that no explicit port, loaded ``main`` module or real owner
supplies. Tests that compose a router subset or the standalone app, without
``main.py``, inject this resolver explicitly::

    compose_bff_app(dependency_resolver=resolve_with_stand_ins)

Nothing under ``services/`` imports this module, so no production path can
select these stand-ins.
"""
from __future__ import annotations

from collections import defaultdict
from contextvars import ContextVar
from typing import Any

from services.control_plane.bff.core.app_factory import (
    _SSE_CHANNEL_CATALOG_FALLBACK,
    UnresolvedBffDependency,
    _resolve_default_dependency,
)


def _none(*_a: Any, **_kw: Any) -> None:
    return None


def _empty_dict(*_a: Any, **_kw: Any) -> dict:
    return {}


def _empty_list(*_a: Any, **_kw: Any) -> list:
    return []


_DICT_STORES = {
    "_GOV_BFF_IDEMPOTENCY", "gov_bff_idempotency", "_AGORA_CORE_BFF_IDEMPOTENCY",
    "_STRATEGY_PERSONA_BFF_IDEMPOTENCY", "_STRATEGY_SEED_REPLICATION_BFF_IDEMPOTENCY",
    "_STRATEGY_SEED_REVIEW_BFF_IDEMPOTENCY", "idempotency_ledger",
}
_NONE_CALLABLES = {
    "_raise_if_read_surface_unavailable", "raise_if_read_surface_unavailable",
    "raise_if_read_surface_unavailable_fn", "_raise_if_session_logged_out",
    "_reject_body_idempotency_key", "reject_body_idempotency_key", "reject_body_idempotency_key_fn",
    "_require_ooda_packet_routes_enabled", "_require_journal_write_role",
    "_strategy_persona_idempotency_check", "strategy_persona_idempotency_check",
    "_handle_sse_stream", "handle_sse_stream", "_publish_event", "publish_event",
    "publish_event_fn", "stream_generic_events",
    "bff_management_readiness_bff_ha", "bff_management_readiness_broker_live",
    "bff_management_readiness_capital_binding_live", "bff_management_readiness_ep5",
    "bff_management_readiness_strict_publish", "bff_types_compat",
    "sem_bff_health_alias", "sem_bff_readiness_alias",
}
_EMPTY_DICT_CALLABLES = {
    "_build_operator_alerts_payload", "build_operator_alerts_payload",
    "_build_management_cockpit_payload", "build_cockpit_payload",
    "_build_management_evidence_payload", "build_evidence_payload",
    "_project_operator_runtime_state_row", "_read_surface_state",
    "_ooda_packet_list_payload", "ooda_packet_list_payload",
    "_assistant_build_context_pack", "build_context_pack",
    "_assistant_provider_readiness", "provider_readiness",
    "_assistant_provider_register", "provider_register",
    "_assistant_provider_reauth", "provider_reauth",
    "_assistant_provider_reauth_status", "provider_reauth_status",
    "_assistant_provider_reauth_code", "provider_reauth_code",
    "_ensure_agora_servant_openclaw_agent", "sync_servant_agent",
    "_resolve_agora_interaction_context_ref", "canonical_context_ref_resolver",
    "_read_surface_meta", "read_surface_meta",
    "_gov_bff_action_command", "gov_bff_action_command",
    "_capital_bff_action_command", "capital_bff_action_command",
    "_evol_exp_bff_action_command", "submit_job_action",
    "submit_program_action", "submit_experiment_action",
    "_submit_final_command_admission", "submit_command", "submit_final_command_admission",
    "_sem_command_response", "sem_command_response", "submit_sem_command",
    "_aggregate_group_surface", "aggregate_group_surface",
}
_EMPTY_LIST_CALLABLES = {
    "_read_management_source_connector_registry", "read_source_connector_registry",
    "_list_governance_audit_events", "list_governance_audit_events",
    "_list_persona_records", "list_persona_records",
    "_list_strategy_summaries", "list_strategy_summaries",
    "_assistant_provider_list", "provider_list",
}
_BLANK_STRING_CALLABLES = {"_alert_target_ref", "_incident_detail_href", "_deployment_review_href"}


def stand_in(name: str, app_deps: Any) -> Any:
    """Return the inert test double for ``name``; raise ``UnresolvedBffDependency`` if there is none."""
    if name in _DICT_STORES:
        return {}
    if name in _NONE_CALLABLES:
        return _none
    if name in _EMPTY_DICT_CALLABLES:
        return _empty_dict
    if name in _EMPTY_LIST_CALLABLES:
        return _empty_list
    if name in _BLANK_STRING_CALLABLES:
        return lambda *a, **kw: ""
    if name in {"_incident_events", "incident_events"}:
        return []
    if name in {"_incident_subscribers", "incident_subscribers"}:
        return set()
    if name in {"_sse_buffers", "sse_buffers"}:
        return defaultdict(list, {ch: [] for ch in _SSE_CHANNEL_CATALOG_FALLBACK})
    if name in {"_sse_subscribers", "sse_subscribers"}:
        return defaultdict(set, {ch: set() for ch in _SSE_CHANNEL_CATALOG_FALLBACK})
    if name in {
        "_composed_surface_status", "composed_surface_status",
        "_composed_dataset_surface_status", "composed_dataset_surface_status",
    }:
        return lambda *a, **kw: "available"
    if name in {"_resolve_final_idempotency_key", "resolve_final_idempotency_key", "resolve_final_idempotency_key_fn"}:
        return lambda k, d=None: k or d or "default-key"
    if name in {"_request_dry_run_requested", "request_dry_run_requested", "_truthy_header", "dry_run_resolver"}:
        return lambda *a, **kw: False
    if name in {"_dry_run_success_response", "dry_run_success_response"}:
        return lambda *a, **kw: {"status": "dry_run"}
    if name in {"_meta_staleness", "meta_staleness"}:
        return lambda *a, **kw: 0.0
    if name in {"_stable_json_hash", "stable_json_hash"}:
        return lambda *a, **kw: "hash"
    if name in {"_management_ai_conversation_store", "conv_store"}:
        return lambda: None
    if name in {"_assistant_ask_enabled", "assistant_ask_enabled"}:
        return lambda *a, **kw: True
    if name == "_REQUEST_DRY_RUN_CONTEXT":
        return ContextVar("request_dry_run_context", default=False)
    if name == "provider_readiness_cache":
        from services.control_plane.bff.auth.service import ProviderReadinessCache
        return ProviderReadinessCache(probe=lambda: {"ready": True}, provider="openclaw")
    if name in {"agora_audit_store", "strategy_write_owner", "loop_truth", "downstream_health_monitor"}:
        return None
    raise UnresolvedBffDependency(name)


def resolve_with_stand_ins(name: str, app_deps: Any) -> Any:
    """Real production resolution first, then the test stand-in."""
    try:
        return _resolve_default_dependency(name, app_deps)
    except UnresolvedBffDependency:
        return stand_in(name, app_deps)
