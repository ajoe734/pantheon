# Paper ledger accounting audit — 2026-11-15

Task: PAPER-LEDGER-ACCOUNTING-AUDIT-20261115. Report only; no production code changed.
Traced tree: dev head 40f024389 (descendant of the claimed 04613fa66; the cited files were re-read at this head and line numbers below are from it).
Reproduction: `PYTHONPATH=. python3 <script>` using the repro in the evidence file (commands recorded in `docs/deployment/evidence/PAPER-LEDGER-ACCOUNTING-AUDIT-20261115/evidence.json`).

## Limit of this report
Workers have no hosted shell. Acceptance 3 (per-persona quarter-return / ranking effect from hosted read-only data) is **not done**: only the analytic size of each effect is given below. The coordinator must supply hosted readback (see "Hosted readback needed"). No number below is a hosted measurement.

## How the ledger feeds ranking
`PaperExecutionAlgorithm` (`services/execution/lean_runtime/paper_runtime.py`) is constructed in production at `paper_runtime.py:5177` with no `initial_cash`, no `risk_policy`, no `risk_policy_loader`. Ranking reads `initial_cash` and `pnl` from the telemetry snapshot (`services/control-plane/bff/personas/service.py:13891`, return = pnl / initial_cash).

## Claim results

| # | Claim | Verdict |
|---|-------|---------|
| 1 | Ledger starts at 100000 regardless of pool budget; instrument layer sizes from pool budget before first fill | **Confirmed** |
| 2 | Cash-share fills carry no commission or sell tax; member books and gate charge them | **Confirmed** |
| 3 | Unreadable capital risk policy => gate uses a policy with no limits | **Confirmed** (see nuance) |
| 4 | No cash sufficiency check for cash shares | **Confirmed** |
| 5 | Taiwan daily orders fill at close of decision bar | **Confirmed for daily bars; refuted for intraday bars** |
| 6 | notional_target_v1: flat or weaker view neither closes nor trims | **Confirmed** |
| 7 | Every producer signal waits three drain ticks; flush_rebalance has no caller | **Confirmed** |

### 1. Starting cash — confirmed
- `paper_runtime.py:353` default `initial_cash=100_000.0`; `:375-380` sets `_cash`. Constructor call at `:5177` omits it, so every hosted runtime starts at 100000 whatever its pool.
- `expression_inputs.py:353-361`: until `_ledger_has_fills(summary)`, `cash = pool_budget`; afterwards `cash = ledger_cash`. A pool with budget 1,000,000 sizes the first order against 1,000,000 then the ledger reports against 100,000.
- `paper_runtime.py:2729` `target_percent` sizing also uses `self._initial_cash`.
- Effect: return denominator (`service.py:13891`) is 100000 regardless of pool; first-order size can be up to budget/100000 times what the ledger holds (combined with claim 4 this is accepted silently).
- Owner: `paper_runtime.py` (construction at :5177) and `expression_inputs.py`.
- Smallest fix: construct the algorithm with `initial_cash` = pool budget read from the capital pool; fail closed with a recorded reason when the pool budget is missing. **Changes reported performance: operator decision.**

### 2. Costs — confirmed
- Cash-share path: `paper_runtime.py:1934-1936` `next_cash = cash - signed_fill_qty * fill_price`; no fee or tax. Fee/tax computed only for futures (`:1549-1560` / `:2142`, `compute_open_or_close_cost`). `broker/paper_simulation.py:206-207` takes `fee`/`tax` defaults 0.0 and the Taiwan caller does not pass them.
- `member_virtual_book.py:18-20` charges commission 0.1425% on every exposure change and sell tax 0.3% (0.1% for codes starting `00`).
- Reproduced: buy 1000 @1000 then sell 1000 @1000 returns cash exactly 100000.0 (pnl 0). Statutory round trip would cost 0.585% of notional for stocks (2330, 2317) and 0.385% for ETF 0050 (commission 2 x 0.1425% + sell tax).
- Effect (analytic): each full round trip overstates quarter return by 0.585 pp (青禾 2330, 澄川 2317) or 0.385 pp (衡岳 0050) of traded notional per round trip; scales with turnover. Hosted turnover needed to give the persona-level figure.
- Owner: `paper_runtime.py` (cash-share branch, `_commit_fill`).
- Smallest fix: apply the same rates (shared constant with member_virtual_book) to cash fills, debit cost from cash and add to `costs`. **Changes reported performance: operator decision.**

### 3. Risk policy fallback — confirmed
- `paper_runtime.py:1009-1032`: when `resolve_bound_pool_risk_policy` returns None (no pool ref, no record, no env ref), returns `RiskPolicy(risk_policy_id="paper-unbound")`. Reproduced: all of `gross_limit`, `net_limit`, `max_single_name_weight`, `max_leverage` are None; `risk_policy.py:85` states missing limits mean no check.
- Nuance: if the reader raises, the exception is not caught in `_resolve_active_risk_policy` (it propagates), so only the "resolved nothing" case falls to unbound. The expression layer (`expression_inputs.py:339-340,383-386`) is fail-closed (raises `ExpressionInputsUnavailable`) but the order gate is not; the two disagree.
- Effect: no effect on return directly; removes the cap that bounds claims 1 and 4. Hosted check needed: does each persona's pool resolve a policy.
- Smallest fix: unresolved policy rejects the order with a recorded reason. Does not change reported performance for orders that stay within a policy; orders that were previously over-limit would now be rejected (operator should be told).

### 4. Cash sufficiency — confirmed
- Cash branch at `paper_runtime.py:1348-1349,1372` only computes equity-after for leverage ratios; no `cash_after >= 0` check; `insufficient_initial_margin` (`:1438`) is futures only.
- Reproduced: one buy of 1,000,000 notional against 100,000 cash filled (`paper_fill_simulated`), cash = -900000.0.
- Effect: leveraged long exposure with no financing cost; return per unit capital inflated in a rising market, deflated in a falling one.
- Owner: `paper_runtime.py` risk-gate pre-check. Smallest fix: reject buys whose `cash_after < 0` (reason `insufficient_cash`). **Changes reported performance: operator decision.**

### 5. Fill timing — daily confirmed, intraday refuted
- Daily path: fill price is the signal's `market_price` (`executor.py:466-472`), set from the decision-bar close (`paper_signal_producer.py:1036` `market_data.close = closes[-1]`); `paper_simulation.py:186-190` fills at that price. Decision and fill are the same bar close (look-ahead for any rule that used that close).
- Intraday: `paper_signal_producer.py:1044-1051,2398-2410,2476-2489` park the decision as `pending_settlement` and fill at the reconciled D+1 first-bar open (`settlement_mode=delayed_simulated`). So the claim does not hold there.
- Which path the three personas use depends on their bar frequency: hosted readback needed (`market_input_bar_frequency`).
- Effect: not quantifiable without hosted bars; bias is that a daily decision enjoys the same close it was computed from.
- Smallest fix: daily path settles at the next session open like the intraday path. **Changes reported performance: operator decision.**

### 6. notional_target_v1 reduce/exit — confirmed
- `expression_policy.py:269-271`: direction not LONG/SHORT returns `no_trade("flat_view")` before any open position is looked at. Same-direction held position with `allow_pyramiding` False (`:50,316`) returns `already_at_target`; with True it adds. No branch sells part of a position when the view weakens (only `close_before_reverse` on an opposite view, `:291-310`).
- Effect: positions are held until the view flips; drawdown exposure persists through flat views. Quantification needs hosted view history.
- Owner: `expression_policy.py`. Smallest fix: on FLAT with an open position emit `close_leg`; trim is a separate design decision. **Changes reported performance: operator decision.**

### 7. Rebalance buffer — confirmed
- Producer signals always carry `run_id` (`paper_signal_producer.py:124,242,1009,1900`), so `signal_consumer.py:242-245` routes them to `_buffer_rebalance`; they execute only at `_tick_rebalance_buffer` after `_REBALANCE_TIMEOUT_BARS = 3` (`:51,619-634`).
- `flush_rebalance` (`signal_consumer.py:636`; wrapper `integrations/lean/pantheon_algo/base.py:235`) is called only from tests (`test_paper_runtime.py`, `test_paper_signal_producer.py:2963`, `trade_journey/test_canonical_paper_lifecycle_integration.py:228`, `paper_fleet_reconciler/test_paper_fleet_reconciler.py:906`); `grep -rn "\.flush_rebalance(" --include=*.py` shows no non-test caller. Tests therefore pass by flushing manually where production waits three ticks.
- Effect: every order executes up to three drain ticks after its decision; with claim 5 the price is still the decision-bar price, so this adds latency but not price drift on the daily path. Quantify the tick duration from hosted data.
- Owner: `signal_consumer.py` / producer service. Smallest fix: producer batches of one signal flush immediately (call flush after enqueue). Changes timing of recorded fills: operator decision.

## Hosted readback needed from the coordinator
For each of persona-2448c7053e33ea48da47, persona-ee7112a829d38b9135ac, persona-6507750312870d03a241:
1. runtime `initial_cash` and capital pool budget; resolved risk policy id/limits (claims 1, 3).
2. fill count, round trips, traded notional in the quarter (claim 2; cost bias = traded notional x rates above / initial_cash).
3. minimum ledger cash reached (claim 4).
4. `market_input_bar_frequency` of the bindings (claim 5).
5. view history with FLAT/weaker views while a position was held (claim 6).
6. decision-to-fill latency (claim 7).
Then the quarter-return and ranking score effect per persona can be filled in; this was not done here.

## Operator decisions required
Claims 1, 2, 4, 5, 6 change reported performance (and 7 changes fill timing). Claim 3 changes order acceptance only. Starting values (pool budget as capital, 0.1425%/0.3%/0.1% rates) are the coordinator's proposals and member_virtual_book constants; the reviewer should confirm them against the cited design files, which were not re-read in this pass.
