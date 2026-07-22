# PPL-ALLOC-009 Evidence-Index Handoff

- **Sidecar Task**: `PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`
- **Parent Task**: `PPL-ALLOC-009`
- **Owner / Reviewer**: `Codex` / `Antigravity`
- **Helper Kind**: `bff_handoff_packet`
- **Scope**: support-only; no canonical, BFF/runtime, registry, governance,
  frontend, deployment, task-dependency, or capital-state mutation
- **Snapshot date**: 2026-07-12

## Purpose

This packet gives the parent owner one evidence-index shape for composing the
existing BFF query, operator-journey, and frontend handoffs into the final
closeout archive. It does not declare a dependency complete, certify a hosted
deployment, or add a route or schema. Every completion verdict must resolve to
durable task, merge, deployment, and smoke evidence rather than prose in this
sidecar.

## Snapshot And Intake Guard

At this snapshot, durable active state records `PPL-ALLOC-009` as `todo`, owned
by `Antigravity` and reviewed by `Claude`. `PPL-ALLOC-007` is `blocked` on an
Execute Plans ownership and dirty-worktree conflict. No archive files for
`PPL-ALLOC-002` through `PPL-ALLOC-006` or `PPL-ALLOC-008` were found by the
task-scoped archive lookup used for this packet.

Therefore:

- the parent must not infer that an absent active task is done;
- `PPL-ALLOC-007` remains a blocking dependency until its recorded conflict is
  resolved and its reviewed delivery is durable; and
- the parent must identify the authoritative archive, reviewed supersession,
  or other canonical task record for every dependency before requesting
  closeout review.

These are coordination observations, not changes to dependency truth.

## Dependency Evidence Index

The parent archive should complete one row for each dependency. A row is
`verified` only when its references can be opened and agree.

| Task | Terminal task evidence | Pantheon delivery | Execute Plans delivery | Validation / review | Deployment relevance | Intake verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `PPL-ALLOC-002` | archive or reviewed supersession + terminal status | PR + merge SHA or `n/a` with reason | PR + merge SHA or `n/a` with reason | commands, result, reviewer | deployed SHA(s) or `not runtime-affecting` | `unknown` |
| `PPL-ALLOC-003` | same required shape | same | same | same | same | `unknown` |
| `PPL-ALLOC-004` | same required shape | same | same | same | same | `unknown` |
| `PPL-ALLOC-005` | same required shape | same | same | same | same | `unknown` |
| `PPL-ALLOC-006` | same required shape | same | same | same | same | `unknown` |
| `PPL-ALLOC-007` | current active blocker plus later terminal record | same | clean task PR + merge SHA required for frontend work | ownership reconciliation, tests, review | frontend deployed SHA | `blocked` |
| `PPL-ALLOC-008` | archive or reviewed supersession + terminal status | PR + merge SHA or `n/a` with reason | PR + merge SHA or `n/a` with reason | commands, result, reviewer | deployed SHA(s) or `not runtime-affecting` | `unknown` |

Do not use a branch name, unmerged commit, open PR, green local test, task
absence, or sidecar approval as terminal dependency evidence. If a task was
superseded, cite the reviewed replacement and explain which acceptance items
it covers.

## Deployment And Hosted-Proof Index

After all dependency rows are verified, record one immutable deployment
identity and attach the journey evidence to it:

| Evidence class | Required index fields | Fail-closed condition |
| --- | --- | --- |
| Pantheon deployment | repository PRs, merge SHAs, deployed BFF SHA, BFF origin, deployment timestamp | deployed SHA is absent or differs from the accepted merge set |
| Execute Plans deployment | repository PRs, merge SHAs, deployed bundle SHA, frontend origin, build timestamp | frontend source is copied here, bundle identity is unknown, or deployment is not derived from the cited commit |
| Runtime posture | `VITE_BFF_MODE=live`, exact BFF base URL, `VITE_BFF_FALLBACK=strict`, safe write defaults, authenticated role | a required request uses seed/mock/fixture fallback or auth is not established |
| Browser coverage | desktop and mobile viewport, timestamp, sanitized trace/screenshot, required-request failures | either viewport is missing or an error is converted to empty/success |
| BFF command proof | sanitized request/response, idempotency and correlation IDs, domain IDs, admission, terminal command state, audit receipt | only admission/`202`, proposal state, toast, or elapsed time is available |
| Authoritative readback | post-command Fleet/Capital/binding query, snapshot timestamp, identities and weights | readback is missing, stale without disclosure, or disagrees with the intended command |

The evidence set must distinguish `401`, `403`, `409`, `422`, network failure,
stale/degraded reads, terminal command failure, and genuine empty results.
Missing truth remains `unknown`; the frontend or smoke harness must not fill it
from labels, timestamps, optimistic state, or default values.

## Journey Correlation Spine

The parent should maintain one response-derived correlation spine across the
four required journeys:

1. Paper creation: `persona_id` -> isolated paper ledger -> runtime binding ->
   `paper_running`, with an explicit no-live-capital-side-effect result.
2. Promotion review: recommendation -> review -> authorized decision ->
   approval/audit receipt, without claiming direct full-live allocation.
3. Real allocation: ranking snapshot -> complete eligible/excluded universe ->
   advisory targets (`applied: false`) -> rebalance -> stable apply intent ->
   terminal command -> authoritative weight readback.
4. Emergency containment: accepted risk-decreasing reduce/freeze/suspend ->
   terminal result/readback, plus rejected promotion and allocation-increase
   probes.

If any arrow requires matching by display label, page order, or approximate
time, mark that segment blocked and name the missing owner query. Do not create
a frontend-owned aggregate as a substitute for authoritative linkage.

## Parent Closeout Verdict Rules

The parent may request review only when:

- all seven dependency rows resolve to reviewed terminal evidence;
- the cited Pantheon and Execute Plans commits are merged and match the
  deployed SHAs;
- each journey retains positive and required negative request/response
  evidence from that deployment;
- command admission, terminal execution, and authoritative readback are
  separately proven;
- authenticated desktop and mobile runs use live/strict BFF posture; and
- every residual risk states severity, blocking status, owner, and an objective
  expiry or recheck condition.

Any missing blocking item keeps the parent verdict `blocked`. A residual-risk
row records a gap; it does not turn absent acceptance evidence into a pass.

## Parent Composition Checklist

- [ ] Resolve `PPL-ALLOC-007` without sweeping its prior-task or unrelated
      Execute Plans changes into a new commit.
- [ ] Locate and cite durable terminal truth for every other dependency.
- [ ] Fill the dependency evidence index with PR numbers and merge SHAs.
- [ ] Record exact BFF/frontend deployed identities and strict live posture.
- [ ] Attach correlated API and browser evidence for all four journeys.
- [ ] Preserve approval and containment negative probes and sanitized bodies.
- [ ] Reconcile terminal commands to authoritative capital/binding readback.
- [ ] List residual risks with owners and recheck conditions.

## Composition Boundary

Owned layer: task-scoped evidence-index shape, intake guards, correlation
guidance, parent verdict rules, and frontend/BFF proof handoff.

Not changing: L1 canonical truth, service or frontend contracts, task or
dependency state, BFF/frontend/runtime implementation, registry/governance
semantics, deployment configuration, approval decisions, or capital state.

Composes with: the original handoff packet, follow-ups 2-5, the canonical
`PPL-ALLOC-009` closeout task packet, and evidence collected by parent owner
`Antigravity`. Parent reviewer `Claude` retains the final parent acceptance
gate; sidecar reviewer `Antigravity` decides whether this support artifact is
suitable for composition.

## Reviewer Checklist

- [x] Snapshot statements match current durable task state.
- [x] Missing active dependency rows are not treated as terminal success.
- [x] The `PPL-ALLOC-007` blocker remains explicit and is not worked around.
- [x] Admission, execution, readback, and frontend rendering remain distinct.
- [x] No new route, schema, governance, deployment, or capital truth is claimed.
- [x] Output is suitable for parent-owner composition as support material only.

## Finalization Record

- Reviewer gate: approved by `Antigravity`; returned to owner `Codex` for
  closeout.
- Delivered artifact: this support-only evidence-index handoff packet.
- Verification: `git diff --check`; all six referenced handoff/closeout files
  exist; focused boundary scan confirms the packet does not claim canonical,
  runtime, frontend, deployment, governance, dependency, or capital mutation.
- Publication boundary: close only after the task branch PR merges into `dev`;
  the parent task retains responsibility for resolving and proving every
  dependency and hosted journey.

## References

- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`
- `support/sidecars/PPL-ALLOC-009/PPL-ALLOC-009-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md`
- `docs/bff/execution-tasks/2026-07-07-persona-promotion-allocation-gap/PPL-ALLOC-009-closeout-dev-publish.md`
