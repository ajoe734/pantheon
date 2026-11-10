# DEV PLAN — strategy discovery, alpha factor library, validation and evolution

Status: implementation plan (Reviewer: Antigravity). Owner: Claude. Date: 2026-11-10.
Base: `origin/dev` 86e19d622. Design: `SD_STRATEGY_DISCOVERY_SOURCES_2026-11-10.md` in this folder
(merged as #7125, 229426acb). Where the two differ, this plan wins.
Scope of this change: this plan, a decision notice at the top of the design, and
`docs/deployment/evidence/SD-STRATEGY-DISCOVERY-DEV-PLAN-20261110/evidence.json`. No source change.

---

## 0. 中文摘要（操作者用）

**你 11/10 的決定：**
- 「好，按照你的建議來做」：O-1 到 O-12 全部照設計文件的建議，取值列在 §1。
- 「應該是要有 alpha 策略因子庫這種東西才對」：新增 §3「因子庫」。

**因子庫怎麼做：**
- 系統裡早就有 `alpha_template` 這個名字，總藍圖也寫了 StrategySpec／AlphaTemplate，但一直沒有實作。這次就把它做成因子庫，不另起新名詞。
- **第一批：**
  - Qlib Alpha158：微軟開源、MIT 授權，158 個因子，全部是單一股票的時間序列公式，可以直接搬。
  - Alpha101：WorldQuant 公開的論文公式，我們依論文自己實作；需要跨股票排名的那幾個先存起來，第 4 波再用。
- **只拿定義、不拿資料或程式碼的：**
  - JKP 全球因子：資料是 CC BY-NC 非商用授權，而且要 WRDS 訂閱；台灣有沒有涵蓋也沒確認到。
  - Open Source Asset Pricing：程式碼是 GPL；它的 200 多個因子論文定義改成假說卡來源。
- **不用的：**
  - 國泰君安 191：原始報告沒查到。
  - FinLab、永豐因子工具：商用，條款沒查到。
- **每個因子都會在台股上自動評估：**
  - 預測力：對未來 1、5、20 日報酬的資訊係數；
  - 衰減、換手率；
  - 和其他因子是否重複。
- 結果放在 Alpha 工廠頁。好的因子包成規則，走真回測閘門，再交給 persona 挑選。

**更正設計文件兩處：**
- 櫃買三大法人本來就走櫃買 OpenAPI，不需要 data.gov.tw。
- 證交所網站端點只有兩種用途：三大法人 T86，以及個股歷史回補（STOCK_DAY 和櫃買 tradingStock）；每日行情本來就走 OpenAPI。
- 歷史回補停掉之後，回測只能用已經存下的歷史加上往後累積。第 2 波會實際量出有多少歷史。不夠的話：
  - 證交所同意，就恢復回補；
  - 不同意，就買 FinMind 商用方案。

**要你親自做的（§5）：**
- 開 DigitalOcean 帳號和新加坡主機；
- 寄兩封信（證交所／櫃買中心、CMoney，草稿在對話裡）；
- 提供書檔；
- 社群資料放量之前請律師看個資。

**任務：** 共 23 筆，已全部開在任務系統，用相依關係控制順序（§4）。

---

## 1. Operator decisions (2026-11-10)

Verbatim:

> 好，按照你的建議來做。
> 另外有這麼多人做量化交易，應該是要有alpha策略因子庫這種東西才對啊

| # | Decided | Value used by the tasks |
| --- | --- | --- |
| O-1 | (a) ask TWSE/TPEx for consent **and** (c) stop automated website downloads now; (b) FinMind Sponsor Pro only if consent is refused and history is insufficient | §2; task A4 |
| O-2 | Threads/Instagram from the web on the collector host (recorded earlier) | A1 |
| O-3 | Instagram: logged-out popular-tag pages only; no login, no proxies, no stealth | A1 |
| O-4 | CMoney: licence request sent; until answered, low-rate public stock pages, internal analysis only | A1 (source created disabled; enabled by the operator command after the letter is sent) |
| O-5 | DigitalOcean, Singapore, 2 vCPU / 4 GB, operator's own account and payment method, not linked to GCP billing | H1 |
| O-6 | counsel review before community sources exceed test volume | W1 acceptance keeps community sources at test volume |
| O-7 | model budget: at most **400 extraction calls per day** through the deny-all extraction agent (≤ 8 000 input tokens each, the existing client limit); stop with `budget_exhausted`; operator may change the number | B2 |
| O-8 | automatic paper binding within **5** concurrent discovery personas; nothing beyond paper | C3 |
| O-9 | economic release calendar maintained by hand once a year from the agencies' published schedules | B1 |
| O-10 | the 649 legacy `proposed` evolution decisions are cancelled with a recorded note through the existing route `POST /api/evolution/proposals/{id}/cancel` (`services/evolution/main.py:2255`, roles operator / risk_owner / governance_committee); no store is edited by hand | H2 |
| O-11 | PTT mirrors off | A1 |
| O-12 | books are files the operator owns or licenses, placed in the collector library | §5 |

## 2. Corrections to the design after verification on 86e19d622

* **TPEx institutional flows already come from the TPEx OpenAPI** (`tpex_3insti_daily_trading`,
  `services/source_ingestion/connectors/taiwan_official.py:251`). The design's
  `collector-tw-tpex-flows` (data.gov.tw 11856) is dropped.
* **Daily prices already come from the OpenAPIs** (`STOCK_DAY_ALL` `:221`,
  `tpex_mainboard_daily_close_quotes` `:231`). The website endpoints are used for:
  * TWSE institutional flows T86 (`:241`);
  * per-symbol **price history backfill**: TWSE `STOCK_DAY` (`:354`) and TPEx `tradingStock` (`:363`)
    through `fetch_price_history_payload` (`:981`), called from
    `services/source_ingestion/provider_adapters.py:417`;
  * a holiday-calendar citation to `www.twse.com.tw` (`:126`); an OpenAPI calendar source exists
    (`:160`).
* Consequence of O-1(c): with website backfill stopped, backtests use the history already stored
  plus what accumulates daily. Task B1 measures stored depth per symbol and dataset. If it is short
  of the 2-year minimum of the design (§10.3) and TWSE has not consented, the operator is asked for
  O-1(b).
* `SD_…` §7.3: the refresh guard's TWSE host set loses `www.twse.com.tw` once A4 lands; the TPEx
  OpenAPI is served from `www.tpex.org.tw/openapi`, so that host stays.

---

## 3. Alpha factor library (AlphaTemplate)

### 3.1 What exists

`alpha_template` exists only as a name: an evolution target type
(`services/control-plane/governance/evolution_decision.py:102`), an allowed use and expected output
of seed sources (`services/source_ingestion/registry/strategy_seed_source_registry.py:74`, `:83`),
and a target kind of `persona_strategy_match` (`docs/contracts/persona_strategy_match.schema.json:41`).
The master document names the L2 output "StrategySpec / AlphaTemplate". Nothing stores or evaluates
a template. This plan implements it under that name; no second concept is introduced.

### 3.2 Libraries (verified 2026-11-10)

| Library | What it is | Licence | Use here |
| --- | --- | --- | --- |
| Qlib Alpha158 / Alpha360 (`microsoft/qlib`, `qlib/contrib/data/loader.py`) | 158 engineered features (k-bar shape, price and volume ratios, rolling ROC/MA/STD/BETA/RSQR/RESI/MAX/MIN/QTL/RANK/IMAX/IMIN/CORR/CORD/CNTP/SUMP/VMA/WVMA… over 5/10/20/30/60 days); Alpha360 = 60 days of normalised raw OHLCV | MIT (repository licence, read via GitHub API) | **import all 158 as templates**, with attribution; every one is a per-symbol time-series expression |
| Alpha101 (Kakushadze, arXiv:1601.00991, Appendix A) | 101 formulas, mostly price-volume (returns, OHLC, volume, VWAP); some use cross-sectional `rank`, `scale` or industry neutralisation | arXiv non-exclusive distribution licence; formulas implemented from the paper text, paper not redistributed | **import the time-series subset** in wave 2; cross-sectional ones stored with `scope = cross_sectional`, evaluated in wave 4 |
| Open Source Asset Pricing (Chen & Zimmermann, `OpenSourceAP/CrossSection`) | code reproducing 200+ published cross-sectional predictors | code **GPL-2.0**; US data via WRDS | **definitions only**: each predictor's paper becomes a hypothesis-card source; no code copied |
| JKP Global Factor Data (`bkelly-lab/jkp-data`, jkpfactors.com) | 153 factors, 93 countries | code MIT; **data CC BY-NC 4.0**; building characteristics needs WRDS; Taiwan coverage not confirmed | **definitions only** (factor details document) |
| GTJA 191 (Guotai Junan short-horizon alphas) | 191 price-volume formulas | primary report **not verified** | not imported |
| FinLab, SinoPac factor tool | Taiwan commercial platforms | terms not found | not used |
| alphalens-reloaded (`stefan-jansen/alphalens-reloaded`) | factor evaluation (IC, turnover, decay) | Apache-2.0 | used by the template evaluator (B5) if its dependencies fit the research image; otherwise the same metrics with pandas |

### 3.3 Contract `alpha_template.v1`

`template_id`, `name`, `family` (`kbar` | `momentum` | `reversal` | `volatility` | `volume` |
`fundamental` | `flow` | `macro` | `attention`), `expression` (the `signal` sub-tree of
`factor_rule_v1`, design §9.2), `inputs[]` (governed dataset fields with availability lag),
`scope` (`time_series` | `cross_sectional`), `origin` (`{library, ref}` — e.g.
`qlib_alpha158:KMID`, `alpha101:#12`, a hypothesis card id, or an evolution parent),
`licence` (`spdx` or text) and `attribution`, `status` (`draft` | `evaluated` | `active` | `retired`),
`evaluation_ref` (latest B5 run).

Stored in the registry as entries of kind `alpha_template`, next to strategy specs.

### 3.4 Grammar additions to `factor_rule_v1`

The design's operator list (§9.2) is extended so both libraries are expressible:
`ts_sum`, `ts_corr`, `ts_cov`, `ts_slope`, `ts_rsquare`, `ts_resi`, `ts_quantile`, `ts_argmax`,
`ts_argmin`, `ts_decay_linear`, `log`, `signed_power`, `max2`, `min2`; inputs `open`, `high`, `low`,
`close`, `volume`, `vwap` (`TradeValue / TradeVolume` from `STOCK_DAY_ALL`). Cross-sectional
operators (`cs_rank`, `cs_scale`, `industry_neutralize`) are parsed and stored but rejected by the
per-symbol evaluator until wave 4.

### 3.5 Evaluation (task B5)

For each template on the governed Taiwan universe (the symbols with daily bars), point-in-time:

* time-series information coefficient (Spearman) between the factor at *t* and the forward return
  over 1, 5 and 20 trading days, per symbol and pooled; hit rate; turnover; IC decay;
* redundancy: correlation with every `active` template; above 0.9 → `duplicate_of`;
* results as a research run with metrics, linked from the template's `evaluation_ref`, and shown
  on the existing Alpha factory page.

### 3.6 From template to strategy

A template becomes a `factor_rule_v1` candidate by one of three wrappers (long when z-score above a
threshold; long/short band; long-only top-quantile of its own history), with the thresholds as
mutable controls. Every wrapper evaluation counts as a trial for the template's family (design
§10.3). Composites come from hypothesis cards and from evolution mutation ("add one filter from the
template catalogue", design §12.2).

---

## 4. Tasks (opened in the live task store 2026-11-10)

All target `ajoe734/pantheon` unless marked `execute-plans`. Owners alternate between the Claude and
Antigravity lanes; Human/Ops tasks are operator actions or hosted acceptance. Codex is neither owner
nor reviewer. Full acceptance and artifact contracts are in the task store; dependencies in the
store are authoritative.

| # | Task id (`…-20261110`) | Owner → Reviewer | Depends on | Scope |
| --- | --- | --- | --- | --- |
| P | `SD-STRATEGY-DISCOVERY-DEV-PLAN` | Claude → Antigravity | — | this plan |
| H1 | `COLLECTOR-HOST-PROVISION` | Human/Ops → Claude | A1 | operator creates the DigitalOcean account and host, DNS, TLS, bundle token; installs `deploy/collector` |
| H2 | `EVOLUTION-LEGACY-PROPOSALS-CANCEL` | Human/Ops → Claude | P | O-10 through the existing cancel route, dry-run list first |
| A1 | `COLLECTOR-STACK` | Antigravity → Claude | P | design §6 as `deploy/collector/` |
| A2 | `SOURCE-COLLECTOR-BUNDLE-CONNECTOR` | Claude → Antigravity | P | design §7.1, §7.2, §7.4, §7.5, canary egress comparison |
| A4 | `TW-OFFICIAL-WEBSITE-ENDPOINT-COMPLIANCE` | Antigravity2 → Claude | P | §2: website endpoints off by default behind a consent flag; holiday from OpenAPI; refresh host set |
| A3 | `DEV-EGRESS-COLLECTOR-HOST-GATE` | Antigravity2 → Claude | A2, A4 | design §7.3 |
| W1 | `DISCOVERY-W1-HOSTED-ACCEPTANCE` | Human/Ops → Claude | H1, A3 | design §13 wave 1 |
| B3 | `STRATEGY-ARTIFACT-FACTOR-RULE-KIND` | Antigravity2 → Claude | P | design §9.1–§9.3 + §3.4 grammar, per-symbol |
| B1 | `GOVERNED-FEATURE-DATASETS` | Antigravity → Claude | A2, A4, B3 | TWSE OpenAPI fundamentals, macro via bundles, community features, feature catalogue for the evaluator, history depth report, release calendar |
| B2 | `L2-HYPOTHESIS-EXTRACTION` | Claude → Antigravity | A2 | design §8.2–§8.4, §8.7; O-7 budget |
| B4 | `ALPHA-TEMPLATE-LIBRARY` | Claude → Antigravity | B3 | §3.3, §3.4 seeding (Alpha158 + Alpha101 time-series subset) |
| B5 | `ALPHA-TEMPLATE-EVALUATION` | Antigravity2 → Claude | B1, B4 | §3.5 |
| W2 | `DISCOVERY-W2-HOSTED-ACCEPTANCE` | Human/Ops → Claude | W1, B1, B2, B5 | cards with verifiable citations; template evaluations on hosted |
| C1 | `HYPOTHESIS-RULE-COMPILER` | Claude → Antigravity | B2, B4 | design §9.4 |
| C2 | `REPLICATION-GATE-REAL-BACKTEST` | Antigravity → Claude | B3, B1 | design §10 |
| C3 | `PERSONA-DISCOVERY-ALLOCATOR` | Claude → Antigravity | C2 | design §11; O-8 |
| C4 | `BFF-DISCOVERY-READ-SURFACES` | Antigravity2 → Claude | C2, B5 | cards, templates, metrics, lineage, attribution on existing read routes |
| C5 | `FE-DISCOVERY-VIEWS` (`execute-plans`) | Antigravity → Claude | C4 | existing pages render them |
| W3 | `DISCOVERY-W3-HOSTED-ACCEPTANCE` | Human/Ops → Claude | W2, C1, C3, C5 | a persona paper-running a discovered strategy traced to a source record or template |
| D1 | `EVOLUTION-AUTO-CONSUMER-SEARCH` | Antigravity2 → Claude | C2, C3 | design §12.2 |
| D2 | `SD-VIEW-UNIVERSE-CROSS-SECTIONAL` | Claude → Antigravity | B5 | design for trade-time universe; unlocks cross-sectional templates |
| W4 | `DISCOVERY-W4-HOSTED-ACCEPTANCE` | Human/Ops → Claude | W3, D1 | evolution without operator action inside the envelope |

Not opened: RD-Agent sandbox (design §8.6, optional); O-1(b) FinMind purchase (only if needed).

## 5. Operator actions

1. Create a DigitalOcean account with the operator's own payment method; create one Singapore
   droplet (2 vCPU / 4 GB); give the coordinator SSH access for installation (H1).
2. Send the TWSE/TPEx consent request and the CMoney licence request (drafts provided in the
   conversation of 2026-11-10; not stored here).
3. Place owned/licensed book files in the collector library when it exists.
4. Before community sources exceed test volume: counsel review of design §6.4.
5. YouTube Data API and Gemini keys from a Google account and project outside the Pantheon GCP
   organisation.

## 6. Risks and unverified items

* Stored price history may be too short for 2-year out-of-sample windows (§2); measured in B1.
* 158 + the Alpha101 time-series subset × 3 wrappers is a large trial count; the deflated threshold (design §10.3)
  will reject most. That is intended.
* Unverified: JKP Taiwan coverage; GTJA 191 primary source; FinLab terms; whether alphalens-reloaded
  installs cleanly in the research image.

## 7. Sources

[Qlib repository](https://github.com/microsoft/qlib) ·
[Qlib Alpha158/360 loader](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py) ·
[101 Formulaic Alphas](https://arxiv.org/abs/1601.00991) ·
[Open Source Asset Pricing code](https://github.com/OpenSourceAP/CrossSection) ·
[Open Source Cross-Sectional Asset Pricing (Fed copy)](https://www.federalreserve.gov/econres/feds/open-source-cross-sectional-asset-pricing.htm) ·
[JKP data](https://jkpfactors.com/data) · [jkp-data](https://github.com/bkelly-lab/jkp-data) ·
[alphalens-reloaded](https://github.com/stefan-jansen/alphalens-reloaded) ·
[TA-Lib Python](https://github.com/TA-Lib/ta-lib-python)
