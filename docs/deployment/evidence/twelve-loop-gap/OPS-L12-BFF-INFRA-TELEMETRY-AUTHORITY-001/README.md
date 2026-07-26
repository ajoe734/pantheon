# OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001 evidence

Status: fourth owner cut, ready for independent `Codex2` re-review.

The canonical reviewer for this task is now `Codex2`. `Antigravity` was the
reviewer for the first three cuts and rejected two of them; both rejections stay
in `evidence.json` `record_log` (sequences 2 and 4) as the historical review
trail, and the reassignment itself is recorded at sequence 6. No `Antigravity`
verdict is restated as a `Codex2` verdict, and no approval has been recorded by
either reviewer.

The first cut (head `796c6e5e3`) had a single-phase admission ledger with a loss
window: a second caller arriving between the durable `admitted` record and the
durable enqueue was answered as a successful duplicate although nothing had been
persisted. That was repaired by the two-phase fenced reservation described in
[§ Durable idempotent admission](#durable-idempotent-admission).

The second cut (head `0d0b015c9`) still did not satisfy AC3, because the
reservation protocol was correct but what it committed against was not durable:
the service accepted `202` and committed the ledger receipt after
`InMemoryBuffer.put`, whose `is_durable()` is `False`. A process crash could
erase the only copy of the event while the committed receipt made every later
retry an idempotent `duplicate` — a permanent loss with no error anywhere. The
repair is [§ Durability is proven, not assumed](#durability-is-proven-not-assumed):
admission now fails closed unless the configured buffer is a durable broker,
and the readback runs against a **real NATS JetStream** file-storage work
queue rather than a memory buffer.

The fourth cut changes no implementation byte. It exists because PR #4211 was
`BEHIND` `dev` and its `Branch CI Gate` failed `Commit trailers` on `0410a89f0`
— an already-merged `dev` commit belonging to
`OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001` whose squash subject is 79 chars.
The pre-fix workflow anchored the scan range at the synthetic merge commit, so
the PR was red on somebody else's history. Merging current `dev` both cleared
that range and picked up the `OPS-CI-PR-TRAILER-RANGE-001` repair. That merge is
the validated head, and every validation command was rerun against it — see
[§ Head binding](#head-binding).

This packet proves that telemetry now owns an authoritative, strict-auth,
non-trading contract for infrastructure health, so control-plane health
monitoring never has to invent RuntimeBinding identity and never gets a
shape-based shortcut around trading validation.

The change is deliberately limited to `services/telemetry`. `services/incidents`
stays owned by `L12-EVO-001` — this contract only carries an optional
`incident_ref` string that telemetry never acts on — and
`services/control-plane/bff` stays owned by `L12-BFF-001`.

## What was delivered

- **A distinct non-trading schema.** `InfrastructureHealthEvent` lives under
  `definitions` in
  [`services/telemetry/telemetry_event.schema.json`](../../../../../services/telemetry/telemetry_event.schema.json)
  and is validated as a standalone schema. It is not reachable from the
  `TelemetryEvent` envelope, requires no RuntimeBinding, and rejects every
  RuntimeBinding evidence field at any depth — including inside `metadata` —
  so a probe producer cannot present, invent, or spoof trading binding identity.

- **A stricter admission authority.** `POST /api/v1/telemetry/infrastructure-health`
  requires a verified **service JWT** even when the deployment runs the rest of
  telemetry in `permissive` mode. The tenant must be bound by the token's own
  claims (the deployment-wide `PANTHEON_TELEMETRY_ALLOWED_TENANTS` fallback and
  wildcard tenant claims are both refused here), and the producer must be inside
  **both** the `PANTHEON_TELEMETRY_INFRA_PRODUCERS` deployment allowlist and the
  token's producer scope. The shared static service bearer token is refused
  because it proves no producer scope.

- <a id="durable-idempotent-admission"></a>**Durable idempotent admission,
  two-phase and fenced.** Admission is keyed by the producer's stable `event_id`
  plus a sha256 fingerprint of the canonical event, in an append-only ledger
  guarded by a POSIX advisory lock. It is deliberately two-phase:

  - `begin()` takes a **`reserved`** claim carrying an owner token and a lease;
  - a **`committed`** record is written only after a durable enqueue receipt, and
    only a committed record makes a later retry an idempotent `202 duplicate`;
  - a caller that meets a live reservation gets `503 INFRA_ADMISSION_IN_FLIGHT`
    and must retry — it is never told an unpersisted observation was delivered;
  - `commit()` and `release()` are token-scoped, so an owner whose lease expired
    and was taken over is fenced out of both;
  - an expired reservation left by a crashed replica is recovered by the next
    caller, which steals the claim with a fresh token;
  - a failed, cancelled, or erroring enqueue releases the claim rather than
    stranding it.

  Reusing an `event_id` for different content returns
  `409 INFRA_EVENT_ID_CONFLICT` in every state.

  Tune `TELEMETRY_INFRASTRUCTURE_HEALTH_LEASE_SECONDS` (default 30) above the
  worst-case durable enqueue latency. A lease that is too short only produces
  retryable in-flight or fenced answers — never a false success or a lost event.

- <a id="durability-is-proven-not-assumed"></a>**Durability is proven, not
  assumed.** The ledger protocol above is only as honest as the thing it commits
  against, so the configured buffer must prove durability from its own
  `is_durable()` — **before** the reservation is taken, and again before the
  receipt is committed:

  - a deployment whose buffer is volatile answers `503 INFRA_BUFFER_NOT_DURABLE`
    and writes **no ledger record at all**, so nothing can later masquerade as an
    admitted event;
  - a backend that degrades between the enqueue and the commit has its
    reservation released instead of committed, so the producer's retry is still
    admissible;
  - `/healthz` exposes `infrastructure_health_buffer_durable`, so a deployment
    that cannot admit infrastructure health says so before any traffic arrives.

  There is no production-enableable volatile bypass. The gate consults nothing
  but the backend's own durability: no environment variable, config key, or event
  field can assert durability on its behalf, and a test sets every telemetry
  `PANTHEON_*` variable to its most permissive value at once to prove it. The
  tests reach a durable broker through an in-process constructor argument that
  `buffer.create_buffer` and the environment-driven service builder never touch —
  and injecting a *volatile* buffer through that same seam still fails closed,
  because durability is what is checked, not who supplied the buffer.

- **Trading validation untouched.** The trading ingest path keeps evidence
  contract E-1 through E-6 and its authoritative RuntimeBinding cross-validation
  exactly as they were, and now also refuses `infrastructure_health` outright so
  the two authorities cannot be confused.

## Fail-closed defaults

`PANTHEON_TELEMETRY_INFRA_PRODUCERS` is empty by default, and an unconfigured or
wildcard allowlist is refused, so this route admits nothing until an operator
explicitly admits a producer. If the authoritative schema or the durable ledger
is unavailable, ingestion refuses the event rather than admitting something it
cannot deduplicate. If the deployment has no durable broker behind the route,
ingestion refuses the event rather than admitting something it cannot keep.

## Product evidence admission

[`evidence.json`](evidence.json) is a schema-valid `ProductEvidenceManifest` for
`schemas/product-evidence.schema.json`. Its exact repository-relative
`task.review_file` is
`docs/deployment/evidence/twelve-loop-gap/OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001/evidence.json`,
and `overall_admission=pass_owner_evidence_ready` means only that the owner proof
is complete enough to enter independent review. **It does not assert reviewer
approval.**

`record_log` contains both `Antigravity` `changes_requested` decisions — on the
first cut and on the second — but **no approval**, and `AC6` remains
`pending_reviewer`. `AC6` is judged against `Codex2`, the canonical reviewer on
the task row; the canonical acceptance row still reads `Antigravity` because it
was written before the reassignment, and that is recorded in `AC6`
`blocking_until` rather than silently rewritten. After a governed `Codex2`
approval, owner closeout must append the actual verdict to `record_log`, record
the merged PR number, merge sha, and merge time, refresh this README and the
companion checksum, merge that update through the task PR, and only then run
`done`.

<a id="head-binding"></a>

### Head binding

`validation.validated_head_sha` is `cca84df53`, the merge of `dev` `6578ef968`
into the task branch, and all three required checks are green on it — `Commit
trailers`, `Runtime mirror guard`, and `Smoke acceptance`, on both the
`pull_request` and `push` events. Both runs are recorded under
`implementation_delivery.required_checks`.

The commit that carries this manifest lands after the validated head and becomes
the final PR head, so its own `Branch CI Gate` run cannot be named inside the
manifest that commit creates. That run is the branch-protection merge gate and
is visible on PR #4211. What makes the binding hold anyway is that the commit
touches documentation only:
`integrity.source_artifact_sha256_by_epoch.implementation_files_at_validated_head`
records the `sha256` of each of the five implementation files at the validated
head, and re-running `git show <final-head>:<path> | sha256sum` must reproduce
them exactly. The same digests hold at head `28b13a16d`, which is why the
runtime readback and the three mutation controls taken there are carried forward
verbatim rather than re-executed: they probe bytes that have not changed.

## Proof

- [`current-runtime-readback.json`](current-runtime-readback.json) — a bounded
  local nonprod readback against the **running** Flask telemetry service over
  real HTTP on `127.0.0.1`, backed by a **real NATS JetStream broker** (file
  storage, work-queue retention, task-scoped stream) and with
  `PANTHEON_RUNTIME_MANAGER_URL` pointed at an unreachable address so
  authoritative binding lookups fail closed:

  | observation | result |
  | --- | --- |
  | `/healthz` on the durable deployment | `200`, `infrastructure_health_buffer_durable=1` |
  | valid strict-auth ingest | `202`, `duplicate=false` |
  | retry with the same stable event ID | `202`, `duplicate=true` |
  | the admitted event in the JetStream stream | present, file storage, work queue |
  | **telemetry process SIGKILLed after the receipt committed** | **the admitted event is still in the broker** |
  | replay after restarting the crashed service | `202`, `duplicate=true` |
  | **reservation stranded as if by a crashed replica, lease still live** | **`503 INFRA_ADMISSION_IN_FLIGHT`** |
  | **same event once the lease expired** | **`202` accepted, receipt committed** |
  | event ID reused with different content | `409 INFRA_EVENT_ID_CONFLICT` |
  | missing token | `401` |
  | wrong tenant | `403 TENANT_FORBIDDEN` |
  | payload tenant ≠ authenticated tenant | `403 TENANT_PAYLOAD_MISMATCH` |
  | wrong producer | `403 PRODUCER_FORBIDDEN` |
  | spoofed `binding_id` on the infra route | `400 INFRA_BINDING_FIELD_FORBIDDEN` |
  | `runtime_health` probe shape on the trading route | `400 rejected` |
  | infrastructure event on the trading route | `400 rejected` |
  | durable ledger after all of the above | `2` committed receipts, `0` orphan reservations |
  | **second service booted with `TELEMETRY_BUFFER_BACKEND=memory`** | **`503 INFRA_BUFFER_NOT_DURABLE`, `0` ledger records, `buffer_durable=0`** |

- [`services/telemetry/test_infrastructure_health_ingest.py`](../../../../../services/telemetry/test_infrastructure_health_ingest.py)
  — 44 real strict-auth route, replica, durability-authority, and crash-matrix
  tests. Beyond the strict-auth and two-phase coverage of the previous cut, the
  crash matrix **SIGKILLs a real ingest process in each admission window** —
  before the durable put, after the durable put but before the commit, and after
  the commit — plus restart replay and four concurrent replica **processes** over
  one broker log and one ledger. Each window asserts both failure modes are
  absent: no producer is ever answered `accepted` or `duplicate` for an
  observation that is not in durable storage, and a retry with the same stable
  event ID always ends with exactly one committed receipt over durable copies
  byte-identical to the original event.

  The tests reach durability through a file-backed broker double whose `put`
  `fsync`s before returning, so the return of `put` is a real durability point
  that a `SIGKILL` one instruction later cannot undo. It lives in the test module
  and is unreachable from `buffer.create_buffer`.

- Mutation controls, run in three groups:

  - strict-auth gates — removing the strict-mode pinning, short-circuiting the
    ledger admission, and replacing the producer-scope intersection with a union
    failed 1, 6, and 2 tests respectively;
  - two-phase reservation — **reinstating the first reviewed bug** (answering a
    live reservation as committed) failed 3 tests; removing the commit token
    fencing failed 1; making reservations non-expiring failed 2; removing the
    release-on-cancellation path failed 1;
  - durability gate — short-circuiting the buffer durability check failed 5 of
    the 6 durability-authority tests; removing only the pre-commit re-check
    failed `test_admission_is_refused_when_durability_is_lost_before_the_commit`;
    and **reinstating the second reviewed bug** by committing the receipt before
    the durable enqueue failed 8 tests, including all three crash-window tests.

  The suite returned to green after each restore, so the repair is proven by
  tests that fail without it rather than by assertions that were already true.

Exact commands and their conclusions are recorded in `evidence.json` under
`validation.commands`. On the validated head the full telemetry suite is clean —
`335 passed, 1 skipped, 29 subtests passed`, no failures. The
missing-`PANTHEON_RUNTIME_MANAGER_URL` failure that the third cut carried as a
documented environment residual is gone, because the `dev` sync brought in the
test-owned isolation fixture from
`OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001`.

One residual remains, outside telemetry: the cross-service regression set fails
`test_p0_paper_operating_loop_smoke.py::MinimumPaperOperatingLoopSmokeTest::test_deployment_plan_to_runtime_binding_to_bff_runtime_status`
on `PermissionError: [Errno 13] Permission denied: '/data'` while the paper
runtime tries to create `/data/runtime/lifecycle-outbox`. That path is an
unwritable host directory on this machine, telemetry admission never creates or
consults it, and no frame of the failure touches this task's files.

Scope of the readback: it ran without `TELEMETRY_DB_DSN`, so the canonical sink
behind the broker was the in-process dev sink and the batch interval was raised
so the writer would not acknowledge inside the readback window. The durable
receipt driving the commit phase is a real JetStream PubAck; the canonical
Postgres commit itself — unchanged by this task and proven by `L12-TEL-001` —
was not re-exercised here. This is recorded as a residual risk rather than
claimed as deployed proof.

## Composition

`L12-BFF-001` must drop its out-of-scope edits to `services/telemetry` and its
shape-based binding bypass, and become a strict-auth client of this route:
obtain a service JWT whose claims bind its tenant and name it as a producer, get
that producer admitted to `PANTHEON_TELEMETRY_INFRA_PRODUCERS`, and derive a
stable `event_id` from the observation identity (producer, component, probe
window) rather than generating one per attempt or per replica.

Client contract for the BFF: treat `202` as delivered, `409` as a producer bug in
`event_id` derivation, and every `503` — including `INFRA_ADMISSION_IN_FLIGHT`,
`INFRA_ADMISSION_FENCED`, and `INFRA_BUFFER_NOT_DURABLE` — as **retry with the
same `event_id`**. A `503` means no durable receipt exists yet, so retrying is
what makes delivery at-least-once, and the ledger keeps it exactly-once.

`INFRA_BUFFER_NOT_DURABLE` is the one `503` a client cannot retry its way out of:
it says the deployment has no durable broker behind the route, so retries will
keep failing until that is fixed. Alert on it rather than backing off silently —
`/healthz` reports the same fact as `infrastructure_health_buffer_durable=0`.
