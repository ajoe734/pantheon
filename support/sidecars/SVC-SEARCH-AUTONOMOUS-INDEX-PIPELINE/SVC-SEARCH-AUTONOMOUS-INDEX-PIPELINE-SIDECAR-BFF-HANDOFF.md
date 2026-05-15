# SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE-SIDECAR-BFF-HANDOFF`
**Parent Task**: `SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE`
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Codex`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-29
**Mutates Canonical**: `no`

This is a support artifact only. It records BFF/frontend handoff facts after the
durable search index pipeline was reviewed and finalized. It does not promote
new canonical truth or change service, compose, BFF, or frontend code.

---

## 1. Closure Snapshot

`SVC-SEARCH-AUTONOMOUS-INDEX-PIPELINE` moved search from a caller-supplied
document wrapper toward an autonomous, server-side durable evidence index:

- `search-svc` reloads source-ingest evidence from a JSONL evidence store and
  can answer `/api/search/query` without caller-supplied `documents`.
- The request-documents path remains only as compatibility behavior with
  `index_adapter.adapter_state == "request_documents_compat"`.
- Durable queries report `index_adapter.adapter_state == "durable"` and persist
  replayable search snapshots available through `/api/search/snapshots/{request_id}`.
- Governed ACL/license/environment filtering still happens before ranking.
- Result citations now point to the matched evidence item citation, avoiding
  leakage from another evidence item in the same bundle.
- `docker-compose.yml` shares source-ingest evidence into `search-svc` read-only
  at `/data/source-ingest/source_evidence.jsonl`.
- `operator-bff` is wired with `PANTHEON_SEARCH_API_URL=http://search-svc:8098`.

Primary review approval recorded an ACL/citation isolation fix for mixed-scope
source evidence before finalization.

---

## 2. Current BFF Boundary

Browser-facing search remains the BFF route:

```http
GET /api/v1/research/search
```

The BFF still owns:

- query parameter validation for `q`, `match_type`, `status`, `date_range`,
  `page_token`, and `page_size`;
- RW-02 row projection: `result_id`, `match_type`, `title`, `excerpt`,
  `linked_ticket_id`, `relevance_score`, and `links`;
- pagination and `meta.surfaces.search_results`;
- exposing governed evidence refs under `meta.governed_evidence` when the
  service returns cited results.

When `PANTHEON_SEARCH_API_URL` or `PANTHEON_SEARCH_SERVICE_URL` is set, BFF
calls:

```http
POST /api/search/query
Host: search-svc:8098
```

The service payload intentionally omits `documents`. It includes the operator
persona/workspace, source type, filters applied, and the governed access
context:

```json
{
  "request_id": "rw02-bff-search",
  "trace_id": "trace-rw02-bff-search",
  "query": "momentum",
  "persona_id": "operator-workbench",
  "workspace_id": "research-workbench",
  "source_types": ["internal_note"],
  "environment": "paper",
  "require_citations": true,
  "filters_applied": {
    "match_type": "all",
    "status": null,
    "date_range": null
  },
  "access_context": {
    "persona_id": "operator-workbench",
    "workspace_id": "research-workbench",
    "environment": "paper",
    "access_scopes": ["operator", "research"],
    "license_scopes": ["internal"]
  }
}
```

The BFF service client projects service results back through locally known RW-02
documents. If the service is unavailable, the current implementation returns an
empty service-backed result list from that client path; the public route still
uses the existing `search_unavailable` envelope when the index adapter itself is
absent or unavailable.

---

## 3. Frontend Handoff

No new frontend endpoint is required.

Frontend should continue to:

- call only `operator-bff`;
- send only the published query params;
- preserve backend result ordering and `relevance_score`;
- render `meta.governed_evidence` as evidence/citation support when present;
- treat `meta.surfaces.search_results` and `meta.index_adapter.adapter_state`
  as authoritative freshness/degradation signals;
- avoid building a browser-side index or calling `search-svc` directly.

The durable index work is intentionally hidden behind BFF and compose service
wiring. Existing RW-02 frontend materials remain the browser contract unless a
separate frontend task explicitly changes the route shape.

---

## 4. Operator Journey

Normal path:

1. Operator opens the research search surface.
2. Frontend calls `GET /api/v1/research/search` on `operator-bff`.
3. BFF validates query parameters and constructs the governed access context.
4. BFF calls `search-svc` at `/api/search/query` without sending caller
   documents.
5. `search-svc` reloads durable source evidence, filters by ACL/license/
   environment before ranking, returns cited results, and persists a replay
   snapshot.
6. BFF maps service result IDs back into the existing RW-02 response rows and
   includes governed evidence refs in response metadata.

Replay path:

1. Smoke or operator tooling records the search `request_id`.
2. `GET /api/search/snapshots/{request_id}` returns the stored replay snapshot.
3. Snapshot refs intentionally avoid raw answer payloads.

---

## 5. Verification Evidence

Recorded by the parent review/approval:

```bash
pytest services/search/tests -q
pytest services/source_ingestion/test_service.py -q
pytest services/search/tests/test_http_service.py \
  services/search/tests/test_service_activation_contract.py \
  services/control-plane/bff/test_search_service_client.py \
  services/source_ingestion/test_compose_activation.py -q
```

Additional behavior covered by code/tests:

- `services/search/tests/test_http_service.py` proves durable evidence queries
  work without `documents`, persist snapshots, and reject mixed-scope evidence
  before ranking.
- `services/control-plane/bff/test_search_service_client.py` proves the BFF
  sends no `documents` to `search-svc` and retains governed evidence refs.
- `scripts/smoke_honest_stack.py` reloads the search index from persisted
  source-ingest evidence, queries `search-svc`, and replays the stored snapshot.
- `docker-compose.yml` wires `search-svc` to the source-ingest evidence store
  read-only and gives `operator-bff` `PANTHEON_SEARCH_API_URL`.

The earlier compileall attempt had an existing `services/control-plane/bff`
`__pycache__` permission issue while in-memory compile of 33 files succeeded;
this was recorded as non-syntax evidence in the parent review.

---

## 6. Reviewer Checklist

| Check | Status |
|---|---|
| Support artifact only | PASS |
| Canonical truth untouched | PASS |
| Durable search closure summarized | PASS |
| BFF no-documents service path documented | PASS |
| Frontend no-direct-service boundary preserved | PASS |
| Governed citation/ACL isolation noted | PASS |
| Verification evidence listed | PASS |
| Reviewer disposition recorded | PASS |

---

## 7. Reviewer Disposition

Codex review approved on 2026-04-29.

Review confirmed this packet is support-only material for the finalized durable
search pipeline. It does not modify canonical truth, service contracts, runtime
code, registry/governance implementation, or frontend route shape. The described
BFF no-documents service path, frontend BFF-only boundary, compose evidence
mount, governed citation/ACL isolation note, and verification evidence match the
parent task closure and focused code/test/compose evidence.

No follow-up is required from this sidecar. Parent ownership decides whether to
reuse any packet content in a later mainline BFF/frontend task.

---

## 8. Handoff Status

Review approved. This packet should be treated as sidecar handoff material for
the already finalized durable search pipeline, not as a new canonical service
contract.
