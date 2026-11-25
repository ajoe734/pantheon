"""PoolRiskSnapshot: one row per paper pool and trading day, from owner valuation facts."""

from __future__ import annotations

import copy
import importlib
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from services.telemetry import pool_risk_snapshot as prs
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from services.telemetry.test_runtime_summary_projection import (
    _RECORDED_2330_CALENDAR_EVIDENCE as CALENDAR,
    _event,
)

POOL = "pool-paper-001"


def _weekdays(count, start=date(2026, 10, 27)):
    days, cursor = [], start
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def valuation(day, equity, *, notional=1000.0, limit=2.0, runtime="rt-paper-001", binding="rtb-paper-001",
              created=None, fills=1, risk_off=0.10, digest="d" * 64):
    """A pnl_snapshot in the recorded Paper shape (mark as_of is the bar date, calendar evidence on the mark)."""
    at = f"{day.isoformat()}T00:00:00Z"
    event = _event("pnl_snapshot", created_at=created or f"{day.isoformat()}T06:00:00Z")
    event.update(runtime_id=runtime, binding_id=binding, event_id=f"v-{runtime}-{day}-{equity}",
                 pnl_as_of=at)
    event["metrics"] = {
        "pnl": equity - 100000.0, "portfolio_value": equity, "initial_cash": 100000.0,
        "cash": equity - notional, "pnl_period_start_at": "2026-10-01T00:00:00Z", "pnl_currency": "TWD",
        "valuation_mark_count": 1, "fill_count": fills,
    }
    event["metadata"].update({
        "valuation_method": "fill_cash_ledger_mark_to_market", "valuation_as_of": at,
        "mark_refs": [{"symbol": "2330.TW", "price": 1000.0, "as_of": at, "source_ref": "owner-daily-bar",
                       "calendar_evidence": copy.deepcopy(CALENDAR)}],
        "position_facts": [{"symbol": "2330.TW", "quantity": notional / 1000.0, "multiplier": 1.0,
                            "mark_price": 1000.0, "notional": notional}],
        "risk_policy_facts": {"risk_policy_id": "pol-1", "risk_policy_version": "3",
                              "risk_policy_digest": digest, "gross_limit": limit,
                              "drawdown_risk_off": risk_off},
    })
    return event


def rows_of(store, runtime="rt-paper-001"):
    return store.get(runtime)["pool_risk_snapshots"]


def make(tmp_path):
    return RuntimeSummaryProjectionStore(path=str(tmp_path / "s.json"))


def test_one_row_per_day_across_replay_duplicate_out_of_order_and_restart(tmp_path):
    store = make(tmp_path)
    days = _weekdays(3)
    events = [valuation(d, 100000.0 + i * 100) for i, d in enumerate(days)]
    for event in (events[2], events[0], events[1], events[1], {**events[0], "event_id": "replay-new-id"}):
        store.project_event(event)
    restarted = RuntimeSummaryProjectionStore(path=str(tmp_path / "s.json"))
    restarted.project_event(events[2])
    rows = rows_of(restarted)
    assert [r["as_of"] for r in rows] == [d.isoformat() for d in days]
    assert all(r["pool_id"] == POOL for r in rows)
    assert rows[0]["policy_ref"] == {"risk_policy_id": "pol-1", "risk_policy_version": "3",
                                     "risk_policy_digest": "d" * 64}
    # history dependent fields are order independent
    assert rows[2]["peak_equity"] == 100200.0 and rows[2]["drawdown_pct"] == 0.0


def test_binding_rollover_keeps_one_row_per_pool_day(tmp_path):
    store = make(tmp_path)
    day = _weekdays(1)[0]
    store.project_event(valuation(day, 100000.0, binding="rtb-paper-001"))
    rolled = valuation(day, 100000.0, binding="rtb-paper-002", created=f"{day}T07:00:00Z")
    rolled["persona_capital_binding_id"] = "pcb-paper-001"
    rolled["metadata"]["runtime_binding_effective_at"] = f"{day}T06:30:00Z"
    store.project_event(rolled)
    rows = rows_of(store)
    assert len(rows) == 1


def test_late_correction_replaces_row_and_keeps_previous_digest(tmp_path):
    store = make(tmp_path)
    day = _weekdays(1)[0]
    store.project_event(valuation(day, 100000.0))
    first = rows_of(store)[0]
    corrected = valuation(day, 99000.0, created=f"{day}T09:00:00Z")
    corrected["pnl_as_of"] = f"{day}T00:00:01Z"
    corrected["metadata"]["valuation_as_of"] = corrected["pnl_as_of"]
    corrected["metadata"]["mark_refs"][0]["as_of"] = f"{day}T00:00:00Z"
    store.project_event(corrected)
    rows = rows_of(store)
    assert len(rows) == 1
    assert rows[0]["equity"] == 99000.0
    assert rows[0]["previous_digest"] == first["digest"] and rows[0]["revision"] == 2


def test_before_close_valuation_writes_nothing(tmp_path):
    store = make(tmp_path)
    day = _weekdays(1)[0]
    summary = store.project_event(valuation(day, 100000.0, created=f"{day}T03:00:00Z"))
    assert not summary.get("_pool_risk_snapshot_rows")
    assert summary["pool_risk_snapshot_last_skip"] == "before_market_close"


def test_holiday_is_not_a_trading_day(tmp_path):
    store = make(tmp_path)
    summary = store.project_event(valuation(date(2026, 10, 23), 100000.0))
    assert not summary.get("_pool_risk_snapshot_rows")
    assert summary["pool_risk_snapshot_last_skip"] == "not_a_trading_day"


def test_values_null_with_reason_and_short_series_have_no_vol_or_cvar(tmp_path):
    store = make(tmp_path)
    days = _weekdays(5)
    for i, d in enumerate(days):
        store.project_event(valuation(d, 100000.0 + i * 50))
    row = rows_of(store)[-1]
    assert row["realized_vol_20d"] is None and row["cvar_95"] is None
    assert row["nulls"]["realized_vol_20d"] == "under_20_consecutive_returns"
    assert row["risk_budget_factor"] is None
    for key in ("effective_bets", "turnover_20d", "ladder_state", "breaches"):
        assert row[key] is None and row["nulls"][key]
    assert row["gross_exposure"] == pytest.approx(1000.0 / row["equity"])
    assert row["gross_utilisation"] == pytest.approx(row["gross_exposure"] / 2.0)


def test_missing_policy_limit_is_null_never_zero(tmp_path):
    store = make(tmp_path)
    day = _weekdays(1)[0]
    event = valuation(day, 100000.0)
    event["metadata"]["risk_policy_facts"] = {"unavailable_reason": "RiskPolicyError"}
    store.project_event(event)
    row = rows_of(store)[0]
    assert row["gross_limit"] is None and row["gross_utilisation"] is None
    assert row["nulls"]["gross_limit"] == "policy_gross_limit_missing"
    assert row["policy_ref"] == {"unavailable_reason": "RiskPolicyError"}


def test_twenty_days_give_vol_cvar_and_recorded_risk_budget_factor(tmp_path):
    store = make(tmp_path)
    days = _weekdays(22)
    equity = 100000.0
    for i, d in enumerate(days):
        equity *= 1.0 + (0.01 if i % 2 else -0.005)
        store.project_event(valuation(d, round(equity, 2), notional=100000.0))
    row = rows_of(store)[-1]
    assert row["realized_vol_20d"] > 0 and row["cvar_95"] < 0
    inputs = row["risk_budget_factor_inputs"]
    assert inputs["applied"] is False and inputs["window_rows"] == 20
    assert row["risk_budget_factor"] == pytest.approx(min(1.0, inputs["mean_gross_utilisation"] / 1.0), abs=1e-6)


def test_retention_cap_deletes_overflow_on_write():
    rows = [{"pool_id": "p", "as_of": (date(2020, 1, 1) + timedelta(days=i)).isoformat()} for i in range(405)]
    kept = prs.enforce_retention(rows)
    assert len(kept) == 400 and kept[0]["as_of"] == rows[5]["as_of"]


def test_served_payload_is_bounded_and_row_is_about_one_kb(tmp_path):
    store = make(tmp_path)
    for i, d in enumerate(_weekdays(30)):
        store.project_event(valuation(d, 100000.0 + i))
    served = store.get("rt-paper-001")
    assert len(served["pool_risk_snapshots"]) == 20 and len(served["pool_observations"]) == 20
    assert "_pool_risk_snapshot_rows" not in served
    row_bytes = len(json.dumps(served["pool_risk_snapshots"][-1]))
    assert row_bytes < 2500
    assert len(json.dumps(served["pool_risk_snapshots"])) < 50_000


def _pool_health_module():
    path = str(Path(__file__).resolve().parents[1] / "control-plane" / "persona")
    sys.path.insert(0, path)
    try:
        sys.modules.pop("pool_policy", None)
        return importlib.import_module("pool_policy")
    finally:
        sys.path.remove(path)


def test_real_pool_health_reads_a_real_summary_idle_and_active(tmp_path):
    pool_policy = _pool_health_module()
    policy = pool_policy.load_policy()
    store = make(tmp_path)
    for i, d in enumerate(_weekdays(20)):
        store.project_event(valuation(d, 100000.0 + i * 10, notional=50000.0))
    summary = store.get("rt-paper-001")
    health = pool_policy.pool_health([], {}, summary["pool_observations"], policy)
    assert health["gross_utilisation"]["status"] == "known"
    assert health["drawdown_utilisation"]["status"] == "known"
    assert health["idle_day_share"] == {"status": "known", "value": 0.0, "reason": None}

    idle = make(tmp_path / "idle") if (tmp_path / "idle").mkdir() is None else None
    start = _weekdays(1)[0]
    first = _event("heartbeat", created_at=f"{start}T01:00:00Z")
    first.update(runtime_id="rt-idle", event_id="idle-seed")
    seed = valuation(start, 100000.0, notional=0.0, runtime="rt-idle", fills=0)
    seed["metadata"]["position_facts"] = []
    seed["metadata"]["mark_refs"] = []
    seed["metrics"]["valuation_mark_count"] = 0
    seed["metrics"]["cash"] = 100000.0
    idle.project_event(seed)
    for d in _weekdays(21)[1:]:
        beat = _event("heartbeat", created_at=f"{d}T06:00:00Z")
        beat.update(runtime_id="rt-idle", event_id=f"beat-{d}")
        idle.project_event(beat)
    idle_summary = idle.get("rt-idle")
    observations = idle_summary["pool_observations"]
    assert len(observations) == 20
    idle_health = pool_policy.pool_health([], {}, observations, policy)
    assert idle_health["gross_utilisation"]["status"] == "known"
    assert idle_health["gross_utilisation"]["value"] == 0.0
    assert idle_health["idle_day_share"]["value"] == 1.0
    assert "low_risk_budget_utilisation" in idle_health["demand_reasons"]


def test_unvalued_position_after_close_is_a_null_row_not_zero(tmp_path):
    store = make(tmp_path)
    day = _weekdays(1)[0]
    seed = _event("heartbeat", created_at=f"{day}T06:00:00Z")
    seed.update(runtime_id="rt-paper-001")
    store.project_event(seed)
    row = store.get("rt-paper-001")["pool_risk_snapshots"][0]
    assert row["equity"] is None and row["gross_exposure"] is None
    assert row["nulls"]["equity"] == "no_known_owner_valuation"


def test_risk_budget_factor_changes_nothing_that_sizes_or_allocates():
    root = Path(__file__).resolve().parents[1]
    readers = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        rel = path.relative_to(root).as_posix()
        if rel.startswith("telemetry/") or "/test_" in rel or rel.split("/")[-1].startswith("test_"):
            continue
        if "pool_risk_snapshots" in text or "risk_budget_factor_inputs" in text or "_pool_risk_snapshot_rows" in text:
            readers.append(rel)
    assert readers == [], f"snapshot is measurement-only; unexpected readers: {readers}"
    # the allocation consumer derives its own factor from pool health and ignores the snapshot's value
    spec = importlib.util.spec_from_file_location(
        "persona_evaluator_agent", root / "persona-evaluator-agent" / "persona_evaluator_agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    health = {"gross_utilisation": {"status": "known", "value": 0.5}}
    baseline = module.pool_health_factors(health, {"budget_full": 1.0})
    polluted = module.pool_health_factors({**health, "risk_budget_factor": 0.01,
                                           "pool_risk_snapshots": [{"risk_budget_factor": 0.01}]},
                                          {"budget_full": 1.0})
    assert baseline == polluted and baseline["risk_budget_factor"] == 0.5
