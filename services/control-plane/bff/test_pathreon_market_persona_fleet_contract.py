from __future__ import annotations

import os
import sys
import tempfile
import json
from copy import deepcopy
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(__file__))

from services.control_plane.bff import main as bff_main
from ports import ReadSurfacePorts, create_in_memory_read_surface_ports


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
    for method_name, key in (
        ("list_agora_signals", "agora_signals"),
        ("list_agora_sessions", "agora_sessions"),
        ("list_agora_watchlist", "agora_watchlist"),
    ):
        setattr(store, method_name, lambda _key=key, **_kwargs: records(data.get(_key)))
    return store


@contextmanager
def _client_with_store(store: ReadSurfacePorts) -> Iterator[TestClient]:
    original_store = bff_main.read_store
    original_env = os.environ.get("PANTHEON_OODA_PACKET_ENABLED")
    os.environ.pop("PANTHEON_OODA_PACKET_ENABLED", None)
    bff_main.read_store = store
    try:
        yield TestClient(bff_main.app, raise_server_exceptions=False)
    finally:
        bff_main.read_store = original_store
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
    observed_at = bff_main.utc_now()

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

    monkeypatch.setattr(bff_main.read_store, "get_telemetry_summary", telemetry_summary)
    row = {
        "binding_summary": {"runtime_ids": ["runtime-authoritative"]},
        "session_summary": {
            "runtime_ids": [],
            "runtime_binding_ids": ["rb-stale-session-alias"],
        },
    }

    metrics = bff_main._pm12_persona_telemetry_metrics(row)

    assert calls == ["runtime-authoritative"]
    assert metrics["runtime_ids"] == ["runtime-authoritative"]
    assert metrics["pnl"] == 0.0
    assert metrics["drawdown"] == 0.0
    assert metrics["total_trades"] == 0


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
    monkeypatch.setattr(
        bff_main,
        "_live_source_health_by_connector",
        lambda: {"tw-finmind-datasets": {"status": "ok", "last_success_at": "2026-06-27T05:00:00Z", "row_count_last_run": 8}},
    )
    out_dss, out_sources = bff_main._overlay_live_finmind_health(dss, sources)
    assert out_dss["provider_statuses"]["finmind"] == "read_ok"
    assert out_dss["state"] == "live_partial_readback"
    assert out_dss["finmind_live_row_count_last_run"] == 8
    by_key = {s["provider_key"]: s for s in out_sources}
    assert by_key["finmind"]["status"] == "read_ok"


def test_overlay_live_finmind_health_noop_when_unavailable(monkeypatch):
    dss = {"state": "partial_readback", "provider_statuses": {"finmind": "read_unavailable"}}
    monkeypatch.setattr(bff_main, "_live_source_health_by_connector", lambda: {})
    out_dss, _ = bff_main._overlay_live_finmind_health(
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
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
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

    out_dss, out_sources, bindings = bff_main._overlay_source_health_truth(
        dss,
        sources,
        required_data_sources=required_sources,
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
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
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

    out_dss, out_sources, _bindings = bff_main._overlay_source_health_truth(dss, sources)

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
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
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

    out_dss, out_sources, _bindings = bff_main._overlay_source_health_truth(dss, sources)

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
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
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

    out_dss, out_sources, _bindings = bff_main._overlay_source_health_truth(dss, sources)

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
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
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

    out_dss, out_sources, _ = bff_main._overlay_source_health_truth(dss, sources)

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
    observed_at = bff_main.utc_now()

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
