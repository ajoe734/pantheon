# Paper ledger accounting audit — 2026-11-15

Task: PAPER-LEDGER-ACCOUNTING-AUDIT-20261115. Report only; no production code changed.
Traced tree: dev head 40f024389 (descendant of the claimed 04613fa66; the cited files were re-read at this head and line numbers below are from it).
Reproduction: `PYTHONPATH=. python3 <script>` using the repro in the evidence file (commands recorded in `docs/deployment/evidence/PAPER-LEDGER-ACCOUNTING-AUDIT-20261115/evidence.json`).

## Limit of this report
Workers have no hosted shell. Acceptance 3 uses the coordinator's read-only readback captured 2026-11-15T09:23Z from hosted dev 94b73806a (`pantheon-acceptance-evidence/PAPER-LEDGER-AUDIT-20261115/hosted-readback-raw.txt`; first 6000 bytes of each ledger state file). It covers only what is in section "Hosted per-persona quantification"; effects needing fields absent from it are listed as still open there. Figures elsewhere in this report marked "analytic" are not hosted measurements.

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

## Hosted per-persona quantification (acceptance 3)
Source: hosted readback above (dev 94b73806a). The ledger window is 2026-11-08 to 2026-11-14, i.e. about one week, not a full quarter; the "return" below is ledger return since start = (cash + holdings value - initial_cash) / initial_cash, the same pnl / initial_cash form that ranking reads (`personas/service.py:13891`).

| Persona | initial_cash | cash | open position | equity | return | fills | costs booked |
|---|---|---|---|---|---|---|---|
| 青禾 2330 (persona-2448c7053e33ea48da47) | 100000 | 100015.00 | 1 share 2330 @ mark 2350 | 102365 | +2.365% | 3 | 0.0 |
| 衡岳 0050 (persona-ee7112a829d38b9135ac) | 100000 | 112839.93 | 2 SSF SRF:202611 @ 101.1, x1000 | 112839.93 | +12.840% | 3 | 60.07 |
| 澄川 2317 (persona-6507750312870d03a241) | 100000 | 99138.00 | 1 share 2317 @ mark 241 | 99379 | -0.621% | 3 | 0.0 |

Claim by claim, from these readbacks:
- **1 Starting cash**: confirmed on hosted. All three runtimes read `initial_cash` 100000.0 and `injected_equity_capital` 0.0. The readback does not include the pool budget (pool ids pool-persona-paper-06d4775a9b6a8f63b311 / -a81b4fd15944698341aa / -039ad01f8bcd2b4aa466), so the divergence size is **open**; return denominators are 100000 regardless of the pool.
- **2 Costs**: confirmed for 青禾 and 澄川: cash-share instrument positions carry `costs` 0.0 after 3 fills each. 衡岳 trades a stock future, whose path does charge costs (60.07 booked), so claim 2 does not bias 衡岳; its 0050 cash-share leg is flat. Cash-share fill quantities and prices are not in the 6000-byte readback, so the exact cost bias is **open**; the bound is per round trip 0.585% of traded notional. At the one-share position size observed (2350 and 241 NTD notional = 2.35% and 0.24% of capital), the whole bias is below 0.02 pp of return in magnitude if the fills are one share each (assumption, not measured), i.e. small against the 2.365% and -0.621% shown.
- **3 Risk policy fallback**: effect on hosted personas **open**: the resolved policy per pool was not read. Observable consequence: 衡岳 holds 2 SSF contracts, notional 2 x 101.1 x 1000 = 202200, about 1.79x its 112840 equity (2.02x initial_cash), with initial margin 17400 reserved. No `margin_call_active`. Whether a limit would have blocked this is unknown until the policy is read.
- **4 Cash sufficiency**: no hosted breach: lowest cash is 99138 (澄川), all positive; rolling portfolio values for 衡岳 never below 100000. Claim stays confirmed in source (repro cash -900000) but has not affected the three personas' reported returns so far.
- **5 Fill timing**: open; `market_input_bar_frequency` not read. All three have `mark_as_of` 2026-11-13 daily marks and fills on 2026-11-14T07:45Z (青禾, 澄川), consistent with daily-bar decisions, but this is inference.
- **6 No reduce/exit**: open; view history not read.
- **7 Flush latency**: open; fill timestamps for 青禾 and 澄川 cluster at 07:01:47-48Z on 2026-11-10 and 07:45:09-10Z on 2026-11-14, consistent with batch drain, but decision times are not in the readback.

Ranking effect: ranking return for 衡岳 (+12.84%) is on 2x notional exposure with no risk-limit evidence, so it is the persona most distorted by claims 3/4; 青禾 and 澄川 rankings are affected by claim 2 only to the sub-0.02 pp level under the stated assumption, which does not change their order (+2.365% vs -0.621%). Ordering 衡岳 > 青禾 > 澄川 is robust to claim 2; it is not shown robust to claims 1 and 3.

## Still-needed hosted fields (exact)
1. Capital pool budget for the three pools (`pool-persona-paper-*`), claim 1.
2. Resolved capital risk policy limits for the three `pcb-persona-paper-*` budgets, claim 3.
3. Per-fill `quantity`, `fill_price`, `side`, `filled_at` for the 3 fills of each ledger (claim 2 exact bias), plus the fill list beyond the 6000-byte cut.
4. `market_input_bar_frequency` of the three bindings (claim 5).
5. Decision `created_at` for the producer signals behind each fill (claim 7); signal/view history with FLAT or reduced views (claim 6).

## Hosted readback originally requested
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
