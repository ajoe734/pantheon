# Agora current-environment gap audit

Audit cutoff: `2026-08-08T10:28:00Z`

This audit separates historical source delivery, historical hosted acceptance,
current deployment truth, cross-service data truth, and canonical task truth.

## Historical delivery truth

The following work is delivered and must not be reopened by this packet:

- Strategy Performance truth and governed actions;
- Trading Room candidate truth;
- the six former Workshop 501 operations;
- Agora v1.13 OpenAPI, capability, generated frontend types, and compatibility
  gate;
- source freshness and replacement-VM restart persistence;
- PPL-ALLOC-009, TJ-E2E-012, and AG-HOSTED-CLOSE-002 historical closeouts;
- Agora UI polish source delivery 001-011.

Historical `AG-HOSTED-CLOSE-002` accepted FE `e4399e3ec68f...` with BFF
`f71c1f8ba889...`, 18 hosted checks, 12 restart/readback checks, strict
read-only posture, and a then-current readiness 200. That result remains valid
for that frozen pair only.

## Current deployment evidence

Public frontend `deployment.json` declared:

- FE `6a8d2d9b4f725056735eefd7165ef47b52cda53d`;
- BFF `be956c07aca889043ef301389412b6744452f20b`;
- accepted read-only profile;
- live BFF mode, strict fallback, real writes false, stub writes false, and no
  embedded token;
- Agora v1.13 compatibility accepted.

Current runtime probes returned HTTP 502 for public BFF `/readyz` and
`/bff/version`. The local operator BFF port refused connections.

Deployment run `31250996848` targeted Pantheon
`a55721bce2a7bc0a4dc01dd6eba1b48a58b78312` and execute-plans
`3ee9f962a36626f085e2ca1c088b3ce4b4d08e6f`. The exact-pair admission and
rollback-baseline capture steps passed. `Deploy dev VM stack under lease`
failed because `pantheon-source-ingest-1` became unhealthy. The subsequent
public BFF proof, Agora restart smoke, and exact-pair switch were skipped.

After failure:

- `pantheon-source-ingest-1` remained running but unhealthy;
- `pantheon-operator-bff-1` remained in `Created` state;
- the BFF candidate image reported OCI revision `a55721bce2a7...`;
- the public BFF returned 502;
- the old frontend manifest remained unchanged.

The admission gate correctly prevented a false manifest switch, but it did not
preserve or restore the serving BFF. That transaction boundary is not covered
by the existing L12 health-monitor task.

## Cross-service data evidence

Read-only aggregate database checks returned:

| Record class | Count |
|---|---:|
| Agora dataset records | 81 |
| Agora evidence handoffs | 81 |
| Pending handoffs | 81 |
| Acknowledged handoffs | 0 |
| Policy Learning candidates | 0 |
| Candidates linked to a dataset version | 0 |
| Terminal candidates | 0 |

Policy Learning readiness was 200 in product dataset mode with Postgres Agora
authority, read-only access, and no seed fallback, but reported `job_count: 0`.
Consultation readiness was 200 in the dev JSONL posture. There was no current
correlated interaction -> dataset -> acknowledgement -> candidate -> memo
evidence.

The counts prove persistence and handoff creation, not downstream consumption.
Historical rows must not be deleted or bulk-acknowledged merely to make the
metric green.

## Verifier gap

The archived `L12-VERIFY-LEARN-001` cannot be used for acceptance because its
verification result is a literal in-process pass structure without service or
database interaction. The successor minimum program contains real component
and E2E tasks, but its specification explicitly requires only one normal happy
path and excludes security, restart, DLQ, and exhaustive negative cases.

The addendum verifier therefore closes a distinct gap: it binds actual IDs
across the Agora-specific chain and proves tenant isolation, idempotency,
restart durability, acknowledgement ordering, DLQ/replay, and current safe
write posture.

## UI and lifecycle truth

UI polish source delivery 001-011 is present in merged execute-plans history.
Most tasks have Pantheon evidence and hosted proof. UI polish 010 is a special
case: its frontend merge is an ancestor of later hosted frontend releases, but
its Pantheon record is primarily a task brief and no equivalent standalone
task-specific hosted closeout was found. The lifecycle task must preserve this
distinction rather than labeling the implementation absent or manufacturing an
old acceptance.

## Canonical task and dispatch truth

At the cutoff, the authoritative task checkpoint contained:

- `SUP-L12-MIN-FUNCTION-DAG-RECONCILE-20260808` as `todo`;
- `L12-MIN-AGORA-20260808`, `L12-MIN-IMIT-20260808`,
  `L12-MIN-CONS-20260808`, `L12-MIN-BFF-20260808`,
  `L12-MIN-INTEGRATE-20260808`, `L12-MIN-E2E-20260808`,
  `L12-MIN-HOSTED-20260808`, and `L12-MIN-CLOSE-20260808` as `todo`;
- no task worktrees or implementation PRs for those product tasks.

The plan PR for that program is GitHub PR #4621 at head `99a57cd0...` and was
blocked on the canonical review gate. Packet A's receipt records dispatched
task calls but `invalid_materialization` because immediate readback failed;
later canonical checkpoint state contains the rows. Packet B has a durable
admission record and verified materialization readback.

This addendum treats the authoritative task rows as the deduplication truth,
requires an explicit reconciliation of Packet A's durable hashes, and never
replays the existing IDs.

## Gap-to-task traceability

| Gap | Closing task | Blocking dependencies |
|---|---|---|
| Public BFF 502 and failed-candidate incumbent loss | `AGORA-DEV-DEPLOY-RECOVERY-20260808` | addendum reconciliation gate |
| Handoffs unacknowledged and no terminal candidate/memo correlation | `AGORA-L12-CROSS-LOOP-INTEGRATE-20260808` | existing MIN Agora, Imitation, Consultation tasks |
| No non-self-attesting Agora-specific verifier | `AGORA-L12-REAL-VERIFIER-20260808` | cross-loop integration and existing MIN E2E |
| No current exact-pair Agora hosted acceptance | `AGORA-CURRENT-HOSTED-REACCEPT-20260808` | deploy recovery, real verifier, existing MIN hosted |
| UI/index/lifecycle truth drift | `AGORA-UI-LIFECYCLE-RECONCILE-20260808` | current hosted reacceptance |
| No current-environment closeout record | `AGORA-CURRENT-CLOSE-20260808` | hosted and lifecycle predecessors |

## Non-goals

- No live-capital enablement.
- No weakening of auth, tenant isolation, strict fallback, or safe write
  defaults.
- No broad L12 architecture redesign.
- No replay of existing minimum-functional task IDs.
- No direct modification of live checkout, canonical task JSON, queue files,
  supervisor scheduling, provider policy, or `.orchestrator/config.json`.
- No use of historical exact-pair evidence as current deployment proof.
