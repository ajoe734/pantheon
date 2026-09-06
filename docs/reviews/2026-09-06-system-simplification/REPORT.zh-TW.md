# Pantheon 全系統簡化與 OSS 更新評估
查核日期：2026-09-06。這是唯讀技術評估與候選清單；查核期間未升級套件、刪除功能、派工或部署。文件的版本控制交付不代表候選簡化已實作。

## 結論
建議先做「刪掉重複責任、使用上游現成功能、把未證明價值的研究分支改為選用」，再用 LLM 合併語意理解。最有把握的減碼機會不需要換模型；LLM 替代則必須比較真實案例的品質、成本和延遲。

目標產品流程可以收斂為：**取得有來源的資料 → LLM 理解與提出結構化建議 → 確定性研究／回測工具產生證據 → domain policy 決定可否晉級 → 執行與對帳 → 回饋與可追溯紀錄。**
不應為了保留某個 OSS 名稱而維持整套服務，也不應把數值運算、授權、資金控制交給模型猜測。

## 查核範圍與基準
- 後端：GitHub 確認 dev 為 [471dc5391a0f9cbde54d51730891583043708e42](https://github.com/ajoe734/pantheon/commit/471dc5391a0f9cbde54d51730891583043708e42)。
- 前端：獨立 repository execute-plans，GitHub 確認 dev 為 [5d4f385284b44a30e10764426a47fd808a7ae3cb](https://github.com/ajoe734/execute-plans/commit/5d4f385284b44a30e10764426a47fd808a7ae3cb)。未在 Pantheon checkout 內建立前端目錄。
- 目前開啟的 Pantheon HEAD 是 be67218f2，與上述 dev 相比 ahead 6 / behind 294，且有大量未提交變更。本報告實作判斷以固定 dev 為準；使用者提供的當前 AGENTS／環境限制仍優先遵守。
- 涵蓋產品服務、BFF／Persona／OpenClaw、研究與學習、搜尋與記憶、資料來源、執行橋接、開發調度、部署配置與前端。重點實作做靜態追蹤／AST 比較，並非逐行人工審閱每個檔案。
- 後端盤點：49 份 requirements、195 筆宣告、44 個 Python 套件；全部 44 個透過 PyPI 官方 API 核對穩定且未撤回的版本。另列出 63 筆 Docker image/FROM、51 筆 inline 安裝／版本宣告。
- 前端盤點：94 個直接依賴，逐一比較 package-lock.json 與 npm 官方 latest。13 項相同，32 項落後 major、23 項落後 minor、26 項落後 patch。
- 主 Compose 有 68 個 service 宣告，52 個未設 profiles、16 個有 profiles；包括初始化與測試用途，**不代表 68 或 52 個常駐程序**。
- LEAN：比較 Pantheon gitlink 與 QuantConnect 上游 master，以及 fork 的自訂差異；未完整審查 LEAN/.NET 的傳遞依賴。
- 未檢查已部署容器的 SBOM／pip freeze、實際流量、訓練品質或雲端 live identity。版本宣告、lockfile、upstream latest、實際部署是四種不同證據。這不是 CVE 掃描或升級驗收。

## 全系統責任收斂
| 領域 | 建議保留的能力 | 優先簡化 |
|---|---|---|
| FE／Management／Agora | 結構化互動、結果與證據檢視、明確批准與失敗狀態 | 一套 server-state cache、一套對話協定；清理未使用依賴、舊入口和 mock/live 混合實作 |
| BFF／Persona／Router | tenant/auth、domain command 與 read model | 複製函式、自製 FastAPI 注入、test monkeypatch shim；模組可合併，write owner 必須唯一 |
| OpenClaw／LLM | 模型選擇、受限工具、session／streaming | 一般對話 CLI／HTTP 雙軌、argv 特例、重複 provider failover |
| Ingestion／StrategySpec | 資料授權、PIT、來源、版本、結構化策略草稿 | 多套 keyword classifier、Markdown parser、語意欄位猜測合併為結構化抽取 |
| Search／Memory | 授權後檢索、引用、歷史教訓與 canonical records | 自製 BM25／hash vector／RRF、重複 substring/alias 檢索；對話壓縮交 provider |
| Research／Learning | 可重現研究與有證據的模型／策略改進 | DSPy／TRL／FinRL／RLlib／QLib 分別用需求與效果決定保留；不全部常駐 |
| Registry／Evaluation／Promotion／Capital | 唯一 artifact 狀態、數值風險、批准、資金綁定 | 可收斂部署單元與共用契約；不可刪決策權責、版本或批准 |
| Execution／Broker／Reconciliation | 訂單語意、fencing、kill switch、broker truth、對帳 | LEAN fork 外掛化；共用可靠 queue／client 的提案須先證明 crash/replay 等價 |
| Telemetry／Incidents／Postmortems／Evolution | 可觀測性、事故、回復、證據與受控改進 | LLM 起草診斷／摘要；domain facts、事故狀態、reconciliation 獨立保存 |
| 開發工具／交付 | task/lease、review identity、不可變 release、回退 | 移出歷史 migration、approval 後續特例；不再為產品加入開發 task authority |

「合併」優先指刪掉重複責任和縮少部署單元；不是先把所有資料庫、worker、資金隔離混成同一個程序。可共用 Postgres／object store 平台，但 domain 資料權責仍要明確。

## 優先候選清單
A：已有上游／現成能力，完成契約回歸後可刪。B：需要 LLM 或檢索品質對照。C：需要使用量／資料移轉／產品取捨。

| 順序／類型 | 目前具體實作 | 簡化結果與退場條件 |
|---|---|---|
| 1／A | BFF main.py 與 personas/service.py 有 136 個 AST 完全相同的同名函式，重複定義合計 3,515 行 | 抽成唯一 owner，刪副本。另有兩份約 580 行的 provisioning evaluator 已分歧，須先定 canonical 行為。3,515 是範圍估計，非無條件可刪行數。[程式](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/personas/service.py#L890) |
| 2／A | core/app_factory.py:79 起自行 inspect.signature／Header／Query／Body 注入；main 動態 module class 與 read-store fallback 支援舊測試 | 改 native APIRouter／Depends 與現有 AppDependencies，測試用正式 dependency overrides，刪自製 dispatcher 與 shim。能力早已存在，不需等新版本。[程式](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/core/app_factory.py#L79)、[FastAPI](https://fastapi.tiangolo.com/tutorial/bigger-applications/) |
| 3／A | assistant_openclaw_provider.py:568 一般請求啟 CLI，:582 超過 96 KiB 改 HTTP，:906 已有 Responses SSE | 統一現有 HTTP 路徑，刪 per-turn subprocess、stdout parser、argv 特例及重複 fallback。必須保留 agent/model/session/trace/deadline；目前大訊息分支未完整傳遞這些值。cron／管理 CLI 與訂閱認證需求另行保留。[程式](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/openclaw-gateway-adapter/assistant_openclaw_provider.py#L568)、[官方 API](https://docs.openclaw.ai/gateway/openresponses-http-api) |
| 4／A | 多處自己組 SSE data/event/id、heartbeat、headers；consultation 有 Pydantic v1/v2 分支 | 選定 FastAPI 0.135+／Pydantic v2 基準，使用原生 SSE 和 v2 方法。保留事件歷史、tenant filter、Last-Event-ID、409 resync、[DONE] 契約與持久資料序列化語意。[SSE](https://fastapi.tiangolo.com/tutorial/server-sent-events/)、[升級](https://fastapi.tiangolo.com/release-notes/#01280) |
| 5／A | FE useV5Live 自建 Map TTL、promise/alive/loading/error；App 已裝 QueryClientProvider | 用 TanStack Query 接管 cache/refetch/cancel，刪通用 async state。保留 domain invalidation，query key 必須含 tenant/operator/environment；目前掛了 Provider，不表示頁面已廣泛使用 Query。[程式](https://github.com/ajoe734/execute-plans/blob/5d4f385284b44a30e10764426a47fd808a7ae3cb/src/management/pages/v5/useV5Live.ts#L19)、[官方快取](https://tanstack.com/query/latest/docs/framework/react/guides/caching) |
| 6／B | interaction_intent_classifier、strategy_seed_builder._infer_*、normalizer、production_distillation、trainer_seed_bridge 各自推論語意 | 一個 typed extraction 介面輸出 intent、StrategySpec、source spans、缺欄位與不確定性。刪經驗關鍵字與 Markdown 格式猜測；格式已正確的資料仍直接 deterministic mapping。先從有 live 呼叫的 seed builder 做。[程式](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/source_ingestion/strategy_seed_builder.py#L608)、[結構化輸出](https://developers.openai.com/api/docs/guides/structured-outputs) |
| 7／B | Search 自製 BM25、RRF、全量 cosine；向量開關實際建立 MockVectorEmbeddingBackend，使用 token hash | 只選一套真正搜尋後端：託管 file search 或合適 OSS 檢索。保留 facade、ACL、license、as-of、來源。測 Recall@k、引用精度、跨 tenant 隔離後移除舊 ranking；不能把 mock 當成熟向量搜尋。[程式](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/search/main.py#L355)、[檢索能力](https://developers.openai.com/api/docs/guides/tools-file-search) |
| 8／B | negative_memory lexical alias／institutional substring 搜尋／persona memory Markdown projection | 共用檢索工具，provider compaction 僅處理暫時對話。保留拒絕／退役紀錄、來源、scope、expiry、reviewed writeback；不能用模型記憶取代帳本。[程式](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/integrations/openclaw/persona_memory_bridge.py#L125)、[compaction](https://developers.openai.com/api/docs/guides/compaction) |
| 9／B/C | DSPy 主要最佳化 intent/tool routing；TRL/FinRL/RLlib/QLib 等有 dormant-smoke 與 stub | 先以 native tool calling＋固定提示＋核准範例比較效果。沒有被使用或無 measurable lift 的 pipeline 退到選用 research job／移除產品入口。保留原始 feedback/eval datasets。CPU softmax shadow learner 是實際另一條路徑，不可因 OSS dormant 一起誤刪。[啟用門檻](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/research-worker-gateway/main.py#L137) |
| 10／A/C | LEAN gitlink 是 2026-05-17 的 5ad0249；較上游 master 落後 206 提交，多 3 個自訂提交；差異僅新增 4 個 pantheon_algo Python 檔 | 將 Pantheon algorithm/bridge 作為外部 library／掛載檔，改用上游固定 SHA／image，降低維護整個 fork 的需求。先驗證 import、bridge events、runtime context 和真 LEAN replay。[客製差異](https://github.com/ajoe734/pantheon-lean/compare/f00d02be834b461557c61fd73146f46a8d4a6b86...5ad0249432459c119f26718007e083808ef7995d)、[Library](https://www.lean.io/docs/v2/lean-cli/api-reference/lean-library-add) |
| 11／A | QuantLib-Python meta-package、兩套手寫 BSM/CRR 與實際 QuantLib backend 共存；Ray 有舊 rollouts fallback | QuantLib 直接釘 core，選一套數值 pricing；Ray 遷移一套 new API stack 後刪版本分支。保留價格／Greeks golden cases，不讓 LLM 計算取代數值工具。[Ray](https://docs.ray.io/en/latest/rllib/new-api-stack-migration-guide.html)、[QuantLib metadata](https://pypi.org/pypi/QuantLib-Python/1.18/json) |
| 12／A/C | python-jose、passlib 在 backend 掃描只有宣告；FE @ai-sdk/react、react-markdown 無 src imports；三種 lockfile，CI 使用 npm ci | 先做乾淨 build/import 驗證，再移除直接未用依賴。移除 python-jose extras 時，產品仍需直接宣告 cryptography：JWKS、AESGCM 與簽章仍使用它；驗收必須確認 crypto auth/privacy tests 真正執行，不能接受缺依賴而 skip。保留實際使用的 ai types／Streamdown。確認 Bun 無其他 consumer 後只保留 package-lock。lovable-tagger 是 dev plugin，若退出該編輯流程可刪，不是線上依賴。[FE manifest](https://github.com/ajoe734/execute-plans/blob/5d4f385284b44a30e10764426a47fd808a7ae3cb/package.json)、[BFF requirements](https://github.com/ajoe734/pantheon/blob/471dc5391a0f9cbde54d51730891583043708e42/services/control-plane/bff/requirements.txt) |
| 13／C | FE Vite 以多個 stub／denylist 排除 mock、taxonomy、writeOverlay；多處 runtime mock/live 分支 | 測試／demo fixtures 從 product entrypoint 分開後，可刪 production mock resolver/overlay 及部分 build 隔離補丁。保留 fixture tests 和「不得展示假 live 資料」檢查；未證明零依賴前不能刪 guard。[程式](https://github.com/ajoe734/execute-plans/blob/5d4f385284b44a30e10764426a47fd808a7ae3cb/vite.config.ts#L13) |
| 14／C | 12 個過 sunset 的 BFF 410 handlers；cron 看最近 5 runs，找不到 run ID 改取第一筆 | 舊端點零流量後刪除，否則集中 tombstone；cron 改 exact-run lookup/wait，刪模糊 polling fallback。保留 deployment saga 和 policy。[詳細](architecture-findings.md) |
| 15／C | supervisor 正常 recovery 仍執行歷史 review migration；approval 後 task brief commit 又需要 approval carry-forward | 把一次性 migration 移出熱路徑；停止製造核准後的非必要 brief commit，待舊任務清空再刪例外。保留身份、reviewed SHA、lease/CAS/replay。LLM SDK 不會自動提供這些工程權責。[詳細](architecture-findings.md) |
| 16／C | MinIO server/init 維運；研究／training 全部跟 core 配置混在主 Compose | 對 MinIO 決定維護中儲存後端或託管服務，核對 S3／retention／資料移轉；profiles 收斂為 core/workers/research/management-ai/execution，只啟需要的組合。保留 durability 和 execution 隔離。[MinIO 上游](https://github.com/minio/minio) |

## OSS 版本重點與可刪實作
完整依賴逐列結果在附錄 CSV。以下「最新版」是 2026-09-06 查核的穩定 release／npm latest；它不是直接升級指令。

| OSS | 目前宣告／鎖定 | 查到的最新版 | 簡化價值／注意事項 |
|---|---|---|---|
| OpenClaw | 2026.7.1 | [2026.9.2](https://github.com/openclaw/openclaw/releases/tag/v2026.9.2) | transport／session／fallback 收斂；HTTP 能力部分原本已有。9/5 發布，9/6 尚不滿現有 48h soak；API key／訂閱路徑能力需分別驗證 |
| FastAPI | 多處完全未釘版 | [0.141.1](https://pypi.org/project/fastapi/0.141.1/) | 原生 SSE 取代格式／heartbeat boilerplate；實裝未知 |
| Pydantic | 未釘版／v2 ranges | [2.13.5](https://pypi.org/project/pydantic/2.13.5/) | 統一 v2，刪明確 v1 相容分支 |
| Uvicorn | 未釘版／ranges | [0.52.4](https://pypi.org/project/uvicorn/0.52.4/) | 與 ASGI baseline 一起鎖版；沒有已證明可刪的業務邏輯 |
| DSPy | dspy-ai 2.4.5 | [3.3.1](https://pypi.org/project/dspy/3.3.1/) | 先決定離線 optimizer 是否有收益；有則改現行 dspy package/API |
| TRL | >=0.8,<0.10 | [1.12.0](https://pypi.org/project/trl/1.12.0/) | 非單純 bump：processing_class 與舊 API、DistilBERT classifier 與生成式 DPO 不吻合 |
| MLflow | 3.11.1 | [3.16.0](https://pypi.org/project/mlflow/3.16.0/) | 保留一個實驗追蹤來源；沒有證據可直接取代全部 registry/governance |
| Ray | 2.55.1 | [2.58.0](https://pypi.org/project/ray/2.58.0/) | new API stack 遷移後刪 hasattr/env_runners/rollouts 舊分支 |
| vectorbt | 0.26.2 | [1.1.0](https://pypi.org/project/vectorbt/1.1.0/) | 新版支援 pandas 3／NumPy 2.4+；numpy<2、pandas<3 可在驗證後移除。[release](https://github.com/polakowo/vectorbt/releases/tag/v1.1.0) |
| statsmodels | 0.14.2 | [0.15.0](https://pypi.org/project/statsmodels/0.15.0/) | 數值回歸；未找到能因 bump 直接刪的特定補丁 |
| pyqlib | 0.9.6 | [0.9.7](https://pypi.org/project/pyqlib/0.9.7/) | 已落後一版；是否保留由實際 factor research 需求決定 |
| FinRL／imitation | 0.3.7／1.0.1 | [0.3.7](https://pypi.org/project/finrl/0.3.7/)／[1.0.1](https://pypi.org/project/imitation/1.0.1/) | 已是最新 PyPI 發布，不代表已啟用或維護狀態良好 |
| QuantLib-Python | 1.18 | wrapper 1.18；[core 1.43](https://pypi.org/project/QuantLib/1.43/) | 1.18 是舊 meta-package，依賴未鎖 QuantLib；不能說 core 只有 1.18。直接鎖 core 可刪 wrapper |
| NumPy／Python | ML images 多為 Python 3.11；numpy 有舊上限 | [NumPy 2.5.2](https://pypi.org/project/numpy/2.5.2/)；[Python 3.14.7](https://www.python.org/downloads/release/python-3147/) | 最新 NumPy 需要 Python >=3.12，不能一鍵全面更新。API runtime 不必等科學套件一起換 |
| React／ReactDOM | 18.3.1 | [19.2.8](https://registry.npmjs.org/react/19.2.8) | 升級與 Compiler 採用分開。Compiler 可減少部分效能 memoization，不表示所有 useMemo/useCallback 可刪。[Compiler](https://react.dev/blog/2025/10/07/react-compiler-1) |
| Vite | 5.4.19 | [8.2.2](https://registry.npmjs.org/vite/8.2.2) | 新版建置基礎；不會自動刪 app 邏輯。Node 要 ^20.19 或 >=22.12 |
| Vitest／TypeScript | 3.2.4／5.8.3 | [5.0.0](https://registry.npmjs.org/vitest/5.0.0)／[7.0.2](https://registry.npmjs.org/typescript/7.0.2) | 分開遷移測試／型別工具；Vitest 5 engine 需要 ^22.12 / ^24 / >=26 |
| Tailwind | 3.4.17 | [4.3.3](https://registry.npmjs.org/tailwindcss/4.3.3) | 使用 v4 Vite plugin 後可移除不再需要的 autoprefixer／PostCSS config 部分；theme 轉 CSS，保留自訂樣式語意與瀏覽器基線。[guide](https://tailwindcss.com/docs/upgrade-guide) |
| React Router／Zod | 6.30.4／3.25.76 | [7.18.3](https://registry.npmjs.org/react-router-dom/7.18.3)／[4.5.4](https://registry.npmjs.org/zod/4.5.4) | Major migration；目前未證明有因升級即可刪的特定 workaround |
| ai／@ai-sdk/react | 6.0.197／3.0.199 | [7.0.93](https://registry.npmjs.org/ai/7.0.93)／[4.0.96](https://registry.npmjs.org/%40ai-sdk%2Freact/4.0.96) | 新版 Node >=22；前端目前 ai 用在 types，react adapter 未用。先刪不用的，不為了用 SDK 另造一套 BFF 協定 |
| TanStack Query | 5.83.0 | [5.102.8](https://registry.npmjs.org/%40tanstack%2Freact-query/5.102.8) | 現有 v5 能力就能替代手寫 useV5Live，不需等 bump |
| ECharts／Recharts | 6.1.0／2.15.4 | [6.1.0](https://registry.npmjs.org/echarts/6.1.0)／[3.10.1](https://registry.npmjs.org/recharts/3.10.1) | ECharts 已最新；兩者確實分擔不同圖表，先盤點 ChartSpec 覆蓋才決定是否統一 |
| PostgreSQL | 16-alpine 浮動 tag | [18.6](https://www.postgresql.org/docs/release/18.6/)；16 維護版 [16.15](https://www.postgresql.org/docs/release/16.15/) | 可先維護 16 patch；升 major 不是簡化前提，不能由 tag 判斷實裝 patch |
| NATS／Redis server | 2.11-alpine／7-alpine | [2.14.5](https://github.com/nats-io/nats-server/releases/tag/v2.14.5)／[8.10.1](https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.10-release-notes/) | 沒有證據可因升級刪 outbox／fencing／重試語意；client/server 版本分開看 |
| MinIO | digest pin；mc 舊 tag／latest 並存 | [community repo 已封存](https://github.com/minio/minio) | 需要維護來源／替換決策，不是追最新版即可。選定後才評估刪本機 server/init |
| LEAN | fork 5ad0249，2026-05-17 | [上游 master 23b735d，2026-09-04](https://github.com/QuantConnect/Lean/commit/23b735d99a357807dc0df9f4c51d30f05fe0d277) | 差距 206 upstream commits。GitHub releases/latest 仍是 2017 tag，不能當現在引擎基準；以 tested SHA/image 為準 |

後端 195 筆宣告中，94 筆完全無版本限制、82 筆 range、19 筆 exact；27 筆、涉及 19 個套件的限制排除了最新穩定版。相容條件允許最新版不等於已安裝最新版。不要把所有 ML 套件塞到同一個環境；應建立核心 API 的共同約束，加上各 runtime 的可重現解析結果和 image digest，保留已存在的獨立開發工具 interpreter。

## 關於新 LLM 能力的採用邊界
結構化輸出／strict tools 適合替代語意分類、格式整理、工具參數生成；provider retrieval／compaction 適合減少搜尋與上下文 plumbing。這些能力有些已存在一段時間，價值在於現在能否取代我們的成本，不把它們一概稱為剛發布的新技術。[OpenAI 結構化輸出](https://developers.openai.com/api/docs/guides/structured-outputs)、[Claude 結構化輸出](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

JSON schema 合法仍可能內容錯誤或被拒絕。應處理 refusal/incomplete、來源缺失和語意驗證。MCP 可統一 tools 介面，但不會自動保留 tenant、授權或資料 residency。若選託管搜尋，必須先確認資料可上傳與檢索 filter 語意等價；若保留本地搜尋，也只選一套成熟後端。[MCP](https://developers.openai.com/api/docs/guides/tools-connectors-mcp)

可讓 LLM 解釋事故、起草研究計畫和分析報告；下列仍由可重現程式／帳本掌握：價格與 Greeks、回測與 PIT、risk limits、capital binding、order state、idempotency/fencing、broker reconciliation、kill switch、reviewed SHA、release/rollback identity。

## 交付順序與驗收
| 批次 | 內容 | 完成定義 |
|---|---|---|
| 第一批：既有能力減碼 | BFF/Persona 去重、native FastAPI 注入、Pydantic/SSE、Query cache、unused deps／lockfiles | API／auth／序列化／stream 契約相同；刪除清單落地；無永久雙軌 |
| 第二批：runtime 與 OSS 收斂 | OpenClaw 單一 transport；LEAN bridge 外掛化；core dependency locks；research profiles；MinIO 選擇 | 模型身份/session、真 LEAN replay、image/dataset identity、restart/crash 行為被驗證 |
| 第三批：LLM 替代試點 | intent＋StrategySpec 共用抽取、真正檢索、feedback examples 取代不必要訓練 | 以現有版本為 baseline、時間 holdout 比較品質／誤判／引用／成本／p95；通過後刪舊實作 |
| 第四批：歷史表面退場 | 410 routes、mock/live 生產分支、task/review migration、沒有有效用途的 dormant frameworks | caller／流量／舊資料移轉完成；必要證據封存，零 active tasks/leases 後才移除對應工具 |

每項工作的成果應包括：刪了哪些檔案／分支／依賴、少了哪些部署單元／版本 pin／fallback、保留哪些契約、品質與成本對照。用這些量測簡化，不用新增 adapter/task 數量衡量進展。尚未取得 runtime 基線，故不承諾減少多少百分比、多少台 VM 或固定週數。

LLM 試點至少覆蓋繁中／英文、否定、歧義、缺欄位、複合意圖、惡意來源、跨 tenant、時點限制。比較現有邏輯、單一現代 LLM、必要時才加 optimizer；必須保留拒答／澄清能力。provider 失效時回報不可用或進入已定義的安全流程，不無聲切回猜測並標記成功。

## 已做過的工作，避免再做一次
- 最新 dev 已把 BFF main.py 從舊工作目錄的 60,472 行減為 22,959 行，已有 AppDependencies、typed ports、router factories。本次候選是殘留副本與 shim。
- AST 掃描最新 BFF／orchestrator 未見 unconditional return/raise 後明顯 unreachable tails；不重開相同 deadcode 清理。
- V2 TaskStore 的小 head＋delta journal、explain-dispatch、產品／開發工具隔離與獨立 tooling interpreter 已存在。
- 前端已使用 Streamdown／AI Elements／ChartSpec，且已有 strict-live fixture 隔離；不要重新建立同用途渲染層或逕刪隔離措施。
- 部署權威文件、已提交版本與工作目錄有時間落差；本次不以歷史 hostname、文件敘述或 service 數量推定當前部署完成。

## 附錄
- [完整 94 項前端版本比較](frontend-dependencies.csv)
- [195 筆 Python 宣告比較](oss-requirements-current-dev.csv)
- [44 個 Python 最新穩定版與 Python 條件](oss-pypi-latest-current-dev.csv)
- [Docker 映像宣告](oss-images-current-dev.csv)
- [Inline 安裝宣告](oss-inline-installs-current-dev.csv)
- [後端 OSS 詳細證據](oss-findings.md)
- [架構與開發工具詳細證據](architecture-findings.md)
- [LLM／研究／記憶詳細證據](llm-research-findings.md)
- [BFF AST 重複明細](bff-exact-duplicates.json)
- [固定版本與方法紀錄](audit-baseline.json)

本次交付保存可供取捨與派工的審查文件及查核資料，沒有實作候選功能變更、升級套件或部署。後續跨元件實作依 repository 預設由 supervisor 協調，除非 operator 明確授權指定範圍直接實作。

