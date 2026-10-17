"""Consultation client integration for Persona Teaching.

Provides conditional Consultation handoff when Teaching policy determines that an
advisory review is required (consultation_required=True).

Important Rule:
- Teaching creates a ConsultRequest via this client.
- Teaching stores the ConsultRequest ID and receipt.
- Teaching MUST NOT write a ConsultMemo directly (ConsultMemo authority belongs to Consultation domain).
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from jsonschema import Draft7Validator


def _consult_request_schema_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "consultation"
        / "consult_request.schema.json"
    )


def load_consult_request_schema() -> Dict[str, Any]:
    path = _consult_request_schema_path()
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def validate_consult_request_payload(payload: Mapping[str, Any]) -> None:
    schema = load_consult_request_schema()
    if schema:
        validator = Draft7Validator(schema)
        errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
        if errors:
            first = errors[0]
            loc = ".".join(str(p) for p in first.path) or "<root>"
            raise ValueError(f"ConsultRequest schema validation failed at {loc}: {first.message}")


@dataclass(frozen=True)
class TeachingConsultationReceipt:
    consult_request_id: str
    tenant_id: str
    session_id: str
    eval_id: str
    status: str
    submitted_at: str
    receipt_id: str
    request_payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consult_request_id": self.consult_request_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "eval_id": self.eval_id,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "receipt_id": self.receipt_id,
            "request_payload": dict(self.request_payload),
        }


class TrainingConsultationClient:
    """Client for creating ConsultRequest from TeachingSession evaluation failures/advisories."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        token: Optional[str] = None,
        service_actor: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("PANTHEON_CONSULTATION_API_URL") or os.getenv("PANTHEON_CONSULTATION_SERVICE_URL") or "").strip().rstrip("/")
        self.token = token.strip() if token is not None else (os.getenv("CONSULTATION_SERVICE_TOKEN") or os.getenv("CONSULTATION_API_TOKEN") or "").strip()
        self.service_actor = (service_actor or os.getenv("CONSULTATION_SERVICE_ACTOR") or "training-session").strip()

    def create_teaching_consult_request(
        self,
        *,
        session_id: str,
        tenant_id: str,
        persona_id: str,
        eval_id: str,
        validation_errors: Sequence[str],
        metrics: Mapping[str, Any],
        trace_id: str,
        requested_by: str = "training-session",
        priority: str = "normal",
        created_at: Optional[str] = None,
    ) -> TeachingConsultationReceipt:
        now_str = created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        # Scope request identity to tenant/session/evaluation so create or submit readback failure
        # cannot generate a different duplicate on the same operation.
        request_digest = hashlib.sha256(
            f"{tenant_id}:{session_id}:{eval_id}".encode("utf-8")
        ).hexdigest()[:12]
        request_id = f"creq-teach-{request_digest}"
        receipt_id = f"rcpt-teach-{request_digest}"

        actor_type = "operator" if requested_by == "operator" else "service"
        request_payload: Dict[str, Any] = {
            "request_id": request_id,
            "tenant_id": tenant_id,
            "request_type": "persona_policy",
            "requested_by": {
                "actor_type": actor_type,
                "actor_id": requested_by,
            },
            "from_persona_id": persona_id,
            "target_type": "teaching_session",
            "target_id": session_id,
            "task": "persona_teaching_advisory",
            "consultation_type": "teaching_policy_review",
            "context_refs": [
                {"type": "teaching_session", "id": session_id},
                {"type": "teaching_eval", "id": eval_id},
            ],
            "evidence_refs": [
                f"trainer-preview:{session_id}:{eval_id}",
            ],
            "priority": priority if priority in ("low", "normal", "high", "urgent") else "normal",
            "status": "draft",
            "metadata": {
                "source": "training-session",
                "eval_id": eval_id,
                "persona_id": persona_id,
                "validation_errors": list(validation_errors),
                "metrics": dict(metrics),
            },
            "trace_id": trace_id or f"trace-{uuid.uuid4().hex[:12]}",
            "created_at": now_str,
        }

        # Validate against canonical ConsultRequest schema
        validate_consult_request_payload(request_payload)

        # Require real Consultation owner acknowledgement (create -> submit -> same-ID GET readback)
        if not self.base_url:
            from services.consultation.client import ConsultationClientError
            raise ConsultationClientError("consultation service URL is not configured")

        from services.consultation.client import ConsultationClientError, ConsultationServiceClient

        client = ConsultationServiceClient(
            base_url=self.base_url,
            token=self.token,
            service_actor=self.service_actor,
            tenant_id=tenant_id,
        )

        # Step 1: Create canonical ConsultRequest in DRAFT state
        client.create_request(request_payload, tenant_id=tenant_id)

        # Step 2: Submit the created request
        client.submit_request(request_id, tenant_id=tenant_id)

        # Step 3: GET readback on the same request_id to verify owner acknowledgement
        ack = client.get_request(request_id, tenant_id=tenant_id)
        if not isinstance(ack, dict):
            raise ConsultationClientError(f"consultation request {request_id} not found on readback")

        ack_status = str(ack.get("status") or "")
        if ack_status not in ("submitted", "assigned", "in_progress", "memo_pending", "published"):
            raise ConsultationClientError(f"consultation request status {ack_status} is not acknowledged")

        if ack.get("tenant_id") != tenant_id:
            raise ConsultationClientError(f"consultation request tenant {ack.get('tenant_id')} does not match {tenant_id}")

        if ack.get("target_id") != session_id or ack.get("target_type") != "teaching_session":
            raise ConsultationClientError("consultation request target does not match session")

        ack_metadata = ack.get("metadata") if isinstance(ack.get("metadata"), dict) else {}
        if ack_metadata.get("eval_id") != eval_id:
            raise ConsultationClientError("consultation request metadata eval_id does not match")

        return TeachingConsultationReceipt(
            consult_request_id=request_id,
            tenant_id=tenant_id,
            session_id=session_id,
            eval_id=eval_id,
            status=ack_status,
            submitted_at=now_str,
            receipt_id=receipt_id,
            request_payload=ack,
        )


__all__ = [
    "TeachingConsultationReceipt",
    "TrainingConsultationClient",
    "load_consult_request_schema",
    "validate_consult_request_payload",
]
