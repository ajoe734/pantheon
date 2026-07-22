# OPS-SUPERVISOR-SINGLETON-LOCK-SCOPE-001: supervisor 單例鎖改以 status root 為界 + 測試 harness 進程衛生

## 背景（2026-07-18 事故）

02:23:49Z 出現第二個 supervisor（PID 113960）與正牌 live supervisor（PID 202546，
`--config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`）
並行約 30 分鐘，最後由人工 kill 收掉。

洩漏源特徵（診斷快照）：
- cwd = `/tmp/pantheon-worker-worktrees/pantheon/ops-watchdog-lock-queue-001`
  （OPS-WATCHDOG-LOCK-QUEUE-001 的 task worktree，該 task 在測試 watchdog/lock 行為）
- 啟動指令 `python3 -u .orchestrator/supervisor.py --verbose`（無 --config，吃 worktree 內
  預設 `.orchestrator/config.json`，paths 全相對）
- stdout/stderr 指向 `/tmp/tmpezxcarce/logs/supervisor-watchdog-restart-20260718T022349Z.log`
  （已 deleted，暫存測試目錄被清但進程沒被收）
- **繼承了 live 環境**：`PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon`、
  `PANTHEON_COMMAND_ROOT=/home/lupin/pantheon-ci-deploy/dev-root`
- 持有的鎖在**自己 worktree 裡**：
  `<worktree>/.orchestrator/supervisor.lock`、`<worktree>/.orchestrator/runtime-admission.lock`

## 根因（兩層，都要修）

### 1. flock 單例鎖的作用域錯了
supervisor 單例鎖（PR #893 引入）鎖的是 config 相對路徑解析出的檔案。從不同
root/cwd 啟動的 supervisor 各鎖各的檔案，flock 永不碰撞 → 單例護欄只防「同 root
重複啟動」，不防「不同 checkout/worktree 對著同一個 live 協調面跑」。這正是
split-brain 派工事故（見 2026-06-09 status-root split-brain 前例）的完整前置條件。

### 2. 測試 harness 洩漏真進程 + 繼承 live env
OPS-WATCHDOG-LOCK-QUEUE-001 的測試在暫存目錄（tmpezxcarce）裡拉起真 supervisor
進程：(a) 未清掉繼承的 `PANTHEON_STATUS_ROOT` 等 live env；(b) teardown 只刪了
暫存目錄，沒有 kill 已 spawn 的進程樹。

## 交付內容

1. **鎖作用域**：單例 flock 改為（或增加一道）鎖在**協調面 status root** 下的固定
   路徑（即 `PANTHEON_STATUS_ROOT` / config 解析後的 live 協調 root，例如
   `<status_root>/.orchestrator/supervisor.lock`）。任何看得到同一個 status root 的
   supervisor 實例都必須碰撞。保留原本 per-root 鎖亦可，但 status-root 鎖是權威。
2. **啟動一致性守門**：supervisor 啟動時若 env `PANTHEON_STATUS_ROOT` 與自身
   config 解析出的 status/paths root 不一致，直接 fail-fast 並印出兩邊路徑；
   測試情境需要別的行為時走顯式 flag（如 `--allow-isolated-status-root`）或
   完全清空的 env，不允許默默繼承 live root。
3. **測試 harness 衛生**：巡一遍會 spawn supervisor/watchdog 真進程的測試
   （`test_supervisor.py`、watchdog/lock 相關測試、OPS-WATCHDOG-LOCK-QUEUE-001
   新增的測試）：spawn 時用 scrub 過的 env（剔除 `PANTHEON_*`），teardown 用
   process-group kill 確保不留孤兒；暫存目錄刪除前先收進程。
4. **回歸測試**：(a) 兩個 supervisor 對同一 status root（不同 cwd）啟動，第二個
   必須立即退出且訊息明確；(b) env/config root 不一致 fail-fast；(c) 測試 harness
   spawn→teardown 後無殘留進程。
5. **文件**：docs/conventions 補一段 supervisor 單例與 status-root 界定的說明。

## 邊界

- 不動 supervisor 300s 輪詢節奏（run-supervisor.sh 有護欄，刻意設定）。
- 不能弄破 watchdog 正常的 restart 流程（watchdog 以 live config 重啟 supervisor
  是合法路徑，restart 瞬間的鎖交接要處理：舊進程死亡即釋放 flock，新進程重取）。
- 不改 worker_runner 的 PANTHEON_STATUS_ROOT 驗證邏輯語義（它已有 symlink/
  repo-root 檢查），只補 supervisor 側。

## 驗收

- 手動重演洩漏情境：在任一 task worktree cwd 下帶 live env 啟動
  `python3 .orchestrator/supervisor.py`，程序必須立刻退出（撞鎖或 fail-fast），
  不進入派工迴圈。
- live supervisor 經 watchdog restart 一輪後仍正常運作（鎖可交接）。
- 既有測試全綠，新增回歸測試全綠。
