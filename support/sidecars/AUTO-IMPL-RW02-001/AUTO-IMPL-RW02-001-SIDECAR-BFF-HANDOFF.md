# AUTO-IMPL-RW02-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `AUTO-IMPL-RW02-001` — Implement RW-02 research search route family
**Parent Owner**: TBD (re-assigned after Qwen terminal)
**Parent Reviewer**: Claude
**Sidecar Task**: `AUTO-IMPL-RW02-001-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-20
**Mutates canonical**: no

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime/registry/governance implementations. It catalogs BFF
> route status for RW-02 research search, identifies query gaps, maps the operator
> journey, and provides frontend handoff materials for the parent task owner to absorb.

---

## 1. Scope

`AUTO-IMPL-RW02-001` implements the Research Workbench search surface. The canonical
contract is published at `docs/bff/RW-02-search.md`. This packet covers:

| Module | Surface | Route |
|---|---|---|
| `RW-02` | Research Search | `GET /api/v1/research/search` |

Companion routes (RW-01 ticket list/detail, RW-03 analysis, RW-04 experiment launch,
RW-05 artifact) are out of scope for this packet.

---

## 2. BFF Route Inventory

Route implemented in `services/control-plane/bff/main.py:5535`.

| ID | Method + Path | Params | Status |
|---|---|---|---|
| `RW-02` | `GET /api/v1/research/search` | `q`, `match_type`, `status`, `date_range`, `page_token`, `page_size` | **Live** |

**Auth**: requires at least `operator`, `approver`, `admin`, or `reviewer` role (enforced by `_require_read_role`).

### Response shape

```
data[]
  result_id             — canonical matched entity identity
  match_type            — "ticket" | "experiment" | "artifact"
  title                 — backend-authored result title
  excerpt               — backend-authored search context snippet
  linked_ticket_id      — owning research ticket for the matched entity
  relevance_score       — backend-owned ranking score (0.0–1.0)
  links.result_detail   — canonical drilldown URL for matched entity
  links.linked_ticket_detail — canonical ticket-detail URL for linked_ticket_id
page_info.next_page_token
page_info.total
meta.snapshot_at
meta.surfaces.search_results   — "fresh" | "stale" | "degraded" | "unavailable"
meta.index_adapter.snapshot_at
meta.index_adapter.adapter_state
meta.index_adapter.indexed_match_types[]
meta.index_adapter.source_watermarks.tickets
meta.index_adapter.source_watermarks.experiments
meta.index_adapter.source_watermarks.artifacts
```

All contract-required fields are present and correctly mapped. See `docs/examples/RW-02-search.json`
for the canonical example payload.

### Filter semantics

| Param | Allowed values | Validation |
|---|---|---|
| `q` | non-empty string | 400 on empty or whitespace-only |
| `match_type` | `"all"` (default), `"ticket"`, `"experiment"`, `"artifact"` | 400 on unknown value |
| `status` | `"open"`, `"in_progress"`, `"closed"`, `"archived"` | 400 on unknown value |
| `date_range` | `"24h"`, `"7d"`, `"30d"`, `"90d"` | 400 on unknown value |
| `page_size` | 1–100 (default 25) | FastAPI Query constraint |
| `page_token` | integer offset string | 400 on non-integer or negative |

### Error shape

| Code | Body |
|---|---|
| `400` | `{ "error": "invalid_search_query", "detail": "..." }` |
| `403` | `{ "error": "forbidden" }` |
| `503` | `{ "error": "search_unavailable", "meta": { "surfaces": { "search_results": "unavailable" } } }` |

---

## 3. BFF Query Gap Analysis

### GAP-RW02-001 — Local fallback for search document store (not service-backed)

**Location**: `services/control-plane/bff/read_store.py:3827` (`list_research_search_results`)

**Current behavior**: `list_research_search_results` reads `research_search_documents` from the local
in-process snapshot initialized from `read_store.py:2410`. The env var
`PANTHEON_BFF_RESEARCH_SEARCH_DOCUMENT_STORE` is registered in the `ServiceBackedReadAdapter._DATASETS`
config at `read_store.py:357–363` but no service currently populates it. In production, all searches
run against the seeded snapshot corpus, not a live service-owned document index.

**Impact**: Search results are static. New research tickets, experiments, and artifacts ingested after
BFF startup are not discoverable via search until the process restarts with an updated snapshot.

**Resolution path for parent task**: Wire the `PANTHEON_BFF_RESEARCH_SEARCH_DOCUMENT_STORE` env var to
a service-owned document store, or implement an event-driven index update path. Until then, the
`meta.index_adapter.adapter_state` should reflect actual snapshot age so operators can see staleness.

### GAP-RW02-002 — Local fallback for search index adapter metadata (not service-backed)

**Location**: `services/control-plane/bff/read_store.py:3767` (`get_research_search_index`)

**Current behavior**: `get_research_search_index` first checks the `ServiceBackedReadAdapter` for
`rw02-search-index`. If unavailable it falls back to the local snapshot seeded at `read_store.py:2493`.
The env var `PANTHEON_BFF_RESEARCH_SEARCH_INDEX_STORE` is registered but not wired to any running
service.

**Impact**: `meta.index_adapter.source_watermarks` values are static snapshot timestamps, not live
watermarks reflecting the latest ticket/experiment/artifact ingestion. The frontend will always see the
same `source_watermarks` values regardless of actual data freshness.

**Resolution path for parent task**: Publish a live adapter metadata record from the indexing service,
or at minimum emit a watermark derived from the newest `updated_at` across the corpus.

### GAP-RW02-003 — In-process relevance scoring (not a search engine)

**Location**: `services/control-plane/bff/read_store.py:3873–3883`

**Current behavior**: The BFF performs text matching on `title`, `excerpt`, and `search_text` fields
via Python string tokenization. The `relevance_score` in the document store is used as a base but is
bumped by token overlap frequency. There is no inverted index, stemming, or semantic similarity.

**Impact for parent task / frontend**: The current scoring is sufficient for the seeded demo corpus.
However, for a live corpus with hundreds of documents, this in-process approach will be slow and
produce low-quality rankings. The contract intentionally places ranking authority in the BFF so the
frontend does not need to change when the backend upgrades to a real search engine.

**Sidecar boundary**: This gap does not block the frontend. The contract shape is correct. Document
here for the parent task owner to address in service-owned implementation.

### GAP-RW02-004 — Fallback URL construction for `links.result_detail`

**Location**: `services/control-plane/bff/read_store.py:3895–3902`

**Current behavior**: If a search document does not include a `links.result_detail` value, the BFF
constructs a default: `/research/tickets/{result_id}` for ticket match types and
`/research/{match_type}s/{result_id}` for experiment and artifact match types.

**Impact**: This silent inference could produce incorrect drilldown URLs if the frontend routing
convention differs from this BFF-inferred default. Per the RW-02 contract, `links.result_detail` must
be BFF-authoritative. Documents in the corpus should carry explicit `links` payloads so the fallback
is never exercised in production.

**Resolution path**: Ensure all corpus documents carry explicit `links.result_detail` and
`links.linked_ticket_detail` values. The fallback should be treated as a safety net only, never the
primary source.

---

## 4. Operator Journey Map

### Search flow (happy path)

```
Operator opens /research/search
  → enters query in query bar
  → optionally selects match_type (default: all), status filter, date_range
  → submits to GET /api/v1/research/search?q=<query>&[filters]
  → BFF validates params, fetches index adapter metadata
  → if adapter_state = "unavailable" → 503 → UI shows unavailable notice
  → BFF filters corpus against match_type, status, date_range
  → BFF scores and ranks matching documents
  → BFF returns page_items (up to page_size), page_info.next_page_token, meta
  → UI renders result list in backend order (no client re-rank)
  → Operator clicks result row → navigates to links.result_detail
  → Operator clicks ticket anchor → navigates to links.linked_ticket_detail
  → Operator clicks next page → repeats request with page_token + same active filters
```

### Degraded path (stale index)

```
Operator submits search
  → BFF returns meta.surfaces.search_results = "stale"
     and meta.index_adapter.adapter_state = "stale"
  → UI must render a non-dismissable staleness banner
  → Result rows remain visible; do not claim authoritative empty state
  → meta.index_adapter.snapshot_at shows age of last index sync
```

### Degraded path (partial corpus coverage)

```
Operator submits search with match_type = "experiment"
  → BFF returns results with adapter_state = "fresh"
     but meta.index_adapter.indexed_match_types = ["ticket"] (experiment not yet indexed)
  → Result list may be empty or partial for experiment match type
  → UI must show corpus-freshness notice if indexed_match_types does not include
     the operator's selected match_type
  → Do NOT claim "no results found" as authoritative if coverage is partial
```

### Error paths

```
Operator submits empty query (q="")
  → 400 { "error": "invalid_search_query", "detail": "..." }
  → UI shows inline validation; no result list state change

Operator submits unknown match_type or status filter
  → 400 { "error": "invalid_search_query", "detail": "..." }
  → UI clears filter rail to known-good value; retry optional

Search adapter fully unavailable
  → 503 { "error": "search_unavailable", "meta": { "surfaces": { "search_results": "unavailable" } } }
  → UI suppresses result list; shows unavailable notice
```

---

## 5. Frontend Handoff Summary

**Gate status**: Route is live. All three readiness gate conditions from `docs/screens/RW-02-search.md` are met:

| Gate | Status | Notes |
|---|---|---|
| `GET /api/v1/research/search` live with all filter params | **Met** | `main.py:5535` |
| Published `SearchResult` row shape including `links.*` | **Met** | All fields returned; GAP-RW02-004 notes a fallback path |
| `meta.index_adapter.*` present in response | **Met** | All watermark and state fields present |

**Frontend can proceed** with `ResearchSearch.tsx` implementation per `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md`.

### Integration checklist for frontend

- [ ] Use `GET /api/v1/research/search` exclusively. No local search index, no demo dataset.
- [ ] Submit `q`, `match_type`, `status`, `date_range` exactly as published query params.
- [ ] Preserve backend result order in the rendered list. No client-side re-ranking.
- [ ] Render `excerpt` exactly as provided. Do not generate own snippet.
- [ ] Navigate to `links.result_detail` for row drilldown (never construct URL from `result_id`).
- [ ] Navigate to `links.linked_ticket_detail` for ticket anchor.
- [ ] Repeat all active filter params when fetching the next page via `page_info.next_page_token`.
- [ ] Gate production page on `meta.surfaces.search_results !== "unavailable"`.
- [ ] Show non-dismissable staleness banner when `meta.surfaces.search_results = "stale"`.
- [ ] Show degradation banner when `meta.surfaces.search_results = "degraded"`.
- [ ] Show corpus-freshness notice when `meta.index_adapter.adapter_state !== "fresh"`.
- [ ] If any required field is missing, emit a `bff-gap` handoff instead of rendering with invented state.

### Fields to display per result row

| Field | Source | Notes |
|---|---|---|
| Title | `data[].title` | Required; never invented |
| Excerpt | `data[].excerpt` | Render as-is; never truncate or augment |
| Match type badge | `data[].match_type` | Backend-owned enum: ticket / experiment / artifact |
| Linked ticket reference | `data[].linked_ticket_id` | Link target: `data[].links.linked_ticket_detail` |
| Relevance score | `data[].relevance_score` | Display only; never sort client-side |
| Row drilldown target | `data[].links.result_detail` | Navigate on row click |

### Degradation surface matrix

| `meta.surfaces.search_results` | `meta.index_adapter.adapter_state` | Required UI behavior |
|---|---|---|
| `"fresh"` | `"fresh"` | Normal query, filter, and result rendering |
| `"stale"` | any | Non-dismissable staleness banner; keep results visible |
| `"degraded"` | any | Degradation banner; suppress authoritative empty state |
| `"unavailable"` | any | Suppress result list; show unavailable notice |
| any | `"stale"` | Corpus-freshness notice; do not claim complete coverage |
| any | `"degraded"` | Corpus-freshness notice; do not claim complete coverage |
| any | `"unavailable"` | Corpus-freshness notice; do not claim complete coverage |

---

## 6. References

| Resource | Path |
|---|---|
| BFF contract | `docs/bff/RW-02-search.md` |
| Screen spec | `docs/screens/RW-02-search.md` |
| Frontend change spec | `docs/pantheon-handoffs/RW-02-search/FRONTEND_CHANGE_SPEC.md` |
| Example payload | `docs/examples/RW-02-search.json` |
| BFF route implementation | `services/control-plane/bff/main.py:5535` |
| Read store — search results | `services/control-plane/bff/read_store.py:3819` |
| Read store — index adapter | `services/control-plane/bff/read_store.py:3767` |
| Contract tests | `services/control-plane/bff/test_rw02_search_contract.py` |
| Packet family | `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` |
