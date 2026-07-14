# EVOLOOP-005 — Governed baselines and threshold activation

Status: blocked; v1 drawdown baseline proposed, PnL activation withheld

Owner: Codex2

Reviewer / low-risk approver: Codex

Branch: `task/EVOLOOP-005`

Merge target: `dev`

## Outcome

This task proposes a drawdown baseline for the first evolvable strategy
artifact, `artifact-tw-session-momentum-v1@1.0.0`, and records a shadow PnL
calibration that is not eligible for activation:

| Control | Proposed value | Unit / comparison |
| --- | ---: | --- |
| `expected_drawdown` | `0.0303` | fractional 20-calendar-day drawdown baseline |
| shadow `rolling_pnl_floor` candidate | `-5000.0` | not activated; lacks observed canonical telemetry |

The values are research-calibrated from real FinMind TaiwanStockPrice closes,
normalized by Pantheon's source-ingest adapter, interpreted by the checked-in
v1 strategy, and valued with the EVOLOOP-002 production portfolio/drawdown
code. They are not copied from the old `0.12` example, the `-500.0`
placeholder, or test fixtures. This shadow calculation supports the research
drawdown expectation; it does not satisfy EVOLOOP-002's explicit handoff to
derive the absolute PnL floor from observed real telemetry.

The config directory is read-only bind-mounted into
`evolution-threshold-sweep-producer` by `docker-compose.yml`. After merge an
operator activates the host-file update by restarting that service; no image
rebuild and no cadence change are required.

## Owned boundary

Owned by EVOLOOP-005:

- the v1 `expected_drawdown` baseline and its provenance;
- preservation of the PnL activation gate until canonical telemetry exists;
- focused config/load/evaluation evidence.

Not changed here:

- EVOLOOP-002 valuation or telemetry code;
- the EVOLOOP-003 strategy contract or parameters;
- RuntimeBinding, DeploymentPlan, signal production, image, or sweep cadence;
- hosted deployment or an unattended producer tick.

## Dependency identities

| Dependency | Durable result |
| --- | --- |
| EVOLOOP-002 | production fill-ledger mark-to-market and 20-day rolling drawdown merged in PR #3622 (`9f292de3a627b72441a12b478ef307119fa2c9ba`); closeout PR #3628 (`9d393816acfe322a12ba1b295218f829db36ac28`) |
| EVOLOOP-003 | v1 artifact contract and registration merged in PR #3623 (`2708d731fa2a36c186ada68c2fef9f37e877d90b`) |
| EVOLOOP-006 readback | active v1 identity recorded as runtime `runtime-tw-equity-paper`, binding `rb-f13ece22967b4f7baf1329c17d0f4cef`, plan `plan-evoloop-006-promote-20260714b`, pool `pool-tw-equity-paper` |

The v1 registration fixes these inputs: symbols `2330.TW`, `2317.TW`, and
`2454.TW`; daily close data; `lookback_bars=2`; `momentum_threshold=0.0`;
one share per action; BUY when momentum is positive and SELL otherwise.

## Calibration evidence

### Market-data inlet

FinMind `TaiwanStockPrice` was read for each v1 symbol at
2026-07-14T08:17Z with request window `2026-05-01..2026-07-14`. Each response
returned 50 rows and the three series had 50 common sessions
(`2026-05-04..2026-07-14`). The market had closed by the capture time.

The raw provider payload is not committed because the source policy marks it
non-redistributable. Audit identity is preserved through response hashes,
normalized-record hashes, provider attribution, query bounds, and the derived
series hash:

| Symbol | Provider response SHA-256 | normalized source records SHA-256 |
| --- | --- | --- |
| `2330.TW` | `2e90f5847415acfe25fd1cb1d39734c1be8c4cb34f0af12e464b2f29029ea5c4` | `829956902c99d3112bfcd9ce7b0b88326de7131bfff1500356e8c3ed8f604a0c` |
| `2317.TW` | `4bd4a771ccd09c88ee176e5f8084e3b2185827405daf37a9aa5a7174ce7ecb55` | `6510097cb2015c823782001ef7422d96ec4b014141368d51ed3178a56c118eb2` |
| `2454.TW` | `563a017dc8d10d6124553058188c631798bc92ea52d7f44ca84150c9cafcae86` | `afce3f59ecacb061b0edc4a8dc62794a7aa20d9fa160420a7cdbe2c971c093b4` |

Normalized source records were produced with
`FinMindTaiwanDatasetAdapter.records_from_data_payload`, yielding canonical
`tw_price_daily.v1` rows and `finmind://data/TaiwanStockPrice/...` source
references. The derived observation series SHA-256 is
`e52867954446b896b464df571870e0a24803f89b2868061f2fafe528996b9e35`.

### Paper ledger and strategy replay

The calibration used the production paper-runtime ledger scale of
`100000.0 TWD`; `PaperRuntimeService` constructs
`PaperExecutionAlgorithm` with that fixed default. Starting on the second
common close, the production `evaluate_strategy_action` interpreter emitted
one action per symbol and session. Each action was filled and marked at that
session's real close; existing holdings were then repriced by subsequent
real closes.

This produced 147 fills and 49 valued observations from 2026-05-05 through
2026-07-14. Every observation passed `value_portfolio`; the same
`RollingDrawdownTracker(window_days=20)` used by EVOLOOP-002 produced the
drawdown series.

| Statistic | Observed value |
| --- | ---: |
| maximum cumulative PnL | `1940.0 TWD` |
| minimum cumulative PnL | `-3998.0 TWD` |
| final cumulative PnL | `-3918.0 TWD` |
| maximum 20-calendar-day drawdown | `0.03020169453400315` |

This is Research/Ops shadow calibration using real provider observations and
the production computation path. It is deliberately not described as hosted
post-fix telemetry. Hosted readback at 2026-07-14T08:14Z showed the v1 binding
active and heartbeating but without `pnl`, `pnl_at`, `drawdown`, or
`drawdown_at`. EVOLOOP-002's closeout directs EVOLOOP-005 to use observed real
telemetry for PnL calibration, so this absence blocks activation rather than
remaining only a downstream check.

## Governance decision

`EVOLUTION_REVIEW_AND_THRESHOLDS.md` section 7.1 sets the governed breach at
drawdown above `1.25` times the research-expected baseline. It does not supply
the artifact baseline itself. EVOLOOP-005 therefore proposes:

```text
expected_drawdown
  = ceil(max_observed_20d_drawdown * 10_000) / 10_000
  = ceil(0.03020169453400315 * 10_000) / 10_000
  = 0.0303
```

The rounding is outward to one basis point so the stored expectation is not
lower than the observed research maximum.

For comparison only, applying the same `1.25` deterioration buffer to the
shadow series and rounding away from zero to the next 10 TWD yields:

```text
rolling_pnl_floor
  = -ceil(abs(min_observed_pnl) * 1.25 / 10) * 10
  = -ceil(3998 * 1.25 / 10) * 10
  = -5000.0 TWD
```

The result is a five-percent shadow loss boundary on a 100,000 TWD paper
ledger. It is not an approved threshold value and remains out of live config.

Codex2 is the proposer for `expected_drawdown=0.0303`. Per the low-risk path
in `EVOLUTION_REVIEW_AND_THRESHOLDS.md` section 6.1, Codex acts as Reviewer on
Duty and approver. The baseline is not publishable live policy until Codex
approves the calculation and task evidence. PnL activation requires new
canonical telemetry evidence before the task can enter review.

## Rolling PnL floor

`threshold_sweep_thresholds.json` intentionally retains the explicit
uncalibrated `-500.0` placeholder with `enabled=false`. Two independent
conditions prevent the shadow `-5000.0` candidate from replacing it:

1. the active v1 binding has no canonical post-fix `pnl_snapshot` series; and
2. the current worker applies an enabled absolute PnL threshold to every
   eligible paper summary, while the shadow evidence covers only the TW v1
   artifact and TWD ledger.

Activation therefore needs observed, provenance-bearing PnL events plus
either an enforced v1/pool/currency scope or a genuinely global calibration
and approval. Storing an ignored scope field in JSON is not sufficient; the
worker must enforce it and tests must prove foreign summaries cannot fire.

## Config evaluation

The focused regression proves that default config loads the governed drawdown
threshold, keeps the uncalibrated PnL floor disabled, loads the v1 baseline
with its `policy_source`, and evaluates a fresh v1-shaped summary without a
baseline-missing diagnostic. Existing fail-closed behavior for
missing/invalid baselines and stale/missing metrics remains covered by the
threshold-sweep suite.

Focused verification on the task branch:

- `jq empty services/evolution/config/threshold_sweep_baselines.json services/evolution/config/threshold_sweep_thresholds.json` — passed.
- `python3 -m pytest services/evolution/test_threshold_sweep_worker.py -q`
  — 64 passed, with two pre-existing FastAPI `on_event` deprecation warnings.
- `git diff --check` — passed.

The suite includes the default-config v1 evaluation and asserts both an empty
payload and empty diagnostics at `drawdown=0.0303`, `pnl=-4990.0`; this proves
the v1 baseline resolves without masking another validation failure.

## Activation and residual risks

- Blocker: obtain a deduplicated, ordered v1 `pnl_snapshot` series with
  `pnl_at`, binding identity, 100,000 TWD capital scale, and authoritative
  source-ingest mark provenance. EVOLOOP-007 is still `todo`, so the promoted
  strategy has not produced the fills needed for that series. Owner:
  EVOLOOP-002 hosted acceptance + EVOLOOP-007; expiry: before EVOLOOP-005
  review.
- Blocker: before enabling an absolute PnL floor, enforce an artifact/pool
  scope in the worker or approve a global cross-paper calibration. Owner:
  EVOLOOP-005; expiry: before activation.
- No image rebuild is required for the eventual config edit. After an
  approved merge/deployment, restart only `evolution-threshold-sweep-producer`
  so it rereads the bind-mounted config.
- EVOLOOP-002's telemetry `202` durability residual remains unchanged. Owner:
  telemetry delivery-contract follow-up; expiry: before production promotion.
- If paper initial cash becomes configurable or differs from 100,000 units,
  the absolute PnL default must be recalibrated or scoped before rollout.
  Owner: LOOP-PROD-TEL-001; expiry: before configurable capital scale ships.
