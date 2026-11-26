"""Tests for the bounded evolution search (EVOLUTION-AUTO-CONSUMER-SEARCH, design §12.2)."""

from __future__ import annotations

import copy
import random

import pytest

from services.research.alpha_replication import evolution_search as es
from services.research.replication.gate import FamilyTrialCounterStore
from services.research.vectorbt.adapter.vectorbt_adapter import (
    BacktestRunResult,
    build_canonical_tw_strategy_artifact,
    build_governed_tw_replication_dataset,
)


@pytest.fixture()
def parent():
    return build_canonical_tw_strategy_artifact()


class RecordingBackend:
    """Fake vectorbt backend: Sharpe = f(lookback) and remembers every window it saw."""

    def __init__(self):
        self.windows: list[tuple[str, str]] = []

    def run(self, dataset, config):
        inst = dataset.instruments[0]
        dates = dataset.dates_by_instrument[inst]
        self.windows.append((dates[0], dates[-1]))
        lb = config.strategy_params["lookback_bars"]
        sharpe = 1.0 - abs(lb - 17) / 40.0
        metrics = {"sharpe_ratio": sharpe, "total_return": 0.0, "max_drawdown": 0.0,
                   "trade_count": 1, "round_trips": 1}
        return BacktestRunResult(
            backend="fake", run_id="r", per_instrument_metrics={inst: metrics},
            aggregate_metrics={"mean_sharpe_ratio": sharpe}, notes=(),
        )


def test_sampling_is_fixed_budget_distinct_in_range_and_seeded(parent):
    a = es.sample_variants(parent, budget=50, seed=11)
    assert a == es.sample_variants(parent, budget=50, seed=11)
    assert a != es.sample_variants(parent, budget=50, seed=12)
    assert len(a) == 50 and len({tuple(sorted(v.items())) for v in a}) == 50
    for v in a:
        assert set(v) <= {"lookback_bars", "momentum_threshold"}
        if "lookback_bars" in v:
            assert isinstance(v["lookback_bars"], int) and 2 <= v["lookback_bars"] <= 60
        if "momentum_threshold" in v:
            assert 0.0 <= v["momentum_threshold"] <= 0.05
    # no longer a two-value toggle
    assert len({v.get("lookback_bars") for v in a}) > 5


def test_toggle_is_removed_from_research_main():
    import pathlib

    src = pathlib.Path("services/research/main.py").read_text()
    assert "def _retrain_parameter_update" not in src
    assert '3 if current_lookback == 2 else 2' not in src


def test_search_counts_every_trial_and_picks_best_improvement(parent, tmp_path):
    counter = FamilyTrialCounterStore(tmp_path / "t.json")
    score = lambda art: 1.0 - abs(art["parameters"]["lookback_bars"] - 17) / 40.0
    out = es.search_in_sample(parent, score_fn=score, decision_id="evd-1", family_id="fam", budget=50, trial_counter=counter)
    assert len(out.trials) == 50 and out.trials_counted == 50
    assert counter.get("fam") == 50 and out.family_trial_count == 50
    assert out.best is not None and out.best.score > out.baseline_score
    # a second decision accumulates on the same family
    es.search_in_sample(parent, score_fn=score, decision_id="evd-2", family_id="fam", budget=10, trial_counter=counter)
    assert counter.get("fam") == 60


def test_search_without_improvement_yields_no_candidate(parent):
    out = es.search_in_sample(parent, score_fn=lambda a: 0.0, decision_id="evd-1", family_id="f", budget=8)
    assert out.best is None


def test_failed_variant_is_a_trial_not_a_crash(parent):
    def score(art):
        if art["parameters"]["lookback_bars"] != 2:
            raise RuntimeError("boom")
        return 0.0

    out = es.search_in_sample(parent, score_fn=score, decision_id="e", family_id="f", budget=5)
    assert any(t.error for t in out.trials)


def test_scorer_never_shows_the_backend_the_out_of_sample_window(parent):
    dataset = build_governed_tw_replication_dataset()
    backend = RecordingBackend()
    scorer = es.make_in_sample_walk_forward_scorer(dataset, backend=backend)
    value = scorer(parent)
    assert isinstance(value, float)
    from services.research.vectorbt.adapter.vectorbt_adapter import GovernedVectorbtInputAdapter

    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    dates = prepared.dates_by_instrument["2330.TWSE"]
    oos_start = dates[int(len(dates) * 0.70)]
    assert len(backend.windows) == 3
    assert all(end < oos_start for _start, end in backend.windows)


def test_retrain_child_keeps_lineage_and_validates(parent):
    child, desc = es.build_child_artifact(parent, decision_id="evd-9", kind="retrain",
                                          parameter_updates={"lookback_bars": 17})
    assert child["parameters"]["lookback_bars"] == 17
    assert child["lineage"]["parent_registry_ids"] == [parent["artifact_id"]]
    assert "evd-9" in child["lineage"]["source_run_ids"]
    assert desc["kind"] == "retrain"


def _factor_parent():
    from services.registry.test_factor_rule import _sample_factor_rule_artifact

    return _sample_factor_rule_artifact()


def test_mutate_child_has_parent_lineage_and_changed_structure():
    parent = _factor_parent()
    snapshot = copy.deepcopy(parent)
    child, desc = es.build_child_artifact(parent, decision_id="evd-3", kind="mutate",
                                          rng=random.Random(5), structural_operator="add_filter")
    assert parent == snapshot
    assert child["lineage"]["parent_registry_ids"] == [parent["artifact_id"]]
    assert child["strategy_logic"] != parent["strategy_logic"]
    assert desc["operator"] == "add_filter"


def test_mutate_refuses_non_factor_rule_parent(parent):
    with pytest.raises(es.EvolutionSearchError):
        es.build_child_artifact(parent, decision_id="e", kind="mutate")


class FakeGate:
    def __init__(self, passed):
        self.passed = passed
        self.counts = {}

    def get_family_trial_count(self, fam):
        return self.counts.get(fam, 0)

    def get_family_total_trial_count(self, fam):
        return sum(v for k, v in self.counts.items() if k == fam or k.startswith(fam + "@"))

    def evaluate_candidate(self, request):
        fam = request["family"]
        self.counts[fam] = self.counts.get(fam, 0) + 1

        class R:  # noqa: D401
            passed = self.passed
            results = [type("C", (), {"criterion_id": "real_backtest_validation",
                                      "details": {"out_of_sample_sharpe_ratio": 0.4,
                                                  "deflated_sharpe_threshold": 0.2,
                                                  "failed_checks": [], "backtest_run_id": "bt-1"}})()]
        return R


def test_child_is_judged_out_of_sample_and_trials_accumulate():
    parent = _factor_parent()
    child, _ = es.build_child_artifact(parent, decision_id="evd-4", kind="mutate",
                                       rng=random.Random(1), structural_operator="change_window")
    gate = FakeGate(True)
    v1 = es.judge_child_out_of_sample(child, gate=gate, family_id="fam", request_factory=lambda c: {"family": "fam"})
    v2 = es.judge_child_out_of_sample(child, gate=gate, family_id="fam", request_factory=lambda c: {"family": "fam"})
    assert v1["window"] == "out_of_sample" and v1["passed"] is True
    assert (v1["trial_count_before"], v1["trial_count_after"]) == (0, 1)
    assert (v2["trial_count_before"], v2["trial_count_after"]) == (1, 2)
    assert v1["parent_registry_ids"] == [parent["artifact_id"]]


def test_mutation_decision_judges_every_child_oos_registers_only_passers_and_accumulates_trials():
    parent = _factor_parent()
    gate = FakeGate(True)
    registered = []
    out = es.run_mutation_decision(
        parent, decision_id="evd-5", gate=gate, family_id="fam", children=3, seed=2,
        request_factory=lambda c: {"family": "fam"},
        register_child=lambda child, verdict: registered.append(child["artifact_id"]) or child,
    )
    built = [c for c in out["children"] if c["built"]]
    assert built and gate.get_family_trial_count("fam") == len(built)
    assert registered == out["passed"]
    for c in built:
        assert c["verdict"]["window"] == "out_of_sample"
        assert c["verdict"]["parent_registry_ids"] == [parent["artifact_id"]]

    failing = FakeGate(False)
    none_registered = []
    out2 = es.run_mutation_decision(
        parent, decision_id="evd-6", gate=failing, family_id="fam2", children=2, seed=2,
        request_factory=lambda c: {"family": "fam2"},
        register_child=lambda child, verdict: none_registered.append(child) or child,
    )
    assert out2["passed"] == [] and none_registered == []


def test_gate_and_search_writers_interleave_without_losing_trials(tmp_path):
    from services.research.alpha_replication.evolution_search import shared_family_trial_counter
    from services.research.alpha_replication.shared_stores import SharedReplicationGate as ReplicationGate

    path = tmp_path / "family_trial_counts.json"
    gate = ReplicationGate(trial_counter_path=path)  # constructed BEFORE the search
    search = shared_family_trial_counter(path)
    for _ in range(50):
        search.increment("fam")
    assert gate.get_family_trial_count("fam") == 50
    assert gate._trial_counter_store.increment("fam") == 51
    assert search.increment("fam") == 52
    restarted = ReplicationGate(trial_counter_path=path)
    assert restarted.get_family_trial_count("fam") == 52


def test_trial_counter_concurrent_processes_do_not_lose_updates(tmp_path):
    import multiprocessing

    path = tmp_path / "counts.json"
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_bump, args=(str(path), 25)) for _ in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    from services.research.alpha_replication.shared_stores import SharedFamilyTrialCounterStore

    assert SharedFamilyTrialCounterStore(path).get("fam") == 100


def test_revalidation_worker_gate_and_run_store_are_the_shared_variants():
    import inspect

    from services.research.alpha_replication import revalidation_worker as rw

    source = inspect.getsource(rw.AlphaRevalidationWorker)
    assert "SharedReplicationGate as ReplicationGate" in source and "SharedBacktestRunStore" in source


def test_backtest_run_store_writers_do_not_drop_each_others_runs(tmp_path):
    from services.research.alpha_replication.shared_stores import SharedBacktestRunStore

    path = tmp_path / "runs.json"
    a, b = SharedBacktestRunStore(path), SharedBacktestRunStore(path)
    a.record({"backtest_run_id": "bt-a", "status": "completed"})
    b.record({"backtest_run_id": "bt-b", "status": "completed"})
    assert SharedBacktestRunStore(path).get("bt-a") and SharedBacktestRunStore(path).get("bt-b")


def _bump(path, n):
    from services.research.alpha_replication.shared_stores import SharedFamilyTrialCounterStore

    store = SharedFamilyTrialCounterStore(path)
    for _ in range(n):
        store.increment("fam")


def test_corrupt_shared_family_counter_fails_closed_on_read(tmp_path):
    import pytest
    from services.research.alpha_replication.shared_stores import SharedFamilyTrialCounterStore
    path = tmp_path / 'counts.json'
    path.write_text('not-json')
    with pytest.raises(ValueError):
        SharedFamilyTrialCounterStore(path).get('family')


def test_retrain_child_preserves_registry_alias_lineage(parent):
    child, _ = es.build_child_artifact(parent, decision_id="evo-alias", kind="retrain",
        parameter_updates={"lookback_bars": 3}, parent_registry_ref="registry-parent-alias")
    assert child["lineage"]["parent_registry_ids"] == ["registry-parent-alias"]


def test_early_gate_refusal_still_counts_a_built_family_trial(parent):
    from types import SimpleNamespace
    class Gate:
        count = 0
        def get_family_trial_count(self, _): return self.count
        def record_family_trial(self, _):
            self.count += 1
            return self.count
        def evaluate_candidate(self, _): return SimpleNamespace(passed=False, results=[])
    gate = Gate()
    verdict = es.judge_child_out_of_sample(parent, gate=gate, request_factory=lambda _: {}, family_id="family")
    assert not verdict['passed']
    assert verdict['trial_count_before'] == 0 and verdict['trial_count_after'] == 1


def test_trial_key_is_frequency_scoped_and_daily_keeps_legacy_key(parent, tmp_path):
    minute = copy.deepcopy(parent)
    minute["parameters"]["bar_frequency"] = "1m"
    assert es.artifact_trial_key("fam", parent) == "fam"
    assert es.artifact_trial_key("fam", minute) == "fam@1m"
    counter = FamilyTrialCounterStore(tmp_path / "t.json")
    score = lambda art: 1.0 - abs(art["parameters"]["lookback_bars"] - 17) / 40.0
    es.search_in_sample(parent, score_fn=score, decision_id="d", family_id="fam", budget=5, trial_counter=counter)
    out = es.search_in_sample(minute, score_fn=score, decision_id="m", family_id="fam", budget=7, trial_counter=counter)
    assert counter.get("fam") == 5 and counter.get("fam@1m") == 7 and out.family_trial_count == 7


def test_oos_judgement_reads_and_records_under_frequency_key():
    parent = _factor_parent()
    child, _ = es.build_child_artifact(parent, decision_id="evd-f", kind="mutate",
                                       rng=random.Random(1), structural_operator="change_window")
    child["parameters"]["bar_frequency"] = "5m"  # declared frequency carried by the judged child
    gate = FakeGate(True)
    v = es.judge_child_out_of_sample(child, gate=gate, family_id="fam", request_factory=lambda c: {"family": "fam@5m"})
    assert (v["trial_count_before"], v["trial_count_after"]) == (0, 1)
    assert gate.counts == {"fam@5m": 1} and gate.get_family_trial_count("fam") == 0


def test_run_mutation_decision_enforces_aggregate_family_cap_across_frequencies():
    parent = _factor_parent()
    parent["parameters"]["bar_frequency"] = "5m"
    gate = FakeGate(True)
    gate.counts = {"fam": 300, "fam@1m": 200}  # 500 combined; the 5m bucket itself is empty
    out = es.run_mutation_decision(parent, gate=gate, family_id="fam", decision_id="evd-cap",
        children=1, request_factory=lambda c: {"family": "fam@5m"}, register_child=lambda c, v: None)
    assert gate.counts == {"fam": 300, "fam@1m": 200}
    assert "family_tried" in str(out)
