# DEP-002 Review Request

請審查 `DEP-002` 的 deployment orchestration saga backbone。

## 主要產出

- `services/control-plane/governance/deployment_saga.py`
- `services/control-plane/governance/deployment_saga.contract.md`
- `services/control-plane/governance/test_deployment_saga.py`
- `services/control-plane/governance/smoke_test_deployment_saga.py`
- `services/control-plane/cron/service.py`
- `services/control-plane/cron/test_cron.py`
- `services/control-plane/cron/smoke_test.py`
- `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`

## 這輪做了什麼

1. 把 `DEP-002` 收斂成正式 `DeploymentSaga` aggregate：
   - `DeploymentSaga`
   - `SagaEventEnvelope`
   - `OutboxRecord`
   - `InboxReceipt`
   - `CompensationDecision`

2. 補上 atomic write backbone：
   - `DeploymentSagaStore.bootstrap_for_plan()` 會在同一個 local transaction 中同時寫入 saga 狀態與第一個 outbox event
   - commit 前若失敗，saga 與 outbox 都不會部分落地

3. 補上 per-aggregate ordering 與 idempotent consumer：
   - consumer 以 `event_id` + `idempotency_key` + per-aggregate `sequence_no` 做 dedupe
   - sequence gap 會寫 `out_of_order` receipt，不直接套用 side effect

4. 補上 compensation matrix，對應 L1 policy：
   - `binding_requested` 失敗 → `abort_plan`
   - `runtime_load_requested` 失敗 → `mark_binding_failed_inactive`
   - `runtime_active` 後 mismatch → `request_rollback`
   - compensation 自己失敗 → `enter_safe_mode_and_raise_incident`

5. 將 `pantheon.deploy` 接到 saga bootstrap：
   - deploy request 現在除了 `DEP-001` plan / execution projection 外，也會產出 `DEP-002` 的 `deployment_saga`
   - `deployment_request["consistency_contract"] = "DEP-002"`

## 驗證

- `python3 -m unittest discover -s services/control-plane/governance -p 'test_deployment_saga.py'`
- `python3 -m unittest discover -s services/control-plane/governance -p 'test_deployment_plan.py'`
- `python3 -m unittest discover -s services/control-plane/cron -p 'test_*.py'`
- `python3 services/control-plane/governance/smoke_test_deployment_saga.py`
- `python3 services/control-plane/cron/smoke_test.py`
- `python3 -m py_compile services/control-plane/governance/deployment_saga.py services/control-plane/cron/service.py`

注意：
- `python3 -m unittest discover -s services/control-plane/governance -p 'test_*.py'` 目前仍會被既有 `test_capital_pool.py` / `test_persona_capital_binding.py` 的 `pytest` import 缺失卡住；這不是 DEP-002 新引入的失敗。

## 請 reviewer 特別看

1. `DeploymentSagaStore` 的 transaction 模型是否足夠表達「business write + outbox append atomic」。
2. `InboxReceipt` 的 duplicate / out-of-order 判準是否對齊 `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`。
3. compensation 決策是否維持 `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` 的 write-owner 邊界。
4. cron deploy 把 saga bootstrap 放進 `deployment_request` 的方式，是否合理作為後續 runtime-manager / EX-002 的接點。
