# P4-001 Router 審查意見（Codex）

**任務**: `P4-001`  
**作者**: Claude  
**審查者**: Codex  
**狀態**: APPROVED for v1 lock

## 結論

這一輪可以通過。

前一版最重要的治理問題已經收掉：

1. permission evaluation 已前移到 Persona invoke 之前  
2. `_evaluate_permission()` 不再是永遠放行的 stub  
3. `console -> operator`、`cron -> system` 的最小 role resolution 已落地  
4. session TTL / rate-limit 已從 open item 變成鎖定政策  
5. monitoring / SSE 已明確標為 deferred，而不是假裝屬於 v1 contract

因此 `P4-001` 已達到 v1 的「可鎖定 contract」標準。

## Confirmed Alignment

### 1. Operator / governance 路徑已修正

- `main.py` 已加入 `_CHANNEL_ROLE`
- `route()` 會根據 channel 傳遞 role 給 `_evaluate_permission()`
- 所以 `console` 來源的 `governance.approve` 不會再被誤判成 persona 路徑

### 2. Session TTL 已改成「政策鎖定、runtime enforcement deferred」

- 這次最重要的不是把 session store 一次做完，而是把 contract 與實作註記說清楚
- 現在 `main.py` 與 `contract.md` 都已明確表示：
  - policy values 已鎖定
  - 真正 enforcement 由 gateway / session backend 承接

### 3. Monitoring 區塊已正確降級成 deferred scope

- `monitoring` response 與 `GET /stream/{session_id}` 不再被描述成 v1 已提供的能力
- 目前文件已足夠避免下游誤接

## Minor Open Items

下面這些不阻擋通過，但要留在 follow-up：

1. 真正的 per-user session store / TTL runtime enforcement  
2. 完整 approval workflow service  
3. MonitoringEvent stream 與 SSE endpoint  
4. policy object storage backend 與 audit persistence

## Reviewer Decision

`P4-001` 可以結案，後續未完成項目應轉成 follow-up task，而不是繼續把 router contract 卡在 review。
