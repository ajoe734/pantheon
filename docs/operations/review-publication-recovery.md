# Canonical Review Publication and Exact-Version GitHub Gate Recovery

Status: active operating rule
Task: OPS-REVIEW-PROOF-001
Plan source: `sha256:07b330d425946fb363e8b94a0e707595bb0e3c7af6be0d161ad9ab88705471f0`
(`/tmp/pantheon-tooling-root-repair-20260907/REVIEW_PROOF_PLAN.md`)

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

### 1. Bounded Peeling and Target Commit Verification
In `scripts/git/canonical_review_gate_ci.py`:
- Implemented `resolve_proof_tag_target(repo, ref_path, token=None, session=None, max_depth=MAX_TAG_PEEL_DEPTH)` with `MAX_TAG_PEEL_DEPTH = 5`.
- Tags pointing directly to commits (lightweight tags) have their target commit validated against `head_sha`.
- Tags pointing to annotated tag objects are iteratively peeled (up to depth 5) to resolve the underlying commit object SHA.
- Mismatched commits, malformed payloads, non-commit objects, API errors, and peel depth exceeded errors fail closed (`None` return, treated as non-existent or invalid).
- `operator_acceptance_proof_tag_exists` similarly resolves the tag target commit against the requested candidate head SHA.

### 2. Observable Dispatch and Retired Legacy PAT Status
In `scripts/git/github_review_bridge.py`:
- `_dispatch_canonical_review_gate_workflow` defaults to `required: bool = True`. Dispatch failures raise `ReviewBridgeError` and are observable to callers instead of being silently swallowed.
- The gate workflow is dispatched on both `APPROVE` and `REOPEN` decisions.
- Direct PAT status posting (`_submit_required_status`) is retired from `bridge_review_decision`. The sole authoritative issuer of the `Pantheon canonical review gate` status is the GitHub Actions workflow running as `github-actions[bot]`.
- Decision bridging operates in `mode="pull_request_review"`, preserving genuine PR review comments while pushing proof tags and triggering the verification workflow.

### 3. Opposing Tag Cleanup and Fail-Closed Conflict Detection
- **Bridge-side cleanup**:
  - Pushing an `approved` or `operator_accept` tag deletes any opposing `reopen` tag on the same commit (`_delete_ref(repo, reopen_tag)`).
  - Pushing a `reopen` tag deletes any opposing `approved` or `operator_accept` tag on the same commit (`_delete_ref(repo, approve_tag)`).
- **Gate-side fail-closed check**:
  - `canonical_review_gate_ci.py` checks `reopen_proof_tag_exists`.
  - If a reopen tag exists, or if conflicting approve/reopen tags coexist, the gate reports `failure` ("Canonical review decision is reopened or conflicting review tags exist for commit").

### 4. Integrator Review Gate Reconciliation
In `scripts/git/auto_integrator.py`:
- When evaluating candidate CI checks (`is_ci_green` / `is_canonical_review_gate_green`):
  - Substantive check failures (e.g. test suites, linters, builds) immediately trigger candidate failure and open `ci-red` without re-dispatching.
  - If the candidate is canonically approved in TaskStore, and only the `Pantheon canonical review gate` check is missing, pending, or failing, the integrator checks GitHub for a valid review proof tag targeting `candidate.head_sha`.
  - If a valid proof tag exists (and no reopen tag exists), the integrator re-dispatches the canonical review gate workflow via `dispatch_canonical_review_gate_workflow(candidate.head_sha, candidate.target_branch, candidate.pr_number, required=True)` and returns `action="waiting"` with `reason="canonical review gate re-dispatched for verified review proof tag"`.
  - Independent candidates continue to integrate without interruption.
  - If the proof tag is missing, mismatched, or reopened, the candidate fails with `ci-red` as expected.

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
   # Check if approved tag exists and points to the exact head commit
   git ls-remote origin refs/tags/pantheon-review/approved/<head-sha>
   # Check if reopen tag exists
   git ls-remote origin refs/tags/pantheon-review/reopen/<head-sha>
   ```
   Peel the tag if annotated:
   ```bash
   git rev-parse refs/tags/pantheon-review/approved/<head-sha>^{commit}
   ```

3. **Manual Gate Re-dispatch (if needed)**:
   If a valid proof tag exists on GitHub but the workflow check was dropped or failed due to GitHub Actions infrastructure issues:
   ```bash
   gh workflow run canonical-review-gate.yml \
     -f head_sha=<head-sha> \
     -f target_branch=dev \
     -f pr_number=<pr-number>
   ```
   Alternatively, allowing the next cycle of `auto_integrator.py` will automatically detect the valid proof tag, trigger re-dispatch, and wait for the check to turn green.

---

## Qualified Activation Handoff Pending Merge

- This task modifies core development tooling (`scripts/git/github_review_bridge.py`, `scripts/git/canonical_review_gate_ci.py`, `scripts/git/auto_integrator.py`).
- Runtime promotion occurs through normal coordinator procedures after the PR is reviewed and merged into `dev`.
- No out-of-band daemons, alternate queues, or secondary verification databases are created.
