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

### 1.1 Eight more live regressions the same evening

The first three were all the same failure through the same entry point. Eight
later events, each reported by Human/Ops on PR #4218, showed the gate needed
to cover more than that:

| PR | Task | Merge grant | Merged | What it proved |
|----|------|-------------|--------|----------------|
| #4217 | `OPS-CI-PR-TRAILER-RANGE-001` | none (`autoMergeRequest=null`) | 21:43:27Z → `71aea154b` | An auto-merge-only guard misses the plain `gh pr merge` path |
| #4222 | `OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001` | auto-merge at 21:54:25Z | 21:55:32Z → `55b17612e` | Enabling auto-merge *is* the grant; 67 s later it was irreversible |
| #4225 | `OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001` | auto-merge at 22:42:46Z | — (Human/Ops disabled it by hand) | Only human intervention inside the CI window prevented the merge |
| #4225 | same | none (request already revoked) | 23:01:39Z → `8d1b50779` | The blocked auto-merge became a direct merge by the same credential |
| #4226 | same | auto-merge at 23:06:04Z | 23:07:09Z → `1cf27337e` | Third unreviewed merge on one task branch; PR history changes nothing |
| #4227 | `SUP-COMMAND-RUNTIME-REFRESH-001` | auto-merge at 23:10:54Z | 23:14:41Z → `e376955ff` | The request outlived its head and landed a different commit |
| #4230 | `OPS-CI-PR-TRAILER-RANGE-001` | auto-merge at 23:33:20Z | 23:34:22Z → `643181a06` | Every required check green; green CI is not canonical review |
| #4201 | `P0-TW-PAPER-ACTIVATE-001` | auto-merge still armed | — (held only by `BEHIND`) | A stale base is not a safety property; the grant must be revoked |

Every one of these merges reports `reviews=[]`, an empty `reviewDecision`,
`mergedBy: ajoe734`, and a canonical task row still `in_progress` with
reviewer `Codex2`.

Three things follow, and they are what this second pass adds:

1. **Nothing GitHub knows can stand in for canonical review.** All Pantheon
   agents push through the single `ajoe734` account, so `mergedBy`, the
   account that enabled auto-merge, and any GitHub approving review are
   unusable as review evidence. Green CI is not review either — #4230 merged
   with all three required checks `SUCCESS`. Only the canonical
   `review_approved` record from the assigned reviewer counts.
2. **An auto-merge request survives a head change.** #4227 is the exact
   shape: enabled at 23:10:54Z, head replaced at 23:13:21Z, and GitHub merged
   the *newer* head at 23:14:41Z. The commit that landed was never the commit
   the request described. A gated PR now revokes any standing request before
   its exact-head merge, and refuses an approved head outright while a
   request that predates it is armed.
3. **Payload risk waives nothing.** #4227 was Stage-1 docs and evidence with
   the live swap still blocked — accurate, and irrelevant. `risk`, `payload`,
   `docs_only`, `review_waived` and their siblings are now read only so the
   decision can report them as *ignored claims*; none of them reaches policy
   resolution.

A fourth, smaller gap: the Human/Ops do-not-merge instructions standing
against #4225 at 22:43:10Z and 22:52:13Z were recorded as `note` events, which
the first pass did not treat as revoking an approval. A note carrying an
explicit `do not merge` / `changes required` marker now revokes, the same as a
`reopen` or `blocker`. This signal can only ever block a merge, never unlock
one, so its failure direction is safe.

### 1.2 Coverage per merge entry point

| Entry point | What denies it | Proven by |
|-------------|----------------|-----------|
| `task_finalize.sh` | gate call before PR creation; no auto-merge label, request on the head revoked | `TaskFinalizeShellTests::test_gated_task_pr_is_opened_without_any_auto_merge` |
| `safe_pr.sh` | same gate call before `gh pr merge --auto --merge` | `prefix-reproduction.txt` §1–§2 |
| `auto_integrator.py` | gate runs before the CI probe; merges only with `--match-head-commit`, never `--auto` | `IntegratorGateTests` |
| auto-merge **creation** (CLI, UI, or API) | `allow_auto_merge` is `False` for every gated task in every state, approved included | `::test_the_shared_credential_cannot_enable_auto_merge`, `::test_an_approved_task_never_unlocks_auto_merge_creation` |
| auto-merge **finalization** by GitHub | a request enabled before the current head is refused outright | `::test_pr_4227_shape_is_refused_even_when_this_head_is_approved` |
| direct `gh pr merge` / merge API | the decision reads no PR-side identity, review, or check state at all | `::test_the_shared_credential_cannot_finalize_a_merge`, `::test_pr_4217_direct_merge_without_any_auto_merge_request`, `::test_pr_4225_direct_merge_by_the_same_credential_is_refused` |

One honest limit: whoever holds the GitHub credential can still press merge
outside every Pantheon helper. No repository-side gate can prevent that. What
this task delivers is that no Pantheon tooling grants merge authority, that a
standing grant is revoked rather than left armed, and that the canonical state
at merge time is auditable afterwards. Closing the hole entirely would require
branch protection to demand a review, which is a config change this task is
explicitly forbidden to make.

Section 4 of `prefix-reproduction.txt` replays all eight from recorded state.

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
| the row claims low risk, a docs-only payload, or an outright review waiver | `review_before_merge` — the claim is recorded in `contract.ignored_waiver_claims` and never consulted |

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
   rejection and reviewer rebinding both revoke it — and no `note` carrying an
   explicit `do not merge` / `changes required` marker follows it either;
5. the PR head is the exact `task/<TASK-ID>` branch, the base is `dev`, the PR
   is not a draft, and the head oid is a resolvable 40-hex sha;
6. if the row declares `github.head_sha`, the PR head equals it exactly;
7. the newest commit on the PR head is **not** newer than the approval — a head
   pushed after approval is a head nobody reviewed;
8. the approval timestamp is not in the future beyond a 120 s skew allowance;
9. for an already-merged PR, `mergedAt` is not earlier than the approval — a
   late approval cannot retroactively bless a premature merge;
10. no auto-merge request is armed that was enabled *before* the current head
    was committed — merge authority granted for a commit that is no longer the
    head is what landed PR #4227.

Anything else, including an unreadable status file or an unparseable
timestamp, resolves to *do not merge, do not enable auto-merge*.

Every decision, allowed or blocked, also carries an `auto_merge_request`
summary (`present`, `enabled_at`, `enabled_by`, `outlived_head`) and sets
`revoke_auto_merge` whenever a request is standing on a gated PR — on the
approved path too, because a gated PR lands through an explicit
`--match-head-commit` merge and a leftover request would arm the next push.

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

AC5 is met as written and then extended: `LiveMergeGovernanceRegressionTests`
covers the eight later live regressions (#4201, #4217, #4222, #4225 auto-merge
enable, #4225 direct merge, #4226, #4227, #4230) under the same data-only
convention.
The full map of PR → entry point → fixture is the `live_regressions` table in
`evidence.json`, and the per-entry-point controls are
`merge_entry_point_coverage`.

## 5. Validation

See `validation.txt` for the captured transcript.

```
python3 scripts/git/test_task_review_merge_gate.py     Ran 64 tests - OK
python3 scripts/git/test_auto_integrator.py            Ran  9 tests - OK
python3 scripts/git/test_git_workflow_helpers.py       Ran 52 tests - OK
python3 scripts/git/test_task_pr_triage.py             Ran 24 tests - OK
python3 scripts/git/test_index_safety.py               Ran 17 tests - OK
bash -n scripts/git/task_finalize.sh scripts/git/safe_pr.sh   syntax ok
```

The pre-fix reproduction is `prefix-reproduction.txt`; its §1 shows the old
helper enabling auto-merge on an unreviewed task and §2 shows the new helper
refusing on the identical fixture. §3 replays PRs #4212/#4213/#4214 and §4
replays the eight later live regressions.

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

**Do-not-merge notes are matched on message text.** A `note` whose message
contains `do not merge`, `changes required`, `rejects`, `rejected`, or
`revert` now revokes a standing approval, so a note that merely quotes one of
those phrases will also revoke. The signal only ever blocks, never unlocks, and
the recovery is the same single re-approval as any other revocation. It exists
because the #4225 and #4222 do-not-merge instructions were filed as notes
rather than as `reopen` or `blocker` events.

**Revoking auto-merge costs an integrator call.** A gated PR now gets
`gh pr merge <n> --disable-auto` before its exact-head merge, and an approved
head whose auto-merge request predates it is refused outright. The extra call
is idempotent; the refusal costs one integrator cycle, after which no request
remains and the approved head merges.

**The gate reads canonical state, not GitHub reviews.** Pantheon reviewers are
agents that approve through `scripts/ai-status.sh approve`, and all task PRs are
authored by one GitHub account, so `reviewDecision` is structurally empty. The
gate therefore trusts the governed status wrapper and its audit, which are
themselves protected by the command-root binding checks in `ai_status.py`.
