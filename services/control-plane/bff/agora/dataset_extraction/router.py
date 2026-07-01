"""Agora dataset extraction BFF router — agora.dataset.v1.

Governance boundary: evidence-only surface.
  - Evidence never promotes artifacts (no_promote_proof)
  - Evidence never mutates RuntimeBinding or capital (no_runtime_mutation_proof)
  - No order routing, no broker calls, no live-capital writes

Routes:
  POST /bff/agora/interaction-evidence                  — submit evidence (idempotent)
  GET  /bff/agora/interaction-evidence/{evidence_id}    — get a specific record
  GET  /bff/agora/datasets/{dataset_kind}               — list dataset records
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from .models import AgoraInteractionEvidenceRequest, DatasetKind
from .extractor import AgoraDatasetStore, extract_evidence


_CAPABILITY = "agora.dataset.v1"
_NO_PROMOTE_PROOF = "agora_observe_learn_only"
_NO_RUNTIME_MUTATION_PROOF = "agora_evidence_extract_only"

_STORE: Optional[AgoraDatasetStore] = None


def _default_store() -> AgoraDatasetStore:
    global _STORE
    if _STORE is None:
        _STORE = AgoraDatasetStore()
    return _STORE


def create_dataset_extraction_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    dataset_store: Optional[AgoraDatasetStore] = None,
) -> APIRouter:
    """Build and return the Agora dataset extraction APIRouter.

    ``dataset_store`` may be injected for tests.  When omitted the module-level
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
            from models import ErrorCode  # production: bff/models.py in sys.path
        except (ImportError, ModuleNotFoundError):
            from bff.models import ErrorCode  # test: bff is a sub-package
        return ErrorCode

    def _require_idempotency_key(key: Optional[str]) -> None:
        if not (key or "").strip():
            ErrorCode = _error_code()
            raise bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Idempotency-Key header is required",
                "missing_idempotency_key",
                suggestion="Supply a UUID v4 in the Idempotency-Key request header",
            )

    # ------------------------------------------------------------------
    # POST /bff/agora/interaction-evidence
    # ------------------------------------------------------------------

    @router.post("/bff/agora/interaction-evidence", status_code=201)
    def submit_interaction_evidence(
        body: AgoraInteractionEvidenceRequest,
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ) -> Dict[str, Any]:
        """Submit an Agora interaction evidence record for dataset extraction.

        Routes evidence to the governed learning dataset:
          observe — ask, journal, note, insight (passive observation)
          learn   — feedback, training_example (supervised signal)

        Idempotent by evidence_id: a second call with the same evidence_id
        returns the existing record with idempotent=true and does not modify state.

        Governance proof:
          no_promote_proof         = agora_observe_learn_only
          no_runtime_mutation_proof = agora_evidence_extract_only
        """
        identity = extract_identity(authorization)
        require_read_role(identity)
        _require_idempotency_key(idempotency_key)

        record, is_new = extract_evidence(
            body,
            tenant_id=str(identity.get("tenant_id", "unknown")),
            user_id=str(identity.get("user_id", "unknown")),
            extracted_at=utc_now(),
            store=store,
        )
        return {
            "status": "created" if is_new else "exists",
            "idempotent": record.idempotent,
            "data": record.model_dump(),
            "meta": _meta(
                audience=f"tenant:{record.tenant_id}:user:{record.user_id}",
            ),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/interaction-evidence/{evidence_id}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/interaction-evidence/{evidence_id}")
    def get_interaction_evidence(
        evidence_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """Return a specific interaction evidence record."""
        identity = extract_identity(authorization)
        require_read_role(identity)

        record = store.get(evidence_id)
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
            "meta": _meta(audience=f"tenant:{record.tenant_id}:user:{record.user_id}"),
        }

    # ------------------------------------------------------------------
    # GET /bff/agora/datasets/{dataset_kind}
    # ------------------------------------------------------------------

    @router.get("/bff/agora/datasets/{dataset_kind}")
    def list_dataset_records(
        dataset_kind: str,
        authorization: Optional[str] = Header(default=None),
        page_size: int = Query(default=50, ge=1, le=200),
    ) -> Dict[str, Any]:
        """List records in a governed dataset bucket (observe or learn).

        Scoped to the authenticated operator's tenant and user.
        """
        identity = extract_identity(authorization)
        require_read_role(identity)

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
        tenant_id = str(identity.get("tenant_id", "unknown"))
        user_id = str(identity.get("user_id", "unknown"))

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

    return router


__all__ = ["create_dataset_extraction_router"]
