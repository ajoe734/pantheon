# Strategy-owned candidate screens

Task: `FACTOR-RULE-SCREEN-SECTION-20261112`; owner Codex2, reviewer Claude.
Design: `docs/04/pantheon_strategy_discovery_sources_2026-11-10/SD_STRATEGY_SCREEN_ENTRY_EXIT_2026-11-12.md` §§2,4.

## Rule and feed contracts

`factor_rule_v1.strategy_logic.screen` is optional. Its version is
`candidate_screen.v1`. It declares `min_size`, eligibility controls
(`min_history_bars`, `liquidity_window`, `min_median_traded_value`), and 1–16
ordered `steps`. All numeric controls use the existing `const_param` grammar
and must have governed mutation controls. Each step declares `expression` and
`refresh` (`daily`, `weekly`, `monthly`, `quarterly`). A `filter` keeps positive
values; a `rank` declares `side` (`top`/`bottom`) and a `count` control. Ties
break by canonical symbol. An optional `group_by: {industry_input: <name>}`
cuts independently inside dated industry groups.

Inputs must be declared catalogue features. Publication cadence comes from the
Source feature catalogue and field-specific provider period mappings; a step
cannot refresh faster than its slowest referenced input (including grouping).
Custom catalogue registrations need explicit `release_cadence`. Industry
grouping fails closed until `tw_industry_classification.industry_code` is
actually registered and its timestamped observations are supplied. This task
does not register or fabricate an industry dataset.

The feed supplies `candidate_pool_market.v1` through
`features.candidate_pool_market` (paper) or dataset `candidate_pool_market`
(vectorbt). It includes:

- `scope: all_governed_tw`, nonempty `source_refs`, and the complete governed
  Taiwan equity `symbols` map; `.TWSE`/`.TPEX` aliases normalize to `.TW`/`.TWO`.
- A governed `trading_sessions` calendar and `calendar_coverage_through`.
- Per-symbol `dataset`, dated daily `bars`, and optional timestamped `features`.
  Bars carry `event_time`, `available_time`, actual OHLCV, `traded_value`, and
  explicit boolean `suspended`. Missing status, stale prior-session bars,
  insufficient history or liquidity each produce a recorded exclusion.
- Explicit `includes_delisted` and `includes_suspended` history coverage.
  Missing/false coverage is marked `survivorship_limited`; the replication
  admission protocol returns that status without admitting the candidate.

The completeness declaration is a feed contract, not a discovery of symbols by
this module. A provisioned list cannot stand in for it. Paper requires matching
`market_inputs` for the complete map, all on one decision bar. Each carries the
existing admitted price snapshot and actual prior `current_direction` and
`holding_bars` when held. Absent position state starts flat. The legacy
`parameters.symbols` identity does not restrict a screened strategy's market.
The existing source endpoint does not supply this new full-market contract;
bindings without it fail closed. No hosted rollout is claimed here.

## Evaluation and durability

`evaluate_screened_views` builds the screen once for the market, then evaluates
entry/strength only for members. Both CurrentArtifactStrategy and the real
vectorbt backend call it. Single-symbol registry/factor interpreter entry points
also enforce the screen and require a symbol and market context.

Screen decisions use data available by the end of D−1 in Asia/Taipei, including
catalogue/declared/row release lag and latest visible revisions. Refreshes anchor
to the day, Monday, month start or quarter start. A slower stage evaluates its
entire upstream prefix at that anchor, preventing daily upstream changes from
silently refreshing a monthly selection. Daily boundary safety exclusions
(suspension, stale history, liquidity) still remove unsafe names between those
selection refreshes; they do not add a name before its next refresh.

Every daily snapshot records members, all exclusions/reasons, stage anchors and
scores, filter/parameter version digest, source references, cutoff, minimum-size
state and survivorship limitation. Its SHA-256 digest is included in paper's
decision key. Paper persists it as
`candidate-pool-<binding-id>-<snapshot-digest>` using the pending store's existing
decision-record API before deduplication or expression processing, including
HOLD days. Backtest artifact bundles preserve the daily snapshot sequence.

A held member leaving the pool emits an explicit closing view with
`target_direction: FLAT` and `exit_reason: left_candidate_pool`. Expression
processing accepts only a validated `close_leg` against the actual held
instrument; a stale position claim cannot open a short instead. Below `min_size`
everyone holds, including previously held names, as required by the task.
Short entry expression remains in the existing stock-futures instrument layer.
Entry inputs cannot substitute raw arrays or synthetic VWAP for missing data.
No-screen artifacts retain their existing normalized shape and checksum.

## Evidence and boundaries

`services/research/tests/fixtures/candidate_pool_tw_recorded.json` contains 78
official TWSE bars: 2330 and 2317, 39 sessions in September–October 2026. Four
recorded STOCK_DAY responses preserve URL, response digest, field names and raw
rows alongside parsed values. Tests prove the values match those recordings.
The test market is deliberately that two-symbol governed slice; it is not
evidence of hosted full-market coverage, delisted history or suspended history.
Suspension false is derived only from an official positive-volume trading row.

The parity test runs `run_vectorbt_workflow` with the real vectorbt 1.1.1 backend
and replays every session through CurrentArtifactStrategy's Object Store loader.
It compares every pool snapshot and complete view, including removals and a
member outside the artifact's legacy provisioned list. Additional tests inject
only explicit availability/status/catalogue faults and grouping metadata; their
prices and volumes remain the recorded observations. Those injected catalogue
rows are not production registration or hosted evidence.

Validation results and commands are recorded in `evidence.json` and
`validation.log`. Scope is the factor-rule screen and its standalone production
entry points. Portfolio-envelope fanout and downstream intraday grading are
separate task surfaces. This packet supplies no hosted, broker or capital proof.
