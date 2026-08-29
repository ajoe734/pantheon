"""Source Management command and query router for Source Ingestion.

Covers connector definitions, command execution and receipts, source instances,
observations, and canaries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException

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

if TYPE_CHECKING:
    from ..runtime import SourceIngestionRuntime


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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

    return router
