# Task Brief: LOOP-PROD-SEQ-RECONCILE-001

## Responsibility

Owner Antigravity implements. Reviewer Codex2 independently reviews.
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
