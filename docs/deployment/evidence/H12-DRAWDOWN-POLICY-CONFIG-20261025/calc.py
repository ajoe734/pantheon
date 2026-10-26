"""Offline replay of the exact paper artifact over saved bars, using e72ae033 production functions.
Usage: python3 -I calc.py <e72_tree_root> <sanitized_input.json> [as_of_time_utc HH:MM:SS] [--series out.json]
"""
import json, sys, hashlib
from datetime import datetime, timezone
from math import ceil

root, inp = sys.argv[1], sys.argv[2]
tod = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else "05:30:00"
sys.path.insert(0, root)
from services.registry.strategy_artifact import evaluate_strategy_action
from services.execution.lean_runtime.performance_telemetry import (
    MarketMark, RollingDrawdownTracker, value_portfolio)

d = json.load(open(inp))
art = d["actual_current_artifact"]
bars = d["market_input"]["records"]
dates = [b["date"] for b in bars]
assert dates == sorted(set(dates)), "bars must be strictly ascending, unique"
sym = art["parameters"]["symbols"][0]
INIT = 100000.0
qty_unit = art["parameters"]["order_quantity"]
cash, pos, fills = INIT, 0.0, 0
tracker = RollingDrawdownTracker(window_days=20)
closes, obs = [], []
lookback = art["parameters"]["lookback_bars"]
for b in bars:
    closes.append(b["close"])
    if len(closes) < lookback:
        continue
    action = evaluate_strategy_action(art, closes)
    signed = qty_unit if action == "BUY" else -qty_unit
    cash -= signed * b["close"]; pos += signed; fills += 1
    as_of = datetime.fromisoformat(f"{b['date']}T{tod}+00:00").astimezone(timezone.utc)
    iso = as_of.strftime("%Y-%m-%dT%H:%M:%SZ")
    mark = MarketMark(symbol=sym, price=b["close"], as_of=iso, source_ref="saved-ohlcv:" + b["date"])
    res = value_portfolio(initial_cash=INIT, cash=cash, positions=[{"symbol": sym, "quantity": pos}],
                          marks={sym: mark}, fill_count=fills, last_fill_at=iso, now=as_of)
    assert res.status == "valued", res.diagnostic
    s = res.sample
    dd = tracker.observe(s, initial_equity_as_of=iso)
    obs.append({"date": b["date"], "action": action, "close": b["close"], "position": pos,
                "cash": cash, "portfolio_value": s.portfolio_value, "pnl": s.pnl,
                "drawdown": dd["drawdown_pct"] if dd else None,
                "window_observations": dd["window_observations"] if dd else None})
dds = [o["drawdown"] for o in obs if o["drawdown"] is not None]
mx = max(dds)
out = {"as_of_time_utc": tod, "bars": len(bars), "first_bar": dates[0], "last_bar": dates[-1],
       "observations": len(obs), "first_obs": obs[0]["date"], "last_obs": obs[-1]["date"], "fills": fills,
       "none_dd_count": len(obs) - len(dds),
       "actions": {a: sum(o["action"] == a for o in obs) for a in ("BUY", "SELL")},
       "final_position": pos, "max_pnl": max(o["pnl"] for o in obs), "min_pnl": min(o["pnl"] for o in obs),
       "final_pnl": obs[-1]["pnl"], "max_drawdown": mx,
       "max_dd_date": next(o["date"] for o in obs if o["drawdown"] == mx),
       "ceil_bp": ceil(mx * 10000) / 10000,
       "series_sha256": hashlib.sha256(json.dumps(obs, sort_keys=True).encode()).hexdigest()}
print(json.dumps(out, indent=1))
if "--series" in sys.argv:
    json.dump(obs, open(sys.argv[sys.argv.index("--series") + 1], "w"), indent=1)
