# TEL-001 Review — Codex

日期：2026-04-10
結論：`request changes`

## Findings

### 1. 新增的 deploy / rollback / kill-switch / heartbeat helpers 在 canonical schema 下全部無法落盤

- 檔案：
  - `services/telemetry/capture.py:428`
  - `services/telemetry/capture.py:455`
  - `services/telemetry/capture.py:505`
  - `services/telemetry/capture.py:528`
  - `services/telemetry/telemetry_event.schema.json:174`
- 問題：
  - 這幾個 TEL-001 新增 helper 一律用 `metrics={}` 建 event。
  - 但 canonical schema 明確要求 `metrics.minProperties = 1`。
  - 結果是 handoff 裡宣稱新增的治理/部署類事件，在真正載入 `telemetry_event.schema.json` 時都會 validation fail，完全不會被存進 canonical event stream。
- 直接重現：
```bash
cd services/telemetry
python3 -c "from capture import TelemetryCapture, ExecutionMode, RollbackActionType; c=TelemetryCapture(schema_path='telemetry_event.schema.json', binding_context={'binding_id':'b5e6f7a8-1234-5678-9abc-def012345678','runtime_id':'lean-worker-01','capital_pool_id':'pool-alpha','artifact_id':'artifact-strategy-v2','artifact_version':'2.1.0','deployment_stage':'canary','plan_id':'plan-deploy-001','persona_capital_binding_id':'pcb-001'}); print(c.capture_deploy_started(ExecutionMode.LIVE,'strat')); print(c.capture_rollback_started(ExecutionMode.LIVE,'strat','11111111-1111-1111-1111-111111111111',RollbackActionType.REPLACE)); print(c.capture_heartbeat(ExecutionMode.LIVE,'strat'))"
```
- 實際結果：
  - 三個 helper 都回傳 `False`
  - stderr 顯示 `{} does not have enough properties`

### 2. evidence 欄位目前不是「驗證 RuntimeBinding 真相」，而是「缺什麼就亂補什麼」

- 檔案：
  - `services/telemetry/capture.py:578`
  - `services/telemetry/capture.py:607`
  - `services/telemetry/capture.py:611`
  - `services/telemetry/capture.py:613`
  - `services/telemetry/TEL_001A_FIELD_PACKET.md:88`
  - `services/telemetry/TEL_001A_FIELD_PACKET.md:104`
  - `services/telemetry/TEL_001A_FIELD_PACKET.md:127`
- 問題：
  - 沒有 `binding_context` 時，canonical schema 直接拒收事件；但目前測試把這條路徑當成正常 legacy mode。
  - 更嚴重的是，只要 `binding_context` 有一個 key，`_build_event()` 就會自動補：
    - 隨機 `binding_id`
    - 空字串 `runtime_id` / `capital_pool_id` / `artifact_id` / `plan_id` / `persona_capital_binding_id`
    - 預設 `artifact_version = 0.0.0`
  - 這讓事件看起來有 evidence 欄位，但實際上沒有任何可對帳的 RuntimeBinding 證據，違反 TEL-001A 要求的完整 `(binding_id, artifact_id, artifact_version, runtime_id)` tuple 與 `(binding_id, plan_id)` admissibility proof。
- 直接重現：
```bash
cd services/telemetry
python3 -c "from capture import TelemetryCapture, ExecutionMode; c=TelemetryCapture(schema_path='telemetry_event.schema.json', binding_context={'binding_id':'b5e6f7a8-1234-5678-9abc-def012345678'}); ok=c.capture_pnl(ExecutionMode.PAPER,'strat',1.0); print(ok); print(c.get_paper_events()[0])"
```
- 實際結果：
  - 事件成功通過 schema
  - 但 payload 內是 `runtime_id=''`, `capital_pool_id=''`, `artifact_id=''`, `plan_id=''`, `persona_capital_binding_id=''`
  - 這不是 canonical evidence，只是 shape-level padding

### 3. `deployment_stage='frozen'` 在 schema 上是合法值，但目前實作永遠產不出合法事件

- 檔案：
  - `services/telemetry/capture.py:617`
  - `services/telemetry/telemetry_event.schema.json:57`
  - `services/telemetry/telemetry_event.schema.json:95`
- 問題：
  - schema 允許 `deployment_stage = frozen`
  - 但 `_build_event()` 會把非 `canary|live` 的 stage 原樣寫回 `execution_mode`
  - 所以 `frozen` stage 會變成 `execution_mode='frozen'`
  - 而 schema 又限制 `execution_mode` 只能是 `paper|live`
  - 結果：`frozen` 這個 TEL-001 明確納入的 canonical stage，實際上完全不可寫入
- 直接重現：
```bash
cd services/telemetry
python3 -c "from capture import TelemetryCapture, ExecutionMode; c=TelemetryCapture(schema_path='telemetry_event.schema.json', binding_context={'binding_id':'b5e6f7a8-1234-5678-9abc-def012345678','runtime_id':'lean-worker-01','capital_pool_id':'pool-alpha','artifact_id':'artifact-strategy-v2','artifact_version':'2.1.0','deployment_stage':'frozen','plan_id':'plan-deploy-001','persona_capital_binding_id':'pcb-001'}); print(c.capture_pnl(ExecutionMode.LIVE,'strat',1.0))"
```
- 實際結果：
  - validation fail: `'frozen' is not one of ['paper', 'live']`

### 4. TEL-001 測試與 smoke 仍在驗舊的 ad-hoc schema，所以前面三個問題全部被假綠燈蓋掉

- 檔案：
  - `services/telemetry/test_capture.py:17`
  - `services/telemetry/smoke_test.py:20`
  - `services/telemetry/telemetry_event.schema.json:1`
- 問題：
  - `test_capture.py` 和 `smoke_test.py` 都不是載入 canonical `telemetry_event.schema.json`
  - 它們各自 inline 了一份只要求 `event_id/event_type/created_at/execution_mode/target/metrics` 的最小 schema
  - 因此：
    - missing binding evidence 不會被抓到
    - `frozen` alias 問題不會被抓到
    - 新 helper 用空 `metrics` 的問題也不會被抓到，因為 smoke 根本沒跑新 helper
- 結果：
  - `31/31` 和 smoke pass 不能證明 TEL-001 已經對齊 canonical telemetry truth，只能證明舊 FB-003 測試路徑還活著

## Canonical Drift To Resolve

- `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md:303-316` 與 `:330-351` 目前仍把 raw event / lineage edge / query contract 寫成 `runtime_binding_id`
- 這輪 schema 採的是 `binding_id`
- 如果要保留 `binding_id`，請在同一 task 內把 L1 canonical wording 一併收斂；否則 telemetry raw layer 與 downstream query contract 會繼續雙軌

## Verification I Ran

```bash
cd services/telemetry
python3 -m unittest test_capture.py
python3 smoke_test.py
python3 -c "from capture import TelemetryCapture, ExecutionMode, RollbackActionType; c=TelemetryCapture(schema_path='telemetry_event.schema.json', binding_context={'binding_id':'b5e6f7a8-1234-5678-9abc-def012345678','runtime_id':'lean-worker-01','capital_pool_id':'pool-alpha','artifact_id':'artifact-strategy-v2','artifact_version':'2.1.0','deployment_stage':'canary','plan_id':'plan-deploy-001','persona_capital_binding_id':'pcb-001'}); print('deploy_started', c.capture_deploy_started(ExecutionMode.LIVE,'strat')); print('rollback_started', c.capture_rollback_started(ExecutionMode.LIVE,'strat','11111111-1111-1111-1111-111111111111',RollbackActionType.REPLACE)); print('heartbeat', c.capture_heartbeat(ExecutionMode.LIVE,'strat')); print('events', len(c.get_live_events()))"
python3 -c "from capture import TelemetryCapture, ExecutionMode; c=TelemetryCapture(schema_path='telemetry_event.schema.json'); ok=c.capture_pnl(ExecutionMode.PAPER,'strat',1.0); print('pnl_without_binding_context', ok); print('events', len(c.get_paper_events()))"
python3 -c "from capture import TelemetryCapture, ExecutionMode; c=TelemetryCapture(schema_path='telemetry_event.schema.json', binding_context={'binding_id':'b5e6f7a8-1234-5678-9abc-def012345678'}); ok=c.capture_pnl(ExecutionMode.PAPER,'strat',1.0); print('partial_binding_context_ok', ok); print(c.get_paper_events()[0] if c.get_paper_events() else 'NO_EVENTS')"
python3 -c "from capture import TelemetryCapture, ExecutionMode; c=TelemetryCapture(schema_path='telemetry_event.schema.json', binding_context={'binding_id':'b5e6f7a8-1234-5678-9abc-def012345678','runtime_id':'lean-worker-01','capital_pool_id':'pool-alpha','artifact_id':'artifact-strategy-v2','artifact_version':'2.1.0','deployment_stage':'frozen','plan_id':'plan-deploy-001','persona_capital_binding_id':'pcb-001'}); print('frozen_ok', c.capture_pnl(ExecutionMode.LIVE,'strat',1.0))"
```

## Required Fix Direction

1. canonical schema path 和 test/smoke path 必須統一，不可再維持一份 ad-hoc minimal schema
2. evidence 欄位必須改成 strict validation / explicit rejection，不可再自動補假值
3. non-metric lifecycle events 要嘛定義 canonical metric payload，要嘛調整 schema 讓這類事件走明確的空 metrics contract
4. `frozen` 與 `execution_mode` alias 規則要明確收斂成合法 mapping
5. `binding_id` vs `runtime_binding_id` 必須在 L1 canonical docs 與 schema/query contract 間收斂成單一命名
