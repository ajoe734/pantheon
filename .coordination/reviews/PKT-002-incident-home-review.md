# PKT-002 Incident Home Review Packet

## Date

2026-04-17

## Reviewer

Codex

## Scope

Review the returned `PKT-002-incident-home` `frontend-feedback` and `ui-done`
handoffs against the published PKT-002 contract, example payload, coordination
replay rules, and the front-repo implementation in
`/home/edna/code/front-ai-trading-system`.

## Reviewed Artifacts

- Pantheon request:
  - `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`
  - `.coordination/requests/PKT-002-incident-home-ui-done.yaml`
- Front-repo publication paths:
  - `../front-ai-trading-system/.coordination/requests/PKT-002-incident-home-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-home/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-home/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-home/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-002-incident-home/QA_STATUS.md`
- Contract sources:
  - `docs/bff/PKT-002-incident-home.md`
  - `docs/screens/PKT-002-incident-home.md`
  - `docs/examples/PKT-002-incident-home.json`
  - `docs/pantheon-handoffs/PKT-002-incident-home/FRONTEND_CHANGE_SPEC.md`
  - `docs/bff/PKT-005-degradation-banner.md`
  - `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/coordination-loop-spec.md`

## Verification Performed

- Confirmed the sibling front repo exists locally and is currently checked out at
  `8d23e02dceb690ed35c1b3800749d2ca90ae4369`.
- Verified that the dispatched `source_commit` `37ebcaf...` does **not** contain:
  - the published `ui-done` payload path
  - the published `frontend-feedback` payload path
  - the feedback bundle directory
  - the routed `/incidents` screen wiring in `src/App.tsx`
  - the sidebar link in `src/components/AppSidebar.tsx`
  - the PKT-002 Incident Home BFF helpers in `src/lib/bffClient.ts`
- Verified that the reviewed UI implementation exists in front-repo history but is
  split across earlier Git-visible commits:
  - `cf4a59b17e3bdb5a2f1778e6753f7241bb8449c6` adds `src/pages/operator/IncidentHome.tsx`
  - `c08acb3ea59f4c56ced578820aa6a5129a309de1` adds the PKT-002 Incident Home types
  - `23984279c1b7e5fe6bfa0d89908b4fe114c78303` adds the `/incidents` route and BFF helpers
  - `56ecdd48bb2fd422a6b1618b65906f02640c938a` adds the sidebar entry
- Verified that the current front-repo working tree still carries unpublished local
  request and feedback artifacts:
  - untracked: `.coordination/requests/PKT-002-incident-home-ui-done.yaml`
  - untracked: `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`
  - untracked: `docs/pantheon-feedback/PKT-002-incident-home/`
- Verified that the two local untracked request payloads still advertise
  `source_commit: faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7`, which also does **not**
  contain the canonical request pair or feedback bundle.
- Reviewed the current candidate implementation in:
  - `/home/edna/code/front-ai-trading-system/src/pages/operator/IncidentHome.tsx`
  - `/home/edna/code/front-ai-trading-system/src/pages/operator/types.ts`
  - `/home/edna/code/front-ai-trading-system/src/lib/bffClient.ts`
  - `/home/edna/code/front-ai-trading-system/src/App.tsx`
  - `/home/edna/code/front-ai-trading-system/src/components/AppSidebar.tsx`
- Re-ran targeted validation successfully on the current working tree:
  - `npx eslint src/pages/operator/IncidentHome.tsx src/pages/operator/types.ts src/lib/bffClient.ts src/App.tsx src/components/AppSidebar.tsx`
  - `npm run build`

## Findings

### 1. The dispatched `source_commit` is not replayable and does not contain the claimed PKT-002 delivery

Pantheon received mirrored `frontend-feedback` and `ui-done` requests that both advertise:

- `source_commit: 37ebcafacb68ff617f097271c46eaac4a478cbb8`

But that commit is the PKT-005 SSE artifact correction commit in the front repo,
not a PKT-002 Incident Home publication commit. At `37ebcaf...`:

- `src/App.tsx` has no `/incidents` route
- `src/components/AppSidebar.tsx` has no `Incident Home` entry
- `src/lib/bffClient.ts` has no `listIncidentHome()` or
  `getIncidentHomeKillSwitchStatus()` helpers
- `git show 37ebcaf:.coordination/requests/PKT-002-incident-home-ui-done.yaml`
  fails because the payload path is not in that commit
- `git show 37ebcaf:.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`
  fails for the same reason
- `git show 37ebcaf:docs/pantheon-feedback/PKT-002-incident-home/LOVABLE_CHANGE_FEEDBACK.md`
  fails because the feedback bundle is not in that commit either

This violates the closed-loop replay contract: the transport tuple
`payload_path + source_commit` must resolve to the published payload in the
front repo, and Pantheon must be able to inspect the referenced UI cycle from
that commit.

### 2. The required front-owned publication pair still exists only in the working tree, and the local copies still point at the older `faa1bc2...` commit

The front repo currently has local copies of both required request payloads:

- `.coordination/requests/PKT-002-incident-home-ui-done.yaml`
- `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`

Both are untracked, not committed, and both still advertise:

- `source_commit: faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7`

The feedback bundle directory is also untracked. So even ignoring the incorrect
Pantheon-dispatched `37ebcaf...` envelope, the front-owned publication set is
still not replayable from any published commit.

Per the coordination spec, `frontend-feedback` is required for every completed
cycle and the feedback bundle must exist before `ui-done`. The current state is
therefore publication-incomplete even though the reviewed UI implementation is
present in Git history and validates locally.

## Confirmed Positives

- The current candidate `IncidentHome.tsx` uses the shared BFF client only and
  does not add raw component-level fetch calls.
- The current candidate screen independently reads:
  - `GET /api/v1/incidents`
  - `GET /api/v1/kill-switch/status`
- The current candidate screen merges split-read `meta.surfaces` via the shared
  degradation-banner helpers instead of inventing local kill-switch state.
- Active versus resolved incident views are driven by the BFF `status` query
  parameter rather than client-side filtering.
- The current working tree passes targeted lint and a full production build.

## Decision

`PKT-002-incident-home` is **not approved yet**.

The blocking issue is publication and replay integrity, not the visible UI
behavior in the current working tree. Pantheon cannot treat this cycle as
review-complete until the front repo publishes a transport-replayable
`frontend-feedback` + `ui-done` pair whose `source_commit` actually contains:

- the request payloads
- the feedback bundle
- the routed Incident Home wiring
- the BFF-client helpers used by the screen

## Required Follow-up Before Re-review

1. Publish the front-owned payload pair from a real front-repo commit:
   - `.coordination/requests/PKT-002-incident-home-frontend-feedback.yaml`
   - `.coordination/requests/PKT-002-incident-home-ui-done.yaml`
2. Set the payload `source_commit` to the commit that actually contains the PKT-002
   implementation and the published payload paths.
3. Ensure the same commit contains the feedback bundle directory
   `docs/pantheon-feedback/PKT-002-incident-home/`.
4. Redispatch the published payload without mutating it in transit.
5. After a replayable publication exists, Pantheon can proceed to the live-BFF
   integration or acceptance step already requested by the UI lane.
