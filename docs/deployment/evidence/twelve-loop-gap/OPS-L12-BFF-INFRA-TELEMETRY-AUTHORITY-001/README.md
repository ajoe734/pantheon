# OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001 evidence

Status: owner evidence ready for independent `Antigravity` review.

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

- **Durable idempotent admission.** Admission is keyed by the producer's stable
  `event_id` plus a sha256 fingerprint of the canonical event, recorded in an
  append-only ledger guarded by a POSIX advisory lock. Retries, restarts, and
  replicas that share the storage volume admit an event exactly once; reusing an
  `event_id` for different content returns `409 INFRA_EVENT_ID_CONFLICT`. A
  reservation whose durable enqueue fails is released so the producer's retry
  still works.

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

`record_log` deliberately contains no reviewer verdict, and `AC6` remains
`pending_reviewer`. After a governed `Antigravity` approval, owner closeout must
append the actual verdict to `record_log`, populate
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
  | replay after a real service restart | `202`, `duplicate=true`, zero new admissions |
  | event ID reused with different content | `409 INFRA_EVENT_ID_CONFLICT` |
  | missing token | `401` |
  | wrong tenant | `403 TENANT_FORBIDDEN` |
  | wrong producer | `403 PRODUCER_FORBIDDEN` |
  | spoofed `binding_id` on the infra route | `400 INFRA_BINDING_FIELD_FORBIDDEN` |
  | `runtime_health` probe shape on the trading route | `400 rejected` |
  | infrastructure event on the trading route | `400 rejected` |
  | durable ledger after all of the above | exactly `1` admitted record |

- [`services/telemetry/test_infrastructure_health_ingest.py`](../../../../../services/telemetry/test_infrastructure_health_ingest.py)
  — 29 real strict-auth route and replica tests, including four concurrent
  replica **processes** racing on one event ID through a shared ledger, which
  admit it exactly once.

- Mutation controls: removing the strict-mode pinning, short-circuiting the
  ledger admission, and replacing the producer-scope intersection with a union
  each failed the intended tests (1, 6, and 2 respectively) and the suite
  returned to green once the implementation was restored, so these assertions
  are load-bearing rather than incidentally passing.

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
