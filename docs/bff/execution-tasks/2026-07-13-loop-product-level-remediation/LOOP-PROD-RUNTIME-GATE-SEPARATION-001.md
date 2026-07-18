# LOOP-PROD-RUNTIME-GATE-SEPARATION-001

Status: corrective fleet task; must be admitted before the primary catalog is
materialized

Plan:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_RUNTIME_GATE_SEPARATION_2026-07-18.md`

## Objective

Separate the runtime-lock safety gate from the final product-completion
authority. The current dispatcher requires a protected Ed25519 completion
record before it will materialize any primary task. That is the wrong ordering:
the signing authority certifies the final program and must not prevent fleets
from doing the development work that the final program will later certify.

## Fleet-owned scope

- `scripts/dispatch_loop_product_level_remediation_2026-07-13.py`
- `scripts/test_dispatch_loop_product_level_remediation_2026_07_13.py`
- `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/tasks.json`
- `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/INDEX.md`
- the exact task/evidence contract files required by the changed schemas

The planner must not implement the dispatcher or any product artifact. The
fleet owns the code and tests in a clean task worktree, with a distinct fleet
reviewer and a normal PR to `dev`.

## Required behavior

### Pre-dispatch gate

The pre-dispatch gate must require only the merged runtime lock protocol:

- exact protocol version and lock order;
- stable lock inode and writer registry coverage;
- exact merged commit ancestry and source digests;
- strict malformed/foreign runtime-state rejection; and
- an authoritative zero-write dry-run of the runtime implementation.

It must **not** require any of the following:

- `completion.json`;
- an external `PANTHEON_RUNTIME_LOCK_VERIFIER_POLICY`;
- an Ed25519 completion signature;
- a revocation check or protected ledger entry; or
- a final Human/Ops program verdict.

Missing final signing authority must therefore not prevent the 48 primary tasks
from being materialized and dispatched.

### Final closeout gate

`LOOP-PROD-CLOSE-002` is the sole final authority. Its direct and transitive
dependencies must include all product tasks and external dependencies already
bound by the catalog. At closeout it must:

- install/read the protected external policy through the documented environment
  procedure;
- obtain the independent Human/Ops Ed25519 verdict;
- bind the verdict to the exact catalog, closeout manifest, FE/BFF/deployment
  identities, merged commits, and current revocation state;
- run the exact merged zero-write verification; and
- append exactly one final completion record.

The final gate remains fail-closed. Moving it later is not permission to weaken
or replace any of its checks.

## Required tests and evidence

The fleet PR must include tests that prove all of the following:

1. a clean runtime-lock implementation with no final policy or signature can
   pass pre-dispatch validation and materialize the primary catalog;
2. a missing, wrong-head, revoked, or malformed final policy still fails the
   final closeout path;
3. pre-dispatch dry-run hashes are unchanged and perform zero writes;
4. the final closeout consumes the protected verdict once and rejects replay,
   self-signing, wrong task/catalog/deployment bindings, and stale signatures;
5. the catalog keeps 48 primary tasks and its dependency graph remains acyclic;
   and
6. the exact merged PR head, checks, independent review, and evidence digests
   are recorded in the task archive.

## Non-goals

- no product feature implementation;
- no direct edits to live `ai-status.json`, the activity log, or archived task
  records;
- no fake `completion.json` or placeholder signature;
- no dispatcher apply or live-capital/broker side effect; and
- no weakening of the final product-level acceptance matrix.

## Handoff

After this task is merged and its exact head is independently accepted, rerun
the catalog validator and the guarded dry-run. The 48 primary tasks may then be
materialized. The protected verifier policy is requested only when the fleet
reaches `LOOP-PROD-CLOSE-002`.
