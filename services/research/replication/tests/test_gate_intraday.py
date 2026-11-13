"""K6: intraday (minute) replication gate -- clean sessions and trade counts.

Fixtures are real ``run_intraday_replication_protocol`` outputs (real vectorbt) over deterministic
synthetic reconciled-shape bars; they are NOT market data (see fixture ``fixture_provenance``).
Because the recorded runs lose money after costs, the *positive* gate case applies named
test-side performance injections on top of the recorded evaluation (the counts, labels and
snapshot stay as recorded).  Every test goes through ``ReplicationGate.evaluate_candidate``.
"""
import copy
import hashlib
import json
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.research.replication import test_gate as gate_tests
from services.research.replication.gate import (
    INSUFFICIENT_HISTORY_STATUS,
    INTRADAY_UNQUALIFIED_STATUS,
    ReplicationGate,
    run_intraday_replication_protocol,
)
from services.research.replication.gate_config import GateConfig

FIXTURES = Path(__file__).parents[1] / "fixtures"
FIXTURE_NAMES = ("recorded_backtest_1m_2330.json", "recorded_backtest_1m_2330_2317.json")


def load(name):
    return json.loads((FIXTURES / name).read_text())


def request_for(evaluation):
    request = gate_tests.TestReplicationGate._build_request(None)
    request.research_handoff["backtest_evaluation"] = None
    request.backtest_evaluation = evaluation
    request.metadata = {
        "strategy_artifact_checksum": evaluation["strategy_artifact"]["checksum"],
        "dataset_version_id": evaluation["dataset_id"],
    }
    return request


def evaluate(evaluation, *, record=True, config=None):
    gate = ReplicationGate(config=config or GateConfig())
    if record:
        gate.backtest_run_store.record(evaluation)
    response = gate.evaluate_candidate(request_for(evaluation))
    result = next(r for r in response.results if r.criterion_id == "real_backtest_validation")
    return gate, result


def inject_passing_performance(ev):
    """Named test-side injection: make the recorded run a performance winner."""
    ev["out_of_sample"].update(sharpe_ratio=3.0, total_return=0.05, max_drawdown=0.05)
    ev["in_sample"].update(sharpe_ratio=3.0)
    for fold in ev["walk_forward"]["folds"]:
        fold.update(sharpe_ratio=2.0, total_return=0.02)
    ev["walk_forward"]["folds"][0]["total_return"] = 0.01
    ev["benchmarks"]["buy_and_hold_same_symbols"]["sharpe_ratio"] = 0.5
    ev["benchmarks"]["tw_0050"]["sharpe_ratio"] = 0.4
    return ev


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_recorded_run_clears_intraday_qualification_and_fails_only_on_performance(name):
    ev = load(name)
    _, result = evaluate(ev)
    intraday = result.details["intraday"]
    assert intraday["evidence_mode"] == "replay" and intraday["live_or_paper_evidence"] is False
    assert intraday["bar_frequency"] == "1m" and intraday["replay_clock"] == "bar_end"
    assert intraday["source_dataset"] == "tw_price_1m" and intraday["source_snapshot_sha256"]
    assert intraday["clean_sessions"] >= 60 and intraday["in_sample_trades"] >= 300
    assert result.details["status"] == "failed"  # the recorded run loses after costs
    assert "oos_minimum_round_trips_met" not in result.details["failed_checks"]
    assert "oos_sharpe_positive_after_costs" in result.details["failed_checks"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_one_minute_fixture_is_an_unmodified_terminal_synthetic_protocol_result(name):
    ev = load(name)
    provenance = ev.pop("fixture_provenance")
    assert provenance["classification"].startswith("SYNTHETIC")
    raw = json.dumps(ev, indent=1, default=str).encode()
    assert hashlib.sha256(raw).hexdigest() == provenance["raw_result_sha256"]
    receipt = provenance["execution_receipt"]
    assert receipt["exit_code"] == 0 and receipt["status"] == "terminal"
    assert receipt["source_head"] == receipt["source_head_after"] == provenance["source_head"]
    assert receipt["tracked_source_clean_after"] and receipt["input_hashes_unchanged"]
    assert receipt["elapsed_seconds"] < receipt["deadline_seconds"] == 900
    assert ev["intraday"]["clean_sessions"] == 66
    assert (ev["in_sample"]["num_bars"], ev["out_of_sample"]["num_bars"]) == (46 * 270, 20 * 270)
    folds = ev["walk_forward"]["folds"]
    assert [fold["num_bars"] for fold in folds] == [15 * 270, 15 * 270, 16 * 270]
    for window in [ev["in_sample"], ev["out_of_sample"], *folds]:
        assert window["total_return"] < 0  # costs are real, no injected profitable fixture
        for metrics in window["per_instrument_metrics"].values():
            assert abs(metrics["net_position"]) < 1e-8
            assert metrics["total_fees_paid"] > 0


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_gate_passes_when_counts_clean_and_performance_wins(name):
    ev = inject_passing_performance(load(name))
    gate, result = evaluate(ev)
    assert result.passed, result.evidence
    d = result.details["intraday"]
    assert d["thresholds"] == {"min_clean_sessions": 60, "min_in_sample_trades": 300,
                               "min_oos_sessions": 20, "min_oos_trades": 100}
    assert d["out_of_sample_sessions"] >= 20 and d["out_of_sample_trades"] >= 100


@pytest.mark.parametrize(
    "mutate, shortfall",
    [
        (lambda e: e["intraday"].update(clean_sessions=59), "clean_sessions"),
        (lambda e: e["intraday"].update(out_of_sample_sessions=19), "out_of_sample_sessions"),
        (lambda e: e["intraday"].update(in_sample_trades=299), "in_sample_trades"),
        (lambda e: e["intraday"].update(out_of_sample_trades=99), "out_of_sample_trades"),
    ],
)
def test_below_threshold_is_insufficient_not_admitted_and_never_relaxed(mutate, shortfall):
    ev = inject_passing_performance(load(FIXTURE_NAMES[0]))
    mutate(ev)
    _, result = evaluate(ev)  # re-recorded so only the thresholds can refuse it
    assert not result.passed
    assert result.details["status"] == INSUFFICIENT_HISTORY_STATUS
    assert shortfall in result.details["shortfalls"]


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda e: e["intraday"]["source_snapshot"].update(waived=True), "quarantined_or_waived"),
        (lambda e: e["intraday"]["source_snapshot"].update(quarantined_days=["2026-02-02"]), "quarantined_or_waived"),
        (lambda e: e["intraday"]["source_snapshot"].update(reconciled_only=False), "not_reconciled"),
        (lambda e: e["intraday"]["source_snapshot"].update(source_dataset="tw_price_1d"), "not_reconciled"),
        (lambda e: e["intraday"].update(live_or_paper_evidence=True), "not_labelled_replay"),
        (lambda e: e["intraday"].update(evidence_mode="live"), "not_labelled_replay"),
        (lambda e: e["intraday"].update(replay_clock="bar_start"), "not_labelled_replay"),
        (lambda e: e["intraday"].update(unclean_sessions=1), "unclean_sessions"),
        (lambda e: e["intraday"].update(bar_frequency="15m"), "bar_frequency"),
        (lambda e: e["intraday"].pop("clean_sessions"), "counts_missing"),
        (lambda e: e.pop("intraday"), "missing_intraday"),
    ],
)
def test_unqualified_minute_evidence_is_refused(mutate, reason):
    ev = inject_passing_performance(load(FIXTURE_NAMES[0]))
    mutate(ev)
    _, result = evaluate(ev)
    assert not result.passed
    assert result.details["status"] == INTRADAY_UNQUALIFIED_STATUS
    assert reason in result.details["reason"]


def test_strategy_frequency_must_match_the_evidence_frequency():
    ev = inject_passing_performance(load(FIXTURE_NAMES[0]))
    gate = ReplicationGate(config=GateConfig())
    gate.backtest_run_store.record(ev)
    request = request_for(ev)
    art = {"parameters": {"bar_frequency": "5m"}}
    request.research_handoff["strategy_artifact"] = art
    request.metadata = {"strategy_artifact_checksum": ev["strategy_artifact"]["checksum"],
                        "dataset_version_id": ev["dataset_id"]}
    result = next(r for r in gate.evaluate_candidate(request).results
                  if r.criterion_id == "real_backtest_validation")
    assert not result.passed  # refused (binding or frequency); never admitted on a mismatch


def test_unrecorded_or_edited_evaluation_is_not_authoritative():
    ev = inject_passing_performance(load(FIXTURE_NAMES[0]))
    _, unrecorded = evaluate(ev, record=False)
    assert not unrecorded.passed and unrecorded.details["reason"] == "unknown_backtest_run"
    gate = ReplicationGate(config=GateConfig())
    gate.backtest_run_store.record(ev)
    edited = copy.deepcopy(ev)
    edited["intraday"]["in_sample_trades"] = 10_000
    result = next(r for r in gate.evaluate_candidate(request_for(edited)).results
                  if r.criterion_id == "real_backtest_validation")
    assert result.details["reason"] == "evaluation_differs_from_persisted_run"


@pytest.mark.parametrize(
    "field", ["min_clean_sessions", "min_in_sample_trades", "min_oos_sessions", "min_oos_trades"]
)
def test_gate_config_floors_cannot_be_lowered(field):
    with pytest.raises(ValueError, match="floor"):
        GateConfig(**{field: 1})
    higher = getattr(GateConfig(), field) + 1
    assert getattr(GateConfig(**{field: higher}), field) == higher


def test_defaults_match_design():
    cfg = GateConfig()
    assert (cfg.min_clean_sessions, cfg.min_in_sample_trades, cfg.min_oos_sessions, cfg.min_oos_trades) == (
        60, 300, 20, 100)


# --- protocol level: thin / unclean history is reported, never run, never lowered -------------

def synthetic_dataset(sessions, *, drop_bar_in_session=None):
    from services.research.vectorbt.adapter.vectorbt_adapter import _sha256_json

    days, d = [], date(2026, 1, 5)
    while len(days) < sessions:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    rng, records = random.Random(3), []
    for day, symbol in ((d_, s_) for d_ in days for s_ in ("2330", "0050")):
        base = datetime.fromisoformat(f"{day}T09:00:00+08:00")
        px = 600.0
        for i, m in enumerate(range(0, 270, 5)):
            if drop_bar_in_session == day and symbol == "2330" and i == 10:
                continue
            st = base + timedelta(minutes=m)
            o, px = px, px * (1 + rng.gauss(0, 0.002))
            records.append({
                "instrument": symbol, "bar_start": st.isoformat(),
                "bar_end": (st + timedelta(minutes=5)).isoformat(),
                "bar_availability_ts": datetime.fromisoformat(f"{day}T16:00:00+08:00").astimezone(timezone.utc).isoformat(),
                "open": o, "high": max(o, px) + 0.1, "low": min(o, px) - 0.1, "close": px, "volume": 1000.0,
            })
    return {
        "dataset_id": "ds-thin", "strategy_id": "S", "source_dataset_refs": ["source-ingest:kbar/tw_price_1m"],
        "data_frequency": "5m", "records": records,
        "replay": {"label": "replay", "clock": "bar_end", "availability_ceiling": "2026-12-31T00:00:00+00:00",
                   "source_snapshot": {"source_dataset": "tw_price_1m", "reconciled_only": True,
                                       "content_sha256": _sha256_json(records)}},
    }


def minute_artifact():
    from services.research.tests.test_vectorbt_intraday import _minute_art

    return _minute_art("5m")


def test_protocol_reports_insufficient_clean_sessions_without_running_the_engine():
    ev = run_intraday_replication_protocol(synthetic_dataset(59), minute_artifact(), config=GateConfig())
    assert ev["status"] == INSUFFICIENT_HISTORY_STATUS
    assert ev["history_check"]["trading_days"] == 59 and ev["history_check"]["unit"] == "clean_sessions"
    assert ev["evidence_mode"] == "replay" and ev["live_or_paper_evidence"] is False


def test_protocol_unclean_session_blocks_replication_evidence():
    ds = synthetic_dataset(70, drop_bar_in_session="2026-01-12")
    ev = run_intraday_replication_protocol(ds, minute_artifact(), config=GateConfig())
    assert ev["status"] == INSUFFICIENT_HISTORY_STATUS
    assert ev["history_check"]["details"]["unclean_sessions"] == ["2026-01-12"]


def test_thin_minute_dataset_through_gate_is_recorded_insufficient_and_not_admitted():
    gate = ReplicationGate(config=GateConfig())
    request = gate_tests.TestReplicationGate._build_request(None)
    request.backtest_evaluation = None
    request.research_handoff["backtest_evaluation"] = None
    art = minute_artifact()
    from services.registry.strategy_artifact import strategy_artifact_checksum

    ds = synthetic_dataset(40)
    request.research_handoff["strategy_artifact"] = art
    request.research_handoff["backtest_dataset"] = ds
    request.metadata = {"strategy_artifact_checksum": strategy_artifact_checksum(art),
                        "dataset_version_id": ds["dataset_id"]}
    response = gate.evaluate_candidate(request)
    result = next(r for r in response.results if r.criterion_id == "real_backtest_validation")
    assert not result.passed and result.details["status"] == INSUFFICIENT_HISTORY_STATUS
    assert response.admission_status.value != "admitted"
