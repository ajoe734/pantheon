# SUP-WORKER-TRUTH-RECONCILE-001 — Supervisor worker-truth reconciliation

Task: Reconcile supervisor worker truth without config mutation
Owner: Claude · Reviewer: Codex2 · Phase: Supervisor Runtime Repair

Scope rule honoured throughout: **no `.orchestrator/config.json` edit and no
hand-edited task board.** Every behaviour below is decided inside
`.orchestrator/supervisor.py` and every task-state write goes through the
existing locked canonical transaction (`write_status` → authoritative
task-state journal → `sync_status_pipeline`).

## 0. Review history

| Round | Delivery | Decision |
|---|---|---|
| 1 | PR #4212, merged into `dev` as `8703d1f5d` | **Rejected** by Codex2 |
| 2 | PR #4215, cut from `dev` `f687d7aeb` | pending independent Codex2 review |

Codex2 accepted the round-1 repairs for the `allowed_warning` rate-limit
classifier (§1.1) and the live auth-probe lane hold (§1.2); both stay exactly as
merged and are untouched by this round. Codex2 rejected the **ownerless
`in_progress` reconciliation** (§1.3) as unsafe, and this round rebuilds its
evidence rule (§1.5, §2.1). The merged runtime must not be loaded into the live
supervisor until this follow-up merges.

Mid-round, Codex2 audited anchor `051eef7c0` and accepted the identity /
timestamp / exact-head binding as materially safer, but found that an
ancestry-only gate can never see a **squash-merged** delivery — correct but
permanently inert for that shape, which brings the §1.3 redispatch loop straight
back. That is §1.6, answered by anchor `6b6eefa59` and §2.2.

## 1. Observed failures and where they came from

### 1.1 A nonthrottling `rate_limit_event` was read as a worker failure

*Repaired in round 1, merged as `8703d1f5d`, unchanged by this round.*

Live worker log
`/home/lupin/pantheon-ci-deploy/dev-root-bdbd0a99bf68/.orchestrator/logs/20260726T185418034797Z-claude1-2-claude1_2-08dcb8.log`
contains exactly one rate-limit record:

```json
{"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1785153600,"rateLimitType":"seven_day","utilization":0.83,"isUsingOverage":false,"surpassedThreshold":0.75},"uuid":"466e8308-da86-4dbd-a188-985b8558a428","session_id":"30c27323-d5f9-41ec-8d84-6ea882f1ba15"}
```

`status` is `allowed_warning` — the request was **served**; only `rejected`
means throttled. `is_allowed_rate_limit_event` accepted the literal string
`"allowed"` only, so the line fell through to
`WORKER_FAILURE_PATTERNS` (`"type"\s*:\s*"rate_limit_event"`),
`detect_worker_failure` returned it as the failure reason, and
`classify_worker_failure` matched no quota/capacity marker and returned
`kind="terminal"` → `FailureResponse.REASSIGN`. A healthy worker was recorded
as failed and its task reassigned away from an owner that never failed.

### 1.2 A fresh not-ready auth probe did not keep the lane unavailable

*Repaired in round 1, merged as `8703d1f5d`, unchanged by this round.*

`refresh_provider_auth_before_dispatch` force-probes the selected provider at
launch time but only mutated the **in-cycle** capability report. The persisted
`provider_capabilities.json` still said `auth_ready: true`, so the next tick's
`build_provider_capabilities` saw `previous.ready is True`, judged the probe not
due (`probe_interval_seconds`, not `failed_probe_interval_seconds`) and reused
the cached ready record. The lane was dispatchable again with no account having
recovered. Separately, an auth dispatch pause that was not marked *sticky*
expired purely on `auth_pause_seconds` wall-clock in
`expire_provider_dispatch_pauses` / `current_provider_dispatch_pause`, and
`reconcile_provider_auth_recovery` accepted a **cached** `auth_ready: true` as
recovery for any non-sticky auth pause.

### 1.3 Ownerless `in_progress` rows were redispatched to their owner forever

At 2026-07-26T19:39Z the live board carried 10 `in_progress` tasks while
`.orchestrator/state.json` held 2 running workers — seven ownerless
`in_progress` rows plus the two live lanes and this task. When an owner worker
merges its delivery and exits, nothing moves the task row off `in_progress`:
`poll_worker_completion_stage` only records a generic-exit failure streak, and
`dispatch_ready_tasks` keeps reading the row as owned work and re-waking the
same owner with `owned_in_progress_dispatch` every cycle, even though there is
nothing left for it to implement.

### 1.4 Queue and lease truth had to stay consistent with that decision

*Repaired in round 1, merged as `8703d1f5d`, unchanged by this round.*

Any supervisor-side reconciliation of an ownerless task must also settle the
worker's queue record and release its lease, or the queue keeps a `started`
record with a `lease_owner` pointing at a dead run id.

### 1.5 The round-1 reconciliation accepted evidence that was not its own

*Reported by Codex2's independent review of PR #4212. This is the failure this
round repairs.*

The merged `merged_delivery_commits` grepped the **whole integration base** for
any commit carrying `Task-ID: <id>`, with no ancestry, delivery, or time
binding, and `merged_owner_delivery_evidence` never compared the terminal
worker's target identity to the task row's **current** owner. Three concrete
false positives follow directly:

1. **Reopened same id.** A task delivered and merged in an earlier round, then
   reopened, still carries that round's trailer commits on `dev`. Any later
   clean worker exit satisfied the grep and moved the reopened task to `review`
   with nothing new delivered.
2. **Reassigned owner.** `latest_owner_worker_for_task` returns the most recent
   owner-dispatch worker regardless of who that worker was dispatched as. After
   a reassignment, the previous owner's terminal worker was still read as
   evidence about the new owner's work.
3. **Clean no-op rerun.** A re-dispatched worker that exits successfully without
   committing anything has a worktree head equal to the already merged tip. The
   merged code never looked at the head at all, so it could not tell that run
   apart from one that actually delivered.

### 1.6 Exact ancestry alone cannot see a squash-merged delivery

*Reported by Codex2's mid-round audit of anchor `051eef7c0`. This is the second
failure this round repairs.*

Binding to the worker's exact delivery head is the right instinct, but a squash
merge deliberately **rewrites** that head into a new commit on the base. The
worker's head is therefore not an ancestor of `dev`, and never will be — so
`_git_commit_is_ancestor(head, dev)` stays false forever even though the
delivery is real and complete.

The live example on this repository is PR #4213:

| | |
|---|---|
| `headRefOid` | `9e484e2522cd8778b85a4c880e4cd33d07ef401f` |
| squash-merged to | `0410a89f0e4ac3c53e7bc5192aebe6925423b4da` on `dev` |
| `mergedAt` | `2026-07-26T20:18:15Z` |

Failing closed here is safe but useless: the phase would be permanently inert
for every squash-merged task, and §1.3's redispatch loop returns for that shape.

## 2. Delivered behaviour

### 2.1 Binding the evidence to one delivery and one owner (repairs §1.5)

The reconciliation phase itself is unchanged in shape. What changed is what it
is allowed to accept as evidence: it must now be able to name **one specific
delivery by one specific current owner**, and it must prove every link.

| # | Change | Location |
|---|---|---|
| 1 | `worker_target_agent_display_name` — resolves the display name the worker was dispatched as, **through the agent registry**. `display_name_for` echoes an unknown id back, so an id absent from `config["agents"]` is treated as unresolved rather than accepted as its own name | `supervisor.py` |
| 2 | `worker_delivery_head_commit` — the commit the worker's own isolated worktree was last observed at (`work_progress_snapshot.commit_sha`), requiring a full 40–64 hex sha | `supervisor.py` |
| 3 | `worker_dispatch_started_at` — the dispatch time (`lease_acquired_at`, then `runner_started_at` / `started_at`) that bounds what this run may claim | `supervisor.py` |
| 4 | `_git_commit_is_ancestor` — a positive `git merge-base --is-ancestor` answer; any non-zero exit or transport failure reads as "not proven merged" | `supervisor.py` |
| 5 | `merged_delivery_commits` now takes **required** keyword arguments `delivery_head` and `since`; the head must be an ancestor of the integration base, and the trailer search runs over that head with `--since=<dispatch time>` | `supervisor.py` |
| 6 | `_merge_commit_carrying_head` — records the oldest merge on the ancestry path from the head into the base, for audit. A fast-forward merge legitimately has none, so this is recorded and not gated | `supervisor.py` |
| 7 | `task_branch_has_unmerged_commits` now fails closed and also blocks when a surviving branch has moved **past** the delivery head; a git failure is read as unmerged | `supervisor.py` |
| 8 | `merged_owner_delivery_evidence` takes a required `owner` and gates on identity, dispatch time, observed commit progress this run, delivery head, merged ancestry, trailer-since, and branch state — returning the whole chain as the recorded evidence | `supervisor.py` |

Two notes on what was deliberately **not** used as the binding:

- **`pr_url` is not authoritative.** It is scraped from provider output. The
  live `.orchestrator/state.json` at 2026-07-26T20:21Z held
  `"https://github.com/ajoe734/pantheon/pull/4170\\\"\\n"` on a worker whose
  actual delivery head was `8703d1f5d` — a malformed string naming an unrelated
  PR. Binding to it would have bound the wrong PR. It is recorded in the
  evidence with `pr_url_is_authoritative: false` and pinned by
  `test_scraped_pr_url_is_never_the_delivery_binding`.
- **The candidate worker is not pre-filtered by owner.** Only the *latest*
  owner-dispatch worker is ever considered, and it must itself match the current
  owner. Filtering the candidate list by owner first would let a reassignment
  fall back to an older worker that happens to match; leaving the filter at the
  gate makes a reassignment fail closed instead.

### 2.2 Recognising both delivery shapes (repairs §1.6)

`merged_delivery_commits` now recognises two shapes and binds each to this exact
worker. Ancestry is tried first because it is local, free, and needs no network;
the PR-metadata path runs only after it fails.

| Shape | How it is proven |
|---|---|
| `merge_ancestry` | The delivery head is an ancestor of the integration base, and a `Task-ID:` trailer commit reachable from that head is dated at or after the worker's dispatch. The merge commit carrying the head into the base is recorded when there is one. |
| `squash_pr_metadata` | Authoritative GitHub PR metadata, because the head was rewritten and git ancestry can never see it. |

The squash binding (`squash_merged_delivery_metadata`) requires **all** of:

- exactly **one** merged PR whose `headRefOid` equals this worker's delivery
  head — no match, or more than one, fails closed;
- `state` `MERGED`;
- `baseRefName` equal to the expected integration branch;
- `mergedAt` at or after the worker's dispatch;
- a `mergeCommit` present locally and an ancestor of the base ref;
- that merge commit itself carrying this task's `Task-ID:` trailer and dated at
  or after the dispatch (`git log --no-walk`, so the search stays on the
  squashed commit rather than walking its whole ancestry).

The task branch name is **only the lookup key** for
`gh pr list --head <branch> --state merged` — it is never the evidence. A task
id alone never implies a squash, and provider prose and `pr_url` are never
consulted. `_merged_pull_requests_for_branch` returns `None` — never an
empty-but-trusted answer — when the lookup is disabled, `gh` is missing, the
`origin` remote is not a resolvable `github.com` `owner/repo`, the process exits
non-zero, it times out, or the payload is not a JSON list.

One consequence for the branch check: a squash-merged branch is *legitimately*
never an ancestor of the base, so the base comparison is skipped for that shape
only. Movement past the delivery head still blocks, and the merge itself is
proven by the PR metadata instead.

### 2.3 Round 1 — merged as `8703d1f5d`, unchanged by this round

| # | Change | Location |
|---|---|---|
| 1 | `rate_limit_info_payload` / `is_allowed_rate_limit_event` accept both `allowed` and `allowed_warning`, and read the envelope nested under `message` too | `supervisor.py` |
| 2 | `is_allowed_rate_limit_line` gives the same verdict textually, so a truncated stream record cannot be re-read as a failure | `supervisor.py` |
| 3 | `classify_worker_failure` returns a `transient`, never-pausing kind for a nonthrottling notice reached through a stored `last_error` | `supervisor.py` |
| 4 | `auth_pause_requires_live_probe` — an auth pause raised from a fresh live not-ready probe holds the lane until a later **live** successful probe; wall-clock expiry no longer reopens it | `supervisor.py` |
| 5 | `mark_provider_auth_probe_not_ready` records that hold in supervisor state, and the flipped capability report is persisted so the next scan re-probes on the failed-probe interval | `supervisor.py` |
| 6 | `reconcile_provider_auth_recovery` requires a live probe success for every live-probe-gated auth pause (previously sticky-only) | `supervisor.py` |
| 7 | `reconcile_ownerless_in_progress_tasks` — new cycle phase between `prune_event_queue` and `refresh_chair_review_state` | `supervisor.py` |
| 8 | `_prepare_ownerless_review_handoff_locked` — locked canonical transaction that sets `review`, appends the pending owner→reviewer handoff, emits a `task_ownerless_review_handoff` outbox event, and journals through `write_status` before `sync_status_pipeline` | `supervisor.py` |
| 9 | The same reconciliation calls `finalize_queue_event_record`, so the queue record and lease settle with the task decision | `supervisor.py` |

### Non-interference guarantees

The reconciliation returns without touching a task when **any** of these hold:

- a worker for the task is in an active status, or its pid is still alive;
- a queue record for the task is `queued` / `pending` / `started` / `stalled` /
  `retry_backoff` (a dispatch is in flight);
- no terminal owner-dispatch worker record exists;
- the terminal outcome is not a clean `completed` runner exit;
- **the latest owner-dispatch worker was not dispatched as the task's current
  owner, or its agent id does not resolve through the agent registry;**
- **the worker has no dispatch timestamp;**
- **the worker recorded no commit progress during this run** (a clean rerun over
  an already merged branch delivered nothing);
- **the worker has no full-sha delivery head;**
- **that delivery head is neither an ancestor of the integration base nor the
  `headRefOid` of exactly one merged PR into that base** (unpushed work, or a
  delivery no recognised shape can prove);
- **no `Task-ID:` trailer commit reachable from that head is dated at or after
  the worker's dispatch** (older-only merged commits from a previous round), or
  for a squash, the squashed commit itself carries no such trailer;
- **the PR metadata disagrees on any field** — wrong head, wrong base, not
  merged, merged before the dispatch, or a merge commit that is not on the base;
- a surviving task branch is ahead of the base **or has moved past the delivery
  head**;
- **any git command fails, or the PR lookup cannot be answered** — a transport
  error, a timeout, a missing `gh`, or unparseable metadata never reads as
  "merged";
- owner or reviewer is missing, or reviewer equals owner;
- the locked re-read no longer shows `in_progress` with the same owner/reviewer.

It is also capped at `max_transitions_per_tick` (default 4) and marks the worker
record so the same task is never handed off twice.

## 3. Verification

Commands run from `.orchestrator/` in the task worktree
`/tmp/pantheon-worker-worktrees/pantheon/sup-worker-truth-reconcile-001`.

### 3.1 Failures reproduced against the merged round-1 supervisor

The tests on this branch were run against `origin/dev:.orchestrator/supervisor.py`
— that is, against the **merged round-1 code Codex2 rejected** (`f687d7aeb`,
which contains PR #4212 / `8703d1f5d`) — with the rest of `.orchestrator`
unchanged:

```bash
git show origin/dev:.orchestrator/supervisor.py > <scratch>/orch/supervisor.py
env -C <scratch>/orch python3 -m unittest \
  test_supervisor.OwnerlessInProgressReconciliationTests \
  test_supervisor.MergedDeliveryEvidenceTests \
  test_supervisor.SquashMergedDeliveryEvidenceTests \
  test_supervisor.MergedPullRequestLookupTests \
  test_supervisor.WorkerDeliveryIdentityTests
```

Result: `Ran 55 tests` → `FAILED (failures=6, errors=38)` — 44 of 55 fail.
The six behavioural failures are the merged code actually producing the false
positives Codex2 described (each asserts `changed is False` and gets `True`,
i.e. the row was moved to `review` on evidence it should not have accepted):

- `test_reassigned_owner_blocks_the_previous_owners_worker`;
- `test_rerun_without_commit_progress_is_not_evidence`;
- `test_unregistered_worker_identity_fails_closed`;
- `test_worker_without_a_delivery_head_fails_closed`;
- `test_worker_without_a_dispatch_timestamp_fails_closed`;
- `test_squash_merged_delivery_reconciles_and_skips_base_comparison`.

The 38 errors are the merged code having no binding entry point at all —
`TypeError` on the keyword-only `delivery_head` / `since` arguments, and
`AttributeError` on helpers that do not exist there.

Full transcript, including the fixed run and the full suite:
`prefix-reproduction.txt`.

### 3.2 Focused suites after the fix

```bash
env -C .orchestrator python3 -m unittest \
  test_supervisor.OwnerlessInProgressReconciliationTests \
  test_supervisor.MergedDeliveryEvidenceTests \
  test_supervisor.SquashMergedDeliveryEvidenceTests \
  test_supervisor.MergedPullRequestLookupTests \
  test_supervisor.WorkerDeliveryIdentityTests
# Ran 55 tests — OK
```

### 3.3 Full supervisor suite

```bash
env -C .orchestrator python3 -m unittest test_supervisor
# Ran 399 tests — OK   (357 after round 1, +42 this round)
```

### 3.3b Live binding against this repository — not a mock

`merged_delivery_commits` was run against the real `pantheon` checkout and the
real GitHub PR metadata, for both shapes and their negatives:

| Input | Result |
|---|---|
| PR #4213 head `9e484e252`, dispatch `2026-07-26T19:53:14Z` | `squash_pr_metadata`, merge commit `0410a89f0`, base `origin/dev`, `mergedAt 2026-07-26T20:18:15Z` |
| same task, delivery head `ffff…` | `None` |
| same task and head, dispatch `2026-07-26T23:00:00Z` (after the merge) | `None` |
| PR #4212 head `0ffc9404c`, dispatch `2026-07-26T18:00:00Z` | `merge_ancestry`, merge commit `8703d1f5d`, base `origin/dev` |
| same task and head, dispatch `2026-07-26T21:00:00Z` | `None` |

Full output: the `LIVE BINDING` section at the end of
`prefix-reproduction.txt`.

### 3.4 Rewrite package suite

```bash
env -C .orchestrator python3 -m unittest discover -s rewrite -p "test_*.py" -t .
# Ran 97 tests — 95 pass; the same 2 pre-existing collection errors as round 1
# (rewrite/test_task_state_runtime_env.py and rewrite/test_task_state_store.py
#  import pytest, which is not installed in this environment — unrelated to this task)
```

## 4. Negative tests requested by the reviewer

| Required case | Tests |
|---|---|
| Reopened same id | `test_older_only_merged_trailer_commits_are_not_this_delivery`, `test_rerun_without_commit_progress_is_not_evidence` |
| Reassigned owner | `test_reassigned_owner_blocks_the_previous_owners_worker`, `test_unregistered_agent_id_is_unresolved_not_echoed` |
| Stale terminal worker plus new work | `test_stale_terminal_worker_with_new_branch_work_is_not_reconciled`, `test_branch_moved_past_the_delivery_head_reports_unmerged_commits` |
| Deleted branch / unpushed work | `test_deleted_task_branch_reports_no_unmerged_commits`, `test_task_branch_ahead_of_base_reports_unmerged_commits`, `test_delivery_head_not_merged_into_the_base_is_not_evidence` |
| Older-only merged `Task-ID` commits | `test_older_only_merged_trailer_commits_are_not_this_delivery`, `test_trailer_commit_reachable_from_the_delivery_head_is_merged_evidence` (pins both the `--since` and the head-scoping arguments) |
| Fail-closed absent linkage | `test_worker_without_a_delivery_head_fails_closed`, `test_worker_without_a_dispatch_timestamp_fails_closed`, `test_unregistered_worker_identity_fails_closed`, `test_missing_delivery_head_or_since_fails_closed`, `test_git_log_failure_fails_closed`, `test_rev_list_failure_reports_unmerged_commits` |

And for the squash shape (`SquashMergedDeliveryEvidenceTests`,
`MergedPullRequestLookupTests`):

| Required case | Tests |
|---|---|
| Squash positive, live #4213 shape | `test_live_4213_squash_shape_binds_through_pr_metadata`, `test_squash_merged_delivery_reconciles_and_skips_base_comparison` |
| Normal merge / fast-forward positive | `test_trailer_commit_reachable_from_the_delivery_head_is_merged_evidence`, `test_fast_forward_merge_without_a_merge_commit_still_binds`, `test_ancestry_shape_is_preferred_and_skips_the_pr_lookup` |
| Wrong PR head | `test_wrong_pr_head_is_not_this_delivery` |
| Wrong base | `test_wrong_base_branch_fails_closed` |
| Pre-dispatch merge | `test_merge_before_the_worker_was_dispatched_fails_closed`, `test_unmergeable_or_unparseable_merged_at_fails_closed` |
| Unrelated merge commit | `test_unrelated_merge_commit_fails_closed`, `test_merge_commit_without_this_tasks_trailer_fails_closed`, `test_missing_merge_commit_oid_fails_closed`, `test_pr_not_actually_merged_fails_closed` |
| Deleted branch | `test_deleted_branch_with_no_merged_pr_fails_closed`, `test_squash_delivery_does_not_require_branch_ancestry` |
| GitHub lookup failure | `test_github_lookup_failure_fails_closed`, and all of `MergedPullRequestLookupTests` (disabled, gh missing, unknown repository, non-zero exit, timeout, unparseable or non-list JSON) |
| Multiple PRs for the same task id | `test_multiple_prs_for_the_task_resolve_by_exact_head`, `test_ambiguous_duplicate_head_metadata_fails_closed` |

## 5. Acceptance mapping

| Acceptance item | Covered by |
|---|---|
| Seven ownerless `in_progress` fixtures reconcile from terminal outcome and durable worker evidence without resetting any live worker | `OwnerlessInProgressReconciliationTests` — seven per-fixture tests plus `test_seven_ownerless_fixtures_reconcile_in_one_pass` |
| Allowed `rate_limit` warning remains nonterminal and redispatchable | `AllowedRateLimitNoticeTests` (6 tests, live payload) |
| Fresh provider `auth_ready` false keeps the lane unavailable until a later fresh successful probe without any config edit | `FreshAuthProbeLaneHoldTests` — asserts the config dict is byte-identical after the whole cycle |
| Merged owner delivery with no implementation remaining transitions through governed review handoff instead of repeated owner redispatch | `test_merged_owner_delivery_moves_to_governed_review_handoff`, `test_reconciled_evidence_records_the_bound_delivery`, `test_squash_merged_delivery_reconciles_and_skips_base_comparison` (both delivery shapes) |
| Authoritative event log projection and queue worker lease state remain consistent under concurrent outcomes | `write_status` journals before the projection write; the locked re-read rejects a raced row; `finalize_queue_event_record` settles the queue record and lease (asserted in fixture 1) |
| Focused supervisor tests reproduce all observed failures and pass | §3.1 / §3.2 |
| Branch, commit, push, PR checks, independent Codex2 review, merge, evidence archive | this follow-up task PR — **in progress**, awaiting independent Codex2 review |

## 6. Residual risks

| Risk | Severity | Containment |
|---|---|---|
| The squash shape is the only one needing a network answer; `gh` may be unavailable, unauthenticated, rate-limited, or slow | low | Fail-closed and retried — the task stays on owner redispatch and the next cycle re-runs the lookup. An unanswered lookup never reads as merged, and `merge_ancestry` needs no network and is always tried first. |
| A PR merged with GitHub's **rebase** strategy also rewrites the commits and produces no single merge commit to bind to, so neither shape recognises it | low | Fail-closed by design — the task stays on the existing owner-redispatch path. This repository's task PRs use merge commits and squash, both covered. Enabling rebase merging would need a third shape (binding each rebased commit's trailer and patch-id), not a relaxed gate. |
| A supervisor root that has not fetched recently sees a merged delivery later than GitHub does | low | A stale ref can only delay a handoff, never manufacture one. |
| Evidence relies on the `Task-ID:` commit trailer enforced by `.githooks/commit-msg` | low | Trailer-exempt subjects simply produce no evidence. |
| The run binding relies on `update_worker_commit_progress` having observed the worktree advance; a delivery that landed entirely between two polls records no progress | low | Fail-closed — no observed progress means no transition. It cannot create a false positive. |
