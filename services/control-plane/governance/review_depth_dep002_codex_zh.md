# DEPTH-DEP002 Review Request

請審查 `DEPTH-DEP002` 這輪對 `DEP-002` deployment orchestration saga acceptance 的重新驗證結果。

## 這輪結論

- `services/deployment/` 不是 stub；deployment service 已對外提供 `DEP-002` 的 dispatch / saga progress / outbox / inbox / compensation API。
- `services/control-plane/governance/deployment_saga.py` 仍是 canonical saga backbone，實際提供 atomic bootstrap、per-saga ordering、idempotent consumer receipt、以及 owner-scoped compensation decision。
- `services/deployment/contract.md`、`services/control-plane/governance/deployment_saga.contract.md`、`CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md` 三者在 acceptance 所需語義上是一致的。

## 對照 acceptance

1. atomic write of business data + event outbox 已驗證
   - `DeploymentSagaStore.bootstrap_for_plan()` 以同一個 local commit 寫入 saga state 與 sequence 1 outbox event。
   - `services/control-plane/governance/test_deployment_saga.py` 的 `test_commit_failure_rolls_back_saga_and_outbox` 驗證 commit failure 不會留下 partial visible state。

2. per-aggregate ordering / idempotent consumer 已驗證
   - `consume_event()` 以 `event_id`、`idempotency_key`、同 aggregate `sequence_no` 寫 `InboxReceipt`。
   - duplicate delivery 會產生 `duplicate` receipt；sequence gap 會產生 `out_of_order` receipt；gap 補齊後才允許 apply。
   - service-level API 透過 `/api/deployment/outbox/{event_id}/consume` 與 `/api/deployment/inbox` 暴露這些行為。

3. compensation / owner-scoped write boundary 已文件化且有測試
   - `services/control-plane/governance/deployment_saga.contract.md` §7 定義 failure point → compensation command → write owner。
   - `services/deployment/contract.md` 明確把 compensation decision derivation 放在 Deployment Service，但 canonical write owner 仍分屬 `governance-svc`、`runtime-manager-svc`、`rollback-controller`。
   - `test_deployment_saga.py` 覆蓋 `abort_plan`、`mark_binding_failed_inactive`、`request_rollback`、`enter_safe_mode_and_raise_incident`。

## 本輪重新執行的驗證

- `python3 -m pytest services/control-plane/governance/test_deployment_saga.py -q`
- `python3 -m pytest services/deployment/test_service.py -q`
- `python3 services/control-plane/governance/smoke_test_deployment_saga.py`
- `python3 services/deployment/smoke_test.py`

結果：

- governance saga tests: `9 passed`
- deployment service tests: `12 passed`
- governance saga smoke: `13/13 checks passed`
- deployment service smoke: all checks passed

## Reviewer focus

1. `DEPTH-DEP002` 作為「重新驗證」任務，是否可以接受不再新增 code，而以現有 implementation + fresh test evidence 關閉。
2. `services/governance/contract.md` 雖然不是 DEP-002 主合約，但它與 deployment service 邊界沒有衝突；DEP-002 的 canonical service contract 應以 `services/deployment/contract.md` 為準。
3. 若 reviewer 同意，這個 task 可直接進 `review_approved`，由 owner 正式收尾為 `done`。
