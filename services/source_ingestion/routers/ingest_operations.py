"""Ingest operations router for Source Ingestion.

Covers connectors, jobs, source records, evidence, crawl frontier, DLQ, schedules,
market snapshots, and audit logging.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Header, HTTPException

from services.runtime_auth_inbound import AuthError, AuthContext, validate_request_auth

from services.foundation import ActorRef, ActorType, DeadLetterStatus
from services.knowledge.evidence.models import EvidenceValidationError

from ..api_models import (
    ConfigureConnectorRequest,
    ReplayDlqRequest,
    ReplayFrontierRequest,
    RunScheduledRequest,
    SetConnectorLifecycleRequest,
    SetScheduleRequest,
    SourceRecordIngestRequest,
    TriggerIngestJobRequest,
)
from ..connectors import SourceEvidenceError
from ..requirement_state import MarketSnapshotStateError, RequirementStateError

if TYPE_CHECKING:
    from ..runtime import SourceIngestionRuntime


def _source_read_tenant(authorization: str | None, requested: str | None) -> str:
    try:
        context: AuthContext = validate_request_auth(
            authorization=authorization,
            required_roles=("source_ingest_reader", "operator", "admin", "persona.admin"),
            env={**os.environ, "PANTHEON_RUNTIME_AUTH_MODE": "strict"},
        )
    except AuthError as exc:
        raise HTTPException(status_code=503 if exc.status_code >= 500 else exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc
    claims = context.claims
    primary = ("tenant_id", "tenantId", "tenant.id", "tid", "org_id", "organization.id", "organization_id")

    def collect(paths: tuple[str, ...]) -> list[str]:
        found: list[str] = []
        for path in paths:
            value: Any = claims
            for part in path.split("."):
                value = value.get(part) if isinstance(value, dict) else None
            for item in value if isinstance(value, (list, tuple, set)) else (value,):
                text = str(item or "").strip()
                if text and text not in found: found.append(text)
        return found

    scoped = collect(primary)
    allowed = collect(("allowed_tenants", "allowedTenants", "tenant_ids", "tenantIds", "tenants", *primary))
    default = str(os.getenv("SOURCE_INGEST_DEFAULT_TENANT_ID") or os.getenv("PANTHEON_TENANT_ID") or "").strip()
    tenant = str(requested or "").strip() or (default if default in allowed else "")
    if not tenant and len(scoped or allowed) == 1: tenant = (scoped or allowed)[0]
    if not tenant or tenant == "*" or ("*" not in allowed and tenant not in allowed):
        raise HTTPException(status_code=403, detail={"code": "TENANT_SCOPE_DENIED", "message": "An explicitly admitted tenant is required"})
    return tenant


def _owned_evidence_read(repository: Any, method: str, tenant: str, record_id: str | None = None, not_found_msg: str | None = None):
    try:
        reader = getattr(repository, method)
        result = reader(tenant_id=tenant) if record_id is None else reader(record_id, tenant_id=tenant)
        if record_id is not None and result is None and not_found_msg:
            raise HTTPException(status_code=404, detail=not_found_msg)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Source evidence owner unavailable") from exc


def create_ingest_operations_router(runtime: SourceIngestionRuntime) -> APIRouter:
    router = APIRouter(tags=["ingest-operations"])

    @router.get("/api/source-ingest/snapshots/latest")
    def get_latest_market_snapshot(symbol: str) -> dict[str, Any]:
        """Return the one Source-owned, already-stored normalized snapshot.

        This is intentionally a read-only projection lookup. It does not invoke
        a connector, call any provider, or schedule ingestion work.
        """
        try:
            snapshot = runtime.latest_market_snapshot_store.get(symbol)
        except (MarketSnapshotStateError, RequirementStateError) as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "market_snapshot_store_unavailable",
                    "symbol": str(symbol or "").strip(),
                    "detail": str(exc),
                },
            ) from exc
        if snapshot is None:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "market_snapshot_not_found",
                    "symbol": str(symbol or "").strip().upper(),
                },
            )
        return snapshot.to_public_dict(requested_symbol=symbol)

    @router.post("/api/source-ingest/connectors", status_code=201)
    def configure_connector(
        request: ConfigureConnectorRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            with runtime.authoritative_reconcile_lock:
                proposed = request.connector.to_domain()
                runtime._fence_managed_connector_mutation(
                    proposed.connector_id,
                    authorization,
                    operation="controller-owned connector configuration",
                    proposed_connector=proposed,
                )
                return runtime._configure_connector(request)
        except SourceEvidenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/source-ingest/connectors")
    def list_connectors() -> dict[str, Any]:
        return {
            "connectors": [
                {
                    "connector": config.connector.to_dict(),
                    "fetch": dict(config.fetch),
                    "state": runtime.connector_store.get_fetch_state(config.connector.connector_id),
                    "updated_at": config.updated_at,
                }
                for config in runtime.connector_store.list_configs()
            ]
        }

    @router.get("/api/source-ingest/connectors/{connector_id}")
    def get_connector(connector_id: str) -> dict[str, Any]:
        config = runtime.connector_store.get_config(connector_id)
        if config is None:
            raise HTTPException(status_code=404, detail="connector config not found")
        return {
            "connector": config.connector.to_dict(),
            "fetch": dict(config.fetch),
            "state": runtime.connector_store.get_fetch_state(connector_id),
            "updated_at": config.updated_at,
        }

    @router.put("/api/source-ingest/connectors/{connector_id}/lifecycle")
    def set_connector_lifecycle(
        connector_id: str,
        request: SetConnectorLifecycleRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            with runtime.authoritative_reconcile_lock:
                runtime._fence_managed_connector_mutation(
                    connector_id,
                    authorization,
                    operation="controller-owned connector lifecycle mutation",
                )
                return runtime._set_connector_lifecycle(connector_id, request)
        except SourceEvidenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/source-ingest/jobs", status_code=201)
    def trigger_job(
        request: TriggerIngestJobRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        with runtime.authoritative_reconcile_lock:
            connector_id = request.connector.connector_id if request.connector is not None else str(request.connector_id or "")
            proposed = request.connector.to_domain() if request.connector is not None else None
            runtime._fence_managed_connector_mutation(
                connector_id,
                authorization,
                operation="controller-owned connector ingest mutation",
                proposed_connector=proposed,
            )
            return runtime.pipeline.run_ingest_request(request)

    @router.post("/api/source-ingest/source-records", status_code=201)
    def ingest_source_records(
        request: SourceRecordIngestRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if not request.records:
            raise HTTPException(status_code=400, detail="records is required for SourceRecord ingest")
        with runtime.authoritative_reconcile_lock:
            connector_id = request.connector.connector_id if request.connector is not None else str(request.connector_id or "")
            proposed = request.connector.to_domain() if request.connector is not None else None
            runtime._fence_managed_connector_mutation(
                connector_id,
                authorization,
                operation="controller-owned connector source-record mutation",
                proposed_connector=proposed,
            )
            return runtime.pipeline.run_ingest_request(
                TriggerIngestJobRequest(
                    connector=request.connector,
                    connector_id=request.connector_id,
                    trace_id=request.trace_id,
                    trigger_type=request.trigger_type,
                    records=request.records,
                    next_watermark=request.next_watermark,
                )
            )

    @router.get("/api/source-ingest/jobs")
    def list_jobs() -> dict[str, Any]:
        return {
            "runs": [run.to_dict() for run in runtime.store.list_runs()],
            "receipts": [receipt.to_dict() for receipt in runtime.store.list_receipts()],
        }

    @router.get("/api/source-ingest/jobs/{ingest_run_id}")
    def get_job(ingest_run_id: str) -> dict[str, Any]:
        run = runtime.store.get_run(ingest_run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="ingest run not found")
        receipt = runtime.store.get_receipt(ingest_run_id)
        return {
            "run": run.to_dict(),
            "receipt": receipt.to_dict() if receipt is not None else None,
        }

    @router.get("/api/source-ingest/receipts")
    def list_ingest_receipts(connector_id: str | None = None) -> dict[str, Any]:
        return {"receipts": [receipt.to_dict() for receipt in runtime.store.list_receipts(connector_id=connector_id)]}

    @router.get("/api/source-ingest/receipts/{ingest_run_id}")
    def get_ingest_receipt(ingest_run_id: str) -> dict[str, Any]:
        receipt = runtime.store.get_receipt(ingest_run_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="ingest receipt not found")
        return {"receipt": receipt.to_dict()}

    @router.get("/api/source-ingest/watermarks/{connector_id}")
    def get_watermark(connector_id: str) -> dict[str, Any]:
        watermark = runtime.store.get_watermark(connector_id)
        if watermark is None:
            raise HTTPException(status_code=404, detail="source watermark not found")
        return {"watermark": watermark.to_dict()}

    @router.get("/api/source-ingest/frontier")
    def list_crawl_frontier(
        status: Literal["queued", "running", "done", "failed", "retry"] | None = None,
    ) -> dict[str, Any]:
        return {"frontier": [item.to_dict() for item in runtime.store.list_frontier(status=status)]}

    @router.post("/api/source-ingest/frontier/{frontier_id}/replay")
    def replay_frontier(frontier_id: str, request: ReplayFrontierRequest | None = None) -> dict[str, Any]:
        with runtime.source_execution_lock:
            try:
                trace_id = request.trace_id if request and request.trace_id else f"frontier-replay-{frontier_id}"
                runtime.store.replay_frontier(frontier_id, trace_id=trace_id)
                item = runtime.store.claim_frontier(frontier_id)
                result, evidence_refs, frontier, source_search_refresh = runtime.pipeline.run_frontier_item(item)
                return {**runtime.pipeline.result_payload(result, evidence_refs, source_search_refresh), "frontier": frontier.to_dict()}
            except (EvidenceValidationError, SourceEvidenceError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/source-ingest/source-records")
    def list_source_records(authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
        return {"source_records": [r.to_dict() for r in _owned_evidence_read(runtime.evidence_repository, "list_source_records", _source_read_tenant(authorization, tenant_id))]}

    @router.get("/api/source-ingest/source-records/{source_id}")
    def get_source_record(source_id: str, authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
        source = _owned_evidence_read(runtime.evidence_repository, "get_source_record", _source_read_tenant(authorization, tenant_id), source_id, "source record not found")
        return {"source_record": source.to_dict()}

    @router.get("/api/source-ingest/evidence/items")
    def list_evidence_items(authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
        return {"items": [r.to_dict() for r in _owned_evidence_read(runtime.evidence_repository, "list_evidence_items", _source_read_tenant(authorization, tenant_id))]}

    @router.get("/api/source-ingest/evidence/items/{evidence_item_id}")
    def get_evidence_item(evidence_item_id: str, authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
        item = _owned_evidence_read(runtime.evidence_repository, "get_evidence_item", _source_read_tenant(authorization, tenant_id), evidence_item_id, "evidence item not found")
        return {"item": item.to_dict()}

    @router.get("/api/source-ingest/evidence/bundles")
    def list_evidence_bundles(authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
        return {"bundles": [r.to_dict() for r in _owned_evidence_read(runtime.evidence_repository, "list_bundles", _source_read_tenant(authorization, tenant_id))]}

    @router.get("/api/source-ingest/evidence/bundles/{evidence_bundle_id}")
    def get_evidence_bundle(evidence_bundle_id: str, authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
        bundle = _owned_evidence_read(runtime.evidence_repository, "get_bundle", _source_read_tenant(authorization, tenant_id), evidence_bundle_id, "evidence bundle not found")
        return {"bundle": bundle.to_dict()}

    @router.get("/api/source-ingest/evidence/knowledge-objects")
    def list_knowledge_objects(authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
        return {"knowledge_objects": [r.to_dict() for r in _owned_evidence_read(runtime.evidence_repository, "list_knowledge_objects", _source_read_tenant(authorization, tenant_id))]}

    @router.get("/api/source-ingest/evidence/knowledge-objects/{knowledge_object_id}")
    def get_knowledge_object(knowledge_object_id: str, authorization: str | None = Header(default=None), tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
        knowledge_object = _owned_evidence_read(runtime.evidence_repository, "get_knowledge_object", _source_read_tenant(authorization, tenant_id), knowledge_object_id, "knowledge object not found")
        return {"knowledge_object": knowledge_object.to_dict()}

    @router.get("/api/source-ingest/dlq")
    def list_dlq(
        status: Literal["pending", "replayed", "duplicate_skipped", "replay_failed", "schema_rejected"] | None = None,
    ) -> dict[str, Any]:
        entries = runtime.dead_letter_queue.entries(status=status)
        all_entries = runtime.dead_letter_queue.entries()
        status_counts = {
            item.value: sum(1 for entry in all_entries if entry.status == item)
            for item in DeadLetterStatus
        }
        unresolved_count = sum(
            status_counts[item.value]
            for item in (
                DeadLetterStatus.PENDING,
                DeadLetterStatus.REPLAY_FAILED,
                DeadLetterStatus.SCHEMA_REJECTED,
            )
        )
        return {
            "entries": [entry.to_dict() for entry in entries],
            "entry_count": len(entries),
            "pending_count": status_counts[DeadLetterStatus.PENDING.value],
            "unresolved_count": unresolved_count,
            "status_counts": status_counts,
        }

    @router.post("/api/source-ingest/dlq/replay")
    def replay_dlq(request: ReplayDlqRequest) -> dict[str, Any]:
        with runtime.source_execution_lock:
            before_entries = {
                entry.entry_id: entry
                for entry in runtime.pipeline.unresolved_dead_letter_entries(tag_filter=request.tag or None)
            }
            entries = list(before_entries.values())
            requested: set[str] = set()
            if request.entry_ids:
                requested = set(request.entry_ids)
                entries = [entry for entry in entries if entry.entry_id in requested]
            unique_entries: list[Any] = []
            seen_frontiers: set[tuple[str, str]] = set()
            for entry in entries:
                frontier_id = str(entry.event.payload.get("frontier_id") or "").strip()
                connector_id = str(entry.event.payload.get("connector_id") or "").strip()
                if frontier_id:
                    correlation = (frontier_id, connector_id)
                    if correlation in seen_frontiers:
                        continue
                    seen_frontiers.add(correlation)
                unique_entries.append(entry)
            actor_ref = ActorRef(ActorType.SERVICE, request.actor_id, roles=("source_ingest_replay",))
            replay_result = runtime.replay_processor.replay(
                unique_entries,
                actor_ref=actor_ref,
                environment=runtime.scheduler.environment,
                reason=request.reason,
                queue=runtime.dead_letter_queue,
                apply_fn=runtime.pipeline.replay_source_event,
                before_replace_fn=lambda result: runtime._append_audit_actions((result.audit_action,)),
            )
            selected_entry_ids = {entry.entry_id for entry in unique_entries}
            after_entries = {entry.entry_id: entry for entry in runtime.dead_letter_queue.entries()}
            correlated_resolutions = []
            for entry_id, before in sorted(before_entries.items()):
                after = after_entries.get(entry_id)
                if after is None or (
                    after.status == before.status
                    and after.replay_attempts == before.replay_attempts
                ):
                    continue
                correlated_resolutions.append(
                    {
                        "entry_id": entry_id,
                        "previous_status": before.status.value,
                        "status": after.status.value,
                        "previous_replay_attempts": before.replay_attempts,
                        "replay_attempts": after.replay_attempts,
                        "explicitly_requested": entry_id in requested if request.entry_ids else False,
                        "selected_for_execution": entry_id in selected_entry_ids,
                    }
                )
            payload = replay_result.to_dict()
            payload["selected_entry_ids"] = sorted(selected_entry_ids)
            payload["correlated_resolutions"] = correlated_resolutions
            payload["summary"]["correlated_resolution_count"] = len(correlated_resolutions)
            return payload

    @router.put("/api/source-ingest/connectors/{connector_id}/schedule")
    def set_connector_schedule(
        connector_id: str,
        request: SetScheduleRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        config = runtime.connector_store.get_config(connector_id)
        if config is None:
            raise HTTPException(status_code=404, detail="connector config not found")
        try:
            with runtime.authoritative_reconcile_lock:
                runtime._fence_managed_connector_mutation(
                    connector_id,
                    authorization,
                    operation="controller-owned connector schedule mutation",
                )
                schedule = runtime.schedule_config_store.upsert_schedule(
                    connector_id,
                    interval_seconds=request.interval_seconds,
                    enabled=request.enabled,
                )
                return {"schedule": schedule.to_dict()}
        except SourceEvidenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/source-ingest/connectors/{connector_id}/schedule")
    def get_connector_schedule(connector_id: str) -> dict[str, Any]:
        schedule = runtime.schedule_config_store.get_schedule(connector_id)
        if schedule is None:
            raise HTTPException(status_code=404, detail="connector schedule not configured")
        return {"schedule": schedule.to_dict()}

    @router.post("/api/source-ingest/run-scheduled")
    def run_scheduled_connectors(
        request: RunScheduledRequest | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        return runtime.run_scheduled_connectors(request, authorization=authorization)

    @router.get("/api/source-ingest/audit")
    def list_audit() -> dict[str, Any]:
        return {"actions": runtime._load_audit_actions()}

    return router
