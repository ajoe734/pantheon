# OPS-STATUS-COMMAND-RUNTIME-PIN-001

## 目的

禁止舊 task worktree 使用自己的舊版 `scripts/ai_status.py` 寫入中央狀態。所有 auto worker 的 governed status mutation 必須執行一份已安裝、可辨識 exact source SHA 的 command runtime；git、測試與 delivery evidence 仍留在各自 task worktree。

這是後續預防任務，不是 legacy overlap 的緊急資料修復。

## 依賴

必須等下列兩項合併並完成 postmerge proof：

- `OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001`
- `OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001`

第一項恢復 activity/outbox；第二項把 canonical mutation root 與 delivery evidence worktree 正確分離。本 task 不得重新發明或放寬兩者的驗證。

## Fleet 分工

- Owner：`Codex2`。
- Reviewer：`Antigravity`。
- owner 與 reviewer 必須不同；auto-merge 關閉。

## 問題模型

- 中央 `PANTHEON_STATUS_ROOT` 只決定資料寫到哪裡，沒有保證「哪一版程式」在寫。
- 長時間存在的 task worktree 可能從舊 `dev` 建立；它的 worktree-local wrapper 仍可拿中央 root 寫入。
- legacy timestamp rotation 在 2026-07-16 仍產生 1,000-line overlap，證明舊 worktree executable 能持續影響新中央狀態。
- delivery closeout 又必須讀取 task worktree 的 branch、HEAD、clean state 與 PR 證據，所以不能簡單把所有 cwd 改成中央 checkout。

## 必要行為

1. supervisor 必須給 auto worker 一個經驗證的 `PANTHEON_STATUS_COMMAND_ROOT`（名稱可調整），指向目前已安裝的 Pantheon control-plane command runtime。
2. command root 必須驗證：絕對路徑、無 symlink component、存在、git repo root、remote slug 正確、source SHA 已合併到 configured `dev`，且與 supervisor 啟動時記錄的 installed SHA 一致。
3. auto worker 呼叫 `scripts/ai-status.sh` 時，worktree wrapper 必須 `exec` command root 的實作；不得 import 或執行 stale worktree-local `ai_status.py`。
4. 執行 command 的 process cwd 可以是 command root，但必須保留並驗證：
   - `PANTHEON_STATUS_ROOT`：中央 canonical mutation root；
   - `PANTHEON_WORKTREE_ROOT`／`ORCH_WORKSPACE_PATH`：真實 task worktree delivery evidence root；
   - task/repo/merge-target identity。
5. `done` 的 branch、commit、clean、push、PR 與 merge evidence 必須來自 delivery worktree；status/archive/activity/locks 必須只改中央 root。
6. auto-worker marker 存在但 command root 缺失、不一致、落後、未合併、remote 錯誤或 path 驗證失敗時，必須在任何讀寫前 fail closed。
7. 人工、非 auto-worker 的中央命令保留明確 fallback；不得猜測最近 worktree或偷偷改用另一 repo。
8. supervisor/dev-root 更新必須先安裝 merged command runtime，再啟動使用它的 workers；證據需記錄 installed SHA 與每次 command metadata。
9. command metadata 必須可看到 command root/source SHA、status root、delivery root/source env；不得記錄 secrets。

## 必要測試

- stale worktree wrapper 實際執行 pinned current command，並以不同的可觀察版本 marker 證明沒有載入 local module。
- 中央 status/activity/archive 變更一次；worktree sentinel bytes 不變；delivery metadata 指向 worktree。
- command root relative、symlink、nested repo、wrong remote、unmerged SHA、behind installed SHA、缺 env 全部拒絕且零 mutation。
- `PANTHEON_WORKTREE_ROOT` 與 `ORCH_WORKSPACE_PATH` 不一致仍依 delivery-context contract 拒絕。
- current command 的 `show`、`note`、`handoff`、`approve`、`done` 全路徑測試。
- supervisor restart/worker environment、watchdog、worker-runner、完整 `scripts/test_ai_status.py` 回歸。
- 模擬 command runtime 更新期間，不可出現一半 workers 用舊版、一半用新版同時寫中央狀態的窗口。

## PR 與驗收

- final candidate compose 最新 `origin/dev`，PR target `dev`，auto-merge 關閉。
- Antigravity 在 final exact head 獨立測試並核准。
- postmerge 安裝 exact merge 後，從至少一個故意落後的 disposable worktree 執行完整 governed lifecycle；證明 command SHA 是新安裝版、delivery SHA 是該 worktree、中央 mutation 正確且 local bytes 不變。
- 再從 wrong-repo 與 symlink worktree 跑負面案例，必須在 mutation 前拒絕。

## 不在範圍

- 不修改產品交易、BFF、frontend、broker 或部署工作流業務行為。
- 不刪除 legacy activity archives。
- 不放寬 delivery、review、clean、push、merge、ancestry 或 audit integrity gate。

## 完成定義

只有在 exact merge 已安裝、所有新 auto worker 都由 pinned command runtime 執行、stale worktree 正負案例與真實 `done` closeout 通過，而且沒有中央／worktree split-brain mutation，才可完成。
