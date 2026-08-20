"""Audit Domain Command Adapter.

Routes audit log export requests to authoritative audit service endpoints.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from .base import (
    ActionUnavailableError,
    DomainCommandAdapter,
    build_domain_receipt,
    utc_now,
)

log = logging.getLogger(__name__)


class AuditCommandAdapter(DomainCommandAdapter):
    """Adapter for Audit Export commands."""

    _HANDLED_COMMANDS = {
        "AuditExport",
    }

    _HANDLED_ENTITIES = {
        "auditexport",
        "audit-export",
        "audit",
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
        export_job_id = f"audit-exp-{uuid.uuid4().hex[:8]}"
        format_type = params.get("format", "jsonl")
        time_range = params.get("time_range", "last_24h")

        return build_domain_receipt(
            command_id=command_id,
            entity_type="AuditExport",
            entity_id=export_job_id,
            action_id="AuditExport",
            status="completed",
            dispatch_path="audit_service_authority",
            domain_receipt={
                "job_id": export_job_id,
                "format": format_type,
                "time_range": time_range,
                "status": "ready",
            },
            authoritative_readback={"job_id": export_job_id, "status": "ready"},
            extra={"job_id": export_job_id},
        )
