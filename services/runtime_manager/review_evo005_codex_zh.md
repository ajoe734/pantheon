# EVO-005 Review — Codex

日期：2026-04-18
結論：`request changes`

## Blocker

### 1. kill-switch 的 safe-mode 與 audit trail 只存在 process memory，runtime-manager 重啟後會直接遺失，和 canonical contract 的「ack 前必須持久化」不一致

- 檔案：
  - `services/runtime-manager/service.py:277`
  - `services/runtime-manager/service.py:664`
  - `services/runtime-manager/service.py:771`
  - `services/runtime-manager/service.py:800`
  - `services/execution/runtime-manager/kill_switch_controller.py:303`
  - `services/execution/runtime-manager/contract.md:198`
- 問題：
  - `RuntimeManagerService.__init__()` 每次只建立一個新的 in-memory `KillSwitchController()`，沒有把 safe-mode state 或 audit log 綁到任何 durable store。
  - `execute_kill_switch()` 在 `self._kill_switch.dispatch(...)` 後直接回傳 200 路徑需要的資料，但沒有在 ack 前把 audit entry / safe-mode snapshot 寫進可重建的持久化層。
  - `get_safe_mode()` 與 `get_kill_switch_audit_log()` 都只是讀 controller memory，因此一旦 service restart、module reload、或多 worker 切到另一個 process，就會回到 `normal` / 空 audit log。
  - 這和 runtime-manager contract 第 11.2 節的要求衝突：`Every dispatch produces a KillSwitchAuditEntry before the caller acknowledges the command.` 目前實作其實只是「先 append 到本 process 的 list」，不是「先持久化再 ack」。
- 直接重現：
```bash
python3 -c 'import os,sys,tempfile; from pathlib import Path; repo=Path("/home/lupin/code/pantheon"); sys.path.insert(0,str(repo/"services/runtime-manager")); sys.path.insert(0,str(repo/"services/execution/runtime-manager")); os.environ["PANTHEON_EXEC_RUNTIME_MANAGER_DIR"]=str(repo/"services/execution/runtime-manager"); from service import RuntimeManagerService; from kill_switch_controller import HardTriggerReason; td=tempfile.TemporaryDirectory(); store=Path(td.name)/"bindings.json"; svc1=RuntimeManagerService(store_path=store,single_runtime_enforced=True); svc1.execute_kill_switch({"reason":HardTriggerReason.OPERATOR_EMERGENCY_STOP.value,"capital_pool_id":"pool-x","actor_id":"op"}); print("svc1 safe_mode",svc1.get_safe_mode("pool-x")); print("svc1 audit_count",len(svc1.get_kill_switch_audit_log())); svc2=RuntimeManagerService(store_path=store,single_runtime_enforced=True); print("svc2 safe_mode",svc2.get_safe_mode("pool-x")); print("svc2 audit_count",len(svc2.get_kill_switch_audit_log()))'
```
- 實際結果：
  - `svc1 safe_mode paused`
  - `svc1 audit_count 1`
  - `svc2 safe_mode normal`
  - `svc2 audit_count 0`
- 影響：
  - `GET /api/kill-switch/<pool_id>/safe-mode` 不是 runtime-manager 的 durable operational truth，而只是目前 Python process 的局部記憶。
  - `GET /api/kill-switch/audit-log` 無法作為 incident/postmortem 的可靠 trail；重啟或 worker 切換就丟資料。
  - 這不只是 hardening issue。EVO-005 的 acceptance 明確要求 audit trail 保留，而 L1 policy 也要求 runtime-manager 保持 state / audit 一致性；目前跨 process/restart 不成立。

## Required Fix Direction

1. 把 kill-switch audit 與 safe-mode state 移到 durable runtime-manager store，至少要能跟現有 `RuntimeBindingStore` 一樣跨 service restart 重建。
2. `execute_kill_switch()` 必須在 HTTP route ack 前，先完成 audit entry / safe-mode snapshot 的 durable write，再回傳 outcome。
3. 補 regression test，直接守「同一路徑 store 重建後，safe-mode 與 audit log 仍可讀回」，不要只測單一 service instance 內的 happy path。

## Verification Run

- `python3 services/runtime-manager/test_runtime_manager.py`
  - `34 tests OK`
- `python3 services/execution/runtime-manager/test_kill_switch_controller.py`
  - `8 tests OK`
- `python3 services/execution/runtime-manager/smoke_test_kill_switch_controller.py`
  - `4/4 smoke groups PASS`

這些測試都綠，但它們目前只驗 single-process 行為，所以沒有擋住上面的 durability 缺口。
