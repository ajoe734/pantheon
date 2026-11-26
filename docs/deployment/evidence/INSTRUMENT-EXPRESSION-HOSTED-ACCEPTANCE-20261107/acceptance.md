# INSTRUMENT-EXPRESSION-HOSTED-ACCEPTANCE-20261107 — hosted acceptance of the instrument expression layer

Verdict: **pass with operator-amended clauses** (see "Operator decision" below).
Window: 2026-11-09 20:46Z to 2026-11-13 02:20Z.
All hosted reads were read-only: telemetry, reconciliation-drift and source-ingest containers on the dev VM, and the Management BFF and pages through a real logged-in browser session. The only hosted write in the window was one dispatch of the existing `dev-tw-market-refresh.yml` workflow (run 43315136210).

## Operator decision (2026-11-13)

On 2026-11-13 the operator was shown that clauses 2 (expiry), 3, the portfolio-book screenshot half of 4, and the all-12 half of 5 had not happened on hosted and would not happen naturally soon. The three successor personas closed every position on the 2026-11-12 bar, and the last hosted all-12 chain predates this feature. Asked how to close, the operator chose:

> 修改條款後今天結案 (Recommended) — 第 2、3 條用已合併 PR 的 CI 測試當證據；第 4 條用 11/10 的實看文字紀錄；第 5 條用每次部署都通過的 lifecycle probe，all-12 留給下一條鏈；第 1 條改成跨 persona。

The amended clauses are therefore:

1. Across the successor personas, hosted decisions choose cash shares, choose a stock future, and record a no trade, each carrying instrument_id, reason_code and decision_id.
2. Daily D-1 settlement marking is shown on hosted. Expiry cash settlement and ledger reconciliation are shown by CI tests.
3. The 2.0 gross notional rejection and the price_stale rejection are shown by CI tests.
4. The attribution page is shown in a real browser with screenshots. The portfolio-book futures columns are shown by the 2026-11-10 browser record plus the 2026-11-12 column-header screenshot.
5. The dev lifecycle probe passes on the served release and the threshold sweep uses the instrument-class baseline. The hosted all-12 chain is deferred to the next chain run.

## Served pairs

| when (UTC) | pantheon | execute-plans | run | result |
|---|---|---|---|---|
| 11/09 20:46 | e88fcf0a9 | f6d528080 | 42685389066 | accepted (PRs 7096 7097 7098 7099) |
| 11/10 00:48 | 39f80bb95 | fe5899413 | 42731056862 | accepted (PR 7103 instrument-class drift baseline) |
| 11/10 03:08 | a51fd808c | fe5899413 | 42747427456 | accepted; first expression decisions ran on this pair |
| 11/10 08:46 | cdea24152 | fe5899413 | 42774607596 | accepted |
| 11/10 10:31 | 85477d05b | 5f13f6ec3 | 42788400465 | accepted (PR 7118 futures valuation mark, PR 7119 holdings reason, PR 7121 counterfactual writer, execute-plans PR 979) |
| 11/10 12:34 | 8173ab180 | 5f13f6ec3 | 42806011305 | accepted (PR 7120 session mark and window, PR 7124 BFF futures freshness) |
| 11/11 23:53 | bee62bc2978618539984aff9db136b064adc14a0 | acf3efaeb9141e1abd80375c9c2a357328c02c79 | 43123219914 | accepted; served for every check from 11/12 onward |

Releases attempted after 43123219914 (11/12 07:59, 12:12, 16:47, 19:14, 22:36, 23:49) were all rejected by the execute-plans FE-BFF integration gate. The stack stayed on bee62bc29 + acf3efaeb.

## Clause 1 — cash shares, stock future, no trade — PASS (amended: across personas)

- 2026-11-10 07:01:37Z on a51fd808c, three opening decisions:
  - rt-ad81501b (2330) bought eq:2330 x43 @2285.0 with reason long_cash, decision dec-rb-3823397e125e4579ba5f4174067087ed-b5fa95075d12d756f69c821b.
  - rt-3252fa75 (2317) bought eq:2317 x414 @241.5 with reason long_cash, decision dec-rb-7f3c1be55a224fdaa36bbdd7a7532b4b-a50703b94234de51d32eae4f.
  - rt-3b65a843 (0050) bought ssf:SRF:202611 x2 @97.1 with reason long_lever, margin 17400, fee 16.0, tax 3.884, decision dec-rb-3516fc86e6f84649a499fc89f3c562b3-b16d6712794d6cec3472fe30.
- 2026-11-11 23:08Z bar: all three personas recorded already_at_target no trades with reason_code and decision_id.
- 2026-11-12 22:01Z bar: all three closed with close_leg. Every lifecycle event carries decision_id, reason_code and expression_decision: signal_generation, trade_decision, risk_evaluation, order_submitted, order_accepted, paper_fill_simulated, position_snapshot and reconciliation_completed.
  - 2330 sold eq:2330 x43 @2340 (dec-rb-3823397e125e4579ba5f4174067087ed-9b94b348a3939290c1b9be56).
  - 0050 sold ssf:SRF:202611 x2 @103.55 (dec-rb-3516fc86e6f84649a499fc89f3c562b3-c4faf828699570379a4246c3). Its trade_date is 2026-11-11 because futures orders price at the D-1 settlement session by design.
  - 2317 sold eq:2317 x414 @240 (dec-rb-7f3c1be55a224fdaa36bbdd7a7532b4b-f397f39718fc81991f83be1e).
- Limitation under the original wording: no single persona has both a cash-share choice and a stock-future choice.

## Clause 2 — daily mark and expiry settlement — daily mark PASS on hosted, expiry by CI

Hosted daily mark of ssf:SRF:202611:

| mark | settlement session | how it was observed |
|---|---|---|
| 97.1 | 11/09 | valuation on 85477d05b at 11/10 09:46Z |
| 99.7 | 11/10 | the 11/11 bar signal carried trade_date 2026-11-10 and session_settlement_prices; MarkFuturesSession applied once |
| 103.55 | 11/11 | valuation from the date=2026-11-12 partition; unrealized 12900; pnl 12880.116 |

- Drift status was ok on all three bindings at every check. `_valuation_failure` was null on all three runtimes on 11/12.
- After the 11/12 close the 0050 runtime pnl is 12859.974. The counterfactual for the chosen SRF leg is pnl_cf 12860.232 with costs 39.768.
- Expiry: SRF:202611 was closed on 2026-11-12, so no position was held into the 2026-11-18 expiry. Expiry settlement, `paper_futures_settled` and ledger reconciliation are evidenced by the CI tests listed below.

## Clause 3 — 2.0 gross notional and price_stale rejections — by CI

No hosted stimulus was run. The books were fully allocated and pyramiding is off, so no natural over-limit or stale order occurred. Evidence is the CI tests listed below. `test_ac3_risk_gate_evaluates_policy_from_capital_reader_and_rejects_all_reasons_without_fill` submits 1 QFF at 2247, which is 2.247x against a 2.0x cap. It asserts the reasons leverage_limit, gross_limit, net_limit and price_stale, each with required, available, limit, instrument_id and decision_id. It also asserts fill_count 0 and no instrument position.

## Clause 4 — portfolio book and attribution in a real browser — PASS (amended)

- 2026-11-10 07:4xZ on fe5899413: the holdings columns 商品種類, 契約月, 乘數 and 保留保證金 were present. The SRF row showed 股票期貨, ssf:SRF:202611, 202611, 1,000 and NT$17,400. 決策原因 showed only an em dash, which led to PR 7119. The attribution tab had no instrument learning block, which led to execute-plans PR 979 and PR 7121.
- 2026-11-10 10:3xZ to 12:4xZ on 85477d05b and 8173ab180: the attribution page rendered 商品決策學習 and 商品別損益拆解. The SRF row first showed 報價過期, which led to PR 7124. On 8173ab180 the page showed NT$97.10 截至 2026-11-09 and 市值 NT$194,200.
- The 11/10 screenshots were lost when the dev host was rebooted at 11/10 22:14Z. The observations above were written down at the time.
- 2026-11-12 22:06Z on bee62bc29 + acf3efaeb, logged in as the operator:
  - The attribution page shows 商品決策學習 with 方向命中率 75.00% (6/8 graded), 商品選擇遺憾 0 (8 graded), 保證金使用 215,636 and 決策筆數 8, plus one row per decision.
  - BFF `performance-attribution?period=latest` returns the same `instrument_learning` block.
  - The portfolio book shows the column headers 商品種類, 契約月, 乘數, 保留保證金 and 決策原因 with no rows, because every position was closed.

Counterfactual grading of the 11/10 opening decisions, observed 2026-11-12 22:01:16Z:

| persona | chosen | chosen pnl_cf | alternatives |
|---|---|---|---|
| 0050 | ssf:SRF:202611 x2 | 12860.232 | eq:0050 x1003 3510.5; ssf:NYF:202611 0 |
| 2330 | eq:2330 x43 | 4730 | ssf:CDF:202611 0; ssf:QFF:202611 0 |
| 2317 | eq:2317 x414 | 207 | ssf:DHF:202611 0 |

## Clause 5 — lifecycle probe, all-12, instrument-class baseline — PASS (amended)

- The "Dev canonical paper lifecycle hosted probe" step passed in every accepted release listed above. On the served release 43123219914 the probe ran in natural mode with outcome passed. The authenticated public BFF readback also passed, with loop_run and trade_journey both live and ready and terminal no_order.
- Drift evaluations use the instrument-class baselines from PR 7103: 2330 cash 0.00235, 0050 stock_future 0.1245, 2317 cash 0.00021.
- Correction to an earlier draft: the accepted releases did **not** run the isolated L1-L12 gate or the hosted all-12 chain. Those jobs only run when dispatched with `isolated_provider_qualification` or `hosted_all12_chain`, and they were skipped in all seven runs. The last hosted all-12 chain is h12-claude-20261027-4923 (E_close 41962827263, 2026-11-05), which predates this feature. Per the operator decision, all-12 is deferred to the next chain run.

## CI tests (re-run on origin/dev bee86402b, 2026-11-13; 23 passed)

The services tests below run in `branch-ci.yml`. Branch CI on dev bee86402b (run 43347244276) succeeded. The two `scripts/test_probe_dev_runtime_paper_lifecycle.py` tests were re-run locally only.

Clause 2, expiry settlement and ledger:
- `services/execution/lean_runtime/test_paper_runtime.py::TestPaperFuturesLedgerAndRiskGate::test_ac6_november_2026_expiry_settlement_example_and_settlement_pending_guard`
- `services/execution/lean_runtime/test_paper_runtime.py::TestPaperFuturesFillEpisode::test_expiry_settlement_clears_episode`
- `services/execution/lean_runtime/test_paper_runtime.py::TestPaperFuturesFillEpisode::test_open_futures_episode_survives_ledger_persistence`
- `services/execution/lean_runtime/test_paper_runtime.py::TestPaperFuturesLedgerAndRiskGate::test_p1_5_mark_futures_session_idempotency_and_service_lifecycle`
- `services/execution/lean_runtime/test_futures_session_inputs.py::ProducerRuntimeFuturesWiringIntegrationTests::test_runtime_bootstrap_dataset_final_settlement_pending_then_settled_for_202611_srf`
- `services/execution/lean_runtime/test_futures_session_inputs.py::ProducerRuntimeFuturesWiringIntegrationTests::test_margin_call_then_forced_liquidation_with_real_srf_margin_figures_via_production_path`
- `services/telemetry/test_runtime_summary_projection.py::TestInstrumentAwarePositionProjection::test_expiry_settlement_closes_the_contract_and_books_realized`
- `services/telemetry/test_runtime_summary_projection.py::TestPositionNetPnlRecognizedCosts::test_settlement_cost_applies_once`
- `services/telemetry/test_runtime_summary_projection.py::TestPositionNetPnlRecognizedCosts::test_partial_close_and_reversal_keep_gross_and_costs_separate`
- `services/telemetry/test_paper_runtime_ingest_contract.py::PaperRuntimeTelemetryIngestContractTest::test_paper_futures_settled_margin_call_and_forced_liquidation_ingest_and_project_terminal_nonfill`
- `services/trade_journey/test_hosted_lifecycle_probe.py::test_probe_accepts_paper_futures_settled_terminal`
- `scripts/test_probe_dev_runtime_paper_lifecycle.py::test_paper_lifecycle_accepts_paper_futures_settled_terminal` (local only)
- `scripts/test_probe_dev_runtime_paper_lifecycle.py::test_paper_lifecycle_settlement_still_requires_paper_only_flags` (local only)

Clause 2, daily mark freshness:
- `services/telemetry/test_runtime_summary_projection.py::TestInstrumentAwarePositionProjection::test_futures_daily_settlement_mark_is_fresh_at_one_session_lag`
- `services/telemetry/test_runtime_summary_projection.py::TestInstrumentAwarePositionProjection::test_futures_daily_settlement_mark_is_fresh_across_a_weekend`
- `services/telemetry/test_runtime_summary_projection.py::TestInstrumentAwarePositionProjection::test_futures_daily_settlement_mark_is_stale_beyond_session_lag`
- `services/telemetry/test_runtime_summary_projection.py::TestPositionNetPnlRecognizedCosts::test_stale_mark_keeps_costs_and_leaves_unrealized_unknown`

Clause 3, rejections:
- `services/execution/lean_runtime/test_paper_runtime.py::TestPaperFuturesLedgerAndRiskGate::test_ac3_risk_gate_evaluates_policy_from_capital_reader_and_rejects_all_reasons_without_fill`
- `services/execution/lean_runtime/test_paper_runtime.py::TestPaperFuturesLedgerAndRiskGate::test_p1_3_strict_d_minus_1_settlement_date_and_no_market_price_fallback`
- `services/execution/lean_runtime/test_paper_runtime.py::PaperRuntimeServiceTest::test_chosen_stock_future_is_rejected_without_a_fill`
- `services/execution/lean_runtime/test_expression_inputs.py::BuildExpressionInputsTests::test_the_limit_is_the_policy_multiple_not_a_constant`
- `services/execution/lean_runtime/test_paper_signal_producer.py::TestInstrumentCounterfactualWriter::test_stale_settlement_is_never_reused_as_the_mark`
- `services/execution/lean_runtime/test_paper_signal_producer.py::TestInstrumentCounterfactualWriter::test_missing_settlement_partition_emits_nothing_and_says_why`

## Correction to PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107/cutover.md line 41

`threshold_sweep_baselines.json` holds 6 entries, not 8:

| underlying | cash_share | stock_future |
|---|---|---|
| 2330 | 0.00235 | 0.235 |
| 0050 | 0.0001245 | 0.1245 |
| 2317 | 0.00021 | 0.42 |

## Environment incidents in the window (not defects of this feature)

- 11/10 07:02:32Z: a reconciler DNS blip paused all bindings for 16 s, then they resumed automatically.
- Release probe personas without budget record budget_unavailable no trades: rb-265d7a7b on 11/10 and rb-6ade8913 ("Pantheon Dev Paper Release 43123219914 Attempt 1") from 11/12.
- TWSE OpenAPI STOCK_DAY_ALL published the 11/11 and 11/12 sessions only at about 21:20Z. Afternoon refreshes failed closed, and source-ingest recreation paused the bindings with market_input_missing. They resumed when the next session was admitted: the 11/11 bar ran 23:08Z and the 11/12 bar ran 22:01Z after manual refresh run 43315136210. That run's conclusion is failure only because its agora projector step got HTTP 503 while source-ingest restarted. The dev-host timer now also dispatches at 23:30Z and 00:30Z.
- 11/12: all automatic releases were rejected by the execute-plans FE-BFF integration gate ("Invalid InterceptionId" in the hosted management acceptance step, plus a lint error from 11/12 22:01Z). Release 43288069362 (19:14Z) also reported "rollback incomplete". The stack was back on bee62bc29 by 21:21Z.
- 11/10 22:14Z: the dev host was rebooted externally, which wiped the 11/10 screenshots.

## Evidence files (operator host, read-only copies)

| file | sha256 |
|---|---|
| perf-attribution-2026-11-12T22-06-59-538Z.png | 560c335e535b4a55037dd69678e1c1341e6f9d60c7b06487fe9c47a48b49abae |
| portfolio-book-2026-11-12T22-06-59-538Z.png | 44072691f0dc954745c8d829a022a505de6641d85a7faf83aacf0ea90e1e1ca1 |
| perf-overview-2026-11-12T22-06-59-538Z.png | aa918c065e684040b3de2d2116e03c19d237566de7c10922f56569a9f1019d22 |
| attribution-2026-11-12T22-06-59-538Z.json (BFF) | 2f6bcbfc3997a5312bc792134a1c0616b3f5c26e91a2103df9f2629c1f362d38 |
| holdings-2026-11-12T22-06-59-538Z.json (BFF) | 4aafaef0630bcb0f7a80a6ba1277ba8ab9d8c380035de90c6a51f2cb46464c92 |
| loop-prod-tel-002-hosted-43123219914-1.json | 9c4ce9183bd70372f9d7ae6b673c1e92fa7610ca69a6820ad70d1b8886578a2d |
| loop-prod-tel-002-bff-readback-43123219914-1.json | 2a5628f376bd1b0b5fc4fac0baf9a4899c395b29025612dafecae3f9c46e4257 |
| ci-tests-bee86402b.log | de54a032b03bc19d124b094f02deacc2d7eb1fa56a294ed14713b0a18fde86bd |
