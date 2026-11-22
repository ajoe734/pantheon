# PAPER-SIZING-BOUND-RISK-POLICY-20261121 Evidence

## Scope & Root-Cause Class
- **Task ID**: `PAPER-SIZING-BOUND-RISK-POLICY-20261121`
- **Register Class**: `RC2` (follow-up to `#7657` `PAPER-CASH-SHARE-SIZING-COMMISSION-20261120`, `#7703` `PAPER-RISK-POLICY-PROVENANCE-20261121`, and `#7726` `PAPER-RISK-POLICY-BINDING-20261121`)
- **Objective**: Consume the resolved `RiskPolicy` and version in pre-trade sizing and final submission validation; bound long and target-position sizing by the most restrictive applicable constraint across fee-inclusive buying power, existing per-underlying holdings, competing reservations, gross/net/leverage limits, and governed per-name (`max_single_name_weight`) or cash-reserve (`min_cash_reserve_pct` / `min_cash_reserve`) rules; derive pre-trade sizing evidence from the single normal `Chosen`/`NoTrade` decision (including injected deciders and candidate-pool exit overrides) without silent legacy-envelope recomputation; re-evaluate at submission time and return compact bounded evidence for any resize, no-trade, or rejection while preserving risk-reducing exits and retry idempotency.

## Hosted Profile-Mapping Approval Status & Exact Pre-Trade Replay (`tj_79726064-4cc2-5510-8e11-7f5e59ff89bd`)
- **Observed Hosted Provenance Status (`PAPER-RISK-POLICY-PROVENANCE-20261121` / `PAPER-RISK-POLICY-BINDING-20261121`)**:
  - On hosted dev, `pool-persona-paper-039ad01f8bcd2b4aa466` carries inline `rp-pool-persona-paper-039ad01f8bcd2b4aa466` v`1.0` (`gross_limit = 2.0`, `net_limit = 2.0`, `max_leverage = 2.0`) with `requested_risk = "low"` and `0` matching owner `capital_pool_activation` profile-mapping approvals (`status = "unresolved_profile_mapping"`, `reason_code = "risk_policy_profile_mapping_unapproved"`).
  - Until genuine policy-owner `capital_pool_activation` approval is recorded, `ExpressionInputsProvider` and `PaperExecutionAlgorithm` fail closed for opening/increasing risk while preserving risk-reducing exits. Genuine owner approval and fresh post-deploy hosted acceptance proof remain explicitly outstanding under `PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121`.
- **451-Share Historical Pre-Trade Replay (`observed_historical_inline_v1_policy_fixture`)**:
  - **Decision ID**: `dec-rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4-7ed048e23fd14b14546cfb09`
  - **Underlying / Dated Price / Capital**: `2317` (`LONG`, `strength = 0.91324200913242`), dated price `221.0` (`2026-11-18`), capital `100,000.0` TWD
  - **Observed Historical Inline v1.0 Policy Fixture**: `rp-pool-persona-paper-039ad01f8bcd2b4aa466` v`1.0` (`gross_limit = 2.0`, `net_limit = 2.0`, `max_leverage = 2.0`, `max_single_name_weight = null`, `min_cash_reserve_pct = null`)
  - **Pre-Trade Sizing Breakdown**:
    - `target_notional`: `182,648.401826` TWD (`826` shares desired)
    - `permitted_units`: `451` shares (`eq:2317`)
    - `binding_limit`: `available_cash_after_fees` (pre-fee cash cap `452` shares shrunk to `451` shares after Taiwan broker buy commission `142.031175` TWD; gross/net/leverage caps are `200,000.0` TWD / `904` shares; `ssf:DHF:202612` 1-contract notional `440,000.0` TWD > `200,000.0` TWD gross limit)
    - `fee_inclusive_required_cash`: `99,813.031175` TWD (`99,671.0` notional + `142.031175` commission)
    - `remaining_cash`: `186.968825` TWD
    - `after_trade_concentration`: `0.998128` (`99,671.0 / 99,857.968825` post-fee equity) / `0.996710` (`99,671.0 / 100,000.0` initial capital)

## Clearly Labelled Nontrivial Explicit Limit & Overlapping-Limit Test Fixtures
*(Local synthetic test fixtures only; not hosted observations)*
1. **`FIXTURE_EXPLICIT_SINGLE_NAME_25PCT`** (`max_single_name_weight = 0.25`):
   - `permitted_units = 113`, `binding_limit = "max_single_name_weight"`, `fee_inclusive_required_cash = 25,008.586525`, `remaining_cash = 74,991.413475`, `after_trade_concentration = 0.249819 <= 0.25`.
2. **`FIXTURE_EXPLICIT_CASH_RESERVE_20PCT`** (`min_cash_reserve_pct = 0.20` -> `20,000.0` TWD reserve):
   - `permitted_units = 361`, `binding_limit = "cash_reserve"`, `fee_inclusive_required_cash = 79,894.687925`, `remaining_cash = 20,105.312075 >= 20,000.0`.
3. **`FIXTURE_EXPLICIT_MAX_LEVERAGE_60PCT`** (`max_leverage = 0.60` vs `gross_limit = 2.0`):
   - `permitted_units = 271`, `binding_limit = "max_leverage"`, `after_trade_gross_exposure = 0.599422 <= 0.60`.
4. **`FIXTURE_EXPLICIT_NET_LIMIT_40K_WITH_10K_LONG`** (`net_limit = 40,000.0`, `signed_net_used = 10,000.0`):
   - `permitted_units = 135`, `binding_limit = "net_limit"`, `after_trade_net_exposure = 0.398518 <= 0.40`.
5. **`FIXTURE_TARGET_POSITION_EXISTING_HOLDINGS_AND_RESERVATION`** (`target_position_v1`, `max_single_name_weight = 0.30`, `50` shares / `11,050.0` TWD already held on `2317`, `30,000.0` TWD competing cash reservation):
   - `permitted_units = 85`, `binding_limit = "max_single_name_weight"`, `after_trade_concentration = 0.298430 <= 0.30`.
6. **`FIXTURE_OVERLAPPING_CASH_10K_VS_SINGLE_NAME_80PCT`** (`equity = 100,000.0`, `raw_cash = 10,000.0`, `available_cash = 10,000.0`, `max_single_name_weight = 0.80`, `price = 221.0`):
   - `permitted_units = 45`, `available_cash_after_fees_cap = 45`, `max_single_name_weight_cap = 361`, `binding_limit = "available_cash_after_fees"`.

## Single Normal Decision Calculation & Submission-Time Re-Evaluation
- **Single normal pre-trade calculation**: `CurrentArtifactStrategy._expression_step` computes `record` once (respecting injected `decider` and the `exit_reason` override to `held_position` `NoTrade`) and passes `decision=record` to `evaluate_pre_trade_sizing` -> `compact_pre_trade_sizing`. `apply_expression_record` attaches stored `pre_trade_sizing` without silently recomputing during legacy envelope reads.
- **Unapproved inline v1.0 pool fail-closed check**: Without synthetic owner approval, opening `2317` submission fails closed with `reason = "risk_policy_unavailable"` (`submitted_to_broker = false`).
- **Submission re-evaluation scenarios (with synthetic local fixture approval reader)**:
  - **Changed price at submission**: D+1 open `225.0 > 221.0` resizes `451 -> 443` shares (`binding_limit = "available_cash_after_fees"`).
  - **Changed policy at submission**: policy tightened to v`1.1` (`max_single_name_weight = 0.25`) resizes `451 -> 113` shares (`binding_limit = "max_single_name_weight"`, `risk_policy_version = "1.1"`).
  - **Competing orders / reservations**: `reserve_order` reserves cash/margin/exposure/holdings idempotently on retry and releases on fill/no-trade/rejection; competing `40,057.0` TWD cash reservation resizes `451 -> 270` shares (`binding_limit = "competing_reservation"`).
  - **Changed holdings at submission**: when holdings already satisfy target notional, submission records `no_trade_at_submission = true` (`reason_code = "already_at_target"`, `submitted_to_broker = false`).
  - **Retry deduplication**: retrying `SubmitTaiwanBrokerOrder` with an already-completed `signal_id` records `duplicate_signal_retry` with `submitted_to_broker = false` and never creates a duplicate reservation or fill.
  - **Risk-reducing exits preserved**: `close_leg` orders clamp down to remaining unreserved held quantity when holdings shrink and record `position_already_closed` (`submitted_to_broker = false`) when already flat.

## Runtime Budget & Affected Existing Writers (`docs/operations/storage-manifest.json`)
- **`telemetry_events` & `public.telemetry_events`**:
  - **Cadence**: `0` extra events emitted; compact sizing metadata attaches only to existing paper `trade_decision`, `paper_fill_simulated`, `paper_order_simulated` (`no_trade_at_submission`), and `order_rejection` events (`1` daily cycle/persona/day across `3` enrolled dev paper personas, `~3-15` decision events/day and `~0-9` order/fill/rejection events/day).
  - **Serialized size & count at hosted scale**: `metadata.pre_trade_sizing` is `266 B` measured (`<= 384 B` cap via `PRE_TRADE_SIZING_EVIDENCE_MAX_BYTES = 384`, omitting duplicate `decision` and `constraint_unit_caps`); `metadata.submission_sizing_evidence` is `215 B` measured (`<= 320 B` cap via `SUBMISSION_SIZING_EVIDENCE_MAX_BYTES = 320`, reduced from `1,068 B`), plus `~162 B` for `risk_policy_id`/`risk_policy_version`/`risk_policy_digest` (`~643 B` total incremental metadata on a fill/order/rejection event).
  - **Daily growth, cap & cleanup**: `~1.5-12 KB/day` (`< 0.4 MB/month`) across `3` dev paper personas; per-event caps enforced in code; whole-table retention for non-heartbeat/non-snapshot rows tracked under `RC3`.
- **`paper_performance_ledger.v1` (`/data/runtime/paper-performance/<scope>.json` `pending_fill_publications`) & `LifecycleTelemetryOutbox` (`/data/runtime/lifecycle-outbox/<scope>.json`)**:
  - **Cadence, size & count**: `1` ledger file and `1` outbox file per paper runtime (`3` enrolled dev paper personas); on each fill (`~0-3` fills/persona/day), `_stage_fill_publication` stages `event.to_dict()` (`~643 B` incremental metadata) in `pending_fill_publications` and `ack_fill_publication` removes it once telemetry admission completes.
  - **Daily growth, cap & cleanup**: `0 B/day` steady-state in `paper_performance_ledger.v1` (`pending_fill_publications` drains to `[]` on `ack_fill_publication`; atomic file rewrite via `atomic_write_text` + `sweep_stale_temp_siblings`); `LifecycleTelemetryOutbox` capped at `MAX_CHAINS = 64` and pruned on delivery ack.
- **`RedisPendingSignalStore` (`pantheon:paper:decisions:v1:{decision_id}` & `pantheon:signals:pending:{binding_id}`)**:
  - **Cadence, size, daily growth, cap & cleanup**: `1` decision record (`put_decision_record`) and `0-1` pending queue entry (`enqueue`) per evaluated bar (`~3-15`/day across `3` personas), adding `266 B` (`<= 384 B`) for compact `pre_trade_sizing`. Queue entries are deleted on `ack()` (`0 B/day` steady-state); decision records expire automatically via `DECISION_RECORD_TTL_SECONDS = 30 * 86400` (`~0.8-4.0 KB/day`, capped at `~24-120 KB` across the 30-day TTL window).
- **In-memory bounds**: `_order_reservations` holds only active in-flight reservations and releases on completion (`0` steady-state growth); `_completed_signal_submissions` is FIFO-capped at `2048` entries (`~320 KB` max, `0` disk writes).
