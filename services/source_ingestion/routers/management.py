"""Source Management command and query router for Source Ingestion.

Covers connector definitions, command execution and receipts, source instances,
observations, and canaries.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Header, HTTPException, Request

from services.runtime_auth_inbound import AuthContext, validate_request_auth

from ..api_models import SourceCommandRequestBody
from ..discovery_summary import read_discovery_summary
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


SOURCE_COMMANDS_FLAG = "SOURCE_MANAGEMENT_COMMANDS_ENABLED"


def source_commands_enabled() -> bool:
    """Fail-closed switch, read per request: only the exact value ``1`` enables commands."""
    return os.environ.get(SOURCE_COMMANDS_FLAG) == "1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_seed_caller(request: Request, *, required_roles: tuple[str, ...] = (*_READ_ROLES, "reviewer", "view_only", "approver")) -> tuple[AuthContext, str]:
    raw_auth, requested = request.headers.get("Authorization"), (request.headers.get("X-Tenant-Id") or "").strip() or None
    tenant = _source_read_tenant(raw_auth, requested, required_roles=required_roles)
    auth = validate_request_auth(authorization=raw_auth, required_roles=required_roles, env={**os.environ, "PANTHEON_RUNTIME_AUTH_MODE": "strict"})
    return auth, tenant


def _seed_meta_tenant(seed: StrategySpecSeed) -> str:
    return str(
        (seed.metadata.get("tenant_id") or seed.metadata.get("tenant") if isinstance(seed.metadata, Mapping) else "")
        or (seed.lineage.get("tenant_id") or seed.lineage.get("tenant") if isinstance(seed.lineage, Mapping) else "") or ""
    ).strip()


def _seed_belongs_to_tenant(runtime: SourceIngestionRuntime, seed: StrategySpecSeed, tenant_id: str) -> bool:
    meta_tenant = _seed_meta_tenant(seed)
    if meta_tenant:
        return meta_tenant == tenant_id
    repo = runtime.evidence_repository
    return (
        any(sid and repo.get_source_record(sid, tenant_id=tenant_id) is not None for sid in dict.fromkeys([seed.source_id, *seed.source_ids]))
        or bool(seed.evidence_bundle_id and repo.get_bundle(seed.evidence_bundle_id, tenant_id=tenant_id) is not None)
        or any(eid and repo.get_evidence_item(eid, tenant_id=tenant_id) is not None for eid in seed.evidence_item_ids)
    )


SEED_PAGE_SIZE_MAX = 200
SEED_PAGE_SCAN_MAX = 1000
"""Records examined per request, however sparse the caller's tenant is in the store."""

_MATCH_METADATA_KEYS = (
    "strategy_family", "strategy_families", "family", "markets", "market_scope", "asset_classes", "asset_class",
    "holding_period", "allowed_use", "access_scope", "backend_hint", "backend", "risk_profile", "risk_level",
    "entitlement_tags", "source_license_scope", "license_scope", "source_status", "source_lifecycle_state",
    "execution_route", "execution_mode_hint", "negative_memory_match", "source_disabled",
    "discovery_requires_execution_access", "data_backfill_available", "mechanism", "mechanism_status",
    "mechanism_support", "tenant_id", "tenant",
)
_MATCH_FIELDS = (
    "seed_id", "source_id", "source_ids", "evidence_bundle_id", "evidence_item_ids", "hypothesis", "asset_class",
    "market_scope", "holding_period", "required_data", "backend_hint", "feature_hints", "risk_notes", "confidence",
    "status", "negative_memory_match", "citation_refs", "trace_refs", "license_scope", "allowed_use",
    "entitlement_tags",
)


def _raw_seed_belongs_to_tenant(repo: Any, raw: Mapping[str, Any], tenant_id: str) -> bool:
    """Tenant rule of ``_seed_belongs_to_tenant`` on a stored record, with per-seed point reads only."""
    meta, lineage = raw.get("metadata"), raw.get("lineage")
    meta_tenant = str(
        (meta.get("tenant_id") or meta.get("tenant") if isinstance(meta, Mapping) else "")
        or (lineage.get("tenant_id") or lineage.get("tenant") if isinstance(lineage, Mapping) else "") or ""
    ).strip()
    if meta_tenant:
        return meta_tenant == tenant_id
    source_ids = dict.fromkeys([str(raw.get("source_id") or ""), *(raw.get("source_ids") or ())])
    bundle_id = str(raw.get("evidence_bundle_id") or "")
    return (
        any(sid and repo.get_source_record(sid, tenant_id=tenant_id) is not None for sid in source_ids)
        or bool(bundle_id and repo.get_bundle(bundle_id, tenant_id=tenant_id) is not None)
        or any(eid and repo.get_evidence_item(eid, tenant_id=tenant_id) is not None for eid in (raw.get("evidence_item_ids") or ()))
    )


def _match_projection(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Only what the discovery scorer reads; nested metadata is cut to the keys it consults."""
    out = {k: raw[k] for k in _MATCH_FIELDS if k in raw}
    meta, lineage = raw.get("metadata"), raw.get("lineage")
    if isinstance(meta, Mapping):
        out["metadata"] = {k: meta[k] for k in _MATCH_METADATA_KEYS if k in meta}
    if isinstance(lineage, Mapping):
        out["lineage"] = {k: lineage[k] for k in ("execution_route", "tenant_id", "tenant") if k in lineage}
    return out


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

        if not source_commands_enabled():
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "source_commands_disabled",
                    "message": f"Source management commands are disabled: set {SOURCE_COMMANDS_FLAG}=1 to enable.",
                    "switch": SOURCE_COMMANDS_FLAG,
                },
            )

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

    def _list_all_tenant_seeds(tenant_id: str) -> dict[str, Any]:
        store = runtime.strategy_seed_store
        all_seeds = store.list_all()
        tenant_source_ids: set[str] | None = None
        tenant_bundle_ids: set[str] | None = None
        tenant_item_ids: set[str] | None = None
        repo = runtime.evidence_repository
        items: list[dict[str, Any]] = []
        for s in all_seeds:
            meta_tenant = _seed_meta_tenant(s)
            if meta_tenant:
                if meta_tenant == tenant_id:
                    items.append(s.to_dict())
                continue
            if tenant_source_ids is None:
                tenant_source_ids = {r.source_id for r in repo.list_source_records(tenant_id=tenant_id) if r.source_id}
            if any(sid and sid in tenant_source_ids for sid in (s.source_id, *s.source_ids)):
                items.append(s.to_dict())
                continue
            if s.evidence_bundle_id:
                if tenant_bundle_ids is None:
                    tenant_bundle_ids = {b.evidence_bundle_id for b in repo.list_bundles(tenant_id=tenant_id) if b.evidence_bundle_id}
                if s.evidence_bundle_id in tenant_bundle_ids:
                    items.append(s.to_dict())
                    continue
            if s.evidence_item_ids:
                if tenant_item_ids is None:
                    tenant_item_ids = {it.evidence_item_id for it in repo.list_evidence_items(tenant_id=tenant_id) if it.evidence_item_id}
                if any(eid and eid in tenant_item_ids for eid in s.evidence_item_ids):
                    items.append(s.to_dict())
        return {"items": items, "count": len(items), "store_path": str(store.path)}

    @router.get("/api/source-ingest/management/discovery-summary")
    def get_management_discovery_summary(request: Request) -> dict[str, Any]:
        """Bounded tenant discovery summary; never reads seeds, evidence or source records."""
        _, tenant_id = _require_seed_caller(request)
        queue_path = getattr(runtime, "DISTILLATION_JOB_QUEUE_PATH", None)
        if queue_path is None:
            queue = getattr(runtime, "distillation_job_queue", None)
            queue_path = getattr(queue, "path", None)
        return read_discovery_summary(queue_path=Path(str(queue_path or "")), tenant_id=tenant_id)

    @router.get("/api/source-ingest/management/strategy-seeds")
    def list_management_strategy_seeds(
        request: Request,
        page_size: int | None = None,
        page_token: str | None = None,
        fields: str | None = None,
    ) -> dict[str, Any]:
        _, tenant_id = _require_seed_caller(request)
        store = runtime.strategy_seed_store
        if fields not in (None, "", "full", "match"):
            raise HTTPException(status_code=422, detail="fields must be full or match")
        if page_size is None and not page_token and fields in (None, "", "full"):
            return _list_all_tenant_seeds(tenant_id)
        size = min(max(1, page_size or SEED_PAGE_SIZE_MAX), SEED_PAGE_SIZE_MAX)
        repo = runtime.evidence_repository
        items: list[dict[str, Any]] = []
        scanned = 0
        token = page_token or None
        # A page ends when full, when the store ends, or when the scan budget is spent; a
        # budget-limited page may be short or empty and still carries next_page_token.
        try:
            while True:
                window, token = store.read_window(page_token=token, max_scan=min(max(size - len(items), 25), SEED_PAGE_SCAN_MAX - scanned))
                scanned += len(window)
                for raw in window:
                    if _raw_seed_belongs_to_tenant(repo, raw, tenant_id):
                        items.append(raw)
                        if len(items) >= size:
                            token = store.cursor_after(str(raw.get("seed_id") or ""), fallback=token)
                            break
                if token is None or len(items) >= size or scanned >= SEED_PAGE_SCAN_MAX:
                    break
        except StrategySpecSeedStoreError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if fields == "match":
            payload_items = [_match_projection(raw) for raw in items]
        else:
            payload_items = [StrategySpecSeed.from_dict(raw).to_dict() for raw in items]
        return {"items": payload_items, "count": len(payload_items), "store_path": str(store.path),
                "page_size": size, "next_page_token": token, "scanned": scanned, "fields": fields or "full"}

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
