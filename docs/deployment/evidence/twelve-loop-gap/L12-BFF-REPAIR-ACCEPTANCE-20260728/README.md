# L12 BFF repair acceptance evidence

Evidence cut: `2026-07-28T16:33:17Z`.

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

## Review state

Owner proof is complete. Codex2 must independently review the exact task PR
head of [PR #4305](https://github.com/ajoe734/pantheon/pull/4305) and bind
this manifest through the governed review transition. The owner evidence
commit is `0701c124f167c97f1b52177f97611e916fab17a3`. The PR is open with
auto-merge disabled by the review-before-merge policy. An open PR, green CI,
or the historical review of PR #4274 does not approve this follow-up evidence
cut.

Machine-readable acceptance mapping, exact commands, delivery identities, and
known limitations are in `evidence.json`. `evidence.sha256` binds this README
and the manifest.
