# OC-003 Source / Search Operator Ops — Frontend Change Spec

## Feature

- Feature ID: `OC-003-source-search-ops`
- Screen ID: `screen-operator-source-ops`, `screen-operator-search-ops`
- Workbench: Operator Console
- Packet status: contract-ready — UI implementation may proceed against the live BFF routes
- Task: `SVC-SOURCE-SEARCH-OPS-BFF`

## Summary

Expose the source-ingestion and search-index operator surfaces inside the Operator Console.
The operator sees connector health, crawl run history, DLQ state, crawl frontier, audit
summary, and search index freshness / pipeline run history.  All write commands (DLQ
replay, frontier replay, index refresh, index materialize) are submitted via idempotent
BFF POST endpoints and must carry an `X-Idempotency-Key` header.

The BFF is the only permitted data source.  The UI must never call source-ingest or search
services directly.

## Files to Create or Modify

```text
src/pages/operator/SourceOps.tsx              — new source ops operator page
src/pages/operator/SearchOps.tsx              — new search ops operator page
src/lib/bffClient.ts                          — add OC-003 source/search ops calls
src/pages/operator/types.ts                   — add OC-003 types
```

## Readiness Gates

Pantheon has confirmed the following are live and returning the published field shape:

- `GET /api/v1/operator/source/ops`
- `GET /api/v1/operator/search/ops`
- `POST /api/v1/operator/source/dlq/replay`
- `POST /api/v1/operator/source/frontier/{frontier_id}/replay`
- `POST /api/v1/operator/search/index/refresh`
- `POST /api/v1/operator/search/index/materialize`

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`. Do not add raw `fetch` in component files.

### Source Ops read surface

```http
GET /api/v1/operator/source/ops
```

Query params (all optional):

| Param | Type | Default | Description |
|---|---|---|---|
| `crawl_run_limit` | int | 50 | Max recent crawl runs to return |
| `dlq_status` | string | — | Filter DLQ by status (pending, replayed, …) |
| `frontier_status` | string | — | Filter frontier by status (queued, failed, …) |
| `audit_limit` | int | 20 | Max audit actions to return |

Response envelope:

```json
{
  "data": {
    "source": "service_client | unavailable | missing",
    "connector_health": [
      {
        "connector_id": "conn-openalex-api",
        "status": "enabled",
        "provider": "OpenAlex",
        "policy": { ... },
        "fetch_policy": { ... },
        "schedule": { ... },
        "state": {
          "attempts": 5,
          "successful_attempts": 4,
          "failed_attempts": 1,
          "last_error": null
        }
      }
    ],
    "crawl_runs": [
      {
        "ingest_run_id": "run-...",
        "connector_id": "conn-openalex-api",
        "status": "completed | failed | rejected",
        "trigger_type": "scheduled | manual | dlq_replay"
      }
    ],
    "dlq": [
      {
        "entry_id": "dlq-...",
        "status": "pending | replayed | replay_failed",
        "tag": "retry_exhausted",
        "reason": "connector fetch failed"
      }
    ],
    "frontier": [
      {
        "frontier_id": "fr-...",
        "connector_id": "conn-openalex-api",
        "status": "queued | running | done | failed | retry"
      }
    ],
    "audit": [
      {
        "action_id": "aud-...",
        "action_type": "ingest_completed | dlq_replay_approved"
      }
    ],
    "summary": {
      "connector_count": 3,
      "recent_run_count": 12,
      "dlq_count": 1,
      "frontier_count": 0,
      "audit_count": 8
    }
  },
  "meta": {
    "snapshot_at": "2026-04-30T08:00:00Z",
    "surfaces": {
      "source_ops": { "status": "ok | unavailable | missing", "source": "service_client" }
    }
  }
}
```

### Search Ops read surface

```http
GET /api/v1/operator/search/ops
```

Query params (all optional):

| Param | Type | Default | Description |
|---|---|---|---|
| `pipeline_run_limit` | int | 50 | Max pipeline run snapshots to return |

Response envelope:

```json
{
  "data": {
    "source": "service_client | unavailable | missing",
    "index_freshness": {
      "within_sla": true,
      "sla_seconds": 3600,
      "seconds_since_last_run": 120,
      "last_run_at": "2026-04-30T07:58:00Z"
    },
    "pipeline_runs": [
      {
        "run_id": "pipe-...",
        "status": "completed | partial | failed",
        "indexed_count": 42,
        "started_at": "2026-04-30T07:58:00Z"
      }
    ],
    "pipeline_run_total": 87,
    "materialized_index": {
      "materialized_at": "2026-04-30T07:55:00Z",
      "indexed_object_count": 210
    },
    "summary": {
      "pipeline_run_count": 87,
      "freshness_ok": true,
      "freshness_status": "ok | stale | unknown"
    }
  },
  "meta": {
    "snapshot_at": "2026-04-30T08:00:00Z",
    "surfaces": {
      "search_ops": { "status": "ok | stale | unavailable | missing", "source": "service_client" }
    }
  }
}
```

### Source DLQ replay (command)

```http
POST /api/v1/operator/source/dlq/replay
X-Idempotency-Key: <stable-key>
Authorization: Bearer <operator-token>
Content-Type: application/json

{
  "entry_ids": ["dlq-001", "dlq-002"],   // optional; omit to replay all pending
  "tag": "retry_exhausted",              // optional
  "reason": "operator-approved replay"  // optional
}
```

Response:
```json
{
  "data": {
    "command": "SourceDLQReplay",
    "status": "accepted",
    "accepted_at": "2026-04-30T08:01:00Z",
    "service_result": { ... }
  },
  "meta": { "snapshot_at": "...", "surfaces": { "source_search_command": { ... } } }
}
```

### Source frontier replay (command)

```http
POST /api/v1/operator/source/frontier/{frontier_id}/replay
X-Idempotency-Key: <stable-key>
Authorization: Bearer <operator-token>
Content-Type: application/json

{
  "trace_id": "optional-trace-id"
}
```

### Search index refresh (command)

```http
POST /api/v1/operator/search/index/refresh
X-Idempotency-Key: <stable-key>
Authorization: Bearer <operator-token>
Content-Type: application/json

{
  "triggered_by": "bff-operator",
  "force_full": false,
  "trigger_ref": "optional-ref"
}
```

### Search index materialize (command)

```http
POST /api/v1/operator/search/index/materialize
X-Idempotency-Key: <stable-key>
Authorization: Bearer <operator-token>
```

## Auth and Idempotency Rules

- All GET surfaces require `operator`, `approver`, `admin`, or `reviewer` role.
- All POST commands require `operator` or `admin` role.
- All POST commands MUST include a non-empty `X-Idempotency-Key` header.  The BFF
  returns `400 INVALID_PARAMS` if the key is missing.
- Retry the same POST with the same `X-Idempotency-Key` to recover from timeouts
  safely — the underlying services are idempotent at the BFF boundary.

## Degradation Rules

- When `data.source` is `missing` or `unavailable`, render a degraded state badge.
  Do not hide the surface; show the summary with zeroed counts.
- When `meta.surfaces.search_ops.status` is `stale`, render a staleness warning
  alongside the freshness panel.
- Command failures (4xx/5xx from BFF) should surface the `error.message` from the
  BFF response envelope to the operator, not a generic toast.

## Verification Commands

```bash
# Read surfaces (requires PANTHEON_BFF_AUTH_STUB=true and BFF running)
curl -H "Authorization: Bearer op:operator" http://localhost:8080/api/v1/operator/source/ops
curl -H "Authorization: Bearer op:operator" http://localhost:8080/api/v1/operator/search/ops

# DLQ replay command
curl -X POST \
  -H "Authorization: Bearer op:operator" \
  -H "X-Idempotency-Key: bff-dlq-replay-$(date +%s)" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://localhost:8080/api/v1/operator/source/dlq/replay

# Index refresh command
curl -X POST \
  -H "Authorization: Bearer op:operator" \
  -H "X-Idempotency-Key: bff-refresh-$(date +%s)" \
  -H "Content-Type: application/json" \
  -d '{}' \
  http://localhost:8080/api/v1/operator/search/index/refresh
```
