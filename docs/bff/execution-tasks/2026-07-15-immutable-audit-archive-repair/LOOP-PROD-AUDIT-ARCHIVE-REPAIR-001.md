# LOOP-PROD-AUDIT-ARCHIVE-REPAIR-001 — Immutable audit archive parser repair

Status: external/bootstrap prerequisite; normal state/outbox admission remains blocked.

Canonical contract:
[`fixtures/archive-audit-repair-bootstrap-task.v1.json`](fixtures/archive-audit-repair-bootstrap-task.v1.json)

This task repairs the *parser-visible projection*, never the historical object.
It exists solely because PR #3652 could not persist the owner-to-reviewer
handoff for `LOOP-PROD-RUNTIME-BOOT-001` while an immutable rotated archive was
malformed.

## Pinned incident

| Item | Required exact value |
| --- | --- |
| Source | `archive/logs/ai-activity-log.jsonl-2026-07-07T1503Z.gz` |
| Source SHA-256 | `47d562e67b6f7f91fe5ea03ad08b36d473470d29e808a3981d44283e37623e24` |
| Defective row | complete newline-terminated line `8004`, 2606 bytes |
| Defective row SHA-256 | `b16fd8057507ca8e76b3e40f07535e067f9fe5991dbe311b5e2e8ca43955fc07` |
| Observed parser diagnostic | line 1, column 910 |
| Active log status | every current active row parsed successfully at observation |

Any mismatch is a new incident, not authority to generalize this task.

## Authority boundary

The normal `ai_status` transaction and normal supervisor/outbox recovery are
intentionally unusable until the archive is parseable.  They must not be
worked around by direct state edits. The sole temporary admission route is the
two-person external-bootstrap envelope in [RUNBOOK.md](RUNBOOK.md); it starts
one isolated fleet worker and records no production task/audit mutation.

The planner may author this packet and validate its contract. Only the admitted
fleet worker may implement the declared artifacts. A distinct runtime reviewer
and Human/Ops must approve the exact repair PR head.

## Required final sequence

1. Preserve and content-address the original gzip object.
2. Quarantine it without replacement; derive a separately addressable repaired
   projection and reviewer attestation.
3. Strictly parse all archives and the active log, then recover the pending
   outbox deterministically and exactly once.
4. Replay the blocked bootstrap handoff as a *new*, linked replay receipt.
5. Re-run the `LOOP-PROD-RUNTIME-BOOT-001` review/merge ceremony; do not treat
   this repair as bootstrap completion or primary-task authorization.

The complete acceptance, proof, dispatch, and non-goal rules are in the
machine contract. No manual success note, parser-only test, or local archive
copy can close the task.
