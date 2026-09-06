# 首版確認後：唯一派工、差異與未完成缺口

取證：2026-09-06 02:38–02:39 UTC。本文件是本工作階段的協作補充，不是另一個 signed task packet、canonical reviewer attestation 或已完成驗收。

## 1. 現行唯一執行入口

- 正式任務：`REGISTRY-STRATEGY-UNIFIED-CONTRACT-001`，Claude 實作／Antigravity review，generation 2，in_progress。
- 生效 [SA/SD](/home/chloe_ong_dev_cctech_support_com/code/pantheon-artifacts/dev-closure-20260906/architecture-resumption-sa-sd.md)，SHA256 `b7fbb1189cc963b2bee58f52c1f267b4e85e6782381685735db9c4dbc4677790`。該 root conversation 為 `01a06776-5119-7ad3-a360-a74741c3466d`。
- Packet `pkt-registry-strategy-unified-contract-20260906-v2`，digest `21c908ec042c93262325d579a4ddd47c53abbf0a8d5aca464b99b9a382324c57`；02:30:23 processed/admitted，materializationReadback verified。這是回讀既有紀錄，不是本工作階段再派一次。
- 舊 `REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001` 02:33:23 正式 superseded，replacement 指向上述 task；archive status 欄雖為 done，terminal_outcome 是 superseded，不能當成功交付。
- Supervisor PID 1801618；worker run `claude-20260906T023359Z-69bd9ede`，PID 3776601、child 3776786；02:39 有新鮮 heartbeat。狀態僅證明開始工作，PR／merge／產品 acceptance 未由此證明。

本地原提議的 GOV-FIRST-RELEASE-AUTHORITY、REGISTRY-FIRST-RELEASE-OWNER、REGISTRY-FIRST-RELEASE-CONSUMERS、STRUCT-RETIRE 四 task batch **沒有 signed／queued／admitted**。其 emitter 的寫入入口已禁用。避免重複的原則同樣適用規劃和派工：不得重新建立同範圍的 Registry 或 consumer worker。

## 2. 複核後仍需承接的技術差異

下表是 source-backed gap／交接要求，不聲稱生效 plan 已正式採納本地全部 endpoint／任務拆分。涉及超出 current artifacts 或改變依賴者，先由現行 owner checkpoint／提出真實 blocker，再正式修訂；不要直接編輯 immutable acceptance 或以本文件偷擴。

| 差異 | 已核對的原因與必要處理 | 現有承接範圍 |
| --- | --- | --- |
| HTTP method／auth／timeout | baseline `command_adapters/base.py:131–134` 把非 GET 送入 `_post_json`，`command_executor.py:257` 固定 POST。選 PATCH metadata／draft params 前，必須擴充既有同一共用 transport 並跑真 HTTP regression，不能新造 Registry HTTP helper／POST alias。 | Registry successor 尚未宣告 base.py／command_executor.py／其 regression；Domain 已有。必要 slice 需正式前移並交接，不能等下游後才驗證上游。 |
| Governance 批准真實性 | Governance main proposal/decide 信任 body actor/role；Registry advance 只有 lineage／caller approver hint；Deployment 接受 request approval/Registry object 或 local snapshot。只做 Registry inbound JWT 不能解決批准權威。 | 現有 Domain broad Governance／Deployment／Runtime 範圍；Registry APPROVED 如需要先驗，須明確先行 contract／能力和 scope，不能回傳假成功。不得再造 approval store／JWT engine。 |
| 單一 approval reader 與 policy | 既有 Persona `HttpGovernanceApprovalVerifier` 和 Runtime deploy authority 已各自有 reader／validity；Governance write-authority 與 control-plane approval model role lists 重複。必須消除平行一般規則，保留 domain-specific target predicates。 | 收斂既有機制，核對 tenant／exact target/version/digest／revocation／expiry／conditions；不可把新 helper 當成多加第三份權威。 |
| Typed draft parameters | 合法名稱草稿不能假造完整 spec。update_params 不是 metadata patch；須由 genuine source schema／mutable policy 定義，绑定 expected family revision／base digest，有正向功能而非全部 unavailable。 | 由 Registry contract matrix 定義、現有 owner 實作；若涉及 paper_strategy_spec／strategy_artifact schema 檔，先正式追加 exact artifacts。 |
| BFF 完整 command retirement | baseline canonical POST 是 `/bff/v1/commands`；GET 仍在 `/api/v1/operator/commands/{id}`，tracking/poll/DTO 與 action/command dual receipts 必須同批收斂。Runtime internal mount 不只 pause，還有 sponsor／approval／rollback／kill／command-state。 | Domain corrective 與現有 FE owner 同步，保留所有真實能力後刪舊入口；不要刪 mount 就把功能一起丟失。 |
| Release 兩分支合流 | 目前 Overlay→Agora→Loop→Management→FE→Delivery 與 Overlay→CW→Journal→Test→Router→Domain 分開；Delivery 的 declared dependencies 沒有 Domain。note 不是 scheduler gate。 | 交由現行整合主線正式安排 source retirement／release 依賴。未 materialized 的原 STRUCT-RETIRE 可作候選，但本工作階段沒有派出。任兩邊未完成不得 hosted acceptance。 |

詳細先前檢查在 [架構補充草案](FIRST_RELEASE_UNIFIED_ARCHITECTURE_SA_SD_20260906.md)、[API 複核](API_CONVERGENCE_RETIREMENT_DISCUSSION_20260906.md)、[架構暫停證據](ARCHITECTURE_DISCUSSION_HOLD_20260906.md)。source baseline Pantheon `471dc5391a0f9cbde54d51730891583043708e42`／execute-plans `5d4f385284b44a30e10764426a47fd808a7ae3cb`；不是新 worker 尚未交付 patch 的判決。

## 3. 已做與未做

已做：讀回 canonical successor／supersession／intake／runtime；停止本地重複 packet；保留所有已簽文件原文；現有 Domain 通过 Human/Ops 正式追加 26 個精確 artifacts，含實際 underscore Runtime core、internal API、command DTO/queue/executor 和既有 adapters/tests。另個工作階段增加 8 項後，總數為 54 = 原20 + 8 + 26；不是暗改 acceptance 或整包新任務。

未做：沒有變更現行 Registry worker owner／lease／source，沒有派出第二個 Gov／Registry／consumer 實作，沒有把架構確認當 hosted MFA。下游 Overlay blocker 未解除，沒有宣告產品部署、12-loop 或 Management／Agora 完整可用。

交接 note 必須保留當時既有 next／真實 blocker，只追加本報告定位與差異；它只表示已留正式訊息，不證明對方已閱讀、已接受 artifact revision 或完成修復。原始20項需求、三項 hosted acceptance 與實際12-loop／Management／Agora／OpenClaw exact-pair／rollback 證據仍完整保留。

## 4. 寫入後再次回讀

- Registry successor、Domain corrective、Overlay、Delivery 四項已各經正式 Human/Ops note 留下本報告和精確差異。逐項回讀證明原 next 已保留，owner／reviewer／status／generation／artifacts／acceptance／dependencies／dev_bridge 不變；這不是重新派工或 reviewer approve。
- 在唯讀核對 Persona 現有 regression 確實涵蓋 approval reader 後，再以正式 artifact-contract 增列 `services/persona/test_training_target_owner.py`。Domain 保持 todo、原 artifacts 全留、signed acceptance 與 dependencies 不改；**目前55 artifacts = 原20 + 另一協作階段8 + 本階段26 + 此 regression1**。已保留前述交接 note 並追加正確總數。
- strict V2 seq2682 證明本地四草案 ID 全部 source null／status missing；不是偷偷排進另一個 queue。直接執行本地 draft emitter 在任何 signing/queue 前以預期 exit1 拒絕，訊息明確指出已有 canonical successor。這是防止重複派工的本地保護驗證，不是產品程式測試。
- 02:43:25 supervisor projection ok／seq2678；worker heartbeat 02:43:17、event02:43:18。`pstree -p 3776601` 證明 runner→sandbox→真實 Claude PID3776857；不只相信 task in_progress 欄位。source audit worktree `git diff --name-only` 空白。
- 生效 signed SA/SD SHA256 實際重算仍為 `b7fbb1189cc963b2bee58f52c1f267b4e85e6782381685735db9c4dbc4677790`。本工作階段沒有修改它，也沒有對執行中 Registry WIP 做 source edits。

仍未完成：Governance 的正向端到端批准證据與重複 verifier 收斂、必要共用 transport slice 的實際 owner contract、兩分支 canonical release join，以及原20項／12-loop／Management／Agora／OpenClaw 的 source+hosted 全套驗收。此回合完成確認後的協作整併與正式交接，不是宣告這些 gap 已修復。
