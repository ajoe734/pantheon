# L12 BFF repair acceptance evidence

Evidence cut: `2026-07-28T18:27:00Z`.

## Owner verdict

The local implementation acceptance surface passes on current `dev`
`e6f77614d2e68252980e12f6ee4789e4bc8297d1`. That revision contains
L12-BFF-001 PR #4274 merge
`7ba7b5e19fbd16aa36bf569c6a46d244eb9da3e1`.

No additional product-code repair was required in this follow-up. The merged
implementation passes:

- the complete 168-test BFF, telemetry, and incidents acceptance suite;
- nine L12-specific drills for strict infrastructure telemetry admission,
  restart/two-replica dedupe, DLQ replay governance, full registry coverage,
  error-rate triggering, a real local target stop/recovery cycle, durable
  incident mapping, and recovery after retention; and
- five incidents application-route tests for non-trading incident creation,
  idempotency, conflict rejection, fake RuntimeBinding isolation, and canonical
  status-route resolution.

The existing L12-BFF-001 product evidence also passes the repository's
ten-rule validator and its companion checksum.

## Acceptance boundary

This task verifies the merged application implementation and records
reproducible owner evidence. It does not claim that the hosted dev BFF
currently serves the implementation, that protected hosted credentials are
provisioned, or that the program-level hosted restart drill has run. Those
claims remain owned by the designated L12 verifier, manifest, deployment, and
hosted-closeout tasks.

The local real-target drill starts an HTTP service on an ephemeral loopback
port, proves health, stops the service, observes failure and incident-open
delivery, restarts the same endpoint, and proves recovery plus incident
resolution. The strict telemetry drill sends the monitor-built event through
the real telemetry application route and durable admission ledger. The
incidents route drill separately exercises the real incidents application
routes rather than treating mocked delivery as write-owner proof.

## Review and closeout state

Codex2 independently approved the exact task PR head of
[PR #4305](https://github.com/ajoe734/pantheon/pull/4305),
`e603207718d7b463510a7d9f82e35908c2cdbb2b`, through the governed Pantheon
canonical review gate. The canonical review status id is `51237901514`.

Human/Ops then bound the same exact head with root merge freeze status id
`51238030508`, and PR #4305 merged to `dev` as
`8934292632704ed4eb5c942a416a0d09f3e78c06`.

This owner closeout cut records that AC5 is now complete and that the manifest
is ready for the governed `done` transition. It does not recut the hosted
boundary or claim hosted BFF deployment, protected service-JWT provisioning,
retained hosted volume, hosted restart, or program-level twelve-loop
completion.

Machine-readable acceptance mapping, exact commands, delivery identities, and
known limitations are in `evidence.json`. `evidence.sha256` binds this README
and the manifest.
