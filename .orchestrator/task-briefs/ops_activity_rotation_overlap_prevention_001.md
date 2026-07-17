# OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001

## Objective

Prevent the first and later content-addressed activity rotations from
reintroducing duplicate history or ambiguous source order, while preserving
strict rejection of content-addressed overlap.

Owner: `Codex2`. Reviewer: `Claude`. Target: `pantheon/dev`. Auto-merge: off.

## Required context

- Read the activity follow-up plan dated 2026-07-16, the original recovery
  brief/addendum, PR #3773, and its postmerge inventory.
- Current approved baseline is merge
  `64844eef7e87c63c955c98fa95579992aa3af5e2`.
- The postmerge snapshot has no content-addressed archive. If that changes
  before implementation or install, stop and request a fresh incident plan.

## Implementation scope

- `.orchestrator/common.py`
- `scripts/ai_status.py` only where writer integration requires it
- directly corresponding tests
- redacted task evidence

Do not modify product trading behavior, BFF/frontend, provider routing,
legacy archive bytes, central status files, or unrelated runtime policy.

## Required behavior

1. Add durable ordered lineage for content-addressed rotation transactions.
2. Make source enumeration consume that lineage after legacy sources and
   before active; filename/hash lexical order is prohibited.
3. Permit one boundary normalization only when the first active prefix is the
   exact 1,000-line byte-identical suffix of the immediately preceding legacy
   timestamp archive and there is no existing content lineage/archive.
4. Exclude that verified duplicate prefix from the first content archive;
   preserve every non-overlap byte in archive or active tail.
5. Bind the exception to predecessor/source hashes, line/byte counts, prefix
   digest, full pre-rotation source digest, and transaction identity.
6. Extend restart recovery so intent, archive, tail, and lineage publish are
   idempotent at every fault point. Intent is removed last. A shared-lock
   reader must fail closed on a pending intent; only an exclusive-lock writer
   may recover it.
7. Add an active lineage-head control record for every completed rotation,
   including `keep_lines=0`. Bind latest sequence/transaction/archive/lineage
   digests plus retained-tail digest, byte count, and line count. Verify that
   record and the following retained-tail bytes before source enumeration.
8. Reject unregistered/missing/tampered archives, sequence gaps/forks,
   duplicate sequence, newest-row plus archive rollback, missing/stale or
   mismatched active lineage-head control records, second boundary exception,
   symlinks, and unstable sources.
9. Keep content-addressed overlap rejection unchanged.
10. Ensure ai-status and supervisor writer paths share the same contract for
   their different thresholds/tail sizes.

## Tests and evidence

Implement every P0 verification row in the plan, including a three-archive
hash-order counterexample, newest-row rollback for keep-lines 1000 and 0, and
the complete crash/tamper matrices. Run the full activity/status/control-plane
suites from repo-external roots. Publish
redacted fixtures, commands, counts, hashes, and fault-point results; never
copy central raw activity payloads.

## Delivery gates

- Compose the latest `origin/dev` before final review.
- Commit only scoped source/tests/evidence with trailers:
  `LLM-Agent: Codex2`, this task ID, and `Reviewer: Claude`.
- Claude independently reviews and reruns the exact final head.
- Keep auto-merge off. Owner must not approve, merge, install, or force a
  central rotation.
- Before merge, owner must produce the time-bounded all-writer transition
  guard runbook required by the plan. Merge is prohibited until the planner
  accepts its writer coverage, monitoring, abort, restoration, and readback
  evidence.
- Postmerge install and smoke require planner approval and exact merge SHA.

## Coordination Root

- Auto workers inherit `PANTHEON_STATUS_ROOT` from the supervisor.
- Run `./scripts/ai-status.sh` normally from this worktree; governed status,
  activity, archive and lock writes are routed to the validated central root.

## Done

Done requires merged code, exact dev-root install, synthetic boundary and
multi-rotation postmerge proof, central read-only logical validation,
governed show/note smoke, and zero remaining P0 finding.
