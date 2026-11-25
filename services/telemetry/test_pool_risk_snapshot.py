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


def test_owner_stamps_closing_positions_at_marks_and_policy_facts_on_the_valuation_event():
    from services.execution.lean_runtime.paper_runtime import position_facts_from
    facts = position_facts_from(
        [{"symbol": "2330.TW", "quantity": 90, "multiplier": 1}, {"symbol": "0050.TW", "quantity": -10},
         {"symbol": "FLAT.TW", "quantity": 0}, {"symbol": "NOMARK.TW", "quantity": 5}],
        {"2330.TW": 221.0, "0050.TW": 150.0})
    by_symbol = {f["symbol"]: f for f in facts}
    assert set(by_symbol) == {"2330.TW", "0050.TW", "NOMARK.TW"}
    assert by_symbol["2330.TW"]["notional"] == 19890.0 and by_symbol["0050.TW"]["notional"] == -1500.0
    assert by_symbol["NOMARK.TW"]["notional"] is None  # unmarked stays null, never zero
    base, _ = prs.build_base(valuation={"as_of": "2026-10-27T00:00:00Z", "equity": 100000.0, "cash": 80000.0,
                                        "initial_cash": 100000.0, "mark_refs": [{"as_of": "2026-10-27T00:00:00Z"}]},
                             metadata={"position_facts": facts}, pool_id="p", binding="b",
                             event_created_at="2026-10-27T07:00:00Z")
    assert base["gross_notional"] is None and base["nulls_base"]["gross_notional"] == "position_mark_missing"


def _idle_state_snapshot(day, *, runtime="rt-3252fa75", binding="rtb-paper-001", created=None,
                         initial_cash=100000.0, cash=100000.0, fill_count=0,
                         limit=0.10, risk_off=0.10, digest="e" * 64, effective_at=None):
    event = _event("runtime_state_snapshot", created_at=created or f"{day.isoformat()}T01:00:00Z")
    event.update(runtime_id=runtime, binding_id=binding, event_id=f"rss-{runtime}-{day}")
    event["metrics"] = {"state_snapshot": 1}
    event["metadata"].update({
        "runtime_package": "paper_execution_runtime",
        "performance_telemetry": {"status": "no_fills" if fill_count == 0 else "unchanged", "fill_count": fill_count},
        "member_virtual_books": [],
        "account_facts": {"initial_cash": initial_cash, "cash": cash, "fill_count": fill_count},
        "risk_policy_facts": {
            "risk_policy_id": "rp-paper-low-v2",
            "risk_policy_version": "2.0",
            "risk_policy_digest": digest,
            "gross_limit": limit,
            "max_leverage": 1.0,
            "drawdown_risk_off": risk_off,
            "drawdown_warn": 0.05,
        },
    })
    if effective_at:
        event["metadata"]["runtime_binding_effective_at"] = effective_at
    return event


def test_never_filled_runtime_with_only_heartbeats_and_one_state_snapshot_passes_daily_check(tmp_path):
    store = make(tmp_path)
    day = date(2026, 11, 25)
    # Heartbeat before close, one runtime_state_snapshot, and heartbeats after close.
    hb_open = _event("heartbeat", created_at=f"{day.isoformat()}T01:00:00Z")
    hb_open.update(runtime_id="rt-3252fa75", event_id="hb-open")
    snap = _idle_state_snapshot(day, created=f"{day.isoformat()}T01:00:01Z")
    hb_close = _event("heartbeat", created_at=f"{day.isoformat()}T06:00:00Z")
    hb_close.update(runtime_id="rt-3252fa75", event_id="hb-close")
    for ev in (hb_open, snap, hb_close):
        store.project_event(ev)

    served = store.get("rt-3252fa75")
    rows = served["pool_risk_snapshots"]
    assert len(rows) == 1
    row = rows[0]
    assert row["as_of"] == "2026-11-25"
    assert row["equity"] == 100000.0
    assert row["initial_cash"] == 100000.0
    assert row["cash"] == 100000.0
    assert row["equity_basis"] == "flat_never_filled_initial_cash"
    assert row["gross_notional"] == 0.0 and row["net_notional"] == 0.0
    assert row["gross_exposure"] == 0.0 and row["net_exposure"] == 0.0
    assert row["gross_limit"] == 0.10 and row["drawdown_limit"] == 0.10
    assert row["gross_utilisation"] == 0.0 and row["drawdown_utilisation"] == 0.0
    assert row["policy_ref"] == {
        "risk_policy_id": "rp-paper-low-v2",
        "risk_policy_version": "2.0",
        "risk_policy_digest": "e" * 64,
        "basis": "runtime_state_snapshot",
    }

    spec = importlib.util.spec_from_file_location(
        "pool_risk_snapshot_day",
        Path(__file__).resolve().parents[2] / "acceptance" / "pool_risk_snapshot_day.py",
    )
    daily_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(daily_check)

    def fetch(path):
        if path.startswith("/bff/personas?"):
            return {"data": [{"id": "persona-0", "owner_lifecycle_state": "paper_running", "status": "active", "tenant_id": "t"}]}
        if path == "/api/v1/runtime-bindings":
            return {"data": [{"persona_id": "persona-0", "runtime_id": "rt-3252fa75", "deployment_stage": "paper", "status": "active"}]}
        if path.startswith("/api/v1/telemetry/"):
            return {"data": served}
        if path.startswith("/bff/management/trade-journeys?"):
            return {"data": {"items": [{"stages": {"trade_decision": {"status": "succeeded", "updated_at": "2026-11-25T06:00:00Z"}}}]}}
        return {"data": {"strategyPool": {"health": {m: {"status": "unknown", "value": None, "reason": "under_20"} for m in daily_check.MEASURES}}}}

    report = daily_check.run(fetch)
    assert report["verdict"] in ("passed", "pending")
    assert report["pools"][0]["passed"] is True
    assert report["pools"][0]["failures"] == []


def test_never_filled_backfill_from_binding_start_and_retention_bound(tmp_path):
    store = make(tmp_path)
    days = _weekdays(5)
    start_at = f"{days[0].isoformat()}T00:00:00Z"
    hb = _event("heartbeat", created_at=f"{days[-1].isoformat()}T06:00:00Z")
    hb.update(runtime_id="rt-3252fa75", event_id="hb-day5")
    snap = _idle_state_snapshot(
        days[-1],
        created=f"{days[-1].isoformat()}T06:00:01Z",
        effective_at=start_at,
    )
    store.project_event(hb)
    store.project_event(snap)

    rows = rows_of(store, "rt-3252fa75")
    assert [r["as_of"] for r in rows] == [d.isoformat() for d in days]
    for r in rows:
        assert r["equity"] == 100000.0
        assert r["equity_basis"] == "flat_never_filled_initial_cash"
        assert r["gross_exposure"] == 0.0
        assert r["gross_limit"] == 0.10
        assert r["policy_ref"]["basis"] == "runtime_state_snapshot"

    # Backfill from >400 trading days ago is bounded by RETENTION_TRADING_DAYS (400).
    capped_store = RuntimeSummaryProjectionStore(path=str(tmp_path / "capped.json"))
    capped_snap = _idle_state_snapshot(
        days[-1],
        runtime="rt-capped",
        created=f"{days[-1].isoformat()}T06:00:00Z",
        effective_at="2023-01-02T00:00:00Z",
    )
    summary = capped_store.project_event(capped_snap)
    assert len(summary[prs.ROWS_KEY]) == prs.RETENTION_TRADING_DAYS
    assert capped_store.get("rt-capped")["pool_risk_snapshot_retained_days"] == prs.RETENTION_TRADING_DAYS


def test_idle_facts_replay_idempotence_and_bounded_replacement(tmp_path):
    store = make(tmp_path)
    days = _weekdays(3)
    events = [
        _idle_state_snapshot(days[0], created=f"{days[0].isoformat()}T01:00:00Z", effective_at=f"{days[0].isoformat()}T00:00:00Z"),
        {**_event("heartbeat", created_at=f"{days[0].isoformat()}T06:00:00Z"), "runtime_id": "rt-3252fa75", "event_id": "hb-0"},
        {**_event("heartbeat", created_at=f"{days[1].isoformat()}T06:00:00Z"), "runtime_id": "rt-3252fa75", "event_id": "hb-1"},
        {**_event("heartbeat", created_at=f"{days[2].isoformat()}T06:00:00Z"), "runtime_id": "rt-3252fa75", "event_id": "hb-2"},
    ]
    for ev in events:
        raw_before = store.project_event(ev)
    served_before = store.get("rt-3252fa75")

    # Replay the exact same events (and with a duplicate event_id): summary and rows stay unchanged.
    for ev in events + [{**events[0], "event_id": "rss-replay"}]:
        raw_after = store.project_event(ev)
    served_after = store.get("rt-3252fa75")

    assert raw_before[prs.ACCOUNT_KEY] == raw_after[prs.ACCOUNT_KEY]
    assert raw_before[prs.POLICY_KEY] == raw_after[prs.POLICY_KEY]
    assert raw_before[prs.ROWS_KEY] == raw_after[prs.ROWS_KEY]
    assert served_before["pool_risk_snapshots"] == served_after["pool_risk_snapshots"]
    assert served_before["pool_observations"] == served_after["pool_observations"]

    # A new snapshot replaces account facts and policy facts in place (never appended).
    updated = _idle_state_snapshot(
        days[2],
        created=f"{days[2].isoformat()}T07:00:00Z",
        initial_cash=100000.0,
        cash=99500.0,
        fill_count=2,
        limit=0.20,
        digest="f" * 64,
    )
    raw_updated = store.project_event(updated)
    assert set(raw_updated[prs.ACCOUNT_KEY].keys()) <= {"initial_cash", "cash", "fill_count", "as_of"}
    assert raw_updated[prs.ACCOUNT_KEY]["cash"] == 99500.0
    assert raw_updated[prs.ACCOUNT_KEY]["fill_count"] == 2
    assert set(raw_updated[prs.POLICY_KEY].keys()) <= set(prs._POLICY_FACT_KEYS) | {"_basis"}
    assert raw_updated[prs.POLICY_KEY]["gross_limit"] == 0.20


def test_owner_valuation_wins_over_idle_row_and_filled_pool_writes_no_row_before_account_facts(tmp_path):
    store = make(tmp_path)
    days = _weekdays(3)
    # Day 2: idle row built from runtime_state_snapshot + heartbeat.
    store.project_event(_idle_state_snapshot(days[2], runtime="rt-paper-001", created=f"{days[2].isoformat()}T06:00:00Z"))
    idle_row = rows_of(store, "rt-paper-001")[-1]
    assert idle_row["equity"] == 100000.0 and idle_row["equity_basis"] == "flat_never_filled_initial_cash"

    # Later on Day 2, a known owner valuation arrives and wins over the idle row.
    val = valuation(days[2], 101250.0, runtime="rt-paper-001", created=f"{days[2].isoformat()}T07:00:00Z")
    store.project_event(val)
    valued_row = rows_of(store, "rt-paper-001")[-1]
    assert valued_row["as_of"] == days[2].isoformat()
    assert valued_row["equity"] == 101250.0
    assert valued_row["equity_basis"] == "owner_valuation"
    assert valued_row["previous_digest"] == idle_row["digest"]
    assert valued_row["revision"] == 2

    # Flat pool WITH fills: keeps flat_cash_ledger and writes no row for a day before the account facts existed.
    filled_store = RuntimeSummaryProjectionStore(path=str(tmp_path / "filled.json"))
    # Heartbeat on days[0] before account facts existed.
    hb_early = _event("heartbeat", created_at=f"{days[0].isoformat()}T06:00:00Z")
    hb_early.update(runtime_id="rt-3b65a843", event_id="hb-early")
    filled_store.project_event(hb_early)
    # Two fills on days[1] that open and close a position (flat after 2 fills).
    for idx, (qty, action, cash_after) in enumerate([(10, "buy", 99000.0), (-10, "sell", 99990.01)], start=1):
        fill = _event("paper_fill_simulated", created_at=f"{days[1].isoformat()}T02:0{idx}:00Z")
        fill.update(runtime_id="rt-3b65a843", event_id=f"fill-{idx}")
        fill["metrics"] = {"fill_quantity": qty, "fill_price": 100.0, "action": action, "submitted_to_broker": True}
        fill["metadata"].update({"symbol": "2330.TW", "ledger_fill_count": idx, "ledger_cash": cash_after})
        filled_store.project_event(fill)
    # Account facts arrive on days[2] after close.
    filled_snap = _idle_state_snapshot(
        days[2],
        runtime="rt-3b65a843",
        created=f"{days[2].isoformat()}T06:00:00Z",
        initial_cash=100000.0,
        cash=99990.01,
        fill_count=2,
        limit=0.20,
    )
    raw_filled = filled_store.project_event(filled_snap)
    # Replaying an earlier heartbeat from days[1] (before account facts existed on days[2]) writes no row for days[1].
    hb_before_acc = _event("heartbeat", created_at=f"{days[1].isoformat()}T06:00:00Z")
    hb_before_acc.update(runtime_id="rt-3b65a843", event_id="hb-before-acc")
    raw_filled = filled_store.project_event(hb_before_acc)

    filled_rows = filled_store.get("rt-3b65a843")["pool_risk_snapshots"]
    assert [r["as_of"] for r in filled_rows] == [days[2].isoformat()]
    assert [r["as_of"] for r in raw_filled[prs.ROWS_KEY]] == [days[2].isoformat()]
    assert filled_rows[0]["equity"] == 99990.01
    assert filled_rows[0]["cash"] == 99990.01
    assert filled_rows[0]["equity_basis"] == "flat_cash_ledger"
    assert filled_rows[0]["gross_exposure"] == 0.0
    assert filled_rows[0]["gross_limit"] == 0.20
    assert filled_rows[0]["policy_ref"]["basis"] == "runtime_state_snapshot"

    # A pool with an open position that has no mark stays null with its existing reason even when account_facts exist.
    open_store = RuntimeSummaryProjectionStore(path=str(tmp_path / "open.json"))
    open_fill = _event("paper_fill_simulated", created_at=f"{days[2].isoformat()}T02:00:00Z")
    open_fill.update(runtime_id="rt-open", event_id="fill-open")
    open_fill["metrics"] = {"fill_quantity": 10, "fill_price": 100.0, "action": "buy", "submitted_to_broker": True}
    open_fill["metadata"].update({"symbol": "2330.TW", "ledger_fill_count": 1, "ledger_cash": 99000.0})
    open_store.project_event(open_fill)
    open_snap = _idle_state_snapshot(
        days[2],
        runtime="rt-open",
        created=f"{days[2].isoformat()}T06:00:00Z",
        initial_cash=100000.0,
        cash=99000.0,
        fill_count=1,
        limit=0.20,
    )
    open_store.project_event(open_snap)
    open_row = open_store.get("rt-open")["pool_risk_snapshots"][0]
    assert open_row["equity"] is None and open_row["gross_exposure"] is None
    assert open_row["nulls"]["equity"] == "no_known_owner_valuation"
