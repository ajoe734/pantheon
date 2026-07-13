# Research Plan Store Restart-Persistence Evidence

The research store is successfully configured with the Postgres backend on dev and persisted all changes (candidate pool, trigger score run, and member reviews) across an `operator-bff` container restart.

## Env Configuration
- `AGORA_RESEARCH_STORE_BACKEND=postgres`
- `AGORA_RESEARCH_STORE_DSN=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon`
- `AGORA_RESEARCH_STORE_SCHEMA=agora_research`

## Request/Response Transcript

### 1. Create Candidate Pool
**Request:**
`POST http://127.0.0.1:18001/bff/agora/candidate-pools`
Headers:
- `Authorization: Bearer agora-test-user:operator`
- `Content-Type: application/json`
- `Idempotency-Key: pool-test-1785114053`
Body:
```json
{
  "operator_id": "agora-test-user"
}
```

**Response (HTTP 201 Created):**
Headers:
- `etag: "1"`
Body:
```json
{
  "data": {
    "pool_id": "cpool-7662d556d1bf4014",
    "strategy_id": null,
    "strategy_family": "WinnerBranch",
    "recipe_id": "recipe-winner-branch-v1",
    "candidates": [
      {
        "artifact_id": "art-003_v1",
        "lifecycle_state": "candidate",
        "score": 0.0
      }
    ],
    "created_at": "2026-07-13T03:40:54Z"
  }
}
```

### 2. Trigger Score Run
**Request:**
`POST http://127.0.0.1:18001/bff/agora/candidate-pools/cpool-7662d556d1bf4014/score`
Headers:
- `Authorization: Bearer agora-test-user:operator`
- `If-Match: "1"`
- `Idempotency-Key: score-test-1785114053`
Body:
```json
{}
```

**Response (HTTP 202 Accepted):**
Body:
```json
{
  "status": "completed",
  "scores": [
    {
      "candidate_id": "art-003_v1",
      "score": 0.0,
      "effective_score": 0.0,
      "triggered_at": "2026-07-13T03:40:54Z",
      "triggered_by": "agora-test-user",
      "details": {},
      "lifecycle_state": "completed"
    }
  ]
}
```

### 3. Create Member Review
**Request:**
`POST http://127.0.0.1:18001/bff/agora/candidate-pools/cpool-7662d556d1bf4014/members/art-003_v1/review`
Headers:
- `Authorization: Bearer agora-test-user:operator`
- `If-Match: "1"`
- `Idempotency-Key: review-test-1785114053`
Body:
```json
{
  "decision": "accept",
  "reviewed_by": "agora-test-user"
}
```

**Response (HTTP 200 OK):**
Body:
```json
{
  "data": {
    "review_id": "rev-ad35a62a-0498-4c8d-8a2b-d36cb6f86c23",
    "artifact_id": "art-003_v1",
    "decision": "accept",
    "reviewed_by": "agora-test-user",
    "reviewed_at": "2026-07-13T03:40:54Z",
    "lifecycle_state": "reviewed"
  }
}
```

---

## Readback Post-Restart
After executing `docker restart pantheon-operator-bff-1` and waiting for recovery:

**Request 1 (Get Scores):**
`GET http://127.0.0.1:18001/bff/agora/candidate-pools/cpool-7662d556d1bf4014/score`
Headers:
- `Authorization: Bearer agora-test-user:operator`

**Response 1 (HTTP 200 OK):**
```json
{
  "data": [
    {
      "candidate_id": "art-003_v1",
      "score": 0.0,
      "effective_score": 0.0,
      "triggered_at": "2026-07-13T03:40:54Z",
      "triggered_by": "agora-test-user",
      "details": {},
      "lifecycle_state": "completed"
    }
  ]
}
```

**Request 2 (Get Member Review):**
`GET http://127.0.0.1:18001/bff/agora/candidate-pools/cpool-7662d556d1bf4014/members/art-003_v1`
Headers:
- `Authorization: Bearer agora-test-user:operator`

**Response 2 (HTTP 200 OK):**
```json
{
  "data": {
    "artifact_id": "art-003_v1",
    "lifecycle_state": "reviewed",
    "reviews": [
      {
        "review_id": "rev-ad35a62a-0498-4c8d-8a2b-d36cb6f86c23",
        "artifact_id": "art-003_v1",
        "decision": "accept",
        "reviewed_by": "agora-test-user",
        "reviewed_at": "2026-07-13T03:40:54Z",
        "lifecycle_state": "reviewed"
      }
    ]
  }
}
```

**Persistence Verdict:** **SUCCESS**. The scores and candidate member reviews were successfully retrieved from Postgres after restart.
