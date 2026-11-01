# H12-4923-DRAWDOWN-POLICY-CONFIG-20261101 — chain 4923 `expected_drawdown` low-risk PAPER policy proposal

Status: **proposed / unapproved (`approved_by: null`); the authoritative decision is the assigned Reviewer on Duty's (Antigravity) explicit §6.1 exact-head review decision.**
Author: Claude. Reviewer: Antigravity. Machine-readable twin: `evidence.json`.
Policy path: `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1 low-risk path (existing governance; no new service, schema, or writer).

## Subject and request

Propose `expected_drawdown = 0.0325` for `hosted-all12-h12-claude-20261027-4923-execution-artifact@1.0.0` (paper; strategy
`strat-hosted-all12-h12-claude-20261027-4923-src-106b4057d07a`, symbol `2330.TW`, runtime `rt-824ff162`, binding
`rb-de37d167f15649618d0916899573a4e3`) in `services/evolution/config/threshold_sweep_baselines.json`, with `policy_source`
`docs/deployment/evidence/H12-4923-DRAWDOWN-POLICY-CONFIG-20261101/policy.md#reviewer-decision`.
Existing entries, `threshold_sweep_thresholds.json` (1.25x breach multiple), the drift service, and tests are unchanged.
Registry entry is `approved` (H3 decision `apv-07db6c62179bfb41bcb790001c955022535fe863aa003a8458e1eea8d62c7574`, checksum
`sha256:db3d764e77031c82745deb9d5ab92ae676c2eb0175f33cd1b059c4ec682b9d83`). Hosted D_observe run 41318651764 shows the only
Evolution input coverage gap is the missing approved `expected_drawdown`.

## Frozen inputs and hashes

Coordinator read-only export in `/tmp/pantheon-h12-4923-drawdown-input-20261101` (exported 2026-11-01 15:00Z):

| Item | SHA-256 |
| --- | --- |
| `source-snapshot-line-2791.json` (line 2791 of `/data/source-ingest/latest_market_snapshots.jsonl`, snapshot `mss-5141f1a45068784037802d7d`, `2330.TWSE`, observed_at `2026-11-01T07:29:48Z`, 53 points 2026-08-12..2026-10-30) | `a4ccb57b4ec7dbc652d6f94f0b02a496da42b54207af35cd755023d7ad8567ee` |
| Snapshot state checksum (as recorded in the line) | `8b91a86d7e4d07958ea9024bd3c32e85c1f5bbdc2996484399c40b9617c61a33` |
| Source store `/data/source-ingest/latest_market_snapshots.jsonl` (`source-store-sha256.txt`, itself `6ae0b0c5…664cb`) | `21e5605f8658048b6a09b2ad26a43b6df3db45aa85ea694fab135b1cd56de347` |
| `execution-artifact-readback.json` (Registry entry + embedded strategy_artifact) | `76809a5e5996a4e599809f7ecb1ad6ba3379c85c6c3fa538fa36714780bdddf7` |
| Committed `input.json` (155 lines; built by copying per-point fields verbatim, `date` = `event_time` date) | `cbee8947196d4b451cca54f3f946e34610de7c4e44635f38b543b1c7254ecd2a` |
| `calc.py` from H12-DRAWDOWN-POLICY-CONFIG-20261025, reused unchanged, not copied | `966b31ed2b7ad006bdc31361321351e728c4e5f5ae1b4e68ce0f0a45875f3d55` |
| Replay `--series` output file | `01c358ae203a5bec0ec9095be5b526a9393936985fcf3006f101dabf59d82137` |
| Replay `series_sha256` (canonical) | `7e64acb2c95319e9d3585f029ba2ae5b4f99cb2d0675e87bd31fd854cfb0f4d3` |

Reproduce against command runtime `c8513d2819dad3cc37b48d3f11268f7f1fb6d656`:

```bash
python3 -I docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/calc.py \
  /home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/command-runtimes/c8513d2819dad3cc37b48d3f11268f7f1fb6d656 \
  docs/deployment/evidence/H12-4923-DRAWDOWN-POLICY-CONFIG-20261101/input.json 05:30:00 --series /tmp/reproduced-series.json
```

## Replay summary

53 bars (2026-08-12..2026-10-30), 52 observations and 52 simulated fills (11 BUY, 41 SELL), final position -30 shares,
`max_pnl = 1815.0`, `min_pnl = -2935.0`, `final_pnl = 1815.0` TWD, maximum 20-calendar-day rolling drawdown
`0.032494393222028406` on 2026-10-06, outward-rounded `ceil(max * 10000) / 10000 = 0.0325` (1.25x trigger = `0.040625`).
This matches the coordinator's pre-review run exactly (no difference to report).

## Basis and limits

- Strategy: unmodified artifact, `close_to_close_momentum`, `lookback_bars=2`, `momentum_threshold=0.01`, `order_quantity=1` SHARES, `zero_momentum_action=SELL`.
- Offline gross replay of 53 TWSE closes; 1 share against 100,000 TWD initial cash; no fee, tax, commission, or slippage; the interpreter permits negative holdings.
- Marks use an explicit `05:30:00Z` simulated close time per point date; original `event_time` values are not rewritten.
- Paper only. Not a hosted fill history, not hosted -30 share holdings, not 20 observed hosted trading days, not an alpha/profit claim. `learning_eligible = false`.
- The newer dev tree rejects synthetic historical marks via the Taiwan freshness guard (`stale_or_future_market_marks`), as explained in the 20261025 policy.md; the guard is not relaxed and no `observed_at` is fabricated. The number is bound to the `c8513d28…` numeric functions, which are unchanged in arithmetic.
- The Source store SHA-256 and snapshot checksum are recorded from the coordinator export; this task did not re-read `/data/source-ingest`.
- Hosted publication is later performed by the operator through a Root deploy; this task has no VM, SSH, runtime, or business POST authority.

## Reviewer decision

`approved_by` is intentionally `null`. Under `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1 the assigned Reviewer on Duty (Antigravity) must decide in canonical exact-head review evidence whether `expected_drawdown = 0.0325` for this artifact (PAPER only) is approved, or reject/reopen with reasons.

- Decision in this commit: _pending — recorded by the reviewer in exact-head review notes for `H12-4923-DRAWDOWN-POLICY-CONFIG-20261101`._
- If rejected: remove the config entry and retain the evidence record.
