# Round 2 — Implementation and Failure-Path Audit

Audit timestamp: `2026-07-26T06:18:00Z`

Method: independently trace controller, worker, store, queue, auth, tenant,
concurrency, retry, failure, and downstream handoff code. This round does not
infer safety from a route name or test title. It looks for what happens under
duplicate workers, process death, stale state, cross-tenant IDs, and failed
downstream calls.

## Cross-loop controller gaps

### Canonical writer is not safe enough for the intended concurrency

`services/loop-control/writer.py` reads an existing record, merges fields, and
upserts through separate connections. Two concurrent updates can overwrite
each other's last-success, last-failure, backlog, lag, or DLQ fields.

Additional gaps:

- `record_tick`, `record_success`, and `record_failure` do not renew a lease;
- only Source, Strategy Distillation, and Alpha code instantiate the canonical
  writer; loops 4 through 12 do not;
- the projector hard-codes downstream actual state to `unobserved` with no
  `checked_at`;
- desired-state presence is not projected;
- BFF controller lookup uses environment tenant configuration rather than the
  authenticated request tenant, creating a potential cross-tenant truth leak.

Required repair:

- one transactional/locked update contract;
- monotonic field merge semantics;
- heartbeat/lease renewal;
- authenticated tenant-scoped lookup;
- desired and downstream-actual projection;
- conformance helper used by all twelve loops.

## Per-loop code-path findings

### 1. Source Ingestion

Confirmed gaps:

- current safe scheduler is one-shot/opt-in, so it does not own scheduled
  desired-state convergence;
- the default desired state comes from a static file rather than the complete
  Persona requirement authority;
- controller truth is stale in current dev;
- external pull safety and scheduler liveness are coupled.

Failure-path work:

- persist schedule intent before provider calls;
- claim due work with lease/fencing;
- distinguish policy-denied, not-applicable, credential failure, provider
  failure, and successful no-change;
- advance acknowledgement only after durable SourceRecord;
- isolate connector failures;
- provide durable missed-tick and replay state.

### 2. Strategy Distillation

New code-level gaps:

- some failures call `record_tick` rather than `record_failure`;
- Registry sync errors are counted/swallowed and the tick can still be marked
  successful;
- runtime writer calls omit evidence references needed by BFF qualification;
- queue/store is explicitly non-thread-safe JSONL;
- jobs have no durable lease, retry, or DLQ;
- failed jobs become manual-only terminal records;
- stable job ID is based only on `source_id`, so revised content under the same
  ID may be silently skipped;
- dry-run and deterministic replay modes are absent.

Required repair:

- canonical source version/content digest in the idempotency key;
- transactional source outbox and Postgres distillation inbox;
- claim lease, retry/backoff, DLQ/replay;
- fail/degrade the tick when Registry synchronization fails;
- record current evidence refs and terminal draft readback.

### 3. Alpha Replication

New code-level gaps:

- discovery queries Registry by distillation `source_id`, while Distillation
  creates `strategy_id = "strat-" + source_id`; this can yield zero discovery;
- queue accepts lifecycle `review` in addition to an approved state;
- `mark_revalidated`, `mark_failed`, and `replay_dlq` omit tenant scope;
- run ID/idempotency also omit tenant;
- claimed jobs have no lease expiry/reclaim;
- DLQ replay does not refresh enqueue time, so aged work can immediately time
  out again;
- worker tick errors do not necessarily fail the replication controller;
- output remains stub/local rather than the research authority.

Required repair:

- one canonical reviewed StrategySpec identifier;
- strict approved-review admission;
- tenant in every key, query, mutation, run ID, and replay;
- expiring claim lease and stale claim recovery;
- durable authoritative ExperimentTask/ExperimentRun handoff;
- controller failure and downstream terminal readback.

### 4. Persona Teaching

New code-level gaps:

- Training Session service has no inbound auth/tenant enforcement on its host
  exposed mutation routes;
- preview worker calls it unauthenticated;
- Postgres backend persists append events, while sessions/jobs/replays remain
  local files;
- alive marker is updated even after exceptions or non-completed jobs, so
  health can be green while useful work fails;
- no canonical loop controller writer is used.

Required repair:

- service-to-service and operator auth;
- tenant-scoped sessions, jobs, and persona target;
- authoritative HA job/session store;
- functional readiness separated from process alive;
- loop controller and BFF terminal persona readback.

### 5. Agora Interaction Evidence

New code-level gaps:

- production BFF passes an `OperatorIdentity` model, but dataset router calls
  `identity.get(...)`; tests substitute a dict and do not exercise the real
  type;
- evidence lookup and DLQ replay are not tenant/user scoped;
- submit/process/replay mutations require only a read role;
- required `Idempotency-Key` is not bound to stored request digest/conflict
  behavior;
- submit synchronously invokes the global inbox, allowing one caller to
  process other tenants' work;
- database claim uses `FOR UPDATE` over all pending work, without
  `SKIP LOCKED`, batch ownership, or lease;
- no canonical loop controller writer exists.

Required repair:

- real OperatorIdentity integration test and typed access;
- tenant/user predicates on every read/mutation/replay;
- mutation capability/RBAC and MFA where applicable;
- stored idempotency digest and conflict response;
- asynchronous tenant-scoped claim/lease worker;
- downstream DatasetVersion/handoff acknowledgement.

### 6. Human Imitation / Shadow Evaluation

New code-level gaps:

- Compose defaults the policy-learning store to JSON and leaves the scheduler
  opt-in without health;
- Agora dataset discovery incorrectly depends on the policy store itself being
  Postgres, so default JSON mode discovers no Agora datasets;
- discovery/aggregation selects persona/session but omits tenant/user;
- lookup error silently substitutes `SEED_DATASET`;
- scheduled tick creates proposed candidates, while actual backlog processing
  still requires manual `/worker/process`;
- backlog processing has no claim/lease, so concurrent calls can train the
  same candidate;
- service and scheduler have no inbound auth/tenant context;
- no canonical loop controller writer exists.

Required repair:

- authoritative Agora DatasetVersion source independent of policy-store
  backend;
- fail-closed no-seed product mode;
- tenant-scoped content aggregation;
- supervised scheduler plus worker claim/lease/recovery;
- authoritative training/evaluation artifact and candidate lineage.

### 7. Consultation

New code-level gaps:

- Compose has the API but no workflow executor;
- API and executor have no inbound auth/tenant contract;
- executor does not obtain committee/provider contributions: it creates a
  synthetic participant and blocks until a real participant-authored
  transcript/evidence/memo already exists;
- documented `MAX_BLOCKED_ATTEMPTS` is not enforced, so blocked work can retry
  forever;
- pending fetch is global and has no claim/lease;
- participant and handoff checks use list-before-create TOCTOU patterns;
- default JSONL append/in-memory indexes lack cross-process lock/fsync;
- no canonical loop controller writer exists.

Required repair:

- real provider/committee execution owner;
- bounded blocked state with reason and DLQ/manual resolution;
- tenant-scoped durable workflow claim;
- uniqueness constraints for participant, memo, and handoff;
- supervised Compose worker and controller truth.

### 8. Promotion / Deployment

New code-level gaps:

- deployment mutation routes are host-exposed without inbound auth/tenant
  enforcement and trust a synthesized/caller-supplied actor role;
- outbox consumer calls APIs unauthenticated;
- polling has no durable claim lease, allowing HA consumers to dispatch the
  same row concurrently;
- downstream idempotency reduces duplicate effects but does not establish
  exactly-once ownership;
- worker has no Compose healthcheck and its health file configuration is empty;
- idle clean ticks may not recover a previously degraded health state unless a
  record is consumed;
- no canonical loop controller writer or tenant partition exists.

Required repair:

- authenticated service and operator mutations;
- tenant-scoped transactional outbox claim/ack;
- recovery after downstream side effect but before acknowledgement;
- functional health on empty queues;
- controller and BFF stage truth.

### 9. Capital Pool Execution

New critical durability gap:

- Redis pending store uses eager `LPOP` before validation/execution;
- process death after pop loses the signal;
- invalid payload or execution exception is not reliably requeued/DLQ'd;
- DLQ and processed-ID writes are best-effort/fail-open;
- unscoped legacy binding/runtime/capital-pool signals pass through;
- fleet exactly-one ownership exists only in one reconciler process' local map
  and lock; two reconciler replicas can each spawn a full fleet;
- Capital API is host-exposed without inbound auth/tenant enforcement and
  trusts caller-supplied actor role;
- no canonical loop controller writer exists.

Required repair:

- Redis claim/visibility-timeout/ack or stream consumer-group pattern;
- durable DLQ and processed-id transaction semantics;
- fail-closed scope in governed paper mode;
- leader lease/fencing for fleet reconciler replicas;
- authenticated, tenant-scoped Capital API.

### 10. Telemetry / Reconciliation

New critical durability/security gaps:

- Compose provides Postgres but does not select a durable telemetry buffer;
  telemetry defaults to an in-memory buffer;
- HTTP returns `202` after enqueue, leaving crash-before-batch-write data loss;
- ingest/read/DLQ routes lack auth/tenant filtering;
- Reconciliation routes are similarly global;
- Postgres reconciliation backend persists evaluations/alerts, while primary
  records and DriftReports remain local JSON;
- consumer state treats corrupt JSON as empty and can overwrite it, uses a
  fixed temporary file, and lacks lock/fsync;
- scheduler uses random tick IDs with no leader lease;
- no canonical loop controller writer exists.

Required repair:

- durable ingest-before-202 acknowledgement;
- authenticated tenant-scoped ingest/read/replay;
- authoritative Postgres reconciliation records and reports;
- corruption fail-closed and atomic state;
- scheduler/consumer leader lease and deterministic window key;
- trace identity contract repair identified in Round 1.

### 11. Evolution

New critical authority gaps:

- review/approve/execute APIs have no inbound auth/tenant enforcement and trust
  actor roles from request bodies;
- EvolutionDecision has no tenant field;
- active uniqueness is only target type/ID, causing possible cross-tenant
  collisions;
- five research action types are declared, but only `retrain` invokes a
  downstream path;
- other actions become synthetic `submitted` results with no side effect;
- retrain runs in a best-effort background thread with stub dispatch fields;
- failure is logged after the decision is already marked executed, with no
  durable retry/outbox;
- daily sweep liveness is process memory and scheduler has no health/retry/lease;
- no canonical loop controller writer exists.

Required repair:

- authoritative actor and tenant;
- tenant-scoped single-active constraint;
- durable action outbox for every supported action;
- real non-stub downstream receipt and terminal readback;
- retry/DLQ/compensation before executed terminal state;
- durable sweep health/controller state.

### 12. BFF Health Monitoring

New code-level gaps:

- the implementation explicitly expects health telemetry to fail binding
  validation and enter DLQ;
- policy-required error-rate-spike trigger is absent;
- only five environment targets are defined and current Compose does not set
  every one of those variables;
- most BFF downstream services are not monitored;
- failure count, probe status, and incident map are memory-only;
- telemetry/incident delivery has no retry/backoff/DLQ and errors are swallowed;
- recovery clears local tracking but deliberately leaves the incident open;
- pre-first-probe truth can report `overall_ok = null`;
- no canonical loop controller writer exists.

Required repair:

- dedicated infrastructure telemetry authority;
- durable probe and delivery state;
- error-rate event trigger;
- complete target registry;
- exactly-once incident update/resolution and telemetry correlation;
- two-replica/restart behavior.

## Cross-loop security conclusion

Direct service exposure has outpaced service-to-service identity and tenant
enforcement. Teaching, Policy Learning, Consultation, Deployment, Capital,
Telemetry, Reconciliation, and Evolution cannot be called product-operable
until inbound authority is enforced. BFF authentication alone is insufficient
when host ports expose the downstream mutation routes.

## Round-2 conclusion

Round 2 found gaps that were not visible from container state:

- loss windows in Capital and Telemetry;
- cross-tenant mutation/read hazards in Alpha, Teaching, Agora, Imitation,
  Consultation, Deployment, Capital, Reconciliation, and Evolution;
- false-green health behavior;
- stub/synthetic downstream results;
- multi-worker duplicate execution;
- non-durable local stores behind APIs described as durable.

These are implementation tasks, not evidence-only cleanup.
