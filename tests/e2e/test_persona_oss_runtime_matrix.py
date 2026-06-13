"""Persona request -> OSS workflow -> usable result E2E tests."""

from __future__ import annotations

from types import SimpleNamespace

import services.persona.oss_runtime as oss_runtime
from services.persona.oss_runtime import (
    PERSONA_OSS_COMPONENTS,
    PersonaOSSRequest,
    run_persona_oss_matrix,
    run_persona_oss_request,
)


EXPECTED_PERSONA_FOLLOWUPS = {
    "openclaw": ("observe", "continue_runtime_session"),
    "dspy": ("learn", "open_learning_candidate_review"),
    "imitation": ("learn", "open_learning_candidate_review"),
    "trl": ("learn", "open_learning_candidate_review"),
    "qlib": ("decide", "draft_strategy_proposal"),
    "vectorbt": ("decide", "draft_strategy_proposal"),
    "statsmodels": ("orient", "attach_risk_or_regime_interpretation"),
    "quantlib": ("orient", "attach_risk_or_regime_interpretation"),
    "finrl": ("learn", "open_learning_candidate_review"),
    "rllib": ("learn", "open_learning_candidate_review"),
    "ray_tune": ("learn", "open_learning_candidate_review"),
    "mlflow": ("observe", "cite_experiment_ref"),
    "wandb": ("observe", "cite_experiment_ref"),
    "lean_handoff": ("act", "submit_runtime_handoff_for_execution_review"),
}


def test_persona_can_run_every_oss_component_and_get_runtime_results() -> None:
    results = run_persona_oss_matrix(
        persona_id="persona-alpha",
        session_id="session-persona-oss-runtime-e2e",
    )

    assert set(results) == set(PERSONA_OSS_COMPONENTS)
    for component, result in results.items():
        assert result.status == "completed", component
        assert result.persona_id == "persona-alpha"
        assert result.session_id == "session-persona-oss-runtime-e2e"
        assert result.artifact_family
        assert result.primary_output, component
        assert result.persona_followup["trigger_component"] == component
        expected_phase, expected_action = EXPECTED_PERSONA_FOLLOWUPS[component]
        assert result.persona_followup["ooda_phase"] == expected_phase
        assert result.persona_followup["next_action"] == expected_action
        assert result.persona_followup["evidence_refs"]


def test_vectorbt_request_runs_real_historical_fixture_backtest() -> None:
    result = run_persona_oss_request(
        PersonaOSSRequest(
            persona_id="persona-alpha",
            session_id="session-vectorbt",
            component="vectorbt",
            intent="backtest_ma_crossover",
        )
    )

    assert result.artifact_family == "vectorbt_backtest"
    assert result.metrics["num_instruments"] == 2
    assert result.metrics["total_trades"] > 0
    assert result.primary_output["per_instrument_metrics"]["ALPHA"]["num_bars"] == 35
    assert result.primary_output["per_instrument_metrics"]["BETA"]["num_bars"] == 35
    assert result.registry_entry is not None
    assert result.registry_entry["artifact_type"] == "backtest_result"
    assert result.persona_followup["ooda_phase"] == "decide"
    assert result.persona_followup["next_action"] == "draft_strategy_proposal"


def test_vectorbt_request_honors_real_backend_env(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeVectorbtBackend:
        pass

    def fake_run_vectorbt_workflow(dataset, *, backend=None, config=None):
        captured["backend"] = backend
        captured["config"] = config
        return SimpleNamespace(
            prepared_dataset=SimpleNamespace(source_dataset_refs=("dataset:fixture",)),
            backtest_result=SimpleNamespace(
                backend="vectorbt_portfolio",
                run_id="vbt-real-test",
                per_instrument_metrics={},
                aggregate_metrics={"num_instruments": 2, "total_trades": 1},
            ),
            artifact_bundle={"artifact_family": "vectorbt_backtest"},
            registry_entry={
                "artifact_type": "backtest_result",
                "strategy_id": "strategy-real-backend-test",
                "version": "1.0.0",
            },
        )

    monkeypatch.setenv("PANTHEON_VECTORBT_BACKEND", "real")
    monkeypatch.setattr(oss_runtime, "VectorbtBackend", FakeVectorbtBackend)
    monkeypatch.setattr(oss_runtime, "run_vectorbt_workflow", fake_run_vectorbt_workflow)

    result = run_persona_oss_request(
        PersonaOSSRequest(
            persona_id="persona-alpha",
            session_id="session-vectorbt-real-env",
            component="vectorbt",
            intent="verify_real_backend_env_selection",
        )
    )

    assert result.primary_output["backend"] == "vectorbt_portfolio"
    assert isinstance(captured["backend"], FakeVectorbtBackend)
    assert captured["config"].requested_by == "persona-alpha"


def test_mlflow_request_records_vectorbt_run_metrics_and_artifacts() -> None:
    result = run_persona_oss_request(
        PersonaOSSRequest(
            persona_id="persona-alpha",
            session_id="session-mlflow",
            component="mlflow",
            intent="track_backtest",
        )
    )

    assert result.artifact_family == "experiment_run"
    assert result.refs["run_id"].startswith("mem-")
    assert result.metrics["mean_total_return"] != 0.0
    assert "registry_entry.json" in result.artifact_bundle["artifacts"]
    assert "artifact_handoff.json" in result.artifact_bundle["artifacts"]
    assert result.primary_output["backend"] == "mlflow"
    assert result.persona_followup["next_action"] == "cite_experiment_ref"


def test_wandb_request_records_offline_run_and_artifact_refs() -> None:
    result = run_persona_oss_request(
        PersonaOSSRequest(
            persona_id="persona-alpha",
            session_id="session-wandb",
            component="wandb",
            intent="track_backtest_offline",
        )
    )

    assert result.artifact_family == "experiment_run"
    assert result.primary_output["backend"] == "wandb"
    assert result.primary_output["sync_status"] == "offline_local"
    assert result.metrics["mean_total_return"] != 0.0
    assert result.refs["local_store_dir"]
    assert result.persona_followup["next_action"] == "cite_experiment_ref"


def test_learning_oss_requests_produce_model_or_prompt_artifacts() -> None:
    components = {
        "dspy": "prompt_bundle",
        "imitation": "imitation_policy",
        "trl": "model_artifact",
    }

    for component, family in components.items():
        result = run_persona_oss_request(
            PersonaOSSRequest(
                persona_id="persona-alpha",
                session_id=f"session-{component}",
                component=component,
                intent=f"train_{component}",
            )
        )
        assert result.artifact_family == family
        assert result.metrics
        assert result.registry_entry is not None
        assert result.registry_entry["producer_run_id"]
        assert result.persona_followup["ooda_phase"] == "learn"
        assert result.persona_followup["next_action"] == "open_learning_candidate_review"


def test_research_oss_requests_produce_analysis_or_policy_results() -> None:
    for component in ("qlib", "statsmodels", "quantlib", "finrl", "rllib", "ray_tune"):
        result = run_persona_oss_request(
            PersonaOSSRequest(
                persona_id="persona-alpha",
                session_id=f"session-{component}",
                component=component,
                intent=f"run_{component}",
            )
        )
        assert result.status == "completed"
        assert result.metrics
        assert result.registry_entry is not None
        assert result.primary_output
        assert result.persona_followup["next_action"]


def test_vectorbt_mlflow_result_materializes_lean_runtime_handoff() -> None:
    result = run_persona_oss_request(
        PersonaOSSRequest(
            persona_id="persona-alpha",
            session_id="session-lean-handoff",
            component="lean_handoff",
            intent="materialize_paper_runtime_bootstrap",
        )
    )

    handoff = result.primary_output
    bootstrap = handoff["runtime_bootstrap_request"]
    runtime_context = handoff["runtime_context"]

    assert result.artifact_family == "lean_runtime_handoff"
    assert result.registry_entry["artifact_state"] == "approved"
    assert result.registry_entry["deployment_stage"] == "paper"
    assert bootstrap["deployment_stage"] == "paper"
    assert bootstrap["artifact"]["artifact_id"] == result.registry_entry["registry_id"]
    assert bootstrap["artifact"]["checksum"] == result.registry_entry["checksum"]
    assert handoff["runtime_env"]["PANTHEON_RUNTIME_MODE"] == "paper"
    assert runtime_context["deployment_stage"] == "paper"
    assert runtime_context["artifact"]["artifact_id"] == result.registry_entry["registry_id"]
    assert handoff["mlflow_experiment_ref"]["backend"] == "mlflow"
    assert handoff["source_vectorbt_metrics"]["total_trades"] > 0
    assert result.persona_followup["ooda_phase"] == "act"
    assert result.persona_followup["next_action"] == "submit_runtime_handoff_for_execution_review"
