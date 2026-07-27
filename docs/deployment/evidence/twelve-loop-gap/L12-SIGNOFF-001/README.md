# L12-SIGNOFF-001 protected closeout evidence

Status: owner evidence sequence 16 revalidated after strict branch protection
refused the sequence-12 reviewed head and later non-overlapping supervisor and
release evidence PRs advanced `dev`, ready for a fresh independent `Codex2`
review of the new immutable head.

Owner is `Codex` and reviewer is `Codex2`, matching the current canonical task
row and supervisor dispatch. Historical owner and reviewer records remain
append-only in `record_log`; sequence 16 is the current owner admission.

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
has approved the current head. Sequence 12 records the prior exact-head
approval as immutable history, but strict branch protection refused that stale
base after `dev` advanced. A fresh formal reviewer verdict must be appended to
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

### Sequence-4 rejection (closed in the sequence-5 cut, merged as PR #4205)

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

Because PR #4183 was already merged and could not be amended, that remediation
was delivered as follow-up PR #4205 against the same task branch.

### Sequence-6 rejection (closed in the sequence-7 cut, merged as PR #4206)

The independent re-review rejected the merged PR #4205 delivery on a single
blocking authority-boundary defect. Manifest resolution had been taught about
the split-root dispatch, but the verifier's own trust boundary had not:
`validate_protected_closeout_transition` passed only the immutable command root
and `PANTHEON_STATUS_ROOT` to `load_verifier_service` as `forbidden_roots`, and
omitted the supervisor-bound `PANTHEON_WORKTREE_ROOT` /
`ORCH_WORKSPACE_PATH` — the one root the candidate demonstrably writes.

The consequence is not theoretical. The reviewer's temp probe accepted an
external protected policy whose `ledger_path` pointed inside the task worktree.
Revocation of an issued verdict correctly failed verification; truncating the
candidate-owned JSONL ledger back to the signed issue record then restored
approval verification. The ledger is a hash chain, so any prefix of it is still
internally consistent: signature and chain checks cannot see a suffix that was
deleted. Location, not cryptography, is what keeps a revocation or a consumption
record durable.

`_protected_forbidden_roots` now derives that boundary from the same validated
split-root bindings that resolve the manifest — the bound task worktree, the
command root, and the status root — and keeps both the literal and the
symlink-resolved spelling of each, since containment is checked without
resolving the protected path. `_bound_workspace_roots` was factored out so the
boundary and the search path cannot drift apart, and the boundary is computed
before any manifest, policy, or ledger access, so a relative or conflicting
binding fails closed instead of quietly narrowing what counts as
candidate-controlled.

Because PR #4205 was already merged and could not be amended, that remediation
was delivered as follow-up PR #4206 against the same task branch.

### Sequence-8 rejection (closed in the sequence-9 cut, merged as PR #4210)

The independent re-review rejected the merged PR #4206 delivery on a single
blocking authority-source defect. The forbidden-root set had been widened to
cover the task worktree, but the answer to *which directory is the task
worktree* was still being read from `PANTHEON_WORKTREE_ROOT` /
`ORCH_WORKSPACE_PATH` — variables that live in the candidate's own environment.

A worker can simply unset both. `validate_active_status_command_lease` treated
the workspace binding as optional, so with a current valid `ORCH_RUN_ID` an
erased environment still passed command-runtime, status-root, and active-lease
validation, and `_bound_workspace_roots` then returned nothing. The worktree
dropped out of `forbidden_roots` and the sequence-6 bypass came back intact:
the reviewer's replay accepted an external policy whose ledger lived under that
omitted worktree, and truncating the candidate-owned JSONL tail to the signed
issue record restored `review_approved` verification. Widening a boundary is
worth nothing if the boundary's own coordinates are candidate-supplied.

The authority now comes from central runtime state, which the supervisor writes
outside every task worktree and the candidate cannot reach.
`ai_status.active_lease_workspace_roots` reads the leased worktree from the
worker record's `workspace_path` and from the `worker_worktrees` lease `path`,
and `_bound_workspace_roots` merges those roots into whatever the environment
claims. The union is the safe direction for both consumers: a wider forbidden
set only rejects more, and a wider manifest search path is still sha256-bound
to the signed verdict. An active run whose lease cannot be resolved raises
before any manifest, policy, or ledger access rather than degrading to no
boundary.

The governed command fails closed on the same erasure. When the lease declares
a worktree, `validate_active_status_command_lease` now requires a matching
environment binding and rejects a missing or blank one, and the
`worker_worktrees` lease `path` enforces that independently of the worker
record's `workspace_path`.

This was reproduced on the live dispatch lease, not only on a fixture. Running
the validator with both workspace variables erased under the real
`ORCH_RUN_ID` was **accepted** against the merged command root and **rejected**
against this head, where the leased worktree was recovered from runtime state
with the environment still erased.

Because PR #4206 was already merged and could not be amended, this remediation
was delivered and merged as follow-up PR #4210 against the same task branch.

### Sequence-10 owner revalidation

After supervisor recovery reassigned the active owner from `Claude` to `Codex`,
the owner evidence was replayed on current `origin/dev`
`6ae436c546942df1ba0a762d7167b456dfedabc8`. The protected-verdict source is
unchanged except for later compatible `ai_status` and test additions already
merged on `dev`; the four focused suites now pass with 294 tests and 39
subtests. A read-only live-lease probe with both workspace variables erased
recovered this task worktree from supervisor runtime state and rejected the
mutation as missing its workspace binding. The current source hashes, merged
PR #4210 receipt, task ownership, and task packet were re-cut here. This remains
owner evidence, not an inherited or self-issued reviewer verdict.

### Sequence-11 exact-head revalidation

Codex2's security review of sequence 10 otherwise passed, but correctly
rejected approval because PR #4261 moved from the dispatched exact head after a
post-handoff `origin/dev` merge. The owner preserved that finding in an anchor,
composed `origin/dev` `4974824687ef5c3acf665fa22a4306e5d3d664f1`,
refreshed the two compatible `ai_status` source hashes, and re-ran the four
focused suites at 296 tests and 39 subtests. Sequence 11 is the final owner
admission before a new exact-head handoff; it carries no inherited or
self-issued reviewer verdict.

### Sequence-12 approval and sequences 13-16 stale-base revalidation

Codex2 independently approved the sequence-11 owner cut and committed that
formal verdict as sequence 12. Before PR #4261 could merge, the independently
reviewed supervisor PR #4257 landed on `dev` as
`4580fc5d19b5bff8c0014006324c56d6368ec5dc`. GitHub then reported PR #4261
`BEHIND`, and strict branch protection refused to merge the reviewed head.
The owner did not use admin override, auto-merge, or the stale review binding.

Sequence 13 preserves the sequence-12 verdict as history, composes the new
`dev` tip normally, and re-runs the four focused suites at 296 tests and 39
subtests. The nine protected-verdict source artifacts and canonical catalog
digest are byte-identical to sequence 11; the new base adds only task-scoped
supervisor refresh evidence.

Before sequence 13 was pushed, the final pre-push ancestry gate caught one
more `dev` advance: L12-IMIT closeout reconciliation PR #4265 merged as
`4688bd252911b91ea0459a38a694c5faa53e3bbd`. Its two added files do not overlap
this task or the protected-verdict sources. Sequence 14 composes that tip and
repeats the 296-test, 39-subtest matrix. Sequence 14 is the current owner
admission at that cut.

After sequence 14 was pushed, supervisor task-state lock latency PR #4266
advanced `dev` to `a6966b13d84430387da9c3a33fcf224c841bc5c6`. Its three
changed files are confined to the `SUP-TASK-STATE-LOCK-LATENCY-001` task brief
and evidence packet; they do not overlap this task or the protected-verdict
sources. Sequence 15 composes that tip and repeats the 296-test, 39-subtest
matrix. Sequence 15 is the owner admission at that cut.

After sequence 15 passed eight fresh CI checks, cross-repository release
controller PR #4268 advanced `dev` to
`b854c2bdeba672d107314c51c7588455be96221e`. Its 13 changed files are confined
to release workflow, controller, deployment documentation, tests, and its own
task evidence; they do not overlap this task or the protected-verdict sources.
Sequence 16 composes that tip and repeats the 296-test, 39-subtest matrix.
Sequence 16 is the current owner admission and requires a fresh Codex2 verdict
bound to the new PR head.

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

The focused run on the current dev integration completed:

```text
/home/lupin/pantheon/.venv/bin/pytest -q \
  services/control-plane/governance/test_product_closeout_verdict.py \
  services/control-plane/bff/test_product_closeout_verdict.py \
  scripts/test_loop_done_guardrail.py \
  scripts/test_ai_status.py

296 passed, 1 warning, 39 subtests
```

The sequence-9 count rose from 275 in the sequence-7 cut by nine
lease-authority regressions: six in `scripts/test_loop_done_guardrail.py` — the
leased root appearing in `forbidden_roots` with no environment binding, ledger
and policy under the leased root, revoked and consumed tail truncation against
the leased root, and the unresolvable-lease fail-closed ordering — and three in
`scripts/test_ai_status.py`, one of which contributes the two new subtests for
missing and blank workspace variables under a valid run lease, plus the
worktree-lease-only authority case and `active_lease_workspace_roots` surviving
an erased environment.

Those regressions were replayed against the pre-fix `origin/dev`
(`5b8addf628c09f88ff8199080ab68429dbe7b531`, which contains the merge of
PR #4206) in a detached worktree with only the two new test files copied in:

```text
9 failed, 214 passed, 31 subtests passed
```

plus both subtests of the erased-binding case reported as `SUBFAILED`. Every
failure is a lease-authority case, and all nine pass at this head, so the
regressions bind the fix rather than restating it.

The live probe on the real dispatch lease, with both workspace variables
erased under the current `ORCH_RUN_ID`, was accepted against the merged command
root and rejected at this head with `status command workspace binding is
required`; the same run recovered the leased worktree from supervisor runtime
state. The probe only validates and mutates no canonical state.

The sequence-4 replay against `403e30bd985ea9b0c166180103a0ab64e4e35d4f`
(`9 failed, 198 passed`) and the sequence-6 replay against
`d54e1510ae64f4e8b0c3a5e20058a9ec93270939` (`8 failed, 82 passed`, including
the `candidate_tail_truncation_restores_approval=true` assertion reproduced
verbatim) are retained in `evidence.json` as the proofs for those earlier
rejections.

At sequence 11,
`scripts/loop_done_guardrail.py --task-id L12-SIGNOFF-001 --evidence-root .`
reported exactly one gap: the then-absent formal `Codex2` verdict. The
sequence-16 target row now passes evidence truth replay because sequence 12
remains append-only review history. That replay does not authorize reuse of
the stale PR-head binding; the canonical GitHub review gate still requires a
fresh Codex2 verdict for the new exact head.

`scripts/run-acceptance.sh smoke`, `py_compile`, and `git diff --check` were
all re-run at the sequence-9 head and passed; the smoke run completed stage-0
validation and the full smoke baseline. Sequence 10 then re-ran the four
focused suites after merging current `origin/dev`; 294 tests and 39 subtests
passed. Sequence 11 re-ran them after its pre-review `dev` composition; 296
tests and 39 subtests passed. Sequences 13 through 16 repeated the same
296-test, 39-subtest matrix after composing `dev` at
`4580fc5d19b5bff8c0014006324c56d6368ec5dc`,
`4688bd252911b91ea0459a38a694c5faa53e3bbd`, and
`a6966b13d84430387da9c3a33fcf224c841bc5c6`, followed by
`b854c2bdeba672d107314c51c7588455be96221e`; all nine
protected-verdict source hashes remained unchanged.

[`evidence.sha256`](evidence.sha256) covers the machine-readable manifest.
