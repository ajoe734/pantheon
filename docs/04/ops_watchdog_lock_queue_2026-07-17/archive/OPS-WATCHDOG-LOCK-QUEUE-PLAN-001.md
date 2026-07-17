# OPS-WATCHDOG-LOCK-QUEUE-001

## 目的

修正 supervisor watchdog 在 runtime-admission lock 被占用時的排隊問題。
目前 cron 每分鐘啟動一個 watchdog；`run_watchdog()` 以 blocking exclusive
lock 等待。若 supervisor 或其他受治理工作長時間持鎖，新的 watchdog 不會
快速結束，而是累積成一串等待中的程序，最後讓 watchdog-state 不再更新，
`supervisor_runtime_health.py --require-watchdog` 報告 stale。

這不是活動紀錄 recovery 失敗，也不是授權問題。這是一個新的 watchdog
single-flight / lock-contention 缺口，交由 fleet 實作。

Owner：`Claude`。Reviewer：`Antigravity`。Priority：P0。Target：`pantheon/dev`。
Auto-merge：off。

## 已觀察證據

- 2026-07-17 02:29:15Z 曾有 `healthy=true` 的讀取樣本。
- 之後唯讀檢查只失敗 `watchdog_probe_fresh`；supervisor 仍存活，沒有
  loop error。
- supervisor PID 405306 處於 `locks_lock_inode_wait`。
- 多個 watchdog 程序同時等待 `.orchestrator/runtime-admission.lock`，而非
  在 cron overlap 時快速退出或合併。
- 本計畫不授權先殺程序、刪 lock、改 crontab 或改任何 live state。

## 不變條件

1. 任何時刻最多一個 watchdog probe 可以進入需要 runtime-admission lock 的
   critical section。
2. 競爭者不能無限 blocking；它必須在明確、可測的 bounded 時間內回報
   `skipped/contended`（或等價的非錯誤結果）並退出。
3. lock 釋放後，下一個正常 probe 必須能更新 watchdog state/metric，健康檢查
   可在設定的 freshness window 內恢復。
4. 不能因此啟動第二個 supervisor；既有 singleton flock、restart budget、
   resource-pressure、safe-mode 與 circuit-breaker gate 必須維持。
5. 不可遺失 watchdog evidence；競爭/跳過要有可辨識、可聚合的 decision/metric，
   且不能把跳過誤報成 healthy。
6. 不得觸碰活動紀錄、task archive、BFF/frontend、交易資料或中央 status root。

## Fleet 實作範圍

- `.orchestrator/supervisor_watchdog.py`、共用 lock helper（若確有必要）、
  `scripts/run-supervisor-watchdog.sh` 或 watchdog install 的最小相關變更。
- 直接對應的單元/整合測試與隔離 runtime fixture。
- 文件補充：contended probe 的語意、exit code/JSON contract、systemd/cron
  single-flight 行為與診斷方式。
- 不得用背景 daemon、無界 retry、粗暴 kill、刪 lock 檔或改全域 lock 順序
  來掩蓋問題。

## 必須交付的驗證

### 自動測試

- 持有 runtime-admission lock 時啟動第二個 watchdog：在 bounded deadline 內
  結束，結果可辨識為 contention/skip，且不寫假 healthy state。
- 連續模擬至少 10 次 cron tick：程序數不會無界增加；最多一個 active
  critical-section owner，所有競爭者都有 terminal result。
- 釋放 lock 後再 probe：state、metric、activity log 更新一次，health check
  在 freshness window 內通過。
- supervisor singleton/restart gate、resource pressure、dry-run 與既有
  watchdog tests 全部仍通過。
- crash/timeout/retry fixture：lock owner 異常結束時不留下永久阻塞；不會
  重啟活著的 supervisor。

### Live acceptance（只可由 fleet 依核准 runbook 執行）

- 先做唯讀 baseline：supervisor PID/lock、watchdog PID 數、state/metric
  timestamps、runtime health、cron/systemd 設定 hash。
- 在隔離且可回復的 lock contention probe 中證明 bounded exit 與程序數上限；
  不得直接清理現有等待者作為「修復」。
- 安裝/部署後觀察至少三個排程週期：無新無界 waiter，watchdog state 持續
  更新，`--require-watchdog --json` 為 healthy。
- 若現有 waiter 需要處理，另提明確 live-repair 步驟與人工核准；本 task
  不包含任意 kill。
- 提供 redacted evidence：commit/PR exact head、測試命令與結果、前後程序
  計數、lock holder、state/metric hash/timestamp、health JSON、部署 SHA。

## Stop conditions

- 無法定義 bounded deadline 或 contention result contract。
- 測試會碰中央 status/lock root，或需要刪除/重建 live lock。
- 任何測試顯示可能啟動第二個 supervisor、遺失 watchdog evidence、或把
  contention 當成 healthy。
- 與既有 `supervisor-watchdog-persistence.md` 的 freshness/安裝契約衝突。
- Antigravity exact-head review 有未解的 P0/P1。

## 完成定義

實作 PR 通過隔離測試與 Antigravity exact-head review、合併到 `dev`、以
精確 SHA 部署；連續三個排程週期無無界 watchdog waiter，health check 通過，
並有完整 redacted evidence。規劃本身不等於修復完成。
