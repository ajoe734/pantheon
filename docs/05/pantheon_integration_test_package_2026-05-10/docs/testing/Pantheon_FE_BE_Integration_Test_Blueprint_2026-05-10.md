# Pantheon / Execute-Plans — FE × BFF × Backend Integration Test Blueprint

**版本**：2026-05-10-A  
**範圍**：`execute-plans` frontend + `pantheon` backend / lupin dev BFF  
**測試目標**：將所有核心使用者 flow 自動化，避免僅靠規格與 mock 判斷功能是否正確。

---

## 1. 測試分層

| Layer | 目的 | 工具 |
|---|---|---|
| Contract drift | 前端 path / DTO / OpenAPI / AsyncAPI 不漂移 | Vitest |
| BFF anonymous route probe | 確認 canonical routes 已註冊，未登入應回 401/403 而非 404 | Node script |
| Authenticated BFF smoke | 確認真 BFF 返回正確 DTO / envelope | Node script |
| Browser hosted probe | 確認部署 bundle、CORS、BFF network 都正確 | Playwright script |
| Playwright user-flow E2E | 以使用者視角跑核心流程 | Playwright |
| A11y smoke | v5 關鍵頁無 critical/serious axe 問題 | Playwright + axe injection |
| Release gate | 把以上結果合併成可審核狀態 | CI workflow |

---

## 2. 環境模式

| Mode | Env | 用途 | 是否可當 release gate |
|---|---|---|---|
| mock | `VITE_BFF_MODE=mock` | 本地 UI 開發 | 否 |
| hybrid | `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=auto` | demo / 探索 | 否 |
| strict | `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict` | 真整合測試 | 是 |
| real-write smoke | `VITE_BFF_REAL_WRITES=true` | 安全寫入 / dry-run | 條件式 |
| hosted browser | `PANTHEON_FE_BASE_URL=https://pantheon-dev.lovable.app` | 部署驗證 | 是 |

---

## 3. 核心使用者 Flow

### F01 Startup / Session Bootstrap

Routes:
- `/management`
- `/management/control-room`

BFF:
- `GET /bff/me`
- `GET /bff/v5/control-room`
- `GET /bff/v5/execution/persona-health`
- `GET /bff/v5/execution/strategy-health`
- `GET /bff/events/stream`

Pass:
- 已登入：MeResponse 載入，tenant / env / user / capabilities 可用。
- 未登入：401 不得 fallback mock；UI 顯示 auth-required 或 dev auth 狀態。
- SSE 開啟成功或在 strict mode 明確失敗。

---

### F02 Control Room Closed-Loop Overview

Route:
- `/management/control-room`

BFF:
- `/bff/v5/control-room`
- `/bff/v5/loop-runs`
- `/bff/v5/sentinel/findings`
- `/bff/v5/interventions`

Pass:
- KPI cards / loops / findings / interventions render。
- Drill-down links 可進 loop / sentinel / interventions。
- Empty data 不 crash。

---

### F03 Execution Loop / Persona Trading Health

Route:
- `/management/loops/execution`
- `/management/loops/execution?focus=personas`

BFF:
- `/bff/v5/loop-runs?loopKind=execution`
- `/bff/v5/execution/persona-health`
- `/bff/v5/execution/strategy-health`

Pass:
- PersonaHealthMatrix 顯示 mode / status / score / routed strategies / open findings。
- critical / degraded 狀態可 drill-down 到 Sentinel finding 或 evidence。
- redacted evidence 不洩漏資料。

---

### F04 Optimization Loop

Route:
- `/management/loops/optimization`

BFF:
- `/bff/v5/loop-runs?loopKind=optimization`
- `/bff/rebalances`
- `/bff/ranking-formulas`
- `/bff/evolution-programs`

Pass:
- ranking → rebalance → approval → apply → evolution/promotion stage 可見。
- awaiting approval 狀態可連到 Approvals / HIQ。
- stage timeline 不依賴 mock-only 欄位。

---

### F05 Sentinel Finding → Remediation

Route:
- `/management/sentinel`

BFF:
- `GET /bff/v5/sentinel/findings`
- `GET /bff/v5/sentinel/findings/{id}`
- `POST /bff/v5/sentinel/findings/{id}/status`
- `POST /bff/v5/sentinel/remediation/build`
- `POST /bff/v5/sentinel/remediation/{actionId}/execute`

Pass:
- Finding detail drawer 顯示 severity / confidence / evidence / blast radius。
- Advisory action 可執行或 queue。
- Emergency action 缺 confirm token 時回 `CONFIRM_TOKEN_REQUIRED` non-2xx envelope，不得回 success `requires_confirm_token`。

---

### F06 Human Intervention Queue

Route:
- `/management/interventions`

BFF:
- `GET /bff/v5/interventions`
- `GET /bff/v5/interventions/{id}`
- `POST /bff/v5/interventions/{id}/claim`
- `POST /bff/v5/interventions/{id}/release`
- `POST /bff/v5/interventions/{id}/escalate`
- `POST /bff/v5/interventions/{id}/decide`
- `POST /bff/v5/interventions/{id}/two-man-sign`

Pass:
- list / drawer / allowed decisions 顯示。
- `/decide` 回 CommandResponse，並產生 intervention.decided SSE。
- same user two-man sign 回 `TWO_MAN_REQUIRED`。

---

### F07 Entity Registry List / Detail

Routes:
- `/management/strategies`
- `/management/personas`
- `/management/capital`
- `/management/rebalance`
- `/management/deployments`
- `/management/evolution`
- `/management/experiments`
- `/management/artifacts`
- `/management/tools`
- `/management/mcp`
- `/management/skills`
- `/management/channels`

BFF:
- `GET /bff/{resource}`
- `GET /bff/{resource}/{id}`

Pass:
- ListResponse shape valid。
- registry lists `totalCountExact=true`。
- detail 404 uses `RESOURCE_NOT_FOUND` envelope。
- ActionDescriptor renders and uses canonical action endpoint.

---

### F08 Create Write Intent

Resources:
- strategies
- personas
- capital-pools
- ranking-formulas
- rebalances
- deployments
- evolution-programs
- research-experiments
- artifacts

BFF:
- `POST /bff/{resource}`

Pass:
- `Idempotency-Key` header present。
- valid create returns `CommandResponse<T>` with `data` required。
- invalid create returns `VALIDATION_FAILED` with field errors。
- live deployment create only creates plan; no live execution.

---

### F09 High-Risk Action / Confirm Token / Cooldown

BFF:
- `GET /bff/actions/{entityType}/{entityId}/{actionId}/cooldown`
- `POST /bff/confirm-tokens`
- `GET /bff/confirm-tokens/{tokenId}`
- `POST /bff/confirm-tokens/{tokenId}/redeem`
- `DELETE /bff/confirm-tokens/{tokenId}`
- `POST /bff/actions/{entityType}/{entityId}/{actionId}`

Pass:
- cooldown active → token issue returns `COOLDOWN_ACTIVE`。
- token issue binds entity/action/version/user/idempotency。
- token reuse returns `CONFIRM_TOKEN_REUSED`。
- binding mismatch returns `CONFIRM_TOKEN_BINDING_MISMATCH`。
- action POST uses canonical `/bff/actions/...` route。

---

### F10 Incident → Rollback Saga

Route:
- `/management/incidents/{id}`

BFF:
- `POST /bff/incidents/{incidentId}/rollback-deployment:dry-run`
- `POST /bff/incidents/{incidentId}/rollback-deployment`
- `GET /bff/rollback-sagas/{sagaId}`
- `POST /bff/rollback-sagas/{sagaId}/cancel`

Pass:
- dry-run shows eligibility, blast radius, required gates。
- execute returns RollbackSagaDTO。
- saga stepper updates via SSE。
- failure displays failureReasonCode and compensation state。

---

### F11 Handoff Reopen SLA

BFF:
- `GET /bff/handoffs`
- `GET /bff/handoffs/{id}`
- `POST /bff/handoffs/{id}/reopen`
- `POST /bff/handoffs/{id}/respond`
- `POST /bff/handoffs/{id}/escalate`

Pass:
- reopen default does not reset SLA。
- reset SLA without approval returns `APPROVAL_REQUIRED`。
- SlaSegment appended and visible。

---

### F12 Approval / Governance

BFF:
- `GET /bff/approvals`
- `GET /bff/approvals/{id}`
- `POST /bff/approvals/{id}/decide`
- `POST /bff/approvals/{id}/two-man-sign`
- `POST /bff/approvals/batch-decide`

Pass:
- single decision updates approval + HIQ。
- quorum progress visible。
- batch partial failure opens BulkResultDrawer and keeps failed selected。

---

### F13 Agora Signal / Ask / Journal

Routes:
- `/agora/signals`
- `/agora/ask`
- `/agora/journal`
- `/agora/insights`

BFF:
- `/bff/agora/signals`
- `/bff/agora/signals/{id}/feedback`
- `/bff/agora/inbox`
- `/bff/agora/journal`
- `/bff/agora/journal/{id}`
- `/bff/agora/ask`
- `/bff/agora/ask/sessions/{id}`

Pass:
- signal feedback emits audit + SSE。
- ask streams `ask.message.delta` and final transcript via REST。
- journal patch uses `application/merge-patch+json` and is atomic.

---

### F14 SSE Reconnect / Resync

BFF:
- `GET /bff/events/stream`

Pass:
- EventSource opens。
- heartbeat received。
- reconnect uses Last-Event-Id。
- expired replay emits `system.resync_required` and frontend refetches resync endpoint。

---

### F15 Strict vs Hybrid Fallback

Pass:
- hybrid: network/5xx fallback mock + LiveBffBanner。
- strict: network/5xx fails test, no mock data shown。
- 4xx BffError never fallback。

---

### F16 Audit / Correlation

Pass:
- `X-Request-Id` sent and echoed。
- `X-Correlation-Id` sent/received。
- audit event and SSE event share correlationId。
- mock overlay audit displays ephemeral badge only in mock mode。

---

### F17 Accessibility

Required pages:
- `/management/control-room`
- `/management/loops/research`
- `/management/loops/execution`
- `/management/loops/optimization`
- `/management/sentinel`
- `/management/interventions`
- PersonaHealthMatrix component

Pass:
- axe critical / serious = 0。
- focus returns to trigger。
- ESC closes topmost overlay only。
- reduced motion respected。

---

### F18 Performance / Stability

Pass:
- no unbounded rerender on SSE stream。
- control room under budget。
- entity list first page under budget。
- Sentinel list under budget。
- LineageGraph >500 nodes warning。
- DataTable density stable。

---

## 4. Release Gate

A release is green only when:

```text
1. npm test green
2. npm run build green
3. contract-drift green
4. OpenAPI / AsyncAPI validate
5. anonymous route probe has 0 canonical 404
6. authenticated live smoke passes required cases
7. hosted browser probe has 0 failed BFF requests
8. Playwright F01–F09 pass
9. F10–F11 pass or are explicitly backend-not-ready
10. axe critical/serious = 0
11. strict mode has no mock fallback
```
