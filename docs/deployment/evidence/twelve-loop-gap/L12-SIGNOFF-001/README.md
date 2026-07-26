# L12-SIGNOFF-001 protected closeout evidence

Status: owner evidence re-cut after the independent re-review rejected the
merged PR #4183 delivery, ready for independent `Codex2` review of the
follow-up PR.

Owner is `Claude` and reviewer is `Codex2`, matching both the
`assignment-revision-1` catalog and the canonical `ai-status.json` assignment.
The earlier cut recorded owner `Codex`, which predated the operator-directed
assignment revision.

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

## Review remediation history

### Sequence-2 rejection (closed in the sequence-3 cut, merged as PR #4183)

The first independent review rejected the sequence-1 cut. Both findings were
closed, and each was first reproduced against the rejected head `4731eb2c`
before being shown closed:

1. **Competing decisions for one binding.** `issue` previously conflicted only
   on `verdict_id` and `nonce`, so two concurrent decisions with distinct IDs
   and distinct nonces — including one `approved` and one `rejected` — both
   committed for the same exact binding. A probe against `4731eb2c` returned
   `issued_records=2`. Issuance now also refuses a second decision while an
   active one exists for the same seven-field binding, evaluated under the
   exclusive ledger lock; the same probe returns `issued_records=1`.
2. **Fail-open principal classification.** The BFF defaulted a session with no
   `principal_type`/`actor_type` claim to `human`, so an MFA-verified JWT admin
   with no classification claim could issue a verdict. Classification is no
   longer inferred: missing, blank, unknown, and self-conflicting claims are
   all refused, and only an explicit trusted human classification passes.

A third point — the evidence owner not matching the canonical assignment — was
reconciled above.

### Sequence-4 rejection (closed in this cut)

The independent re-review rejected the merged PR #4183 delivery on a single
blocking split-root defect. `_load_product_closeout_binding` resolved
`review_file` only through the status root, so a governed reviewer executing
from the immutable command root — with the reviewed manifest in the
supervisor-bound task worktree and not yet merged into the central status root
— was refused with `closeout review_file is missing or not regular` even while
holding a valid Human/Ops-signed verdict. The guard therefore failed exactly
the dispatch shape it exists to protect.

`_safe_protected_closeout_artifact` now resolves the manifest across the bound
task worktree, the command root, and the status root, in that order, reusing
the same `_review_workspace_roots` binding validation as the generic review
path. Containment and per-component symlink rejection are unchanged per root;
`_safe_rooted_artifact` reports absence rather than raising, so a missing
candidate defers to the next trusted root while an untrustworthy one still
fails closed. The status-root-only helper was deleted rather than left behind
as a reintroducible call site.

Search order is not a trust decision. The resolved manifest bytes are hashed
into `closeout_manifest_sha256`, which the Human/Ops signature covers, so a
worker-authored worktree copy that was not signed over fails the binding check
instead of being admitted.

Because PR #4183 is already merged and cannot be amended, this remediation is
delivered as a follow-up PR against the same task branch.

## Authority and binding

The BFF boundary accepts only authenticated JWT or JWT-backed cookie sessions,
requires an allowed Human/Ops role and verified MFA, requires an explicit
trusted human principal classification, and rejects fleet actor identities even
when a caller claims an authorized role. The governance
service independently repeats actor-class, role, MFA, signature, key,
catalog-digest, task, manifest, target-environment, frontend-SHA, backend-SHA,
freshness, expiry, revocation, and nonce checks.

Candidate-controlled environment variables cannot replace the protected
policy. A direct policy path exists only as an explicit library argument for
isolated unit tests and is not exposed by `ai_status`.

## Replay, recovery, and transition enforcement

Issuance and consumption use a regular-file, no-symlink, lock-protected,
fsynced JSONL ledger. Concurrent decisions for one exact binding admit one
verdict and reject competitors, whether or not the competitors agree and
whether or not they use distinct verdict IDs and nonces. Consumption is exactly
once for a logical transition,
with deterministic idempotent retry for the same operation; another attempt is
replay. Revocation is allowed only before consumption.

The guard checks the authoritative ledger record and its ID, digest, and
idempotency key instead of trusting task metadata. Missing, rejected, revoked,
expired, stale, tampered, mismatched, unconsumed, or replayed verdicts block
protected transitions. Alternate lifecycle paths (`approve`,
`restore_approved`, `done`, and merged-done reconciliation) all pass through
the same enforcement.

A decision leaves the active set only through explicit revocation or its own
expiry, so a later valid verdict may replace an expired or revoked task
reference and nothing else. Once a
verdict is consumed, its completion evidence remains auditable after the
issuance TTL by evaluating it at the recorded consumption time.

## Validation

The focused run at this head completed:

```text
/home/lupin/pantheon/.venv/bin/pytest -q \
  services/control-plane/governance/test_product_closeout_verdict.py \
  services/control-plane/bff/test_product_closeout_verdict.py \
  scripts/test_loop_done_guardrail.py \
  scripts/test_ai_status.py

269 passed, 1 warning, 23 subtests
```

The count rose from 260 in the sequence-3 cut by the nine split-root
regressions added here: seven guardrail resolution cases and two end-to-end
`ai_status` `command_approve` cases.

Those regressions were replayed against the pre-fix `origin/dev`
(`403e30bd985ea9b0c166180103a0ab64e4e35d4f`) in a detached worktree with only
the two new test files copied in:

```text
9 failed, 198 passed, 23 subtests passed
```

Eight are the new split-root behaviours; the ninth is the pre-existing
parent-traversal test, which fails there only because it now calls the renamed
resolver. The end-to-end case fails with exactly the reported reviewer defect:

```text
SystemExit: Protected Human/Ops verdict rejected for L12-CLOSE-001
review_approved transition: RuntimeError: closeout review_file is missing or
not regular: docs/evidence/closeout.json
```

`scripts/loop_done_guardrail.py --evidence-root` replay of this manifest
reports exactly one gap — the absent formal `Codex2` verdict — confirming the
cut is otherwise `done`-eligible.

The broader `scripts/run-acceptance.sh smoke` run recorded in the sequence-3
cut passed stage-0 validation and the complete smoke baseline, including
data-plane, promotion, governance-saga, runtime-binding, telemetry, lineage,
and BFF checks; this cut does not re-assert it at the new head.
`py_compile` and `git diff --check` were re-run here and passed.

[`evidence.sha256`](evidence.sha256) covers the machine-readable manifest.
