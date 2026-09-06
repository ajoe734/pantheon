# Registry 首版統一架構與重複實作退役：討論稿

盤點時間：2026-09-06 02:05 UTC。狀態：**待操作者確認；不是已核准的 SA/SD、contract 修訂或派工指令。**

## 1. 已確認的原則與尚待決定的方案

操作者已明確要求：發現架構問題先停下來討論；若新 API 替代舊 API，舊的必須退役，不允許永久維持兩套相同責任的機制。最新前提是 **第一版尚未上線，不需要前後相容設計**。本稿據此修正；先前將問題當作既有上線產品升級的前提不再適用。

建議採用「首版唯一業務權威與統一 contract」，而不是讓既有路徑或 WIP 先入為主：

- 由 RegistryService 收斂負責 canonical StrategySpec、immutable version、artifact identity 與 artifact-state；重用正確的共用機制，不因舊程式已存在就保留錯誤責任或介面。
- 以同一套 durable repository／transaction／授權及驗證規則取代既有 production process-memory 實作；不是保留舊的薄弱內部實作。
- 移除 WIP 中另存完整 spec／revisions 的平行權威及重複公開路徑；舊實作中與統一 contract 重複的路徑也一併移除。不設 compatibility endpoint、deprecated alias、雙寫或過渡 fallback。
- 名称草稿、metadata、下一 immutable revision 是必須補足的正向能力；它們不得因撤回重複 store 而一起消失，也不得偽裝成 review、paper promotion 或 activation。
- 直接依首版業務責任定義 API／DTO／identity，前端、BFF、Agora 與服務端呼叫者同步修改。現有呼叫者是需一起修正的範圍，不是維持舊 API 的理由。不另列舊版客戶端支援期或舊版升級路線。

「同一權威」不是只能有一張表、一個 class 或一條 GET。重點是同一業務事實只有一個寫入責任、一套不變條件；不同查詢視圖與不同 artifact 能力不是理所當然的重複開發。同一 mutation 若有多個公開入口，仍須明確決定 canonical 入口與其餘入口的退出方式。

## 2. 來源與證據界線

- Pantheon 唯讀工作樹：`/tmp/pantheon-closure-audit-20260906.HKFCSg`，HEAD 與 02:01 的遠端 dev 均為 `471dc5391a0f9cbde54d51730891583043708e42`。
- WIP 已停止並封存：`/tmp/pantheon-worker-worktree-archive/registry-strategy-durability-prerequisite-001-20260906T015727Z-1801618/files`。未提交實作不能描述為已上線的新 API。
- WIP SHA256：service.py `f3614d1752c0519d73a5bd459c168b558b1f21986ca16613d0ca1c55797d0a74`；command_contract.py `576794734cc50d1c64124ee033133a184420241d275cc89857c2c319eb5986a7`；pg_store.py `f0220dcd100dff0b106e361f09fd4fe29846703659cf0c5cc9b762472d0f9576`；strategy_adapter.py `207795c7bfb051aedaecb5337931bbdf2303154b81e153e24efd6c731a6d19b1`。
- execute-plans 當前遠端 dev 是 `5d4f385284b44a30e10764426a47fd808a7ae3cb`，已以 git object 檢查。其本地 checkout `4fd6088…` 落後 46 commits，未拿本地舊版當作當前遠端證據，也未切換或修改前端工作樹。
- 以下是來源與掛載／呼叫關係證據，不是 hosted 流量、真實產品寫入、完整 runtime integration 或 12 循環完成證據。本輪未執行 provider／DB／產品 API mutation 或測試。

## 3. API 能力、收斂與退役對照

路徑均以 `/api/registry` 為前綴。B = baseline `services/registry/service.py`；W = archived WIP 同名檔案。

| 能力與入口 | 實際關係 | 建議處置／退出條件 |
| --- | --- | --- |
| `POST /strategy-specs`、`GET /strategy-specs/{registry_id}`、`GET /strategies/{strategy_id}/strategy-specs`（B423/454/464），對比新 `POST /strategy-commands/{strategy_id}/spec` 與 `GET /strategy-commands/{strategy_id}`（W1016/1068） | 舊路徑經 RegistryService；新路徑將完整 spec 存入獨立 aggregate。這是真正的權威重複。 | 訂定首版單一 typed spec contract，具備完整 schema、lineage、checksum、identity 與讀/list 能力。URL／DTO 不受舊版相容性限制；所有 callers 同步採用，重複入口與平行 spec/state store 同一交付範圍刪除。 |
| `POST /strategy-commands/draft`、`POST /strategy-commands/{strategy_id}/metadata`（W987/1000） | baseline 沒有等價的名稱草稿／metadata CAS 能力；不是完整已驗證 spec。 | 保留能力，於同一 Registry owner 定義獨立 record kind、允許欄位、identity 連結、CAS 及 receipt。最終 URL 待 API matrix 確認；不以完整 spec 假資料填補草稿。 |
| 新 `POST /strategy-commands/{strategy_id}/revisions`、版本 GET/list（W1049/1074/1080） | 新增下一 spec revision 能力合理，但 WIP 另存完整 revisions map。 | 版本內容必須成為／引用 canonical immutable RegistryEntry；退休獨立 revisions map。新 command 可存在，但不得產生第二份版本權威。 |
| 新 `POST /strategy-commands/{strategy_id}/metrics`（W1032） | 接受 caller 任意 metrics 寫入 aggregate，並非既有 SA/SD 所要求。 | 不採用此寫入權威。PnL／performance 由真正 execution／research 資料來源投影；不能將它與 immutable artifact evaluation_summary 混為一談。 |
| 通用 `/entries` create/get/list（B319/330/340）與 typed spec façade | 現有入口共用 RegistryService／RegistryStore，不是兩個獨立 store；但寫入入口的型別責任仍應統一。 | 保留其他 artifact 的必要能力。針對 StrategySpec 建立單一 canonical mutation 入口；泛型入口是否應拒絕已由 typed contract 負責的種類，須完成 caller／型別盤點後決定。不能因名稱含「舊」就刪掉其他 artifact 能力。 |
| `/strategy-artifacts` create/get/list 與 `/{registry_id}/mutate`（B506/519/534/553） | execution_bundle 與 schema-declared mutable parameter child revision；不是任意 StrategySpec 修改。 | 保留不同能力與其唯一 owner，不拿 mutate 偷渡 metadata／任意 spec revision。若另行替換，需單獨列明同等能力與退役範圍。 |
| `/entries/{id}/advance` 及 typed spec/artifact/allocation advance（B349/483/593/821） | 共用 artifact-state 狀態機。 | 同一狀態機只保留一份業務實作；typed 與 generic 入口的保留／退役需明確列項。這些操作不能被 `stage=draft/spec/revision` 的收據字串取代。 |
| `latest-approved`、`deployment-view`、`deployment-summary`（B373/385/396） | artifact 查詢與 deployment/runtime 投影。 | 保留 artifact-state 與 deployment-stage 責任分離。註冊規格／建立版本不等於 paper/live promotion；Registry 不接管部署權威。 |
| `/allocation-policy-artifacts` create/get/list/advance（B754/783/798/821） | allocation artifact 專屬驗證，底層仍共用 RegistryService。 | 不是本次 Strategy command API 的替代目標，不可為減少 endpoint 數量而刪除。 |

WIP 重複機制：pg_store.py:119 的 `registry.registry_entries` 與 :373 的 `registry.strategy_command_authority`；command_contract.py:465–470、:634–648 寫入完整 spec/revisions，:685–692 又有獨立 singleton factory。新增 transaction :409–449 僅提交 aggregate＋receipt，沒有 canonical RegistryEntry，不能因同樣使用 PostgreSQL 就宣稱同一權威。

## 4. 已追到的真實來源呼叫端

這些是來源中確認必須一起修改／驗證的 callers，不是假設有既有外部客戶端需要相容支援。清單用於首版整體正確性，不是保留舊 API 的理由。

| 呼叫端 | 來源證據 | 首版統一 contract／授權的整合範圍 |
| --- | --- | --- |
| Agora Strategy Workshop | `bff/agora/strategy_workshop/operations.py:145,164` 的 GET/POST spec；`runner.py:113,130`、`routes/versions.py:381`、`_admission.py:228` 呼叫它 | 需要保留 registry_id、strategy_id 與版本讀回。現有 transport :97–107 僅建立 Accept/Content-Type headers，不能假定自動帶入已驗證 tenant/actor token。 |
| Persona provisioning | `bff/personas/service.py:3895–3906` 建立並呼叫 coordinator；`persona_provisioning_coordinator.py:287,337,852–853,1269–1286` 建立 spec、讀回並進行 artifact approval | 不只 Agora。名稱、identity、approval decision 與 monotonic replay 是原流程的一部分。transport :710–750 另需核對共用 HTTP helper 的 credential propagation。`main.py:9383` 是 compensation 路徑，不應誤報成正常 create 掛載證據。 |
| Source ingestion distillation | `services/source_ingestion/distillation_controller.py:137,151` GET/POST spec；實際使用 :340,372,373；Compose :313 有 controller command | URL 或 auth 變更會影響來源蒸餾的 create-or-adopt/readback。當前這兩個 HTTP 函式沒有傳 Authorization；不得用 body tenant 當作登入證明。 |
| Alpha replication controller | `services/research/alpha_replication/replication_controller.py:56,175` 讀取 approved spec list；Compose :361 | 需要 artifact_state=approved 的版本列舉及 inline spec envelope，不能改成只讀 draft aggregate。 |
| Alpha revalidation worker | `services/research/alpha_replication/revalidation_worker.py:364,419` 讀取確切 spec entry；replication_controller :210 建立 worker | 保留不可變 identity、tenant lineage、approval 與實驗重放關係；其 GET 目前無 Authorization。 |
| Deployment／Runtime consumers | `services/deployment/promote_pipeline.py:301,322,332,589` 讀/advance StrategyArtifact 與更新投影；`services/runtime-manager/deploy_authority.py:245` 讀 artifact proof | 這些讀寫 execution artifact，不應被「StrategySpec 新版」順手刪除。paper/live 的真實責任不在 CreateRevision。 |
| execute-plans 前端 | current dev `src/lib/stateMachines/index.ts:18` 將 submit_review 定義為 review_workflow；`src/lib/v3/status.ts:71` 為 approved→paper；`src/management/pages/StrategyDetail.tsx:459` 使用 promote_paper | WIP 將 submit_review→CreateDraft、promote_paper→RegisterSpec、activate→CreateRevision，與業務語義不符。前端 source 搜尋未見直接呼叫 Registry 的字串，不等於已完成所有 SDK／動態 URL 審核；BFF action 行為仍須保持正確。 |

## 5. 必須在恢復實作前定下的邊界

1. 公開 API：依首版業務模型凍結唯一 contract／DTO／route matrix，明列每項能力、唯一 writer 及刪除項目；不再討論保留舊版或提供相容期。
2. Identity：`tenant + strategy_id`、草稿 identity、`registry_id + immutable version` 的關係；保留 stable references，不把 aggregate CAS counter 當作 semver。
3. 授權：操作人與服務間 principal、tenant、actor、role/scope、builtin/system artifact visibility 的真實信任契約。沿用既有 verifier；缺少 claim／credential 不能以 header/body 或合成預設值補上。
4. 狀態責任：草稿、提交審查、核准、paper promotion、activation 分別呼叫哪個既有 owner。必須凍結 action→owner→合法 transition→receipt/readback matrix。
5. 退役範圍：每一條被替代 route、backend selector、store、fallback、設定、測試與文件都有具名去向及刪除驗收。無相容期、無雙寫過渡；舊測試應依正確業務契約重寫，不以維持舊測試綠燈為理由保留重複設計。

新的 caller 盤點顯示，僅交接 Overlay／Agora 不足以宣稱首版 API／auth 整合完整。Source ingestion／research 的必要變更不在目前 Registry task 的 26 個 artifacts；**此處只是 scope 影響證據，未自行新增 artifact、task 或修改 dependency。** 討論確認後應一次規劃完整跨元件修改，不能用相容層避開必要工作。

## 6. 修訂方案應具備的驗收與交付次序（尚未派工）

- 先凍結 canonical contract、caller matrix 與 retirement manifest，再實作唯一 owner。
- 先證明草稿、metadata、valid full spec、immutable revision 全部正向可用，並證明 scoped auth、原子 state＋receipt、CAS、同鍵 replay、原始確切版本讀回、真 PostgreSQL fresh-process／two-process 行為。只通過既有測試或全部 unavailable 不算完成。
- 完成每個實際 caller 的成功／拒絕／重試 integration。不能只改服務端、放寬 auth 或用假 readback 維持表面成功。
- 首版部署前完成 schema 初始化、內建資料 identity 一致性與 rollback-safe release；不規劃舊版客戶端或舊版產品資料的相容遷移。開發資料的清除不因尚未上線而自動取得授權。來源交付與 hosted 接受分開記錄。
- 同一交付範圍清除被替代的 route／store／fallback，驗證退役 route 不再掛載且不能再寫；針對動態載入與實際 callers 補 runtime evidence，單純 rg 無結果不能證明全部退役。
- 恢復派工與 scope 改版仍須走既有正式工具及 worker/reviewer 流程；不建立新 cron 或另一套控制系統。

## 7. 暫停與文件完整性

02:01 canonical readback：REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001 為 owner Human/Ops、reviewer Antigravity、generation 2、status in_progress、26 artifacts；runtime 沒有此任務 worker。這是現行設定下的人工保留，不是正式 blocked lifecycle，也不是完成證明。未恢復派工。

原 Registry V2 SA/SD SHA256 仍為 `a028f255638346bdeb050d46d3daf7fdc86c141cb7405ea222342f446dde6a3f`；原 residual SA/SD 仍為 `6ec3b02f78435f26e48f491f8f86d593771cab9c115e7fb5745d94930bd064aa`，本輪未修改已簽署文件。

完整目標仍包含原 20 項結構任務、12 循環、Management／Agora、dead／duplicate code 與實際 hosted 驗收；本討論稿不將成功定義縮減成 Registry 修復。
