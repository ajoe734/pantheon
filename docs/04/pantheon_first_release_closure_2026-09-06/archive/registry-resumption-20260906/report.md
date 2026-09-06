# Dev exact-pair 與完整閉環驗收續報

最新續報：2026-09-06 03:36 UTC（§14，依最新指示正式調整 reviewer、解決 Registry scope blocker）；§1–8 原取證截止為 01:55 UTC。本報告保留原始完整目標，沒有把 compatibility、登入成功或測試替身回執當作產品閉環。§9–11 的架構 hold 是歷史記錄，已由 §12 的正式交接取代；§12 的「尚無 PR」已由 §13 更新。

## 1. 本輪結論與實際進展

完整目標尚未完成，本輪未切換 dev。已完成的新增工作：

1. 查明另一 GPT-6 Astra 主對話、兩個審查代理與真實 Claude Registry worker 的重疊範圍；本對話不重派 source 實作／審查，保留整合、hosted 驗收、rollback 責任。詳見 [查重記錄](overlap-audit.md)。
2. 重新核對目前 GitHub protected dev tips、部署變數、實際 FE/BFF served identity、DNS、HTTPS、CORS、帳密登入與 authenticated `/bff/me`。
3. 透過現有 `agora_compat_manifest.py` 建立 exact candidate，執行現有 compatibility deployment gate，兩個命令退出碼均為 0。這是本地 source/contract admission evidence，不是 GitHub deployment run、image build、FE switch 或完整 release acceptance。
4. 使用唯讀 SQL 讀取歷史 bootstrap 失敗紀錄；在本機專用 PostgreSQL 以不同程序 seed、route replay、reload，實證 ledger 可從失敗安全推進至 `schedule_registered`。完整 route 仍因隔離測試未提供新版 Persona HTTP owner 設定而失敗。沒有把這個不同錯誤冒稱歷史 hosted 502 的已確診原因。
5. 重新核對原 release controller：目前只做到 FE switch 前 gate，BFF 已先公開；BFF rollback 仍重建 prior source，不是原 image bytes restore。修補沿用既有 `DEV-DELIVERY-001`，沒有建立競爭部署流程。

01:55 前的 goal turn 屬於 progress：查重、新增 exact candidate/gate 證據及真正 PostgreSQL 跨程序鑑識改變了接續行動；不是只有狀態重述。Registry worker 在 01:55 仍以 PID 3433923 / 3434137 驗證存活；其後已正式暫停，見 §9，不能繼續當成 verified live wait。

## 2. 當前服務與 candidate 必須分開

| 身分 | Backend | Frontend |
| --- | --- | --- |
| GitHub protected dev tip，01:55 再驗 | `471dc5391a0f9cbde54d51730891583043708e42` | `5d4f385284b44a30e10764426a47fd808a7ae3cb` |
| Candidate tree | `f854d487e05ad3cf2272e84619cbc7430eb267d0` | `b5607456f107a21119d7cf9a41b14bac3ad7d226` |
| 實際 public served source | `4d7f440c29d8f9057641b680f31e4ecd012f7558` | `a3bf4060f803d1f8b44f6611e89347d59cd6ae0f` |

新的 source candidate：

- `release_candidate_id`: `e0df3603738f906703021afe2a9b4038cab93ed4224517b2c0a339fbb7398597`
- Compatibility manifest canonical digest：`66b5b04d1661f0572c204a91cc5b766f46c95df01eab72dadba2145f88520fbb`
- [Manifest](candidate/release-compatibility-manifest.json) / [gate ledger](candidate/release-candidate-ledger.json)。
- 既有 gate 的 `compatibility_status=accepted` 僅屬於其 source/contract 驗證範圍；本報告不將它提升為 `dev_verified`。
- 尚未產生 requested unified artifact-bound `release_id`；尚無這組新版 FE/BFF build artifacts 或 running BFF digest。
- 初次生成因 FE 本地 `origin/dev` 過期而 fail-closed；僅 fetch 更新 refs，沒有 checkout、修改或提交 FE 工作目錄。更新後兩個 gate 命令成功，並再次查 GitHub 確认 tips 未移動。

當前 served pair：

- pair ID：`b77904160d21a32125d584897b1f9a62750838dce978679b218fc4a9a6020711`
- FE release：`20260904T205348Z-a3bf4060f803-gate-33916577101-33917729612-1-234423`
- FE content digest：`1ff6e40e174fba7891f6ea369a1f375cd1db033cc06e391557030b2be98c8a96`
- 實際 BFF image：`sha256:4aa55dd413d51daeaab7a7515fe0f13fa96c63055ce7ee49b5017bda18e49b66`
- `/bff/version` 仍回 `image_digest=unknown`。FE manifest 的 accepted 字段不補足失敗的 parent deployment 或缺失的 artifact-bound acceptance。
- [Public served identity / CORS](hosted/served-pair-cors.json)；最新 Nonprod run 仍為失敗的 [33943312084](https://github.com/ajoe734/pantheon/actions/runs/33943312084)。

## 3. Deployment variables、DNS、HTTPS 與簡單登入

GitHub repo variables 指向正確 dev：project `pantheon-dev-20260902`、VM `pantheon-dev-deploy`、zone `asia-east1-b`、IP `34.81.52.222`，remote user 與 managed worktree path 也一致。FE repo 為 live / strict，BFF base URL 是下列正式 dev host。環境層的 CORS/public-host override 與 repo 相同；没有讀出登入密碼或 token 值。

| 項目 | 本次證據 | 尚缺／限制 |
| --- | --- | --- |
| FE host | `https://app.dev.mvl-cap.tw`；A `34.81.52.222`；TLS 驗證成功 | DNS 管理帳戶／zone 控制權本輪未重新調用管理 API；不由 A record 單獨推論權限 |
| BFF host | `https://api.dev.mvl-cap.tw`；A `34.81.52.222`；TLS 驗證成功 | 未變更 DNS 或憑證 |
| 允許來源 CORS | FE origin OPTIONS 回 204、exact allow-origin、credentials=true | 不是 authenticated browser journey |
| 非允許來源 CORS | `https://untrusted.invalid` 回 400、無 allow-origin | 沒有放寬 wildcard |
| 帳號密碼登入 | 現有 operator_a dev-login 200，`/bff/me` 200，operator role | 密碼只在記憶體使用；沒有輪替、披露、寫入報告 |
| Auth posture | strict、stub=false、dev_login=true、MFA=false | 符合簡單 dev 登入，不代表所有業務功能可用 |
| FE 寫入安全 | read-only、REAL_WRITES=false、ALLOW_DEV_STUB_WRITES=false、無嵌入 bearer | Management AI paper action 尚未完成安全接線／驗收 |
| Source 環境權威文件 | protected dev 的 §3.1 仍含 retired lupin/sslip.io | dirty root 已有其他人的修正，不能整包搬入；沿用 DEV-DELIVERY-001 修補。未探測退役環境 |

登入證據：[authenticated prior-pair readback](hosted/authenticated-prior-pair-readback.json)，時間 01:53:27 UTC。這是 prior recovery readback，不是新 Management/Agora journey。

## 4. Paper baseline 鑑識結果

歷史 row 只用於部署失敗鑑識，不作 fresh-stimulus 的輸入或通過證據。

[Hosted 唯讀紀錄](hosted/ledger-readonly.json)：tenant-dev / persona-d57f6d76254c20418a9e；state=failed、step=capital_pool_failed、attempt_count=4；failed_at 仍為 2026-09-04T11:52:48Z；references={}、compensation=null、lease=null。實際 decoder 後 safe-early-failure=true、needs-compensation=false。SQL transaction_read_only=on，沒有呼叫 coordinator 或 owner mutation。

[隔離 PostgreSQL 跨程序結果](baseline-pg-replay.json)：

| 程序 | PID | 結果 |
| --- | --- | --- |
| seed | 3550083 | 在專用 ephemeral DB 重建上述失敗 row，attempt=4 |
| packaged route replay | 3550171 | coordinator 推進成功；後續 Persona owner projection/create 因測試缺 URL 回 502，precondition=`provisioning_coordination` |
| 新程序 reload | 3550523 | 從 PostgreSQL 回讀 provisioning / schedule_registered、attempt=5、error=null、compensation=null、27 個 reference keys |

這證明上述 SQL decoder / checkpoint / reload 邊界可處理該歷史 row，不能證明完整 route、真實 owner receipts、paper-running 或 hosted 原因已修好。Capital/Registry/Governance/Deployment/schedule 與 auth 均明確為測試替身；其回執即使寫入真實 PostgreSQL 也不變成 real product evidence。

完整 route 的新錯誤來自原 #5609 測試 scaffold 沒有配置新版 Persona HTTP owner，而 current root compose 有該 URL/token 接線。故它不能直接推出「current dev 真正部署也缺 URL」，亦不能解釋歷史 `capital_pool` 502。下一個有效整合測試需要明確的隔離 Persona owner 正向接線，不能改回 permissive production fallback 來讓測試綠燈。

原 #5609 的同一 MemoryProvisioningBackend 在同一 subprocess 換 store instance，並不是 durable restart proof。該 PR 只有 tests/evidence，不是歷史 hosted 502 的 runtime 修補。原失敗 candidate image 已被清除，無法逐 byte 還原當時執行；保留此不確定性。

本輪 ephemeral PostgreSQL container 已停止並自動移除；沒有碰 live DB 或其他 worker 的 DB。首輪工具輸出超長遭截斷，保留為 `baseline-pg-replay-output-truncated.txt`，不作 JSON 證據；再次執行後的有效 JSON 才是上表來源。

## 5. Gate-before-switch、artifact retention 與 rollback 缺口

現有 code 路徑在 frozen candidate 471dc5391 的具體缺口：

1. Nonprod source/contract admission 在 mutation 前，但 candidate BFF 在 FE integration gate 前已公開。故現有行為是 gate-before-FE-switch，不是完整 FE/BFF pair 的 pre-publication gate。
2. `scripts/deploy_nonprod_vm.sh:3110` 的 prior rollback 使用 compose `up --build`。`compensate_cross_repo_release.sh` 也以 prior SHA rebuild，再比 source SHA，沒有 original BFF image digest/archive restore input。
3. Root deploy 會 `docker image prune -af`（:2978）；GitHub `PANTHEON_DEV_DOCKER_PRUNE=true`。原／candidate image bytes 保全尚未納入同一 admission transaction。
4. FE pairIdentity 綁定 FE profile digests 與 BFF source SHA，沒有綁定 BFF image digest；public BFF digest 仍 unknown，因此沒有完整 artifact-bound release identity。
5. FE rollback drill 只還原 FE symlink/content，controller 目前 dispatch `rollback_drill=false`；即使單獨通過也不能證明 BFF exact artifact restore。
6. Nonprod coordinator 在 `bff_fe_pair_verified=true` 時可繼續，即使整個 deploy job 因 OpenClaw smoke 失敗。pair accepted 不等於 Management AI 驗收成功。

原 prior pair 的 bytes 已保全於 [Sept 5 報告](../dev-closure-20260905/report.md) 與其 `rollback/`：

- 原 BFF image：`sha256:4d2b000984ea13f473a1f48a03fd6319c9fcd3293390206340b2f89148b0d6b8`
- BFF archive SHA256：`cb293532f2101d3a7bdeb9bd2442c1eb3ffde6c420d624e712faa294f03995de`
- FE archive SHA256：`200dcb502702200f0cb9efd76b053d661a707d8f0a0f98c24a928c14035aa364`
- 本輪沒有執行 restore；當前 image 4aa55 並不等於原 image 4d2b。Source compensation 不能取代 exact-pair rollback drill。

以上均沿用既有 DEV-DELIVERY-001 的 release authority / artifact scope，不另建並行 controller。

## 6. 全新 stimulus：每個 Loop 的五項證據仍缺

本輪尚未開始單一、全新的 hosted stimulus。每格「缺」表示本輪新鏈的證據缺失，不表示 repo 完全沒有該功能；舊 IDs、隔離 fake receipts 或過去分段紀錄不填入本表。

| Loop / 需要閉合的路徑 | trigger ID | terminal output ID | next-consumer receipt ID | owner worker identity | durable reload |
| --- | --- | --- | --- | --- | --- |
| 1 ingest → SourceRecord → distillation | 缺 | 缺 | 缺 | 缺 | 缺 |
| 2 distillation → StrategySpec → replication | 缺 | 缺 | 缺 | 缺 | 缺 |
| 3 replication → evaluation → teaching admission | 缺 | 缺 | 缺 | 缺 | 缺 |
| 4 teaching proof → Persona target commit | 缺 | 缺 | 缺 | 缺 | 缺 |
| 5 Agora interaction → imitation candidate → handoff | 缺 | 缺 | 缺 | 缺 | 缺 |
| 6 imitation/research → terminal research → consumer | 缺 | 缺 | 缺 | 缺 | 缺 |
| 7 consultation → provider memo → Governance | 缺 | 缺 | 缺 | 缺 | 缺 |
| 8 approved artifact/plan → executable RuntimeBinding → causal consumer | 缺 | 缺 | 缺 | 缺 | 缺 |
| 9 paper signal/fill → terminal lifecycle → same-fill trade episode | 缺 | 缺 | 缺 | 缺 | 缺 |
| 10 telemetry/drift → reconciliation → incident | 缺 | 缺 | 缺 | 缺 | 缺 |
| 11 incident/sweep → evolution → downstream decision | 缺 | 缺 | 缺 | 缺 | 缺 |
| 12 attributed controller output → Management consumer | 缺 | 缺 | 缺 | 缺 | 缺 |

Loop 5 命名與既有 harness research dispatch 編號不同；使用者要求的 research provenance 仍是必要驗收，不因表格編號而移除。Simulation 必須明確維持 simulation，沒有 real backend terminal/readback 不得標 real。Loop 8 不能用同一 binding ID 當獨立 consumer；Loop 9 不能把 heartbeat 或同 runtime 但不同 event 的 episode 當 fill 消費。

## 7. 使用者要求的 journeys 與其他完成條件

| 要求 | 本次狀態 | 完成所需證據 |
| --- | --- | --- |
| 新 GitHub vars、Pantheon dev host、HTTPS/CORS | vars/endpoint/CORS 已複驗 | Source 環境權威文件仍需交付；zone admin 權限若需證明需實際控制面 evidence |
| 當前 protected tips 的 exact FE/BFF candidate | source/contract candidate 已產生並通過既有 gate | build artifacts、兩端 served digest、原子/安全切換、完整 acceptance 尚缺 |
| `release_id` / digest / served identity | 舊 served source/digest 已確認；新 candidate ID 已產生 | unified artifact-bound release_id 及新 running image digest 尚缺 |
| Fresh Loops 1–12 五項紀錄 | 全部缺 | 上節逐項可追溯的新鏈、真正 owner/consumer 與獨立 reload |
| Loop 5 simulation provenance | 修補 source 曾合併；本輪沒有 hosted 新 research | 禁止將 fake/simulated backend 或僅 env flag 當 real 的 hosted 正負向證據 |
| Loop 8 executable RuntimeBinding | 本輪未執行 | 新 approved artifact、saga、真實 executable worker、獨立因果消費 |
| Loop 9 paper lifecycle | 本輪未執行 | 新 paper-only signal/fill/episode，terminal + reload，real order/capital=false |
| Authenticated Management desktop | 只有 login/me/readiness 複驗 | 桌面全部必要功能 journey、terminal results 與 reload |
| Authenticated Agora Workshop → Trading Room → performance | 本輪未執行 | 新 Workshop session 的自然 handoff、Trading Room receipt、performance readback |
| Management AI OpenClaw answer → paper-only action → terminal receipt → reload | 本輪未執行 | 真實 product ask/answer、受限 paper action、同因果 terminal receipt、reload |
| Exact prior-pair rollback drill | 原 artifact 已保全，但未 drill | 還原原 BFF image + 原 FE bytes、兩端 identity/health/auth/readback、再恢復目標的受控證據 |
| 詳細缺口報告 | 本報告已更新 | 不以報告完成冒稱產品完整目標完成 |

## 8. 下一步與不重複工作的約束

- Registry owner 能力與 Overlay、Agora、Loop truth、Management、FE strict-live、release controller 原任務留給既有 supervisor / agy / Claude 工作線；Registry 已正式暫停，見 §9–10，不宣稱 worker 仍在執行。
- 本對話只將具體部署整合失敗歸回既有責任，不再對同一 source 範圍重新派工或動他人 worktree。
- 新的 positive baseline integration 要在實際 Persona owner 接線、完整套件入口、真實持久化與 restart 邊界上驗證；不能把本輪的 ledger-only 結果提升為 ready。
- 待 source / delivery 必要能力交付，再從當時最新 protected dev tips 重新固定 pair、保全 artifacts、跑完整 gate、受控切版、fresh stimulus/journeys 與 exact-artifact rollback。今天的 candidate 若 tips 移動，必須重新生成，不拿舊 candidate 冒充 current tips。
- 不修改 canonical JSON、Codex DB、既有 active tasks dependencies；不碰 production、live trading、真實資金或退役 VM。

### 8.1 已落地的既有任務交接

已透過 promoted Human/Ops `note` 將本輪 candidate、整對切换前 gate、BFF artifact digest/retention/restore 與 OpenClaw acceptance 邊界補入既有 `DEV-DELIVERY-001`。正式 `show` 回讀確認 next 含本輪 candidate ID；狀態仍 todo、owner Antigravity2、reviewer Antigravity、依賴仍為 FE-STRICTLIVE-001 / PLAN-ADMIT-001。沒有建立新 task、沒有更改 dependencies/artifact scope，也沒有把 advisory note 當 reviewer 或 operator acceptance proof。

## 9. 02:05 續報：真實 Persona owner 的狀態契約衝突與架構 hold

### 9.1 已取得的實際整合反例

在相同 frozen source `471dc5391a0f9cbde54d51730891583043708e42`，這次給隔離 BFF route 配置真實 `services.persona.write_owner:app` HTTP 服務、strict service credential、PostgreSQL Persona store；不再省略 URL，也沒有用 dict owner 代替 Persona API。Persona API 正是部署的 control-plane Persona main 包入的 owner router。

[完整機器證據](baseline-real-persona-pg-replay.json)：

- 真實 Persona owner PID 3606905；ledger seed PID 3606990；BFF route PID 3607016；獨立 ledger reload PID 3607608。
- BFF 先將 coordinator 推到 `schedule_registered`，再建立 Persona，送出 `lifecycle_state=provisioning`。
- 真實 Persona owner 回 **422**：`Persona creation must start in 'draft'; use the governed lifecycle endpoint`。
- BFF wrapper 對外回 **502 UPSTREAM_ERROR**，precondition=`provisioning_coordination`。
- 新程序仍從 PostgreSQL 讀到 `provisioning / schedule_registered`、attempt=5、27 個 reference keys；這不是整個業務建立成功。其餘 Capital/Registry/Governance/Deployment/schedule 仍為明確測試替身，不能將它們的已持久化 receipts 說成 real。
- 後續 Persona GET 發生 HTTPError，未能進行成功 owner restart readback。程式没有記下該 GET 的 status，故不填造 404 或 reload success。
- Owner 與本輪 ephemeral DB 已清理；沒有 hosted mutation、production data、source patch、新 task 或 worker restart。

Source 契約對照：

| 邊界 | Exact current source | 衝突 |
| --- | --- | --- |
| BFF provisioning projection | `personas/service.py:3701` 附近將 ledger 狀態投影為 provisioning / provisioning_failed / paper_running；:3726 將其送進 creator | 把協調／執行狀態當成 canonical Persona lifecycle |
| 呼叫順序 | `personas/service.py:3905–3919` 先 coordinator.coordinate，再 `_persona_record_for_provisioning(...mutate_store=True)` | 下游協調已持久化，Persona 本體建立卻可能被拒絕 |
| BFF HTTP port | `ports/persona_write_owner.py:249–268` 將 lifecycle_state 原樣送 POST `/api/personas` | 未定義兩個狀態模型的責任與合法轉移 |
| Persona authority | `services/persona/write_owner.py:61` 定義 draft → research_only → consultable → paper_owner 等治理生命週期；:706–710 只准從 draft 建立 | provisioning / provisioning_failed / paper_running 不在其 canonical lifecycle 集合 |

因此這不是「補 URL 就好」或「把 draft 限制拿掉」；需要明確區分 canonical Persona lifecycle、provisioning saga state 與 runtime operational state，以及建立、批准、paper admission、補償和 readback 的合法順序。本反例是 current source 的真實 boundary failure，仍不是歷史已清除 image 的逐 byte 根因鑑識。

### 9.2 其他工作線已正式暫停，不能私自重派

已讀取[架構討論 hold](/tmp/pantheon-archive-reconcile-prerequisite-20260905.PrI7ms/ARCHITECTURE_DISCUSSION_HOLD_20260906.md)與 authoritative journal / task：

- Registry prerequisite 在 seq 2550、01:57:00 正式由 Claude 改派 Human/Ops，generation 1→2，用於等待操作者架構討論；seq 2554、01:59:52 保持 hold。
- 原 run `claude-20260906T012928Z-da7305a6` 的 queue event 已 completed / stale_dispatch_event，lease 01:57:26 釋放；原 PIDs 已不存在，沒有交付 PR/head。
- canonical status 仍是 in_progress，而不是 blocked；不把 owner 改派冒稱正式 blocker transition。
- WIP 在 01:57:28 由 supervisor 保全，1966 個 files copied，僅 `.venv/lib64` skipped。這不是 merge、部署或驗收成功。
- 本對話沒有恢復 worker、擴充 artifacts、修改 task dependencies 或繞過架構討論。Registry 的 competing authority、command semantics、fake readback 問題由原對話處理，這裡不重做其 source review。

### 9.3 待討論的部署前置決策

建議保留 Persona owner 的治理 lifecycle 作為唯一權威，將 provisioning saga / paper runtime 狀態作為獨立、receipt-derived projection；先確認 command/action → owner → 合法 transition 與先後／補償契約，再在原 source task scope 修復。不能直接將所有狀態互相 alias、允許任意 lifecycle PATCH，或以 BFF overlay 製造成功。

這是待確認的架構方向，不是已核准 SD、scope expansion 或 source delivery。`DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001` 已有 BFF personas/ports 相關 scope，狀態 todo；若決策涉及其未宣告的 Persona owner 檔案，需要原正式流程處理，不能偷偷增加 scope。

本次因真實 owner 反例而取得新 evidence，屬於 progress。現在的下一個必要外部條件是架構討論決策／正式恢復原交付線；沒有將完整 goal 標 complete，也沒有在首次出現此 hold 時標 blocked。

## 10. 02:13 續報：暫停再驗與首版 contract 前提

正式 Human/Ops `show` 再驗：Registry prerequisite 仍為 Human/Ops、generation 2、in_progress，26 artifacts，最後變更 01:59:52，沒有 delivery binding；其 next 明確保留「待架構決策，不增 scope、不派工、不推進 acceptance/merge/deployment」。DEV-DELIVERY-001 仍為 Antigravity2、generation 2、todo，依賴 FE-STRICTLIVE-001 / PLAN-ADMIT-001，沒有 delivery binding。沒有更改上述任務。

02:11:48–02:12 的 GitHub 唯讀複核：兩個 protected dev tips 仍為 §2 的 471dc5391 / 5d4f3852；最新 Nonprod run 仍為 completed/failure 的 33943312084；兩項 task 未找到 PR，02:00 後沒有新的 Pantheon PR 更新。Runtime workers 與 worktree leases 都為空。Supervisor PID 1801618 存活只證明排程器在運作，不能当成產品實作或部署的 verified wait。

已閱讀原對話最新[首版 API 收斂／退役討論稿](/tmp/pantheon-archive-reconcile-prerequisite-20260905.PrI7ms/API_CONVERGENCE_RETIREMENT_DISCUSSION_20260906.md)。該稿記錄操作者前提為第一版尚未上線，不需要前後相容；被替代 API、平行 store、alias 與 fallback 應在同一交付範圍退役，callers 同步更新。但具體 contract、identity、授權、action/owner/transition/receipt matrix 及退役清單仍待確認，並未恢復派工。本對話不替代原對話確認方案，也不把這份待討論稿當已核准 SA/SD。

此變更不取消本報告的 exact-pair release、原 artifact rollback、fresh Loops 1–12 或全部 authenticated journeys；source compatibility 工具的通過也不是保留舊 API 的理由。開發資料清除沒有因此取得授權。

本輪來源文件 SHA256（文件仍可能由原對話更新）：

- architecture hold：`d7f89dac3e9929b1340be1c29de727869050a557af6c03bab998eed4c1d1ec5f`
- API 收斂／退役討論稿：`2e860b4611939ad3ca740f2a1dd6d933d043de1a68945873e2cb0832289e331b`

Goal audit：前一回合的 auto-worker slot 查詢回答了使用者問題，但對完整部署目標不算新增交付進展；本輪確認相同人工架構 hold 仍有效，是停頓再驗，不是 live-worker wait。從首次查明 hold 的回合算起，這是第二個受同一條件限制的回合；不將反覆更新報告算成產品進展，也不提前標 complete 或 blocked。下一步需要原架構討論完成決策並正式恢復既有交付線，本對話再負責整合、驗證與 dev 部署。

## 11. 02:14 最終暫停交接

連續第三個受同一架構決策條件限制的 goal 回合再驗：Registry 正式 `show` 仍為 Human/Ops / in_progress / generation 2，next 仍明確暫停派工、驗收、merge 與 deployment；DEV-DELIVERY-001 仍 todo，沒有 delivery binding。Runtime workers/leases 為空。02:14:09 的 GitHub 複核確認兩個 protected tips 未變、最新 Nonprod run 仍為 33943312084 completed/failure，沒有兩項任務的新 PR 或 integration receipt。原討論稿仍待確認。

前一回合與本回合均為 no progress／同一 blocker 再驗，不是 verified wait；Supervisor 存活不能替代缺少的產品 worker。安全唯讀檢查與報告已完成，在未取得架構決策或正式交付線恢復之前，沒有不重複工作且不繞過暫停的有效部署推進動作。

因此已透過 goal 狀態工具將**本對話完整 goal 標為 blocked**，不是 complete。原目標與所有未完成驗收完整保留。這不會改寫 canonical 任務狀態：Registry 仍是 in_progress / Human/Ops，沒有將它手改成 blocked；沒有重啟 worker、新增 task、修改來源、切 dev 或清除資料。

恢復條件：在原架構對話確認首版唯一 contract／owner／合法 transition／receipt 與退役範圍，透過原正式流程修訂必要 contract 並恢復 agy／Claude 交付線；通知本對話繼續後，再重新核對最新 protected tips、artifact retention、全 pair gate、fresh Loops 與 journeys、exact-artifact rollback。不能直接沿用本報告 candidate 當作恢復時的最新版本。

## 12. 02:36 恢復交接：已取得確認並啟動真實 Claude worker

操作者在本對話明確回覆「確認」，核准前述跨元件修正方向、解除架構暫停，沿用 agy／Claude 實作，由本 root 整合、驗證與 dev 部署。已保存[原意與授權界線](operator-architecture-confirmation.md)，不捏造 message ID、精確頭版接受、MFA 或 reviewer proof。02:18:55 是確認被讀取的時間。

### 12.1 正式替代原 immutable 任務

Promoted CLI 不支援改寫已派工任務的 acceptance／signed-plan references。因此採用既有簽署 bridge 建立 successor，先由沒有 executable endpoint 的 Human/Ops 暫管；正式接收／回讀後，才 supersede 原任務並以 expected-owner/reviewer 指派回 Claude。沒有修改已簽署 V2 文件或 canonical JSON，也沒有原任務與新版 Registry worker 並跑。

- 舊任務 `REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001`：正式 archive，status=done、**terminal_outcome=superseded**，superseded_by 指向下列新版。這個 done 顯示是 lifecycle envelope，不能解讀為 implementation completed。
- 新任務 `REGISTRY-STRATEGY-UNIFIED-CONTRACT-001`：Claude / Antigravity，generation 2，28 artifacts／10 acceptance，dependency 仍為真正已完成的 DOMAIN-WRITERS-001。
- [新版完整 SA/SD](architecture-resumption-sa-sd.md)，SHA256 `b7fbb1189cc963b2bee58f52c1f267b4e85e6782381685735db9c4dbc4677790`；保留完整正向能力、原子回執、strict auth、真正跨程序 PostgreSQL 證據及原全產品驗收。
- Signed packet `pkt-registry-strategy-unified-contract-20260906-v2`，digest `21c908ec042c93262325d579a4ddd47c53abbf0a8d5aca464b99b9a382324c57`；canonical seq 2558、02:30:25 materialized，authoritative readback verified。
- V1 因 acceptance 直接使用配置的 agent 名稱而 fail-closed，未建立 task；保留失敗 receipt。V2 只將 acceptance 改用角色名稱，結構化 owner/reviewer 不變，原 SA/SD digest 不變。沒有將這次格式修正說成產品程式修好。
- Supersession 前再次確認沒有 active task 以舊 Registry ID 作 depends_on，沒有遺留 worker/lease；superseded ID 不會被依賴解析器自動當成新任務完成。

### 12.2 原 caller／delivery 任務的正式檔案範圍補足

使用 Human/Ops `artifact-contract add` 與 `note`，逐項 readback 比對。下列原 acceptance、depends_on、owner/reviewer、status 均保留；歷史重要 next 指引亦保存，沒有用 note 冒充 immutable acceptance 改版。

| 原任務 | 正式增加 | 現在 artifact 數 | 保留狀態與責任 |
| --- | --- | --- | --- |
| OVERLAY-RETIRE-001 | Persona coordinator／ledger 與 4 個真實 owner／PostgreSQL integration regression 檔案，共 6 項 | 16 | blocked，agy / Claude；等真正 Registry capability，不能只因本次派工就解除業務 blocker |
| DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 | source-distillation caller／3 tests、Persona owner／測試範圍、兩個既有 compose，共 8 項 | 28 | todo，agy / Claude；research/deployment/runtime 原已在 scope |
| DEV-DELIVERY-001 | scripts/deploy_nonprod_vm.sh | 6 | todo，agy2 / agy；補足 exact-artifact retention／restore 的實際被呼叫腳本 |

已向既有 Overlay、Agora、Domain、Loop truth、Management、FE、Delivery 任務記錄新版 owner 契約與完整驗收交接，不新增一組重複 caller 任務、不更改其 dependency chain。FE/BFF/Agora/Persona/source/research 必須在同一完整 dev release 中通過，不能只合併 Registry 就切版。其他 Astra 可從同一正式任務記錄查閱；沒有宣稱另一對話已雙向確認收到訊息。

### 12.3 已啟動，不只是 queued

[結構化恢復證據](resumption-evidence.json)：

- run `claude-20260906T023359Z-69bd9ede`
- queue event `evt-20260906T023352Z-0022bae7`，started
- live supervisor PID 1801618 → runner 3776601 → bwrap 3776786/3776787 → **實際 Claude PID 3776857**
- task 已 in_progress；root 再次以 ps 與 authoritative show 驗證，不只引用 bridge 的 dispatched 字樣
- 乾淨 worktree `/tmp/pantheon-worker-worktrees/pantheon/registry-strategy-unified-contract-001`，branch `task/REGISTRY-STRATEGY-UNIFIED-CONTRACT-001`，起始 HEAD 471dc5391a0f9cbde54d51730891583043708e42
- 02:35:16 runner heartbeat；agy 是已配置的獨立 reviewer，尚未冒稱已開始／完成 source review

本輪為實際 progress：正式接收 successor、封存舊 scope、擴充既有必要 artifacts、恢復真實 worker，操作者架構決策 blocker 已解決。尚無新版 source PR／merge／deployment binding；本 root 未修改產品來源、未重新部署 dev、未開始新的 hosted stimulus。§6–7 的全部新鏈／journey／exact-artifact rollback 證據仍待後續實際執行，不因恢復派工而改成通過。

## 13. PR 交付、整合退回與 rollback 預檢（03:26 UTC）

### 13.1 真實交付不等於已符合完整契約

Claude 於 03:13:38 提交 [PR #5620](https://github.com/ajoe734/pantheon/pull/5620)，exact head `b12225f3cf0797cb5c52a7104a008b791e742476`，base `471dc5391a0f9cbde54d51730891583043708e42`。交付 evidence blob `c4c0c9615a7a947fe291717a0abd8fcd41a422f5`；沒有 merge commit。

Antigravity 的真實 review run `antigravity-20260906T031428Z-13febf8f` 於 03:20:41 正式批准該 head。這是實際 reviewer 決定，不是假派工；但 root 獨立整合核對發現：

1. PR 修改 `services/control-plane/bff/command_adapters/base.py`，不在現有 28 個 canonical artifacts 中；Domain corrective 已負責該共用 transport。沒有相應正式 artifact 修訂或 owner blocker。
2. PR 自己的 `evidence.json` 與 `first_release_contract.json` 把完整 Registry mutation JWT wiring、所要求的兩程序／response-loss／outbox crash-window proof 排除為後續 scope。這與 immutable SA/SD §3、signed canonical acceptance 不符。§4 允許後續 consumer integration，並不豁免 Registry 本身的安全與持久化驗證。
3. Worker 報告 194 passed，reviewer approval message 報告 228 passed；本 root 尚未逐一核對後者的 terminal command/output，因此不把數字或 reviewer approval 當成完整 acceptance。

Root 已將 PR 轉為 draft 並留下 [exact-head 整合阻擋紀錄](https://github.com/ajoe734/pantheon/pull/5620#issuecomment-5556597232)，使用正式 **Human/Ops reopen**（03:23:21）退回原 owner。這不是冒充 reviewer reject，也没有偽造 operator accept proof；舊 review 事實保留於 audit，現行 task 的 delivery/review binding 已按既有 lifecycle 清除。

- Requeue intent：`review-requeue-0918e4fecb2d1ea5b5c7e82d91ae68a9e2aeb7404131fcd44e5cc714165b58aa`。
- 03:23:52 materialized，journal seq 2708；queue `evt-20260906T032352Z-c3457aef`。
- 原 Claude 已真正恢復：run `claude-20260906T032417Z-14d9295c`，runner PID 4041257、provider PID 4041439。
- Canonical：in_progress，Claude／Antigravity，generation 2，仍 28 artifacts；03:26 尚無新正式 blocker/checkpoint。
- 下一步先保留 genuine PR checkpoint、取得實際 owner scope blocker，再正式前移最小既有共用 transport/test slice，讓 Domain 消費同一實作；不建立第二個 Registry worker 或 HTTP framework。

### 13.2 現行 dev 未被此 PR 切換

03:24:52 再次實際帳密登入與讀回：dev-login 200、authenticated `/bff/me` 200、readyz 200，identity operator_a、role operator；strict auth、stub=false、dev_login=true、MFA=false。密碼/token 僅在程序記憶體使用，沒有記錄值。

Public source pair 仍 FE `a3bf4060f803d1f8b44f6611e89347d59cd6ae0f` / BFF `4d7f440c29d8f9057641b680f31e4ecd012f7558`，pair ID `b77904160d21a32125d584897b1f9a62750838dce978679b218fc4a9a6020711`。Protected dev tips 再查仍是 §2 的 471dc5391 / 5d4f3852；最新 Nonprod 仍為失敗的 33943312084。本輪未 dispatch 新部署，沒有新的 artifact-bound release_id、fresh loops 或 authenticated desktop/Agora/OpenClaw journey acceptance。

### 13.3 Exact rollback 預檢：有保全，不宣稱已演練

[03:24 唯讀 Compose 預檢](hosted/rollback-compose-preflight-0324.json) 使用當前 Docker labels 的真實工作目錄 `.../managed-deploy-worktrees/dev-root/dev-bff`，讀取配置並在記憶體比對，不匯出 credentials、credential hashes 或完整 environment。

- 現行 container `d3c09048b53a5e724c91ffee36f0e905719459b7bb66c95003334eba84f0b113`，image 仍 `sha256:4aa55dd413d51daeaab7a7515fe0f13fa96c63055ce7ee49b5017bda18e49b66`。
- 原 BFF 4d2b 與 FE archive 的 SHA256 已在 VM 再核對吻合 §5；原 BFF image 尚未載入 Docker store。
- 一般 managed Compose render 與現行容器有 32 個 environment key 差異，包含 auth／CORS／login／OpenClaw 設定。用現行 container environment 只在記憶體覆蓋後，仍有 `PANTHEON_ASSISTANT_CONTROL_PASSPHRASE_HASH` 值不一致；此處只記 key 名，不記值或值的 hash。
- 初次診斷的 `BFF_COMMIT` 假設不成立；實際 source identity 是 `GIT_SHA` 與 OCI revision label，兩者均 exact 4d7f440。未放寬 image guard。直接 probe VM loopback :8001 被拒絕，但正式 HTTPS `/bff/version` 成功，不能據此宣稱服務離線。
- Compose 沒有明列 command/entrypoint；加入當前 image 的實際繼承預設比對後，兩者都吻合。因此初步 raw-null 比對不再當作 command mismatch。
- 尚無 image load、restart、restore、symlink switch、lease acquire 或新 live mutation。原始 artifact 存在不等於 exact restore 已驗證；當前仍是 rebuilt prior-source image，不是 original image bytes。

安全執行下一次 drill 前，既有 release lane 必須保留 incumbent 與 original artifacts、重建完全相符的安全配置，且用現有跨 repo CAS environment lease 與 heartbeat guard 保護整個操作。禁止以 `up --build`、自行平行部署控制器或 runtime credential-bearing docker commit 取代 exact-artifact rollback。即使先完成 prior-artifact restore 預演，也不能冒稱完整 new-pair → exact prior → new-pair acceptance。

### 13.4 Canonical release contract 的實際缺口

03:25 回讀 DEV-DELIVERY-001：todo，Antigravity2／Antigravity，generation 2，6 artifacts，只有「唯一當前環境身分」與「validated manifest baseline」兩項 acceptance。其 dependencies 僅 FE-STRICTLIVE-001、PLAN-ADMIT-001，沒有另一條 Domain corrective 分支。先前 notes 已保留 full release 目標，但 **note 不是 scheduler join 或 immutable acceptance 修訂**。需正式收斂這個缺口，再讓原 agy delivery/review 工作線交付；不得只因 source 分支之一完成就宣稱可發布。

## 14. 最新分工：Claude／agy 實作，Codex 獨立審查（03:36 UTC）

操作者最新指示為「盡量讓claude 跟agy都來做工作，codex 做reviewer」。已記錄 [指示與作用範圍](operator-worker-role-preference.md)，並使用 Human/Ops expected-owner/reviewer assignment 正式調整下列 7 個任務，逐項 readback 比對原 acceptance、artifacts、depends_on、signed packet digest/spec hash、status 及 binding 不變；不是僅修改說明文字。[結構化分配證據](worker-role-assignments.json)。

| 任務 | 實作 owner 保留 | 新 reviewer | generation |
| --- | --- | --- | --- |
| REGISTRY-STRATEGY-UNIFIED-CONTRACT-001 | Claude | Codex | 3 |
| OVERLAY-RETIRE-001 | Antigravity | Codex | 6 |
| AGORA-CHAIN-001 | Antigravity | Codex2 | 4 |
| DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 | Antigravity | Codex2 | 2 |
| LOOP-TRUTH-001 | Antigravity2 | Codex | 4 |
| FE-STRICTLIVE-001 | Antigravity2 | Codex2 | 2 |
| DEV-DELIVERY-001 | Antigravity2 | Codex | 3 |

MGMT-READ-001 原本即為 Antigravity／Codex，未重複 assign。未為 reviewer 更換而重啟活躍 worker；未修改全域 fallback 設定、帳戶、quota 或其他無關 fleet task。Configured reviewer 不等於已認證、已啟動或已完成審查，實際排程仍需其 task-scoped auth/capacity evidence。舊 agy approval 不轉移成 Codex approval。

Registry 原 Claude worker 於 **03:29:00** 真正保存 unchanged draft PR #5620 exact head 並提出 scope blocker，未再改 source。Root 在 blocked checkpoint 正式增加既有 4 個共用 transport／regression artifacts：`command_adapters/base.py`、`command_executor.py`、`test_command_executor.py`、`tests/test_command_adapters_router.py`（均在 services/control-plane/bff 下），總数 **28 → 32**。限於 Registry 必需的既有 HTTP method/auth/timeout/error handling 與真 HTTP 回歸；不擴張成一般 business executor 改寫或第二套 transport。

已向 Domain corrective 正式記錄串行交接：後續消費同一 merged shared implementation，保留 Domain 的獨立 business durability 任務及 55 artifacts，不重寫同一 transport。Registry 於 **03:36:28** 正式 reopen 為 in_progress、Claude／Codex、generation 3；new requeue intent `review-requeue-1894e717ef845fa433cf1206b6dea1094abb82b625f7a0d119afe72587962916`。當時 intent pending，因此不冒稱新 run 已啟動；所有原 Registry strict identity、真實 positive capability、atomic receipt／CAS／兩程序／crash-window 要求仍需實作與 fresh exact-head review。

Release gap 查重補充：DEV-DELIVERY-001 沒有 active dependent、worker、worktree、PR 或 delivery checkpoint，但另有歷史 open PR #5577／#5064 觸及 nonprod workflow／deploy script／compose；其 task 在現行 canonical 為 Unknown，不能當現行交付，也不能直接刪除或忽略 diff。需正式處理 §13.4 的 delivery acceptance＋Domain join 缺口，並補入既有 controller、deploy、lease-deploy regression 範圍；若需 FE 發布協定改動，必須在 execute-plans 正式交接，Pantheon scripts scope 不會跨 repo 生效。尚未 materialize 或 supersede delivery successor，沒有宣稱這個 scheduler 缺口已補好。

本 root 仍負責整合／驗證／dev 部署，未與 worker 重複實作產品 source。上述為派工與契約修正進展；dev 沒有新切版，§6–7 新 stimulus、journeys、artifact-bound release 與 exact rollback 的缺證狀態仍保留。
