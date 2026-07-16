# Task Brief: LOOP-PROD-ARCHIVE-CLOSURE-REPAIR-DISPATCH-001

## Responsibility

Owner Claude produces the repair execution catalog and governed board
materialization. Reviewer Antigravity independently verifies exact coverage,
immutability, sequencing and delivery. The planner does not implement product
code.

## Start gate

Do not start until both tasks below are accepted and merged:

- `LOOP-PROD-DONE-GUARDRAIL-REPAIR-001`
- `LOOP-PROD-SEQ-RECONCILE-001`

Use their exact merged outputs, not an earlier draft or PR description.

## Required result

Read the immutable 18-snapshot closeout truth audit. Preserve the two valid
closures. For each of the 12 `false_closure` and four `stale_evidence`
results, generate one new, unique repair task ID; never reopen, replace or
edit an archived task in place.

The accepted catalog must contain exactly these 16 source-to-repair mappings;
the fleet may not rename, omit, merge or split them:

| Audit classification | Archived source | New repair task ID |
| --- | --- | --- |
| `false_closure` | `LOOP-PROD-AGORA-001` | `LOOP-PROD-AGORA-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-AGORA-002` | `LOOP-PROD-AGORA-002-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-ALPHA-001` | `LOOP-PROD-ALPHA-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-AUTH-001` | `LOOP-PROD-AUTH-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-CAP-001` | `LOOP-PROD-CAP-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-CONS-001` | `LOOP-PROD-CONS-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-DIST-001` | `LOOP-PROD-DIST-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-IMIT-001` | `LOOP-PROD-IMIT-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-MAI-001` | `LOOP-PROD-MAI-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-OODA-001` | `LOOP-PROD-OODA-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-SRC-001` | `LOOP-PROD-SRC-001-FALSE-CLOSEOUT-REPAIR` |
| `false_closure` | `LOOP-PROD-TEL-001` | `LOOP-PROD-TEL-001-FALSE-CLOSEOUT-REPAIR` |
| `stale_evidence` | `LOOP-PROD-DEP-001` | `LOOP-PROD-DEP-001-STALE-EVIDENCE-REPAIR` |
| `stale_evidence` | `LOOP-PROD-GAP-ADDENDUM-001` | `LOOP-PROD-GAP-ADDENDUM-001-STALE-EVIDENCE-REPAIR` |
| `stale_evidence` | `LOOP-PROD-GAP-ADDENDUM-002` | `LOOP-PROD-GAP-ADDENDUM-002-STALE-EVIDENCE-REPAIR` |
| `stale_evidence` | `LOOP-PROD-RUNTIME-BOOT-001` | `LOOP-PROD-RUNTIME-BOOT-001-STALE-EVIDENCE-REPAIR` |

`LOOP-PROD-REC-001` and `LOOP-PROD-TEACH-001` are the only preserved valid
closures. They must appear in the mapping matrix as preserved inputs, but no
new task may be created for either one.

Create a versioned repair catalog, one detailed task specification per repair,
and a human-readable mapping matrix. Every repair task must bind:

- the archived source task ID, snapshot SHA-256 and audit classification;
- every exact missing or contradictory proof reported by the accepted audit;
- its relationship to the original 48-task catalog;
- its pre-G2, G2-path, deferred-hardening or final-verification placement from
  the accepted sequencing overlay;
- distinct admitted fleet owner and reviewer;
- explicit product artifacts, dependencies, non-goals and acceptance checks;
- PR, tests, hosted/runtime proof and post-merge evidence required to close.

Do not reduce a false closure to a documentation-only task when its missing
proof requires actual product behavior. Do not duplicate the two valid
closures. Do not silently discard any of the 16 audit results.

## Validation and delivery

The catalog validator must fail closed on a missing, extra or duplicate repair
ID; wrong source task or snapshot hash; unresolved dependency; dependency
cycle; owner/reviewer collision; missing acceptance/proof; or a repair placed
on the wrong side of the G2 gate.

Commit the repair catalog, all 16 specifications, mapping matrix, validator
tests and immutable-source digest evidence through a PR to `dev`. After merge
and independent approval, use governed `ai-status` commands to materialize the
16 tasks and record before/after board evidence. Do not hand-edit canonical
status, implement the repair work in this catalog task, deploy, or mark any
repair task done.
