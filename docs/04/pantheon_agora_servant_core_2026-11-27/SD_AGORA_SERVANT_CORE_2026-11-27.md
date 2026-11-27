# SD-AGORA-SERVANT-CORE-CAPABILITIES-20261127 — the Agora trading servant serves a human trader

- Status: design for review. Owner Claude, reviewer Antigravity (different model family).
- Base: `76786decd` (origin/dev, 2026-11-27). Line numbers are taken from that base.
- Scope: design only. This document changes no code. It claims no hosted behaviour beyond the read-only checks in §3, which are recorded in `docs/deployment/evidence/SD-AGORA-SERVANT-CORE-CAPABILITIES-20261127/evidence.json`.
- Every number marked *(proposal)* is a proposal by the owner of this SD for the reviewer and operator to change.
- Problem register: RC4 Agora row (`docs/operations/problem-register.md`).

## 0. 中文摘要（操作者用）

**為什麼有這份：** 操作者 11/27 定調：

- Agora 是為了服務真人交易員。
- 交易僕人要能幫交易員做這些事：
  - 找資料
  - 釐清問題
  - 整理交易習慣
  - 模擬交易員真實的交易行為
  - 幫他做交易
  - 整理出給真人看的報表或 dashboard，並隨交易策略改進而更新
- 不一定要開會。

**現況：**

- 僕人的對話只有一次 LLM 呼叫：
  - 不帶歷史、不帶資料，也沒有任何查資料的工具，所以回答只有方法、沒有數字。
  - 對話紀錄只存在 BFF 記憶體裡，重啟就消失。
- 習慣整理：完全沒有。
- 模仿管線（policy-learning）在 hosted 有在跑，但只吃「問答、筆記、日誌」等互動紀錄，不吃交易員的實際裁示、拒絕與干預；舊的 imitation worker 是空轉的 stub。
- 「送 Shadow」：寫進一個叫 `shadow_research` 的標籤，全 repo 沒有任何人讀。
- 「申請紙上測試」：寫進 `management_governance`，也沒有人讀；交易員的決定從來不會變成 paper 交易，也沒有交易員自己的 paper 帳本。
- 分點資料（FinMind 券商分點日報）：hosted 每天有進來（11/26 已是最新），但 Agora 與僕人都讀不到。
- 給交易員的定期報表：沒有。
- 版面改版提案：只有交易員手動觸發。

**這份訂的事（七項能力各自的設計見 §4–§10）：**

1. **找資料：** 僕人取得一組唯讀工具，查既有的治理資料來源，回答內附出處（來源、連接器、資料時間、新鮮度）。數字一律由工具算，LLM 不算數字。指令列與對話用同一條路。
2. **釐清問題：** 僕人只問會改變結論的問題，給 2–4 個選項並附自己的推定預設，沿用 V10 §4.1 的「下一個高價值決策」格式，工坊以外也適用。
3. **習慣：**
   - 由交易員的裁示、日誌、拒絕、干預，用固定規則算出「習慣事實」，交易員可以看、可以更正。
   - 僕人對這些事實的解讀存在僕人自己的私人記憶，不外流；只有會議紀錄能跨出去。
   - 僕人提案時會引用習慣。
4. **模仿：**
   - 不新增第二條迴圈：裁示、拒絕、干預、日誌變成既有 dataset 管線的新證據種類，送進既有 policy-learning。
   - 學出的行為模型在 shadow 跑同樣輸入，和交易員本人比較；比較附 bootstrap 運氣區間（依 11/16 決定）。
   - 結果給交易員看。
5. **幫他交易（paper）：**
   - 交易員確認的決定變成治理過的意圖，由既有的 paper 執行元件下單到「交易員自己的 paper 帳本」；Agora 本身不持有下單路徑。
   - 成交與部位回讀到 Agora。
   - canary 與 live 維持既有晉升關卡，而且需要操作者另外明確下指令。
   - `shadow_research` 改由既有的決策鎖與歸因擁有者接手，原標籤移除。
6. **報表與 dashboard：**
   - 僕人在三種情況主動提出版面改版：新策略版本、交易員反覆修改同一個元件、資料或盤勢改變。
   - 每週一份給交易員的報表，在 Agora 內看。
   - 每個新寫入者都寫明頻率、大小、成長、上限與清理方式。
7. **會議是選配：** 預設流程都不經過 persona 面板或會議；問 persona、開會都放在一個選配控制底下。

**實作單：** 見 §12。既有的 8 張重整任務照用、不重開；新增 12 張。

## 1. Operator statements and standing rules

| Date | Statement (verbatim or as recorded) | Where recorded |
|---|---|---|
| 2026-11-27 | 「可是他不一定要開會阿，而且agora是為了服務真人交易員的，因此他要能夠幫真人交易員去找資料，釐清問題，整理交易習慣，模擬真人交易員真實的交易行為，幫他做交易。並且要能夠整理出要給真人看得報表或是dashboard，隨著交易策略的改進」 | task row `SD-AGORA-SERVANT-CORE-CAPABILITIES-20261127` `next` |
| 2026-11-27 (standing) | Follow the original design; where documents conflict the later one wins: V6 < V10 < V11 < 8/27 SA. | task row `next`. No document in the repo states this order; `docs/bff/execution-tasks/2026-07-13-agora-ui-polish/parity-matrix.md:24-36` records V11 > V10 > V6 only. This SD records the full order as the operator's rule. |
| 2026-11-26 | Agora is for human traders; institutional personas are viewed in Management; the Agora committee lists only meetings the trader convened plus MT-4 reviews of the trader. | `docs/operations/problem-register.md:602-603` (WP6.4; AGORA-COMMITTEE-TRADER-MEETINGS-20261126, #7915) |
| 2026-11-16 | Agora shadow mode is a platform data source: servant insights, decision journal and shadow records become a platform source with full fields, including trader identity, account, broker ids and raw text. B2 de-identification is superseded on this path. Agora still never trades or moves capital. Attribution belongs to `services/evaluation`; each component carries sample size and a bootstrap luck check (1,000 resamples); no language model computes any number. | `docs/04/pantheon_persona_meetings_2026-11-15/SD_PERSONA_MEETINGS_2026-11-15.md` §12.2 (line 257), §12.3 (268), §12.4 (276), §12.5 (282) |
| 2026-11-13 (memory rule) | Facts are shared. Memory is each persona's interpretation and stays private. Interpretations cross only through meeting minutes (`scope:committee`). | `docs/04/pantheon_persona_cycle_reconnect_2026-11-12/SD_MEMORY_HINDSIGHT_ADOPTION_2026-11-13.md:60-67, 154`; restated in SD_PERSONA_MEETINGS §12.2 |
| standing | Agora has no execution authority of its own. | servant metadata `execution_authority: "none"` (`services/control-plane/bff/agora/servant/router.py:265-268`); SA 8/27 line 35 "Agora remains advisory and non-executing" |

**"Agora has no execution authority" versus "trade for the trader".** The two statements are reconciled the same way the 11/16 decision reconciled shadow mode:

- Agora records the trader's confirmed decision as a governed intent.
- The existing execution owners execute it in a paper book bound to that trader.
- Agora reads the result back.
- Agora holds no order route, broker credential or capital authority.

## 2. Original planning this design follows

| Ref | Path:line | What it says that binds this design |
|---|---|---|
| Blueprint §5.1 | `Pantheon_總索引版系統分析文件.md:325` | Agora and Ask Personas share one Console Plane. The trader asks for analysis, proposal fixes, rationale, journal and signal labels, and these interactions become evidence for Observe and Learn. |
| Blueprint §6.5 | same file, `:415` | The trader may not know they are training AI. Interaction becomes correction trace, preference pair or trader trajectory, then persona memory, strategy lesson or imitation dataset. That evidence cannot promote artifacts, change live LEAN or bypass approval. |
| Blueprint §6.7 | same file, `:437` | Agora traces and trader trajectories become an imitation dataset, then a behaviour policy candidate, then shadow or paper evaluation. The shadow persona runs its own OODA in the same market context and is compared with the human decision. Reaching runtime needs experiment, approval, DeploymentPlan and RuntimeBinding. |
| Blueprint §9.4.3 | same file, `:743` | The trader submits signal feedback, notes, journal, insights, training examples and research tasks. These go to Observe and Learn, the dataset builder, persona lessons or imitation candidates. Nothing is promoted and no approval is bypassed. |
| A4 | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/A4_shadow_human_actual_sourcing.md:5, 9, 36, 65, 185` | Agora places no live order. `human_actual` is the arm the trader chose, and a paper proxy is never presented as a real fill. `execution_observation_kind` takes `verified_broker_fill`, `imported_broker_fill`, `pantheon_paper_proxy`, `manual_declared_outcome` or `declared_no_trade`. Each ruling creates an immutable Human Decision Lock. The shadow arms (`base_strategy`, `private_servant`, `human_actual`, `committee_variant`, `optional_alt_versions`) share one `decision_time`. |
| V6 §2.3 | `docs/design/agora-trading-desk-design/uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V6_MultiStrategy_Dashboard_2026-06-18.md:134` | The ten command-bar quick tasks are: 建立策略視角, 產生候選池, 分析分點, 找落後補漲, 比較多檔股票, 檢查進場條件, 檢查出場條件, 重排目前畫面, 新增圖表, 送影子追蹤. Most of these are data tasks. |
| V6 §13 | same file, `:708` | Personalisation is keyed by `userId + strategyLensId + workspace + phase`. The §13.2 Personalization Status reads 「僕人已學到：您偏好先看籌碼再看價格 / 您常剔除低流動性候選…」. §13.3 is the dashboard change log. |
| V6 §15 | same file, `:893`, `:960` | Performance must answer which decisions the trader intervened in and whether that improved the result. §15.5 adds intervention tracking: what the rule would do, what the trader did, the reason, the result, the shadow outcome if not intervened, whether it improved, and 「交易僕人學到什麼」. |
| V10 §0.1, §4, §4.1 | `…V10_Expert_Strategy_Dialogue_2026-06-18.md:41, 181, 210` | Do not treat the trader as a beginner. Ask only high-information questions that change the result. Propose a provisional design when it can reasonably be inferred. Twelve blocks. The next high-value decision. |
| V11 §10, §11 | `…V11_WinnerBranch_TradingRoom_2026-06-19.md:843, 895` | Versions are kept per trader, strategy and strategy version (`TradingRoomDashboardVersion{generatedBy, previousVersionId, changeSummary, views}`). The change log records who proposed, why, whether the effect was evaluated, and whether it can be rolled back. Decision card actions are 確認執行, 修改部位, 延後, 送 Shadow, 拒絕 and 要求補充研究. No back-office vocabulary is shown. §8.3: the servant proposes a widget revision and never edits directly. |
| SA 8/27 | `docs/04/pantheon_agora_hosted_gap_sa_sd_2026-08-27/SA_AGORA_PRODUCT_CLOSURE_2026-08-27.md:35, 107, 124, 135, 150, 427` | Agora is advisory and non-executing. AD-AG-05: HTTP 202 means durably queued, and provider work never runs in the request. AD-AG-06: stream transport. AD-AG-07: freshness states, and stale data is never shown as a zero market. AD-AG-08: Agora Performance is owner-scoped and separate from Management attribution. §10 non-goals: live broker and capital enablement. |

The planning is consistent. The capabilities the operator asked for on 11/27 are already in the blueprint (§6.5, §6.7, §9.4.3), V6 (§2.3, §13.2, §15.5) and A4. What is missing is the wiring, not the intent.

## 3. Read-only checks on hosted (2026-11-27)

The method and raw values are in the evidence file. **No authenticated BFF read was made in this pass:** the worker has no hosted operator credential, and the build host (`pantheon-dev`) serves no BFF. The checks use GitHub Actions run logs for this repository and hosted evidence that other tasks have already recorded.

| Check | Result | Source |
|---|---|---|
| FinMind broker-branch daily report (`tw-finmind-broker-daily-report`, dataset `tw_broker_top`) | **Live.** Schedule run 45900668771 (2026-11-26 21:48Z): step "Execute bounded FinMind broker branch refresh" succeeded. Preflight returned `already_fresh` for session 2026-11-26, `source_id` `finmind-broker-top:0050:2026-11-26:sell:f22adfe6e427325e`. | GH Actions job 140185364113 log |
| Same refresh, latest run | Run 45917069646 (2026-11-27 00:30Z) failed overall. The broker step succeeded; the chip profiles failed (TWSE/TPEx margin, TPEx institutional flow, TAIFEX positions, put/call, TX daily: exit 1). | GH Actions; residual for the market-data owner |
| Broker-branch data reaching Agora | **Not read by any Agora surface.** `scripts/project_market_data_to_bff_agora_surfaces.py:27` projects only `tw-twse-tpex-official-market`. No adapter in `services/control-plane/bff/agora/trading_data/` reads `tw_broker_top`. | code |
| `tw-finmind-broker-bulk-parquet` | One-time historical backfill (`services/source_ingestion/active_universe.py:468`). Hosted state not verified in this pass. | code |
| `tw-yahoo-broker-top15` | `DISABLED_BY_BUILD` (`connector_definitions.py:669`) | code |
| `tw-mops-official-disclosures` | Scheduled by `source-ingest-scheduler` as `event_poll_10m_to_30m` (`active_universe.py:244-275`). Hosted freshness not verified in this pass. | code |
| News RSS (`collector-news-rss`) | Hourly on the collector host, finance routes since 2026-11-25. | `docs/deployment/evidence/COLLECTOR-NEWS-FINANCE-ROUTES-20261125/evidence.json` |
| Services running on hosted Dev | `policy-learning-svc`, `policy-learning-shadow-eval-scheduler`, `evaluation`, `broker`, `runtime-manager`, `capital`, `paper-signal-producer`, `paper-fleet-reconciler`, `agora-interaction-worker`, `source-ingest`, `source-ingest-scheduler`. **No `imitation-worker`.** | `docs/deployment/evidence/DEV-COMPOSE-MEMORY-LIMITS-PEAK-SIZED-20261117/evidence.json` (release `80d4cf2ca`, cgroup peaks) |
| Trader decisions on hosted | `GET /bff/agora/trading-room/decision-events` returned `items=[]` for the Dev operator on 2026-11-26, so no decision lock exists. | `docs/deployment/evidence/AGORA-SHADOW-ATTRIBUTION-HOSTED-ACCEPTANCE-20261116/evidence.json` (AC1) |
| Servant answer in the 11/27 walkthrough | The answer gave a method but no data. | coordinator `70b5f70f` facts in the task row |

What to enable is listed per source in §4.3.

## 4. Capability 1 — find data for the trader

### 4.1 Exists and wired

- **Servant routes.** `services/control-plane/bff/agora/servant/router.py`:
  - GET `/bff/agora/servant` (:793)
  - POST `/ensure` (:823)
  - POST `/sessions` (:1011), GET `/sessions/{id}` (:1111)
  - POST `/sessions/{id}/messages` (:1169)
  - GET `/stream` (:1389)
- **Message turn.** One call to `invoke_assistant_provider(provider="openclaw", mode="user")` at :1237-1265. The `context_pack` carries only session and audit ids.
- **Capabilities.** The only interaction capability is `persona_opinion` (:265). `execution_authority` is `none`.
- **Read surfaces next to the servant that the conversation does not use:**
  - `InstitutionalKnowledgeReader`, `servant/institutional_knowledge.py:185` (attribution, research and memory reads, :253-327). It is reachable only through GET `/servant/institutional-knowledge` (:1488) and the system-suggestion routes (:1510, :1532).
  - POST `/bff/agora/trading-data/query` and `/allowlist`, `agora/trading_data/router.py:25, 62`. It has seven allowlisted widget adapters (`trading_data/service.py:36-42`): strategy performance, account positions, risk metrics, signal decision queue, candidate funnel, candidate ranking and evidence trace.
  - The Agora platform source, `agora/platform_source_router.py:1050, 1106`.
  - The owner bounded read GET `/api/source-ingest/source-records?connector_id=&page_size=&page_token=`, `services/source_ingestion/routers/ingest_operations.py:554-596`. No BFF route proxies it.
- **Research.** `servant/research_proposal.py:154` drafts a research plan through a structured call. It is reachable only from the workshop route `research/routes/plans.py:98` and is stored as a draft.

### 4.2 Missing

1. The servant has no tool and no data in its turn.
2. Conversation history is kept only in the process dict `_SERVANT_SESSION_EVENTS` (`router.py:51, 122`). The Agora owner already has `append_session_message` (`services/agora/service.py:149`, `store.py:315`), but the servant does not use it.
3. No BFF read exists for broker-branch rows, MOPS disclosures or news.

### 4.3 Design

- **Tool set.** The servant gets a fixed, read-only tool set. A tool call is resolved by the BFF; the model never reaches an owner directly. Each tool returns **cited facts**: `{value, unit, source_id, connector_id or owner, as_of, freshness (AD-AG-07 state), query_hash}`. The language model writes prose around facts and never computes a number (11/16 rule).

  | Tool | Backed by | Live on hosted today | To enable |
  |---|---|---|---|
  | `market_bars`, `candidate_pool`, `candidate_ranking` | existing trading-data adapters | candidate pool depends on the walkthrough strategy reaching the trading room | none beyond the adapters |
  | `broker_branch_flow(symbol, date range, side)` | owner bounded `source-records?connector_id=tw-finmind-broker-daily-report` through a new BFF proxy (bounded: page_size ≤ 200, 10 pages *(proposal)*) | rows are ingested (§3), no reader | the BFF proxy and adapter (T2) |
  | `disclosures(symbol, since)` | `tw-mops-official-disclosures` through the same proxy | not verified | verify freshness in T2 hosted proof |
  | `news(symbol or keyword, since)` | collector news records in source-ingest | hourly | the proxy |
  | `my_decisions`, `my_journal`, `my_shadow` | Agora owner reads (decision locks, journal) and the Agora shadow owner route (AGORA-SHADOW-COMPARISON-OWNER-ROUTE-20261127) | no decisions yet on hosted | none |
  | `institutional_facts` | `InstitutionalKnowledgeReader` | live (AGORA-SHADOW-ATTRIBUTION evidence) | none |
  | `propose_research(question)` | existing `draft_research_plan` | wired for the workshop only | callable from the conversation; still a draft that the trader approves |

- **Turn shape.**
  1. POST `/sessions/{id}/messages` returns 202 after durably queuing the turn (AD-AG-05).
  2. `agora-interaction-worker` runs the turn: a structured provider call that may request tools, at most 4 tool rounds *(proposal)*, then the final answer.
  3. The answer streams over `/stream`.
- **History.** Messages persist through the Agora owner. The turn carries the last 20 messages *(proposal)* plus the habit profile (§6).
- **Command bar.** The command bar (FE-AGORA-SHELL-ORIGINAL-IA-20261127) sends to the same route with page, strategy and widget context, so V6 §2.3 「分析分點」 becomes `broker_branch_flow` on the selected symbols.
- **Unanswerable requests.** When no tool covers a request, the servant says which data is missing. It never answers from model memory as if it were data.

## 5. Capability 2 — clarify the trader's question

- **Exists.** V10 §4.1 high-value questions exist only inside the workshop, and only as a reconstruction rail. AGORA-WORKSHOP-TWELVE-BLOCK-COMPLETENESS-20261127 adds the next high-value decision with 2-4 options and POST `/bff/agora/workshops/{id}/decisions`.
- **Missing.** A conversation or command-bar request outside the workshop gets one free-text answer. There is no typed clarification.
- **Design.**
  - The servant turn may return a `clarification` card instead of an answer. The card uses the same shape as the workshop decision: a question, 2-4 options, the servant's provisional default and why it matters.
  - The servant asks only when the answer changes the data selected or the result (V10 §0.1). Otherwise it states its assumption and answers.
  - At most one clarification per turn, and two in a row before it must answer with stated assumptions *(proposal)*.
  - The trader's choice is a session event. Choices about style, such as horizon, universe or risk tolerance, are habit facts (§6).

## 6. Capability 3 — organise the trader's habits

- **Exists.**
  - The servant declares `memory_scope: private_user` (`servant/router.py:262`), but nothing reads or writes servant memory.
  - `PersonaMemoryType.PREFERENCE` exists (`services/memory/persona_memory_store.py:45-51`).
  - Free-text `personalizationHints` on workspace proposals (`trading_room/service.py:878`).
  - The Agora platform source carries locks and journal with full fields (11/16 S2).
- **Missing.** There is no habit model, derivation, store, route or UI. V6 §13.2 Personalization Status has no backing data.
- **Design.**
  - **Facts (shared within the tenant per 11/16).** A deterministic derivation runs per trader and strategy over decision locks, journal entries, rejections and interventions. It produces a habit profile owned by the Agora owner (`services/agora`), with these features *(proposal)*:
    - holding period distribution
    - entry timing relative to signal
    - add, reduce and exit discipline versus the rule
    - position size distribution
    - which candidates are rejected, and the features they share (liquidity, sector, price level)
    - data order (from widget interaction and clarification choices)
    - intervention rate and direction

    Each feature carries its sample size and is shown only from 10 decisions *(proposal)*. No language model computes a feature.
  - **Interpretations (private).** The servant's reading of the profile is written to PersonaMemory under the servant's own `persona_id` with type `preference`, and never tagged `scope:committee`. For example: 「您偏好先看籌碼再看價格」. It crosses to anyone else only through a meeting the trader convenes (memory rule; A2).
  - **Read and correct.** GET `/bff/agora/servant/habits` shows facts and the servant's interpretations to their trader only. The trader can mark an interpretation wrong or add their own statement through PATCH. The correction is stored as `training_example` evidence (§7), and the interpretation is superseded, not deleted.
  - **Use.** Every servant turn and every workspace or decision proposal carries the profile. Proposal text names the habit it relied on (V6 §13.2).
- **Writer budget.**
  - Recompute when a decision lock or journal entry is written (event-driven; no schedule).
  - One row per trader and strategy, about 4 KB.
  - Keep the latest row and 12 superseded versions *(proposal)*; older versions are deleted on write.

## 7. Capability 4 — imitate the trader's real trading behaviour

### 7.1 Exists and wired

- The policy-learning loop (L12-IMIT-001) runs on hosted:
  - `services/policy-learning/scheduler_worker.py` runs hourly (`docker-compose.yml:935-985`).
  - `agora_handoff_drainer.py:166, 193, 230` claims Agora dataset handoffs, posts them to `/api/policy-learning/agora-handoff` and acks them.
  - `/api/policy-learning/worker/process` performs shadow evaluation.
  - Training is fail-closed behind separate approval (`scheduler_worker.py:20-21`).
- Dataset handoffs come from `services/control-plane/bff/agora/dataset_extraction/`. Its interaction kinds are only `ask`, `feedback`, `journal`, `note`, `insight` and `training_example` (`models.py:18-39`).
- Translation to the imitation contracts is done by `services/policy-learning/agora_dataset_authority.py` and `services/research/imitation/agora_dataset_source.py`.
- Settlement and luck:
  - `services/evaluation/attribution/settlement.py` settles the four arms (`:1-36`, `settle_decision_lock` :475).
  - `decomposition.py:167-236` provides `bootstrap_luck_interval`.
- `services/learning/imitation/worker.py:9-20` sleeps forever unless `PANTHEON_IMITATION_MODE=smoke`. No compose service runs it, so it is a stub.

### 7.2 Missing

- Decisions, rejections, deferrals and interventions are not evidence kinds, so the loop never sees what the trader actually did.
- No per-trader behaviour candidate exists.
- No imitation arm exists in the shadow comparison.
- The trader sees no result.

### 7.3 Design

- **No second loop.**
  - The extractor gains the evidence kinds `decision`, `rejection`, `deferral` and `intervention`, built from decision locks with their decision-time snapshot and from journal entries. They route to `learn` and reach policy-learning through the existing drainer.
  - The idle `services/learning/imitation/` worker image is removed from the build, or kept only as the documented smoke harness; nothing new is built on it.
- **Candidate.** Policy-learning trains one behaviour candidate per trader and strategy from the IMT-003 dataset (behaviour cloning). It runs under the existing training approval gate. The minimum is 30 decisions *(proposal)*.
- **Shadow.** The candidate runs in shadow evaluation on the same decision-time inputs as each new decision lock. Its output is recorded as an A4 `optional_alt_versions` arm, named `trader_imitation`, on that lock. Evaluation settles it with the same cutoff and cost model as the other arms.
- **Comparison.** Evaluation computes the agreement rate and the outcome gap between `trader_imitation` and `human_actual`. Each carries sample size and a bootstrap interval (1,000 resamples, 11/16 §12.5). A gap whose interval includes zero is labelled 「可能是運氣」 and never stated as a finding.
- **Shown to the trader.** Strategy Performance gains a 「僕人模仿您」 section: agreement rate, where the model and the trader differ, and outcomes with intervals. It is read through an Agora owner-scoped route (AD-AG-08).
- **Runtime.**
  - Behaviour runs as part of the hourly scheduler that already exists; no new schedule.
  - Keep 3 candidate versions per trader and strategy *(proposal)*.
  - One arm row per decision lock.

## 8. Capability 5 — trade for the trader in paper

### 8.1 Exists and wired

- **Intent handoff.** `submit_intent_handoff` (`trading_room/service.py:676-760`; route POST `/bff/agora/trading-intents/{id}/handoffs`, `routes/intents.py:59`) writes a handoff with `no_order_route_proof="agora_request_only_no_order_route"`. The target is derived at `service.py:1634-1640`:

  | Stage | Handoff type | Target | Reader |
  |---|---|---|---|
  | shadow | `shadow_start` | `shadow_research` | **none** (repo-wide grep: only `service.py:1636`, `routes/common.py:335`, the schema and tests) |
  | paper | `paper_validation_request` | `management_governance` | **none** (the governance router reads approval-owner decisions only, `bff/governance/router.py:947-974`) |
  | canary, live | `promotion_review_request` | `promotion_review` | none from Agora |

  These targets are string fields on records inside one JSONB row (`trading_room/store.py:438-462`). There is no queue behind them.
- **Decision lock.** `services/agora/shadow_mode.py:153-229` and `services/agora/service.py:508-550`. The BFF writes the lock before recording a decision (`trading_room/service.py:540-580`).
- **Paper execution path.**
  - runtime-manager: `/api/runtime-bindings`; `promotion_authority.py` fails closed for paper→canary→live.
  - `paper-signal-producer` (`services/execution/lean_runtime/paper_signal_producer.py`) enqueues signals onto binding-scoped Redis queues; paper only, `submitted_to_broker=false`.
  - `paper-fleet-reconciler` runs one worker per paper binding.
  - The broker paper simulator: `services/broker/main.py:95-157`.
  - capital pools and persona bindings: `services/capital/main.py:1535-1903`.
  - persona paper-capital provisioning: `bff/personas/routes/provisioning.py:222-267`.

### 8.2 Missing

- No path takes a confirmed decision to a paper order.
- No paper book belongs to a trader.
- Agora has no fills or positions read.

### 8.3 Design

1. **The trader's paper book.**
   - The trader's servant is already a persona with `environment_ceiling: paper` (`servant/router.py:258-266`).
   - On first use, the existing persona paper-capital provisioning gives that servant persona a paper capital pool and one paper RuntimeBinding through runtime-manager.
   - That binding is the trader's paper book.
   - Starting capital is an operator setting *(proposal: TWD 10,000,000 notional, paper only)*.
2. **Confirmed decision.** 確認執行 or 修改部位 on the decision card writes the decision lock (as today) and a governed intent with stage `paper`, carrying symbol, side, size and limit, and the lock id.
3. **Handoff target.**
   - The `paper` stage handoff gets a real target, `paper_execution`, and a real reader.
   - A new consumer in the paper execution lane validates each submitted handoff, then enqueues one paper signal onto the trader's binding queue through the existing `DecisionSignalProducer` validation.
   - Validation checks: identity, binding owner equals the intent owner, risk limits through the existing paper risk gate, and idempotency on the intent id.
   - The consumer runs inside `paper-signal-producer`'s cycle, so no new service is added.
   - The consumer writes the handoff state back to the Agora owner: `accepted`, `rejected` with a typed reason, `filled` or `partially_filled`.
   - Agora itself still holds no order route.
   - `paper_validation_request` remains only for strategy-version validation, which is the meaning it had, and gets its own reader or is removed (T8).
4. **Read back.**
   - An Agora owner-scoped route returns the trader's paper fills, positions and the intent's progress, reading the broker paper orders and the binding's telemetry.
   - The decision lock's `human_actual` arm takes `execution_observation_kind=pantheon_paper_proxy` with the fill (A4 §1). It is never labelled as a real fill.
5. **Canary and live.**
   - `canary` and `live` handoffs keep `promotion_review` and the runtime-manager promotion authority.
   - Agora shows them as 「需要另行核准」 and never moves past them.
   - Enabling either for a trader needs a separate explicit operator instruction. It is not part of this design.
6. **Shadow.**
   - 送 Shadow means 「不下單、但追蹤」.
   - It writes a decision lock with `human_actual.execution_observation_kind=declared_no_trade`, so evaluation settles all arms on it like any other lock.
   - The `shadow` stage handoff is no longer created, and `shadow_research` is removed from the stage table, request model and schema enum.
   - The decision-lock and attribution owner is the consumer that already exists.

- **Writer budget.**
  - One handoff row per confirmed decision (existing store).
  - One paper signal and order per handoff.
  - Fills follow the broker paper store's existing retention.
  - Cap: 50 paper intents per trader per day *(proposal)*; above the cap the consumer rejects with `daily_cap`.

## 9. Capability 6 — reports and dashboards that change with the strategy

### 9.1 Exists

- Workspace proposals: `trading_room/routes/workspaces.py:70, 134`, generated by the deterministic `generate_trading_room_workspace_proposal`, `integrations/openclaw/skills/agora/trading_room_workspace/skill.py:87`.
- Widget revision proposals: `:411, 451`.
- Versions and rollback: `:495, 517`.
- Direct servant widget edits are blocked (`service.py:1281-1287`, V11 §8.3).
- All of these are triggered by the trader only.
- Agora has no report route. The `/dashboard-recipes` routes exist only in a test (`services/control-plane/tests/agora/test_cross_user_isolation.py:281-302`).

### 9.2 Design: when the servant proposes a workspace revision

| Trigger | Detected by | Proposal |
|---|---|---|
| A new strategy version is selected (`strategy_workshop/routes/versions.py:502`) | the select route emits an event | a new workspace proposal for that version through `create_workspace_proposal`, with `changeSummary` naming what changed in the strategy |
| Repeated trader edits: the same widget is edited 3 times in 7 days *(proposal)* | count of layout and widget versions per widget | a widget revision proposal that makes the edit the default |
| Data or regime change: a source used by a widget turns `stale`, `unavailable` or newly `fresh`, or a governed RegimeState changes | freshness readback and regime owner event | a widget revision proposal (replace, annotate or add) |

- At most one pending proposal per workspace *(proposal)*. A newer trigger replaces the pending one.
- Accepted or rejected proposals follow the V11 §10.3 change log: proposer, reason, whether evaluated, and rollback.

### 9.3 Design: the periodic trader report

- **Content.**
  - The strategy version in use and changes since the last report.
  - Decisions made, followed, modified, rejected and sent to shadow.
  - Paper book P&L and positions; never fabricated (SA §4.5), and missing values shown as missing.
  - Rule versus human for each intervention (V6 §15.5).
  - The shadow comparison per arm with luck intervals.
  - The imitation agreement (§7).
  - Habit changes (§6).
  - Data sources that were stale.
  - The servant's suggested next steps; each is a proposal the trader can accept.
- **Cadence.**
  - Weekly, after the weekly attribution run, so the arms are settled.
  - Also on demand from the command bar (「整理本週報表」).
  - Generated by `agora-interaction-worker`.
  - All numbers come from owners; the language model writes only the narrative.
- **Delivery inside Agora.**
  - A report card in the servant drawer (FE-AGORA-GLOBAL-DRAWERS-20261127).
  - A 「報表」 section on Strategy Performance with a history list.
  - No email, no external channel.
- **Writer budget.**
  - One report per trader per strategy per week, plus on-demand reports capped at 3 per day *(proposal)*.
  - About 20 KB each.
  - Keep 26 weekly reports per trader and strategy *(proposal)*. Delete older ones on write; on-demand reports are deleted after 7 days.

## 10. Capability 7 — meetings and persona consults are optional

- **Exists.** No default trader flow goes through a persona panel:
  - Servant messages go straight to the provider.
  - Consultations and meetings start only on trader action (`agora/interaction/router.py:849-855, 1557-1574`; `strategy_workshop/routes/execution.py:298`).
  - The committee lists only trader-convened meetings and MT-4 reviews of the trader (11/26).
- **Design.**
  - **Optional control.** A single 「問 persona／開會」 control is the only entry to persona consults (Ask, Challenge, Compare, Propose, Reflect) and meetings, in the workshop (FE-AGORA-WORKSHOP-CONVERSATION-FIRST-20261127) and in the drawer (FE-AGORA-GLOBAL-DRAWERS-20261127).
  - **No persona in the default turn.** The servant's own turn (§4.3) never calls `persona_opinion`. `persona_opinion` stays a capability used only when the trader asks.
  - **Unaffected flows.** Data, clarification, habits, imitation, paper trading and reports (§4–§9) need no persona and no meeting.
  - **Test.** An acceptance test proves that a full flow (ask with data, confirm a decision, paper fill readback, weekly report) makes no `persona_opinion` and no meeting call.

## 11. Rules every implementation task inherits

- Agora holds no order route, broker credential or capital authority. Execution is done by existing owners.
- No language model computes a number. Every number shown carries its source and `as_of`.
- Facts are shared within the tenant with full fields (11/16). Servant interpretations stay private and cross only through meeting minutes.
- Owner-scoped reads for traders (AD-AG-08). No trader page calls `/bff/management`.
- Provider work runs out of the request (AD-AG-05).
- Every new writer states cadence, size, growth, cap and cleanup in its acceptance. The values in §6–§9 are the starting proposals.
- The reviewer comes from a different model family than the owner whenever another family has capacity.

## 12. Implementation tasks (ordered)

The RC4 Agora row lists eight existing realignment tasks. They are reused as they are; their scope is not repeated here:

- **R1** AGORA-WORKSHOP-RECONSTRUCTION-HOSTED-20261127
- **R2** AGORA-WORKSHOP-TWELVE-BLOCK-COMPLETENESS-20261127
- **R3** AGORA-SHADOW-COMPARISON-OWNER-ROUTE-20261127
- **R4** FE-AGORA-SHELL-ORIGINAL-IA-20261127 (command bar)
- **R5** FE-AGORA-TRADING-ROOM-PERFORMANCE-ALIGN-20261127 (rule versus human, paper progress display)
- **R6** FE-AGORA-WORKSHOP-CONVERSATION-FIRST-20261127 (personas optional in the workshop)
- **R7** FE-MGMT-PERSONA-TOOLS-FROM-AGORA-20261127
- **R8** FE-AGORA-GLOBAL-DRAWERS-20261127 (servant drawer, shadow, journal, expert review)

New tasks, in order:

| # | Title (proposed task id) | Repo | Artifacts | Acceptance outline | Depends on |
|---|---|---|---|---|---|
| T1 | Servant conversation is durable and queued (AGORA-SERVANT-SESSION-DURABLE-20261127) | pantheon | `services/control-plane/bff/agora/servant/router.py`, `services/agora/service.py`, `services/agora/store.py`, `scripts/run_agora_interaction_worker.py` (compose `agora-interaction-worker`), tests | Messages persist through the Agora owner `append_session_message`, and the process dict is removed. POST returns 202 queued (AD-AG-05) and the worker runs the turn. The turn carries the last 20 messages. Restarting the BFF keeps history. Budget: about 2 KB per message, 500 messages per session cap, sessions kept 180 days. | none |
| T2 | Servant read-only data tools with cited facts (AGORA-SERVANT-DATA-TOOLS-20261127) | pantheon | servant tools module, BFF bounded source-records proxy, `trading_data/adapters.py`, openclaw structured schema, openapi, bundle index, capability manifest, route snapshot, tests | §4.3 tools. Each fact carries source, connector, `as_of` and freshness. At most 4 tool rounds. No number is produced by the model (test). Hosted proof: 「分析分點」 on a held symbol returns broker-branch rows for the latest session with citations, and MOPS and news freshness are recorded. A flow with no persona call (§10 test). | T1 |
| T3 | Servant clarification card outside the workshop (AGORA-SERVANT-CLARIFY-20261127) | pantheon | servant turn schema, cards, tests | Typed `clarification` with 2-4 options and a default, same shape as R2. Asks only when the answer changes the data or result, and at most two in a row. The choice is a session event. | T2, R2 |
| T4 | Decision, rejection, deferral and intervention as dataset evidence (AGORA-DECISION-EVIDENCE-KINDS-20261127) | pantheon | `bff/agora/dataset_extraction/models.py`, `extractor.py`, `services/agora` lock emit, `services/policy-learning/agora_dataset_authority.py`, `services/research/imitation/agora_dataset_source.py`, tests | Four new kinds routed to `learn` with the decision-time snapshot. The drainer delivers them (test through the drainer). One evidence item per lock or journal entry, following the existing dataset retention. | none |
| T5 | Trader habit profile, private interpretations, read and correct (AGORA-TRADER-HABIT-PROFILE-20261127) | pantheon | `services/agora` habit derivation and store, BFF `/bff/agora/servant/habits`, PersonaMemory write for the servant persona, openapi and manifests, tests | §6 features computed deterministically, each with its sample size. Interpretations are private (a test shows another persona and a meeting cannot read them unless they are carried in minutes). PATCH correction is stored as training-example evidence. The servant turn and proposals cite habits. Budget as in §6. | T4, T1 |
| T6 | 送 Shadow becomes a decision lock; the `shadow_research` target is removed (AGORA-SHADOW-HANDOFF-TO-DECISION-LOCK-20261127) | pantheon | `trading_room/service.py`, `routes/common.py`, `specs/agora/v4/governed_intent_handoff.schema.json`, openapi and manifests, tests | Shadow writes a lock with `declared_no_trade`, and evaluation settles it. No `shadow_research` remains outside archives. Hosted proof: one shadowed decision has settled arms on the R3 route. | R3 |
| T7 | Trader paper book provisioning (PAPER-TRADER-BOOK-PROVISION-20261127) | pantheon | `bff/personas/routes/provisioning.py`, capital and runtime-manager clients, servant ensure path, tests | On first paper use, the servant persona gets one paper capital pool and one paper RuntimeBinding (idempotent). No canary or live binding can be created from this path (test). The operator setting is the starting capital. | PAPER-RUNTIME-MANAGER-DYNAMIC-BEARER-20261125 |
| T8 | Confirmed decision executes in the trader's paper book (AGORA-PAPER-INTENT-EXECUTION-20261127) | pantheon | `trading_room/service.py` stage table, `services/execution/lean_runtime/paper_signal_producer.py` consumer, handoff state writeback, schema, tests | `paper` handoffs target `paper_execution` and are consumed in the producer cycle, then enqueued through `DecisionSignalProducer` with owner, risk and idempotency checks. State is written back. `paper_validation_request` gets a reader or is removed. The daily cap is 50. Agora keeps no order route (test on `no_order_route_proof`). | T7 |
| T9 | Paper fills and positions read back in Agora (AGORA-PAPER-BOOK-READBACK-20261127) | pantheon | Agora owner-scoped route, broker paper and telemetry clients, lock `human_actual` paper proxy, openapi and manifests, tests | The trader reads only their own book (404 for others). Fills are labelled `pantheon_paper_proxy`. Hosted proof: one confirmed decision is filled and read back. | T8 |
| T10 | Trader imitation in shadow, compared with the trader (IMITATION-TRADER-SHADOW-COMPARISON-20261127) | pantheon | `services/policy-learning/`, `services/evaluation/attribution/`, Agora owner-scoped read, removal or documentation of `services/learning/imitation/` and its cloudbuild entry, tests | One behaviour candidate per trader and strategy from T4 data, after the existing training approval, with a minimum of 30 decisions. The `trader_imitation` arm sits on each new lock. Agreement and gap carry 1,000-resample bootstrap intervals, and an interval containing 0 is labelled luck. It runs in the existing hourly scheduler. Keep 3 candidates. | T4, T6 |
| T11 | Servant-initiated workspace revisions and the weekly trader report (AGORA-SERVANT-REVISIONS-AND-REPORT-20261127) | pantheon | `trading_room/service.py` triggers, `strategy_workshop/routes/versions.py` event, report generator in `agora-interaction-worker`, report store and routes, openapi and manifests, tests | The §9.2 triggers each produce one proposal, with at most one pending per workspace. The §9.3 report is weekly after attribution and on demand at most 3 per day; numbers come from owners only. Budget as in §9.3. | T2, T5, T9, T10 |
| T12 | Agora UI for data citations, clarification, habits, paper book, imitation and reports (FE-AGORA-SERVANT-CORE-20261127) | execute-plans | `src/agora/drawers/`, `src/agora/pages/strategy-performance/`, `src/lib/bff-v1/agora/`, locales, e2e | zh-TW. Citations are shown under each answer. The clarification options are clickable. A habits page shows read and correct. Paper fills and positions are shown with no order action. 「僕人模仿您」 is shown with intervals. A report history is shown. The persona control is optional (§10). Hosted browser proof. | R4, R5, R8, T2-T11 |

**Order.**

- T1 and T4 start in parallel with R1-R8.
- T2 → T3.
- T4 → T5.
- R3 → T6.
- T7 → T8 → T9.
- T4 + T6 → T10.
- Then T11, then T12.

Each task names the RC4 Agora row of `docs/operations/problem-register.md`.

## 13. Residual findings (outside this task's scope)

1. The Dev Taiwan Market Daily Refresh run 45917069646 (2026-11-27 00:30Z) failed on six chip profiles with exit 1. Owner: market-data.
2. `services/learning/imitation/worker.py` is built in `cloudbuild.yaml:50, 105` but never run. T10 decides between removing it and documenting it.
3. Attribution weekly runs are requested only from the persona meeting triggers (`services/persona/meeting_triggers.py:113, 380`). The weekly trader report (T11) depends on that request happening even when no persona cycle runs; T11 must verify this or add the request to the existing trigger.
4. A second attribution module exists at `services/control-plane/bff/agora/performance/attribution.py`, next to the owner `services/evaluation/attribution/`. Its role should be checked under AD-AG-08 when R5 or T10 touches it.
