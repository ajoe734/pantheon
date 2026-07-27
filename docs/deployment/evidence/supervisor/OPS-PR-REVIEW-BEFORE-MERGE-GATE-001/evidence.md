# OPS-PR-REVIEW-BEFORE-MERGE-GATE-001 — Review-before-merge gate for task PRs

Task: Gate task auto-merge on exact independent review when required
Owner: Codex · Reviewer: Codex2 · Phase: Fleet delivery governance

Codex adopted the branch after the first exact-head review rejected
`190fb7fe8c95fa060a33e45edc0e6ac0a0e55a59`, then addressed the second
independent rejection of
`5a9ad1643eba2580bf8a51c71a6f3a43ad8c57b6`. Claude authored the initial
implementation; Codex owns the case-preserving archive repair, helper
revocation readback, final evidence, handoff, and closeout.

A later independent pass rejected PR #4218 head
`23109d468ea1c5ccda9318253d5b4221eac92d61` because its reviewed base
`eecb96fa3826e8e3527a77da7f187a32b33c6c93` had become stale. Codex preserved
that rejection, fetched `dev` through the explicit remote-tracking refspec,
and composed authoritative base `6692d51c9bc5a48ffcbaac8cf817b635351a7c9a`.
The next exact-head review rejected `dcd4b9ccf80d520c6d95cb84e5e4a83091c71dc3`
because the integrator trusted a zero `--disable-auto` exit without reading
live `autoMergeRequest` back. Codex added that mandatory readback and
normalized the gate/integrator regression imports to one module identity so
package-mode pytest and direct script execution exercise the same
`CommandFailure` class.

The following review dispatch named exact head
`30b57020d73ba7aefd261a12326b83114d83eec2`, but PR #4218 had already moved to
`4cfd09852fc3dcaf6490cd25e6d5a35e5d6b6873`; Codex2 rejected reuse of the
old-head review. Codex preserved that rejection in anchor
`456982f4d8566befb60b54d221065c4573cc423f`, fast-forwarded to the remote task
work, composed current authoritative base
`e1512d207d9b5df3739ac7b7d0cac202b2798ac8`, and revalidated tree
`7fb2f318783114d7cbd8ecd981390e84f2af355a` before this evidence-only refresh.

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
| `task_finalize.sh` | gate call before PR creation; no auto-merge label; already-off is a no-op, while a standing request is revoked and read back; unreadable/still-armed fails closed | `TaskFinalizeShellTests::test_gated_task_pr_is_opened_without_any_auto_merge`, `::test_task_finalize_revokes_a_standing_request_and_verifies_it_off`, `::test_task_finalize_fails_closed_when_revocation_leaves_request_armed` |
| `safe_pr.sh` | same gate call and the same before/after `autoMergeRequest` verification | `::test_safe_pr_distinguishes_an_already_off_request`, `::test_safe_pr_revokes_a_standing_request_and_verifies_it_off`, `::test_safe_pr_fails_closed_when_revocation_leaves_request_armed` |
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

### 1.3 Fail-open cases the reviewed implementations still had

Independent review at exact head `190fb7fe8` rejected the first implementation
with two reproduced fail-open cases. A later review at exact head `5a9ad1643`
found two more, and the review at `dcd4b9ccf` extended the revocation finding
to the zero-exit/still-armed case. All are now closed; the first pair is replayed
against the pre-fix modules in §5 of `prefix-reproduction.txt`, the latest
revocation shape is replayed in §8, and all cases are pinned by the 87-test
gate suite.

**The approval was not structurally bound to the reviewed head.**
`command_approve` recorded only actor, timestamp and free-text message, and
`ApprovalRecord` carried no approved head, so the gate could only ask *is the
head newer than the approval?* That question cannot see a head **replaced with
an older commit**. Approving `bbbb…` and then setting the PR head to `cccc…`,
committed *before* the approval, left every timestamp rule satisfied:

```
-> allow_merge=True reason=exact_head_approved
   reviewer Claude approved ABC-001 at 2026-07-26T12:00:00Z;
   head cccccccc… is unchanged since then
```

A reviewer typing the head into the approval message did not help: nothing
compared it. The fix records the reviewed PR number, head sha, head branch and
expected base inside the immutable `review_approved` audit event and compares
those exact identities in the gate.

**An unverified auto-merge revocation did not stop the merge.**
`disable_auto_merge` only trusted the local `gh pr merge --disable-auto` exit
code. When the command failed, and also when it reported success without
withdrawing the server-side `autoMergeRequest`, the approved path could still go
on to the direct `--match-head-commit` merge:

```
-> action=merged
   gh pr merge 100 --disable-auto            (returncode 1, ignored)
   gh pr merge 100 --merge --match-head-commit bbbbbbbb…
```

The armed request survives that merge attempt, so GitHub kept independent
authority to land whatever head stood next. The integrator now reads
`autoMergeRequest` back from GitHub after every attempted revocation and blocks
before emitting any merge call if the read is unavailable or still armed.

**Archived task lookup changed the canonical task ID's case.**
The archive writer stores `LUV-REACTIVATE-KW01-001.json`, preserving case and
percent-escaping all characters except `-_.`. The gate instead looked for
`luv-reactivate-kw01-001.json`, so a terminal task present in production
resolved as `source=missing`. The lookup now uses the writer's exact
case-preserving escaping contract, with an uppercase production-shape
regression.

**The PR-opening helpers claimed revocation without proving it.**
`task_finalize.sh` and `safe_pr.sh` both ended their gated path with
`gh pr merge --disable-auto ... || true` and then printed that auto-merge was
off. A failed command could therefore leave `autoMergeRequest` armed while the
helper returned success. Both helpers now read the request before acting,
distinguish already-off from standing, and read back after revocation. An
unreadable result or a request that remains armed aborts the helper. A nonzero
disable command is accepted only when readback proves another actor already
turned the request off.

## 2. The gate

`scripts/git/task_review_merge_gate.py` is the single canonical authority. It
answers one question — *may `task/<TASK-ID>` merge into `dev` right now?* — from
canonical state only:

* the task row in `ai-status.json` under the bound `PANTHEON_STATUS_ROOT`
  (falling back to the task archive for a terminal task using the archive
  writer's case-preserving, percent-escaped filename contract);
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
7. the approval carries a `review_binding` naming the reviewed PR number, head
   sha and expected base, and **every one of those identities matches the PR
   standing now** — see § 2.3;
8. the newest commit on the PR head is **not** newer than the approval — a head
   pushed after approval is a head nobody reviewed;
9. the approval timestamp is not in the future beyond a 120 s skew allowance;
10. for an already-merged PR, `mergedAt` is not earlier than the approval — a
    late approval cannot retroactively bless a premature merge;
11. no auto-merge request is armed that was enabled *before* the current head
    was committed — merge authority granted for a commit that is no longer the
    head is what landed PR #4227.

Anything else, including an unreadable status file or an unparseable
timestamp, resolves to *do not merge, do not enable auto-merge*.

### 2.3 What the approval is bound to

`scripts/ai_status.py::command_approve` reads `REVIEW_PR`, `REVIEW_HEAD_SHA`,
and optionally `REVIEW_BASE` (default `dev`) and `REVIEW_HEAD_BRANCH` (default
`task/<TASK-ID>`), validates them, and writes them as `review_binding` into
both the immutable `review_approved` audit event and the canonical task row.
An abbreviated sha, a non-numeric PR, or one of the pair without the other is
refused outright — an identity that cannot be compared exactly is not a
binding.

The gate compares each identity against the live PR and blocks with a distinct
reason: `approval_head_mismatch`, `approval_pr_mismatch`,
`approval_base_mismatch`, `approval_head_branch_mismatch`,
`approval_binding_unusable` (a malformed binding), or
`approval_head_binding_missing` (no binding at all).

Both helpers print the exact `approve` invocation with the PR number and head
sha filled in, so the reviewer does not have to assemble it.

Two deliberate asymmetries:

* `approve` **warns** rather than refusing when no binding is supplied, because
  not every task produces a PR and a reviewer must still be able to approve
  one that does not. The refusal belongs to the merge gate, which is only ever
  consulted for a task that has a PR.
* An unbound approval is therefore *unusable*, not permissive. Approvals
  recorded before this change block with `approval_head_binding_missing`; the
  recovery is one re-approval naming the head.

Every decision, allowed or blocked, also carries an `auto_merge_request`
summary (`present`, `enabled_at`, `enabled_by`, `outlived_head`) and sets
`revoke_auto_merge` whenever a request is standing on a gated PR — on the
approved path too, because a gated PR lands through an explicit
`--match-head-commit` merge and a leftover request would arm the next push.

## 3. What each helper now does

### `task_finalize.sh` / `safe_pr.sh`

Both ask the gate for the policy before opening the PR. Under
`review_before_merge` the PR is created **without** the `auto-merge` label,
auto-merge is never enabled, and request state is read from GitHub. An
already-off request is left alone. A standing request is revoked with
`gh pr merge --disable-auto` and then read back; an unreadable or still-armed
request terminates the helper nonzero. A gate failure resolves to the gated
path, so a broken canonical read can only withhold merge authority, never widen
it. Under `merge_then_review` the previous label + `--auto --merge` behaviour is
preserved.

Both helpers print the follow-up commands that actually land a gated PR — the
approval that binds this exact head, then the integrator pass:

```bash
AI_NAME=<reviewer> REVIEW_PR=<pr-number> REVIEW_HEAD_SHA=<40-hex head oid> \
  "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" approve <TASK-ID> "<review evidence>"
python3 scripts/git/auto_integrator.py --execute --task-id <TASK-ID>
```

The PR number and head sha are resolved and substituted by the helper, so the
reviewer copies a complete command rather than looking the head up by hand.

### `auto_integrator.py`

The gate is evaluated immediately after PR eligibility, **before** the CI and
merge-state probes, so a premature auto-merge request is revoked without
waiting for checks to turn green. For a gated task the integrator:

* blocks and opens an `INTEGRATION-UNBLOCK-*-REVIEW-GATE-*` task when the gate
  refuses, revoking any pending auto-merge request first;
* reads `autoMergeRequest` back after every attempted revocation and blocks —
  before emitting any merge call, on the approved path included — when the
  command failed, the readback is unavailable, or the request still reads armed.
  `gh pr merge --disable-auto` returning zero is not sufficient proof: approval
  of *this* head does not make it safe to merge alongside a standing grant. A
  gate refusal keeps its own more precise reason, so this guard adds a failure
  mode rather than masking one;
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
| 2 | Approval is bound to exact PR head, expected base, reviewer identity, and a non-stale timestamp | pass | `ApprovalBindingTests` (the recorded binding, compared exactly), `ai_status` `::test_approve_records_the_reviewed_pr_head_binding`, `ApprovedPathTests`, `FailClosedTests::test_head_change_after_approval_blocks`, `::test_wrong_base_branch_blocks`, `::test_approval_by_another_agent_blocks`, `::test_future_approval_timestamp_blocks` |
| 3 | Reviewer rejection, head change, missing state, GitHub ambiguity, failed or unverified revocation in either helper/integrator, and concurrent finalize all fail closed | pass | `FailClosedTests`, `UnreadableStateTests`, `ApprovalBindingTests::test_pre_dated_head_replacement_is_refused`, the task-finalize/safe-pr failed-revocation shell regressions, `IntegratorGateTests::test_successful_revocation_that_still_reads_armed_never_merges`, `::test_unreadable_revocation_readback_never_reaches_the_merge`, `::test_nonzero_revocation_can_continue_only_when_readback_proves_off`, `::test_concurrent_open_prs_for_one_task_branch_fail_closed`, `--match-head-commit` on the merge call |
| 4 | Tasks explicitly governed as merge-then-review retain their documented integration behavior | pass | `PolicyResolutionTests`, `IntegratorGateTests::test_merge_then_review_task_keeps_its_documented_behavior`, `TaskFinalizeShellTests::test_merge_then_review_task_still_enables_auto_merge` |
| 5 | Regression fixtures cover PRs #4212, #4213 and #4214 without impersonating owner or reviewer | pass | `PrematureMergeRegressionTests` (recorded state replayed as data only) |
| 6 | Focused workflow tests cover branch, commit, push, PR, checks, independent review, merge, and uppercase production-shape evidence archive | pass | `validation.txt`, `UnreadableStateTests::test_archived_task_row_is_still_gated` |

AC5 is met as written and then extended: `LiveMergeGovernanceRegressionTests`
covers the eight later live regressions (#4201, #4217, #4222, #4225 auto-merge
enable, #4225 direct merge, #4226, #4227, #4230) under the same data-only
convention.
The full map of PR → entry point → fixture is the `live_regressions` table in
`evidence.json`, and the per-entry-point controls are
`merge_entry_point_coverage`.

## 5. Validation

See `validation.txt` for the captured transcript.

This pass ran against authoritative `origin/dev`
`e1512d207d9b5df3739ac7b7d0cac202b2798ac8` and validated tree
`7fb2f318783114d7cbd8ecd981390e84f2af355a`.

```
.venv-pantheon/bin/python3 scripts/git/test_task_review_merge_gate.py
                                                       Ran  87 tests - OK
.venv-pantheon/bin/python3 scripts/git/test_auto_integrator.py
                                                       Ran   9 tests - OK
.venv-pantheon/bin/python3 scripts/git/test_git_workflow_helpers.py
                                                       Ran  52 tests - OK
.venv-pantheon/bin/python3 -m pytest -q scripts/git/test_task_git_helpers_refspec.py
                                                        2 tests - OK
.venv-pantheon/bin/python3 scripts/git/test_task_pr_triage.py
                                                       Ran  24 tests - OK
.venv-pantheon/bin/python3 scripts/git/test_index_safety.py
                                                       Ran  17 tests - OK
.venv-pantheon/bin/python3 scripts/test_ai_status.py   Ran 142 tests - OK
four exact integrator revocation-readback cases           all OK
bash -n task_finalize.sh safe_pr.sh nightly_publish.sh        syntax ok
py_compile task_review_merge_gate.py auto_integrator.py ai_status.py
                                                        compile ok
git diff --check origin/dev...HEAD                         clean
pytest combined focused matrix                              333 passed, 31 subtests
pytest post-dev-compose matrix                              341 passed, 45 subtests
check_commit_trailers origin/dev..HEAD --skip-merge         ok
```

The pre-fix reproduction is `prefix-reproduction.txt`; its §1 shows the old
helper enabling auto-merge on an unreviewed task and §2 shows the new helper
refusing on the identical fixture. §3 replays PRs #4212/#4213/#4214 and §4
replays the eight later live regressions. §5 reproduces the two 2026-07-27
review findings against the modules as they stood at reviewed head `190fb7fe8`,
§6 shows the same two fixtures refused after the fix, and §7 is the verbose run
of the regressions that pin them. The later `5a9ad1643` archive/revocation
findings are covered by the uppercase archive fixture and five helper-state
tests. The `dcd4b9ccf` zero-exit/still-armed finding and its unreadable and
nonzero/already-off boundary cases are reproduced in §8 and pinned by the three
additional integrator regressions in the 87-test gate suite.

## 6. Residual risks

**The governed command runtime has a bootstrap boundary.** At the latest
revalidation `PANTHEON_COMMAND_RUNTIME_SHA` was
`6692d51c9bc5a48ffcbaac8cf817b635351a7c9a`; its `scripts/ai_status.py` did
not yet contain the `REVIEW_PR` / `REVIEW_HEAD_SHA` binding added by this task.
PR #4218 must therefore keep auto-merge disabled, receive independent Codex2
review against the exact pushed head, and merge only that head. The repository
delivery does not prove live activation: the installed runtime remains
unproven until its normal refresh reports a merged SHA containing this change.

**Gated PRs need an integrator pass to land.** Under `review_before_merge`
nothing merges the PR automatically. After approval the merge comes from
`auto_integrator.py --execute`, whether run by cron
(`scripts/run-auto-integrator.sh`) or invoked directly. If that lane is not
running, approved PRs sit open rather than merging. This is the intended
fail-closed direction — an unmerged approved PR is recoverable, a merged
unreviewed commit is not — but it is a real operational dependency and both
helpers now print the exact command.

**Approvals recorded before this change cannot open the gate.** They carry no
`review_binding`, so a gated PR whose only approval predates this delivery
blocks with `approval_head_binding_missing` until the reviewer re-approves
naming the head. This is a one-time cost paid in re-approvals, and it fails in
the safe direction, but it does mean any task sitting in `review_approved` with
an open PR at cutover needs one extra reviewer action.

**A failed or unverified revocation stalls an otherwise mergeable PR.** When
`gh pr merge --disable-auto` fails for an environmental reason — a `gh` auth
blip, a transient GitHub error — or when the follow-up read still reports an
armed request, an approved, green PR now blocks rather than merging. That is
deliberate: the alternative is merging while a grant we could not prove
withdrawn is still armed. The same rule applies while either PR-opening helper
is withholding auto-merge. The recovery is to revoke the request and rerun the
helper or integrator; if another actor already revoked it, readback proves
`autoMergeRequest=null` and the helper may continue.

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

**A standing auto-merge request costs a revocation and readback.** An
already-off gated PR is a no-op. When a request is present, the helpers and
integrator issue `gh pr merge <n> --disable-auto`, then verify it is absent.
An approved head whose request predates it is refused outright; that refusal
costs one integrator cycle, after which no request remains and the approved
head can merge.

**The gate reads canonical state, not GitHub reviews.** Pantheon reviewers are
agents that approve through `scripts/ai-status.sh approve`, and all task PRs are
authored by one GitHub account, so `reviewDecision` is structurally empty. The
gate therefore trusts the governed status wrapper and its audit, which are
themselves protected by the command-root binding checks in `ai_status.py`.
