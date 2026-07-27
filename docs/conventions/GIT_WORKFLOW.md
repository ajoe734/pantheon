# Pantheon Git Workflow

Status: canonical · Owner: chair-review · Last reviewed: 2026-05-17

Operational source of truth for Pantheon branching, per-task PR flow,
nightly publish, promote to master, hotfixes, and CI gates. If anything
here conflicts with `AI_COLLABORATION_GUIDE.md`, this file wins.

This document supersedes the wave-based design (2026-05-16) after a
peer review flagged that wave + permanent worker-branch does not fit
Pantheon's 24/7 multi-AI / multi-sub-lane parallel execution.

---

## 1. Branch Topology

```
master   ── PR-only ── canonical / production source
   ▲
   │ promote/<v> PR  (auto-merged after soak + CI)
   │
publish/v<YYYY>.<MM>.<DD>.<N> ── immutable snapshots from dev
   ▲
   │ nightly-publish-cut.yml  (cron hourly :00 UTC)
   │
dev      ── PR-only ── integration line, every task PR auto-merges here
   ▲                       ↑
   │ PR (auto-merge)   hotfix/<topic> ── dual-PR back to dev + master
   │
task/<TASK-ID>  ── ephemeral, auto-deleted by GitHub when PR merges
```

### 1.1 Branch types (5 total)

| Type        | Naming                                | Lifetime          | Writer                                |
|-------------|---------------------------------------|-------------------|---------------------------------------|
| canonical   | `master`                              | permanent         | PR auto-merge only (promote / hotfix) |
| integration | `dev`                                 | permanent         | PR auto-merge only (task / hotfix)    |
| task        | `task/<TASK-ID>`                      | minutes to hours  | one autoworker / human; PR + auto-delete |
| publish     | `publish/v<YYYY>.<MM>.<DD>.<N>`       | permanent (snapshot) | nightly cron after release-state discipline; immutable after cut |
| hotfix      | `hotfix/<topic>`                      | < 24 h            | one author; dual-PR (master + dev)    |

### 1.2 Tag types

| Tag                              | When set                                          | What it marks                                   |
|----------------------------------|---------------------------------------------------|-------------------------------------------------|
| `release/v<YYYY>.<MM>.<DD>.<N>`  | At publish snapshot creation                      | Immutable snapshot ref                          |
| `prod/v<YYYY>.<MM>.<DD>.<N>`     | When promote PR merges into master                | Production release marker                       |
| `archive/<branch>-<YYYY-MM-DD>`  | Before retiring any branch                        | Snapshot of branch tip prior to deletion        |
| `recovery/<timestamp>`           | Before destructive ops that may go wrong          | Manual backup point (used for rebase rescue)    |

### 1.3 Why per-task branches, not per-worker

Earlier design used one permanent `worker/<name>` branch per autoworker
(8 branches). That assumed each AI runs one task at a time. Pantheon's
reality:

- Codex / Codex2 lanes split into parallel sub-lanes.
- Sidecar dispatch spawns concurrent helper workers on the same lane.
- Multiple tasks per agent can run in parallel through the orchestrator.

A single permanent `worker/codex` cannot serve N concurrent sub-lanes
without serializing them (loses throughput) or fanning out into
`worker/codex-a/b/c` (loses simplicity). Ephemeral `task/<TASK-ID>`
branches scale naturally: one branch per task, deleted on merge, no
collision between parallel workers.

---

## 2. Task Branch Lifecycle (the main loop)

### 2.1 Open a task branch

```bash
./scripts/git/task_start.sh <TASK-ID>
```

Equivalent to:

```bash
git fetch origin dev
git checkout -B task/<TASK-ID> origin/dev
```

The helper also echoes the recommended `--index-file` path
(`/tmp/git-index-task-<TASK-ID>`) for `worker_commit.py` to use.

### 2.2 Commit task work

Workers must use `scripts/git/worker_commit.py` so staging discipline is
enforced (see § 5.4):

```bash
python3 scripts/git/worker_commit.py \
  --task-id "$TASK" \
  --message-file /tmp/$TASK-msg.txt \
  --scope <path1> <path2> ... \
  --index-file /tmp/git-index-task-$TASK
```

`--scope` is mandatory. The wrapper resets staging, stages only the
declared scope, verifies, then `git commit -F <message-file>`.

### 2.3 Open the PR

```bash
./scripts/git/task_finalize.sh <TASK-ID>
```

Equivalent to:

```bash
git push -u origin task/<TASK-ID>
gh pr create --base dev --head task/<TASK-ID> --label auto-merge \
  --title "<TASK-ID>: <subject>" --body-file /tmp/<TASK-ID>-pr-body.md
gh pr merge task/<TASK-ID> --auto --merge
```

Auto-merge holds until `dev` branch protection's required status checks
(see § 7) turn green; then GitHub merges and **auto-deletes the
`task/<TASK-ID>` branch**.

### 2.4 Lifetime guarantee

A `task/<TASK-ID>` PR should reach merge within 24 h. A task PR that
lingers > 24 h with no merge is a process violation and chair-review
surfaces it as a Finding.

### 2.5 Preemption anchors

Uncommitted worktree diffs are not durable collaboration state. Before a
worker is reassigned, interrupted, or asked to switch tasks, any
non-trivial diff must either be committed on its `task/<TASK-ID>` branch
or explicitly marked disposable in the handoff / activity note.

High-fragility surfaces must not live as session-only work:

- docs that change canonical process or product truth
- `.orchestrator/skills/*` and other skill instructions
- config and workflow files
- supervisor dispatch / routing contact points, especially
  `.orchestrator/supervisor.py`

For these surfaces, open a task branch first and create an anchor commit
as soon as the design intent is clear, even if a follow-up commit will
polish the wording or finish tests. The anchor commit must still obey
the normal subject, trailer, scope, and generated-file gates. Its commit
message should name the owned layer and any boundary it intentionally
does not change, for example:

```text
<TASK-ID>: anchor supervisor routing boundary

Touches .orchestrator/supervisor.py dispatch-slot routing only.
Does not change chair-review reassignment semantics.

LLM-Agent: Codex
Task-ID: <TASK-ID>
Reviewer: Claude
```

If `dev` advances before the work is ready to merge, rebase or merge the
task branch as a committed patch. `git stash pop` is a last-resort
recovery tool for disposable local state, not the normal path for
preserving design work across mainline movement.

Pantheon implementation:

- worker wakeup messages render the expected branch from
  `branch_workflow.task_branch_prefix` and `branch_workflow.dev_branch`
  rather than hard-coding lane-owned branch names
- `.orchestrator/skills/worker-anchor-commit.md` defines the mid-task
  anchor procedure and commit message shape
- `.orchestrator/skills/task-closeout-finalization.md` defines how
  final closeout handles prior anchor commits and unrelated dirty files
- `worker_worktrees` leases a separate git worktree per execution task
  and launches auto workers from that isolated cwd while routing
  `ai-status.sh` updates back to the supervisor root with
  `PANTHEON_STATUS_ROOT`
- `worker_tree_guard` may be enabled in warn or block mode to detect
  dirty high-fragility surfaces inside the task worktree before
  dispatch; it is disabled by default and does not auto-restore state
  files

If a downstream repo keeps a separate `branch-strategy.md`, mirror this
section there. In Pantheon, this document is the canonical branch
strategy.

---

## 3. Hourly Publish

`nightly-publish-cut.yml` runs hourly at :00 UTC and:

1. Compares `origin/dev` HEAD against the latest `release/v*` tag.
2. If `dev` advanced (new task PRs merged since last cut):
   - Runs `scripts/release_branch_discipline.py version`.
   - Reads `.orchestrator/release-state.json`; in current `per_task` mode,
     `ai-status.json.wave_state` is legacy read-only state and does not gate
     publish cuts.
   - Creates `publish/v<YYYY>.<MM>.<DD>.<N>` from `origin/dev` HEAD.
   - Pushes the branch.
   - Tags `release/v<YYYY>.<MM>.<DD>.<N>` (annotated).
   - Does **not** dispatch a deployment. A publish snapshot is an immutable
     promotion input, not proof that the exact Pantheon/execute-plans pair is
     admitted for dev. Dev delivery is a separate governed operation and keeps
     its own exact-pair gate before any switch.
3. If `dev` has not advanced, no-op.

### 3.1 Version format `vYYYY.MM.DD.N`

| Segment | Meaning                                 | Example       |
|---------|-----------------------------------------|---------------|
| `YYYY`  | Calendar year                           | `2026`        |
| `MM`    | Month (zero-padded)                     | `06`          |
| `DD`    | Day of month (zero-padded)              | `09`          |
| `N`     | Same-day patch slot, starting at 0      | `0`           |

Examples:
- `v2026.06.09.0` — first publish cut on 2026-06-09
- `v2026.06.09.1` — second publish cut on 2026-06-09

### 3.2 Manual cut

```bash
./scripts/git/nightly_publish.sh now
```

Use only when a publish must happen between cron runs (e.g. hotfix
landed and you want a fresh release tag immediately).

### 3.3 Publish snapshots are immutable

After cut, **never push commits onto a `publish/v*` branch**. To patch,
merge the hotfix through the normal path, then cut a fresh daily publish
snapshot once release discipline passes (§ 6).

---

## 4. Promote to Master

The existing `publish-promote.yml` workflow runs hourly and on every
`release/v*` push. It:

1. Discovers `release/v*` tags older than `promote.soak_days` (default
   `1`) and not yet on master (via `merge-base --is-ancestor`).
2. For each eligible candidate, opens `promote/<v>` PR into master.
3. Calls `gh pr merge promote/<v> --auto --merge`.
4. Branch protection holds the PR until status checks (§ 7.1) turn
   green; GitHub auto-merges and tags `prod/<v>` via `master-release.yml`.

### 4.1 Soak window

`soak_days = 1`: each publish snapshot lives on the dev VM for at least
24 hours before its promote PR opens. This is the window in which
`regression/<v>` issue labels can block promotion.

If a release was cut at 03:00 UTC Monday, its promote PR opens earliest
03:00 UTC Tuesday (next hourly cron pickup after the soak passes).

### 4.2 Blocking a promote

Open a GitHub issue with label `regression/v<YYYY>.<MM>.<DD>.<N>` (or any
configured `block_labels`). `publish-promote.yml` skips that candidate
until the label is removed.

### 4.3 No direct push to master

Branch protection enforces PR + 3 required status checks. There is no
operator path that bypasses this — all master entries are PR merges.

---

## 5. Commit Conventions

### 5.1 Subject

Format: `<TASK-ID>: <imperative summary>`, ≤ 70 chars.

Examples:
- `EP5-FOO-001: implement adapter for foo service`
- `MGMT-BROKER-002: use Shioaji venv for supervisor restart`

Exempt subjects (trailer check skipped):
- `Merge ` / `Revert ` / `fixup!` / `squash!` / `Initial commit`
- `promote:` / `hotfix:` / `publish:` (system actors)
- `OPS-GIT-WORKFLOW-` / `OPS-GIT-REDESIGN-` / `OPS-DOC-` / `OPS-REBASE-`

### 5.2 Required trailers

Enforced by `.githooks/commit-msg` via `scripts/git/check_commit_trailers.py`:

```
LLM-Agent: <Claude | Claude2 | Codex | Codex2 | Gemini | Gemini2 | Copilot | Qwen>
Task-ID: <task-id>
Reviewer: <name, must differ from LLM-Agent>
```

(The legacy `Wave:` trailer is **dropped** in the new model. The
trailer check tolerates legacy commits that carry it but does not
require it on new commits.)

### 5.3 Optional trailers

- `Verified: <command summary>` — required when tests / checks ran.
- `Hotfix: yes` — required on hotfix-path commits.
- `Cross-Dir: yes` — required when a single commit intentionally spans
  more than 3 top-level directories. (See § 5.4 scope guard.)

### 5.4 Staging discipline (Shared-Index footgun)

Pantheon's autoworkers and chair share one worktree, hence one
`.git/index`. The 2026-05-16 sweep-in incident (`e06f5cf2`) showed that
a stalled `git commit` leaves files staged for the next process to
absorb. Defenses:

1. **`scripts/git/worker_commit.py`** is the mandatory commit path for
   autoworkers. It `git restore --staged --` first, stages only the
   declared `--scope`, verifies, then commits. With `--index-file` it
   uses a private staging index so workers cannot collide even on
   concurrent edits.
2. **`scripts/git/check_commit_scope.py`** runs from the `commit-msg`
   hook. It reads the `Task-ID` trailer, looks up
   `.orchestrator/task-briefs/<id>.md` for a `scope:` block, and aborts
   if any staged file is outside the declared scope. If no manifest
   exists it falls back to a heuristic: > 3 top-level directories
   without a `Cross-Dir: yes` trailer is rejected.

### 5.5 Forbidden

- `--amend` on a commit that has been pushed.
- `--no-verify` / `--no-gpg-sign` (unless explicit chair-review note).
- empty commits — they jam Pantheon's rebase loop.
- staging `ai-activity-log.jsonl`, `dashboard-bundle.json`, or
  `docs-site/*` — `.githooks/pre-commit` rejects them.
- routine `--force` / `--mirror` / `--all` / `--delete` push.

---

## 6. Hotfix Path

```
master      ← PR (auto-merge after CI) ←┐
                                         hotfix/<topic>
dev         ← PR (auto-merge after CI) ←┘
```

```bash
TOPIC=login-redirect
HOTFIX=hotfix/$TOPIC

git fetch origin master
git checkout -B $HOTFIX origin/master
# … fix … commit with `Hotfix: yes` trailer (use worker_commit.py)
git push -u origin $HOTFIX

# Open two PRs — one to master, one to dev
gh pr create --base master --head $HOTFIX --label auto-merge,hotfix \
  --title "hotfix: $TOPIC" --body-file /tmp/hotfix-body.md
gh pr merge $HOTFIX --auto --merge

gh pr create --base dev --head $HOTFIX --label auto-merge,hotfix \
  --title "hotfix: $TOPIC (dev sync)" --body-file /tmp/hotfix-dev-body.md
gh pr merge $HOTFIX --auto --merge
```

Once both PRs merge, the `hotfix/<topic>` branch is auto-deleted. The
next nightly publish cut picks up the dev side; if the hotfix must ship
immediately, manually `./scripts/git/nightly_publish.sh now` after the
master merge.

---

## 7. CI Gates and Branch Protection

### 7.1 Required status checks

Provided by `.github/workflows/branch-ci.yml`:

| Status check name      | Source job in branch-ci.yml | What it does                                              |
|------------------------|------------------------------|-----------------------------------------------------------|
| `Commit trailers`      | trailers                     | Enforce subject prefix + LLM-Agent / Task-ID / Reviewer   |
| `Runtime mirror guard` | generated-files              | Reject `ai-activity-log.jsonl` etc. from the diff         |
| `Smoke acceptance`     | smoke                        | Run `scripts/run-acceptance.sh smoke`                     |

### 7.2 Branch protection on origin

| Branch     | Require PR | Required status checks            | Force push | Delete |
|------------|------------|------------------------------------|------------|--------|
| `master`   | ✅          | 3 (above) + base up-to-date        | blocked    | blocked|
| `dev`      | ✅          | 3 (above) + base up-to-date        | blocked    | blocked|
| `task/*`   | n/a        | runs but not required              | allowed    | allowed (auto-deleted on merge) |
| `publish/*`| n/a        | runs                               | blocked    | blocked|
| `hotfix/*` | n/a        | runs (required by the PR itself)   | allowed    | allowed (auto-deleted on merge) |

Approvals required: **0**. Bots auto-merge after status checks pass.
This is intentional: gating discipline is in CI, not in human review
(which doesn't scale to dozens of AI-generated PRs/day).

---

## 8. Workflow Files

| File                                       | Trigger                                                                 | Purpose                                                  |
|--------------------------------------------|--------------------------------------------------------------------------|----------------------------------------------------------|
| `.github/workflows/branch-ci.yml`          | push/PR on `task/**`, `hotfix/**`, `dev`, `publish/**`, `master`         | Trailer check + mirror guard + smoke acceptance gate     |
| `.github/workflows/nightly-publish-cut.yml`| cron `0 * * * *` + `workflow_dispatch`                                    | Cut an immutable publish snapshot only; never dispatch deployment |
| `.github/workflows/publish-promote.yml`    | cron hourly + `release/v*` push + `workflow_dispatch`                    | Open `promote/<v>` PR after soak; auto-merge             |
| `.github/workflows/master-release.yml`     | push on `master`                                                         | Tag `prod/<v>` on promote merges; tag hotfix merges      |
| `.github/workflows/nonprod-deploy.yml`     | push on `publish/v*`, push on `master`, and `workflow_dispatch`           | Fail-closed nonprod deploy with exact-pair admission before dev switch |
| `.github/workflows/orchestrator-sync.yml`  | push/tag/PR labeled                                                      | POST git event to orchestrator webhook (no-op without SYNC_URL) |

---

## 9. Environment ↔ Ref Bindings

| Environment      | Tracks ref                              | Auto-deploy trigger                          | Operator role |
|------------------|------------------------------------------|----------------------------------------------|---------------|
| **dev**          | exact admitted Pantheon/execute-plans pair | separate governed deploy after pair admission | observe       |
| **staging-live** | `master` HEAD (post-promote)             | push on `master` (every promote / hotfix merge) | smoke / sign-off |
| **production**   | a chosen `prod/v<...>` tag (locked)      | never auto                                    | sign + manual workflow_dispatch |

dev is the **CI-gate environment**, but a nightly snapshot does not by itself
authorize a switch. The deploy lane must first admit the exact backend/frontend
pair; inadmissible snapshots remain promotion inputs without creating a deploy
dispatch. `publish-promote.yml` still opens promote PRs only after its publish
criteria pass. staging-live is the post-promote pre-production rehearsal —
`master` push automatically redeploys both
`pantheon-lupin-staging-{control,exec}` VMs. Production is operator-locked.

---

## 9.1 Shared Deploy Workflow Ownership (Fleet Infrastructure)

`.github/workflows/nonprod-deploy.yml` (pantheon, workflow id `269991390`)
and its execute-plans counterpart (`292028803`) are **fleet
infrastructure**: every task's deploy and proof runs go through them, not
just the task that last touched one. This is a hard rule, not a style
preference — a 2026-07-16 incident (`OPS-DEPLOY-WORKFLOW-GUARD-001`) found
worker-spawned local loops that repeatedly ran `gh workflow disable` on
both workflows and `gh run cancel` / `gh api .../force-cancel` on runs they
did not dispatch, in order to protect one task's own proof run. That froze
the deploy path for the entire fleet — including tasks strictly upstream of
the worker doing the freezing — and outlived the run it was meant to
protect.

**Forbidden, unconditionally:**

- `gh workflow disable` (or the equivalent `gh api --method PUT
  .../actions/workflows/<id>/disable`) against a shared deploy workflow.
  No task ever owns exclusivity over the workflow itself.
- `gh run cancel` / `gh run ... force-cancel` against a run your task did
  not itself dispatch. "Not the run I'm waiting on" is not the same as
  "mine to cancel."
- Any loop that keeps re-applying either of the above on a timer. A guard
  script that outlives its own protected run and re-disables/re-cancels
  every few seconds is exactly the pattern this section exists to stop.

**Sanctioned isolation, in order of preference:**

1. **The workflow's own `concurrency:` group.** `nonprod-deploy.yml` keys
   its group on `inputs.environment` for `workflow_dispatch` and on a
   separate `dev-auto` / `staging-auto` key for push triggers, with
   `cancel-in-progress` true only for push. A manual proof dispatch to
   `dev` therefore already queues behind (never cancels, is never
   cancelled by) any other manual `dev` dispatch, and runs independently
   of automatic `publish/v*` / `master` push redeploys in their own
   group. This is usually all a proof run needs: dispatch with the
   `environment` input that matches what you're proving and let the
   group serialize it.
2. **The dev environment lease** (`scripts/dev_environment_lease.py`,
   compare-and-swap state on the `ajoe734/execute-plans`
   `environment-coordination` branch). `nonprod-deploy.yml`'s `deploy-dev`
   job already acquires this lease, holds it with an identity-bound
   heartbeat, and releases it only after every protected step succeeds —
   a crashed or killed run's lease self-expires via TTL instead of
   blocking the fleet. If a task needs exclusivity stronger than the
   concurrency group (for example, excluding a differently-triggered run
   that lands in a different group), it must go through this lease, not a
   home-rolled disable/cancel loop.

Any helper that still needs to watch its own run must be scoped to runs it
dispatched itself and must exit as soon as that run reaches a terminal
state (`success` / `failure` / `cancelled`) — never a fixed-duration sleep
loop that keeps acting after the protected run is done.

**Detecting a stuck disabled workflow:** `scripts/check_shared_deploy_workflow_disabled.py`
reports (and, with `--enable`, restores) any watched workflow found in
`disabled_manually` state. Wire it into cron the same way as
`scripts/reap_hung_workers.py` (see the `pantheon-hung-worker-reap` cron
line) so a stray disable cannot silently freeze the fleet's deploy path
until a human happens to notice.

---

## 10. Recovery Recipes

### 10.1 Rebase stuck mid-pick

```bash
git tag recovery/$(date +%Y%m%d-%H%M%S)
git rebase --abort
git checkout -B <branch> recovery/<tag>
```

### 10.2 Empty commits jamming rebase

Pre-check:

```bash
git diff --cached --quiet && { echo "empty commit; aborting"; exit 1; }
```

### 10.3 Runtime mirrors stuck `M`

These files are orchestrator-regenerated and never committed:

- `ai-activity-log.jsonl`
- `ai-status.json`
- `dashboard-bundle.json`
- `current-work.md`
- `docs-site/{ai-status.json, current-work.md, dashboard-bundle.json}`

`.githooks/pre-commit` blocks them. To reset, regenerate via
`scripts/ai_status.py sync` or `git checkout -- <file>`.

### 10.4 Cross-branch checkout fights orchestrator

Use an isolated worktree:

```bash
git worktree add ../pantheon-isolated <ref>
cd ../pantheon-isolated
# … perform operation …
cd -
git worktree remove --force ../pantheon-isolated
```

---

## 11. Configuration

All workflow parameters live in `.orchestrator/config.json`:

```json
"branch_workflow": {
  "enabled": true,
  "task_branch_prefix": "task/",
  "publish_branch_prefix": "publish/",
  "release_tag_prefix": "release/",
  "prod_tag_prefix": "prod/",
  "archive_tag_prefix": "archive/",
  "dev_branch": "dev",
  "main_branch": "master",
  "nightly_publish": {
    "enabled": true,
    "cron_utc": "0 3 * * *",
    "version_format": "vYYYY.MM.DD.N",
    "release_state_file": ".orchestrator/release-state.json",
    "skip_if_no_new_commits": true
  },
  "promote": {
    "trigger": "publish_soak",
    "soak_days": 1,
    "regression_label_prefix": "regression/",
    "block_labels": ["hold-promote", "regression"],
    "promote_pr_label": "auto-promote",
    "auto_merge": true
  },
  "task_pr": {
    "auto_merge": true,
    "required_status_checks": [
      "Commit trailers",
      "Runtime mirror guard",
      "Smoke acceptance"
    ],
    "max_open_hours": 24
  },
  "drift_alarms": {
    "task_pr_must_merge_within_hours": 24,
    "publish_must_promote_within_days": 7,
    "dev_must_not_diverge_from_master_more_than_days": 14
  },
  "orchestrator_sync_webhook": {
    "enabled": false,
    "url_env": "PANTHEON_ORCHESTRATOR_SYNC_URL",
    "secret_env": "PANTHEON_ORCHESTRATOR_SYNC_SECRET"
  }
}
```

---

## 12. Migration From the Wave Model (2026-05-17)

The wave-based model (`wave/<id>` + 8 permanent `worker/<name>` branches)
is retired by OPS-GIT-REDESIGN-001:

1. All wave_*, worker/*, and promote/* artifacts on `worker/claude`
   landed in `dev` via the final wave-close on 2026-05-17.
2. `worker/{claude,claude2,codex,codex2,gemini,gemini2,copilot,qwen}`
   on origin are tagged `archive/worker-<name>-2026-05-17` and deleted.
3. `wave_open.sh`, `wave_close.sh`, `wave_merge_worker.sh` removed.
4. `ai_status.py wave` subcommand removed; the `wave_state` field stays
   in `ai-status.json` as a legacy read-only record but is no longer
   written.
5. `.orchestrator/config.json wave_workflow` renamed to
   `branch_workflow` with the new nightly publish + task_pr keys.
6. Branch protection on `dev` upgraded from "block force/delete only"
   to "PR required + 3 required status checks + 0 approvals".
7. `wave-ci.yml` renamed to `branch-ci.yml`; triggers updated from
   `wave/**` / `worker/**` to `task/**` / `hotfix/**`.

---

## 13. References

- `.github/workflows/branch-ci.yml`
- `.github/workflows/nightly-publish-cut.yml`
- `.github/workflows/publish-promote.yml`
- `.github/workflows/master-release.yml`
- `.github/workflows/nonprod-deploy.yml`
- `.github/workflows/orchestrator-sync.yml`
- `.githooks/pre-commit`, `.githooks/commit-msg`
- `scripts/git/task_start.sh`, `scripts/git/task_finalize.sh`
- `scripts/git/nightly_publish.sh`
- `scripts/git/worker_commit.py`
- `scripts/git/check_commit_trailers.py`, `scripts/git/check_commit_scope.py`
- `scripts/git/publish_promote.py`, `scripts/git/notify_orchestrator.py`
- `scripts/dev_environment_lease.py`, `scripts/run_with_dev_environment_lease.sh`
- `scripts/check_shared_deploy_workflow_disabled.py`, `scripts/reap_hung_workers.py`
- `.orchestrator/templates/wakeup.txt`
- `.orchestrator/skills/worker-anchor-commit.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `.orchestrator/skills/chairman-review.md`
- `AI_COLLABORATION_GUIDE.md` § 2 Multi-Branch Integration Policy
