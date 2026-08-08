# Agora current-environment gap closure

Date: 2026-08-08

Frozen Pantheon baseline: `de8635aa958593ca332395baba1880aacf096952`

Audit cutoff: `2026-08-08T11:31:00Z`

Dispatch status: final packet pending validation and exact-head planning review.
No product or runtime implementation is authorized by the planning branch
itself.

## Objective

Restore current dev BFF availability without weakening safe defaults, close
the two missing durable edges from Agora to Policy Learning and from terminal
Imitation candidates to Consultation, bind those workers safely, execute the
already-planned real four-loop Learning verifier, reaccept one exact hosted
FE/BFF pair, and then synchronize Agora lifecycle documentation.

This is a coordination packet. The Pantheon supervisor is the only routine
implementation dispatcher. Chatbox planning does not implement product,
deployment, or control-plane changes.

## Frozen conclusion

Agora's principal feature and UI source work is substantially delivered, and
the historical `AG-HOSTED-CLOSE-002` result remains valid for FE
`e4399e3ec68f...` plus BFF `f71c1f8ba889...`. The current environment is not
accepted:

- public BFF `/readyz` and `/bff/version` returned 502;
- deployment run `31250996848` left `operator-bff` unserved after a candidate
  dependency failed;
- the old frontend manifest remained, but it did not identify a functioning
  BFF runtime;
- all 81 existing Agora dataset records are `observe`, all 81 evidence
  handoffs are pending, and zero are acknowledged;
- Policy Learning has zero candidates and no Agora handoff consumer;
- Consultation has no Policy-Learning candidate intake, zero memos/handoffs,
  and two historical dead-letter items; and
- the real Learning verifier has been planned but not materialized.

See [GAP-AUDIT.md](./GAP-AUDIT.md) for the full evidence inventory and
[execution-tasks.json](./execution-tasks.json) for the machine-readable task
contracts.

## Deduplication decisions

| Existing scope | Canonical owner | This addendum's boundary |
|---|---|---|
| Source Ingestion readiness/tick repair | `L12-MIN-SRC-20260808` | Deployment task owns incumbent preservation and rollback transaction, not Source Ingestion product code |
| Agora interaction to DatasetVersion/handoff | `L12-MIN-AGORA-20260808` | New consumer starts after this component task |
| Dataset to shadow candidate happy path | `L12-MIN-IMIT-20260808` | New task owns durable handoff consumption/ack ordering, not the base candidate implementation |
| Consultation request to terminal memo | `L12-MIN-CONS-20260808` | New task owns candidate-derived request intake only |
| Shared Compose/catalog integration | `L12-MIN-INTEGRATE-20260808` | New binding task waits for this shared-file owner |
| Twelve normal service-bound paths | `L12-MIN-E2E-20260808` | Real Learning verifier adds lineage, auth/tenant, duplicate, restart, ack, and DLQ/replay proof |
| Twelve hosted terminal readbacks | `L12-MIN-HOSTED-20260808` | Agora current hosted task adds exact-pair and full Learning-chain acceptance |
| Real Learning verifier successor | `L12-VERIFY-LEARN-REAL-VERIFIER-001` in prior planned catalog | Reuse this ID; do not create `AGORA-L12-REAL-VERIFIER-20260808` and do not dispatch the obsolete 28-task catalog |
| Broader minimum closeout | `L12-MIN-CLOSE-20260808` | Agora closeout does not close the broader twelve-loop milestone |

A preliminary packet was incorrectly queued before this audit was complete:
`pkt-agora-current-environment-gap-20260808-e765bcbed`. It is not accepted as
implementation authority. The one final handoff must explicitly supersede it,
read back canonical IDs/hashes once, and refuse to replay or overwrite any
conflicting materialization.

## Final execution DAG

```text
AGORA-CURRENT-GAP-EXECUTION-20260808
  |
  +--> AGORA-DEV-DEPLOY-RECOVERY-20260808 ----------------------+
  |                                                             |
  +--> existing L12-MIN-AGORA + L12-MIN-IMIT                    |
          |                                                     |
          v                                                     |
      AGORA-IMIT-HANDOFF-CONSUME-20260808                       |
          |                                                     |
          +--> existing L12-MIN-CONS                            |
          v                                                     |
      IMIT-CONSULTATION-INTAKE-20260808                         |
          |                                                     |
          +--> existing L12-MIN-INTEGRATE                       |
          v                                                     |
      AGORA-LEARNING-CROSS-LOOP-BIND-20260808                   |
          |                                                     |
          +--> existing L12-MIN-TEACH + L12-MIN-E2E             |
          v                                                     |
      L12-VERIFY-LEARN-REAL-VERIFIER-001                        |
          |                                                     |
          +--> existing L12-MIN-HOSTED                          |
          |                                                     |
          +-----------------------------------------------------+
          v
      AGORA-CURRENT-HOSTED-REACCEPT-20260808
          |
          v
      AGORA-UI-LIFECYCLE-RECONCILE-20260808
          |
          v
      AGORA-CURRENT-CLOSE-20260808
```

The P0 deployment lane may run after admission without waiting for the product
DAG. Exact hosted acceptance waits for both deployment recovery and the real
Learning verifier.

## Governed task packets

### `AGORA-CURRENT-GAP-EXECUTION-20260808`

Admission/reconciliation only. It performs one authoritative task/PR/branch/
worktree readback, supersedes the preliminary packet, compares every canonical
ID and immutable task hash, validates the final DAG, and releases only absent
or exact-matching task specifications. A hash conflict blocks; it is never
overwritten or replayed.

### `AGORA-DEV-DEPLOY-RECOVERY-20260808`

Owns the deployment transaction and governed dev recovery. It must restore a
public BFF 200, keep safe defaults, validate dependencies before incumbent
replacement, automatically restore the last accepted BFF/manifest when a
candidate or lease fails, and add deterministic failure-matrix coverage. Any
Source Ingestion product defect returns to `L12-MIN-SRC-20260808`.

### `AGORA-IMIT-HANDOFF-CONSUME-20260808`

Owns the missing durable Agora-to-Policy-Learning edge. It consumes only
eligible tenant-scoped `learn` handoffs, persists an idempotent downstream
receipt/job/candidate, and acknowledges through the Agora-owned authenticated
contract only after persistence succeeds. Policy Learning may not write Agora
tables directly, and the 81 historical `observe` rows may not be bulk-acked.

### `IMIT-CONSULTATION-INTAKE-20260808`

Owns the terminal-candidate-to-Consultation intake edge. It creates exactly
one tenant-scoped governed request with dataset/handoff/candidate/trace lineage
and lets the existing Consultation executor produce the memo/handoff. It must
prove state filtering, retry/DLQ, replay idempotency, tenant isolation, and no
runtime or capital effect.

### `AGORA-LEARNING-CROSS-LOOP-BIND-20260808`

Owns only Compose/runtime endpoints, scoped credentials, activation, health,
and fail-closed configuration. It waits for `L12-MIN-INTEGRATE-20260808` so
workers never edit shared Compose/catalog files in parallel. Both new consumers
remain disabled until this reviewed task activates them in paper/shadow dev.

### `L12-VERIFY-LEARN-REAL-VERIFIER-001`

Uses the existing successor ID and verifier-only file scope. It must call real
Teaching, Agora, Policy Learning, and Consultation boundaries; correlate one
non-fixture learn chain; prove ack order, duplicate/two-worker behavior,
tenant/RBAC/auth negatives, restart persistence, DLQ/replay, safe writes, and
zero runtime mutation; and fail nonzero on missing or self-attested evidence.

### `AGORA-CURRENT-HOSTED-REACCEPT-20260808`

Requires public BFF readiness/version 200 and exact equality between served FE,
served BFF, pair ID, manifest, compatibility identity, reviewed heads, and
merged commits. It runs the real Learning verifier against hosted dev, repeats
governed restart/readback, and rejects stale, local, historical-only,
manifest-only, fallback, unsafe, or mismatched proof.

### `AGORA-UI-LIFECYCLE-RECONCILE-20260808`

Documentation only. It builds the exact 001-011 matrix and distinguishes
source delivery, task-scoped hosted proof, and later hosted ancestry. In
particular, 001 and 010 must not be called unimplemented merely because no
same-named standalone hosted evidence file was found.

### `AGORA-CURRENT-CLOSE-20260808`

The unique final sink. It links every gap to canonical status, PR, merge SHA,
independent exact-head review, required checks, deployed identities, durable
lineage, runtime readback, and rollback evidence. It closes only the Agora
current-environment milestone.

## Acceptance invariants

Every source-changing task must:

1. use its declared clean worktree and task branch;
2. target Pantheon `dev` (or execute-plans `dev` if a later explicitly scoped
   frontend task is ever needed);
3. stay inside declared artifacts and defer overlap to its predecessor;
4. run focused and relevant regression validation;
5. commit and push only task-owned files;
6. open a PR, pass visible required checks, and obtain independent exact-head
   review;
7. merge through repository policy and archive reviewer-consumable evidence;
8. retain strict authentication, tenant isolation, read-only/safe-write
   defaults, and no live-capital authority; and
9. preserve durable audit rows on rollback.

No task may directly edit canonical task/event JSON, queue files,
`.orchestrator/config.json`, or
`/home/lupin/pantheon-ci-deploy/dev-root`. A smallest temporary dev BFF rescue
is allowed only inside the deployment recovery task and must be followed by the
exact permanent source delivery flow in the same task.

## Merge, rollout, and rollback order

1. Admit the final addendum after one conflict/hash readback.
2. Run the P0 deployment recovery lane.
3. Complete the existing Agora/Imitation minimum components.
4. Merge the handoff consumer.
5. Complete the existing Consultation minimum component and merge candidate
   intake.
6. Complete the existing shared integration task and merge runtime binding.
7. Complete Teaching/E2E predecessors and merge the real Learning verifier.
8. Complete the generic hosted gate, then perform one Agora exact-pair hosted
   admission.
9. Reconcile UI/lifecycle truth.
10. Merge the Agora current-environment closeout.

If any candidate, service, identity, tenant, auth, restart, DLQ/replay, or safe
posture proof fails, the candidate remains unaccepted. Hosted rollback restores
the previous accepted pair; product rollback disables the new consumers while
preserving their durable rows for diagnosis and later replay.
