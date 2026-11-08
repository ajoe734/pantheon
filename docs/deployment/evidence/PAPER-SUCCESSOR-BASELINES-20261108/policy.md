# PAPER-SUCCESSOR-BASELINES-20261108 — Approved Drawdown Baselines for the Three Successor Paper Personas

## Scope and Trigger Multiple

This policy governs the per-artifact, per-instrument-class `expected_drawdown` baselines added to `services/evolution/config/threshold_sweep_baselines.json` under `baselines_by_instrument_class` for the three successor Taiwan paper personas provisioned on hosted dev, while preserving every existing entry in `baselines` (all seven flat entries) and `baselines_by_instrument_class` (all three predecessor entries) byte-for-byte unchanged.

- **Metric**: `rolling_drawdown_multiple` (`summary_field: "drawdown"`, `ratio_baseline_key: "expected_drawdown"`, `comparator: "gt"`).
- **Capital and sizing**: `100000` (`100,000`) TWD starting capital and `1` unit per signal (`one unit per signal` held from fill 1).
- **Trigger multiple**: **`1.25`** (`threshold_value: 1.25` per `EVOLUTION_REVIEW_AND_THRESHOLDS.md` section 7.1 and `services/evolution/config/threshold_sweep_thresholds.json`). A breach occurs only when `observed_drawdown / expected_drawdown > 1.25` (equivalently `observed_drawdown > 1.25 * expected_drawdown`).
- **Fail-closed guarantee (no fabricated baseline)**: No baseline is fabricated for any other artifact or for any instrument class without an approved value. When a runtime summary omits its instrument class on an instrument-class-governed artifact, reports an ambiguous class, or reports an instrument class or artifact without a positive finite approved baseline and `policy_source`, `threshold_sweep_worker` fails closed with an explicit diagnostic and opens **no incident**.

## Successor Paper Personas and Artifacts (`PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107`)

Hosted dev facts read by the operator on `2026-11-08` through the BFF confirm the three successor paper personas and their execution artifacts:

1. **`2330` (`青禾｜2330 台股 Paper 學習 R3`)**
   - `persona_id`: `persona-2448c7053e33ea48da47`
   - `capital_pool_id`: `pool-persona-paper-06d4775a9b6a8f63b311`
   - `artifact_id`: `artifact-persona-paper-06d4775a9b6a8f63b311`
   - Lifecycle state observed at cutover: `paper_running`
   - Underlying / stock future: `2330.TWSE` / `QFF` (`x100`)
   - Predecessor artifact retained unchanged: `artifact-persona-paper-298e138c136c9840b0e4` (`persona-be19a1fac471247f2b2a`)

2. **`0050` (`衡岳｜0050 台股 Paper 學習 R2`)**
   - `persona_id`: `persona-ee7112a829d38b9135ac`
   - `capital_pool_id`: `pool-persona-paper-a81b4fd15944698341aa`
   - `artifact_id`: `artifact-persona-paper-a81b4fd15944698341aa`
   - Underlying / stock future: `0050.TWSE` / `SRF` (`x1000`)
   - Predecessor artifact retained unchanged: `artifact-persona-paper-01390a4b72713295b001` (`persona-9d4fb3f44cbc5e7cb84f`)

3. **`2317` (`澄川｜2317 台股 Paper 學習 R2`)**
   - `persona_id`: `persona-6507750312870d03a241`
   - `capital_pool_id`: `pool-persona-paper-039ad01f8bcd2b4aa466`
   - `artifact_id`: `artifact-persona-paper-039ad01f8bcd2b4aa466`
   - Underlying / stock future: `2317.TWSE` / `DHF` (`x2000`)
   - Predecessor artifact retained unchanged: `artifact-persona-paper-8ae996980eeccb80d928` (`persona-d9363cffc0efbbbcbeb9`)

Each successor artifact carries `expression_policy`, and each corresponding pool policy allows `equity` and `future` with `max_leverage: 2.0`. The predecessor artifacts `artifact-persona-paper-298e138c136c9840b0e4`, `artifact-persona-paper-01390a4b72713295b001`, and `artifact-persona-paper-8ae996980eeccb80d928` keep their existing entries in both `baselines` and `baselines_by_instrument_class` byte-for-byte unchanged while the operator retires the predecessor personas through the governed command in `PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107`.

## Replay Sequences and Arithmetic (`100000` TWD Capital, `1` Unit per Signal)

Following the exact precedent of `PAPER-DRAWDOWN-REBASELINE-INSTRUMENT-CLASS-20261107` (merged PR 7071) and `SD_PERSONA_INSTRUMENT_DECISION_2026-11-07.md` §8 at `100000` (`100,000`) TWD starting capital, `1` unit per signal held from fill 1, and trigger multiple `1.25`:

1. **`2330` (`persona-2448c7053e33ea48da47`, `artifact-persona-paper-06d4775a9b6a8f63b311`, `pool-persona-paper-06d4775a9b6a8f63b311`)** — input: `input-2330.json`
   - Fill price sequence: `2370 -> 2330 -> 2295 -> 2260 -> 2240 -> 2135` (move `-235` points from `2370` to `2135`).
   - `cash_share` (`2330.TWSE`, multiplier `1`): PnL sequence `[0, -40, -75, -110, -130, -235]`, equity sequence `[100000, 99960, 99925, 99890, 99870, 99765]`, PnL at trough `-235`, worst equity `99,765` (`99765`), `expected_drawdown = (100000 - 99765) / 100000 = 0.00235`, `1.25x` breach trigger `= 0.0029375`.
   - `stock_future` (`QFF`, multiplier `100` TWD/pt): PnL sequence `[0, -4000, -7500, -11000, -13000, -23500]`, equity sequence `[100000, 96000, 92500, 89000, 87000, 76500]`, PnL at trough `-23,500` (`-23500`), worst equity `76,500` (`76500`), `expected_drawdown = (100000 - 76500) / 100000 = 0.235`, `1.25x` breach trigger `= 0.29375`.

2. **`0050` (`persona-ee7112a829d38b9135ac`, `artifact-persona-paper-a81b4fd15944698341aa`, `pool-persona-paper-a81b4fd15944698341aa`)** — input: `input-0050.json`
   - Fill price sequence: `104.95 -> 104.1 -> 100.6 -> 99.85 -> 97.15 -> 92.5` (move `-12.45` points from `104.95` to `92.5`).
   - `cash_share` (`0050.TWSE`, multiplier `1`): PnL sequence `[0.0, -0.85, -4.35, -5.1, -7.8, -12.45]`, equity sequence `[100000.0, 99999.15, 99995.65, 99994.9, 99992.2, 99987.55]`, PnL at trough `-12.45`, worst equity `99,987.55` (`99987.55`, `99,988` rounded TWD), `expected_drawdown = (100000 - 99987.55) / 100000 = 0.0001245`, `1.25x` breach trigger `= 0.000155625`.
   - `stock_future` (`SRF`, multiplier `1000` TWD/pt): PnL sequence `[0.0, -850.0, -4350.0, -5100.0, -7800.0, -12450.0]`, equity sequence `[100000.0, 99150.0, 95650.0, 94900.0, 92200.0, 87550.0]`, PnL at trough `-12,450` (`-12450`), worst equity `87,550` (`87550`), `expected_drawdown = (100000 - 87550) / 100000 = 0.1245`, `1.25x` breach trigger `= 0.155625`.

3. **`2317` (`persona-6507750312870d03a241`, `artifact-persona-paper-039ad01f8bcd2b4aa466`, `pool-persona-paper-039ad01f8bcd2b4aa466`)** — input: `input-2317.json`
   - Fill price sequence: `245.5 -> 251 -> 245.5 -> 240 -> 235 -> 224.5` (move `-21.0` points from entry `245.5`, `-26.5` points from peak `251.0`).
   - `cash_share` (`2317.TWSE`, multiplier `1`): PnL sequence `[0.0, 5.5, 0.0, -5.5, -10.5, -21.0]`, equity sequence `[100000.0, 100005.5, 100000.0, 99994.5, 99989.5, 99979.0]`, PnL at trough `-21.0`, worst equity `99,979` (`99979`), `expected_drawdown = (100000 - 99979) / 100000 = 0.00021`, `1.25x` breach trigger `= 0.0002625` (peak-to-trough from `100,005.5` is `0.000264985...`).
   - `stock_future` (`DHF`, multiplier `2000` TWD/pt): PnL sequence `[0.0, 11000.0, 0.0, -11000.0, -21000.0, -42000.0]`, equity sequence `[100000.0, 111000.0, 100000.0, 89000.0, 79000.0, 58000.0]`, PnL at trough `-42,000` (`-42000`), worst equity `58,000` (`58000`), `expected_drawdown = (100000 - 58000) / 100000 = 0.42`, `1.25x` breach trigger `= 0.525` (peak-to-trough from `111,000` high-water mark is `53000 / 111000 = 0.477477...`).

## Reviewer Decision {#reviewer-decision}

- **Governance reference**: `SD_PERSONA_INSTRUMENT_DECISION_2026-11-07.md` §8 & §10, `docs/deployment/evidence/PAPER-DRAWDOWN-REBASELINE-INSTRUMENT-CLASS-20261107/policy.md#reviewer-decision`, and `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1 / §7.1.
- **Operator authorization and Reviewer on Duty**: Operator instruction on `2026-11-08` (`ajoe734`, Reviewer on Duty under `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1) authorized binding the exact approved underlying `cash_share` and `stock_future` `expected_drawdown` baselines from `PAPER-DRAWDOWN-REBASELINE-INSTRUMENT-CLASS-20261107` to the three newly provisioned successor paper persona artifacts; independent governed reviewer for `PAPER-SUCCESSOR-BASELINES-20261108`: `Claude`.
- **Coordinated runtime summary field**: `instrument_class` / `instrument_kind` at summary top level or unambiguous `kind` (`cash_share` | `stock_future`) on `positions[]` / `positions_by_underlying.<symbol>.instruments[]` as projected by `services/telemetry/runtime_summary.py` (`INSTRUMENT-AWARE-TELEMETRY-BFF-DRIFT-20261107`).
- **Reviewer decision**: APPROVED for `services/evolution/config/threshold_sweep_baselines.json` (`baselines_by_instrument_class`) at `100000` (`100,000`) TWD capital, `1` unit per signal, and trigger multiple `1.25`:
  - `artifact-persona-paper-06d4775a9b6a8f63b311` (`persona-2448c7053e33ea48da47`, `青禾｜2330 台股 Paper 學習 R3`, `pool-persona-paper-06d4775a9b6a8f63b311`, `2330.TWSE` / `QFF`): `cash_share` `expected_drawdown = 0.00235` (`1.25x` trigger `= 0.0029375`), `stock_future` `expected_drawdown = 0.235` (`1.25x` trigger `= 0.29375`).
  - `artifact-persona-paper-a81b4fd15944698341aa` (`persona-ee7112a829d38b9135ac`, `衡岳｜0050 台股 Paper 學習 R2`, `pool-persona-paper-a81b4fd15944698341aa`, `0050.TWSE` / `SRF`): `cash_share` `expected_drawdown = 0.0001245` (`1.25x` trigger `= 0.000155625`), `stock_future` `expected_drawdown = 0.1245` (`1.25x` trigger `= 0.155625`).
  - `artifact-persona-paper-039ad01f8bcd2b4aa466` (`persona-6507750312870d03a241`, `澄川｜2317 台股 Paper 學習 R2`, `pool-persona-paper-039ad01f8bcd2b4aa466`, `2317.TWSE` / `DHF`): `cash_share` `expected_drawdown = 0.00021` (`1.25x` trigger `= 0.0002625`), `stock_future` `expected_drawdown = 0.42` (`1.25x` trigger `= 0.525`).
  - Every existing entry in `baselines` (all seven flat entries) and `baselines_by_instrument_class` (all three predecessor entries `artifact-persona-paper-298e138c136c9840b0e4`, `artifact-persona-paper-01390a4b72713295b001`, and `artifact-persona-paper-8ae996980eeccb80d928`) remains byte-for-byte unchanged, and no baseline is fabricated for any other artifact or for any instrument class without an approved value.
