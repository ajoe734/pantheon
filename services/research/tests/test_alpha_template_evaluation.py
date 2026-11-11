"""Tests for ALPHA-TEMPLATE-EVALUATION-20261110 (Dev Plan §3.5).

Covers:
1. Every time-series AlphaTemplate (171 templates: 158 Qlib Alpha158 + 13 Alpha101)
   evaluates on governed Taiwan data and reports 1d/5d/20d Spearman IC per symbol
   and pooled, plus hit rate, turnover, and IC decay.
2. Strict point-in-time evaluation with a no-look-ahead proof (future bar
   perturbation invariance, prefix truncation equivalence, stepwise parity, and
   availability_lag_days / available_time enforcement).
3. Redundancy correlation against every ``active`` template: correlation > 0.9
   marks ``duplicate_of`` that active template.
4. Results stored as a completed research run in ``ResearchOrchestratorStore`` and
   linked from each template's ``evaluation_ref`` in ``RegistryService`` (both
   direct service API and FastAPI endpoints).
"""
from __future__ import annotations

import copy
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.registry.alpha_template import (
    ALPHA_TEMPLATE_METADATA_KEY,
    AlphaTemplateValidationError,
    alpha_template_registry_id,
    ensure_builtin_alpha_templates,
    load_library_templates,
)
from services.registry.split_api import RegistryService
from services.registry.storage import get_store, reset_store
from services.research.alpha_templates import (
    DEFAULT_TAIWAN_SYMBOLS,
    EVALUATION_HORIZONS,
    HORIZON_KEYS,
    build_taiwan_point_in_time_universe,
    evaluate_alpha_template_library,
    evaluate_single_alpha_template,
    evaluate_template_point_in_time_symbol,
    run_alpha_template_evaluation,
)
from services.research.store import ResearchOrchestratorStore


@pytest.fixture(autouse=True)
def _explicit_registry_backend(monkeypatch):
    """The registry never defaults its backend; pin the in-memory double explicitly."""
    monkeypatch.setenv("REGISTRY_STORE_BACKEND", "memory")


@pytest.fixture(scope="module")
def taiwan_universe():
    return build_taiwan_point_in_time_universe(
        symbols=DEFAULT_TAIWAN_SYMBOLS,
        bar_count=160,
    )


@pytest.fixture(scope="module")
def full_library_evaluation(taiwan_universe):
    return evaluate_alpha_template_library(universe=taiwan_universe)


def test_every_alpha_template_reports_1d_5d_20d_ic_per_symbol_and_pooled_plus_hit_rate_turnover_decay(
    full_library_evaluation,
):
    """AC 1: Each template evaluation reports IC for 1/5/20d per symbol and pooled + hit rate + turnover + decay."""
    assert full_library_evaluation["symbols"] == sorted(DEFAULT_TAIWAN_SYMBOLS)
    assert full_library_evaluation["horizons"] == list(EVALUATION_HORIZONS)
    # 158 Qlib Alpha158 + 13 Alpha101 time-series = 171 evaluated; 11 Alpha101 cross-sectional skipped
    assert full_library_evaluation["evaluated_count"] == 171
    assert full_library_evaluation["skipped_cross_sectional_count"] == 11

    evaluations = full_library_evaluation["evaluations"]
    assert len(evaluations) == 171

    for tid, ev in evaluations.items():
        assert ev["template_id"] == tid
        assert ev["scope"] == "time_series"
        assert ev["status"] == "evaluated"

        # Pooled IC for 1d, 5d, 20d
        pooled_ic = ev["pooled_ic"]
        assert set(pooled_ic.keys()) == set(HORIZON_KEYS)
        for h_key in HORIZON_KEYS:
            val = pooled_ic[h_key]
            assert isinstance(val, float) and math.isfinite(val)
            assert -1.0 <= val <= 1.0
        assert ev["ic_1d"] == pooled_ic["1d"]
        assert ev["ic_5d"] == pooled_ic["5d"]
        assert ev["ic_20d"] == pooled_ic["20d"]

        # Per-symbol IC for every Taiwan symbol and 1d, 5d, 20d horizon
        per_symbol_ic = ev["per_symbol_ic"]
        assert set(per_symbol_ic.keys()) == set(DEFAULT_TAIWAN_SYMBOLS)
        for sym in DEFAULT_TAIWAN_SYMBOLS:
            sym_ic = per_symbol_ic[sym]
            assert set(sym_ic.keys()) == set(HORIZON_KEYS)
            for h_key in HORIZON_KEYS:
                s_val = sym_ic[h_key]
                assert isinstance(s_val, float) and math.isfinite(s_val)
                assert -1.0 <= s_val <= 1.0
            assert ev["per_symbol"][sym]["ic"] == sym_ic
            assert 0.0 <= ev["per_symbol"][sym]["hit_rate"] <= 1.0
            assert 0.0 <= ev["per_symbol"][sym]["turnover"] <= 1.0
            assert ev["per_symbol"][sym]["valid_bars"] > 50

        # Hit rate
        assert isinstance(ev["hit_rate"], float) and math.isfinite(ev["hit_rate"])
        assert 0.0 <= ev["hit_rate"] <= 1.0
        assert set(ev["hit_rate_by_horizon"].keys()) == set(HORIZON_KEYS)
        for h_key in HORIZON_KEYS:
            assert 0.0 <= ev["hit_rate_by_horizon"][h_key] <= 1.0

        # Turnover
        assert isinstance(ev["turnover"], float) and math.isfinite(ev["turnover"])
        assert 0.0 <= ev["turnover"] <= 1.0
        assert set(ev["turnover_by_horizon"].keys()) == set(HORIZON_KEYS)
        for h_key in HORIZON_KEYS:
            assert 0.0 <= ev["turnover_by_horizon"][h_key] <= 1.0

        # Decay
        decay = ev["decay"]
        assert ev["ic_decay"] == decay
        for req_key in (
            "1d",
            "5d",
            "20d",
            "ic_change_1d_to_5d",
            "ic_change_1d_to_20d",
            "abs_ic_decay_1d_to_20d",
            "decay_ratio_20d_over_1d",
            "slope_per_day",
        ):
            assert req_key in decay
            assert isinstance(decay[req_key], float) and math.isfinite(decay[req_key])


def test_point_in_time_no_look_ahead_proof(taiwan_universe):
    """AC 2: Evaluation uses only point-in-time inputs and proves no look-ahead."""
    library_by_id = {t["template_id"]: t for t in load_library_templates()}
    sample_ids = [
        "qlib_alpha158:KMID",
        "qlib_alpha158:ROC20",
        "qlib_alpha158:MA60",
        "qlib_alpha158:STD30",
        "qlib_alpha158:CORR20",
        "qlib_alpha158:WVMA20",
        "alpha101:#7",
        "alpha101:#46",
    ]
    sym_2330 = taiwan_universe["2330"]
    cutoff_idx = 110
    cutoff_dt = sym_2330["decision_times"][cutoff_idx]

    # 1. Perturb all future bars (t > cutoff_idx) with extreme 10x price and 50x volume shocks
    shocked_2330 = copy.deepcopy(sym_2330)
    for t in range(cutoff_idx + 1, len(shocked_2330["bars"])):
        shocked_2330["bars"][t]["open"] *= 10.0
        shocked_2330["bars"][t]["high"] *= 12.0
        shocked_2330["bars"][t]["low"] *= 8.0
        shocked_2330["bars"][t]["close"] *= 10.5
        shocked_2330["bars"][t]["vwap"] *= 10.2
        shocked_2330["bars"][t]["volume"] *= 50.0
        for field_name in ("open", "high", "low", "close", "vwap", "volume"):
            shocked_2330["features"][field_name][t]["value"] = float(
                shocked_2330["bars"][t][field_name]
            )

    # 2. Truncated dataset containing only bars up to cutoff_idx (inclusive)
    truncated_2330 = {
        "symbol": "2330",
        "dataset": "TWSE_Normalized",
        "decision_times": sym_2330["decision_times"][: cutoff_idx + 1],
        "bars": sym_2330["bars"][: cutoff_idx + 1],
        "features": {
            k: v[: cutoff_idx + 1] for k, v in sym_2330["features"].items()
        },
    }

    for tid in sample_ids:
        tpl = library_by_id[tid]
        res_base = evaluate_template_point_in_time_symbol(tpl, sym_2330, symbol="2330")
        res_shocked = evaluate_template_point_in_time_symbol(
            tpl, shocked_2330, symbol="2330"
        )
        res_truncated = evaluate_template_point_in_time_symbol(
            tpl, truncated_2330, symbol="2330"
        )
        res_stepwise = evaluate_template_point_in_time_symbol(
            tpl, truncated_2330, symbol="2330", strict_stepwise=True
        )

        for t in range(cutoff_idx + 1):
            v_base = res_base["factor_values"][t]
            v_shock = res_shocked["factor_values"][t]
            v_trunc = res_truncated["factor_values"][t]
            v_step = res_stepwise["factor_values"][t]
            if math.isnan(v_base):
                assert math.isnan(v_shock) and math.isnan(v_trunc) and math.isnan(v_step)
            else:
                assert v_base == pytest.approx(v_shock, rel=1e-12, abs=1e-12), (tid, t)
                assert v_base == pytest.approx(v_trunc, rel=1e-12, abs=1e-12), (tid, t)
                assert v_base == pytest.approx(v_step, rel=1e-12, abs=1e-12), (tid, t)

    # 3. Prove availability_lag_days / delayed available_time prevents look-ahead
    lagged_tpl = copy.deepcopy(library_by_id["qlib_alpha158:ROC5"])
    lagged_tpl["template_id"] = "custom:ROC5_lagged"
    lagged_tpl["origin"] = {"library": "custom", "ref": "ROC5_lagged"}
    # Set availability_lag_days = 5 on close
    lagged_tpl["inputs"][0]["availability_lag_days"] = 5

    res_unlagged_trunc = evaluate_template_point_in_time_symbol(
        library_by_id["qlib_alpha158:ROC5"], truncated_2330, symbol="2330"
    )
    res_lagged_at_cutoff = evaluate_template_point_in_time_symbol(
        lagged_tpl,
        sym_2330,
        symbol="2330",
        decision_times=[cutoff_dt],
        strict_stepwise=True,
    )
    # Because close has a 5-calendar-day availability lag, observations in the last
    # 5 calendar days before cutoff_dt are NOT yet available at cutoff_dt.
    # Find the latest bar index whose event_time + 5 days <= cutoff_dt:
    from datetime import datetime, timedelta

    cutoff_dt_obj = datetime.fromisoformat(cutoff_dt.replace("Z", "+00:00"))
    visible_indices = [
        idx
        for idx, dt_s in enumerate(sym_2330["decision_times"])
        if datetime.fromisoformat(dt_s.replace("Z", "+00:00")) + timedelta(days=5)
        <= cutoff_dt_obj
    ]
    latest_visible_idx = visible_indices[-1]
    assert latest_visible_idx < cutoff_idx
    assert res_lagged_at_cutoff["factor_values"][0] == pytest.approx(
        res_unlagged_trunc["factor_values"][latest_visible_idx], rel=1e-12, abs=1e-12
    )


def test_redundancy_correlation_above_0_9_marks_duplicate_of_active_template(
    taiwan_universe,
):
    """AC 3: Templates correlated above 0.9 with an active template are marked duplicate_of that template."""
    library_by_id = {t["template_id"]: copy.deepcopy(t) for t in load_library_templates()}

    # Promote qlib_alpha158:KMID and qlib_alpha158:ROC5 to status="active"
    active_kmid = copy.deepcopy(library_by_id["qlib_alpha158:KMID"])
    active_kmid["status"] = "active"

    active_roc5 = copy.deepcopy(library_by_id["qlib_alpha158:ROC5"])
    active_roc5["status"] = "active"

    # Candidate 1: alpha101:#101 is (close - open) / ((high - low) + 0.001), which is
    # highly correlated (> 0.9) with qlib_alpha158:KMID2; and a scaled clone of KMID
    # has correlation 1.0 > 0.9 with active_kmid.
    cand_kmid_clone = copy.deepcopy(library_by_id["qlib_alpha158:KMID"])
    cand_kmid_clone["template_id"] = "custom:kmid_scaled_clone"
    cand_kmid_clone["origin"] = {"library": "custom", "ref": "kmid_scaled_clone"}
    cand_kmid_clone["status"] = "draft"
    cand_kmid_clone["expression"] = {
        "op": "mul",
        "args": [cand_kmid_clone["expression"], {"const": 2.5}],
    }

    # Candidate 2: monotonic shift of ROC5 -> correlation 1.0 > 0.9 with active_roc5
    cand_roc5_clone = copy.deepcopy(library_by_id["qlib_alpha158:ROC5"])
    cand_roc5_clone["template_id"] = "custom:roc5_shifted"
    cand_roc5_clone["origin"] = {"library": "custom", "ref": "roc5_shifted"}
    cand_roc5_clone["status"] = "draft"
    cand_roc5_clone["expression"] = {
        "op": "add",
        "args": [cand_roc5_clone["expression"], {"const": 0.05}],
    }

    # Candidate 3: uncorrelated volume factor VSTD20 (correlation <= 0.9 with KMID & ROC5)
    cand_vstd20 = copy.deepcopy(library_by_id["qlib_alpha158:VSTD20"])
    cand_vstd20["status"] = "draft"

    batch = evaluate_alpha_template_library(
        [active_kmid, active_roc5, cand_kmid_clone, cand_roc5_clone, cand_vstd20],
        universe=taiwan_universe,
    )

    evals = batch["evaluations"]
    assert evals["custom:kmid_scaled_clone"]["duplicate_of"] == "qlib_alpha158:KMID"
    assert evals["custom:kmid_scaled_clone"]["max_active_correlation"] > 0.99
    assert (
        evals["custom:kmid_scaled_clone"]["active_correlations"]["qlib_alpha158:KMID"]
        > 0.99
    )

    assert evals["custom:roc5_shifted"]["duplicate_of"] == "qlib_alpha158:ROC5"
    assert evals["custom:roc5_shifted"]["max_active_correlation"] > 0.99

    assert evals["qlib_alpha158:VSTD20"]["duplicate_of"] is None
    assert evals["qlib_alpha158:VSTD20"]["max_active_correlation"] < 0.9

    # Active templates themselves are not marked duplicate_of themselves
    assert evals["qlib_alpha158:KMID"]["duplicate_of"] is None
    assert evals["qlib_alpha158:ROC5"]["duplicate_of"] is None


def test_research_run_stored_and_linked_from_template_evaluation_ref(
    tmp_path: Path, taiwan_universe
):
    """AC 4: Results are stored as a research run and linked from the template evaluation_ref."""
    reset_store()
    registry_service = RegistryService(get_store())
    ensure_builtin_alpha_templates(registry_service)

    # Mark qlib_alpha158:KMID2 as active in the registry before running evaluation
    kmid2_reg_id = alpha_template_registry_id("qlib_alpha158:KMID2")
    kmid2_entry = registry_service.store.get(kmid2_reg_id)
    assert kmid2_entry is not None
    kmid2_meta = copy.deepcopy(dict(kmid2_entry.metadata))
    kmid2_meta[ALPHA_TEMPLATE_METADATA_KEY]["status"] = "active"
    registry_service.update_metadata(
        kmid2_reg_id,
        expected_metadata=kmid2_entry.metadata,
        new_metadata=kmid2_meta,
    )

    research_store = ResearchOrchestratorStore(tmp_path / "research-store")
    subset_ids = [
        "qlib_alpha158:KMID",
        "qlib_alpha158:KMID2",
        "alpha101:#101",  # (close - open) / ((high - low) + 0.001), correlated > 0.99 with KMID2
        "qlib_alpha158:ROC10",
    ]
    result = run_alpha_template_evaluation(
        research_store=research_store,
        registry_service=registry_service,
        template_ids=subset_ids,
        universe=taiwan_universe,
        run_id="rrun-alpha-eval-20261110",
        task_id="rtask-alpha-eval-20261110",
    )

    assert result["run_id"] == "rrun-alpha-eval-20261110"
    assert result["status"] == "completed"
    assert result["evaluated_count"] == 4
    # alpha101:#101 is nearly identical to active qlib_alpha158:KMID2 (correlation > 0.99)
    assert result["duplicates_detected"].get("alpha101:#101") == "qlib_alpha158:KMID2"

    # Verify ResearchOrchestratorStore persisted the run, task, artifact, and event
    stored_run = research_store.get_run("rrun-alpha-eval-20261110")
    assert stored_run is not None
    assert stored_run["status"] == "completed"
    assert stored_run["run_type"] == "alpha_template_evaluation"
    assert set(stored_run["evaluations"].keys()) == set(subset_ids)

    stored_task = research_store.get_task("rtask-alpha-eval-20261110")
    assert stored_task is not None
    assert stored_task["status"] == "completed"

    stored_artifact = research_store.get_artifact(result["artifact_id"])
    assert stored_artifact is not None
    assert stored_artifact["run_id"] == "rrun-alpha-eval-20261110"

    # Verify each evaluated template in RegistryService has evaluation_ref linked to the research run
    for tid in subset_ids:
        reg_id = alpha_template_registry_id(tid)
        view = registry_service.get(reg_id)
        tmpl_meta = view.entry.metadata[ALPHA_TEMPLATE_METADATA_KEY]
        assert tmpl_meta["evaluation_ref"] == "rrun-alpha-eval-20261110"
        if tid == "qlib_alpha158:KMID2":
            assert tmpl_meta["status"] == "active"
            assert tmpl_meta["duplicate_of"] is None
        elif tid == "alpha101:#101":
            assert tmpl_meta["status"] == "evaluated"
            assert tmpl_meta["duplicate_of"] == "qlib_alpha158:KMID2"
        else:
            assert tmpl_meta["status"] == "evaluated"

    reset_store()


def test_research_orchestrator_http_endpoints_for_alpha_template_evaluation(
    taiwan_universe,
):
    """Verify HTTP routes on services/research/main.py evaluate templates and link evaluation_ref."""
    from services.research.main import app

    reset_store()
    with TestClient(app) as client:
        resp = client.post(
            "/api/research-orchestrator/alpha-templates/evaluate",
            json={
                "template_ids": ["qlib_alpha158:KMID", "qlib_alpha158:ROC5", "alpha101:#12"],
                "run_id": "rrun-http-alpha-eval-01",
            },
        )
        assert resp.status_code == 201, resp.text
        payload = resp.json()
        assert payload["run_id"] == "rrun-http-alpha-eval-01"
        assert payload["evaluated_count"] == 3

        detail = client.get(
            "/api/research-orchestrator/alpha-templates/evaluations/qlib_alpha158:KMID"
        )
        assert detail.status_code == 200, detail.text
        detail_json = detail.json()
        assert detail_json["evaluation_ref"] == "rrun-http-alpha-eval-01"
        assert detail_json["status"] == "evaluated"
        assert detail_json["research_run"]["run_id"] == "rrun-http-alpha-eval-01"
        assert set(detail_json["evaluation"]["pooled_ic"].keys()) == {"1d", "5d", "20d"}

        listed = client.get(
            "/api/research-orchestrator/alpha-templates/evaluations",
            params={"status": "evaluated"},
        )
        assert listed.status_code == 200
        assert listed.json()["count"] == 3

    reset_store()


def test_cross_sectional_template_refused_in_single_evaluator(taiwan_universe):
    cs_tpl = next(t for t in load_library_templates() if t["scope"] == "cross_sectional")
    with pytest.raises(AlphaTemplateValidationError, match="refuses cross-sectional"):
        evaluate_single_alpha_template(cs_tpl, universe=taiwan_universe)
