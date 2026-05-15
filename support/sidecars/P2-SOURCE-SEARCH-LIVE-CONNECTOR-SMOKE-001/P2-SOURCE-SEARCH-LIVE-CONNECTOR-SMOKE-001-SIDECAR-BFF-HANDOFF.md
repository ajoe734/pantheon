# BFF & Frontend Handoff Packet
## P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001-SIDECAR-BFF-HANDOFF

**Sidecar kind:** `bff_handoff_packet`
**Parent task:** `P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001`
**Produced by:** Claude (owner)
**Reviewer:** Codex2
**Date:** 2026-05-01
**Status:** review_approved — closeout finalized 2026-05-01
**Review:** support/reviews/P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001-SIDECAR-BFF-HANDOFF-codex2-review.md

---

## 1. Purpose

This packet supports the parent task `P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001` by:

1. Mapping **existing BFF surfaces** relevant to source/search live connector smoke
2. Identifying **BFF query gaps** that must be resolved before smoke can be operator-verified
3. Documenting the **operator journey** for connector onboarding → ingest → search readback
4. Providing **frontend handoff materials** (endpoint inventory, schema summary, open items)

This document is a support artifact only. It does not modify canonical truth, runtime contracts, or governance policy.

---

## 2. Canonical Reference Paths

| Layer | File | Scope |
|---|---|---|
| BFF API contract | `services/control-plane/bff/BFF_API_CONTRACT.md` | Canonical surface inventory |
| BFF command contract | `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md` | Write/command surfaces |
| Source ingest service | `services/source_ingestion/main.py` | REST API routes (lines 752–899) |
| Connector schema | `services/source_ingestion/connectors/base.py` | SourceConnector / SourceRecord / policy types |
| External source policy | `services/source_ingestion/external_sources.py` | Forbidden use / PIT / entitlement rules |
| Search gateway | `services/search/gateway.py` | GovernedSearchResponse contract (lines 84–177) |
| OpenClaw adapter | `services/openclaw-gateway-adapter/main.py` | Adapter search / broker / session routes |
| BFF source/search client | `services/control-plane/bff/source_search_ops_client.py` | BFF-side HTTP client (lines 123–238) |
| Production posture | `docs/deployment/source-search-prod-hardening.md` | Env vars / health checks / smoke script |
| Connector framework | `docs/deployment/source-connector-framework.md` | Connector registration and governance |

---

## 3. Existing BFF Surfaces (Source / Search)

### 3.1 Research / Search Surfaces

| Route | Method | Purpose | BFF main.py lines |
|---|---|---|---|
| `/api/v1/research/source-connectors` | GET | List configured source connectors with provider metadata | ~8976–8996 |
| `/api/v1/research/search` | GET | Query research data with rank / filter / pagination | ~8738–8850 |
| `/api/v1/research/tickets` | GET | Research ticket list | ~8767 |
| `/api/v1/research/analysis` | GET | Analysis / experiment list | ~8999 |

**`/api/v1/research/search` response shape** (from `GovernedSearchResponse` + BFF adapter):

```json
{
  "data": [
    {
      "result_id": "<knowledge_object_id>",
      "evidence_bundle_id": "<uuid>",
      "matched_items": [
        {
          "knowledge_object_id": "...",
          "source_id": "...",
          "evidence_item_id": "...",
          "content_ref": "news://...",
          "citation_label": "...",
          "matched_terms": ["..."]
        }
      ],
      "answer_context": "<snippet>",
      "citations": ["..."],
      "relevance_score": 0.87
    }
  ],
  "page_info": { "page_token": null, "has_next": false },
  "meta": {
    "surfaces": ["rw-02"],
    "index_adapter": "bff-local",
    "governed_evidence": true
  }
}
```

### 3.2 Operator Source Surfaces

| Route | Method | Purpose | BFF main.py lines |
|---|---|---|---|
| `/api/v1/operator/source/ops` | GET | Connector list, job state, watermarks, DLQ summary, degradation reasons | ~8530–8593 |
| `/api/v1/operator/source/dlq/replay` | POST | Replay DLQ entries by ID / tag | ~8595–8632 |
| `/api/v1/operator/source/frontier/{frontier_id}/replay` | POST | Replay stuck crawl frontier item | ~8632–8667 |
| `/api/v1/operator/research/oss-activation-ready` | GET | Composed OSS activation ops surface (read-only) | ~8233 |

### 3.3 OpenClaw Adapter (reachable from BFF via service mesh)

| Route | Method | Notes |
|---|---|---|
| `/api/openclaw-adapter/search/query` | POST | Governed evidence search — fail-closed ACL |
| `/api/openclaw-adapter/lifecycle/sessions` | GET/POST | Durable session lifecycle with audit trail |
| `/api/openclaw-adapter/lifecycle/sessions/{id}/audit` | GET | Append-only session audit |
| `/api/openclaw-adapter/broker/live/orders` | POST | **ALWAYS REJECTED** — no live execution |
| `/api/openclaw-adapter/broker/paper/orders` | POST | Gated paper handoff (requires env flag + runtime-manager check) |

---

## 4. SourceRecord & SourceConnector Schema Summary

### SourceRecord (frozen dataclass — `services/source_ingestion/connectors/base.py`)

| Field | Type | Notes |
|---|---|---|
| `source_id` | str | Unique source identifier |
| `connector_id` | str | Parent connector reference |
| `source_type` | SourceType | `paper|repo|internal_note|filing|news|social|alpha_db|macro|market|telemetry` |
| `title` | str | Required |
| `content_ref` | str | URI: `memory://`, `social://`, `alpha-db://`, `https://`… |
| `status` | SourceRecordStatus | `raw→normalized→indexed→rejected→archived` |
| `metadata` | Mapping[str, Any] | `license_scope`, `entitlement_tags`, `access_scope`, `event_time`, `available_time` |
| `trace_id` | str | Propagated from ingest context |
| `created_at` | datetime | Source creation timestamp |

### SourceConnector governance fields (relevant for operator UX)

| Field | Type | Notes |
|---|---|---|
| `auth_type` | AuthType | `none|api_key|oauth|secret_ref|broker_ref` |
| `secret_ref_id` | str \| None | e.g. `env://OPENALEX_API_KEY`, `vault://...` |
| `rate_limit_policy` | RateLimitPolicy | `requests_per_minute`, `burst`, `retry_after_seconds`, `policy_ref` |
| `license_policy` | LicensePolicy | `license_scope`, `allowed_use`, `attribution_required`, `redistribution_allowed` |
| `status` | ConnectorStatus | `enabled|disabled|degraded` |

### EvidenceBundle fields relevant to search UX

| Field | Notes |
|---|---|
| `evidence_bundle_id` | Durable citation reference for all downstream consumers |
| `evidence_items[].available_time` | Point-in-time watermark — must be ≥ `event_time` |
| `evidence_items[].citation_label` | Human-readable citation label for UI display |
| `evidence_items[].content_ref` | Normalized content pointer — no inline secrets |
| `audit_trail` | Ingest completion, index trigger, replay events |

---

## 5. BFF Query Gaps — Identified

The following gaps block or impair operator verification of the live connector smoke:

### GAP-01 — No auth-requirements endpoint for operator secret provisioning

**Problem:** `/api/v1/research/source-connectors` returns connector metadata but does not surface which secrets need to be provisioned, their `secret_ref_id`, or rotation policy.
**Impact:** Operators cannot self-serve connector activation without out-of-band knowledge.
**Proposed resolution:** Add `auth_requirements_summary` to the connector list response, or create `GET /api/v1/operator/source/connectors/{connector_id}/auth-requirements` returning:
```json
{
  "connector_id": "...",
  "auth_type": "api_key",
  "secret_ref_id": "env://OPENALEX_API_KEY",
  "auth_scope": "read",
  "rotation_policy_ref": "...",
  "provisioning_guide_url": "..."
}
```
**Owner recommendation:** Add `auth_requirements_summary` inline in `/api/v1/operator/source/ops` response for MVP; full endpoint can follow.

---

### GAP-02 — No ingest-job → index readback link

**Problem:** After a job completes (`/api/source-ingest/jobs/{ingest_run_id}`), there is no BFF surface that shows whether the completed job triggered a search index refresh, how many items were indexed, or which `evidence_bundle_id` values are now queryable.
**Impact:** Operator cannot confirm end-to-end smoke: ingest ran but search results may not yet reflect it.
**Proposed resolution:** Extend `/api/v1/operator/source/ops` to include per-job `index_refresh_status`:
```json
{
  "last_job": {
    "ingest_run_id": "...",
    "status": "complete",
    "indexed_count": 42,
    "index_refreshed_at": "2026-05-01T16:00:00Z",
    "index_refresh_status": "confirmed | pending | failed"
  }
}
```

---

### GAP-03 — No source-to-search lineage in search results

**Problem:** Search results (`/api/v1/research/search`) return `evidence_bundle_id` but no `source_record_id` or `ingest_run_id`. Operators cannot trace which ingest job produced a given search result.
**Impact:** Breaks the smoke verification chain: ingest run → source record → evidence bundle → search result.
**Proposed resolution:** Add `lineage_refs` to each `RetrievalResult`:
```json
{
  "result_id": "...",
  "lineage_refs": {
    "source_record_id": "...",
    "ingest_run_id": "...",
    "indexed_at": "2026-05-01T15:58:00Z"
  }
}
```

---

### GAP-04 — No dedicated BFF DLQ preview endpoint

**Problem:** `POST /api/v1/operator/source/dlq/replay` requires knowing `entry_ids` up front. While `GET /api/v1/operator/source/ops` already composes DLQ entries from the source-ingest `GET /api/source-ingest/dlq` (including optional `dlq_status`), there is no dedicated BFF endpoint that exposes DLQ entries in a paginated, filterable form specifically for pre-replay inspection.
**Impact:** Operators who want to browse DLQ entries with rich filtering (by status, failure reason, connector) must parse the composed ops view, which is a general-purpose surface not optimized for DLQ triage.
**Proposed resolution:** Add `GET /api/v1/operator/source/dlq/entries?status=pending&limit=50` as a dedicated paginated endpoint returning entry list with failure reason before replay is committed. The current ops endpoint may be sufficient for MVP.

---

### GAP-05 — No source-search composed health view

**Problem:** There is no single BFF surface showing: connector count, job success rate, index freshness, DLQ depth, rejected ACL/license items. Operator must query `/api/v1/operator/source/ops`, search response meta, and OpenClaw adapter separately.
**Impact:** No single screen for operator smoke sign-off.
**Proposed resolution:** Add composed view `GET /api/v1/operator/source-search-health` that aggregates:
- Active / degraded / disabled connector count
- Last 24h job success rate and error breakdown
- Current index item count and freshness timestamp
- DLQ depth
- Rejected-by-ACL count from last search window

---

### GAP-06 — OpenClaw session audit not surfaced in BFF

**Problem:** `/api/openclaw-adapter/lifecycle/sessions/{id}/audit` exists at the adapter layer but is not composed into any BFF operator view.
**Impact:** Operator cannot view governed session audit trail from the control plane.
**Proposed resolution:** Include `audit_trail_url` in `/api/v1/operator/openclaw/ops` pointing to the lifecycle audit endpoint, or proxy it at `GET /api/v1/operator/openclaw/sessions/{id}/audit`.

---

## 6. Operator Journey: Connector Onboarding to Search Readback

This is the intended end-to-end operator journey for the smoke. Each step maps to a BFF or service endpoint.

```
STEP 1 — Discover available connectors
  GET /api/v1/research/source-connectors
  → lists enabled connectors with provider / license / auth_type metadata
  → GAP-01: auth secret requirements are not yet surfaced here

STEP 2 — Provision required credentials (out-of-band for now)
  → place secret at secret_ref_id (e.g., export OPENALEX_API_KEY=...)
  → GAP-01 resolution would surface this in BFF

STEP 3 — Trigger a bounded test ingest job
  POST /api/source-ingest/jobs (direct to source-ingest service)
  Body: { connector_id: "example-openalex-feed", max_items: 10 }
  → returns { ingest_run_id: "..." }

STEP 4 — Poll job completion
  GET /api/source-ingest/jobs/{ingest_run_id}
  → returns status: "complete|running|failed", item_count, error list
  → check failed item count and DLQ depth before proceeding

STEP 5 — Verify source records created
  GET /api/source-ingest/source-records?limit=50
  → note: connector_id is not a supported server-side filter today; filter by connector_id client-side,
    or the parent task can add a narrow server-side filter before the operator UX is productized
  → confirm source_type, license_scope, available_time, content_ref are present
  → no forbidden allowed_use values (broker, execution, live_trading)

STEP 6 — Confirm evidence bundles indexed
  GET /api/v1/operator/source/ops
  → check last_job.index_refresh_status == "confirmed" (GAP-02 resolution)
  → without GAP-02 fix: poll search until results appear (fragile)

STEP 7 — Execute governed search readback
  GET /api/v1/research/search?q=<test_query>&source_types=paper,news
  → confirm results have evidence_bundle_id and citations
  → with GAP-03 fix: confirm lineage_refs.source_record_id traces back to Step 5

STEP 8 — Operator health sign-off
  GET /api/v1/operator/source-search-health (GAP-05 resolution)
  → confirm: connector active, job success, index fresh, DLQ empty, zero ACL rejects

STEP 9 — Record smoke evidence
  → capture ingest_run_id, source_record_id sample, evidence_bundle_id sample, search result sample
  → confirm raw_secret_material_present_in_artifacts = false
  → confirm no broker/order/capital routing in any response
```

---

## 7. Frontend Handoff Items

### 7.1 Required Data Surfaces (operator panel)

| Panel | Primary endpoint | Data needed | Gap |
|---|---|---|---|
| Connector Registry | `GET /api/v1/research/source-connectors` | connector list, auth_type, status | GAP-01: auth secrets |
| Ingest Job Monitor | `GET /api/v1/operator/source/ops` | job list, last run, watermarks | GAP-02: index readback |
| DLQ Manager | new `GET /api/v1/operator/source/dlq/entries` | entries, failure reason, replay button | GAP-04 |
| Search Result Viewer | `GET /api/v1/research/search` | results with citations, evidence refs | GAP-03: lineage |
| Source-Search Health | new `GET /api/v1/operator/source-search-health` | aggregated health | GAP-05 |
| OpenClaw Session Audit | proxy to `/api/openclaw-adapter/lifecycle/sessions/{id}/audit` | audit trail | GAP-06 |

### 7.2 Safe Display Rules (no secrets in UI)

- Never render `secret_ref_id` values in plain text — only show the ref name (e.g., `env://OPENALEX_API_KEY`, not the value)
- `content_ref` values may contain internal URIs — do not follow or display raw URI to end users without normalization
- `allowed_use` values from LicensePolicy must be displayed; if any value includes `broker`, `execution`, or `live_trading`, the UI should flag it as a governance violation
- `available_time` must always be displayed alongside `event_time` to communicate PIT watermark to the operator

### 7.3 Governance Boundary Guards (no-op on frontend but must be visible)

The following controls are enforced at the service layer. The frontend should surface their state to give operators visibility:

| Guard | Where enforced | Frontend signal needed |
|---|---|---|
| No order routing | `external_sources.py` forbidden_routes check | "Order routing: disabled" badge on connector card |
| License allowed_use | `LicensePolicy.allowed_use` | Warn if any `broker/execution/live_trading` present |
| PIT watermark | `available_time >= event_time` validation | Show `available_time` with a lag indicator |
| No raw secrets | `raw_secret_material_present_in_artifacts = false` | Evidence packet metadata display |
| Fail-closed posture | `PANTHEON_SOURCE_SEARCH_POSTURE=production` env var | Health surface: "Production posture: enforced / NOT enforced" |

### 7.4 Environment Requirements for Smoke

```bash
# Required for durable storage (production posture)
PANTHEON_SOURCE_SEARCH_POSTURE=production
DATABASE_URL=postgresql://...
SOURCE_INGEST_EVIDENCE_BACKEND=postgres
SEARCH_INDEX_STORE_BACKEND=postgres
SEARCH_EVIDENCE_BACKEND=postgres
SEARCH_DURABLE_INDEX_ONLY=true

# Required for artifact bucket
PANTHEON_S3_ENDPOINT=...
PANTHEON_ARTIFACT_BUCKET=...
PANTHEON_S3_ACCESS_KEY=...
PANTHEON_S3_SECRET_KEY=...

# Source-specific (example)
OPENALEX_API_KEY=<provisioned-via-vault>

# Smoke test script (from docs/deployment/source-search-prod-hardening.md)
SOURCE_INGEST_URL=http://127.0.0.1:8097 \
SEARCH_URL=http://127.0.0.1:8098 \
  python3 scripts/smoke_source_search_prod_posture.py
```

---

## 8. Open Items Summary

| ID | Description | Priority | Action |
|---|---|---|---|
| GAP-01 | BFF does not surface connector auth requirements | High | Add `auth_requirements_summary` to `/api/v1/operator/source/ops` |
| GAP-02 | No ingest-job → index readback in BFF | High | Extend `/api/v1/operator/source/ops` with `index_refresh_status` per job |
| GAP-03 | Search results lack lineage refs | Medium | Add `lineage_refs` (`source_record_id`, `ingest_run_id`, `indexed_at`) to `RetrievalResult` |
| GAP-04 | No dedicated BFF DLQ preview endpoint (ops surface already composes DLQ entries; dedicated endpoint needed for paginated pre-replay triage) | Medium | Add `GET /api/v1/operator/source/dlq/entries` dedicated endpoint |
| GAP-05 | No composed source-search health view | Medium | Add `GET /api/v1/operator/source-search-health` |
| GAP-06 | OpenClaw audit not exposed in BFF | Low | Proxy or link `/api/openclaw-adapter/lifecycle/sessions/{id}/audit` |

**Sidecar scope note:** Gap resolution is not in scope for this sidecar. This packet surfaces the gaps so the parent task owner (Codex2) and frontend team can decide which gaps must be resolved before the smoke is considered verified, versus which are deferred follow-up items.

---

## 9. Acceptance Criteria Mapping

The parent task acceptance criteria map to BFF surfaces as follows:

| Parent acceptance criterion | BFF verification path | Gap risk |
|---|---|---|
| Bounded live/test connector smoke ingests ≥1 governed external source through SourceRecord and EvidenceBundle with entitlement, license, PIT, and available_time | `GET /api/source-ingest/source-records` + verify fields | GAP-02: index readback not yet confirmed via BFF |
| Search index and BFF/SearchGateway readback prove durable citation/evidence refs without caller-supplied doc normal path | `GET /api/v1/research/search` → check `evidence_bundle_id` and `citations` | GAP-03: lineage tracing not yet in search response |
| No connector can route directly to Lean broker / order-capable execution / paper-canary-live / capital binding | `services/source_ingestion/external_sources.py` forbidden_routes enforcement | Enforced at service layer; BFF does not currently expose enforcement status — GAP-01 and connector policy-check endpoint |

---

## 10. Handoff Note to Reviewer (Codex2)

This packet is ready for review. Please verify:

1. The gap list in Section 5 is accurate and complete given your knowledge of the current source-ingest / search implementation.
2. The operator journey in Section 6 reflects the correct step ordering (no missing steps or wrong endpoints).
3. The frontend handoff items in Section 7 are actionable and do not inadvertently create new canonical truth.
4. If any gap is a blocking gate for the parent smoke acceptance criteria, please flag it so the parent task owner can decide how to proceed.

This document should not be absorbed into any L1 canonical file without explicit human approval. It is a scoped support artifact for the parent task.

---

*Artifact generated by Claude as owner of `P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001-SIDECAR-BFF-HANDOFF`. Reviewer: Codex2.*
