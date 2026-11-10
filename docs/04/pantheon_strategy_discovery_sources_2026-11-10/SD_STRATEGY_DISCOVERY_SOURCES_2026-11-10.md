# SD-STRATEGY-DISCOVERY-SOURCES-20261110 — personas find, validate and evolve strategies from external knowledge

Status: design for review (Reviewer: Antigravity). Owner: Claude. Date: 2026-11-10.
Scope of this change: this document and `docs/deployment/evidence/SD-STRATEGY-DISCOVERY-SOURCES-20261110/evidence.json`.
No source, test, workflow, configuration or infrastructure change. No host is created and no
implementation task is created by this work; §13 lists proposed tasks for the operator to open.

**What is decided and what is not.** §1 lists the operator decisions of 2026-11-10 with the
verbatim statements. Everything else marked "recommendation" is a proposal; the open questions in
§14 are the operator's.

---

## 0. 中文摘要（操作者用）

**為什麼要做：** 11/10 在 hosted 實際查證，persona「自己找策略、自己驗證、自己演化」三段只有行程在跑，沒有實際產出：
- 3,217 筆策略草稿全部是「某檔股票某天的收盤價」；
- 真正跑過的回測只有 1 次，而且是人工發起；
- 649 筆演化提案沒有人處理；
- 重訓只是把回看天數在 2 和 3 之間切換；
- persona 執行端只接受一種固定的動能規則。

外部知識來源（論文、社群、書、經濟數據）在 8 月和 9 月被陸續砍掉或延後（§3）。

**這份文件要做到的完整流程（不是最小循環）：**
1. **第三地抓取主機**（不在 GCP、不同帳號、不放任何 GCP 憑證）用現成工具抓資料：
   - 網頁用 Scrapling；
   - 新聞用 RSSHub；
   - 論文和書用 Docling；
   - 其他走官方 API；
   - 抓到的資料存成「原始檔＋清單」。

   Pantheon 只從這一台主機拉資料，對外只多開一個出口（§6、§7）。
2. **來源分三種用途**（§5、§8）：
   - **論文、書、研究文章、長文分析** → 萃取成「策略假說卡」。
   - **社群（Threads、IG、股市爆料同學會、PTT、YouTube）** → 每檔股票每天的關注度與情緒「特徵」，另外每週整理出熱門主題。
   - **經濟數據（央行、國發會景氣燈號、主計總處、證交所 OpenAPI 財報與月營收、櫃買三大法人）** → 景氣與基本面特徵。
3. **假說卡變成可執行規則**（§9）：
   - 新增一種「因子規則」策略格式，只能用白名單運算子組合已治理的資料，不跑任意程式碼；
   - 回測和 paper 用同一個計算函式，接上 11/07 已完成的「觀點 → 商品決策」分層。
4. **真實驗證**（§10）：
   - 准入閘門必須有一筆真的在台股資料上跑過的回測，包含樣本外、交易成本和多重測試控制；
   - 只看文件欄位的檢查不再算通過。
5. **persona 自己挑策略上 paper**（§11）：
   - 依現有的 `persona_strategy_match` 打分挑選；
   - 在操作者設定的 paper 名額內自動綁定。
6. **真實演化**（§12）：
   - 每一筆演化提案都會被處理：在宣告的參數範圍內搜尋、變異結構、淘汰衰退的策略；
   - 每個子代都必須重新通過樣本外驗證。

**11/10 已決定：**
- 要做完整流程；
- 先找現成工具，不自己開發；
- 用第三地主機；
- Threads 和 IG 在第三地主機直接爬網頁。

**我 11/10 的實測（未登入，從資料中心 IP）：**
- **Threads**：搜尋「台股」可以拿到最新貼文的第一頁，個人頁約 30 篇。
- **IG**：hashtag 頁只會轉到「熱門」頁，約 12 支 Reels，而且不是最新的；關鍵字搜尋和個人頁兩次請求就回 429 並要求登入。

所以在不登入的前提下，IG 的資料量很有限（§5.3、O-3）。

**你要決定的事（§14）：**
- 證交所／櫃買中心網站端點的條款問題；
- IG 要不要登入（我建議不要）；
- 股市爆料同學會的抓法；
- 第三地主機選哪家；
- 個資法要不要請律師看；
- LLM 每月預算；
- persona 自動上 paper 的名額；
- 經濟行事曆的來源；
- 649 筆舊提案怎麼處理；
- PTT 鏡像站要不要開；
- 書的來源。

**時程（§13）：** 分五波。這是做的順序，不是砍範圍；每一波都有 hosted 驗收。

---

## 1. Operator statements and decisions (2026-11-10, verbatim)

> 不能做這種最小循環，因為已經驗證過可以運作，就要往下做完整。我記得自己找策略，應該有各種不同的找尋入口，包含找paper，看社群，看書增加知識等。

> 另外還有各種經濟數據之類的也應該是要找的資料才對

> 你先找一下有沒有什麼工具能夠把這些抓資料查閱資料的功能補足，不用自己開發。例如github上面有scrapling這個工具搞不好就可以用，另外因為我們在GCP上面，所以搞不好你要弄一個第三地的主機專門抓資料，避免整個GCP都被封掉

> Reddit、X 社群不是這兩個，台灣都用thread跟IG好嗎，另外股市爆料同學會跟youtube有一堆影片趨勢內容之類的 ptt不是直接去找ptt，有很多人做成web版本的

> Threads、IG 這種也丟到第三方主機，直接用網頁爬，因為官方帳號我們根本沒辦法搜尋

> 你接著把規劃文件做出來。

Decisions recorded from these statements:

| # | Decision | Consequence in this design |
| --- | --- | --- |
| D1 | The full discovery → validation → evolution chain, not a minimal template loop | §8–§12 all in scope; §13 orders the work, it does not cut it |
| D2 | Entry points include papers, community, books/knowledge and economic data | §5 catalogue covers all four, plus news |
| D3 | Prefer existing open-source tools over new development | §6.2: Scrapling, RSSHub, Docling/MinerU, official APIs; RD-Agent as a hypothesis source (§8.1) |
| D4 | Fetch from a third-location host outside GCP | §6 |
| D5 | Taiwan community = Threads, Instagram, 股市爆料同學會, YouTube, PTT web fronts (not Reddit/X) | §5.3 |
| D6 | Threads and Instagram are fetched from the web on the collector host | §5.3, §6.3; the operator accepted the Meta terms risk (§15 R1) |

Standing decisions not reopened here: paper trading only; Codex is not owner or reviewer;
backtest on the underlying and choose the instrument at paper time
(`docs/04/pantheon_persona_instrument_decision_2026-11-07/DEV_PLAN_INSTRUMENT_EXPRESSION_LAYER_2026-11-07.md`);
Yahoo and Anue stay disabled for licence reasons (2026-09-02).

Earlier statements this design reverses (each reversal follows from D1–D6):

| Earlier statement | Where | Reversal |
| --- | --- | --- |
| 「dev 平時維持不對外拉資料，測試時才用 bounded one-shot 手動拉一次」 | `docs/04/pantheon_product_functional_closure_2026-08-20/INDEX.md:19-20`; `docs/04/pantheon_external_data_source_management_2026-08-24/SA_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md:106-113` (AD-SRCM-07) | dev pulls continuously **from the collector host only**; every other egress posture is unchanged |
| AI / OpenClaw source discovery is phase 2 | `…_2026-08-24/INDEX.md:36-46`; `SA_…:140-145` | model-based extraction of hypotheses from fetched text is in scope (§8); model-proposed *source changes* stay draft-only, as before |
| arXiv / SSRN / PDF wait on licence rules | `SA_…:417`; `DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md:358-359` | the rules are now written (§5.2): metadata CC0, PDFs stored for internal extraction only, never re-served |
| "Do not implement a generic arbitrary scraper"; a provider must be selected first | `SD_…:878`; `SA_…:424` | still honoured: no generic scraper; one spider per named source with its own fetch policy (§6.3) |
| Evolution = minimal parameter mutation | `docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/EVOLUTION_GENERATIVE_LOOP_GAP.md:83-95` | bounded search + structural mutation + retirement (§12) |

---

## 2. Current state (hosted dev and `origin/dev` 8173ab180, verified 2026-11-10)

Hosted figures were read with a read-only Postgres session and read-only file reads on the dev VM.

| Stage | What runs | What it produces | Break |
| --- | --- | --- | --- |
| L1 sources | `source_ingest.source_evidence`: 644 `market` + 3 `internal_note` (all three are the all-12 acceptance chain note) | no paper, news, article, social or macro record | egress `deny` by default (`docker-compose.yml:203-204`); text connectors disabled or never built |
| L2 distillation | `services/source_ingestion/distillation_controller.py` | 3,217 `strategy_spec` drafts 2026-09-13…11-06, each one stock's one daily bar; hypothesis = `"Evidence suggests: " + first sentence` (`services/source_ingestion/strategy_seed_builder.py:699-703`). Since 11-06 market records are skipped (`MARKET_SOURCE_SKIP_REASON`, `distillation_controller.py:354`) so nothing is produced | no model call; no text source |
| L3 replication | replication gate | 4 admissions (2 acceptance chain, SPY anchor, one 2330 bar draft) | gate checks document fields, does not run a backtest |
| Research | research runs | 22 runs; **1** real backtest (`rrun-20261016-006`, vectorbt, operator-started, mean Sharpe −0.73; its task still `running`) | no automatic backtest of any candidate |
| L11 evolution | decisions | 653 decisions, **649 `proposed` never acted on**; 444 target evaluations, 0 met | no consumer for `proposed`; retrain = `lookback_bars` 2↔3 (`services/research/main.py:1749-1754`) |
| Execution | persona paper runtime | every running persona uses the coordinator-created `close_to_close_momentum` template | provisioning hard-codes the kind (`services/control-plane/bff/persona_provisioning_coordinator.py:2158`); evaluator accepts only that kind (`services/registry/strategy_artifact.py:1091`, `:1139`, `:1206`) |
| Promotion | policy learning | — | `POST /api/policy-learning/candidates/{id}/promote` is fail-closed 409 by design (`services/policy-learning/main.py:1368-1373`) |

Loop health showing 12/12 green means the controllers have heartbeats; it does not mean output.

Since 2026-11-07 the instrument expression layer is on `dev`: `evaluate_strategy_view`
(`services/registry/strategy_artifact.py:1120`), `expression_policy` validation (`:334`, `:420`),
and `services/execution/lean_runtime/{expression_policy,instrument_candidates,expression_replay}.py`.
This design plugs new strategy kinds into that view step instead of adding a parallel path.

---

## 3. Earlier plans and what happened to them

| Plan | Date | What it planned | Outcome |
| --- | --- | --- | --- |
| Master document §6 (`Pantheon_總索引版系統分析文件.md:381-475`) | 2026-04 | L1 paper/repo/internal ingest → L2 seed → StrategySpec → L3 backtest/RL lab → L11 retrain/freeze/rollback/**mutate**, with evolution feeding back into StrategySpec | only the scaffolding exists |
| `LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md:56-63` | 2026-04-09 | papers daily/hourly by cron, repos daily/weekly, notes on arrival | never revised, never implemented for papers |
| `docs/04/pantheon_data_strategy_source_design_2026-06-09/DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md` | 2026-06-09 | data and strategy-seed registries, catalogue (OpenAlex, arXiv, SSRN, GitHub, QC/LEAN, Qlib, FinRL, vectorbt, internal notes, alpha vendors, DSD:357-367), `strategy_spec_seed`, 8-factor `persona_strategy_match` (DSD:483-528), point-in-time fields (DSD:690-693) | registries and seed store delivered as library code; no external feed admitted |
| `…/EPIC_INTERACTION_DERIVED_SEEDS.md` | 2026-06-12 | seeds from persona/operator conversation with redaction and negative memory | merged; Agora intake surfaces deleted 2026-09-30 |
| `docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/` | 2026-07-14 | EVOLOOP-001…011, first evolved artifact = minimal parameter mutation | mutation reduced to 2↔3 toggle |
| `docs/04/pantheon_external_data_source_management_2026-08-24/` (SRCM) | 2026-08-24 | four-layer source management (ConnectorDefinition / DataSourceEntry / SourceDesiredState / SourceObservedState), commands with receipts, Waves A–D (macro; papers/news; community/alpha DB; alt data) | Phase 1 closed as "functional closure" with dev zero-egress; every catalogue entry still `candidate` (CURRENT_GAP:205-226) |

Never planned anywhere: **books**, **Taiwan macro** (NDC indicators, DGBAS, CBC), **an economic
release calendar**, and **Taiwan community platforms**.

---

## 4. Target architecture

```
 third-location collector host (non-GCP, own account, no GCP credentials)
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ Scrapling spiders  RSSHub  API pollers  Docling/MinerU  Gemini video notes │
 │        └──────────── bundles/<source>/<date>/<run>/ (raw + manifest) ─────┤
 │                      HTTPS bundle server (read-only token)                 │
 └───────────────────────────────▲──────────────────────────────────────────┘
                                 │ the only new egress host
 Pantheon (GCP)                  │
 L1  collector-bundle connector ─┘ → SourceRecord / source_evidence (+ licence, attribution, hashed author)
       │                                   │
       │ text records                      │ numeric / count records
       ▼                                   ▼
 L2  hypothesis extraction (model,     governed feature datasets (point-in-time:
     no tools, cited spans)            fundamentals, flows, macro, social attention/sentiment)
       │ strategy_spec_seed + hypothesis card          │
       ▼                                               │
     rule compiler → factor_rule_v1 artifact ◄─────────┘ (inputs)
       │
 L3  real backtest on Taiwan bars (in-sample / out-of-sample / walk-forward, costs, trial count)
       │ admitted candidate (research_finding_published)
       ▼
     candidate pool → persona_strategy_match → paper binding (within operator paper budget)
       │                                         │
       │                     persona paper runtime: view (evaluate_strategy_view)
       │                     → instrument decision → risk gate → ledger   [2026-11-07 layer]
       ▼                                         │ telemetry, drift
 L11 evolution worker: every proposal → bounded search / structural mutation / retire
       └──── each child re-enters L3; lineage kept ─────────────────────┘
```

One path per concern: one intake door (the bundle connector inside the existing source framework),
one rule format evaluated by one function in backtest and paper, one admission gate, one
evolution consumer. Nothing in this design adds a second registry, a second scheduler on Pantheon,
or a second paper runtime.

---

## 5. Source catalogue (verified 2026-11-10)

"Verified" means the terms page, API document or robots file was read on 2026-11-10, or the
behaviour was observed with a logged-out headless browser from a datacenter address (the GCP dev
host, which behaves like the proposed collector host). Every row names where it is fetched from:
**C** = collector host (§6), **P** = Pantheon pulls directly through the existing egress allowlist.

### 5.1 Macro and economic data (role: feature datasets, §7.4 and §9.2)

| Source | Access verified | Terms verified | Fetch | Cadence |
| --- | --- | --- | --- | --- |
| Central Bank (CBC) statistics | `https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=<code>`, JSON, no key documented; codes e.g. `BP01D01` (daily FX), `EF15M01`/`EF17M01` (money aggregates), `EG2AY01` (rates) | government open-data terms | C | daily |
| NDC business indicators (景氣燈號, leading/coincident index) | data.gov.tw dataset 6099, one ZIP per month | data.gov.tw licence | C | monthly |
| DGBAS CPI | xls download only; no API found | data.gov.tw / DGBAS open-data declaration | C | monthly |
| DGBAS GDP, unemployment | statistical database pages; **no API found** | same | C (manual mapping first) | quarterly / monthly |
| TWSE OpenAPI fundamentals | `openapi.twse.com.tw/v1`: monthly revenue `t187ap05_L`, statements `t187ap06*`/`t187ap07*`, margin `MI_MARGN`, valuation `BWIBBU` | swagger says 「歡迎各位介接使用」, licence → data.gov.tw | P (host already allowlisted) | daily / monthly |
| TPEx institutional flows | data.gov.tw dataset 11856 (OAS API) | data.gov.tw licence | C | daily |
| GDELT DOC 2.0 | no key, max 250 records per call, 3-month default window | commercial use allowed with citation | C | hourly |
| DBnomics | no key | inherits each provider's terms; **almost no Taiwan series** — use only for US/EU/JP/CN context | C | daily |
| Economic release calendar | **not found** as an API for Taiwan | — | open item O-9 | — |

data.gov.tw licence: commercial use allowed, **attribution is mandatory and missing attribution is
treated as never having been licensed** — the record schema must carry the attribution string and
every surface that shows the data must render it (§7.4).

### 5.2 Papers and research (role: strategy hypotheses, §8.1)

| Source | Access verified | Terms verified | Fetch | Cadence |
| --- | --- | --- | --- | --- |
| arXiv (q-fin.*, stat.ML filtered) | export API | 1 request / 3 s, single connection; metadata CC0; **full-text redistribution needs the author's licence** (store PDF only for internal extraction, never re-serve) | C | daily |
| OpenAlex | REST, no key for basic use; USD 1/day free credit; quarterly snapshot free | data CC0 | C | daily |
| NBER / journal RSS via RSSHub | RSSHub routes `nber`, `openalex`, `arxiv` exist | per publisher | C | daily |
| SSRN | no public API found | **UNVERIFIED** | not in first wave | — |

### 5.3 Community and social (role: attention/sentiment features and theme digests, §8.5)

Operator decision 2026-11-10: Threads and Instagram are fetched **from the web on the collector
host**, not through the official API, because without Meta's approval the official keyword search
returns only the app owner's own posts.

| Source | What a logged-out client actually gets (observed 2026-11-10) | Terms / robots | Fetch |
| --- | --- | --- | --- |
| Threads | `threads.com/search?q=台股` returns the first page of recent posts (minutes old) under a login overlay; `threads.com/@<user>` returns about 30 recent posts. No pagination beyond the first page logged out. | `robots.txt` disallows everything; Meta Automated Data Collection Terms require prior written permission. Operator accepted this on 2026-11-10. | C, web |
| Instagram | `/explore/tags/<tag>/` redirects to `/popular/<tag>/` and shows about 12 **popular** reels (captions + view counts, not newest posts). Keyword search and profile pages returned **HTTP 429 → login redirect** after two requests. Content is mostly video; captions are the only text. | Same Meta terms. | C, web, popular-tag pages only (O-3) |
| 股市爆料同學會 (CMoney forum) | Web version `cmoney.tw/forum/stock/<code>` | `robots.txt` allows `/forum/`; terms do not mention crawlers but forbid reproduction and unauthorised commercial activity | C, web (O-4) |
| PTT Stock board | `www.ptt.cc/bbs/Stock/` official web front | `robots.txt` allows generic crawlers; `Content-Signal: ai-train=no`; named AI crawlers disallowed. No training on PTT text; extraction for analysis only. | C, web |
| PTT mirrors | `pttweb.cc`: unofficial, no operator, no terms, no API/RSS. `disp.cc`: separate BBS, RSS `rss.disp.cc/Stock.xml`, reproduction needs written consent | — | fallback only, off by default |
| YouTube | Data API: `search.list` 100 units, `videos.list` 1 unit, 10 000 units/day. `captions.download` only for the video's editor. | ToS forbids automated access except search engines or with permission; the API is the permitted path | C, API key |
| YouTube content understanding | Gemini API accepts a public YouTube URL as input (preview; free tier 8 h of video/day) | Google API terms | C, API key |
| Dcard (optional) | RSSHub route exists | **UNVERIFIED** | not in first wave |

### 5.4 Books and curated knowledge (role: strategy hypotheses and persona knowledge, §8.1)

No web source. Books are **operator-supplied files** (PDF/EPUB the operator owns or licenses)
placed in a library bucket on the collector host; parsed by Docling or MinerU into sections with
page references. Nothing is downloaded from shadow libraries. The plan documents never had this
entry (verified 2026-11-10, §3); it is new.

### 5.5 News (role: event features and hypothesis cards, §8.1)

RSSHub routes verified to exist: `cna`, `udn`, `ltn`, `chinatimes`, `cw`. Yahoo and Anue (cnyes)
stay **DISABLED_BY_BUILD** (2026-09-02 licence decision); the collector host does not re-enable
them — isolation does not make a source licensed. RSSHub has no `cnyes` and no `ptt` route.

### 5.6 Existing Taiwan market connector — compliance finding

`services/source_ingestion/connectors/taiwan_official.py` downloads three datasets from **website**
endpoints, not from the OpenAPI:

* TWSE `https://www.twse.com.tw/rwd/zh/fund/T86` (institutional flows),
* TWSE `https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY` (per-stock history),
* TPEx `https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock` (per-stock history),

plus the holiday schedule page. TWSE and TPEx website terms forbid automated download of website
data without consent; the OpenAPI is explicitly offered for integration. The OpenAPI has **no T86**.
Moving these calls to the collector host does not change the terms. Operator choices are listed as
O-1 (§14); this design does not change the connector.

---

## 6. Collector host

### 6.1 Purpose and boundary

The collector host exists for one reason stated by the operator: web fetching must not put the GCP
organisation at risk. It is a **separate provider, separate account, separate payment method**,
holding **no GCP credential, no Pantheon secret, no product database access**. If the provider
suspends it, Pantheon loses new external text for the duration and nothing else.

It is a fetcher and a file server, not a second ingestion pipeline. It does not normalise into
Pantheon schemas, does not call Pantheon, does not decide what is a strategy. Pantheon pulls from it.

Provider candidates verified 2026-11-10 (operator choice O-5):

| Provider | Region | Size | Price | Acceptable-use check |
| --- | --- | --- | --- | --- |
| DigitalOcean | Singapore | 2 vCPU / 4 GB | USD 24 / month | AUP forbids attacking/scanning networks and abusing DO's own services; no clause against fetching public web pages |
| Hetzner Cloud | Singapore | CPX22 | EUR 26.99 / month excl. tax | terms forbid network abuse; no clause against fetching public web pages |

Tokyo regions, Vultr and Linode were not verifiable on 2026-11-10 (pages refused automated reads).

### 6.2 Components (all existing open-source tools, no new crawler code)

| Component | Tool (licence) | Used for |
| --- | --- | --- |
| Web spiders | Scrapling (BSD-3): HTTP fetcher first, browser fetcher only where the page needs JavaScript (Threads, Instagram, CMoney) | Threads, Instagram, CMoney, PTT, CBC, DGBAS |
| RSS bridge | RSSHub (AGPL-3.0, run unmodified as a separate container; Pantheon only consumes its HTTP output) | news, NBER, arXiv/OpenAlex feeds |
| Document parsing | Docling or MinerU | operator books, arXiv PDFs |
| API pollers | small scheduled jobs using each provider's documented API | TWSE-adjacent data.gov.tw, GDELT, OpenAlex, arXiv, YouTube Data API, Gemini video understanding |
| Scheduler | systemd timers | per-source cadence |
| Bundle server | static HTTPS file server (Caddy or nginx) with a bearer token, read-only | Pantheon pull (§7) |

Scrapling's stealth fetcher, Cloudflare/Turnstile bypass, CAPTCHA solving and proxy rotation are
**disabled by configuration** and the configuration is checked at start-up (O-3 records the only
operator-decidable exception).

### 6.3 Fetch policy (enforced per source in the collector config)

* One config entry per source: `source_id`, `base_urls`, `cadence`, `max_requests_per_run`,
  `min_interval_seconds`, `fetcher` (`http` | `browser` | `api`), `respect_robots` (true except
  the Meta sources the operator exempted on 2026-11-10), `logged_in=false`, `enabled`.
* HTTP 429, 403 or a login redirect on two consecutive runs **auto-pauses** that source and writes
  a `paused` manifest entry; it resumes only by a config change. No retry storm, no IP change.
* The HTTP fetcher sends a project user agent with an operator-controlled contact address; the
  browser fetcher uses the stock Chromium user agent with no fingerprint spoofing.
* No login, no account creation, no cookie reuse. Meta sources are fetched logged-out (§5.3).

### 6.4 Personal data handling (Taiwan Personal Data Protection Act)

Public posts are personal data when they identify the author. PDPA art. 19(1)(iii) permits a
non-government collector to collect data the subject made public, **only for a specific purpose and
with a reasonable link to it**; later use outside that purpose needs art. 20. Design rules:

* purpose recorded in the collector config: "aggregate market attention and sentiment research";
* author handles are replaced on the collector by `HMAC(salt, platform + handle)`; the salt never
  leaves the collector host; display names, avatars, follower lists and profile pages are not stored;
* post text is kept for 90 days raw, then only derived features and the hashed id remain;
* no per-person profile, no cross-platform identity linking, no outbound contact.

This is a design control, not legal advice; O-6 asks the operator whether counsel reviews it.

### 6.5 Bundle format (the only interface to Pantheon)

```
bundles/<source_id>/<yyyy-mm-dd>/<run_id>/
  manifest.json      # run metadata + one entry per item
  items.jsonl        # normalised-by-collector minimal item: id, url, fetched_at, published_at, text|null, lang, hashed_author|null, metrics{}
  raw/<sha256>.<ext> # original bytes (HTML, JSON, PDF, CSV, XLS) for audit
```

`manifest.json` fields: `schema_version`, `source_id`, `run_id`, `collector_version`,
`started_at`, `finished_at`, `fetch_policy_hash`, `robots_decision` (`allowed` | `exempted_by_operator_2026-11-10` | `not_applicable_api`),
`terms_ref` (URL + date read), `attribution` (required for data.gov.tw sources), `items[]`
(`item_id`, `url`, `http_status`, `content_sha256`, `raw_path`, `bytes`), `status`
(`complete` | `partial` | `paused`), and `bundle_sha256` over the sorted item hashes.

### 6.6 Operations

* Infrastructure is described in this repository (a `deploy/collector/` folder with compose file,
  collector config and systemd units); the host is created by the operator under their own provider
  account (O-5). No provider credential is stored in this repository or on Pantheon hosts.
* The only inbound port is 443 for the bundle server; SSH by key from the operator's machine.
* Disk budget: bundles older than 90 days are pruned. The collector never learns what Pantheon
  consumed (Pantheon only reads); Pantheon alarms when its cursor lags more than 7 days (§7.5).
* Health: the collector publishes `status.json` (per-source last run, status, paused reason) on the
  bundle server; Pantheon reads it into the existing source health surface.

---

## 7. Intake into Pantheon (one door)

### 7.1 Connector definitions — reuse the source management model

The 2026-08-24 source management model is implemented and is reused unchanged:
`ConnectorDefinition` entries in `_CANONICAL_DEFINITIONS`
(`services/source_ingestion/connector_definitions.py:319-976`), the runtime adapter allowlist
`ALLOWED_PROVIDER_ADAPTERS` (`services/source_ingestion/provider_adapters.py:956`), creation
always `configured_disabled` with the schedule off
(`services/source_ingestion/source_management_commands.py:348-358`), and the validate / canary /
enable / disable / degrade / resume commands with receipts exposed on
`/bff/management/data-sources` (`services/control-plane/bff/console_gap/datasources.py:186-1062`).

Additions:

* **One adapter token**, `CollectorBundleAdapter.records_from_bundle`, added to
  `ALLOWED_PROVIDER_ADAPTERS`. It is the only code that talks to the collector host.
* **One `ConnectorDefinition` per collector source** so that every source keeps its own lifecycle,
  canary and kill switch: `collector-tw-threads`, `collector-tw-instagram-popular`,
  `collector-tw-cmoney-forum`, `collector-tw-ptt-stock`, `collector-youtube-finance`,
  `collector-news-rss`, `collector-tw-macro-cbc`, `collector-tw-macro-ndc`,
  `collector-tw-macro-dgbas`, `collector-tw-tpex-flows`, `collector-gdelt`,
  `collector-library-books`. All have `allowed_host_patterns = [<collector host>]`.
* The existing `strategy-seed-paper-corpus` definition (`connector_definitions.py:805`, provider
  "ArXiv/OpenAlex/SSRN") has **no implementing class** for its adapter token
  (`StrategySeedPaperCorpusAdapter.records_from_metadata`). It is re-pointed to the bundle adapter
  instead of adding a second paper definition. `social-admitted-market-discussion` (StockTwits,
  `:911`) is left as it is.

### 7.2 Pull and verification

* The adapter reads `https://<collector host>/bundles/index.json` and every bundle newer than the
  cursor kept in the definition's `SourceObservedState`, through `open_external_url`
  (`services/external_egress.py:457-478`), with a bearer token from a secret named
  `PANTHEON_COLLECTOR_BUNDLE_TOKEN` (name only; value lives in the deployment secret store).
* Verification before any record is written: `manifest.source_id` equals the definition's source,
  `schema_version` is known, every `content_sha256` matches its raw file, and `bundle_sha256` is
  recomputed. Any mismatch fails the run with no partial write.
* Raw bytes are copied into the existing raw storage that every run already uses
  (`raw/<connector>/<dataset>/date=…/`, `services/source_ingestion/market_data_storage.py:202-227`,
  `services/source_ingestion/pipeline.py:360-377`).

### 7.3 Egress — exactly one more host, continuously

Today every container defaults to `deny` with an empty host list (`docker-compose.yml:203-204`,
`:745-746`; mode default `services/external_egress.py:106-112`). Bounded refreshes recreate
`source-ingest` with an exact host set and restore `deny` afterwards
(`scripts/deploy_nonprod_vm.sh:2190-2197`), and the refresh guard accepts only two connectors with
exact host sets (`scripts/deploy_nonprod_vm.sh:1316-1332`).

Change:

* `source-ingest` steady state becomes `PANTHEON_EXTERNAL_EGRESS=allowlist` with
  `PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=<collector host>` — exactly one name, no wildcard (the gate
  already rejects wildcards, ports and IP literals, `services/external_egress.py:115-130`). The
  "restore" step restores to this posture instead of `deny`.
* The bounded TWSE/TPEx and TAIFEX refreshes keep their exact host sets; the collector pull simply
  pauses for the minutes of a bounded refresh.
* The refresh guard gains a `collector-bundle` branch that requires the host set to equal
  `{<collector host>}`.
* The canary stage `EGRESS_POLICY_ADMITTED` is today always marked passed
  (`source_management_commands.py:537-545`) and `allowed_host_patterns` is metadata only
  (`connector_definitions.py:71`). The canary is changed to compare the definition's hosts with the
  process allowlist and fail on any difference.

No other service gets egress. The research orchestrator stays `deny`.

### 7.4 Record mapping

Each bundle item becomes one `SourceRecord` (`services/source_ingestion/connectors/base.py:488-497`)
with `source_type` from the definition (`social`, `news`, `paper`, `macro`, `filing`; enum
`base.py:102-112`). The existing external-source contract already demands what is needed and is
reused as is (`services/source_ingestion/external_sources.py:94-180`):

* all external records: `license_scope`, `entitlements`, `event_time` (= published_at),
  `available_time` (= collector `fetched_at`), sha256 `content_hash`,
  `direct_execution_allowed = false`;
* social: `platform`, `author_id_hash` (= the collector HMAC, §6.4), `post_id`,
  `platform_policy_ref` (= manifest `terms_ref`), `trust_score` (initial per platform from the
  definition) (`external_sources.py:229-234`);
* news: `publisher`, `published_at` (`external_sources.py:223-226`).

`LicensePolicy` (`base.py:283-317`) carries `attribution_required` and `redistribution_allowed`:
`redistribution_allowed = false` for every collector source; `attribution_required = true` with
the attribution text in `policy_ref` for every data.gov.tw source. Management surfaces that render
such data show the attribution.

Numeric series (macro, fundamentals, flows, social aggregates of §8.5) are written as normalized
datasets in the same storage with the point-in-time fields `event_time` / `available_time` /
`ingest_time` (`DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md:690-693`), so that the training-session
dataset path that already serves real backtests
(`services/training-session/source_dataset_authority.py:178-356`) can read them.

### 7.5 Health

The collector's `status.json` (per-source last run, status, paused reason) is read on every pull into
the definition's `SourceObservedState` and shown on the existing data-sources page. A source whose
newest bundle is older than twice its cadence, or whose cursor lags more than 7 days, is `degraded`.

---

## 8. From text to hypotheses (L2)

### 8.1 What each source family is for

| Family | Becomes | Why |
| --- | --- | --- |
| Papers, books, research articles | hypothesis cards (§8.2) | they state mechanisms that can be tested |
| YouTube analysis videos | Gemini video notes → hypothesis cards | same, for video |
| News, GDELT | event features + hypothesis cards for explicit claims | context and dated events |
| Threads, Instagram, CMoney, PTT | per-symbol daily attention/sentiment **features** (§8.5) + weekly theme digest | single posts are noise; aggregates are measurable |
| Macro, fundamentals, flows | governed feature datasets | inputs to rules (§9) |

### 8.2 Hypothesis extraction

The distillation worker (`services/source_ingestion/distillation_worker.py`) gains one model step for
records of type `paper`, `news`, `social` (only theme digests), and library books:

* call path: the existing `SemanticExtractionClient`
  (`services/source_ingestion/semantic_extraction_client.py:243`, token limits `:76-79`, today used
  only by an evaluation script) → OpenClaw adapter `/structured`, which only admits the dedicated
  deny-all extraction agent (`services/openclaw-gateway-adapter/main.py:1823`, `:1863-1880`);
* admission first: `SemanticExtractionAdmission` (`services/source_ingestion/semantic_extraction.py:372-440`)
  checks tenant, status, licence and point-in-time; the collector licence scopes are added to its
  permitted list explicitly, one by one;
* output: a **hypothesis card** (JSON schema, new contract `hypothesis_card.v1`):
  `claim`, `mechanism`, `signal_inputs[]` (named against the governed dataset catalogue),
  `rule_sketch` (in the vocabulary of §9), `horizon`, `universe`, `direction`, `conditions`,
  `cited_spans[]` (`source_record_id`, start, end), `testability`
  (`testable` | `needs_data:<dataset>` | `not_testable`), `origin`
  (`paper` | `book` | `video` | `news` | `community_theme`);
* the card is merged into the record's `metadata.strategy_seed`, which the existing seed builder
  already prefers over its heuristics (`services/source_ingestion/strategy_seed_builder.py:201`
  reads `hypothesis`/`strategy_hypothesis` before falling back; nested `strategy_seed` is merged at
  `:551` and `:583`); the registry delivery path stays the same (`strategy_seed_builder.py:163-165`,
  `services/research/strategy_spec/conversion.py:283-314`);
* the `"Evidence suggests: " + first sentence` fallback (`strategy_seed_builder.py:201`, `:699-703`) is
  disabled for every external text source: no card → no draft, recorded as `no_hypothesis`.
  Market records keep being skipped (`MARKET_SOURCE_SKIP_REASON`,
  `services/source_ingestion/distillation_worker.py:50-53`).

### 8.3 Untrusted text

Fetched text is attacker-controllable. There is no quarantine status today
(`base.py:145-150`). Controls:

* the model sees the text only inside a data block of the deny-all agent: no tools, no browsing,
  no follow-up URLs, schema-only output;
* every `cited_spans` entry must be an exact substring of the stored record text — checked in code;
  a card with an unverifiable citation is rejected;
* output keys are allowlisted; route and sink keys are already rejected in metadata
  (`external_sources.py:306`);
* raw external text never enters persona memory; only reviewed findings do
  (`SA_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md:127-132`, kept).

### 8.4 De-duplication and support

After compilation (§9.4) each card has a rule fingerprint. Cards with the same fingerprint from
different records raise a `support_count` instead of creating a new draft; the per-source yield is
counted in the existing `SourceUsageDaily.strategy_seed_yield_count`
(`DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md:657-670`).

### 8.5 Community features

Per symbol and trading day: `mention_count`, `unique_authors` (hashed), `engagement_weighted_sentiment`
(model-classified in batches), `attention_z` (vs 60-day mean). Symbols are detected from the code
and the listed name dictionary. `available_time` = the end of the collection day; a feature is never
visible to a rule before that. Bursts dominated by one hashed author are down-weighted. Weekly, the
top posts per platform are summarised into a theme digest, which goes through §8.2 with
`origin = community_theme`.

### 8.6 RD-Agent

RD-Agent's value is generating and running factor code against Qlib (Chinese market data by
default). Running model-generated code in Pantheon contradicts §9 (no arbitrary code). It is
therefore **not** in waves 1–4. Wave 5 may run it in an isolated sandbox with no Pantheon secret;
only its factor *formulations* enter Pantheon, as hypothesis cards through §8.2.

### 8.7 Books

Operator-supplied files in the collector library are parsed by Docling or MinerU into chapters with
page references, then go through §8.2 with `origin = book`. Findings reviewed by the operator may be
published to persona memory as `research_finding_published`
(`SD_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md:1095-1117`).

---

## 9. Executable rule format

### 9.1 Why a new kind

The artifact schema fixes `strategy_logic.kind` to the constant `close_to_close_momentum`
(`services/registry/strategy_artifact.schema.json:93-94`). The same single kind is enforced in
`evaluate_strategy_action` (`services/registry/strategy_artifact.py:1091`), `evaluate_strategy_view`
(`:1139`), `EXECUTION_RECIPE_KIND` (`:1206`), the paper producer's single supported interpreter
(`services/execution/lean_runtime/paper_signal_producer.py:413-415`), the vectorbt sizing
(`services/research/vectorbt/adapter/vectorbt_adapter.py:513-562`) and provisioning
(`services/control-plane/bff/persona_provisioning_coordinator.py:2158`). A hypothesis about revenue
growth, institutional flows, macro regime or attention cannot be expressed.

### 9.2 `factor_rule_v1`

A second `strategy_logic.kind`, declarative, with no executable code:

```json
{
  "kind": "factor_rule_v1",
  "inputs": [{"name": "rev_yoy", "dataset": "tw_monthly_revenue", "field": "yoy", "availability_lag_days": 10}],
  "signal": {"op": "ts_zscore", "args": [{"ref": "rev_yoy"}, {"const_param": "z_window"}]},
  "entry": {"long_when": {"op": "gt", "args": [{"ref": "signal"}, {"const_param": "entry_z"}]},
            "short_when": null},
  "exit": {"max_holding_bars": {"const_param": "hold_bars"}, "flat_when": {"op": "lt", "args": [{"ref": "signal"}, 0]}},
  "strength": {"op": "clip01", "args": [{"op": "div", "args": [{"ref": "signal"}, {"const_param": "strength_scale"}]}]}
}
```

* Operators are an allowlist: `lag`, `delta`, `pct_change`, `ts_mean`, `ts_std`, `ts_zscore`,
  `ts_rank`, `ts_min`, `ts_max`, `ratio`, `add`, `sub`, `mul`, `div`, `abs`, `sign`, `clip01`,
  `gt`, `lt`, `and`, `or`, `not`. No loops, no user functions, bounded depth and window.
* Inputs reference governed datasets only (closes and volumes; TWSE OpenAPI fundamentals; TPEx
  flows; macro series; §8.5 community features). Each input declares its availability lag; the
  evaluator only sees values with `available_time ≤` the decision time.
* Every numeric constant is a `const_param` declared in `parameters` and in
  `mutation_surface.controls` with a range and step; the structure is immutable. The validator's
  "every parameter is mutable or immutable" rule (`strategy_artifact.py:543-568`) stays; its
  momentum-specific key assumptions (`:609-623`) move under the momentum branch.

### 9.3 One evaluator in backtest and paper

* `evaluate_strategy_view` and `evaluate_strategy_action` dispatch on `kind`; the output stays the
  2026-11-07 view `{action, direction, strength, …}`, so the instrument decision, risk gate and
  ledger downstream (`services/execution/lean_runtime/expression_policy.py`,
  `instrument_candidates.py`) work unchanged.
* The paper producer passes only `market_input.closes` today
  (`paper_signal_producer.py:617-646`); it gains a point-in-time `market_input.features` map read
  from the governed datasets, and its interpreter allowlist accepts both kinds.
* The vectorbt adapter computes orders through the same evaluator per bar with the same feature
  snapshots.
* **Parity test** (acceptance of the runtime task): for a fixed artifact and fixed data, the action
  sequence from the backtest equals the sequence from a paper replay, bar for bar.

### 9.4 Card → rule compilation

The model proposes a `factor_rule_v1` JSON for a `testable` card using only the catalogue of
datasets and operators (same constrained style as `alpha_rule_query.v1`,
`SD_EXTERNAL_DATA_SOURCE_MANAGEMENT_2026-08-24.md:1039-1067`). Code validates it against the schema;
anything else becomes `not_expressible` with a reason, or `needs_data:<dataset>` which is counted per
dataset so the operator sees which data would unlock most hypotheses.

### 9.5 Universe

Personas today trade a provisioned symbol list. `factor_rule_v1` v1 rules are per-symbol time-series
rules over that list. Cross-sectional rules ("buy the top five by revenue acceleration") need a
trade-time universe, which the 2026-11-07 plan explicitly left for a next stage (its §9, "view
universe"). It is wave 4 here (§13) and depends on that stage.

---

## 10. Validation (L3)

### 10.1 What the gate does today

The alpha replication controller (`services/research/alpha_replication/replication_controller.py:147-349`)
runs `ReplicationGate().evaluate_candidate` (`services/research/replication/gate.py:129-410`) on
JSON-schema validity, lineage keys, governance flags and bypass fields. The worker fills the
governance flags itself (`"verified"`, `"high"`, `"ready_for_replication"`,
`services/research/alpha_replication/revalidation_worker.py:701-728`) and records an experiment run
with `artifact_refs: []` (`:753`). No market data is read. A real vectorbt backtest exists
(`services/research/main.py:3150-3208`, real only with the backend flag) and is already used by the
retrain path through training preview jobs on the Taiwan dataset
(`services/research/main.py:1761-1830`; `services/training-session/source_dataset_authority.py:178-356`).

### 10.2 Change

* The worker stops writing governance values it did not check.
* After the schema check, admission requires a **real backtest run** of the candidate artifact
  through the same training-preview path, on the governed Taiwan daily bars and feature datasets.
* The run id and metrics go into the experiment run's `artifact_refs` and into a
  `research_finding_published` record.

### 10.3 Protocol (proposed thresholds; operator may change)

| Item | Rule |
| --- | --- |
| Windows | in-sample = first 70 % of available history; out-of-sample = last 30 %, **never** used by search or evolution; plus 3 walk-forward folds inside in-sample |
| Costs | commission and securities transaction tax on every fill; rates taken from the paper cost model, verified against the statutory rates in the implementing task |
| Benchmarks | buy-and-hold of the same symbols; 0050 |
| Trial accounting | every evaluated variant of a hypothesis family increments a trial counter; the admission Sharpe threshold is deflated by the trial count |
| Admission | out-of-sample Sharpe > 0 after costs and above the buy-and-hold Sharpe; max drawdown within the persona mandate; ≥ 20 round trips out-of-sample; consistent sign across walk-forward folds |
| Data sufficiency | history length per dataset is measured in wave 3; a candidate whose inputs have less than 2 years of point-in-time history is `insufficient_history`, not rejected |

The registry `PromotionGate` (`services/registry/promotion/gate.py:77-99`) already asks for
`replication_success` and a Sharpe value; it now receives real values.

---

## 11. Personas choose strategies

### 11.1 Today

Provisioning accepts name, mandate, symbols, bar frequency, data source, market, budget, currency,
risk policy and traits; `_strategy_artifact_payload` (`persona_provisioning_coordinator.py:2121`)
always builds the momentum template. There is no field to bind an existing registry artifact and no
selection among candidates.

### 11.2 Change

* Provisioning accepts an optional `strategy_artifact_ref` (registry id, version, checksum). When
  present, the saga uses that admitted artifact instead of building the template; every other saga
  step (binding, deployment plan, schedule) is unchanged.
* A **discovery allocator** runs daily in the research orchestrator: for each discovery persona slot
  it scores admitted candidates with the existing 8-factor `persona_strategy_match`
  (`DATA_STRATEGY_SOURCE_SYSTEM_DESIGN.md:483-528`) against the persona mandate and binds the best
  unbound one through the provisioning command.
* Autonomy envelope (O-8): at most N concurrent discovery personas (recommended 5) with a paper
  capital cap; inside it no operator approval is needed; beyond paper nothing changes.
* The existing coordinator-created personas (青禾, 衡岳, 澄川) stay as baselines for comparison.

---

## 12. Evolution (L11)

### 12.1 Today

Decision states are proposed / reviewed / approved / executed / rejected / canceled / superseded
(`services/control-plane/governance/evolution_decision.py:60-67`). Nothing moves a `proposed`
decision automatically; operator commands call approve and review
(`services/control-plane/bff/command_executor.py:1014`, `:1089`), and the dispatch worker sends
approved intents to research (`services/evolution/dispatch_worker.py:401-424`). Only `retrain` has an
executor (`services/research/main.py:1589`, `:1856`), and its change is lookback 2↔3 or threshold
0↔0.01 (`:1749-1758`). `research_revalidate` has no executor. The `parameter_search` stage maps to
Ray Tune (`services/research/constants.py:6`), which is deferred, so the stage returns 503.

### 12.2 Change

* **Automatic consumer** in `services/evolution`: proposals whose target is a discovery-persona
  artifact and whose action is retrain / mutate / revalidate / retire are reviewed and approved by
  an automated gate with recorded evidence, inside the §11 envelope. Everything else still needs
  the operator.
* **Retrain** = bounded random search over the artifact's declared `mutation_surface` ranges on the
  in-sample window, fixed budget per decision (e.g. 50 variants), scored by walk-forward. It replaces
  the 2↔3 toggle. It runs inside the existing vectorbt stage; Ray Tune is not revived.
* **Mutate** (`factor_rule_v1` only) = structural operators: change a window, swap an operator within
  the same type, add or remove one filter from the feature catalogue, change the exit. Each child
  keeps lineage to its parent.
* **Revalidate** = the §10 gate (gives `research_revalidate` its executor).
* **Retire** = governed persona retire when out-of-sample paper performance decays (the existing
  L10 → L11 triggers in `services/evolution/main.py:2820-2844`).
* Every child passes §10 on the untouched out-of-sample window; trial counts accumulate per family.

### 12.3 Legacy proposals

The 649 `proposed` decisions were produced before any discovered strategy existed, so none of them
targets one. They are not fed to the new consumer (O-10).

---

## 13. Delivery waves and proposed tasks

Waves are the order of delivery, not a reduction of scope: every element of §5–§12 is in a wave.
Task ids get their date suffix when opened. Owners alternate between the Claude and Antigravity
lanes so the other lane reviews; Human/Ops tasks are operator actions or hosted acceptance and are
not dispatched to workers. Codex is neither owner nor reviewer. No task claims hosted behaviour
before its wave's acceptance task passes.

| Wave | Task | Owner → Reviewer | Depends on | Scope | Done when |
| --- | --- | --- | --- | --- | --- |
| 0 | operator decisions O-1…O-12 | operator | this document | §14 | answers recorded in this document |
| 0 | `COLLECTOR-HOST-PROVISION` | Human/Ops → Claude | O-5 | operator creates the provider account, host, DNS name, TLS and bundle token; nothing from GCP | host answers `status.json` over HTTPS |
| 1 | `COLLECTOR-STACK` | Antigravity → Claude | this document | `deploy/collector/`: compose, fetch-policy config with auto-pause (§6.3), Scrapling spiders (Threads, Instagram popular tags, CMoney, PTT), RSSHub, pollers (CBC, data.gov.tw 6099/11856, DGBAS, GDELT, arXiv, OpenAlex, YouTube Data API, Gemini video notes), Docling library, HMAC author hashing (§6.4), bundle writer and server (§6.5); stealth/proxy/CAPTCHA settings rejected at start-up | spider and poller tests on captured fixtures; manifest schema test; no live call in CI |
| 1 | `SOURCE-COLLECTOR-BUNDLE-CONNECTOR` | Claude → Antigravity | this document | §7.1, §7.2, §7.4, §7.5; canary egress comparison (§7.3 last bullet) | definitions created disabled; tampered bundle rejected; social/news/macro records pass the external-source contract; attribution carried |
| 1 | `DEV-EGRESS-COLLECTOR-HOST-GATE` | Antigravity2 → Claude | bundle connector | §7.3: steady-state allowlist of exactly the collector host, refresh guard branch, restore posture | exact-host tests; bounded TWSE/TAIFEX refresh unchanged |
| 1 | `DISCOVERY-W1-HOSTED-ACCEPTANCE` | Human/Ops → Claude | wave 1, O-2…O-6, O-11 | each enabled source produces records on three consecutive days; `source-ingest` egress is the collector host only outside bounded refreshes; data-sources page shows health and pauses | hosted evidence |
| 2 | `GOVERNED-FEATURE-DATASETS` | Antigravity → Claude | wave 1 | TWSE OpenAPI fundamentals (revenue, statements, margin, valuation) on the existing official connector; TPEx flows; CBC, NDC, DGBAS series; community features (§8.5); point-in-time fields | history length per dataset reported; availability lag enforced in a test |
| 2 | `L2-HYPOTHESIS-EXTRACTION` | Claude → Antigravity | wave 1, O-7 | §8.2–§8.4, §8.7; `hypothesis_card.v1` contract; budget stop | injected-instruction fixtures produce no tool call and no extra keys; fabricated citation rejected; fallback hypothesis gone for external text |
| 2 | `DISCOVERY-W2-HOSTED-ACCEPTANCE` | Human/Ops → Claude | wave 2 | real papers/posts on hosted produce cards with verifiable citations; spend within cap | hosted evidence |
| 3 | `STRATEGY-ARTIFACT-FACTOR-RULE-KIND` | Antigravity2 → Claude | wave 2 | §9.1–§9.3 (schema, validator, both evaluators, producer features, vectorbt) | parity test; every existing momentum artifact validates with an unchanged checksum |
| 3 | `HYPOTHESIS-RULE-COMPILER` | Claude → Antigravity | factor rule kind | §9.4 | `not_expressible` / `needs_data` counted; no free code accepted |
| 3 | `REPLICATION-GATE-REAL-BACKTEST` | Antigravity → Claude | factor rule kind | §10 | gate refuses admission without a real run; self-written governance values removed; trial counter |
| 3 | `PERSONA-DISCOVERY-ALLOCATOR` | Claude → Antigravity | gate, O-8 | §11 | provisioning with `strategy_artifact_ref`; allocator within envelope |
| 3 | `BFF-DISCOVERY-READ-SURFACES` | Antigravity2 → Claude | gate | existing research items, alpha factory, evolution journal and strategy detail read routes show cards with citations, in-/out-of-sample metrics, lineage to source records, attribution | real-shape tests |
| 3 | `FE-DISCOVERY-VIEWS` (`execute-plans`) | Antigravity → Claude | BFF surfaces | the same pages render them | component tests; checked in a real browser by the acceptance task |
| 3 | `DISCOVERY-W3-HOSTED-ACCEPTANCE` | Human/Ops → Claude | wave 3 | one persona paper-running a `factor_rule_v1` strategy whose lineage reaches an external source record, visible in the browser | hosted evidence |
| 4 | `EVOLUTION-AUTO-CONSUMER-SEARCH` | Antigravity2 → Claude | wave 3 | §12 | automated child from a proposal, judged on out-of-sample; automated retire on decay |
| 4 | `SD-VIEW-UNIVERSE-CROSS-SECTIONAL` | Claude → Antigravity | wave 3 | design for trade-time universe (§9.5) | design merged, operator decisions recorded |
| 4 | `DISCOVERY-W4-HOSTED-ACCEPTANCE` | Human/Ops → Claude | wave 4 | evolution children and retirements happen without operator action inside the envelope | hosted evidence |
| 5 | `RD-AGENT-SANDBOX` (optional) | Claude → Antigravity | wave 3 | §8.6 | formulations arrive as cards; no generated code reaches Pantheon |
| — | `TWSE-TPEX-WEBSITE-ENDPOINT-COMPLIANCE` | per O-1 | O-1 | §5.6 | per the operator's choice |

Sources whose decision is open (Instagram depth, CMoney, PTT mirrors) are built in wave 1 but stay
`configured_disabled` until the operator enables them through the existing source commands; enabling
does not need a code change.

YouTube Data API and Gemini keys must come from a Google account and project **outside** the
Pantheon GCP organisation, held only on the collector host.

---

## 14. Open questions for the operator

| # | Question | Options | Recommendation |
| --- | --- | --- | --- |
| O-1 | TWSE/TPEx **website** endpoints in `taiwan_official.py` (T86, STOCK_DAY, tradingStock, holiday page) | (a) ask TWSE/TPEx for written consent; (b) FinMind Sponsor Pro (NT$3,330/month, the only FinMind tier licensed for commercial use including internal systems); (c) OpenAPI + data.gov.tw only (TPEx flows: dataset 11856; per-stock daily from `STOCK_DAY_ALL`, which is current-day only, so history is built forward) | (a) in parallel with (c); (b) if consent is refused and T86 history is needed |
| O-2 | Recorded: Threads and Instagram fetched from the web on the collector host (D6) | — | — |
| O-3 | Instagram depth: logged out yields only ~12 popular reels per tag and 429 on profile/search | (a) popular-tag pages only; (b) a logged-in account; (c) residential proxies / stealth | (a). (b) breaches terms the account holder accepted and risks the account; (c) is evasion. Neither is in this design unless the operator decides otherwise |
| O-4 | 股市爆料同學會 | (a) low-rate public stock pages, internal analysis only, no reproduction; (b) ask CMoney for a data licence | (b) started, (a) until answered |
| O-5 | Collector provider | DigitalOcean Singapore USD 24/mo; Hetzner Singapore EUR 26.99/mo | either; the account and payment method must be the operator's and not linked to the GCP billing account |
| O-6 | Personal data: counsel review of §6.4 | yes / no | yes before Threads/Instagram/CMoney/PTT go above test volume |
| O-7 | Model budget for L2 extraction and weekly theme digests | monthly cap in currency; provider | set a cap; the extractor stops at the cap and records `budget_exhausted` |
| O-8 | Autonomy for paper binding | (a) every discovered strategy needs operator approval; (b) automatic within N concurrent discovery personas and a paper capital cap | (b) with N = 5; anything beyond paper unchanged |
| O-9 | Economic release calendar (no Taiwan API found) | (a) hand-maintained yearly calendar from agency release schedules; (b) a paid calendar | (a) |
| O-10 | 649 legacy `proposed` evolution decisions | (a) supersede as legacy; (b) let the new consumer process them | (a): they target release probes and retired template strategies |
| O-11 | PTT mirrors (`pttweb.cc`, `disp.cc`) | on / off as fallback | off |
| O-12 | Books | operator supplies owned/licensed files to the library | yes; nothing is downloaded |

## 15. Risks and unverified items

| # | Risk | Control |
| --- | --- | --- |
| R1 | Meta terms and `robots.txt` forbid automated collection of Threads/Instagram; Meta litigates scraping. US case law (Meta v. Bright Data, 2024) treated logged-off collection of public data differently from logged-in collection; this is not Taiwanese law and not legal advice | logged-out only (§6.3), separate host and account, per-source kill switch, no reproduction, O-6 |
| R2 | GCP risk is not zero: Pantheon still pulls from one external host | that host is the operator's; no third-party site is contacted from GCP for these sources |
| R3 | Prompt injection through fetched text | model sees text only as data, no tools, schema-only output, cited spans checked (§8.3) |
| R4 | Overfitting from automated search | trial counts, untouched holdout, deflated thresholds (§10.3) |
| R5 | Social data is noisy and manipulable (pump posts) | used as aggregated features with lag; never a direct trade trigger; single-account bursts discounted |
| R6 | Licence drift: a source changes its terms | terms URL + date in every manifest; quarterly terms re-read task |
| R7 | Cost: model tokens | O-7 cap enforced in code |
| R8 | Existing TWSE/TPEx website downloads | O-1 |

Unverified on 2026-11-10: Instagram and Threads terms pages (render as an empty shell; robots and the
Meta Automated Data Collection Terms were read instead); a PTT terms page (`www.ptt.cc` home returned
403 to an automated read; only `robots.txt` was read); TAIFEX website terms (empty page); Tokyo
regions, Vultr, Linode; a DGBAS API; T86 on data.gov.tw; SSRN API; Dcard terms.

---

## 16. Sources (read 2026-11-10)

Meta / social:
[Threads keyword search](https://developers.facebook.com/documentation/threads/keyword-search) ·
[Instagram hashtag search](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/hashtag-search) ·
[Meta Automated Data Collection Terms](https://www.facebook.com/legal/automated_data_collection_terms) ·
[Meta v. Bright Data analysis](https://www.fbm.com/technology/publications/major-decision-affects-law-of-scraping-and-online-data-collection-meta-platforms-v-bright-data/) ·
[Instaloader troubleshooting](https://instaloader.github.io/troubleshooting.html) ·
[CMoney terms](https://www.cmoney.tw/member/tos.aspx) ·
[PTT robots.txt](https://www.ptt.cc/robots.txt) · [pttweb.cc](https://www.pttweb.cc/) · [Disp BBS terms](https://disp.cc/tos)

YouTube / Gemini:
[YouTube Terms](https://www.youtube.com/t/terms) ·
[YouTube API quota costs](https://developers.google.com/youtube/v3/determine_quota_cost) ·
[captions.download](https://developers.google.com/youtube/v3/docs/captions/download) ·
[Gemini video understanding](https://aistudio.google.com/docs/video-understanding)

Taiwan official:
[TWSE terms](https://www.twse.com.tw/zh/page/terms/use.html) ·
[TWSE OpenAPI swagger](https://openapi.twse.com.tw/v1/swagger.json) ·
[TPEx terms](https://www.tpex.org.tw/zh-tw/gtsm_disclaimer.html) ·
[data.gov.tw 11856](https://data.gov.tw/dataset/11856) ·
[data.gov.tw 6099](https://data.gov.tw/dataset/6099) ·
[data.gov.tw licence](https://data.gov.tw/license) ·
[CBC API](https://cpx.cbc.gov.tw/Data/ExportToAPIInfo) ·
[DGBAS open-data declaration](https://www.stat.gov.tw/cp.aspx?n=3164) ·
[FinMind licence and plans](https://finmind.github.io/llms.txt) ·
PDPA art. 19 (`laws.gov.taipei` interpretation index)

Research / macro:
[GDELT DOC 2.0](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) ·
[GDELT terms](https://www.gdeltproject.org/about.html#termsofuse) ·
[arXiv API terms](https://info.arxiv.org/help/api/tou.html) ·
[arXiv bulk data](https://info.arxiv.org/help/bulk_data.html) ·
[OpenAlex pricing](https://help.openalex.org/access/pricing/) ·
[DBnomics legal terms](https://docs.db.nomics.world/legal-terms/)

Tools: [Scrapling](https://github.com/D4Vinci/Scrapling) · [RSSHub](https://github.com/DIYgod/RSSHub) ·
[RD-Agent](https://github.com/microsoft/RD-Agent) · [Crawl4AI](https://github.com/unclecode/crawl4ai) ·
[Docling](https://github.com/docling-project/docling) · [MinerU](https://github.com/opendatalab/MinerU)

Hosting: [DigitalOcean pricing](https://www.digitalocean.com/pricing/droplets) ·
[DigitalOcean AUP](https://www.digitalocean.com/legal/acceptable-use-policy) ·
[Hetzner Cloud](https://www.hetzner.com/cloud/) · [Hetzner terms](https://www.hetzner.com/legal/cloud-server/)

Observation evidence (logged-out headless Chromium from the GCP dev host, 2026-11-10, five page
loads): Threads search 200 with recent posts; Threads profile 200; Instagram tag → `/popular/`
200 with 12 reel links; Instagram keyword search 429 → login; Instagram profile 429 → login.
Recorded in the evidence file of this task.
