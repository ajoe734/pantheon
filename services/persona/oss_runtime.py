"""Persona-driven OSS runtime harness.

This module is intentionally about usability: given a persona-scoped request,
it calls the actual governed OSS adapter workflow and returns the result shape a
persona can cite, track, or hand off. It does not replace each adapter's unit
tests; it proves the components compose from the persona side.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from services.execution.lean_runtime.bootstrap_contract import (
    PANTHEON_LEAN_REMOTE,
    PANTHEON_LEAN_SOURCE_PATH,
    materialize_runtime_bootstrap_request,
)
from services.execution.lean_runtime.runtime_context import (
    PantheonRuntimeContext,
    RuntimeContextSource,
)
from services.learning.dspy.adapter import TrainingConfig as DSPyTrainingConfig
from services.learning.dspy.adapter import run_dspy_workflow
from services.learning.imitation.adapter import TrainingConfig as ImitationTrainingConfig
from services.learning.imitation.adapter import run_imitation_workflow
from services.learning.trl.adapter import TrainingConfig as TRLTrainingConfig
from services.learning.trl.adapter import run_trl_dpo_workflow
from services.registry.experiments.adapter import (
    InMemoryMlflowBackend,
    OfflineWandbLocalBackend,
    RegistryExperimentAdapter,
)
from services.research.finrl.engine.finrl_adapter import (
    PolicyTrainingConfig,
    run_finrl_workflow,
)
from services.research.qlib.adapter import TrainingConfig as QlibTrainingConfig
from services.research.qlib.adapter import run_qlib_workflow
from services.research.quantlib.adapter.quantlib_adapter import (
    GovernedBondSpec,
    GovernedMarketSnapshot,
    GovernedOptionSpec,
    run_quantlib_workflow,
)
from services.research.rllib.adapter import (
    RLlibTrainingConfig,
    RayTuneSearchConfig,
    run_ray_tune_workflow,
    run_rllib_workflow,
)
from services.research.statsmodels.adapter.statsmodels_adapter import (
    GovernedDataset,
    run_statsmodels_workflow,
)
from services.research.vectorbt.adapter import BacktestConfig, run_vectorbt_workflow


REPO_ROOT = Path(__file__).resolve().parents[2]

PERSONA_OSS_COMPONENTS = (
    "openclaw",
    "dspy",
    "imitation",
    "trl",
    "qlib",
    "vectorbt",
    "statsmodels",
    "quantlib",
    "finrl",
    "rllib",
    "ray_tune",
    "mlflow",
    "wandb",
    "lean_handoff",
)

PERSONA_OSS_VERSION = "1.0.0"


@dataclass(frozen=True)
class PersonaOSSRequest:
    persona_id: str
    session_id: str
    component: str
    intent: str
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: f"persona-oss-{uuid.uuid4().hex[:12]}")


@dataclass(frozen=True)
class PersonaOSSResult:
    component: str
    persona_id: str
    session_id: str
    request_id: str
    status: str
    artifact_family: str
    primary_output: dict[str, Any]
    metrics: dict[str, Any] = field(default_factory=dict)
    registry_entry: dict[str, Any] | None = None
    artifact_bundle: dict[str, Any] | None = None
    refs: dict[str, Any] = field(default_factory=dict)
    persona_followup: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "persona_id": self.persona_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "status": self.status,
            "artifact_family": self.artifact_family,
            "primary_output": copy.deepcopy(self.primary_output),
            "metrics": copy.deepcopy(self.metrics),
            "registry_entry": copy.deepcopy(self.registry_entry),
            "artifact_bundle": copy.deepcopy(self.artifact_bundle),
            "refs": copy.deepcopy(self.refs),
            "persona_followup": copy.deepcopy(self.persona_followup),
        }


def run_persona_oss_request(request: PersonaOSSRequest) -> PersonaOSSResult:
    """Dispatch one persona-scoped request to the real OSS workflow entrypoint."""

    component = request.component.strip().lower().replace("-", "_")
    dispatch: dict[str, Callable[[PersonaOSSRequest], PersonaOSSResult]] = {
        "openclaw": _run_openclaw,
        "dspy": _run_dspy,
        "imitation": _run_imitation,
        "trl": _run_trl,
        "qlib": _run_qlib,
        "vectorbt": _run_vectorbt,
        "statsmodels": _run_statsmodels,
        "quantlib": _run_quantlib,
        "finrl": _run_finrl,
        "rllib": _run_rllib,
        "ray_tune": _run_ray_tune,
        "mlflow": _run_mlflow,
        "wandb": _run_wandb,
        "lean_handoff": _run_lean_handoff,
    }
    if component not in dispatch:
        raise ValueError(f"Unsupported persona OSS component: {request.component!r}")
    return dispatch[component](request)


def run_persona_oss_matrix(
    *,
    persona_id: str = "persona-alpha",
    session_id: str = "session-persona-oss-e2e",
) -> dict[str, PersonaOSSResult]:
    """Run every persona-facing OSS component through a request/result cycle."""

    return {
        component: run_persona_oss_request(
            PersonaOSSRequest(
                persona_id=persona_id,
                session_id=session_id,
                component=component,
                intent=f"run_{component}",
            )
        )
        for component in PERSONA_OSS_COMPONENTS
    }


def _completed(
    request: PersonaOSSRequest,
    *,
    artifact_family: str,
    primary_output: Mapping[str, Any],
    metrics: Mapping[str, Any] | None = None,
    registry_entry: Mapping[str, Any] | None = None,
    artifact_bundle: Mapping[str, Any] | None = None,
    refs: Mapping[str, Any] | None = None,
    persona_followup: Mapping[str, Any] | None = None,
) -> PersonaOSSResult:
    return PersonaOSSResult(
        component=request.component,
        persona_id=request.persona_id,
        session_id=request.session_id,
        request_id=request.request_id,
        status="completed",
        artifact_family=artifact_family,
        primary_output=copy.deepcopy(dict(primary_output)),
        metrics=copy.deepcopy(dict(metrics or {})),
        registry_entry=copy.deepcopy(dict(registry_entry)) if registry_entry is not None else None,
        artifact_bundle=copy.deepcopy(dict(artifact_bundle)) if artifact_bundle is not None else None,
        refs=copy.deepcopy(dict(refs or {})),
        persona_followup=copy.deepcopy(
            dict(persona_followup or _persona_followup_for(request, artifact_family, primary_output, metrics or {}))
        ),
    )


def _persona_followup_for(
    request: PersonaOSSRequest,
    artifact_family: str,
    primary_output: Mapping[str, Any],
    metrics: Mapping[str, Any],
) -> dict[str, Any]:
    """Map an OSS response back into the persona's next OODA step."""

    component = request.component.strip().lower().replace("-", "_")
    base = {
        "persona_id": request.persona_id,
        "session_id": request.session_id,
        "trigger_component": component,
        "trigger_request_id": request.request_id,
        "trigger_artifact_family": artifact_family,
    }
    if component == "openclaw":
        return {
            **base,
            "ooda_phase": "observe",
            "next_action": "continue_runtime_session",
            "reason": "Runtime session is active and can accept persona task messages.",
            "evidence_refs": [str(primary_output.get("session_id", ""))],
        }
    if component in {"vectorbt", "qlib"}:
        return {
            **base,
            "ooda_phase": "decide",
            "next_action": "draft_strategy_proposal",
            "reason": "Backtest or alpha evidence is available for persona proposal synthesis.",
            "evidence_refs": [str(primary_output.get("run_id") or metrics.get("run_id") or request.request_id)],
        }
    if component in {"statsmodels", "quantlib"}:
        return {
            **base,
            "ooda_phase": "orient",
            "next_action": "attach_risk_or_regime_interpretation",
            "reason": "Analytical evidence changes the persona's market/risk interpretation.",
            "evidence_refs": [request.request_id],
        }
    if component in {"dspy", "imitation", "trl", "finrl", "rllib", "ray_tune"}:
        return {
            **base,
            "ooda_phase": "learn",
            "next_action": "open_learning_candidate_review",
            "reason": "Learning or policy artifact is available for persona improvement review.",
            "evidence_refs": [str(primary_output.get("bundle_id") or primary_output.get("artifact_id") or request.request_id)],
        }
    if component in {"mlflow", "wandb"}:
        return {
            **base,
            "ooda_phase": "observe",
            "next_action": "cite_experiment_ref",
            "reason": "Experiment tracking backend returned a run reference usable in persona evidence packets.",
            "evidence_refs": [str(primary_output.get("run_id", request.request_id))],
        }
    if component == "lean_handoff":
        return {
            **base,
            "ooda_phase": "act",
            "next_action": "submit_runtime_handoff_for_execution_review",
            "reason": "Persona OSS evidence has been materialized into a LEAN paper runtime bootstrap packet.",
            "evidence_refs": [str(primary_output.get("runtime_bootstrap_request", {}).get("request_id", request.request_id))],
        }
    return {
        **base,
        "ooda_phase": "observe",
        "next_action": "record_oss_response",
        "reason": "OSS response completed and is available to the persona session.",
        "evidence_refs": [request.request_id],
    }


def _fixture(path: str) -> dict[str, Any]:
    return json.loads((REPO_ROOT / path).read_text(encoding="utf-8"))


def _run_openclaw(request: PersonaOSSRequest) -> PersonaOSSResult:
    module_path = REPO_ROOT / "services/openclaw-gateway-adapter/session_lifecycle.py"
    spec = importlib.util.spec_from_file_location("_persona_openclaw_session_lifecycle", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load OpenClaw session lifecycle from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    class FakeOpenClawUpstream:
        def create_session(self, req: Any) -> dict[str, Any]:
            return {
                "session_id": f"upstream-{request.session_id}",
                "state": "active",
                "request": getattr(req, "__dict__", {}),
            }

    store_path = Path(tempfile.mkdtemp(prefix="pantheon-openclaw-persona-")) / "sessions.json"
    store = module.SessionLifecycleStore(
        storage_path=store_path,
        upstream_factory=lambda: FakeOpenClawUpstream(),
        id_factory=lambda: request.session_id,
    )
    record, replayed = store.create_session(
        agent_id=request.persona_id,
        session_type="research_task",
        operator_id=request.persona_id,
        idempotency_key=request.request_id,
        context_bundle={
            "persona_id": request.persona_id,
            "intent": request.intent,
            "requested_component": "openclaw",
        },
    )
    return _completed(
        request,
        artifact_family="openclaw_session",
        primary_output={
            "session_id": record.session_id,
            "state": record.state,
            "upstream_session_id": record.upstream_session_id,
            "replayed": replayed,
            "audit_events": len(record.audit_log),
        },
        refs={"store_path": str(store_path)},
    )


def _run_dspy(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _fixture("services/learning/dspy/examples/preference_dataset_sample.json")
    result = run_dspy_workflow(
        dataset,
        config=DSPyTrainingConfig(version=PERSONA_OSS_VERSION, requested_by=request.persona_id),
    )
    return _completed(
        request,
        artifact_family=result.artifact_bundle["artifact_family"],
        primary_output=result.artifact_bundle["prompt_bundle"],
        metrics=result.artifact_bundle["evaluation_report"],
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={"training_run_id": result.training_result.run_id},
    )


def _run_imitation(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _fixture("services/learning/imitation/examples/trajectory_dataset_sample.json")
    result = run_imitation_workflow(
        dataset,
        config=ImitationTrainingConfig(version=PERSONA_OSS_VERSION, requested_by=request.persona_id),
    )
    return _completed(
        request,
        artifact_family=result.artifact_bundle["artifact_family"],
        primary_output=result.artifact_bundle["policy"],
        metrics=result.artifact_bundle["evaluation_summary"],
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={"training_run_id": result.training_result.run_id},
    )


def _trl_events() -> list[dict[str, Any]]:
    base = {
        "strategy_family": "persona-preference-alpha",
        "operator_id": "operator-persona-e2e",
        "actor_role": "operator",
        "promotion_state": "candidate",
    }
    artifact = {
        "artifact_id": "artifact-pref-alpha-v1",
        "registry_id": "reg-pref-alpha-v1",
        "artifact_version": "1.0.0",
        "artifact_type": "strategy_spec",
        "strategy_id": "persona-preference-alpha",
    }
    return [
        {
            **base,
            "feedback_event_id": "fb-persona-trl-001",
            "action": "approve",
            "artifact": artifact,
        },
        {
            **base,
            "feedback_event_id": "fb-persona-trl-002",
            "action": "reject",
            "artifact": {**artifact, "artifact_id": "artifact-pref-alpha-risky"},
        },
        {
            **base,
            "feedback_event_id": "fb-persona-trl-003",
            "action": "edit",
            "artifact": artifact,
            "artifact_edited": {**artifact, "artifact_id": "artifact-pref-alpha-v2"},
        },
    ]


def _run_trl(request: PersonaOSSRequest) -> PersonaOSSResult:
    result = run_trl_dpo_workflow(
        _trl_events(),
        dataset_id="dataset:persona-trl-e2e",
        strategy_id="persona-preference-alpha",
        source_dataset_refs=["dataset://persona/feedback/e2e"],
        config=TRLTrainingConfig(version=PERSONA_OSS_VERSION, requested_by=request.persona_id),
    )
    return _completed(
        request,
        artifact_family=result.registry_entry["artifact_type"],
        primary_output=result.artifact_bundle["model"],
        metrics=result.artifact_bundle["evaluation_summary"],
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={
            "training_run_id": result.training_result.run_id,
            "evaluator_packet": result.evaluator_packet["registry_id"],
            "candidate_packet": result.candidate_packet["packet_id"],
        },
    )


def _run_qlib(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _fixture("services/research/qlib/examples/equity_dataset_sample.json")
    result = run_qlib_workflow(
        dataset,
        config=QlibTrainingConfig(version=PERSONA_OSS_VERSION, requested_by=request.persona_id),
    )
    return _completed(
        request,
        artifact_family=result.artifact_bundle["artifact_family"],
        primary_output={
            "model": result.artifact_bundle["model"],
            "model_artifact_ref": result.artifact_refs["model_artifact_ref"],
            "evaluation_report_ref": result.artifact_refs["evaluation_report_ref"],
        },
        metrics=result.artifact_bundle["evaluation_summary"],
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={
            "training_run_id": result.training_result.run_id,
            "candidate_packet": result.candidate_packet["packet_id"],
            "model_artifact_ref": result.artifact_refs["model_artifact_ref"]["artifact_ref"],
        },
    )


def _run_vectorbt(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _fixture("services/research/vectorbt/examples/strategy_dataset_sample.json")
    result = run_vectorbt_workflow(
        dataset,
        config=BacktestConfig(
            version=PERSONA_OSS_VERSION,
            requested_by=request.persona_id,
            strategy_params={"short_window": 5, "long_window": 20},
        ),
    )
    return _completed(
        request,
        artifact_family=result.artifact_bundle["artifact_family"],
        primary_output={
            "backend": result.backtest_result.backend,
            "run_id": result.backtest_result.run_id,
            "per_instrument_metrics": result.backtest_result.per_instrument_metrics,
            "aggregate_metrics": result.backtest_result.aggregate_metrics,
        },
        metrics=result.backtest_result.aggregate_metrics,
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={"source_dataset_refs": result.prepared_dataset.source_dataset_refs},
    )


def _run_statsmodels(request: PersonaOSSRequest) -> PersonaOSSResult:
    raw = _fixture("services/research/statsmodels/examples/regime_dataset_sample.json")
    dataset = GovernedDataset(
        price_series={k: [float(x) for x in v] for k, v in raw["price_series"].items()},
        factor_series={k: [float(x) for x in v] for k, v in raw["factor_series"].items()},
        metadata=raw.get("metadata", {}),
    )
    artifact = run_statsmodels_workflow(dataset)
    return _completed(
        request,
        artifact_family=artifact["artifact_family"],
        primary_output=artifact["results_summary"],
        metrics={
            "analysis_path": artifact["analysis_path"],
            "result_count": len(artifact["results_summary"]),
        },
        registry_entry=artifact["registry_entry"],
        artifact_bundle=artifact,
    )


def _quantlib_snapshot(raw: Mapping[str, Any]) -> GovernedMarketSnapshot:
    return GovernedMarketSnapshot(
        dataset_id=str(raw["dataset_id"]),
        source_dataset_refs=tuple(str(x) for x in raw["source_dataset_refs"]),
        valuation_date=str(raw["valuation_date"]),
        option_specs=tuple(GovernedOptionSpec(**dict(spec)) for spec in raw["option_specs"]),
        bond_specs=tuple(GovernedBondSpec(**dict(spec)) for spec in raw["bond_specs"]),
        metadata=dict(raw.get("metadata", {})),
    )


def _run_quantlib(request: PersonaOSSRequest) -> PersonaOSSResult:
    snapshot = _quantlib_snapshot(_fixture("services/research/quantlib/examples/pricing_dataset_sample.json"))
    artifact = run_quantlib_workflow(snapshot)
    return _completed(
        request,
        artifact_family=artifact["artifact_family"],
        primary_output=artifact["results_summary"],
        metrics={
            "analysis_path": artifact["analysis_path"],
            "result_count": len(artifact["results_summary"]),
        },
        registry_entry=artifact["registry_entry"],
        artifact_bundle=artifact,
    )


def _run_finrl(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _fixture("services/research/finrl/examples/policy_dataset_sample.json")
    result = run_finrl_workflow(
        dataset,
        config=PolicyTrainingConfig(version=PERSONA_OSS_VERSION, requested_by=request.persona_id),
    )
    return _completed(
        request,
        artifact_family=result.artifact_bundle["artifact_family"],
        primary_output=result.artifact_bundle["policy"],
        metrics=result.artifact_bundle["evaluation_summary"],
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={"training_run_id": result.training_result.run_id},
    )


def _run_rllib(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _fixture("services/research/rllib/examples/train_eval_input_sample.json")
    result = run_rllib_workflow(
        dataset,
        config=RLlibTrainingConfig(version=PERSONA_OSS_VERSION, requested_by=request.persona_id),
    )
    return _completed(
        request,
        artifact_family=result.artifact_bundle["artifact_family"],
        primary_output=result.artifact_bundle["policy"],
        metrics=result.artifact_bundle["evaluation_summary"],
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={"training_run_id": result.train_eval_result.run_id},
    )


def _run_ray_tune(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _fixture("services/research/rllib/examples/train_eval_input_sample.json")
    result = run_ray_tune_workflow(
        dataset,
        training_config=RLlibTrainingConfig(version=PERSONA_OSS_VERSION, requested_by=request.persona_id),
        search_config=RayTuneSearchConfig(num_trials=8, top_k=3),
    )
    return _completed(
        request,
        artifact_family=result.artifact_bundle["artifact_type"],
        primary_output={
            "summary": result.search_result.summary,
            "top_trials": [asdict(trial) for trial in result.search_result.trial_results[:3]],
        },
        metrics=result.search_result.summary,
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={"search_run_id": result.search_result.run_id},
    )


def _registry_entry_for_tracking(persona_id: str) -> dict[str, Any]:
    vectorbt_result = _run_vectorbt(
        PersonaOSSRequest(
            persona_id=persona_id,
            session_id="session-persona-tracking-source",
            component="vectorbt",
            intent="produce_tracking_source",
        )
    )
    entry = copy.deepcopy(vectorbt_result.registry_entry or {})
    entry["evaluation_summary"] = copy.deepcopy(vectorbt_result.metrics)
    return entry


def _run_mlflow(request: PersonaOSSRequest) -> PersonaOSSResult:
    entry = copy.deepcopy(request.payload.get("registry_entry") or _registry_entry_for_tracking(request.persona_id))
    backend = InMemoryMlflowBackend(tracking_uri="memory://persona-oss-e2e/mlflow")
    sync = RegistryExperimentAdapter(backend=backend).sync_registry_entry(entry)
    run_payload = backend.runs[sync.experiment_ref.run_id]
    return _completed(
        request,
        artifact_family="experiment_run",
        primary_output=sync.experiment_ref.to_metadata_ref(),
        metrics=run_payload["metrics"],
        registry_entry=entry,
        artifact_bundle={
            "record": {
                "experiment_name": sync.record.experiment_name,
                "run_name": sync.record.run_name,
                "tags": sync.record.tags,
                "params": sync.record.params,
                "metrics": sync.record.metrics,
            },
            "artifacts": sync.record.artifacts,
        },
        refs={"run_id": sync.experiment_ref.run_id},
    )


def _run_wandb(request: PersonaOSSRequest) -> PersonaOSSResult:
    entry = copy.deepcopy(request.payload.get("registry_entry") or _registry_entry_for_tracking(request.persona_id))
    store_dir = tempfile.mkdtemp(prefix="pantheon-wandb-persona-")
    backend = OfflineWandbLocalBackend(store_dir=store_dir)
    sync = RegistryExperimentAdapter(backend=backend).sync_registry_entry(entry)
    run_payload = backend.get_run(sync.experiment_ref.run_id)
    if run_payload is None:
        raise RuntimeError(f"Offline W&B run was not written: {sync.experiment_ref.run_id}")
    return _completed(
        request,
        artifact_family="experiment_run",
        primary_output=sync.experiment_ref.to_metadata_ref(),
        metrics=run_payload["metrics"],
        registry_entry=entry,
        artifact_bundle={
            "run": run_payload,
            "artifacts": sync.record.artifacts,
        },
        refs={"run_id": sync.experiment_ref.run_id, "local_store_dir": store_dir},
    )


def _run_lean_handoff(request: PersonaOSSRequest) -> PersonaOSSResult:
    vectorbt_result = _run_vectorbt(
        PersonaOSSRequest(
            persona_id=request.persona_id,
            session_id=request.session_id,
            component="vectorbt",
            intent="produce_lean_handoff_backtest",
        )
    )
    approved_entry = copy.deepcopy(vectorbt_result.registry_entry or {})
    approved_entry["artifact_state"] = "approved"
    approved_entry["deployment_stage"] = "paper"
    approved_entry["promoted_at"] = "2026-06-12T00:00:00Z"
    approved_entry["approver"] = request.persona_id
    approved_entry["evaluation_summary"] = copy.deepcopy(vectorbt_result.metrics)

    mlflow_backend = InMemoryMlflowBackend(tracking_uri="memory://persona-oss-e2e/lean-handoff")
    mlflow_sync = RegistryExperimentAdapter(backend=mlflow_backend).sync_registry_entry(approved_entry)

    plan_id = f"dp-persona-oss-{request.persona_id}-paper"
    binding_id = f"rtb-persona-oss-{request.persona_id}-paper"
    capital_pool_id = str(request.payload.get("capital_pool_id") or "pool-persona-oss-paper")
    risk_policy_ref = "risk-policy-persona-oss-paper"
    deployment_plan = {
        "plan_id": plan_id,
        "approval_decision_id": f"approval-{request.request_id}",
        "artifact_id": approved_entry["registry_id"],
        "artifact_version": approved_entry["version"],
        "artifact_state": "approved",
        "artifact_checksum": approved_entry["checksum"],
        "strategy_id": approved_entry["strategy_id"],
        "capital_pool_id": capital_pool_id,
        "target_stage": "paper",
        "runtime_role": "paper",
        "runtime_config_ref": "/workspace/lean/Launcher/config.json",
        "runtime_config_status": "approved",
        "risk_policy_ref": risk_policy_ref,
        "risk_policy_evaluation": {
            "risk_policy_id": risk_policy_ref,
            "risk_policy_version": "v1",
            "capital_pool_id": capital_pool_id,
            "target_type": "runtime_launch",
            "target_id": plan_id,
            "decision": "allowed",
            "checks": ["persona_oss_backtest_present", "experiment_ref_present"],
            "blocking_reasons": [],
            "warnings": [],
            "evaluated_at": "2026-06-12T00:00:00Z",
            "trace_id": f"trace-risk-{request.request_id}",
        },
        "metadata": {
            "persona_id": request.persona_id,
            "source_oss_components": ["vectorbt", "mlflow"],
            "mlflow_experiment_ref": mlflow_sync.experiment_ref.to_metadata_ref(),
            "vectorbt_aggregate_metrics": vectorbt_result.metrics,
        },
    }
    runtime_binding = {
        "binding_id": binding_id,
        "runtime_id": f"rt-persona-oss-{request.persona_id}-paper",
        "plan_id": plan_id,
        "artifact_id": approved_entry["registry_id"],
        "artifact_version": approved_entry["version"],
        "capital_pool_id": capital_pool_id,
        "deployment_mode": "paper",
        "persona_capital_binding_id": f"pcb-{request.persona_id}-paper",
        "metadata": {
            "engine_bridge_repo": PANTHEON_LEAN_REMOTE,
            "engine_bridge_path": PANTHEON_LEAN_SOURCE_PATH,
            "engine_bridge_commit": "persona-oss-e2e",
            "strategy_id": approved_entry["strategy_id"],
            "artifact_checksum": approved_entry["checksum"],
        },
    }
    bootstrap = materialize_runtime_bootstrap_request(
        deployment_plan=deployment_plan,
        runtime_binding=runtime_binding,
        request_id=f"rbr-{request.request_id}",
        trace_id=f"trace-{request.request_id}",
    )
    runtime_context = PantheonRuntimeContext.from_mapping(
        bootstrap.to_dict(),
        source=RuntimeContextSource.LAUNCH_MANIFEST,
        expected_stage="paper",
    )
    primary_output = {
        "deployment_plan": deployment_plan,
        "runtime_binding": runtime_binding,
        "runtime_bootstrap_request": bootstrap.to_dict(),
        "runtime_env": bootstrap.to_runtime_env(),
        "runtime_context": runtime_context.to_dict(),
        "mlflow_experiment_ref": mlflow_sync.experiment_ref.to_metadata_ref(),
        "source_vectorbt_metrics": vectorbt_result.metrics,
    }
    return _completed(
        request,
        artifact_family="lean_runtime_handoff",
        primary_output=primary_output,
        metrics=vectorbt_result.metrics,
        registry_entry=approved_entry,
        artifact_bundle=primary_output,
        refs={
            "deployment_plan_id": plan_id,
            "runtime_binding_id": binding_id,
            "mlflow_run_id": mlflow_sync.experiment_ref.run_id,
        },
    )
