"""The retrain proposal is a bounded in-sample search, never a fixed toggle."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture()
def research(monkeypatch):
    service_dir = ROOT / "services" / "research"
    monkeypatch.setenv("RESEARCH_ORCHESTRATOR_DATA_DIR", tempfile.mkdtemp())
    monkeypatch.syspath_prepend(str(service_dir))
    spec = importlib.util.spec_from_file_location("research_retrain_search_update", service_dir / "main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["research_retrain_search_update"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("store", None)
    return module


def test_no_toggle_helper_remains(research):
    assert not hasattr(research, "_retrain_parameter_update")


def test_search_without_governed_dataset_fails_closed(research):
    from services.research.vectorbt.adapter.vectorbt_adapter import build_canonical_tw_strategy_artifact

    with pytest.raises(research._RetrainFinished) as caught:
        research._retrain_search_update(build_canonical_tw_strategy_artifact(), {"decision_id": "d"}, "run-1")
    assert caught.value.outcome == "no_candidate"
    assert caught.value.reason == "in_sample_dataset_unavailable"


def test_counter_defaults_to_the_authoritative_oos_admission_file(research, monkeypatch, tmp_path):
    monkeypatch.delenv("RESEARCH_REPLICATION_TRIAL_COUNTER_PATH", raising=False)
    path = research._retrain_trial_counter_path()
    assert path == Path(research.ALPHA_REPLICATION_DATA_DIR) / "family_trial_counts.json"
    monkeypatch.setenv("RESEARCH_REPLICATION_TRIAL_COUNTER_PATH", str(tmp_path / "x.json"))
    assert research._retrain_trial_counter_path() == tmp_path / "x.json"


def test_family_trials_accumulate_across_decisions_restarts_and_the_gate_writer(tmp_path):
    from services.research.alpha_replication.evolution_search import shared_family_trial_counter
    from services.research.replication.gate import FamilyTrialCounterStore

    path = tmp_path / "family_trial_counts.json"
    first = shared_family_trial_counter(path)
    for _ in range(3):
        first.increment("fam")
    second = shared_family_trial_counter(path)  # next decision / restarted process
    for _ in range(3):
        second.increment("fam")
    assert second.get("fam") == 6
    # a counter written by the admission gate between two search increments is not lost
    FamilyTrialCounterStore(path).increment("fam")
    assert first.increment("fam") == 8


def test_governed_dataset_is_resolved_from_training_authority_env_not_a_stub(research, monkeypatch):
    for name in ("RESEARCH_RETRAIN_CANONICAL_DATASET_PATH", "TRAINING_SESSION_CANONICAL_DATASET_PATH"):
        monkeypatch.delenv(name, raising=False)
    assert research._RETRAIN_DATASET_LOADER({"strategy_id": "s"}, {}) is None
    monkeypatch.setenv("RESEARCH_RETRAIN_CANONICAL_DATASET_PATH", "/nonexistent/dataset.json")
    monkeypatch.setenv("RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH", "/nonexistent/policy.json")
    assert research._RETRAIN_DATASET_LOADER({"strategy_id": "s"}, {}) is None  # fails closed
