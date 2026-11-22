# PAPER-SIZING-BOUND-RISK-POLICY-20261121 Evidence

## Scope & Root-Cause Class
- **Task ID**: `PAPER-SIZING-BOUND-RISK-POLICY-20261121`
- **Register Class**: `RC2` (follow-up to `#7657` `PAPER-CASH-SHARE-SIZING-COMMISSION-20261120`, `#7703` `PAPER-RISK-POLICY-PROVENANCE-20261121`, and `#7726` `PAPER-RISK-POLICY-BINDING-20261121`)
- **Objective**: Consume the resolved `RiskPolicy` and version in pre-trade sizing and final submission validation; bound long and target-position sizing by the most restrictive applicable constraint across fee-inclusive buying power, existing per-underlying holdings, competing reservations, gross/net/leverage limits, and governed per-name (`max_single_name_weight`) or cash-reserve (`min_cash_reserve_pct` / `min_cash_reserve`) rules; re-evaluate at submission time and return structured evidence for any resize, no-trade, or rejection while preserving risk-reducing exits and retry idempotency.

## Exact Pre-Trade Replay (`tj_79726064-4cc2-5510-8e11-7f5e59ff89bd`)
- **Decision ID**: `dec-rb-a2cb762c5aeb42ebbcf8f2e6894a1bc4-7ed048e23fd14b14546cfb09`
- **Underlying / Dated Price / Capital**: `2317` (`LONG`, `strength = 0.91324200913242`), dated price `221.0` (`2026-11-18`), capital `100,000.0` TWD
- **Resolved Approved Baseline Policy**: `rp-pool-persona-paper-039ad01f8bcd2b4aa466` v`1.0` (`gross_limit = 2.0`, `net_limit = 2.0`, `max_leverage = 2.0`, `max_single_name_weight = null`, `min_cash_reserve_pct = null`)
- **Baseline Pre-Trade Sizing Breakdown**:
  - `target_notional`: `182,648.401826` TWD (`826` shares desired)
  - `permitted_units`: `451` shares (`eq:2317`)
  - `binding_limit`: `available_cash_after_fees` (pre-fee cash cap `452` shares shrunk to `451` shares after Taiwan broker buy commission `142.031175` TWD; gross/net/leverage caps are `200,000.0` TWD / `904` shares; `ssf:DHF:202612` 1-contract notional `440,000.0` TWD > `200,000.0` TWD gross limit)
  - `fee_inclusive_required_cash`: `99,813.031175` TWD (`99,671.0` notional + `142.031175` commission)
  - `remaining_cash`: `186.968825` TWD
  - `after_trade_concentration`: `0.998128` (`99,671.0 / 99,857.968825` post-fee equity) / `0.996710` (`99,671.0 / 100,000.0` initial capital)

## Clearly Labelled Nontrivial Explicit Limit Fixtures
*(Test fixtures only; delivered runtime policy and acceptance are based on approved runtime settings)*
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

## Submission-Time Re-Evaluation, Reservations, Retry Deduplication & Risk-Reducing Exits
- **Changed price at submission**: D+1 open `225.0 > 221.0` resizes `451 -> 443` shares (`binding_limit = "available_cash_after_fees"`).
- **Changed policy at submission**: policy updated to v`1.1` (`max_single_name_weight = 0.25`) resizes `451 -> 113` shares (`binding_limit = "max_single_name_weight"`, `risk_policy_version = "1.1"`).
- **Competing orders / reservations**: `reserve_order` reserves cash/margin/exposure/holdings idempotently on retry and releases on fill/no-trade/rejection; competing `40,057.0` TWD cash reservation resizes `451 -> 270` shares (`binding_limit = "competing_reservation"`).
- **Changed holdings at submission**: when holdings already satisfy target notional, submission records a clean no-trade (`no_trade_at_submission = true`, `reason_code = "already_at_target"`, `submitted_to_broker = false`).
- **Retry deduplication**: retrying `SubmitTaiwanBrokerOrder` with an already-completed `signal_id` records `duplicate_signal_retry` with `submitted_to_broker = false` and never creates a duplicate reservation or fill.
- **Risk-reducing exits preserved**: `close_leg` orders clamp down to remaining unreserved held quantity when holdings shrink and record `position_already_closed` (`submitted_to_broker = false`) when already flat, never flipping position sign.

## Runtime Budget
- **Persistent writers added or changed**: `0` (`paper_performance_ledger.v1` and `pending_signal_store` decision record schemas add `0` top-level keys and `0` persistent disk bytes).
- **In-memory bounds**: `_order_reservations` holds only active in-flight reservations and releases on completion; `_completed_signal_submissions` is FIFO-capped at `2048` entries (`~512 KB` max).
