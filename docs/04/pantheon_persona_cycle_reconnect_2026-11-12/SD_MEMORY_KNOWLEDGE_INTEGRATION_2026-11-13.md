# SD-MEMORY-KNOWLEDGE-INTEGRATION-20261113 — put memory and the knowledge base inside meetings, research, evolution and the persona cycle, on the open-source retrieval stack already chosen

| Field | Value |
| --- | --- |
| Status | Design for implementation; the operator asked on 2026-11-13 to write it and open the tasks |
| Date | 2026-11-13 |
| Repo base | `origin/dev` `bc8807e04` (2026-11-13T07:18Z); every repository reference below was read on this base |
| Extends | `docs/04/pantheon_persona_cycle_reconnect_2026-11-12/SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` (C1 cycle, C3 memory writers and readers, C4 panel, C8 harness) and `docs/04/pantheon_strategy_discovery_sources_2026-11-10/SD_PERSONA_STRATEGY_POOL_AUTONOMY_2026-11-12.md`; nothing there is replaced |
| Restores | blueprint axiom 9 「回饋不只回模型，也回 persona 與知識庫」 and 「知識共享」; the retrieval contexts and the lesson-to-gate example of `services/memory/MEMORY_LAYER_DESIGN_NOTE.md`; the governed search / RAG gateway of `docs/03/SD-03_source_knowledge_evidence.md`; the accepted retrieval backend contract `services/search/backend-contract.json` |
| Scope | design only; no source, test, workflow, configuration or infrastructure change |

---

## 0. 中文摘要（操作者用）

**你看到的問題是對的。** 記憶和知識庫都「寫得進去」，但會議、找策略、演化、每日循環幾乎沒有「用出來」：

- 每日循環和會議讀記憶時，都用一句固定的通用查詢抓 8 筆，跟這次要決定的事無關。
- 會議參加者只拿到一個編號（例如排名快照 ID），看不到策略規格、回測、相似策略的下場；參加者沒有工具，自己也查不到。
- 研究和演化只「寫」記憶，產生提案時完全不「讀」。
- 收新策略、調比重、移除只看數字；教訓沒有任何路徑能變成規則。唯一會影響決定的是「試過的策略類型不要再找」。

**RAG 不該自己做，而且其實已經選好開源方案了。** 9/10 的檢索任務已經驗收「Postgres 全文檢索＋pgvector 向量檢索、兩者用 RRF 合併、embedding 用本機跑的 multilingual-e5-large」，Qdrant 只當比較基準。但：

- hosted 的 Postgres 沒裝 pgvector，檢索資料表不存在，搜尋服務跑的是 JSON 檔，語意檢索關閉；
- 記憶從來沒有寫進那個索引（投影函式沒有任何呼叫者），記憶服務也沒有設定連線，所以一直退回自己寫的「用空格切字」關鍵字比對，中文幾乎找不到。

**這份設計做六件事：**
1. **啟用已經選好的開源檢索。** 開源調查（§4）的結論跟 9/10 的選擇一致：Mem0、Letta、Graphiti、Cognee 這類記憶框架都會自己保管記憶或讓 AI 直接寫，跟「只有擁有者服務能寫」衝突，不用它們當真相來源；檢索全部留在 Postgres（pgvector 向量＋全文檢索，用 RRF 合併）。要改的是：中文斷詞改用 jieba（現在是一個字一個字切）；embedding 用真實資料的評估集在 e5-large、bge-m3 和小模型之間挑；評估用 ranx。pgvector 放在獨立的檢索專用 Postgres（索引可以從原始資料重建），不動主資料庫。
2. **記憶寫入時同步進索引。** 新增、取代、封存、到期都同步；舊資料補建；中英文混合查詢要有評估數字。
3. **決策卷宗。** 一個共用元件，依「這次要決定什麼」組出一份附出處的卷宗：結構化事實（策略規格、驗證、回測、虛擬帳、池子）直接向擁有者服務讀；相似歷史（退役、被拒、移除、負面記憶）；教訓（自己的私人記憶＋可在會議分享的記憶＋機構記憶）；證據摘錄（搜尋閘道）。
4. **每個決策點都讀卷宗。** 每日循環、寫需求單、研究開工、演化提案、會議、harness 提案；會議的每一句主張都要引用卷宗裡的條目，否則該份意見無效。改良提案要寫明它怎麼處理引用的教訓。
5. **教訓變成規則的正式路徑。** 審核過的教訓可以轉成三種提案：persona 門檻或會診規則（harness 流程）、驗證關卡（研究關卡，需治理核准）、池子參數（池子政策，需治理核准）。都不直接碰交易。
6. **追蹤記憶有沒有用。** 每次被引用的教訓，等決定的結果出來後標「有幫助、有害、沒差、無法判斷」，排序改用這個，而不是只看被讀過幾次；一直「有害」的送記憶審核。

**不變的界線：** 交易執行永遠不讀記憶和卷宗；AI 只能提候選；私人記憶不跨 persona；會議只看可分享的範圍；讀取沒授權就整個拒絕。

---

## 1. Operator statements (2026-11-13, verbatim)

> 記憶的部分你再詳細說明，跟知識庫怎樣合作?

> 我感覺你現在知識庫跟記憶並沒有整合到你會議以及persona交易策略優化內? 感覺起來是單獨功能阿

> 當然阿，而且奇怪你rag為什麼要自己做?
> 不是應該有很多做得不錯的opensource嗎?

The second statement was answered with a per-touchpoint audit and a proposal; the third approves writing this design and opening the tasks, and directs the retrieval layer to open-source components.

---

## 2. What the original planning already decided

| Planned | Source |
| --- | --- |
| Knowledge is shared across personas; capital and live execution are isolated. Feedback returns to models, personas **and the knowledge base**. | `Pantheon_總索引版系統分析文件.md:70`, `:108` (axiom 9) |
| The Knowledge & Registry Plane is the truth source for source, strategy, alpha, experiment, artifact, approval, insight and evidence. | `Pantheon_總索引版系統分析文件.md:348-349` (§5.7) |
| Agents search only through a governed search gateway that returns a **cited evidence bundle** and an audit event. | `docs/03/SD-03_source_knowledge_evidence.md:17-24` |
| Memory has persona-private and institutional layers; only owner services write when an event ends; sessions read through one retrieval facade with read authority enforced. | `services/memory/MEMORY_LAYER_DESIGN_NOTE.md` §2–§5 |
| Retrieval is **contextual**: research kickoff asks for "prior findings relevant to this strategy/instrument"; consultation asks for "cross-persona observations relevant to this decision"; evolution review asks for "regime patterns and incident lessons relevant to this persona's evolution". | `services/memory/MEMORY_LAYER_DESIGN_NOTE.md:257-268` (§5.2) |
| A lesson changes a later decision: incident lesson → institutional memory → a new canary gate for another momentum persona. | `services/memory/MEMORY_LAYER_DESIGN_NOTE.md:270-346` (§6) |
| Storage is PostgreSQL with a pgvector index for semantic retrieval. | `services/memory/MEMORY_LAYER_DESIGN_NOTE.md:348-394` (§7) |
| The single production retrieval backend is `postgres_pgvector` (native FTS + pgvector, RRF fusion) with `intfloat/multilingual-e5-large` run locally through FastEmbed ONNX; Qdrant is only an evaluation baseline; memory and negative memory are declared wired to it. | `services/search/backend-contract.json`, `services/search/model-manifest.json` |
| OpenClaw `MEMORY.md` is a cache materialised from canonical memory, never a second truth source. | `docs/04/pantheon_openclaw_persona_memory_gap_2026-07-03/OPENCLAW_PERSONA_MEMORY_GAP_SPEC.md` §Correct Source-Of-Truth Boundary |
| The persona panel and memory writers of the reconnect design. | `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` §6.3, §6.4 |

## 3. Verified state on `origin/dev` bc8807e04 and hosted dev (read only, 2026-11-13 07:00–07:45Z)

### 3.1 Readers

| Touchpoint | What it reads today | Where |
| --- | --- | --- |
| Daily cycle Observe | pool, books, telemetry, incidents, human feedback and a materialised memory cache; no knowledge-base item (findings, candidate specs, registry outcomes) | `services/persona/persona_cycle_controller.py:274` (`observe`), memory at `:302-316` |
| Memory materialisation | a fixed query `"recent lessons, preferences, risks, and institutional context for this persona"`, limit 8; the request model accepts only `persona_id` and `workspace_ref` | `integrations/openclaw/persona_memory_bridge.py:18-20`; `services/openclaw-gateway-adapter/main.py:3486-3494` |
| Panel participants | the same fixed query for committee-visible memory, limit 8; the decision evidence is a reference id only (canary candidacy sends `ranking-snapshot:<id>`); participants are deny-all with no tools | `services/openclaw-gateway-adapter/consultation_provider.py:365`, `:494-515`; `services/persona-evaluator-agent/persona_evaluator_agent.py:748-772` |
| Pool cycle | only `tried_family:` tags, to exclude families from the next demand | `services/persona/pool_autonomy_cycle.py:440-446` |
| Research | writes findings to memory; never reads memory; a retrieval-influence record exists but nothing fills it automatically | `services/research/main.py:2844-2904` (finding writeback outbox); retrieval-influence endpoint `services/research/main.py:3412`; `services/research/retrieval_influence.py` |
| Evolution | writes outcome payloads; never reads memory or findings when proposing | `services/evolution/main.py:2078-2082` (Learn feedback writeback payload) |
| Pool admission, weights, removal | numeric rules only | `services/control-plane/persona/pool_policy.py` |

### 3.2 Retrieval layer

| Fact | Where |
| --- | --- |
| Persona memory ranking is whitespace-split keyword overlap ×10 + tag overlap ×5 + reuse count; Chinese text without spaces rarely matches | `services/memory/persona_memory_store.py:343-390` |
| Memory uses the Postgres backend only when `PANTHEON_SEARCH_POSTGRES_DSN` is set, otherwise falls back to the local keyword store | `services/memory/search_retrieval.py:33-48`; `services/memory/main.py:653-710` |
| `project_institutional_entry` and `project_persona_entry` have no caller outside tests, so memory is never indexed | `services/memory/search_retrieval.py:55`, `:103` |
| The search index pipeline indexes only `knowledge_object` records | `services/search/index_pipeline.py:169` |
| No compose service sets `PANTHEON_SEARCH_POSTGRES_DSN`; the Postgres image is `postgres:16-alpine` without pgvector; the index schema requires `CREATE EXTENSION vector` | `docker-compose.yml:56`; `services/search/sql/retrieval_index.sql:2` |
| Search embeddings default off | `services/search/main.py:68` (`SEARCH_VECTOR_EMBEDDING_ENABLED`) |
| Lexical side uses the `simple` parser with CJK characters spaced into unigrams | `services/search/pg_retrieval.py:50-52`; `services/search/sql/retrieval_index.sql:29` |
| The model manifest is marked "unaccepted; prior model comparison numbers have no reproducible validation evidence" | `services/search/model-manifest.json` |

Hosted dev, read only: the Postgres extensions are `plpgsql` and `pg_trgm` only; no retrieval table exists; `pantheon-search-svc-1` serves JSONL files with `SEARCH_VECTOR_EMBEDDING_ENABLED` unset; `pantheon-memory-1` has neither search DSN set and its data directory is empty (0 entries). The running backend is still `bee62bc29` because twelve Dev releases since 2026-11-12T04:17Z failed.

So the accepted open-source backend (`SIMPLIFY-RETRIEVAL-ACCEPTANCE-CLOSURE-001`, 2026-09-10, commit `f46537d25`) exists in code and in an evaluation, but it was never activated, and memory never reached it although the contract records memory as wired. The same pattern — a component accepted on tests while production never calls it — is recorded for the July persona controller in `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` §2.1.

---

## 4. Open-source decision

Survey read on 2026-11-13 (sources in §9). The question was whether a ready-made memory framework or retrieval product should replace the hand-written path.

### 4.1 Agent-memory frameworks: none may own memory state

| Framework | License | Latest seen | Why it does or does not fit |
| --- | --- | --- | --- |
| Mem0 | Apache-2.0 | mem0ai 2.2.1, 2026-09-25 | keeps its own collection; by default the LLM writes memories — conflicts with owner-only writes |
| Letta (MemGPT) | Apache-2.0 | `letta` 0.34.12 (now the Letta Code CLI), 2026-10-12 | agents edit their own memory blocks |
| Zep / Graphiti | Apache-2.0 | graphiti-core 0.31.2, 2026-10-20; Zep CE deprecated | needs Neo4j/FalkorDB/Neptune; its LLM rewrites the graph |
| Cognee | Apache-2.0 | 1.6.8, 2026-11-12 | its pipeline builds and owns its own datasets and graph |
| LangMem | MIT | 0.0.30, 2025-10-27 | extraction can return candidates over any store, but no release for a year |
| LlamaIndex memory | MIT | llama-index-core 0.14.25, 2026-09-21 | chat-session oriented, heavy dependency |

Decision: the canonical memory tables stay the only truth; owner services keep writing them. No framework store is introduced. Owner services already emit structured events, so v1 needs no LLM extraction helper; if one is added later it may only produce candidates.

### 4.2 Retrieval: everything inside Postgres, as already accepted

The accepted backend (`services/search/pg_retrieval.py`) already implements the recommended pattern: one SQL query with identical filters on a lexical branch and a pgvector branch fused by reciprocal rank fusion (k = 60). It is kept. Alternatives were rejected for this scale: Qdrant (second store to keep in sync), OpenSearch (JVM service), ParadeDB `pg_search` (AGPL-3.0; held as a phase-2 option, see 4.4).

### 4.3 Chinese lexical side

The current lexical branch spaces every CJK character into a unigram (`services/search/pg_retrieval.py:50-52`), so `plainto_tsquery` requires every character of a query to appear. Decision: segment with Python `jieba` (MIT) using `dict.txt.big` plus a governed user dictionary of Taiwan tickers and company names, both at index time and at query time, stored in the existing `simple` `tsvector`. No new Postgres extension is needed for this.

### 4.4 Models and evaluation

* Embedding candidates, all local and permissively licensed: `intfloat/multilingual-e5-large` (MIT, the pinned but "unaccepted" manifest), `BAAI/bge-m3` (MIT, 1024 dimensions, 8192 tokens), and a CPU fallback (`multilingual-e5-small` MIT, or `granite-embedding-97m-multilingual-r2` Apache-2.0). `jina-embeddings-v3` is excluded (CC BY-NC 4.0).
* Optional reranker `BAAI/bge-reranker-v2-m3` (Apache-2.0) over the top 30–50 hits, enabled only if the evaluation shows a gain worth its CPU latency.
* Evaluation with `ranx` (MIT): a gold set of 150–300 recorded real zh-TW and English queries labelled by `source_event_id`; compare current keyword ranking, lexical only, dense only, hybrid, hybrid plus reranker; report recall@10, nDCG@10 and p95 latency on the dev host. The model with the best recall within the latency budget is pinned in the manifest with its checksums.
* Phase 2, only if the gold set shows a lexical gap after jieba: ParadeDB `pg_search` with its jieba tokenizer (`chinese_convert=tw2s`) for true BM25. It is AGPL-3.0 and needs an operator licence decision before any task opens.

### 4.5 Where pgvector runs (**change**)

The contract chose "no extra container". Installing pgvector means changing the image of the main hosted Postgres (`postgres:16-alpine`), which also backs every product store, is referenced by CI service containers and release capture, and could change collation behaviour if the base libc changed. The retrieval index is derived data that can be rebuilt from the canonical memory and knowledge stores. Decision: run the index in a dedicated `retrieval-postgres` service from the official pgvector image pinned by digest, with its own volume and a rebuild entry point; the main Postgres is not touched. Embedding runs only in the search service; the memory service calls the search service for projection and candidate retrieval and keeps hydration and authorisation in its own store, so the model is packaged once.

---

## 5. Decisions

Each decision follows the cited plan unless marked **change**. Numeric values are coordinator starting values recorded in policy, not operator decisions.

| # | Decision | Follows |
| --- | --- | --- |
| M1 | Activate the accepted retrieval backend on hosted dev in a dedicated `retrieval-postgres` service (§4.5) with the existing `retrieval_index.sql`, jieba segmentation (§4.3), the embedding model chosen by the gold-set evaluation and packaged offline with manifest checksums verified at start (§4.4), and the search service as the only embedding runtime. No vector database other than pgvector; no memory framework store. | `services/search/backend-contract.json`; §4 |
| M2 | Memory writes project into the shared retrieval index through the search service on create, supersede, archive and expiry, with a backfill entry point and tombstones; the memory service keeps hydration, expiry and read authorisation. Every retrieval response states its mode (`hybrid`, `lexical_only`, `keyword_fallback`) and fallback is never silent. | `MEMORY_LAYER_DESIGN_NOTE.md` §5, §7 |
| M3 | One **decision case file** composer, a library in the knowledge plane used by owner services (no new service). Input: decision type, persona, tenant, member artifact refs, strategy family, underlyings, instrument class, optional regime tag. Output: bounded cited items in four groups — structured facts read from owner APIs (registry, research runs and gate verdicts, pool and member virtual books); similar history (negative-memory matches and retired, rejected or removed strategies); lessons (caller-scoped persona memory, committee-visible memory, institutional memory) retrieved with a query and tag filters built from the input; evidence excerpts through the governed search gateway. Each item keeps its source id, excerpt, retrieval score and mode. The case file is persisted with an id and digest and referenced by the decision, packet or memo that used it. | SD-03 §1; `MEMORY_LAYER_DESIGN_NOTE.md` §5.2 |
| M4 | Readers: the daily cycle builds its retrieval query from the pool-health shortfall and member families and underlyings, and the materialiser accepts that query (no fixed query); demand writing and research kickoff attach a case file and record retrieval influence automatically; evolution proposals for a member attach a case file and the child declares `addresses_lessons` (cited lesson ids, or `none_applicable` with a reason); harness proposals read experiences through the same retrieval. | `MEMORY_LAYER_DESIGN_NOTE.md` §5.2; `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` §6.1, §6.8 |
| M5 | Panels receive the case file content, not only reference ids; each participant also receives its own committee-visible memory retrieved with the decision query. A contribution whose claims do not cite case-file item ids is invalid and is not synthesised. The deny-all participant boundary is unchanged. | `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` D7; SD-03 cited evidence |
| M6 | **Change (operator direction 2026-11-13).** Endorsed lessons may be converted, with cited lesson ids, into typed proposals through existing owners only: a harness patch (persona thresholds, consult policy; paper rules of the reconnect design D10 apply); a gate-check proposal to the research gate owner (governance decision required, shared by all personas); a pool-policy parameter proposal (governance decision required). No conversion changes execution directly. | `MEMORY_LAYER_DESIGN_NOTE.md` §6; reconnect D10 |
| M7 | **Change.** Usefulness replaces raw reuse count in ranking: every case-file item used by a decision is recorded; after that decision's outcome window the cited lessons are marked `helped`, `hurt`, `no_effect` or `confounded` with the same rules as harness experiences; ranking adds a bounded usefulness term; three `hurt` marks route the lesson to memory review. | reconnect §6.8 review rules |
| M8 | Execution never reads memory or case files; agents only propose candidates; private memory never crosses personas; committee scope stays `persona_and_committee`; retrieval without governance authorisation fails closed. | `TARGET_ARCHITECTURE.md:14`; `MEMORY_LAYER_DESIGN_NOTE.md` §3 |
| M9 | Retrieval quality is measured on a recorded mixed Traditional Chinese / English evaluation set built from real entries and documents (recall@10 and nDCG@10, keyword fallback versus hybrid, plus dense-only and reranker variants, with `ranx`); the activation task publishes the numbers and p95 latency. | `services/search/evaluation/run_retrieval_eval.py`; §4.4 |

## 6. Target flow

```text
owner event ends ──► Memory (canonical, owner-written) ──► projection ──┐
source / registry / research ──► Knowledge plane (truth) ──► search index ┤  Postgres FTS + pgvector, RRF
                                                                          ▼
decision point (cycle · demand · research · evolution · harness · panel)
   └─► case file composer: facts (owner APIs) + similar history + lessons + evidence, all cited
         ├─► agent turn / panel participants (read only, cite item ids)
         ├─► deterministic gates decide (unchanged)
         └─► decision record keeps case_file_id ─► outcome window ─► usefulness marks ─► ranking
endorsed lesson ─► typed proposal (harness / gate check / pool policy) ─► existing owner and approvals
```

## 7. Delivery

Every task depends on this design task and keeps the standing rules: production entry point exercised by an integration test; missing data fails closed with a recorded reason; no synthetic data in production code; paper only; no model call on the trade path; no new privileged route.

| Wave | Task (suffix -20261113) | Scope |
| --- | --- | --- |
| 0 | `SD-MEMORY-KNOWLEDGE-INTEGRATION` | this document |
| 1 | `RETRIEVAL-BACKEND-ACTIVATION` | M1, M9 |
| 1 | `MEMORY-INDEX-PROJECTION` | M2 |
| 2 | `DECISION-CASE-FILE` | M3 |
| 3 | `PERSONA-CYCLE-CASE-FILE` | M4 cycle and materialiser |
| 3 | `CONSULT-PANEL-CASE-FILE` | M5 |
| 3 | `RESEARCH-EVOLUTION-CASE-FILE` | M4 demand, research kickoff, evolution |
| 4 | `LESSON-RULE-PROPOSALS` | M6 |
| 4 | `MEMORY-USEFULNESS-OUTCOMES` | M7 |
| 5 | `BFF-DECISION-CASE-FILE-VIEWS`, `FE-DECISION-CASE-FILE-VIEWS` | operator sees the case file and cited memory per decision, memo and packet |
| 6 | `MEMORY-KNOWLEDGE-HOSTED-ACCEPTANCE` | operator-side hosted acceptance on natural events |

## 8. Risks

| # | Risk | Control |
| --- | --- | --- |
| R1 | Model files need network to download | package offline in the image; verify manifest checksums at start; fail closed |
| R2 | CPU cost of a 1024-dimension model on the dev VM | embed on write only; bounded batch backfill; measure p95 in M9 |
| R3 | Case files leak private memory into panels | caller-scoped retrieval; committee scope enforced in the facade; tests with two personas |
| R4 | Agents game citations | validator checks every cited id exists in the case file and the claim's subject matches the item's subject fields |
| R5 | Lessons harden into wrong rules | conversions go through existing owners and approvals; usefulness tracking flags harmful lessons |
| R6 | Shared files with open tasks | ordering through dependencies on the current writers |
| R7 | The deploy pipeline is failing | hosted acceptance waits for an accepted Dev release; source tasks do not |

## 9. Sources

All paths are relative to the repository root and were read on `bc8807e04`. Hosted observations were read only through SSH `docker exec` and a read-only `psql` session on 2026-11-13.

Open-source survey (read 2026-11-13): https://pypi.org/project/mem0ai/ · https://docs.mem0.ai/core-concepts/memory-operations/add · https://pypi.org/project/letta/ · https://pypi.org/project/graphiti-core/ · https://blog.getzep.com/announcing-a-new-direction-for-zeps-open-source-strategy/ · https://pypi.org/project/cognee/ · https://pypi.org/project/langmem/ · https://github.com/run-llama/llama_index/releases · https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md · https://github.com/pgvector/pgvector-python/tree/master/examples/hybrid_search · https://github.com/paradedb/paradedb · https://www.paradedb.com/docs/reference/hybrid/rrf.md · https://www.paradedb.com/docs/reference/tokenizers/available-tokenizers/jieba.md · https://qdrant.tech/documentation/concepts/indexing/ · https://github.com/fxsjy/jieba · https://huggingface.co/BAAI/bge-m3 · https://huggingface.co/intfloat/multilingual-e5-large · https://huggingface.co/blog/ibm-granite/granite-embedding-multilingual-r2 · https://huggingface.co/api/models/jinaai/jina-embeddings-v3 · https://huggingface.co/BAAI/bge-reranker-v2-m3 · https://github.com/AmenRa/ranx
