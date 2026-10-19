"""Source Management command and query router for Source Ingestion.

Covers connector definitions, command execution and receipts, source instances,
observations, and canaries.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Header, HTTPException, Request

from services.runtime_auth_inbound import AuthContext, validate_request_auth

from ..api_models import SourceCommandRequestBody
from ..connector_definitions import get_connector_definition, list_connector_definitions
from ..source_management_commands import AdapterNotSupportedError, CommandPreconditionError
from ..source_management_models import SourceManagementCommand
from ..source_management_store import (
    DuplicateInstanceError,
    IdempotencyConflictError,
    SourceInstanceNotFoundError,
    SourceManagementContractError,
    StaleRevisionError,
)
from ..strategy_seed_builder import StrategySpecSeed
from ..strategy_seed_store import StrategySpecSeedReviewError, StrategySpecSeedStoreError
from .ingest_operations import _SOURCE_INGEST_READ_ROLES as _READ_ROLES, _SOURCE_INGEST_WRITE_ROLES as _WRITE_ROLES, _source_read_tenant

if TYPE_CHECKING:
    from ..runtime import SourceIngestionRuntime


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_seed_caller(request: Request, *, required_roles: tuple[str, ...] = (*_READ_ROLES, "reviewer", "view_only", "approver")) -> tuple[AuthContext, str]:
    raw_auth, requested = request.headers.get("Authorization"), (request.headers.get("X-Tenant-Id") or "").strip() or None
    tenant = _source_read_tenant(raw_auth, requested, required_roles=required_roles)
    auth = validate_request_auth(authorization=raw_auth, required_roles=required_roles, env={**os.environ, "PANTHEON_RUNTIME_AUTH_MODE": "strict"})
    return auth, tenant


def _seed_belongs_to_tenant(runtime: SourceIngestionRuntime, seed: StrategySpecSeed, tenant_id: str) -> bool:
    meta_tenant = str(
        (seed.metadata.get("tenant_id") or seed.metadata.get("tenant") if isinstance(seed.metadata, Mapping) else "")
        or (seed.lineage.get("tenant_id") or seed.lineage.get("tenant") if isinstance(seed.lineage, Mapping) else "") or ""
    ).strip()
    if meta_tenant:
        return meta_tenant == tenant_id
    repo = runtime.evidence_repository
    return (
        any(sid and repo.get_source_record(sid, tenant_id=tenant_id) is not None for sid in dict.fromkeys([seed.source_id, *seed.source_ids]))
        or bool(seed.evidence_bundle_id and repo.get_bundle(seed.evidence_bundle_id, tenant_id=tenant_id) is not None)
        or any(eid and repo.get_evidence_item(eid, tenant_id=tenant_id) is not None for eid in seed.evidence_item_ids)
    )


def _require_tenant_seed(runtime: SourceIngestionRuntime, seed_id: str, tenant_id: str, *, error_code: str = "seed_not_found") -> StrategySpecSeed:
    seed = runtime.strategy_seed_store.get(seed_id)
    if seed is None or not _seed_belongs_to_tenant(runtime, seed, tenant_id):
        raise StrategySpecSeedReviewError(error_code, f"StrategySpecSeed not found: {seed_id}")
    return seed


def _raise_seed_store_http_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", "") or "seed_store_error"
    status = 404 if code in {"seed_not_found", "merge_target_not_found"} else (409 if code in {"idempotency_conflict", "terminal_seed_status", "invalid_status_transition", "invalid_merge_target"} else 422)
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": str(exc)}})


def create_management_router(runtime: SourceIngestionRuntime) -> APIRouter:
    router = APIRouter(tags=["source-management"])

    @router.get("/api/source-ingest/management/connector-definitions")
    def list_management_connector_definitions() -> dict[str, Any]:
        definitions = list_connector_definitions()
        return {
            "definitions": [d.to_dict() for d in definitions],
            "count": len(definitions),
        }

    @router.get("/api/source-ingest/management/connector-definitions/{definition_id}")
    def get_management_connector_definition(definition_id: str) -> dict[str, Any]:
        definition = get_connector_definition(definition_id)
        if definition is None:
            raise HTTPException(status_code=404, detail=f"connector definition not found: {definition_id}")
        return {"definition": definition.to_dict()}

    @router.post("/api/source-ingest/management/commands", status_code=202)
    def execute_source_management_command(
        request: SourceCommandRequestBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        runtime._require_service_authorization(authorization, operation="source management command")

        cmd = SourceManagementCommand(
            command_id=request.command_id or f"srcmd-{uuid.uuid4().hex[:12]}",
            idempotency_key=request.idempotency_key,
            command_type=request.command_type,
            source_instance_id=request.source_instance_id,
            expected_revision=request.expected_revision,
            actor=request.actor.model_dump(),
            reason=request.reason,
            parameters=request.parameters,
            trace_id=request.trace_id,
            requested_at=request.requested_at or _utc_now(),
        )

        try:
            receipt = runtime.source_command_engine.execute_command(cmd)
            return {"receipt": receipt.to_dict()}
        except AdapterNotSupportedError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "adapter_not_supported",
                    "message": str(exc),
                    "development_need": exc.development_need,
                },
            ) from exc
        except StaleRevisionError as exc:
            raise HTTPException(status_code=409, detail=f"STALE_REVISION: {exc}") from exc
        except IdempotencyConflictError as exc:
            raise HTTPException(status_code=409, detail=f"RESOURCE_CONFLICT: {exc}") from exc
        except DuplicateInstanceError as exc:
            raise HTTPException(status_code=409, detail=f"RESOURCE_CONFLICT: {exc}") from exc
        except SourceInstanceNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"RESOURCE_NOT_FOUND: {exc}") from exc
        except CommandPreconditionError as exc:
            raise HTTPException(status_code=412, detail=f"PRECONDITION_FAILED: {exc}") from exc
        except SourceManagementContractError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/source-ingest/management/commands/{receipt_id}")
    def get_source_management_command_receipt(receipt_id: str) -> dict[str, Any]:
        receipt = runtime.source_management_store.get_receipt(receipt_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail=f"command receipt not found: {receipt_id}")
        return {"receipt": receipt.to_dict()}

    @router.get("/api/source-ingest/management/sources")
    def list_management_sources(
        source_kind: str | None = None,
        lifecycle_state: str | None = None,
    ) -> dict[str, Any]:
        instances = runtime.source_management_store.list_instances(
            source_kind=source_kind,
            lifecycle_state=lifecycle_state,
        )
        return {
            "sources": [inst.to_dict() for inst in instances],
            "count": len(instances),
        }

    @router.get("/api/source-ingest/management/sources/{source_instance_id}")
    def get_management_source(source_instance_id: str) -> dict[str, Any]:
        instance = runtime.source_management_store.get_instance(source_instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"source instance not found: {source_instance_id}")
        desired = runtime.source_management_store.get_desired_state(source_instance_id)
        observed = runtime.source_management_store.get_latest_observed_snapshot(source_instance_id)
        return {
            "source": instance.to_dict(),
            "desired": desired.to_dict() if desired else None,
            "observed": observed.to_dict() if observed else None,
        }

    @router.get("/api/source-ingest/management/sources/{source_instance_id}/observations")
    def list_management_source_observations(
        source_instance_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        instance = runtime.source_management_store.get_instance(source_instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"source instance not found: {source_instance_id}")
        observations = runtime.source_management_store.list_observed_snapshots(source_instance_id, limit=limit)
        return {
            "observations": [obs.to_dict() for obs in observations],
            "count": len(observations),
        }

    @router.get("/api/source-ingest/management/sources/{source_instance_id}/canaries")
    def list_management_source_canaries(
        source_instance_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        instance = runtime.source_management_store.get_instance(source_instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"source instance not found: {source_instance_id}")
        canaries = runtime.source_management_store.list_canary_results(source_instance_id, limit=limit)
        return {
            "canaries": [can.to_dict() for can in canaries],
            "count": len(canaries),
        }

    @router.get("/api/source-ingest/management/sources/{source_instance_id}/canaries/{canary_id}")
    def get_management_source_canary(
        source_instance_id: str,
        canary_id: str,
    ) -> dict[str, Any]:
        canary = runtime.source_management_store.get_canary_result(canary_id)
        if canary is None or canary.source_instance_id != source_instance_id:
            raise HTTPException(status_code=404, detail=f"canary result not found: {canary_id}")
        return {"canary": canary.to_dict()}

    @router.get("/api/source-ingest/management/sources/{source_instance_id}/receipts")
    def list_management_source_receipts(
        source_instance_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        instance = runtime.source_management_store.get_instance(source_instance_id)
        if instance is None:
            raise HTTPException(status_code=404, detail=f"source instance not found: {source_instance_id}")
        receipts = runtime.source_management_store.list_receipts(source_instance_id, limit=limit)
        return {
            "receipts": [rcp.to_dict() for rcp in receipts],
            "count": len(receipts),
        }

    @router.get("/api/source-ingest/management/strategy-seeds")
    def list_management_strategy_seeds(request: Request) -> dict[str, Any]:
        _, tenant_id = _require_seed_caller(request)
        store = runtime.strategy_seed_store
        items = [s.to_dict() for s in store.list_all() if _seed_belongs_to_tenant(runtime, s, tenant_id)]
        return {"items": items, "count": len(items), "store_path": str(store.path)}

    @router.get("/api/source-ingest/management/strategy-seeds/{seed_id}")
    def get_management_strategy_seed(seed_id: str, request: Request) -> dict[str, Any]:
        _, tenant_id = _require_seed_caller(request)
        try:
            seed = _require_tenant_seed(runtime, seed_id, tenant_id)
        except (StrategySpecSeedReviewError, StrategySpecSeedStoreError) as exc:
            raise _raise_seed_store_http_error(exc) from exc
        return {"seed": seed.to_dict(), "store_path": str(runtime.strategy_seed_store.path)}

    @router.post("/api/source-ingest/management/strategy-seeds/{seed_id}/review")
    def review_management_strategy_seed(seed_id: str, request: Request, payload: dict[str, Any] = Body(default_factory=dict), x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key")) -> dict[str, Any]:
        auth, tenant_id = _require_seed_caller(request, required_roles=_WRITE_ROLES)
        try:
            _require_tenant_seed(runtime, seed_id, tenant_id)
            updated, decision = runtime.strategy_seed_store.record_review_decision(
                seed_id, decision=str(payload.get("decision") or payload.get("action") or ""), reviewer_id=auth.actor_id, reason=str(payload.get("reason") or ""),
                target_refs=list(payload.get("target_refs") or ()), created_at=payload.get("created_at"),
                idempotency_key=str(x_idempotency_key or payload.get("idempotency_key") or "").strip() or None, request_hash=str(payload.get("request_hash") or "").strip() or None,
            )
        except (StrategySpecSeedReviewError, StrategySpecSeedStoreError) as exc:
            raise _raise_seed_store_http_error(exc) from exc
        return {"seed": updated.to_dict(), "decision": {**decision.to_dict(), "idempotent_replay": decision.idempotent_replay}}

    @router.post("/api/source-ingest/management/strategy-seeds/{seed_id}/merge")
    def merge_management_strategy_seed(seed_id: str, request: Request, payload: dict[str, Any] = Body(default_factory=dict), x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key")) -> dict[str, Any]:
        auth, tenant_id = _require_seed_caller(request, required_roles=_WRITE_ROLES)
        target_seed_id = str(payload.get("target_seed_id") or "").strip()
        try:
            _require_tenant_seed(runtime, seed_id, tenant_id)
            if target_seed_id and target_seed_id != seed_id:
                _require_tenant_seed(runtime, target_seed_id, tenant_id, error_code="merge_target_not_found")
            updated, decision = runtime.strategy_seed_store.merge_seed(
                seed_id, target_seed_id=target_seed_id, reviewer_id=auth.actor_id, reason=str(payload.get("reason") or ""), created_at=payload.get("created_at"),
                idempotency_key=str(x_idempotency_key or payload.get("idempotency_key") or "").strip() or None, request_hash=str(payload.get("request_hash") or "").strip() or None, target_refs=list(payload.get("target_refs") or ()),
            )
        except (StrategySpecSeedReviewError, StrategySpecSeedStoreError) as exc:
            raise _raise_seed_store_http_error(exc) from exc
        return {"seed": updated.to_dict(), "decision": {**decision.to_dict(), "idempotent_replay": decision.idempotent_replay}}

    @router.post("/api/source-ingest/management/strategy-seeds/{seed_id}/replication-submission")
    def record_management_strategy_seed_replication(seed_id: str, request: Request, payload: dict[str, Any] = Body(default_factory=dict), x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key")) -> dict[str, Any]:
        auth, tenant_id = _require_seed_caller(request, required_roles=_WRITE_ROLES)
        try:
            _require_tenant_seed(runtime, seed_id, tenant_id)
            updated = runtime.strategy_seed_store.record_replication_submission(
                seed_id, replication_ref=str(payload.get("replication_ref") or ""), experiment_task_id=str(payload.get("experiment_task_id") or ""),
                strategy_id=str(payload.get("strategy_id") or ""), strategy_spec_version=str(payload.get("strategy_spec_version") or "1.0.0"),
                submitted_by=auth.actor_id, submitted_at=str(payload.get("submitted_at") or _utc_now()),
                idempotency_key=str(x_idempotency_key or payload.get("idempotency_key") or ""), research_task_ref=str(payload.get("research_task_ref") or "").strip() or None,
            )
        except (StrategySpecSeedReviewError, StrategySpecSeedStoreError) as exc:
            raise _raise_seed_store_http_error(exc) from exc
        return {"seed": updated.to_dict()}

    return router
