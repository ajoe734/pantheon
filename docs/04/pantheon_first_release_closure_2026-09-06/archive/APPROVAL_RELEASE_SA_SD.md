# 批准權威前移、文件正式交付與首版 source acceptance

日期：2026-09-06。此文件簽署後不可覆寫；後續修訂另立版本。

## 1. 操作者指示與範圍

操作者要求將「批准權威與發布前的完整檢查」完成，並追問文件是否 commit／push／merge。已如實回報原 audit 文件未追蹤、後續 SA/SD 與報告仍在 Git 外，均不能算正式交付。對新發現的功能相依環，操作者已同意「將現有 Domain 的批准權威修復提前，維持單一實作，再接回原任務鏈」。本文件落實該確認，不再另立一套 Registry、Governance、caller framework 或 release controller。

完整目標仍為原始20項結構工作、12個循環、Management／Management AI／Agora／OpenClaw、dead/duplicate code、單一資料權威與可維護性，以及同一 exact FE/BFF pair 的 hosted journeys/restart/rollback。第一版未上線：callers 同批修改，被取代 API／writer 同批退役，不保留前後相容層。這不授權變更真實帳號權限、產生產品 token、hosted MFA、自動批准、live trading 或資本操作。

基準為 Pantheon dev `471dc5391a0f9cbde54d51730891583043708e42` 與 execute-plans dev `5d4f385284b44a30e10764426a47fd808a7ae3cb`。每項實作／審查／交付仍須重新對齊當時 current dev，不能用本文件的舊 SHA 當部署證據。共享 dirty checkout 不作實作工作樹。

## 2. 已核對的根因及唯一責任

Registry successor `REGISTRY-STRATEGY-UNIFIED-CONTRACT-001` 為唯一在執行的 Registry task。它的原本正向 draft／metadata／spec／immutable revision、strict identity、atomic original receipt／CAS／replay／fresh readback 全部保留。先完成這些合法能力不必等待 Governance；但在 genuine approval reader 未就緒前，APPROVED 必須 fail closed，不能信任 caller approver／decision_id hints。

目前 Persona coordinator 的真實 provisioning 流程會經 Governance propose/review/decide，再 Registry advance，才能繼續。Overlay 正向驗收需要此能力；舊排序的 Domain corrective 卻經 Router→Test→Journal→CW 依賴 Overlay。將批准修復留在 Domain 尾端，會形成真實功能相依環。不是靠移除測試、fake approved 或把 task note 說成 dependency 可解。

批准權威精確 slice 前移為 `GOV-APPROVAL-AUTHORITY-PREREQUISITE-001`，只改同一套既有 Governance owner、共同 reader 與 Registry／Persona／Runtime／Deployment 的批准邊界，依 Registry successor 完成後執行。Domain 原 task ID／完整後續工作保留，消費這個已合併能力，不重新實作。Registry approval 接線是新增明列的後續整合 slice，不把 Registry 原本欠缺能力倒給此 task。

| Owner | 唯一寫入責任 |
| --- | --- |
| Registry | draft/family metadata、validated artifacts、immutable versions、artifact-state、自己的 receipt |
| Governance | proposal/review/approval/revocation 與其授權、有效性、durable decision receipt |
| Deployment／Runtime | 驗證真正 owner references 後的 plan／binding／dispatch／執行狀態 |
| BFF／Agora／FE | typed command coordination、經驗證的 read projection；不另存可寫批准／spec truth |

## 3. 批准權威 SA/SD

### 3.1 Governance 入站與唯一政策

沿用 `services/runtime_auth_inbound.py` 的既有驗證器，明確 strict JWT、configured expected issuer/audience、有效 exp 及 verified nonempty actor/subject、tenant、必要 role/scope。不要複製 JWT engine。Body/header actor／role／tenant、合成 default operator 或 structured token 不能作授權證据。

對 mounted proposal/review/decide/revoke/get/list/latest-approved 全量接線。保留既有 risk-role matrix，包括經真正授權的 low-risk automated_gate；不能直接套僅服務 freeze/rollback 的角色集合而刪掉合法能力，也不能新增真實帳號 grant。Governance `write_authority.py` 與 control-plane `approval_decision.py` 的重複 role lists 收斂到一份政策，其他入口委派使用。

Command admission／decision state／原始 receipt 的 tenant/actor/target/idempotency/request hash／expected base revision 綁定一致；實際 PostgreSQL state+receipt 原子 transaction 與兩程序 CAS，沿用並聚焦擴充 foundation primitive，不能連續多 connection put 就稱原子。重試返回第一次 commit 的確切 outcome；相同 key 不同 request 衝突，receipt 不覆寫。Outbox 若不是同交易，必須明確使用既有 recoverable protocol 並驗 crash windows；audit 失敗不能吞掉後宣稱完整成功。

### 3.2 一個共同 decision reader

使用 `services/governance/approval_authority.py` 收斂既有 Persona HttpGovernanceApprovalVerifier 與 Runtime deploy authority 的一般 reader／validity。不要載入 Governance main／啟動 DB 的副作用，不再增加第三份一般 verifier、另一 HTTP 框架或可寫 ApprovalDecision replica。沿既有 HTTP/auth transport；只有一個 exact-ID reader 與 typed canonical evidence DTO。

共同檢查 ID、tenant、target type/id/version、適用 content/proof digest、decided/approved、revoked/superseded、expiry、conditions。各 domain 保留確有必要的 training/deployment target predicates，不將 training session 欄位強塞所有 artifact。每次使用重新核對有效性，不能以歷史 Registry approved 抵銷後續 Governance revoke。

Registry advance 只接受 genuine verified decision reference，保留 checked provenance，不信任 caller approval blob／approver。Deployment 移除 request-supplied Registry/approval object 與 local JSON production authority，讀真正 owner references。Persona、Runtime、Deployment 都消費同一 reader；test doubles 只能明確注入，不能是 production selector／fallback。

### 3.3 Caller 與交接

背景服務使用合法配置的 scoped service principal，人類命令使用真正委派 principal；不得把 body automated_gate 當認證。憑證缺少時回具體配置 prerequisite，不硬編碼 token、不自簽產品憑證、不將 bearer 存入 journal／receipt／evidence。

本 authority task 不改 BFF Persona coordinator／composition（屬 blocked Overlay）、Workshop（屬 Agora）或 source/research callers（屬後續 Domain）。它必須交付可用的 typed HTTP/auth/DTO/decision/readback contract，並以真 mounted isolated service 驗證。接線的正向完整 provisioning 由原 Overlay 在此能力完成後繼續，不能先用假的批准通關。

### 3.4 驗收矩陣

正向必須涵蓋合法 principal 的 propose→review→decide→exact GET、每個既有 risk-role 的有效流程、low-risk automated gate、重啟後原 receipt、Registry APPROVED→Deployment plan／Runtime 使用時驗權及 Persona domain predicates。測試用獨立隔離 PostgreSQL 與合成 tenant/principals，不連現有業務資料庫、不作真 hosted/provider/capital mutation。

負向必須涵蓋 missing/invalid claims、issuer/audience/expiry、body/header升權、cross-tenant/private supplied ID、錯 target/version/digest、revoked/superseded/expired/conditions、owner missing/HTML/malformed/unavailable、兩人決定競爭、stale CAS、同 key 異 request、commit/receipt/outbox failure、response loss 後原 receipt replay。Registry 不能在拒絕時變 approved，Deployment 不能退回 caller/local blob；revoke 後新的 execution 使用必須拒絕。

所有測試是有界 foreground，必須收齊 terminal exit/count、exact source、隔離資源身分。dict fake、fresh instance 代替 fresh process、collection-only、skip/xfail、總 passed 數或 JSON matrix 自稱 implemented 不等於驗收。

## 4. 三項正式 tasks 與相依

| Task | 調整與前置 |
| --- | --- |
| DOC-FIRST-RELEASE-PLAN-DELIVERY-001 | 補原 PLAN-ADMIT 的文件未入庫缺口；依 canonical PLAN-ADMIT-001，但不把 predecessor done 當文件已存在。純 docs，獨立可執行。 |
| GOV-APPROVAL-AUTHORITY-PREREQUISITE-001 | 上述精確 authority slice；依文件入庫 task、Registry successor 與 canonical DOMAIN-WRITERS-001。文件實際合併後才實作；與 Registry 序列化，不依 Overlay／Domain corrective。 |
| STRUCT-RETIRE-001 | 原尚未 materialize 的 ID，依 docs task、authority slice、Registry successor、Domain corrective、DEV-DELIVERY；是實際 canonical source-join，非另一 release controller。 |

Overlay ID／checkpoint／genuine external blocker 不替换、不假 done；Registry 與 authority slice 真正交付前保持阻擋。若現行正式 tooling 不支援追加 dependency edge，維持既有 authentic external capability hold，清楚標註它不是新的 declared edge；不可手改 canonical JSON、偽造 owner lease 或 supersede Overlay 導致所有下游依賴失效。

Domain 原大範圍已含 Governance／Persona／Runtime／Deployment，但本文件將其中批准責任明確前移；其 reviewer 必須確認 downstream 只整合一套已完成 owner。其他 domain actions、command ingress/receipt retirement、source/research caller、Runtime internal capabilities 等完整要求不刪。未宣告 artifacts 必須先 checkpoint／正式修訂，不能以 broad wording 偷擴 scope。

正式範圍分工不是只有 note：在 Domain 仍 todo/blocked 時，透過既有 artifact-contract 把三個有重疊的 service-wide patterns 改成未移交的現有精確檔案／非重疊子目錄，移除前置切片的精確 files 與暫由它負責的 compose settings。原其他需求和 signed acceptance／dependency 不改；Journal 的 decision-journal／migration責任保留。前置能力交付後，若 Domain 的其餘整合确需修改共享檔案，必須正式 re-add 該確切檔案與理由，且只消費／接回既有能力，不重做批准 writer。Registry shared transport 的獨立 scope checkpoint／修訂由現有Registry協調線處理，不包含在本次批准切片移交。

新增 tasks 的實作／審查人員沿用目前正式工作線的角色分工；2026-09-06 03:35 回讀 Domain 已正式指定 Codex2 reviewer，對應協作紀錄 `operator-worker-role-preference.md`。配置名稱不代表認證／額度正常，不冒名或轉移舊批准。若新 reviewer 缺少合法登入，必須誠實保留 review blocker，不自動降級成免審查。該人員偏好不變更架構或驗收。

## 5. 文件入庫：具體交付規則

原始 audit 來源：`/tmp/pantheon-current-gap-audit-20260903/docs/04/pantheon_current_full_gap_audit_2026-09-03/` 的 INDEX、REPORT、SA、SD、TRACEABILITY、EXECUTION_TASKS 及歷史 tasks.json。補充來源為 `/tmp/pantheon-archive-reconcile-prerequisite-20260905.PrI7ms/` 中20個 Markdown 文件；現行 Registry 方案來源為 `/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/dev-closure-20260906/` 的 architecture-resumption-sa-sd.md、operator-architecture-confirmation.md、operator-worker-role-preference.md、report.md、overlap-audit.md；本文件亦須入庫。

目標沿 repository docs/04 convention：原六份 audit 文件與歷史 tasks.json 放回原 `docs/04/pantheon_current_full_gap_audit_2026-09-03/`；新的單一入口為 `docs/04/pantheon_first_release_closure_2026-09-06/INDEX.md`，整合可閱讀的 current SA/SD、execution ordering／gap status、retirement及hosted需求追溯，附來源快照與 SHA256 manifest。

Snapshot 必須明確標註 source capture time／hash／baseline／分類（active signed source、superseded signed source、rejected draft、historical finding），保留原有有效需求與已簽 bytes。不可把原 four-task 未派草案改稱 admitted；不可覆寫 canonical packet／archive evidence；也不可把過時環境／暫停紀錄当現在 truth。Current 文件使用 repo-relative links，audit snapshot 的舊 absolute refs 可保留作歷史但需在索引提供新位置對照。不要保留兩份競爭的 current plan；快照是歷史證據。

只納入本範圍 Markdown／歷史 tasks catalog／source-hash manifest 與 task-scoped evidence。不要批次搬整個 tmp 目錄，不複製 .env、私鑰／token、queue／canonical task JSON、runtime logs、venv、live DB、未審查 patch 或可重放 signed packet。若某来源文件有秘密，先記錄阻擋與去識別化 derivative，不將秘密入庫，不假稱 derivative 仍具原簽章。

驗證所有必要來源被追溯、相對連結及 schema/hash 一致，原20項／12-loop／Management／Agora／hosted需求零遺漏，status清楚區分 planned/admitted/running/review/merged/hosted accepted。最後 clean task branch、精確 staging、required subject/trailers、commit、push、PR、獨立 exact-head review、required CI、canonical integrator merge dev，交付 PR／merge SHA。任一步未完就不能把文件交付記成 done。

## 6. 首版 source retirement 與共同驗收

依首版不相容決策，原「hosted 後才刪 compatibility」改為 source retirement 完成後才進 hosted；不是刪除原 hosted 證據要求。STRUCT 沿既有 ownership/import/test/route gate 擴充真實檢查，不建立 checker framework。

它必須核對17 dead tails、208 duplicate groups、216 unique test files 的逐項處置／caller/import/mounted-route證據，去除非允許的 copied bodies／old aliases／fallback writer。獨立 value type／import re-export 不自動算重複業務實作；正當保留要經審查，不能用 KEEP/PLANNED metadata 冒充 retirement。禁止刪測試／削弱 assertions、通用 fake、global main/sys.path耦合替代完整驗收。

實際 mutation method/path 唯一性不能先 set 去重再判無重複；第二 owner／retired writer／漏列 mounted mutation 必須讓既有 gate negative tests失敗。BFF POST/GET `/bff/v1/commands`、tracking/poll/DTO、receipt收斂與 FE同步；Runtime舊 internal mount 的 sponsor／approval／rollback／kill／command-state要有真實 canonical替代，不能連功能一起刪。其他 domain owner source缺陷回原 owner正式修復，不以本task七項artifact偷擴全repo。

必須對 actual owner-backed Registry／Governance／Persona／Deployment／Runtime 跑正負整合與 durability/restart/two-process CAS/replay/tenant/unavailable。建立全部12 loops、Management／Agora／OpenClaw 的 source→consumer readiness matrix，完整保留原 SD §12 與每項 mandatory case。FE取證在獨立execute-plans，不複製到Pantheon。

既有 DEV-DELIVERY 必須證明整個 exact FE/BFF pair gate 在公開切換前、invalid baseline在mutation前拒絕、artifact-bound release_id/digests、原BFF image bytes與FEartifact可精確rollback，不能source rebuild冒充original artifact restore。本task只驗source/controller的實際測試，不做hosted mutation。

## 7. Hosted 与完成邊界

三項原 hosted task 保留完整要求、另等合法 one-shot MFA-backed admission：DEV-RELEASE-HOSTED-001 依 STRUCT，走既有 lane 部署／rollback／served identity；L12-HOSTED-001 在同一 accepted pair 建全新因果鏈並跑全部12-loop；MGMT-AGORA-E2E-001 在同pair跑 authenticated journeys、restart、SSE、durable replay。不得將本functional packet當hosted授權。

整體完成必須同時有所有source分支、單一批准權威/零替代舊路徑、文件真正合併、同一已接受hosted FE/BFF identity與所有mandatory證據。沒有合併或沒有hosted驗收就明列未完；任務admitted／worker健康／局部tests／PR存在都不等於閉環。
