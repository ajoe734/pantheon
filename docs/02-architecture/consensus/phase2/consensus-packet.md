# Consensus Packet

Session: `phase2-2026-04-12-blueprint-gap-convergence`
Status: `ready_for_human` — Codex repo-evidence refresh on top of the canonical round-1 state
Facilitator of record: Claude

## Machine State

- The canonical machine state already reports this session as `active`, `current_round = 1`, `consensus_status = ready_for_human`, and `human_gate_status = not_requested`. (`planning-session.json:92-95`)
- The derived planning snapshot reports the same state and keeps `Codex` as baton owner. (`current-work.md:24-31`)

## Consensus

- The accepted governance/runtime/telemetry/persona/BFF/OSS backbone is already complete and should not be reopened in this wave. The active board marks `REG-004` through `OSS-003` as done, which means the next delivery wave is about upstream data/decision truth and acceptance closure, not another backbone rewrite. (`ai-status.json:1468-2022`)
- All eight blueprint gaps remain real, but they are not all the same kind of gap:
  - `GAP-00`, `GAP-01`, `GAP-03`, and `GAP-05` are missing-truth / missing-acceptance blockers for production sign-off. (`Pantheon_Blueprint_Gap_Review_v1.md:94-274,376-449,527-597,714-718`)
  - `GAP-02` and `GAP-06` are packaging gaps on top of already-landed implementation. (`Pantheon_Blueprint_Gap_Review_v1.md:280-370,601-665`; `integrations/oss-002/regrade_report.md:187-224`; `services/control-plane/bff/BFF_SURFACE_INVENTORY.md:255-267`)
  - `GAP-04` and `GAP-07` are convergence-tail design/language gaps and should stay P2. (`Pantheon_Blueprint_Gap_Review_v1.md:455-521,669-726`)
- GAP-02 and GAP-05 feasibility evaluation (Gemini):
  - GAP-02 (Research Maturity) is highly feasible for existing backends (MLflow, DSPy, Imitation) once documentation debt is cleared to satisfy `OSS_INTEGRATION_CHECKLIST.md`.
  - GAP-05 (Golden Replay) is feasible but carries a hard dependency on GAP-01 (Data Plane objects) for truth-anchoring. Replay will initially use synthetic/mocked execution feedback for the LEAN segment due to `EX-001` deferral.
- Repo evidence confirms the missing upstream objects and artifacts:
  - `StrategySpec` still uses free-form market/data inputs. (`services/control-plane/specs/strategy_spec.schema.json:38-95`; `services/research/strategy_spec/README.md:27-31`)
  - Scoped `rg` searches on 2026-04-12 found no Pantheon-owned implementations for the seven Data Plane objects, the five decision-front objects, memory-layer artifacts, or golden replay artifacts under `services/**`, `integrations/**`, and `support/**`.
  - `rg --files` found none of the market/data policy docs the market-data brief explicitly requires. (`Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:609-638`)

## Resolved Items

- `BG-005` is a P0 acceptance gate, not a P2 tail item. The blueprint priority table is authoritative and the current `planning-session.json` task list already reflects that correction. (`Pantheon_Blueprint_Gap_Review_v1.md:714-718`; `planning-session.json:269-282`)
- `BG-005` must anchor to real `DatasetVersion` and decision-chain refs. A synthetic telemetry-only replay does not close GAP-05. (`Pantheon_Blueprint_Gap_Review_v1.md:549-594`; `Pantheon_Market_Data_Scope_and_Source_Plan_v1.md:526-533,626-638`)
- `BG-002` scope is matrix + production-path mapping only. Checklist-format documentation hardening remains follow-on work after the matrix closes. (`planning-session.json:185-189,230-241`; `integrations/oss-002/regrade_report.md:218-224`)
- `BG-004` and `BG-007` remain P2 convergence-tail work. (`Pantheon_Blueprint_Gap_Review_v1.md:724-726`; `planning-session.json:256-308`)
- The only remaining planning-session issue is the waived Copilot lane (`DISC-COPILOT-PLANNING`); no unresolved semantic conflict remains. (`planning-session.json:164-189`)

## Delivery Wave

| Wave | Priority | Tasks | Why this wave exists |
|---|---|---|---|
| Wave 0 | P0 foundations | `BG-000`, `BG-001`, `BG-003` | Establish market/data truth and decision-front provenance objects before acceptance work begins. (`Pantheon_Blueprint_Gap_Review_v1.md:714-718`; `planning-session.json:203-254`) |
| Wave 1 | P0 acceptance gate | `BG-005` | Convert the completed backbone plus new upstream truth objects into one scripted golden replay artifact for production sign-off. (`Pantheon_Blueprint_Gap_Review_v1.md:716-718,578-594`; `planning-session.json:269-282`) |
| Wave 2 | P1 packaging | `BG-002`, `BG-006` | Publish maturity/acceptance artifacts on top of already-implemented research and operator surfaces. (`Pantheon_Blueprint_Gap_Review_v1.md:720-722`; `planning-session.json:230-241,284-295`) |
| Wave 3 | P2 convergence tail | `BG-004`, `BG-007` | Finish memory-layer design and product/operator language once the sign-off vocabulary is stable. (`Pantheon_Blueprint_Gap_Review_v1.md:724-726`; `planning-session.json:256-308`) |

Critical path to production sign-off: `BG-000` → (`BG-001` ∥ `BG-003`) → `BG-005`

## Materialization Set

| Task | Owner | Reviewer | Hard deps | Expected artifact |
|---|---|---|---|---|
| `PLAN-002` | Codex | Claude | - | Reusable discussion-planning runtime. (`planning-session.json:193-202`) |
| `BG-000` | Codex | Gemini | `PLAN-002` | Market scope / instrument / source-class policy package. (`planning-session.json:203-215`) |
| `BG-001` | Qwen | Codex | `PLAN-002` | Data Plane schemas + replay identifiers. (`planning-session.json:217-228`) |
| `BG-003` | Qwen | Claude | `PLAN-002` | Decision-front object map and schemas. (`planning-session.json:243-254`) |
| `BG-005` | Codex | Qwen | `BG-000`, `BG-001`, `BG-003` | Golden replay scenario + runbook. (`planning-session.json:269-282`) |
| `BG-002` | Qwen | Gemini | `PLAN-002` | Research backend maturity matrix. (`planning-session.json:230-241`) |
| `BG-006` | Qwen | Claude | `PLAN-002` | Operator acceptance matrix. (`planning-session.json:284-295`) |
| `BG-004` | Claude | Codex | `PLAN-002` | Memory-layer design note. (`planning-session.json:256-267`) |
| `BG-007` | Codex | Claude | `PLAN-002` | Product/operator glossary and language pack. (`planning-session.json:297-308`) |

## BG-000 Provider Brief (human-supplied, 2026-04-12)

`PRIMARY_DATA_PROVIDER_SHORTLIST.md` must cover at minimum the following provider shortlist per market. This is the authoritative input for the BG-000 deliverable.

### 美股 US Equities

| Data class | Primary source | Backup / note |
|---|---|---|
| 現股行情 OHLCV | **Polygon.io** (REST + WebSocket) | Alpaca Markets (cheaper, paper-stage) |
| Corporate actions | **Polygon.io** (splits/dividends/ticker changes) | Nasdaq Data Link Sharadar |
| Fundamentals | **Nasdaq Data Link Sharadar (SF1)** | Simfin (free tier) |
| Borrow / shortability | **Interactive Brokers TWS API** | FINRA Short Interest (bi-weekly, free; aggregate only) |
| Corporate events calendar | **Polygon.io** | SEC EDGAR (raw filings) |
| Broker-aligned execution | **Interactive Brokers TWS API** | — |

### 美股 US Listed Derivatives

| Data class | Primary source | Backup / note |
|---|---|---|
| Options chains / OI | **Polygon.io Options** | Interactive Brokers (execution-aligned) |
| Greeks / IV surface | **Interactive Brokers** (real-time) | OptionMetrics (historical, institutional) |
| Futures continuous series | **Nasdaq Data Link CHRIS** (adjusted continuous) | Interactive Brokers TWS |
| Futures contract rolls / margin | **CME Group DataMine** (official) | Interactive Brokers TWS |
| Futures calendar | **CME Group** official | Interactive Brokers |

### 台股 TW Equities

| Data class | Primary source | Backup / note |
|---|---|---|
| 現股行情（上市/上櫃/零股） | **TWSE OpenAPI + TPEx OpenAPI** (free, rate-limited) | FinMind (community, free, delayed) |
| Corporate actions（除權/除息/增資） | **TWSE 開放資料 + MOPS** (official) | TEJ（付費，最完整） |
| 法說 / 重大公告 | **MOPS 公開資訊觀測站** (official) | TEJ event database |
| 籌碼 / 三大法人 / 資券 / 當沖 | **TWSE 開放資料** (daily, free) | TEJ（完整歷史，付費） |
| 借券費率歷史 | **TEJ**（付費） | TWSE 官方借券報告 |
| Broker-aligned execution | **永豐金 API / 元大 API / 富邦 API** | — |

### 台股 TW Derivatives (TAIFEX)

| Data class | Primary source | Backup / note |
|---|---|---|
| TX / MTX / 台指選（日線/OI） | **TAIFEX 官網開放資料** (free) | TEJ 期貨資料庫（付費） |
| 個股期 / 個股選 | **TAIFEX 官網** (free, but thin liquidity — validate strategy need first) | TEJ |
| 三大法人期貨部位 | **TAIFEX 官網開放資料** (free) | — |
| Broker-aligned execution | **元大期貨 API / 凱基期貨 API** | — |

### 加密貨幣 Crypto

| Data class | Primary source | Backup / note |
|---|---|---|
| Spot OHLCV | **Binance REST API** | OKX, Bybit (cross-venue); Coinbase Advanced Trade (US-compliant) |
| Perp futures OHLCV | **Binance USDT-M Perps** | OKX, Bybit |
| Delivery futures | **Binance COIN-M Futures** | CME Bitcoin/ETH Futures (institutional/compliant) |
| Funding rate | **各交易所 API** (Binance/OKX/Bybit native) | Coinglass (aggregated, free) |
| Open interest | **各交易所 API** | Coinglass (cross-venue aggregated) |
| Liquidations | **各交易所 API** | Coinglass |
| Mark price / index price | **各交易所 API** | — |
| L2 order book / tick trades (real-time) | **各交易所 WebSocket** | — |
| Historical tick / L2 (backtest quality) | **Tardis.dev** (paid, industry standard) | Kaiko (enterprise) |
| Options (BTC/ETH) | **Deribit** | CME (institutional) |

### v1 接入優先順序

1. **Interactive Brokers** — 美股現股 + 期權 + 期貨執行同步、borrow，單一接口
2. **Polygon.io** — 美股研究行情、歷史、corporate actions、options snapshot
3. **Binance** — crypto spot + perp，funding rate、OI、mark/index price
4. **TWSE / TPEx / TAIFEX 開放資料** — 台股免費官方層
5. **TEJ** — 台股完整歷史與籌碼，付費後補
6. **Tardis.dev** — crypto 歷史 tick，backtest 精度需要時開啟
7. **Nasdaq Data Link Sharadar** — 美股 fundamentals，研究層需要時

---

## Human Gate

- The materialization order, gap classification, and BG-000 provider brief are now approved by the human operator.
- Human gate status: **approved** (2026-04-12)
- After approval, materialize the proposed execution tasks via `./scripts/planning-state.sh materialize`. (`docs/02-architecture/consensus/phase2/README.md:45-60`)
