# OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001 evidence

Status: second owner cut, ready for independent `Antigravity` re-review.

`Antigravity` rejected the first cut (head `796c6e5e3`) for a real defect, and
the rejection is recorded in `evidence.json` `record_log` sequence 2. The
single-phase admission ledger had a loss window: a second caller arriving
between the durable `admitted` record and the durable enqueue was answered as a
successful duplicate although nothing had been persisted, and the first caller
could still fail and release the reservation. A crash in the same window left an
event_id permanently marked admitted and permanently never enqueued. See
[§ Durable idempotent admission](#durable-idempotent-admission) for the repair
and the tests that fail without it.

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

- **Trading validation untouched.** The trading ingest path keeps evidence
  contract E-1 through E-6 and its authoritative RuntimeBinding cross-validation
  exactly as they were, and now also refuses `infrastructure_health` outright so
  the two authorities cannot be confused.

## Fail-closed defaults

`PANTHEON_TELEMETRY_INFRA_PRODUCERS` is empty by default, and an unconfigured or
wildcard allowlist is refused, so this route admits nothing until an operator
explicitly admits a producer. If the authoritative schema or the durable ledger
is unavailable, ingestion refuses the event rather than admitting something it
cannot deduplicate.

## Product evidence admission

[`evidence.json`](evidence.json) is a schema-valid `ProductEvidenceManifest` for
`schemas/product-evidence.schema.json`. Its exact repository-relative
`task.review_file` is
`docs/deployment/evidence/twelve-loop-gap/OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001/evidence.json`,
and `overall_admission=pass_owner_evidence_ready` means only that the owner proof
is complete enough to enter independent review. **It does not assert reviewer
approval.**

`record_log` contains the `Antigravity` `changes_requested` decision on the first
cut but **no approval**, and `AC6` remains `pending_reviewer`. After a governed
`Antigravity` approval, owner closeout must append the actual verdict to
`record_log`, populate
`implementation_delivery.required_checks` and the merged PR record, refresh this
README and the companion checksum, merge that update through the task PR, and
only then run `done`.

## Proof

- [`current-runtime-readback.json`](current-runtime-readback.json) — a bounded
  local nonprod readback against the **running** Flask telemetry service over
  real HTTP on `127.0.0.1`, with `PANTHEON_RUNTIME_MANAGER_URL` pointed at an
  unreachable address so authoritative binding lookups fail closed:

  | observation | result |
  | --- | --- |
  | valid strict-auth ingest | `202`, `duplicate=false` |
  | retry with the same stable event ID | `202`, `duplicate=true` |
  | **concurrent attempt while the owner is inside its durable enqueue** | **`503 INFRA_ADMISSION_IN_FLIGHT`** |
  | same attempt replayed after the owner's receipt committed | `202`, `duplicate=true` |
  | **reservation stranded as if by a crash, lease still live** | **`503 INFRA_ADMISSION_IN_FLIGHT`** |
  | **same event once the lease expired** | **`202` accepted, receipt committed** |
  | fenced owner trying to commit after takeover | refused unless the receipt already exists |
  | replay after a real service restart | `202`, `duplicate=true`, zero new admissions |
  | event ID reused with different content | `409 INFRA_EVENT_ID_CONFLICT` |
  | missing token | `401` |
  | wrong tenant | `403 TENANT_FORBIDDEN` |
  | wrong producer | `403 PRODUCER_FORBIDDEN` |
  | spoofed `binding_id` on the infra route | `400 INFRA_BINDING_FIELD_FORBIDDEN` |
  | `runtime_health` probe shape on the trading route | `400 rejected` |
  | infrastructure event on the trading route | `400 rejected` |
  | durable ledger after all of the above | `3` committed receipts, `0` orphan reservations |

- [`services/telemetry/test_infrastructure_health_ingest.py`](../../../../../services/telemetry/test_infrastructure_health_ingest.py)
  — 33 real strict-auth route and replica tests, including four concurrent
  replica **processes** racing on one event ID through a shared ledger where
  exactly one commits and every loser is told to retry, and a real service
  instance **SIGKILLed inside its durable enqueue** whose stranded claim is
  refused while its lease is live and recovered once it expires.

- Mutation controls, run in two groups:

  - strict-auth gates — removing the strict-mode pinning, short-circuiting the
    ledger admission, and replacing the producer-scope intersection with a union
    failed 1, 6, and 2 tests respectively;
  - two-phase reservation — **reinstating the reviewed bug** (answering a live
    reservation as committed) failed 3 tests, including
    `test_concurrent_admission_loser_is_not_told_it_succeeded`; removing the
    commit token fencing failed 1; making reservations non-expiring failed 2;
    removing the release-on-cancellation path failed 1.

  The suite returned to green after each restore, so the repair is proven by
  tests that fail without it rather than by assertions that were already true.

Exact commands and their conclusions, including the two pre-existing baseline
residuals that reproduce identically on the unmodified tree, are recorded in
`evidence.json` under `validation.commands`.

## Composition

`L12-BFF-001` must drop its out-of-scope edits to `services/telemetry` and its
shape-based binding bypass, and become a strict-auth client of this route:
obtain a service JWT whose claims bind its tenant and name it as a producer, get
that producer admitted to `PANTHEON_TELEMETRY_INFRA_PRODUCERS`, and derive a
stable `event_id` from the observation identity (producer, component, probe
window) rather than generating one per attempt or per replica.

Client contract for the BFF: treat `202` as delivered, `409` as a producer bug in
`event_id` derivation, and every `503` — including `INFRA_ADMISSION_IN_FLIGHT`
and `INFRA_ADMISSION_FENCED` — as **retry with the same `event_id`**. A `503`
means no durable receipt exists yet, so retrying is what makes delivery
at-least-once, and the ledger keeps it exactly-once.
