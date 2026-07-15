# OPS-WATCHDOG-CIRCUIT-EARLYCLOSE — watchdog circuit 應在壓力解除時提早關閉

## 背景 / 根因
`sync-dev-root.sh`(cron `:30`)偵測到 dev code 變更時會**故意** `kill -TERM` supervisor,讓 `run-supervisor-watchdog.sh` 帶新 code 重啟——這是 merged `.orchestrator/` code 上線的正常機制,不是 bug。

**問題**:這個刻意 SIGTERM 若撞上**瞬間 load 尖峰**(例如多個 worker 同時 npm ci / pytest bootstrap),`.orchestrator/supervisor_watchdog.py` 會判定 `resource_pressure:load_above_threshold` + supervisor 死 → `open_circuit(...)`,`circuit_cooldown_seconds=1800`(30 分鐘)。之後**即使 load 立刻回落**,circuit 仍硬等到 `until` 才自動關閉,期間每分鐘 `suppress_restart reason=watchdog_circuit_open`,supervisor 不重啟、ready-frontier 的 todo 全部餓死。

2026-07-14 03:29 實際發生過:circuit 因 load 尖峰開啟(until 03:59),但 03:50 時 load 已回到 4,fleet 仍卡在 2 worker,需人工清 `watchdog-state.json` 的 circuit 才恢復。

## 目標
讓 circuit 在**造成它開啟的壓力已解除**的那一個 watchdog tick 就提早關閉並允許重啟,而不是無條件硬等 30 分鐘 cooldown。純瞬時尖峰只應卡 fleet 約 1 個 tick,而非半小時。

## 修改點(`.orchestrator/supervisor_watchdog.py`)
1. circuit 檢查邏輯(約 line 369-375,`until` 到期才 close 的那段):
   - 若 `circuit.open` 且其 `reason` 屬 `resource_pressure:*`,**且本 tick 計算出的 `pressure_reasons` 為空**(壓力已解),則立即 `circuit.open=False`(early close),不必等 `until`。
   - 非 resource_pressure 原因(真正的 crash-loop)維持原本 cooldown 行為不變——不可讓真的反覆崩潰被提早放行。
2. 保留既有的 `until` 到期自動關閉作為 fallback。
3. (可選)把 intentional code-reload SIGTERM 與 crash 區分:sync 觸發的重啟不應計入 crash-loop 計數。若成本高可延後,early-close 已能解決眼前 stranding。

## 驗收
- 新增/擴充 `.orchestrator/test_supervisor_watchdog.py`:
  - case A:circuit 因 `resource_pressure:load_above_threshold` 開啟,下一 tick pressure 已清 → decision 應為 restart(circuit early-closed),不是 suppress。
  - case B:circuit 因非-pressure 原因開啟且仍在 cooldown 內 → 仍 suppress(不可被 early-close 影響)。
  - case C:pressure 仍在 → 維持 suppress。
- `python3 -m pytest .orchestrator/test_supervisor_watchdog.py` 綠。
- 不動 poll/sleep cadence(見 feedback_never_change_poll_interval);只改 circuit 關閉條件。

## 部署
改的是 `.orchestrator/` code,合到 dev 後由 sync-dev-root(:30)自動 SIGTERM+watchdog 帶新 code 上線;上線後觀察一次 circuit 開→壓力解→提早關的實跡。

Owner 建議:Claude。
