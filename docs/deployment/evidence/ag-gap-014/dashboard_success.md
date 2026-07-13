# Dashboard Recipe Store Restart-Persistence Evidence

The dashboard recipe store is successfully configured with the Postgres backend on dev and persisted all changes across an `operator-bff` container restart.

## Env Configuration
- `AGORA_DASHBOARD_STORE_BACKEND=postgres`
- `AGORA_DASHBOARD_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon`
- `AGORA_DASHBOARD_STORE_SCHEMA=agora`

## Startup Logs
```
INFO:agora.dashboard.store:Agora dashboard recipe store initialized backend=postgres store=PostgresDashboardRecipeStore
```

## Request/Response Transcript

### 1. Propose Dashboard Recipe
**Request:**
`POST http://127.0.0.1:18001/bff/agora/strategies/strat-db-test/dashboard-recipes/proposals`
Headers:
- `Authorization: Bearer agora-test-user:operator`
- `Content-Type: application/json`
- `Idempotency-Key: db-test-1785114053`
Body:
```json
{
  "strategy_version_id": "v1",
  "workspace": "trading_room",
  "phase": "candidate_review"
}
```

**Response (HTTP 201 Created):**
Headers:
- `etag: "1"`
Body:
```json
{
  "data": {
    "recipe_id": "rec-ad1143c74de14dbca7c5b6ff0bfb36f1",
    "strategy_id": "strat-db-test",
    "strategy_version_id": "v1",
    "workspace": "trading_room",
    "phase": "candidate_review",
    "active_version": 1
  },
  "meta": {
    "route": "POST /bff/agora/strategies/{strategy_id}/dashboard-recipes/proposals"
  }
}
```

### 2. Accept Recipe (Materializes Version 2)
**Request:**
`POST http://127.0.0.1:18001/bff/agora/dashboard-recipes/rec-ad1143c74de14dbca7c5b6ff0bfb36f1/accept`
Headers:
- `Authorization: Bearer agora-test-user:operator`
- `If-Match: "1"`
Body:
```json
{
  "expected_version": 1
}
```

**Response (HTTP 200 OK):**
Headers:
- `etag: "2"`
Body:
```json
{
  "data": {
    "recipe_id": "rec-ad1143c74de14dbca7c5b6ff0bfb36f1",
    "version": 2,
    "status": "active"
  }
}
```

### 3. Rollback Recipe to Version 1 (Appends Version 3)
**Request:**
`POST http://127.0.0.1:18001/bff/agora/dashboard-recipes/rec-ad1143c74de14dbca7c5b6ff0bfb36f1/rollback`
Headers:
- `Authorization: Bearer agora-test-user:operator`
- `If-Match: "2"`
Body:
```json
{
  "target_version": 1
}
```

**Response (HTTP 200 OK):**
Headers:
- `etag: "3"`
Body:
```json
{
  "data": {
    "recipe_id": "rec-ad1143c74de14dbca7c5b6ff0bfb36f1",
    "version": 3,
    "status": "active"
  }
}
```

---

## Readback Post-Restart
After executing `docker restart pantheon-operator-bff-1` and waiting for recovery:

**Request:**
`GET http://127.0.0.1:18001/bff/agora/dashboard-recipes/rec-ad1143c74de14dbca7c5b6ff0bfb36f1/versions`
Headers:
- `Authorization: Bearer agora-test-user:operator`

**Response (HTTP 200 OK):**
```json
{
  "data": [
    {
      "recipe_id": "rec-ad1143c74de14dbca7c5b6ff0bfb36f1",
      "version": 1,
      "status": "superseded",
      "recipe_json": {},
      "created_at": "2026-07-13T03:40:53Z"
    },
    {
      "recipe_id": "rec-ad1143c74de14dbca7c5b6ff0bfb36f1",
      "version": 2,
      "status": "superseded",
      "recipe_json": {},
      "created_at": "2026-07-13T03:40:53Z"
    },
    {
      "recipe_id": "rec-ad1143c74de14dbca7c5b6ff0bfb36f1",
      "version": 3,
      "status": "active",
      "recipe_json": {},
      "created_at": "2026-07-13T03:40:53Z"
    }
  ]
}
```
**Persistence Verdict:** **SUCCESS**. Version history [1, 2, 3] was successfully read back with the rollback version 3 set as active.
