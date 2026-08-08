# Agora current-environment gap audit

Audit window: `2026-08-08T10:28:00Z` through `2026-08-08T11:31:00Z`

Frozen Pantheon planning baseline: `de8635aa958593ca332395baba1880aacf096952`

The audit is a bounded snapshot. Auto-worker, PR, branch, task-state, and hosted
progress after the cutoff is deliberately not followed or folded into this
packet. The supervisor must recheck only admission conflicts when the final
packet is consumed; it must not reinterpret this document as a live dashboard.

## Verdict

Agora's principal product and UI source delivery is substantially complete.
The historical `AG-HOSTED-CLOSE-002` exact-pair acceptance also remains valid
for its frozen 2026-07-24 pair. The current environment cannot be called
accepted because all of the following are true at the cutoff:

1. the public BFF `/readyz` and `/bff/version` returned HTTP 502;
2. the served frontend manifest and the candidate BFF runtime image identify
   different Pantheon revisions;
3. the failed deployment preserved the old manifest but did not preserve or
   restore a serving BFF;
4. Agora has durable dataset records and handoffs, but Policy Learning does not
   consume and acknowledge those handoffs;
5. no terminal Policy Learning candidate is correlated into Consultation; and
6. the rejected/self-attesting Learning verifier has not been replaced by the
   already-planned real verifier task.

The accurate status is therefore:

> Major Agora feature and UI development is delivered; current deployment
> recovery, durable cross-loop integration, real verification, and exact-pair
> hosted reacceptance remain open.

## Evidence classes and acceptance rules

| Evidence class | What it proves | What it does not prove |
|---|---|---|
| Archived task and merged source | A historical implementation or evidence change was delivered | The currently served VM is running that change |
| Historical hosted closeout | One frozen FE/BFF pair passed at its recorded time | A later deployment or the current environment passes |
| Frontend `deployment.json` | The frontend host declares a candidate pair and safety posture | The declared BFF is reachable or actually serving that revision |
| Runtime `/readyz` and `/bff/version` | The public BFF is reachable and reports its runtime identity | The frontend is serving the matching reviewed commit |
| Database/API readback | Durable rows exist in the owning service | The next service consumed, acknowledged, or correlated them |
| Canonical task checkpoint | Task materialization and status at the cutoff | Auto-worker progress after the cutoff |

No closeout may substitute one class for another. In particular, neither an old
hosted result nor a manifest-only declaration is current runtime proof.

## Historical delivery inventory

The following items are historical closed scope and must not be redispatched by
this packet.

| Scope | Canonical task/evidence | Frozen result |
|---|---|---|
| Strategy Performance real truth and governed actions | `AG-PERF-TRUTH-001-BE`, `AG-PERF-TRUTH-001-FE` | Backend archived 2026-07-22 18:18Z; frontend archived 2026-07-22 22:53Z |
| Trading Room candidate truth | `AG-CAND-TRUTH-001-BE`, `AG-CAND-TRUTH-001-FE` | Backend archived 2026-07-22 21:20Z; frontend archived 2026-07-22 23:02Z |
| Six former Workshop 501 operations | `AG-WS-OPS-001`, `AG-WS-OPS-002` | Contract/service delivery archived 2026-07-22 18:04Z and 2026-07-23 00:57Z |
| Agora v1.13 OpenAPI, capability, and generated FE types | `AG-COMPAT-001-BE`, `AG-COMPAT-001-FE` | Archived 2026-07-23 01:32Z and 09:20Z |
| Compatibility admission gate | `AG-COMPAT-002-GATE` | Archived 2026-07-24 01:34Z |
| Source freshness | `PAN-SOURCE-FRESH-001` | Archived 2026-07-23 00:01Z |
| Trade Journey hosted E2E | `TJ-E2E-012` | Archived 2026-07-24 01:04Z |
| Allocation/hosted predecessor | `PPL-ALLOC-009` | Historical remaining-work packet records the hosted closeout; this packet does not reopen it |
| Agora exact-pair hosted closeout | `AG-HOSTED-CLOSE-002` | Accepted and archived with frozen evidence on 2026-07-24 |

### Historical exact-pair boundary

`AG-HOSTED-CLOSE-002` accepted:

| Field | Frozen value |
|---|---|
| Frontend SHA | `e4399e3ec68f882ace35d0349e6597cdd101525f` |
| BFF SHA | `f71c1f8ba889ba64956006ef0f9159840be6d065` |
| Pair ID | `ec91a4aa...c3de2` |
| Initial hosted checks | 18 accepted |
| Post-restart checks | 12 accepted |
| Posture | strict auth, live BFF mode, read-only/safe writes |

This is valid historical evidence for that pair only. It cannot admit the
current manifest or repair the current 502.

## Agora UI polish 001-011 inventory

The old UI index is lifecycle-stale. `draft` in that index is not reliable
source-delivery truth. The bounded repository and merged-history inventory is:

| Task | Source/evidence inventory | Correct interpretation |
|---|---|---|
| `AG-UIPOL-001` | execute-plans PR #293, merge `19587a0d...` | Source delivered; later hosted descendants include it; no same-named standalone hosted evidence file was found |
| `AG-UIPOL-002` | PR #291, merge `71e84a9f...`; task-specific hosted evidence exists | Source delivered and task-scoped hosted proof recorded |
| `AG-UIPOL-003` | PR #292, merge `1a4265c...`; task-specific hosted evidence exists | Source delivered and task-scoped hosted proof recorded |
| `AG-UIPOL-004` | PRs #290/#295/#325/#335; task-specific hosted evidence exists | Composed source delivered and task-scoped hosted proof recorded |
| `AG-UIPOL-005` | parity audit plus hosted evidence directory pinned to `1a4265c...` | Audit/evidence task, not a separate missing product feature |
| `AG-UIPOL-006` | PRs #314/#316, merges `8ad0a152...`/`886e357f...`; hosted evidence exists | Delivered despite stale draft label |
| `AG-UIPOL-007` | PRs #319/#320/#322/#341; hosted evidence exists | Delivered and reviewer-approved |
| `AG-UIPOL-008` | PRs #313/#315/#330/#331; hosted evidence exists | Delivered despite stale draft label |
| `AG-UIPOL-009` | PRs #317/#318; implementation head `e60fd...`; hosted evidence exists | Delivered despite stale draft label |
| `AG-UIPOL-010` | PR #321, merge `dcdbd96c...`; Pantheon task brief/evidence ancestry | Source delivered and present in later hosted ancestry; no equivalent standalone `AG-UIPOL-010-hosted-evidence.md` was found |
| `AG-UIPOL-011` | PRs #344/#345/#346; merge lineage includes `cbc687...`, `b6a5...`, `cb139...`; hosted evidence exists | Completed and hosted reverified |

The lifecycle reconciliation task must preserve three separate states:
`source-delivered`, `task-scoped-hosted-proof`, and
`included-in-later-hosted-ancestry`. It must not turn missing standalone proof
for 001 or 010 into a claim that their source code was never delivered.

## Current deployment snapshot

### Served frontend declaration

The public frontend `deployment.json` returned HTTP 200 and declared:

| Field | Value |
|---|---|
| Frontend SHA | `6a8d2d9b4f725056735eefd7165ef47b52cda53d` |
| Declared BFF SHA | `be956c07aca889043ef301389412b6744452f20b` |
| Pair ID | `c05fc6b0...` |
| Deployment state | `accepted` |
| Profile | `read-only` |
| BFF mode/fallback | `live` / `strict` |
| Real writes / stub writes | `false` / `false` |
| Embedded bearer | `false` |
| Agora compatibility | v1.13 accepted |
| Recorded acceptance date | 2026-07-26 |

The safety declaration is correct as a manifest posture, but runtime outage
prevents it from being revalidated as the currently functioning pair.

### Actual BFF and deployment failure

| Probe/runtime item | Cutoff result |
|---|---|
| Public BFF `/readyz` | HTTP 502 |
| Public BFF `/bff/version` | HTTP 502 |
| `pantheon-operator-bff-1` | `Created`, not serving |
| Candidate BFF OCI revision | `a55721bce2a7bc0a4dc01dd6eba1b48a58b78312` |
| `pantheon-source-ingest-1` | running, Docker health `unhealthy` |
| Source Ingestion application log | `/readyz` requests returned 200, while the Docker health command repeatedly exceeded its five-second timeout |

GitHub deployment run `31250996848` targeted Pantheon
`a55721bce2a7bc0a4dc01dd6eba1b48a58b78312` and execute-plans
`3ee9f962a36626f085e2ca1c088b3ce4b4d08e6f`. Exact-pair admission and rollback
baseline capture passed. The stack update then failed after Source Ingestion
did not become healthy; the environment-lease heartbeat later became
unavailable and the step exited 75. Public BFF proof, Agora restart smoke, and
the exact-pair switch were skipped.

The gate correctly refused to publish a candidate manifest. The transaction
still failed its incumbent guarantee: the old manifest remained, but the
previous serving BFF was not restored. That is a distinct deployment-transaction
gap. The Source Ingestion health/tick defect itself remains owned by
`L12-MIN-SRC-20260808` and must not be duplicated here.

## Cross-service durable-data snapshot

### Stored state

| Record/worker class | Cutoff result |
|---|---:|
| `agora.agora_dataset_records` | 81 |
| Distinct Agora tenants | 1 |
| Distinct Agora tenant/user scopes | 1 |
| Dataset kind `observe` | 81 |
| Dataset kind `learn` | 0 |
| `learning_eligible=true` | 81 |
| `agora.agora_evidence_handoffs` | 81 |
| Pending handoffs | 81 |
| Acknowledged handoffs | 0 |
| `policy_learning.candidates` | 0 |
| Consultation submitted requests | 2 historical `strategy_workshop` requests |
| Consultation memos | 0 |
| Consultation handoffs | 0 |
| Consultation workflow DLQ | 2 |

### What already exists

Agora dataset extraction already provides tenant/user-scoped records,
DatasetVersion persistence, durable inbox/leases/idempotency, durable evidence
handoffs, and an exact acknowledgement route. The acknowledgement contract
requires the correct tenant/user scope, dataset version, digest/idempotency,
and an authorized write identity.

Policy Learning runs in product dataset mode against Postgres with read-only
Agora access, no seed fallback, a healthy scheduler, and zero jobs. Its dataset
authority defaults discovery to `dataset_kind=learn`; the 81 existing rows are
all `observe`, so zero discovered jobs and zero candidates are truthful. The
current reader consumes dataset records directly and does not consume or
acknowledge Agora's durable handoff table.

Consultation already has durable workflow leases, idempotent event/evidence,
memo and handoff persistence, downstream acknowledgement, DLQ, and replay.
There is no connector that creates a Consultation request from a terminal
Policy Learning candidate, and the two existing requests are unrelated
historical Workshop records.

### Actual missing edges

```text
Agora interaction
  -> DatasetVersion + durable handoff        [implemented]
  -> Policy Learning consumes handoff        [missing]
  -> durable ingestion receipt, then ack     [missing]
  -> terminal shadow candidate               [minimum component task exists]
  -> governed Consultation request           [missing]
  -> terminal memo/handoff + common lineage  [executor exists; intake edge missing]
```

The 81 historical `observe` rows must not be bulk-acknowledged or relabeled to
make a dashboard green. Acceptance must create one fresh tenant-scoped `learn`
dataset flow and prove each durable transition. Historical rows may change
only when their own legitimate consumer receipt justifies it.

## Learning verifier inventory and deduplication

Archived `L12-VERIFY-LEARN-001` is superseded and cannot be used: its result is
an in-process/literal pass structure rather than proof from real service and
database boundaries.

The repository already defines the intended successor
`L12-VERIFY-LEARN-REAL-VERIFIER-001` in the 2026-07-31 and corrected 2026-08-03
guarded-remediation catalogs. At the cutoff it was not present in canonical
task state, and no implementation PR or worktree was found. Therefore:

- do not create `AGORA-L12-REAL-VERIFIER-20260808`;
- materialize only `L12-VERIFY-LEARN-REAL-VERIFIER-001`;
- update its dependencies to the current minimum DAG and the three explicit
  cross-loop bridge tasks in this addendum; and
- keep its implementation scope verifier-only. Product failures return to the
  owning component task.

This preserves the existing task identity without dispatching the obsolete
28-task catalog that originally contained it.

## Canonical task, PR, branch, and worktree snapshot

The authoritative task checkpoint was updated at
`2026-08-08T11:22:05Z`. The following relevant minimum-program rows were all
`todo` at that snapshot:

- `SUP-L12-MIN-FUNCTION-DAG-RECONCILE-20260808`;
- `L12-MIN-SRC-20260808`;
- `L12-MIN-TEACH-20260808`;
- `L12-MIN-AGORA-20260808`;
- `L12-MIN-IMIT-20260808`;
- `L12-MIN-CONS-20260808`;
- `L12-MIN-BFF-20260808`;
- `L12-MIN-INTEGRATE-20260808`;
- `L12-MIN-E2E-20260808`;
- `L12-MIN-HOSTED-20260808`; and
- `L12-MIN-CLOSE-20260808`.

No product task worktree or implementation PR for those rows was present in
the bounded snapshot. The minimum-DAG plan was PR #4621 at head `99a57cd0...`
and was waiting for independent canonical review. This is deduplication
evidence at the cutoff, not a promise that workers remained idle afterward.

The Agora addendum planning PR is #4628. A preliminary bridge packet was sent
before this audit was complete; its last bounded observation was `processing`
without a supervisor receipt. It is not accepted implementation authority.
The final handoff must name it as superseded, perform one authoritative
ID/hash readback, admit only missing or exact-matching task revisions, and
never replay a conflicting canonical row.

## Final gap register

| Gap | Severity | Existing owner/dedup boundary | Closing task |
|---|---|---|---|
| Public BFF 502; failed candidate can remove the serving incumbent | P0 | Source service repair remains `L12-MIN-SRC`; this addendum owns deployment transaction only | `AGORA-DEV-DEPLOY-RECOVERY-20260808` |
| Agora handoff has no durable Policy Learning consumer/ack | P1 | Ordered after `L12-MIN-AGORA` and `L12-MIN-IMIT` | `AGORA-IMIT-HANDOFF-CONSUME-20260808` |
| Terminal candidate has no Consultation intake/lineage | P1 | Ordered after handoff consumer and `L12-MIN-CONS` | `IMIT-CONSULTATION-INTAKE-20260808` |
| Cross-service credentials/runtime bindings are absent or unproved | P1 | Ordered after shared-file owner `L12-MIN-INTEGRATE` | `AGORA-LEARNING-CROSS-LOOP-BIND-20260808` |
| Real four-loop Learning verifier not materialized | P1 | Reuse existing planned ID; do not create a duplicate | `L12-VERIFY-LEARN-REAL-VERIFIER-001` |
| No current exact-pair Agora hosted acceptance | P1 | Generic twelve-loop hosted gate remains `L12-MIN-HOSTED` | `AGORA-CURRENT-HOSTED-REACCEPT-20260808` |
| UI index/lifecycle truth is stale | P2 | Documentation only; no frontend reimplementation | `AGORA-UI-LIFECYCLE-RECONCILE-20260808` |
| No current-environment closeout record | P2 | Must not close broader `L12-MIN-CLOSE` | `AGORA-CURRENT-CLOSE-20260808` |

## Non-goals and hard boundaries

- No live-capital, production, real-write, or automatic deployment authority.
- No weakening of authentication, RBAC, tenant/user isolation, strict
  fallback, or read-only defaults.
- No broad twelve-loop redesign and no replay of existing `L12-MIN-*` tasks.
- No direct cross-owner database writes; use service-owned APIs and durable
  receipts.
- No deletion, bulk acknowledgement, or relabeling of historical Agora rows.
- No direct edit to canonical task/event JSON, queue files,
  `.orchestrator/config.json`, or `/home/lupin/pantheon-ci-deploy/dev-root`.
- No frontend source in the Pantheon repository. Any future frontend task must
  use `ajoe734/execute-plans` and target `dev`.
- No continuous polling or repeated redispatch caused by auto-worker progress
  after the frozen cutoff.
