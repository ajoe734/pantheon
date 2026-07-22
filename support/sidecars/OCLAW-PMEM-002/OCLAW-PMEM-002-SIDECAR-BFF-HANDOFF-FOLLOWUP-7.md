# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 7

Status: support-only composition verdict; not canonical truth or runtime proof
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-7`
Owner: Codex2
Reviewer: Codex

## Purpose And Boundary

This packet re-audits the parent task anchor identified by follow-up 6 and
states what may safely compose into the parent delivery. It does not modify the
reconciler, BFF, frontend, Persona Registry, Memory Plane, governance, or any
canonical contract. The parent owner decides whether to adopt these findings.

The audited support baseline is `origin/dev` at `2d66f2475`, which includes
follow-up 6. The parent branch `origin/task/OCLAW-PMEM-002` points to
`db8e7ca0f`. That anchor is not contained in `origin/dev` and is based on an
older repository snapshot.

## Anchor Audit Verdict

Commit `db8e7ca0f` changes only:

- `.orchestrator/task-briefs/oclaw_pmem_002.md`; and
- the owner-verification section of
  `docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/OCLAW-PMEM-002-openclaw-agent-reconcile.md`.

It records PR #3003 ancestry, 37 focused passing tests, and a blocker for a
real `model=openclaw/{persona_id}` response. It does not add or change runtime
code, storage, BFF routes, BFF tests, or frontend source. Therefore it is useful
task evidence, but it satisfies neither Dispatch A nor Dispatch B from
follow-up 6.

| Follow-up 6 gate | Evidence in `db8e7ca0f` | Verdict |
|---|---|---|
| Durable attempt/result storage | None | Open |
| Persona-scoped governed BFF projection | None | Open |
| Consumer acknowledgement and generation ordering | None | Open |
| Current-generation observed readback and probe | Explicitly blocked | Open |
| Unknown/unavailable and secret-redaction contract tests | None | Open |
| `execute-plans` operator consumption | None; Pantheon commit only | Open |
| Existing reconciler focused tests | `37 passed` recorded | Supporting evidence only |

The parent branch is also substantially behind current `origin/dev`. Its raw
branch-to-branch diff includes unrelated deletions and reversions caused by the
stale base. The parent owner must not merge that raw branch state or treat the
anchor as current BFF/frontend delivery.

## Safe Composition Path

1. Preserve the two-file anchor as evidence by rebasing or selectively
   reapplying only those task-owned changes onto a fresh parent task branch
   from current `dev`.
2. Implement Dispatch A on that current base: durable persona/generation
   attempt and result truth, observed readback plus probe, ordering protection,
   and a fail-closed governed BFF projection.
3. Merge and deploy the Pantheon contract before assigning Dispatch B in
   `ajoe734/execute-plans`.
4. Have the frontend consume only the governed projection, keep every non-ready
   or unknown state conversation-disabled, and reject stale generations.
5. Capture a sanitized current-generation response through
   `model=openclaw/{persona_id}` after both repository SHAs are deployed.

The live-access blocker in the anchor is relevant only to the final hosted
probe. It does not block local implementation or contract tests for the durable
result store, BFF projection, or frontend state machine.

## Dispatch A Acceptance Delta

The Pantheon parent/BFF task remains incomplete until automated evidence shows:

- one durable attempt for duplicate delivery of the same persona and requested
  generation;
- `reconciling` only after durable consumer acknowledgement;
- desired and observed identity/model/workspace/generation kept distinct;
- successful readback and sanitized probe evidence recorded before `ready`;
- completion for generation N cannot overwrite N+1;
- a failed newer generation retains the prior success only as labelled history;
- result-store failure and unknown lifecycle values project unavailable rather
  than desired-only success; and
- provider secrets, raw CLI output, stderr, and exception text are absent from
  the BFF response.

Expanding `metadata.openclaw_agent_reconcile` or retaining only aggregate
`SyncReport` membership does not satisfy this delta.

## Dispatch B Acceptance Delta

The separate `execute-plans` task should start only after the BFF contract is
merged and should prove:

- persona mutation success and agent readiness are separate operator outcomes;
- conversation is enabled only for current-generation observed `ready` with
  probe evidence;
- queued, reconciling, drifted, blocked, failed, unavailable, and unknown all
  disable conversation;
- desired and observed values remain visible during drift;
- a prior success is labelled `previous generation` during a newer attempt;
- stale poll or event responses cannot regress the displayed generation; and
- Retry is offered only when the server declares `retryable=true` and receives
  an idempotency key.

Mock contract tests are useful but do not replace the final deployed response
and ancestry evidence.

## Parent And Reviewer Handoff

- `OCLAW-PMEM-002` owns refreshing its branch, adopting the evidence anchor,
  durable lifecycle, ordering, probe, result taxonomy, and hosted proof.
- The Pantheon BFF owner owns the governed fail-closed projection.
- The assigned `execute-plans` owner owns operator rendering and stale-response
  defense after the BFF contract merges.
- Reviewer `Codex` should accept this sidecar only as a support verdict and
  reject any parent readiness claim based on the evidence anchor, desired
  metadata, aggregate sync output, or agent existence alone.

No canonical truth or primary runtime/registry/governance implementation is
changed by this follow-up.
