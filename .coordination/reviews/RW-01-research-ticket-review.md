# RW-01 Research Ticket Review Packet

## Date

2026-04-21

## Reviewer

Codex

## Findings

### 1. High: live HTTP acceptance is still blocked because the active operator-bff runtime does not yet serve the RW-01 route family

- The current Pantheon workspace still implements and verifies the RW-01 route family locally:
  - `python3 -m pytest services/control-plane/bff/test_rw01_research_ticket_contract.py -q`
  - Result: `6 passed`
- Seeded local FastAPI `TestClient` probes confirm truthful RW-01 degradation semantics:
  - degraded list: `200`, `meta.surfaces.ticket_list = degraded`
  - degraded detail: `200`, `meta.surfaces.ticket_detail = degraded`
  - unavailable list: `200`, `meta.surfaces.ticket_list = unavailable`, `data = []`
  - unavailable detail: `200`, `meta.surfaces.ticket_detail = unavailable`, `ticket_id = rt-20260419-007`
- The active runtime on `http://127.0.0.1:18001` is still stale relative to that workspace:
  - `GET /api/v1/research/tickets` returned `404 Not Found`
  - `GET /openapi.json` exposes no `/api/v1/research/tickets*` paths
- Impact: the front handoff is ready for Pantheon follow-up, but the readiness gate cannot be lifted and live-BFF validation cannot close against the running deployment target yet.

## Reviewed Artifacts

- Pantheon contract bundle:
  - `docs/bff/RW-01-research-ticket.md`
  - `docs/examples/RW-01-research-ticket.json`
  - `docs/screens/RW-01-research-ticket.md`
  - `docs/pantheon-handoffs/RW-01-research-ticket/FRONTEND_CHANGE_SPEC.md`
  - `.coordination/responses/RW-01-research-ticket-contract-ready.yaml`
  - `.coordination/responses/RW-01-research-ticket-lovable-ui-task.yaml`
- Pantheon coordination state:
  - `.coordination/requests/RW-01-research-ticket-ui-done.yaml`
  - `.coordination/requests/RW-01-research-ticket-needs-runtime.yaml`
  - `.coordination/responses/RW-01-research-ticket-frontend-feedback.yaml`
- Returned front-owned artifacts on `origin/pkt-004-detail-fix`:
  - implementation commit `7b807fbe9ebcd5c84baca77de966121c0b2d1d73`
  - handoff metadata repoint commit `4ff0651`
  - current remote branch head `521bcb87139139a8157ecf4cf63aaa4bc89118e1`
  - `../front-ai-trading-system/.coordination/requests/RW-01-research-ticket-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/RW-01-research-ticket-frontend-feedback.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-01-research-ticket/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-01-research-ticket/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-01-research-ticket/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-01-research-ticket/QA_STATUS.md`
  - `../front-ai-trading-system/src/pages/research/ResearchTicketList.tsx`
  - `../front-ai-trading-system/src/pages/research/ResearchTicketDetail.tsx`
  - `../front-ai-trading-system/src/pages/research/types.ts`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
- Pantheon BFF verification:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_rw01_research_ticket_contract.py`

## Verified Positives

- The current remote transport chain is replay-clean for review purposes:
  - `7b807fbe9ebcd5c84baca77de966121c0b2d1d73` contains the RW-01 UI files, the initial handoff bundle, and the feedback bundle.
  - `4ff0651` repoints the canonical request pair to that truthful `source_commit`.
  - `origin/pkt-004-detail-fix` contains both commits.
- The reviewed front router mounts the requested owner surfaces:
  - `/research/tickets`
  - `/research/tickets/:ticket_id`
- `ResearchTicketList.tsx` and `ResearchTicketDetail.tsx` route all RW-01 traffic through `rw01TicketApi`; no raw component-level `fetch` path was introduced.
- Owner selection and filtering now use backend-provided persona identities through `personaDrilldownApi`.
- The generic save path only patches editable fields. Close and archive remain separate explicit CTAs gated directly by `allowedActions.canClose` and `allowedActions.canArchive`.
- Linked experiments and artifacts are rendered as BFF-supplied read-only refs, which matches the RW-01 contract-ready packet.
- Both routes now fail closed to a blocked placeholder when the active runtime returns `404`, `NOT_FOUND`, or `ROUTE_NOT_FOUND`.
- Sibling front verification passed for the reviewed RW-01 slice:
  - `cd ../front-ai-trading-system && npx eslint src/pages/research/ResearchTicketList.tsx src/pages/research/ResearchTicketDetail.tsx src/pages/research/types.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx`
  - `cd ../front-ai-trading-system && npx tsc --noEmit`
  - `cd ../front-ai-trading-system && npm run build`

## Decision

`RW-01-research-ticket` remains blocked for this review cycle.

The front handoff is now contract-aligned and replayable from the published
remote branch. The earlier front-end findings are fixed: owner controls are
persona-backed, lifecycle transitions are explicitly gated, linked refs are no
longer speculative navigation claims, and the readiness gate is enforced in the
screen components. Pantheon still cannot close the loop because the active
operator-bff runtime is stale and does not yet expose the RW-01 route family
over live HTTP.

Pantheon must refresh runtime truth through:

- `.coordination/requests/RW-01-research-ticket-needs-runtime.yaml`

After runtime refresh, rerun the live HTTP verification against the real BFF
before removing the blocked placeholder.

## Required Follow-up

1. Pantheon runtime: refresh or redeploy the running operator-bff service so
   real HTTP exposes:
   - `POST /api/v1/research/tickets`
   - `GET /api/v1/research/tickets`
   - `GET /api/v1/research/tickets/{ticket_id}`
   - `PATCH /api/v1/research/tickets/{ticket_id}`
2. Pantheon runtime: after refresh, verify over real HTTP that degraded and
   unavailable RW-01 responses remain truthful for both
   `meta.surfaces.ticket_list` and `meta.surfaces.ticket_detail`.
3. If the refreshed live runtime diverges from the published RW-01 field shape,
   emit `.coordination/requests/RW-01-research-ticket-bff-gap.yaml` instead of
   widening the UI or inventing local fallback state.

## Residual Risk

- This review validated the current Pantheon app through local contract tests
  and seeded FastAPI `TestClient` probes, then validated the active runtime only
  far enough to prove it is stale for RW-01 over live HTTP.
- No deployed browser session was exercised against a refreshed RW-01 runtime,
  so live operator behavior remains pending runtime confirmation.

## 2026-04-21 Runtime Approval Addendum

The runtime blocker from the original review is now resolved.

- Pantheon refreshed the stale RW-01 contract-test expectation in
  `services/control-plane/bff/test_rw01_research_ticket_contract.py` to match
  the current seeded research-ticket dataset, then reran:
  - `python3 -m pytest services/control-plane/bff/test_rw01_research_ticket_contract.py -q`
  - Result: `6 passed`
- `GET http://127.0.0.1:18001/openapi.json` now exposes:
  - `/api/v1/research/tickets` with `get` and `post`
  - `/api/v1/research/tickets/{ticket_id}` with `get` and `patch`
- Authenticated live probe
  `GET /api/v1/research/tickets?status=in_progress,closed` on `18001`
  returned `200` with:
  - `ids = [rt-20260419-007, rt-20260415-001, tkt-7a8b9c0d-1234-5678-abcd-ef0123456789]`
  - `page_info.total = 3`
  - `meta.surfaces.ticket_list = degraded`
- Authenticated live probe
  `GET /api/v1/research/tickets/rt-20260419-007` on `18001` returned `200`
  with:
  - `meta.surfaces.ticket_detail = degraded`
  - `links.workbench_detail = /research/tickets/rt-20260419-007`
  - `allowedActions = {canEdit: true, canClose: true, canArchive: false}`
- A controlled workspace-backed HTTP probe on `127.0.0.1:18012` launched with
  `BFF_READ_SURFACE_STATE=unavailable` returned:
  - `GET /api/v1/research/tickets?status=in_progress,closed` -> `200`,
    `data = []`, `page_info.total = 0`,
    `meta.surfaces.ticket_list = unavailable`
  - `GET /api/v1/research/tickets/rt-20260419-007` -> `200`,
    `meta.surfaces.ticket_detail = unavailable`,
    `links.workbench_detail = /research/tickets/rt-20260419-007`
- Remote publication truth is now anchored at
  `93a4b58891031442133a6966d0354ae216a80b72` on `origin/pkt-004-detail-fix`,
  and that Git-visible tree contains the canonical RW-01 request pair,
  feedback bundle, and reviewed UI files together.

This clears the remaining live-runtime blocker for `RW-01-research-ticket`.
Deployed browser QA and a live create/patch round-trip on the shared `18001`
store remain non-blocking residual checks, but no contract gap remains in the
current RW-01 loop.
