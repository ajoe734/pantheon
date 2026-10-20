# PAPER-DRAWDOWN-BASELINE-PROPOSAL-20261020 — research expected_drawdown proposal

Status: **research proposal only** — not approved, not published, not live config.
Proposer: Claude. Reviewer: Antigravity. Machine-readable twin: `evidence.json`.

## Proposal

| Field | Value |
| --- | --- |
| Subject artifact | `artifact-persona-paper-0b4aedc1da04dba924d8@1.0.0` (strategy `strategy-persona-0b4aedc1da04dba924d8`, registry checksum `sha256:915d33f404097200ec51f62e56af3af49e90ea71e11cb7ffd0d7caef4c762c73`) |
| Proposed `expected_drawdown` | **0.0027** (fractional, 20-calendar-day rolling high-water drawdown) |
| Observed maximum (unrounded) | `0.002697706949093271` on 2026-09-16 |
| Rounding rule | `ceil(max_observed * 10_000) / 10_000` — outward to one basis point, so the stored expectation is never below the observed research maximum and is strictly positive whenever any drawdown was observed. Same rule as the EVOLOOP-005 precedent; the rule, not its number, is reused. |

For information only: the governed breach multiple (EVOLUTION_REVIEW_AND_THRESHOLDS.md §7.1, 1.25x) would put the
trigger at 0.003375 on this baseline. The 1.25 threshold is **not** altered and not baked into the proposal.

## Strategy and ledger assumptions (from production source at `e72ae033b38cb808d19df9cca12127a749ba437c`)

- Strategy: `services.registry.strategy_artifact:evaluate_strategy_action` — close-to-close momentum, `lookback_bars=2`,
  `momentum_threshold=0.0`, BUY if momentum > threshold else SELL (`zero_momentum_action=SELL`), `order_quantity=1`
  SHARES, symbol `2330.TW`, daily bars. The artifact JSON from the sanitized input is passed to the interpreter unchanged.
- Fill model (Taiwan path, `paper_runtime.SubmitTaiwanBrokerOrder` + `broker/paper_simulation`): MARKET order fills at the
  admitted market price (the bar close); **no fee, tax, commission or slippage is modelled** — fills are gross.
  SELL with no long position is accepted and produces a negative holding (short), cash is credited; there is no
  shorting restriction in the ledger. Currency label is `TWD`; ledger scale is `initial_cash = 100000.0` (the
  `PaperExecutionAlgorithm` default). No fee/currency claims beyond this are made.
- One action and one 1-share fill per bar starting at the 2nd close (lookback 2); every fill is marked at that bar's
  close and valued with `performance_telemetry.value_portfolio`; drawdown comes from
  `RollingDrawdownTracker(window_days=20)` seeded with `initial_cash`.
- Bar timestamps: production marks carry an RFC3339 time; the saved bars only carry dates. As-of was set to 05:30:00Z
  (13:30 Taipei close). Re-running with 00:00:00Z gives a byte-identical series, since only day spacing matters to the
  20-calendar-day window.

## Observed research window

| Item | Value |
| --- | --- |
| Input bars | 40 daily bars 2026-08-18 .. 2026-10-15 (dated official TWSE/TPEX OHLCV, dataset `dataset:dsv-602cce23916f1b0d380f054e`) |
| Valued observations / fills | 39 (2026-08-19 .. 2026-10-15) / 39 (19 BUY, 20 SELL); final position -1 share |
| Cumulative PnL (TWD, gross) | max 255.0, min -185.0, final -15.0 |
| Max 20-calendar-day drawdown | 0.002697706949093271 (2026-09-16); later plateau 0.002693 (2026-10-13..15) |
| Series SHA-256 | `0cb91c67c4fb499ea19180ab8dca19e0f3693419c5788f0805db4bb8c01b9ff3` |

Interpretation limits: this is a **new offline research calculation on 40 saved bars**, not a hosted result, not
observed hosted days, not same-strategy live/paper validation, and not a profit claim (no positive-PnL requirement
exists and none is asserted). The value is small because the ledger trades 1 share against a 100,000 TWD base; it is
faithful to the actual paper ledger scale, and the reviewer should weigh that scale, not treat 0.0027 as a risk-model
output for any other sizing. The EVOLOOP-005 value 0.0303 belongs to a different 3-symbol artifact and was not used;
no prior VectorBT metrics and no 1.25 multiplier were used in the calculation.

## Identity, hashes, reproduction

| Input | SHA-256 |
| --- | --- |
| Sanitized input JSON (`/tmp/pantheon-assist-20261013/PAPER-DRAWDOWN-BASELINE-PROPOSAL-20261020-sanitized-input.json`) | `467a7bf6eae1f950b46b25dfbd1ee7251ffbd3b7bce927567c7732d0feb8bea3` |
| Canonical OHLCV (as recorded in the input) | `01c1dbf12219a4b32071c1849ef5ea9fc1bb39b42f7277fb645e1d4870801994` |
| Registry source receipt | `149e3e552894d9aa5ab592efacd827346b88fbc018a1445ccfb052b4e0d617b5` |
| `services/registry/strategy_artifact.py` @ e72ae033 | `9da2ebb36e69e349c78dfccc55b5ef2c402f083527a137c0db57a45b365f2c57` |
| `services/execution/lean_runtime/performance_telemetry.py` @ e72ae033 | `664f6aa766e1ed5526e1c3dbb22480d7622c853c52b83989e7d8117bb5df6ebb` |
| Replay script below (`calc.py`) | `966b31ed2b7ad006bdc31361321351e728c4e5f5ae1b4e68ce0f0a45875f3d55` |

The canonical OHLCV hash and the bar bytes' provenance are taken from the supplied input; this task did not
re-derive that hash (the canonicalization is not in scope) and did not contact any source.

Reproduce (no network, no credentials):

```
mkdir e72 && git archive e72ae033b38cb808d19df9cca12127a749ba437c services | tar -x -C e72
python3 -I calc.py ./e72 <sanitized-input.json> [05:30:00]
```

`calc.py` (verbatim):

```python
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
```

## Artifact identity lifecycle (obligations before any publication)

This proposal is bound to the artifact id above, the e72ae033 execution code, those exact parameters, and that exact
input window. A later software release may register a **different** artifact id and source commit; the value then
does **not** transfer automatically. Before any later publication of an `expected_drawdown` for a new id, root/
reviewer must verify and record: (1) the new artifact's executable parameters and strategy logic are identical to
those here (or the replay is repeated for the new ones); (2) the production interpreter, valuation and drawdown code
at the new commit still match the hashes above, or the replay is repeated against them; (3) the eligible input scope
(symbol, bars, market-data-only reuse, `learning_eligible=false`) still holds; (4) the legitimate existing policy
approval path (§6.1 low-risk path, Reviewer on Duty) has approved the exact value. Root alone binds a final target
after actual software release through the existing governed policy path; this evidence is not a deploy gate.

## Not done / not changed

No change to `services/evolution/config`, `policy_source` approval, the 1.25 threshold, PnL activation, or runtime/
calibration services. No VM/SSH/credentials/HTTP/provider/Research/backtest/original-case replay. Only these two
evidence files are added.
