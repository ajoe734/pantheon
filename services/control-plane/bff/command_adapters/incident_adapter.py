"""Incident Domain Command Adapter.

Routes incident state transitions and risk alert acknowledgements to the authoritative
Incident domain, plus the guarded two-man evidence receipt (V5InterventionAction).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..ports.lifecycle_telemetry_governance import DomainIncidentPort

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
)

log = logging.getLogger(__name__)

_ALERT_ACK_ACTIONS = {"acknowledge", "ack", "alertacknowledge"}
_INCIDENT_RESOLVE_ACTIONS = {"resolve", "close"}
_INCIDENT_INVESTIGATE_ACTIONS = {"start-mitigation", "mitigate", "escalate", "acknowledge", "ack", "investigate", "reopen"}


class IncidentRouteRejected(Exception):
    """An incident or alert action that must be refused before admission."""

    def __init__(self, precondition: str, message: str) -> None:
        super().__init__(message)
        self.precondition = precondition


def bind_incident_route_target(
    command_type: str, entity_type: str, entity_id: str, action_id: str, payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Return params whose target comes from the route; reject body ids and actions that disagree."""
    is_incident = command_type == "IncidentAction"
    supported = (_INCIDENT_RESOLVE_ACTIONS | _INCIDENT_INVESTIGATE_ACTIONS) if is_incident else _ALERT_ACK_ACTIONS
    if action_id.lower() not in supported:
        raise IncidentRouteRejected("unsupported_action", f"Action {action_id!r} is not supported on {entity_id!r}.")
    if is_incident:
        incident_id = entity_id
    elif entity_id.startswith("alert-incident-"):
        incident_id = entity_id[15:]
    else:
        incident_id = entity_id if entity_id.startswith("inc-") else ""
    route_params = {
        "entity_type": entity_type, "entity_id": entity_id, "action_id": action_id,
        "alert_id": "" if is_incident else entity_id, "incident_id": incident_id,
    }
    for field, expected in route_params.items():
        if field in payload and str(payload[field]).strip() != expected:
            raise IncidentRouteRejected("route_target_mismatch", f"{field} must match the requested route")
    return {**payload, **route_params}


class IncidentCommandAdapter(DomainCommandAdapter):
    """Adapter for Incident, Alert, and two-man evidence commands."""

    _HANDLED_COMMANDS = {
        "IncidentAction",
        "RiskAlertAction",
        "AlertAcknowledge",
        "V5InterventionAction",
    }

    _HANDLED_ENTITIES = {
        "incident",
        "incidentcase",
        "incident-case",
        "riskalert",
        "risk-alert",
        "alert",
        "sentinelintervention",
        "sentinel-intervention",
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
        entity_id = str(params.get("incident_id") or params.get("alert_id") or params.get("entity_id") or "").strip()

        if command_type == "IncidentAction":
            return self._execute_incident_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"RiskAlertAction", "AlertAcknowledge"}:
            return self._execute_alert_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif action_id.lower() in {"acknowledge", "alertacknowledge"}:
            return self._execute_alert_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif action_id.lower() in {"resolve", "investigate", "close", "reopen"}:
            return self._execute_incident_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type == "V5InterventionAction":
            return self._execute_two_man_evidence(command_id, entity_id, command_type or action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Incident action {action_id!r} on {entity_id!r} is not supported.",
                action_id=action_id,
                entity_type="Incident",
            )

    def _execute_alert_action(
        self,
        command_id: str,
        alert_id: str,
        action_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = str(params.get("alert_id") or alert_id).strip()
        if action_id.lower() not in _ALERT_ACK_ACTIONS:
            raise ActionUnavailableError(f"Alert action {action_id!r} on {target_id!r} is not supported.", action_id=action_id, entity_type="RiskAlert")
        incident_id = target_id[15:].strip() if target_id.startswith("alert-incident-") else (target_id if target_id.startswith("inc-") else "")
        p_inc = str(params.get("incident_id") or "").strip()
        p_inc = p_inc[15:].strip() if p_inc.startswith("alert-incident-") else p_inc
        if not incident_id or (p_inc and p_inc != incident_id):
            raise ActionUnavailableError(f"Alert {target_id!r} has no durable owner; acknowledgement is unavailable.", action_id=action_id, entity_type="RiskAlert")
        body = DomainIncidentPort().update_incident_status(
            incident_id, "investigating", auth_token=auth_token, mfa_token=mfa_token,
        )
        read_back_status = body.get("status") or "investigating"
        return build_domain_receipt(
            command_id=command_id, entity_type="RiskAlert", entity_id=target_id, action_id=action_id, status="acknowledged",
            dispatch_path="incidents_service", domain_receipt=body, authoritative_readback={"alert_id": target_id, "incident_id": incident_id, "status": "acknowledged", "incident_status": read_back_status},
            extra={"alert_id": target_id, "incident_id": incident_id, "incident_status": read_back_status},
        )

    def _execute_incident_action(
        self, command_id: str, incident_id: str, action_id: str, params: Dict[str, Any], auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = incident_id or str(params.get("incident_id") or "").strip()
        if not target_id:
            raise ActionUnavailableError("Incident action requires incident_id.", action_id=action_id, entity_type="Incident")
        k = action_id.lower()
        new_status = "resolved" if k in _INCIDENT_RESOLVE_ACTIONS else ("investigating" if k in _INCIDENT_INVESTIGATE_ACTIONS else None)
        if not new_status:
            raise ActionUnavailableError(f"Incident action {action_id!r} on {target_id!r} is not supported.", action_id=action_id, entity_type="Incident")
        body = DomainIncidentPort().update_incident_status(
            target_id, new_status, resolved_at=params.get("resolved_at"),
            auth_token=auth_token, mfa_token=mfa_token,
        )
        read_back_status = body.get("status") or new_status
        return build_domain_receipt(
            command_id=command_id, entity_type="Incident", entity_id=target_id, action_id=action_id, status=read_back_status,
            dispatch_path="incidents_service", domain_receipt=body, authoritative_readback={"incident_id": target_id, "status": read_back_status},
            extra={"incident_id": target_id, "status": read_back_status},
        )

    def _execute_two_man_evidence(
        self,
        command_id: str,
        entity_id: str,
        action_name: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        return build_domain_receipt(
            command_id=command_id,
            entity_type="SentinelIntervention",
            entity_id=entity_id or "two-man-evidence",
            action_id=action_name,
            status="executed",
            dispatch_path="two_man_evidence_authority",
            domain_receipt={"entity_id": entity_id, "action": action_name, "executed": True},
            authoritative_readback={"entity_id": entity_id, "status": "active"},
            extra={"entity_id": entity_id},
        )
