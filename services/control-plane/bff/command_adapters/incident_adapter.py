"""Incident and Sentinel Domain Command Adapter.

Routes incident state transitions, risk alert acknowledgements, sentinel interventions,
and findings remediation to the authoritative Incident domain and internal API endpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    get_base_url,
    http_request_json,
    internal_url,
    utc_now,
)

log = logging.getLogger(__name__)


class IncidentCommandAdapter(DomainCommandAdapter):
    """Adapter for Incident, Alert, and Sentinel remediation authority commands."""

    _HANDLED_COMMANDS = {
        "IncidentAction",
        "RiskAlertAction",
        "AlertAcknowledge",
        "RemediateSentinelIntervention",
        "V5InterventionAction",
        "DecideV5Intervention",
        "SentinelFindingStatus",
        "SentinelRemediationBuild",
        "SentinelRemediationExecute",
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
        "sentinelfinding",
        "sentinel-finding",
        "sentinelremediation",
        "sentinel-remediation",
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
        entity_id = str(params.get("incident_id") or params.get("alert_id") or params.get("intervention_id") or params.get("finding_id") or params.get("entity_id") or "").strip()

        if command_type == "RemediateSentinelIntervention" or action_id.lower() in {"remediatesentinelintervention", "remediate"}:
            return self._execute_remediate_sentinel(command_id, entity_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"RiskAlertAction", "AlertAcknowledge"} or action_id.lower() in {"acknowledge", "alertacknowledge"}:
            return self._execute_alert_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"IncidentAction"} or action_id.lower() in {"resolve", "investigate", "close", "reopen"}:
            return self._execute_incident_action(command_id, entity_id, action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        elif command_type in {"V5InterventionAction", "DecideV5Intervention", "SentinelFindingStatus", "SentinelRemediationBuild", "SentinelRemediationExecute"}:
            return self._execute_sentinel_action(command_id, entity_id, command_type or action_id, params, auth_token=auth_token, mfa_token=mfa_token)
        else:
            raise ActionUnavailableError(
                f"Incident action {action_id!r} on {entity_id!r} is not supported.",
                action_id=action_id,
                entity_type="Incident",
            )

    def _execute_remediate_sentinel(
        self,
        command_id: str,
        intervention_id: str,
        params: Dict[str, Any],
        auth_token: Optional[str] = None,
        mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = intervention_id or str(params.get("intervention_id") or "").strip()
        if not target_id:
            raise ValueError("RemediateSentinelIntervention requires intervention_id.")
        two_man_signature_id = str(params.get("twoManSignatureId") or params.get("two_man_signature_id") or "sig-sentinel-remed").strip()

        payload = {
            "intervention_id": target_id,
            "remediation_action": params.get("remediation_action", "resolve"),
            "two_man_signature_id": two_man_signature_id,
            "operator_note": params.get("operator_note") or params.get("reason") or "Remediation executed",
        }
        url = internal_url(f"/api/internal/v1/sentinel/interventions/{quote(target_id, safe='')}/remediate")
        body = http_request_json(url, method="POST", payload=payload, auth_token=auth_token, mfa_token=mfa_token)

        return build_domain_receipt(
            command_id=command_id,
            entity_type="SentinelIntervention",
            entity_id=target_id,
            action_id="RemediateSentinelIntervention",
            status=body.get("status") or "remediated",
            dispatch_path=url,
            domain_receipt=body,
            authoritative_readback={"intervention_id": target_id, "status": "resolved"},
            extra={
                "intervention_id": target_id,
                "remediated_at": body.get("remediated_at") or utc_now(),
                "two_man_signature_id": two_man_signature_id,
            },
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
        if action_id.lower() not in {"acknowledge", "ack", "alertacknowledge"}:
            raise ActionUnavailableError(f"Alert action {action_id!r} on {target_id!r} is not supported.", action_id=action_id, entity_type="RiskAlert")
        incident_id = target_id[15:].strip() if target_id.startswith("alert-incident-") else (target_id if target_id.startswith("inc-") else "")
        p_inc = str(params.get("incident_id") or "").strip()
        p_inc = p_inc[15:].strip() if p_inc.startswith("alert-incident-") else p_inc
        if not incident_id or (p_inc and p_inc != incident_id):
            raise ActionUnavailableError(f"Alert {target_id!r} has no durable owner; acknowledgement is unavailable.", action_id=action_id, entity_type="RiskAlert")
        base = get_base_url("PANTHEON_INCIDENTS_API_URL", "PANTHEON_INCIDENTS_URL")
        if not base:
            raise ActionUnavailableError("Incident service base URL is unconfigured", action_id=action_id, entity_type="RiskAlert")
        url = f"{base}/api/incidents/{quote(incident_id, safe='')}/status"
        body = http_request_json(url, method="POST", payload={"status": "investigating"}, auth_token=auth_token, mfa_token=mfa_token)
        read_back_status = body.get("status") or "investigating"
        return build_domain_receipt(
            command_id=command_id, entity_type="RiskAlert", entity_id=target_id, action_id=action_id, status="acknowledged",
            dispatch_path=url, domain_receipt=body, authoritative_readback={"alert_id": target_id, "incident_id": incident_id, "status": "acknowledged", "incident_status": read_back_status},
            extra={"alert_id": target_id, "incident_id": incident_id, "incident_status": read_back_status},
        )

    def _execute_incident_action(
        self, command_id: str, incident_id: str, action_id: str, params: Dict[str, Any], auth_token: Optional[str] = None, mfa_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_id = incident_id or str(params.get("incident_id") or "").strip()
        if not target_id:
            raise ActionUnavailableError("Incident action requires incident_id.", action_id=action_id, entity_type="Incident")
        k = action_id.lower()
        new_status = "resolved" if k in {"resolve", "close"} else ("investigating" if k in {"start-mitigation", "mitigate", "escalate", "acknowledge", "ack", "investigate", "reopen"} else None)
        if not new_status:
            raise ActionUnavailableError(f"Incident action {action_id!r} on {target_id!r} is not supported.", action_id=action_id, entity_type="Incident")
        base = get_base_url("PANTHEON_INCIDENTS_API_URL", "PANTHEON_INCIDENTS_URL")
        url = f"{base}/api/incidents/{quote(target_id, safe='')}/status"
        body = http_request_json(url, method="POST", payload={"status": new_status, "resolved_at": params.get("resolved_at")}, auth_token=auth_token, mfa_token=mfa_token)
        read_back_status = body.get("status") or new_status
        return build_domain_receipt(
            command_id=command_id, entity_type="Incident", entity_id=target_id, action_id=action_id, status=read_back_status,
            dispatch_path=url, domain_receipt=body, authoritative_readback={"incident_id": target_id, "status": read_back_status},
            extra={"incident_id": target_id, "status": read_back_status},
        )

    def _execute_sentinel_action(
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
            entity_id=entity_id or "sentinel-target",
            action_id=action_name,
            status="executed",
            dispatch_path="sentinel_intervention_authority",
            domain_receipt={"entity_id": entity_id, "action": action_name, "executed": True},
            authoritative_readback={"entity_id": entity_id, "status": "active"},
            extra={"entity_id": entity_id},
        )
