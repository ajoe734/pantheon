# OPS-DISPATCH-PIDCOUNT-001 — ready dispatcher worker over-count freeze

## 問題(2026-07-13 live 實證)

`.orchestrator/supervisor.py` 的 ready dispatch 入口:

```python
max_concurrent = ready_dispatch_max_concurrent_workers(config)
if max_concurrent is not None and max_concurrent > 0:
    live_total = sum(len(pids) for pids in scan_live_worker_pids_by_agent().values())
    if live_total >= max_concurrent:
        return changed        # ← 靜默早退,不留任何 log
```

`scan_live_worker_pids_by_agent()` 掃 `/proc/*/cmdline` 找 worker 喚醒詞,但**每個
worker run 有 ~3 個 process 帶同一段 cmdline**(worker_runner.py wrapper、node CLI
shim、真正的 CLI binary),所以 PID 數 ≈ 3 × 實際 worker 數。

實測:5 個 worker 在跑 → 掃出 15 個 PID ≥ `max_concurrent_workers=14` → 派工
完全凍結約 40 分鐘、零新事件、零 log。config 的 14 從未真正生效,有效上限
一直是 ~4-5 個 worker(慢性 fleet 低利用率的直接原因之一)。

watchdog(`supervisor_watchdog.py` 的 `resource_pressure_reasons` →
`active_worker_count_above_threshold`)疑似用同一個 scan,同一天觀察到它因此
suppress 掉 supervisor 重啟——需一併查證修正。

## 已做的 stopgap(本 task 收尾時要還原)

2026-07-13T13:37Z:runtime config
`/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`
的 `ready_dispatcher.max_concurrent_workers` 由 14 → 42(3 倍補償),並重啟
supervisor。備份在同目錄 `*.bak-20260713T1330Z`。

**code 修好部署後,42 的語意會變成「42 個真 worker」——收尾時必須把
runtime config 改回 14 並重啟 supervisor,否則會超派。**

## 要求的修法

1. `scan_live_worker_pids_by_agent()`(或其呼叫端)改為計「不重複的 worker
   run」:最簡單是只計 `worker_runner.py` 的 process(每 run 恰一個),或以
   cmdline 中的 `--run-id` 去重。維持回傳 shape 相容(下游
   `agent_auto_dispatch_block_reason` 的 duplicate-slot guard 也用它)。
2. `max_concurrent` 早退路徑加一行 log(現況凍結完全沉默,不可診斷)。
3. 檢查 watchdog `resource_pressure_reasons` 的 active_worker_count 來源,
   同樣去重;其 threshold 語意同步修正。
4. 單元測試:偽造 /proc 樹(既有 `proc_root` 參數)覆蓋 1 run=3 PIDs 的
   場景,斷言 count=1;cap 觸發時有 log。
5. **部署注意**:merge 到 dev 不會自動上 live fleet(sync-dev-root cron 已被
   停用)。收尾要:hot-patch
   `/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/` 同步這個修正、
   runtime config 42→14、重啟 supervisor、並以「新事件持續產生 + active
   workers 數符合 14 上限語意」的 live 證據收尾。

## Acceptance

- 修正後 scan/計數在 3-PID-per-run 場景回報真 worker 數(測試證明)。
- cap 早退有 log 可診斷。
- watchdog worker-count pressure 用去重後的數字。
- dev-root 已 hot-patch + supervisor 重啟 + runtime config 還原 14 的 live 證據。
- PR merge 進 dev,commit 帶正常 trailers。
