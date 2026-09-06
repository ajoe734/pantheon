# 架構討論暫停紀錄

最新操作者前提：第一版尚未上線，不考慮前後相容。討論稿已改為首版唯一 contract、所有 callers 同步修改、重複實作同批退役；不保留 compatibility endpoint、deprecated alias 或雙寫過渡。仍未恢復實作／派工。

02:05 補充：操作者進一步要求「若新 API 替代舊 API，舊 API 必須退役」。已完成來源比對與 caller 影響的[API 收斂／退役討論稿](API_CONVERGENCE_RETIREMENT_DISCUSSION_20260906.md)；方案尚待確認，未恢復派工或擴充 contract。

時間：2026-09-06 01:59 UTC。此文件是待討論紀錄，不是核准的新 SA/SD、task packet 或完成證明。既有已簽署計畫保持不變。

## 操作者最新要求與目前工作性質

操作者要求發現架構問題時停下來討論，不要僅修正症狀。本輪 chatbox 主要執行唯讀盤點、正式 task contract 維護與 supervisor/worker 協助，未直接實作 Registry 產品程式。先前確有已交付的開發工具修復；不能把目前 Registry 產品持久化工作稱為單純 cron/tooling 救援。

已停止本輪預定的三個 Domain adapter artifact 增補、新探針與進一步派送／驗收推進。它們未因本次暫停而新增進 task contract。

## 已確認的 WIP 架構問題

快照基底 HEAD：471dc5391a0f9cbde54d51730891583043708e42。以下是未完成實作的實際問題，並非宣稱完成版已審查失敗。

| 問題 | 實際來源 | 結構修復待決策方向 |
| --- | --- | --- |
| 同一完整 spec/revision 存在平行權威 | pg_store.py:119 的 RegistryEntry table，與 :373 的 strategy_command_authority 分離；command_contract.py:465、:646 寫入完整 spec/revision。service.py:513–570 舊 API 仍使用 RegistryService，新 :1016、:1049、:1068 路由只用 StrategyCommandStore，沒有 canonical registry_id linkage。 | 建議保留既有 Registry 作為已驗證 spec/version 唯一權威。Command 層協調既有 owner 的交易／收據；草稿 metadata 與 receipt 可以分表，但不能再持有另一份可獨立修改的完整 spec/version 權威。不能用永久雙寫橋接掩蓋問題。 |
| 既有業務動作語義被替換 | strategy_adapter.py:110 submit_review→CreateDraft；:153 promote_paper→RegisterSpec；:175 activate→CreateRevision。 | 先明訂 command→責任 owner→合法狀態轉移。草稿、提交審查、paper promotion、activation 不是同一種操作；缺少真正 capability 時明確 unavailable，不借其他動作回成功。 |
| 寫入回應被當作權威讀回 | strategy_adapter.py:110–201 只有 POST；:216–235 直接宣告 committed，從輸入及 POST body 組 authoritative_readback，沒有真正 GET 和 tenant/identity/version 核對。 | 明確區分 accepted、durably committed、readback verified。由 owner 提供原始 durable receipt／確切版本讀回；BFF 不自行製造成功證明。 |

重點不是「只能有一張表／一個 class」，而是同一業務事實只能有一個可寫權威與明確的不變條件。新增 transaction 與 PostgreSQL 持久化方向可保留；是否保留某個新 class 應由責任邊界決定，不能以有持久化就視為已收斂。

## 安全暫停與保全證據

- 任務：REGISTRY-STRATEGY-DURABILITY-PREREQUISITE-001。
- 01:57:00 透過既有 Human/Ops CLI，使用 expected owner=Claude / reviewer=Antigravity 正式暫改派至 Human/Ops；generation 1→2，reviewer 保留 Antigravity。26 個 artifacts、acceptance、depends_on 與原 dev_bridge 全部逐欄比對不變。此改派僅為停止並等待操作者架構決策，不是 chatbox 接手產品開發。
- 嘗試既有 blocker 指令遭本地 Human/Ops allowed-actions guard 拒絕，未繞過。因此 canonical status 仍是 in_progress、owner=Human/Ops；不能聲稱已改為 blocked。
- Supervisor 依 generation fence 停止原 worker。PID 3433923／3434075 已不存在，01:57:30 runtime snapshot 已無該任務 worker。
- 現行 config 沒有 Human/Ops 執行 endpoint 或 owner fallback；實際 fallback resolver 僅採設定 allow-list，不自動補全其他 roster。因此目前暫改派沒有自動重派目標；未修改 supervisor、cron、設定或 canonical JSON。
- Supervisor 在 01:57:27 自動封存並清理原 worktree。封存目錄：`/tmp/pantheon-worker-worktree-archive/registry-strategy-durability-prerequisite-001-20260906T015727Z-1801618`。保留 binary diff、staged diff 及 1966 個 copied files；唯一 skipped 項目為 `.venv/lib64`，產品來源檔案已保全。不要將虛擬環境或整包未驗證變更盲目恢復／提交。
- 原 branch 與封存成果不是 PR review、merge、部署或完整產品驗收證據。

保全來源 SHA256：

| 檔案 | SHA256 |
| --- | --- |
| services/registry/command_contract.py | 576794734cc50d1c64124ee033133a184420241d275cc89857c2c319eb5986a7 |
| services/registry/pg_store.py | f0220dcd100dff0b106e361f09fd4fe29846703659cf0c5cc9b762472d0f9576 |
| services/registry/service.py | f3614d1752c0519d73a5bd459c168b558b1f21986ca16613d0ca1c55797d0a74 |
| services/control-plane/bff/command_adapters/strategy_adapter.py | 207795c7bfb051aedaecb5337931bbdf2303154b81e153e24efd6c731a6d19b1 |

## 討論後才恢復的順序

先確認既有 Registry 與 lifecycle owners 的責任邊界、command/action matrix、版本與 receipt 不變條件、舊路徑退場及相容性策略；再正式版本化 SA/SD 與必要 contract 變更，最後才恢復 supervisor 指派。討論前不新增 competing owner/store、cron 或症狀修補任務。既有 12 循環、Management、Agora、hosted acceptance 仍不可宣稱完成。
