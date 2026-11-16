"""Registry model_artifact registration: draft -> candidate, approved for paper only on a pass."""
from __future__ import annotations

import pytest

from services.research.regime import features as F
from services.research.regime import registration as R
from services.research.regime import validation as V
from services.research.tests.test_regime_research_route import DECISION_TIME, FakeRegistry, small_config, synthetic_observations

OBS, _, _ = synthetic_observations(1700, seed=7)


def _fit(config):
    inputs = F.build_volatility_inputs(OBS, decision_time=DECISION_TIME, horizon="short", config=config)
    model, report = V.fit_and_validate(inputs, config)
    return inputs, model, report


def test_passing_model_is_registered_draft_candidate_approved_with_versioned_name():
    config = small_config(max_switches_per_year=40.0)
    inputs, model, report = _fit(config)
    assert report.passed, report.reason
    client = FakeRegistry()
    registered = R.register_regime_model(client, model, report, input_refs=inputs.input_refs, config=config)
    assert [c[:2] for c in client.calls] == [("create", "draft"), ("advance", "candidate"), ("advance", "approved")]
    assert registered.artifact_state == "approved" and registered.reason == "approved_for_paper"
    method, rest = registered.model_version.split("@")
    lib_version, artifact_id = rest.split("/")
    assert method == "markov_switching" and lib_version == "0.15.0"
    assert artifact_id == registered.artifact_id and artifact_id.startswith("regime-tw-volatility-short-")
    entry = client.entries[registered.registry_id]
    assert entry["artifact_type"] == "model_artifact"
    assert entry["metadata"]["scope"] == "paper" and entry["metadata"]["no_language_model"] is True
    assert entry["lineage"]["source_dataset_refs"] and entry["evaluation_summary"]["validation"]["passed"] is True
    assert client.calls[-1][2] == R.APPROVER


def test_failed_validation_stays_candidate():
    config = small_config(max_switches_per_year=0.1)
    inputs, model, report = _fit(config)
    assert not report.passed
    client = FakeRegistry()
    registered = R.register_regime_model(client, model, report, input_refs=inputs.input_refs, config=config)
    assert registered.artifact_state == "candidate" and registered.reason.startswith("gates_failed")
    assert ("advance", "approved") not in [c[:2] for c in client.calls]


def test_less_than_minimum_training_history_stays_candidate_even_if_gates_would_pass():
    config = small_config(min_training_years=30.0)
    inputs, model, report = _fit(config)
    assert report.status == "insufficient_history"
    client = FakeRegistry()
    registered = R.register_regime_model(client, model, report, input_refs=inputs.input_refs, config=config)
    assert registered.artifact_state == "candidate"
    assert registered.reason.startswith("insufficient_training_history:")
    # Even a forced pass flag cannot approve a model trained on too little history.
    report.passed = True
    client2 = FakeRegistry()
    assert R.register_regime_model(client2, model, report, input_refs=inputs.input_refs,
                                   config=config).artifact_state == "candidate"


def test_same_fit_gets_the_same_artifact_id_and_idempotency_key():
    config = small_config(max_switches_per_year=40.0)
    inputs, model, report = _fit(config)
    assert R.artifact_id_for(model) == R.artifact_id_for(type(model).from_dict(model.to_dict()))
    a, b = FakeRegistry(), FakeRegistry()
    R.register_regime_model(a, model, report, input_refs=inputs.input_refs, config=config)
    R.register_regime_model(b, model, report, input_refs=inputs.input_refs, config=config)
    assert a.calls[0][2] == b.calls[0][2] == f"regime-model:{R.artifact_id_for(model)}"


def test_run_regime_research_registers_every_available_axis_and_reports_the_rest():
    config = small_config(max_switches_per_year=40.0, jump_penalty_grid=(30.0,), refit_sessions=200)
    no_chip, _, _ = synthetic_observations(1700, seed=7, with_chip=False)
    client = FakeRegistry()
    out = R.run_regime_research(no_chip, decision_time=DECISION_TIME, client=client, config=config,
                                horizons=("short",), source_run_id="rrun-test")
    by_axis = {r["axis"]: r for r in out["axes"]}
    assert by_axis["volatility"]["registration"]["status"] in ("candidate", "approved")
    assert by_axis["trend"]["registration"]["status"] in ("candidate", "approved")
    assert by_axis["risk_appetite"]["status"] == "unavailable" and "inputs_missing" in by_axis["risk_appetite"]["reason"]
    assert by_axis["macro"]["status"] == "available" and by_axis["macro"]["method"] == "rule_based"
    assert out["paper_only"] and out["no_language_model"] and out["proposals"]
    assert all(e["lineage"]["source_run_ids"] == ["rrun-test"] for e in client.entries.values())


def test_run_regime_research_with_no_governed_data_registers_nothing():
    client = FakeRegistry()
    out = R.run_regime_research([], decision_time=DECISION_TIME, client=client, config=small_config())
    assert out["status"] == "unavailable" and client.calls == [] and out["approved"] == []
    assert all(r["status"] == "unavailable" for r in out["axes"])
