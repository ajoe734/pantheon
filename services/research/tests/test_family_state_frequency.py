"""Family-state API reads the frequency-scoped trial key (SD §9 K8)."""
import pytest

from services.research import main as research
from services.research.alpha_replication.shared_stores import SharedFamilyTrialCounterStore


@pytest.fixture
def state(monkeypatch, tmp_path):
    path = tmp_path / "counts.json"
    monkeypatch.setattr(research, "_retrain_trial_counter_path", lambda: path)
    monkeypatch.setattr(research, "_retrain_call",
                        lambda **kw: [{"entry": {"owner_tenant": "t1"}}])
    store = SharedFamilyTrialCounterStore(path)

    def seed(key, n):
        store.increment(key, preset_count=n)

    return lambda **kw: research.evolution_family_state("fam", tenant_id="t1", **kw), seed


@pytest.mark.parametrize("seeded,freq,trials,total,status", [
    ({"fam": 500}, None, 500, 500, "tried"),
    ({"fam": 500}, "5m", 0, 500, "tried"),      # legacy daily count consumes the family-wide cap
    ({"fam@5m": 500}, None, 0, 500, "tried"),
    ({"fam@5m": 500}, "5m", 500, 500, "tried"),
    ({"fam@5m": 500}, "1m", 0, 500, "tried"),
    ({"fam": 500}, "1d", 500, 500, "tried"),
    ({"fam@1m": 400, "fam@5m": 400}, "5m", 400, 800, "tried"),   # combined exhaustion
    ({"fam": 100, "fam@1m": 200, "fam@5m": 199}, "5m", 199, 499, "available"),
    ({"fam": 100, "fam@1m": 200, "fam@5m": 200}, "1m", 200, 500, "tried"),
    ({"fam2": 500, "fam2@5m": 500}, "5m", 0, 0, "available"),    # other families not counted
    ({"fam": 10, "fam@5m": 20}, "5m", 20, 30, "available"),
])
def test_family_state_isolated_by_frequency(state, seeded, freq, trials, total, status):
    call, seed = state
    for key, n in seeded.items():
        seed(key, n)
    out = call(bar_frequency=freq)
    assert (out["trials"], out["family_total_trials"], out["status"]) == (trials, total, status)


# --- real-gate admission: deflation counts every frequency (SD_INTRADAY_MINUTE_BARS) ---
import json as _json  # noqa: E402

from services.research.replication.gate import ReplicationGate  # noqa: E402
from services.research.replication.gate_config import GateConfig  # noqa: E402
from services.research.replication.tests import test_gate_intraday as _gi  # noqa: E402


def _gate_eval(tmp_path, prior, oos_sharpe):
    counter = tmp_path / "trials.json"
    counter.write_text(_json.dumps(prior))
    ev = _gi.inject_passing_performance(_gi.load(_gi.FIXTURE_NAMES[0]))
    ev["out_of_sample"]["sharpe_ratio"] = oos_sharpe
    gate = ReplicationGate(config=GateConfig(), trial_counter_path=counter)
    gate.backtest_run_store.record(ev)
    request = _gi.request_for(ev)
    request.research_handoff["family_id"] = "fam"
    response = gate.evaluate_candidate(request)
    result = next(r for r in response.results if r.criterion_id == "real_backtest_validation")
    return gate, result


def test_gate_deflation_uses_aggregate_family_trials_across_frequencies(tmp_path):
    gate, result = _gate_eval(tmp_path, {"fam": 400}, 0.9)
    d = result.details
    assert d["frequency_trial_count"] == 1 and d["family_total_trial_count"] == 401
    assert d["trial_count"] == 401
    assert d["deflated_sharpe_threshold"] == pytest.approx(1.365589, abs=1e-5)
    # marginal candidate (0.9 > per-frequency 0.5 but < aggregate threshold) is rejected
    assert not result.passed
    assert "oos_sharpe_exceeds_deflated_threshold" in d["failed_checks"]
    assert gate.get_family_total_trial_count("fam") == 401
    assert gate.get_family_trial_count("fam", "1m") == 1  # isolated accounting kept
    assert gate.get_family_trial_count("fam") == 400  # daily bucket untouched


def test_gate_fresh_family_threshold_unchanged_and_strong_candidate_passes(tmp_path):
    _, result = _gate_eval(tmp_path, {}, 3.0)
    assert result.details["family_total_trial_count"] == result.details["frequency_trial_count"] == 1
    assert result.details["deflated_sharpe_threshold"] == pytest.approx(0.5)
    assert result.passed, result.evidence
