# LLM / 搜尋 / 記憶簡化任務設計（2026-09-06）

設計 reviewer：`/root/publication_review`。這是非 canonical 設計草案；未實作、未派工、未改任務狀態。依據固定 audit baseline `471dc5391a0f9cbde54d51730891583043708e42`、本次讀取的 `origin/dev` `55cd327b9200648e5d42360907dedc17ddf6f5fc` 及本資料夾 `canonical-tasks-snapshot.json` 的 active artifacts。執行時必須重新核對 current dev 與 active lease，從乾淨 worktree 開始。建議由 Claude / Antigravity 實作，配置中的 Codex / Codex2 做真正獨立的 exact-head review；本草案不是 reviewer attestation。

## 最少任務集合與排序

| 候選 task ID | 報告覆蓋 | 執行／review 建議 | 真正依賴 | 可立即開始的部分 |
|---|---|---|---|---|
| SIMPLIFY-OPENCLAW-001 | #3 一般 turn 單 HTTP；#14 cron exact-run 部分 | Claude / Codex | 無新增外部 task 依賴 | adapter 與 cron 檔案無 active owner 重疊；維持 BFF public envelope |
| SIMPLIFY-EXTRACTION-001 | #6 intent / StrategySpec / lesson 共用 typed extraction | Antigravity / Codex2 | SIMPLIFY-OPENCLAW-001、AGORA-CHAIN-001、DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 | 等待 owner 時只可準備獨立、無 production mutation 的 baseline；正式 task 不搶先寫重疊檔案 |
| SIMPLIFY-RETRIEVAL-MEMORY-001 | #7 真實搜尋；#8 共用記憶檢索 | Antigravity / Codex | 無新增外部 task 依賴 | Search / Memory / negative_memory / provider memory bridge 無 active artifact owner 重疊 |

不另外建立「LLM 平台」、「memory 向量庫」、「search SDK」、「評估服務」或新的 integration owner。每個 task 自帶同一份 baseline、實作、退場決策、回歸與 PR delivery；搜尋與記憶放在同一 task，避免做完 search 又複製一套 memory ranking。三項都是 functional source lane：允許隔離本地服務／測試和真實核准模型評估；不包含 hosted deployment、MFA、live trading 或資料移轉執行。

### 與現有任務保持唯一 owner

- `AGORA-CHAIN-001` 正式擁有 `services/**/agora*`，因此 `source_ingestion/agora_seed_bridge.py` 的 extraction consumer migration 要在它實際合併後；不再做 Workshop command、receipt、actor credential propagation。
- `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001` 擁有 `services/research/**`，所以 normalizer / production_distillation 在它合併後使用已完成的 owner 呼叫。新 task 不改 distillation_controller、Registry admission、Governance approval、CAS、outbox 或 BFF command adapters。
- `REGISTRY-STRATEGY-UNIFIED-CONTRACT-001` 是唯一 Strategy durable owner；draft/typed extraction 只產生建議，不直接 register、approve、advance 或另存已接受 StrategySpec。Extraction 經 Agora/Domain 既有 caller 送唯一 Registry。Registry 依賴已由上列現有 chain 傳遞，不新增會形成循環的前置任務。
- `MGMT-READ-001` 保留 BFF assistant / management read model / main owner。Transport 沿用既有輸入輸出、錯誤與 authenticated session contract；只能讀／跑 Management consumer regression，不能改其檔案或再加 BFF extraction / memory endpoint。若 endpoint 確有缺口，先以 genuine blocker 讓既有 owner 正式補最小 slice。
- `STRUCT-RETIRE-001` 保留 BFF 全域結構與 first-release source join；這三 task 不授予 BFF／ownership YAML／check_product_ownership 的寫入權。只有當新 task 真正改動其共用範圍時，才需既有 owner 正式 handoff／依賴，不能全部空等 STRUCT。既有 STRUCT / hosted acceptance 不因新 task queued 或 source-only tests 自動通過。
- 新 task 無 root `docker-compose.yml` / `docker-compose.control.yml` grant（Registry 正持有）；Search 隔離啟動配置用新增 `docker-compose.search.yml`。Parent 的 infrastructure / profile task 是唯一 root Compose 合併 owner，消費這份已測 overlay，不複製 retrieval 實作。

## SIMPLIFY-OPENCLAW-001

### 行為與退場

一般 user / Persona turn 的 invoke、stream、readiness answer probe 使用同一 HTTP request builder 和同一錯誤／usage normalization，走既有 Gateway `/v1/responses`。普通短訊息也使用這條路；刪 general-turn `_invoke_cli`、stdout result decoding、96 KiB argv 分支及重複 provider fallback。正常 turn 的 transport 選擇不得由 prompt 長度決定。

Gateway/cron administration CLI、訂閱 provider 登入依賴、`kernel_debug` 的現有獨立 read-only runtime 均不在 general-turn 刪除範圍。不能為了普通 turn cleanup 刪整個 Node/OpenClaw image 或擴大 kernel 的權限。

唯一 request envelope 必須保存已授權 agent、explicit model、tenant/actor、conversation session、messages/context/attachments、trace、總 deadline 及取消行為。Session key 必須來自 authenticated tenant + actor + conversation scope，不接受只有 operator 或 caller metadata session_id 的碰撞；驗證 `previous_response_id` 也不允許越界。Gateway HTTP shared credential 是完整 operator surface，不是產品 tenant 授權：Pantheon admission 與 restricted agent tool policy 不得省略。

最小 typed-extraction transport 能力與本 task 一起做：沿用同一 HTTP builder，可帶**伺服器核准且僅回傳資料**的 `emit_extraction` function schema / pinned tool choice；不得讓 product caller 任意提供 shell/tool definitions。保留 function-call arguments、name/id、response status、refusal/incomplete、usage；不執行該 tool 的 domain mutation。這是下一 task 的必要能力，不能把 `/v1/responses` 當作完整 OpenAI JSON-schema 支援證據。

Cron 部分保留 business saga / workflow schema。刪 `entries[0]` fallback；只有 `(job_id, run_id)` 完全吻合且 terminal 才可回 completed。優先採 pinned upstream 支援的 exact-run lookup/wait；若当前 pinned API 無 exact lookup，既有 bounded polling 只能找同一 run、找不到就 timeout/unknown，不能誤認其他 run 成功。`cron.run` 未回 run ID 必須明確失敗或 in-doubt，不能盲目 retry 提交又產生重複執行。共用總 deadline 下的每次 RPC、poll sleep 都要 bounded；不新增 Node sidecar。

### 精確檔案

現有可改：`services/openclaw-gateway-adapter/assistant_openclaw_provider.py`、`services/openclaw-gateway-adapter/main.py`（僅 invoke request/response contract 與 restricted structured data request）、`services/openclaw-gateway-adapter/test_main.py`、`services/openclaw-gateway-adapter/test_assistant_openclaw_provider_live.py`、`services/openclaw-gateway-adapter/tests/test_assistant_provider_runtime.py`、`services/openclaw-gateway-adapter/tests/test_prompt_injection.py`、`services/control-plane/cron/openclaw_client.py`、`services/control-plane/cron/test_cron.py`、`services/control-plane/cron/test_service.py`、`integrations/openclaw/integration.md`。

新增可改：`services/openclaw-gateway-adapter/tests/test_openresponses_transport_contract.py`、`services/openclaw-gateway-adapter/tests/test_structured_data_tool_contract.py`、`services/control-plane/cron/test_exact_run_wait.py`、`docs/deployment/evidence/SIMPLIFY-OPENCLAW-001/evidence.json`。

若 pinned Gateway 版本不能提供以上功能，不能偷偷加 direct vendor SDK。將 pin 更新交 parent 唯一 OSS version owner，傳遞實測缺口並等待合併；不得同時修改兩份 Dockerfile pin。最新版 / 路由登入身份的功能要逐項 capability probe，不用 release 名稱猜測。

### 驗收

1. 對短／超 96 KiB／多 turn／explicit non-default agent+model／attachment、不同 tenant 同名 session 做 HTTP mounted contract regression；正常 invoke/readiness/stream 不啟 subprocess。管理 CLI 仍可用，kernel scope 不擴張。
2. inject 400/401/403/404 disabled/429/5xx、DNS/TLS/connection fail、server disconnect、malformed/multiline SSE、response.failed/refusal/incomplete、[DONE] 缺失、partial data 後 cancellation；只能一個 terminal event，無假 done、無改模型或回 CLI。重試不得超 deadline，已可能被接受的具副作用 turn 不盲重送。
3. restricted tool schema 的 positive / invalid args / wrong tool / no matching tool / denied capability 都真跑 pinned Gateway；client tool matching 不等於 JSON args 語意正確，兩層各驗。
4. 固定同 agent/model/prompt/session setup 做 CLI base SHA 與 candidate HTTP 的 controlled replay；至少 100 requests，分開 cold/warm、TTFT / full p50/p95、error rate、input/output usage、subprocess count。輸出語意不要求逐字相同，授權與工具選擇、正確 terminal outcome 必須相同；無新增錯誤；full-turn p95 不大於 base 1.10 倍，同 token budget 平均 token usage 不增加 5%。不以未報 usage 當 0 成本。
5. cron 同 job 交錯 run、target 不在最近 5 runs、run_id 缺失、target late arrival、RPC 掛住、target failure/cancel、timeout 後晚到的結果；0 次將另一 run 當本次完成，0 次自動重送 cron.run。

## SIMPLIFY-EXTRACTION-001

### 行為與退場

在 source_ingestion 建立唯一 typed semantic extraction contract 和一個借用既有 adapter 的 client。用 intent / seed / lesson 的 task discriminant 描述結果差異，重用現有 enums / StrategySpec seed 契約，不另定 Registry 模型。輸出含欄位來源 span、缺欄位、unsupported / ambiguous / abstain、source identity、schema/model/prompt digest；confidence 只作待校準訊號，不能作授權或批准。

模型只能抽取供給的已核准資料；無 web/shell/execution tools，不讀完整 workspace、不上傳未被現有 provider 資料政策涵蓋的 SourceRecord。跨 tenant、redaction、license / as-of admission 在呼叫前完成。既有有效 JSON / 已知欄位 exact mapping 保持 deterministic；只有語意推斷交模型。

通過下列品質 gate 後，統一 `interaction_intent_classifier`、`StrategySpecSeedBuilder._infer_*`、research normalizer / production distillation 的語意猜測、trainer lesson rules。保留 domain validation / lineage / draft status / source rejected guards；刪 keyword dictionaries、Markdown headings/regex fallback、重複 `_infer_*` semantic implementation，不能把整份含契約檔案按 LOC 直接刪。沒有 runtime caller 的 distillation CLI 可以改用同一 interface 或經確認刪 CLI，不能留下第二個解析器。

### 精確檔案

現有可改：`services/source_ingestion/interaction_intent_classifier.py`、`services/source_ingestion/strategy_seed_builder.py`、`services/source_ingestion/agora_seed_bridge.py`、`services/source_ingestion/trainer_seed_bridge.py`、`services/source_ingestion/tests/test_interaction_intent_classifier.py`、`services/source_ingestion/tests/test_strategy_seed_builder.py`、`services/source_ingestion/tests/test_agora_seed_bridge.py`、`services/source_ingestion/tests/test_trainer_seed_bridge.py`、`services/research/strategy_spec/normalizer.py`、`services/research/strategy_spec/production_distillation.py`、`services/research/strategy_spec/test_normalizer.py`、`services/research/strategy_spec/test_production_distillation.py`。

新增可改：`services/source_ingestion/semantic_extraction.py`、`services/source_ingestion/semantic_extraction_client.py`、`services/source_ingestion/tests/test_semantic_extraction.py`、`services/source_ingestion/tests/test_semantic_extraction_admission.py`、`services/source_ingestion/evaluation/semantic_extraction_manifest.schema.json`、`services/source_ingestion/evaluation/run_semantic_extraction_eval.py`、`services/source_ingestion/evaluation/README.md`、`docs/deployment/evidence/SIMPLIFY-EXTRACTION-001/evidence.json`。

不修改 Registry、BFF、Governance、strategy_seed_store 或 distillation_controller 的 mutation contract；用已合併 owner capability 跑只讀／隔離正向 chain regression。

### 可重現 baseline / 品質、錯誤、成本與 p95

- 先使用已獲准的既有標註；不足時從固定source與已知owner contract建立versioned deterministic source-derived fixtures，獨立reviewer adjudicate關鍵/歧義案例，標示existing-labelled、source-derived或AI-assisted provenance。目標至少200個獨立holdout案例，至少80繁中、80英文，其餘混合；intent/seed/lesson都有正負/缺欄位/否定/複合intent/拒絕來源。按source-family+semantic duplicate group分割，時間holdout適用於有真實事件時間的資料；synthetic資料不能假裝有operational temporal proof。這允許worker自行完成可重現source功能驗收，不要求user另給200人工標註；缺真實operational corpus必須明列外推限制，不能把fixture quality當production effectiveness。
- 在第一次 candidate 評估前凍結 corpus/dataset digest、split IDs、labels、baseline source SHA、model exact ID/auth route、prompt/schema digest、decoding/max-output/deadline、價目或經核對 subscription usage 計算方式、hardware/concurrency。隱私 corpus 留核准資料位置；repo 僅保存 manifest、可公開 synthetic boundary fixtures、hashes 與 aggregate 結果，不能 commit runtime/private prompt dump。
- 舊邏輯從固定 base worktree 跑 baseline，新邏輯從 candidate worktree 跑 3 次 holdout；保存每次結果與 paired diff。模型即使 temperature=0 仍可能變動，不宣稱 bitwise deterministic。不得把 holdout 錯例放進 prompt 後繼續叫同一組 holdout。
- 硬 gate：所有驗收租戶/權限/retired source/side-effect injection case 0 次越權、0 自動批准／register；critical field 有來源支持率 100%；來源 ID/span 可定位率 100%；數值／缺欄位不得憑空補齊。refusal、incomplete、invalid schema、wrong tool、missing citation、timeout、cost cap、upstream down 是 typed failure / abstention，不進 accepted draft。
- 品質預設：intent macro-F1 >= 0.95 且不低於 baseline 1 percentage point；seed/lesson supported field F1 >= 0.95 且不低於 baseline；應 abstain 案例 recall >= 0.95。分語言／任務報告，禁止用 overall 平均掩蓋子群大退步。對語意歧義由獨立 reviewer adjudication，不能只讓相同模型自評。這些是 pre-registered go/no-go 門檻，不是已達到的測量值。
- 固定 <= 8k input tokens + <= 1k output tokens 的 bucket 預設 full extraction p95 <= 10 秒；每筆總 deadline <= 15 秒，包含 queue / capped single retry，超時不開新背景補答。更大輸入明確分桶或拒絕，不能靜默截掉決策證據。
- 成本不能拿舊規則的接近 0 token 費用做假「不劣化」。預設每已完成 case mean <= USD 0.05、p95 <= USD 0.10（含失敗/retry攤提），同時報每 1k cases 成本、abstention /人工補完率與成功正確 case 成本；實際價格用執行時官方費率或真實 billing metadata 核對。若現有 model 成本不合，調 validation cohort 上的模型/提示；不得看 holdout 降門檻。真正 subscription 費用未知時標 unknown，不以 0 通過 gate。

## SIMPLIFY-RETRIEVAL-MEMORY-001

### 可執行預設與選擇後必須實作

預設先用**既有 PostgreSQL native FTS + 必要 pgvector + 本地 embedding**，與 self-hosted Qdrant dense+sparse/native RRF 在同一固定語料、ACL/as-of與硬體budget比較。本task必須選一個、實作既有Search/Memory consumer、通過驗收及移除舊ranking；不能只交decision文件結束，也不能同時merge兩套backend為長期feature flags。

先測PG既有psycopg/store能否用FTS取代手寫BM25，中文斷詞與跨語不足由單一本地embedding+pgvector補足。既有Postgres owner/store不搬遷；rank只存derived Search資料，權限條件在SQL候選集合內，禁止只ANN top-k後filter造成隔離與Recall缺口；tiny SQL組合不等於另寫Python搜尋框架。Qdrant只在PG無法經最多兩輪bounded調整達到硬gate，或同品質下query CPU/p95下降至少30%且總運維成本未增加時選用。總成本必須包含新增service/image/backup/restore/patch監控及記憶體，不能只比較query latency。兩者同時過gate而無明確收益時選既有PG，減少新增維運。

FastEmbed / ONNX在Search runtime本地inference。多語dense候選從官方支持的`intfloat/multilingual-e5-large`與官方custom-model示例`intfloat/multilingual-e5-small`擇一；Qdrant的sparse候選可用`Qdrant/bm25`。release只釘一個已驗證model/ONNX digest與query/passage preprocessing，不同時維護多套embedding/ranker；所有選擇先在validation完成，holdout只驗一次已選配置。

所選PG/pgvector或Qdrant server/client/FastEmbed/ONNX dependencies 與 model revision/artifact digest 必須於實作時核对官方 metadata 並精確鎖定；不在本草案憑空宣告 latest 版本。建立 image / model cache 時可取得公開模型 weights，正式 retrieval 不下載或對外呼叫；配置 `cloud_inference=false`、不傳 `models.Document` 到外部 cluster、不設外部 inference key。Search request、corpus、memory、embedding 全留本地；外部 hosted file search 是另行 operator 決策，不是 fallback。

### 唯一 owner / 既有資料路徑

- 保留 `services/search/gateway.py` 作唯一 governed Search facade。所選PG/Qdrant retrieval table/collection 只是由已接受 SourceRecord / EvidenceBundle / Memory events 重建的 index，不是新的 canonical source 或 Memory write owner。沿用 index pipeline/scheduler watermark / receipt；不加另一 ingestion supervisor。
- `PostgresReadOnlyEvidenceRepository` 已明確 source-ingest 才能 write；維持此界線。保留 source canonical records、version/citation identity；刪的是自製 BM25 / cosine / token-hash mock / Python RRF ranking，不是來源、checkpoint、receipt store。
- 所有 indexing/search filters 由 authenticated context 與 server policy 建構。現有 `AccessContextBody` 的 roles/workspace/access/license 不能作為權限來源；body 只能要求更窄 scope。tenant/environment/persona/workspace/role/license/capital/as-of/available_time 在所有 dense/sparse prefetch 都先限制，回傳 owner revalidation 再防 stale ACL／retention；不能只 rank 全庫 top-k 後 filter。
- PG使用tenant-scoped SQL/RLS與非owner/non-bypass查詢角色；若選Qdrant，sensitive tenant使用可信identity映射的tenant-scoped collection，避免BM25統計混入別的tenant。Memory/source以record_kind區分；public/shared資料仍要明確授權projection。權限先決條件與owner revalidation兩者都不可省略。
- Memory canonical institutional/persona store、writeback review、scope、expiry/supersession、reuse accounting 繼續由 `services/memory` owner 掌握。共用 Search 只回 candidate IDs/version/source refs；Memory owner hydrate / recheck active+expiry+auth，保留 consulted metrics 的原本一致性。不得向 retrieval index 寫入批准、資金帳本或用 vector hit 更新 canonical memory。
- negative_memory 保留 exact source/strategy ID 和 deterministic retired/rejected block；語意相似由同 Search 回 candidate，不能用模型相似度覆蓋明確拒絕。若 generic Search 排名不足以達到高風險提醒 recall，拒絕替代，不偷調 rejection policy。
- persona_memory_bridge 只投影已授權 memory，仍透過唯一 memory owner 的 reviewed writeback。可刪自製語意 alias/substring 搜尋；Markdown rendering 在 provider 仍需該格式時保留薄 projection，不能把維持 contract 的渲染誤刪。Provider compaction 只適用 transient conversation；本 task 不改 MGMT server-side conversation history、不把 canonical history 搬去 provider。

### 精確檔案

現有可改：`services/search/retriever.py`、`services/search/hybrid_retriever.py`、`services/search/gateway.py`、`services/search/main.py`、`services/search/filters.py`、`services/search/index_pipeline.py`、`services/search/index_adapter.py`、`services/search/requirements.txt`、`services/search/Dockerfile`、`services/search/tests/test_governed_search.py`、`services/search/tests/test_governed_search_v2.py`、`services/search/tests/test_retrieval_rank_filter_cutoff_contract.py`、`services/search/tests/test_service_activation_contract.py`、`services/search/test_index_pipeline.py`、`services/memory/main.py`、`services/memory/institutional_memory_store.py`（僅 retrieval seam）、`services/memory/persona_memory_store.py`（僅 retrieval seam）、`services/memory/test_main.py`、`services/memory/test_institutional_memory_store.py`、`services/memory/test_persona_memory_store.py`、`services/source_ingestion/negative_memory.py`、`services/source_ingestion/tests/test_negative_memory_matcher.py`、`integrations/openclaw/search_gateway.py`、`integrations/openclaw/persona_memory_bridge.py`、`integrations/openclaw/test_persona_memory_bridge.py`。

新增可改：`services/search/pg_retrieval.py`、`services/search/qdrant_backend.py`（比較候選，只有勝出backend可進runtime）、`services/search/local_embeddings.py`、`services/search/model-manifest.json`、`services/search/backend-contract.json`、`services/search/requirements.lock`、`services/search/sql/retrieval_index.sql`、`services/search/tests/test_pg_retrieval.py`、`services/search/tests/test_qdrant_backend.py`、`services/search/tests/test_local_retrieval_isolation.py`、`services/search/evaluation/run_retrieval_eval.py`、`services/search/evaluation/retrieval_manifest.schema.json`、`services/search/evaluation/README.md`、`services/memory/search_retrieval.py`、`services/memory/test_search_retrieval.py`、`docker-compose.search.yml`、`docs/deployment/evidence/SIMPLIFY-RETRIEVAL-MEMORY-001/evidence.json`。

`docker-compose.search.yml` 僅本 task 的 Search override + 隔離PG/pgvector或Qdrant比較與所選test profile/volume/network，無宿主 public binding、外部 DSN、prod activation 或 root Compose rewrite。如實作需要 canonical memory event publisher 以外新 path，正式補最小 artifacts；不能擅自授權 memory store 大改或來源 mutation。

### 驗收

1. 優先用permissioned既有relevance labels；不足時建立versioned deterministic source-derived relevance fixtures，由獨立reviewer adjudicate關鍵/歧義案例，標記source-derived/AI-assisted provenance。目標至少200個held-out queries（繁中/英文/跨語至少各50，另含exact ID、來源時間、negative memory）與10k個固定index documents；可用有來源模版產生標記清楚的synthetic distractors，不得称人工標註或production效果。缺真實operational corpus明列外推限制，不能變成要求user人工標註的暗中前提。固定 model/artifact/query preprocessing/chunking、source watermark、所選PG/pgvector或Qdrant image/client version、hardware、index/search params 與 corpus digest。train/validation/holdout 以 source family split，只有真實operational timestamps可聲稱 time split，baseline 從舊 SHA 執行，candidate 不讀 holdout label。
2. Recall@10 >= 0.90 且不低於 baseline；nDCG@10 不低於 baseline 0.01；citation identity correctness 100%；negative-memory 已知拒絕 exact match recall 100%，semantic warning recall >= 0.95 且 false positive 不高於 baseline。必須單列中英／跨語／memory子群；不把 hash mock cosine 分數視作經校準 confidence。
3. 0 跨 tenant/persona/workspace、expired/archived/superseded/license/as-of 越界結果；同名 ID 不相撞；forged body scope 無擴權。ACL 改變／memory supersede 在 index lag 期間仍由 owner readback 擋住，不洩漏 result_count、citation、snippet 或 inaccessible ID。deny 场景不送文字到外部 network，空 result 與 unavailable 不混為成功。
4. 對 schema/dimension/model digest不符、retrieval backend down、model cache缺失、index未完成/過舊、owner讀取失敗、inference timeout、部份batch失败明確 degraded/unavailable；不回啟 hash mock、不跨tenant fallback。Index rebuild/restored snapshot 的 deterministic IDs/version、retry/idempotency、crash/restart checkpoint、delete/revoke tombstones、time cutoff 確實通過。
5. 含 local query embedding + 所選backend search + authorized hydrate 的 warm p95 <= 1 秒（10k fixed docs、concurrency 4，記錄實際 CPU/RAM/threads），cold-start另報並在模型未ready時不接假成功。至少 1000 replay requests，0 denied-boundary leak。記錄吞吐、index rebuild時間、disk/RSS、CPU-seconds/query、local infrastructure cost assumption；外部 inference/request 次數 = 0。官方模型未達硬體/p95門檻時在 validation 換單一小模型並重新freeze，不削弱繁中門檻。
6. 所選實際Postgres/pgvector或self-hosted Qdrant container跑 positive retrieval、restart/rebuild與兩tenant negatives；in-process fake只能做protocol negative，不能作完成證據。Memory回傳保持原 DTO、read accounting、reviewed writeback與server history。保留 source/search scheduler/Memory/auth現有回歸，不以 missing dep skip 通過。

## 避免永久雙軌的交付規則

每 task 在一個 source task 內記錄明確兩個可結束結果：`adopted_and_retired`（品質/安全/成本 gate 全過，唯一 runtime 路徑與刪除證據）或 `rejected_by_evaluation`（用最多兩輪bounded修正仍無法通過，保留舊唯一 runtime，從待合併 product scope 撤出 candidate，保存 harness + 被拒原因）。第二種不是原簡化的完成狀態；task必須報真正blocker與具體可review的取捨，不能以decision文件自行close或把候選被拒報成「取代完成」。

baseline 在固定舊 worktree / isolated replay runner，candidate 在乾淨新 worktree；不要把 old+new router flag永久merge。評估最多兩輪預先登記 candidate；看過holdout後改模型/提示必須準備新 holdout。若短期需要 shadow，只有本地 bounded replay，固定輸入count/timeout/資源上限與結束條件；沒完成不能交「flags都保留」作最終成品。通過後回滾以已驗證前一 git/image/model/index manifest 重建，canonical source/memory從未改owner，無需保留另一套 live ranker。

每個 PR 證據含 exact source head/base、實際 terminal commands/exits/counts、dataset/model/schema/prompt digests、metrics per slice、故障結果、runtime reachable-path與刪除清單，以及獨立 configured reviewer exact-head審查。先 local validation再commit/push/PR/checks/merge current dev；source完成和hosted驗收分開報告。

## 本次查閱的上游原始證據

- [OpenClaw OpenResponses](https://docs.openclaw.ai/gateway/openresponses-http-api)：HTTP agent/session與function tool契約；本設計只採文件明列能力，不假定 json_schema 格式相容。
- [PostgreSQL native text search](https://www.postgresql.org/docs/current/textsearch-controls.html) 與 [pgvector](https://github.com/pgvector/pgvector)：既有PG可執行預設，SQL候選filter/ANN品質須實測。
- [Qdrant hybrid Query API](https://qdrant.tech/documentation/search/hybrid-queries/)：native prefetch / RRF，應取代應用層融合。
- [Qdrant local inference](https://qdrant.tech/documentation/inference/)：client-side inference可在本地運行。
- [Qdrant filtering](https://qdrant.tech/documentation/search/filtering/) 與 [multitenancy](https://qdrant.tech/documentation/tutorials/multiple-partitions/)：payload filter與tenant統計隔離注意事項。
- [FastEmbed supported models](https://qdrant.github.io/fastembed/examples/Supported_Models/) 與 [官方custom model示例](https://github.com/qdrant/fastembed)：large為內建候選；small如需採用必須按官方custom-model配置與固定ONNX artifact驗證。
