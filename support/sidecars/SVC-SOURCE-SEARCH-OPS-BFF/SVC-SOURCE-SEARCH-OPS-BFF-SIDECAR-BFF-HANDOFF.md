# SVC-SOURCE-SEARCH-OPS-BFF BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SVC-SOURCE-SEARCH-OPS-BFF` - Expose source and search operations in BFF  
**Parent Owner**: `Codex2`  
**Parent Reviewer**: `Claude`  
**Parent Status at packet creation time**: `todo`  
**Sidecar Task**: `SVC-SOURCE-SEARCH-OPS-BFF-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: `Claude`  
**Sidecar Reviewer**: `Codex2`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: `2026-04-30`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not change L1 truth, BFF route contracts, source/search service implementations, registry/governance behavior, or runtime-manager implementation. It packages the current service route inventory, BFF gap analysis, operator journey, and candidate surface shape for the parent owner to accept, revise, or ignore while implementing the canonical task.

---

## 1. Executive Summary

The parent task `SVC-SOURCE-SEARCH-OPS-BFF` must build an operator ops surface in the BFF that covers:

- connector health and crawl run state (from `source_ingestion` service)
- DLQ entries and replay controls (from `source_ingestion` service)
- index freshness, pipeline runs, and reindex controls (from `search` service)
- audit and error summaries (from both services)
- all commands must be idempotent and auth-guarded
- the BFF must not read source or search volumes directly

Current repo state:

- `services/source_ingestion/main.py` exposes a complete ingest API including connectors, jobs, frontier, DLQ, audit, schedule, source records, evidence records, and knowledge objects — **none of these are currently proxied through the BFF operator surface**.
- `services/search/main.py` exposes index status, freshness, pipeline runs, refresh, reload, materialize, and query routes — **none of these operator-facing routes are currently proxied through the BFF operator surface**.
- `services/control-plane/bff/read_store.py` has `get_source_connector_registry()` (reads `GET /api/source-ingest/registry`) and `research_search_index` dataset plumbing (reads from search service). These are the **only existing BFF↔service touchpoints for source/search**.
- The BFF API contract (`BFF_API_CONTRACT.md`) does not yet list any `/api/v1/operator/source` or `/api/v1/operator/search` routes.
- The BFF surface inventory (`BFF_SURFACE_INVENTORY.md`) defers source/search surface objects to Appendix A.

The BFF/frontend gap is therefore the entire ops panel: no BFF routes exist to give operators visibility into connector health, crawl activity, DLQ state, or index freshness.

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable owner/reviewer/status truth for parent and sidecar tasks |
| `.orchestrator/task-briefs/svc_source_search_ops_bff_sidecar_bff_handoff.md` | Sidecar scope and artifact target |
| `services/source_ingestion/main.py` | Full source ingest service route inventory: connectors, jobs, frontier, DLQ, audit, schedule |
| `services/search/main.py` | Full search service route inventory: index status, freshness, refresh, reload, pipeline runs, materialize, query |
| `services/control-plane/bff/read_store.py` | Current BFF source/search consumption (registry read + research search index dataset) |
| `services/control-plane/bff/BFF_API_CONTRACT.md` | BFF design rules, degraded-path policy, and current registered operator routes |
| `services/control-plane/bff/BFF_SURFACE_INVENTORY.md` | Surface object catalog; source/search ops deferred to Appendix A |
| `services/control-plane/bff/test_source_connector_service_client.py` | Evidence that BFF already reads connector registry through service client |
| `services/control-plane/bff/test_search_service_client.py` | Evidence that BFF already issues search queries through service client |

---

## 3. Current Surface Snapshot

### 3.1 Source Ingest Service Routes (Available, Not Yet BFF-Proxied)

| Route | Method | Purpose | BFF operator relevance |
|---|---|---|---|
| `GET /health` | GET | Service health + DLQ count | BFF may compose into connector-health projection |
| `GET /api/source-ingest/registry` | GET | Full connector registry with policy/fetch config | **Already consumed by read_store**; candidate for enhanced ops read |
| `GET /api/source-ingest/connectors` | GET | List configured connector instances | Candidate source for connector-health table |
| `POST /api/source-ingest/connectors` | POST | Configure a new connector | Operator command; requires auth + idempotency key |
| `GET /api/source-ingest/connectors/{connector_id}` | GET | Single connector detail | Candidate for connector drill-down |
| `GET /api/source-ingest/connectors/{connector_id}/schedule` | GET | Get connector crawl schedule | Candidate for schedule view |
| `PUT /api/source-ingest/connectors/{connector_id}/schedule` | PUT | Set connector crawl schedule | Operator command; requires auth + idempotency |
| `POST /api/source-ingest/jobs` | POST | Trigger ingest job | Operator command; requires auth + idempotency |
| `GET /api/source-ingest/jobs` | GET | List ingest runs | Candidate source for crawl-runs table |
| `GET /api/source-ingest/jobs/{ingest_run_id}` | GET | Single run detail | Candidate for run drill-down |
| `GET /api/source-ingest/watermarks/{connector_id}` | GET | Crawl watermark per connector | Candidate for connector-health enrichment |
| `GET /api/source-ingest/frontier` | GET | Crawl frontier entries | Candidate source for frontier panel |
| `POST /api/source-ingest/frontier/{frontier_id}/replay` | POST | Replay single frontier entry | Operator command; requires auth + idempotency |
| `GET /api/source-ingest/dlq` | GET | List DLQ entries | Candidate source for DLQ panel |
| `POST /api/source-ingest/dlq/replay` | POST | Replay DLQ entries | Operator command; requires auth + idempotency |
| `POST /api/source-ingest/run-scheduled` | POST | Trigger all due scheduled connectors | Operator command; requires auth + idempotency |
| `GET /api/source-ingest/audit` | GET | Ingest audit actions log | Candidate source for audit/error summary |
| `GET /api/source-ingest/source-records` | GET | List source records | Low-level; not needed in ops panel directly |
| `GET /api/source-ingest/source-records/{source_id}` | GET | Get one source record | Low-level detail route; not needed in ops panel directly |
| `GET /api/source-ingest/evidence/items` | GET | Evidence items | Not needed in ops panel directly |
| `GET /api/source-ingest/evidence/items/{evidence_item_id}` | GET | Get one evidence item | Not needed in ops panel directly |
| `GET /api/source-ingest/evidence/bundles` | GET | Evidence bundles | Not needed in ops panel directly |
| `GET /api/source-ingest/evidence/bundles/{evidence_bundle_id}` | GET | Get one evidence bundle | Not needed in ops panel directly |
| `GET /api/source-ingest/evidence/knowledge-objects` | GET | Knowledge objects derived from evidence | Not needed in ops panel directly |
| `GET /api/source-ingest/evidence/knowledge-objects/{knowledge_object_id}` | GET | Get one knowledge object | Not needed in ops panel directly |

### 3.2 Search Service Routes (Available, Not Yet BFF-Proxied as Ops Surface)

| Route | Method | Purpose | BFF operator relevance |
|---|---|---|---|
| `GET /health` | GET | Service health | BFF may compose into index-health projection |
| `GET /api/search/index/status` | GET | Index availability and adapter state | **Primary candidate** for BFF index-health read |
| `GET /api/search/index/freshness` | GET | Index freshness metrics | **Primary candidate** for BFF freshness surface |
| `GET /api/search/index/pipeline-runs` | GET | List recent index pipeline runs | Candidate source for pipeline-runs table |
| `POST /api/search/index/refresh` | POST | Trigger incremental index refresh | Operator command; requires auth + idempotency |
| `POST /api/search/index/reload` | POST | Full index reload from store | Operator command; requires auth + idempotency |
| `POST /api/search/index/materialize` | POST | Materialize index from current ingest state | Operator command; requires auth + idempotency |
| `GET /api/search/index/materialize` | GET | Get current materialized index state | Candidate for index snapshot read |
| `POST /api/search/query` | POST | Issue search query | Research plane; already used via read_store |
| `POST /api/search/query/request-documents-compat` | POST | Deprecated compat query path | **Should not appear in operator ops panel** |
| `GET /api/search/snapshots/{request_id}` | GET | Get query snapshot | Research plane; not ops panel |

### 3.3 BFF Routes Currently Available for Source/Search

| Route | Content today | Operator ops gap |
|---|---|---|
| `GET /api/v1/operator/research/oss-activation-ready` | Includes research OSS backend inventory (OpenClaw, Qlib, W&B, etc.) but no source/search connector or index ops | No source connector health, crawl runs, DLQ, index freshness, or reindex controls |
| *(none)* | No `/api/v1/operator/source` or `/api/v1/operator/search` routes exist | Entire source/search ops surface is missing from BFF |

---

## 4. Candidate BFF Operator Surface

This packet does not define a canonical route. If the parent owner (`Codex2`) chooses to expose an operator view, the lowest-risk shape follows the established BFF pattern: one composed read route per domain plus narrow command endpoints that forward through service-owned ingest/search logic.

### 4.1 Candidate Read Route — Source Ops

`GET /api/v1/operator/source/ops`

Recommended query parameters:

| Parameter | Default | Bounds | Purpose |
|---|---:|---:|---|
| `connector_limit` | `50` | `1..200` | Bound connector rows |
| `run_limit` | `25` | `1..100` | Bound recent run rows |
| `dlq_limit` | `25` | `1..100` | Bound DLQ entries |
| `frontier_limit` | `25` | `1..100` | Bound frontier entries |

Recommended response envelope:

```json
{
  "data": {
    "connector_health": {
      "status": "ok | degraded | unavailable",
      "connectors": [
        {
          "connector_id": "conn-openalex-api",
          "provider": "OpenAlex",
          "source_type": "paper",
          "status": "enabled | disabled | error",
          "last_run": {},
          "watermark": {},
          "schedule": {},
          "allowedActions": {
            "canTriggerJob": true,
            "canSetSchedule": true
          }
        }
      ]
    },
    "crawl_runs": {
      "status": "ok | degraded | unavailable",
      "runs": [],
      "total": 0
    },
    "frontier": {
      "status": "ok | degraded | unavailable",
      "entries": [],
      "total": 0
    },
    "dlq": {
      "status": "ok | degraded | unavailable",
      "entries": [],
      "total": 0,
      "allowedActions": {
        "canReplay": true
      }
    },
    "audit_summary": {
      "recent_errors": [],
      "total_actions": 0,
      "last_activity_at": null
    }
  },
  "meta": {
    "snapshot_at": "2026-04-30T00:00:00Z",
    "surfaces": {
      "source_connector_health": {"status": "ok | degraded | unavailable"},
      "crawl_runs": {"status": "ok | degraded | unavailable"},
      "dlq": {"status": "ok | degraded | unavailable"},
      "frontier": {"status": "ok | degraded | unavailable"},
      "audit_summary": {"status": "ok | degraded | unavailable"}
    }
  }
}
```

### 4.2 Candidate Read Route — Search Ops

`GET /api/v1/operator/search/ops`

Recommended query parameters:

| Parameter | Default | Bounds | Purpose |
|---|---:|---:|---|
| `pipeline_run_limit` | `25` | `1..100` | Bound pipeline run rows |

Recommended response envelope:

```json
{
  "data": {
    "index_health": {
      "status": "ok | degraded | unavailable",
      "adapter_state": "ready | building | error | unknown",
      "document_count": 0,
      "last_refresh_at": null
    },
    "freshness": {
      "status": "ok | stale | unavailable",
      "staleness_seconds": 0,
      "freshness_threshold_seconds": 3600,
      "last_indexed_at": null
    },
    "pipeline_runs": {
      "status": "ok | degraded | unavailable",
      "runs": [],
      "total": 0
    },
    "snapshot": {},
    "allowedActions": {
      "canRefreshIndex": true,
      "canReloadIndex": true,
      "canMaterializeIndex": false
    }
  },
  "meta": {
    "snapshot_at": "2026-04-30T00:00:00Z",
    "surfaces": {
      "index_health": {"status": "ok | degraded | unavailable"},
      "freshness": {"status": "ok | degraded | unavailable"},
      "pipeline_runs": {"status": "ok | degraded | unavailable"}
    }
  }
}
```

### 4.3 Candidate Command Endpoints

Commands must be auth-guarded (`X-Operator-Id` required) and idempotency-key protected (`X-Idempotency-Key` recommended). All commands forward to the appropriate service — the BFF must not read service volumes directly.

| Candidate route | Method | Forwards to | Auth | Idempotency |
|---|---|---|---|---|
| `/api/v1/operator/source/ops/connectors/{connector_id}/jobs` | POST | `POST /api/source-ingest/jobs` | Required | Required |
| `/api/v1/operator/source/ops/connectors/{connector_id}/schedule` | PUT | `PUT /api/source-ingest/connectors/{connector_id}/schedule` | Required | Not required (idempotent PUT) |
| `/api/v1/operator/source/ops/frontier/{frontier_id}/replay` | POST | `POST /api/source-ingest/frontier/{frontier_id}/replay` | Required | Required |
| `/api/v1/operator/source/ops/dlq/replay` | POST | `POST /api/source-ingest/dlq/replay` | Required | Required |
| `/api/v1/operator/search/ops/index/refresh` | POST | `POST /api/search/index/refresh` | Required | Required |
| `/api/v1/operator/search/ops/index/reload` | POST | `POST /api/search/index/reload` | Required | Required |

Do not expose:

- `POST /api/search/index/materialize` directly — should require explicit operator approval gate (equivalent to BFF admin-only command, not standard operator command).
- `POST /api/source-ingest/run-scheduled` directly — should be scheduler-triggered, not a browser-facing button.
- `POST /api/source-ingest/connectors` — connector configuration is a provisioning-plane operation, not an operator ops surface action.

---

## 5. Operator Journey

### 5.1 Connector Health and Crawl Run Monitoring

1. Operator opens the Source Ops panel (`GET /api/v1/operator/source/ops`).
2. BFF composes connector list from `GET /api/source-ingest/connectors` + watermarks from `GET /api/source-ingest/watermarks/{connector_id}`.
3. Operator sees each connector's status, last-run result, and watermark.
4. If a connector shows `status: error`, operator drills into crawl-runs table.
5. Operator can trigger a manual run via `POST /api/v1/operator/source/ops/connectors/{connector_id}/jobs`.

**Degraded path**: If source ingest service is unreachable, surface shows `status: degraded` with last-known connector entries from read_store local snapshot fallback (if available). BFF must never return `data: null` on downstream outage (per BFF API contract rule).

### 5.2 DLQ Review and Replay

1. Operator sees DLQ count in the connector health panel header.
2. Operator opens DLQ drawer to inspect entries from `GET /api/source-ingest/dlq`.
3. For approved entries, operator issues `POST /api/v1/operator/source/ops/dlq/replay` with entry IDs.
4. BFF forwards to `POST /api/source-ingest/dlq/replay` and returns operation receipt.
5. Operator sees updated run list reflecting replay result.

### 5.3 Index Freshness and Reindex Controls

1. Operator opens the Search Ops panel (`GET /api/v1/operator/search/ops`).
2. BFF composes index health from `GET /api/search/index/status` and freshness from `GET /api/search/index/freshness`.
3. If `freshness.status === "stale"`, panel shows staleness age and highlights available actions.
4. Operator triggers refresh via `POST /api/v1/operator/search/ops/index/refresh` (incremental) or reload (full).
5. BFF returns operation receipt. Operator polls `GET /api/v1/operator/search/ops` to see updated freshness after background index rebuild completes.

**Degraded path**: If search service is unreachable, surface shows `status: degraded` with last-known freshness from read_store `research_search_index` dataset. BFF must not fall through to a broken state.

---

## 6. BFF Gap Matrix

| Gap area | Current state | What parent task must add |
|---|---|---|
| **G1 — Connector health read** | `read_store.get_source_connector_registry()` reads registry only; no connector instance list, watermarks, or health state | BFF route `GET /api/v1/operator/source/ops` composing connectors + watermarks + last-run state |
| **G2 — Crawl run visibility** | No BFF route for ingest job history | BFF reads `GET /api/source-ingest/jobs` and projects into `crawl_runs` section |
| **G3 — DLQ visibility** | No BFF route for DLQ entries | BFF reads `GET /api/source-ingest/dlq` and projects DLQ count + entries into ops surface |
| **G4 — DLQ replay command** | No BFF command endpoint for DLQ replay | BFF `POST /api/v1/operator/source/ops/dlq/replay` forwarding to `POST /api/source-ingest/dlq/replay` with auth + idempotency |
| **G5 — Frontier visibility** | No BFF route for crawl frontier | BFF reads `GET /api/source-ingest/frontier` and projects into ops surface |
| **G6 — Frontier replay command** | No BFF command for frontier replay | BFF `POST /api/v1/operator/source/ops/frontier/{frontier_id}/replay` with auth |
| **G7 — Manual job trigger** | No BFF command for manual ingest job | BFF `POST /api/v1/operator/source/ops/connectors/{connector_id}/jobs` with auth |
| **G8 — Schedule set/get** | No BFF command for connector schedule | BFF `GET`/`PUT /api/v1/operator/source/ops/connectors/{connector_id}/schedule` with auth |
| **G9 — Source audit summary** | No BFF audit surface for source ingestion | BFF reads `GET /api/source-ingest/audit` and projects recent errors into `audit_summary` |
| **G10 — Index health read** | `research_search_index` dataset in read_store is for research query path; no operator index health surface | BFF route `GET /api/v1/operator/search/ops` composing `GET /api/search/index/status` + `GET /api/search/index/freshness` |
| **G11 — Index freshness read** | No BFF freshness surface | BFF reads `GET /api/search/index/freshness` and projects into `freshness` section |
| **G12 — Pipeline runs visibility** | No BFF pipeline-runs surface | BFF reads `GET /api/search/index/pipeline-runs` and projects into `pipeline_runs` section |
| **G13 — Index refresh command** | No BFF command for index refresh | BFF `POST /api/v1/operator/search/ops/index/refresh` forwarding to `POST /api/search/index/refresh` with auth + idempotency |
| **G14 — Index reload command** | No BFF command for index reload | BFF `POST /api/v1/operator/search/ops/index/reload` forwarding to `POST /api/search/index/reload` with auth + idempotency |
| **G15 — BFF API contract entry** | `/api/v1/operator/source/ops` and `/api/v1/operator/search/ops` not listed in BFF_API_CONTRACT.md | Parent owner must add contract entries when routes are implemented |
| **G16 — Degraded surface handling** | No source/search degraded path in BFF | Both new routes must follow BFF "never show none" rule: return `status: degraded` with stale data rather than empty/null on service outage |

---

## 7. Frontend Screen Regions

The following regions describe the minimum operator UI needed to consume the candidate BFF surface. This does not define a canonical UI contract — it is a frontend handoff note for the UI implementer to review.

### 7.1 Source Ops Panel

| Region | Data source | Purpose |
|---|---|---|
| Connector health table | `data.connector_health.connectors[]` | Status badge, last-run time, watermark, and trigger button per connector |
| Crawl run history drawer | `data.crawl_runs.runs[]` | Tabular list of recent ingest jobs with status, connector, start/end time, record count |
| Frontier entries panel | `data.frontier.entries[]` | List of crawl frontier items with replay action |
| DLQ panel | `data.dlq.entries[]` + `data.dlq.total` | Count badge in header, expandable list with replay action |
| Audit summary rail | `data.audit_summary.recent_errors[]` | Compact error log with timestamp and connector reference |
| Source service health badge | `meta.surfaces.source_connector_health.status` | Green/amber/red badge in panel header |

**`allowedActions` gating**: show trigger-job button only when `connector.allowedActions.canTriggerJob === true`; show replay button only when `data.dlq.allowedActions.canReplay === true`.

### 7.2 Search Ops Panel

| Region | Data source | Purpose |
|---|---|---|
| Index health badge | `data.index_health.status` + `data.index_health.adapter_state` | Green/amber/red badge with adapter state label |
| Freshness indicator | `data.freshness.status` + `data.freshness.staleness_seconds` | Age display; amber/red when stale |
| Pipeline runs table | `data.pipeline_runs.runs[]` | List of recent index pipeline runs with status and duration |
| Reindex action bar | `data.allowedActions` | Refresh (incremental) and reload (full) buttons, disabled when actions not allowed |
| Search service health badge | `meta.surfaces.index_health.status` | Green/amber/red badge in panel header |

**`allowedActions` gating**: show refresh button only when `data.allowedActions.canRefreshIndex === true`; show reload button only when `data.allowedActions.canReloadIndex === true`.

---

## 8. Reviewer Checklists

### 8.1 Parent Task Reviewer Checklist (for `SVC-SOURCE-SEARCH-OPS-BFF` reviewer: `Claude`)

- [ ] BFF routes `GET /api/v1/operator/source/ops` and `GET /api/v1/operator/search/ops` are implemented and consume services through `read_store` or explicit service clients, not filesystem volumes.
- [ ] DLQ replay, frontier replay, manual job trigger, and index refresh/reload are auth-guarded (`X-Operator-Id` required) and idempotency-protected.
- [ ] Degraded path: both surfaces return `status: degraded` with last-known stale data rather than HTTP 5xx on service outage.
- [ ] `BFF_API_CONTRACT.md` is updated to list the new routes.
- [ ] Tests cover: connector health read (ok + degraded), DLQ read (ok + empty + degraded), index freshness (ok + stale + degraded), and at least one command endpoint (auth denied + success).
- [ ] No source ingestion or search volumes (`*.jsonl`, `*.db`) are read directly from the BFF process.

### 8.2 Sidecar Packet Reviewer Checklist (for `SVC-SOURCE-SEARCH-OPS-BFF-SIDECAR-BFF-HANDOFF` reviewer: `Codex2`)

- [ ] Service route inventory in §3 is accurate against current `services/source_ingestion/main.py` and `services/search/main.py`.
- [ ] Gap matrix in §6 correctly identifies gaps (G1–G16) between current BFF state and parent task acceptance criteria.
- [ ] Candidate surface in §4 does not violate BFF design rules (no direct volume reads, no parallel truth sources, commands require auth).
- [ ] Operator journey in §5 is coherent with the parent task acceptance criteria.
- [ ] No canonical truth (L1 policy docs, BFF_API_CONTRACT.md, BFF_SURFACE_INVENTORY.md, service implementations) was modified by this sidecar.
- [ ] Packet is useful as-is for the parent owner to implement the canonical task.
