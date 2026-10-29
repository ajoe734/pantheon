import ast
import json
import os
import sys
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.params import Param as FastAPIParam
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.control_plane.bff.ports import ReadSurfacePorts, create_in_memory_read_surface_ports
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.router import create_personas_router
from services.control_plane.bff.personas.service import (
    PersonaService,
    _pm12_persona_telemetry_metrics,
    create_persona_registry_write_owner,
    utc_now,
)
from services.control_plane.bff.runtime.router import create_runtime_router
from services.control_plane.bff.research.router import create_research_router
from services.control_plane.bff.management_read_models.ranking_router import (
    create_performance_attribution_router,
)
from services.control_plane.bff.shared.cross_domain_utils import (
    _management_as_float,
    _management_first_float,
    _management_nested_value,
    _management_telemetry_rollup,
    _resolve_param,
)


# `_tw_qlib_research_experiment_default` (and its small, fully self-contained
# dependency chain of pure helpers/constants) IS practical to port locally, so it is
# reproduced here verbatim instead of importing it from read_store.
_LOCAL_TW_QLIB_DATASET_MANIFEST_REF = "support/evidence/MGMT-QLIB-001/dataset_manifest.json"
_LOCAL_TW_QLIB_LINKAGE_PACKET_REF = (
    "support/evidence/MGMT-QLIB-006/management_linkage_packet.json"
)
_LOCAL_TW_QLIB_EXPERIMENT_ID = "exp-mgmt-qlib-006"
_LOCAL_TW_QLIB_STRATEGY_ID = "tw-cross-sectional-equity-alpha"
_LOCAL_TW_QLIB_STRATEGY_SPEC_ID = "qlib-tw-cross-sectional-alpha-spec-v1"
_LOCAL_TW_QLIB_ARTIFACT_ID = "qlib-tw-cross-sectional-alpha-model-draft-v1"
_LOCAL_TW_QLIB_DATASET_REF = "dataset:tw-equity-ohlcv-top50-2024-daily"
_LOCAL_TW_QLIB_DATASET_MANIFEST_ID = (
    "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily"
)


def _local_tw_qlib_evidence_refs() -> list[dict[str, Any]]:
    return [
        {
            "ref_type": "management_linkage_packet",
            "ref_id": "mgmt-qlib-006-management-linkage-v1",
            "ref": _LOCAL_TW_QLIB_LINKAGE_PACKET_REF,
        },
        {
            "ref_type": "dataset_manifest",
            "ref_id": _LOCAL_TW_QLIB_DATASET_MANIFEST_ID,
            "ref": _LOCAL_TW_QLIB_DATASET_MANIFEST_REF,
        },
        {
            "ref_type": "research_experiment",
            "ref_id": _LOCAL_TW_QLIB_EXPERIMENT_ID,
            "route": f"/bff/research-experiments/{_LOCAL_TW_QLIB_EXPERIMENT_ID}",
        },
        {
            "ref_type": "strategy_artifacts",
            "ref_id": _LOCAL_TW_QLIB_STRATEGY_ID,
            "route": f"/bff/strategies/{_LOCAL_TW_QLIB_STRATEGY_ID}/artifacts",
        },
    ]


def _local_tw_qlib_safety_assertions() -> dict[str, Any]:
    return {
        "registry_write_performed": False,
        "registry_write_authority": "registry_service_only",
        "broker_session_opened": False,
        "order_route": "none",
        "deployment_stage": "none",
        "live_capital_side_effects": False,
    }


def _local_tw_qlib_research_linkage() -> dict[str, Any]:
    return {
        "kind": "qlib_admission_research_linkage",
        "framework": "qlib",
        "admission_stage": "management_review_linked",
        "strategy_id": _LOCAL_TW_QLIB_STRATEGY_ID,
        "strategy_spec_id": _LOCAL_TW_QLIB_STRATEGY_SPEC_ID,
        "dataset_manifest_id": _LOCAL_TW_QLIB_DATASET_MANIFEST_ID,
        "source_task_ids": [
            "MGMT-QLIB-001",
            "MGMT-QLIB-002",
            "MGMT-QLIB-004",
            "MGMT-QLIB-006",
        ],
        "pending_task_ids": ["MGMT-QLIB-003", "MGMT-QLIB-005"],
        "evidence_refs": [
            {
                "ref_type": "dataset_manifest",
                "task_id": "MGMT-QLIB-001",
                "ref": _LOCAL_TW_QLIB_DATASET_MANIFEST_REF,
            },
            {
                "ref_type": "strategy_spec_packet",
                "task_id": "MGMT-QLIB-002",
                "ref": "support/evidence/MGMT-QLIB-002/strategy_spec_packet.json",
            },
            {
                "ref_type": "model_eval_artifact_review",
                "task_id": "MGMT-QLIB-004",
                "ref": "support/reviews/MGMT-QLIB-004-review-codex2.md",
            },
        ],
        "expected_evidence_refs": [
            {
                "ref_type": "registry_admission_packet",
                "task_id": "MGMT-QLIB-005",
                "ref": "support/evidence/MGMT-QLIB-005/registry_admission_packet.json",
                "status": "pending_upstream_task",
            },
        ],
        "artifact_refs": [
            {
                "artifact_name": "model_artifact",
                "artifact_type": "model_artifact",
                "artifact_ref": f"{_LOCAL_TW_QLIB_ARTIFACT_ID}@1.0.0",
                "artifact_state": "draft",
                "deployment_stage": "none",
                "registry_id": _LOCAL_TW_QLIB_ARTIFACT_ID,
            },
            {
                "artifact_name": "evaluation_report",
                "artifact_type": "evaluation_result",
                "artifact_ref": f"eval-{_LOCAL_TW_QLIB_ARTIFACT_ID}@1.0.0",
                "target_artifact_ref": f"{_LOCAL_TW_QLIB_ARTIFACT_ID}@1.0.0",
                "artifact_state": "draft",
                "deployment_stage": "none",
            },
            {
                "artifact_name": "registry_entry_projection",
                "artifact_type": "registry_entry_projection",
                "artifact_ref": f"artifact://qlib/{_LOCAL_TW_QLIB_ARTIFACT_ID}/1.0.0/registry_entry",
                "artifact_state": "draft",
                "deployment_stage": "none",
            },
            {
                "artifact_name": "candidate_packet",
                "artifact_type": "registry_candidate_handoff",
                "artifact_ref": f"artifact://qlib/{_LOCAL_TW_QLIB_ARTIFACT_ID}/1.0.0/candidate_packet",
                "artifact_state": "draft",
                "deployment_stage": "none",
            },
        ],
        "ooda_refs": [
            {
                "stage": "observe",
                "ref_type": "dataset_manifest",
                "ref": _LOCAL_TW_QLIB_DATASET_MANIFEST_REF,
            },
            {
                "stage": "orient",
                "ref_type": "strategy_spec_packet",
                "ref": "support/evidence/MGMT-QLIB-002/strategy_spec_packet.json",
            },
            {
                "stage": "decide",
                "ref_type": "registry_admission_packet",
                "ref": "support/evidence/MGMT-QLIB-005/registry_admission_packet.json",
                "status": "pending_upstream_task",
            },
        ],
        "management_routes": {
            "artifact_detail": f"/bff/artifacts/{_LOCAL_TW_QLIB_ARTIFACT_ID}",
            "api_artifact_detail": f"/api/v1/artifacts/{_LOCAL_TW_QLIB_ARTIFACT_ID}",
            "research_experiment_detail": f"/bff/research-experiments/{_LOCAL_TW_QLIB_EXPERIMENT_ID}",
            "strategy_artifacts": f"/bff/strategies/{_LOCAL_TW_QLIB_STRATEGY_ID}/artifacts",
        },
        "safety_assertions": _local_tw_qlib_safety_assertions(),
    }


def _local_tw_qlib_research_experiment_default() -> dict[str, Any]:
    linkage = _local_tw_qlib_research_linkage()
    return {
        "experiment_id": _LOCAL_TW_QLIB_EXPERIMENT_ID,
        "ticket_id": "rt-mgmt-qlib-006",
        "experiment_name": "MGMT-QLIB-006 Qlib TW admission linkage",
        "status": "completed",
        "stage": "management_review_linked",
        "queued_at": "2026-05-15T17:25:00Z",
        "started_at": "2026-05-15T17:26:00Z",
        "completed_at": "2026-05-15T17:30:00Z",
        "progress": {
            "percent": 100,
            "phase": "management_review_linked",
            "message": "Management linkage packet is ready; registry admission remains gated.",
        },
        "strategy_selector": {"strategy_id": _LOCAL_TW_QLIB_STRATEGY_ID, "variant_id": None},
        "linked_strategy_id": _LOCAL_TW_QLIB_STRATEGY_ID,
        "parameter_set": {
            "framework": "qlib",
            "model_family": "lightgbm",
            "market": "TW",
            "universe": "tw-equity-top50",
        },
        "run_config": {
            "backend": "qlib",
            "dataset_ref": _LOCAL_TW_QLIB_DATASET_REF,
            "dataset_manifest_id": _LOCAL_TW_QLIB_DATASET_MANIFEST_ID,
            "time_range": {
                "start_at": "2024-01-02T00:00:00Z",
                "end_at": "2026-01-05T00:00:00Z",
            },
            "execution_mode": "offline_admission_review",
            "priority": "normal",
            "requested_by": "pathreon-management",
        },
        "launch_context": {
            "task_id": "MGMT-QLIB-006",
            "analysis_refs": None,
            "source_task_ids": linkage["source_task_ids"],
            "pending_task_ids": linkage["pending_task_ids"],
        },
        "validation_warnings": [
            {
                "code": "REGISTRY_ADMISSION_PENDING",
                "message": "MGMT-QLIB-005 registry admission evidence is pending; deployment is blocked.",
            }
        ],
        "artifact_ids": [_LOCAL_TW_QLIB_ARTIFACT_ID],
        "artifact_refs": linkage["artifact_refs"],
        "framework": "qlib",
        "dataset_ref": _LOCAL_TW_QLIB_DATASET_REF,
        "dataset_manifest_id": _LOCAL_TW_QLIB_DATASET_MANIFEST_ID,
        "research_linkage": linkage,
        "evidence_refs": _local_tw_qlib_evidence_refs(),
        "safety_assertions": _local_tw_qlib_safety_assertions(),
        "registry_admission_status": "pending_upstream_task",
        "can_deploy": False,
        "deployment_stage": "none",
        "failure": {"reason_code": None, "message": None},
        "governed_default_source": "composed_market_persona_defaults",
    }


HEADERS = {"Authorization": "Bearer op-pathreon-fleet:operator,reviewer,admin:mfa"}
PERSONA_FLEET_DEFAULT_TARGET_BYTES = 250_000
PERSONA_FLEET_DEFAULT_HARD_LIMIT_BYTES = 1_000_000
PERSONA_FLEET_ROW_HARD_LIMIT_BYTES = 8_000
PERSONA_FLEET_FORBIDDEN_LIST_KEYS = {
    "currentResearchProjects",
    "current_research_projects",
    "dataSourceRefs",
    "data_source_refs",
    "dataSourceStatus",
    "data_source_status",
    "dataSources",
    "requiredDataSources",
    "required_data_sources",
    "researchRefs",
    "research_refs",
    "researchStatus",
    "research_status",
    "sourceHealthBindings",
    "source_health_bindings",
}


MARKET_PERSONAS = {
    "US": "persona-us-equity",
    "TW": "persona-tw-equity",
    "CRYPTO": "persona-crypto",
}


def _make_store(
    *,
    allow_local_snapshot_fallback: bool = True,
    telemetry_service_summaries: list[dict[str, Any]] | None = None,
) -> ReadSurfacePorts:
    data: dict[str, Any] = {}

    def records(value: object) -> list[dict[str, Any]]:
        if isinstance(value, Mapping):
            source = value.values()
        elif isinstance(value, list):
            source = value
        else:
            source = []
        return [deepcopy(item) for item in source if isinstance(item, Mapping)]

    def load_records(env_name: str) -> list[dict[str, Any]]:
        path = os.getenv(env_name)
        if not path:
            return []
        try:
            return records(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, TypeError, ValueError):
            return []

    if not allow_local_snapshot_fallback:
        data.update(
            {
                "personas": load_records("PANTHEON_BFF_PERSONA_REGISTRY_STORE"),
                "sessions": load_records("PANTHEON_BFF_PERSONA_SESSION_STORE"),
                "bindings": load_records("PANTHEON_BFF_PERSONA_BINDING_STORE"),
                "runtime_bindings": load_records("PANTHEON_BFF_RUNTIME_BINDING_STORE"),
                "deployment_plans": load_records("PANTHEON_BFF_DEPLOYMENT_PLAN_STORE"),
                "telemetry_summaries": load_records("PANTHEON_BFF_TELEMETRY_SUMMARY_STORE"),
            }
        )

    personas = records(data.get("personas"))
    capital_pools = records(data.get("capital_pools"))
    bindings = records(data.get("bindings") or data.get("persona_bindings"))
    runtime_bindings = records(data.get("runtime_bindings"))
    deployment_plans = records(data.get("deployment_plans"))
    persona_league = records(data.get("persona_league"))
    sessions = records(data.get("sessions"))
    teaching_sessions = records(data.get("teaching_sessions"))
    capability_snapshots = records(data.get("capability_snapshots"))
    telemetry_summaries = records(data.get("telemetry_summaries"))
    if telemetry_service_summaries is not None:
        telemetry_summaries = records(telemetry_service_summaries)
    catalog_default_personas: list[dict[str, Any]] = []
    research_experiments = dict(data.get("research_experiments") or {})
    research_experiments.setdefault(
        "exp-mgmt-qlib-006",
        _local_tw_qlib_research_experiment_default(),
    )

    store = create_in_memory_read_surface_ports(
        persona_capital_runtime_kwargs={
            "personas": personas,
            "capital_pools": capital_pools,
            "bindings": bindings,
            "runtime_bindings": runtime_bindings,
            "deployment_plans": deployment_plans,
            "rankings": records(data.get("persona_rankings") or data.get("rankings")),
            "persona_league": persona_league,
            "rebalances": records(data.get("rebalances")),
            "capital_allocations": records(data.get("capital_allocations")),
            "containments": records(data.get("containments")),
        },
        ooda_management_kwargs={
            "ooda_packets": records(data.get("ooda_packets")),
            "deployment_plans": deployment_plans,
            "approval_decisions": records(data.get("governance_approvals")),
        },
        research_knowledge_source_kwargs={
            "strategy_specs_store": data.get("strategy_specs") or {},
            "research_experiments_store": research_experiments,
            "research_artifacts_store": data.get("research_artifacts") or {},
            "research_tickets_store": data.get("research_tickets") or {},
            "research_notes_store": data.get("research_notes") or {},
        },
        lifecycle_telemetry_governance_kwargs={
            "telemetry_summaries": telemetry_summaries,
            "incidents": data.get("incidents") or {},
        },
    )

    def list_persona_league(**kwargs: Any) -> list[dict[str, Any]]:
        market_scope = str(kwargs.get("market_scope") or "").upper()
        status = str(kwargs.get("status") or "").lower()
        rows = deepcopy(persona_league)
        if market_scope:
            rows = [
                row
                for row in rows
                if market_scope in {str(scope).upper() for scope in row.get("market_scope") or []}
            ]
        if status:
            rows = [row for row in rows if str(row.get("status") or "").lower() == status]
        return sorted(rows, key=lambda row: (int(row.get("rank") or 9999), str(row.get("persona_id") or "")))

    def list_personas(**kwargs: Any) -> list[dict[str, Any]]:
        rows = deepcopy(personas)
        if kwargs.get("include_market_persona_defaults"):
            known_ids = {
                str(row.get("persona_id") or row.get("id") or "") for row in rows
            }
            rows.extend(
                deepcopy(row)
                for row in catalog_default_personas
                if str(row.get("persona_id") or row.get("id") or "") not in known_ids
            )
        lifecycle_state = kwargs.get("lifecycle_state") or kwargs.get("status")
        if lifecycle_state:
            requested = str(lifecycle_state).lower()
            rows = [
                row
                for row in rows
                if str(row.get("lifecycle_state") or row.get("status") or "").lower()
                == requested
            ]
        return rows

    def list_bindings(**kwargs: Any) -> list[dict[str, Any]]:
        rows = deepcopy(bindings)
        persona_id = kwargs.get("persona_id")
        pool_id = kwargs.get("pool_id") or kwargs.get("capital_pool_id")
        if persona_id:
            rows = [row for row in rows if row.get("persona_id") == persona_id]
        if pool_id:
            rows = [row for row in rows if row.get("capital_pool_id") == pool_id]
        return rows

    def get_persona_league_entry(persona_id: str | None) -> dict[str, Any] | None:
        return next(
            (row for row in list_persona_league() if row.get("persona_id") == persona_id),
            None,
        )

    def create_persona(**kwargs: Any) -> dict[str, Any]:
        persona_id = str(kwargs["persona_id"])
        tenant_id = str(kwargs["tenant_id"]).strip()
        if not tenant_id:
            raise ValueError("admitted Persona fixtures require an explicit tenant_id")
        metadata = deepcopy(kwargs.get("metadata") or {})
        metadata.setdefault("owner", kwargs["actor_id"])
        metadata["tenant_id"] = tenant_id
        record = {
            "id": persona_id,
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "name": kwargs["name"],
            "owner": kwargs["actor_id"],
            "created_by": kwargs["actor_id"],
            "created_at": kwargs.get("created_at"),
            "lifecycle_state": kwargs.get("lifecycle_state") or "draft",
            "status": kwargs.get("lifecycle_state") or "draft",
            "metadata": metadata,
        }
        personas.append(record)
        return record

    def create_runtime_binding(**kwargs: Any) -> dict[str, Any]:
        params = deepcopy(kwargs.get("params") or {})
        runtime_id = str(kwargs["runtime_id"])
        record = {
            "id": runtime_id,
            "runtime_id": runtime_id,
            "name": kwargs["name"],
            "state": kwargs.get("state") or "stopped",
            "status": kwargs.get("state") or "stopped",
            "persona_id": kwargs["persona_id"],
            "binding_id": kwargs["binding_id"],
            "runtime_binding_id": kwargs["binding_id"],
            "persona_capital_binding_id": kwargs["binding_id"],
            "deployment_plan_id": kwargs["deployment_plan_id"],
            "plan_id": kwargs["deployment_plan_id"],
            "runtime_kind": kwargs["runtime_kind"],
            "deployment_stage": kwargs["runtime_kind"],
            "deployment_mode": kwargs["runtime_kind"],
            "capital_pool_id": params.get("capital_pool_id"),
            "params": params,
            "created_by": kwargs["actor_id"],
            "created_at": kwargs.get("created_at"),
            "metadata": {"persistenceMode": "local_typed_double"},
        }
        runtime_bindings.append(record)
        return record

    def list_runtime_bindings(**kwargs: Any) -> list[dict[str, Any]]:
        active_bindings = {
            str(binding.get("binding_id") or binding.get("persona_capital_binding_id") or ""): binding
            for binding in bindings
            if str(binding.get("status") or binding.get("validity") or "active").lower() == "active"
        }
        declared_by_runtime: dict[str, list[str]] = {}
        for persona in personas:
            metadata = persona.get("metadata") or {}
            runtime_id = metadata.get("runtimeId") or metadata.get("runtime_id")
            if runtime_id:
                declared_by_runtime.setdefault(str(runtime_id), []).append(str(persona.get("persona_id")))
        for session in sessions:
            if session.get("runtime_id") and session.get("persona_id") and session.get("active", True):
                declared_by_runtime.setdefault(str(session["runtime_id"]), []).append(str(session["persona_id"]))

        rows: list[dict[str, Any]] = []
        for source in runtime_bindings:
            row = deepcopy(source)
            runtime_id = str(row.get("runtime_id") or row.get("id") or "")
            runtime_binding_id = str(row.get("runtime_binding_id") or row.get("binding_id") or row.get("id") or "")
            capital_binding_id = str(row.get("persona_capital_binding_id") or "")
            canonical = active_bindings.get(capital_binding_id)
            candidates = sorted(set(declared_by_runtime.get(runtime_id) or []))
            row["runtime_binding_id"] = runtime_binding_id
            row["persona_capital_binding_id"] = capital_binding_id or row.get("binding_id")
            row["persona_id"] = (
                canonical.get("persona_id")
                if canonical is not None
                else candidates[0]
                if len(candidates) == 1
                else None
            )
            rows.append(row)
        deployment_mode = kwargs.get("deployment_mode")
        if deployment_mode:
            rows = [row for row in rows if row.get("deployment_mode") == deployment_mode]
        return rows

    def runtime_by_id(runtime_id: str | None) -> dict[str, Any] | None:
        return next(
            (row for row in list_runtime_bindings() if row.get("runtime_id") == runtime_id),
            None,
        )

    def runtime_by_binding(binding_id: str | None) -> dict[str, Any] | None:
        return next(
            (row for row in list_runtime_bindings() if row.get("runtime_binding_id") == binding_id),
            None,
        )

    ranking_snapshots: dict[str, dict[str, Any]] = {}
    store.list_personas = list_personas
    store.list_capital_pools = lambda **_kwargs: deepcopy(capital_pools)
    store.list_bindings = list_bindings
    store.list_persona_league = list_persona_league
    store.get_persona_league_entry = get_persona_league_entry
    store.create_persona = create_persona
    store.create_runtime_binding = create_runtime_binding
    store.list_runtime_bindings = list_runtime_bindings
    store.get_runtime_binding_by_runtime_id = runtime_by_id
    store.get_runtime_binding = runtime_by_binding
    store.put_ranking_snapshot = lambda snapshot: ranking_snapshots.setdefault(
        str(snapshot["ranking_snapshot_id"]), deepcopy(snapshot)
    )
    store.get_ranking_snapshot = lambda snapshot_id: deepcopy(
        ranking_snapshots.get(str(snapshot_id))
    )
    store._save = lambda: None
    store.list_persona_sessions = lambda persona_id, **_kwargs: [
        deepcopy(session) for session in sessions if session.get("persona_id") == persona_id
    ]
    store.get_sessions_for_persona = lambda persona_id: [
        deepcopy(session) for session in sessions if session.get("persona_id") == persona_id
    ]
    store.get_teaching_sessions_for_persona = lambda persona_id: [
        deepcopy(session)
        for session in teaching_sessions
        if session.get("persona_id") == persona_id
    ]
    store.get_capability_snapshot_for_persona = lambda persona_id: next(
        (
            deepcopy(snapshot)
            for snapshot in capability_snapshots
            if snapshot.get("persona_id") == persona_id
        ),
        None,
    )
    store.get_telemetry_summary = lambda runtime_id: next(
        (
            deepcopy(summary)
            for summary in telemetry_summaries
            if summary.get("runtime_id") == runtime_id
        ),
        None,
    )
    original_dataset_source = store.dataset_source
    store.dataset_source = lambda dataset: (
        "composed_market_persona_defaults"
        if dataset == "research_experiments"
        else original_dataset_source(dataset)
    )
    store.get_research_experiment = lambda experiment_id: research_experiments.get(experiment_id)
    store.list_research_experiments = lambda **kw: list(research_experiments.values())
    return store


class _SourceTruthReadStore:
    """Explicit registry/snapshot read-port fixture for PersonaService.overlay_source_health_truth.

    Migrated by BFF-LOOPS-PAPER-V5-PROJECTION-SEAM-CORRECTIVE-001: the overlay
    is now solely owned by a PersonaService instance and its own TTL cache,
    so it is exercised through its real read ports rather than by patching a
    bare module-level truth loader.
    """

    def __init__(self, truths: dict) -> None:
        self._truths = truths

    def get_source_connector_registry(self) -> dict:
        return {"connectors": [truth["connector"] for truth in self._truths.values()]}

    def get_source_health_usage_snapshot(self) -> dict:
        return {
            "sources": [
                {"health": truth["health"], "usage_aggregate_30d": {}}
                for truth in self._truths.values()
            ]
        }


def _overlay_source_health_truth(data_source_status, data_sources, *, required_data_sources=None, truths=None):
    service = PersonaService(
        write_owner=create_persona_registry_write_owner(),
        ranking_write_owner=object(),
        read_store=_SourceTruthReadStore(truths or {}),
        command_store=object(),
    )
    return service.overlay_source_health_truth(
        data_source_status, data_sources, required_data_sources=required_data_sources
    )


_live_source_health_by_connector = lambda: {}


def _overlay_live_finmind_health(data_source_status, data_sources):
    """Read-only overlay: flip the TW FinMind placeholder to live source-ingest health."""
    if not isinstance(data_source_status, dict):
        return data_source_status, data_sources
    provider_statuses = data_source_status.get("provider_statuses")
    if not isinstance(provider_statuses, dict) or "finmind" not in provider_statuses:
        return data_source_status, data_sources
    health = _live_source_health_by_connector().get("tw-finmind-datasets")
    if not isinstance(health, dict) or str(health.get("status") or "").lower() != "ok":
        return data_source_status, data_sources
    dss = json.loads(json.dumps(data_source_status))
    dss["provider_statuses"]["finmind"] = "read_ok"
    if dss.get("state") == "partial_readback":
        dss["state"] = "live_partial_readback"
    if health.get("last_success_at"):
        dss["finmind_live_last_success_at"] = health.get("last_success_at")
    if health.get("row_count_last_run") is not None:
        dss["finmind_live_row_count_last_run"] = health.get("row_count_last_run")
    srcs = json.loads(json.dumps(data_sources or []))
    for source in srcs:
        if isinstance(source, dict) and source.get("provider_key") == "finmind":
            source["status"] = "read_ok"
            source["reason"] = "live FinMind readback via source-ingest health snapshot"
    return dss, srcs


_PM12_FUNCS = None


def _compile_pm12_namespace(store):
    from functools import partial
    import inspect

    def _page_slice(items, page_token, page_size):
        start = int(page_token) if page_token else 0
        end = start + page_size
        next_page_token = str(end) if end < len(items) else None
        return items[start:end], next_page_token

    def _aggregate_group_surface(surface_key, source_surfaces, *, snapshot_at, unavailable_message, degraded_message):
        return {"status": "ok", "snapshot_at": snapshot_at, "source": "bff_composed", "available": True}

    ns = dict(__import__("typing").__dict__)
    ns.update({
        "__package__": "services.control_plane.bff.pm12",
        "datetime": datetime,
        "date": datetime.date,
        "timezone": timezone,
        "timedelta": timedelta,
        "read_store": store,
        "_list_persona_records": personas_service._list_persona_records,
        "utc_now": getattr(personas_service, "utc_now", lambda: datetime.now(timezone.utc).isoformat()),
        "_dataset_surface_status": getattr(personas_service, "_dataset_surface_status", lambda *a, **kw: {"status": "ok"}),
        "_PM12_ATTRIBUTION_DIMENSIONS": ("persona", "strategy", "pool", "asset", "broker", "runtime", "regime"),
        "ops_read_model_sanitize_metric": lambda v: v,
        "FastAPIParam": FastAPIParam,
        "_page_slice": _page_slice,
        "_aggregate_group_surface": _aggregate_group_surface,
        "_snapshot_meta": lambda snapshot_at: {"snapshot_at": snapshot_at},
        "_management_as_float": _management_as_float,
        "_management_nested_value": _management_nested_value,
        "_management_first_float": _management_first_float,
        "_management_telemetry_rollup": _management_telemetry_rollup,
        "_resolve_param": _resolve_param,
    })
    pm12_tree = ast.parse((Path(__file__).resolve().parent / "pm12" / "service.py").read_text(encoding="utf-8"))
    funcs = [
        n for n in pm12_tree.body
        if isinstance(n, ast.FunctionDef) and (n.name.startswith("_pm12_") or n.name.startswith("_management_"))
    ]
    exec(compile(ast.Module(body=funcs, type_ignores=[]), "main_pm12.py", "exec"), ns)
    for attr, val in list(ns.items()):
        if callable(val) and attr.startswith(("_pm12_", "_management_")):
            try:
                sig = inspect.signature(val)
                if "read_store" in sig.parameters:
                    ns[attr] = partial(val, read_store=store)
            except Exception:
                pass
    return ns


class _FakeCommandStore:
    def append_command(self, record):
        pass

    def _get_all_commands(self):
        return []

    def list_commands(self, *args, **kwargs):
        return []


@contextmanager
def _client_with_store(store: ReadSurfacePorts) -> Iterator[TestClient]:
    import os
    original_env = os.environ.get("PANTHEON_OODA_PACKET_ENABLED")
    os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)

    service = PersonaService(
        read_store=store,
        write_owner=store,
        ranking_write_owner=store,
        command_store=_FakeCommandStore(),
    )
    token = personas_service._current_persona_service.set(service)

    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
            return JSONResponse(status_code=exc.status_code, content=detail)
        code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else ("VALIDATION_FAILED" if exc.status_code == 422 else "ERROR")
        if isinstance(detail, dict) and "code" in detail:
            code = detail["code"]
            message = detail.get("message", "")
        else:
            message = str(detail)
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": message}})

    app.include_router(create_personas_router(service=service))

    def _page_slice(items, page_token, page_size):
        start = int(page_token) if page_token else 0
        end = start + page_size
        next_page_token = str(end) if end < len(items) else None
        return items[start:end], next_page_token

    runtime_router = create_runtime_router(
        get_read_store=lambda: store,
        dependencies={
            "_extract_identity": lambda _authorization: {"sub": "op", "roles": ["operator"]},
            "_require_read_role": lambda _identity: None,
            "_require_operator_role": lambda _identity: None,
            "_read_surface_meta": lambda *_args, **_kwargs: {},
            "_dataset_surface_status": lambda *a, **kw: {"status": "ok"},
            "_page_slice": _page_slice,
            "_snapshot_meta": lambda s: {"snapshot_at": s},
            "_meta_staleness": lambda: None,
            "utc_now": utc_now,
        },
    )
    app.include_router(runtime_router)

    pm12_ns = _compile_pm12_namespace(store)
    app.include_router(create_performance_attribution_router(
        bff_me_tenant_payload=lambda ident, requested_tenant=None: {"id": "pantheon-dev"},
        pm12_performance_attribution_response=pm12_ns["_pm12_performance_attribution_response"],
    ))

    def _bff_error(status_code, code, message, **kwargs):
        return HTTPException(status_code=status_code, detail={"code": code, "message": message})

    def _dataset_surface_status(dataset, **kwargs):
        source = kwargs.get("source") or (store.dataset_source(dataset) if hasattr(store, "dataset_source") else "bff_composed")
        return {
            "status": "ok",
            "source": source,
            "available": True,
        }

    app.include_router(create_research_router(
        read_surface=store,
        extract_identity=lambda _auth: {"sub": "op", "roles": ["operator"]},
        require_read_role=lambda _ident: None,
        require_operator_role=lambda _ident: None,
        bff_error=_bff_error,
        utc_now=utc_now,
        page_slice=_page_slice,
        snapshot_meta=lambda s: {"snapshot_at": s},
        dataset_surface_status=_dataset_surface_status,
        include_prepared_subrouters=True,
    ))

    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        personas_service._current_persona_service.reset(token)
        if original_env is None:
            os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)
        else:
            os.environ["PANTHEON_OODA_PACKET_ENABLED"] = original_env


def test_management_persona_fleet_prefers_declared_runtime_identity_over_market_default() -> None:
    persona_id = "persona-20260528-04688755"
    runtime_id = f"runtime-{persona_id}-paper"
    persona_binding_id = f"binding-{persona_id}-paper"
    pool_id = f"pool-{persona_id}-paper"
    store = _make_store(allow_local_snapshot_fallback=True)
    store.create_persona(
        persona_id=persona_id,
        tenant_id="pantheon-dev",
        name="Crypto-Alt-Hunter",
        actor_id="pantheon-dev-browser",
        created_at="2026-05-28T00:00:00Z",
        lifecycle_state="deployed",
        metadata={
            "capital_mode": "paper",
            "deployment_stage": "paper",
            "legacy_paper_capital_pool_id": pool_id,
            "runtime_id": runtime_id,
            "runtime_binding_id": persona_binding_id,
        },
    )
    runtime_record = store.create_runtime_binding(
        runtime_id=runtime_id,
        name="Crypto-Alt-Hunter paper runtime",
        persona_id=persona_id,
        binding_id=persona_binding_id,
        deployment_plan_id=f"paper-plan-{persona_id}",
        runtime_kind="paper",
        actor_id="pantheon-dev-browser",
        params={"capital_pool_id": pool_id},
        state="active",
    )
    runtime_record["persona_id"] = None
    store._save()

    with _client_with_store(store) as client:
        fleet_response = client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=HEADERS,
        )
        runtime_response = client.get("/bff/runtimes?page_size=200", headers=HEADERS)

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_row = next(
        item
        for item in fleet_response.json()["data"]["items"]
        if item["persona_id"] == persona_id
    )
    assert fleet_row["legacy_paper_capital_pool_id"] == pool_id
    assert fleet_row["runtime_id"] == runtime_id
    assert fleet_row["runtime_binding_id"] == persona_binding_id

    assert runtime_response.status_code == 200, runtime_response.text
    runtime_row = next(
        item for item in runtime_response.json()["items"] if item["runtime_id"] == runtime_id
    )
    assert runtime_row["persona_id"] == persona_id
    assert runtime_row["persona_capital_binding_id"] == persona_binding_id


def test_real_paper_runtime_identity_drives_formal_persona_attribution_and_fleet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persona_a = "persona-paper-alpha"
    persona_b = "persona-paper-beta"
    runtime_a = "runtime-persona-paper-alpha-paper"
    runtime_b = "runtime-persona-paper-beta-paper"
    runtime_binding_a = "rb-paper-alpha"
    runtime_binding_b = "rb-paper-beta"
    capital_binding_a = "binding-persona-paper-alpha-paper"
    capital_binding_b = "binding-persona-paper-beta-paper"
    pool_a = "pool-persona-paper-alpha-paper"
    pool_b = "pool-persona-paper-beta-paper"
    observed_at = utc_now()

    def write_store(name: str, payload: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    stores = {
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE": write_store(
            "personas.json",
            {
                persona_a: {
                    "persona_id": persona_a,
                    "tenant_id": "pantheon-dev",
                    "name": "Paper Alpha",
                    "lifecycle_state": "paper_running",
                    "status": "paper_running",
                    "created_at": "2026-07-13T00:00:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                        "runtimeId": runtime_a,
                        "runtimeBindingId": capital_binding_a,
                        "legacyPaperCapitalPoolId": pool_a,
                    },
                },
                persona_b: {
                    "persona_id": persona_b,
                    "tenant_id": "pantheon-dev",
                    "name": "Paper Beta",
                    "lifecycle_state": "paper_running",
                    "status": "paper_running",
                    "created_at": "2026-07-13T00:01:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                        "runtime_id": runtime_b,
                        "runtime_binding_id": capital_binding_b,
                        "legacy_paper_capital_pool_id": pool_b,
                    },
                },
            },
        ),
            "PANTHEON_BFF_PERSONA_SESSION_STORE": write_store(
                "sessions.json",
                {
                    "session-paper-alpha": {
                        "session_id": "session-paper-alpha",
                        "persona_id": persona_a,
                        "runtime_id": runtime_a,
                        "runtime_binding_id": runtime_binding_a,
                        "status": "active",
                        "active": True,
                        "last_heartbeat_at": observed_at,
                    },
                    "session-paper-beta": {
                        "session_id": "session-paper-beta",
                        "persona_id": persona_b,
                        "runtime_id": runtime_b,
                        "runtime_binding_id": runtime_binding_b,
                        "status": "active",
                        "active": True,
                        "last_heartbeat_at": observed_at,
                    },
                },
            ),
        "PANTHEON_BFF_PERSONA_BINDING_STORE": write_store(
            "persona_capital_bindings.json",
            {
                capital_binding_a: {
                    "binding_id": capital_binding_a,
                    "persona_id": persona_a,
                    "capital_pool_id": pool_a,
                    "status": "active",
                },
                capital_binding_b: {
                    "binding_id": capital_binding_b,
                    "persona_id": persona_b,
                    "capital_pool_id": pool_b,
                    "status": "active",
                },
            },
        ),
        "PANTHEON_BFF_RUNTIME_BINDING_STORE": write_store(
            "runtime_bindings.json",
            {
                runtime_binding_a: {
                    "binding_id": runtime_binding_a,
                    "runtime_id": runtime_a,
                    "persona_id": "persona-us-equity",
                    "persona_capital_binding_id": capital_binding_a,
                    "capital_pool_id": pool_a,
                    "plan_id": "plan-paper-alpha",
                    "deployment_mode": "paper",
                    "status": "active",
                },
                runtime_binding_b: {
                    "binding_id": runtime_binding_b,
                    "runtime_id": runtime_b,
                    "persona_capital_binding_id": capital_binding_b,
                    "capital_pool_id": pool_b,
                    "plan_id": "plan-paper-beta",
                    "deployment_mode": "paper",
                    "status": "active",
                },
            },
        ),
        "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE": write_store(
            "deployment_plans.json",
            {
                "plan-paper-alpha": {
                    "plan_id": "plan-paper-alpha",
                    "binding_ids": [capital_binding_a],
                    "capital_pool_id": pool_a,
                    "strategy_id": "strategy-paper-alpha",
                    "target_stage": "paper",
                    "status": "executed",
                },
                "plan-paper-beta": {
                    "plan_id": "plan-paper-beta",
                    "binding_ids": [capital_binding_b],
                    "capital_pool_id": pool_b,
                    "strategy_id": "strategy-paper-beta",
                    "target_stage": "paper",
                    "status": "executed",
                },
            },
        ),
        "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE": write_store(
            "telemetry_summaries.json",
            {
                runtime_a: {
                    "runtime_id": runtime_a,
                    "projection_source": "telemetry_ingest",
                    "collected_at": "2026-07-13T00:10:00Z",
                    "pnl": 0.12,
                    "drawdown": 0.02,
                    "fill_rate": 0.99,
                    "avg_slippage_bps": 1.0,
                    "sharpe_ratio": 1.8,
                    "total_trades": 17,
                },
                runtime_b: {
                    "runtime_id": runtime_b,
                    "projection_source": "telemetry_ingest",
                    "collected_at": "2026-07-13T00:11:00Z",
                    "summary": {
                        "total_pnl": -0.08,
                        "max_drawdown": 0.08,
                        "fill_rate": 0.80,
                        "avg_slippage_bps": 5.0,
                        "sharpe": 0.3,
                        "total_trades": 0,
                    },
                },
            },
        ),
    }
    for env_name in (
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_DATA_DIR",
        "PANTHEON_PERSONA_SERVICE_URL",
        "PANTHEON_RUNTIME_MANAGER_URL",
        "PANTHEON_TELEMETRY_API_URL",
        "PANTHEON_TELEMETRY_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    for env_name, path in stores.items():
        monkeypatch.setenv(env_name, str(path))

    store = _make_store(allow_local_snapshot_fallback=False)
    runtimes = {runtime["runtime_id"]: runtime for runtime in store.list_runtime_bindings()}
    assert runtimes[runtime_a]["persona_id"] == persona_a
    assert runtimes[runtime_b]["persona_id"] == persona_b
    assert runtimes[runtime_a]["runtime_binding_id"] == runtime_binding_a
    assert runtimes[runtime_b]["runtime_binding_id"] == runtime_binding_b
    assert runtimes[runtime_a]["persona_capital_binding_id"] == capital_binding_a
    assert runtimes[runtime_b]["persona_capital_binding_id"] == capital_binding_b

    with _client_with_store(store) as client:
        attribution_response = client.get(
            "/bff/management/performance-attribution/by-persona?page_size=100",
            headers=HEADERS,
        )
        rankings_response = client.get(
            "/bff/management/persona-league/rankings?criteria=overall&limit=100",
            headers=HEADERS,
        )
        fleet_response = client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=HEADERS,
        )

    assert attribution_response.status_code == 200, attribution_response.text
    attribution_rows = {
        item["dimension_key"]: item
        for item in attribution_response.json()["data"]["items"]
    }
    assert attribution_rows[persona_a]["data_confidence"] == "formal"
    assert attribution_rows[persona_b]["data_confidence"] == "formal"
    assert attribution_rows[persona_a]["source_refs"]["runtime_ids"] == [runtime_a]
    assert attribution_rows[persona_b]["source_refs"]["runtime_ids"] == [runtime_b]
    assert attribution_rows[persona_a]["metrics"]["total_pnl"] == 0.12
    assert attribution_rows[persona_b]["metrics"]["total_pnl"] == -0.08
    assert attribution_rows[persona_a]["metrics"]["total_trades"] == 17
    assert attribution_rows[persona_b]["metrics"]["total_trades"] == 0
    assert all(
        runtime_a not in item["source_refs"]["runtime_ids"]
        and runtime_b not in item["source_refs"]["runtime_ids"]
        for item in attribution_rows.values()
        if item["dimension_key"] == "unassigned"
    )

    assert rankings_response.status_code == 200, rankings_response.text
    ranking_rows = {
        item["persona_id"]: item
        for item in rankings_response.json()["data"]["items"][0]["items"]
    }
    assert ranking_rows[persona_a]["eligible"] is True
    assert ranking_rows[persona_b]["eligible"] is True
    assert ranking_rows[persona_a]["exclusion_reason"] is None
    assert ranking_rows[persona_b]["exclusion_reason"] is None
    assert ranking_rows[persona_a]["evidence_coverage"] > 0
    assert ranking_rows[persona_b]["evidence_coverage"] > 0
    assert ranking_rows[persona_a]["source_confidence"] == "formal"
    assert ranking_rows[persona_b]["source_confidence"] == "formal"
    assert ranking_rows[persona_a]["metrics"]["runtime_ids"] == [runtime_a]
    assert ranking_rows[persona_b]["metrics"]["runtime_ids"] == [runtime_b]
    assert ranking_rows[persona_a]["metrics"]["total_trades"] == 17
    assert ranking_rows[persona_b]["metrics"]["total_trades"] == 0
    assert ranking_rows[persona_a]["score"] > ranking_rows[persona_b]["score"]

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_rows = {
        item["persona_id"]: item
        for item in fleet_response.json()["data"]["items"]
    }
    assert fleet_rows[persona_a]["runtime_id"] == runtime_a
    assert fleet_rows[persona_b]["runtime_id"] == runtime_b
    assert fleet_rows[persona_a]["runtime_binding_id"] == runtime_binding_a
    assert fleet_rows[persona_b]["runtime_binding_id"] == runtime_binding_b
    assert fleet_rows[persona_a]["performance_summary"]["source"] == "telemetry_summaries"
    assert fleet_rows[persona_b]["performance_summary"]["source"] == "telemetry_summaries"
    assert fleet_rows[persona_a]["performance_summary"]["pnl"] == 0.12
    assert fleet_rows[persona_b]["performance_summary"]["pnl"] == -0.08
    assert fleet_rows[persona_a]["performance_summary"]["total_trades"] == 17
    assert fleet_rows[persona_b]["performance_summary"]["total_trades"] == 0
    seed_values = {24560.0, 426000.0, 48000.0, 0.057, 0.071, 0.064}
    for persona_id in (persona_a, persona_b):
        performance = fleet_rows[persona_id]["performance_summary"]
        assert performance["pnl"] not in seed_values
        assert performance["max_drawdown"] not in seed_values


def test_runtime_registry_identity_reconciliation_is_unique_and_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persona_store = tmp_path / "personas.json"
    runtime_store = tmp_path / "runtime_bindings.json"
    binding_store = tmp_path / "persona_capital_bindings.json"
    persona_store.write_text(
        json.dumps(
            {
                "persona-candidate-a": {
                    "persona_id": "persona-candidate-a",
                    "metadata": {"runtimeId": "runtime-ambiguous"},
                },
                "persona-candidate-b": {
                    "persona_id": "persona-candidate-b",
                    "metadata": {"runtime_id": "runtime-ambiguous"},
                },
                "persona-unique": {
                    "persona_id": "persona-unique",
                    "metadata": {"runtimeId": "runtime-unique"},
                },
            }
        ),
        encoding="utf-8",
    )
    runtime_store.write_text(
        json.dumps(
            {
                "rb-ambiguous": {
                    "binding_id": "rb-ambiguous",
                    "runtime_id": "runtime-ambiguous",
                    "persona_id": "persona-us-equity",
                    "deployment_mode": "paper",
                },
                "rb-unique": {
                    "binding_id": "rb-unique",
                    "runtime_id": "runtime-unique",
                    "persona_id": "persona-us-equity",
                    "deployment_mode": "paper",
                },
            }
        ),
        encoding="utf-8",
    )
    binding_store.write_text("{}", encoding="utf-8")
    for env_name in (
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_DATA_DIR",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("PANTHEON_BFF_PERSONA_REGISTRY_STORE", str(persona_store))
    monkeypatch.setenv("PANTHEON_BFF_RUNTIME_BINDING_STORE", str(runtime_store))
    monkeypatch.setenv("PANTHEON_BFF_PERSONA_BINDING_STORE", str(binding_store))

    store = _make_store(allow_local_snapshot_fallback=False)
    runtimes = {runtime["runtime_id"]: runtime for runtime in store.list_runtime_bindings()}

    assert runtimes["runtime-unique"]["persona_id"] == "persona-unique"
    assert runtimes["runtime-ambiguous"]["persona_id"] is None


def test_pm12_authoritative_runtime_id_avoids_stale_alias_probe_and_reuses_summary(
    monkeypatch,
) -> None:
    calls: list[str] = []

    def telemetry_summary(runtime_id: str):
        calls.append(runtime_id)
        return {
            "runtime_id": runtime_id,
            "pnl": 0.0,
            "drawdown": 0.0,
            "total_trades": 0,
            "collected_at": "2026-07-13T00:00:00Z",
        }

    class _TelemetryStore:
        def get_telemetry_summary(self, runtime_id: str):
            return telemetry_summary(runtime_id)

        def list_runtime_bindings(self, **kw):
            return []

        def dataset_source(self, *a, **kw):
            return "mock"

    svc = PersonaService(
        read_store=_TelemetryStore(),
        write_owner=_TelemetryStore(),
        ranking_write_owner=_TelemetryStore(),
        command_store=_FakeCommandStore(),
    )
    token = personas_service._current_persona_service.set(svc)
    try:
        row = {
            "binding_summary": {"runtime_ids": ["runtime-authoritative"]},
            "session_summary": {
                "runtime_ids": [],
                "runtime_binding_ids": ["rb-stale-session-alias"],
            },
        }

        metrics = _pm12_persona_telemetry_metrics(row)

        assert calls == ["runtime-authoritative"]
        assert metrics["runtime_ids"] == ["runtime-authoritative"]
        assert metrics["pnl"] == 0.0
        assert metrics["drawdown"] == 0.0
        assert metrics["total_trades"] == 0
    finally:
        personas_service._current_persona_service.reset(token)


def test_management_persona_fleet_keeps_market_personas_with_live_dev_overlay_only() -> None:
    store = _make_store(allow_local_snapshot_fallback=False)
    store.create_persona(
        persona_id="persona-dev-probe",
        tenant_id="pantheon-dev",
        name="dev-probe",
        actor_id="pantheon-dev-browser",
        lifecycle_state="paper",
        created_at="2026-06-03T08:27:44Z",
        metadata={"owner": "pantheon-dev-browser"},
    )
    with _client_with_store(store) as client:
        response = client.get("/bff/management/persona-fleet", headers=HEADERS)

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    rows = {item["persona_id"]: item for item in data["items"]}
    assert "persona-dev-probe" in rows
    assert not any(market_id in rows for market_id in MARKET_PERSONAS.values())
    assert data["summary"]["total_personas"] == 1
    assert data["summary"]["canonical_total"] == 1
    assert data["summary"]["catalog_default_total"] >= 0
    assert "capital_pools" not in data
    assert "persona_league" not in data


def _seed_fleet_classification_store(archived_count: int = 105):
    store = _make_store(allow_local_snapshot_fallback=False)

    def add(pid, state, metadata, tenant="pantheon-dev"):
        store.create_persona(
            persona_id=pid,
            tenant_id=tenant,
            name=pid,
            actor_id="pantheon-dev-browser",
            lifecycle_state=state,
            created_at="2026-06-03T08:27:44Z",
            metadata={"owner": "pantheon-dev-browser", **metadata},
        )

    prov = {"provisioning_idempotency_key": "k", "capital_mode": "paper"}
    add("p-draft", "draft", {"provisioning_idempotency_key": "k"})
    add("p-paper", "paper", prov)
    add("p-test", "paper", {**prov, "data_scope": "test"})
    add("p-legacy", "paper", {})
    add(
        "p-servant",
        "paper",
        {"persona_class": "agora_servant", "agora_user_id": "u1", "execution_authority": "none"},
    )
    add("p-other-tenant", "paper", prov, tenant="other-tenant")
    for i in range(archived_count):
        add(f"p-old-{i:04d}", "retired", prov)
    return store


def _fleet(client, **params):
    response = client.get("/bff/management/persona-fleet", headers=HEADERS, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_persona_fleet_purpose_data_scope_and_archive_filters() -> None:
    store = _seed_fleet_classification_store()
    with _client_with_store(store) as client:
        default = _fleet(client, page_size=200)
        trading = _fleet(client, purpose="trading", data_scope="regular", archived="exclude")
        archived = _fleet(client, archived="only", page_size=20)
        unknown = _fleet(client, data_scope="unknown", archived="exclude")
        test = _fleet(client, data_scope="test", archived="exclude")
        advisory = _fleet(client, purpose="advisory_assistant", archived="exclude")

    # Omitted parameters keep the historical contents (everything, incl. retired).
    assert default["page_info"]["total"] == 110
    ids = {i["persona_id"] for i in trading["data"]["items"]}
    assert ids == {"p-draft", "p-paper"}
    assert "p-other-tenant" not in {i["persona_id"] for i in default["data"]["items"]}
    draft = next(i for i in trading["data"]["items"] if i["persona_id"] == "p-draft")
    assert draft["persona_purpose"] == "trading" and draft["data_scope"] == "regular"
    # Archive count is global, not just the page.
    pop = trading["data"]["summary"]["population"]
    assert pop["archived_total"] == 105 and pop["authorized_total"] == 110
    assert archived["page_info"]["total"] == 105
    assert len(archived["data"]["items"]) == 20 and archived["page_info"]["next_page_token"]
    assert {i["persona_id"] for i in unknown["data"]["items"]} == {"p-legacy"}
    assert {i["persona_id"] for i in test["data"]["items"]} == {"p-test"}
    servant = advisory["data"]["items"][0]
    assert servant["persona_id"] == "p-servant"
    for key in ("league_rank", "rank", "paper_ledger_id", "paper_ledger", "capital_mode", "deployment_stage"):
        assert servant[key] is None
    assert servant["state"] == "advisory" and servant["health"] == "not_applicable"


def test_persona_fleet_archived_pages_traverse_stably_beyond_100() -> None:
    store = _seed_fleet_classification_store(archived_count=105)
    seen = []
    with _client_with_store(store) as client:
        token = None
        while True:
            params = {"archived": "only", "page_size": 40}
            if token:
                params["page_token"] = token
            body = _fleet(client, **params)
            seen.extend(i["persona_id"] for i in body["data"]["items"])
            token = body["page_info"]["next_page_token"]
            if not token:
                break
    assert len(seen) == 105 and len(set(seen)) == 105


def test_persona_fleet_servant_excluded_from_trading_ranks_and_invalid_filter_rejected() -> None:
    store = _seed_fleet_classification_store(archived_count=0)
    with _client_with_store(store) as client:
        body = _fleet(client, archived="exclude", page_size=200)
        bad = client.get("/bff/management/persona-fleet", headers=HEADERS, params={"purpose": "bogus"})
    ranked = [
        i["rank"]["league_rank"]
        for i in body["data"]["items"]
        if isinstance(i.get("rank"), dict) and i["rank"].get("league_rank") is not None
    ]
    assert "p-servant" not in {
        i["persona_id"] for i in body["data"]["items"] if i.get("rank")
    }
    assert len(ranked) == len(set(ranked))
    assert bad.status_code == 422, bad.text


def test_unadmitted_catalog_defaults_do_not_fabricate_ghost_fleet_rows_or_detail() -> None:
    store = _make_store(allow_local_snapshot_fallback=False)
    with _client_with_store(store) as client:
        fleet = client.get("/bff/management/persona-fleet", headers=HEADERS)
        detail = client.get("/bff/personas/persona-crypto", headers=HEADERS)
        unknown = client.get("/bff/personas/persona-not-in-catalog", headers=HEADERS)

    assert fleet.status_code == 200, fleet.text
    fleet_ids = {item["persona_id"] for item in fleet.json()["data"]["items"]}
    assert "persona-crypto" not in fleet_ids
    summary = fleet.json()["data"]["summary"]
    assert summary["canonical_total"] == 0
    assert summary["catalog_default_total"] >= 0

    assert detail.status_code == 404, detail.text
    assert detail.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_tw_qlib_research_experiment_drilldown_is_governed_default_not_seed() -> None:
    store = _make_store(allow_local_snapshot_fallback=False)
    with _client_with_store(store) as client:
        detail = client.get(
            "/bff/research-experiments/exp-mgmt-qlib-006",
            headers=HEADERS,
        )
        listing = client.get("/bff/research-experiments", headers=HEADERS)

    assert detail.status_code == 200, detail.text
    payload = detail.json()
    record = payload["data"]
    assert record["experiment_id"] == "exp-mgmt-qlib-006"
    assert record["stage"] == "management_review_linked"
    assert record["framework"] == "qlib"
    assert record["dataset_ref"] == "dataset:tw-equity-ohlcv-top50-2024-daily"
    assert record["dataset_manifest_id"] == (
        "qlib-dataset-manifest:dataset-tw-equity-ohlcv-top50-2024-daily"
    )
    assert record["research_linkage"]["admission_stage"] == "management_review_linked"
    assert record["registry_admission_status"] == "pending_upstream_task"
    assert record["can_deploy"] is False
    assert record["safety_assertions"]["broker_session_opened"] is False
    assert record["safety_assertions"]["order_route"] == "none"
    surface = payload["meta"]["surfaces"]["research_experiment_detail"]
    assert surface["status"] == "ok"
    assert surface["source"] == "composed_market_persona_defaults"

    assert listing.status_code == 200, listing.text
    list_payload = listing.json()
    ids = {item["experiment_id"] for item in list_payload["items"]}
    surface_list = list_payload["meta"]["surfaces"]["research_experiments"]
    assert surface_list["status"] == "ok"
    assert surface_list["source"] == "composed_market_persona_defaults"


def test_overlay_live_finmind_health_flips_to_read_ok(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {"finmind": "read_unavailable", "shioaji": "read_ok"},
    }
    sources = [
        {"provider_key": "finmind", "status": "read_unavailable"},
        {"provider_key": "shioaji", "status": "read_ok"},
    ]
    import sys
    monkeypatch.setattr(
        sys.modules[__name__],
        "_live_source_health_by_connector",
        lambda: {"tw-finmind-datasets": {"status": "ok", "last_success_at": "2026-06-27T05:00:00Z", "row_count_last_run": 8}},
    )
    out_dss, out_sources = _overlay_live_finmind_health(dss, sources)
    assert out_dss["provider_statuses"]["finmind"] == "read_ok"
    assert out_dss["state"] == "live_partial_readback"
    assert out_dss["finmind_live_row_count_last_run"] == 8
    by_key = {s["provider_key"]: s for s in out_sources}
    assert by_key["finmind"]["status"] == "read_ok"


def test_overlay_live_finmind_health_noop_when_unavailable(monkeypatch):
    import sys
    dss = {"state": "partial_readback", "provider_statuses": {"finmind": "read_unavailable"}}
    monkeypatch.setattr(sys.modules[__name__], "_live_source_health_by_connector", lambda: {})
    out_dss, _ = _overlay_live_finmind_health(
        dss, [{"provider_key": "finmind", "status": "read_unavailable"}]
    )
    assert out_dss["provider_statuses"]["finmind"] == "read_unavailable"
    assert out_dss["state"] == "partial_readback"


def test_source_health_truth_overlay_projects_connector_panel_fields(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "finmind": "read_unavailable",
            "twse": "read_unavailable",
            "tpex": "read_unavailable",
        },
    }
    sources = [
        {"provider_key": "finmind", "status": "read_unavailable"},
        {"provider_key": "twse", "status": "read_unavailable"},
        {"provider_key": "tpex", "status": "read_unavailable"},
    ]
    required_sources = [
        {
            "dataset": "tw_broker_top",
            "market": "TW",
            "cadence": "daily",
            "source_class": "live_push",
            "connector_candidates": ["tw-finmind-broker-daily-report"],
        }
    ]
    out_dss, out_sources, bindings = _overlay_source_health_truth(
        dss,
        sources,
        required_data_sources=required_sources,
        truths={
            "tw-finmind-broker-daily-report": {
                "health": {
                    "source_id": "tw-finmind-broker-daily-report",
                    "status": "failed",
                    "last_success_at": "2026-06-27T05:00:00Z",
                    "last_failure_at": "2026-06-27T06:00:00Z",
                    "latest_watermark": "2026-06-26",
                    "row_count_last_run": 0,
                    "metadata": {"source_error": "FinMind quota exhausted"},
                },
                "connector": {
                    "connector_id": "tw-finmind-broker-daily-report",
                    "status": "enabled",
                    "schedule": {
                        "configured": True,
                        "enabled": True,
                        "interval_seconds": 86400,
                    },
                    "freshness": {
                        "status": "degraded",
                        "latest_run": {
                            "ingest_run_id": "run-finmind-001",
                            "status": "failed",
                            "finished_at": "2026-06-27T06:00:00Z",
                        },
                    },
                    "health_metrics": {"source_error": "FinMind quota exhausted"},
                },
            },
            "tw-twse-tpex-official-market": {
                "health": {
                    "source_id": "tw-twse-tpex-official-market",
                    "status": "ok",
                    "last_success_at": "2026-06-27T04:30:00Z",
                    "row_count_last_run": 42,
                    "metadata": {},
                },
                "connector": {
                    "connector_id": "tw-twse-tpex-official-market",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "fresh", "last_success_at": "2026-06-27T04:30:00Z"},
                    "health_metrics": {},
                },
            },
        },
    )

    assert out_dss["source_health_source"] == "source_ingest"
    assert out_dss["live_ingestion_enabled"] is True
    assert out_dss["provider_statuses"]["finmind"] == "source_health_failed"
    assert out_dss["provider_statuses"]["twse"] == "read_ok"
    by_provider = {source["provider_key"]: source for source in out_sources}
    finmind = by_provider["finmind"]
    assert finmind["health_source"] == "source_ingest"
    assert finmind["connectorSchedule"]["enabled"] is True
    assert finmind["lastFetchAt"] == "2026-06-27T06:00:00Z"
    assert finmind["lastPushAt"] == "2026-06-27T05:00:00Z"
    assert finmind["failureReason"] == "FinMind quota exhausted"
    assert bindings[0]["source_class"] == "live_push"
    assert bindings[0]["selectedConnectorId"] == "tw-finmind-broker-daily-report"
    assert bindings[0]["failureReason"] == "FinMind quota exhausted"


def test_overlay_preserves_credential_unavailable_when_health_degraded(monkeypatch):
    """polygon/alphavantage must stay credential_unavailable when source-ingest
    reports degraded health (missing key).  The only valid upgrade path is
    health.status=ok.  Regression probe for SRCLIVE-002 review issue (1)."""
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "polygon": "credential_unavailable",
            "alphavantage": "credential_unavailable",
        },
    }
    sources = [
        {
            "provider_key": "polygon",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://POLYGON_API_KEY",
            "secret_ref": "env://POLYGON_API_KEY",
        },
        {
            "provider_key": "alphavantage",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://ALPHA_VANTAGE_API_KEY",
            "secret_ref": "env://ALPHA_VANTAGE_API_KEY",
        },
    ]
    out_dss, out_sources, _bindings = _overlay_source_health_truth(
        dss,
        sources,
        truths={
            "us-polygon-daily-ohlcv": {
                "health": {
                    "source_id": "us-polygon-daily-ohlcv",
                    "status": "degraded",
                    "metadata": {"credential_status": "credential_unavailable"},
                },
                "connector": {
                    "connector_id": "us-polygon-daily-ohlcv",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "degraded"},
                    "health_metrics": {},
                },
            },
            "us-alpha-vantage-daily-ohlcv": {
                "health": {
                    "source_id": "us-alpha-vantage-daily-ohlcv",
                    "status": "degraded",
                    "metadata": {"credential_status": "credential_unavailable"},
                },
                "connector": {
                    "connector_id": "us-alpha-vantage-daily-ohlcv",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "degraded"},
                    "health_metrics": {},
                },
            },
        },
    )

    by_provider = {s["provider_key"]: s for s in out_sources}

    polygon = by_provider["polygon"]
    assert polygon["status"] == "credential_unavailable", (
        "polygon must not be projected as source_health_degraded when credential is missing"
    )
    assert polygon.get("secret_ref") == "env://POLYGON_API_KEY"
    assert "POLYGON_API_KEY" in (polygon.get("reason") or "")

    alphavantage = by_provider["alphavantage"]
    assert alphavantage["status"] == "credential_unavailable", (
        "alphavantage must not be projected as source_health_degraded when credential is missing"
    )
    assert alphavantage.get("secret_ref") == "env://ALPHA_VANTAGE_API_KEY"
    assert "ALPHA_VANTAGE_API_KEY" in (alphavantage.get("reason") or "")

    assert out_dss["provider_statuses"]["polygon"] == "credential_unavailable"
    assert out_dss["provider_statuses"]["alphavantage"] == "credential_unavailable"


def test_overlay_upgrades_credential_unavailable_when_health_ok(monkeypatch):
    """When source-ingest confirms health.status=ok (key is now present and working),
    credential_unavailable must be upgraded to read_ok."""
    dss = {
        "state": "partial_readback",
        "provider_statuses": {"polygon": "credential_unavailable"},
    }
    sources = [
        {
            "provider_key": "polygon",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://POLYGON_API_KEY",
            "secret_ref": "env://POLYGON_API_KEY",
        },
    ]
    out_dss, out_sources, _bindings = _overlay_source_health_truth(
        dss,
        sources,
        truths={
            "us-polygon-daily-ohlcv": {
                "health": {
                    "source_id": "us-polygon-daily-ohlcv",
                    "status": "ok",
                    "last_success_at": "2026-06-28T01:00:00Z",
                    "row_count_last_run": 500,
                    "metadata": {},
                },
                "connector": {
                    "connector_id": "us-polygon-daily-ohlcv",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "fresh", "last_success_at": "2026-06-28T01:00:00Z"},
                    "health_metrics": {},
                },
            },
        },
    )

    by_provider = {s["provider_key"]: s for s in out_sources}
    polygon = by_provider["polygon"]
    assert polygon["status"] == "read_ok", (
        "polygon must be read_ok when source-ingest confirms health.status=ok"
    )
    assert out_dss["provider_statuses"]["polygon"] == "read_ok"


def test_source_health_truth_overlay_maps_stooq_and_preserves_fred_key_gate(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "stooq": "read_unavailable",
            "fred": "credential_unavailable",
        },
    }
    sources = [
        {"provider_key": "stooq", "status": "read_unavailable"},
        {
            "provider_key": "fred",
            "status": "credential_unavailable",
            "reason": "FRED_API_KEY is not configured",
            "secret_ref": "env://FRED_API_KEY",
        },
    ]
    out_dss, out_sources, _bindings = _overlay_source_health_truth(
        dss,
        sources,
        truths={
            "us-stooq-daily-ohlcv": {
                "health": {
                    "source_id": "us-stooq-daily-ohlcv",
                    "status": "ok",
                    "last_success_at": "2026-06-28T01:00:00Z",
                    "row_count_last_run": 3,
                    "metadata": {"provider": "Stooq"},
                },
                "connector": {
                    "connector_id": "us-stooq-daily-ohlcv",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "fresh", "last_success_at": "2026-06-28T01:00:00Z"},
                    "health_metrics": {},
                },
            },
            "us-fred-macro": {
                "health": {
                    "source_id": "us-fred-macro",
                    "status": "degraded",
                    "metadata": {"credential_status": "credential_unavailable"},
                },
                "connector": {
                    "connector_id": "us-fred-macro",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "degraded"},
                    "health_metrics": {},
                },
            },
        },
    )

    by_provider = {s["provider_key"]: s for s in out_sources}
    assert by_provider["stooq"]["status"] == "read_ok"
    assert by_provider["stooq"]["connectorId"] == "us-stooq-daily-ohlcv"
    assert by_provider["fred"]["status"] == "credential_unavailable"
    assert by_provider["fred"]["secret_ref"] == "env://FRED_API_KEY"
    assert out_dss["provider_statuses"]["stooq"] == "read_ok"
    assert out_dss["provider_statuses"]["fred"] == "credential_unavailable"


def test_source_health_truth_overlay_maps_coingecko_provider_to_crypto_connector(monkeypatch):
    dss = {"state": "datasource_smoke_ok", "provider_statuses": {"coingecko": "read_unavailable"}}
    sources = [{"provider_key": "coingecko", "status": "read_unavailable"}]
    out_dss, out_sources, _ = _overlay_source_health_truth(
        dss,
        sources,
        truths={
            "crypto-coingecko-spot": {
                "health": {
                    "source_id": "crypto-coingecko-spot",
                    "status": "ok",
                    "last_success_at": "2026-06-27T05:00:00Z",
                    "latest_watermark": "2026-06-27",
                    "row_count_last_run": 2,
                    "metadata": {"provider": "CoinGecko", "market": "CRYPTO"},
                },
                "connector": {
                    "connector_id": "crypto-coingecko-spot",
                    "status": "enabled",
                    "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
                    "freshness": {"status": "fresh", "last_success_at": "2026-06-27T05:00:00Z"},
                    "health_metrics": {},
                },
            }
        },
    )

    assert out_dss["provider_statuses"]["coingecko"] == "read_ok"
    assert out_dss["live_source_connector_ids"] == ["crypto-coingecko-spot"]
    assert out_sources[0]["connectorId"] == "crypto-coingecko-spot"
    assert out_sources[0]["sourceHealthAvailable"] is True


def test_unassigned_runtime_telemetry_isolation_and_no_seed_leaks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persona_custom = "persona-custom-empty"
    runtime_unassigned = "runtime-devloop-unassigned"
    runtime_binding_unassigned = "rb-devloop-unassigned"

    def write_store(name: str, payload: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    stores = {
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE": write_store(
            "personas.json",
            {
                persona_custom: {
                    "persona_id": persona_custom,
                    "tenant_id": "pantheon-dev",
                    "name": "Custom Empty US",
                    "lifecycle_state": "deployed",
                    "status": "deployed",
                    "created_at": "2026-07-13T00:00:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                    },
                },
            },
        ),
        "PANTHEON_BFF_PERSONA_SESSION_STORE": write_store("sessions.json", {}),
        "PANTHEON_BFF_PERSONA_BINDING_STORE": write_store("persona_capital_bindings.json", {}),
        "PANTHEON_BFF_RUNTIME_BINDING_STORE": write_store(
            "runtime_bindings.json",
            {
                runtime_binding_unassigned: {
                    "binding_id": runtime_binding_unassigned,
                    "runtime_id": runtime_unassigned,
                    "persona_id": "persona-us-equity",  # stale seed persona_id
                    "deployment_mode": "paper",
                    "status": "active",
                },
            },
        ),
        "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE": write_store("deployment_plans.json", {}),
        "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE": write_store(
            "telemetry_summaries.json",
            {
                runtime_unassigned: {
                    "runtime_id": runtime_unassigned,
                    "projection_source": "telemetry_ingest",
                    "collected_at": "2026-07-13T00:10:00Z",
                    "pnl": 0.55,
                    "drawdown": 0.05,
                    "fill_rate": 0.95,
                    "avg_slippage_bps": 2.0,
                    "sharpe_ratio": 2.0,
                    "total_trades": 6841,
                },
            },
        ),
    }
    for env_name in (
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_DATA_DIR",
        "PANTHEON_PERSONA_SERVICE_URL",
        "PANTHEON_RUNTIME_MANAGER_URL",
        "PANTHEON_TELEMETRY_API_URL",
        "PANTHEON_TELEMETRY_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    for env_name, path in stores.items():
        monkeypatch.setenv(env_name, str(path))

    store = _make_store(allow_local_snapshot_fallback=False)
    runtimes = {runtime["runtime_id"]: runtime for runtime in store.list_runtime_bindings()}
    # The unassigned devloop runtime has no canonical binding or unique declaration, so it must reconcile to None.
    assert runtimes[runtime_unassigned]["persona_id"] is None

    with _client_with_store(store) as client:
        attribution_response = client.get(
            "/bff/management/performance-attribution/by-persona?page_size=100",
            headers=HEADERS,
        )
        fleet_response = client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=HEADERS,
        )

    assert attribution_response.status_code == 200, attribution_response.text
    attribution_rows = {
        item["dimension_key"]: item
        for item in attribution_response.json()["data"]["items"]
    }
    # Unassigned telemetry remains categorized as unassigned
    assert attribution_rows["unassigned"]["metrics"]["total_trades"] == 6841
    # Custom empty persona does not get any telemetry
    assert persona_custom not in attribution_rows

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_rows = {
        item["persona_id"]: item
        for item in fleet_response.json()["data"]["items"]
    }
    # Custom empty persona has no telemetry, so performance fields must be null (not faked from same-market seed)
    custom_perf = fleet_rows[persona_custom]["performance_summary"]
    assert custom_perf["source"] == "unavailable"
    assert custom_perf["pnl"] is None
    assert custom_perf["max_drawdown"] is None
    assert custom_perf["total_trades"] is None


def test_canonical_binding_precedence_and_mixed_topology(
    tmp_path: Path,
    monkeypatch,
) -> None:
    persona_test = "persona-test-precedence"
    persona_missing = "persona-test-missing"
    rt_stale = "rt-stale"
    binding_stale = "binding-stale"
    rt_assigned = "rt-assigned"
    binding_canonical = "binding-canonical"
    rt_devloop = "rt-devloop"
    rt_missing = "rt-missing"
    binding_missing = "binding-missing"
    observed_at = utc_now()

    def write_store(name: str, payload: object) -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    stores = {
        "PANTHEON_BFF_PERSONA_REGISTRY_STORE": write_store(
            "personas.json",
            {
                persona_test: {
                    "persona_id": persona_test,
                    "tenant_id": "pantheon-dev",
                    "name": "Precedence Persona",
                    "lifecycle_state": "deployed",
                    "status": "deployed",
                    "created_at": "2026-07-13T00:00:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                    },
                },
                persona_missing: {
                    "persona_id": persona_missing,
                    "tenant_id": "pantheon-dev",
                    "name": "Missing Telemetry Persona",
                    "lifecycle_state": "deployed",
                    "status": "deployed",
                    "created_at": "2026-07-13T00:00:00Z",
                    "metadata": {
                        "market_scope": ["US"],
                        "capital_mode": "paper",
                        "deployment_stage": "paper",
                    },
                },
            },
        ),
        "PANTHEON_BFF_PERSONA_SESSION_STORE": write_store(
            "sessions.json",
            {
                "session-assigned": {
                    "session_id": "session-assigned",
                    "persona_id": persona_test,
                    "runtime_id": rt_assigned,
                    "runtime_binding_id": "rb-assigned",
                    "status": "active",
                    "active": True,
                    "last_heartbeat_at": observed_at,
                }
            },
        ),
        "PANTHEON_BFF_PERSONA_BINDING_STORE": write_store(
            "persona_capital_bindings.json",
            {
                binding_canonical: {
                    "binding_id": binding_canonical,
                    "persona_capital_binding_id": binding_canonical,
                    "persona_id": persona_test,
                    "status": "active",
                    "validity": "active",
                },
                binding_missing: {
                    "binding_id": binding_missing,
                    "persona_capital_binding_id": binding_missing,
                    "persona_id": persona_missing,
                    "status": "active",
                    "validity": "active",
                }
            }
        ),
        "PANTHEON_BFF_RUNTIME_BINDING_STORE": write_store(
            "runtime_bindings.json",
            {
                "rb-stale": {
                    "binding_id": "rb-stale",
                    "runtime_id": rt_stale,
                    "persona_capital_binding_id": binding_stale,
                    "persona_id": persona_test,
                    "deployment_mode": "paper",
                    "status": "active",
                },
                "rb-assigned": {
                    "binding_id": "rb-assigned",
                    "runtime_id": rt_assigned,
                    "persona_capital_binding_id": binding_canonical,
                    "persona_id": None,
                    "deployment_mode": "paper",
                    "status": "active",
                },
                "rb-devloop": {
                    "binding_id": "rb-devloop",
                    "runtime_id": rt_devloop,
                    "persona_capital_binding_id": "binding-devloop-unassigned",
                    "persona_id": "persona-us-equity",  # stale seed
                    "deployment_mode": "paper",
                    "status": "active",
                },
                "rb-missing": {
                    "binding_id": "rb-missing",
                    "runtime_id": rt_missing,
                    "persona_capital_binding_id": binding_missing,
                    "persona_id": None,
                    "deployment_mode": "paper",
                    "status": "active",
                }
            },
        ),
        "PANTHEON_BFF_DEPLOYMENT_PLAN_STORE": write_store("deployment_plans.json", {}),
        "PANTHEON_BFF_TELEMETRY_SUMMARY_STORE": write_store("telemetry_summaries.json", {}),
    }

    for env_name in (
        "PANTHEON_PERSONA_DATA_DIR",
        "PANTHEON_GOVERNANCE_DATA_DIR",
        "PANTHEON_RUNTIME_DATA_DIR",
    ):
        monkeypatch.delenv(env_name, raising=False)
    for env_name, path in stores.items():
        monkeypatch.setenv(env_name, str(path))

    # Set service URL env to simulate HTTP service-backed client
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry-service.pantheon")

    store = _make_store(
        allow_local_snapshot_fallback=False,
        telemetry_service_summaries=[
            {
                "runtime_id": rt_assigned,
                "collected_at": "2026-07-13T01:00:00Z",
                "pnl": 0.0,
                "drawdown": 0.0,
                "fill_rate": 0.0,
                "avg_slippage_bps": 0.0,
                "total_trades": 0,
            },
            {
                "runtime_id": rt_devloop,
                "collected_at": "2026-07-13T02:00:00Z",
                "pnl": 120.5,
                "drawdown": 0.01,
                "fill_rate": 0.99,
                "avg_slippage_bps": 0.5,
                "total_trades": 45,
            },
        ],
    )

    # 1. Verify Canonical-binding precedence without registry fallback
    runtimes = {runtime["runtime_id"]: runtime for runtime in store.list_runtime_bindings()}
    # Active runtime binding resolves to persona_test via binding-canonical
    assert runtimes[rt_assigned]["persona_capital_binding_id"] == binding_canonical
    assert runtimes[rt_assigned]["persona_id"] == persona_test

    # Stale runtime binding resolves to None because binding-stale is not active/canonical
    assert runtimes[rt_stale]["persona_id"] is None

    # Devloop runtime binding resolves to None because binding-devloop-unassigned is not active/canonical
    assert runtimes[rt_devloop]["persona_id"] is None

    with _client_with_store(store) as client:
        attribution_response = client.get(
            "/bff/management/performance-attribution/by-persona?page_size=100",
            headers=HEADERS,
        )
        fleet_response = client.get(
            "/bff/management/persona-fleet?page_size=100",
            headers=HEADERS,
        )
        league_response = client.get(
            "/bff/management/persona-league/rankings",
            headers=HEADERS,
        )

    assert attribution_response.status_code == 200, attribution_response.text
    attribution_rows = {
        item["dimension_key"]: item
        for item in attribution_response.json()["data"]["items"]
    }
    # Assigned persona has its own zero metrics record
    assert attribution_rows[persona_test]["metrics"]["total_trades"] == 0
    # Devloop telemetry stays fail-closed in unassigned
    assert attribution_rows["unassigned"]["metrics"]["total_trades"] == 45

    assert fleet_response.status_code == 200, fleet_response.text
    fleet_rows = {
        item["persona_id"]: item
        for item in fleet_response.json()["data"]["items"]
    }
    # Assigned persona must show exact telemetry fields and source 'telemetry_summaries'
    assigned_perf = fleet_rows[persona_test]["performance_summary"]
    assert assigned_perf["source"] == "telemetry_summaries"
    assert assigned_perf["pnl"] == 0.0
    assert assigned_perf["max_drawdown"] == 0.0
    assert assigned_perf["total_trades"] == 0

    # Absent persona-owned evidence on custom persona must not leak seed values
    assert fleet_rows[persona_test]["perf_delta"] is None

    # Missing telemetry persona must have "unavailable" source in fleet
    missing_perf = fleet_rows[persona_missing]["performance_summary"]
    assert missing_perf["source"] == "unavailable"

    assert league_response.status_code == 200, league_response.text
    ranking_rows = {
        item["persona_id"]: item
        for item in league_response.json()["data"]["items"][0]["items"]
    }
    assert ranking_rows[persona_missing]["eligible"] is False
    assert ranking_rows[persona_missing]["metrics"]["telemetry_coverage_count"] == 0

    assert ranking_rows[persona_test]["eligible"] is True
    assert ranking_rows[persona_test]["source_confidence"] == "formal"


@pytest.mark.parametrize("archived_count", [100, 1000])
def test_fleet_detail_projection_is_page_bounded(archived_count, monkeypatch):
    calls = {"detail": 0}
    original = personas_service._project_persona_fleet_list_row

    def counting(**kwargs):
        calls["detail"] += 1
        return original(**kwargs)

    monkeypatch.setattr(personas_service, "_project_persona_fleet_list_row", counting)
    with _client_with_store(_seed_fleet_classification_store(archived_count)) as client:
        body = _fleet(client, purpose="trading", archived="include", page_size=20)
    assert len(body["data"]["items"]) <= 20
    assert body["page_info"]["total"] >= archived_count
    assert calls["detail"] <= 20


class _OwnerCallCounter:
    """Delegating proxy that counts every owner method invoked on the store."""

    def __init__(self, inner, fail=None):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "calls", {})
        object.__setattr__(self, "_fail", fail or {})

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def wrapped(*args, **kwargs):
            self.calls[name] = self.calls.get(name, 0) + 1
            if name in self._fail:
                raise self._fail[name]
            return attr(*args, **kwargs)

        return wrapped


def _owner_calls(archived_count, fail=None, **params):
    proxy = _OwnerCallCounter(_seed_fleet_classification_store(archived_count), fail=fail)
    with _client_with_store(proxy) as client:
        try:
            response = client.get("/bff/management/persona-fleet", headers=HEADERS, params=params)
        except RuntimeError as exc:  # owner failure surfaces truthfully, never as healthy data
            return proxy.calls, exc
    return proxy.calls, response


@pytest.mark.parametrize("params", [
    {"purpose": "trading", "archived": "include", "page_size": 20},
    {"purpose": "trading", "archived": "exclude", "page_size": 20},
    {"page_size": 20},
])
def test_fleet_owner_transport_calls_are_independent_of_archive_size(params):
    small, small_resp = _owner_calls(100, **params)
    large, large_resp = _owner_calls(1000, **params)
    assert small_resp.status_code == large_resp.status_code == 200
    # Same owner methods, same call counts: no per-persona / per-archived-record N+1.
    assert small == large, (small, large)


@pytest.mark.parametrize("archived_count", [100, 1000])
def test_fleet_reads_command_log_once_regardless_of_ranked_population(archived_count, monkeypatch):
    # Measured cost pattern: quarterly-rank governance state used to re-read the
    # whole command log once per ranked persona (archived history included).
    reads = {"n": 0}

    def counting(self):
        reads["n"] += 1
        return []

    monkeypatch.setattr(_FakeCommandStore, "_get_all_commands", counting)
    with _client_with_store(_seed_fleet_classification_store(archived_count)) as client:
        body = _fleet(client, purpose="trading", data_scope="regular", archived="exclude", page_size=20)
    assert body["data"]["summary"]["population"]["archived_total"] == archived_count
    assert reads["n"] <= 1, reads


class _LedgerCounter:
    """Delegating provisioning-ledger proxy counting durable reads."""

    def __init__(self, inner):
        self._inner = inner
        self.reads = 0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in ("list_by_tenant", "list_all"):
            return attr

        def wrapped(*args, **kwargs):
            self.reads += 1
            return attr(*args, **kwargs)

        return wrapped


@pytest.mark.parametrize("archived_count", [100, 1000])
def test_fleet_reads_durable_persona_ledger_once_per_request(archived_count, monkeypatch):
    # Measured cost pattern: the deployed fleet read the whole durable persona
    # ledger twice (league rows, then directory snapshot), ~0.5s each.
    counter = _LedgerCounter(personas_service._persona_provisioning_store())
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: counter)
    with _client_with_store(_seed_fleet_classification_store(archived_count)) as client:
        body = _fleet(client, purpose="trading", data_scope="regular", archived="exclude", page_size=20)
    assert body["data"]["summary"]["population"]["archived_total"] == archived_count
    assert counter.reads == 1, counter.reads


def test_fleet_overlaps_provisioning_ledger_with_canonical_personas_read(monkeypatch):
    # Measured cost pattern: durable provisioning list_by_tenant (~0.2-0.45s)
    # started only after the canonical /api/personas read finished.
    import threading
    import time as _time

    store = _seed_fleet_classification_store(100)
    delay = 0.4
    spans = {}

    def timed(name, fn):
        def wrapped(*args, **kwargs):
            start = _time.monotonic()
            _time.sleep(delay)
            try:
                return fn(*args, **kwargs)
            finally:
                spans.setdefault(name, (start, _time.monotonic(), threading.current_thread() is threading.main_thread()))

        return wrapped

    counter = _LedgerCounter(personas_service._persona_provisioning_store())
    real_list = counter.list_by_tenant
    counter.list_by_tenant = timed("ledger", real_list)
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: counter)
    monkeypatch.setattr(store, "list_personas", timed("personas", store.list_personas))
    with _client_with_store(store) as client:
        body = _fleet(client, purpose="trading", data_scope="regular", archived="exclude", page_size=20)
    assert body["data"]["summary"]["population"]["archived_total"] == 100
    assert counter.reads == 1, counter.reads
    assert spans["ledger"][2] is False, spans
    assert spans["ledger"][0] < spans["personas"][1] and spans["personas"][0] < spans["ledger"][1], spans


def test_fleet_provisioning_prefetch_failure_stays_failure(monkeypatch):
    store = _seed_fleet_classification_store(5)
    counter = _LedgerCounter(personas_service._persona_provisioning_store())
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("ledger down")

    counter.list_by_tenant = boom
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: counter)
    with _client_with_store(store) as client:
        response = client.get("/bff/management/persona-fleet", headers=HEADERS, params={"purpose": "trading", "page_size": 20})
    assert response.status_code == 503, response.text
    assert calls["n"] == 1, calls


def test_fleet_overlaps_strategy_spec_and_source_registry_reads(monkeypatch):
    # Measured cost pattern: strategy-spec registry (~3.4s) and the first
    # source-ingest registry read (~0.9s) ran serially on the request thread.
    import threading
    import time as _time

    store = _seed_fleet_classification_store(100)
    delay = 0.4
    spans = {}

    def timed(name, fn):
        def wrapped(*args, **kwargs):
            start = _time.monotonic()
            _time.sleep(delay)
            try:
                return fn(*args, **kwargs)
            finally:
                spans[name] = (start, _time.monotonic(), threading.current_thread() is threading.main_thread())

        return wrapped

    monkeypatch.setattr(store, "count_strategy_persona_bindings", timed("specs", store.count_strategy_persona_bindings))
    monkeypatch.setattr(store, "get_source_connector_registry", timed("source", store.get_source_connector_registry), raising=False)
    with _client_with_store(store) as client:
        started = _time.monotonic()
        _fleet(client, purpose="trading", data_scope="regular", archived="exclude", page_size=20)
        elapsed = _time.monotonic() - started
    assert "specs" in spans
    # The slow registry read starts off the request thread (caller context copied).
    assert spans["specs"][2] is False, spans
    for name, (start, end, _) in spans.items():
        assert end - start >= delay, name
    if "source" in spans:
        # The two slow reads overlap instead of running back to back.
        assert max(s for s, _, _ in spans.values()) < min(e for _, e, _ in spans.values()), spans
        assert elapsed < len(spans) * delay + 0.35, (elapsed, spans)


@pytest.mark.parametrize("fail_registry", [False, True])
def test_fleet_cold_source_truth_single_flight_when_registry_slowest(monkeypatch, fail_registry):
    # Reverse latency: the source registry is slower than every other stage, so the
    # prefetch worker and the request thread both want the cold source truth.
    import threading
    import time as _time

    store = _seed_fleet_classification_store(100)
    reads = {"registry": 0, "health": 0}
    lock = threading.Lock()
    original_registry = store.get_source_connector_registry
    original_health = store.get_source_health_usage_snapshot

    def slow_registry():
        with lock:
            reads["registry"] += 1
        _time.sleep(0.6)
        if fail_registry:
            raise RuntimeError("registry unavailable")
        return original_registry()

    def counted_health():
        with lock:
            reads["health"] += 1
        return original_health()

    monkeypatch.setattr(store, "get_source_connector_registry", slow_registry)
    monkeypatch.setattr(store, "get_source_health_usage_snapshot", counted_health)
    with _client_with_store(store) as client:
        body = _fleet(client, purpose="trading", data_scope="regular", archived="exclude", page_size=20)
    assert reads == {"registry": 1, "health": 1}, reads
    assert body["data"]["summary"]["population"]["archived_total"] == 100


def test_fleet_strategy_spec_prefetch_failure_stays_failure(monkeypatch):
    store = _seed_fleet_classification_store(100)
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("strategy registry down")

    monkeypatch.setattr(store, "count_strategy_persona_bindings", boom)
    with _client_with_store(store) as client:
        try:
            response = client.get("/bff/management/persona-fleet", headers=HEADERS, params={"purpose": "trading", "page_size": 20})
        except RuntimeError as exc:
            assert "strategy registry down" in str(exc)
        else:
            assert response.status_code >= 500 or response.json().get("data", {}).get("summary", {}).get("population") is not None
    assert calls["n"] == 1, calls


def test_fleet_overlaps_incident_and_evolution_reads_and_fetches_each_once(monkeypatch):
    # Measured cost pattern: GET incidents (~6MB) then evolution proposals (~1MB)
    # ran serially after the other reads. Each is fetched once, off the request thread.
    import threading
    import time as _time

    store = _seed_fleet_classification_store(100)
    delay = 0.4
    spans = {}
    calls = {"incidents": 0, "evolution": 0}
    lock = threading.Lock()

    def timed(name, fn):
        def wrapped(*args, **kwargs):
            start = _time.monotonic()
            with lock:
                calls[name] += 1
            _time.sleep(delay)
            try:
                return fn(*args, **kwargs)
            finally:
                spans[name] = (start, _time.monotonic(), threading.current_thread() is threading.main_thread())

        return wrapped

    monkeypatch.setattr(store, "list_incidents", timed("incidents", store.list_incidents))
    monkeypatch.setattr(store, "list_evolution_decisions", timed("evolution", store.list_evolution_decisions))
    with _client_with_store(store) as client:
        body = _fleet(client, purpose="trading", data_scope="regular", archived="exclude", page_size=20)
    assert calls == {"incidents": 1, "evolution": 1}, calls
    assert spans["incidents"][2] is False and spans["evolution"][2] is False, spans
    assert spans["incidents"][0] < spans["evolution"][1] and spans["evolution"][0] < spans["incidents"][1], spans
    assert body["data"]["summary"]["population"]["archived_total"] == 100


@pytest.mark.parametrize("failing", ["list_incidents", "list_evolution_decisions"])
def test_fleet_incident_evolution_prefetch_failure_stays_failure(monkeypatch, failing):
    store = _seed_fleet_classification_store(100)
    calls = {"n": 0}

    def boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("owner read down")

    monkeypatch.setattr(store, failing, boom)
    with _client_with_store(store) as client:
        try:
            response = client.get("/bff/management/persona-fleet", headers=HEADERS, params={"purpose": "trading", "page_size": 20})
        except RuntimeError as exc:
            assert "owner read down" in str(exc)
        else:
            assert response.status_code >= 500 or response.json().get("data", {}).get("summary", {}).get("population") is not None
    assert calls["n"] == 1, calls


def test_routed_strategy_counts_require_explicit_count_contract(monkeypatch):
    store = _seed_fleet_classification_store(3)
    monkeypatch.setattr(store, "count_strategy_persona_bindings", lambda: None)
    monkeypatch.setattr(personas_service, "_get_active_read_store", lambda: store)
    with pytest.raises(TypeError):
        personas_service._routed_strategy_counts_by_persona()


def test_ranking_governance_state_is_identical_with_shared_command_log(monkeypatch):
    commands = [
        {
            "type": "QuarterlyRankingRecommendationSubmit",
            "status": "executed",
            "params": {"recommendation_id": "pm12-2026q4-p-a-1"},
        },
        {
            "type": "HumanGateDecision",
            "status": "executed",
            "target": {"type": personas_service.ObjectType.HUMAN_GATE_ITEM.value, "id": "promotion_review:pm12-2026q4-p-b-1"},
            "params": {"decision": "reject"},
        },
    ]

    class _Store:
        def _get_all_commands(self):
            return commands

    monkeypatch.setattr(personas_service, "_get_active_command_store", lambda explicit=None: _Store())
    commands += [
        # Hyphenated ids sharing a prefix must not bleed into each other.
        {
            "type": "QuarterlyRankingRecommendationSubmit",
            "status": "blocked",
            "params": {"recommendationId": "PM12-2026Q4-p-a-b-1"},
        },
        {
            "type": "HumanGateDecision",
            "status": "applied",
            "target": {"type": personas_service.ObjectType.HUMAN_GATE_ITEM.value, "id": "promotion_review:pm12-2026q4-p-a-b-9"},
            "params": {"decision": "approve"},
        },
        {"type": "Other", "status": "executed", "params": {"recommendation_id": "pm12-2026q4-p-a-1"}},
    ]
    fn = personas_service._pm12_quarterly_ranking_governance_state
    index = personas_service._pm12_quarterly_governance_command_index(commands, "2026Q4")
    for pid in ("p-a", "p-a-b", "p-b", "p-none", "P-A", "p"):
        assert fn(pid, "2026q4") == fn(pid, "2026q4", commands) == fn(pid, "2026q4", index.get(pid, ())), pid
    assert fn("p-a-b", "2026q4") == "applied receipt"
    assert fn("p-none", "2026q4", commands) == "recommendation"


@pytest.mark.parametrize("failing", ["list_telemetry_summaries", "list_runtime_bindings", "list_incidents"])
def test_fleet_owner_failure_keeps_fixed_call_budget(failing):
    outcomes = []
    for count in (100, 1000):
        calls, response = _owner_calls(
            count, fail={failing: RuntimeError(f"{failing} owner down")},
            purpose="trading", archived="include", page_size=20,
        )
        outcomes.append((calls, type(response).__name__ if isinstance(response, Exception) else response.status_code))
    # Failure must not fan out retries/lookups with archive size.
    assert outcomes[0] == outcomes[1], outcomes


def _filtered_archived(client, **params):
    return _fleet(client, **params)["data"]["summary"]["filtered_archived_total"]


def test_filtered_archived_total_scopes_and_ignores_paging(monkeypatch) -> None:
    store = _seed_fleet_classification_store(archived_count=10)
    store.create_persona(
        persona_id="p-old-test", tenant_id="pantheon-dev", name="p-old-test",
        actor_id="pantheon-dev-browser", lifecycle_state="retired",
        created_at="2026-06-03T08:27:44Z",
        metadata={"owner": "pantheon-dev-browser", "provisioning_idempotency_key": "k",
                  "capital_mode": "paper", "data_scope": "test"},
    )
    store.create_persona(
        persona_id="p-old-unknown", tenant_id="pantheon-dev", name="p-old-unknown",
        actor_id="pantheon-dev-browser", lifecycle_state="retired",
        created_at="2026-06-03T08:27:44Z", metadata={"owner": "pantheon-dev-browser"},
    )
    store.create_persona(
        persona_id="p-old-other", tenant_id="other-tenant", name="p-old-other",
        actor_id="x", lifecycle_state="retired", created_at="2026-06-03T08:27:44Z",
        metadata={"provisioning_idempotency_key": "k", "capital_mode": "paper"},
    )
    detail_ids = []
    original = personas_service._project_persona_fleet_list_row

    def spy(**kwargs):
        detail_ids.append(personas_service._persona_id(kwargs["persona"]))
        return original(**kwargs)

    monkeypatch.setattr(personas_service, "_project_persona_fleet_list_row", spy)
    with _client_with_store(store) as client:
        regular = _filtered_archived(client, data_scope="regular", archived="exclude")
        test = _filtered_archived(client, data_scope="test", archived="exclude")
        unknown = _filtered_archived(client, purpose="unknown", data_scope="unknown", archived="exclude")
        paged = _filtered_archived(client, data_scope="regular", archived="exclude", page_size=1)
        assert not any(pid.startswith("p-old") for pid in detail_ids)
        only = _filtered_archived(client, data_scope="regular", archived="only", page_size=2)
        include = _filtered_archived(client, data_scope="regular", archived="include", page_size=3)
        detail_ids.clear()
        queried = _filtered_archived(client, q="p-old-000", archived="exclude")
        miss = _filtered_archived(client, q="no-such-persona", archived="exclude")
        staged = _filtered_archived(client, state="draft", archived="exclude")
        ordinary = _fleet(client, archived="exclude", page_size=50)

    assert regular == 10 and test == 1 and unknown == 1
    assert paged == only == include == regular
    assert queried == 10 and miss == 0 and staged == 0
    # Global population is unchanged and other-tenant retired is excluded.
    assert ordinary["data"]["summary"]["population"]["archived_total"] == 12
    assert not any(pid.startswith("p-old") for pid in detail_ids)
    assert not any(i["persona_id"].startswith("p-old") for i in ordinary["data"]["items"])


def test_filtered_archived_total_excludes_other_users_private_records() -> None:
    store = _seed_fleet_classification_store(archived_count=2)
    store.create_persona(
        persona_id="p-old-private", tenant_id="pantheon-dev", name="p-old-private",
        actor_id="u-other", lifecycle_state="retired", created_at="2026-06-03T08:27:44Z",
        metadata={"provisioning_idempotency_key": "k", "capital_mode": "paper",
                  "owner_scope": "user_private", "agora_user_id": "u-other"},
    )
    with _client_with_store(store) as client:
        assert _filtered_archived(client, archived="exclude") == 2


def test_filtered_archived_total_is_null_when_owner_read_fails(monkeypatch) -> None:
    store = _seed_fleet_classification_store(archived_count=3)

    def boom(**kwargs):
        if kwargs["persona"].get("lifecycle_state") == "retired" or kwargs["persona"].get("status") == "retired":
            raise RuntimeError("cohort projection down")
        return original(**kwargs)

    original = personas_service._project_persona_fleet_cohort_row
    monkeypatch.setattr(personas_service, "_project_persona_fleet_cohort_row", boom)
    with _client_with_store(store) as client:
        body = _fleet(client, archived="exclude")
    assert body["data"]["summary"]["filtered_archived_total"] is None
    assert body["data"]["summary"]["population"]["archived_total"] == 3


def _store_with_persona_owner(records_provider):
    from services.control_plane.bff.ports.persona_capital_runtime import PersonaFleetPort

    store = _make_store(allow_local_snapshot_fallback=False)
    store.persona_capital_runtime.persona = PersonaFleetPort(records_provider=records_provider)
    return store


@pytest.mark.parametrize("archived", ["exclude", "only", "include"])
def test_filtered_archived_total_is_null_when_real_persona_port_fails(archived) -> None:
    calls = []

    def failed_owner():
        calls.append(1)
        raise RuntimeError("persona owner down")

    with _client_with_store(_store_with_persona_owner(failed_owner)) as client:
        body = _fleet(client, archived=archived)
    assert body["data"]["summary"]["filtered_archived_total"] is None
    assert len(calls) == 1


@pytest.mark.parametrize("archived", ["exclude", "only", "include"])
def test_filtered_archived_total_is_zero_for_healthy_empty_owner(archived) -> None:
    with _client_with_store(_store_with_persona_owner(lambda: [])) as client:
        body = _fleet(client, archived=archived)
    assert body["data"]["summary"]["filtered_archived_total"] == 0


def test_advisory_record_never_enters_league_rows():
    with _client_with_store(_seed_fleet_classification_store(0)) as client:
        body = client.get("/bff/management/quarterly-ranking", headers=HEADERS, params={"page_size": 200}).json()
    assert "p-servant" not in [r.get("persona_id") for r in body["data"]["items"]]


def _private_servant_store():
    store = _seed_fleet_classification_store(0)
    store.create_persona(
        persona_id="p-private-servant", tenant_id="pantheon-dev", name="Private servant",
        actor_id="u1", lifecycle_state="paper", created_at="2026-06-03T08:27:44Z",
        metadata={
            "persona_class": "agora_servant", "agora_user_id": "u1",
            "owner_scope": "user_private", "visibility_scope": "private",
            "deployment_stage": "paper", "environment_ceiling": "paper",
            "execution_authority": "none",
        },
    )
    return store


def test_advisory_servant_has_no_nested_trading_facts_and_is_owner_private():
    seen = {}
    with _client_with_store(_private_servant_store()) as client:
        for caller in ("u1", "u2"):
            body = client.get(
                "/bff/management/persona-fleet",
                headers={"Authorization": f"Bearer {caller}:operator"},
                params={"purpose": "advisory_assistant"},
            ).json()["data"]
            seen[caller] = body
    ids_u2 = [r["persona_id"] for r in seen["u2"]["items"]]
    assert "p-private-servant" not in ids_u2
    assert seen["u2"]["summary"]["population"]["authorized_total"] == (
        seen["u1"]["summary"]["population"]["authorized_total"] - 1
    )
    row = next(r for r in seen["u1"]["items"] if r["persona_id"] == "p-private-servant")
    for key in ("capital_scope", "capital_scope_id", "binding_state", "capital_binding",
                "runtime_binding", "paper_ledger_id", "stage", "capital_mode"):
        assert row.get(key) is None, key


def _verified_jwt_header(monkeypatch, *, sub: str, user_id: str) -> dict:
    import time
    from services.runtime_auth_inbound import encode_jwt_hs256

    secret = "test-only-persona-fleet-secret"
    for key, value in {
        "PANTHEON_BFF_AUTH_STUB": "", "PANTHEON_BFF_AUTH_MODE": "strict",
        "PANTHEON_BFF_JWT_SECRET": secret,
        "PANTHEON_BFF_JWT_ISSUER": "https://auth.pantheon.local",
        "PANTHEON_BFF_JWT_AUDIENCE": "pantheon-bff",
    }.items():
        monkeypatch.setenv(key, value)
    claims = {
        "sub": sub, "userId": user_id, "tenant_id": "pantheon-dev", "roles": ["operator"],
        "iss": "https://auth.pantheon.local", "aud": "pantheon-bff",
        "iat": int(time.time()), "exp": int(time.time()) + 300,
    }
    return {"Authorization": "Bearer " + encode_jwt_hs256(claims, secret=secret)}


@pytest.mark.parametrize(
    "sub,user_id,visible",
    [("u1", "u2", False), ("u2", "u1", True)],
)
def test_private_servant_visibility_uses_agora_user_scope_for_verified_jwt(
    monkeypatch, sub, user_id, visible
):
    with _client_with_store(_private_servant_store()) as client:
        other = client.get(
            "/bff/management/persona-fleet",
            headers={"Authorization": "Bearer u9:operator"},
            params={"purpose": "advisory_assistant"},
        ).json()["data"]["summary"]["population"]["authorized_total"]
    headers = _verified_jwt_header(monkeypatch, sub=sub, user_id=user_id)
    with _client_with_store(_private_servant_store()) as client:
        resp = client.get(
            "/bff/management/persona-fleet", headers=headers,
            params={"purpose": "advisory_assistant"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    ids = [r["persona_id"] for r in body["items"]]
    assert ("p-private-servant" in ids) is visible
    total = body["summary"]["population"]["authorized_total"]
    assert total == other + (1 if visible else 0)


def test_routed_strategy_counts_reject_none_through_real_read_surface(monkeypatch):
    from types import SimpleNamespace
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts

    store = ReadSurfacePorts(research_knowledge_source=SimpleNamespace(count_strategy_persona_bindings=lambda: None))
    monkeypatch.setattr(personas_service, "_get_active_read_store", lambda: store)
    with pytest.raises(TypeError):
        personas_service._routed_strategy_counts_by_persona()

    empty = ReadSurfacePorts(research_knowledge_source=SimpleNamespace(count_strategy_persona_bindings=lambda: {}))
    monkeypatch.setattr(personas_service, "_get_active_read_store", lambda: empty)
    assert personas_service._routed_strategy_counts_by_persona() == {}
