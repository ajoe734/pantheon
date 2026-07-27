# OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001 evidence

Status: fifth owner cut, ready for independent `Codex2` re-review.

The canonical reviewer for this task is `Codex2`. `Antigravity` was the reviewer
for the first three cuts and rejected two of them; both rejections stay in
`evidence.json` `record_log` (sequences 2 and 4) as the historical review trail,
and the reassignment itself is recorded at sequence 6. `Codex2`'s own
`changes_requested` verdict on the fourth cut is recorded at sequence 8. No
verdict is restated as another reviewer's verdict, and no approval has been
recorded by anyone.

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

The fifth cut repairs the two blockers `Codex2` raised against the fourth. Both
were real and both are fixed in implementation, not in wording:

1. **The RuntimeBinding evidence scan stopped at depth 8.** The schema
   description, the `ingest_infrastructure_health` docstring, and this README all
   said RuntimeBinding evidence is rejected *at any depth*, but
   `_forbidden_binding_fields` recursed with a `depth > 8` cap and returned **no
   findings** past it. A `metadata` `binding_id` nested at depth 10 was therefore
   admitted. `metadata` is `additionalProperties: true` by contract, so the
   standalone schema accepts producer context at arbitrary depth and the ingest
   scan is the *only* gate — a gate that stops looking is not a gate, and a scan
   that cannot see the whole payload must never answer "clean". The scan is now
   iterative over an explicit stack with no depth ceiling, so it cannot exhaust
   the interpreter stack either, and containers are tracked by identity so a
   reused or self-referential payload terminates without hiding a field. Proven
   by seven new tests — five on the scan itself and two over the strict-auth
   HTTP route — a re-executed mutation control that reinstates the cap and fails
   5 of them, and two new runtime probes at depth 12 and depth 40 over real HTTP.

2. **The replica race test conflated the admission owner with post-receipt
   duplicates.** `test_concurrent_replica_processes_admit_exactly_once` asserted
   `outcomes.count("committed") == 1`, but the ledger answers the literal word
   `committed` **both** to the one reservation owner that writes the receipt and
   to a replica that begins after that receipt is already durable. A barrier
   lines replicas up at the start; it cannot stop the OS from scheduling one
   late. Under load the reviewer saw all four replicas report `committed` — one
   owner and three legitimate idempotent duplicates — and the assertion failed on
   correct behaviour. The child process now reports a structured role, the owner
   follows the real ingest ordering (durable enqueue receipt, then commit), and
   the parent binds the single committed ledger receipt to the single owner token
   and asserts exactly one durable broker copy. See
   [§ Replica race stability](#replica-race-stability).

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
  `TelemetryEvent` envelope and requires no RuntimeBinding.

  Every RuntimeBinding evidence field is rejected at any depth, including inside
  `metadata`, so a probe producer cannot present, invent, or spoof trading
  binding identity. The enforcement point is the ingest scan, not the schema:
  `metadata` is deliberately `additionalProperties: true` so a producer can
  attach arbitrary context, which means the standalone schema accepts a
  `binding_id` nested inside it and `TelemetryIngestService` is what refuses the
  event. "Any depth" is therefore a property of that scan, and it is literal —
  the traversal is iterative with no depth ceiling. It was not literal in the
  fourth cut, which is what `Codex2` blocker 1 found.

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

<a id="replica-race-stability"></a>

## Replica race stability

Four real replica **processes** contend for one `event_id` over one ledger and
one durable broker log. Exactly one of them owns the admission; the others must
be told to retry or told duplicate, and never told they succeeded.

The word the ledger returns is not sufficient to identify the owner.
`begin()` answers `committed` to a caller that arrives after the receipt is
already durable, and `commit()` answers `committed` to the owner that just wrote
it. Both are truthful. Which one a given replica sees depends on OS scheduling,
which a barrier cannot control — it releases the replicas together, it does not
keep them together. The fourth cut asserted on the word and so failed under load
on correct behaviour: the reviewer's run reported four `committed` outcomes,
which was one owner and three idempotent duplicates.

What is asserted now, per round:

- exactly one replica reaches the `commit_owner` role — it reserved, obtained a
  durable enqueue receipt, and then committed;
- the single `committed` ledger record's owner token equals that replica's token,
  so the one receipt provably belongs to the one owner;
- exactly one copy of the event exists in the durable broker log, because a
  losing replica never enqueues;
- every other replica is either `in_flight` (it met a live reservation) or
  `post_receipt_duplicate` (it began after the receipt was durable), and nothing
  else.

The race repeats over eight independent event IDs in one test, so a single
favourable interleaving cannot stand in for stability. Because the
`post_receipt_duplicate` interleaving is load-dependent and did not occur on the
validation host at all, it is covered by a separate **staged** test that forks a
first replica to completion and then three more, making the interleaving certain
rather than hoped for. Stability numbers are recorded in `evidence.json` under
`validation.commands`: ten isolated repeats and an eight-way concurrent run,
which together exercise 88 four-process races with no failure.

## Product evidence admission

[`evidence.json`](evidence.json) is a schema-valid `ProductEvidenceManifest` for
`schemas/product-evidence.schema.json`. Its exact repository-relative
`task.review_file` is
`docs/deployment/evidence/twelve-loop-gap/OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001/evidence.json`,
and `overall_admission=pass_owner_evidence_ready` means only that the owner proof
is complete enough to enter independent review. **It does not assert reviewer
approval.**

`record_log` contains three `changes_requested` decisions — `Antigravity` on the
first and second cuts, `Codex2` on the fourth — but **no approval**, and `AC6`
remains `pending_reviewer`. `AC6` is judged against `Codex2`, the canonical
reviewer on the task row; the canonical acceptance row still reads `Antigravity`
because it was written before the reassignment, and that is recorded in `AC6`
`blocking_until` rather than silently rewritten. After a governed `Codex2`
approval, owner closeout must append the actual verdict to `record_log`, record
the merged PR number, merge sha, and merge time, refresh this README and the
companion checksum, merge that update through the task PR, and only then run
`done`.

<a id="head-binding"></a>

### Head binding

`validation.validated_head_sha` is `a4f9083df`, the merge of `dev` `643181a06`
into the task branch. PR #4211 was `BEHIND` again when this cut started — the
fourth cut's head `cca84df53` was 14 commits behind `dev` — so the branch was
brought forward before anything was validated, and every validation command in
`validation.commands` ran at `a4f9083df`.

The implementation change of this cut is carried by anchor commit `7537f2b4c`,
`OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001: anchor depth and race fix`, which
touches exactly two files: `services/telemetry/ingest_svc.py` and
`services/telemetry/test_infrastructure_health_ingest.py`. The `dev` merge
changed no implementation byte — all five digests under
`integrity.source_artifact_sha256_by_epoch.implementation_files_at_validated_head`
are identical at `7537f2b4c` and at `a4f9083df`.

The commits that carry this manifest land after the validated head, and the last
of them becomes the final PR head, so their own `Branch CI Gate` runs cannot be
named inside the manifest they create. Those runs are the branch-protection
merge gate and are visible on PR #4211. What makes the binding hold anyway is
that they touch documentation and the task brief only:
`integrity.source_artifact_sha256_by_epoch.implementation_files_at_validated_head`
records the `sha256` of each of the five implementation files at the validated
head, and re-running `git show <final-head>:<path> | sha256sum` must reproduce
them exactly.

**Nothing is carried forward from an earlier head in this cut.** The fourth cut
reused the runtime readback and the three durability mutation controls taken at
`28b13a16d`, justified by byte-identical implementation files. That
justification no longer holds, because `ingest_svc.py` changed. So the runtime
readback was **re-executed** against the running service at the new bytes, and
the three durability mutation controls were **re-executed** as well; each
reproduced its previously recorded conclusion. The readback harness is now
committed as [`readback_probe.py`](readback_probe.py) so the reviewer can rerun
it instead of trusting a transcript.

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
  | **`binding_id` nested at depth 12 inside `metadata`** | **`400 INFRA_BINDING_FIELD_FORBIDDEN`** |
  | **`runtime_id` at depth 40 through alternating lists and objects** | **`400 INFRA_BINDING_FIELD_FORBIDDEN`** |
  | **clean `metadata` nested 40 levels deep, no binding fields** | **`202` accepted — depth itself is not the gate** |
  | `runtime_health` probe shape on the trading route | `400 rejected` |
  | infrastructure event on the trading route | `400 rejected` |
  | durable ledger after all of the above | `3` committed receipts, `0` orphan reservations |
  | **second service booted with `TELEMETRY_BUFFER_BACKEND=memory`** | **`503 INFRA_BUFFER_NOT_DURABLE`, `0` ledger records, `buffer_durable=0`** |

- [`readback_probe.py`](readback_probe.py) — the harness that produces the table
  above, committed with the evidence so the readback is reproducible rather than
  an unverifiable transcript. Run it from the repository root against a
  JetStream server; it boots both services, drives every probe over real HTTP,
  `SIGKILL`s the durable one, restarts it, and writes
  `current-runtime-readback.json`.

- [`services/telemetry/test_infrastructure_health_ingest.py`](../../../../../services/telemetry/test_infrastructure_health_ingest.py)
  — 52 real strict-auth route, binding-scan, replica, durability-authority, and
  crash-matrix tests. Beyond the strict-auth and two-phase coverage of the previous cut, the
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

  The eight tests added by this cut are the two blocker repairs. Five cover the
  binding scan: the field is found just past the old depth-8 cap, at 5000 levels
  of nesting where recursion could not reach at all, across mixed
  dict/list/tuple containers, and in a self-referential payload that must
  terminate without hiding a field — plus a clean payload that must still report
  clean. Two drive the same escape over real HTTP through the strict-auth route,
  one of them first asserting that the standalone schema *accepts* the payload,
  so the test cannot pass for the wrong reason if the scan regresses again. The
  eighth is the staged post-receipt replica described in
  [§ Replica race stability](#replica-race-stability).

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

    These three were re-executed at the validated head of this cut rather than
    carried forward, because `ingest_svc.py` changed. Each reproduced its
    recorded conclusion exactly: 5, 1, and 8 failures.

  - this cut's two repairs — **reinstating the depth-8 cap** exactly as the
    fourth cut had it failed 5 tests, including both HTTP-level deep spoofs and
    the extreme-depth scan; **reinstating the owner/duplicate conflation** in the
    replica child, so a post-receipt duplicate reports itself as the commit
    owner, failed the staged post-receipt test; and letting a losing replica
    enqueue its own durable copy also failed it, so the one-durable-admission
    assertion is load-bearing rather than incidentally true.

  The suite returned to green after each restore, so the repair is proven by
  tests that fail without it rather than by assertions that were already true.

Exact commands and their conclusions are recorded in `evidence.json` under
`validation.commands`. On the validated head the full telemetry suite is clean —
`348 passed, 1 skipped, 35 subtests passed`, no failures. The count rose from the
fourth cut's `335` because this cut adds 8 tests and the `dev` merge brought in
`services/telemetry/test_discovery_imports.py` from
`OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001`.

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
