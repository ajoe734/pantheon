# Agora current-environment gap closure

Date: 2026-08-08

Frozen repository baseline: `0f2ab06f8de5be84ad18862d5f55a1fd65266324`

Audit cutoff: `2026-08-08T10:28:00Z`

## Objective

Restore the current Pantheon dev BFF without weakening safe defaults, complete
the real Agora -> Learning / Imitation -> Consultation handoff chain, prove it
with a service-bound verifier, reaccept one exact hosted FE/BFF pair, and then
reconcile the Agora lifecycle documents.

This packet is an addendum to the already-materialized twelve-loop minimum
functional program. It does not redispatch work already owned by
`L12-MIN-AGORA-20260808`, `L12-MIN-IMIT-20260808`,
`L12-MIN-CONS-20260808`, `L12-MIN-BFF-20260808`,
`L12-MIN-E2E-20260808`, or `L12-MIN-HOSTED-20260808`.

## Current verdict

Agora's principal product and UI source delivery is substantially complete,
and historical exact-pair hosted acceptance is valid for its frozen 2026-07-24
pair. The current environment is not accepted:

- the public BFF `/readyz` and `/bff/version` returned HTTP 502;
- `pantheon-operator-bff-1` was left in `Created` state after deployment run
  `31250996848` failed;
- `pantheon-source-ingest-1` was running but unhealthy;
- the served frontend manifest still declares BFF `be956c07aca8...`, while the
  unstarted candidate image declares revision `a55721bce2a7...`;
- the manifest still declares read-only, strict fallback, real writes false,
  stub writes false, and no embedded bearer token, but the unavailable BFF
  prevents runtime revalidation;
- the current database contains 81 Agora dataset records and 81 evidence
  handoffs, all 81 handoffs are pending, zero are acknowledged, and Policy
  Learning contains zero candidates.

The full frozen evidence and gap-to-task traceability are in
`GAP-AUDIT.md`. The machine-readable task DAG is in `execution-tasks.json`.

## Deduplication result

| Observed gap | Existing canonical owner | Addendum delta |
|---|---|---|
| Source Ingestion service cannot finish readiness/tick | `L12-MIN-SRC-20260808` | No duplicate service repair. The addendum deployment task owns only incumbent preservation, governed recovery, and deploy transaction behavior. |
| Agora interaction -> evidence/dataset handoff | `L12-MIN-AGORA-20260808` | No duplicate component task. |
| Agora dataset -> terminal shadow candidate | `L12-MIN-IMIT-20260808` | No duplicate component task. |
| Consultation request -> terminal memo | `L12-MIN-CONS-20260808` | No duplicate component task. |
| Dependency health event and BFF readback | `L12-MIN-BFF-20260808` | No duplicate health-monitor task. Deployment recovery separately restores serving availability. |
| Twelve normal happy paths | `L12-MIN-E2E-20260808` | Add one Agora-specific service-bound verifier for correlation, tenant isolation, duplicate/restart, acknowledgement, and DLQ/replay, which the minimum task explicitly excludes. |
| Twelve hosted terminal readbacks | `L12-MIN-HOSTED-20260808` | Add current Agora exact-pair reacceptance after the generic hosted gate. |
| Minimum twelve-loop closeout | `L12-MIN-CLOSE-20260808` | Add a separate Agora current-environment closeout; it must not close the broader L12 milestone. |
| UI polish lifecycle/index drift | None active | Add documentation-only reconciliation after current hosted proof. |

The canonical checkpoint already contains the minimum successor task cards.
Packet A's bridge receipt reports `invalid_materialization` because its
immediate installed-runtime readback failed, while a later authoritative
checkpoint contains the task rows. The reconciliation task must confirm the
durable hashes and must not replay or duplicate those IDs. Packet B was
admitted with verified authoritative readback for `L12-MIN-HOSTED-20260808`
and `L12-MIN-CLOSE-20260808`.

## Execution DAG

```text
AGORA-CURRENT-GAP-EXECUTION-20260808
        |
        +--> AGORA-DEV-DEPLOY-RECOVERY-20260808
        |
        +--> existing L12-MIN-AGORA / IMIT / CONS
                    |
                    v
            AGORA-L12-CROSS-LOOP-INTEGRATE-20260808
                    |
          existing L12-MIN-E2E-20260808
                    |
                    v
            AGORA-L12-REAL-VERIFIER-20260808
                    |
          existing L12-MIN-HOSTED-20260808
                    |   + deployment recovery
                    v
            AGORA-CURRENT-HOSTED-REACCEPT-20260808
                    |
            AGORA-UI-LIFECYCLE-RECONCILE-20260808
                    |
            AGORA-CURRENT-CLOSE-20260808
```

The deployment recovery lane may proceed as soon as the reconciliation gate
passes. It does not wait for the product DAG because public BFF availability is
a P0 dev-environment issue. The current hosted reacceptance remains ordered
after both the deployment recovery and the real cross-loop verifier.

## Task packets

### `AGORA-CURRENT-GAP-EXECUTION-20260808`

- Owner capability: task-state and cross-worktree reconciliation.
- Independent reviewer capability: cross-service plan and scope review.
- Scope: read-only current-state inspection plus task-scoped evidence.
- Acceptance: verify the seven addendum tasks remain non-duplicates; verify
  every external `L12-MIN-*` dependency and task-spec hash in authoritative
  state; resolve Packet A's receipt/readback ambiguity without replaying it;
  record current PR, branch, worktree, and artifact collisions; release the
  addendum DAG only when dependency and ownership truth is exact.
- Out of scope: product changes, direct state JSON edits, queue-file edits,
  provider policy, supervisor scheduling changes, and live deployment.

### `AGORA-DEV-DEPLOY-RECOVERY-20260808`

- Owner capability: nonproduction deployment transaction and rollback repair.
- Independent reviewer capability: failure-injection and hosted identity
  review.
- Scope: `.github/workflows/nonprod-deploy.yml`,
  `scripts/deploy_nonprod_vm.sh`, focused deploy contract tests, and task
  evidence. It must not change Source Ingestion product code owned by
  `L12-MIN-SRC-20260808`.
- Acceptance: perform only the smallest governed dev recovery necessary to
  restore a BFF readiness 200; preserve or restore the last accepted incumbent
  when any candidate dependency is unhealthy; never publish a new manifest
  before candidate admission; make dependency failure leave no unserved
  `Created` BFF; add failure-injection regression coverage; merge the source
  fix and perform one bounded dev rollout with exact identity evidence.
- Rollback: under the environment lease, select the last accepted exact pair;
  revert the source merge if the transaction change regresses delivery.

### `AGORA-L12-CROSS-LOOP-INTEGRATE-20260808`

- Owner capability: Agora, Policy Learning, and Consultation integration.
- Independent reviewer capability: tenant-safe durable workflow review.
- Scope is dependency-ordered after the three existing component tasks so
  their overlapping service paths are never edited in parallel.
- Acceptance: one tenant-scoped Agora interaction produces persisted evidence
  and a DatasetVersion handoff; the handoff is acknowledged only after durable
  downstream ingestion; Policy Learning produces a terminal shadow candidate
  linked to that dataset; the candidate produces a governed Consultation
  request and terminal memo/handoff; all identifiers are correlated; no seed
  fallback and no live-capital authority are introduced.
- Rollback: revert the integration merge; retain or quarantine durable audit
  rows instead of deleting them.

### `AGORA-L12-REAL-VERIFIER-20260808`

- Owner capability: independent cross-service verification.
- Independent reviewer capability: verifier integrity and negative-control
  review.
- Scope: a new dedicated verifier, focused tests, and evidence only. Product
  repair is returned to the owning task.
- Acceptance: use actual service calls and durable database/API readbacks;
  fail when a required service is absent; record exact interaction, evidence,
  dataset, handoff, acknowledgement, candidate, consultation request, and memo
  identifiers; cover duplicate/idempotency, tenant isolation, restart
  readback, failure-to-DLQ, replay, and safe write posture; reconcile backlog
  counts without purging historical rows; reject fixture-only or literal-pass
  evidence.

### `AGORA-CURRENT-HOSTED-REACCEPT-20260808`

- Owner capability: hosted exact-pair validation.
- Independent reviewer capability: deployment and security-posture evidence
  review.
- Acceptance: require public BFF readiness and version 200; bind runtime BFF
  revision, FE revision, pair ID, deployment manifest, Agora v1.13 manifest,
  and reviewed source; rerun the real verifier against hosted dev; repeat BFF
  restart/readback; prove live/strict/read-only settings and negative writes;
  refuse acceptance on drift, fallback, unreviewed source, or stale evidence.
- Rollback: keep or reselect the previous accepted exact pair; never switch the
  manifest on a failed candidate.

### `AGORA-UI-LIFECYCLE-RECONCILE-20260808`

- Owner capability: evidence and lifecycle truth reconciliation.
- Independent reviewer capability: historical PR/ancestry verification.
- Acceptance: update the 001-011 matrix from exact merged PRs and hosted
  descendants; distinguish task-scoped hosted proof from descendant ancestry;
  state honestly that UI polish 010 was merged but lacks equivalent standalone
  hosted closeout; update remaining-work/current-environment language only
  after the current hosted task passes.
- Out of scope: frontend product changes and manufactured evidence.

### `AGORA-CURRENT-CLOSE-20260808`

- Owner capability: release evidence closeout.
- Independent reviewer capability: exact-head and hosted acceptance review.
- Acceptance: publish one final table linking each gap to implementation PR,
  merge SHA, independent review, check run, deployed identities, runtime
  readback, rollback proof, and canonical task status; close only when every
  predecessor is done and the public environment still serves the reviewed
  pair.

## Shared delivery rules

Every source-changing task must use its declared clean task worktree and
branch, target `dev`, run focused and relevant regression validation, commit
only declared artifacts, push, open a PR, wait required checks, obtain an
independent exact-head review, merge, and archive evidence. The Pantheon
supervisor is the sole routine dispatcher.

No task may edit `/home/lupin/pantheon-ci-deploy/dev-root`, canonical runtime
state, queue JSON, or `.orchestrator/config.json` directly. A temporary live
repair is permitted only for the smallest dev BFF recovery and must be followed
by the exact source/config delivery flow in the same task.

## Merge and rollout order

1. Merge and close the reconciliation gate.
2. Restore BFF and merge the deployment transaction repair.
3. Complete the existing `L12-MIN-AGORA`, `L12-MIN-IMIT`, and
   `L12-MIN-CONS` component tasks.
4. Merge the Agora cross-loop integration task.
5. Complete `L12-MIN-E2E` and merge the real Agora verifier.
6. Complete the generic L12 hosted task, then run Agora exact-pair
   reacceptance.
7. Reconcile UI/lifecycle truth.
8. Close Agora current-environment status.

If a hosted probe fails, keep or restore the previous accepted pair, leave the
new candidate unaccepted, attach the exact failure evidence to its owner task,
and do not advance downstream closeout.
