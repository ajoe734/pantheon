"""100 persona -> OSS -> persona round-trip specs backed by alpha seed sources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from services.persona.oss_runtime import PersonaOSSRequest, PersonaOSSResult, run_persona_oss_request


ROOT = Path(__file__).resolve().parents[2]
HISTORICAL_OHLCV_FIXTURE = "services/research/qlib/examples/smoke_dataset.json"
HISTORICAL_OHLCV_DATASET_ID = "dataset:tw-equity-ohlcv-top50-2024-daily"

FOLLOWUPS = {
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

ALPHA_SEED_SOURCES = (
    {
        "key": "qlib_tw_cross_sectional",
        "strategy_id": "tw-cross-sectional-equity-alpha",
        "source_strategy_spec_id": "qlib-tw-cross-sectional-alpha-spec-v1",
        "source_dataset_refs": ["dataset:tw-equity-ohlcv-top50-2024-daily"],
        "evidence_path": "services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md",
        "anchors": ["Strategy ID**: `tw-cross-sectional-equity-alpha`", "5 trading days"],
    },
    {
        "key": "per002_alpha_momentum",
        "strategy_id": "per-002-alpha-tw-equity-momentum",
        "source_strategy_spec_id": "eb-per-002-alpha-001",
        "source_dataset_refs": ["src-per-002-alpha-001", "ei-per-002-alpha-001"],
        "evidence_path": "tests/e2e/test_persona_abc_ooda_evidence_chain.py",
        "anchors": ["src-per-002-alpha-001", "TW equity momentum mandate"],
    },
    {
        "key": "per002_beta_reversal",
        "strategy_id": "per-002-beta-tw-equity-reversal",
        "source_strategy_spec_id": "eb-per-002-beta-001",
        "source_dataset_refs": ["src-per-002-beta-001", "ei-per-002-beta-001"],
        "evidence_path": "tests/e2e/test_persona_abc_ooda_evidence_chain.py",
        "anchors": ["src-per-002-beta-001", "short-term reversal patterns"],
    },
    {
        "key": "per002_gamma_value_quality",
        "strategy_id": "per-002-gamma-global-equity-value-quality",
        "source_strategy_spec_id": "eb-per-002-gamma-001",
        "source_dataset_refs": ["src-per-002-gamma-001", "ei-per-002-gamma-001"],
        "evidence_path": "tests/e2e/test_persona_abc_ooda_evidence_chain.py",
        "anchors": ["src-per-002-gamma-001", "low price-to-book ratio"],
    },
    {
        "key": "source_ingestion_lightgbm_alpha",
        "strategy_id": "paper-lightgbm-tw-alpha",
        "source_strategy_spec_id": "strat-tw-lightgbm-alpha-v1",
        "source_dataset_refs": ["src-paper-alpha-001", "evbundle-alpha-001", "evi-alpha-001"],
        "evidence_path": "services/source_ingestion/tests/test_strategy_seed_builder.py",
        "anchors": ["src-paper-alpha-001", "LightGBM TW equity factors"],
    },
)

COMPONENT_CASE_COUNTS = (
    ("openclaw", 8),
    ("dspy", 7),
    ("imitation", 7),
    ("trl", 7),
    ("qlib", 8),
    ("vectorbt", 10),
    ("statsmodels", 7),
    ("quantlib", 7),
    ("finrl", 7),
    ("rllib", 7),
    ("ray_tune", 6),
    ("mlflow", 7),
    ("wandb", 6),
    ("lean_handoff", 6),
)


@dataclass(frozen=True)
class PersonaRoundTripSpec:
    spec_id: str
    component: str
    intent: str
    seed: dict[str, Any]
    payload: dict[str, Any]
    assertion_label: str


def _version(case_no: int) -> str:
    return f"1.0.{case_no}"


def _source_dataset_refs(seed: dict[str, Any]) -> list[str]:
    refs = [HISTORICAL_OHLCV_DATASET_ID]
    refs.extend(str(ref) for ref in seed["source_dataset_refs"])
    return list(dict.fromkeys(ref for ref in refs if ref))


def _base_seed_payload(seed: dict[str, Any], case_no: int) -> dict[str, Any]:
    strategy_id = f"{seed['strategy_id']}-oss-{case_no:03d}"
    return {
        "dataset_fixture_path": HISTORICAL_OHLCV_FIXTURE,
        "dataset_id": HISTORICAL_OHLCV_DATASET_ID,
        "strategy_id": strategy_id,
        "source_strategy_spec_id": seed["source_strategy_spec_id"],
        "source_dataset_refs": _source_dataset_refs(seed),
        "version": _version(case_no),
        "instrument_count": 2,
        "instrument_offset": case_no % 48,
        "metadata": {
            "alpha_seed_key": seed["key"],
            "alpha_seed_base_strategy_id": seed["strategy_id"],
            "alpha_seed_source_ref": seed["evidence_path"],
            "source_strategy_spec_id": seed["source_strategy_spec_id"],
            "historical_ohlcv_fixture": HISTORICAL_OHLCV_FIXTURE,
            "historical_ohlcv_dataset_id": HISTORICAL_OHLCV_DATASET_ID,
        },
    }


def _payload_for(component: str, seed: dict[str, Any], case_no: int, component_index: int) -> dict[str, Any]:
    base = _base_seed_payload(seed, case_no)
    if component == "openclaw":
        session_types = (
            "research_task",
            "consult_session",
            "committee_review",
            "seed_triage",
            "evidence_capture",
            "strategy_review",
            "learning_review",
            "handoff_review",
        )
        return {
            "session_type": session_types[component_index % len(session_types)],
            "operator_id": f"operator-{seed['key']}-{case_no:03d}",
            "upstream_state": "active",
            "context_bundle": {
                "alpha_seed_key": seed["key"],
                "alpha_seed_source_ref": seed["evidence_path"],
                "base_strategy_id": seed["strategy_id"],
                "source_strategy_spec_id": seed["source_strategy_spec_id"],
                "source_dataset_refs": list(seed["source_dataset_refs"]),
                "case_no": case_no,
            },
        }
    if component == "dspy":
        base.update(
            {
                "base_bundle_ref": seed["source_strategy_spec_id"],
                "lifecycle_state": "draft",
            }
        )
        return base
    if component == "imitation":
        base.update(
            {
                "epochs": 1 + (component_index % 3),
                "seed": 17 + case_no,
                "lifecycle_state": "draft",
            }
        )
        return base
    if component == "trl":
        base.update(
            {
                "strategy_family": seed["key"],
                "operator_id": f"operator-{seed['key']}-{case_no:03d}",
                "feedback_event_prefix": f"fb-{seed['key']}-{case_no:03d}",
                "beta": round(0.05 + 0.01 * (component_index % 5), 4),
                "learning_rate": 0.000005 + (component_index * 0.000001),
                "batch_size": 8 + component_index,
                "num_epochs": 2 + (component_index % 3),
                "seed": 100 + case_no,
            }
        )
        return base
    if component == "qlib":
        base.update(
            {
                "seed": 200 + case_no,
                "n_estimators": 8 + component_index,
                "num_leaves": 7 + (component_index % 4) * 2,
                "max_depth": 3 + (component_index % 3),
                "learning_rate": round(0.035 + 0.005 * (component_index % 5), 4),
            }
        )
        return base
    if component == "vectorbt":
        short_window = 3 + (component_index % 5)
        base.update(
            {
                "short_window": short_window,
                "long_window": short_window + 8 + (component_index % 4),
                "init_cash": 75_000 + case_no * 100,
                "fees": round(0.0005 + 0.0001 * (component_index % 5), 5),
            }
        )
        return base
    if component == "statsmodels":
        return {
            "dataset_id": base["dataset_id"],
            "source_dataset_refs": base["source_dataset_refs"],
            "source_strategy_spec_id": base["source_strategy_spec_id"],
            "metadata": base["metadata"],
            "series_suffix": f"_S{case_no:03d}",
            "price_multiplier": round(1.0 + case_no / 1000.0, 4),
            "factor_multiplier": round(1.0 + component_index / 100.0, 4),
            "data_frequency": "daily",
        }
    if component == "quantlib":
        return {
            "dataset_id": base["dataset_id"],
            "source_dataset_refs": base["source_dataset_refs"],
            "metadata": base["metadata"],
            "instrument_suffix": f"-S{case_no:03d}",
            "valuation_date": f"2026-05-{10 + (component_index % 10):02d}",
            "spot_shift": round(0.5 + component_index * 0.25, 4),
            "strike_shift": round(component_index * 0.1, 4),
            "volatility_shift": round(0.005 * (component_index % 4), 4),
            "quantity_multiplier": 1 + (component_index % 3),
            "market_rate_shift": round(0.0005 * component_index, 5),
        }
    if component == "finrl":
        base.update(
            {
                "seed": 300 + case_no,
                "lookback_window": 3 + (component_index % 2),
                "learning_rate": 0.0002 + component_index * 0.00002,
                "gamma": round(0.97 + component_index * 0.002, 4),
                "reward_scale": round(0.9 + component_index * 0.05, 4),
                "risk_aversion": round(0.15 + component_index * 0.02, 4),
            }
        )
        return base
    if component == "rllib":
        base.update(
            {
                "seed": 400 + case_no,
                "lookback_window": 3 + (component_index % 2),
                "learning_rate": 0.00025 + component_index * 0.00002,
                "gamma": round(0.975 + component_index * 0.002, 4),
                "gae_lambda": round(0.92 + component_index * 0.005, 4),
                "entropy_coeff": round(0.001 + component_index * 0.0005, 5),
                "clip_param": round(0.15 + component_index * 0.01, 4),
                "num_trials": 6 + component_index,
                "search_strategy": ("pbt", "grid", "bayesian")[component_index % 3],
            }
        )
        return base
    if component == "ray_tune":
        strategies = ("pbt", "grid", "bayesian")
        triggers = ("manual", "scheduled", "drift_detected", "evaluation_recommendation")
        base.update(
            {
                "optimizer_id": f"ray_tune_{seed['key']}_{case_no:03d}",
                "search_strategy": strategies[component_index % len(strategies)],
                "num_trials": 6 + component_index,
                "top_k": 2 + (component_index % 3),
                "seed": 500 + case_no,
                "training_seed": 600 + case_no,
                "trigger": triggers[component_index % len(triggers)],
                "max_iterations": 8 + component_index,
            }
        )
        return base
    if component in {"mlflow", "wandb"}:
        source_payload = _base_seed_payload(seed, case_no)
        short_window = 4 + (component_index % 4)
        source_payload.update(
            {
                "short_window": short_window,
                "long_window": short_window + 9,
                "init_cash": 90_000 + case_no * 50,
                "fees": round(0.0007 + 0.0001 * component_index, 5),
            }
        )
        payload = {"source_vectorbt_payload": source_payload}
        if component == "mlflow":
            payload["tracking_uri"] = f"memory://persona-oss-100/mlflow/{case_no:03d}"
        return payload
    if component == "lean_handoff":
        source_payload = _base_seed_payload(seed, case_no)
        short_window = 5 + (component_index % 3)
        source_payload.update(
            {
                "short_window": short_window,
                "long_window": short_window + 10,
                "init_cash": 100_000 + case_no * 75,
                "fees": round(0.0008 + 0.0001 * component_index, 5),
            }
        )
        return {
            "source_vectorbt_payload": source_payload,
            "plan_suffix": f"{seed['key']}-{case_no:03d}",
            "capital_pool_id": f"pool-{seed['key']}-{case_no:03d}",
            "risk_policy_ref": f"risk-policy-{seed['key']}-{case_no:03d}",
            "runtime_config_ref": f"runtime-config://paper/{seed['key']}/{case_no:03d}",
            "engine_bridge_commit": f"persona-oss-100-{case_no:03d}",
            "tracking_uri": f"memory://persona-oss-100/lean/{case_no:03d}",
            "metadata": base["metadata"],
        }
    raise AssertionError(f"Unhandled component {component}")


def _build_specs() -> tuple[PersonaRoundTripSpec, ...]:
    specs: list[PersonaRoundTripSpec] = []
    for component, count in COMPONENT_CASE_COUNTS:
        for component_index in range(count):
            case_no = len(specs) + 1
            seed = ALPHA_SEED_SOURCES[(case_no - 1) % len(ALPHA_SEED_SOURCES)]
            payload = _payload_for(component, seed, case_no, component_index)
            specs.append(
                PersonaRoundTripSpec(
                    spec_id=f"persona-oss-rt-{case_no:03d}-{component}",
                    component=component,
                    intent=f"roundtrip_{component}_{seed['key']}_{case_no:03d}",
                    seed=seed,
                    payload=payload,
                    assertion_label=f"{component}:{seed['key']}:{case_no:03d}",
                )
            )
    return tuple(specs)


ROUND_TRIP_SPECS = _build_specs()


def _fingerprint(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def test_100_roundtrip_specs_are_distinct_and_repo_seed_backed() -> None:
    assert len(ROUND_TRIP_SPECS) == 100
    assert {component for component, _ in COMPONENT_CASE_COUNTS} == {
        spec.component for spec in ROUND_TRIP_SPECS
    }
    assert len({spec.spec_id for spec in ROUND_TRIP_SPECS}) == 100
    assert len({spec.intent for spec in ROUND_TRIP_SPECS}) == 100
    assert len({spec.assertion_label for spec in ROUND_TRIP_SPECS}) == 100
    assert len({_fingerprint(spec.payload) for spec in ROUND_TRIP_SPECS}) == 100

    for seed in ALPHA_SEED_SOURCES:
        evidence = ROOT / seed["evidence_path"]
        assert evidence.exists(), seed["evidence_path"]
        text = evidence.read_text(encoding="utf-8")
        for anchor in seed["anchors"]:
            assert anchor in text


@pytest.mark.parametrize("spec", ROUND_TRIP_SPECS, ids=[spec.spec_id for spec in ROUND_TRIP_SPECS])
def test_persona_to_oss_to_persona_roundtrip_spec(spec: PersonaRoundTripSpec) -> None:
    request = PersonaOSSRequest(
        persona_id="persona-alpha",
        session_id=f"session-{spec.spec_id}",
        component=spec.component,
        intent=spec.intent,
        payload=spec.payload,
        request_id=f"req-{spec.spec_id}",
    )

    result = run_persona_oss_request(request)

    _assert_common_roundtrip(spec, request, result)
    _assert_component_result(spec, result)


def _assert_common_roundtrip(
    spec: PersonaRoundTripSpec,
    request: PersonaOSSRequest,
    result: PersonaOSSResult,
) -> None:
    expected_phase, expected_action = FOLLOWUPS[spec.component]
    assert result.status == "completed"
    assert result.component == spec.component
    assert result.persona_id == request.persona_id
    assert result.session_id == request.session_id
    assert result.request_id == request.request_id
    assert result.primary_output
    assert result.persona_followup["persona_id"] == request.persona_id
    assert result.persona_followup["session_id"] == request.session_id
    assert result.persona_followup["trigger_component"] == spec.component
    assert result.persona_followup["trigger_request_id"] == request.request_id
    assert result.persona_followup["ooda_phase"] == expected_phase
    assert result.persona_followup["next_action"] == expected_action
    assert result.persona_followup["evidence_refs"]


def _registry_lineage(result: PersonaOSSResult) -> dict[str, Any]:
    assert result.registry_entry is not None
    lineage = result.registry_entry.get("lineage")
    assert isinstance(lineage, dict)
    return lineage


def _source_payload(spec: PersonaRoundTripSpec) -> dict[str, Any]:
    return spec.payload.get("source_vectorbt_payload", spec.payload)


def _assert_registry_seed_lineage(spec: PersonaRoundTripSpec, result: PersonaOSSResult) -> None:
    payload = _source_payload(spec)
    lineage = _registry_lineage(result)
    assert result.registry_entry["strategy_id"] == payload["strategy_id"]
    assert result.registry_entry["version"] == payload["version"]
    assert set(payload["source_dataset_refs"]).issubset(set(lineage["source_dataset_refs"]))
    if "source_strategy_spec_id" in lineage:
        assert lineage["source_strategy_spec_id"] == payload["source_strategy_spec_id"]
    assert payload["strategy_id"].startswith(spec.seed["strategy_id"])


def _assert_component_result(spec: PersonaRoundTripSpec, result: PersonaOSSResult) -> None:
    component = spec.component
    payload = spec.payload
    if component == "openclaw":
        context = result.primary_output["context_bundle"]
        assert result.primary_output["session_type"] == payload["session_type"]
        assert result.primary_output["operator_id"] == payload["operator_id"]
        assert context["alpha_seed_key"] == spec.seed["key"]
        assert context["source_strategy_spec_id"] == spec.seed["source_strategy_spec_id"]
        assert context["source_dataset_refs"] == spec.seed["source_dataset_refs"]
        return

    if component == "dspy":
        _assert_registry_seed_lineage(spec, result)
        assert result.primary_output["strategy_id"] == payload["strategy_id"]
        assert result.primary_output["version"] == payload["version"]
        assert payload["source_strategy_spec_id"] in result.registry_entry["lineage"]["parent_registry_ids"]
        return

    if component == "imitation":
        _assert_registry_seed_lineage(spec, result)
        assert result.metrics["epochs"] == payload["epochs"]
        assert result.artifact_bundle["training_config"]["seed"] == payload["seed"]
        return

    if component == "trl":
        _assert_registry_seed_lineage(spec, result)
        assert result.primary_output["strategy_id"] == payload["strategy_id"]
        assert result.primary_output["beta"] == payload["beta"]
        assert result.registry_entry["metadata"]["strategy_families"] == [payload["strategy_family"]]
        return

    if component == "qlib":
        _assert_registry_seed_lineage(spec, result)
        training_config = result.artifact_bundle["training_config"]
        assert training_config["n_estimators"] == payload["n_estimators"]
        assert training_config["num_leaves"] == payload["num_leaves"]
        assert result.metrics["num_instruments"] >= 2
        return

    if component == "vectorbt":
        _assert_registry_seed_lineage(spec, result)
        dataset_summary = result.artifact_bundle["dataset_summary"]
        assert dataset_summary["dataset_id"] == HISTORICAL_OHLCV_DATASET_ID
        assert dataset_summary["num_instruments"] == 2
        assert dataset_summary["total_bars"] >= 60
        assert all(instrument.startswith("TWSE_") for instrument in dataset_summary["instruments"])
        backtest_config = result.artifact_bundle["backtest_config"]
        assert backtest_config["strategy_params"]["short_window"] == payload["short_window"]
        assert backtest_config["strategy_params"]["long_window"] == payload["long_window"]
        assert backtest_config["init_cash"] == payload["init_cash"]
        assert result.metrics["num_instruments"] == 2
        return

    if component == "statsmodels":
        metadata = result.primary_output["dataset_metadata"]
        assert metadata["alpha_seed_key"] == spec.seed["key"]
        assert metadata["source_strategy_spec_id"] == spec.seed["source_strategy_spec_id"]
        assert metadata["source_dataset_refs"] == payload["source_dataset_refs"]
        assert HISTORICAL_OHLCV_DATASET_ID in metadata["source_dataset_refs"]
        assert all(name.endswith(payload["series_suffix"]) for name in result.primary_output["price_series_names"])
        assert result.metrics["price_series_count"] >= 2
        return

    if component == "quantlib":
        metadata = result.primary_output["metadata"]
        assert metadata["alpha_seed_key"] == spec.seed["key"]
        assert metadata["source_strategy_spec_id"] == spec.seed["source_strategy_spec_id"]
        assert result.primary_output["dataset_id"] == payload["dataset_id"]
        assert result.primary_output["valuation_date"] == payload["valuation_date"]
        assert all(option_id.endswith(payload["instrument_suffix"]) for option_id in result.primary_output["option_ids"])
        assert result.metrics["option_count"] >= 1
        return

    if component in {"finrl", "rllib"}:
        _assert_registry_seed_lineage(spec, result)
        training_config = result.artifact_bundle["training_config"]
        assert training_config["seed"] == payload["seed"]
        assert training_config["learning_rate"] == payload["learning_rate"]
        assert result.primary_output["lookback_window"] == payload["lookback_window"]
        return

    if component == "ray_tune":
        _assert_registry_seed_lineage(spec, result)
        assert result.artifact_bundle["optimizer_id"] == payload["optimizer_id"]
        assert result.metrics["num_trials"] == payload["num_trials"]
        assert result.metrics["top_k"] == payload["top_k"]
        assert result.artifact_bundle["trigger"] == payload["trigger"]
        return

    if component == "mlflow":
        _assert_registry_seed_lineage(spec, result)
        assert result.primary_output["backend"] == "mlflow"
        assert result.primary_output["artifact_uri"].startswith(payload["tracking_uri"])
        assert result.metrics["num_instruments"] == 2
        return

    if component == "wandb":
        _assert_registry_seed_lineage(spec, result)
        assert result.primary_output["backend"] == "wandb"
        assert result.primary_output["sync_status"] == "offline_local"
        assert result.refs["local_store_dir"]
        return

    if component == "lean_handoff":
        _assert_registry_seed_lineage(spec, result)
        handoff = result.primary_output
        assert handoff["deployment_plan"]["plan_id"].endswith(payload["plan_suffix"])
        assert handoff["deployment_plan"]["capital_pool_id"] == payload["capital_pool_id"]
        assert handoff["deployment_plan"]["risk_policy_ref"] == payload["risk_policy_ref"]
        assert handoff["deployment_plan"]["runtime_config_ref"] == payload["runtime_config_ref"]
        assert handoff["runtime_binding"]["metadata"]["engine_bridge_commit"] == payload["engine_bridge_commit"]
        assert handoff["deployment_plan"]["metadata"]["alpha_seed_key"] == spec.seed["key"]
        assert handoff["source_vectorbt_metrics"]["num_instruments"] == 2
        return

    raise AssertionError(f"Unhandled component {component}")
