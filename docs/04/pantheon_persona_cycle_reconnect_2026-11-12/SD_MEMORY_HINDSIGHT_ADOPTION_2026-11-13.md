# SD-MEMORY-HINDSIGHT-ADOPTION-20261113 — use Hindsight as the memory index behind the canonical memory service

| Field | Value |
| --- | --- |
| Status | Design for implementation; the operator approved the conclusion and asked for the design and tasks on 2026-11-13 |
| Date | 2026-11-13 |
| Repo base | `origin/dev` `6cb713b01`; every repository reference below was read on this base |
| Changes | `SD_MEMORY_KNOWLEDGE_INTEGRATION_2026-11-13.md` decision M2 (memory projection into the search index) is **replaced**; M4/M5 readers and M7 usefulness are **kept** and re-pointed; M1 and M9 (knowledge-base retrieval) are **unchanged** |
| Keeps | `services/memory/MEMORY_LAYER_DESIGN_NOTE.md` §2–§4 (canonical stores and owner-only writers); `SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` panel and cycle boundaries |
| Scope | design only; no source, test, workflow, configuration or infrastructure change |

---

## 0. 中文摘要（操作者用）

**操作者 11/13 的方向：** 記憶的標籤、時間、權重、關聯圖、新記憶進入、無效淘汰要用成熟開源方案，不要自己造輪子；評估依結構和技術判斷即可。協調者比較後建議記憶改用 Hindsight，操作者同意（「1 好 2好」）。

**分層模型（操作者更正後的版本）：**
- **事實**（發生了什麼）共享：現行共用層（InstitutionalMemory）存的就是事件事實，維持。
- **記憶**是每個 persona 對事實的**解讀**，私人。
- **會議**是各 persona 交流對同一件事的不同解讀與關聯；分享的解讀給**完整內容**。

**這份設計做的事：**
1. 正式記憶仍由擁有者服務寫進現有記憶庫（不變）；Hindsight 只當**可重建的索引**，編號就是我們的記憶編號。
2. 記憶庫對應：每個租戶一個「事實庫」（共用層）；每個 persona 一個「解讀庫」（私人記憶，可在會議分享的另加標籤）。
3. 錨點（策略、父代、類型、標的、商品、資料集、盤勢、決定類型）由我們提供，同時當實體與標籤，名稱不讓 AI 改。
4. 讀取：先依錨點標籤嚴格篩（關聯先篩）→ Hindsight 四路搜尋（語意、關鍵字、關聯圖、時間）＋重新評分＋新舊/時間加成 → 最後用我們的「有沒有用」調整。
5. 讓 AI 控制讀取：記憶服務提供 recall 與 reflect（AI 讀記憶後回答並附出處）；persona 每日循環多兩個唯讀工具；會議前每個參加者用 reflect 整理自己的解讀，全文帶進會議。
6. 淘汰：我們取代/封存/到期時，同步在 Hindsight 標成失效；有害 3 次送審照舊；不靠刪除提升效果。
7. 拿掉自製的記憶排序與「記憶寫進搜尋索引」那段，只留一套記憶索引；知識庫的文件與證據檢索不變。

**不變的界線：** 交易執行不讀記憶；AI 只提候選；只有記憶服務能連 Hindsight（Hindsight 只有一把共用金鑰、沒有逐庫權限，所以不能讓 agent 直接連）；私人解讀不跨 persona；會議只拿標成可分享的解讀。

**待操作者決定：** AI 拆解／整理／reflect 用哪個模型（決定前先用不呼叫 AI 的模式上線）；私人記憶期限；盤勢錨點來源；是否啟用心智模型。

---

## 1. Operator statements (2026-11-13, verbatim)

Transcript `/home/chloe_ong_dev_cctech_support_com/.claude/projects/-home-chloe-ong-dev-cctech-support-com-code-pantheon/cb4f6795-9064-4bb2-ab77-c0740122bce1.jsonl`.

| Line | Time (UTC) | uuid | Text |
| --- | --- | --- | --- |
| 2236 | 22:32:14.578Z | 5c72c3a2-4d66-4464-b63a-d1cd96c06bf7 | 我感覺你關聯圖把好幾個地方的應用混再一起，知識庫，策略演化，還有記憶都混再一起講，其實你要分開來討論才對。例如記憶的關聯圖可以提供除了權重、時間序、這兩個以外的記憶篩選機制。先依關聯選出來，然後再看權重跟時間序。這樣有可能可以極大程度降低當次無效記憶的引用，應該可以這樣想 |
| 2258 | 22:36:20.156Z | ec85a1d0-ce43-4767-9c85-ac750177b841 | 所以你的篩選裡面已經有標籤、時間序、權重，加上關聯圖。你的重點應該是這些標籤 時間序 權重跟關聯圖 怎麼做 跟怎麼調整 包含新記憶進入，無效記憶淘汰等 這樣才會是有效的利用 而我認為這種rag的設計很有可能都已經有很成熟的oss了 所以你現在糾結的問題應該人家都已經有完整解決方案才對 你應該先搜尋那些解決方案合用，而不是重複造輪子 |
| 2423 | 22:44:11.727Z | 818f66a9-391a-414a-8a50-4302af4ac471 | 把這些確認一下，我感覺不太是問題，呼叫AI本來就可以用不同LLM阿 |
| 2535 | 22:57:13.179Z | 310aefa2-9b3c-4eba-a4d2-d510f4d37f60 | persona 私人記憶不跨 persona；如果要分享從會議討論中進行分享，並提供完整內容予以其他persona |
| 2572 | 22:57:58.911Z | 45329ccf-d197-4f17-a728-583bf3712125 | 另外我感覺Hindsight 加上用LLM來控制操作可能會比較理想耶 |
| 2662 | 23:16:17.677Z | ef676e94-9562-403c-99fd-03e8a1db5db2 | 原本的做法沒問題啊，因為每個persona在會議交流才會知道大家對於同一個記憶解讀有哪些不同怎樣關聯啊，不是只分享事實，你這兩個又搞混了 |
| 2694 | 23:19:59.916Z | 73bdb705-aff5-43a3-bae6-2752b6877449 | 其實評估根本不需要跑過資料，你只要依據結構完整跟技術性哪個比較好做比較判斷即可，你這沒有資料的評比根本沒用 |
| 2707 | 23:28:51.039Z | f58ae734-5c65-46cc-9e6e-3b93f9610247 | 1 好 / 2好 — answering the coordinator's structural evaluation at line 2704 (23:21:24.438Z, uuid 0bbbcf64-b372-408b-824e-8cfb8b5bede6): (1) proceed with Hindsight as the memory index and write this design and its tasks; (2) withdraw the two data-comparison tasks |

The two comparison tasks (`MEMORY-HINDSIGHT-COMPARISON-HARNESS-20261113`, `MEMORY-HINDSIGHT-COMPARISON-RUN-20261113`) were superseded on 2026-11-13 with no surviving obligation.

---

## 2. Model the design serves

| Layer | What it is | Who writes | Who reads |
| --- | --- | --- | --- |
| Facts | what happened: event details, evidence, telemetry, proposal ids | owner services through the existing writers; the institutional entry is built in `services/memory/learn_feedback_writeback.py:395-447` (`_build_institutional_entry`) | every persona of the tenant |
| Interpretations | one persona's reading of facts: lessons, consultation outcomes, preferences | owner services on behalf of that persona (`PersonaMemory`) | only that persona; items marked shareable may be brought into a meeting by that persona |
| Meeting exchange | each participant's interpretation of the decision at hand, in full, so participants see where readings differ and how they relate | produced per participant before the panel; recorded in the meeting record; each participant then writes its own `consultation_outcome` | panel participants of that meeting |

Knowledge-base retrieval (documents and evidence through the search gateway) is a separate concern and is unchanged.

---

## 3. Verified current state (`origin/dev` `6cb713b01`)

| Fact | Where |
| --- | --- |
| Memory retrieval route merges persona and institutional hits by score then written time | `services/memory/main.py:600` (`/api/memory/retrieve`), `:706-707` |
| Memory is projected into the shared search index on every mutation through store hooks | `services/memory/institutional_memory_store.py:406` and `services/memory/persona_memory_store.py:393` (`_project`); `services/memory/search_retrieval.py:106` (`sync_memory_projection`), `:122` (`backfill_memory_index`), `:143`, `:193` |
| Candidate retrieval for memory goes through the search service and falls back to keyword ranking (`matched_terms*10 + matched_tags*5 + usefulness`) | `services/memory/search_retrieval.py:240`, `:328`; `services/memory/persona_memory_store.py:410-458` |
| The search service carries memory record kinds only for that projection | `services/search/main.py:433`, `:451` (`MemoryProjectionBody`), `:855`, `:1028` |
| Usefulness term: `5 * (helped - hurt) / (2 + marks)`; three `hurt` marks open a memory review | `services/memory/usefulness.py:14-15`, `:46-50` |
| Case file lessons and negative memory use `lesson_query` (family, underlyings, datasets, instrument, decision type, optional regime) with any-tag matching | `services/knowledge/case_file.py:206` (`lesson_query`), `:311` (`_lessons`), `:395` (negative memory) |
| No caller supplies `regime_tag` | `git grep "regime_tag\s*=" -- services` outside tests returns nothing |
| Case file budgets 8 items per group, 400-character excerpts, 12,000 characters total; the panel case file is trimmed to that total | `services/knowledge/case_file.py:50`; `services/consultation/workflow_executor.py:783` (`_compose_panel_case_file`), `:876` |
| Panel participants are deny-all agents with no tools; each receives its own committee-visible memory retrieved with the decision query | `services/openclaw-gateway-adapter/consultation_provider.py:340-348`, `:494`, `:559` (`committee_visible_memory`), `:891` |
| Panel contributions must cite case-file items | `services/consultation/workflow_executor.py:892` (`_case_file_rejection`) |
| Daily-cycle proposals must cite case-file item ids or state why none applies | `services/persona/persona_cycle_controller.py:51`, `:63`, `:93` |
| Persona agents may use only `read` and the proposal tool | `integrations/openclaw/persona_agent_sync.py:154-183` (`PERSONA_PROPOSAL_TOOL`, `PERSONA_AGENT_TOOL_PROFILE`) |
| Retrieval Postgres for the knowledge base is a separate compose service with no host port | `docker-compose.yml:82-98` |
| Memory service is reachable at `memory:8086` and already calls the search service | `docker-compose.yml:2034` onward |

Hosted dev (read only, 2026-11-13 23:00Z): memory 0 entries; 0 persona pools; 2 executed evolution decisions; 6 consultation memos. The hosted pair is still the 2026-11-12 baseline.

---

## 4. Why Hindsight (structural evaluation summary)

Sources read on 2026-11-13 (docs version 1.2; release v1.2.1): retain, recall, reflect, mental models, memory banks, documents, memories, configuration, models, storage, multilingual, installation, MCP server, extensions, and the GitHub repository (MIT `LICENSE`).

| Mechanism | Current in-house | Hindsight |
| --- | --- | --- |
| Tags | owner tags; any-tag match only | per item and document tags; `tags_match` any, any_strict, all, all_strict, exact; boolean `tag_groups`; bank entity labels with `tag: true` |
| Association | none (fixed owner queries for similar history) | entity graph at retain (caller `entities` merged with extracted ones, `resolve_entities`), entity, temporal, semantic and causal links; a graph leg in recall |
| Weights | relevance plus usefulness ±5 | four-way recall fused by RRF (k = 60), cross-encoder over the top 300, multiplicative recency, temporal-proximity and proof-count boosts |
| Time | tie-break only | event `timestamp` versus mention time; temporal leg for time expressions; recency boost over 365 days |
| New memory | whole entry, no merge | extraction modes (concise, verbose, custom, verbatim, chunks); background observations with near-duplicate threshold 0.97 and history-preserving contradictions; mental models |
| Invalid memory | supersede, archive, institutional TTL, review after three hurt marks | soft invalidation per memory, document delete and reprocess; no TTL by design |
| Tuning | constants in code | configuration per operation and per request |
| Fit with Section 2 | facts and interpretations exist; no consolidation; 400-character excerpts | native world facts, experiences, observations, mental models; isolated banks |
| LLM-controlled reads | none | `reflect`: an agent loop over mental models, observations and recall with `based_on` citations |
| Stack | Postgres | PostgreSQL 15+ with pgvector; pgroonga for CJK; one extra API service |

Not chosen: Graphiti (needs a graph database outside Postgres; ingestion only through LLM extraction), Mem0 (current open-source path is add-only with retrieval-time handling; graph status inconsistent across sources), Cognee (owns its graph datasets; its feedback weighting is the only feature we keep in-house), Letta (agents edit their own memory, against owner-only writes). Vendor benchmark scores are self-reported and were not used.

Hindsight has no per-memory custom boost and no outcome-based weighting; usefulness stays in-house as the final re-rank. Hindsight authentication is one shared key with no per-bank access control and its MCP server exposes destructive tools, so only the memory service may call it.

---

## 5. Decisions

Numeric values are coordinator starting values, not operator decisions.

| # | Decision |
| --- | --- |
| H1 | **Service.** Add an internal `hindsight` API service (official `ghcr.io/vectorize-io/hindsight-api` image, version pinned by digest) and a dedicated `memory-index-postgres` built in the repository from `groonga/pgroonga:4.0.8-debian-17` plus `postgresql-17-pgvector` (as in the upstream pgroonga recipe), base image pinned by digest. Neither publishes a host port. `HINDSIGHT_API_TEXT_SEARCH_EXTENSION=pgroonga`, `HINDSIGHT_API_VECTOR_EXTENSION=pgvector`, migrations on start. The built-in API-key tenant extension is enabled with a key supplied the same way as other internal service passwords in `docker-compose.yml`; only the memory service receives it. The MCP endpoint is disabled. Deploy, release capture and rollback handle both services; the index volume can be dropped and rebuilt (H3). |
| H2 | **Models offline.** Embeddings use the ONNX provider with the same `multilingual-e5-large` files already packaged and checksummed for the search service, with `query: ` and `passage: ` prefixes; the reranker is a local multilingual cross-encoder packaged with checksums (starting candidate `BAAI/bge-reranker-v2-m3`, Apache-2.0). No model is downloaded at runtime; checksum failure keeps the service not ready. The image variant (slim or full) is whichever runs these ONNX providers; the deploy task records the choice and image size. |
| H3 | **Banks and mapping.** One facts bank per tenant holds institutional entries; one interpretation bank per persona holds that persona's entries. Each canonical entry is one Hindsight document: `document_id` is the canonical id; `metadata` carries source event type and id, write authority, memory type and relevance scope; `timestamp` is the event time; `context` is the source event type; `entities` are the anchors (H4) with `resolve_entities=false`; tags are the anchors plus `scope:private` or `scope:committee` and the memory type. Canonical supersede, archive and expiry invalidate the document's memories; a compliance deletion deletes the document. A rebuild entry point drops a bank and re-retains every active canonical entry. Canonical stores remain the only truth. |
| H4 | **Anchors.** Anchors are derived deterministically from canonical structured fields and tags by one mapping function: strategy, parent strategy, family, underlying, instrument class, dataset, decision type and regime when present. They are never produced by an LLM. Entries without an anchor are retained with tag `anchor:none` and counted in diagnostics. |
| H5 | **Extraction and LLM layers.** Retain, consolidation (observations), reflect and mental-model refresh each use their own LLM settings. Until the operator chooses a provider, retain uses `chunks` mode (no LLM call; caller entities only), consolidation and reflect are off, and responses report `llm_layers: disabled`. After the choice, retain uses `verbatim` (original text kept with extracted entities and dates) so the stored text stays the owner's text. Observations are per bank, so they are that persona's consolidated interpretations; they are derived, always carry `source_fact_ids`, and are never written back as canonical memory. Mental models stay off until the operator enables them. |
| H6 | **Read facade.** The memory service remains the only retrieval entry point. `/api/memory/retrieve` calls Hindsight recall on each bank the caller may read (own interpretation bank with private items only for the persona itself; facts bank; other personas' banks never), with `tag_groups` requiring at least one subject anchor (`any_strict`) when the request carries subject anchors, then maps results to canonical ids (documents directly, observations through `source_fact_ids`), hydrates and authorises from the canonical stores, applies `usefulness_score` as the final re-rank, and states `retrieval_mode` (`hindsight`, or `keyword_fallback` with its reason). Starting request values: `budget=mid`, `max_tokens` sized to the caller's budget, types per caller. |
| H7 | **One memory index.** The memory-to-search projection is removed: store `_project` hooks, `sync_memory_projection`, `backfill_memory_index`, `project_*_entry`, `retrieve_*_with_backend`, and the search service's memory record kinds. The keyword ranking in the stores remains only as the explicit fallback. |
| H8 | **Case file.** `lesson_query` callers pass anchors as subject anchors so lessons and negative memory use H6 association-first filtering; `regime` is included when the subject carries it. Budgets, groups and citation rules are unchanged. |
| H9 | **LLM-controlled reads.** The memory service adds read-only `POST /api/memory/reflect` scoped exactly like H6 (one bank per call; the facts bank and the caller's own bank are separate calls), with low temperature, returning text plus citations mapped to canonical ids. Persona daily-cycle agents get two read-only tools, `persona_memory_recall` and `persona_memory_reflect`, brokered by the gateway with the agent's verified persona identity to these routes; no direct Hindsight access, no write tool. Proposals may cite canonical ids returned by these tools as well as case-file items, and the citation check verifies them against the tool-call record of that cycle. |
| H10 | **Meeting interpretations.** Before a panel call, each participant's interpretation is produced by `reflect` on that participant's own bank restricted to `scope:committee`, with the decision query, and is passed to the panel in full with its cited canonical ids. Length is controlled by count (one interpretation per participant, at most 12 cited items), never by truncating text. Participants stay deny-all; interpretations are produced by the memory service, not by giving participants tools. The meeting record stores each interpretation and its citations; the existing `consultation_outcome` writer records what each participant took away. While reflect is off (H5), the participant's committee-visible entries are passed in full instead. |
| H11 | **Invalidation and review.** H3 mirrors every canonical state change. The three-hurt review is unchanged; an archive decision from that review invalidates the document. No eviction for performance. Persona memory retention stays as today until the operator decides. |
| H12 | **Boundaries.** Execution never reads memory, case files or Hindsight; agents only propose; private interpretations never cross personas; meetings receive only `scope:committee` interpretations; any authorisation or availability failure fails closed with a recorded reason. |

---

## 6. Target flow

```text
owner event ends ──► canonical memory (owner-written; facts = institutional, interpretations = persona)
                          │ create / supersede / archive / expiry
                          ▼
                    memory-svc ──retain/invalidate──► Hindsight (internal)
                                                      facts bank per tenant · interpretation bank per persona
decision point ──► case file (owner facts + evidence search + memory via memory-svc recall)
daily cycle agent ──tools──► gateway ──► memory-svc recall / reflect (own bank + facts bank)
panel ──► memory-svc reflect per participant (own bank, scope:committee) ──► full interpretations to panel
outcome window ──► usefulness marks ──► final re-rank in memory-svc
```

---

## 7. Delivery

Every task keeps the standing rules: production entry point exercised by an integration test against real Hindsight and Postgres containers; missing data fails closed with a recorded reason; no synthetic data in production code; paper only; no model call on the trade path.

| Wave | Task (suffix -20261113) | Scope |
| --- | --- | --- |
| 0 | `SD-MEMORY-HINDSIGHT-ADOPTION` | this document |
| 1 | `HINDSIGHT-SERVICE-DEPLOY` | H1, H2 |
| 2 | `MEMORY-HINDSIGHT-INDEX` | H3–H7, H9 route, H11 |
| 3 | `SEARCH-MEMORY-PROJECTION-REMOVAL` | H7 search-service side |
| 3 | `CASE-FILE-HINDSIGHT-ANCHORS` | H8 |
| 3 | `PERSONA-MEMORY-TOOLS` | H9 tools and citation check |
| 3 | `PANEL-FULL-INTERPRETATIONS` | H10 |
| 4 | `MEMORY-HINDSIGHT-HOSTED-ACCEPTANCE` | operator-side hosted acceptance after an accepted Dev release |

---

## 8. Risks

| # | Risk | Control |
| --- | --- | --- |
| R1 | Shared Hindsight key allows any holder to read every bank | only memory-svc holds the key; no host port; MCP disabled; H6 authorises from canonical stores after mapping |
| R2 | Image and model size on the hosted VM disk | ONNX providers with shared model files; deploy records image size; release capture prunes as today |
| R3 | CPU cost of cross-encoder and 1024-dimension embeddings | embed on write only; reranker candidate cap configurable; p95 measured at hosted acceptance |
| R4 | LLM layers change stored text or merge names | `verbatim` mode; caller entities with `resolve_entities=false`; derived layers never become canonical |
| R5 | Index drift from canonical | every canonical mutation mirrored; rebuild entry point; hosted acceptance compares counts |
| R6 | Upstream project direction | MIT licence; version pinned by digest; canonical data stays in our stores, so the index can be replaced |
| R7 | Shared files with blocked owners (`MEMORY-WRITEBACK-WIRING-20261112`, `PERSONA-AGENT-PROVISION-20261112`) | ordered after them on the functional track |

---

## 9. Pending operator decisions

1. LLM provider and model for retain, consolidation and reflect (until then H5 runs without LLM layers).
2. Persona memory retention (no default today).
3. Source of the regime anchor.
4. Whether to enable mental models and which standing questions.
