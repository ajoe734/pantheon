# Trading Room Store Restart-Persistence Blocker Analysis

The Trading Room store restart-persistence check **FAILED** on dev due to a code regression where all Postgres storage implementation and initialization logic was removed.

## Blocker Root Cause

### 1. The Regression Commit
The Postgres implementation of the trading room store (`PostgresTradingRoomStore`) was introduced and merged in PR #3444 (commit `247a80330`).
However, during the merge of **AG-GAP-003** (commit `5266361ffbbaf1087c29d0d8bfa3b64d6d4e5cbf`), the merge conflict was incorrectly resolved or overwritten, resulting in:
- Deletion of `services/control-plane/bff/agora/trading_room/test_postgres_store.py`
- Removal of `PostgresTradingRoomStore` from `services/control-plane/bff/agora/trading_room/store.py`
- Reversion of `make_trading_room_store()` to always return the in-memory backend `TradingRoomStore()`:
  ```python
  def make_trading_room_store() -> TradingRoomStore:
      return TradingRoomStore()
  ```

### 2. Silent Startup Fallback on Dev
On the dev environment, `pantheon-operator-bff-1` is launched with:
- `AGORA_TRADING_ROOM_STORE_BACKEND=postgres`
- `AGORA_TRADING_ROOM_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon`
- `AGORA_TRADING_ROOM_STORE_SCHEMA=agora`

But because the factory function `make_trading_room_store()` ignores all environment parameters and defaults to `TradingRoomStore()` (in-memory), the container startup logs show:
```
INFO:agora.strategy_workshop.store:Agora workshop store initialized backend=postgres store=PostgresWorkshopStore schema=agora
INFO:agora.dashboard.store:Agora dashboard recipe store initialized backend=postgres store=PostgresDashboardRecipeStore
```
*(No `agora.trading_room.store` log is outputted because the memory fallback has no logging or fails to initialize Postgres).*

---

## Live Proof Transcript

### 1. Create Workspace Proposal
**Request:**
`POST http://127.0.0.1:18001/bff/agora/strategies/strat-tr-test/trading-room/proposals`
Headers:
- `Authorization: Bearer agora-test-user:operator`
- `Content-Type: application/json`
- `Idempotency-Key: tr-test-1785114053`
Body:
```json
{
  "proposalId": "prop-tr-1785114053",
  "userId": "agora-test-user",
  "strategyId": "strat-tr-test",
  "strategyVersion": "v1",
  "views": []
}
```

**Response (HTTP 201 Created):**
```json
{
  "data": {
    "proposalId": "prop-tr-1785114053",
    "userId": "agora-test-user",
    "strategyId": "strat-tr-test",
    "strategyVersion": "v1",
    "views": []
  }
}
```

### 2. Accept Proposal to Materialize Workspace
**Request:**
`POST http://127.0.0.1:18001/bff/agora/strategies/strat-tr-test/trading-room/proposals/prop-tr-1785114053/accept`
Headers:
- `Authorization: Bearer agora-test-user:operator`

**Response (HTTP 201 Created):**
```json
{
  "data": {
    "id": "ws_prop-tr-1785114053",
    "userId": "agora-test-user",
    "strategyId": "strat-tr-test",
    "strategyVersion": "v1",
    "views": []
  }
}
```

### 3. Readback Post-Restart
After executing `docker restart pantheon-operator-bff-1`:

**Request:**
`GET http://127.0.0.1:18001/bff/agora/trading-room/workspaces/ws_prop-tr-1785114053`
Headers:
- `Authorization: Bearer agora-test-user:operator`

**Response (HTTP 404 Not Found):**
```json
{
  "error": {
    "code": "RESOURCE_NOT_FOUND",
    "i18nKey": "errors.RESOURCE_NOT_FOUND",
    "message": "TradingRoomWorkspaceProposal 'prop-tr-1785114053' not found",
    "retryable": false,
    "userActionable": true,
    "details": {
      "reason": "workspace_proposal_not_found",
      "precondition_failed": null,
      "suggestion": null
    }
  },
  "meta": {
    "correlationId": "7bc2c877-3009-4e2b-9425-877f7eb273f0"
  }
}
```

**Persistence Verdict:** **FAILED**. The workspace was lost upon container restart because it was held in process memory instead of being written to Postgres.

---

## Action Plan
A separate follow-up task is required to restore `PostgresTradingRoomStore` from commit `247a80330ea9624683b84565f85462aca1f58344` or commit `0f210d76799dafe9fb8c8a1aac75ebb35fe84e7c` back into `services/control-plane/bff/agora/trading_room/store.py` and fix `make_trading_room_store()` to load it when `backend="postgres"`.
