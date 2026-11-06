# PAPER-PERSONA-BASELINES-20261105 — Three Active Paper Persona `expected_drawdown` Low-Risk PAPER Policy Review

Status: **approved by the operator on 2026-11-05 (Reviewer on Duty, 6.1); see Reviewer decision below.**
Author: Antigravity2. Assigned reviewer: Claude (code review covers the replay and evidence: the reviewer re-runs the replay independently and must not record itself as the policy approver). Machine-readable twin: `evidence.json`.
Path: `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1 low-risk path, Reviewer on Duty (existing governance; no new policy service, schema, queue, or writer).

## Subject and request

Publish approved `expected_drawdown` baselines for the three real active paper persona execution artifacts in `services/evolution/config/threshold_sweep_baselines.json`, with `policy_source` `docs/deployment/evidence/PAPER-PERSONA-BASELINES-20261105/policy.md#reviewer-decision`. Why these three: they are the real paper personas; of 126 persona creations on hosted, 112 are deploy probes with `data_scope=test` that retire on the next deploy and do not get baselines.

| Case | Artifact ID (`@1.0.0`) | Strategy ID | Symbol | Runtime ID | Binding ID | Persona ID | `deployment_plan_id` / `plan_id` | `persona_capital_binding_id` | `capital_pool_id` | Registry Checksum | Approval Decision ID | `expected_drawdown` | 1.25x Breach Trigger |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qinghe-2330` | `artifact-persona-paper-298e138c136c9840b0e4` | `strategy-persona-298e138c136c9840b0e4` | `2330.TWSE` | `rt-535a82a3` | `rb-a1c3ada3d5894d57bc2e012f337753be` | `persona-be19a1fac471247f2b2a` | `plan-persona-paper-298e138c136c9840b0e4` | `pcb-persona-paper-298e138c136c9840b0e4` | `pool-persona-paper-298e138c136c9840b0e4` | `sha256:a11791e9d18c0f0dcec765a7df477a7fc65ae0a66f7c892f777ce01a162db880` | `apv-persona-paper-artifact-298e138c136c9840b0e4` | `0.0027` | `0.003375` |
| `hengyue-0050` | `artifact-persona-paper-01390a4b72713295b001` | `strategy-persona-01390a4b72713295b001` | `0050.TWSE` | `rt-8b0d8649` | `rb-fd733d7c192f45898bbe0e90c8555878` | `persona-9d4fb3f44cbc5e7cb84f` | `plan-persona-paper-01390a4b72713295b001` | `pcb-persona-paper-01390a4b72713295b001` | `pool-persona-paper-01390a4b72713295b001` | `sha256:d6bb93411b738b9f50022ecf473be327ffb07aa017eca01a14cb8f503becbe1b` | `apv-persona-paper-artifact-01390a4b72713295b001` | `0.0002` | `0.00025` |
| `chengchuan-2317` | `artifact-persona-paper-8ae996980eeccb80d928` | `strategy-persona-8ae996980eeccb80d928` | `2317.TWSE` | `rt-c98e4223` | `rb-eda9b9a3379d4fb5a09f0e8432c84f20` | `persona-d9363cffc0efbbbcbeb9` | `plan-persona-paper-8ae996980eeccb80d928` | `pcb-persona-paper-8ae996980eeccb80d928` | `pool-persona-paper-8ae996980eeccb80d928` | `sha256:2383a5084ab3110d83282accddb168e724e3174e9a8cfece3fbb8f84dada6040` | `apv-persona-paper-artifact-8ae996980eeccb80d928` | `0.0011` | `0.001375` |

All four existing entries (`artifact-tw-session-momentum-v1 = 0.0303`, `artifact-persona-paper-00400aa279b49774cde4 = 0.0027`, `hosted-all12-h12-01a11-20261023-9aeb-execution-artifact = 0.0325`, `hosted-all12-h12-claude-20261027-4923-execution-artifact = 0.0325`) and `services/evolution/config/threshold_sweep_thresholds.json` (`1.25x` breach multiple; `rolling_pnl_floor` disabled placeholder) are unchanged. `reconciliation-drift` keeps reading `services/evolution/config/threshold_sweep_baselines.json` through `RECONCILIATION_DRIFT_APPROVED_BASELINES_PATH`; no second copy of a baseline is added.

## Frozen inputs and hashes

Coordinator read-only export in `/tmp/pantheon-persona-baselines-input-20261105`. Each `input/<name>.input.json` (committed byte-identical as `input-<name>.json` in record-per-line layout: 170, 157, and 170 lines) replaced the earlier `input-indent1-superseded/<name>.input.json` whitespace variant specifically to satisfy the evidence line cap (`json.load` parsed content, raw snapshots, `max_drawdown`, `series_sha256`, and approved values are identical between both variants):

| Item | SHA-256 / Value |
| --- | --- |
| Source store `/data/source-ingest/latest_market_snapshots.jsonl` (recorded in `source-store-sha256.txt`, itself `2cb89b39c727402de79e9dfd32f44d55036d9c9920eba6ca9b2fdefe094f3c18`) | `ac9739daf419be8dd33f061dc2f497e40ac3b5534dc305a966bbf787c7676b1e` |
| `docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/calc.py` (reused unchanged, not copied) | `966b31ed2b7ad006bdc31361321351e728c4e5f5ae1b4e68ce0f0a45875f3d55` |
| `qinghe-2330/source-snapshot-line-2809.json` (snapshot `mss-5b15382be257a50c73f65fc3`, `2330.TWSE`, `observed_at = 2026-11-04T15:06:14Z`, state checksum `afe3a00143143bf6c09472f70f17daaad5dfd4140bf9576393acb23fa624f61f`) | `06241aec67482221554e075ac967a81b2cb7cd866767eaee57dee18d7244a492` |
| `qinghe-2330/execution-artifact-readback.json` | `1cc8029527caca4cd62e48bca32277f583d14c9c4d7f38a363e5ff15816da8c9` |
| Committed `input-qinghe-2330.json` (170 lines; replaced superseded whitespace-variant `776f3a927b8fa04e71704f7f1bd5cc2dc7f7cf9b921bb9d34be718e2d163011c` for the evidence line cap) | `4b29bb9817916e5e3525fa4669623e9bbe9a3b8393668f8ec9145c74e8f96ec8` |
| `qinghe-2330` replay `series_sha256` (canonical; `--series` file `970efaec24b7a5ff01f2d07ca9e98eabc1dc3b3bcbae24aa6cfc86afcacce0ca`) | `a8369060567c85183bcc1b5171f27c2eac34bb7603fdd3ae5115deed654ff6b3` |
| `hengyue-0050/source-snapshot-line-2807.json` (snapshot `mss-813cb8424a77db08bcafefac`, `0050.TWSE`, `observed_at = 2026-11-04T15:06:14Z`, state checksum `bcb301bcf1c7afad1b89ef5ea9e3a88d84a8f75e2d5c40df8349844ef35e4a2b`) | `81edad1ea409c54abe393fcd11420dcb12ebc07ab3f82b615e7d3844dcc61840` |
| `hengyue-0050/execution-artifact-readback.json` | `2204b0b3dc94047504b339e924ab82048f3dc2a53c1fc090929d662ebb47ca17` |
| Committed `input-hengyue-0050.json` (157 lines; replaced superseded whitespace-variant `6ac0f6c62aa1859478dadb0077bb51f7d663a79eebf294e8e5ba880f2123ec05` for the evidence line cap) | `fafee4ad94ecb74fe89238b34d19c659214c00bfec8a983a6f88003077aa98ad` |
| `hengyue-0050` replay `series_sha256` (canonical; `--series` file `d75ad41d79c9eaad34c48bb8d9c684886185082b7b7009ea5e26b6e6d42580be`) | `73d1909cd469aa851008d4a2f549df2c0ad091c9ac9bce3288469ea8ea3af653` |
| `chengchuan-2317/source-snapshot-line-2808.json` (snapshot `mss-c9e5f131378603f0829a5524`, `2317.TWSE`, `observed_at = 2026-11-04T15:06:14Z`, state checksum `85c711cb9bf887716787c11187d849876eb8cf0363c6982ed78eb1adcf17365b`) | `bf7a016f3b34594b216da73cade602fcb9448c36703ae1a4527a55da2b77de38` |
| `chengchuan-2317/execution-artifact-readback.json` | `109624e9c43e4f224a117b267aeb3252bc0a8ae46d0b749ff201738d477fcae9` |
| Committed `input-chengchuan-2317.json` (170 lines; replaced superseded whitespace-variant `7f1043f71c68adc99f2a49baf8d4e8bb885b0d58e8dbf99bce7a7cdc6c1e914a` for the evidence line cap) | `379822f912d24f18e6e8cfef67720f4e2c6475e8d3ca9778fd821cdd1ab1696a` |
| `chengchuan-2317` replay `series_sha256` (canonical; `--series` file `58da7859a47f5cf5475c984bd8e361585ef86a40e037e41e4a2f16b1a9a55939`) | `8d18429418abec3af8e4b1b44be36ed3d9d56ad6ebde97661a7635857c17d006` |

Reproduce against command runtime `c8513d2819dad3cc37b48d3f11268f7f1fb6d656`:

```bash
python3 -I docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/calc.py \
  /home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/command-runtimes/c8513d2819dad3cc37b48d3f11268f7f1fb6d656 \
  docs/deployment/evidence/PAPER-PERSONA-BASELINES-20261105/input-qinghe-2330.json 05:30:00 --series /tmp/reproduced-qinghe-2330.json

python3 -I docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/calc.py \
  /home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/command-runtimes/c8513d2819dad3cc37b48d3f11268f7f1fb6d656 \
  docs/deployment/evidence/PAPER-PERSONA-BASELINES-20261105/input-hengyue-0050.json 05:30:00 --series /tmp/reproduced-hengyue-0050.json

python3 -I docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/calc.py \
  /home/chloe_ong_dev_cctech_support_com/pantheon-ci-deploy/command-runtimes/c8513d2819dad3cc37b48d3f11268f7f1fb6d656 \
  docs/deployment/evidence/PAPER-PERSONA-BASELINES-20261105/input-chengchuan-2317.json 05:30:00 --series /tmp/reproduced-chengchuan-2317.json
```

## Replay summary

| Case | Bars & Window | Observations / Fills | Actions | Final Pos (Shares) | `max_pnl` (TWD) | `min_pnl` (TWD) | `final_pnl` (TWD) | `max_drawdown` | Max DD Date | Outward `ceil_bp` (`ceil(max * 10000) / 10000`) | 1.25x Breach Trigger |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `qinghe-2330` | 56 (`2026-08-12`..`2026-11-04`) | 55 / 55 | 24 BUY, 31 SELL | `-7.0` | `600.0` | `-245.0` | `600.0` | `0.002699325168707823` | `2026-09-16` | `0.0027` | `0.003375` |
| `hengyue-0050` | 43 (`2026-08-31`..`2026-11-04`) | 42 / 42 | 17 BUY, 25 SELL | `-8.0` | `29.84999999999127` | `-14.300000000017462` | `29.84999999999127` | `0.00017649152840672381` | `2026-10-02` | `0.0002` | `0.00025` |
| `chengchuan-2317` | 56 (`2026-08-12`..`2026-11-04`) | 55 / 55 | 23 BUY, 32 SELL | `-9.0` | `55.0` | `-59.5` | `23.0` | `0.0010894607169451122` | `2026-11-02` | `0.0011` | `0.001375` |

All three replays match the coordinator's `EXPECTED.json` values exactly (no difference to report).

## Basis and limits

- Strategy logic across all three unmodified artifacts: `close_to_close_momentum`, `lookback_bars=2`, `momentum_threshold=0.0`, `order_quantity=1` SHARES, `zero_momentum_action=SELL`, `bar_frequency=1d`, `data_source=source-ingest:paper/persona-bootstrap`.
- Offline gross replay of 43 or 56 daily TWSE closes; 1 share against `100000` TWD initial cash per artifact; no fee, tax, commission, or slippage; the interpreter permits negative share holdings.
- Marks use an explicit `05:30:00Z` simulated close time per point date; original `event_time` values are not rewritten.
- Paper only (`deployment_stage = paper`). Not a hosted fill history, not hosted `-7` / `-8` / `-9` share holdings, not 20 observed hosted trading days, not net-of-cost, not an alpha or profit claim, and does not transfer to live trading or any other position sizing. `learning_eligible = false`.
- Honest statistical and scaling limits: the same market window produced the value so it is not an out-of-sample estimate; `hengyue-0050` is small (`0.0002`, 2 bps) mostly because `0050.TWSE` has a low per-share price against `100000` TWD initial cash and trades one share.
- The newer `dev` tree rejects synthetic historical marks via the Taiwan freshness guard (`stale_or_future_market_marks`), as documented in `H12-DRAWDOWN-POLICY-CONFIG-20261025/policy.md`; the guard is not relaxed and no `observed_at` is fabricated. The replay numbers are bound to the `c8513d2819dad3cc37b48d3f11268f7f1fb6d656` runtime arithmetic, which is unchanged in drawdown calculation.
- Source store SHA-256 and snapshot checksums are recorded from the coordinator export; this task did not access the VM or re-read `/data/source-ingest`. Hosted activation is done later through a Root deploy and checks `baseline_metric_count = 1` for these three bindings.

## Validation (existing code paths only; no code, test, or compose edits)

- `load_baselines()` and `approved_baseline_value()` return `0.0027`, `0.0002`, and `0.0011` for the three new paper persona artifacts while preserving all four prior entries (`0.0303`, `0.0027`, `0.0325`, `0.0325`).
- Using the real hosted summary shape read from hosted telemetry at `2026-11-05T16:48Z` (`deployment_stage="paper"`, `state="active"`, `connectivity_status="connected"`, `last_heartbeat_at`, `drawdown`, `drawdown_at`, `drawdown_binding_id == binding_id`, `drawdown_event_id`, `artifact_id`, `artifact_version="1.0.0"`, `binding_id`, `runtime_id`, `capital_pool_id`, `deployment_plan_id`, `plan_id`, `persona_capital_binding_id`) with current timestamps, `assess_input_coverage()` returns `coverage_complete = true` (`monitored_artifacts = 3`, `complete_artifacts = 3`, `incomplete_artifacts = 0`) and marks each artifact's `rolling_drawdown_multiple` metric `evaluable = true`.
- The same summary at `deployment_stage="live"` or with a stale heartbeat still fails closed (`coverage_complete = false`, `complete_artifacts = 0`, `0` breach payloads).
- `pytest services/evolution/test_threshold_sweep_worker.py` passes (`89 passed`).

## Reviewer decision

Following `docs/deployment/evidence/PAPER-DRAWDOWN-CURRENT0040-CONFIG-20261021/policy.md` and `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1:

- Decision: APPROVED on 2026-11-05 by the operator (`ajoe734`; a human acting as Reviewer on Duty under `EVOLUTION_REVIEW_AND_THRESHOLDS.md` 6.1). Operator decision 2026-11-05 (quoted): the operator was shown these three values with their algorithm and limits and selected `'approve the three values'` (`核准三個數值`): `qinghe 2330 baseline 0.27% trigger 0.3375%; hengyue 0050 baseline 0.02% trigger 0.025%; chengchuan 2317 baseline 0.11% trigger 0.1375%`. Scope exactly as stated in this document: gross research-calibrated `expected_drawdown` baselines in `services/evolution/config/threshold_sweep_baselines.json` for `artifact-persona-paper-298e138c136c9840b0e4@1.0.0 = 0.0027` (`2330.TWSE`; PAPER only; 1 share against 100000 TWD; breach trigger 1.25x = `0.003375`), `artifact-persona-paper-01390a4b72713295b001@1.0.0 = 0.0002` (`0050.TWSE`; PAPER only; 1 share against 100000 TWD; breach trigger 1.25x = `0.00025`), and `artifact-persona-paper-8ae996980eeccb80d928@1.0.0 = 0.0011` (`2317.TWSE`; PAPER only; 1 share against 100000 TWD; breach trigger 1.25x = `0.001375`). Not a net-of-cost, live, other-sizing, or other-artifact approval, and claims no other approval. Code review covers the replay and evidence: the assigned code reviewer (`Claude`) re-runs the replay independently and must not record itself as the policy approver.
- If rejected: remove the config entries; keep this file as the record of the rejection.
