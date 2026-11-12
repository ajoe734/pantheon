"""Out-of-sample pool admission on governed, observed daily bars.

Replays the existing registry interpreter and immutable envelope netting, on
underlying exposure-fraction returns with Taiwan costs. This is the virtual
book basis, deliberately distinct from the gate's share-sized Sharpe. The
member gate itself is verified separately against the registry owner's proof.
No stub backend, invented returns, or execution-side writes exist here.
"""
from __future__ import annotations

import math
import statistics

from services.execution.lean_runtime.member_virtual_book import NET_BASIS, _net_interval_costs
from services.registry.portfolio_envelope import build_portfolio_envelope, required_portfolio_history
from services.registry.strategy_artifact import evaluate_strategy_view
from services.research.vectorbt.adapter.vectorbt_adapter import (
    GovernedVectorbtInputAdapter, _bar_features_for_index,
)


def _replay(prepared, artifact, dates):
    """Same dated inputs/interpreter/netting as paper, previous-bar exposure only."""
    from services.execution.shioaji_adapter import normalize_taiwan_symbol

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
            rows.append({"exposure": exposure, "price": prices[index]})
        costs = _net_interval_costs(symbol, rows)
        # Include opening and final liquidation costs in the untouched window.
        returns = []
        for i, (prior, row) in enumerate(zip(rows, rows[1:]), 1):
            value = prior["exposure"] * (row["price"] / prior["price"] - 1) - costs[i]
            if i == 1:
                value -= costs[0]
            if i == len(rows) - 1:
                value -= _net_interval_costs(symbol, [rows[-1], {"exposure": 0}])[-1]
            if not math.isfinite(value) or value <= -1:
                raise ValueError("backtest_return_invalid")
            returns.append(value)
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
    before = _sharpe(_replay(prepared, _envelope(current, entries), dates), policy.periods_per_year)
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
    return {"admitted": not reasons, "reasons": reasons, "correlations": correlations,
            "pool_oos_sharpe": before, "combined_oos_sharpe": after, "candidate_oos_sharpe": candidate_sharpe,
            "persona_trial_count": trial_count, "sharpe_haircut": haircut, "required_candidate_sharpe": threshold,
            "return_basis": NET_BASIS, "periods_per_year": policy.periods_per_year,
            "oos_start": dates[0], "oos_end": dates[-1], "return_observations": len(dates) - 1,
            "dataset_id": prepared.dataset_id, "source_dataset_refs": list(prepared.source_dataset_refs)}
