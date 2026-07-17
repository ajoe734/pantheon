# Execution Task: OPS-WATCHDOG-LOCK-QUEUE-001

## Assignment

依照已歸檔規劃
`docs/04/ops_watchdog_lock_queue_2026-07-17/archive/OPS-WATCHDOG-LOCK-QUEUE-PLAN-001.md`
修正 watchdog 在 runtime-admission lock contention 下的無界排隊。

Owner：`Claude`。Reviewer：`Antigravity`。只做 `pantheon` backend/orchestrator
範圍，target `dev`，auto-merge 關閉。

## 開工前

1. 從乾淨 task worktree 開始，讀完上述 plan、
   `docs/operations/supervisor-watchdog-persistence.md`、目前 watchdog/common
   lock 實作與測試。
2. 記錄 base SHA、`git status -sb`、remote/branch；不要使用 shared live
   checkout、中央 `.orchestrator` state 或中央 lock 做測試。
3. 先建立 repo-external isolated runtime root，所有 fixture、pid、lock、state、
   metric、activity log 都留在該 root。

## 實作要求

- 讓 watchdog probe 在 lock 已被占用時 bounded、可辨識、可聚合地 skip/return；
  不得無限等待。
- 保留 singleton supervisor flock、restart budget、resource-pressure、
  safe-mode、circuit-breaker 與現有成功 probe 的行為。
- contention 不得寫出 `healthy=true`；應記錄明確 decision/reason/metric，並
  讓下一次取得 lock 的 probe 正常更新 freshness。
- 避免背景排隊、無界 retry、粗暴 kill、刪 lock 檔或改變既有全域 lock 順序。
- 只改必要的 watchdog/lock wrapper、測試、文件與 redacted evidence。

## 驗收矩陣

1. unit：held lock + second probe bounded return；結果、exit code/JSON contract
   固定且有測試。
2. integration：模擬 10+ cron ticks，程序數 bounded、無永久 waiter、最多一個
   critical-section owner。
3. release：釋放 lock 後單一 probe 更新 state/metric/activity 一次，health 在
   freshness window 內通過。
4. safety：singleton、restart、dry-run、pressure、circuit、crash/timeout/retry
   fixtures 全部通過，且證明不會雙啟 supervisor。
5. regression：既有 watchdog/runtime-health/install/supervisor test suite 通過。

## Live acceptance runbook

- 先唯讀記錄 supervisor PID/lock、watchdog PID 數、cron/systemd 設定、state/
  metric timestamps 與 health JSON。
- 只在隔離、可回復 fixture 驗證 contention；不要把現有 live waiter 當成可
  任意 kill 的測試資料。
- 部署後連續觀察至少三個排程週期，記錄每週期 watchdog decision、程序數、
  state freshness 與 `--require-watchdog --json`。
- 現有 live waiter 若需清理，先停在 evidence 與人工核准，不得偷偷處理。

## PR / review gates

- commit 只含本 task scope，附 trailers：`LLM-Agent: Claude`、
  `Task: OPS-WATCHDOG-LOCK-QUEUE-001`、`Reviewer: Antigravity`。
- PR 必須由 Antigravity 以 exact final head 獨立 review/test；Owner 不得自批、
  自合併或自行宣稱 live 完成。
- merge 前後都要提供 redacted evidence、精確 commit/merge/deploy SHA。
- 任一 P0/P1、中央 root 被碰觸、無法 bounded、或 freshness 被錯誤標 healthy，
  立即停止並回報。

## Done

只有在實作合併、精確 SHA 部署、三個排程週期穩定、健康檢查通過、證據歸檔且
無未解 P0/P1 後，才可標記本 task 完成。規劃文件本身不代表修復已完成。
