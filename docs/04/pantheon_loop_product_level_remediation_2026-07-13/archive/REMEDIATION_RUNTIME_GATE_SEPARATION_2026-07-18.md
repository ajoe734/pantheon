# Runtime Gate Separation Re-plan — 2026-07-18

Status: planning correction; fleet implementation required

This addendum corrects a sequencing error in the 2026-07-13 execution packet.
The packet currently treats the protected completion authority for the runtime
bootstrap (external policy, Ed25519 completion evidence, revocation and ledger
binding) as a prerequisite for materializing the 48 primary tasks. That couples
development dispatch to the final program closeout authority and deadlocks the
program when the environment administrator has not yet installed the final
signing authority.

## The correction

The runtime lock implementation and the protected program completion ceremony
are separate concerns and must be separate gates:

| Concern | When it is required | Owner | What it proves |
| --- | --- | --- | --- |
| Runtime lock protocol | Before canonical task materialization | Fleet | Writers use the stable runtime/task/audit lock order and the merged implementation is exact and safe to run. |
| Product completion authority | Only in the final closeout task | Final closeout fleet + independent Human/Ops authority | All product tasks and evidence are complete, and the final result was independently accepted. |

The runtime lock task may therefore be accepted and used for dispatch without a
`completion.json`, external verifier policy, Ed25519 signature, revocation
record, or protected ledger entry. Those records must remain absent from the
pre-dispatch contract and must not be fabricated as a workaround.

The final `LOOP-PROD-CLOSE-002` task already owns the program completion
authority. It must consume the protected policy and signed verdict exactly once,
after the other 47 primary tasks, external dependencies, and product-level
evidence are complete. The final task then performs the exact-merge,
zero-write readback and appends the sole program completion record.

## Required fleet change

`LOOP-PROD-RUNTIME-GATE-SEPARATION-001` is the corrective implementation task.
It must be executed by a fleet worker in a clean task worktree and reviewed by a
different fleet identity. The worker must:

1. split the dispatcher into a **pre-dispatch runtime protocol gate** and a
   **final protected completion gate**;
2. make the pre-dispatch gate verify the merged runtime implementation,
   writer registry, lock order, source digests, and zero-write safety only;
3. remove the requirement for `completion.json`, external verifier policy,
   Ed25519 signature, revocation and ledger fields from primary task
   materialization;
4. preserve the catalog-bound completion authority and make
   `LOOP-PROD-CLOSE-002` the only task allowed to install/consume it;
5. add regression tests proving that a missing final signing authority does not
   block primary task materialization, while final closeout still fails closed
   without it; and
6. rebind the catalog, task contract, dispatcher and evidence digests together
   in one reviewed PR.

## Ordering after the correction

```text
runtime-lock implementation
        |
        v
materialize and dispatch the 48 primary tasks
        |
        v
fleets implement, merge, deploy, and prove product-level tasks
        |
        v
LOOP-PROD-CLOSE-001 (checkpoint only)
        |
        v
LOOP-PROD-SIGNOFF-001 (guard implementation)
        |
        v
LOOP-PROD-CLOSE-002 (install protected policy, independent signature,
                     exact final verification, and program completion)
```

## Acceptance boundary

This correction does not weaken product acceptance. It only moves the final
cryptographic ceremony to the point where it has something complete to certify.
No task may claim product-level completion from a local signature, a fixture,
an unsigned JSON file, or a planner-authored verdict.

The old pre-dispatch completion-evidence requirement is superseded by this
addendum. The historical documents and evidence snapshots remain immutable
history; the fleet correction must update the live contract and its tests
through the normal branch/PR/review/merge workflow.
