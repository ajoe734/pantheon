# 正式 contract 擴充與執行交接現況

## 2026-09-06 02:39 UTC 最新補充

操作者已確認首版單一 owner、呼叫端同步修改、取代的舊 API／重複實作同批退役，不做前後相容。正式 TaskStore 回讀確認：另一協作工作階段已於 02:30:23 接收 `REGISTRY-STRATEGY-UNIFIED-CONTRACT-001`，02:33:23 將舊 Registry prerequisite 正式 supersede，02:34:04 successor 進入 in_progress。Supervisor 與 Claude worker 均有新鮮 heartbeat；此為開始實作，不是完成或 merged。

本工作階段原擬四任務草案尚未簽署／派發；發現現行 successor 後即停止發送並禁用 emitter 的派發入口，避免第二套 Registry／caller 任務。**現行唯一生效 SA／SD 是 [architecture-resumption-sa-sd.md](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/dev-closure-20260906/architecture-resumption-sa-sd.md)**；[本地補充草案](FIRST_RELEASE_UNIFIED_ARCHITECTURE_SA_SD_20260906.md) 不是已接收 contract。

仍需正式承接的 Governance 信任、共用 HTTP method、完整 command retirement 及兩分支 release gate 問題整理於 [協作與缺口交接](FIRST_RELEASE_CANONICAL_RECONCILIATION_20260906.md)。不改 live worker 的 owner／lease，不把 note 偽稱 immutable contract 或 scheduler dependency；實際 scope 超出仍須 owner checkpoint 與正式修訂。

Domain corrective 本階段精確增加 26 artifacts；另一个協作工作階段已先增加 8 項，因此現為 54 項（原 20 + 8 + 26）。既有 acceptance／dependencies／dev_bridge 不改。Overlay 保留真實 external blocker；未宣告 12 loops 或 Management／Agora 全部完成。以下內容依原取證時間保留，01:30 的 Registry V2 執行狀態已被本段取代。

後續複驗更新：四個既有任務 Registry successor／Domain corrective／Overlay／Delivery 已正式留下追加交接 note，回讀確認原 note、owner／reviewer／status／generation／acceptance／dependencies／dev_bridge 均保留。Domain 再正式增列既有 `services/persona/test_training_target_owner.py`，現為 **55 artifacts = 原20 + 他方8 + 本方26 + regression1**。strict V2 seq2682 再確認本地四草案 ID 都是 missing，沒有重複 materialize。02:43:25 supervisor projection ok，實際 Claude process PID3776857 存在且在現有 worker process tree 中；沒有產品測試／merge／hosted 完成聲稱。

最新複驗：2026-09-06 01:30 UTC；原任務/五項corrective取證00:42，見 [今日完整差異與負向證據](CURRENT_SOURCE_REAUDIT_20260906.md)。
原始 20 項 canonical：10 done / 1 blocked（Overlay01:19 owner checkpoint）/ 5 todo / 4 未 admitted；done 不等於完整需求驗收。
CW、BFF tests、Journal、Router、Domain writers 的已合併內容仍有本日具體缺口。
Overlay 已提交 scoped checkpoint 98a295700 與 authenticated Registry-owner blocker，尚未接受/合併。
五項 source residual corrective 已於 00:40:23 正式 admitted，authoritative readback verified；
逐項 show 為 todo，詳見上述報告 §9 及 [簽章 SA/SD](STRUCTURAL_RESIDUAL_SA_SD_20260906.md)。
00:50 再核 Overlay：10 個 undeclared source edits 不是 mandatory symbol retirement 所需，
未授權整包擴 scope；Strategy 仍有局部 overlay／吞錯回成功。已補正式 note 與
[範圍複驗及暫時 live repair 紀錄](OVERLAY_SCOPE_REVALIDATION_20260906.md)。
只中斷無界 collect-only 子程序，原 worker 保留並已改跑指定檔案實際測試；尚未通過驗收。
00:57 指定檔案批次terminal summary為524passed/92failed；worker同程序繼續逐檔複驗。
Collection另有3750collected摘要但沒有可見退出碼，不能記作通過或推論確定interrupted exit。
01:12：worker已去掉Strategy局部overlay dict/吞錯成功，以及main任意forwarding，ControlLoops改明確注入。
但Strategy仍以optional read-store/_data寫入；[Registry實際memory owner與adapter假讀回的複驗](STRATEGY_REGISTRY_OWNER_REAUDIT_20260906.md)
確認不只是接線。正在查重與界定必要owner prerequisite，尚未新增task、沒有偷擴active scope。
局部21/14/19項通過已取證，不代表combined或durable驗收通過。
01:25：查重與第二次SA/SDreview後，Registry唯一owner capability prerequisite已正式signed queued，
26exact artifacts；未新增cron、未改Overlay相依JSON。待intake receipt/authoritative readback。
其[SA/SD](REGISTRY_STRATEGY_PREREQUISITE_SA_SD_20260906.md)與最新checkpoint/archive事實見上述Registry報告。
Owner退出後worktree由supervisor封存移除，64files/0skipped與patches可恢復；不是root刪除資料。
01:30：Registry V1因legacyarchive-only前置拒收、未建立task；V2更正canonical dependency後
01:28:53正式admitted，01:29:31 supervisor已派Claude執行，Antigravityreview，26artifacts。
生效[Registry V2 SA/SD](REGISTRY_STRATEGY_PREREQUISITE_SA_SD_20260906_V2.md)不降低任何驗收要求。
Overlay在真實blocked-checkpoint狀態正式增列2個necessary regression artifacts；仍未授權整批WIP。
[最新checkpoint與migration負例](OVERLAY_CHECKPOINT_REVIEW_20260906.md)已確認假backfill成功及foreign tenantId問題，
需在原migration範圍修正，不能以目前101passed或新prerequisite當結案證據。

以下為歷史交接紀錄，核對截止原為 2026-09-05 05:56 UTC；不應當成今日現況。
各項保留其實際取證時間。這是本次執行交接補充紀錄，
不是「12 個業務循環全部閉環」或「Management / Agora 已可完整運作」的宣告。

原始盤點、SA/SD 與簽章 task packet 保留不變；canonical contract 變更
透過現有 Human/Ops `artifact-contract` 指令留下 audit，沒有手改任務 JSON。
本次 chatbox 沒有接管 worker 的產品程式實作，也沒有新增 cron。

最新結論：正式 contract 擴充已完成；cron recovery、Python 與 foreground
completion policy prerequisite 均已合併封存。最新 qualified runtime dd3f0563
已 promote，05:31:59 watchdog/runtime health 全通過，新真實 worker 確有
no-background 環境設定及新版 completion prompt。CW 契約與 domain mapping
回歸已重新取證，新增七檔 corrective 正式自動 admitted 並開工。
開發派工入口已恢復，但原產品結構與 12 循環工作尚未完成。

## 1. 已完成的正式合約修訂

共 12 個 execution task 已回讀確認具有獨立 JSON 審查證據路徑，
不再包含未展開的大括號目錄選項。這個「12」是任務數，不是業務循環數。
原始實作範圍、相依順序與驗收要求保留；沒有放寬為全倉庫寫入。

| Task | 修訂 | 回讀狀態 |
| --- | --- | --- |
| BFF-DEADCODE-001 | 五個目錄選項明列，新增 task-scoped evidence.json | PR #5597 已合併，done / archived |
| BFF-TEST-ARCH-001 | 新增 evidence.json，保留原 SD 分層／解耦验收 | PR #5600 因範圍縮減退回；局部 blocker 複驗後 in_progress |
| BFF-ROUTER-STRUCT-001 | trading_room / research 目錄明列，新增 evidence.json | todo |
| DOMAIN-WRITERS-001 | BFF 八個目錄、服務四個目錄明列，新增 evidence.json | todo |
| JOURNAL-OWNER-001 | 新增 evidence.json | todo |
| OVERLAY-RETIRE-001 | 原六個路徑選項明列，新增 evidence.json | todo |
| AGORA-CHAIN-001 | 新增 evidence.json | todo |
| LOOP-TRUTH-001 | 新增 evidence.json | todo |
| MGMT-READ-001 | 新增 evidence.json | todo |
| FE-STRICTLIVE-001 | 新增 execute-plans 倉庫內的 evidence.json | todo |
| DEV-DELIVERY-001 | 新增 evidence.json | todo |
| DEV-EMPTY-HOST-BOOTSTRAP-001 | 新增 evidence.json；reviewer 退回後，正式增列既有 scripts/deploy_nonprod_vm.sh | corrective PR #5601 已合併，02:41:59 UTC done / archived |

各證據路徑為 `docs/deployment/evidence/<TASK-ID>/evidence.json`；
FE-STRICTLIVE-001 位於獨立的 execute-plans 倉庫，不是 Pantheon 子目錄。

03:22 UTC 再逐項透過 Human/Ops show 回讀上述 12 項：全部仍有獨立
evidence.json，全部不含未展開的 `{}` artifact；兩項 done、一項
in_progress、九項 todo。這是合約有效性的複驗，不是新增 12 項任務。

05:36:54 使用 qualified dd3f0563 Human/Ops 全量複驗上述 12 項，分布與
precise artifacts 不變：全部有 exact evidence.json、零 `{}` artifacts，
目錄展開與 empty-host deploy_nonprod_vm.sh 擴充保留；FE target_repo
仍正確為 execute-plans。沒有把新 prerequisite 混入這 12 項 contract。

## 2. 已正式接收並由 supervisor 開始執行的前置修復

### OPS-MERGED-ARCHIVE-RECONCILIATION-PREREQUISITE-001

- Owner Codex；獨立 reviewer Claude；done / archived。
- [SA/SD](SA_SD.md)。
- Packet `pkt-ops-merged-archive-reconciliation-prerequisite-20260905-v1`。
- 正式接收 01:45:14 UTC，receipt processed / admission admitted，
  authoritative materialization readback verified。
- 修復既有 `reconcile_merged_done` 的同一原始工作復原契約；
  保留不可變封存，不建立替代結案命令或新 TaskStore。
- [PR #5598](https://github.com/ajoe734/pantheon/pull/5598) 已由正式 integrator
  合併，merge `4e09159049fd54ae209fbdef4c4a7c1218b8b2d6`；
  02:06:26 UTC canonical done / archived。
- reviewer 重跑 ai_status 測試 393 passed、TaskStore 測試 24 passed。
- 05:00 與 Python prerequisite 一同隨已合併版本 promote；真實 PPL 案例
  另有歷史 audit 缺失，不能宣稱該任務狀態已復原。

### OPS-SUPERVISOR-PYTHON-RUNTIME-PREREQUISITE-001

- Owner Antigravity；獨立 reviewer Codex；04:51:23 canonical review_approved，
  04:57:08 done / archived。
- 合格 head `a99fc938f72d506209dd334be9f56fe0e9b73971`，evidence blob
  `1449432572f588e9a678f24859f9ce152e70ad79`；reviewer 重跑 97 passed、
  1 skipped，bash -n／py_compile 通過，包含 helper 回傳 final interpreter
  的真實簽章 queue／drain 與 fixture provisioning 失敗不得 skip 的驗證。
- Root 04:44 另跑兩項 fixture fail-closed regressions：2 passed、2 deselected；
  前後檔案 SHA256 `79f43a5d47298c818fd848e99f9d325fd256af3619ce4c0f40a6eb54556c01c5`。
  這是獨立補驗，不冒充 canonical reviewer 的批准。以下按時間保留歷次退回紀錄。
- [SA/SD](PYTHON_RUNTIME_SA_SD.md)。
- Packet `pkt-ops-supervisor-python-runtime-prerequisite-20260905-v1`。
- 正式 receipt processed / admission admitted，authoritative readback verified。
- 修復現有 bootstrap / provision / promotion / watchdog 的 Python 與
  相依套件契約，啟動前驗證必要 imports，失敗不得替換健康 incumbent。
- 不新增 supervisor launcher、packet dispatcher 或 cron。
- [PR #5599](https://github.com/ajoe734/pantheon/pull/5599) 04:55:24 已合併，
  merge `62ecea34b5ae51bb35f484f1a7a78d39fecd9c08`。05:00 正式 promotion
  已執行，05:01:24 identity／liveness／readiness／progress 全部健康。
  自動 intake 已能處理 packet；第一版新 packet 因 root 提供的 acceptance
  文字觸發 role-only 規則被拒，未建立 task。重新簽章 v2 後 05:02:23
  已 admitted、05:02:27 drain 完成、05:03:27 canonical in_progress。
- Human/Ops 已提交兩項 pre-merge finding：metadata-only preflight 會錯放
  不相容／不可匯入套件；共用 venv 更新會在 promotion 前改動 incumbent。
  [詳細負向測試與審查意見](https://github.com/ajoe734/pantheon/pull/5599#issuecomment-5548628224)。
  這些是隔離的合成診斷案例，未變更 live 套件或 supervisor。
- 獨立 reviewer 在 rebased head `cbea5c308bf2994e7ce1b3c238f39ab1374582b0`
  重跑 focused suite：77 passed、1 skipped，但驗收未通過，已正式 reopen。
  除上述兩項，另確認 fresh-host 在 venv 建立前仍以 ambient Python 匯入
  cryptography 產生 keypair，以及缺少要求的真實 subprocess signed-packet
  intake 證據。這四項均須修正，不因 focused suite 全數通過而放行。
- 第二次唯讀檢查 owner 修訂中的程式：per-SHA venv 雖隔離不同版本，
  同 SHA bootstrap 仍無條件 pip install，尚未證明不會改動 incumbent。
  新自製版本比較器會將 `2.9rc1` 接受為 `>=2.9,<3`，也會接受
  `2.9 >=2.9post1` 及 `>=not-a-version`。這是隔離函式診斷，不是
  live package 測試；已透過正式 note 要求沿用標準版本／specifier
  機制並補同 SHA 重入驗證，沒有新增另一項重複任務。
  [第二次檢查的 PR 意見](https://github.com/ajoe734/pantheon/pull/5599#issuecomment-5548765595)
  已提交；02:44 PR 尚未合併，owner 仍在實作與驗證。
- 02:45 owner 提交 head `82aaf61b8677c57c8d10a2ef98c0775dc52d4659`，
  補上 fresh-host ordering、actual imports 與兩項 real signed-intake
  subprocess tests。但上述三個 version counterexamples 在 exact head
  仍回傳 True；bootstrap 同 SHA 仍會 pip install。一般 sync no-op 會
  提早退出，真正需補的 sync 案例是同 SHA config-drift／re-promotion。
  Root 已正式 reopen，並提交[exact-head finding](https://github.com/ajoe734/pantheon/pull/5599#issuecomment-5548838679)。
- 獨立 reviewer Codex 回讀 in_progress 與未解缺口後，未嘗試繞過狀態
  或批准 unchanged head；02:53 supervisor 已派回 owner Claude 修正。
  81 項 wiring tests／兩項 intake tests 的進展保留，但不構成完整驗收。
- 03:10 owner WIP 已改用 packaging，並補上既有環境 read-only reuse 與
  isolated candidate provisioning；但 bootstrap 與 sync 各自複製了一份
  約百行的 reuse／install／preflight／atomic publish policy。
  Root 已將「必須共用單一 provisioning owner，不只修好兩份副本」寫入
  正式 note 與 PR 結構審查意見；這仍是原不重複機制要求，沒有新開
  launcher／cron 或接管 worker 實作。WIP 尚未當作通過驗收。
- 隨後 owner 將同一份重複實作提交為 exact head
  `68e2277aa33b53f4f95e7d6aebac521dc75178c3`，並以 86 passed／1 skipped
  handoff。Root 確認兩個完整 provisioning blocks 仍存在後正式 reopen，
  保留已改善的 version／reuse 行為，不接受「兩份副本都修好」作為結構收斂。
  [Exact-head 結構退回](https://github.com/ajoe734/pantheon/pull/5599#issuecomment-5548998952)。
  Reviewer Codex 獨立確認 finding 與 canonical in_progress，未批准；
  03:20 supervisor 已派回 Claude，新 worker 已開始檢查重複區塊。
  此 PR 尚未合併，live runtime 未切換。
- 03:32–03:37 owner WIP 已將政策集中至既有
  `ensure_supervisor_python_environment`，bootstrap／sync 改為呼叫者。
  這是結構修正進展；owner 報告 92 tests passed，但尚未交付新 head。
  Root 的 synthetic race-policy test 發現 EEXIST winner 未在最終路徑
  重新驗證：空的競爭者目錄仍使 helper 回傳成功與臨時 candidate proof，
  此時 final Python 不存在。已記入同一 PR finding／canonical note，
  要求 final-path validation 與負向測試。這是隔離 mock policy probe，
  不是 live package／intake 測試；下游 promotion preflight 另有保護，
  不能拿它代替 helper 自己結果的正確性。
- Owner 隨後提交 shared-owner `42b525f6103a6ce769e47127b5039361186969d8`，
  rebase current dev 後以 `06671fb888b2cb8d41c104b466d4f73c878be8a0`
  handoff；helper bytes 未變，final-path 缺口仍在。Root 已正式 reopen
  此 exact head，保留單一 owner 收斂成果，並提供可執行的 synthetic
  競爭者案例。尚未合併、尚未 promote，不拿 92 passes 代替未覆蓋條件。
- 03:50 後的 WIP 已在 publish 後驗證最終 interpreter，isolated normal／
  valid winner／invalid EEXIST winner 複驗符合預期；owner 提交
  `7020830cf`，再以 `e73fc7e3836be2b2b6acee5a2ec77e71bd71dfd3`
  handoff，報告 93 passed / 1 skipped。Root 保留這項有效修正。
- 04:06 exact-head／canonical 複驗仍發現兩件未完成事項：新增的
  `.orchestrator/development_bridge/tests/test_dev_bridge_inbox_bootstrap_python_runtime.py`
  不在目前 14-artifact contract；其 fixture 另建 EnvBuilder／pip venv，
  並未使用 shared helper 真正回傳的 final executable 進行 signed intake。
  原 SA/SD 本來就要求此 intake，因此只需正式增列缺少的測試 artifact，
  不新增 task 或第二套 provisioning。Root 已正式 reopen，要求 owner
  checkpoint／authenticated blocker 後，由 Human/Ops 在允許的 blocked
  狀態增列精確路徑。尚未執行該增列，尚未接受 PR 或 promote。
- 04:10:30 owner Claude 已 checkpoint／正式 blocker，04:11:28 Human/Ops
  透過 `artifact-contract` 增列上述精確測試路徑。Canonical 回讀確認
  artifacts 由 14 增為 15，previous／current／reason／Human/Ops／timestamp
  均有紀錄；04:11:51 正式 reopen，回讀為 in_progress、waiting_for 清除。
  這項範圍缺口已解除。仍须 worker 使用 helper 回傳的真實 final Python
  補完簽章 intake 測試並重新審查；沒有手改 canonical JSON 或 promote。
- 04:17–04:19 WIP 已改用 shared helper 的 final interpreter，owner 回報
  兩項實際通過；這些有效結果保留。獨立 fixture 診斷另發現 helper 任意
  ValueError 或回傳不存在 interpreter 會被轉為 skip，可能掩蓋日後 regression。
  已要求讓 helper failure 正常使測試失敗、檢查 final executable 存在；
  offline opt-out 若需保留須明確區分，不能泛稱所有錯誤為 package-index 問題。
  此為測試可靠性缺口，不是宣稱上述實際兩項通過無效或 runtime 有新故障。
- 同一 failure-as-skip 在 committed `330b78219`／frozen handoff
  `3f2c0921b367c813734bedb26ada27e34ddcd661` 仍存在，root 04:29 正式
  reopen，保留真實 helper intake 改善。後續 Claude WIP 已移除這些 skip、
  加兩個輕量 regression，報告四項實際通過；尚未提交成合格 head。
- Claude 在等待 broader test background job 時結束 run，04:37:57 supervisor
  以 lost lease 正式改派 Antigravity（generation 5），reviewer Codex。
  Root 已交接 valid WIP／四項證據與原 scope，要求確認真實 test handle，
  不把等待文字當作完成、不重做有效修改、不重跑仍 live 的 job。
  目前 source／live intake 仍未完成交付；正在唯讀檢查這類提前退出的原因。

前兩個 packet 使用既有受信任 Ed25519 簽章與現有 verifier-backed inbox
API 完成接收。此次由 Human/Ops 使用已有依賴的 Python 暫時協助 drain；
未修改 supervisor 原始碼、live config、金鑰或 canonical JSON。

### BFF-PACKAGE-BOUNDARY-CORRECTIVE-PREREQUISITE-001

- 新的 referencing corrective，引用已封存 BFF-PACKAGE-001；不是重開
  terminal row、複製 package tree 或重做已交付功能。
- [SA/SD](BFF_PACKAGE_BOUNDARY_SA_SD.md)；owner Codex、reviewer Claude。
- 根據 current GitHub dev `161f0a0d7c179fb5d5299dc9d4bdcaa2f5b11926`
  的 Git blobs 重新 AST 盤點，215 個選定非 test-named Python 檔、
  0 parse errors，51 檔共 197 處 unqualified BFF-local imports。
  此選法包含兩個診斷／report entrypoints；它們占兩處 import 與全部
  三處 sys.path mutations，不能誤稱 production domain/router 有三處。
- Root 以 canonical package、沒有 main／models alias 的 fresh process
  呼叫 Agora cross-user forbidden branch，重現 ModuleNotFoundError
  (`models`) 而非預期 HTTPException 403。這是真正延遲分支缺口，
  app import 或 test collection 成功不代表該路徑可用。
- 只授權明列 51 個既有 source files 的 import 邊界修正、獨立的
  scripts regression test、runbook 與 evidence manifest；不接管
  test 架構、Persona globals、Management 投影或 hosted 工作。
- Packet `pkt-bff-package-boundary-corrective-prerequisite-20260905-v1`
  已用既有受信任 signer 簽署、verify 後 queue，queuedAt
  03:33:15 UTC；兩份 signed source documents 為 SA/SD 與逐檔 inventory。
- 曾在 pending 等待，supervisor 03:33:56 仍記錄缺 pydantic。
  為讓真正的產品 corrective 先執行，03:44:22 UTC root 使用既有
  workspace Python 暫時協助同一 verifier-backed API drain 一筆：
  processedCount 1、errorCount 0；receipt processed、admission admitted、
  authoritative materialization readback verified。
- Canonical 回讀先為 todo，03:45 已是 in_progress，owner Codex、
  reviewer Claude，54 個明列 artifacts（51 source + test/doc/evidence）。
  Supervisor 啟動 worker PID 1094320，run log
  `20260905T034441443842Z-codex-codex1_2-da4bbe.log`；已讀 signed inventory
  並確認 clean task branch、dependency fulfilled。這不是僅 queue 的宣告。
- 這是臨時人工協助接收，不是 automatic intake 恢復；不再把這筆
  已人工 drain 的工作當作後續 automatic admission 的成功證據。
  Python promotion 仍須取得其自身 live 自動入口驗證。
- Queue 後 dev 前進至 `4804b6d863e68dc65ab8a923ebc93eeef7923cec`；
  intervening Persona diff 已修正兩處 bare imports。Root 已正式 note
  要求在 current dev 重算剩餘缺口、沿用這兩個既有修正，而非重做或
  復原舊 fallback。原 signed 161f0a0d7 inventory 保留為精確基準，未改寫。
- Owner 提交 [PR #5604](https://github.com/ajoe734/pantheon/pull/5604)，
  head `3e72b59c9fbcf7cea73f9bd2473a4e1c9bd98cd2`。獨立 Git-blob AST
  重算：signed baseline 197 處、actual branch base `4804b6d86` 195 處、
  handoff 0 處 bare imports。但仍有 12 檔共 30 個 copied shared-model
  classes，另有 canonical import 重試相同 canonical import／None fallback。
  原簽章 SA/SD 明確要求清除這些重複機制，不能只替換 import 字串。
- Root 已正式 reopen，要求沿同一 corrective 清除 namespace-only fallback、
  驗證 canonical class identity 與真實延遲負向分支，保留真正 optional
  third-party handling。四個 focused tests 的 zero-import gate 無法覆蓋
  重複 class／fallback invariant；import 四個模組也不是 disabled-provider／
  capability／validation branch 的實際執行。04:04 owner WIP 已開始移除
  fallback，尚未視為完成。[Exact-head finding](https://github.com/ajoe734/pantheon/pull/5604#issuecomment-5549187774)。
- 第二次 handoff `2e288c92395fa3eb32a84e75dcd941df91f357ba` 已真正清除
  上述範圍的 copied shared models／canonical import-only fallbacks：51 檔
  AST 複查皆為零，沒有重做已修好的部分。三個安全 checkout probes 也得到
  預期 403 capability_missing、400 missing_field、503 provider-unconfigured；
  無 network／file-write／downstream access，source hashes 前後一致。
- 但 committed 七個 focused tests 仍未包含原 acceptance 3 的上述分支，
  cross-user test 未 assert error code；canonical diagnostic 也只有 AST scan，
  子程序仍手動插入 sys.path。Root 04:16 正式 reopen，要求補上原本就要求
  的 committed regressions／canonical diagnostic imports；不新增 scope／
  task，不把已成功的局部 7 passed、24 passed / 4 skipped smoke 誤作完整验收。
- 新 head `0950fff3a71e402a620d19abd24af28039393be1` 加入八項 focused
  tests、四種實際負向行為與 exact error code，移除 test sys.path surgery。
  但 required CI smoke 暴露真正 main import failure：Persona service 匯入
  coordinator 根本沒有的 PersonaCronRegistrar。Root 的兩個 guarded
  installed-editable-package diagnostic imports 同樣失敗，無 network／write
  guard violation。Reviewer Claude 已在 04:27:02 正式 reopen；root 隨後
  的重複 reopen 被正確拒絕，改以 note 提供獨立證據與移除 dead import 的方向。
- `13524b1d35f56da501d4f6b32cf46743061384b3` 刪除多餘的缺失 symbol import，
  沿用原 function-local canonical cron owner，沒有新增 registrar／re-export／
  fallback 或 cron 行為。獨立 reviewer 重跑 main smoke 26 passed、boundary
  八項、compileall、workshop 24 passed / 4 skipped，04:35:08 正式
  review_approved；required smoke 已綠。04:39 PR #5604 仍 OPEN，等待既有
  integrator，不宣稱已合併或完成 hosted／12 loops。
- 04:40:18 既有 integrator 已合併 [PR #5604](https://github.com/ajoe734/pantheon/pull/5604)，
  merge `4bffcf93de4d740bd7141cd561708ea917c7652d`。Root 已把 exact merge
  交接给 BFF-TEST owner，要求先保存 WIP，再沿正常流程 rebase／重跑相關
  negative paths，不再補 namespace aliases。合併後第一次 canonical show
  仍為 review_approved，封存完成待後續回讀，不用 GitHub merge 直接代填 done。
- 後續正式回讀已確認 source archive／status done／last_update
  `2026-09-05T04:41:24Z`，delivery head 仍綁 `13524b1d35f56da501d4f6b32cf46743061384b3`。
  至此 package corrective source 交付完成，原 20 項中的 BFF-TEST／後續
  九項與 hosted 驗收仍未因此完成。
- Merged `4bffcf93` 的兩個 diagnostic entrypoints 已另經 configured isolated
  installed-package imports 驗證成功，均實際載入 main；sys.path 未變、
  無 bare aliases、無 network／DB／provider call、guard violations 為零。
  使用既有測試 synthetic ranking DSN／BOOTSTRAP=0 及已記錄的 temporary
  BFF／SLO data directories；只建立隔離初始化檔案並清理。這完成原 import
  驗證，不代表 DB 或產品 readiness。Six source hashes／HEAD 前後不變。
- 診斷 wrapper 曾用 UV --no-sync；其不保證 uv.lock 不變，並行期間出現
  untracked uv.lock，歸屬無法唯一判定，已保留未刪除／未提交。後續改用
  cached interpreter 或 --frozen，避免重複產生 lockfile。Child import 的
  source error／guard 證據不受此 wrapper caveat 影響。

## 3. 重新盤點後更正的現況

### 廢碼清理

[PR #5597](https://github.com/ajoe734/pantheon/pull/5597)，
head `454a2398ceea0a9583952f90f8bb883c5eb66803`，已取得獨立審查並合併，
merge `0a884c5d7b2c634ae1ec8e5bc63b052f3ac698b4`。
已於 01:51:58 UTC 正式結案封存；01:52:05 UTC 的受控回讀為
source archive / status done，交付 head 與上述已審查版本一致。
Owner 已刪除原盤點 17 段不可達程式，分布於六個 BFF 檔案，
並提交正式 JSON 審查 manifest。

Owner 記錄：AST scan 0 findings；指定九組測試 184 passed；
smoke 26 passed、incident smoke 21 passed；九個 deprecated route
保留 410 與 deprecation headers。GitHub packaging / smoke / trailer
檢查通過，獨立 reviewer 已重跑上述測試與路由檢查並通過。
以上不是整個 BFF 測試全綠的宣告。

另一支 `services/control-plane/bff/tests/test_bff_path_dedupe.py`
實際 collection 失敗：使用已退役的未限定 `import main`。
已正式記錄至 BFF-TEST-ARCH-001，要求遷移至 canonical installed package，
禁止以 sys.path / sys.modules 或 runtime alias 補丁繞過。

### 測試架構：有 import 整理進展，但尚未完成解耦

BFF-TEST-ARCH-001 的 owner WIP 已將未限定 import 改為 canonical
package，完整指定範圍 collection 記錄為 3432 tests / 205.64 秒，
在 240 秒硬限制內；這只代表收集成功，不是全部測試執行成功。

第二次 AST 盤點仍發現 217 個測試檔匯入 canonical `main`，至少
35 處 literal `main.read_store`／overlay monkeypatch 呼叫。
當時新增的 gate 只禁止未限定 import 與 sys.path 修改，未限制
composition coupling。原 SD § 8.5 要求的 218 檔分類、typed domain
fixtures、小型 composition allowlist 與 route/application 實際執行
時間證據，尚不能由目前成果證明。已正式註記保留原驗收，不得把
217 個檔案全部列入 allowlist 來宣稱解耦完成。

Owner 隨後提交 [PR #5600](https://github.com/ajoe734/pantheon/pull/5600)，
exact head `67d01be8a12b96428c4abbb29cf8f4ea4bc45534`。該版精確重算為
212 個 direct main-importing files，加五個透過新
`tests/isolated_composition.py` 間接執行 main 的 KW suites，合計仍有
217 個 composition-dependent files；35 處 direct store patches 未清除。
此區別已更正在[正式審查意見](https://github.com/ajoe734/pantheon/pull/5600#issuecomment-5548753129)。
新 loader 在 `sys.modules` 下執行多個 main 副本，不是 domain fixtures。

Human/Ops 已正式 reopen。Reviewer 對 unchanged head 的批准嘗試只引用
3432 collected / 六項 focused passes，被 canonical in_progress 狀態拒絕，
沒有取得有效批准或合併。Supervisor 已將工作派回原 owner Codex。

Owner 另提出「缺少 injectable whole-app factory，因此所有 category 2–4
遷移均被 test-only contract 阻塞」。Root 複驗確認前半部源碼事實：
bootstrap 只 export AppDependencies、app 早於 app_deps 建立、main 掛載
捕捉 import-time dependencies。但「全部測試工作不能進行」不成立：
真實 subprocess 使用既有 `create_management_router(service=...)` 與
typed ManagementService subclass，回應 HTTP 200 注入資料，且 sys.modules
沒有 main。分類與可直接建立的 router/application 測試可先沿原計畫完成。
已正式解除 blanket blocker，要求剩餘個案提供逐檔 owner／factory／missing
seam 證據，再決定最小必要 contract／prerequisite；未新增另一套 app factory。
[複驗證據](https://github.com/ajoe734/pantheon/pull/5600#issuecomment-5548816628)。

02:51 的兩筆 owner anchors 已真正移除個別測試對 main app 的依賴：
`51c60207fd6d1c9c2c58a473f3aa0dbe6eeab1cd` 遷移 shell-summary，
`3e8727d7e0de71c71051387169e47508a6ea2bd6` 遷移 management cockpit。
Root 在後者 exact head、測試前後皆無未提交變更的工作樹重跑兩檔：
7 passed / 4.41 秒，使用 90 秒硬限制。
這只是兩檔的 partial source progress，尚未完成整個 task 的 review／merge。
Supervisor 隨後正式將 owner lane 由 Codex 改派 Antigravity（generation 3），
reviewer 仍為 Antigravity2；root 已核對 canonical row 並交接兩筆 anchors、
完整原驗收與 residual coverage 要求，沒有自行改派或混同 agent 身分。

### 測試架構分類版的再次退回

03:00 新提交 head `0ec56e2119d4325815d4d21c3d7d84a45c72d502` 已有
classification，但 exact-head AST 與其自身分類交叉檢查發現：仍有
101 application、93 router、3 adapter、4 hosted-labelled 檔案直接匯入
main，合計 201 個非 composition 檔案；另九個 direct importers 分類為
composition。新 gate 只要求檔案出現在完整分類清單、總數不超過 210，
沒有檢查 importer 必須屬於 composition，因此仍是一份過大的 legacy
allowlist。Manifest 卻把三項原验收全部標為 passed，與來源證據矛盾。

Root 已正式 reopen，保留兩個實際遷移 anchors，但不接受「分類即遷移」
或把 local TestClient smoke 當 hosted 驗收。要求依 domain fixture batches
持續遷移、partial checkpoint 只記錄進度，不提前 handoff 整項任務。
[完整 exact-head rejection](https://github.com/ajoe734/pantheon/pull/5600#issuecomment-5548915851)。
03:03 supervisor 已將原任務派回目前 owner Antigravity，原驗收未放寬。

### 原始測試數量的精確複驗

Root 在原 signed audit ref `675a488d78e8f991e2f1ecfc92e595b2d84625a1`
掃描 BFF 內全部 336 個「檔名含 test」的 Python 檔，而非只計 grep 命中：

| 分類 | 檔數／案例 | 判讀 |
| --- | --- | --- |
| AST direct BFF main imports | 215 檔 | 逐檔記錄來源 hash 與 import 行號 |
| 其他 domain main imports | 2 檔 | telemetry、evolution；不能混稱 BFF composition |
| multiline subprocess import main | 1 檔 | test_development_route_boundary.py 的子程序案例 |
| AST parse errors | 0 | 未因語法錯誤漏算 |

上述分類不是互斥集合：兩個 other-domain main 檔案本身也在 215 個
BFF main-importing files 內；加上 subprocess 案例後，已記錄類別的
unique file union 是 216，不是 218。原報告的 218 direct-file 數字
無法按此次完整 AST 方法重現，不能把重複類別相加成檔案數。

因此原報告「218 個直接匯入 BFF main」的標示必須區分類型，不能拿單一
數字要求 worker 補出不存在的三檔。這是方法／分類更正，不是將原整改範圍
缩減為 215 個測試。完整歷史與現況 direct／indirect／subprocess coverage
仍須對照。原 signed snapshot 未改；新證據置於
[基準逐檔 inventory](/tmp/pantheon-bff-baseline-main-import-inventory-20260905.json)，
已正式 note 交給 owner，並確認新 worker 實際讀取了該檔。

另外要求為 path_dedupe 移除的七組過時 assertions 製作現行路由
coverage 對照；部分路由目前應為 active 202，不能恢復錯誤的 410
expectation，也不能只刪除測試而不保留現行契約覆蓋。

### 測試遷移暴露的產品 projection 差異與合約邊界

03:20 UTC 發現 BFF-TEST-ARCH-001 的 WIP 在
`management_read_models/service.py` 新增九行 positions market-value
fallback，超出現有 test-only artifact contract。Root 未代為提交或
默認授權；已正式要求保留證據、先完成獨立 test batches，若確須產品
改動，須由 owner checkpoint／blocker 後走正式 contract 或 prerequisite。

隔離 subprocess 使用同一份 synthetic persona／positions 資料與
standalone router，未匯入 main，也未變更 source 或 live state：
committed service 回傳 HTTP 200、formal，但 holdings unavailable 與
MISSING_HOLDINGS_MATCH；WIP fallback 則回傳 holdings ok、無該 diagnostic。
這證明是業務行為改動，不能用 import migration 名義掩蓋。

同時確認 main 的 `_ops_read_model_entry_for_persona` 與 service 各有
persona-facts projection，而目前 production router 未注入 optional
ops callback；不能假設 main helper 就是現行 endpoint 的 canonical owner。
具體差異已寫入既有 MGMT-READ-001，要求單一 projection owner 與來源／
confidence parity，不新開另一套投影機制。尚未為九行 symptom patch
擴充 contract，亦未把不通過的 regression 當作已解。
[範圍及複驗證據](https://github.com/ajoe734/pantheon/pull/5600#issuecomment-5549012259)。

後續 worker 在 partial anchor
`5b1199c833b09afa1be672b9dea58f1590173eda` 提交五組 migration，
卻一併提交上述 runtime hunk；commit 的「不改 service behavior」說法
與實際 diff／probe 矛盾。已正式記錄，不接受越界 hunk 作為 test-only
交付。保留 in-scope 進展，要求 corrective removal 或 owner checkpoint／
blocker 後再走正式範圍修訂。該 anchor AST 精算為 205 個 direct importers，
其中 196 個非 composition；不是只有 owner 某次不完整 scan 所報的六檔。

下一筆 `f4aad94143d09ea29bac7c400be0faf583e3b7f1` 再遷移四組，
宣告累計 11 組、direct-main ceiling 201；manifest 已將原三項驗收改回
in_progress，沒有再把局部成果標成全數 passed。Root 獨立重跑本批
settings／audit／evolution／telemetry：20 passed / 10.31 秒，
90 秒硬限制，四個測試檔前後 SHA-256 一致。Worker 同時改其他檔案，
所以不是整棵 worktree clean 的宣告；也不是整項任務的最終 review。
Production incident wiring assertion 被轉成 router-only 的 coverage
保留要求，同樣已記在 PR／canonical note，不能因遷移而少驗組裝層。

之後 `36920314680556709d0b8d47fbf34e725215ba11` 再提交 alpha factory、
inspiration graph、兩個 lineage suites，owner 宣告累計 15 組與
direct-main ceiling 197，83 tests passed（包含 architecture gate）。
Root 尚未獨立重跑此四檔；保留為 owner partial evidence，不混入上述
已獨立重跑的 20 passes，也不宣稱整項 BFF-TEST-ARCH 已完成。

另一次唯讀複查比較 alpha／lineage／settings／audit 四檔與 `0ec56e2`：
115 個 assertion AST 全數保留，且仍執行真正 router／store。這是有效進展。
但 fixture 各自複製 token／read-role／admin-MFA 政策，部分與 production
allowed-role filter 不同；audit 又因未注入 page_slice，改用 router default，
與 production 注入的 paginator 在 malformed／negative token 時不同。
這不是宣稱 production auth 漏洞，也沒有被刪掉的 invalid-token assertion；
是測試 owner 選擇漂移。已要求使用 typed identity／recording guard doubles，
保留現有 auth-owner policy 與 production paginator 的針對性覆蓋，不新增
通用 main fake，也不在 test-only 合約中修改產品行為。
[獨立複查](https://github.com/ajoe734/pantheon/pull/5600#issuecomment-5549174038)。

04:03 新 partial anchor `be90d6f37fea6db53407392dd73e1b1ac8a16d74`
再遷移 CW02、evolution、trading pulse、evidence suites。Root 隨後以
90 秒 hard timeout 重跑這四檔，31 passed / 24.27 秒、一項 dependency
deprecation warning；四個 source SHA-256 前後相同。這是局部驗證，
不是全量解耦；原 service 越界 hunk 與完整解耦要求仍未結案。

04:05 supervisor 回收 lost lease 後正式改派 owner Claude、reviewer
Antigravity，目前 generation 6；root 04:07 再次交接 partial anchors、
原驗收、越界 hunk 與 fixture／paginator findings。未自行改派或抹除
前 owner 的成果，也沒有把 notes 寫入等同 worker 已讀取／已完成。

後續 anchor `edac50ed56d6839d2f17067ab806e5cb5d84d9f1` 保留；04:25:26
supervisor 再次 lost-lease 改派 Antigravity、reviewer Claude、generation 8。
舊 owner 所啟動「79 檔 sweep」log 只顯示三檔完成、第四檔開始，04:25 後
未更新；不能把它當 79 檔已驗證或仍 live 的證據。Root 已交接此前所有
有效 anchors／範圍 finding，並要求以真實 handle 確認後才處理未完成驗證。

### 新確認的治理契約差異與待決範圍

[CW01／CW03／CW04 重新盤點](/tmp/pantheon-governance-cw-contract-revalidation-20260905.md)
記錄三個安全、無 source/data/provider writes 的 direct probes：合法
risk_review 在 governance layer 回 422；committee detail 缺少現行 command
validator 仍使用的 action policy 投影；memo metadata／detail 缺少 published
contract 的 surface／projection。DI seam 已存在，不構成全量測試遷移 blocker。

進一步發現 `tests/test_governance_router.py` 的較新 characterization tests
明確驗不同 envelopes／payload 值，和已發布 CW 文件及保留的 CW suites
衝突。Current consultation schema/model 也區分 request_type 與 consultation_type，
不可混用同一 enum；但要選擇完整公開格式，仍須一份明確正式契約。
Root 已向操作員提出非阻塞問題：以已發布 CW 為準修復（建議），或正式改版
為新治理格式；不並存兩套 alias/envelope 湊綠燈。

原 DOMAIN-WRITERS 含 governance scope，卻排在 BFF-TEST 後；已設計最小
governance owner prerequisite 的責任邊界，修復既有 router/service、共享
CW03 read/command policy，不新增 factory/store/globals。契約選擇尚未回覆，
該產品 corrective 尚未建立／簽章／接收，不假稱已派工。其他 Python／package／
test migration 工作繼續，沒有把全目標標為 blocked。

### 空主機部署

舊 PR #5550 是 CLOSED、未合併，原 unsafe head 不得重新當作合格交付。
但目前 dev 已有另一筆
`1404ff35fea3f8e0ab8db1155b3b7f272a76f5ff`，實作只接受 HTTP 404
的 empty-host 判斷，並加入 auth / server / transport / parse 負向測試。
因此舊任務「核心修正尚未做」的文字已過時；已正式更正給 worker。

原任務接續盤點合約內仍存的 retired fallback 與交付證據；
沿用既有修正，不重寫相同機制。不進行主機部署或以原已停用環境驗收。

續查證實：修訂 head `249cd9c03675e2566a3d5f1e6a4be06af405da45`
只移除 workflow 的 staging 預設值，但底層 `deploy_nonprod_vm.sh`
仍在輸入空白時恢復 retired 預設值。無網路 dry-run 重現，獨立 reviewer
也重現後退回。Owner 保存成果並正式 blocker；Human/Ops 增列上述既有
部署目標 owner 檔案至 contract 後重新派工，不新增第二套 target resolver。

後續 head `cda18f6881dbc276ae098ed41615bae51f0b143b` 已修正 explicit-empty
dev 欄位被恢復預設值的問題。隔離 dry-run 驗證空 project 會拒絕，
但兩個 AGENTS 明確退役 IP `35.201.239.38`、`34.81.75.241` 與
`/home/lupin/code/pantheon` 仍會被接受。
[唯讀負向檢查與 PR 意見](https://github.com/ajoe734/pantheon/pull/5550#issuecomment-5548706915)
已記錄；所有探測只在本機使用合成設定與 `--dry-run`，沒有連線到
上述主機，也沒有改 live config。

獨立 reviewer 在 rebased head
`41190341030efa8ce551811f6722052db2058587` 重跑 199 passed / 2 skipped
並批准，但 root 回讀確認上述缺口仍存在，故正式 reopen 原任務。
02:25 cron 同時已將 [PR #5550](https://github.com/ajoe734/pantheon/pull/5550)
合併為 `83af1dbf4f32293cd36d3cc83a56232160685766`；其 integration receipt
因 delivery binding 已變更而拒絕写入。02:26 正式回讀為 in_progress，
不是 done。已要求沿用原任務、既有 target validator，從 current dev
交付 corrective PR 與新 exact-head 審查；不得只凭舊 PR 合併結案。
此次沒有主機部署，也沒有 root 接管 worker 的程式修改。

上述缺口後續由同一原任務交付
[corrective PR #5601](https://github.com/ajoe734/pantheon/pull/5601)，
head `a396b7f67b20c0240ab52d1cf2a159bbc7806c41`。Root 重跑九組隔離
dry-run：三個 synthetic dev／staging control／staging exec 正向案例通過，
兩個遺漏 IP、dev 舊路徑、staging control／exec 舊路徑及 exec health
退役 IP 均拒絕。獨立 Antigravity2 reviewer 重跑 220 項測試通過，
required checks 與 canonical review gate 通過。

02:40 既有 integrator 合併為
`161f0a0d7c179fb5d5299dc9d4bdcaa2f5b11926`；02:41:59 UTC 原任務
正式 done / archived。此為 source 與任務交付完成，不是主機部署驗收。

### 原有 architecture invariant 的回歸

目前 `personas/router.py:41–43` 又有迴圈將 private service symbols
複製進 `globals()`；git blame 指向 `c46c505889`，為 earlier package
normalization 之後的 helper-binding 修正。它違反原 no-symbol-forwarding
驗收，故不能用 BFF-PACKAGE-001 的歷史 done 證明現況仍滿足該 invariant。
已將具體證據與「不能直接刪除而重新造成 NameError」的約束寫入既有
BFF-ROUTER-STRUCT-001，沿原 router split／明確 dependency owner 修正，
不另開重複實作機制。

03:35–03:36 另以 isolated two-instance probe 確認
PersonaService constructor 在 service.py:13936／13938 寫入 module-global
read_store／command_store；第二個 instance 建立後，第一個 instance
保有自己的 port，但 module stores 已指向第二個。這是隱性共享狀態的
實證，尚不是 live 跨租戶洩漏實證。已追加至原 BFF-ROUTER-STRUCT-001，
要求 helper 消費者收斂至 instance-scoped ports 與兩 instance isolation
測試。Import-only corrective 不拿來掩蓋或接管這項結構工作。

### PPL-ALLOC-007 已交付功能、但狀態復原受阻

已驗證 [execute-plans PR #285](https://github.com/ajoe734/execute-plans/pull/285)
合併至 dev，merge `c62c0e8b9a49643c42f67614c542578afb233e84`，
integration-gate SUCCESS。已合併的獨立 Claude 審查 task brief 也存在。

同時存在舊的 completed archive 與較舊的 blocked active row。
正式 `reconcile_merged_done` 通過來源證據驗證後，在封存衝突處拒絕，
沒有成功結案，也沒有覆寫歷史封存。由上述 archive prerequisite 解決；
不得重新開發 PR #285 的功能，不得拿無關 completed task 充作替代證據。

02:00 UTC 的真實唯讀 preflight 額外發現：candidate 要驗證歷史
Claude → Codex2 reviewer 轉派時，現存 canonical audit 找不到 2026-07-19
的原始事件。Scope 與 delivery 一致仍不足以通過該證據檢查。
原始 archive bytes 已驗證完全等於 dev-merged
`ecc3358c24605d0cd6ef3441d219121729188cf5`，SHA256
`cf95cc79c44a027f42046f83572029d07563a207fa1cc66a4685b755f7ce4c78`；
其中保有先前的 reviewer_reassignment proof，但不能擅自偽造缺少的
canonical event。已向操作員詢問歷史 audit 備份路徑，其他工作繼續。

### TJ-E2E-012 不可套用同一個結案推論

這項也有歷史 completed archive，但目前 active generation 是 2，
不是已證明相同原始範圍的復原案例。舊 hosted 環境也已停用。
已留下正式註記：先確認新 generation 的範圍與 hosted 授權，
不得用舊驗收宣告現行環境可用。

## 4. Cron 與派工能力的不同邊界

既有 OPS-AUTO-INTEGRATOR-CRON-RECOVERY-001 已 done / archived：
[PR #5596](https://github.com/ajoe734/pantheon/pull/5596)，
merge `8a798bbae82f4e374be04a59db92e1c7b109b722`。
此為先前完成的正式恢復，本次重新回讀確認，不另開相同 cron 任務。

03:22 UTC 唯讀檢查目前使用者 crontab：active auto-integrator entry
恰為一條，排程 `*/5 * * * *`；既有 cron log 最後更新 03:20:01 UTC。
這不等同所有 OS 使用者／systemd timer 的全機盤點，也不證明產品部署。
04:06 再查 cron log 已更新至 04:05:01 UTC；automatic bridge drain 的
04:06:58 真實狀態仍為 unavailable / missing pydantic。

04:06 當時 supervisor PID 96329，heartbeat 更新；本交接範圍的 BFF 測試架構、
package corrective 與 Python runtime 修復依 supervisor 派工執行／審查。
同一 supervisor 亦有其他 chatbox 的 Persona owner、Loop 8/9 probe 與
deploy preflight 任務；已唯讀核對各自 artifacts／責任，不接管其交付或
把它們混算為本次原 20 項的驗收。當時封存復原來源已交付，
但尚未 promote，也未繞過 PPL 歷史證據缺失。
當時 `/usr/bin/python3` 缺少 pydantic，automatic packet intake
會記錄 `assistant_dev_packet_drain_unavailable`。健康 heartbeat
不能當作所有派工入口健康，也不能當作產品已部署或完整運作。

### 05:00 正式 exact-version promotion 與 persistence

- 已合併來源 `62ecea34b5ae51bb35f484f1a7a78d39fecd9c08` 用 isolated clone
  與 no-clobber move materialize；origin 是正式倉庫、commit 屬 origin/dev，
  clean tree `c40608aa694e51fab6089202ec2aa73cddd61a33`。
  未執行會 stash/reset shared staging 的整支 sync-dev-root.sh。
- 使用該版本唯一 provisioning helper 建立獨立 per-SHA venv：pydantic
  2.13.5、cryptography 50.0.1、packaging 26.3。保留回傳的 bin/python3
  symlink invocation，未 resolve 到 ambient Python。
- Candidate 62ecea／rollback 20282 + 新 Python 都完成 discover-only 與真實
  supervisor／bridge imports。Public verifier／bwrap preflight passed。
  Discovery 的 integration fetch／write probe 已知並列明，非全唯讀。
- 比較 candidate 與 live config，扣除既有產生欄位後完整相等；沒有額外
  dispatch／account／auth 政策變更。既有 watchdog promotion lock 包住正式
  promotion CLI，內部沿用 integrator lock，未新增 launcher／cron。
- 05:00:11 記錄既有 PID 96329 → 1617163，config SHA256
  `eb45211e146ad75b364d119b7440da808acafecd27807b63ec48e1cca359feab`。
  [工具產生的 promotion evidence](PYTHON_RUNTIME_PROMOTION_62ecea34.json)。
  初次 05:00:22 health 只因 first loop 未完成而未全綠；05:01:24 全部通過。
- 切換前後四個 worker 的 PID、task generation、process generation、lease
  acquired_at 均相同，lease 正常延展；未殺掉／重派它們。包括 BFF-TEST-ARCH
  generation 10／Antigravity2，以及三項其他 chatbox 任務。
- 05:01:25 經既有 installer dry-run／正式 install 重指 persistence：仍只有
  一個 enabled/active systemd user watchdog timer（KillMode=process），
  一條 `*/5` auto-integrator cron；均指 exact 62ecea 與相同 live config。
- Status root、git-external task journal、兩倉 source/integration roots 保留。
  Root 沒有修改 shared repository source，沒有 hosted/product deployment。

### Worker 背景測試提前收尾：已確認的原因

兩次 Claude owner run 都是 voluntarily completed／exit_code 0／signal null，
CLI result 是 success／end_turn，不是 runtime timeout。BFF run 在
04:25:20、Python run 在 04:37:35 結束；其後各自背景 task handle
bz3j22zqm／b5nzmfkf4 出現 killed／stopped 記錄，wrapper/child PID 均不存在。
兩份 log 都沒有 TaskOutput 呼叫。

目前 immutable worker_runner.py:904–923 在 direct child 正常退出後清理剩餘
process group，這是預期的 one-shot session cleanup；不可關掉 cleanup、
延長假 lease 或再建 watcher 掩蓋。Immediate handover 要求用 bounded
foreground verification，或在 final／handoff 前以真實 TaskOutput／session
handle 等到 terminal 結果。CLI 本身也有 exit cleanup；不能把每次 kill
精確歸因給 runner，亦不可關掉任何一層清理。

已核對 17 項 active/nonterminal task 的 artifact／描述，新增小範圍
OPS-WORKER-FOREGROUND-VALIDATION-PREREQUISITE-001，七個精確 artifacts，
依賴 Python prerequisite done，避免共享 provision test 的平行編輯。
[不可變 SA/SD](ONESHOT_VALIDATION_SA_SD.md) 沿既有 wakeup／provider runtime.env
修正政策，不新增背景程序機制。Source/live 生效驗收分列。

04:55:51 v1 已簽章入列；05:00:19 新 supervisor 真正自動處理，但 root
packet acceptance 裡的 provider 名稱撞到 role-only validator，被正式拒絕。
該失敗紀錄保留、canonical 確認未建立任務；沒有放寬 validator 或手改 JSON。
05:01:48 以同一 immutable SA/SD、修正 acceptance 用詞的新 v2 packet
重新簽章入列；05:02:23 automatic admitted、05:02:27 drain 完成，
processedCount=1、errorCount=0。Receipt 與 durable admission record 均回讀。
Packet digest `d4f4801f158e91b5e5e7018d5ac2f42ccc930a43fc57c8dce6d200d1ec6f0c44`；
SA/SD SHA256 `f3583333813e230e566a7d2438a66ee663885388dad0f9f24413d5ef84a32779`。
05:03:27 supervisor 將 canonical task 設為 in_progress；owner Codex、
reviewer Claude，既有 worker run `codex-20260905T050324Z-e4a6b5fb`、
PID 1636121、generation 1，status command runtime 正確綁定 62ecea。
七個 artifacts 與 Python dependency 均由正式 show 回讀確認。
此次 root 沒有手動 drain；可作為自動開發派工入口恢復的實證。

### 05:05 BFF 測試架構增量複驗

HEAD `067af04f5f038282cc0147d96e4377aaa780c564`，兩個未提交測試檔。
Direct main importers 由 be90／edac 的 193 降至 189 committed／188 WIP；
仍不是原完整 SA/SD 結案。先前 out-of-contract 九行 Management projection
fallback 已移除，service bytes 與原 0ec56 baseline 完全相同，保留有效修正。
Management fixture focused 11 passed 有效，但先前 combined 1 failed／
163 passed 尚無全批 rerun 取代。

新的 WIP coverage finding：test_consultation_surfaces.py:74–79 自製 persona
endpoint，但同檔 persona-links 測試仍宣稱驗證真實 HTTP 連結。Production
personas/router.py:123 已有同一 endpoint，應 mount 該既有 router／narrow
dependencies，不可用 test endpoint 取代。已正式 note 原任務並留下
[PR #5600 增量意見](https://github.com/ajoe734/pantheon/pull/5600#issuecomment-5549534293)。
這是當時未完 WIP 修正指引，未接管實作或另建重複任務。CW 格式歧義與
persona fixture 的最新修正見以下 05:34 補充，不再沿用當時的待選擇狀態。

### 05:34 正式前置交付、runtime 生效與新 corrective 接收

Foreground prerequisite：canonical 05:23:14 archive/done；獨立 reviewed
head 8232aa8b0839eeca8061a7e00e211eafd823206a、evidence blob
ddf54897ce6364b24a95d49ba0f163fc5bc032bd。[PR #5608](https://github.com/ajoe734/pantheon/pull/5608)
05:15:21 合併，merge dd3f0563a6a3f9ca2976a354de29221d91665a73。
Owner/reviewer completed suites 66 passed + 2 subtests，cleanup 3 passed；
advisory 另跑新增三項 regressions 3 passed + 2 subtests 與真實模板/config probes。
沿既有 runtime.env/template policy，沒有改 adapter/runner 或新增程序機制。

同一 immutable source/Python pair 完成現有 provision 與 preflight；05:30:46
以現有 promotion lock/CLI 替換 supervisor PID 1617163→1801618。
[工具生成 promotion evidence](WORKER_POLICY_PROMOTION_dd3f0563.json)。
05:31:59 require-watchdog health healthy=true、failed_checks=[]；source tree
71fe58ba1ece01ed752c7b3458a7e8988a3152aa，config SHA256
e4656be755b6d372ecad3399e0341111e22efe32921045b7e73d633556957cdb。
既有 installer 更新同一 enabled/active systemd watchdog timer 與唯一 */5
auto-integrator cron；沒有增加第二個 timer/cron。Status root、journal、
repository source/integration roots 均保留，舊 source/Python pair 留存供回退。
BFF-TEST worker PID1563014、generation10、process generation 與 lease
acquired_at 04:51:33 前後一致且存活，未重啟或製造 lease。

CW 的 formal V2 ledger functional_scope_change=none 與搬移 catalog 已消除
格式歧義；Git 回溯證明 port extraction 另遺失原 consultation subtype/priority
映射。因此 corrective 包含原 port 的單一 mapping owner，不只修改 router。
[不可變完整 SA/SD](GOVERNANCE_CW_CONTRACT_SA_SD.md)，SHA256
00e36a3742b5fdf8dd331988d92044c6600f860e880160062fc7f0ecc8c2d1b3。
七檔 active-owner overlap 複查通過；依賴已完成 package corrective/composition，
保留既有 BFF test、writers/journal/overlay 所有權，不形成循環依賴。

Packet pkt-bff-governance-cw-contract-corrective-prerequisite-20260905-v1
05:31:40 signed queued、05:31:56 automatic admitted，digest
47b367f1a10ec57deebfd215f859d8d5e573172700890dc4ecc8a5d2482e7531；
receipt durable/readback verified、errors=[]。05:33 canonical show 回讀
in_progress、owner Claude/reviewer Codex、兩個 dependencies 與七檔正確。
真正 worker run claude-20260905T053232Z-f14a9c3c、PID1810941，
CLI PID1811225；新 dd3f0563 command runtime 與 prompt TaskOutput/bounded
文字均已查到。僅白名單讀取 CLI process env，確認
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1，不輸出其他環境或 credentials。
此為 live policy delivery/automatic dispatch 實證，不是未來所有測試必成功。

BFF 測試架構 HEAD e8e4136：ASK001/004 改遷移後仍共用 global app/client/
store/idempotency。Actual nested-client probe 重現 outer read 被覆蓋與
相同 key 重建第二筆 session；新 gate 同時漏 conftest/helper path mutations。
已正式 note 原任務，並提交[PR #5600 隔離 finding](https://github.com/ajoe734/pantheon/pull/5600#issuecomment-5549695575)。
要求 context-owned dependencies/common typed fixtures/nested replay 回歸，
不是另建測試機制或新平行任務。保留 55 個 AST 不變的既有 assertions。
先前假 persona endpoint 已在 WIP 改 mount 真實 service/router；保留有效
修正但尚未獨立 rerun。185 direct main importers 仍只是增量，不是全 scope 結案。

### 05:56 持續執行與新 checkpoint 複驗

前一輪是實質 progress：foreground prerequisite canonical archive、正式
runtime promotion 與 CW task automatic admission 都已改變 authoritative
state。本輪未縮小原 scope；檢查新 source/checkpoint、將新反例回饋原 owner。
05:55:56 supervisor dd3f0563 PID1801618 healthy、無 loop error；05:56
實際 ps 確認 BFF-TEST PID1563014/CLI1563300 與 CW PID1810941/CLI1811225
均存活。沒有因觀察時間長而 restart 或改寫 lease。

BFF-TEST 新 pushed checkpoint/PR head
14bcbc39a67b29d9eaf617ecd454ddf89ea31861，四組新遷移、ceiling181。
Root AST 複驗新四組加 consultation 共五檔的 28 tests/221 assertions
全部保留。原 global fixture 問題在該 commit 還存在，但最新 WIP 已在
ASK001/003/004、core/journal 修正；core 新 actual nested probe 確認
distinct clients、outer conflict 前中後皆409、inner key 獨立200、outer
replay仍200。這是有效進度，未混稱為新的 committed/full-suite 驗收。

Exact-head helper body hash 複查發現一組 identity helper 十份相同，另有
三份相同 error/role/exception handler；原 SD8.5 common typed fixtures
尚未完成。新的 cookie-session WIP 把被測 bearer/cookie 分類抄進 fixture，
須保留真正 production auth boundary coverage，不能以 assertion 沒刪
當作覆蓋未退步。Helper scan 擴大有效，但新增 baseline allowlist 保留
兩個 BFF-dir path inserts 不符原移除要求，應刪 owned blocks/例外。
Journal test guard 實測 viewer PATCH200，與 production 權限不同；需保留
原授權覆蓋，不把 fixture政策通過誤稱為 product權限驗證。

上述 committed vs WIP、有效修正與 residuals 均詳記
[增量盤點](../pantheon-bff-test-isolation-review-20260905.md)，並更新同一
[PR comment](https://github.com/ajoe734/pantheon/pull/5600#issuecomment-5549695575)
與正式 Human/Ops note。PR5600 尚 open；CI Commit trailers 於 run33947797070
因兩個歷史 checkpoints be90d6f/3692031 缺 task prefix/trailers 失敗，已要求
owner 依現有 clean checkpoint/history 工作流程修復，不關 gate 或冒造 review。

Persona 真實 router 的 two-client probe 新增更強證據：第二次 service
建構會改變第一個 client 的 source/state metadata，但其 persona data
仍為第一實例資料。這是繼承的 product-global debt；已正式補強原
BFF-ROUTER-STRUCT-001 note，保留原 owner/artifacts/JOURNAL dependency。
沒有把它新增成 BFF-TEST 門檻，沒有新增 duplicate prerequisite 或 global
reset workaround；原測試可繼續真實 route coverage，誠實保留 source 限制。

CW corrective 已實際修改四個 owned source 檔，尚無 commit/PR。
正向進度含還原六 subtype/critical mapping、context refs、移除 synthetic
create、公開 envelope 與 main command validator 共用 governance projection。
Actual service negative probes 在穩定 SHA342ce03e 證明：record unavailable
可能被 dataset ok 忽略、memo degraded 可蓋過 dataset unavailable 而暴露
應隱藏的內容、缺來源/default callbacks 可變成 healthy/CTA可用，以及
omitted redactor 可無視 empty capabilities。正常 production main/router
目前有接 real callbacks，未宣稱 hosted route 已資料洩漏。
[CW owner review](../pantheon-governance-cw-owner-review-20260905.md) 已建立，
正式 note 要求修同一 availability/policy owner 與負向矩陣，不另造 fallback。
這些均屬原 signed SA/SD，不是擴張新業務規則；未將 WIP 當作修復完成。

## 5. 後續完成條件

1. 廢碼清理已達 canonical done；測試架構接續中，須依原 SA/SD 真正
   解耦並取得獨立 exact-head 審查，再推進單一寫入者／循環真實性鏈。
2. 兩個前置修復已完成來源交付與 exact-version promotion，worker continuity、
   Python/runtime/watchdog health 與 v2 automatic admission/readback 均通過。
   新 foreground completion policy 亦已 source archive、exact-version promote，
   真實新 worker 的 env/prompt 驗證通過；CW source corrective 仍在執行。
   [唯讀 promotion 準備紀錄](PROMOTION_PREFLIGHT_NOTES.md) 另列 immutable
   source materialization、watchdog promotion lock、rollback interpreter
   與 discovery 副作用；該文件是歷史準備，實際執行以本節與工具 evidence 為準。
3. 使用既有正式結案指令復原 PPL-ALLOC-007，驗證封存 bytes 未改、
   terminal fact 已建立、active row 移除且依賴可正確解析。
4. Management、Agora、12 循環與 hosted journeys 仍須各自驗收。
   尚未交付、未驗收或需 hosted 授權的工作均保留未完成，不混算為閉環。
5. 空主機 source corrective delivery 已完成；後續 hosted acceptance
   仍須按原授權與 exact-version 部署契約執行，不能以本次 source merge 代替。

## 6. 原始 20 項 execution scope 全量回讀

2026-09-05 02:58 UTC 逐項透過現有 Human/Ops show 回讀，並核對已接收
16 項的 packetId 均為原 `pkt-pantheon-structural-closure-functional-v2-20260903`。
04:09–04:10 再次逐項 show，以下 lifecycle 分布不變；BFF-TEST owner／
reviewer 已依 supervisor 正式改派。四個尚未接收項目均回傳 Unknown task，
沒有將它们的未接收狀態誤作 todo 或 done。
結果為六項 canonical done、一項 in_progress、九項 todo、四項尚未接收。
這不是業務循環閉環率；封存狀態不自動證明現行架構 invariant 或 hosted 可用性。

05:06–05:07 使用新 qualified runtime 的 Human/Ops show 再逐項回讀全部
20 個 IDs，分布仍為 6 done／1 in_progress／9 todo／4 Unknown task。
BFF-TEST-ARCH 目前 owner Antigravity2、reviewer Codex；其餘前後相依與
未接收邊界保留。新 foreground prerequisite 另外列計，不混入原 20 項。

05:36:54 再使用 qualified dd3f0563 Human/Ops 逐項回讀全部 20 個 IDs：
仍為 6 done／1 in_progress／9 todo／4 Unknown task，無 lock retry、無
artifacts 或 dependency 記載差異。新的 CW corrective 另列，不混算原 scope。

| 原任務 | Canonical 回讀 | 完成邊界／剩餘條件 |
| --- | --- | --- |
| PLAN-ADMIT-001 | archive / done | 計畫接收，不是產品验收 |
| STRUCT-OWNERSHIP-001 | archive / done | owner 規劃已交付，現行程式仍須逐域符合 |
| ENV-STAGING-PROD-PLAN-001 | archive / done | 環境規劃，不是實際環境部署 |
| BFF-PACKAGE-001 | archive / done | Persona forwarding 回歸已另列於原 router 任務 |
| BFF-COMPOSITION-001 | archive / done | typed ports 來源交付；whole-app import-time coupling 尚不能視為完全解除 |
| BFF-DEADCODE-001 | archive / done | 指定 17 段 unreachable tails 已刪除，不代表全庫無廢碼 |
| BFF-TEST-ARCH-001 | active / in_progress | 原完整分類、typed fixtures、非 composition 解耦仍未通過 |
| JOURNAL-OWNER-001 | active / todo | 等待測試架構與 ownership 依賴 |
| BFF-ROUTER-STRUCT-001 | active / todo | 等待 journal owner，保留 router split 與 no-forwarding 验收 |
| DOMAIN-WRITERS-001 | active / todo | canonical domain writers 尚未完成 |
| OVERLAY-RETIRE-001 | active / todo | 等待 writers／journal，不能先刪狀態資料或覆蓋層 |
| AGORA-CHAIN-001 | active / todo | 還不能宣稱 Agora 業務鏈閉環 |
| LOOP-TRUTH-001 | active / todo | 12 循環真實性仍待驗證 |
| MGMT-READ-001 | active / todo | Management canonical read models 尚待整合 |
| FE-STRICTLIVE-001 | active / todo | execute-plans 獨立倉庫工作，非 Pantheon 子目錄 |
| DEV-DELIVERY-001 | active / todo | exact-version 交付鏈尚待完成 |
| DEV-RELEASE-HOSTED-001 | unknown / 尚未接收 | 位於 unsigned hosted packet，等待原 one-shot operator/MFA 授權與前置工作 |
| L12-HOSTED-001 | unknown / 尚未接收 | 同上；不能使用舊環境證據代替 |
| MGMT-AGORA-E2E-001 | unknown / 尚未接收 | 同上；仍需真正的 authenticated hosted journeys |
| STRUCT-RETIRE-001 | unknown / 尚未接收 | unsigned retirement packet 等待兩個 hosted 验收依賴 |

已另外完成／進行中的 supervisor、cron、empty-host 等 prerequisite
屬執行支援，沒有拿來替代或消除這 20 項原工作。後段依賴、原 packet
簽章與 hosted 授權邊界均保留，未手動新增 canonical JSON 或跳過 gates。
