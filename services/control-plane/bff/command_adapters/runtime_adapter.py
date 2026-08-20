"""Runtime Domain Command Adapter.

Routes runtime start, pause, resume, repair, rollback, safe-mode, and kill-switch
actions to the authoritative internal API and RuntimeManager service client.
"""
from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    governance_approval_url,
    http_request_json,
    internal_url,
    runtime_repair_url,
    utc_now,
)

log = logging.getLogger(__name__)


def _get_runtime_manager_client():
    import importlib.util

    module_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "runtime-manager", "runtime_manager_client.py")
    )
    spec = importlib.util.spec_from_file_location("pantheon_runtime_manager_client", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load runtime_manager_client from {module_path!r}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.RuntimeManagerClient()


class RuntimeCommandAdapter(DomainCommandAdapter):
    """Adapter for Runtime and RuntimeManager domain actions."""

    _HANDLED_COMMANDS = {
        "RuntimeAction",
        "StartRuntime",
        "PauseRuntime",
        "PauseExecution",
        "PausePaperRuntime",
        "ResumePaperRuntime",
        "RestartPaperRuntime",
        "RestartTelemetryBridge",
        "TerminateStalePaperMonitoringSession",
        "StartPaperMonitoringSession",
        "ProbeTelemetryIngest",
        "IssueSafeMode",
        "IssueRiskOff",
        "ExecuteRollback",
        "ApproveRollback",
        "RejectRollback",
        "HardRollback",
        "ActivateKillSwitch",
    }

    _HANDLED_ENTITIES = {
        "runtime",
        "runtimebinding",
        "runtime-binding",
        "killswitchorder",
        "kill-switch-order",
        "rollback",
    }

    def can_handle(self, command_type: str, entity_type: str, action_id: str) -> bool:
        normalized_cmd = str(command_type or "").strip()
        normalized_entity = str(entity_type or "").strip().lower().replace("_", "-")
        return normalized_cmd in self._HANDLED_COMMANDS or normalized_entity in self._HANDLED_ENTITIES

    def execute(
        self,
        command_id: str,
        command_type: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        action_id = str(params.get("action_id") or command_type or "").strip()
        entity_id = str(params.get("entity_id") or params.get("runtime_id") or params.get("binding_id") or params.get("runtime_binding_id") or "").strip()

        if command_type in {"StartRuntime"} or action_id.lower() == "start":
            return self._execute_start(command_id, entity_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"PauseRuntime", "PauseExecution", "PausePaperRuntime"} or action_id.lower() in {"pause", "pauseruntime", "pauseexecution", "pausepaperruntime"}:
            return self._execute_pause(command_id, entity_id, "pause", params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ResumePaperRuntime"} or action_id.lower() in {"resume", "unpause", "resumepaperruntime"}:
            return self._execute_pause(command_id, entity_id, "resume", params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"RestartPaperRuntime"} or action_id.lower() == "restartpaperruntime":
            return self._execute_repair_action(command_id, "RestartPaperRuntime", "/api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/restart", "runtime_id", entity_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"RestartTelemetryBridge"} or action_id.lower() == "restarttelemetrybridge":
            return self._execute_repair_action(command_id, "RestartTelemetryBridge", "/api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/telemetry-bridge/restart", "runtime_id", entity_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"TerminateStalePaperMonitoringSession"} or action_id.lower() == "terminatestalepapermonitoringsession":
            session_id = str(params.get("session_id") or entity_id)
            return self._execute_repair_action(command_id, "TerminateStalePaperMonitoringSession", "/api/internal/v1/runtime-repair/monitoring-sessions/{session_id}/terminate-stale", "session_id", session_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"StartPaperMonitoringSession"} or action_id.lower() == "startpapermonitoringsession":
            return self._execute_repair_action(command_id, "StartPaperMonitoringSession", "/api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/monitoring-sessions/start", "runtime_id", entity_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ProbeTelemetryIngest"} or action_id.lower() == "probetelemetryingest":
            return self._execute_repair_action(command_id, "ProbeTelemetryIngest", "/api/internal/v1/runtime-repair/paper-runtimes/{runtime_id}/telemetry-ingest/probe", "runtime_id", entity_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"IssueSafeMode"} or action_id.lower() == "issuesafemode":
            return self._execute_safe_mode(command_id, params)
        elif command_type in {"ExecuteRollback", "HardRollback"} or action_id.lower() in {"executerollback", "hardrollback", "rollback"}:
            return self._execute_rollback(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ApproveRollback"} or action_id.lower() == "approverollback":
            return self._execute_approve_rollback(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"RejectRollback"} or action_id.lower() == "rejectrollback":
            return self._execute_reject_rollback(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"ActivateKillSwitch", "IssueRiskOff"} or action_id.lower() in {"activatekillswitch", "issueriskoff", "killswitch"}:
            return self._execute_kill_switch(command_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Runtime action {action_id!r} on entity {entity_id!r} is unsupported.",
                action_id=action_id,
                entity_type="Runtime",
            )

    def _execute_start(
        self,
        command_id: str,
        runtime_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = runtime_id or str(params.get("runtime_id") or "").strip()
        if not target_id:
            raise ValueError("StartRuntime requires runtime_id.")
        confirm_token = str(params.get("confirm_token") or "").strip()
        if not confirm_token:
            raise ValueError("StartRuntime requires confirm_token.")

        two_man_token = params.get("two_man_token") or params.get("twoManToken") or params.get("two_man_signature_id") or ""
        payload = {
            "confirm_token": confirm_token,
            "command_id": command_id,
        }
        if two_man_token:
            payload["two_man_token"] = two_man_token

        url = internal_url(f"/api/internal/v1/runtimes/{quote(target_id, safe='')}/start")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Runtime",
            entity_id=target_id,
            action_id="StartRuntime",
            status=body.get("status", "accepted"),
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"runtime_id": target_id, "state": body.get("state", "starting")},
            extra={
                "runtime_id": target_id,
                "state": body.get("state", "starting"),
                "started_at": body.get("started_at") or utc_now(),
                "two_man_token": two_man_token or None,
            },
        )

    def _execute_pause(
        self,
        command_id: str,
        binding_id: str,
        pause_action: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = binding_id or str(params.get("runtime_binding_id") or params.get("binding_id") or params.get("runtime_id") or "").strip()
        if not target_id:
            raise ValueError(f"Pause/Resume runtime requires binding_id.")

        payload = {
            "pause_action": pause_action,
            "duration_seconds": params.get("duration_seconds", 3600),
            "reason": params.get("reason", f"Operator {pause_action}"),
        }
        if "pause_new_entries" in params:
            payload["pause_new_entries"] = params.get("pause_new_entries")
        if "cancel_open_orders" in params:
            payload["cancel_open_orders"] = params.get("cancel_open_orders")

        url = internal_url(f"/api/internal/v1/runtimes/{quote(target_id, safe='')}/pause")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Runtime",
            entity_id=target_id,
            action_id="PauseRuntime" if pause_action == "pause" else "ResumePaperRuntime",
            status=body.get("status") or "executed",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={
                "runtime_binding_id": target_id,
                "status_after": body.get("status_after") or body.get("status"),
                "pause_expires_at": body.get("pause_expires_at"),
            },
            extra={
                "runtime_binding_id": target_id,
                "pause_action": pause_action,
                "status_after": body.get("status_after"),
            },
        )

    def _execute_repair_action(
        self,
        command_id: str,
        action_name: str,
        path_template: str,
        target_key: str,
        target_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        actual_id = target_id or str(params.get(target_key) or "").strip()
        if not actual_id:
            raise ValueError(f"{action_name} requires {target_key}.")
        confirm_token = str(params.get("confirm_token") or "repair-confirm-token").strip()

        payload = {
            "command_id": command_id,
            "confirm_token": confirm_token,
            "reason": params.get("reason") or f"Operator repair {action_name}",
            "idempotency_key": params.get("idempotency_key") or command_id,
            "trace_id": params.get("trace_id"),
            "actor_id": params.get("actor_id") or "operator",
            "stage": params.get("stage") or "paper",
        }
        if "staleness_evidence" in params:
            payload["staleness_evidence"] = params["staleness_evidence"]

        url = runtime_repair_url(path_template.format(**{target_key: quote(actual_id, safe="")}))
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Runtime",
            entity_id=actual_id,
            action_id=action_name,
            status=body.get("status", "accepted"),
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={
                "target_id": actual_id,
                "heartbeat_freshness": body.get("heartbeat_freshness"),
                "telemetry_projection": body.get("telemetry_projection"),
            },
            extra={
                "audit_id": body.get("audit_id"),
                "heartbeat_freshness": body.get("heartbeat_freshness"),
            },
        )

    def _execute_safe_mode(self, command_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        capital_pool_id = str(params.get("capital_pool_id") or params.get("pool_id") or params.get("entity_id") or "").strip()
        if not capital_pool_id:
            raise ValueError("IssueSafeMode requires capital_pool_id.")

        target_state = str(params.get("target_state") or "guarded").strip()
        actor_id = str(params.get("actor_id") or "operator-command").strip()
        client = _get_runtime_manager_client()
        body = client.advance_safe_mode(
            capital_pool_id,
            target_state,
            actor_id=actor_id,
            note=params.get("reason"),
        )
        return build_domain_receipt(
            command_id=command_id,
            entity_type="Runtime",
            entity_id=capital_pool_id,
            action_id="IssueSafeMode",
            status="executed",
            dispatch_path="RuntimeManagerClient.advance_safe_mode",
            domain_receipt=body,
            authoritative_readback={"capital_pool_id": capital_pool_id, "safe_mode_state": body.get("safe_mode_state")},
            extra={
                "capital_pool_id": capital_pool_id,
                "safe_mode_after": body.get("safe_mode_state"),
                "actor_id": actor_id,
            },
        )

    def _execute_rollback(
        self,
        command_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = str(params.get("target_id") or params.get("entity_id") or params.get("binding_id") or "unknown").strip()
        import hashlib
        h = hashlib.sha256(command_id.encode("utf-8")).hexdigest()[:8]
        rollback_id = f"rb-{target_id}-{h}"

        payload = {
            "rollback_target_type": params.get("rollback_target_type", "deployment"),
            "target_id": target_id,
            "rollback_to_version": params.get("rollback_to_version", "previous"),
            "rollback_id": rollback_id,
        }
        if "rollback_action_type" in params:
            payload["rollback_action_type"] = params.get("rollback_action_type")
        if "target_artifact_id" in params:
            payload["target_artifact_id"] = params.get("target_artifact_id")

        url = internal_url("/api/internal/v1/rollbacks/execute")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="Rollback",
            entity_id=rollback_id,
            action_id="ExecuteRollback",
            status=body.get("status") or "completed",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"rollback_id": rollback_id, "status": body.get("status") or "completed"},
            extra={
                "rollback_id": rollback_id,
                "target_id": target_id,
                "tracking_url": body.get("tracking_url"),
            },
        )

    def _execute_approve_rollback(
        self,
        command_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        rollback_id = str(params.get("rollback_id") or params.get("entity_id") or "").strip()
        if not rollback_id:
            raise ValueError("ApproveRollback requires rollback_id.")
        payload = {"approval_notes": params.get("approval_notes", "Approved by operator")}
        url = internal_url(f"/api/internal/v1/rollbacks/{quote(rollback_id, safe='')}/approve")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        return build_domain_receipt(
            command_id=command_id,
            entity_type="Rollback",
            entity_id=rollback_id,
            action_id="ApproveRollback",
            status=body.get("status") or "approved",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"rollback_id": rollback_id, "status": "approved"},
            extra={"rollback_id": rollback_id},
        )

    def _execute_reject_rollback(
        self,
        command_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        rollback_id = str(params.get("rollback_id") or params.get("entity_id") or "").strip()
        if not rollback_id:
            raise ValueError("RejectRollback requires rollback_id.")
        payload = {"rejection_reason": params.get("rejection_reason", "Rejected by operator")}
        url = internal_url(f"/api/internal/v1/rollbacks/{quote(rollback_id, safe='')}/reject")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        return build_domain_receipt(
            command_id=command_id,
            entity_type="Rollback",
            entity_id=rollback_id,
            action_id="RejectRollback",
            status=body.get("status") or "rejected",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"rollback_id": rollback_id, "status": "rejected"},
            extra={"rollback_id": rollback_id},
        )

    def _execute_kill_switch(
        self,
        command_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "action": "activate",
            "scope": params.get("scope", "all"),
            "scope_id": params.get("scope_id") or params.get("capital_pool_id"),
            "severity": params.get("severity"),
            "reason": params.get("trigger_reason") or params.get("reason", "operator_emergency_stop"),
        }
        if "action_override" in params:
            payload["action_override"] = params.get("action_override")

        url = internal_url("/api/internal/v1/kill-switch")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)
        kill_switch_order_id = body.get("kill_switch_order_id") or f"ks-{uuid.uuid4().hex[:12]}"

        return build_domain_receipt(
            command_id=command_id,
            entity_type="KillSwitchOrder",
            entity_id=kill_switch_order_id,
            action_id="ActivateKillSwitch",
            status=body.get("status") or "active",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"kill_switch_order_id": kill_switch_order_id, "status": body.get("status")},
            extra={
                "kill_switch_order_id": kill_switch_order_id,
                "scope": body.get("scope"),
                "action": body.get("action"),
            },
        )
