# H12-DRAWDOWN-POLICY-CONFIG-20261025 — H12 `expected_drawdown` low-risk PAPER policy review

Status: **proposed / unapproved in this pre-review commit (`approved_by: null`); authoritative actual policy decision is the assigned Reviewer on Duty's (`Antigravity2`) explicit §6.1 exact-head review decision (`review_notes_zh` / `approve`) on this frozen PR head before publication and merge.**
Author: Antigravity. Assigned Reviewer on Duty: Antigravity2. Machine-readable twin: `evidence.json`.
Policy path: `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1 low-risk path, Reviewer on Duty (existing governance path; no new policy service, approval framework, schema, queue, or writer).

## Subject and request

Propose `expected_drawdown = 0.0325` for the H12 paper execution artifact
`hosted-all12-h12-01a11-20261023-9aeb-execution-artifact@1.0.0` (strategy
`strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8`, symbol `2330.TW`, runtime `rt-eb295fe2`, binding
`rb-eea1cd16cf3745479f42e8b4440b3c27`, stage `paper`) in `services/evolution/config/threshold_sweep_baselines.json`,
with `policy_source` pointing to `docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/policy.md#reviewer-decision`.

Existing baseline entries (`artifact-tw-session-momentum-v1 = 0.0303` and
`artifact-persona-paper-00400aa279b49774cde4 = 0.0027`), the `1.25` breach multiple in
`threshold_sweep_thresholds.json`, the disabled `rolling_pnl_floor` placeholder, and the existing config schema are
preserved unchanged. Prior `0040` (`0.0027`) and `EVOLOOP-005` (`0.0303`) values and approvals are **not** reused.

## Frozen inputs, provenance, and reproduction hashes

All archived research inputs from `/tmp/pantheon-codex-01a11-review-20261023/h12-drawdown-research-20261025` were
verified by SHA-256 and reproduced without any provider, model, or network call:

| Item / Path | SHA-256 |
| --- | --- |
| Frozen artifact bytes (`/tmp/pantheon-codex-01a11-review-20261023/h12-drawdown-research-20261025/frozen-artifact.bin`, Registry key `openclaw/registry/strat-hosted-all12-h12-01a11-20261023-9aeb-src-e018ff6d0eb8/1.0.0/artifact.bin`) | `fa3a3cdf56c61c8ada5cf29863a89204e13aafa89845b98fe60bff419c6aed75` |
| Source snapshot receipt (`/tmp/pantheon-codex-01a11-review-20261023/h12-drawdown-research-20261025/source-exact-checksummed-dated-points-readonly.json`) | `0ee3b9ddf6cef4b3bd3d39be97ffd44f36419512e5a68a71850fe795d8d3ffc0` |
| Source snapshot `mss-ff49b0e9e567bba38063946a` state checksum (verified via `LatestMarketSnapshot.from_dict`; store SHA-256 `e2b298b598d2c0dc724291468f88f3155a72a4cbc9a4146e3cd756418b7c0aa1` at `/data/source-ingest/latest_market_snapshots.jsonl` line 2593) | `fc0d5b2375e622a44d723d67cd1b66085f7273a96265dbde8ae20596ee3fb146` |
| Sanitized input (`/tmp/pantheon-codex-01a11-review-20261023/h12-drawdown-research-20261025/sanitized-input.json`, `indent=2`) | `97399386b32b4da4a7f6a09078d36a548732f1e4013a104ff96dc7b32da5f727` |
| Committed `docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/input.json` (138 lines, 1 line per bar record for `diff_budget.py` ≤400-line cap; `json.dumps(json.load(open("input.json")), indent=2) + "\n"` reproduces `97399386…` byte-for-byte) | `25dfdcb23840e39451fe2208fd524df7b7d3342013be37e571ce8fb23454cc74` |
| Verbatim replay script `docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/calc.py` (from commit `c3cc35862cf66e45b6dff419d6a0b3e0eb77ab90`) | `966b31ed2b7ad006bdc31361321351e728c4e5f5ae1b4e68ce0f0a45875f3d55` |
| Frozen source & method manifest (`frozen-source-and-method.json`) | `9af37b32d8586f1dbe1e3e1c78249b295a1e86c0f49f8791919946dc2f914341` |
| Replay execution receipt (`replay-execution.json`) | `93d0881ea6c459e75bc9d98d7792de7394481627915ab7007758a19c74e591ee` |
| Replay summary (`result.json`) | `c498cdd1dc85db32270cf02d2d764a1ad19845e3a7dde3066ea43e614e498f0f` |
| Replay 48-observation file (`series.json`) / `series_sha256` | `4ae0c58bbad6d4a53114e1d8bc4d2605adc110bf749d2a5c42eb834dc73292c4` / `50d27a43f8083c214e8b7b6b7b21d9183cab94ef1e28092ae8f41d95c1184783` |
| Proposal brief (`proposal-for-independent-review.md`) | `d782e75a1336ff2f3a0b23b9b69178f115302574b0992b636f56105025a3ce41` |
| Input evidence hash manifest (`input-evidence-hashes.json`) | `7ac7beab55da1f8b6ed2730453a06531afd94e26c9b76d5513d679b66994e19a` |
| Independent static source comparison (`independent-source-compatibility-advice.json`) | `c15cf6856c2d67a641f46e5cce70162cb6c62ba1389c1c27f8c938fd6bcb6ae1` |
| Latest-code identity clarification (`latest-admission-vs-unchanged-numeric-code-native-task-handoff.json`) | `07304a6c0a59f20be69f93c85734d52a624f157879e938dc2d732bbf651debfd` |
| D402214 Evolution input coverage GET receipt (`D402214-same-execution-target-Evolution-input-coverage-GET-only-20261025.json`) | `e40489be1d13329d3877b932165c7f7c91f9ef18007cb6d1601b4232258a4ef3` |

Reproduce offline against the exact A1 / frozen `c8513d2819dad3cc37b48d3f11268f7f1fb6d656` production tree:

```bash
python3 -I docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/calc.py \
  /home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/command-runtimes/c8513d2819dad3cc37b48d3f11268f7f1fb6d656 \
  docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/input.json \
  05:30:00 --series /tmp/reproduced-series.json
```

## Basis, strategy parameters, ledger assumptions, and explicit limits

- **Strategy & parameters (unmodified frozen artifact `fa3a3cdf…`):** `services.registry.strategy_artifact:evaluate_strategy_action`,
  `close_to_close_momentum`, `symbols=["2330.TW"]`, `bar_frequency="1d"`,
  `data_source="source-ingest:normalized/taiwanstockprice/finmind-daily"`, `lookback_bars=2`,
  `momentum_threshold=0.01`, `order_quantity=1`, `quantity_type="SHARES"`, `zero_momentum_action="SELL"` (BUY when
  2-bar close-to-close momentum `> 0.01`, else SELL).
- **Market data provenance:** 49 original checksummed `state.points` from Source snapshot `mss-ff49b0e9e567bba38063946a`
  (`2026-08-12` .. `2026-10-22`), retaining per-point `event_time`, `close`, `source_id`, `connector_id`
  (`tw-twse-tpex-official-market`), `content_ref`, and `ingest_run_id` (no zipping of independently sorted arrays).
  `05:30:00Z` is an explicit historical daily close simulation timestamp assumption; original Source `event_time` is not
  rewritten. Market-data-only offline simulation: `learning_eligible = false`.
- **Gross paper ledger & shorting behavior:** `initial_cash = 100000.0` TWD, gross `MARKET`-at-close fills with no fee,
  tax, commission, or slippage modelled; original production SELL behavior allows negative holdings (short position).
  Across 49 bars (`2026-08-12` .. `2026-10-22`), the replay produces **48 observations and 48 simulated fills** (`10 BUY`,
  `38 SELL`), ending at `-28.0` shares (`max_pnl = 1375.0`, `min_pnl = -2935.0`, `final_pnl = 1375.0` TWD) and maximum
  20-calendar-day rolling high-water drawdown **`0.032494393222028406`** on `2026-10-06`, rounded outward 1 bp
  (`ceil(max * 10000) / 10000`) = **`0.0325`** (1.25x breach trigger = `0.040625`).
- **Explicit distinctions:** This is a pure offline simulation, **not** a Research-owner API backtest, **not** hosted
  fills or hosted `-28` share holdings, **not** 20 observed hosted trading days, and **not** a live profit or alpha
  quality acceptance claim. `calc.py` is copied verbatim as evidence, not a new product CLI or service.

## Production code comparison and PR #6805 admission boundary

At previous accepted production `a1b80a026650148bfa85044514e464e6c8303792` (A1), current merged `origin/dev` / current
BFF source `82001a8659940e433db177d003469462de535ec8` (`release/v2026.10.25.6`, while `root40226368878` is in flight),
and command runtime `c8513d2819dad3cc37b48d3f11268f7f1fb6d656`, all four production files match exact A1 SHA-256s:
`strategy_artifact.py` (`f64918b2…`), `performance_telemetry.py` (`664f6aa7…`), `paper_runtime.py` (`9d0d7dcc…`), and
`paper_simulation.py` (`ae42327c…`).

In disjoint task `PAPER-MTM-GOVERNED-CALENDAR-20261025` (PR #6805, commit `4b11f6b1dc4c3e5d70b93f9d5c0227f2d48a999d`),
`performance_telemetry.py` (`9329e2a4c…`) adds only the shared Taiwan market-session freshness guard (`_is_mark_fresh`
delegating to `evaluate_taiwan_market_freshness` using `observed_at`, `lineage`, and `calendar_evidence`). Static source
comparison (`independent-source-compatibility-advice.json`) confirms `evaluate_strategy_action`, `PerformanceSample`,
`ValuationResult`, `RollingDrawdownTracker`, and `value_portfolio`'s cash/position/PnL/drawdown arithmetic are unchanged.
Because verbatim `calc.py` builds synthetic historical `MarketMark` rows without per-point refresh receipts
(`observed_at`) or `lineage`, PR #6805's admission guard rightly rejects synthetic marks on that tree; we do not
fabricate historical `observed_at` or relax the validator. The numeric baseline remains bound to the exact A1 /
`c8513d28…` calculation functions (`82001a86…` on `origin/dev`).

## Validation and delivery boundary

Validated `services/evolution/config/threshold_sweep_baselines.json` with `load_baselines()`,
`approved_baseline_value()`, `evaluate_breaches()`, and `assess_input_coverage()`, plus
`pytest -q services/evolution/test_threshold_sweep_worker.py` (all passed):
- With baseline present: `approved_baseline_value` = `0.0325` (`0.0303` and `0.0027` preserved); `0` payloads and `0`
  diagnostics on non-breaching paper summary; `assess_input_coverage` marks `has_approved_baseline=True`.
- Missing baseline / `deployment_stage=live` / stale (`>2d`): `0` payloads and expected fail-closed diagnostics.
- No forced freshness/price-date/baseline incident, no breached positive, and no manual sweep.

Source workers have no VM, SSH, runtime mutation, business POST, model, provider, cloud, or Root authority. `Root01a11`
alone later publishes the merged config through the existing bind-mount and verifies natural producer coverage. Merging
this config does **not** complete `all12` or the paused 9-loop `D402214` / `H4` / `E` / `TOTAL` obligations.

## Reviewer decision

`approved_by` is intentionally `null` in this committed file and no human, Root, or generic code-review decision is
claimed as policy approval. Under `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1, the assigned independent Reviewer on Duty
(`Antigravity2`) must explicitly decide in canonical exact-head review evidence (`REVIEW_NOTES_ZH` / `approve` on the
frozen `delivery_binding` head of task `H12-DRAWDOWN-POLICY-CONFIG-20261025`) whether this exact **`expected_drawdown =
0.0325` gross 49-bar research-calibrated baseline for `hosted-all12-h12-01a11-20261023-9aeb-execution-artifact@1.0.0`
(`2330.TW`, `PAPER` scope only, 1 share against 100,000 TWD, 1.25x breach trigger = `0.040625`)** is approved under §6.1,
or reject/reopen it with concrete reasons (honoring rejection and manual-only rules if applicable).

- Decision in pre-review commit: _pending — authoritative actual §6.1 Reviewer on Duty decision is recorded in canonical exact-head review notes (`review_notes_zh` / `review_binding` for `H12-DRAWDOWN-POLICY-CONFIG-20261025`) by `Antigravity2` before merge and Root publication._
- If rejected: reopen and remove the config entry; retain the evidence record of the rejection.
