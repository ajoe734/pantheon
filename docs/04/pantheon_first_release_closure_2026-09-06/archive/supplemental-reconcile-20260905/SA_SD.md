# OPS-MERGED-ARCHIVE-RECONCILIATION-PREREQUISITE-001

## SA: verified problem, 2026-09-05 UTC

The current supervisor is healthy at immutable runtime
`20282eba2ce2304560ab7eab0cd27af824a22b8b`. This prerequisite repairs a
TaskStore lifecycle recovery boundary, not product functionality or cron.

`PPL-ALLOC-007` is blocked in active TaskStore with owner Codex / reviewer
Claude. Its unchanged scope already shipped through execute-plans PR 285:
head `1e5b1881d42a74a6234ce8ee3a83684c7d5076de`, merge
`c62c0e8b9a49643c42f67614c542578afb233e84`, base dev, integration-gate SUCCESS.
The merge is an ancestor of current execute-plans origin/dev.

Pantheon's committed `.orchestrator/task-briefs/ppl_alloc_007.md` at
`2f61fcea733ac830b21adbc516aae76eff8c922b` records the independent Claude
review_approved decision and exact delivery repository/merge. This evidence
is a dev ancestor and the current runtime file matches the committed blob.

The canonical archive already contains PPL-ALLOC-007 completed on
2026-07-20, same delivery commit, title, artifacts, acceptance, dependencies
and normalized generation 1. It records a historically audited reviewer
reassignment from Claude to Codex2. The currently resurrected row is older.
Both original archive and reviewer-assignment history must remain immutable.

On 2026-09-05 at approximately 01:37 UTC, the official local Human/Ops
`reconcile_merged_done` passed merged evidence validation, then rejected with
`existing archive snapshot conflicts with terminal task: PPL-ALLOC-007`.
The function rebuilds a new archive snapshot with new timestamps and current
role metadata instead of preserving the verified already-completed snapshot.
No canonical change was committed by this failed transaction.

Existing primitives do not fit this exact recovery:

- `record_terminal_fact` rejects active task rows.
- `archive_reconcile` imports rich records only for existing terminal facts.
- `retire_archive_collision` requires a distinct completed replacement task;
  this incident is stale resurrection of the same original work, not later
  replacement delivery. Do not manufacture a replacement or use an unrelated
  task to satisfy that guard.

## SD: extend the existing recovery contract

Keep `reconcile_merged_done` as the single ingress. Reuse existing exact
merged-delivery and independent-review validation. Add a narrow recovery
branch for an existing immutable completed archive proven to describe the
same original scope and delivery as the stale active task. Preserve its
bytes, archived_at, reviewer history and original delivery metadata.

Revalidate task identity, normalized generation, scope/acceptance/dependencies,
delivery repository and exact commit; reject changed scope or uncertain
identity. Do not silently treat a later reused task id as already complete.
Read back the immutable snapshot and record its terminal fact plus normal
receipt/outbox lineage through the existing transaction and lock ordering.
Record fresh recovery evidence in the canonical audit, not by rewriting the
archive. Preserve the ordinary conflicting-archive fail-closed behavior for
all non-matching cases. Owner/reviewer drift must remain evidence-backed.

Reuse existing task archive, lifecycle, receipt and audit helpers. Extract a
small focused helper only where needed; do not add another TaskStore,
generic task-state override, bypass flag, new cron, or parallel closeout CLI.
No automatic broad archive import and no second path-matching implementation.

## Execution and acceptance

Owner Codex, independent reviewer Claude, repository pantheon. Development
tooling functional source changes only; no hosted/production/live writes.

1. Reproduce with fixtures shaped like this stale active row plus immutable
   completed archive; document the pre-fix failure.
2. Implement the narrow branch in the existing reconcile ingress and its
   shared helpers, preserving normal validation and atomicity.
3. Tests cover success, exact archive-byte preservation, terminal fact and
   receipt readback, idempotent retry, no active dispatch after recovery,
   generation/scope/repository/commit mismatch, missing/unmerged review
   evidence, forged reviewer drift, archive mutation during readback and
   failed transaction preservation. Existing archive-collision and lifecycle
   tests remain green.
4. Commit scoped implementation, focused tests, operational documentation
   and genuine task-scoped JSON evidence. Rebase current origin/dev and use
   canonical independent exact-head review plus normal integrator delivery.
5. State clearly that source delivery alone does not repair the live row.
   After exact-version promotion, Human/Ops will run the existing governed
   reconciliation against PPL-ALLOC-007 and verify archive bytes unchanged,
   terminal fact, dependency readiness and absence from the active queue.

Do not touch product/frontend source, modify canonical task JSON, rewrite
historic archives, impersonate reviewers, forge evidence, deploy hosts or
claim current product readiness from historical delivery.

`TJ-E2E-012` also has an older archive but the active row is generation 2;
it is NOT accepted by this proof and must stay rejected unless separately
re-audited. Its historical hosted environments are not current readiness.

Rollback: revert only this scoped source change through normal delivery and
promote the prior immutable runtime. Never roll back or overwrite historical
task/archive evidence to force queue progress.
