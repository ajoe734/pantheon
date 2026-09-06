# Python prerequisite：交付後 promotion 檢查紀錄

2026-09-05 04:12 UTC。這是唯讀操作準備，不是 promotion 成功證據，也不是
新的 launcher／dispatcher／cron。PR #5599 尚未通過剩餘原驗收；未執行下列
provision、clone、discovery、restart 或 install 操作。

## 已確認的現有機制

- 既有 `provision_live_supervisor_config.py --ensure-python-environment`
  是唯一 Python provisioning owner。合格 merge 後，應以該 exact SHA 的
  requirements 建立 environment，保留回傳的 executable 路徑，不 resolve
  到 base Python。
- 既有 `promote_supervisor_runtime.py` 接受 exact command-root、status-root、
  live-config、Python、明列 source/integration roots、public authority env file
  與 evidence-path。不得直接使用 WIP 或未審查 task HEAD。
- `sync-dev-root.sh` 的完整執行會 stash／reset staging；不能為了 materialize
  新 runtime 就盲目執行它。其 isolated/no-clobber clone 邏輯目前在腳本內，
  沒有獨立 materialization CLI。切換前仍須確認 exact immutable tree 的安全
  建立方式，不改動 shared dirty coordination checkout。
- `--discover-only` 不會切換 supervisor，但 supplied integration roots 會有
  write probe 和 git fetch；不能把它稱為完全唯讀。Promotion 才另做 verifier／
  sandbox preflight，這些須在 stop incumbent 之前通過。

## 切換時必須保留的邊界

現在 source 為 `20282eba2ce2304560ab7eab0cd27af824a22b8b`、PID 96329，
`/usr/bin/python3` 尚缺 pydantic。Status root／journal／worker leases 必須
沿用現有配置；不得把健康 heartbeat 當作 intake 完成。

Watchdog 已有 `.orchestrator/supervisor-runtime-promotion.lock` 的 flock
偵測及 suppress_restart 分支；promotion CLI 目前只取得 auto-integrator lock，
沒有自行取得此 watchdog lock。這是源碼讀取得到的操作風險，不是本次已重現的
live 故障。後續切換可用標準 bounded flock 包住既有 promotion CLI，沿用該
既有 suppression 機制，避免另建 launcher 或停止所有 workers；實際操作前須
再確認當時 watchdog backend／鎖定順序及取得成功。不能只建立空 lock file。

Promotion 的 `outcome=launched` 只證明 process 啟動呼叫返回，不證明新服務健康，
也不是自動 rollback。須另查 exact interpreter/source、singleton lock、fresh
heartbeat、canonical readback、worker PID/generation/lease（正常完成不誤判遺失）。
再以一筆真實待辦的 automatic pending → processed → canonical admission
確認入口恢復；已由 root 手動 drain 的 package corrective 不可重算為自動成功。

Direct promotion 不會自動重指 watchdog persistence；須依現有 backend 使用
既有 installer，先預覽再確認只有一個預期 binding。不要將 watchdog dry-run
誤稱唯讀，它仍可能寫 watchdog state／metrics。

## Rollback 與未滿足條件

Rollback 應沿既有 exact-version promotion，保留上一組已驗證 source／Python。
目前裸 `/usr/bin/python3` 已知無法做 intake，不能宣稱是合格 interpreter
rollback target。第一次 stop 前，仍須準備並驗證 rollback 組合及相同 state／
journal 邊界；不以手改 live JSON 或刪除 task state 回退。

以上來自 candidate `7020830cf` 與 incumbent 的 source read。最後合格 merge
若有相關變動，必須重讀，不照抄這份候選操作紀錄執行。
