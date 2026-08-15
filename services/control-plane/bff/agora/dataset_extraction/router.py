"""Agora dataset extraction BFF router — agora.dataset.v1.

Governance boundary: evidence-only surface.
  - Evidence never promotes artifacts (no_promote_proof)
  - Evidence never mutates RuntimeBinding or capital (no_runtime_mutation_proof)
  - No order routing, no broker calls, no live-capital writes

Routes:
  POST /bff/agora/interaction-evidence                  — admit evidence (idempotent, admit-only)
  GET  /bff/agora/interaction-evidence/{evidence_id}    — get a specific record
  GET  /bff/agora/datasets/{dataset_kind}               — list dataset records
  POST /bff/agora/dataset-worker/process                — trigger worker claim & extraction
  GET  /bff/agora/dataset-worker/backlog                — list pending inbox items
  GET  /bff/agora/dataset-worker/dlq                    — list failed DLQ items
  POST /bff/agora/dataset-worker/dlq/{evidence_id}/replay — replay a failed DLQ item
  GET  /bff/agora/dataset-worker/handoffs               — list dataset handoffs
  POST /bff/agora/dataset-worker/handoffs/{handoff_id}/ack — acknowledge handoff
  GET  /internal/agora/dataset-handoffs                  — tenant-scoped service drain
  POST /internal/agora/dataset-handoffs/{handoff_id}/ack — service acknowledgement
"""
from __future__ import annotations

import hmac
import os
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from .models import (
    AgoraInteractionEvidenceRequest,
    DatasetAdmissionReceipt,
    DatasetKind,
    HandoffAcknowledgementRequest,
    route_to_dataset,
)
from .extractor import (
    AgoraDatasetStore,
    HandoffConflictError,
    IdempotencyConflictError,
    PrivacyConsentError,
    acknowledgement_request_digest,
    admit_evidence,
    evidence_request_digest,
    _dataset_version_id,
)


_CAPABILITY = "agora.dataset.v1"
_NO_PROMOTE_PROOF = "agora_observe_learn_only"
_NO_RUNTIME_MUTATION_PROOF = "agora_evidence_extract_only"

_STORE: Optional[AgoraDatasetStore] = None
_SERVICE_ACTOR = "policy-learning-agora-handoff-drainer"
_LOCAL_SERVICE_TOKEN = "pantheon-local-agora-handoff-service-token"


def _default_store() -> AgoraDatasetStore:
    global _STORE
    if _STORE is None:
        _STORE = AgoraDatasetStore()
    return _STORE


def create_dataset_extraction_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    dataset_store: Optional[AgoraDatasetStore] = None,
) -> APIRouter:
    """Build and return the Agora dataset extraction APIRouter.

    ``dataset_store`` may be injected for tests. When omitted the module-level
    singleton is used.
    """
    store = dataset_store if dataset_store is not None else _default_store()
    router = APIRouter(tags=["agora-dataset"])

    def _meta(**extra: Any) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "snapshot_at": utc_now(),
            "capability": _CAPABILITY,
            "no_promote_proof": _NO_PROMOTE_PROOF,
            "no_runtime_mutation_proof": _NO_RUNTIME_MUTATION_PROOF,
        }
        meta.update({k: v for k, v in extra.items() if v is not None})
        return meta

    def _error_code() -> Any:
        try:
            from ...models import ErrorCode
            return ErrorCode
        except Exception:
            try:
                from models import ErrorCode  # type: ignore[import]
                return ErrorCode
            except Exception:
                class FallbackErrorCode:
                    VALIDATION_FAILED = "VALIDATION_FAILED"
                    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
                    AUTH_REQUIRED = "AUTH_REQUIRED"
                    FORBIDDEN = "FORBIDDEN"
                    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
                    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
                return FallbackErrorCode

    def _require_idempotency_key(key: Optional[str]) -> str:
        resolved = (key or "").strip()
        if not resolved:
            ErrorCode = _error_code()
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )
        return resolved

    def _scope(identity: Any, requested_tenant_id: Optional[str]) -> Any:
        try:
            return resolve_agora_user_scope(
                identity,
                utc_now=utc_now,
                requested_tenant_id=requested_tenant_id,
            )
        except AgoraScopeResolutionError as exc:
            ErrorCode = _error_code()
            code = ErrorCode.AUTH_REQUIRED if exc.status_code == 401 else ErrorCode.FORBIDDEN
            raise bff_error(
                exc.status_code,
                code,
                exc.message,
                exc.reason,
                precondition_failed="agora_user_scope",
                details_extra=exc.details,
            )

    def _service_tenant_scope(
        authorization: Optional[str],
        service_actor: Optional[str],
        requested_tenant_id: Optional[str],
    ) -> tuple[str, str]:
        """Authenticate the non-user Agora-to-policy-learning boundary."""

        expected_token = str(os.getenv("AGORA_HANDOFF_SERVICE_TOKEN") or "").strip()
        expected_actor = str(
            os.getenv("AGORA_HANDOFF_ALLOWED_SERVICE_ACTOR") or _SERVICE_ACTOR
        ).strip()
        allowed_tenants = {
            item.strip()
            for item in str(
                os.getenv("AGORA_HANDOFF_SERVICE_TENANTS") or ""
            ).split(",")
            if item.strip()
        }
        posture = str(
            os.getenv("PANTHEON_PERSISTENCE_POSTURE")
            or os.getenv("PANTHEON_ENV")
            or "dev"
        ).strip().lower()
        enforced = posture in {
            "stage",
            "staging",
            "staging-live",
            "prod",
            "production",
        } or posture.startswith(("staging", "prod"))
        if (
            not expected_token
            or not expected_actor
            or not allowed_tenants
            or (enforced and expected_token == _LOCAL_SERVICE_TOKEN)
        ):
            raise HTTPException(
                status_code=503,
                detail="Agora handoff service boundary is not safely configured",
            )
        scheme, _, supplied_token = str(authorization or "").partition(" ")
        if (
            scheme.lower() != "bearer"
            or not supplied_token
            or not hmac.compare_digest(supplied_token, expected_token)
        ):
            raise HTTPException(
                status_code=401,
                detail="Agora handoff service credential is invalid",
            )
        actor = str(service_actor or "").strip()
        if actor != expected_actor:
            raise HTTPException(
                status_code=403,
                detail="Agora handoff service actor is outside authority",
            )
        tenant_id = str(requested_tenant_id or "").strip()
        if not tenant_id or tenant_id not in allowed_tenants:
            raise HTTPException(
                status_code=403,
                detail="Agora handoff tenant is outside service authority",
            )
        return actor, tenant_id

    def _raise_idempotency_conflict(exc: Exception) -> None:
        ErrorCode = _error_code()
        raise bff_error(
            409,
            ErrorCode.IDEMPOTENCY_CONFLICT,
            str(exc),
            "AGORA_DATASET_IDEMPOTENCY_CONFLICT",
            precondition_failed="idempotency_conflict",
            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
        )

    # ------------------------------------------------------------------
    # POST /bff/agora/interaction-evidence (admit-only)
    # ------------------------------------------------------------------

    @router.post("/bff/agora/interaction-evidence", status_code=201)
    def submit_interaction_evidence(
        body: AgoraInteractionEvidenceRequest,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Submit an Agora interaction evidence record for dataset extraction (admit-only).

        Atomically persists eligible evidence into the durable inbox and returns
        an admission receipt immediately without invoking process_inbox or waiting
        for worker completion.

        Routes evidence to the governed learning dataset:
          observe — ask, journal, note, insight (passive observation)
          learn   — feedback, training_example (supervised signal)

        Idempotent by evidence_id: a second call with the same evidence_id
        returns the existing admission record with idempotent=true and does not modify state.

        Governance proof:
          no_promote_proof         = agora_observe_learn_only
          no_runtime_mutation_proof = agora_evidence_extract_only
        """
        identity = extract_identity(authorization)
        require_write_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)
        resolved_key = _require_idempotency_key(idempotency_key)
        digest = evidence_request_digest(body)
        try:
            entry, is_new = admit_evidence(
                body,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                idempotency_key=resolved_key,
                request_digest=digest,
                admitted_at=utc_now(),
                store=store,
            )
        except IdempotencyConflictError as exc:
            _raise_idempotency_conflict(exc)
        except PrivacyConsentError as exc:
            ErrorCode = _error_code()
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                str(exc),
                "AGORA_DATASET_CONSENT_REQUIRED",
                precondition_failed="dataset_consent",
            )

        kind = route_to_dataset(entry["interaction_kind"])
        version_id = _dataset_version_id(
            tenant_id=entry["tenant_id"],
            user_id=entry["user_id"],
            evidence_id=entry["evidence_id"],
            request_digest=entry["request_digest"],
        )
        receipt_data = {
            "evidence_id": entry["evidence_id"],
            "dataset_version_id": version_id,
            "dataset_kind": kind.value,
            "interaction_kind": entry["interaction_kind"],
            "persona_id": entry["persona_id"],
            "session_id": entry.get("session_id"),
            "tenant_id": entry["tenant_id"],
            "user_id": entry["user_id"],
            "content": entry["content"],
            "source_refs": entry["source_refs"],
            "learning_eligible": entry["learning_eligible"],
            "governance_boundary": "observe_or_learn_only",
            "no_promote_proof": "agora_observe_learn_only",
            "no_runtime_mutation_proof": "agora_evidence_extract_only",
            "captured_at": entry["captured_at"],
            "extracted_at": entry["extracted_at"],
            "status": entry.get("status", "pending"),
            "idempotent": not is_new,
            "version": 1,
            "admission_receipt_id": entry.get("admission_receipt_id") or f"rcpt-adm-{entry['evidence_id']}",
            "consent_granted": entry.get("consent_granted", True),
            "purpose": entry.get("purpose", "policy_learning"),
            "retention_days": entry.get("retention_days", 30),
            "is_raw_conversation": entry.get("is_raw_conversation", False),
            "explicit_conversation_consent": entry.get("explicit_conversation_consent", False),
        }
        return {
            "status": "created" if is_new else "exists",
            "idempotent": not is_new,
            "data": receipt_data,
            "meta": _meta(
                audience=f"tenant:{entry['tenant_id']}:user:{entry['user_id']}",
                evidence_id=entry["evidence_id"],
                status=entry.get("status", "pending"),
            ),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/interaction-evidence/{evidence_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/interaction-evidence/{evidence_id}")
    def get_interaction_evidence(
        evidence_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return a specific interaction evidence record."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)

        record = store.get(
            evidence_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        if record is None:
            inbox_entry = store.get_inbox_entry(
                evidence_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if inbox_entry is not None:
                from .models import DatasetRecord, InteractionKind
                kind = route_to_dataset(inbox_entry["interaction_kind"])
                version_id = _dataset_version_id(
                    tenant_id=inbox_entry["tenant_id"],
                    user_id=inbox_entry["user_id"],
                    evidence_id=inbox_entry["evidence_id"],
                    request_digest=inbox_entry["request_digest"],
                )
                record = DatasetRecord(
                    evidence_id=inbox_entry["evidence_id"],
                    dataset_version_id=version_id,
                    dataset_kind=kind,
                    interaction_kind=InteractionKind(inbox_entry["interaction_kind"]),
                    persona_id=inbox_entry["persona_id"],
                    session_id=inbox_entry.get("session_id"),
                    tenant_id=inbox_entry["tenant_id"],
                    user_id=inbox_entry["user_id"],
                    content=inbox_entry["content"],
                    source_refs=inbox_entry["source_refs"],
                    learning_eligible=inbox_entry["learning_eligible"],
                    captured_at=inbox_entry["captured_at"],
                    extracted_at=inbox_entry["extracted_at"],
                    version=1,
                    status=inbox_entry.get("status", "pending"),
                    consent_verified=bool(inbox_entry.get("consent_granted", True)),
                    redaction_applied=False,
                    purpose=inbox_entry.get("purpose"),
                    retention_days=inbox_entry.get("retention_days"),
                    admission_receipt_id=inbox_entry.get("admission_receipt_id") or f"rcpt-adm-{inbox_entry['evidence_id']}",
                )
        if record is None:
            ErrorCode = _error_code()
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Interaction evidence not found",
                evidence_id,
            )
        return {
            "status": "found",
            "data": record.model_dump(),
            "meta": _meta(
                audience=f"tenant:{record.tenant_id}:user:{record.user_id}",
                status=record.status,
            ),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/datasets/{dataset_kind}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/datasets/{dataset_kind}")
    def list_dataset_records(
        dataset_kind: str,
        authorization: Optional[str] = Header(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """List records in a governed dataset bucket (observe or learn).

        Scoped to the authenticated operator's tenant and user.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)

        valid_kinds = {DatasetKind.OBSERVE.value, DatasetKind.LEARN.value}
        if dataset_kind not in valid_kinds:
            ErrorCode = _error_code()
            raise bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                f"dataset_kind must be one of: {', '.join(sorted(valid_kinds))}",
                dataset_kind,
            )

        kind = DatasetKind(dataset_kind)
        tenant_id = scope.tenant_id
        user_id = scope.user_id

        records = store.list_by_dataset(
            kind,
            tenant_id=tenant_id,
            user_id=user_id,
            page_size=page_size,
        )
        return {
            "dataset_kind": kind.value,
            "items": [r.model_dump() for r in records],
            "total": len(records),
            "meta": _meta(audience=f"tenant:{tenant_id}:user:{user_id}"),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/dataset-worker/process
    # ------------------------------------------------------------------
    @router.post("/bff/agora/dataset-worker/process")
    def process_dataset_inbox(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
        x_worker_id: Optional[str] = Header(default=None, alias="X-Worker-Id"),
        batch_size: int = Query(default=25, ge=1, le=100),
        lease_seconds: int = Query(default=30, ge=1, le=300),
    ) -> Dict[str, Any]:
        """Manually trigger the default worker to process the pending inbox entries."""
        identity = extract_identity(authorization)
        require_write_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)

        result = store.process_inbox(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            worker_id=x_worker_id,
            batch_size=batch_size,
            lease_seconds=lease_seconds,
        )
        return {
            "status": "success",
            "data": result,
            "meta": _meta(audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}"),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/dataset-worker/backlog
    # ------------------------------------------------------------------
    @router.get("/bff/agora/dataset-worker/backlog")
    def get_dataset_backlog(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the list of pending items in the durable inbox."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)
        tenant_id = scope.tenant_id
        user_id = scope.user_id
        items = store.get_backlog(tenant_id=tenant_id, user_id=user_id)
        return {
            "status": "success",
            "items": items,
            "total": len(items),
            "meta": _meta(audience=f"tenant:{tenant_id}:user:{user_id}"),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/dataset-worker/dlq
    # ------------------------------------------------------------------
    @router.get("/bff/agora/dataset-worker/dlq")
    def get_dataset_dlq(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the list of failed items in the durable inbox (DLQ)."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)
        tenant_id = scope.tenant_id
        user_id = scope.user_id
        items = store.get_dlq(tenant_id=tenant_id, user_id=user_id)
        return {
            "status": "success",
            "items": items,
            "total": len(items),
            "meta": _meta(audience=f"tenant:{tenant_id}:user:{user_id}"),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/dataset-worker/dlq/{evidence_id}/replay
    # ------------------------------------------------------------------
    @router.post("/bff/agora/dataset-worker/dlq/{evidence_id}/replay")
    def replay_dataset_dlq_item(
        evidence_id: str,
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Reset a failed inbox item back to pending."""
        identity = extract_identity(authorization)
        require_write_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)

        success = store.replay_dlq_item(
            evidence_id,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        if not success:
            ErrorCode = _error_code()
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Failed or matching interaction evidence not found in DLQ",
                evidence_id,
            )
        return {
            "status": "success",
            "meta": _meta(audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}"),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/dataset-worker/handoffs
    # ------------------------------------------------------------------
    @router.get("/bff/agora/dataset-worker/handoffs")
    def get_dataset_handoffs(
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Return the list of generated evidence handoffs."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)
        tenant_id = scope.tenant_id
        user_id = scope.user_id
        items = store.list_handoffs(tenant_id=tenant_id, user_id=user_id)
        return {
            "status": "success",
            "items": items,
            "total": len(items),
            "meta": _meta(audience=f"tenant:{tenant_id}:user:{user_id}"),
        }

    # ------------------------------------------------------------------
    # POST /bff/agora/dataset-worker/handoffs/{handoff_id}/ack
    # ------------------------------------------------------------------
    @router.post("/bff/agora/dataset-worker/handoffs/{handoff_id}/ack")
    def acknowledge_dataset_handoff(
        handoff_id: str,
        body: HandoffAcknowledgementRequest,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        """Acknowledge durable downstream completion exactly once."""

        identity = extract_identity(authorization)
        require_write_role(identity)
        scope = _scope(identity, x_tenant_id or x_pantheon_tenant)
        _require_idempotency_key(idempotency_key)
        digest = acknowledgement_request_digest(
            handoff_id=handoff_id,
            acknowledgement_id=body.acknowledgement_id,
            dataset_version_id=body.dataset_version_id,
            downstream_ref=body.downstream_ref,
        )
        try:
            handoff, acknowledged = store.acknowledge_handoff(
                handoff_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                acknowledgement_id=body.acknowledgement_id,
                dataset_version_id=body.dataset_version_id,
                downstream_ref=body.downstream_ref,
                acknowledged_by=scope.operator_id,
                request_digest=digest,
                acknowledged_at=utc_now(),
            )
        except IdempotencyConflictError as exc:
            _raise_idempotency_conflict(exc)
        except HandoffConflictError as exc:
            ErrorCode = _error_code()
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                str(exc),
                "AGORA_DATASET_VERSION_MISMATCH",
                precondition_failed="dataset_version_match",
            )
        if handoff is None:
            ErrorCode = _error_code()
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Dataset handoff not found",
                handoff_id,
            )
        return {
            "status": "acknowledged" if acknowledged else "exists",
            "idempotent": not acknowledged,
            "data": handoff,
            "meta": _meta(audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}"),
        }

    # ------------------------------------------------------------------
    # Dedicated service boundary. These routes intentionally do not call
    # extract_identity: a policy worker is not an Agora browser user and must
    # not invent a user_id merely to reach tenant-owned handoffs.
    # ------------------------------------------------------------------
    @router.get("/internal/agora/dataset-handoffs")
    def get_tenant_dataset_handoffs(
        authorization: Optional[str] = Header(default=None),
        service_actor: Optional[str] = Header(
            default=None,
            alias="X-Pantheon-Service-Actor",
        ),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        actor, tenant_id = _service_tenant_scope(
            authorization,
            service_actor,
            x_tenant_id,
        )
        items = store.list_handoffs_for_tenant(
            tenant_id=tenant_id,
            pending_only=True,
        )
        return {
            "status": "success",
            "items": items,
            "total": len(items),
            "meta": _meta(
                audience=f"tenant:{tenant_id}:service:{actor}",
                pending_only=True,
            ),
        }

    @router.post("/internal/agora/dataset-handoffs/{handoff_id}/ack")
    def acknowledge_tenant_dataset_handoff(
        handoff_id: str,
        body: HandoffAcknowledgementRequest,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(
            default=None,
            alias="Idempotency-Key",
        ),
        service_actor: Optional[str] = Header(
            default=None,
            alias="X-Pantheon-Service-Actor",
        ),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
    ) -> Dict[str, Any]:
        actor, tenant_id = _service_tenant_scope(
            authorization,
            service_actor,
            x_tenant_id,
        )
        resolved_key = _require_idempotency_key(idempotency_key)
        if resolved_key != f"ack-{handoff_id}":
            ErrorCode = _error_code()
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key does not match the Agora handoff identity",
                "invalid_handoff_idempotency_key",
            )
        digest = acknowledgement_request_digest(
            handoff_id=handoff_id,
            acknowledgement_id=body.acknowledgement_id,
            dataset_version_id=body.dataset_version_id,
            downstream_ref=body.downstream_ref,
        )
        try:
            handoff, acknowledged = store.acknowledge_handoff_for_tenant(
                handoff_id,
                tenant_id=tenant_id,
                acknowledgement_id=body.acknowledgement_id,
                dataset_version_id=body.dataset_version_id,
                downstream_ref=body.downstream_ref,
                acknowledged_by=actor,
                request_digest=digest,
                acknowledged_at=utc_now(),
            )
        except IdempotencyConflictError as exc:
            _raise_idempotency_conflict(exc)
        except HandoffConflictError as exc:
            ErrorCode = _error_code()
            raise bff_error(
                409,
                ErrorCode.RESOURCE_CONFLICT,
                str(exc),
                "AGORA_DATASET_VERSION_MISMATCH",
                precondition_failed="dataset_version_match",
            )
        if handoff is None:
            ErrorCode = _error_code()
            raise bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Dataset handoff not found",
                handoff_id,
            )
        return {
            "status": "acknowledged" if acknowledged else "exists",
            "idempotent": not acknowledged,
            "data": handoff,
            "meta": _meta(audience=f"tenant:{tenant_id}:service:{actor}"),
        }

    return router


__all__ = ["create_dataset_extraction_router"]
