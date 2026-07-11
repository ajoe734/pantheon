# MGMT-OPS-003 GAP-003 Flow Focus Fix — BFF / Frontend Handoff

Task: `MGMT-OPS-003-GAP-003-FLOW-FOCUS-FIX-SIDECAR-BFF-HANDOFF`  
Parent: `MGMT-OPS-003-GAP-003-FLOW-FOCUS-FIX`  
Owner: Codex  
Reviewer: Codex2  
Kind: support-only `bff_handoff_packet`

## Boundary

This packet describes how the parent frontend repair should consume existing
Pantheon BFF routes. It does not change canonical truth, route contracts,
runtime data, frontend code, or the parent's acceptance verdict. The parent
owner must verify the deployed OpenAPI document before treating any query as
available.

## Observed Gap And Required Mapping

| Journey point | Canonical UI state | BFF request | Required behavior |
|---|---|---|---|
| Portfolio entry | `/management/portfolio-book` plus BFF-provided context | `GET /bff/management/portfolio-book` and its holdings/positions children | Use the canonical page route; do not preserve or introduce a competing legacy route. Follow response-provided links where present. |
| Persona Fleet focus | `persona_id=<id>` is the link shape currently emitted by Portfolio Book | `GET /bff/management/persona-fleet?q=<id>&page_size=<bounded value>` when deployed OpenAPI advertises `q` | Normalize `persona_id` and legacy `persona` at the page boundary, then issue a server-side query. Do not fetch the default page and filter it locally. |
| Human Inbox target | preserve persona plus incident/holding/runtime/pool/risk/source context | `GET /bff/management/human-inbox` and BFF-provided detail route | Resolve the target from the BFF item identity/detail link. Client filtering is not proof that the target exists. If the list contract has no target query, fetch the exact detail item or retain an explicit unresolved state. |

The repository currently exposes Portfolio Book links shaped like
`/management/persona-fleet?persona_id=...`, while the existing Fleet page reads
`persona` and historically filters a default list response in memory. That is
the focus/pagination failure: the requested persona may exist beyond the
loaded page. The repair may accept `persona` as a compatibility input, but
should emit one stable URL spelling and must not silently turn an absent page
match into “persona does not exist.”

## Query And State Rules

- URL focus must survive reload, browser back/forward, desktop/mobile layout,
  and tab changes.
- The Fleet live request must depend on the normalized focus value so changing
  focus triggers a new request.
- Send snake_case BFF query keys. Do not invent `persona_id` as a BFF query if
  deployed OpenAPI only advertises `q`.
- Use a bounded `page_size` only as a supplement to server search, never as the
  correctness mechanism. A large first page is not an identity lookup.
- Preserve production/non-production classification from the returned row.
  Focus must not move a row between tabs or hide a non-production match behind
  a production default without a clear operator state.
- Treat BFF-provided links as navigation authority, but normalize known
  compatibility query aliases at the destination page boundary.
- Missing, degraded, stale, or unavailable responses remain visible. No seed,
  mock, or independent client reconstruction may fill a strict-live gap.

## Human Review Context Receipt

The Portfolio incident transition should carry, when supplied by the source
response:

- `persona_id`, incident/item identity, and holding identity;
- `runtime_id`, `capital_pool_id`, and deployment stage;
- risk state and source issue codes;
- the BFF detail/manage/recommended-action/evidence links.

The destination must distinguish these outcomes:

1. exact target item loaded;
2. list loaded but target not present or not queryable;
3. source/detail unavailable or degraded;
4. malformed/unsupported context.

Only the first is a successful focused handoff. A substring match across item
titles, summaries, or hrefs is a compatibility aid, not acceptance evidence.

## Parent Operator Journey

1. Cold-load the canonical Portfolio Book route in strict live mode and save
   the BFF response that supplies the incident and navigation links.
2. Follow the incident's Persona Fleet link. Assert that the normalized focus
   appears in the URL, the outgoing Fleet request contains the server query,
   and the returned row identity equals the source persona.
3. Reload and use browser back/forward. Confirm focus, tab classification, and
   source context remain truthful.
4. Follow Performance Attribution. Confirm degraded Portfolio data is not
   promoted into formal attribution.
5. Follow Human Review. Assert the exact item/detail identity and the retained
   persona, holding, runtime, pool, risk, and source-issue context.
6. Repeat at desktop and mobile widths, including a persona that is not in the
   unfiltered first page and an unresolved/degraded review target.

## Rerunnable Evidence Matrix

For each desktop/mobile and normal/degraded case, record:

| Evidence | Required assertion |
|---|---|
| Served identities | frontend commit, Pantheon commit, deployment/run identity, UTC timestamp |
| Source capture | Portfolio incident row and its exact BFF-authored links/context |
| Network | Fleet query and Human Inbox list/detail requests; zero failed required requests |
| UI | focused persona/item identity agrees with the captured source response |
| Navigation | URL state survives reload and back/forward; no blank/lazy-chunk route |
| Truth posture | no fallback/seed data; degraded or unresolved state remains explicit |
| Browser health | console exception and unexpected network failure counts, including zeroes |
| Responsive state | screenshots show no clipped labels, overlapping controls, or hidden focus status |

The reviewer should request changes if the test uses independently mocked
counts, relies only on a large `page_size`, filters an already paginated list,
or cannot name the exact deployed commits.

## Composition Handoff

- Parent owner (`Codex2`): implement in `ajoe734/execute-plans`; do not create a
  frontend mirror in Pantheon.
- Parent reviewer (`Codex`): verify request URLs and exact target identities,
  not merely visible banners or screenshots.
- `MGMT-OPS-003-GAP-003`: consume the repaired path in the hosted desktop/mobile
  workflow proof.
- `MGMT-OPS-003-GAP-004`: use the resulting evidence during independent
  difference closeout; this packet alone is not approval.

## Source References

- `.orchestrator/task-briefs/mgmt_ops_003_gap_003_flow_focus_fix.md` (parent worktree)
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-003-hosted-workflow-e2e.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-V2-review.md`
- `services/control-plane/bff/main.py` (`portfolio-book`, `persona-fleet`, and `human-inbox` routes/link shaping)
- `ajoe734/execute-plans`: `src/management/pages/oversight/_core.tsx`, `src/lib/bff-v1/management.ts`, and hosted E2E coverage
