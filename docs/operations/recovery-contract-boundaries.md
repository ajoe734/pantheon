# Development-tooling recovery contract repair

Date: 2026-09-08. Scope: supervisor worker cleanup, exact-delivery merge
receipts, review-audit recovery, and auto-integrator repair materialization.
Operator explicitly authorized direct tooling implementation and delivery.
This is not product runtime, Management/Agora readiness, or twelve-loop acceptance.

## SA: verified failure and authority boundaries

The 2026-09-07 installed-code replay used runtime
`621c6622cb82d4e55e08a76caeb781a1c0667e93`, canonical event-count 5142,
and 608 relevant logical audit events. Five isolated cases proved that an
archived normal-done task, represented without roles, made lost-lease recovery
propose `superseded` while final CAS rejected that same transition. This
discarded another worker's valid cleanup in the same batch. The archive
receipt/hash/fact already proved the missing roles; recovery was not absent.

PR #5639 was merged at `2026-09-06T18:05:28Z` with head
`50adbe0c68cfcc5d42024061c91f86dedb45159c` and merge
`c607359338026f4e440f62d052e19648d78d3f04`. Its merge receipt recorded
generation 1, whereas recovery advanced the assignment to generation 2.
The old consumption predicate reopened integration eligibility. A diagnostic
note saying the owner done command was "rejected" was then misclassified as
a review revocation and caused a nested unblock request.

Prior claims of missing `approval_binding`, absent shared recovery, and
missing deduplication were incorrect: the field is `review_binding`, boot
and poll already share recovery, and request-digest deduplication exists.
The abandoned proposal for an additional recovery state machine is not an
implementation basis. Its placement after worker-mount work would also wait
behind the PR whose closeout it needed to repair. The existing dependency
graph itself did not have a cycle.

| Authority | Existing owner | Repair boundary |
| --- | --- | --- |
| Task lifecycle/generation | V2 TaskStore and task machine | Never resurrect archived tasks or reset generations |
| Worker attempts | Supervisor shared terminal classifier and boot/poll recovery | Resolve the same archive-proven evidence before each decision/CAS |
| Merge occurrence | Canonical integration receipt | Immutable repository/base/PR/head identity; generation remains provenance |
| Approval | Typed review events and explicit merge holds | Generic diagnostic words are not review decisions |
| Repair admission | Supervisor unblock materializer | One source-delivery scope, not a task per symptom/retry |

## SD: implementation contracts

### Terminal evidence

`canonical_task_with_archive_proof` returns a detached evidence view only.
For a thin terminal row it checks the caller's current terminal fact and the
existing receipt verifier: exact archive root, snapshot digest, task identity,
generation, status and outcome. It reads only the affected archive, not every
archive during task-index construction. The normal-done classifier, active
lease decision, lost-lease recovery and fresh final-CAS read use this resolver.
The former separate archive-loading block in cancellation is removed.

Exact event digest, actor, PID/start-ticks/run/queue/process generation and
current task generation remain mandatory for normal-done completion. Missing
roles do not prove reassignment and cannot trigger a retry for a done task.
Unrelated legitimate cleanup can commit when an unverifiable worker is simply
preserved. Evidence changing between classification and commit still rejects
the whole batch. No lock, atomic batch, or termination guard is removed.
CAS-conflict telemetry distinguishes runtime digest/token drift from canonical
transition revalidation failure through `reason_code`.

### Merge receipt versus assignment epoch

A valid canonical receipt for the same frozen repository, base, PR and head
consumes an already-delivered integration candidate even after assignment
generation advances. Future-generation receipts are rejected. A present
delivery binding must agree with the review binding; changed delivery identity
cannot consume the old receipt. Original receipt bytes/generation are retained.
Receipt recording still uses exact-generation CAS and the existing integration
authority checks. Receipt consumption is not approval, owner closeout, task
completion, or permission to merge another head.

### Review and recovery

Typed `reopen`/`assign` still revoke approval. Explicit `do not merge` and
`changes required/requested` notes still block. Generic `rejected`, `rejects`
and `revert` words no longer act as implicit decisions. Use typed rejection or
an explicit hold to exercise review authority, not diagnostic prose.

The audit reader retains whether any non-resumable revocation occurred after
approval, even if a later environment blocker supersedes the last reason.
`resume_integration` reuses that reader, compares exact binding and acceptance
actor, and refuses missing/unreadable approval or a non-resumable decision
before any mutation. It cannot disagree with the integrator merely because
the task row still carries old bridge evidence. No audit note is removed and
no new approval is fabricated.

### Repair deduplication

Both producer and consumer classify review/authority-evidence failures as
source-task blockers, not new implementation tasks. The integrator still
returns the blocked reason; the gate is not bypassed. CI/rebase defects remain
eligible for independent repair work.

The existing digest-addressed request envelope and its strict root/runtime/
source-generation/PR/head/role validation remain. After admission, the locked
materializer coalesces requests for the same source/repository/PR/head across
different symptoms or generations. It preserves the original task and binds
each request receipt to that semantic identity. Multiple pre-existing matches
fail closed for explicit consolidation; terminal IDs cannot be resurrected.
The producer recognizes only a matching coalesced receipt identity.

## Verification and delivery gates

Focused regression coverage includes real exited PIDs, on-disk archive,
canonical journal, runtime lock/CAS, both polling and boot recovery, positive
and bad-proof batches, proof drift before commit, exact-event negatives,
receipt identity/generation negatives, resume rejection without mutation,
source-authority failure suppression and same-delivery repair coalescence.
Existing process recovery, cross-process locks, receipt, gate, integrator and
status-command suites must also pass. Isolated tests never use live authority.

Delivery uses a clean task worktree and exact validated commits on current
`dev`. If GitHub requires PR-triggered checks, a mechanical `delivery:tooling`
PR is transport only, not a reviewer-attestation request. Runtime activation
uses existing immutable-version promotion and pre/postflight; no live source
patching or hand-edited task/queue JSON is permitted.

## Existing work and limits

This repairs gaps in the already-delivered review-handoff and archive
contracts. Broader `OPS-REVIEW-PROOF-001` work must not reimplement these fixes;
remaining acceptance, mounts, dependency maintenance and product validation
remain separate scopes. No new canonical implementation packet is dispatched
for this operator-direct change. Source delivery, runtime activation and
product readiness must be reported separately with actual evidence.

Rollback uses the existing immutable runtime promotion to the last verified
source/Python pair. It must not roll back the canonical journal or resurrect
old worker leases. Before activation, capture current state again: the live
fleet can have advanced since the original diagnostic snapshot.
