# SVC-SEARCH-RETRIEVAL-AND-CUTOFF BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SVC-SEARCH-RETRIEVAL-AND-CUTOFF` - Harden search retrieval and cut off request document normal path
**Parent Owner**: `Claude`
**Parent Reviewer**: `Codex`
**Parent Status at packet creation time**: `todo`
**Sidecar Task**: `SVC-SEARCH-RETRIEVAL-AND-CUTOFF-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Claude2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-30`
**Mutates canonical**: `no`

> This is a support artifact only. It does not change L1 truth, BFF route
> contracts, search service behavior, search gateway implementation, or
> frontend implementation. It packages the current BFF search surface, the
> expected parent-task changes, and the remaining handoff guidance for
> reviewer and frontend adoption.

## 1. Executive Summary

The parent task `SVC-SEARCH-RETRIEVAL-AND-CUTOFF` is queued and will harden the
search retrieval and cutoff stack. Two upstream tasks are already done:

- `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE` (commit `80e2a5a`) quarantined
  request-document compatibility behind an explicit flag and a separate compat
  route. The normal query path no longer accepts caller-supplied documents by
  default.
- `SVC-SEARCH-INDEXING-PIPELINE` (commit `8c3bec0`) delivered incremental
  index refresh, schema-versioned retained pipeline snapshots, freshness SLA
  status, and ingest-completion trigger support.

The BFF already exposes a service-backed search route (`GET /api/v1/research/search`)
that calls the search service at `PANTHEON_SEARCH_API_URL` and falls back to a
local `SearchGateway` when that env var is absent. The parent task will
harden the retrieval contract, make the durable index the only allowed normal
path in staging and production, and close the local-fallback gap in the BFF
staging posture.

The frontend should continue calling `GET /api/v1/research/search` through the
BFF client. No new browser-facing routes are expected from the parent task. The
frontend must not call the search service directly, must preserve backend ranking
order, and must render BFF-provided degradation state without inventing reasons.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable sidecar owner/reviewer/status truth |
| `ai-task-archive/tasks/SVC-SEARCH-INDEXING-PIPELINE.json` | Upstream dependency done record, closeout commit `8c3bec0` |
| `ai-task-archive/tasks/SVC-SEARCH-DURABLE-COMPAT-QUARANTINE.json` | Compat quarantine done record, closeout commit `80e2a5a` |
| `.orchestrator/task-briefs/svc_search_retrieval_and_cutoff_sidecar_bff_handoff.md` | Sidecar scope and artifact target |
| `services/search/main.py` | Search service HTTP routes, compat guard, pipeline, index status, freshness |
| `services/search/gateway.py` | `SearchGateway`: ACL/license/environment filter chain and retrieval invocation |
| `services/search/retriever.py` | `KeywordRetriever`: deterministic ranking with relevance score and title-hit boost |
| `services/search/filters.py` | `SearchRequest` and `SearchAccessContext`: persona/workspace/scope/environment checks |
| `services/search/index_pipeline.py` | `IncrementalIndexPipeline`: incremental and full rebuild logic, freshness SLA |
| `services/control-plane/bff/read_store.py` | BFF search client: `list_research_search_results`, `_rw02_search_service_payload`, `get_last_governed_search_refs` |
| `services/control-plane/bff/main.py` | BFF route `GET /api/v1/research/search` and helpers `_rw02_*` |
| `services/control-plane/bff/test_rw02_search_contract.py` | Focused BFF search contract tests |
| `services/control-plane/bff/test_search_service_client.py` | BFF search service client test (service-backed path) |
| `services/control-plane/bff/test_staging_read_store_cutoff_contract.py` | Staging cutoff contract evidence |
| `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md` | Frontend-ready change spec for the existing search surface |

## 3. Current BFF Surface Snapshot

### 3.1 Read Routes

| Route | Status | Purpose |
|---|---|---|
| `GET /api/v1/research/search` | implemented | Ranked, governed, paginated research corpus search |

Supported query parameters:

| Parameter | Default | Bounds | Purpose |
|---|---:|---:|---|
| `q` | required | — | Search query string |
| `match_type` | `all` | `all \| ticket \| experiment \| artifact` | Restrict to a specific corpus segment |
| `status` | absent | open/closed/archived/pending/sealed/superseded/failed | Filter by research ticket status |
| `date_range` | absent | 7d/30d/90d | Restrict results to a recent time window |
| `page_token` | absent | numeric offset string | Cursor for the next page |
| `page_size` | `25` | `1..100` | Page size |

The BFF enforces `require_citations: true` when calling the search service:
only evidence objects with citation refs qualify as results. The access context
is fixed by the BFF at `persona_id: operator-workbench`, `workspace_id:
research-workbench`, `environment: paper`, `access_scopes: [operator, research]`,
`license_scopes: [internal]`.

### 3.2 Command Routes

No command routes exist on the search surface. Index refresh and pipeline
controls are internal to the search service and are not exposed through the
current BFF surface for operators.

## 4. Current Response Model

The search route returns:

```json
{
  "data": [
    {
      "result_id": "rt-20260419-007",
      "match_type": "ticket",
      "title": "...",
      "excerpt": "...",
      "linked_ticket_id": "rt-20260419-007",
      "relevance_score": 0.91,
      "links": {
        "result_detail": "/research/tickets/rt-20260419-007",
        "linked_ticket_detail": "/research/tickets/rt-20260419-007"
      }
    }
  ],
  "page_info": {
    "next_page_token": "25",
    "total": 42
  },
  "meta": {
    "snapshot_at": "2026-04-30T07:00:00Z",
    "surfaces": {
      "search_results": "ok | degraded | stale | unavailable"
    },
    "index_adapter": {
      "snapshot_at": "2026-04-30T06:58:00Z",
      "adapter_state": "fresh | degraded | stale | unavailable",
      "indexed_match_types": ["ticket", "experiment", "artifact"],
      "source_watermarks": {
        "tickets": "2026-04-30T06:55:00Z",
        "experiments": "2026-04-30T06:54:00Z",
        "artifacts": "2026-04-30T06:53:00Z"
      }
    },
    "governed_evidence": {
      "<result_id>": {
        "evidence_bundle_id": "evbundle-rw02-<result_id>",
        "citations": ["<citation_label>"],
        "matched_items": [
          {
            "knowledge_object_id": "<result_id>",
            "source_id": "src-rw02-<result_id>",
            "evidence_item_id": "evi-rw02-<result_id>",
            "content_ref": "/research/<type>/<result_id>#search-index",
            "citation_label": "<citation_label>",
            "matched_terms": ["term1", "term2"]
          }
        ]
      }
    }
  }
}
```

Important UI interpretation:

- `meta.surfaces.search_results = "unavailable"` returns HTTP 503 with error
  `search_unavailable` — do not treat empty results as success.
- `meta.surfaces.search_results = "degraded"` means the search index is stale or
  the service is partially available — show the degradation banner and retain
  results as best-effort.
- `meta.index_adapter.adapter_state != "fresh"` means the corpus may be partially
  indexed — show a corpus-freshness notice using backend metadata.
- `meta.governed_evidence` is present only when at least one result was returned
  from the service-backed path; use it for citation display, not for ranking or
  filtering logic.
- Result ordering must be preserved exactly as returned — do not re-sort client-side.

## 5. Search Service Routes (Not BFF-Exposed)

These routes exist in the search service (`services/search/main.py`) and are
used internally or by authorized pipeline clients, not by the BFF operator surface:

| Route | Purpose | BFF exposure |
|---|---|---|
| `POST /api/search/query` | Normal durable index query | Called by BFF internally |
| `POST /api/search/query/request-documents-compat` | Explicit compat path (dev/test only) | Not exposed |
| `POST /api/search/index/refresh` | Trigger incremental or full index refresh | Not exposed |
| `GET /api/search/index/freshness` | SLA freshness status | Not exposed |
| `GET /api/search/index/status` | Index snapshot status | Not exposed |
| `GET /api/search/index/pipeline-runs` | Recent pipeline run history | Not exposed |
| `POST /api/search/index/materialize` | Snapshot materialized index | Not exposed |
| `GET /api/search/index/materialize` | Get last materialized index | Not exposed |
| `GET /api/search/snapshots/{request_id}` | Get specific result snapshot | Not exposed |

Do not add browser calls to search service internals. The BFF route
`GET /api/v1/research/search` is the browser contract.

## 6. BFF Query Gap Matrix

| Area | Current state | Handoff gap |
|---|---|---|
| Search results | BFF route implemented and service-backed when `PANTHEON_SEARCH_API_URL` is set | Frontend must call BFF only; do not call search service directly |
| Request-document cutoff | Search service rejects caller docs on normal path (commit `80e2a5a`); BFF never sends documents to service | Parent task will add explicit BFF staging/prod enforcement; fallback-only mode will be restricted to dev |
| Index freshness display | BFF search response includes `meta.index_adapter.adapter_state` and `snapshot_at` | Frontend must render freshness notice when `adapter_state != "fresh"` — no separate freshness route needed for current UI |
| Corpus coverage display | `meta.index_adapter.indexed_match_types` and `source_watermarks` are present | Frontend may show watermarks in a corpus-status tooltip or notice but must not infer completeness from them |
| Filter vocabulary | `match_type`, `status`, `date_range` are backend-owned and validated | Frontend must not invent filter values; submit only validated param values |
| Pagination | `page_info.next_page_token` drives cursor-based pagination | Frontend must use `next_page_token` as-is; do not re-derive page offsets from `total` |
| Citation display | `meta.governed_evidence` provides per-result citation bundle refs | Frontend may show citation labels from `matched_items[].citation_label`; do not call evidence service directly |
| Pipeline run history | Search service has pipeline-runs route; BFF does not expose it | Future ops surface can expose pipeline run history after BFF projection rules are reviewed |
| Reindex controls | Search service has refresh/materialize controls; BFF does not expose them | Keep reindex controls internal until a reviewed BFF command with auth and idempotency is designed |
| Degradation | BFF composes surface state and returns backend reasons in `meta` | Frontend must use BFF-provided state; do not infer degradation from empty result counts |

## 7. Operator Journey

1. Operator opens the Research Workbench search screen.
2. UI calls `GET /api/v1/research/search?q=<query>` through the shared BFF client.
3. If `meta.surfaces.search_results = "unavailable"` (HTTP 503), UI shows the
   backend-provided unavailable message and stops result rendering.
4. If `meta.index_adapter.adapter_state != "fresh"`, UI shows a corpus-freshness
   notice using `meta.index_adapter.snapshot_at` and `source_watermarks`.
5. UI renders the ranked result list preserving backend order.
6. Row click navigates to `links.result_detail`. A secondary ticket anchor may
   navigate to `links.linked_ticket_detail`.
7. UI submits filter changes as BFF query params (`match_type`, `status`,
   `date_range`) and re-fetches the full list (not a client-side filter).
8. Pagination uses `page_info.next_page_token` to fetch the next page. It should
   not construct page offsets from `page_info.total`.
9. After the parent task lands: the BFF will enforce that staging/prod must use
   the search service (`PANTHEON_SEARCH_API_URL` required), and the local
   fallback will be dev-only. The operator journey and frontend contract remain
   identical; only the backend enforcement posture changes.

## 8. Frontend Screen Regions

| Region | Data source | Rendering rule |
|---|---|---|
| Query bar | Local UI state | On submit, call `GET /api/v1/research/search?q=<query>` |
| Filter rail | Local UI state | Map to BFF param vocabulary: `match_type`, `status`, `date_range` |
| Degradation banner | `meta.surfaces.search_results`, `meta.index_adapter.adapter_state` | Show when `degraded` or `unavailable`; include backend snapshot_at timestamp |
| Result list | `data[]` | Preserve backend order; render `excerpt` as-is; no client-side re-sort |
| Result row | `data[].result_id`, `title`, `excerpt`, `match_type`, `relevance_score`, `links` | Navigate via `links.result_detail`; use `links.linked_ticket_detail` for ticket anchor |
| Corpus freshness notice | `meta.index_adapter.adapter_state`, `meta.index_adapter.snapshot_at` | Show when `adapter_state != "fresh"` |
| Pagination rail | `page_info.next_page_token`, `page_info.total` | Use `next_page_token` as cursor; `total` is for display only |
| Citation drawer | `meta.governed_evidence[result_id].citations`, `matched_items` | Display citation labels and matched terms; do not call evidence service directly |

## 9. Frontend Guard Rails

- Use `GET /api/v1/research/search` only; do not call `services/search` directly.
- Preserve backend result ordering exactly; do not re-sort by score, date, or match_type client-side.
- Use BFF-provided `links.result_detail` for navigation; do not derive URLs from
  `match_type`, `result_id`, or `linked_ticket_id`.
- Do not send `documents` to the BFF; request-document compatibility is
  quarantined to dev/test paths in the search service and is not a browser concern.
- Treat `meta.surfaces.search_results = "degraded"` as partial results, not failure;
  do not suppress all results on degraded state.
- Treat `meta.surfaces.search_results = "unavailable"` (HTTP 503) as a true
  unavailable state; show the backend message and do not render an empty table as success.
- Do not infer corpus completeness from `page_info.total`; use
  `meta.index_adapter.adapter_state` and `source_watermarks` for freshness signals.
- Keep reindex controls, pipeline run history, and freshness SLA details
  absent from this screen; those belong in a future operator ops surface.

## 10. Post-Parent-Task Change Impact

When the parent task `SVC-SEARCH-RETRIEVAL-AND-CUTOFF` closes:

| Change | Frontend impact |
|---|---|
| BFF staging/prod requires `PANTHEON_SEARCH_API_URL` | No frontend change; BFF posture becomes stricter |
| Local BFF fallback restricted to dev | No frontend change; frontend always calls BFF |
| Retrieval ranking/citation contract hardened | Response shape unchanged; `relevance_score` and `citations` fields continue to be present |
| Request-document compat explicitly dev/deprecated | No frontend change; BFF never sends documents |
| Tests covering cutoff and compat quarantine added | No frontend change; backend enforcement is internal |

The BFF route `GET /api/v1/research/search` and its response shape will remain
stable across the parent task work. The frontend should not need to change when
the parent task lands.

## 11. Reviewer Checklist

For `Claude` sidecar review:

- Confirm this packet is support-only and only adds this sidecar artifact.
- Confirm parent task status and upstream dependency commits match the task archive.
- Confirm route statements match `services/control-plane/bff/main.py` (`GET /api/v1/research/search`).
- Confirm response model fields match `services/control-plane/bff/read_store.py`
  and `test_rw02_search_contract.py`.
- Confirm search service route table matches `services/search/main.py`.
- Confirm compat quarantine history references match
  `ai-task-archive/tasks/SVC-SEARCH-DURABLE-COMPAT-QUARANTINE.json`.
- Confirm frontend guard rails do not expose compat paths or internal search
  service routes.
- Confirm the post-parent-task impact table does not over-promise scope that
  belongs to the parent task.

## 12. Verification Notes

Sidecar preparation verification performed by Claude2:

- Read task-scoped brief, closeout rules, collaboration guide, and current
  `ai-status.json` task entry.
- Confirmed sidecar status is `in_progress`, owner is `Claude2`, reviewer is
  `Claude`, and artifact path is this file.
- Confirmed parent task `SVC-SEARCH-RETRIEVAL-AND-CUTOFF` is still `todo` and
  dependent task `SVC-SEARCH-INDEXING-PIPELINE` is `done` with commit
  `8c3bec024803f0a3d85a52e49dcb32d5584af69d`.
- Confirmed `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE` is `done` with commit
  `80e2a5a0d0fc93ba73c59b73842a03f9e963eb16`, quarantining request-document compat.
- Inspected `services/search/main.py` for all search service HTTP routes,
  compat guard behavior, pipeline refresh endpoints, freshness and status routes.
- Inspected `services/search/gateway.py` for `SearchGateway` filter chain,
  ACL/license/environment enforcement, and citation requirement.
- Inspected `services/search/retriever.py` for `KeywordRetriever` deterministic
  ranking logic (relevance score, title-hit boost).
- Inspected `services/search/filters.py` for `SearchRequest` and
  `SearchAccessContext` persona/workspace/scope/environment checks.
- Inspected `services/control-plane/bff/main.py` for the `GET /api/v1/research/search`
  route, `_rw02_*` helpers, and service-backed vs. local fallback logic.
- Inspected `services/control-plane/bff/read_store.py` for `list_research_search_results`,
  `_rw02_search_service_payload`, `_list_research_search_results_from_service`, and
  `get_last_governed_search_refs`.
- Inspected `services/control-plane/bff/test_rw02_search_contract.py` and
  `test_search_service_client.py` for contract coverage evidence.
- Inspected `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md` for
  existing frontend contract reference.
- Ran `git diff --check -- support/sidecars/SVC-SEARCH-RETRIEVAL-AND-CUTOFF/SVC-SEARCH-RETRIEVAL-AND-CUTOFF-SIDECAR-BFF-HANDOFF.md`: passed (new file).

No runtime, registry, governance, BFF implementation, search service
implementation, L1 canonical document, or frontend implementation files were
edited by this sidecar.
