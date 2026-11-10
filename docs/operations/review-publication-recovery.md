# Canonical Review Publication and Exact-Version GitHub Gate Recovery

Status: active operating rule (`review_gate.github_review_bridge_required: true` and `review_gate.github_review_identity: "shared"` live since runtime `30ae728a8` on `2026-11-08T12:50Z`; `Pantheon canonical review gate` required in branch protection on both `pantheon` `dev` and `execute-plans` `dev` as of 2026-11-09)
Task: OPS-REVIEW-PROOF-001
Plan source: `sha256:07b330d425946fb363e8b94a0e707595bb0e3c7af6be0d161ad9ab88705471f0`
(`/tmp/pantheon-tooling-root-repair-20260907/REVIEW_PROOF_PLAN.md`)

> **Scope (`OPS-REVIEW-GATE-SHARED-IDENTITY-20261108` / `OPS-REVIEW-GATE-PROTECTION-DOCS-20261109`):** `.orchestrator/config.json` sets `review_gate.github_review_bridge_required: true`, sets `review_gate.github_review_identity: "shared"`, and includes `"Pantheon canonical review gate"` in `branch_workflow.task_pr.required_status_checks` (live since runtime `30ae728a8` on `2026-11-08T12:50Z`). Under `github_review_identity: "shared"`, `scripts/git/github_review_bridge.py` records `approve` and `reopen` decisions as a `COMMENT` pull request review (`event: "COMMENT"`, matched state `"COMMENTED"`) carrying the `<!-- pantheon-review-bridge ... -->` marker on the exact PR head (never submitting `APPROVE` or `REQUEST_CHANGES`), pushes the `refs/tags/pantheon-review/<decision>/<head_sha>` proof tag, and dispatches `canonical-review-gate.yml` as the sole issuer of the `Pantheon canonical review gate` commit status. GitHub branch protection now requires `Pantheon canonical review gate` (bound to GitHub Actions `app_id: 15368`) on both `ajoe734/pantheon` `dev` (5 required checks: `Commit trailers`, `Runtime mirror guard`, `Smoke acceptance`, `Service suites`, `Pantheon canonical review gate`; `strict: false`, no `required_pull_request_reviews`, `enforce_admins: true`, gate added `2026-11-09T13:26:52Z`) and `ajoe734/execute-plans` `dev` (4 required checks: `Commit trailers`, `Generated files guard`, `Component merge gate`, `Pantheon canonical review gate`; `strict: true`, a PR rule with `0` approvals, `enforce_admins: false`, gate added `2026-11-09T23:36:52Z`), while `master` (`pantheon`) and `main` (`execute-plans`) do not require the gate because promote pull requests do not run the gate workflow. Operator-directed direct deliveries require a `task/<TASK-ID>` branch with an operator acceptance proof tag (`refs/tags/pantheon-review/operator-accept/<head_sha>`) because GitHub now rejects a merge into either `dev` branch without a green gate. Residual risk: GitHub now refuses a merge into either `dev` branch without a green gate posted by the GitHub Actions workflow, while a holder of the shared `ajoe734` GitHub credential can still deliberately push a proof tag or add the `delivery:tooling` label directly, and only a separate review identity (`review_gate.github_review_identity: "independent"`) closes that. Rollback order: remove `Pantheon canonical review gate` from branch protection in both repositories before setting `review_gate.github_review_bridge_required` to `false` and promoting the runtime, because the verifier stops publishing once the flag is `false` and every merge would then be blocked.

## The Contradictions and Defects This Closes

On dev base `46bbfe935df1e68740569fb5dc6233897de98b0b`, three critical defects in the canonical review publication pipeline prevented reliable verification and merge gating:

1. **Tag Target Commit Verification Flaw**:
   `scripts/git/canonical_review_gate_ci.py`'s `review_proof_tag_exists` and `operator_acceptance_proof_tag_exists` verified only tag ref names (e.g. `refs/tags/pantheon-review/approved/<head_sha>`) without verifying that the underlying Git object actually resolved to the target commit SHA. If a tag ref was created pointing to another commit or was malformed, the gate accepted it as valid proof, violating exact-version integrity.

2. **Swallowed Workflow Dispatch and Dual-Issuer Conflict**:
   `scripts/git/github_review_bridge.py` caught and swallowed exceptions during `workflow_dispatch` when `required=False`. As a result, canonical review approval completed in TaskStore while the required GitHub Actions gate check was never dispatched or remained failing/missing. Furthermore, `github_review_bridge` posted legacy direct PAT commit status (`_submit_required_status`), creating two conflicting issuers for the `Pantheon canonical review gate` context: the direct bridge PAT and the `github-actions[bot]` workflow run.

3. **Reopen vs. Approve Tag Conflict (Livelock / False Green)**:
   When a candidate commit was approved and subsequently reopened on the same commit SHA, both `refs/tags/pantheon-review/approved/<head_sha>` and `refs/tags/pantheon-review/reopen/<head_sha>` existed simultaneously on GitHub. The GitHub Actions review gate checked only for the presence of the approved tag, reporting green even after the reviewer had explicitly rejected or reopened the task.

4. **Integrator Gate Check Stagnation**:
   `scripts/git/auto_integrator.py` had no reconciliation mechanism for canonical review gate status. If TaskStore held a valid approval and GitHub held a valid review proof tag, but the GitHub Actions check was pending, absent, or transiently red, the integrator either stalled indefinitely or opened a false `ci-red` failure, potentially jamming the integration pipeline.

---

## Architecture & Root Repairs

### 1. Tag Inspection and Exact-Head Integrity
In `scripts/git/canonical_review_gate_ci.py`:
- Implemented `TagInspection` dataclass with 5 distinct states: `confirmed_absent`, `valid`, `mismatched`, `malformed`, `api_error`.
- Implemented `inspect_proof_tag` resolving both lightweight and peeled annotated tags (up to `MAX_TAG_PEEL_DEPTH = 5`).
- Mismatched target commit, malformed payloads, non-commit objects, API lookup errors, and peel depth exceeded errors fail closed.
- `build_status_payload` requires that `reopen_inspection.is_absent` is strictly confirmed before evaluating approval or operator acceptance tags. If a reopen tag exists or is malformed/mismatched/api_error, it fails closed.

### 2. Intent-Fenced Opposing Tag Deletion and Stale Retry Protection
In `scripts/git/github_review_bridge.py`:
- Opposing decision tag deletion verifies intent ordering against the PR reviews timeline:
  - Sequence `approve(111) -> reopen(222) -> retry approve(111)` fails closed on the retry, preserving `reopen(222)`.
  - A retry of an earlier approval cannot delete a newer reopen tag or restore revoked approval.
  - A genuine reapproval `approve(333)` is strictly newer in the PR timeline and successfully deletes the reopen tag.
  - Malformed opposing tags, mismatched target commits, or missing payloads fail closed.
- Same-decision tags are accepted as idempotent replays ONLY if `task_id`, `actor`, `decision`, and `intent_nonce` all match. If the caller provides a strictly newer intent of the same decision, the tag is updated; otherwise it fails closed.
- `_dispatch_canonical_review_gate_workflow` defaults to `required=True`. Dispatch failures are observable to callers.

### 3. Integrator Reconciliation, Self-Heal, and Observable Failure Reporting
In `scripts/git/auto_integrator.py`:
- `make_integrator_tag_lookup` re-raises non-404 exceptions so API lookup failures result in `api_error` rather than false absence. Empty mappings, lists, and null payloads are preserved as malformed payloads rather than collapsing to absent, reserving absence for confirmed HTTP 404.
- In `integrate_candidate`, the reopen tag is inspected using `canonical_review_gate_ci.inspect_proof_tag`. Re-dispatch is only attempted when `reopen_inspection.is_absent` is true and a valid review or operator acceptance tag exists.
- When `review_bridge_is_required` is true, the `Pantheon canonical review gate` check is not green, no `approve`, `operator-accept`, or `reopen` tag exists for the exact head, and the active task store holds a `review_approved` for that exact head by an assigned reviewer different from the owner, the integrator self-heals by calling `bridge_review_decision` with that reviewer as actor, dispatching the workflow, and reporting `"waiting"` instead of opening a `ci-red` unblock task. It never publishes a proof without an exact-head approval by a reviewer distinct from the owner and never publishes when a `reopen` tag exists.
- `_dispatch_canonical_review_gate_workflow` is invoked with `required=True`. If dispatch fails (e.g. transient API failure), the error is recorded in `detail` without falsely claiming `re-dispatched`, and action remains `"waiting"` without blocking unrelated candidates.

### 4. Review Mode Convergence and Shared Identity Mode (`OPS-REVIEW-GATE-SHARED-IDENTITY-20261108`)
In `scripts/ai_status.py`, `.orchestrator/common.py`, and `scripts/git/github_review_bridge.py`:
- `GITHUB_REVIEW_MODES` is converged to `{"pull_request_review"}` only.
- `review_gate.github_review_identity` accepts `"shared"` or `"independent"` (missing defaults to `"independent"`). In `"shared"` mode, `bridge_review_decision` submits `event: "COMMENT"` (`state: "COMMENTED"`) carrying the `<!-- pantheon-review-bridge ... -->` marker on the exact head so intent ordering across `approve` and `reopen` works under a single shared GitHub account without hitting GitHub's self-approval refusal.
- Direct PAT status posting (`_submit_required_status`) is completely retired in both modes; the sole authoritative status issuer is the GitHub Actions workflow (`github-actions[bot]`).
- `bridge_github_review_decision` strictly validates that the returned mode is in `GITHUB_REVIEW_MODES`.

### 5. Optional Diagnostic Attestation Audit Simplification
In `.github/workflows/canonical-review-attestation-audit.yml`:
- The workflow triggers have been simplified to `workflow_dispatch` only (manual-only diagnostic).
- Automated triggers (`pull_request_target`, `issue_comment`, `schedule`) and claims of an active external issuer have been retired.
- Security contracts (trusted base checkout, verification of dev/master base branch before checkout, read-only permissions, and fail-closed evaluation) are preserved.

---

## Operational Runbook

### Diagnosing Review Gate Issues

1. **Inspect PR Check Status**:
   ```bash
   gh pr checks <pr-number>
   ```
   Check if `Pantheon canonical review gate` is passing, failing, or pending.

2. **Verify Proof Tags on GitHub**:
   ```bash
   # Check if approve tag exists and points to the exact head commit
   git ls-remote origin refs/tags/pantheon-review/approve/<head-sha>
   # Check if reopen tag exists
   git ls-remote origin refs/tags/pantheon-review/reopen/<head-sha>
   ```
   Peel the tag if annotated:
   ```bash
   git rev-parse refs/tags/pantheon-review/approve/<head-sha>^{commit}
   ```

3. **Manual Gate Re-dispatch (if needed)**:
   If a valid proof tag exists on GitHub but the workflow check was dropped or failed due to GitHub Actions infrastructure issues:
   ```bash
   gh workflow run canonical-review-gate.yml \
     --ref dev \
     -f head_ref=task/<task-id> \
     -f head_sha=<head-sha>
   ```
   Alternatively, allowing the next cycle of `auto_integrator.py` will automatically detect the valid proof tag, trigger re-dispatch, and wait for the check to turn green.

---

## Qualified Activation Handoff Pending Merge

- This task modifies core development tooling (`scripts/git/github_review_bridge.py`, `scripts/git/canonical_review_gate_ci.py`, `scripts/git/auto_integrator.py`).
- Runtime promotion occurs through normal coordinator procedures after the PR is reviewed and merged into `dev`.
- No out-of-band daemons, alternate queues, or secondary verification databases are created.
