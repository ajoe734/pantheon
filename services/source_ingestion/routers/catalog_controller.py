"""Catalog and controller router for Source Ingestion.

Covers connector registry, policy registry, financial data source catalog,
active universe planning and scheduling, persona source provisioning reconciliation,
controller readback, and schemas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Header, HTTPException

from ..active_universe import (
    DEFAULT_SOURCE_UPDATE_RULES,
    build_active_universe_job_fanout,
    build_active_universe_update_plan,
)
from ..api_models import (
    ActiveUniversePlanRequest,
    ActiveUniverseScheduleRequest,
    PersonaSourceProvisioningRequest,
)
from ..connectors import ConnectorStatus, SourceEvidenceError
from ..controller_state import ControllerStateError
from ..financial_source_catalog import financial_data_source_catalog_payload
from ..process_lock import exclusive_file_lock
from ..requirement_state import RequirementStateError

if TYPE_CHECKING:
    from ..runtime import SourceIngestionRuntime


def create_catalog_controller_router(runtime: SourceIngestionRuntime) -> APIRouter:
    router = APIRouter(tags=["catalog-controller"])

    @router.get("/api/source-ingest/registry")
    def source_connector_registry() -> dict[str, Any]:
        entries = runtime._source_connector_entries()
        financial_catalog = financial_data_source_catalog_payload()
        return {
            "schema_version": "source_connector_registry.v1",
            "connectors": entries,
            "provider_examples": runtime._provider_example_payloads(),
            "policy_registry": runtime._source_policy_registry_payload(entries),
            "financial_data_source_catalog": financial_catalog,
            "active_universe_policy": financial_catalog["active_universe_policy"],
        }

    @router.post("/api/source-ingest/persona-source-provisioning/reconcile")
    def reconcile_persona_source_provisioning(
        request: PersonaSourceProvisioningRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            if not request.dry_run:
                runtime._require_controller_authorization(
                    authorization,
                    operation="source reconciliation mutation",
                )
                with exclusive_file_lock(
                    runtime.RECONCILE_TRANSACTION_LOCK_PATH,
                    runtime.authoritative_reconcile_lock,
                ):
                    runtime.requirement_snapshot_store.reload()
                    runtime.connector_store.reload()
                    runtime.schedule_config_store.reload()
                    return runtime._persona_source_provisioning_payload(request)
            return runtime._persona_source_provisioning_payload(request)
        except (SourceEvidenceError, RequirementStateError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/source-ingest/controller/readback")
    def source_ingest_controller_readback() -> dict[str, Any]:
        """Return authoritative connector/schedule/record/health actual state."""
        try:
            with runtime.authoritative_reconcile_lock:
                return runtime._controller_readback_payload()
        except ControllerStateError as exc:
            raise HTTPException(status_code=503, detail=f"controller state is invalid: {exc}") from exc

    @router.get("/api/source-ingest/policy-registry")
    def source_policy_registry() -> dict[str, Any]:
        return runtime._source_policy_registry_payload()

    @router.get("/api/source-ingest/data-sources/financial-catalog")
    def financial_data_source_catalog() -> dict[str, Any]:
        return financial_data_source_catalog_payload()

    @router.get("/api/source-ingest/active-universe/policy")
    def active_universe_policy() -> dict[str, Any]:
        return financial_data_source_catalog_payload()["active_universe_policy"]

    @router.post("/api/source-ingest/active-universe/plan")
    def active_universe_plan(request: ActiveUniversePlanRequest) -> dict[str, Any]:
        try:
            rules = [rule.to_domain() for rule in request.rules] if request.rules else DEFAULT_SOURCE_UPDATE_RULES
            return build_active_universe_update_plan(
                [member.to_domain() for member in request.members],
                rules=rules,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post("/api/source-ingest/active-universe/schedule")
    def active_universe_schedule(
        request: ActiveUniverseScheduleRequest,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        try:
            rules = [rule.to_domain() for rule in request.rules] if request.rules else DEFAULT_SOURCE_UPDATE_RULES
            fanout = build_active_universe_job_fanout(
                [member.to_domain() for member in request.members],
                rules=rules,
                run_date=request.run_date,
                default_max_symbols_per_job=request.default_max_symbols_per_job,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        enqueued: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = list(fanout["skipped"])
        if request.enqueue:
            with runtime.authoritative_reconcile_lock, runtime.source_execution_lock:
                managed_job_ids = {
                    str(job["connector_id"])
                    for job in fanout["jobs"]
                    if (
                        (config := runtime.connector_store.get_config(str(job["connector_id"]))) is not None
                        and runtime._is_controller_owned(config.connector)
                    )
                }
                if managed_job_ids:
                    runtime._require_controller_authorization(
                        authorization,
                        operation="controller-owned active-universe scheduling",
                    )
                now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                for job in fanout["jobs"]:
                    connector_id = str(job["connector_id"])
                    config = runtime.connector_store.get_config(connector_id)
                    if config is None:
                        skipped.append({**job, "reason": "connector-config-missing"})
                        continue
                    if config.connector.status == ConnectorStatus.DISABLED:
                        skipped.append({**job, "reason": "connector-disabled"})
                        continue
                    frontier = runtime.store.enqueue_frontier(
                        connector_id=connector_id,
                        trace_id=request.trace_id or f"active-universe-{connector_id}-{request.run_date}",
                        trigger_type="active_universe_scheduled",
                        max_attempts=runtime.FRONTIER_MAX_ATTEMPTS,
                        available_at=now_iso,
                        job_parameters=job,
                    )
                    enqueued.append(frontier.to_dict())

        return {
            **fanout,
            "enqueued": enqueued,
            "skipped": skipped,
            "summary": {
                **fanout["summary"],
                "enqueued_count": len(enqueued),
                "skipped_count": len(skipped),
            },
        }

    @router.get("/api/source-ingest/schemas/source-record")
    def source_record_schema() -> dict[str, Any]:
        return runtime._source_record_schema()

    return router
