# Management List API Contract

| Field | Value |
|---|---|
| Status | Required for new Management BFF list endpoints |
| Date | 2026-07-02 |
| Scope | `/bff/management/*` list, table, board, ranking, inbox, and dashboard section endpoints |
| Guardrail | `python3 scripts/audit_management_list_contract.py --baseline docs/architecture/management-list-contract-baseline.json --fail-on-new` |

## Purpose

Management console tables must be cheap to load, cheap to parse, and honest about
what they show. A table route is not allowed to download a full aggregate, embed
detail payloads, or return the same rows under multiple envelopes so the browser
can sort it out later.

This contract applies to new endpoints immediately. Existing endpoints are
tracked in `management-list-contract-baseline.json`; every migration must remove
baseline entries rather than add new ones.
The guardrail audits route handlers and Management builder helpers such as
`_build_management_*`; moving a duplicate envelope into a helper is still a
contract violation.

## Required Envelope

List endpoints must use one canonical response shape:

```json
{
  "data": {
    "items": [],
    "summary": {}
  },
  "page_info": {
    "next_page_token": null,
    "total": 0,
    "page_size": 50
  },
  "meta": {
    "snapshot_at": "2026-07-02T00:00:00Z",
    "surfaces": {},
    "related": {}
  }
}
```

Rules:

- Do not also return top-level `items`, `rows`, `pools`, `rankings`, or other
  aliases for the same list.
- Do not return `data.items` and `data.<domain_plural>` with the same list.
- Do not return both camelCase and snake_case copies of the same field in the
  same DTO. The Management BFF wire format is snake_case for new list DTOs.
  Frontend adapters can camelize at the UI boundary.
- Detail endpoints should use `{ "data": { ... }, "meta": { ... } }` and can
  expose richer nested records when the user explicitly opens a row.
- `meta.related` may expose href-only links to child aggregates. It must not
  embed child aggregate payloads.
- Backend and adapter tests for every new or migrated list route must assert
  that top-level aliases such as `items`, `rows`, `summary`, `rankings`,
  `pools`, and domain-specific plural list names are absent.

## Slim Row DTO

Default list rows may include only:

- stable ids, labels, status/state, severity/risk, owner, timestamps;
- small scalar metrics already needed in the visible table;
- bounded summaries such as counts, latest status, and latest timestamp;
- route refs or links to detail/read-only drilldown endpoints;
- explicit degraded/missing-source signals needed for table honesty.

Default list rows must not include:

- raw `sourceRecord`, `source_record`, `sourceDocument`, or full source rows;
- full connector/source health trees;
- full persona league, capital pool, runtime binding, telemetry, session,
  memory, evaluation, or capability aggregates;
- large evidence arrays, history arrays, transcript turns, or child endpoint
  payloads;
- hidden rows that the frontend will discard after download.

If a table needs expandable row detail, provide a detail endpoint or an explicit
bounded include such as `include=source_summary`, not a default full aggregate.

## Filtering And Paging

Every table/list endpoint must accept the filters that the visible route needs:

- `page_token` and `page_size`;
- route-specific filters such as `q`, `state`, `status`, `environment`,
  `deployment_stage`, `owner`, `persona_id`, `capital_pool_id`, and date window;
- sort/order parameters when the UI exposes sorting over more than one page.

Apply filters before expensive fanout and before detail-grade projection. Page
before row expansion. If the backing store cannot filter directly, the BFF must
still avoid hydrating hidden rows with connector health, telemetry, memory,
sessions, or other deep details.

Frontend code must not fetch all rows and then hide production/non-production,
status, owner, or search matches in the browser. Client-side filtering is only
allowed within the current server-returned page.

## Aggregate And Board Endpoints

Board, pack, cockpit, or dashboard endpoints must be summary-first:

- return section id, label, status, counts, key deltas, degraded reason, and
  hrefs;
- do not embed complete child endpoint payloads;
- do not call child list endpoints and wrap their responses under `data`;
- if previews are required, return a separately bounded `preview_items` list
  with a documented limit and slim DTO.

The user should pay for full portfolio, league, attribution, or inbox rows only
after opening that section.

## Payload Budgets

Default authenticated list requests must target these budgets:

- default response body: target <= 250 KB, hard review gate at 1 MB;
- default page size: <= 50 rows unless the endpoint has a measured reason;
- serialized row: target <= 8 KB p95, hard review gate at 16 KB;
- first visible rows: target <= 1.5 s on dev BFF under normal shell fanout.

Any endpoint above the hard review gate needs a documented waiver and a follow-up
task. A response around 16 MB for a 10-20 row table is a contract failure.

## Dense Table UX

Large management tables must be operable without hunting for controls:

- a table wider than the viewport must use a pinned horizontal scrollbar, a
  sticky bottom table scrollbar, or pagination/column groups that remove the
  need for horizontal scroll;
- the horizontal scrollbar cannot exist only at the end of a long vertical
  table;
- rows above 100 visible records require server paging or virtualization;
- table actions and primary identity columns should remain discoverable when
  scrolling horizontally.

## Development Checklist

Before adding or changing a Management list endpoint:

- define the visible table columns first, then define the slim DTO;
- document which filters are server-side and prove the frontend passes them;
- keep one envelope and one casing;
- move source/detail/health aggregates to detail endpoints;
- add or update route and frontend adapter tests that consume only
  `data.items` and `data.summary`;
- run the contract guardrail with `--fail-on-new`;
- remove baseline fingerprints when an existing endpoint is slimmed.
