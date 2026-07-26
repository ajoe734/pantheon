# L12-TEL-001 telemetry durability evidence

Status: ready for independent `Codex` review.

This packet proves the `Codex2` telemetry lane removes the
202-before-durable-write loss window, preserves authoritative lifecycle
identity when freshness events arrive, and enforces service/operator tenant
authority on ingest, reads, and DLQ replay.

The implementation is deliberately limited to `services/telemetry`. Runtime
producer credentials and Compose wiring remain owned by `L12-MANIFEST-001`;
hosted deployment admission remains owned by `L12-HOSTED-001`. No live-capital
authority was enabled.

The resumed owner audit added a fail-closed hardening anchor
(`644321d8a`): telemetry no longer inherits runtime-manager auth defaults,
roleless strict JWTs remain unprivileged, service credentials require their own
tenant allowlist, and broker receipt identity is bound to the tenant and
immutable event content rather than only `event_id`.

The machine-readable receipt is [`evidence.json`](evidence.json). The bounded
current-runtime result is
[`current-runtime-readback.json`](current-runtime-readback.json), and
[`evidence.sha256`](evidence.sha256) covers both JSON artifacts.

## Durable acknowledgement

Production defaults to a NATS JetStream work queue configured with file
storage, work-queue retention, discard-new capacity behavior, a durable pull
consumer, and explicit acknowledgement. HTTP ingest returns success only after
JetStream returns a persistence PubAck. The canonical batch writer acknowledges
the broker receipt only after Postgres succeeds, or after a failed event is
fsynced to the replayable DLQ.

`Nats-Msg-Id` is the SHA-256 of the canonical event JSON. Exact retries dedupe,
while a reused event ID with another tenant or payload produces a distinct
durable receipt that reaches canonical conflict handling instead of disappearing
inside the broker duplicate window.

A real `nats:2.11-alpine` probe published event
`evt-crash-3488df2aa9`, fetched it in a child process, and exited that process
before batch flush with `acked=0`. A new consumer recovered the exact event
after the ACK wait and acknowledged it once. The probe result was `PASS`.
The checked-in `RealNatsCrashRecoveryTest` repeated that sequence against the
active NATS 2.11 service after hardening: the child exited with code 23 without
ACK, and the replacement consumer recovered and acknowledged the exact event.

## Authority and tenant scope

Protected routes require a verified JWT or exact internal service credential,
a permitted service/operator/reviewer/admin role as appropriate, and an
authorized `X-Tenant-Id`. Ingest binds the payload to that tenant; exact-event,
runtime-summary, trade-episode, and DLQ reads filter by it; DLQ replay is
operator/admin-only and tenant-filtered. Negative tests cover missing
authority, forbidden tenant scope, payload/header mismatch, cross-tenant
readback, cross-tenant DLQ visibility, and service-token replay denial.
Roleless strict JWTs are forbidden even if the process also carries an
unrelated privileged runtime-manager default. An exact service credential with
no `PANTHEON_TELEMETRY_SERVICE_TENANTS` is also forbidden; the general
interactive-caller tenant allowlist cannot silently widen service authority.

## Lifecycle identity and current runtimes

Lifecycle correlation is retained in `last_lifecycle_identity` and projected
at the consumer-compatible summary top level. Heartbeats advance freshness but
cannot erase or replace that identity.

The task packet referred to six current runtimes. At the bounded nonprod
readback on 2026-07-26, the active local dev stack had expanded to nine.
All nine current summaries and their exact current events were read from the
running telemetry owner—no seed data was used. The installed baseline exposed
complete top-level identity for 0/9. Applying the candidate projection and
reloading its persisted store exposed `tenant_id`, `trace_id`,
`aggregate_type`, `aggregate_id`, `correlation_envelope`, and
`last_lifecycle_identity` for 9/9.

## Validation

```text
PANTHEON_TEST_NATS_URL=nats://127.0.0.1:14222 \
/home/lupin/pantheon/.venv/bin/python -m unittest \
  services.telemetry.test_ingest_shock_absorption \
  services.telemetry.test_l12_tel_001_durable_ingest \
  services.telemetry.test_main_routes \
  services.telemetry.test_runtime_summary_projection

Ran 127 tests in 8.356s
OK

/home/lupin/pantheon/.venv/bin/python services/telemetry/smoke_test_ingest.py
ALL SMOKE TESTS PASSED

python3 -m compileall -q services/telemetry
exit 0

python3 -m json.tool services/telemetry/trade_journal_event.schema.json
exit 0
```

The broader telemetry discovery run completed 260 tests successfully and hit
one unrelated baseline error in `test_lineage_write_path`: that legacy test
constructs `RuntimeManagerClient()` without the now-required explicit
`allow_local=True`. No task code is present in its failing stack.
