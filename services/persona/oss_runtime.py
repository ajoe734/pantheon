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


def _payload_str(request: PersonaOSSRequest, key: str, default: str) -> str:
    value = request.payload.get(key, default)
    if value is None:
        return default
    return str(value)


def _payload_int(request: PersonaOSSRequest, key: str, default: int) -> int:
    value = request.payload.get(key, default)
    if value is None:
        return default
    return int(value)


def _payload_float(request: PersonaOSSRequest, key: str, default: float) -> float:
    value = request.payload.get(key, default)
    if value is None:
        return default
    return float(value)


def _payload_mapping(request: PersonaOSSRequest, key: str) -> dict[str, Any]:
    value = request.payload.get(key)
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _payload_refs(request: PersonaOSSRequest, default: list[str]) -> list[str]:
    value = request.payload.get("source_dataset_refs")
    if isinstance(value, list) and value:
        return [str(ref) for ref in value]
    if isinstance(value, tuple) and value:
        return [str(ref) for ref in value]
    single = request.payload.get("source_dataset_ref")
    if single:
        return [str(single)]
    return list(default)


def _version_for(request: PersonaOSSRequest) -> str:
    return _payload_str(request, "version", PERSONA_OSS_VERSION)


def _dataset_with_payload(raw: Mapping[str, Any], request: PersonaOSSRequest) -> dict[str, Any]:
    dataset = copy.deepcopy(dict(raw))
    for key in (
        "dataset_id",
        "strategy_id",
        "source_strategy_spec_id",
        "data_frequency",
        "decision_focus",
    ):
        if key in request.payload:
            dataset[key] = str(request.payload[key])
    if "source_dataset_refs" in request.payload or "source_dataset_ref" in request.payload:
        dataset["source_dataset_refs"] = _payload_refs(
            request,
            [str(ref) for ref in dataset.get("source_dataset_refs", [])],
        )
    metadata = _payload_mapping(request, "metadata")
    if metadata:
        dataset.setdefault("metadata", {})
        if isinstance(dataset["metadata"], Mapping):
            dataset["metadata"] = {**dict(dataset["metadata"]), **metadata}
    return dataset


def _retarget_imitation_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    strategy_id = str(dataset["strategy_id"])
    for index, session in enumerate(dataset.get("sessions", []), start=1):
        if not isinstance(session, dict):
            continue
        target = session.setdefault("target", {})
        if isinstance(target, dict):
            target["strategy_id"] = strategy_id
            target.setdefault("artifact_type", "strategy_spec")
            target["registry_id"] = f"reg-{strategy_id}-{index}"
    return dataset


def _retarget_dspy_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    strategy_id = str(dataset["strategy_id"])
    for split_name in ("training_examples", "evaluation_examples"):
        for index, example in enumerate(dataset.get(split_name, []), start=1):
            if not isinstance(example, dict):
                continue
            target = example.setdefault("target", {})
            if isinstance(target, dict):
                target["strategy_id"] = strategy_id
                target.setdefault("artifact_type", "prompt_bundle")
                target["registry_id"] = f"reg-{strategy_id}-prompt-source-{index}"
    return dataset


def _scale_named_series(raw_series: Mapping[str, Any], *, suffix: str, multiplier: float) -> dict[str, list[float]]:
    scaled: dict[str, list[float]] = {}
    for name, values in raw_series.items():
        series_name = f"{name}{suffix}" if suffix else str(name)
        scaled[series_name] = [round(float(value) * multiplier, 8) for value in values]
    return scaled


def _quantlib_raw_with_payload(raw: Mapping[str, Any], request: PersonaOSSRequest) -> dict[str, Any]:
    snapshot = _dataset_with_payload(raw, request)
    if "valuation_date" in request.payload:
        snapshot["valuation_date"] = str(request.payload["valuation_date"])

    instrument_suffix = _payload_str(request, "instrument_suffix", "")
    spot_shift = _payload_float(request, "spot_shift", 0.0)
    strike_shift = _payload_float(request, "strike_shift", 0.0)
    volatility_shift = _payload_float(request, "volatility_shift", 0.0)
    quantity_multiplier = _payload_int(request, "quantity_multiplier", 1)
    market_rate_shift = _payload_float(request, "market_rate_shift", 0.0)
    coupon_rate_shift = _payload_float(request, "coupon_rate_shift", 0.0)

    options = []
    for option in snapshot.get("option_specs", []):
        item = dict(option)
        if instrument_suffix:
            item["option_id"] = f"{item['option_id']}{instrument_suffix}"
        item["spot"] = round(float(item["spot"]) + spot_shift, 8)
        item["strike"] = round(float(item["strike"]) + strike_shift, 8)
        item["volatility"] = round(max(0.01, float(item["volatility"]) + volatility_shift), 8)
        item["quantity"] = int(item.get("quantity", 1)) * quantity_multiplier
        options.append(item)
    snapshot["option_specs"] = options

    bonds = []
    for bond in snapshot.get("bond_specs", []):
        item = dict(bond)
        if instrument_suffix:
            item["instrument_id"] = f"{item['instrument_id']}{instrument_suffix}"
        item["market_rate"] = round(max(0.0001, float(item["market_rate"]) + market_rate_shift), 8)
        item["coupon_rate"] = round(max(0.0, float(item["coupon_rate"]) + coupon_rate_shift), 8)
        bonds.append(item)
    snapshot["bond_specs"] = bonds
    return snapshot


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
                "state": _payload_str(request, "upstream_state", "active"),
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
        session_type=_payload_str(request, "session_type", "research_task"),
        operator_id=_payload_str(request, "operator_id", request.persona_id),
        idempotency_key=request.request_id,
        context_bundle={
            "persona_id": request.persona_id,
            "intent": request.intent,
            "requested_component": "openclaw",
            **_payload_mapping(request, "context_bundle"),
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
            "session_type": record.session_type,
            "operator_id": record.operator_id,
            "context_bundle": record.context_bundle,
        },
        refs={"store_path": str(store_path)},
    )


def _run_dspy(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _retarget_dspy_dataset(
        _dataset_with_payload(
            _fixture("services/learning/dspy/examples/preference_dataset_sample.json"),
            request,
        )
    )
    if "base_bundle_ref" in request.payload:
        dataset["base_bundle_ref"] = str(request.payload["base_bundle_ref"])
    result = run_dspy_workflow(
        dataset,
        config=DSPyTrainingConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            lifecycle_state=_payload_str(request, "lifecycle_state", "draft"),
            storage_backend=_payload_str(request, "storage_backend", "object_store"),
        ),
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
    dataset = _retarget_imitation_dataset(
        _dataset_with_payload(
            _fixture("services/learning/imitation/examples/trajectory_dataset_sample.json"),
            request,
        )
    )
    result = run_imitation_workflow(
        dataset,
        config=ImitationTrainingConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            epochs=_payload_int(request, "epochs", 1),
            seed=_payload_int(request, "seed", 7),
            lifecycle_state=_payload_str(request, "lifecycle_state", "draft"),
            storage_backend=_payload_str(request, "storage_backend", "object_store"),
        ),
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


def _trl_events(request: PersonaOSSRequest) -> list[dict[str, Any]]:
    strategy_id = _payload_str(request, "strategy_id", "persona-preference-alpha")
    strategy_family = _payload_str(request, "strategy_family", strategy_id)
    event_prefix = _payload_str(request, "feedback_event_prefix", "fb-persona-trl")
    base = {
        "strategy_family": strategy_family,
        "operator_id": _payload_str(request, "operator_id", "operator-persona-e2e"),
        "actor_role": "operator",
        "promotion_state": "candidate",
    }
    artifact = {
        "artifact_id": f"artifact-{strategy_id}-v1",
        "registry_id": f"reg-{strategy_id}-v1",
        "artifact_version": _version_for(request),
        "artifact_type": "strategy_spec",
        "strategy_id": strategy_id,
    }
    return [
        {
            **base,
            "feedback_event_id": f"{event_prefix}-001",
            "action": "approve",
            "artifact": artifact,
        },
        {
            **base,
            "feedback_event_id": f"{event_prefix}-002",
            "action": "reject",
            "artifact": {**artifact, "artifact_id": f"artifact-{strategy_id}-risky"},
        },
        {
            **base,
            "feedback_event_id": f"{event_prefix}-003",
            "action": "edit",
            "artifact": artifact,
            "artifact_edited": {**artifact, "artifact_id": f"artifact-{strategy_id}-v2"},
        },
    ]


def _run_trl(request: PersonaOSSRequest) -> PersonaOSSResult:
    result = run_trl_dpo_workflow(
        _trl_events(request),
        dataset_id=_payload_str(request, "dataset_id", "dataset:persona-trl-e2e"),
        strategy_id=_payload_str(request, "strategy_id", "persona-preference-alpha"),
        source_dataset_refs=_payload_refs(request, ["dataset://persona/feedback/e2e"]),
        config=TRLTrainingConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            method=_payload_str(request, "method", "dpo"),
            beta=_payload_float(request, "beta", 0.1),
            learning_rate=_payload_float(request, "learning_rate", 5e-6),
            batch_size=_payload_int(request, "batch_size", 16),
            num_epochs=_payload_int(request, "num_epochs", 3),
            seed=_payload_int(request, "seed", 42),
            storage_backend=_payload_str(request, "storage_backend", "object_store"),
        ),
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
    dataset = _dataset_with_payload(
        _fixture("services/research/qlib/examples/equity_dataset_sample.json"),
        request,
    )
    result = run_qlib_workflow(
        dataset,
        config=QlibTrainingConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            seed=_payload_int(request, "seed", 42),
            n_estimators=_payload_int(request, "n_estimators", 10),
            num_leaves=_payload_int(request, "num_leaves", 7),
            max_depth=_payload_int(request, "max_depth", 3),
            learning_rate=_payload_float(request, "learning_rate", 0.05),
            storage_backend=_payload_str(request, "storage_backend", "object_store"),
        ),
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
    fixture_path = _payload_str(
        request,
        "dataset_fixture_path",
        "services/research/vectorbt/examples/strategy_dataset_sample.json",
    )
    dataset = _dataset_with_payload(_fixture(fixture_path), request)
    dataset = _limit_dataset_instruments(
        dataset,
        instrument_count=_payload_int(request, "instrument_count", 0),
        instrument_offset=_payload_int(request, "instrument_offset", 0),
    )
    result = run_vectorbt_workflow(
        dataset,
        config=BacktestConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            strategy_params={
                "short_window": _payload_int(request, "short_window", 5),
                "long_window": _payload_int(request, "long_window", 20),
            },
            init_cash=_payload_float(request, "init_cash", 100_000.0),
            fees=_payload_float(request, "fees", 0.001),
            storage_backend=_payload_str(request, "storage_backend", "object_store"),
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


def _limit_dataset_instruments(
    dataset: dict[str, Any],
    *,
    instrument_count: int,
    instrument_offset: int,
) -> dict[str, Any]:
    if instrument_count <= 0:
        return dataset
    records = dataset.get("records")
    if not isinstance(records, list):
        return dataset
    instruments = sorted({
        str(record.get("instrument"))
        for record in records
        if isinstance(record, Mapping) and str(record.get("instrument") or "").strip()
    })
    if not instruments:
        return dataset
    start = instrument_offset % len(instruments)
    selected: list[str] = []
    index = start
    while len(selected) < min(instrument_count, len(instruments)):
        instrument = instruments[index % len(instruments)]
        if instrument not in selected:
            selected.append(instrument)
        index += 1
    selected_set = set(selected)
    dataset["records"] = [
        record
        for record in records
        if isinstance(record, Mapping) and str(record.get("instrument")) in selected_set
    ]
    dataset.setdefault("metadata", {})
    if isinstance(dataset["metadata"], Mapping):
        dataset["metadata"] = {
            **dict(dataset["metadata"]),
            "instrument_subset": selected,
            "instrument_subset_source": "persona_oss_request",
        }
    return dataset


def _run_statsmodels(request: PersonaOSSRequest) -> PersonaOSSResult:
    raw = _fixture("services/research/statsmodels/examples/regime_dataset_sample.json")
    metadata = {**dict(raw.get("metadata", {})), **_payload_mapping(request, "metadata")}
    if "dataset_id" in request.payload:
        metadata["dataset_id"] = str(request.payload["dataset_id"])
    if "source_dataset_refs" in request.payload or "source_dataset_ref" in request.payload:
        metadata["source_dataset_refs"] = _payload_refs(
            request,
            [str(ref) for ref in metadata.get("source_dataset_refs", [])],
        )
    if "data_frequency" in request.payload:
        metadata["data_frequency"] = str(request.payload["data_frequency"])
    series_suffix = _payload_str(request, "series_suffix", "")
    price_multiplier = _payload_float(request, "price_multiplier", 1.0)
    factor_multiplier = _payload_float(request, "factor_multiplier", 1.0)
    dataset = GovernedDataset(
        price_series=_scale_named_series(raw["price_series"], suffix=series_suffix, multiplier=price_multiplier),
        factor_series=_scale_named_series(raw["factor_series"], suffix=series_suffix, multiplier=factor_multiplier),
        metadata=metadata,
    )
    artifact = run_statsmodels_workflow(dataset)
    primary_output = copy.deepcopy(artifact["results_summary"])
    primary_output["dataset_metadata"] = copy.deepcopy(metadata)
    primary_output["price_series_names"] = list(dataset.price_series)
    primary_output["factor_series_names"] = list(dataset.factor_series)
    return _completed(
        request,
        artifact_family=artifact["artifact_family"],
        primary_output=primary_output,
        metrics={
            "analysis_path": artifact["analysis_path"],
            "result_count": len(artifact["results_summary"]),
            "price_series_count": len(dataset.price_series),
            "factor_series_count": len(dataset.factor_series),
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
    snapshot = _quantlib_snapshot(
        _quantlib_raw_with_payload(
            _fixture("services/research/quantlib/examples/pricing_dataset_sample.json"),
            request,
        )
    )
    artifact = run_quantlib_workflow(snapshot)
    primary_output = copy.deepcopy(artifact["results_summary"])
    primary_output["dataset_id"] = snapshot.dataset_id
    primary_output["valuation_date"] = snapshot.valuation_date
    primary_output["option_ids"] = [option.option_id for option in snapshot.option_specs]
    primary_output["bond_ids"] = [bond.instrument_id for bond in snapshot.bond_specs]
    primary_output["metadata"] = copy.deepcopy(snapshot.metadata)
    return _completed(
        request,
        artifact_family=artifact["artifact_family"],
        primary_output=primary_output,
        metrics={
            "analysis_path": artifact["analysis_path"],
            "result_count": len(artifact["results_summary"]),
            "option_count": len(snapshot.option_specs),
            "bond_count": len(snapshot.bond_specs),
        },
        registry_entry=artifact["registry_entry"],
        artifact_bundle=artifact,
    )


def _run_finrl(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _dataset_with_payload(
        _fixture("services/research/finrl/examples/policy_dataset_sample.json"),
        request,
    )
    result = run_finrl_workflow(
        dataset,
        config=PolicyTrainingConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            algorithm=_payload_str(request, "algorithm", "ppo"),
            seed=_payload_int(request, "seed", 42),
            lookback_window=_payload_int(request, "lookback_window", 3),
            learning_rate=_payload_float(request, "learning_rate", 3e-4),
            gamma=_payload_float(request, "gamma", 0.99),
            reward_scale=_payload_float(request, "reward_scale", 1.0),
            risk_aversion=_payload_float(request, "risk_aversion", 0.25),
            storage_backend=_payload_str(request, "storage_backend", "object_store"),
            governance_scope=_payload_str(request, "governance_scope", "offline_deferred_prep_only"),
        ),
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
    dataset = _dataset_with_payload(
        _fixture("services/research/rllib/examples/train_eval_input_sample.json"),
        request,
    )
    result = run_rllib_workflow(
        dataset,
        config=RLlibTrainingConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            algorithm=_payload_str(request, "algorithm", "ppo"),
            seed=_payload_int(request, "seed", 42),
            lookback_window=_payload_int(request, "lookback_window", 3),
            learning_rate=_payload_float(request, "learning_rate", 3e-4),
            gamma=_payload_float(request, "gamma", 0.99),
            gae_lambda=_payload_float(request, "gae_lambda", 0.95),
            entropy_coeff=_payload_float(request, "entropy_coeff", 0.002),
            clip_param=_payload_float(request, "clip_param", 0.2),
            num_trials=_payload_int(request, "num_trials", 8),
            search_strategy=_payload_str(request, "search_strategy", "pbt"),
            objective_metric=_payload_str(request, "objective_metric", "validation_sharpe_proxy"),
            storage_backend=_payload_str(request, "storage_backend", "object_store"),
        ),
    )
    return _completed(
        request,
        artifact_family=result.artifact_bundle["artifact_family"],
        primary_output={
            **result.artifact_bundle["policy"],
            "lookback_window": result.artifact_bundle["dataset_schema"]["observed_lookback_window"],
            "rollout_summary": result.artifact_bundle["rollout_summary"],
        },
        metrics=result.artifact_bundle["evaluation_summary"],
        registry_entry=result.registry_entry,
        artifact_bundle=result.artifact_bundle,
        refs={"training_run_id": result.train_eval_result.run_id},
    )


def _run_ray_tune(request: PersonaOSSRequest) -> PersonaOSSResult:
    dataset = _dataset_with_payload(
        _fixture("services/research/rllib/examples/train_eval_input_sample.json"),
        request,
    )
    result = run_ray_tune_workflow(
        dataset,
        training_config=RLlibTrainingConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            seed=_payload_int(request, "training_seed", _payload_int(request, "seed", 42)),
            lookback_window=_payload_int(request, "lookback_window", 3),
            learning_rate=_payload_float(request, "learning_rate", 3e-4),
            gamma=_payload_float(request, "gamma", 0.99),
            search_strategy=_payload_str(request, "search_strategy", "pbt"),
            objective_metric=_payload_str(request, "objective_metric", "validation_sharpe_proxy"),
        ),
        search_config=RayTuneSearchConfig(
            version=_version_for(request),
            requested_by=request.persona_id,
            optimizer_id=_payload_str(request, "optimizer_id", "ray_tune_rllib_search_v1"),
            search_strategy=_payload_str(request, "search_strategy", "pbt"),
            objective_metric=_payload_str(request, "objective_metric", "validation_sharpe_proxy"),
            num_trials=_payload_int(request, "num_trials", 8),
            top_k=_payload_int(request, "top_k", 3),
            seed=_payload_int(request, "seed", 42),
            trigger=_payload_str(request, "trigger", "manual"),
            cpu_per_trial=_payload_int(request, "cpu_per_trial", 2),
            max_iterations=_payload_int(request, "max_iterations", 12),
            storage_backend=_payload_str(request, "storage_backend", "object_store"),
        ),
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


def _registry_entry_for_tracking(persona_id: str, source_payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    vectorbt_result = _run_vectorbt(
        PersonaOSSRequest(
            persona_id=persona_id,
            session_id="session-persona-tracking-source",
            component="vectorbt",
            intent="produce_tracking_source",
            payload=copy.deepcopy(dict(source_payload or {})),
        )
    )
    entry = copy.deepcopy(vectorbt_result.registry_entry or {})
    entry["evaluation_summary"] = copy.deepcopy(vectorbt_result.metrics)
    return entry


def _run_mlflow(request: PersonaOSSRequest) -> PersonaOSSResult:
    source_payload = _payload_mapping(request, "source_vectorbt_payload")
    entry = copy.deepcopy(request.payload.get("registry_entry") or _registry_entry_for_tracking(request.persona_id, source_payload))
    entry.update(_payload_mapping(request, "registry_entry_overrides"))
    backend = InMemoryMlflowBackend(tracking_uri=_payload_str(request, "tracking_uri", "memory://persona-oss-e2e/mlflow"))
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
    source_payload = _payload_mapping(request, "source_vectorbt_payload")
    entry = copy.deepcopy(request.payload.get("registry_entry") or _registry_entry_for_tracking(request.persona_id, source_payload))
    entry.update(_payload_mapping(request, "registry_entry_overrides"))
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
    source_payload = _payload_mapping(request, "source_vectorbt_payload")
    vectorbt_result = _run_vectorbt(
        PersonaOSSRequest(
            persona_id=request.persona_id,
            session_id=request.session_id,
            component="vectorbt",
            intent="produce_lean_handoff_backtest",
            payload=source_payload,
        )
    )
    approved_entry = copy.deepcopy(vectorbt_result.registry_entry or {})
    approved_entry["artifact_state"] = "approved"
    approved_entry["deployment_stage"] = "paper"
    approved_entry["promoted_at"] = _payload_str(request, "promoted_at", "2026-06-12T00:00:00Z")
    approved_entry["approver"] = _payload_str(request, "approver", request.persona_id)
    approved_entry["evaluation_summary"] = copy.deepcopy(vectorbt_result.metrics)

    mlflow_backend = InMemoryMlflowBackend(
        tracking_uri=_payload_str(request, "tracking_uri", "memory://persona-oss-e2e/lean-handoff")
    )
    mlflow_sync = RegistryExperimentAdapter(backend=mlflow_backend).sync_registry_entry(approved_entry)

    plan_suffix = _payload_str(request, "plan_suffix", "paper")
    plan_id = f"dp-persona-oss-{request.persona_id}-{plan_suffix}"
    binding_id = f"rtb-persona-oss-{request.persona_id}-{plan_suffix}"
    capital_pool_id = str(request.payload.get("capital_pool_id") or "pool-persona-oss-paper")
    risk_policy_ref = _payload_str(request, "risk_policy_ref", "risk-policy-persona-oss-paper")
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
        "runtime_role": _payload_str(request, "runtime_role", "paper"),
        "runtime_config_ref": _payload_str(request, "runtime_config_ref", "/workspace/lean/Launcher/config.json"),
        "runtime_config_status": "approved",
        "risk_policy_ref": risk_policy_ref,
        "risk_policy_evaluation": {
            "risk_policy_id": risk_policy_ref,
            "risk_policy_version": _payload_str(request, "risk_policy_version", "v1"),
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
            **_payload_mapping(request, "metadata"),
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
            "engine_bridge_commit": _payload_str(request, "engine_bridge_commit", "persona-oss-e2e"),
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
