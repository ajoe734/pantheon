# MGMT-OPS-003 GAP-001 Deploy Probe Fix BFF Handoff

Status: reviewer-approved support packet; pending task PR merge and owner closeout

Parent task: `MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX`

Sidecar task: `MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF`

Owned layer: BFF/frontend query handoff, operator journey, and deploy-probe
acceptance notes

Not changing: L1 canonical truth, BFF runtime or contracts, governance logic,
deployment workflow, or the `execute-plans` implementation

Intended consumers: the parent owner and the assigned reviewer

## Outcome

No new Pantheon BFF route or query contract is required for this repair. The
failure was in frontend consumption and hosted verification: the Persona Fleet
landing page must remain on the production view when production has zero rows,
while an explicit focused-persona journey may switch context and must request a
large enough result page to find that persona.

The relevant read remains:

```text
GET /bff/management/persona-fleet
```

The parent repair should compose with the existing response and preserve the
strict-live boundary. It must not invent a BFF fallback, expose non-production
rows on an unfocused landing page, or reinterpret an empty production result as
permission to switch tabs.

## Query And View Handoff

| Frontend state | BFF request / view behavior | Fail-closed expectation |
|---|---|---|
| Plain `/management/persona-fleet` landing | Keep the production tab selected and consume the normal fleet read. | Zero production rows render a truthful empty production view; they do not auto-select non-production data. |
| Explicit `personaFocus` | Forward the focus value through the BFF `q` search parameter and refresh when the focus changes. | If the focused identity is absent, show a truthful not-found/empty state; do not substitute another persona. |
| Focused fleet pagination | Send `page_size=100` with the focused query, as implemented and reviewed by the pagination repair. | Do not claim the persona is absent after inspecting only the default first page. |
| Returned fleet rows | Render only BFF-returned live rows and source/degradation state. | No seed, fixture, or locally synthesized persona may appear as live truth. |

`personaFocus` is frontend route state, not a request to add a new BFF
parameter. The frontend maps it to the already supported `q` query. The focus
value must participate in request dependencies so navigation and reload issue
the matching read rather than retaining a stale collection.

## Operator Journey

1. The operator opens Persona Fleet without a focus target. The production tab
   remains the default, including when the production result is empty.
2. The page states that the live production view has no matching rows; it does
   not reveal non-production personas merely to avoid an empty screen.
3. A governed link carrying `personaFocus` opens Persona Fleet for a specific
   identity. The frontend maps the focus to `q`, requests `page_size=100`, and
   refreshes when the focus changes.
4. If the BFF returns the focused persona, the UI may show its appropriate
   context without changing the unfocused default policy. If the BFF does not
   return it, the UI remains fail-closed and explicit.
5. Returning to the plain route restores the production-only default rather
   than persisting an implicit non-production selection.

## Parent Acceptance Handoff

The parent owner should preserve these checks in `execute-plans`:

- component/client coverage proves the plain route stays production-only when
  `productionCount === 0` and non-production rows exist;
- focus coverage proves `personaFocus` maps to `q`, participates in reload
  dependencies, and sends `page_size=100`;
- the deployed `/deployment.json` SHA matches the intended merged frontend
  commit before browser evidence is accepted;
- the hosted probe records `persona fleet rows valid: true`,
  `persona fleet live banner valid: true`, and
  `persona fleet has non-production rows: false` for the plain landing page;
- both Persona Fleet BFF requests return `200`, with zero failed network
  requests or console errors;
- the linked-page Playwright journey passes against the deployed host.

The reviewed delivery evidence records `execute-plans` PR #254 for the default
view repair, PR #256 for focus pagination, and deploy run `29156996097` at
deployed SHA `30bc432f`. These values are evidence for that delivery, not a new
canonical BFF contract.

## Composition Boundary

- The parent owner decides whether to absorb these notes into its delivery and
  remains responsible for frontend code, deployment, and hosted evidence.
- Pantheon BFF remains the read owner; this sidecar does not change its route,
  filtering semantics, authentication, tenant boundary, or response shape.
- A later hosted mismatch should first be classified as frontend bundle,
  request mapping, pagination, auth/CORS, or deployment drift. A BFF change is
  justified only by evidence that the existing route itself cannot satisfy the
  documented read.
- This packet neither approves nor completes the parent task.

## Verification Sources

- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-review.md`
- `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-V2-review.md`
- `support/sidecars/MGMT-OPS-003-GAP-001/MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/MGMT-OPS-003-GAP-001/MGMT-OPS-003-GAP-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `docs/frontend/execute-plans-dev-hosting.md`

Reviewer `Codex` should verify that the packet stays support-only, does not
promote frontend route state into BFF contract truth, and accurately preserves
the fail-closed production-default and focused-query boundaries.

## Closeout Record

Reviewer `Codex` approved anchor `c7e265d92` as support-only: it introduces no
BFF, runtime, or canonical-truth delta, and preserves the production-default
fail-closed boundary plus the `personaFocus` to `q` mapping with
`page_size=100`. Parent-task absorption, frontend delivery, and deployment
remain owned by the parent owner.

Owner closeout verification:

- `git diff --check`
- task status inspected with
  `AI_NAME=Codex2 python3 scripts/ai_status.py show MGMT-OPS-003-GAP-001-DEPLOY-PROBE-FIX-SIDECAR-BFF-HANDOFF`
- artifact claims rechecked against the task-scoped review sources listed
  above
