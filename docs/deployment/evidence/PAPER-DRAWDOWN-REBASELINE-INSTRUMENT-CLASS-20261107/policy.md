# PAPER-DRAWDOWN-REBASELINE-INSTRUMENT-CLASS-20261107 — Per-Artifact and Per-Instrument-Class Drawdown Baseline Policy

## Scope and Trigger Multiple

This policy governs the per-artifact, per-instrument-class `expected_drawdown` baselines added to `services/evolution/config/threshold_sweep_baselines.json` under `baselines_by_instrument_class`, while preserving all seven existing flat entries under `baselines` unchanged.

- **Metric**: `rolling_drawdown_multiple` (`summary_field: "drawdown"`, `ratio_baseline_key: "expected_drawdown"`, `comparator: "gt"`).
- **Trigger multiple**: **`1.25`** (`threshold_value: 1.25` per `EVOLUTION_REVIEW_AND_THRESHOLDS.md` section 7.1 and `services/evolution/config/threshold_sweep_thresholds.json`). A breach occurs only when `observed_drawdown / expected_drawdown > 1.25` (equivalently `observed_drawdown > 1.25 * expected_drawdown`).
- **Fail-closed guarantee (no fabricated baseline)**: No baseline is ever fabricated for an artifact or instrument class without an approved value. When a runtime summary omits its instrument class on an instrument-class-governed artifact, reports an ambiguous class, or reports an instrument class or artifact without a positive finite approved baseline and `policy_source`, `threshold_sweep_worker` fails closed with an explicit diagnostic and opens **no incident**.

## Replay Sequences and Arithmetic (`100,000` TWD Capital, 1 Unit per Signal)

Derived from the six real fill sequences in `SD_PERSONA_INSTRUMENT_DECISION_2026-11-07.md` §8 at `100,000` TWD starting capital and `1` unit held from fill 1:

1. **`2330` (`qinghe-2330`, `artifact-persona-paper-298e138c136c9840b0e4@1.0.0`)** — input: `input-2330.json`
   - Fill price sequence: `2370 -> 2330 -> 2295 -> 2260 -> 2240 -> 2135` (move `-235` points from `2370` to `2135`).
   - `cash_share` (`2330.TWSE`, multiplier `1`): PnL at trough `-235`, worst equity `99,765`, `expected_drawdown = (100000 - 99765) / 100000 = 0.00235`, `1.25x` breach trigger `= 0.0029375`.
   - `stock_future` (`QFF`, multiplier `100` TWD/pt): PnL at trough `-23,500`, worst equity `76,500`, `expected_drawdown = (100000 - 76500) / 100000 = 0.235`, `1.25x` breach trigger `= 0.29375`.

2. **`0050` (`hengyue-0050`, `artifact-persona-paper-01390a4b72713295b001@1.0.0`)** — input: `input-0050.json`
   - Fill price sequence: `104.95 -> 104.1 -> 100.6 -> 99.85 -> 97.15 -> 92.5` (move `-12.45` points from `104.95` to `92.5`).
   - `cash_share` (`0050.TWSE`, multiplier `1`): PnL at trough `-12.45`, worst equity `99,987.55` (`99,988` rounded TWD), `expected_drawdown = (100000 - 99987.55) / 100000 = 0.0001245`, `1.25x` breach trigger `= 0.000155625`.
   - `stock_future` (`SRF`, multiplier `1000` TWD/pt): PnL at trough `-12,450`, worst equity `87,550`, `expected_drawdown = (100000 - 87550) / 100000 = 0.1245`, `1.25x` breach trigger `= 0.155625`.

3. **`2317` (`chengchuan-2317`, `artifact-persona-paper-8ae996980eeccb80d928@1.0.0`)** — input: `input-2317.json`
   - Fill price sequence: `245.5 -> 251 -> 245.5 -> 240 -> 235 -> 224.5` (move `-21.0` points from entry `245.5`, `-26.5` points from peak `251.0`).
   - `cash_share` (`2317.TWSE`, multiplier `1`): PnL at trough `-21.0`, worst equity `99,979`, `expected_drawdown = (100000 - 99979) / 100000 = 0.00021`, `1.25x` breach trigger `= 0.0002625` (peak-to-trough from `100,005.5` is `0.000264985...`).
   - `stock_future` (`DHF`, multiplier `2000` TWD/pt): PnL at trough `-42,000`, worst equity `58,000`, `expected_drawdown = (100000 - 58000) / 100000 = 0.42`, `1.25x` breach trigger `= 0.525` (peak-to-trough from `111,000` high-water mark is `53000 / 111000 = 0.477477...`).

## Artifacts That Must Be Re-Provisioned Before a `stock_future` Baseline Applies

Per `SD_PERSONA_INSTRUMENT_DECISION_2026-11-07.md` §5 and `DEV_PLAN_INSTRUMENT_EXPRESSION_LAYER_2026-11-07.md` §1 & §4 (T9 `PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107`), deployed `operating_context` and `RuntimeBinding` records are immutable after provisioning. The three currently deployed Taiwan paper artifacts below are bound to cash-only operating contexts and **must be retired and re-provisioned** with `expression_layer_enabled: true` and `allowed_instruments: ["cash_share", "stock_future"]` before a `stock_future` baseline applies in paper runtime:

1. `artifact-persona-paper-298e138c136c9840b0e4` (`qinghe-2330`, `persona-be19a1fac471247f2b2a`, current binding `rb-a1c3ada3d5894d57bc2e012f337753be`, underlying `2330.TWSE`, stock future `QFF`).
2. `artifact-persona-paper-01390a4b72713295b001` (`hengyue-0050`, `persona-9d4fb3f44cbc5e7cb84f`, current binding `rb-fd733d7c192f45898bbe0e90c8555878`, underlying `0050.TWSE`, stock future `SRF`).
3. `artifact-persona-paper-8ae996980eeccb80d928` (`chengchuan-2317`, `persona-d9363cffc0efbbbcbeb9`, current binding `rb-eda9b9a3379d4fb5a09f0e8432c84f20`, underlying `2317.TWSE`, stock future `DHF`).

Any newly minted successor `artifact_id` created during cutover (`PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107`) must have its own approved baseline entry recorded in `services/evolution/config/threshold_sweep_baselines.json` before `stock_future` evaluation applies; until approved, `threshold_sweep_worker` fails closed (diagnostic only, no fabricated baseline or incident).

## Reviewer Decision {#reviewer-decision}

- **Governance reference**: `SD_PERSONA_INSTRUMENT_DECISION_2026-11-07.md` §8 & §10 (T7) and `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1 / §7.1.
- **Assigned Reviewer**: `Codex`.
- **Decision status**: Scoped per-artifact, per-instrument-class `expected_drawdown` values derived deterministically from the §8 fill sequences (`2330`: `cash_share=0.00235`, `stock_future=0.235`; `0050`: `cash_share=0.0001245`, `stock_future=0.1245`; `2317`: `cash_share=0.00021`, `stock_future=0.42`) at `100,000` TWD capital and `1` unit per signal with trigger multiple `1.25`, subject to governed reviewer sign-off (`Codex` / Reviewer on Duty) and telemetry runtime-summary instrument-class field coordination with `INSTRUMENT-AWARE-TELEMETRY-BFF-DRIFT-20261107`.
