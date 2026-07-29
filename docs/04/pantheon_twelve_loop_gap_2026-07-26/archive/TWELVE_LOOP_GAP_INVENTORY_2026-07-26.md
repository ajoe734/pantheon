# Twelve-Loop Master Gap Inventory

Inventory ID: `Twelve-Loop-Gap-2026-07-26`

Supersedes as current gap truth:

- planning-era maturity statements that predate the replacement dev VM;
- component-local “done” labels without current terminal readback;
- old-host evidence not admitted by the current closeout guardrail.

Does not supersede:

- canonical domain policies and schemas;
- valid historical evidence as supporting contract evidence;
- immutable approval and no-live-capital safety rules.

## Reconciled verdict after three passes

| # | Loop | Runtime state | Product maturity baseline | Highest-risk missing development | Required completion proof |
| --- | --- | --- | --- | --- | --- |
| 1 | Source Ingestion | API healthy; scheduler absent | not implemented | supervised safe due-state controller and Persona authority | requirement→schedule→real SourceRecord, duplicate/restart/provider failure |
| 2 | Strategy Distillation | worker alive; no eligible source | not implemented | transactional outbox/inbox, versioned idempotency, failure truth | SourceRecord→Registry draft across real services, race/replay/immutability |
| 3 | Alpha Replication | worker alive; stub/handoff | not implemented | approved-only tenant-safe queue to authoritative research run | approved spec→ExperimentRun, negative gate, lease/restart/DLQ |
| 4 | Persona Teaching | worker healthy; idle | not implemented | inbound authority, HA job/session truth, functional health | hosted session→eval→persona before/after, fail/no-mutation, restart |
| 5 | Agora Evidence | routes/storage present | not implemented | typed identity, tenant/RBAC, leased processor owner | all actions→DatasetVersion/handoff, idempotency/race/no-runtime-mutation |
| 6 | Imitation/Shadow | scheduler absent | not implemented | real dataset discovery, no seed fallback, worker lease | real dataset→candidate→gates, tenant/restart/duplicate |
| 7 | Consultation | API healthy; executor absent | not implemented | real committee/provider executor and durable handoff | composed async workflow, multi-worker/crash/blocked/DLQ |
| 8 | Promotion/Deployment | outbox worker alive | not implemented | auth/tenant, outbox claim lease, current health/truth | immutable artifact→binding on current dev, duplicate/crash/compensation |
| 9 | Capital Execution | active governed paper | not implemented/manual | claim/ack signal durability, strict scope, fleet leader lease | six-binding restart/kill/retire/isolation/order-fill-heartbeat |
| 10 | Telemetry/Reconciliation | actively degraded/unhealthy | not implemented | durable ingest, trace contract, scheduler/consumer lease | current six summaries, incident triggers, duplicates/restart/RPO |
| 11 | Evolution | workers alive; zero candidates | not implemented | telemetry baseline and real durable approved-action dispatch | anomaly→incident→postmortem→decision→executed downstream |
| 12 | BFF Health | monitor process alive | not implemented | infrastructure telemetry, durable state, full target/error trigger | stop/recover targets→accepted telemetry→one incident→resolved truth |

## Common platform gaps

### Controller truth

- canonical writer needs transactional lost-update protection;
- leases are not renewed by normal status writes;
- loops 4–12 do not write canonical loop controller records;
- desired-state presence and downstream actual state are not projected;
- BFF tenant lookup must bind to authenticated tenant;
- catalog must remain fail-closed until current evidence is accepted.

### Durability

- JSON/JSONL or memory remains in product paths for Distillation, Teaching,
  Policy Learning, Consultation, Deployment health, Reconciliation, and
  Evolution sweep state;
- Capital pops a signal before durable acknowledgement;
- Telemetry can return 202 while an event is only memory-buffered;
- several worker queues have no claim lease or stale-claim recovery.

### Authority and tenant boundary

Directly exposed service routes need inbound service/operator identity and
tenant enforcement. This is mandatory for Teaching, Agora extraction, Policy
Learning, Consultation, Deployment, Capital, Telemetry, Reconciliation, and
Evolution. Caller-supplied roles are not authority.

### Health truth

Process alive and functionally able to complete work must be distinct. A clean
idle tick should recover health; failed downstream sync, corrupt state, or
non-completed jobs must not remain green.

### Fleet readiness

The running supervisor is healthy and idle, but its next-start runtime config
has drifted to disable Codex/Codex2. The repository policy enables four Codex
and four Codex2 slots. Fleet capacity reconciliation is a program prerequisite,
not a manual edit.

The remaining `PPL-ALLOC-009` Human/Ops gate owns broad BFF and frontend source
scopes. It is a declared external dependency, not work that this program may
silently supersede. Non-overlapping loop tasks may run; overlapping tasks stay
blocked until its canonical terminal status is `done`.

### Evidence and delivery

- current guardrail: `4/20` replay sources accepted;
- most loop evidence lacks a formal reviewer;
- some manifests are schema-invalid;
- historical Deployment evidence is bound to the old dev environment;
- current replacement-dev restart and cross-loop drills are missing;
- Human/Ops signoff is descriptive metadata without a protected
  transition-time verifier;
- evidence must be created from behavior after implementation, not repaired by
  relabeling.

## Development work packages

| Task | Owned result | Primary loop(s) |
| --- | --- | --- |
| `L12-FLEET-001` | restore and prove eight Codex-family execution slots | platform |
| `L12-CTRL-001` | transactional controller truth and tenant-safe BFF projection | all |
| `L12-TEL-001` | durable telemetry acknowledgement and lifecycle identity | 10, 11, 12 |
| `L12-REC-001` | durable/leased reconciliation and timeout recovery | 10 |
| `L12-SRC-001` | safe supervised source due-state controller | 1 |
| `L12-DIST-001` | transactional source-to-draft distillation | 2 |
| `L12-ALPHA-001` | authoritative approved-only ExperimentRun path | 3 |
| `L12-TEACH-001` | authenticated tenant-safe HA teaching path | 4 |
| `L12-AGORA-001` | governed tenant-safe extraction processor | 5 |
| `L12-IMIT-001` | scheduled real-dataset imitation/shadow executor | 6 |
| `L12-CONS-001` | supervised real committee/red-team executor | 7 |
| `L12-DEP-001` | leased/authenticated deployment dispatcher | 8 |
| `L12-CAP-001` | durable claim/ack and strict paper isolation | 9 |
| `L12-EVO-001` | baseline-complete durable action dispatcher | 11 |
| `L12-BFF-001` | infrastructure health telemetry and incident recovery | 12 |
| `L12-SIGNOFF-001` | protected Human/Ops final-verdict enforcement | all |
| `L12-MANIFEST-001` | single-owner Compose/default runtime activation | all |
| `L12-TRUTH-001` | all-loop controller integration and BFF/catalog truth | all |
| `L12-FE-TRUTH-001` | hosted operator truth in `execute-plans` | all |
| `L12-VERIFY-KNOW-001` | loops 1–3 product drill | 1–3 |
| `L12-VERIFY-LEARN-001` | loops 4–7 product drill | 4–7 |
| `L12-VERIFY-RUNTIME-001` | loops 8–9 governed-paper drill | 8–9 |
| `L12-VERIFY-OBS-001` | loops 10–12 incident/evolution/health drill | 10–12 |
| `L12-HOSTED-001` | replacement-dev deployment and restart drill | all |
| `L12-CLOSE-001` | evidence admission and exact maturity promotion | all |

## Definition of done per loop

A loop is not done until:

1. its declared trigger reaches non-seed desired state;
2. a durable owner claims work using tenant-scoped idempotency and fencing;
3. downstream side effects complete before acknowledgement;
4. failures are visible with bounded retry, DLQ, and replay;
5. duplicate and concurrent execution are harmless;
6. process/service/database/stack restart recovery is proved;
7. authority, tenant, approval, environment, and no-live-capital negatives pass;
8. authoritative actual state and correlation IDs are read back;
9. controller record and BFF truth are current and accepted;
10. exact branch, PR, merge, deployment and image identities are archived;
11. independent reviewer approves the checksummed evidence manifest;
12. protected Human/Ops verdict enforcement is active for final closeout;
13. catalog maturity is no higher than the collected proof.

## Program completion

The program is complete only after `L12-CLOSE-001` is the unique terminal
closeout, all predecessor tasks are `done`, the current hosted stack passes the
global restart drill, and no blocking residual risk remains. The closeout must
fail closed if any loop record is stale, any required target is unobserved, or
any evidence source is indirect or contradicted.
