# SVC-SEARCH-SERVICE BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-SEARCH-SERVICE-SIDECAR-BFF-HANDOFF`
**Parent Task**: `SVC-SEARCH-SERVICE`
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Codex`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-28
**Last Refresh**: 2026-04-28T14:58:28Z
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime/registry/governance implementation, BFF implementation,
frontend code, or compose wiring. The parent owner decides whether and how to
absorb this packet into the main search service activation slice.

---

## 1. Scope Snapshot

`SVC-SERVICE-DISPOSITION` closed with `search` classified as deployable service
deferred for the first single-VM baseline:

- `services/search/` contains governed filtering, a search gateway, index
  adapter/store, deterministic retriever, and tests.
- It does not currently contain a FastAPI/HTTP entrypoint, service Dockerfile,
  health endpoint, port contract, or root compose service.
- `docker-compose.yml` does not run a `search-svc` or equivalent service.
- The existing BFF `GET /api/v1/research/search` route is live for RW-02, but
  its normal implementation path is a BFF read-store projection plus in-process
  governed search library usage, not a network service client.

The parent task should therefore not be framed as "create search semantics from
scratch." It is "promote the existing governed search library into an explicit
search HTTP service, then move the BFF search normal path to that service while
preserving the published RW-02 browser contract."

---

## 2. Current Implementation Snapshot

| Area | Current fact | Evidence |
|---|---|---|
| Governed search library | `SearchGateway` applies `SearchAccessContext` ACL/license/environment filters before ranking, returns cited `EvidenceBundle` refs, and can append replayable index snapshots. | `services/search/gateway.py`, `services/search/filters.py`, `services/search/index_store.py` |
| Index adapter | `KeywordIndexAdapter` builds retriever documents from governed knowledge objects and exposes adapter state, indexed source types, watermarks, and snapshot time. | `services/search/index_adapter.py` |
| Search tests | Tests cover cited evidence refs, OpenClaw scope enforcement, replay without raw payloads, adapter-only keyword input, and metadata search text. | `services/search/tests/test_governed_search.py`, `services/search/tests/test_contracts.py` |
| BFF public route | `GET /api/v1/research/search` validates `q`, `match_type`, `status`, `date_range`, and pagination; returns the published `SearchResult` row shape plus `meta.index_adapter.*`. | `services/control-plane/bff/main.py` |
| BFF current data path | BFF reads `research_search_documents` and `research_search_index` through read-store datasets, builds an in-memory governed repository, invokes `SearchGateway` in process, then projects back to RW-02 rows. | `services/control-plane/bff/read_store.py` |
| BFF unavailable behavior | When the index adapter is absent or unavailable, the BFF returns `503` with `{"error":"search_unavailable","meta":{"surfaces":{"search_results":"unavailable"}}}`. | `services/control-plane/bff/test_rw02_search_contract.py` |
| BFF env/data contract today | Search document and index dataset envs are `PANTHEON_BFF_RESEARCH_SEARCH_DOCUMENT_STORE` and `PANTHEON_BFF_RESEARCH_SEARCH_INDEX_STORE`. There is no explicit search service URL env in compose. | `services/control-plane/bff/read_store.py`, `docker-compose.yml` |
| Root compose | `operator-bff` is present, but there is no `search-svc` service block and no BFF `PANTHEON_SEARCH_SERVICE_URL`/equivalent env. | `docker-compose.yml` |
| Frontend contract | RW-02 frontend materials are already published and require browser calls to the Pantheon BFF only. | `docs/bff/RW-02-search.md`, `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md` |

---

## 3. Activation Target for Parent Owner

Suggested service boundary shape for review, not canonical truth:

| Boundary | Proposed normal-path target |
|---|---|
| Compose service | `search-svc` built from a new `services/search/Dockerfile` |
| Health | `GET /health` |
| Internal service port | Pick one explicit container port, for example `8080`, and use it consistently in Dockerfile, compose, and smoke tests |
| Search API | HTTP route that accepts a governed query request plus BFF-provided identity/access context, then returns cited results and index adapter metadata |
| Replay API | HTTP route or response field that exposes replayable search refs from `JsonlSearchIndexStore` without raw answer payloads |
| Storage/env | Explicit index/document storage envs owned by `search-svc`, not hidden BFF seed data |
| BFF env | New explicit URL such as `PANTHEON_SEARCH_SERVICE_URL=http://search-svc:8080` |
| Browser boundary | Frontend continues to call only `operator-bff`; browser must not call `search-svc` directly |
| Fallback | Any BFF-local dataset fallback remains explicitly fenced as migration/test behavior, not the default single-VM normal path after activation |

The BFF should continue owning the public RW-02 response envelope:

- query param vocabulary
- `SearchResult` projection
- pagination envelope
- `meta.surfaces.search_results`
- `meta.index_adapter.*`
- `links.result_detail` and `links.linked_ticket_detail`
- `search_unavailable` error shape

---

## 4. BFF Query Gap Matrix

| BFF route / flow | Current implementation path | Service API readiness | Activation gap |
|---|---|---|---|
| `GET /api/v1/research/search` | BFF validates params, reads local/service-store datasets, builds an in-process repository, calls `SearchGateway`, and projects RW-02 rows. | No search HTTP service exists. | Add a BFF search-service client behind an explicit URL while preserving the public RW-02 route and payload shape. |
| Query request semantics | BFF currently maps `q`, `match_type`, `status`, `date_range`, `page_token`, and `page_size`; `SearchGateway` receives a narrower `SearchRequest`. | Library `SearchRequest` has governed query/persona/workspace/source/citation fields but no HTTP schema wrapper. | Parent must decide which filters are service-owned vs. BFF-owned projection filters; frontend query vocabulary must remain unchanged. |
| ACL/license/environment filters | `SearchGateway.search()` applies `SearchAccessContext` before ranking. | Library is ready; no network identity/context mapping exists. | BFF must pass operator identity/access/license/environment context to the service; service must filter before ranking and before replay refs are persisted. |
| Result projection | BFF projects `result_id`, `match_type`, `title`, `excerpt`, `linked_ticket_id`, `relevance_score`, and returned links from indexed documents. | Library result is evidence-oriented (`RetrievalResult`) and not identical to RW-02 `SearchResult`. | BFF must either keep projection ownership or the service must return enough typed metadata for BFF to preserve the existing row contract. |
| Index adapter metadata | BFF reads or derives `adapter_state`, `indexed_match_types`, `source_watermarks`, and `snapshot_at`. | Library adapter has snapshot primitives; service wrapper does not exist. | Search service should expose equivalent adapter metadata so BFF no longer treats local BFF seed/index files as the normal truth. |
| Replay/index refs | BFF writes replay refs to a BFF-local JSONL path under `source_evidence/rw02-search-index.jsonl`. | `JsonlSearchIndexStore` is available. | Parent should move replay storage ownership to `search-svc` or define an explicit shared-store boundary; do not leave hidden BFF-local replay as the normal service-backed claim. |
| Unavailable/degraded behavior | With index missing/unavailable, BFF returns the published `503 search_unavailable` envelope. With fallback data enabled, it can still serve rows from local datasets. | No network failure semantics yet. | Service transport failure, missing index, and stale/partial index states must map to truthful BFF `unavailable`, `degraded`, or `stale` states rather than authoritative empty results. |
| Compose readiness | Root compose has no search service and no BFF service URL. | Not ready. | Add Dockerfile, compose service, healthcheck, env/storage contract, and smoke coverage before declaring service activation. |
| Frontend gap behavior | Existing RW-02 spec tells frontend to emit `.coordination/requests/RW-02-search-bff-gap.yaml` if required fields are missing. | Frontend route can remain unchanged. | Service activation must not require a new browser endpoint. Any field drift remains a BFF gap, not a frontend workaround or mock. |

---

## 5. Operator Journey Handoff

### 5.1 Normal Search Journey After Activation

1. Operator opens `/research/search`; frontend calls
   `GET /api/v1/research/search` on `operator-bff`.
2. BFF authenticates the operator, validates query params, and builds the
   governed search request context.
3. BFF calls `search-svc` through the explicit service URL.
4. `search-svc` loads its indexed corpus, applies ACL/license/environment
   filters before ranking, persists replayable cited refs, and returns results
   plus adapter metadata.
5. BFF maps the service result into the existing RW-02 `SearchResult` envelope,
   including `meta.surfaces.search_results` and `meta.index_adapter.*`.
6. Frontend preserves backend ordering, renders `excerpt` as provided, and
   navigates through `links.result_detail` and `links.linked_ticket_detail`.

### 5.2 Degraded or Migration Journey

1. If `search-svc` is unreachable or its index is unavailable, BFF should return
   the published `503 search_unavailable` envelope or an explicit degraded/stale
   response if partial results are intentionally served.
2. If a BFF-local dataset fallback remains during migration, responses must not
   claim service-backed freshness.
3. Frontend must not build a client-side index, backfill from ticket/experiment
   lists, or call `search-svc` directly.
4. Empty result states are authoritative only when the BFF reports a fresh
   search surface. Degraded/unavailable search must render the existing
   degradation/unavailable UI behavior.

---

## 6. Frontend Handoff Materials

This sidecar does not create a new Lovable task. The existing RW-02 frontend
bundle remains valid because service activation is behind the BFF.

| Screen / flow | Frontend contract material | Notes |
|---|---|---|
| Research Search BFF contract | `docs/bff/RW-02-search.md` | Public browser route and response shape remain unchanged. |
| Frontend change spec | `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md` | Continue using the existing BFF client; no raw browser calls to `search-svc`. |
| Screen spec | `docs/screens/RW-02-search.md` | Query bar, filters, result list, pagination, freshness notice, and degradation rules remain valid. |
| Example payload | `docs/examples/RW-02-search.json` | Implementation target for field shape only; not mock production state. |
| Contract-ready handoff | `.coordination/responses/RW-02-search-contract-ready.yaml` | Route-live packet already published for RW-02. |
| Runtime revalidation record | `.coordination/requests/RW-02-search-needs-runtime.yaml` | Earlier runtime drift was resolved by live BFF revalidation; parent service activation should avoid regressing this route. |

Frontend implementation constraints:

- Use `operator-bff` only.
- Preserve backend ordering and `relevance_score`; do not re-rank locally.
- Send only the published query params: `q`, `match_type`, `status`,
  `date_range`, `page_token`, `page_size`.
- Navigate only through returned `links.*`.
- Treat missing required fields as a BFF gap.
- Treat `meta.surfaces.search_results` and `meta.index_adapter.adapter_state`
  as authoritative freshness/degradation signals.

---

## 7. Minimal Smoke Requests for Parent QA

Search service health after compose activation:

```http
GET /health
Host: search-svc
```

Candidate service query shape for parent alignment, not a canonical contract:

```http
POST /api/search/query
Host: search-svc
Content-Type: application/json

{
  "request": {
    "request_id": "search-smoke-001",
    "query": "momentum volatility",
    "persona_id": "operator-workbench",
    "workspace_id": "research-workbench",
    "source_types": ["internal_note"],
    "top_k": 3,
    "require_citations": true,
    "trace_id": "trace-search-smoke-001"
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

BFF normal path with explicit service URL:

```http
GET /api/v1/research/search?q=momentum&page_size=3
Authorization: Bearer op-42:operator
```

BFF unavailable path:

```http
GET /api/v1/research/search?q=momentum
Authorization: Bearer op-42:operator
```

Expected when the search service/index is absent and local fallback is disabled:

```json
{
  "error": "search_unavailable",
  "meta": {
    "surfaces": {
      "search_results": "unavailable"
    }
  }
}
```

Suggested focused verification commands for the parent owner:

```bash
python3 -m pytest -q services/search/tests
python3 -m pytest -q services/control-plane/bff/test_rw02_search_contract.py
docker compose config --quiet
```

After implementation, add a service-wrapper smoke that starts `search-svc`,
queries it over HTTP, then proves BFF uses the explicit service URL in the
normal path.

---

## 8. Verification Evidence

Sidecar rerun:

```bash
python3 -m pytest -q services/search/tests services/control-plane/bff/test_rw02_search_contract.py
```

Result: `13 passed in 2.50s`.

Structural checks performed for this packet:

- Confirmed `services/search/` has library modules and tests, but no Dockerfile
  or HTTP entrypoint.
- Confirmed `docker-compose.yml` has `operator-bff` but no search service block
  and no explicit search service URL env for BFF.
- Confirmed `GET /api/v1/research/search` remains the public BFF/browser route.
- Confirmed RW-02 frontend materials already require BFF-only calls and BFF-gap
  handoff on field drift.

---

## 9. Reviewer Checklist

| Check | Status |
|---|---|
| Support artifact only | PASS |
| Canonical truth untouched | PASS |
| Parent acceptance mapped | PASS |
| BFF query gaps identified | PASS |
| Operator journey handoff included | PASS |
| Frontend no-direct-service boundary preserved | PASS |
| SVC-SERVICE-DISPOSITION negative boundary respected | PASS |
| Reviewer disposition recorded | PENDING |

---

## 10. Handoff Status

Ready for `Codex2` review. Parent owner can use this packet as support-only
input for `SVC-SEARCH-SERVICE`; it should not be treated as canonical design
promotion by itself.
