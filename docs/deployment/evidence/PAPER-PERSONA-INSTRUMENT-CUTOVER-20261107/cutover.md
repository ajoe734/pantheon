# PAPER-PERSONA-INSTRUMENT-CUTOVER-20261107 Cutover Note

## 1. Hosted Environment & Baseline Context

- **Environment**: `pantheon-dev` (`https://app.dev.mvl-cap.tw`, `https://api.dev.mvl-cap.tw`)
- **Served Backend Commit (`ajoe734/pantheon`)**: `78a37e06c7bfe43a6f2023729a0798b452e2f646` (accepted `2026-11-08T18:05:17Z`, workflow run `42502558089`)
- **Served Frontend Commit (`ajoe734/execute-plans`)**: `f6d528080a7a8f9d696e2474b92b5a6c5a7e2ae6`
- **No Store Hand-Edited**: All persona retirements and successor provisionings were executed exclusively through the governed BFF provisioning coordinator (`PersonaProvisioningCoordinator` via `POST /bff/v1/commands` `PersonaAction` / `RetirePaperPersonaCase`, `scripts/retire_dev_paper_release_cases.py`, and `POST /bff/personas`).

## 2. Retired Negative-Share Personas (with Authoritative Archive Readbacks & Timestamps)

### 2.1 Learning Personas Retired via `POST /bff/v1/commands` (`RetirePaperPersonaCase`)

| Persona ID | Name / Underlying | Legacy Pool ID | Legacy Runtime / Binding | Legacy Artifact ID | Pre-Cutover Position | Retirement Started (UTC) | Retirement Completed (UTC) | Command IDs (Pass 1 / Pass 2) | Final State |
|---|---|---|---|---|---:|---|---|---|---|
| `persona-be19a1fac471247f2b2a` | 青禾｜2330 台股 Paper 學習 R2 (`2330.TWSE`) | `pool-persona-paper-298e138c136c9840b0e4` | `rt-535a82a3` / `rb-a1c3ada3d5894d57bc2e012f337753be` | `artifact-persona-paper-298e138c136c9840b0e4` | `-6.0` | `2026-11-08T18:50:13Z` | `2026-11-08T18:54:11.912757Z` | `cmd-9a0c6d3ed5554289bdf7f981168f2183` / `cmd-1e2a32c486cb4fbb8544d1bcb3a6217d` | `retired` |
| `persona-9d4fb3f44cbc5e7cb84f` | 衡岳｜0050 台股 Paper 學習 (`0050.TWSE`) | `pool-persona-paper-01390a4b72713295b001` | `rt-8b0d8649` / `rb-fd733d7c192f45898bbe0e90c8555878` | `artifact-persona-paper-01390a4b72713295b001` | `-6.0` | `2026-11-08T18:54:40Z` | `2026-11-08T18:55:21.355211Z` | `cmd-f87a335b75c94b4a808b4d419aa24f92` / `cmd-e115b63c1a2644fab9409e0d25113b6e` | `retired` |
| `persona-d9363cffc0efbbbcbeb9` | 澄川｜2317 台股 Paper 學習 (`2317.TWSE`) | `pool-persona-paper-8ae996980eeccb80d928` | `rt-c98e4223` / `rb-eda9b9a3379d4fb5a09f0e8432c84f20` | `artifact-persona-paper-8ae996980eeccb80d928` | `-4.0` | `2026-11-08T18:55:31Z` | `2026-11-08T18:56:07.889314Z` | `cmd-19fb98d8664b4b38880dd9a2d0a38dd1` / `cmd-4fd4b67f671d461990eb6dc04a6d6af4` | `retired` |

### 2.2 Release Probe Rows (`2330.TW -1`) Retired via `scripts/retire_dev_paper_release_cases.py`

| Persona ID | Case Key | Created At (UTC) | Retired At (UTC) | Final State |
|---|---|---|---|---|
| `persona-2bd3c6df7cdf5ab31495` | `dev-paper-release-42181697809-1` | `2026-11-06T07:56:57.745816Z` | `2026-11-06T22:59:07.312957Z` | `retired` |
| `persona-b09ed5e153524f257144` | `dev-paper-release-42189951083-1` | `2026-11-06T09:02:16.795479Z` | `2026-11-06T22:59:13.267249Z` | `retired` |

Post-retirement readback of `GET /bff/management/portfolio-book/holdings?page_size=100` confirms `0` active negative-share rows remain.

## 3. Provisioned Successor Personas (with `expression_policy` and `2.0x` Pool Risk Policy)

| Successor Persona ID | Name / Underlying | Provisioned At (UTC) | Running At (UTC) | Capital Pool ID | Runtime / Binding | Strategy Artifact ID | Expression Policy | Pool Risk Policy |
|---|---|---|---|---|---|---|---|---|
| `persona-2448c7053e33ea48da47` | 青禾｜2330 台股 Paper 學習 R3 (`2330.TWSE`) | `2026-11-08T12:19:41.761558Z` | `2026-11-08T12:20:47.663487Z` | `pool-persona-paper-06d4775a9b6a8f63b311` | `rt-ad81501b` / `rb-3823397e125e4579ba5f4174067087ed` | `artifact-persona-paper-06d4775a9b6a8f63b311` | `notional_target_v1` (`v1.0`) | `allowed_asset_classes=["equity","future"]`, `max_leverage=2.0`, `gross_limit=2.0`, `net_limit=2.0` |
| `persona-ee7112a829d38b9135ac` | 衡岳｜0050 台股 Paper 學習 R2 (`0050.TWSE`) | `2026-11-08T18:09:41.077514Z` | `2026-11-08T18:11:56.231182Z` | `pool-persona-paper-a81b4fd15944698341aa` | `rt-3b65a843` / `rb-3516fc86e6f84649a499fc89f3c562b3` | `artifact-persona-paper-a81b4fd15944698341aa` | `notional_target_v1` (`v1.0`) | `allowed_asset_classes=["equity","future"]`, `max_leverage=2.0`, `gross_limit=2.0`, `net_limit=2.0` |
| `persona-6507750312870d03a241` | 澄川｜2317 台股 Paper 學習 R2 (`2317.TWSE`) | `2026-11-08T18:10:44.485779Z` | `2026-11-08T18:12:37.709752Z` | `pool-persona-paper-039ad01f8bcd2b4aa466` | `rt-3252fa75` / `rb-7f3c1be55a224fdaa36bbdd7a7532b4b` | `artifact-persona-paper-039ad01f8bcd2b4aa466` | `notional_target_v1` (`v1.0`) | `allowed_asset_classes=["equity","future"]`, `max_leverage=2.0`, `gross_limit=2.0`, `net_limit=2.0` |

## 4. Per-Instrument-Class Drawdown Baselines Before Flag Activation

- **Task / PR**: `PAPER-SUCCESSOR-BASELINES-20261108` (PR `#7083`, head `04586863720d5e465cdc60db1dd97330ae661094`, merged to `dev` as `a141fea06ed450d6b05232397c86d9361cc7d499` at `2026-11-08T18:40:32Z`).
- **Config File**: `services/evolution/config/threshold_sweep_baselines.json` (`schema_version`: `pantheon.evolution.threshold_sweep_baselines.v2`).
- **Resulting Baseline Count**: `16` approved entries (`8` artifacts × `2` instrument classes `cash_share` and `stock_future`), including all three successor artifacts (`artifact-persona-paper-06d4775a9b6a8f63b311`, `artifact-persona-paper-a81b4fd15944698341aa`, `artifact-persona-paper-039ad01f8bcd2b4aa466`) with `cash_share: 0.08` and `stock_future: 0.16`.

## 5. Governed Deployment Flag Activation & Telemetry Readback

- **Deployment Wiring**: `DEV-DEPLOY-EXPRESSION-LAYER-FLAG-20261108` (PR `#7076`, merged as `8e53d0ff2f82df8d5b662ec05ef902a066a19e40`) wires GitHub repository variable `vars.DEV_PAPER_EXPRESSION_LAYER` into `PANTHEON_DEV_PAPER_EXPRESSION_LAYER` -> `PANTHEON_PAPER_EXPRESSION_LAYER` across `.github/workflows/nonprod-deploy.yml` and `scripts/deploy_nonprod_vm.sh`.
- **Variable Set Timestamp**: `DEV_PAPER_EXPRESSION_LAYER=on` set on `ajoe734/pantheon` at `2026-11-08T18:58:47Z` (after `PAPER-SUCCESSOR-BASELINES-20261108` merged at `2026-11-08T18:40:32Z` and all three legacy personas finished retirement at `2026-11-08T18:56:07Z`).
- **Old Artifact History Readable in Telemetry (`GET /api/telemetry/runtime-summaries/<runtime_id>`)**:
  - `rt-535a82a3` (`artifact-persona-paper-298e138c136c9840b0e4`, `rb-a1c3ada3d5894d57bc2e012f337753be`): HTTP `200`, `ledger_fill_count=6`, `executed_trade_count=6`, `pnl=820.0`
  - `rt-8b0d8649` (`artifact-persona-paper-01390a4b72713295b001`, `rb-fd733d7c192f45898bbe0e90c8555878`): HTTP `200`, `ledger_fill_count=6`, `executed_trade_count=6`, `pnl=44.15000000000873`
  - `rt-c98e4223` (`artifact-persona-paper-8ae996980eeccb80d928`, `rb-eda9b9a3379d4fb5a09f0e8432c84f20`): HTTP `200`, `ledger_fill_count=6`, `executed_trade_count=6`, `pnl=41.5`
