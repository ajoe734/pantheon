# Auto Integrator Contract

Status: conservative first implementation for `OPS-AUTO-INTEGRATOR-001`.

`scripts/git/auto_integrator.py` is a serialized lane for the final task-PR
integration step:

```text
review_approved task OR active canonical merge_then_review task
-> clean task PR into dev -> local rebase smoke -> merge or unblock task
```

## Safety Model

- Default mode is dry-run. `--execute` is reserved for the scheduled canonical
  supervisor integration runner; workers, reviewers, and PR helpers never use
  it.
- A kernel `flock` at `.orchestrator/auto-integrator.lock` permits one
  integration pass at a time. The file retains PID/owner metadata for
  diagnostics, recovers a dead legacy owner, and never displaces a live owner.
  A concurrent scheduled pass is a bounded successful skip with
  `reason=integration_lock_held`; malformed or unusable lock state remains a
  nonzero `integration_lock_error`.
- Review-before-merge rows are eligible only at `status=review_approved`.
  Active `in_progress`/`review` rows are also eligible when canonical policy
  resolution explicitly honors `merge_then_review` (owner and reviewer are not
  independent). Approved rows are evaluated first so an unfinished
  merge-then-review row cannot starve them. `ReviewGate` performs the final
  policy decision against the live PR before merge.
- Repository scope is resolved per task via `.orchestrator/multi_repo_registry.py`:
  derives repository ID (`pantheon`, `execute_plans`), GitHub slug (`ajoe734/pantheon`,
  `ajoe734/execute-plans`), local checkout root, and target branch (`dev`).
  Path authority is anchored in `PANTHEON_STATUS_ROOT` / status file, ensuring
  sibling repository paths (e.g. `../code/execute-plans`) resolve against the
  canonical coordination root.
- While holding the integration lock, preflight verifies that the target root
  exists, is an absolute clean Git repository root, both the checkout and Git
  common dir are writable, and its origin matches the configured repository
  slug. Missing, dirty, read-only, invalid, or mismatched checkouts fail closed
  before PR review or merge operations.
- The PR head must be `task/<TASK-ID>` and the base must match the target repository's
  configured default branch (e.g. `dev`).
- The PR URL's GitHub slug must match the candidate's resolved repository slug.
  A mismatched slug, unrecognized `target_repo`, or conflicting multi-repository
  artifacts fails closed with a blocking unblock task (`invalid-repository-scope` or
  `repository_mismatch`).
- All git fetch/rebase/smoke operations and GitHub CLI merge calls execute in the
  resolved local repository root for that candidate, while canonical status tracking
  and unblock task creation remain anchored in `PANTHEON_STATUS_ROOT`.
- Draft PRs, truly missing PRs, required failing checks, missing checks, dirty merge
  states, and rebase conflicts are not merged.
- Status check classification uses a data-driven required-versus-diagnostic
  classifier: a check is ignored only when it is positively identified as
  non-required (`isRequired: false` in GraphQL context nodes) **and** its
  `workflowName` identifies the known read-only diagnostic issuer. Missing,
  ambiguous (`isRequired` omitted/None), required (`isRequired: true`), missing
  or unknown workflow provenance, stale-head, review-binding, trailer, or
  actual CI failures continue to block fail-closed. In particular, an optional
  Branch CI job remains blocking when it fails.
- The `gh pr view` rollup is enriched from GitHub GraphQL by check type plus
  check name/context. An unavailable query, malformed response, unmatched
  context, or conflicting duplicate identity cannot downgrade a check: the
  context stays ambiguous and blocking, and duplicate identities are required
  when any matching node is required.
- Dry-run and successful merge results name every explicitly non-required
  diagnostic excluded from blocking so the classifier decision is visible in
  task evidence instead of being a silent allow-list.
- `mergeStateStatus: UNSTABLE` (GitHub's status when required checks pass but
  optional diagnostic checks fail) is recognized as an eligible merge state when
  all required status checks pass.
- Merge authority is delegated to `scripts/git/task_review_merge_gate.py`.
  Every policy is pinned to the exact PR head used for its gate, checks, and
  smoke. The integrator never enables GitHub auto-merge, never rewrites that
  head, and revokes any standing auto-merge request before proceeding. A
  review-before-merge task additionally requires that exact head's canonical
  reviewer approval.
- Two open PRs claiming the same task branch fail closed instead of resolving
  to the first row.
- If the open PR is already gone because GitHub merged it, the integrator
  verifies the merged PR's merge commit is already in `origin/<target-branch>`
  and reports `already_merged`, preserving canonical task status without
  opening spurious unblock tasks or mutating task status to `done`.
- The integrator never resolves conflicts and never bypasses branch protection.
- Blockers publish a content-addressed request under the canonical status
  root's `.orchestrator/auto-integrator-unblock-inbox/`. The publisher has no
  generic status-command identity and never calls `assign` or `progress`.
  The request binds the canonical status-root identity, promoted immutable
  command-runtime SHA, source task generation, repository ID/slug, frozen PR
  and head, owner/reviewer, allow-listed reason, and exact generated
  `INTEGRATION-UNBLOCK-*` namespace.
- Only the supervisor consumes that inbox. It revalidates every binding against
  current canonical task truth and its own promoted runtime, then creates the
  unblock row idempotently through an isolated authoritative TaskStore
  transaction. Each cycle drains at most 32 requests. Every consumed source is
  moved to the outcome-specific durable archive and receives a
  `processed`, `rejected`, or `error` receipt; one malformed request or failed
  materialization/write cannot prevent a later valid request from being tried.
  Forged, stale, wrong-root, wrong-runtime, wrong-repository, arbitrary-reason,
  and arbitrary-task requests are rejected by an exact finite reason allowlist
  rather than an extensible regex family. A request publication failure is
  reported without discarding the candidate result or aborting the pass.

## Merge Flow

For each eligible task, capped by `max_tasks_per_run` after observational
`waiting`, `not_ready`, and `already_merged` results are skipped:

1. Read the task row from canonical `ai-status.json` (`PANTHEON_STATUS_ROOT` or `--status-file`)
   and resolve repository scope (`repository_id`, `repository_slug`,
   dedicated `integration_path`, `target_branch`). Live execution refuses a
   repository without an explicit integration path.
2. Find the open PR for `task/<TASK-ID>` into the candidate's `target_branch` within `repository_root`.
3. If no open PR exists, check for a merged PR from the same head/base whose
   merge commit is already in `origin/<target-branch>`; if found, report `already_merged`
   and leave the task in `review_approved` for owner finalization.
4. If an active merge-then-review row has not opened a PR yet, report
   `not_ready` and continue scanning. Other missing-PR cases create an unblock
   task in `PANTHEON_STATUS_ROOT`.
5. Evaluate the review-before-merge gate against this exact PR head. Any
   pending auto-merge request on a gated PR is revoked here, before the CI and
   merge-state probes, whatever the gate decided - a PR that ends up `waiting`
   because it is `BEHIND` must not keep one. A gated PR that is not approved is
   then blocked at this step.
6. Read `autoMergeRequest` back after every revocation attempt. The command
   exit status is diagnostic only: zero can leave the grant armed, while
   nonzero can race with another actor that already turned it off. If the
   readback is unavailable or still armed, block before any merge call is
   emitted - approval of this head does not make it safe to merge alongside a
   standing grant.
7. Require green GitHub status rollup.
8. Fetch `origin/<target-branch>` and the task branch in the dedicated
   integration checkout.
9. Create a temporary detached worktree at the gate decision's exact head and
   run configured smoke commands. The integrator never pushes or rewrites it.
10. Reload canonical state into a fresh `ReviewGate`, refetch the PR, and
    require unchanged policy, owner, reviewer, head, and green checks.
11. Call the synchronous GitHub REST merge endpoint with `sha=<exact-head>`
    and `merge_method=merge`. Only `merged: true` is success; every refusal is
    waiting/blocked and never becomes an auto-merge or queue request.
12. After merge, preserve canonical task status. A review-before-merge row
    remains `review_approved` for supervisor `owned_finalize_dispatch`; an
    active merge-then-review row proceeds through its post-merge review/finalize
    lifecycle. The integrator never mutates canonical task state to `done`.

## Configuration

Optional settings live under `.orchestrator/config.json`:

```json
{
  "branch_workflow": {
    "auto_integrator": {
      "max_tasks_per_run": 1,
      "lock_file": ".orchestrator/auto-integrator.lock",
      "merge_method": "merge",
      "smoke_commands": [
        "python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD"
      ],
      "unblock_owner": "Codex",
      "unblock_reviewer": "Claude"
    }
  }
}
```

If no smoke commands are configured, the integrator still performs the rebase
probe and PR status checks. `--smoke-command` and `--skip-smoke` are dry-run
diagnostics only; live execution accepts smoke policy only from the promoted
watchdog config.

## Read-only CLI

Dry-run one task:

```bash
python3 scripts/git/auto_integrator.py --task-id TASK-123 --json
```

An isolated test may add `--no-lock`; production execution rejects
`--execute --no-lock`.

## Scheduled Runner

`scripts/run-auto-integrator.sh` is the supervisor-owned cron wrapper. It
defaults to `--execute --max-tasks 1`. The Python integrator derives canonical
status/config/lock authority from that live config's watchdog command and the
versioned command-runtime root; execute-mode CLI path overrides are rejected.
Workers and PR helpers do not invoke this executing entry point.

Use `AUTO_INTEGRATOR_DRY_RUN=1` for a non-mutating scheduled smoke and
`AUTO_INTEGRATOR_MAX_TASKS=<n>` to override the default one-task limit.

Install the cron runner with:

```bash
python3 scripts/auto_integrator_install.py \
  --repo /home/lupin/pantheon-ci-deploy/dev-root \
  --status-root /home/lupin/pantheon
```

The installed line is tagged `# pantheon-auto-integrator`, runs every five
minutes by default, and writes logs to
`$PANTHEON_STATUS_ROOT/.orchestrator/logs/auto-integrator-cron.log`.

## Non-goals

- No automatic conflict resolution.
- No admin merge or branch-protection bypass.
- No broad batching; first version is intentionally serialized.
- No publish/master promotion. “Sole task merge owner” is deliberately scoped
  to canonical `task/* -> dev` integration. `publish_promote.py` remains the
  separate release authority for `promote/* -> master` and may request its
  protected release auto-merge; it cannot be used as a task-PR merge path.
