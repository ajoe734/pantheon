"""Typed domain ports and read models for Operations, OpenClaw, Workflows/Catalog, and Consultation.

This module provides typed domain ports (protocols and domain adapters) for:
- ACG-02-006: Workflow templates, hook registry, and automation/governance catalog reads
- ACG-02-007: OpenClaw operations snapshots, broker adapter readiness, and Research OSS preactivation
- ACG-02-008: Consultation sessions, transcripts, memos, requests, participants, outcome and evidence reads

These ports decouple route handlers and read models from the monolithic ReadSurfaceStore,
ensuring domain reads route directly to their typed domain stores and service clients while
preserving exact identity, sorting, filtering, and truthful error semantics.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Set, Tuple, Union, runtime_checkable

# Typed service client imports with fail-safe fallbacks
from services.control_plane.bff.openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

try:
    from services.consultation.client import ConsultationClientError, ConsultationServiceClient
    from services.consultation.models import (
        ConsultAuditEvent,
        ConsultEvidenceAttachment,
        ConsultGateHandoff,
        ConsultMemo,
        ConsultParticipant,
        ConsultPriority,
        ConsultRequest,
        ConsultRequestStatus,
        ConsultRequestType,
        ConsultTranscript,
        MemoStatus,
        TranscriptEvent,
    )
    from services.consultation.store import ConsultationStore, build_consultation_store
except ImportError:  # pragma: no cover
    try:
        from consultation.client import ConsultationClientError, ConsultationServiceClient  # type: ignore[no-redef]
        from consultation.models import (  # type: ignore[no-redef]
            ConsultAuditEvent,
            ConsultEvidenceAttachment,
            ConsultGateHandoff,
            ConsultMemo,
            ConsultParticipant,
            ConsultPriority,
            ConsultRequest,
            ConsultRequestStatus,
            ConsultRequestType,
            ConsultTranscript,
            MemoStatus,
            TranscriptEvent,
        )
        from consultation.store import ConsultationStore, build_consultation_store  # type: ignore[no-redef]
    except ImportError:  # pragma: no cover
        ConsultationClientError = None  # type: ignore[assignment,misc]
        ConsultationServiceClient = None  # type: ignore[assignment,misc]
        ConsultationStore = None  # type: ignore[assignment,misc]
        build_consultation_store = None  # type: ignore[assignment,misc]
        ConsultRequest = None  # type: ignore[assignment,misc]
        ConsultMemo = None  # type: ignore[assignment,misc]
        ConsultParticipant = None  # type: ignore[assignment,misc]
        ConsultTranscript = None  # type: ignore[assignment,misc]
        ConsultEvidenceAttachment = None  # type: ignore[assignment,misc]
        ConsultGateHandoff = None  # type: ignore[assignment,misc]
        ConsultAuditEvent = None  # type: ignore[assignment,misc]
        TranscriptEvent = None  # type: ignore[assignment,misc]
        ConsultRequestType = None  # type: ignore[assignment,misc]
        ConsultRequestStatus = None  # type: ignore[assignment,misc]
        ConsultPriority = None  # type: ignore[assignment,misc]
        MemoStatus = None  # type: ignore[assignment,misc]


# =====================================================================
# Constants & Helper Mappings
# =====================================================================

def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        normalized = cleaned.replace("Z", "+00:00") if cleaned.endswith("Z") else cleaned
        dt = datetime.fromisoformat(normalized)
        return dt
    except (ValueError, TypeError):
        return None


def _model_to_data(model: Any) -> Dict[str, Any]:
    if isinstance(model, dict):
        return model
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    if hasattr(model, "dict"):
        return model.dict()
    if hasattr(model, "json"):
        return json.loads(model.json())
    return dict(model)


_CONSULTATION_DATA_DIR_ENVS = (
    "PANTHEON_BFF_CONSULTATION_DATA_DIR",
    "PANTHEON_CONSULTATION_DATA_DIR",
    "CONSULTATION_DATA_DIR",
)

_SERVICE_TO_BFF_REQUEST_STATUS: Dict[str, str] = {
    "draft": "created",
    "submitted": "submitted",
    "assigned": "assigned",
    "in_progress": "in_progress",
    "memo_pending": "in_progress",
    "published": "completed",
    "cancelled": "canceled",
    "canceled": "canceled",
}

_SERVICE_TO_SESSION_STATUS: Dict[str, str] = {
    "draft": "active",
    "submitted": "active",
    "assigned": "active",
    "in_progress": "active",
    "memo_pending": "active",
    "published": "completed",
    "cancelled": "terminated",
    "canceled": "terminated",
}

_BFF_TO_SERVICE_REQUEST_TYPE: Dict[str, Any] = {}
_BFF_TO_SERVICE_PRIORITY: Dict[str, Any] = {}
if ConsultRequestType is not None:
    _BFF_TO_SERVICE_REQUEST_TYPE = {
        "pre_deployment": ConsultRequestType.STRATEGY_REVIEW,
        "risk_review": ConsultRequestType.EXECUTION_RISK,
        "macro_regime_shift": ConsultRequestType.STRATEGY_REVIEW,
        "incident_response": ConsultRequestType.INCIDENT,
        "policy_change": ConsultRequestType.PERSONA_POLICY,
        "general": ConsultRequestType.STRATEGY_REVIEW,
    }
if ConsultPriority is not None:
    _BFF_TO_SERVICE_PRIORITY = {
        "low": ConsultPriority.LOW,
        "normal": ConsultPriority.NORMAL,
        "high": ConsultPriority.HIGH,
        "critical": ConsultPriority.URGENT,
    }


# Memo Redaction Rules
_CONSULT_MEMO_REVIEW_REDACTED_KEYS = {
    "policyinternals",
    "memorytrace",
    "internalscore",
    "personainternalstate",
    "secretcredentials",
    "secretref",
    "capabilitymapinternals",
    "capabilitymap",
    "capabilitysnapshot",
    "effectivecapabilities",
    "effectivetools",
    "effectiveskills",
    "toolgrants",
    "envgrants",
    "permissionmap",
}

_CONSULT_MEMO_REVIEW_REDACTED_TEXT_TOKENS = (
    "policy_internals",
    "memory_trace",
    "internal_score",
    "persona_internal_state",
    "secret_credentials",
    "secretRef",
    "secret_ref",
    "capability_map_internals",
    "capabilityMap",
    "capability_map",
    "effective_tools",
    "effective_skills",
)


def _consult_memo_review_redaction_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key or "").lower())


def _redact_consult_memo_review_text(value: str) -> str:
    redacted = value
    for token in _CONSULT_MEMO_REVIEW_REDACTED_TEXT_TOKENS:
        redacted = re.sub(
            rf"(?i)(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])",
            "[redacted]",
            redacted,
        )
    return redacted


def _redact_consult_memo_review_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            if _consult_memo_review_redaction_key(key) in _CONSULT_MEMO_REVIEW_REDACTED_KEYS:
                continue
            redacted[key] = _redact_consult_memo_review_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_consult_memo_review_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_consult_memo_review_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_consult_memo_review_text(value)
    return value


# Dormant OSS specs & gates
_DORMANT_OSS_BACKENDS = ("dsp", "dspy", "qlib", "finrl", "imitation", "mlflow", "openclaw")
_DORMANT_SAFE_DISPATCHERS = ("finrl", "imitation")
_DORMANT_FAIL_CLOSED_REASONS = {
    "offline_gate_disabled",
    "paper_adapter_disabled",
    "live_adapter_disabled",
    "broker_execution_disabled",
    "capital_binding_disabled",
}
_DORMANT_OFFLINE_SCOPE = "offline_training_preactivation"

_OPENCLAW_GATE_FIELDS: Dict[str, Dict[str, str]] = {
    "broker_execution": {
        "activation_gate": "OPENCLAW_LIVE_ADAPTER_ENABLED",
        "allowed_scope": "capability_metadata_read_only",
    },
    "paper_adapter": {
        "activation_gate": "OPENCLAW_PAPER_ADAPTER_ENABLED",
        "allowed_scope": "offline_paper_evaluation_gated",
    },
    "live_adapter": {
        "activation_gate": "OPENCLAW_LIVE_ADAPTER_ENABLED",
        "allowed_scope": "capability_metadata_read_only",
    },
    "capital_binding": {
        "activation_gate": "OPENCLAW_CAPITAL_BINDING_ENABLED",
        "allowed_scope": "capability_metadata_read_only",
    },
}

_DORMANT_SERVICE_SPECS: Dict[str, Dict[str, Any]] = {
    "research_worker_gateway": {
        "env": "PANTHEON_RESEARCH_GATEWAY_URL",
        "default_port": 8013,
        "capabilities_path": "/capabilities",
        "activity_path": "/runs",
        "upstream_status_path": "/healthz",
        "actor_field": "backend",
        "id_fields": ("run_id", "id"),
        "activity_kind": "research_run",
    },
    "research_operator_bridge": {
        "env": "PANTHEON_RESEARCH_OPERATOR_BRIDGE_URL",
        "default_port": 8012,
        "capabilities_path": "/capabilities",
        "activity_path": "/dispatches",
        "upstream_status_path": "/healthz",
        "actor_field": "framework",
        "id_fields": ("dispatch_id", "id"),
        "activity_kind": "research_dispatch",
    },
    "openclaw_gateway_adapter": {
        "env": "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL",
        "default_port": 8014,
        "capabilities_path": "/api/openclaw-adapter/capabilities",
        "activity_path": "/api/openclaw-adapter/invocations",
        "upstream_status_path": "/api/openclaw-adapter/upstream/status",
        "actor_field": "adapter",
        "id_fields": ("invocation_id", "trace_id", "id"),
        "activity_kind": "openclaw_invocation",
    },
}


def _automation_record_sort_key(record: Dict[str, Any]) -> str:
    return str(
        record.get("workflow_id")
        or record.get("hook_id")
        or record.get("cron_id")
        or record.get("template_id")
        or record.get("id")
        or record.get("name")
        or ""
    )


# =====================================================================
# Domain Protocols
# =====================================================================

@runtime_checkable
class WorkflowHookCatalogReaderPort(Protocol):
    """Port for Workflows, Hooks, and Automation/Governance Catalog reads."""

    def list_workflow_templates(self) -> List[Dict[str, Any]]: ...

    def list_hook_registry(self) -> List[Dict[str, Any]]: ...

    def list_governance_permissions(self) -> List[Dict[str, Any]]: ...

    def list_memory_governance_rules(self) -> List[Dict[str, Any]]: ...

    def list_consult_rules(self) -> List[Dict[str, Any]]: ...

    def list_route_policies(self) -> List[Dict[str, Any]]: ...

    def list_alpha_factory_cards(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        lane: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def list_skills(self) -> List[Dict[str, Any]]: ...

    def list_tools(self) -> List[Dict[str, Any]]: ...

    def list_mcp_servers(self) -> List[Dict[str, Any]]: ...

    def list_mcp_tools(self) -> List[Dict[str, Any]]: ...

    def dataset_source(self, dataset: str) -> str: ...


@runtime_checkable
class OpenClawOperationsReaderPort(Protocol):
    """Port for OpenClaw Operations, snapshots, and broker adapter readiness."""

    def get_research_oss_preactivation_snapshot(
        self,
        *,
        activity_limit: int = 20,
    ) -> Dict[str, Any]: ...

    def get_openclaw_ops_snapshot(
        self,
        *,
        session_limit: int = 25,
        audit_limit: int = 20,
        operator_id: Optional[str] = None,
        state: Optional[str] = None,
        agent_id: Optional[str] = None,
        effective_tools_session_id: Optional[str] = None,
        requesting_operator_id: Optional[str] = None,
        effective_tools_mode: Optional[str] = None,
        requesting_operator_role: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def get_openclaw_broker_adapter_readiness(self) -> Dict[str, Any]: ...


@runtime_checkable
class ConsultationReaderPort(Protocol):
    """Port for Consultation sessions, transcripts, memos, requests, outcome and evidence."""

    def list_consultations_for_persona(
        self,
        persona_id: Optional[str],
        consultation_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Optional[List[Dict[str, Any]]]: ...

    def get_consultation(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]: ...

    def get_consultation_participants(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]: ...

    def get_consultation_outcome(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]: ...

    def get_consultation_evidence(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]: ...

    def get_consult_transcript(
        self,
        session_id: Optional[str],
        *,
        from_sequence_no: Optional[int] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]: ...

    def list_consult_requests(
        self,
        *,
        statuses: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        consultation_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...

    def get_consult_request(self, request_id: Optional[str]) -> Optional[Dict[str, Any]]: ...

    def create_consult_request(
        self,
        *,
        from_persona_id: str,
        target_type: str,
        target_ref: str,
        task: str,
        context_refs: List[Dict[str, str]],
        priority: str,
        consultation_type: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def cancel_consult_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        canceled_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]: ...

    def list_consult_memos(
        self,
        *,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]: ...

    def get_consult_memo(self, memo_id: Optional[str]) -> Optional[Dict[str, Any]]: ...

    def list_committees(
        self,
        *,
        quorum_states: Optional[List[str]] = None,
        consensus_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]: ...

    def get_committee(self, committee_id: Optional[str]) -> Optional[Dict[str, Any]]: ...

    def dataset_source(self, dataset: str) -> str: ...


@runtime_checkable
class OperationsConsultationPort(
    WorkflowHookCatalogReaderPort,
    OpenClawOperationsReaderPort,
    ConsultationReaderPort,
    Protocol,
):
    """Combined typed port for Operations, OpenClaw, Workflows/Catalog, and Consultation."""
    pass


# =====================================================================
# Concrete Domain Port Implementations
# =====================================================================

class DomainWorkflowCatalogPort:
    """Workflow templates, hook registry, and catalog provider."""

    def __init__(
        self,
        *,
        service_store: Optional[Any] = None,
        datasets: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._service = service_store
        self._datasets: Dict[str, Any] = datasets or {}

    def _resolve_dataset_records(self, dataset: str, env_var: Optional[str] = None) -> Tuple[str, List[Dict[str, Any]]]:
        if dataset in self._datasets:
            raw = self._datasets[dataset]
            if isinstance(raw, list):
                return "in_memory", [dict(item) for item in raw if isinstance(item, dict)]
            if isinstance(raw, dict):
                return "in_memory", [dict(item) for item in raw.values() if isinstance(item, dict)]
            return "in_memory", []

        if env_var:
            env_path = os.getenv(env_var, "").strip()
            if env_path and Path(env_path).exists():
                try:
                    payload = json.loads(Path(env_path).read_text(encoding="utf-8"))
                    if isinstance(payload, list):
                        return "service_store", [dict(item) for item in payload if isinstance(item, dict)]
                    if isinstance(payload, dict):
                        return "service_store", [dict(item) for item in payload.values() if isinstance(item, dict)]
                except Exception:
                    pass

        if self._service is not None:
            list_records = getattr(self._service, "list_records", None)
            if callable(list_records):
                available, records = list_records(dataset)
                if available:
                    return "service_store", [dict(r) for r in records if isinstance(r, dict)]

        return "missing", []

    def dataset_source(self, dataset: str) -> str:
        env_map = {
            "workflow_templates": "PANTHEON_BFF_WORKFLOW_TEMPLATE_STORE",
            "hook_registry": "PANTHEON_BFF_HOOK_REGISTRY_STORE",
        }
        source, _ = self._resolve_dataset_records(dataset, env_map.get(dataset))
        return source

    def list_workflow_templates(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("workflow_templates", "PANTHEON_BFF_WORKFLOW_TEMPLATE_STORE")
        return sorted(records, key=_automation_record_sort_key)

    def list_hook_registry(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("hook_registry", "PANTHEON_BFF_HOOK_REGISTRY_STORE")
        return sorted(records, key=_automation_record_sort_key)

    def list_governance_permissions(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("governance_permissions")
        return records

    def list_memory_governance_rules(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("memory_governance_rules")
        return records

    def list_consult_rules(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("consult_rules")
        return records

    def list_route_policies(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("route_policies")
        return records

    def list_alpha_factory_cards(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        lane: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("alpha_factory_cards")
        if lane:
            records = [r for r in records if str(r.get("lane") or "").lower() == lane.lower()]
        start = (page - 1) * page_size
        return records[start : start + page_size]

    def list_skills(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("skills")
        return records

    def list_tools(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("tools")
        return records

    def list_mcp_servers(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("mcp_servers")
        return records

    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        _, records = self._resolve_dataset_records("mcp_tools")
        return records


class DomainOpenClawOperationsPort:
    """OpenClaw operations reader and truthful error adapter."""

    def __init__(
        self,
        *,
        client: Optional[OpenClawOpsClient] = None,
        service_specs: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._client = client or OpenClawOpsClient()
        self._specs = service_specs or _DORMANT_SERVICE_SPECS

    def _openclaw_client(self) -> OpenClawOpsClient:
        return self._client

    @staticmethod
    def _openclaw_error_surface(exc: OpenClawOpsClientError) -> Dict[str, Any]:
        if hasattr(exc, "to_surface"):
            surface = exc.to_surface()
            return {key: value for key, value in surface.items() if value is not None}
        return {
            "status": "unavailable",
            "source": "service_client",
            "reason": getattr(exc, "error_code", "openclaw_client_error") or "openclaw_client_error",
            "message": str(exc),
        }

    def _fetch_openclaw_surface(
        self,
        surface_key: str,
        call: Callable[[], Any],
    ) -> Tuple[Dict[str, Any], Any]:
        try:
            payload = call()
        except OpenClawOpsClientError as exc:
            return self._openclaw_error_surface(exc), None
        except Exception as exc:  # pragma: no cover
            return {
                "status": "unavailable",
                "source": "service_client",
                "reason": "internal_error",
                "message": str(exc),
            }, None
        return {
            "status": "ok",
            "source": "service_client",
            "surface": surface_key,
        }, payload

    @staticmethod
    def _openclaw_gate_enabled(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value or "").strip().lower()
        return normalized in {"1", "true", "yes", "on", "enabled", "active", "available"}

    @classmethod
    def _project_openclaw_gate_state(cls, capabilities: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        activation_gates = capabilities.get("activation_gates")
        if not isinstance(activation_gates, dict):
            activation_gates = {}
        gates: Dict[str, Dict[str, Any]] = {}
        for gate_name, defaults in _OPENCLAW_GATE_FIELDS.items():
            raw_state = capabilities.get(gate_name)
            state = str(raw_state or "deferred").strip().lower()
            enabled = cls._openclaw_gate_enabled(raw_state)
            gate_reason = (
                "enabled_by_adapter"
                if enabled
                else f"{activation_gates.get(gate_name) or defaults['activation_gate']} is not enabled"
            )
            gates[gate_name] = {
                "state": state,
                "enabled": enabled,
                "activation_gate": activation_gates.get(gate_name) or defaults["activation_gate"],
                "allowed_scope": "enabled_by_adapter" if enabled else defaults["allowed_scope"],
                "gate_reason": gate_reason,
                "bff_activation_command": "not_exposed",
            }
        return gates

    @staticmethod
    def _project_openclaw_capabilities(capabilities: Dict[str, Any]) -> Dict[str, Any]:
        upstream = capabilities.get("upstream") if isinstance(capabilities.get("upstream"), dict) else {}
        return {
            "adapter_version": capabilities.get("adapter_version"),
            "activation_state": capabilities.get("activation_state") or "unknown",
            "session_lifecycle_state": capabilities.get("session_lifecycle_state") or "unknown",
            "fail_closed": bool(capabilities.get("fail_closed", True)),
            "supported_session_types": list(capabilities.get("supported_session_types") or []),
            "minimum_runtime_contract": capabilities.get("minimum_runtime_contract") or {},
            "upstream_status": upstream.get("status"),
            "upstream_error_code": upstream.get("error_code"),
        }

    @staticmethod
    def _project_openclaw_upstream(payload: Any, surface: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {
                "status": "unavailable",
                "reachable": False,
                "reason": surface.get("reason") or "upstream_status_unavailable",
                "details": surface,
            }
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        reachable = bool(payload.get("reachable"))
        reason = (
            details.get("reason")
            or payload.get("reason")
            or surface.get("reason")
            or (None if reachable else "upstream_unreachable")
        )
        return {
            "status": "ok" if reachable else "degraded",
            "upstream_url": payload.get("upstream_url"),
            "reachable": reachable,
            "reason": reason,
            "http_status": details.get("http_status"),
            "probe": details.get("probe"),
            "details": details,
        }

    @staticmethod
    def _project_openclaw_session(record: Dict[str, Any]) -> Dict[str, Any]:
        state = str(record.get("state") or record.get("status") or "unknown").strip().lower()
        audit_log = record.get("audit_log") if isinstance(record.get("audit_log"), list) else []
        context_bundle = record.get("context_bundle") if isinstance(record.get("context_bundle"), dict) else {}
        last_error = record.get("last_error") if isinstance(record.get("last_error"), dict) else None
        cancelable = state in {"pending", "active", "lost", "cancel_requested"}
        return {
            "session_id": record.get("session_id") or record.get("id"),
            "agent_id": record.get("agent_id"),
            "session_type": record.get("session_type"),
            "state": state,
            "operator_id": record.get("operator_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "upstream_session_id": record.get("upstream_session_id"),
            "degraded": state in {"lost", "failed"} or bool(last_error),
            "last_error": last_error,
            "audit_count": len(audit_log),
            "latest_audit_event": audit_log[-1] if audit_log else None,
            "context_keys": sorted(str(key) for key in context_bundle.keys()),
            "allowedActions": {
                "canCancel": cancelable,
                "canInvokeTool": False,
                "canTriggerWorkflow": False,
            },
        }

    @staticmethod
    def _project_openclaw_invocation(entry: Dict[str, Any]) -> Dict[str, Any]:
        error = entry.get("error") if isinstance(entry.get("error"), dict) else None
        return {
            "at": entry.get("at"),
            "request_type": entry.get("request_type"),
            "trace_id": entry.get("trace_id"),
            "operator_id": entry.get("operator_id"),
            "session_id": entry.get("session_id"),
            "tool_name": entry.get("tool_name"),
            "workflow_ref": entry.get("workflow_ref"),
            "policy_decision": entry.get("policy_decision"),
            "policy_class": entry.get("policy_class"),
            "policy_reason": entry.get("policy_reason"),
            "outcome": entry.get("outcome"),
            "retryable": bool(error.get("retryable")) if error else False,
            "error": (
                {
                    "error_code": error.get("error_code"),
                    "message": error.get("message"),
                    "status_code": error.get("status_code"),
                    "retryable": bool(error.get("retryable")),
                }
                if error
                else None
            ),
            "args_hash": entry.get("args_hash"),
            "context_hash": entry.get("context_hash"),
        }

    @staticmethod
    def _openclaw_counts_by_key(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in rows:
            value = str(row.get(key) or "unknown")
            counts[value] = counts.get(value, 0) + 1
        return counts

    def get_openclaw_ops_snapshot(
        self,
        *,
        session_limit: int = 25,
        audit_limit: int = 20,
        operator_id: Optional[str] = None,
        state: Optional[str] = None,
        agent_id: Optional[str] = None,
        effective_tools_session_id: Optional[str] = None,
        requesting_operator_id: Optional[str] = None,
        effective_tools_mode: Optional[str] = None,
        requesting_operator_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        bounded_session_limit = max(min(session_limit, 100), 1)
        bounded_audit_limit = max(min(audit_limit, 100), 1)
        client = self._openclaw_client()

        service_status: Dict[str, Dict[str, Any]] = {}

        cap_surface, cap_payload = self._fetch_openclaw_surface(
            "openclaw_capabilities",
            client.get_capabilities,
        )
        service_status["openclaw_capabilities"] = cap_surface
        capabilities = cap_payload if isinstance(cap_payload, dict) else {}

        upstream_surface, upstream_payload = self._fetch_openclaw_surface(
            "openclaw_upstream_status",
            client.get_upstream_status,
        )
        service_status["openclaw_upstream_status"] = upstream_surface

        session_surface, session_payload = self._fetch_openclaw_surface(
            "openclaw_session_lifecycle",
            lambda: client.list_lifecycle_sessions(operator_id=operator_id, state=state),
        )
        service_status["openclaw_session_lifecycle"] = session_surface

        policy_surface, policy_payload = self._fetch_openclaw_surface(
            "openclaw_tool_policy",
            client.get_tool_policy,
        )
        service_status["openclaw_tool_policy"] = policy_surface

        audit_surface, audit_payload = self._fetch_openclaw_surface(
            "openclaw_invocation_audit",
            lambda: client.list_invocation_audit(
                operator_id=operator_id,
                limit=bounded_audit_limit,
            ),
        )
        service_status["openclaw_invocation_audit"] = audit_surface

        effective_tools: Optional[Dict[str, Any]] = None
        if agent_id and requesting_operator_id:
            tools_surface, tools_payload = self._fetch_openclaw_surface(
                "openclaw_effective_tools",
                lambda: client.list_effective_tools(
                    agent_id=agent_id,
                    operator_id=requesting_operator_id,
                    session_id=effective_tools_session_id,
                    mode=effective_tools_mode,
                    operator_role=requesting_operator_role,
                ),
            )
            service_status["openclaw_effective_tools"] = tools_surface
            effective_tools = tools_payload if isinstance(tools_payload, dict) else None

        raw_sessions = []
        if isinstance(session_payload, dict) and isinstance(session_payload.get("sessions"), list):
            raw_sessions = [
                dict(item)
                for item in session_payload["sessions"]
                if isinstance(item, dict)
            ]
        sessions = [
            self._project_openclaw_session(item)
            for item in raw_sessions[:bounded_session_limit]
        ]

        raw_invocations = []
        if isinstance(audit_payload, dict) and isinstance(audit_payload.get("entries"), list):
            raw_invocations = [
                dict(item)
                for item in audit_payload["entries"]
                if isinstance(item, dict)
            ]
        invocations = [self._project_openclaw_invocation(item) for item in raw_invocations]

        session_counts = self._openclaw_counts_by_key(sessions, "state")
        invocation_outcomes = self._openclaw_counts_by_key(invocations, "outcome")
        policy_decisions = self._openclaw_counts_by_key(invocations, "policy_decision")

        upstream = self._project_openclaw_upstream(upstream_payload, upstream_surface)
        gate_state = self._project_openclaw_gate_state(capabilities)

        degraded_reasons: List[str] = []
        for surface_key, surface in service_status.items():
            if surface.get("status") == "ok":
                continue
            reason = str(surface.get("reason") or surface.get("message") or surface_key)
            degraded_reasons.append(f"{surface_key}:{reason}")
        if not upstream.get("reachable"):
            degraded_reasons.append(f"openclaw_upstream:{upstream.get('reason') or 'unreachable'}")
        cap_upstream = capabilities.get("upstream") if isinstance(capabilities.get("upstream"), dict) else {}
        if cap_upstream.get("status") == "degraded":
            degraded_reasons.append(
                f"openclaw_capabilities_upstream:{cap_upstream.get('error_code') or 'degraded'}"
            )
        for session in sessions:
            if session.get("degraded"):
                last_error = session.get("last_error") if isinstance(session.get("last_error"), dict) else {}
                degraded_reasons.append(
                    "openclaw_session:"
                    f"{session.get('session_id')}:{last_error.get('error_code') or session.get('state')}"
                )

        unique_reasons = sorted({reason for reason in degraded_reasons if reason})
        surface_states = [surface.get("status") for surface in service_status.values()]
        if surface_states and all(state == "unavailable" for state in surface_states):
            overall_status = "unavailable"
        elif any(state != "ok" for state in surface_states) or unique_reasons:
            overall_status = "degraded"
        else:
            overall_status = "ok"

        return {
            "surface": "openclaw_ops",
            "surface_aliases": ["openclaw_tool_workflow_bridge"],
            "overall_status": overall_status,
            "activation": self._project_openclaw_capabilities(capabilities),
            "gate_state": gate_state,
            "production_activation": "disabled",
            "upstream": upstream,
            "session_lifecycle": {
                "status": session_surface.get("status"),
                "count": len(sessions),
                "state_counts": session_counts,
                "sessions": sessions,
                "degraded_session_count": len([s for s in sessions if s.get("degraded")]),
                "filters": {"operator_id": operator_id, "state": state},
            },
            "tool_workflow": {
                "policy": policy_payload if isinstance(policy_payload, dict) else {},
                "effective_tools": effective_tools,
                "audit": {
                    "status": audit_surface.get("status"),
                    "count": len(invocations),
                    "outcome_counts": invocation_outcomes,
                    "policy_decision_counts": policy_decisions,
                    "entries": invocations,
                },
                "bridge_posture": {
                    "policy_state": (
                        "adapter_enforcing"
                        if policy_surface.get("status") == "ok"
                        else "degraded"
                    ),
                    "unknown_tools": "fail_closed",
                    "disallowed_tools": "fail_closed",
                    "workflow_triggers": "adapter_policy_checked",
                    "bff_tool_invocation_commands": "not_exposed",
                    "bff_workflow_trigger_commands": "not_exposed",
                },
            },
            "operator_controls": {
                "read_operations": [
                    "upstream_status",
                    "capability_inventory",
                    "session_lifecycle",
                    "tool_policy",
                    "tool_workflow_audit",
                    "degraded_reason",
                ],
                "commands": {
                    "create_session": {
                        "endpoint": "POST /api/v1/operator/openclaw/sessions",
                        "requires_auth": True,
                        "requires_idempotency_key": True,
                        "adapter_route": "POST /api/openclaw-adapter/lifecycle/sessions",
                    },
                    "cancel_session": {
                        "endpoint": "POST /api/v1/operator/openclaw/sessions/{session_id}/cancel",
                        "requires_auth": True,
                        "requires_idempotency_key": True,
                        "adapter_route": "POST /api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel",
                    },
                    "invoke_tool": "not_exposed_by_bff",
                    "trigger_workflow": "not_exposed_by_bff",
                },
                "blocked_commands": {
                    "enable_paper_adapter": "activation_gate_required_not_available_in_bff",
                    "enable_canary_adapter": "activation_gate_required_not_available_in_bff",
                    "enable_live_adapter": "activation_gate_required_not_available_in_bff",
                    "enable_broker_execution": "execution_gate_required_not_available_in_bff",
                    "enable_capital_binding": "capital_binding_gate_required_not_available_in_bff",
                },
            },
            "allowedActions": {
                "canCreateSession": getattr(client, "configured", False) and session_surface.get("status") != "unavailable",
                "canInvokeTool": False,
                "canTriggerWorkflow": False,
                "canEnablePaper": False,
                "canEnableCanary": False,
                "canEnableLive": False,
            },
            "degradation": {
                "status": overall_status,
                "reasons": unique_reasons,
            },
            "service_status": service_status,
        }

    def get_openclaw_broker_adapter_readiness(self) -> Dict[str, Any]:
        client = self._openclaw_client()
        surface, payload = self._fetch_openclaw_surface(
            "openclaw_broker_capabilities",
            client.get_broker_capabilities,
        )
        if not isinstance(payload, dict):
            return {
                "surface": "openclaw_broker_adapter_readiness",
                "overall_status": "unavailable",
                "sandbox_adapter_state": "unknown",
                "paper_adapter_state": "unknown",
                "canary_adapter_state": "fail_closed",
                "live_adapter_state": "fail_closed",
                "live_execution_enabled": False,
                "canary_execution_enabled": False,
                "is_real_capital": False,
                "is_real_order": False,
                "gate_reason": surface.get("reason") or "broker_capabilities_unavailable",
                "service_status": surface,
            }

        def _state(key: str, default: str = "deferred") -> str:
            return str(payload.get(key) or default).strip().lower()

        def _gate_reason(state: str, gate_env: str, fail_closed: bool = False) -> str:
            if fail_closed:
                return "fail_closed_explicit_gate_required"
            if state in {"enabled", "active"}:
                return "enabled_by_adapter"
            return f"{gate_env} is not enabled"

        sandbox_state = _state("sandbox_adapter_state", "activation_ready")
        paper_state = _state("paper_adapter_state", "gated")
        canary_state = "fail_closed"
        live_state = "fail_closed"

        return {
            "surface": "openclaw_broker_adapter_readiness",
            "overall_status": "ok" if surface.get("status") == "ok" else surface.get("status", "degraded"),
            "sandbox_adapter_state": sandbox_state,
            "sandbox_gate": payload.get("sandbox_gate") or "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "sandbox_gate_reason": _gate_reason(sandbox_state, str(payload.get("sandbox_gate") or "")),
            "paper_adapter_state": paper_state,
            "paper_adapter_gate": payload.get("paper_adapter_gate") or "OPENCLAW_PAPER_ADAPTER_ENABLED",
            "paper_gate_reason": _gate_reason(paper_state, str(payload.get("paper_adapter_gate") or "")),
            "canary_adapter_state": canary_state,
            "canary_adapter_gate": payload.get("canary_adapter_gate") or "OPENCLAW_CANARY_ADAPTER_ENABLED",
            "canary_gate_reason": _gate_reason(canary_state, "", fail_closed=True),
            "live_adapter_state": live_state,
            "live_adapter_gate": payload.get("live_adapter_gate") or "OPENCLAW_LIVE_ADAPTER_ENABLED",
            "live_gate_reason": _gate_reason(live_state, "", fail_closed=True),
            "live_execution_enabled": False,
            "canary_execution_enabled": False,
            "is_real_capital": False,
            "is_real_order": False,
            "broker_sidecar_configured": bool(payload.get("broker_sidecar_configured")),
            "runtime_manager_configured": bool(payload.get("runtime_manager_configured")),
            "bff_activation_command": "not_exposed",
            "note": (
                "Live and canary broker execution remain fail-closed. "
                "Sandbox/paper state reflects adapter configuration only. "
                "Activation requires explicit gate enablement outside the BFF."
            ),
            "service_status": surface,
        }

    def _dormant_service_base_url(self, service: str) -> Optional[str]:
        spec = self._specs.get(service)
        if not spec:
            return None
        env_val = os.getenv(spec["env"], "").strip()
        if env_val:
            return env_val.rstrip("/")
        return None

    def _fetch_dormant_json(self, service: str, path_key: str) -> Tuple[Dict[str, Any], Any]:
        spec = self._specs.get(service)
        if not spec:
            return {"status": "unavailable", "source": "service_client", "reason": "unknown_service"}, None
        base_url = self._dormant_service_base_url(service)
        path = str(spec.get(path_key) or "")
        if not base_url or not path:
            return {"status": "unavailable", "source": "service_client", "reason": "service_url_not_configured"}, None

        url = f"{base_url}/{path.lstrip('/')}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                text = resp.read().decode("utf-8").strip()
                payload = json.loads(text) if text else None
                return {"status": "ok", "source": "service_client", "path": path}, payload
        except urllib.error.HTTPError as exc:
            return {"status": "degraded", "source": "service_client", "reason": f"http_{exc.code}", "path": path}, None
        except Exception as exc:
            return {"status": "unavailable", "source": "service_client", "reason": "service_unavailable", "message": str(exc)}, None

    def _project_dormant_capabilities(
        self,
        service: str,
        payload: Any,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        if not isinstance(payload, dict):
            return [], []
        if service == "openclaw_gateway_adapter":
            fail_closed = bool(payload.get("fail_closed"))
            return [
                {
                    "service": "openclaw_gateway_adapter",
                    "backend": "openclaw",
                    "status": "deferred",
                    "gate_state": "fail_closed" if fail_closed else "unknown",
                    "allowed_scope": "capability_metadata_read_only",
                    "activation_state": payload.get("activation_state"),
                    "broker_execution": payload.get("broker_execution"),
                    "paper_adapter": payload.get("paper_adapter"),
                    "live_adapter": payload.get("live_adapter"),
                    "capital_binding": payload.get("capital_binding"),
                    "fail_closed": fail_closed,
                }
            ], []

        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, list):
            capabilities = []

        dormant: List[Dict[str, Any]] = []
        safe_dispatchers: List[str] = []
        for item in capabilities:
            if not isinstance(item, dict):
                continue
            backend = str(item.get("backend") or item.get("framework") or item.get("id") or "").strip().lower()
            if not backend:
                continue
            if backend in _DORMANT_SAFE_DISPATCHERS and str(item.get("status") or "").lower() == "available":
                safe_dispatchers.append(backend)
            if backend not in _DORMANT_OSS_BACKENDS:
                continue
            dormant.append(
                {
                    "service": service,
                    "backend": backend,
                    "status": item.get("status"),
                    "gate_state": item.get("gate_state") or "unknown",
                    "allowed_scope": item.get("allowed_scope") or "unknown",
                    "offline_gate": payload.get("offline_gate"),
                    "offline_dispatch": item.get("offline_dispatch"),
                    "gateway_routing": item.get("gateway_routing"),
                    "activation_gate": item.get("activation_gate"),
                    "entrypoint": item.get("entrypoint"),
                    "purpose": item.get("purpose"),
                    "note": item.get("note"),
                }
            )
        return dormant, sorted(set(safe_dispatchers))

    def _project_dormant_activity(
        self,
        service: str,
        payload: Any,
    ) -> List[Dict[str, Any]]:
        if isinstance(payload, dict):
            records = payload.get("items") or payload.get("data") or payload.get("runs") or payload.get("jobs") or []
        else:
            records = payload
        if not isinstance(records, list):
            return []

        spec = self._specs.get(service, {})
        actor_field = str(spec.get("actor_field") or "adapter")
        activity_kind = str(spec.get("activity_kind") or "activity")
        projected: List[Dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            actor = str(record.get(actor_field) or "").strip().lower()
            rejection = record.get("rejection") if isinstance(record.get("rejection"), dict) else {}
            reason = str(rejection.get("reason") or "").strip()
            status = str(record.get("status") or "").strip().lower()
            projected.append(
                {
                    "service": service,
                    "object_type": activity_kind,
                    "object_id": record.get("run_id") or record.get("dispatch_id") or record.get("invocation_id") or record.get("id"),
                    "backend": actor,
                    "status": status,
                    "requested_mode": record.get("requested_mode"),
                    "dispatch_mode": record.get("dispatch_mode"),
                    "production_activation": record.get("production_activation") or "disabled",
                    "rejection_reason": reason or None,
                    "fail_closed_rejection": (
                        status == "rejected"
                        and reason in _DORMANT_FAIL_CLOSED_REASONS
                    ),
                    "gateway_ref": record.get("gateway_ref") if isinstance(record.get("gateway_ref"), dict) else None,
                    "artifact_refs": list(record.get("artifact_refs") or []),
                    "proposal_refs": list(record.get("proposal_refs") or []),
                    "logs": list(record.get("logs") or []),
                    "error_summary": {
                        "has_error": status in {"failed", "error", "rejected"},
                        "error_count": 1 if status in {"failed", "error", "rejected"} else 0,
                    },
                    "exit_code": record.get("exit_code"),
                    "updated_at": record.get("updated_at") or record.get("created_at") or _utc_now_rfc3339(),
                }
            )
        return projected

    def get_research_oss_preactivation_snapshot(
        self,
        *,
        activity_limit: int = 20,
    ) -> Dict[str, Any]:
        service_status: Dict[str, Dict[str, Any]] = {}
        capability_entries: List[Dict[str, Any]] = []
        activity: List[Dict[str, Any]] = []
        safe_dispatch: Dict[str, List[str]] = {}

        for service, spec in self._specs.items():
            cap_surface, cap_payload = self._fetch_dormant_json(service, "capabilities_path")
            service_status[service] = dict(cap_surface)
            entries, dispatchers = self._project_dormant_capabilities(service, cap_payload)
            capability_entries.extend(entries)
            if dispatchers:
                safe_dispatch[service] = dispatchers

            activity_path = spec.get("activity_path")
            if activity_path:
                activity_surface, activity_payload = self._fetch_dormant_json(service, "activity_path")
                service_status[service]["activity_status"] = activity_surface.get("status")
                activity.extend(self._project_dormant_activity(service, activity_payload))

            upstream_status_path = spec.get("upstream_status_path")
            if upstream_status_path:
                upstream_surface, upstream_payload = self._fetch_dormant_json(service, "upstream_status_path")
                service_status[service]["upstream_status"] = upstream_surface.get("status")
                if isinstance(upstream_payload, dict):
                    service_status[service]["upstream_reachable"] = bool(upstream_payload.get("reachable"))

        backend_inventory: List[Dict[str, Any]] = []
        entries_by_backend: Dict[str, List[Dict[str, Any]]] = {
            backend: [] for backend in _DORMANT_OSS_BACKENDS
        }
        for entry in capability_entries:
            backend = str(entry.get("backend") or "").strip().lower()
            if backend in entries_by_backend:
                entries_by_backend[backend].append(entry)

        offline_gate_observed = False
        for backend in _DORMANT_OSS_BACKENDS:
            entries = entries_by_backend[backend]
            observed_gate_states = {
                str(entry.get("gate_state") or "unknown").strip().lower()
                for entry in entries
            }
            observed_scopes = {
                str(entry.get("allowed_scope") or "unknown").strip().lower()
                for entry in entries
            }
            backend_offline_ready = (
                "activation_ready" in observed_gate_states
                or _DORMANT_OFFLINE_SCOPE in observed_scopes
                or any(str(entry.get("offline_dispatch") or "").lower() == "enabled" for entry in entries)
                or any(str(entry.get("gateway_routing") or "").lower() == "enabled" for entry in entries)
            )
            offline_gate_observed = offline_gate_observed or backend_offline_ready
            if backend_offline_ready:
                gate_state = "activation_ready"
                allowed_scope = _DORMANT_OFFLINE_SCOPE
                offline_dispatch = "enabled"
            elif entries and observed_gate_states == {"fail_closed"}:
                gate_state = "fail_closed"
                allowed_scope = (
                    "capability_metadata_read_only"
                    if observed_scopes == {"capability_metadata_read_only"}
                    else "mixed"
                )
                offline_dispatch = "disabled"
            elif entries:
                gate_state = "mixed" if len(observed_gate_states) > 1 else next(iter(observed_gate_states))
                allowed_scope = "mixed" if len(observed_scopes) > 1 else next(iter(observed_scopes))
                offline_dispatch = "disabled"
            else:
                gate_state = "unknown"
                allowed_scope = "unknown"
                offline_dispatch = "disabled"
            backend_inventory.append(
                {
                    "backend": backend,
                    "activated": False,
                    "activation_state": (
                        "offline_activation_ready"
                        if backend_offline_ready
                        else "preactivation_only"
                    ),
                    "production_activation": "disabled",
                    "gate_state": gate_state,
                    "allowed_scope": allowed_scope,
                    "offline_dispatch": offline_dispatch,
                    "service_count": len(entries),
                    "services": {str(entry["service"]): entry for entry in entries},
                }
            )

        activity.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        activity = activity[: max(min(activity_limit, 100), 1)]

        rejected_activity = [
            row for row in activity if str(row.get("status") or "").lower() == "rejected"
        ]
        failed_activity = [
            row for row in activity if str(row.get("status") or "").lower() in {"failed", "error"}
        ]
        activation_state = (
            "offline_activation_ready"
            if offline_gate_observed
            else "preactivation_only"
        )

        return {
            "surface": "research_oss_activation_ready",
            "surface_aliases": ["research_oss_preactivation"],
            "activation_state": activation_state,
            "production_activation": "disabled",
            "activated": False,
            "offline_gate": "enabled" if offline_gate_observed else "disabled",
            "allowed_scope": (
                _DORMANT_OFFLINE_SCOPE
                if offline_gate_observed
                else "capability_metadata_read_only"
            ),
            "write_paths": {
                "training_dispatch": "disabled",
                "paper_canary_live": "disabled",
                "registry_writes": "disabled",
                "governance_writes": "disabled",
                "broker_execution": "disabled",
                "capital_binding": "disabled",
            },
            "backend_inventory": backend_inventory,
            "safe_dispatch": safe_dispatch,
            "run_history": activity,
            "activity": activity,
            "error_summary": {
                "activity_with_errors": len([r for r in activity if r.get("status") in {"failed", "error", "rejected"}]),
                "rejection_count": len(rejected_activity),
                "failed_count": len(failed_activity),
            },
            "service_status": service_status,
        }


class DomainConsultationPort:
    """Consultation reader and mutation adapter directly backed by ConsultationServiceClient/Store."""

    def __init__(
        self,
        *,
        client: Optional[ConsultationServiceClient] = None,
        store: Optional[ConsultationStore] = None,
        data_dir: Optional[str] = None,
        persona_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self._client_instance = client
        self._store_instance = store
        self._data_dir = data_dir
        self._persona_provider = persona_provider

    def _resolve_data_dir(self) -> Optional[Path]:
        if self._data_dir:
            return Path(self._data_dir)
        for env_name in _CONSULTATION_DATA_DIR_ENVS:
            raw = os.getenv(env_name, "").strip()
            if raw:
                return Path(raw)
        return None

    def _consultation_store(self) -> Optional[ConsultationStore]:
        if self._store_instance is not None:
            return self._store_instance
        data_dir = self._resolve_data_dir()
        if data_dir is None:
            return None
        if build_consultation_store is not None:
            try:
                return build_consultation_store(str(data_dir))
            except Exception:
                pass
        if ConsultationStore is not None:
            return ConsultationStore(str(data_dir))
        return None

    def _consultation_client(self) -> Optional[ConsultationServiceClient]:
        if self._client_instance is not None:
            return self._client_instance
        if ConsultationServiceClient is not None and ConsultationServiceClient.configured():
            timeout = float(os.getenv("PANTHEON_CONSULTATION_API_TIMEOUT_SECONDS", "10"))
            return ConsultationServiceClient(timeout_seconds=timeout)
        return None

    def dataset_source(self, dataset: str) -> str:
        if self._consultation_client() is not None:
            return "service_client"
        if self._consultation_store() is not None:
            return "service_store"
        return "missing"

    @staticmethod
    def _service_context_refs_to_bff(req: Dict[str, Any]) -> List[Dict[str, str]]:
        metadata = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
        bff_refs = metadata.get("bff_context_refs")
        if isinstance(bff_refs, list):
            return [
                {"type": str(item.get("type") or ""), "id": str(item.get("id") or "")}
                for item in bff_refs
                if isinstance(item, dict) and item.get("type") and item.get("id")
            ]
        refs: List[Dict[str, str]] = []
        for raw_ref in req.get("context_refs") or []:
            text = str(raw_ref or "")
            if ":" not in text:
                continue
            ref_type, ref_id = text.split(":", 1)
            if ref_type and ref_id:
                refs.append({"type": ref_type, "id": ref_id})
        return refs

    @staticmethod
    def _service_request_status(req: Dict[str, Any]) -> str:
        status = str(req.get("status") or "").strip().lower()
        return _SERVICE_TO_BFF_REQUEST_STATUS.get(status, status or "created")

    @classmethod
    def _project_service_request_record(cls, req: Dict[str, Any]) -> Dict[str, Any]:
        metadata = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
        bff_priority = metadata.get("bff_priority") or req.get("priority")
        consultation_type = req.get("consultation_type") or metadata.get("consultation_type") or req.get("request_type")
        status = cls._service_request_status(req)
        request_to_session_status = (
            req.get("request_to_session_status")
            or metadata.get("request_to_session_status")
            or ("session_completed" if status == "completed" else "pending_session")
        )
        return {
            "request_id": req.get("request_id"),
            "status": status,
            "from_persona_id": req.get("from_persona_id"),
            "target_type": req.get("target_type"),
            "target_ref": req.get("target_ref") or req.get("target_id"),
            "task": req.get("task") or metadata.get("task") or "",
            "context_refs": cls._service_context_refs_to_bff(req),
            "priority": bff_priority,
            "consultation_type": consultation_type,
            "created_at": req.get("created_at"),
            "completed_at": req.get("completed_at"),
            "canceled_at": req.get("canceled_at"),
            "linked_session_id": req.get("linked_session_id") or metadata.get("linked_session_id"),
            "request_to_session_status": request_to_session_status,
            "session_handoff_note": (
                req.get("session_handoff_note")
                or metadata.get("session_handoff_note")
                or "Request is served from the consultation service lifecycle store."
            ),
            "created_by": (req.get("requested_by") or {}).get("actor_id"),
            "service_request_type": req.get("request_type"),
            "service_trace_id": req.get("trace_id"),
            "service_evidence_refs": list(req.get("evidence_refs") or []),
        }

    @staticmethod
    def _service_evidence_ref(ref_id: str) -> Dict[str, Any]:
        return {
            "id": ref_id,
            "type": "evidence_link",
            "evidence_type": "consultation_evidence",
            "artifact_ref": ref_id,
            "description": ref_id,
            "link": f"/evidence/{ref_id}",
        }

    @classmethod
    def _project_service_session_records_from_data(
        cls,
        request_records: List[Dict[str, Any]],
        handoff_records: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        sessions: Dict[str, Dict[str, Any]] = {}
        handoffs_by_request: Dict[str, List[Dict[str, Any]]] = {}
        for handoff in handoff_records:
            handoffs_by_request.setdefault(str(handoff.get("request_id") or ""), []).append(handoff)

        for req in request_records:
            metadata = req.get("metadata") if isinstance(req.get("metadata"), dict) else {}
            consult = dict(metadata.get("consultation") or {})
            request_id = str(req.get("request_id") or "")
            linked_session_id = (
                req.get("linked_session_id")
                or consult.get("requester_session_id")
                or consult.get("root_session_id")
                or request_id
            )
            service_handoffs = sorted(
                handoffs_by_request.get(request_id, []),
                key=lambda item: str(item.get("created_at") or ""),
                reverse=True,
            )
            latest_handoff = service_handoffs[0] if service_handoffs else None

            evidence_refs = consult.get("evidence_refs")
            if not evidence_refs:
                evidence_refs = [
                    cls._service_evidence_ref(str(ref_id))
                    for ref_id in req.get("evidence_refs") or []
                    if str(ref_id or "").strip()
                ]
            consult.setdefault("consultation_type", req.get("consultation_type") or req.get("request_type"))
            consult.setdefault("requester_session_id", linked_session_id)
            consult.setdefault("responder_session_ids", [])
            consult.setdefault("committee_session_ids", [])
            consult.setdefault("outcome", cls._service_request_status(req))
            consult.setdefault("evidence_refs", evidence_refs)
            consult.setdefault(
                "synthesis_summary",
                {
                    "outcome": consult.get("outcome"),
                    "rationale_ref": consult.get("rationale_ref"),
                    "evidence_refs": [
                        item.get("id") if isinstance(item, dict) else item
                        for item in evidence_refs
                    ],
                },
            )
            if latest_handoff:
                consult["service_handoff"] = {
                    "handoff_id": latest_handoff.get("handoff_id"),
                    "target_gate": latest_handoff.get("target_gate"),
                    "evidence_refs": list(latest_handoff.get("evidence_refs") or []),
                    "audit_refs": list(latest_handoff.get("audit_refs") or []),
                    "status": latest_handoff.get("status"),
                }

            sessions[linked_session_id] = {
                "id": linked_session_id,
                "session_id": linked_session_id,
                "persona_id": req.get("from_persona_id") or (req.get("requested_by") or {}).get("actor_id"),
                "session_type": "consult",
                "status": _SERVICE_TO_SESSION_STATUS.get(str(req.get("status") or ""), "active"),
                "started_at": req.get("created_at"),
                "ended_at": req.get("completed_at") or req.get("canceled_at"),
                "capability_snapshot_id": consult.get("capability_snapshot_id"),
                "trace_id": req.get("trace_id"),
                "request_id": request_id,
                "context_bundle_ref": consult.get("context_bundle_ref"),
                "task_ref": req.get("task"),
                "runtime_binding_id": consult.get("runtime_binding_id"),
                "deployment_stage": consult.get("deployment_stage"),
                "capital_pool_id": consult.get("capital_pool_id"),
                "metadata": {"consultation": consult},
            }

            for participant in consult.get("committee_participants") or []:
                if not isinstance(participant, dict):
                    continue
                session_id = str(participant.get("session_id") or participant.get("participant_id") or "").strip()
                if not session_id:
                    continue
                sessions[session_id] = {
                    "id": session_id,
                    "session_id": session_id,
                    "persona_id": participant.get("persona_id") or participant.get("participant_ref"),
                    "session_type": "committee",
                    "status": participant.get("status") or "active",
                    "started_at": participant.get("started_at") or req.get("created_at"),
                    "ended_at": participant.get("ended_at"),
                    "capability_snapshot_id": participant.get("capability_snapshot_id"),
                    "trace_id": participant.get("trace_id") or req.get("trace_id"),
                    "request_id": request_id,
                    "context_bundle_ref": participant.get("context_bundle_ref") or consult.get("context_bundle_ref"),
                    "task_ref": req.get("task"),
                    "runtime_binding_id": participant.get("runtime_binding_id") or consult.get("runtime_binding_id"),
                    "deployment_stage": participant.get("deployment_stage") or consult.get("deployment_stage"),
                    "capital_pool_id": participant.get("capital_pool_id") or consult.get("capital_pool_id"),
                    "metadata": {
                        "consultation": {
                            "consultation_type": consult.get("consultation_type"),
                            "root_session_id": linked_session_id,
                            "committee_ref": consult.get("committee_ref"),
                            "participant_status": participant.get("participant_status") or participant.get("status"),
                            "outcome_signal": participant.get("outcome_signal"),
                            "role": participant.get("role") or "committee_participant",
                            "rationale_ref": participant.get("rationale_ref"),
                        }
                    },
                }

        return list(sessions.values())

    @staticmethod
    def _project_service_transcript_record(transcript: Dict[str, Any]) -> Dict[str, Any]:
        transcript_id = str(transcript.get("transcript_id") or f"tr-{transcript.get('session_id')}")
        events: List[Dict[str, Any]] = []
        for event in transcript.get("events") or []:
            if not isinstance(event, dict):
                continue
            actor = event.get("actor") if isinstance(event.get("actor"), dict) else {}
            content = event.get("content") if isinstance(event.get("content"), dict) else {}
            events.append(
                {
                    "transcript_id": transcript_id,
                    "session_id": event.get("session_id") or transcript.get("session_id"),
                    "event_id": event.get("event_id"),
                    "sequence_no": event.get("sequence_no"),
                    "parent_event_id": event.get("parent_event_id"),
                    "event_type": event.get("event_type"),
                    "event_time": event.get("event_time"),
                    "ingest_time": event.get("ingest_time") or event.get("event_time"),
                    "actor": {
                        "actor_type": actor.get("actor_type"),
                        "actor_id": actor.get("actor_id"),
                        "display_name": actor.get("display_name"),
                        "role": actor.get("role") or content.get("actor_role"),
                    },
                    "content": {
                        "format": content.get("format") or "json",
                        "text": content.get("text"),
                        **{key: value for key, value in content.items() if key not in {"format", "text"}},
                    },
                    "evidence_refs": list(event.get("evidence_refs") or []),
                    "visibility": event.get("visibility") or "committee",
                    "redaction": event.get("redaction") or {"is_redacted": False, "reason": None},
                    "meta": event.get("meta") or {"source": "consultation-service", "hash": None},
                }
            )
        return {
            "transcript_id": transcript_id,
            "session_id": transcript.get("session_id") or transcript.get("request_id"),
            "linked_request_id": transcript.get("request_id") or transcript.get("linked_request_id"),
            "events": events,
        }

    def _project_service_memo_record(
        self,
        memo: Dict[str, Any],
        request_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        memo_id = str(memo.get("memo_id") or "")
        request_id = str(memo.get("request_id") or "")
        request = (request_lookup or {}).get(request_id)
        if request is None:
            client = self._consultation_client()
            if client is not None and request_id:
                try:
                    request = client.get_request(request_id)
                except Exception:
                    request = None
        if request is None:
            store = self._consultation_store()
            if store is not None and request_id:
                found = store.get_request(request_id)
                request = _model_to_data(found) if found else None

        request_metadata = request.get("metadata") if isinstance((request or {}).get("metadata"), dict) else {}
        consult = request_metadata.get("consultation") if isinstance(request_metadata.get("consultation"), dict) else {}
        linked_session_id = (
            (request or {}).get("linked_session_id")
            or consult.get("requester_session_id")
            or request_id
        )
        findings = [item for item in memo.get("findings") or [] if isinstance(item, dict)]
        evidence_ref_ids = []
        for finding in findings:
            evidence_ref_ids.extend(str(ref_id) for ref_id in finding.get("evidence_refs") or [] if str(ref_id or "").strip())
        evidence_ref_ids = list(dict.fromkeys(evidence_ref_ids))
        recommendation = memo.get("recommendation")
        recommendations = [
            finding.get("recommendation")
            for finding in findings
            if finding.get("recommendation")
        ] or ([recommendation] if recommendation else [])
        return {
            "memo_id": memo_id,
            "memo_type": "red_team" if memo.get("memo_type") == "redteam_report" else memo.get("memo_type"),
            "status": str(memo.get("status") or "").lower(),
            "lifecycle_state": str(memo.get("status") or "").lower(),
            "author_ref": memo.get("author_ref"),
            "linked_request_id": request_id,
            "linked_session_id": linked_session_id,
            "session_to_memo_mapping": {
                "mapping_id": f"map-{memo_id}" if memo_id else None,
                "source_session_id": linked_session_id,
                "transcript_id": f"tr-{linked_session_id}" if linked_session_id else None,
                "transcript_version": None,
                "memo_id": memo_id,
                "memo_type": "red_team" if memo.get("memo_type") == "redteam_report" else memo.get("memo_type"),
                "created_by": {
                    "actor_type": memo.get("author_type"),
                    "actor_id": memo.get("author_ref"),
                },
                "evidence_refs": evidence_ref_ids,
                "mapping_status": "active",
                "created_at": memo.get("created_at"),
            },
            "summary": memo.get("summary"),
            "recommendations": recommendations,
            "evidence_refs": [self._service_evidence_ref(ref_id) for ref_id in evidence_ref_ids],
            "published_at": memo.get("published_at"),
            "created_at": memo.get("created_at"),
            "supersedes_memo_id": None,
            "superseded_by_memo_id": None,
            "surface_state": "ok",
            "governance_target": {
                "target_type": memo.get("target_type"),
                "target_id": memo.get("target_id"),
                "deployment_plan_id": memo.get("target_id") if memo.get("target_type") == "deployment_plan" else None,
                "artifact_id": memo.get("target_id") if memo.get("target_type") == "artifact" else None,
                "strategy_id": memo.get("target_id") if memo.get("target_type") == "strategy" else None,
            },
            "suppressed": False,
            "withdrawn": False,
            "active_governance_review_id": None,
        }

    def _consultation_session_records(self) -> Dict[str, Dict[str, Any]]:
        client = self._consultation_client()
        if client is not None:
            try:
                records = self._project_service_session_records_from_data(
                    client.list_requests(),
                    client.list_handoffs(),
                )
                return {
                    str(s.get("session_id") or s.get("id")): s
                    for s in records
                    if str(s.get("session_id") or s.get("id") or "").strip()
                }
            except Exception:
                pass

        store = self._consultation_store()
        if store is not None:
            records = self._project_service_session_records_from_data(
                [_model_to_data(r) for r in store.list_requests()],
                [_model_to_data(h) for h in store.list_handoffs()],
            )
            return {
                str(s.get("session_id") or s.get("id")): s
                for s in records
                if str(s.get("session_id") or s.get("id") or "").strip()
            }

        return {}

    def _resolve_root_consultation_id(self, session_id: str) -> str:
        session = self._consultation_session_records().get(session_id)
        if session is None:
            return session_id
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        if meta_consult.get("requester_session_id"):
            return session_id
        root_ref = meta_consult.get("root_session_id")
        if root_ref:
            return str(root_ref)
        return session_id

    def list_consultations_for_persona(
        self,
        persona_id: Optional[str],
        consultation_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Optional[List[Dict[str, Any]]]:
        if not persona_id:
            return None
        if self._persona_provider is not None and self._persona_provider(persona_id) is None:
            return None

        all_sessions = self._consultation_session_records()
        sessions = [
            s for s in all_sessions.values()
            if s.get("persona_id") == persona_id
            and s.get("session_type") in {"consult", "committee"}
            and s.get("session_id") == (
                (s.get("metadata") or {}).get("consultation", {}).get("requester_session_id")
            )
        ]
        if consultation_type:
            sessions = [
                s for s in sessions
                if (s.get("metadata") or {}).get("consultation", {}).get("consultation_type") == consultation_type
            ]
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        sessions = sorted(sessions, key=lambda x: str(x.get("started_at") or ""), reverse=True)
        start = (page - 1) * page_size
        return sessions[start : start + page_size]

    def get_consultation(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        session = self._consultation_session_records().get(session_id)
        if session is None:
            return None
        if session.get("session_type") not in {"consult", "committee"}:
            return None
        return session

    def get_consultation_participants(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        if not session_id:
            return None
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        root = all_sessions.get(root_id)
        if root is None:
            return None
        meta_consult = (root.get("metadata") or {}).get("consultation", {})
        requester_id = meta_consult.get("requester_session_id")
        responder_ids: List[str] = meta_consult.get("responder_session_ids") or []
        committee_ids: List[str] = meta_consult.get("committee_session_ids") or []

        participants = []
        def _role_for(sid: str) -> str:
            if sid == requester_id:
                return "requester"
            if sid in committee_ids:
                return "committee_participant"
            return "responder"

        for sid in [requester_id] + responder_ids + committee_ids:
            if not sid:
                continue
            session = all_sessions.get(sid)
            if session:
                enriched = dict(session)
                enriched["consultation_role"] = _role_for(sid)
                participants.append(enriched)

        return participants

    def get_consultation_outcome(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return {
            "session_id": session_id,
            "root_session_id": root_id,
            "source_session": f"/api/v1/consultations/{root_id}",
            "metadata": {
                "consultation": {
                    "outcome": meta_consult.get("outcome"),
                    "actual_reviewers": meta_consult.get("actual_reviewers"),
                    "responder_session_ids": meta_consult.get("responder_session_ids", []),
                    "rationale_ref": meta_consult.get("rationale_ref"),
                    "evidence_refs": meta_consult.get("evidence_refs", []),
                    "escalation_path": meta_consult.get("escalation_path"),
                }
            },
        }

    def get_consultation_evidence(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        if not session_id:
            return None
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        session = self.get_consultation(root_id)
        if session is None:
            return None
        meta_consult = (session.get("metadata") or {}).get("consultation", {})
        return list(meta_consult.get("evidence_refs") or [])

    def get_consult_transcript(
        self,
        session_id: Optional[str],
        *,
        from_sequence_no: Optional[int] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        all_sessions = self._consultation_session_records()
        if session_id not in all_sessions:
            return None
        root_id = self._resolve_root_consultation_id(session_id)
        root_session = all_sessions.get(root_id)
        if root_session is None:
            return None

        record: Optional[Dict[str, Any]] = None
        client = self._consultation_client()
        if client is not None:
            try:
                for item in client.list_transcripts():
                    if str(item.get("session_id") or item.get("request_id") or item.get("transcript_id")) == root_id:
                        record = self._project_service_transcript_record(item)
                        break
            except Exception:
                pass

        if record is None:
            store = self._consultation_store()
            if store is not None:
                for item in store.list_transcripts():
                    data = _model_to_data(item)
                    if str(data.get("session_id") or data.get("request_id") or data.get("transcript_id")) == root_id:
                        record = self._project_service_transcript_record(data)
                        break

        if record is None:
            surface_state = "unavailable"
            events: List[Dict[str, Any]] = []
            transcript_id = f"tr-{root_id}"
            linked_request_id = root_session.get("request_id")
        else:
            transcript_id = str(record.get("transcript_id") or f"tr-{root_id}")
            linked_request_id = record.get("linked_request_id") or root_session.get("request_id")
            raw_events = list(record.get("events") or [])
            raw_events.sort(key=lambda e: int(e.get("sequence_no") or 0))

            full_seqs = [int(e.get("sequence_no") or 0) for e in raw_events]
            has_gap = any(
                full_seqs[i + 1] != full_seqs[i] + 1
                for i in range(len(full_seqs) - 1)
            )
            surface_state = "degraded" if has_gap else "ok"

            if from_sequence_no is not None:
                raw_events = [e for e in raw_events if int(e.get("sequence_no") or 0) >= from_sequence_no]
            events = raw_events

        offset = 0
        if page_token:
            try:
                offset = int(page_token)
            except (ValueError, TypeError):
                offset = 0

        page_events = events[offset : offset + page_size]
        next_page_token = str(offset + page_size) if (offset + page_size < len(events)) else None

        return {
            "transcript_id": transcript_id,
            "session_id": root_id,
            "linked_request_id": linked_request_id,
            "surface_state": surface_state,
            "total_events": len(events),
            "returned_events": len(page_events),
            "next_page_token": next_page_token,
            "events": page_events,
        }

    def _consult_request_can_cancel(self, req: Dict[str, Any]) -> bool:
        status = str(req.get("status") or "created").lower()
        if status in {"completed", "canceled", "cancelled"}:
            return False
        return not bool(req.get("linked_session_id"))

    def _project_consult_request_summary(self, req: Dict[str, Any]) -> Dict[str, Any]:
        status = str(req.get("status") or "created")
        can_cancel = self._consult_request_can_cancel(req)
        task_full = str(req.get("task") or "")
        task_summary = task_full[:120] + ("…" if len(task_full) > 120 else "")
        return {
            "request_id": req.get("request_id"),
            "status": status,
            "from_persona_id": req.get("from_persona_id"),
            "target_type": req.get("target_type"),
            "target_ref": req.get("target_ref"),
            "task_summary": task_summary,
            "priority": req.get("priority"),
            "consultation_type": req.get("consultation_type"),
            "created_at": req.get("created_at"),
            "linked_session_id": req.get("linked_session_id"),
            "request_to_session_status": req.get(
                "request_to_session_status", "pending_session"
            ),
            "allowedActions": {"canCancel": can_cancel},
        }

    def _project_consult_request_detail(self, req: Dict[str, Any]) -> Dict[str, Any]:
        status = str(req.get("status") or "created")
        can_cancel = self._consult_request_can_cancel(req)
        linked_session_id = req.get("linked_session_id")
        r2s_status = str(req.get("request_to_session_status") or "pending_session")
        session_route_href = (
            f"/api/v1/consultations/{linked_session_id}" if linked_session_id else None
        )
        return {
            "request_id": req.get("request_id"),
            "status": status,
            "from_persona_id": req.get("from_persona_id"),
            "target_type": req.get("target_type"),
            "target_ref": req.get("target_ref"),
            "task": req.get("task"),
            "context_refs": req.get("context_refs", []),
            "priority": req.get("priority"),
            "consultation_type": req.get("consultation_type"),
            "created_at": req.get("created_at"),
            "completed_at": req.get("completed_at"),
            "canceled_at": req.get("canceled_at"),
            "linked_session_id": linked_session_id,
            "request_to_session_status": r2s_status,
            "session_handoff": {
                "status": r2s_status,
                "linked_session_id": linked_session_id,
                "session_route_href": session_route_href,
                "note": req.get("session_handoff_note", ""),
            },
            "allowedActions": {"canCancel": can_cancel},
        }

    def list_consult_requests(
        self,
        *,
        statuses: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        consultation_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        raw_requests: List[Dict[str, Any]] = []
        client = self._consultation_client()
        if client is not None:
            try:
                raw_requests = [
                    self._project_service_request_record(r)
                    for r in client.list_requests()
                ]
            except Exception:
                pass
        if not raw_requests:
            store = self._consultation_store()
            if store is not None:
                raw_requests = [
                    self._project_service_request_record(_model_to_data(r))
                    for r in store.list_requests()
                ]

        requests = raw_requests
        if statuses:
            requested = {s.strip().lower() for s in statuses if s.strip()}
            requests = [
                r for r in requests
                if str(r.get("status") or "").strip().lower() in requested
            ]
        if target_type:
            requested_tt = target_type.strip().lower()
            requests = [
                r for r in requests
                if str(r.get("target_type") or "").strip().lower() == requested_tt
            ]
        if consultation_type:
            requested_ct = consultation_type.strip().lower()
            requests = [
                r for r in requests
                if str(r.get("consultation_type") or "").strip().lower() == requested_ct
            ]
        requests.sort(
            key=lambda r: (_parse_rfc3339(r.get("created_at")) or datetime.min).replace(tzinfo=None),
            reverse=True,
        )
        return [self._project_consult_request_summary(r) for r in requests]

    def get_consult_request(self, request_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not request_id:
            return None
        client = self._consultation_client()
        if client is not None:
            try:
                req = client.get_request(request_id)
                if req:
                    return self._project_consult_request_detail(
                        self._project_service_request_record(req)
                    )
            except Exception:
                pass

        store = self._consultation_store()
        if store is not None:
            found = store.get_request(request_id)
            if found:
                return self._project_consult_request_detail(
                    self._project_service_request_record(_model_to_data(found))
                )
        return None

    def create_consult_request(
        self,
        *,
        from_persona_id: str,
        target_type: str,
        target_ref: str,
        task: str,
        context_refs: List[Dict[str, str]],
        priority: str,
        consultation_type: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        timestamp = created_at or _utc_now_rfc3339()
        client = self._consultation_client()
        if client is not None:
            serialized_context_refs = [
                f"{item['type']}:{item['id']}"
                for item in context_refs
                if isinstance(item, dict) and item.get("type") and item.get("id")
            ]
            req_type_enum = _BFF_TO_SERVICE_REQUEST_TYPE.get(
                consultation_type,
                getattr(ConsultRequestType, "STRATEGY_REVIEW", "strategy_review"),
            )
            req_type_val = req_type_enum.value if hasattr(req_type_enum, "value") else str(req_type_enum)
            priority_enum = _BFF_TO_SERVICE_PRIORITY.get(
                priority,
                getattr(ConsultPriority, "NORMAL", "normal"),
            )
            priority_val = priority_enum.value if hasattr(priority_enum, "value") else str(priority_enum)
            service_request = client.create_request(
                {
                    "request_type": req_type_val,
                    "requested_by": {"actor_type": "operator", "actor_id": actor_id},
                    "from_persona_id": from_persona_id,
                    "target_type": target_type,
                    "target_id": target_ref,
                    "task": task,
                    "consultation_type": consultation_type,
                    "context_refs": serialized_context_refs,
                    "priority": priority_val,
                    "status": "draft",
                    "linked_session_id": None,
                    "request_to_session_status": "pending_session",
                    "completed_at": None,
                    "canceled_at": None,
                    "session_handoff_note": "Request accepted; session creation is pending Persona Plane assignment.",
                    "metadata": {
                        "bff_context_refs": context_refs,
                        "bff_priority": priority,
                        "task": task,
                        "consultation_type": consultation_type,
                    },
                    "trace_id": f"trace-cr-{uuid.uuid4().hex[:12]}",
                    "created_at": timestamp,
                }
            )
            return self._project_consult_request_detail(
                self._project_service_request_record(service_request)
            )

        store = self._consultation_store()
        if store is not None:
            request_id = f"cr-{timestamp[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
            serialized_context_refs = [
                f"{item['type']}:{item['id']}"
                for item in context_refs
                if isinstance(item, dict) and item.get("type") and item.get("id")
            ]
            trace_id = f"trace-{request_id}"
            req_type_enum = _BFF_TO_SERVICE_REQUEST_TYPE.get(
                consultation_type,
                getattr(ConsultRequestType, "STRATEGY_REVIEW", "strategy_review"),
            )
            priority_enum = _BFF_TO_SERVICE_PRIORITY.get(
                priority,
                getattr(ConsultPriority, "NORMAL", "normal"),
            )
            if ConsultRequest is not None:
                service_request = ConsultRequest(
                    request_id=request_id,
                    request_type=req_type_enum,
                    requested_by={"actor_type": "operator", "actor_id": actor_id},
                    from_persona_id=from_persona_id,
                    target_type=target_type,
                    target_id=target_ref,
                    task=task,
                    consultation_type=consultation_type,
                    context_refs=serialized_context_refs,
                    priority=priority_enum,
                    status=getattr(ConsultRequestStatus, "DRAFT", "draft"),
                    linked_session_id=None,
                    request_to_session_status="pending_session",
                    completed_at=None,
                    canceled_at=None,
                    session_handoff_note="Request accepted; session creation is pending Persona Plane assignment.",
                    metadata={
                        "bff_context_refs": context_refs,
                        "bff_priority": priority,
                        "task": task,
                        "consultation_type": consultation_type,
                    },
                    trace_id=trace_id,
                    created_at=timestamp,
                )
                store.put_request(service_request)
                if ConsultAuditEvent is not None:
                    store.append_audit(
                        ConsultAuditEvent(
                            audit_id=f"aud-{uuid.uuid4().hex[:12]}",
                            request_id=request_id,
                            actor_ref={"actor_type": "operator", "actor_id": actor_id},
                            action="request_created",
                            after_state="draft",
                            trace_id=trace_id,
                        )
                    )
                return self._project_consult_request_detail(
                    self._project_service_request_record(_model_to_data(service_request))
                )

        raise RuntimeError("Consultation store/client is not available.")

    def cancel_consult_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        canceled_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        client = self._consultation_client()
        if client is not None:
            try:
                request = client.cancel_request(
                    request_id,
                    actor_id=actor_id,
                    canceled_at=canceled_at or _utc_now_rfc3339(),
                )
                if request is not None:
                    return self._project_consult_request_detail(
                        self._project_service_request_record(request)
                    )
            except Exception:
                return None

        store = self._consultation_store()
        if store is not None:
            request = store.get_request(request_id)
            if request is None:
                return None
            projected = self._project_service_request_record(_model_to_data(request))
            if not self._consult_request_can_cancel(projected):
                return None
            timestamp = canceled_at or _utc_now_rfc3339()
            request.status = getattr(ConsultRequestStatus, "CANCELLED", "cancelled")
            request.canceled_at = timestamp
            request.request_to_session_status = "canceled_before_session"
            request.session_handoff_note = "Request canceled by operator."
            store.put_request(request)
            if ConsultAuditEvent is not None:
                store.append_audit(
                    ConsultAuditEvent(
                        audit_id=f"aud-{uuid.uuid4().hex[:12]}",
                        request_id=request_id,
                        actor_ref={"actor_type": "operator", "actor_id": actor_id},
                        action="request_cancelled",
                        before_state="draft",
                        after_state="cancelled",
                        trace_id=request.trace_id,
                    )
                )
            return self._project_consult_request_detail(
                self._project_service_request_record(_model_to_data(request))
            )

        return None

    def list_consult_memos(
        self,
        *,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        raw_memos: List[Dict[str, Any]] = []
        client = self._consultation_client()
        if client is not None:
            try:
                raw_memos = [
                    self._project_service_memo_record(memo)
                    for memo in client.list_memos()
                ]
            except Exception:
                pass
        if not raw_memos:
            store = self._consultation_store()
            if store is not None:
                raw_memos = [
                    self._project_service_memo_record(_model_to_data(memo))
                    for memo in store.list_memos()
                ]

        memos = raw_memos
        if statuses:
            requested = {str(value).strip().lower() for value in statuses if str(value).strip()}
            memos = [
                memo for memo in memos
                if str(memo.get("status") or memo.get("lifecycle_state") or "").strip().lower() in requested
            ]
        memos.sort(
            key=lambda memo: (
                (_parse_rfc3339(memo.get("published_at") or memo.get("created_at")) or datetime.min).replace(tzinfo=None),
                (_parse_rfc3339(memo.get("created_at")) or datetime.min).replace(tzinfo=None),
                str(memo.get("memo_id") or ""),
            ),
            reverse=True,
        )
        return [self._project_consult_memo_summary(memo) for memo in memos]

    def get_consult_memo(self, memo_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not memo_id:
            return None
        client = self._consultation_client()
        if client is not None:
            try:
                memo = client.get_memo(memo_id)
                if memo:
                    return self._project_consult_memo_detail(
                        self._project_service_memo_record(memo)
                    )
            except Exception:
                pass

        store = self._consultation_store()
        if store is not None:
            found = store.get_memo(memo_id)
            if found:
                return self._project_consult_memo_detail(
                    self._project_service_memo_record(_model_to_data(found))
                )
        return None

    def _project_consult_memo_summary(self, memo: Dict[str, Any]) -> Dict[str, Any]:
        memo_id = str(memo.get("memo_id") or memo.get("id") or "").strip()
        recommendations = list(memo.get("recommendations") or [])
        return {
            "object_ref": {
                "type": "ConsultMemo",
                "id": memo_id,
            },
            "memo_id": memo_id,
            "memo_type": memo.get("memo_type") or "red_team",
            "status": memo.get("status") or memo.get("lifecycle_state") or "draft",
            "linked_request_id": memo.get("linked_request_id"),
            "recommendation_count": len(recommendations),
            "published_at": memo.get("published_at"),
            "created_at": memo.get("created_at"),
            "route_href": f"/consultation/memos/{memo_id}" if memo_id else None,
        }

    def _project_consult_memo_detail(self, memo: Dict[str, Any]) -> Dict[str, Any]:
        memo = _redact_consult_memo_review_payload(memo)
        memo_id = str(memo.get("memo_id") or memo.get("id") or "").strip()
        mapping = memo.get("session_to_memo_mapping") if isinstance(memo.get("session_to_memo_mapping"), dict) else {}
        governance_target = memo.get("governance_target") if isinstance(memo.get("governance_target"), dict) else {}
        return {
            "object_ref": {
                "type": "ConsultMemo",
                "id": memo_id,
            },
            "memo_id": memo_id,
            "memo_type": memo.get("memo_type") or "red_team",
            "status": memo.get("status") or memo.get("lifecycle_state") or "draft",
            "lifecycle_state": memo.get("lifecycle_state") or memo.get("status") or "draft",
            "author_ref": memo.get("author_ref"),
            "linked_request_id": memo.get("linked_request_id"),
            "linked_session_id": memo.get("linked_session_id"),
            "session_to_memo_mapping": {
                "mapping_id": mapping.get("mapping_id"),
                "source_session_id": mapping.get("source_session_id"),
                "transcript_id": mapping.get("transcript_id"),
                "transcript_version": mapping.get("transcript_version"),
                "memo_id": mapping.get("memo_id") or memo_id,
                "memo_type": mapping.get("memo_type") or memo.get("memo_type") or "red_team",
                "created_by": dict(mapping.get("created_by") or {}),
                "evidence_refs": list(mapping.get("evidence_refs") or []),
                "mapping_status": mapping.get("mapping_status"),
                "created_at": mapping.get("created_at"),
            },
            "summary": memo.get("summary"),
            "recommendations": list(memo.get("recommendations") or []),
            "evidence_refs": list(memo.get("evidence_refs") or []),
            "published_at": memo.get("published_at"),
            "created_at": memo.get("created_at"),
            "supersedes_memo_id": memo.get("supersedes_memo_id"),
            "superseded_by_memo_id": memo.get("superseded_by_memo_id"),
            "surface_state": memo.get("surface_state") or "ok",
            "governance_target": dict(governance_target),
            "suppressed": bool(memo.get("suppressed")),
            "withdrawn": bool(memo.get("withdrawn")),
            "active_governance_review_id": memo.get("active_governance_review_id"),
        }

    @staticmethod
    def _committee_board_row(root_session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        consult = (root_session.get("metadata") or {}).get("consultation", {})
        committee_id = str(consult.get("committee_ref") or "").strip()
        committee_session_ids = list(consult.get("committee_session_ids") or [])
        if not committee_id or not committee_session_ids:
            return None
        return {
            "committee_id": committee_id,
            "committee_ref": committee_id,
            "escalation_reason": json.loads(json.dumps(consult.get("escalation_reason") or {})),
            "quorum_state": consult.get("quorum_state"),
            "consensus_state": consult.get("consensus_state"),
            "linked_request_id": root_session.get("request_id"),
            "started_at": consult.get("committee_started_at") or root_session.get("started_at"),
            "surface_state": str(consult.get("committee_surface_state") or "ok"),
            "route_href": f"/consultation/committees/{committee_id}",
        }

    def list_committees(
        self,
        *,
        quorum_states: Optional[List[str]] = None,
        consensus_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        sessions = self._consultation_session_records()
        rows: List[Dict[str, Any]] = []
        for session in sessions.values():
            if session.get("session_type") != "consult":
                continue
            row = self._committee_board_row(session)
            if row is None:
                continue
            rows.append(row)
        if quorum_states:
            requested = {str(v).strip().lower() for v in quorum_states if str(v).strip()}
            rows = [r for r in rows if str(r.get("quorum_state") or "").strip().lower() in requested]
        if consensus_states:
            requested = {str(v).strip().lower() for v in consensus_states if str(v).strip()}
            rows = [r for r in rows if str(r.get("consensus_state") or "").strip().lower() in requested]
        return rows

    def get_committee(self, committee_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not committee_id:
            return None
        sessions = self._consultation_session_records()
        root_session: Optional[Dict[str, Any]] = None
        for session in sessions.values():
            if session.get("session_type") != "consult":
                continue
            consult = (session.get("metadata") or {}).get("consultation", {})
            if str(consult.get("committee_ref") or "") == str(committee_id):
                root_session = session
                break
        if root_session is None:
            return None

        consult = (root_session.get("metadata") or {}).get("consultation", {})
        committee_session_ids = list(consult.get("committee_session_ids") or [])
        sponsor_session_id = str(consult.get("sponsor_session_id") or "").strip()

        participant_roster: List[Dict[str, Any]] = []
        for session_id in committee_session_ids:
            participant = sessions.get(session_id)
            if not participant:
                continue
            participant_consult = (participant.get("metadata") or {}).get("consultation", {})
            participant_roster.append(
                {
                    "participant_id": participant.get("session_id"),
                    "persona_id": participant.get("persona_id"),
                    "persona_label": None,
                    "role": (
                        "sponsor"
                        if participant.get("session_id") == sponsor_session_id
                        else (participant_consult.get("role") or "committee_participant")
                    ),
                    "status": participant_consult.get("participant_status") or participant.get("status"),
                    "outcome_signal": participant_consult.get("outcome_signal"),
                    "rationale_ref": participant_consult.get("rationale_ref"),
                }
            )

        sponsor_assignment = next(
            (row for row in participant_roster if str(row.get("participant_id") or "") == sponsor_session_id),
            None,
        )
        board_row = self._committee_board_row(root_session)
        if board_row is None:
            return None

        return {
            **board_row,
            "linked_session_id": root_session.get("session_id"),
            "participant_roster": participant_roster,
            "sponsor_assignment": sponsor_assignment,
            "sponsor_decision": consult.get("sponsor_decision"),
            "sponsor_decided_at": consult.get("sponsor_decided_at"),
            "sponsor_decided_by": consult.get("sponsor_decided_by"),
            "synthesis_summary": json.loads(json.dumps(consult.get("synthesis_summary") or {})),
            "linked_evidence": json.loads(json.dumps(consult.get("evidence_refs") or [])),
            "service_handoff": json.loads(json.dumps(consult.get("service_handoff") or {})),
        }


# =====================================================================
# Composite & In-Memory Ports
# =====================================================================

class CompositeOperationsConsultationPort:
    """Composite adapter fulfilling OperationsConsultationPort by delegation."""

    def __init__(
        self,
        *,
        workflow_port: WorkflowHookCatalogReaderPort,
        openclaw_port: OpenClawOperationsReaderPort,
        consultation_port: ConsultationReaderPort,
    ) -> None:
        self._workflow = workflow_port
        self._openclaw = openclaw_port
        self._consultation = consultation_port

    # Workflow / Hook / Catalog delegation
    def list_workflow_templates(self) -> List[Dict[str, Any]]:
        return self._workflow.list_workflow_templates()

    def list_hook_registry(self) -> List[Dict[str, Any]]:
        return self._workflow.list_hook_registry()

    def list_governance_permissions(self) -> List[Dict[str, Any]]:
        return self._workflow.list_governance_permissions()

    def list_memory_governance_rules(self) -> List[Dict[str, Any]]:
        return self._workflow.list_memory_governance_rules()

    def list_consult_rules(self) -> List[Dict[str, Any]]:
        return self._workflow.list_consult_rules()

    def list_route_policies(self) -> List[Dict[str, Any]]:
        return self._workflow.list_route_policies()

    def list_alpha_factory_cards(self, *, page: int = 1, page_size: int = 20, lane: Optional[str] = None) -> List[Dict[str, Any]]:
        return self._workflow.list_alpha_factory_cards(page=page, page_size=page_size, lane=lane)

    def list_skills(self) -> List[Dict[str, Any]]:
        return self._workflow.list_skills()

    def list_tools(self) -> List[Dict[str, Any]]:
        return self._workflow.list_tools()

    def list_mcp_servers(self) -> List[Dict[str, Any]]:
        return self._workflow.list_mcp_servers()

    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        return self._workflow.list_mcp_tools()

    # OpenClaw Operations delegation
    def get_research_oss_preactivation_snapshot(self, *, activity_limit: int = 20) -> Dict[str, Any]:
        return self._openclaw.get_research_oss_preactivation_snapshot(activity_limit=activity_limit)

    def get_openclaw_ops_snapshot(
        self,
        *,
        session_limit: int = 25,
        audit_limit: int = 20,
        operator_id: Optional[str] = None,
        state: Optional[str] = None,
        agent_id: Optional[str] = None,
        effective_tools_session_id: Optional[str] = None,
        requesting_operator_id: Optional[str] = None,
        effective_tools_mode: Optional[str] = None,
        requesting_operator_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._openclaw.get_openclaw_ops_snapshot(
            session_limit=session_limit,
            audit_limit=audit_limit,
            operator_id=operator_id,
            state=state,
            agent_id=agent_id,
            effective_tools_session_id=effective_tools_session_id,
            requesting_operator_id=requesting_operator_id,
            effective_tools_mode=effective_tools_mode,
            requesting_operator_role=requesting_operator_role,
        )

    def get_openclaw_broker_adapter_readiness(self) -> Dict[str, Any]:
        return self._openclaw.get_openclaw_broker_adapter_readiness()

    # Consultation delegation
    def list_consultations_for_persona(
        self,
        persona_id: Optional[str],
        consultation_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Optional[List[Dict[str, Any]]]:
        return self._consultation.list_consultations_for_persona(
            persona_id=persona_id,
            consultation_type=consultation_type,
            status=status,
            page=page,
            page_size=page_size,
        )

    def get_consultation(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._consultation.get_consultation(session_id)

    def get_consultation_participants(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        return self._consultation.get_consultation_participants(session_id)

    def get_consultation_outcome(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._consultation.get_consultation_outcome(session_id)

    def get_consultation_evidence(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        return self._consultation.get_consultation_evidence(session_id)

    def get_consult_transcript(
        self,
        session_id: Optional[str],
        *,
        from_sequence_no: Optional[int] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self._consultation.get_consult_transcript(
            session_id,
            from_sequence_no=from_sequence_no,
            page_size=page_size,
            page_token=page_token,
        )

    def list_consult_requests(
        self,
        *,
        statuses: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        consultation_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return self._consultation.list_consult_requests(
            statuses=statuses,
            target_type=target_type,
            consultation_type=consultation_type,
        )

    def get_consult_request(self, request_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._consultation.get_consult_request(request_id)

    def create_consult_request(
        self,
        *,
        from_persona_id: str,
        target_type: str,
        target_ref: str,
        task: str,
        context_refs: List[Dict[str, str]],
        priority: str,
        consultation_type: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._consultation.create_consult_request(
            from_persona_id=from_persona_id,
            target_type=target_type,
            target_ref=target_ref,
            task=task,
            context_refs=context_refs,
            priority=priority,
            consultation_type=consultation_type,
            actor_id=actor_id,
            created_at=created_at,
        )

    def cancel_consult_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        canceled_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self._consultation.cancel_consult_request(
            request_id,
            actor_id=actor_id,
            canceled_at=canceled_at,
        )

    def list_consult_memos(
        self,
        *,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return self._consultation.list_consult_memos(statuses=statuses)

    def get_consult_memo(self, memo_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._consultation.get_consult_memo(memo_id)

    def list_committees(
        self,
        *,
        quorum_states: Optional[List[str]] = None,
        consensus_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        return self._consultation.list_committees(
            quorum_states=quorum_states,
            consensus_states=consensus_states,
        )

    def get_committee(self, committee_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._consultation.get_committee(committee_id)

    def dataset_source(self, dataset: str) -> str:
        if dataset in ("workflow_templates", "hook_registry", "governance_permissions", "memory_governance_rules", "consult_rules", "route_policies", "alpha_factory_cards", "skills", "tools", "mcp_servers", "mcp_tools"):
            return self._workflow.dataset_source(dataset)
        return self._consultation.dataset_source(dataset)


class InMemoryOperationsConsultationPort:
    """In-memory test fake implementation of OperationsConsultationPort."""

    def __init__(
        self,
        *,
        workflow_templates: Optional[List[Dict[str, Any]]] = None,
        hook_registry: Optional[List[Dict[str, Any]]] = None,
        governance_permissions: Optional[List[Dict[str, Any]]] = None,
        memory_governance_rules: Optional[List[Dict[str, Any]]] = None,
        consult_rules: Optional[List[Dict[str, Any]]] = None,
        route_policies: Optional[List[Dict[str, Any]]] = None,
        alpha_factory_cards: Optional[List[Dict[str, Any]]] = None,
        skills: Optional[List[Dict[str, Any]]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        mcp_servers: Optional[List[Dict[str, Any]]] = None,
        mcp_tools: Optional[List[Dict[str, Any]]] = None,
        openclaw_ops_snapshot: Optional[Dict[str, Any]] = None,
        openclaw_broker_readiness: Optional[Dict[str, Any]] = None,
        research_oss_snapshot: Optional[Dict[str, Any]] = None,
        consult_requests: Optional[List[Dict[str, Any]]] = None,
        consult_memos: Optional[List[Dict[str, Any]]] = None,
        consult_transcripts: Optional[List[Dict[str, Any]]] = None,
        consult_sessions: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.workflow_templates = list(workflow_templates or [])
        self.hook_registry = list(hook_registry or [])
        self.governance_permissions = list(governance_permissions or [])
        self.memory_governance_rules = list(memory_governance_rules or [])
        self.consult_rules = list(consult_rules or [])
        self.route_policies = list(route_policies or [])
        self.alpha_factory_cards = list(alpha_factory_cards or [])
        self.skills = list(skills or [])
        self.tools = list(tools or [])
        self.mcp_servers = list(mcp_servers or [])
        self.mcp_tools = list(mcp_tools or [])
        self.openclaw_ops_snapshot = openclaw_ops_snapshot
        self.openclaw_broker_readiness = openclaw_broker_readiness
        self.research_oss_snapshot = research_oss_snapshot
        self.consult_requests = {r["request_id"]: dict(r) for r in (consult_requests or []) if "request_id" in r}
        self.consult_memos = {m["memo_id"]: dict(m) for m in (consult_memos or []) if "memo_id" in m}
        self.consult_transcripts = {t["session_id"]: dict(t) for t in (consult_transcripts or []) if "session_id" in t}
        self.consult_sessions = {s["session_id"]: dict(s) for s in (consult_sessions or []) if "session_id" in s}

    def dataset_source(self, dataset: str) -> str:
        return "in_memory"

    def list_workflow_templates(self) -> List[Dict[str, Any]]:
        return sorted(self.workflow_templates, key=_automation_record_sort_key)

    def list_hook_registry(self) -> List[Dict[str, Any]]:
        return sorted(self.hook_registry, key=_automation_record_sort_key)

    def list_governance_permissions(self) -> List[Dict[str, Any]]:
        return list(self.governance_permissions)

    def list_memory_governance_rules(self) -> List[Dict[str, Any]]:
        return list(self.memory_governance_rules)

    def list_consult_rules(self) -> List[Dict[str, Any]]:
        return list(self.consult_rules)

    def list_route_policies(self) -> List[Dict[str, Any]]:
        return list(self.route_policies)

    def list_alpha_factory_cards(self, *, page: int = 1, page_size: int = 20, lane: Optional[str] = None) -> List[Dict[str, Any]]:
        records = self.alpha_factory_cards
        if lane:
            records = [r for r in records if str(r.get("lane") or "").lower() == lane.lower()]
        start = (page - 1) * page_size
        return records[start : start + page_size]

    def list_skills(self) -> List[Dict[str, Any]]:
        return list(self.skills)

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(self.tools)

    def list_mcp_servers(self) -> List[Dict[str, Any]]:
        return list(self.mcp_servers)

    def list_mcp_tools(self) -> List[Dict[str, Any]]:
        return list(self.mcp_tools)

    def get_research_oss_preactivation_snapshot(self, *, activity_limit: int = 20) -> Dict[str, Any]:
        if self.research_oss_snapshot is not None:
            return dict(self.research_oss_snapshot)
        return {
            "surface": "research_oss_activation_ready",
            "activation_state": "preactivation_only",
            "production_activation": "disabled",
            "activated": False,
            "offline_gate": "disabled",
            "allowed_scope": "capability_metadata_read_only",
            "write_paths": {},
            "backend_inventory": [],
            "safe_dispatch": {},
            "run_history": [],
            "activity": [],
            "error_summary": {"activity_with_errors": 0, "rejection_count": 0, "failed_count": 0},
            "service_status": {},
        }

    def get_openclaw_ops_snapshot(
        self,
        *,
        session_limit: int = 25,
        audit_limit: int = 20,
        operator_id: Optional[str] = None,
        state: Optional[str] = None,
        agent_id: Optional[str] = None,
        effective_tools_session_id: Optional[str] = None,
        requesting_operator_id: Optional[str] = None,
        effective_tools_mode: Optional[str] = None,
        requesting_operator_role: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.openclaw_ops_snapshot is not None:
            return dict(self.openclaw_ops_snapshot)
        return {
            "surface": "openclaw_ops",
            "overall_status": "ok",
            "activation": {},
            "gate_state": {},
            "production_activation": "disabled",
            "upstream": {"status": "ok", "reachable": True},
            "session_lifecycle": {"status": "ok", "count": 0, "sessions": []},
            "tool_workflow": {"policy": {}, "audit": {"status": "ok", "entries": []}},
            "operator_controls": {"read_operations": [], "commands": {}},
            "allowedActions": {"canCreateSession": True},
            "degradation": {"status": "ok", "reasons": []},
            "service_status": {},
        }

    def get_openclaw_broker_adapter_readiness(self) -> Dict[str, Any]:
        if self.openclaw_broker_readiness is not None:
            return dict(self.openclaw_broker_readiness)
        return {
            "surface": "openclaw_broker_adapter_readiness",
            "overall_status": "ok",
            "sandbox_adapter_state": "activation_ready",
            "paper_adapter_state": "gated",
            "canary_adapter_state": "fail_closed",
            "live_adapter_state": "fail_closed",
            "live_execution_enabled": False,
            "canary_execution_enabled": False,
            "is_real_capital": False,
            "is_real_order": False,
            "gate_reason": "fail_closed_explicit_gate_required",
            "service_status": {"status": "ok"},
        }

    def list_consultations_for_persona(
        self,
        persona_id: Optional[str],
        consultation_type: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Optional[List[Dict[str, Any]]]:
        if not persona_id:
            return None
        sessions = [
            s for s in self.consult_sessions.values()
            if s.get("persona_id") == persona_id
        ]
        if consultation_type:
            sessions = [s for s in sessions if (s.get("metadata") or {}).get("consultation", {}).get("consultation_type") == consultation_type]
        if status:
            sessions = [s for s in sessions if s.get("status") == status]
        start = (page - 1) * page_size
        return sessions[start : start + page_size]

    def get_consultation(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        return self.consult_sessions.get(session_id)

    def get_consultation_participants(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        if not session_id or session_id not in self.consult_sessions:
            return None
        session = self.consult_sessions[session_id]
        meta = (session.get("metadata") or {}).get("consultation", {})
        participants = [dict(session, consultation_role="requester")]
        for p in meta.get("committee_participants", []):
            if isinstance(p, dict):
                participants.append(p)
        return participants

    def get_consultation_outcome(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not session_id or session_id not in self.consult_sessions:
            return None
        session = self.consult_sessions[session_id]
        meta = (session.get("metadata") or {}).get("consultation", {})
        return {
            "session_id": session_id,
            "root_session_id": session_id,
            "source_session": f"/api/v1/consultations/{session_id}",
            "metadata": {"consultation": meta},
        }

    def get_consultation_evidence(self, session_id: Optional[str]) -> Optional[List[Dict[str, Any]]]:
        if not session_id or session_id not in self.consult_sessions:
            return None
        session = self.consult_sessions[session_id]
        meta = (session.get("metadata") or {}).get("consultation", {})
        return list(meta.get("evidence_refs") or [])

    def get_consult_transcript(
        self,
        session_id: Optional[str],
        *,
        from_sequence_no: Optional[int] = None,
        page_size: int = 50,
        page_token: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not session_id or session_id not in self.consult_sessions:
            return None
        transcript = self.consult_transcripts.get(session_id)
        events = list(transcript.get("events", []) if transcript else [])
        if from_sequence_no is not None:
            events = [e for e in events if int(e.get("sequence_no") or 0) >= from_sequence_no]
        offset = int(page_token) if page_token and page_token.isdigit() else 0
        page_events = events[offset : offset + page_size]
        next_token = str(offset + page_size) if (offset + page_size < len(events)) else None
        return {
            "transcript_id": f"tr-{session_id}",
            "session_id": session_id,
            "linked_request_id": self.consult_sessions[session_id].get("request_id"),
            "surface_state": "ok" if transcript else "unavailable",
            "total_events": len(events),
            "returned_events": len(page_events),
            "next_page_token": next_token,
            "events": page_events,
        }

    def list_consult_requests(
        self,
        *,
        statuses: Optional[List[str]] = None,
        target_type: Optional[str] = None,
        consultation_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        requests = list(self.consult_requests.values())
        if statuses:
            req_set = {s.lower() for s in statuses}
            requests = [r for r in requests if str(r.get("status") or "").lower() in req_set]
        if target_type:
            requests = [r for r in requests if str(r.get("target_type") or "").lower() == target_type.lower()]
        if consultation_type:
            requests = [r for r in requests if str(r.get("consultation_type") or "").lower() == consultation_type.lower()]
        return requests

    def get_consult_request(self, request_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not request_id:
            return None
        return self.consult_requests.get(request_id)

    def create_consult_request(
        self,
        *,
        from_persona_id: str,
        target_type: str,
        target_ref: str,
        task: str,
        context_refs: List[Dict[str, str]],
        priority: str,
        consultation_type: str,
        actor_id: str,
        created_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        req_id = f"cr-{uuid.uuid4().hex[:8]}"
        req = {
            "request_id": req_id,
            "status": "created",
            "from_persona_id": from_persona_id,
            "target_type": target_type,
            "target_ref": target_ref,
            "task": task,
            "context_refs": context_refs,
            "priority": priority,
            "consultation_type": consultation_type,
            "created_at": created_at or _utc_now_rfc3339(),
            "linked_session_id": None,
            "request_to_session_status": "pending_session",
            "allowedActions": {"canCancel": True},
        }
        self.consult_requests[req_id] = req
        return req

    def cancel_consult_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        canceled_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if request_id not in self.consult_requests:
            return None
        req = self.consult_requests[request_id]
        if req.get("linked_session_id"):
            return None
        req["status"] = "canceled"
        req["canceled_at"] = canceled_at or _utc_now_rfc3339()
        return req

    def list_consult_memos(
        self,
        *,
        statuses: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        memos = list(self.consult_memos.values())
        if statuses:
            req_set = {s.lower() for s in statuses}
            memos = [m for m in memos if str(m.get("status") or "").lower() in req_set]
        return memos

    def get_consult_memo(self, memo_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not memo_id:
            return None
        return self.consult_memos.get(memo_id)

    @staticmethod
    def _committee_board_row(root_session: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return DomainConsultationPort._committee_board_row(root_session)

    def list_committees(
        self,
        *,
        quorum_states: Optional[List[str]] = None,
        consensus_states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for session in self.consult_sessions.values():
            if session.get("session_type") != "consult":
                continue
            row = self._committee_board_row(session)
            if row is None:
                continue
            rows.append(row)
        if quorum_states:
            requested = {str(v).strip().lower() for v in quorum_states if str(v).strip()}
            rows = [r for r in rows if str(r.get("quorum_state") or "").strip().lower() in requested]
        if consensus_states:
            requested = {str(v).strip().lower() for v in consensus_states if str(v).strip()}
            rows = [r for r in rows if str(r.get("consensus_state") or "").strip().lower() in requested]
        return rows

    def get_committee(self, committee_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not committee_id:
            return None
        root_session: Optional[Dict[str, Any]] = None
        for session in self.consult_sessions.values():
            if session.get("session_type") != "consult":
                continue
            consult = (session.get("metadata") or {}).get("consultation", {})
            if str(consult.get("committee_ref") or "") == str(committee_id):
                root_session = session
                break
        if root_session is None:
            return None

        consult = (root_session.get("metadata") or {}).get("consultation", {})
        committee_session_ids = list(consult.get("committee_session_ids") or [])
        sponsor_session_id = str(consult.get("sponsor_session_id") or "").strip()

        participant_roster: List[Dict[str, Any]] = []
        for session_id in committee_session_ids:
            participant = self.consult_sessions.get(session_id)
            if not participant:
                continue
            participant_consult = (participant.get("metadata") or {}).get("consultation", {})
            participant_roster.append(
                {
                    "participant_id": participant.get("session_id"),
                    "persona_id": participant.get("persona_id"),
                    "persona_label": None,
                    "role": (
                        "sponsor"
                        if participant.get("session_id") == sponsor_session_id
                        else (participant_consult.get("role") or "committee_participant")
                    ),
                    "status": participant_consult.get("participant_status") or participant.get("status"),
                    "outcome_signal": participant_consult.get("outcome_signal"),
                    "rationale_ref": participant_consult.get("rationale_ref"),
                }
            )

        sponsor_assignment = next(
            (row for row in participant_roster if str(row.get("participant_id") or "") == sponsor_session_id),
            None,
        )
        board_row = self._committee_board_row(root_session)
        if board_row is None:
            return None

        return {
            **board_row,
            "linked_session_id": root_session.get("session_id"),
            "participant_roster": participant_roster,
            "sponsor_assignment": sponsor_assignment,
            "sponsor_decision": consult.get("sponsor_decision"),
            "sponsor_decided_at": consult.get("sponsor_decided_at"),
            "sponsor_decided_by": consult.get("sponsor_decided_by"),
            "synthesis_summary": json.loads(json.dumps(consult.get("synthesis_summary") or {})),
            "linked_evidence": json.loads(json.dumps(consult.get("evidence_refs") or [])),
            "service_handoff": json.loads(json.dumps(consult.get("service_handoff") or {})),
        }


def create_operations_consultation_port(
    *,
    service_store: Optional[Any] = None,
    openclaw_client: Optional[OpenClawOpsClient] = None,
    consultation_client: Optional[ConsultationServiceClient] = None,
    consultation_store: Optional[ConsultationStore] = None,
    consultation_data_dir: Optional[str] = None,
    persona_provider: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
) -> OperationsConsultationPort:
    """Factory creating a production-grade CompositeOperationsConsultationPort."""
    workflow_port = DomainWorkflowCatalogPort(service_store=service_store)
    openclaw_port = DomainOpenClawOperationsPort(client=openclaw_client)
    consultation_port = DomainConsultationPort(
        client=consultation_client,
        store=consultation_store,
        data_dir=consultation_data_dir,
        persona_provider=persona_provider,
    )
    return CompositeOperationsConsultationPort(
        workflow_port=workflow_port,
        openclaw_port=openclaw_port,
        consultation_port=consultation_port,
    )


def create_in_memory_operations_consultation_port(**kwargs: Any) -> InMemoryOperationsConsultationPort:
    """Factory creating an in-memory test fake port."""
    return InMemoryOperationsConsultationPort(**kwargs)
