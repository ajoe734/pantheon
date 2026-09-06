# 首版單一架構 SA／SD 與 execution task 修訂

## 文件狀態：未簽署草案，停止派發

2026-09-06 02:38 UTC 再讀 canonical TaskStore 發現另一協作工作階段已正式接收並啟動 `REGISTRY-STRATEGY-UNIFIED-CONTRACT-001`，舊 Registry prerequisite 已被它 supersede。本文件四任務拆分沒有簽署、queue 或 admitted，不是該 worker 的生效 contract；不得重複派工。現行唯一生效方案為 `/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/dev-closure-20260906/architecture-resumption-sa-sd.md`。下文保留為技術補充與規劃建議，尚未落入生效 contract 的差異見 [協作與缺口交接](FIRST_RELEASE_CANONICAL_RECONCILIATION_20260906.md)。操作者已確認的單一 owner／首版不相容原則仍有效；文件草案不等於派工批准或程式驗收。

日期：2026-09-06。操作者已確認責任劃分，並明定第一版尚未上線、不做前後相容，取代的 API／重複實作同批退役。本文件簽章後不可覆寫；必要修訂另立版本。

## SA1. 範圍、基準與優先次序

完整目標仍是原始 20 項結構任务、12 個業務循環、Management／Management AI／Agora、dead／duplicate code、可維護性及 hosted exact-pair 驗收。原 `pantheon_current_full_gap_audit_2026-09-03` 的 SA、SD、EXECUTION_TASKS、TRACEABILITY 繼續提供完整需求；不得把本次三個 prerequisite 的完成當作整體完成。

本文件承接 `STRUCTURAL_RESIDUAL_SA_SD_20260906.md` 與 Registry V2 的全部正向功能、隔離、durability、readback、真實驗收要求。與操作者最新決策衝突的「相容路徑、維持舊 DTO／別名、上線後才退休」前提，以本文件為準；不是將原計畫整包重做或降低驗收。

來源基準為 Pantheon dev `471dc5391a0f9cbde54d51730891583043708e42`；execute-plans dev `5d4f385284b44a30e10764426a47fd808a7ae3cb`。任務執行仍須 rebase 當時的 current dev。共享 dirty checkout 不作實作工作樹。

Registry 舊 prerequisite 未交付的 WIP 已封存於 `/tmp/pantheon-worker-worktree-archive/registry-strategy-durability-prerequisite-001-20260906T015727Z-1801618`。其 spec／revision 第二權威、action 錯配、假 readback 不能採納；可保留經重新驗證的 transaction 等有用部分，不盲目恢復整個 patch／venv。

## SA2. 確認的結構根因

1. Registry WIP 將完整 spec／revision 同時交給 RegistryEntry 與 StrategyCommandStore；相同 PostgreSQL 不等於同一權威。
2. Strategy adapter 用 CreateDraft／RegisterSpec／CreateRevision 代替 submit_review／promote_paper／activate，並從 POST body 拼出 authoritative_readback。
3. Governance approval 路由接受 body actor／role；Registry advance 只檢 lineage 並可接受 caller approver／decision ID；Deployment Planner 接受 request approval／Registry object 或 local file。因此補 Registry GET 還不足以建立真實批准權威。
4. 既有 source ingestion、research、Persona、Agora 與 BFF callers 的 principal／DTO 沒有完整接線；兩條原 task 分支最後沒有 source gate 匯合。
5. 舊 task artifact 說明將 Runtime source 一概視為 hyphen 目錄不精確：`services/runtime-manager/service.py` 是 re-export，核心在 `services/runtime_manager/service.py`。wrapper 不等於第二份 state machine；但實際 mounted `/api/internal/v1/...` fallback 確須退休。

詳細 source/caller 證據見同目錄 `API_CONVERGENCE_RETIREMENT_DISCUSSION_20260906.md` 與 `ARCHITECTURE_DISCUSSION_HOLD_20260906.md`。這些來源證據不代表 hosted 漏洞利用或已通過測試。

## SD1. 唯一 owner 與分層

| 元件 | 寫入責任 | 不可持有的第二權威 |
| --- | --- | --- |
| Registry | 草稿／family metadata、validated artifact、不可變 spec／execution-bundle 版本、artifact-state、自己的 transaction receipt | ApprovalDecision、RuntimeBinding、caller-authored performance、另一套完整 spec aggregate |
| Governance | proposal／review／approval／revocation、其權限與有效性規則、decision receipt | 以別的服務或 request body 自稱批准的平行 ApprovalDecision |
| Deployment | 經真實 Registry／Governance 驗證的 plan、dispatch／compensation | request-supplied Registry／approval object 或 local file 當 production authority |
| Runtime | binding、deploy／pause／resume／replace／retire、實際執行回讀 | 以 Strategy ID 猜 runtime ID、缺 binding 仍回 executed |
| BFF／Agora／execute-plans | typed input、command admission、協調工作流、read projection、UI | BFF local strategy／approval store、fixture overlay、偽造 downstream_verified |
| 開發工具／交付工具 | 前者維護 TaskStore／supervisor／lease；後者建置與部署 exact pair | 不取得產品資料或 readiness 的權威 |

HTTP router 只解析、呼叫既有 auth 邊界、DTO／錯誤映射；application use case 管一個業務 transaction；repository 管選定持久 backend；domain policy 不複製於 router、test fake 或 transport。不得新增 universal dispatcher、全能 service locator、第二組 queue／cron／JWT verifier／TaskStore。

## SD2. 首版 API 與 command contract

### Registry

- 同一 RegistryService／repository 管所有 typed artifact；保留 strategy_spec 與 execution_bundle 的不同 schema 能力，不能因名字近似刪掉其中一種。
- 首版名稱草稿採 `POST /api/registry/strategies`，family read 採 `GET /api/registry/strategies/{strategy_id}`，允許欄位 metadata 更新採 `PATCH /api/registry/strategies/{strategy_id}`。metadata 與 validated spec 是不同 record kind；草稿不得填造 semver／lineage 以冒充完整 spec。
- typed 草稿參數更新採 `PATCH /api/registry/strategies/{strategy_id}/draft-parameters`，由 Registry owner task 實作 UpdateDraftParameters；BFF Domain task 只接此能力。草稿可有經 owner 核對的 source_registry_id，參數定義來自該 canonical artifact 的 schema／mutable policy，不另存一套完整 spec。操作綁 expected family revision、base draft digest、source registry identity/version，只接受已宣告名稱／範圍。名稱草稿尚無參數定義時回明確 precondition；必須證明有合法 schema 的草稿參數更新正向成功，不能一律 unavailable，也不能以 metadata writer 或任意 JSON 代替。
- canonical spec create/get/list 採 `/api/registry/strategy-specs`、`/strategy-specs/{registry_id}`、`/strategies/{strategy_id}/strategy-specs`。下一 immutable spec revision 採 `POST /api/registry/strategy-specs/{registry_id}/revisions`；父 registry_id、expected base digest、expected family revision、新 semver 與 schema/lineage 必須檢查。完整內容只存一份 canonical entry。
- execution_bundle 與 allocation artifact 沿 typed contract，以同一 Registry application policy／transaction 實作。通用 `/entries` mutation 不可為已由 typed contract 負責的 artifact 提供第二條可繞過 typed 驗證的入口；typed 種類經 generic mutation 必須明確拒絕。通用其他 artifact 能力與授權 read views 不因此刪除。
- 保留一套 artifact-state 規則；typed advance 呼叫共同政策。APPROVED 必須核對 Governance exact decision，不接受 approver hint／任意 approval blob。Registry 不以狀態變更冒充部署。
- 不採用 WIP `/strategy-commands/**`、其獨立 factory／backend selector／full spec/revisions map；不建立 alias。derived metrics 不提供 caller-authoritative update endpoint。

現有 `bff/command_adapters/base.py` 將所有非 GET 操作交給 `_post_json`，而 `command_executor.py` 固定送 POST；選用 PATCH contract 時必須先擴充這一套既有共用傳輸層，實際尊重 HTTP method、正規化 raw-token/Bearer 與傳遞 timeout，連同既有 executor／adapter regression 驗證。不得另造 Registry-only HTTP helper 或 POST compatibility alias。這是原 Domain 整合範圍中需精確前移的 slice，後續 Domain 消費已合併的同一傳輸實作；目前生效 Registry task 尚未宣告這些 artifacts，必須由現有 owner checkpoint／正式擴充後才能編輯。

### BFF 與真實業務動作

沿用已存在的 `POST /bff/v1/commands` 作單一 operator-command admission，不另建 queue。query/status API 與此 command contract 一致。以實際 action catalog 全量盤點，不限下列六項；同一操作的 `/api/v1/operator/commands`、通用 `/bff/actions/...`、resource-action aliases 與平行 dispatch 路徑，在 callers 同步修改後直接移除，不留 deprecated handler。其他不同業務能力的 typed routes 需明確分類，不因 URL 含 actions 就盲目刪除。

canonical status 為 `GET /bff/v1/commands/{command_id}`，由既有 command store scoped query 提供。現在唯一 GET 在 `/api/v1/operator/commands/{command_id}`，必須連同 trackingUrl、poll／recovery clients、response DTO 同批改用新 canonical family並刪除舊入口。main 目前 action／command receipt dual-write 與 deprecation payload 一併收斂；receipt 只記錄真實同一 command outcome，不新建 ledger。

| 使用者意圖 | canonical operation／owner | 禁止替代 |
| --- | --- | --- |
| 建草稿／改 metadata | Registry CreateDraft／UpdateMetadata，明確 expected family revision | submit_review 代替 create，或 metadata 代替策略參數 |
| submit_review | Governance 建立針對確切 artifact/version/digest 的 proposal，讀回 review lifecycle | CreateDraft、直接把 artifact 改 approved |
| promote_paper | Deployment 建 plan／驗證／dispatch；Runtime 建 binding 並讀回 | RegisterSpec、canary/live promote API 當 paper admission |
| activate | 廢除無目標的模糊 action；明確 DispatchDeployment(plan_id) 或 Runtime resume(binding_id) | 猜 ID、不存在的 start endpoint、CreateRevision |
| pause | Runtime 對明確 binding 暫停並讀回狀態 | 缺 binding 仍回 executed／paused |
| archive | Registry 退休未來 selection，保留 immutable history；活動 binding 的處理是明確 precondition 或另立 workflow | 默默終止資本執行或只改 UI status |
| update_params | Registry 修改草稿的 typed params，或依 artifact schema 產生新的 immutable child | 原地修改 published version、UpdateMetadata、任意 schema PATCH |

confirmation、MFA 與 business approval 是不同責任；不能為減少 API 數量把它們合併成一個無驗證布林。所有 advertised actions 均須真實正向 owner flow；缺能力要在正確 owner 完成，不能把整套功能改成 unavailable 後結案。

## SD3. Governance 與跨服務信任

沿用 `runtime_auth_inbound.validate_request_auth` 及現有 documented authority matrix，補上明確 strict JWT、nonempty issuer/audience、有效 exp、verified tenant／actor／必要 role/scope。其 synthesized actor/operator defaults、structured tokens、body/header fallback 不是身分證據。不得另寫 JWT engine，不修改真實 credentials／角色授權／帳號設定。

Governance proposal/review/decide/revoke/list/get 全部使用已驗證 principal；body actor／role／tenant 不得提升權限。統一既有 Governance write-authority 與 approval domain model 的重複 role lists，使用同一政策，不改變操作者原訂權限矩陣。收據、optimistic version、tenant/private scope 與 idempotency 必須 durable。

跨 owner 只傳 decision ID，使用 Governance 擁有的單一 typed decision reader／共同有效性檢查，再加各 domain 的必要 target 條件。由現有 Persona HttpGovernanceApprovalVerifier 與 Runtime deploy_authority 收斂，不新增第三份 generic approval validator。共同檢查 exact ID、tenant、target type/id/version、content/proof digest、decided/approved、revoked/superseded/expiry／conditions；domain-specific training fields 不強塞進所有 artifact。

Registry 記錄 verified decision reference/digest 與 checked-at provenance，不複製一份可寫 ApprovalDecision。Deployment／Runtime 在使用時重新驗證；Registry 的歷史 approved 不覆蓋後續 revocation。Deployment 移除 request object／local JSON authority fallback，test doubles 只能明確注入。

consumer transport 必須傳遞合法委派 principal 或明確配置的 service principal。人類命令與背景 service 的權限不能混同；重啟後的憑證由原服務認證機制取得，不能將 bearer token 寫入 journal／receipt／evidence，也不能自行生成「看似合法」的產品 token。環境缺少合法憑證必須報實際配置 prerequisite，不能繞過。

## SD4. Durability、identity 與成功判定

Registry 使用一個明確選定的 PostgreSQL repository／foundation CAS 機制；memory 僅 explicit injected test double，不提供 production memory selector、silent fallback 或獨立 Strategy selector。startup/readiness 驗 schema／driver／backend，liveness 不能冒充可用。

清楚區分 tenant+strategy identity、family optimistic revision、registry_id 與 immutable semver。唯一鍵與 private supplied-ID scope 在 DB 強制；builtin/system catalog 必須有明確 visibility／principal，不能把缺 tenant 的資料當作 public。保留有用開發資料；沒有刪除資料／volume 或實際 schema migration 的額外授權。

狀態與 durable receipt 在同一 transaction 提交；foundation store 各自開 connection 的連續 put 不是原子 transaction。優先擴充既有 transaction-aware primitive，不複製 generic CAS SQL。outbox 如採既有 prepare/commit/activate/reconcile，必須如實稱 recoverable protocol 並證明 crash windows，不偽稱單一 DB transaction。

idempotency key 的 lookup 身分不包含可變 request hash；hash 由 owner 正規化後計算並作衝突比較。tenant、actor、command、aggregate 綁定一致。重試查找與提交不得一邊 raw strategy ID、一邊 namespaced ID；receipt 不可 ON CONFLICT 覆寫首次結果。CAS 使用 caller/base version，不在 stale write 時取最新 row 偽裝 CAS。

accepted 需 durable command admission；committed 需 owner commit；readback_verified 需真實 scoped GET／原始 immutable version 與 receipt 核對。commit 後失聯不宣告 rollback，也不重做 mutation；重試回第一次 commit 的 exact version，即使已有新版。結果不得以 dispatch_path、request target 或 POST 缺欄位的 body 生成 downstream_verified。

## SD5. Execution tasks、範圍與順序

三個新的 functional prerequisites 不涉及 live／hosted mutation、credential administration 或 capital 操作。各 task 的精確 artifacts 以同批已簽署 packet 為準，超出 artifact 先 checkpoint／正式修訂，禁止偷擴。

| 任務 | 責任／順序 |
| --- | --- |
| GOV-FIRST-RELEASE-AUTHORITY-001 | 沿現有 Governance owner 實作 SD3；收斂 Persona／Runtime decision reader；Deployment 消除 caller/local authority。依 canonical done DOMAIN-WRITERS-001。 |
| REGISTRY-FIRST-RELEASE-OWNER-001 | 依上項，完成 SD1–4 的 Registry 與 Strategy adapter genuine Registry capabilities；完整承接旧 prerequisite 正向 durability 要求。舊 REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001 正式 supersede，不冒充 done。 |
| REGISTRY-FIRST-RELEASE-CONSUMERS-001 | 依新 Registry，完成精確 source ingestion、research、Persona coordinator 的 DTO／credential／readback 整合；不新建 writer。research 已屬 Domain corrective broad scope，本項只提早承接 caller slice，後續 Domain 保留其餘完整需求。 |
| OVERLAY-RETIRE-001 | 保留 ID、checkpoint 與 genuine external blocker；待三個 prerequisite 及可用 caller contract 真正完成後，才經既有流程恢復。修 BFF personas／strategies composition 與 auth transport，退休 overlays／可選 read-store writer。這不是偽造新的 canonical dependency edge。 |
| AGORA-CHAIN-001 | 保留 ID，完成 Workshop principal／DTO／Registry exact readback 與全部 Agora suggestion chain；不保留第二份 spec。 |
| CW → Journal → BFF Test → Router → Domain 五項 corrective | 保留 ID／已驗證成果／完整 scorecard。依新規範去除相容路徑與同一機制副本；Domain 整合 Governance／Registry 成果，處理其餘 action／Runtime／Ranking／Formula／Persona／Incident／Deployment 假成功。 |
| LOOP-TRUTH-001 → MGMT-READ-001 → FE-STRICTLIVE-001 → DEV-DELIVERY-001 | 保留 IDs；FE 在獨立 execute-plans 同步 command contract、typed UI state；Delivery 只修既有 exact-pair controller，原任務禁止 hosted mutation。 |
| STRUCT-RETIRE-001 | 使用原先尚未 materialize 的 ID，提前為首版 source retirement／acceptance gate，依新 Registry consumers、Domain corrective、DEV-DELIVERY 與 FE 等分支匯合點。兩條分支都完成才可到此，不能先部署再留相容層。 |

原四項未 materialize 中的三項 hosted tasks 保留完整要求，待合法 one-shot MFA-backed admission：DEV-RELEASE-HOSTED-001 必须依 STRUCT-RETIRE-001 與兩條 source 分支終點；L12-HOSTED-001 及 MGMT-AGORA-E2E-001 依同一已接受 hosted pair。原 STRUCT-RETIRE 的 hosted 證據核對要求由這些 hosted acceptance 及整體 completion audit 承接，不刪除。將 source 退休提前是操作者最新首版決策，不是縮減 full goal。

不要 supersede Overlay 來假裝補 edge：現行 resolver 不追蹤 superseded_by，會使整條原任務鏈失去依賴。不要將 canonical note 說成 scheduler dependency；現有外部 blocker gate 與新 STRUCT-RETIRE 真正 declared dependencies 各自明確。不得另做 cron／scheduler 或手改 canonical JSON。

## SD6. 退役清單與驗收

各 owner 在同一 execution wave 完成以下 retirement，首版 release gate 前零生產可達舊路徑：

- Registry 第二 full-spec/revision store／selector／factory、WIP `/strategy-commands/**`、production memory fallback、typed validation bypass、caller metrics writer。
- Governance body-asserted actor/role/approval、重複 role matrix／generic verifier、Deployment caller/local object truth。
- BFF 重複 operator command ingress／dispatch、local approval／strategy authority、fake authoritative_readback／path-based downstream_verified。
- Runtime 實際 mounted internal API aliases／缺 binding 假成功；保留真正唯一 canonical service，移除的 wrapper 與 package path 必須先做 import/caller 分類，不盲刪共享 value types。
- Runtime 的 internal mount 目前同時掛載 sponsor decision、deployment approve、rollback approve/reject/abort/list、kill-switch、command-state。Domain task 必須逐項安置到真正 owner 的 canonical API，更新所有 callers並驗證正向／拒絕／readback；不可只為移除 Runtime alias 而讓這些能力消失。Registry prerequisite 不負責擅自刪除整個 mount。
- 全部原始 dead/duplicate symbol disposition、test import migration、Journal 第二 writer/bootstrap、source-confidence 假 healthy、FE mock/fixture/local-overlay 生產可達路徑、過時環境說明。

STRUCT-RETIRE 沿現有 `scripts/check_product_ownership.py`、`scripts/component_boundary.py`、既有 BFF test architecture gate 擴充實際 AST/import/mounted-route／bundle 檢查，不另建平行 gate。208 duplicate groups／17 dead tails 與原 test universe 全量追溯；metadata 標 KEEP／PLANNED 不等於刪除或通過。獨立 frontend source/gate 成果由 FE task提供，Pantheon 不複製 execute-plans 原始碼。

每條要求要有 actual caller／owner／資料表／command／terminal outcome／next consumer／fresh readback 證據。使用隔離合成 tenant/data、專用 PostgreSQL、真 mounted service、fresh process restart／two-process CAS；涵蓋 missing auth/config/schema、cross-tenant/private collisions、divergent replay、stale version、commit failure／response lost／readback mismatch、approval revocation。不得用 dict fake、fresh instance 當 process restart、collection-only、skip/xfail 或總 passed 數替代完整 acceptance。

## SD7. 交付、安全與完成

supervisor 派工，worker clean current-dev branch/worktree 實作；bounded foreground tests、精確 artifacts staging、真實 author/task/reviewer trailers、push／PR／independent exact-head review／required CI／existing integrator merge/archive。source capability 的順序合併不是 product 上線；所有 callers＋retirement gate 通過前不得宣稱首版可用。

部署 environment 以當前 operator 指定的 `docs/deployment/vm-dev-staging-prod-management-plan.md` 與 exact manifest 為準，禁止 retired hosts／invented hostname。FE live/strict/safe-write defaults；沒有 hosted MFA authority 時不發 privileged packet、不執行產品／capital 寫入。

完成必須同时具備兩條 source branch、首版零重複權威／零舊相容路徑、同一 hosted FE/BFF identity、12-loop 全部 mandatory cases、Management／Agora 完整 journeys、restart/reconnect/replay／fresh reader。工具健康、task done、文件存在、單一 task passing 不作替代證據。
