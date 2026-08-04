# SUP-REVIEW-PIPELINE-INTEGRITY-20260804

Status: proposed
Owner: Claude
Reviewer: Human/Ops (independent from owner; see Rollout below)
Depends on: none (supersedes the four stalled 2026-08-04 SUP-* tasks as the
root-cause fix; those tasks were symptoms of this design gap)

## Problem

Three structural gaps in the task-PR / review-gate pipeline compound into a
livelock. Diagnosed from source (not docs) on 2026-08-04 while investigating
why `SUP-DISPATCH-EXPLAIN-TOOL-20260804` (PR #4532) and
`SUP-TASK-FAILURE-STREAK-SCHEMA-20260804` (PR #4533) could not merge despite
independent reviewer approval.

1. **PR creation is bound to a transient task-board state, not to an
   idempotent invariant.** `.orchestrator/github_bus.py::sync_outbound()`
   only calls `upsert_review_pr()` for tasks whose *current* status equals
   `"review"` at the instant a sync tick runs:
   ```python
   review_tasks = [task for task in status.get("tasks", []) if task.get("status") == "review"]
   for task in review_tasks:
       upsert_review_pr(config, bus_state, status, repo, task)
   ```
   `upsert_review_pr()` itself is idempotent (queries existing PR/candidate
   heads before acting), but the outer filter is not: if a task leaves
   `"review"` before a PR is ever opened for it, no future sync call reconsiders
   it, and the task is permanently PR-less unless it happens to cycle back
   through `"review"` again.

2. **The required GitHub status check is not guaranteed to be emitted for
   every PR, yet branch protection marks it required for all of them.**
   `Pantheon canonical review gate` is posted exclusively by
   `scripts/git/github_review_bridge.py::bridge_review_decision()`, which is
   only invoked from `scripts/ai_status.py` inside the `approve`/`done`
   command handlers (`bridge_github_review_decision` call sites, ai_status.py
   ~L5956/~L6782). No GitHub Action or webhook evaluates this check for PRs
   in general. A PR whose task is not registered on the board, or whose task
   never reaches an `approve`/`done` transaction, can never receive this
   status -- yet `dev`'s branch protection lists it as a required context with
   no exemption. This is a hard contract contradiction, confirmed via
   `gh api repos/ajoe734/pantheon/branches/dev/protection`.

3. **`required_status_checks.strict: true` has no merge-queue behind it.**
   Confirmed `gh api repos/ajoe734/pantheon/rulesets` returns `[]` -- no
   ruleset/merge-queue is configured. Under `strict: true`, every open PR
   must be re-synced with `dev` before merging. During merge bursts (e.g.
   2026-08-02 17:33-21:27, 15+ commits/4h to `dev`) any in-flight PR is
   perpetually stale; nothing but a human or bot manually re-rebasing can
   catch up. This is exactly the problem GitHub's native merge queue exists
   to solve.

**Compounding failure mode observed live:** closeout workers write a review
evidence manifest as a *new* commit after approval (task-closeout-finalization.md
allows this as a fallback: "the owner may bind the same already committed and
reviewed manifest" -- but when no manifest was committed pre-approval, the
owner has no choice but to add one). That new commit changes the head SHA,
which invalidates the SHA-exact approval binding in
`scripts/git/task_review_merge_gate.py`, which flips the task back to
`review`, which re-triggers gap #1's PR-sync (fine) but requires a brand new
independent review of a commit that only changed bookkeeping -- and gap #2
means the resulting required check may never even post if the re-review also
stalls. Patching any one of the three symptoms individually (retry harder,
add a reconciler, allow force-push) does not fix this; each patch assumes
the other two layers are stable, and none of them are.

## Fix (four parts, must land together to avoid re-creating the loop)

### A. PR existence as a reconciled invariant
In `github_bus.py::sync_outbound()`, replace the `status == "review"` filter
with: any task carrying an unmerged `task/<TASK-ID>` branch with a diff
against the delivery base, regardless of current status. `upsert_review_pr()`
needs no behavior change -- only its call-site eligibility does.

### B. Evidence-before-review ordering
`task-closeout-finalization.md` Section "Review Evidence Manifest Rule": change
from "owner may add evidence during closeout" (fallback) to a hard
precondition -- the review evidence manifest must already be committed in the
PR *before* a reviewer is asked to approve. `scripts/ai_status.py`'s `approve`
handler should reject (fail closed) an approval attempt when no
`review_file`-eligible manifest is present in the PR diff, instead of
allowing approval now and evidence-writing later. This removes the only
source of post-approval, SHA-shifting commits in the normal path.

### C. Canonical review gate becomes a guaranteed-emitting GitHub Action
New `.github/workflows/canonical-review-gate.yml`, triggered on
`pull_request: [opened, synchronize, reopened, ready_for_review]` for
`dev`/`master`. For every PR head:
- Resolve `task_id` from the `task/<TASK-ID>` head branch naming convention.
- If resolvable, fetch the PR snapshot fields `task_review_merge_gate.py`
  already contracts on (`headRefOid,headRefName,baseRefName,isDraft,number,
  state,mergedAt,commits,autoMergeRequest`) and run the *existing, already
  tested* `scripts/git/task_review_merge_gate.py check <task_id> --pr-json
  ... --json` -- reuse the policy engine, do not reimplement it.
- Post `Pantheon canonical review gate` as a commit status on the exact head
  SHA, `success` iff the gate's exit code is 0, `failure` otherwise --
  including the "task not found on board" case, which today emits nothing.
- If `task_id` cannot be resolved from the branch name at all (non-task
  branch opened against `dev`), post an explicit `failure` with that reason,
  not silence.
`scripts/ai_status.py`'s existing internal bridge call stays as a fast-path
optimization (posts success slightly sooner on the governed `approve`/`done`
path); the Action is now the source of truth that guarantees the context
always exists.

### D. Native merge queue on `dev`
Add a repository ruleset enabling GitHub's merge queue for `dev`, required
checks evaluated against the queue's merge-preview commit rather than the
raw PR head. This is a live branch-protection change with fleet-wide,
immediate blast radius -- **do not apply without an explicit human go-ahead
in the same turn**, separate from the code merge below.

## Acceptance

- `scripts/git/test_github_bus.py`: new case proving a task whose status
  moves `review -> blocked -> review` (or any path that skips a sync tick)
  still gets a PR once it has a branch+diff, not only while status is
  literally `"review"` at scan time.
- New `scripts/git/test_canonical_review_gate_workflow.py` unit-tests the
  two new/changed helper scripts backing part C in isolation (task-id
  resolution from branch name; status-posting payload construction) without
  requiring live GitHub credentials.
- `scripts/test_ai_status.py`: new case proving `approve` fails closed when
  no evidence manifest is present in the PR diff and no `REVIEW_FILE` is
  supplied.
- Full existing suite for `github_bus.py`, `ai_status.py`,
  `task_review_merge_gate.py` still green.
- Manual: open a throwaway PR from a non-`task/*` branch against `dev` in a
  scratch repo (or dry-run the Action logic locally) and confirm the new
  workflow posts an explicit failing status rather than nothing.

## Rollout note

This PR is authored by an interactive Human/Ops-attended session (git
trailers: `LLM-Agent: Claude`, `Reviewer: Human/Ops`), not a fleet
auto-worker, specifically to avoid concurrent board-write races with the
live supervisor while it is actively dispatching. It intentionally does
**not** self-approve: `Reviewer` must be an identity distinct from
`LLM-Agent`/owner, and fabricating that trailer would defeat the exact
integrity property this task restores. Part D (merge queue
/ branch protection ruleset) is called out separately above and requires an
explicit go-ahead beyond "implement this task" before being applied, given
its immediate effect on every in-flight PR from the live fleet.
