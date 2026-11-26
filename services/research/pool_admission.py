"""Out-of-sample pool admission on governed, observed daily bars.

Daily decisions follow the shared convention: computed from the close of session D, filled at the open of D+1
(``member_virtual_book.next_open_net_returns``); a session without an open gives no fill, never a close fill.
Replays the existing registry interpreter and immutable envelope netting, on
underlying exposure-fraction returns with Taiwan costs. This is the virtual
book basis, deliberately distinct from the gate's share-sized Sharpe. The
member gate itself is verified separately against the registry owner's proof.
No stub backend, invented returns, or execution-side writes exist here.
"""
from __future__ import annotations

import math
import statistics

from services.execution.lean_runtime.member_virtual_book import FILL_CONVENTION, NET_BASIS, next_open_net_returns
from services.registry.portfolio_envelope import build_portfolio_envelope, required_portfolio_history
from services.registry.strategy_artifact import evaluate_strategy_view
from services.research.vectorbt.adapter.vectorbt_adapter import (
    GovernedVectorbtInputAdapter, _bar_features_for_index,
)


def _replay(prepared, artifact, dates, fills_out=None, rows_out=None):
    """Same dated inputs/interpreter/netting as paper, previous-bar exposure only.

    ``fills_out`` (optional list) receives every per-session fill of the daily next-open replay.
    ``rows_out`` (optional list) receives every per-symbol session row the replay decided from the
    close (``symbol, date, exposure, direction, open, close``); the member virtual book admission
    fill reads them so it never runs a second replay.  Target-position artifacts decide portfolio
    targets, not per-symbol views, and add no row.
    """
    from services.execution.shioaji_adapter import normalize_taiwan_symbol

    from services.registry.portfolio_envelope import uses_target_positions
    if uses_target_positions(artifact):
        from services.research.vectorbt.adapter.vectorbt_adapter import _portfolio_target_sequence
        replay = _portfolio_target_sequence(prepared, artifact)
        calendar = list(prepared.dates_by_instrument[prepared.instruments[0]])
        indexes = [calendar.index(day) for day in dates]
        if any(replay["bars"][i] is None for i in indexes):
            raise ValueError("backtest_warmup_missing")
        return [replay["returns"][i] for i in indexes[1:]]
    if "screen" in artifact.get("strategy_logic", {}):
        from services.research.vectorbt.adapter.vectorbt_adapter import _screened_artifact_view_sequence
        sequences = _screened_artifact_view_sequence(prepared, artifact)
        series = []
        for symbol, views in sequences.items():
            calendar = list(prepared.dates_by_instrument[symbol])
            if not set(dates) <= set(calendar):
                raise ValueError("backtest_dates_missing_or_duplicate")
            rows = []
            strength = 0.0
            for index, view in enumerate(views):
                direction = view.get("target_direction", view["direction"])
                if view.get("exit_reason"):
                    direction = "FLAT"
                if view["action"] != "HOLD":
                    strength = 1.0 if view["strength"] is None else view["strength"]
                if direction == "FLAT":
                    strength = 0.0
                if calendar[index] in dates:
                    rows.append({"exposure": {"LONG": 1, "SHORT": -1, "FLAT": 0}[direction] * strength,
                                 "open": prepared.ohlcv_by_instrument[symbol][index][0],
                                 "close": prepared.ohlcv_by_instrument[symbol][index][3], "date": calendar[index],
                                 "direction": {"LONG": 1, "SHORT": -1, "FLAT": 0}[direction]})
            if rows_out is not None:
                rows_out.extend({"symbol": symbol, **row} for row in rows)
            values, fills = next_open_net_returns(symbol, rows, liquidate_final=True)
            if fills_out is not None:
                fills_out.extend({"symbol": symbol, **fill} for fill in fills)
            if any(not math.isfinite(v) or v <= -1 for v in values):
                raise ValueError("backtest_return_invalid")
            series.append(values)
        return [statistics.mean(values) for values in zip(*series)]
    wanted = {normalize_taiwan_symbol(s) for s in artifact["parameters"]["symbols"]}
    instruments = [s for s in prepared.instruments if normalize_taiwan_symbol(s) in wanted]
    if {normalize_taiwan_symbol(s) for s in instruments} != wanted:
        raise ValueError("backtest_member_symbols_missing")
    series = []
    for symbol in instruments:
        all_dates = list(prepared.dates_by_instrument[symbol])
        if len(set(all_dates)) != len(all_dates) or not set(dates) <= set(all_dates):
            raise ValueError("backtest_dates_missing_or_duplicate")
        prices = [bar[3] for bar in prepared.ohlcv_by_instrument[symbol]]
        opens = [bar[0] for bar in prepared.ohlcv_by_instrument[symbol]]
        rows = []
        for day in dates:
            index = all_dates.index(day)
            if index + 1 < required_portfolio_history(artifact):
                raise ValueError("backtest_warmup_missing")
            features, decision_time = _bar_features_for_index(prepared, symbol, index)
            view = evaluate_strategy_view(artifact, prices[:index + 1], features=features,
                                          decision_time=decision_time, symbol=symbol)
            strength = 1.0 if view["strength"] is None else view["strength"]
            exposure = {"LONG": 1, "SHORT": -1, "FLAT": 0}[view["direction"]] * strength
            rows.append({"exposure": exposure, "open": opens[index], "close": prices[index], "date": day,
                         "direction": {"LONG": 1, "SHORT": -1, "FLAT": 0}[view["direction"]]})
        if rows_out is not None:
            rows_out.extend({"symbol": symbol, **row} for row in rows)
        # A decision from day D's close fills at day D+1's open; the untouched window's final
        # position is marked out at the last close with its liquidation costs.
        returns, fills = next_open_net_returns(symbol, rows, liquidate_final=True)
        if fills_out is not None:
            fills_out.extend({"symbol": symbol, **fill} for fill in fills)
        if any(not math.isfinite(v) or v <= -1 for v in returns):
            raise ValueError("backtest_return_invalid")
        series.append(returns)
    return [statistics.mean(values) for values in zip(*series)]


def _sharpe(values, periods):
    deviation = statistics.stdev(values) if len(values) > 1 else 0
    if deviation <= 0:
        raise ValueError("backtest_sharpe_undefined")
    return statistics.mean(values) / deviation * math.sqrt(periods)


def _envelope(members, entries):
    return build_portfolio_envelope(
        artifact_id="pool-admission-backtest", strategy_id="pool-admission-backtest", version="1.0.0",
        members=[{"member_id": m["artifact_ref"], "artifact": entries[m["artifact_ref"]]["metadata"]["strategy_artifact"],
                  "risk_weight": m["risk_weight"]} for m in members if m["stage"] != "removed"],
    )


def evaluate_admission(*, dataset, current, proposed, candidate, entries, trial_count, policy):
    """No utilisation argument: idle capital cannot affect any admission criterion."""
    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    if prepared.data_frequency not in ("daily", "1d"):
        raise ValueError("backtest_requires_daily_bars")
    calendars = [tuple(prepared.dates_by_instrument[s]) for s in prepared.instruments]
    if len(set(calendars)) != 1:
        raise ValueError("backtest_windows_not_aligned")
    if any(not math.isfinite(v) or v <= 0 for bars in prepared.ohlcv_by_instrument.values() for bar in bars for v in bar[:4]):
        raise ValueError("backtest_prices_invalid")
    if any(isinstance(row.get(key), bool) for row in dataset["records"] for key in ("open", "high", "low", "close", "volume")):
        raise ValueError("backtest_boolean_observations_invalid")
    if any(not math.isfinite(bar[4]) or bar[4] < 0 for bars in prepared.ohlcv_by_instrument.values() for bar in bars):
        raise ValueError("backtest_volume_invalid")
    all_dates = calendars[0]
    dates = all_dates[int(len(all_dates) * policy.in_sample_fraction):]
    if len(dates) - 1 < policy.minimum_oos_days:
        raise ValueError("backtest_out_of_sample_history_missing")
    candidate_returns = _replay(prepared, entries[candidate["artifact_ref"]]["metadata"]["strategy_artifact"], dates)
    correlations = {}
    for member in current:
        if member["stage"] != "active":
            continue
        returns = _replay(prepared, entries[member["artifact_ref"]]["metadata"]["strategy_artifact"], dates)
        if statistics.stdev(returns) <= 0 or statistics.stdev(candidate_returns) <= 0:
            raise ValueError("backtest_correlation_undefined")
        correlations[member["artifact_ref"]] = statistics.correlation(returns, candidate_returns)
    # An idle pool (no member that is not removed) has no return stream: its Sharpe is 0 by
    # definition, so "combined better than the pool alone" means the combined result must clear the
    # minimum improvement on its own out of sample; nothing is waived and no criterion is skipped.
    idle = not any(m["stage"] != "removed" for m in current)
    before = 0.0 if idle else _sharpe(_replay(prepared, _envelope(current, entries), dates), policy.periods_per_year)
    after = _sharpe(_replay(prepared, _envelope(proposed, entries), dates), policy.periods_per_year)
    candidate_sharpe = _sharpe(candidate_returns, policy.periods_per_year)
    haircut = policy.trial_penalty_scale * math.sqrt(2 * math.log(trial_count))
    threshold = policy.base_sharpe_threshold + haircut
    reasons = []
    if any(value >= policy.correlation_cutoff for value in correlations.values()):
        reasons.append("candidate_correlated_with_active_member")
    if after - before <= policy.minimum_sharpe_improvement:
        reasons.append("combined_oos_sharpe_did_not_improve")
    if candidate_sharpe <= threshold:
        reasons.append("candidate_oos_sharpe_below_persona_trial_threshold")
    from services.registry.portfolio_envelope import uses_target_positions
    target_basis = "portfolio_target_position_cash_net_taiwan_costs"
    before_basis = NET_BASIS if idle else (
        target_basis if uses_target_positions(_envelope(current, entries)) else NET_BASIS)
    after_basis = target_basis if uses_target_positions(_envelope(proposed, entries)) else NET_BASIS
    return {"admitted": not reasons, "reasons": reasons, "correlations": correlations,
            "pool_oos_sharpe": before, "pool_idle": idle, "combined_oos_sharpe": after, "candidate_oos_sharpe": candidate_sharpe,
            "persona_trial_count": trial_count, "sharpe_haircut": haircut, "required_candidate_sharpe": threshold,
            "fill_convention": FILL_CONVENTION, "return_basis": after_basis, "pool_oos_return_basis": before_basis,
            "combined_oos_return_basis": after_basis, "candidate_oos_return_basis": NET_BASIS,
            "periods_per_year": policy.periods_per_year,
            "oos_start": dates[0], "oos_end": dates[-1], "return_observations": len(dates) - 1,
            "dataset_id": prepared.dataset_id, "source_dataset_refs": list(prepared.source_dataset_refs)}
