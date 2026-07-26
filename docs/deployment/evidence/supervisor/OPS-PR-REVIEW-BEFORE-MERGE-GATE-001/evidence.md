# OPS-PR-REVIEW-BEFORE-MERGE-GATE-001 — Review-before-merge gate for task PRs

Task: Gate task auto-merge on exact independent review when required
Owner: Claude · Reviewer: Codex2 · Phase: Fleet delivery governance

Scope rule honoured throughout: **no `.orchestrator/config.json` edit**, no
hand-edited task board, no owner or reviewer action performed on behalf of
anyone. Every decision is derived from canonical task state that already
exists.

## 1. What went wrong

On 2026-07-26 three task PRs landed in `dev` without any independent review:

| PR | Task | Opened | Merged | Reviewer approval at merge time |
|----|------|--------|--------|---------------------------------|
| #4212 | `SUP-WORKER-TRUTH-RECONCILE-001` | 20:02:43Z | 20:04:01Z | none — task was `in_progress` |
| #4213 | `OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001` | 20:04:46Z | 20:18:15Z | none — task was `in_progress` |
| #4214 | `OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001` | 20:21:11Z | 20:22:27Z | none — task was `in_progress` |

`gh pr view <n> --json reviewDecision` is empty for all three. The activity
audit holds no `review_approved` event for either task before those merge
timestamps. #4212 was independently **rejected** at 20:12:48Z — eight minutes
after it had already merged into `dev`.

### Root cause

`scripts/git/task_finalize.sh` and `scripts/git/safe_pr.sh` both ended with an
unconditional

```bash
gh pr create ... --label auto-merge
gh pr merge task/<TASK-ID> --auto --merge
```

so merge authority was handed to GitHub at PR-open time. `dev` branch
protection requires 0 approvals by design (§ 7.2 of
`docs/conventions/GIT_WORKFLOW.md`), so the only remaining gate was CI. The
moment the three required status checks turned green — one to two minutes
after the push — GitHub merged. Independent review was structurally unable to
happen before the merge, no matter how fast the reviewer was.

`scripts/git/auto_integrator.py` had the mirror-image gap: it selected
`review_approved` tasks but never verified *who* approved, *which head* they
approved, or whether that approval was still live. It could also rebase a task
branch, force-push the new head, and then enable auto-merge on a head no
reviewer had ever seen.

Section 1 of `prefix-reproduction.txt` replays the pre-fix helper against a
synthetic repository whose canonical row is `in_progress` with an independent
reviewer, and shows it enabling `--auto --merge` anyway.

## 2. The gate

`scripts/git/task_review_merge_gate.py` is the single canonical authority. It
answers one question — *may `task/<TASK-ID>` merge into `dev` right now?* — from
canonical state only:

* the task row in `ai-status.json` under the bound `PANTHEON_STATUS_ROOT`
  (falling back to the task archive for a terminal task);
* the immutable `review_approved` event in the activity audit, including its
  rotated `.gz` archives.

Nothing in the PR, no label, and no helper flag can open it.

### 2.1 Policy resolution

| Canonical contract | Policy |
|--------------------|--------|
| an independent reviewer is assigned (`reviewer` present and ≠ `owner`) | `review_before_merge` |
| the row declares `merge_policy: merge_then_review` **and** requires no independent review | `merge_then_review` — preserved unchanged |
| the row declares `merge_policy: merge_then_review` but *does* require independent review | `review_before_merge` — the declaration is not honoured |
| task row missing, unreadable, unknown declaration, or any gate error | `review_before_merge` |

`scripts/ai_status.py::command_assign` refuses `reviewer == owner`, so in
practice every assigned Pantheon task is gated, and a merge-then-review
declaration can only survive on a row whose contract genuinely requires no
independent review. The publish/promote and hotfix PR paths are untouched:
they are not `task/<TASK-ID>` PRs and never call these helpers.

### 2.2 Approval binding

For a gated task the gate opens only when **all** of the following hold:

1. canonical status is `review_approved` (or `done`);
2. a `review_approved` audit event exists for the task;
3. the event's `agent` equals the canonical `reviewer` (case-insensitive
   identity comparison, no renaming);
4. no `reopen`, `blocker`, or `assign` event follows that approval — reviewer
   rejection and reviewer rebinding both revoke it;
5. the PR head is the exact `task/<TASK-ID>` branch, the base is `dev`, the PR
   is not a draft, and the head oid is a resolvable 40-hex sha;
6. if the row declares `github.head_sha`, the PR head equals it exactly;
7. the newest commit on the PR head is **not** newer than the approval — a head
   pushed after approval is a head nobody reviewed;
8. the approval timestamp is not in the future beyond a 120 s skew allowance;
9. for an already-merged PR, `mergedAt` is not earlier than the approval — a
   late approval cannot retroactively bless a premature merge.

Anything else, including an unreadable status file or an unparseable
timestamp, resolves to *do not merge, do not enable auto-merge*.

## 3. What each helper now does

### `task_finalize.sh` / `safe_pr.sh`

Both ask the gate for the policy before opening the PR. Under
`review_before_merge` the PR is created **without** the `auto-merge` label,
auto-merge is never enabled, and any stale auto-merge request on the head is
revoked with `gh pr merge --disable-auto`. A gate failure resolves to the gated
path, so a broken canonical read can only withhold merge authority, never widen
it. Under `merge_then_review` the previous label + `--auto --merge` behaviour is
byte-for-byte preserved.

Both helpers print the follow-up command that actually lands a gated PR:

```bash
python3 scripts/git/auto_integrator.py --execute --task-id <TASK-ID>
```

### `auto_integrator.py`

The gate is evaluated immediately after PR eligibility, **before** the CI and
merge-state probes, so a premature auto-merge request is revoked without
waiting for checks to turn green. For a gated task the integrator:

* blocks and opens an `INTEGRATION-UNBLOCK-*-REVIEW-GATE-*` task when the gate
  refuses, revoking any pending auto-merge request first;
* never force-pushes a rebase (`allow_push=False`), because replacing the head
  would discard the reviewed commit; if the branch needs a refreshed head the
  result is `waiting` with an explicit "owner refreshes, reviewer re-approves"
  instruction;
* merges with `gh pr merge --merge --match-head-commit <approved-oid>` and never
  `--auto`, so a concurrent finalize that moves the head makes GitHub refuse the
  merge instead of landing an unreviewed commit;
* refuses to reconcile an already-merged PR to `done` when that merge would not
  have passed the gate, so the reconciliation path cannot launder a premature
  merge.

Two open PRs claiming the same task branch now raise `AmbiguousPullRequests`
and block, instead of silently resolving to the first row returned by GitHub.

## 4. Acceptance

| # | Acceptance statement | Status | Where proven |
|---|----------------------|--------|--------------|
| 1 | A canonical review-before-merge task never enables or performs merge before exact assigned reviewer approval | pass | `TaskFinalizeShellTests`, `IntegratorGateTests::test_unapproved_gated_pr_is_never_merged_and_auto_merge_is_revoked`, `prefix-reproduction.txt` §1–§2 |
| 2 | Approval is bound to exact PR head, expected base, reviewer identity, and a non-stale timestamp | pass | `ApprovedPathTests`, `FailClosedTests::test_head_change_after_approval_blocks`, `::test_wrong_base_branch_blocks`, `::test_approval_by_another_agent_blocks`, `::test_future_approval_timestamp_blocks` |
| 3 | Reviewer rejection, head change, missing state, GitHub ambiguity, and concurrent finalize all fail closed | pass | `FailClosedTests`, `UnreadableStateTests`, `IntegratorGateTests::test_concurrent_open_prs_for_one_task_branch_fail_closed`, `--match-head-commit` on the merge call |
| 4 | Tasks explicitly governed as merge-then-review retain their documented integration behavior | pass | `PolicyResolutionTests`, `IntegratorGateTests::test_merge_then_review_task_keeps_its_documented_behavior`, `TaskFinalizeShellTests::test_merge_then_review_task_still_enables_auto_merge` |
| 5 | Regression fixtures cover PRs #4212, #4213 and #4214 without impersonating owner or reviewer | pass | `PrematureMergeRegressionTests` (recorded state replayed as data only) |
| 6 | Focused workflow tests cover branch, commit, push, PR, checks, independent review, merge, and evidence archive | pass | `validation.txt` |

## 5. Validation

See `validation.txt` for the captured transcript.

```
python3 scripts/git/test_task_review_merge_gate.py     Ran 38 tests - OK
python3 scripts/git/test_auto_integrator.py            Ran  9 tests - OK
python3 scripts/git/test_git_workflow_helpers.py       Ran 34 tests - OK
python3 scripts/git/test_task_pr_triage.py             Ran 24 tests - OK
python3 scripts/git/test_index_safety.py               Ran 17 tests - OK
bash -n scripts/git/task_finalize.sh scripts/git/safe_pr.sh   syntax ok
```

The pre-fix reproduction is `prefix-reproduction.txt`; its §1 shows the old
helper enabling auto-merge on an unreviewed task and §2 shows the new helper
refusing on the identical fixture.

## 6. Residual risks

**Gated PRs need an integrator pass to land.** Under `review_before_merge`
nothing merges the PR automatically. After approval the merge comes from
`auto_integrator.py --execute`, whether run by cron
(`scripts/run-auto-integrator.sh`) or invoked directly. If that lane is not
running, approved PRs sit open rather than merging. This is the intended
fail-closed direction — an unmerged approved PR is recoverable, a merged
unreviewed commit is not — but it is a real operational dependency and both
helpers now print the exact command.

**Approval events are matched by task id in the audit.** A rotation that loses
both the active tail and the archived `.gz` for an approval would make the gate
report `approval_record_missing` and block. Fail-closed, but it converts an
audit gap into a merge stall; the recovery is a fresh reviewer approval.

**`assign` is treated as revoking.** An `assign` after approval blocks the
merge even when owner and reviewer are unchanged. This is deliberate — the
2026-07-26 timeline includes a post-merge reviewer rebinding — and the recovery
is one reviewer re-approval.

**The gate reads canonical state, not GitHub reviews.** Pantheon reviewers are
agents that approve through `scripts/ai-status.sh approve`, and all task PRs are
authored by one GitHub account, so `reviewDecision` is structurally empty. The
gate therefore trusts the governed status wrapper and its audit, which are
themselves protected by the command-root binding checks in `ai_status.py`.
