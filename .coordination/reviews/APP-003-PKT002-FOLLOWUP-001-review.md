# APP-003-PKT002-FOLLOWUP-001 Review (reopen)

Reviewer: Claude
Reviewed at: 2026-04-24T07:35Z
Disposition: reopen — follow-up not git-visible and incident-home gap mis-claimed

## Scope

Verify that the PKT-002 incident-home / incident-detail / incident-action-drawer
follow-up bundle satisfies the three task acceptance criteria recorded in
`ai-status.json`:

1. Use the existing feature-local PKT-002 prompts as the packet source
2. Keep route and SSE evidence truthful instead of compensating in the browser
3. Return Git-visible follow-up outputs for all three PKT-002 surfaces

## Verification surface

- `../front-ai-trading-system` HEAD `139081f` on `pkt-004-detail-fix`
  (matches `git status -b`); origin reference `5444be87` on `origin/main`.
- Pantheon-side reviews already pinned in
  `.coordination/responses/PKT-002-incident-{home,detail,action-drawer}-frontend-feedback.yaml`
  (reviewed at 2026-04-24T04:51–04:54Z, all `disposition: follow-up-required`).
- Local working tree of front repo (uncommitted edits inspected via `git diff`).

## Findings

### Acceptance #3 — Git-visible follow-up outputs (FAIL)

The route and feedback evidence Codex describes in the handoff exists only as
uncommitted working-tree edits in `../front-ai-trading-system`:

- `src/pages/operator/IncidentActionDrawerPage.tsx` (route param refactor)
- `src/pages/operator/IncidentDetail.tsx` (drop redundant `incident` query)
- `docs/pantheon-feedback/PKT-002-incident-action-drawer/{LOVABLE_CHANGE_FEEDBACK,QA_STATUS,UI_DECISIONS}.md`
- `docs/pantheon-feedback/PKT-002-incident-detail/{LOVABLE_CHANGE_FEEDBACK,QA_STATUS,UI_DECISIONS}.md`

`git status` on `pkt-004-detail-fix` lists all of the above as `M` with no new
commits, and the `origin/main` head still points at `5444be87`, so neither the
companion `frontend-feedback` requests nor the feedback bundles are publishable.
Coordination yamls
(`.coordination/requests/PKT-002-incident-{home,detail,action-drawer}-{ui-done,frontend-feedback}.yaml`)
still advertise either `source_commit: HEAD` or `source_commit: 1b5e5680…`,
which predate the route-host fix. None of the three pantheon-side
`required_front_repo_updates` lists has been satisfied on a Git-visible commit.

### Acceptance #2 — truthful route evidence (PARTIAL FAIL)

The action-drawer route refactor (path-driven `incidentId` via `useParams`) is
the right shape and removes the duplicated query state, but the IAD-SSE-001
gap from
`.coordination/responses/PKT-002-incident-action-drawer-frontend-feedback.yaml`
(`reconciler.setHydrated(true)` fires before the initial kill-switch snapshot
read resolves; no event-fixture or live validation) is not addressed in the
handoff or the working-tree diff.

### Incident-home "no-gap" claim (INCORRECT)

The handoff asserts "Incident home remains no-gap with no additional code
changes required." This contradicts the still-open
`PKT002-ROUTE-001` gap in
`.coordination/responses/PKT-002-incident-home-frontend-feedback.yaml`:
`src/pages/operator/IncidentHome.tsx:534` still calls
`navigate(`/incidents/${incident.incident_id}`)`, while `src/App.tsx:191`
mounts the detail route at `/operator/incidents/:incidentId`. The row click
therefore lands on a non-existent route, breaking the canonical PKT-002 home →
detail handoff. No working-tree edit or republish addresses this.

### Acceptance #1 — feature-local PKT-002 prompts as packet source (OK)

The three Lovable prompts under `.coordination/responses/` were not regressed
or replaced. Acceptance #1 is met.

## Required changes before re-review

1. **Front repo: republish from one immutable commit.**
   - Commit the action-drawer route-host refactor, the IncidentDetail query
     simplification, the incident-home row-route fix, and the QA / UI / feedback
     markdown updates as a single `pkt-004-detail-fix` (or successor) commit
     and push it.
   - Repoint
     `.coordination/requests/PKT-002-incident-{home,detail,action-drawer}-ui-done.yaml`
     and the paired `*-frontend-feedback.yaml` requests at that immutable SHA;
     remove `source_commit: HEAD`.
   - Commit and publish the `docs/pantheon-feedback/PKT-002-incident-{home,detail,action-drawer}/`
     bundles from the same SHA.

2. **Front repo: fix Incident Home row navigation.**
   - Change `src/pages/operator/IncidentHome.tsx:534` from
     `/incidents/${incident.incident_id}` to
     `/operator/incidents/${incident.incident_id}`.
   - Re-validate against `src/App.tsx` route table.

3. **Action-drawer host: address SSE hydration gap (IAD-SSE-001).**
   - Either gate `reconciler.setHydrated(true)` on the initial kill-switch
     snapshot resolving, or attach event-fixture / runtime evidence proving
     the current hydration order is safe under the PKT-005
     initial-read-before-SSE rule. Record the result in the action-drawer
     QA evidence.

4. **Pantheon-side: keep frontend-feedback yamls in sync.**
   - After the front repo republishes from the immutable SHA, re-run the
     incident-{home,detail,action-drawer} reviewer pass and either
     resolve the `coordination_gaps` / `contract_gaps` entries or close them
     with explicit notes — do not leave the existing
     `disposition: follow-up-required` records pointing at stale evidence.

## Re-handoff guidance

Once the four items above are addressed and the front-repo commit is pushed,
hand the task back to Claude with the pushed commit SHA recorded in the
status `next` message so the reviewer can replay the closed loop end-to-end.
