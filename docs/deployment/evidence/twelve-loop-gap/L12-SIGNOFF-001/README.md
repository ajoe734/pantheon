# L12-SIGNOFF-001 protected closeout evidence

Status: owner evidence ready for independent `Codex2` review.

This packet proves that protected product closeout decisions are signed by a
configured Human/Ops authority, recorded in a lock-protected append-only
ledger, bound to the exact catalog/task/manifest/target/frontend/backend
identities, and consumed at the `review_approved` and `done` transition
boundaries.

No Human/Ops verdict was issued by this worker. No private key, live policy,
key identifier, nonce ledger, or deployment credential is checked into the
repository. The implementation fails closed until an external protected
policy is provisioned by the owning deployment lane.

## Product evidence admission

[`evidence.json`](evidence.json) is a schema-valid
`ProductEvidenceManifest` for
`schemas/product-evidence.schema.json`. Its
`overall_admission=pass_owner_evidence_ready` means the implementation and
owner proof are ready for independent review; it does not assert that Codex2
has approved the task. The formal reviewer verdict must be appended to
`record_log` before governed closeout can succeed.

The implementation is intentionally limited to the verdict governance
contract, the BFF authentication boundary, and transition-time status guards.
Mounting the BFF router in the application, provisioning the external
Human/Ops verification policy and ledger, and deploying those settings remain
with `L12-BFF-001` and `L12-MANIFEST-001`. `L12-CLOSE-001` consumes this
guard before program closure.

## Authority and binding

The BFF boundary accepts only authenticated JWT or JWT-backed cookie sessions,
requires an allowed Human/Ops role and verified MFA, and rejects fleet actor
identities even when a caller claims an authorized role. The governance
service independently repeats actor-class, role, MFA, signature, key,
catalog-digest, task, manifest, target-environment, frontend-SHA, backend-SHA,
freshness, expiry, revocation, and nonce checks.

Candidate-controlled environment variables cannot replace the protected
policy. A direct policy path exists only as an explicit library argument for
isolated unit tests and is not exposed by `ai_status`.

## Replay, recovery, and transition enforcement

Issuance and consumption use a regular-file, no-symlink, lock-protected,
fsynced JSONL ledger. Concurrent decisions for one task admit one verdict and
reject competitors. Consumption is exactly once for a logical transition,
with deterministic idempotent retry for the same operation; another attempt is
replay. Revocation is allowed only before consumption.

The guard checks the authoritative ledger record and its ID, digest, and
idempotency key instead of trusting task metadata. Missing, rejected, revoked,
expired, stale, tampered, mismatched, unconsumed, or replayed verdicts block
protected transitions. Alternate lifecycle paths (`approve`,
`restore_approved`, `done`, and merged-done reconciliation) all pass through
the same enforcement.

A later valid verdict may replace an expired or revoked task reference. Once a
verdict is consumed, its completion evidence remains auditable after the
issuance TTL by evaluating it at the recorded consumption time.

## Validation

The post-merge focused run completed:

```text
/home/lupin/pantheon/.venv/bin/pytest -q \
  services/control-plane/governance/test_product_closeout_verdict.py \
  services/control-plane/bff/test_product_closeout_verdict.py \
  scripts/test_loop_done_guardrail.py \
  scripts/test_ai_status.py

236 passed, 1 warning, 23 subtests
```

The broader `scripts/run-acceptance.sh smoke` run passed stage-0 validation
and the complete smoke baseline, including data-plane, promotion,
governance-saga, runtime-binding, telemetry, lineage, and BFF checks.
`py_compile` and `git diff --check` also passed.

[`evidence.sha256`](evidence.sha256) covers the machine-readable manifest.
