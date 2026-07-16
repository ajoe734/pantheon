# Task Brief: LOOP-PROD-SEQ-RECONCILE-001

> Temporary coordination routing: until
> `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-001` is accepted, every owner or
> reviewer working this task must run governed state, review, handoff and
> closeout commands through `/home/lupin/code/pantheon/scripts/ai-status.sh`
> with its own real identity (`AI_NAME=Codex` for the fleet owner or
> `AI_NAME=Codex2` for the reviewer). Do not use the task-worktree wrapper for state. Git
> and tests stay in the task worktree. Verify with central `show`.

## Responsibility

Fleet owner Codex implements after the 2026-07-16 chair reassignment from
Antigravity. Reviewer Codex2 independently reviews.
The planner does not implement product code.

## Authoritative inputs

- Original 48-task catalog:
  `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/tasks.json`
  SHA-256 `44a893162da5779fc64292a70ba59fb7237eb4102ffb65f8e3ad3b64a8f31357`
- Sequencing addendum:
  `docs/04/pantheon_loop_product_level_remediation_2026-07-13/REMEDIATION_SEQUENCING_ADDENDUM_2026-07-16.md`
  SHA-256 `9a3b735ac161b612e35a1d0e313cc7037da444f8b0311c623d27396a06d4b519`
- Merged addendum authority: PR #3737 merge
  `a4b5df9a51bc3da6df0d39d422d9db4edc553aba`.

Both inputs are immutable.

## Required result

Produce a sequencing overlay and human-readable execution matrix covering
all 48 catalog IDs exactly once. For each task state whether it is:

1. permitted before the paper-trade proof,
2. part of the G2 proof path,
3. deferred strict-auth/security/governance work, or
4. final verification/closeout after the appropriate gate.

Each structured task entry must include that classification, a rationale,
the original dependency list, and the amended dependency list. A 48-entry
map containing only `wave` and `depends_on` does not satisfy this contract.

G2 opens only from machine evidence for one complete paper-trade chain:
signal -> order -> fill -> telemetry -> loop-run projection. Before that
evidence exists, the dispatcher must fail closed for tasks that flip dev to
strict auth, remove the browser dev bearer, or require MFA, two-person, or
negative-identity proof. After valid G2 evidence, those deferred tasks may
be released without deleting or rewriting them.

The G2 evidence contract must be versioned and declared in the overlay. It
must bind the exact task, catalog/addendum authority, and linked identities
or digests for signal, order, fill, telemetry and loop-run projection. Five
arbitrary non-empty strings plus a `done` task status are not proof. Reject
bare/fabricated strings, malformed or extra fields, wrong task/source hashes,
stale evidence, mismatched chain links, and a false-closed archive snapshot.

The overlay must embed both source hashes and the PR #3737 merge SHA. Validate the complete 48-ID set,
unknown/missing/duplicate IDs, dependency cycles, and wave ordering. Do not
auto-apply a partially validated overlay and do not materialize or close the
48 tasks as part of this reconciliation.

## Required tests and delivery

Tests must cover 48/48 classification, hash mismatch, missing/extra/duplicate
IDs, cycles, pre-G2 denial, invalid G2 evidence, and post-G2 release. Submit
the overlay, matrix, dispatcher changes and tests through a PR, then hand
off to Codex2 for independent review and merged-candidate verification.

## PR #3746 post-merge rejection (2026-07-16)

PR #3746 head `5f51574df2791d7cb1c4551e46571ae5f06ea71a`
merged as `aae333959e0566759a4e7eb955f860d280fa5e3d` after the owner
re-enabled auto-merge. It had no Codex2 independent review and does not
satisfy this task. Preserve it as failed/interim evidence and open a new
corrective PR from current `dev`; do not materialize the 48 tasks.

The corrective PR must fix all of these exact defects:

- move the current hard-coded `wave >= 5` release rule into the versioned
  overlay contract, including the exact gated classifications/task set and
  release predicate;
- resolve every signal, order, fill, telemetry and loop-run projection ID
  against authoritative canonical records, recompute/compare their digests,
  and bind status, tenant, environment, loop/run identity and event ordering;
  digest-shaped strings and linked-looking IDs alone are fabricated evidence;
- require accepted closeout-truth admission for the target task. An active
  `status: done` task without source/approval evidence and a minimal archived
  `done` snapshot without reviewer verdict/admitted evidence must both fail;
- enforce evidence freshness and ordering, not only parse `issued_at`;
- validate the exact PR #3737 merge SHA, allowed classification vocabulary,
  exact per-entry keys, non-empty rationale, explicit amended dependencies,
  and duplicate JSON task IDs;
- add negative tests for wrong merge SHA, extra and duplicate IDs, stale
  evidence, false-closed active/archive tasks, missing canonical records,
  digest mismatch, wrong status/tenant/environment/run, and mismatched event
  ordering;
- keep auto-merge disabled and hand the exact final head to Codex2 before
  merge.
