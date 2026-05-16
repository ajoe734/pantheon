# Pantheon Git Workflow

Status: canonical · Owner: chair-review · Last reviewed: 2026-05-16

This document is the **operational** source of truth for branching, waves,
publish versioning, promotion to master, and CI gates. It replaces ad-hoc
guidance previously scattered across `AI_COLLABORATION_GUIDE.md` and the
2026-05-16 backlog post-mortem.

If something here conflicts with `AI_COLLABORATION_GUIDE.md`, this file wins
and the guide must be updated.

---

## 1. Branch Topology

```
master      ──A─────────────────────M─────────────H────  prod canonical
                  \                /  \
dev         ───────B──W20──W21──W22────H'──────────────  integration line
                       \    \    \
wave/W22                \    \    └── current wave (short-lived)
                         \    └─ wave/W21 (archive tag + delete)
                          \
worker/claude    ──c─c─c   ← rebased onto wave/<id> at every wave open
worker/codex     ──c─c
worker/gemini    ──c
worker/copilot   ──c─c
worker/qwen      ──c
(... one branch per autoworker)

publish/v2026.20.0   ← snapshot cut from dev at wave-close
hotfix/<topic>       ← cut from master, merged back to master + dev
```

### 1.1 Branch types

| Type           | Naming                                | Lifetime    | Writer                                   |
|----------------|---------------------------------------|-------------|------------------------------------------|
| canonical      | `master`                              | permanent   | merge-only (release / hotfix)            |
| integration    | `dev`                                 | permanent   | merge-only (wave-close / hotfix)         |
| wave           | `wave/<YYYY>-W<NN>`                   | 5–7 days    | chair-review (wave-merge worker PRs)     |
| worker         | `worker/<name>`                       | permanent   | one per autoworker (see §1.2)            |
| publish        | `publish/v<YYYY>.<WW>.<P>`            | permanent   | cut from dev at wave-close, not written  |
| hotfix         | `hotfix/<YYYY>-W<NN>-<topic>`         | < 24h       | one engineer; merged to master + dev     |
| archive tag    | `archive/<branch>-<YYYY-MM-DD>`       | permanent   | retirement marker                        |
| recovery tag   | `recovery/<timestamp>`                | permanent   | rebase backup tag (see §6.1)             |

### 1.2 Worker branches (one per autoworker)

Long-lived; reset to the current wave at every wave open.

```
worker/claude       worker/claude2
worker/codex        worker/codex2
worker/gemini       worker/gemini2
worker/copilot
worker/qwen
```

The orchestrator config keys (`autoworkers.<name>.git_branch`) must match
exactly. Adding a new worker means: add the branch, add the config entry, push
the empty branch from `dev`, archive on retirement.

---

## 2. Wave Lifecycle (5 working days)

Default cadence: **Mon 09:00 open · Fri 12:00 freeze · Fri 17:00 close**, ISO
week-aligned. Wave id is `<YYYY>-W<NN>` (e.g. `2026-W20`).

### 2.1 T0 — Wave Open (chair-review)

```bash
WAVE=2026-W20
git fetch origin
git checkout -B wave/$WAVE origin/dev
git push -u origin wave/$WAVE

./scripts/ai-status.sh wave open $WAVE
```

Each worker resyncs:

```bash
git fetch origin
git checkout worker/claude
git reset --hard origin/wave/$WAVE
git push --force-with-lease origin worker/claude
```

> `--force-with-lease` is only permitted on `worker/*` branches at wave-open.
> Anywhere else it requires explicit chair-review authorisation.

### 2.2 T1–T3 — Execution (per task closeout)

Worker commit:

```
EP5-FOO-001: <imperative subject>

LLM-Agent: Claude
Task-ID: EP5-FOO-001
Reviewer: Codex
Wave: 2026-W20
Verified: <one-line summary of tests/checks>
```

Push to its own branch only:

```bash
git push origin worker/claude
```

Chair-review merges into the wave (auto-driven by the orchestrator wave-merge
loop, or manually):

```bash
git fetch origin
git checkout wave/$WAVE
git merge --no-ff origin/worker/claude \
  -m "wave-merge: claude EP5-FOO-001"
git push origin wave/$WAVE
```

If the merge conflicts, chair-review resolves; the worker does **not** touch
the wave branch. Before the worker starts the next task it rebases its branch
onto the wave so subsequent commits stay current:

```bash
git pull --rebase origin wave/$WAVE
```

### 2.3 T4 — Freeze (Friday 12:00)

```bash
./scripts/run-acceptance.sh wave/$WAVE
./scripts/ai-status.sh wave freeze $WAVE
```

After freeze the wave accepts bugfix commits only; feature work moves to the
next wave.

### 2.4 T5 — Wave Close (Friday 17:00)

```bash
git checkout dev
git pull
git merge --no-ff origin/wave/$WAVE -m "wave-close: $WAVE"
git push origin dev

DATE=$(date +%F)
git tag archive/wave-$WAVE-$DATE
git push origin archive/wave-$WAVE-$DATE
git push origin --delete wave/$WAVE
```

Then immediately cut the publish snapshot (§3).

---

## 3. Publish Versioning

Cut a permanent snapshot from `dev` at every wave-close.

### 3.1 Version format `vYYYY.WW.P`

| Segment | Meaning                                | Example |
|---------|----------------------------------------|---------|
| `YYYY`  | calendar year                          | `2026`  |
| `WW`    | ISO week (= wave number)               | `20`    |
| `P`     | patch counter (0 = wave-close release) | `0`     |

Hotfixes within the same week bump `P` (`v2026.20.0` → `v2026.20.1`).

### 3.2 Cut command

```bash
VER=v2026.20.0
git checkout -B publish/$VER origin/dev
git push -u origin publish/$VER
git tag release/$VER -m "dev publish: wave 2026-W20"
git push origin release/$VER
```

Publish branches are immutable snapshots. **Never** push commits onto them
after the initial cut. Deploy automation reads `publish/<VER>` by tag or
branch ref and pushes to staging.

---

## 4. Promotion to Master

Default trigger: **publish promote after 3 days clean in staging.** The
GitHub Action `publish-promote.yml` opens a PR `publish/<VER> → master`
when:

1. `release/<VER>` tag age ≥ 3 days
2. No `regression/<VER>` label exists on any open issue
3. The CI gate is green on `publish/<VER>`

Manual promote (when automation is unavailable):

```bash
VER=v2026.20.0
git checkout -B promote/$VER origin/master
git merge --no-ff origin/publish/$VER -m "promote: $VER"
git push origin promote/$VER
gh pr create --base master --head promote/$VER \
  --title "Promote $VER to master" \
  --body "Promotion of publish snapshot $VER after staging soak."
```

On merge, the same workflow tags `prod/<VER>` on master.

### 4.1 No more Codex direct-to-master

Codex / Codex2 integration contracts now flow through `worker/codex(2)` like
every other lane. The only path that bypasses dev is **hotfix** (§5).

---

## 5. Hotfix Path

```bash
TOPIC=login-redirect
HOTFIX=hotfix/2026-W20-$TOPIC

git checkout -B $HOTFIX origin/master
# fix → commit with `Hotfix: yes` trailer + Task-ID
git push -u origin $HOTFIX

# Dual merge
git checkout master && git merge --no-ff $HOTFIX && git push origin master
git checkout dev    && git merge --no-ff $HOTFIX && git push origin dev

# Patch publish
VER=v2026.20.1
git checkout -B publish/$VER origin/master
git push -u origin publish/$VER
git tag release/$VER && git tag prod/$VER
git push origin release/$VER prod/$VER

git tag archive/$HOTFIX-$(date +%F)
git push origin --delete $HOTFIX
```

Hotfixes always land on both master **and** dev so the next wave-close merge
sees them already integrated.

---

## 6. Recovery Recipes

### 6.1 Rebase stuck mid-pick

```bash
git tag recovery/$(date +%Y%m%d-%H%M%S)
git rebase --abort
git checkout -B <branch> recovery/<tag>
# re-apply commits one-by-one or via cherry-pick
```

### 6.2 Empty commit jamming rebase

Empty commits jam the rebase loop because workers don't pass `--allow-empty`
or `--skip`. Pre-check with:

```bash
git diff --cached --quiet && { echo "empty commit; aborting"; exit 1; }
```

If already jammed, see §6.1.

### 6.3 Runtime mirrors stuck `M`

The following files are orchestrator-regenerated and are blocked from commits
by `.githooks/pre-commit`:

- `ai-activity-log.jsonl`
- `dashboard-bundle.json`
- `docs-site/{ai-status.json, current-work.md, dashboard-bundle.json}`

They live permanently in `git status` as modified. Never stage them. To reset
the working tree, run the orchestrator regenerate (`scripts/ai_status.py
sync`) or `git checkout -- <file>`.

### 6.4 Cross-branch checkout fights orchestrator

Use an isolated worktree:

```bash
git worktree add ../pantheon-integration <ref>
cd ../pantheon-integration
# … perform merge …
cd -
git worktree remove ../pantheon-integration
```

---

## 7. Commit Conventions

Subject line ≤ 70 chars, format:

```
<TASK-ID>: <imperative summary>
```

Required body trailers (enforced by `.githooks/commit-msg`):

```
LLM-Agent: <Owner>
Task-ID: <task-id>
Reviewer: <reviewer>
Wave: <YYYY>-W<NN>
```

Optional trailers:

- `Verified: <command-or-summary>` — required when tests/checks were run
- `Hotfix: yes` — required on hotfix-path commits

Forbidden:

- `--amend` on already-pushed commits
- `--no-verify`, `--no-gpg-sign`
- empty commits (see §6.2)
- staging runtime mirrors (see §6.3)
- `--force` / `--mirror` / `--all` / `--delete` pushes outside the workflows
  declared in §1 (wave-open `worker/*` reset is the only routine exception)

---

## 8. CI Gates

| Workflow file                          | Trigger                                             | Purpose                                          |
|----------------------------------------|-----------------------------------------------------|--------------------------------------------------|
| `.github/workflows/wave-ci.yml`        | push / PR on `worker/*`, `wave/*`, `dev`            | Lint, type, test, acceptance subset              |
| `.github/workflows/publish-promote.yml`| schedule (hourly) + `release/v*` tag push           | Open promote PR after staging soak               |
| `.github/workflows/master-release.yml` | merge of promote PR into `master`                   | Tag `prod/<VER>`, archive publish branch         |
| `.github/workflows/orchestrator-sync.yml` | push on `dev`, `wave/*`, tags `release/*`, `prod/*` | POST events to orchestrator status webhook       |

All four gate on the commit-message trailer check (`scripts/git/check_commit_trailers.py`).

---

## 9. Migration from current state (2026-05-16 cutover)

| # | Action |
|---|--------|
| 1 | `git checkout -B dev origin/merge/backend-dev-into-master && git push -u origin dev` |
| 2 | Tag `archive/merge-backend-dev-into-master-2026-05-16` then `git push origin --delete merge/backend-dev-into-master` |
| 3 | For each autoworker, `git push origin dev:refs/heads/worker/<name>` to create the long-lived branch |
| 4 | Merge `bff-luv-fe-006-dev-deploy` into `dev` (via integration worktree), then archive and delete |
| 5 | Triage `codex/*` branches: merge into dev or `archive/`-tag and delete |
| 6 | Open the first wave: `./scripts/ai-status.sh wave open 2026-W21` |
| 7 | Enable hooks repo-wide: `git config core.hooksPath .githooks` |
| 8 | Update orchestrator config (see `.orchestrator/config.example.json` `wave_workflow` block) |

---

## 10. Open settings (live in `.orchestrator/config.json`)

```json
"wave_workflow": {
  "enabled": true,
  "wave_length_days": 5,
  "wave_open_dow": "monday",
  "wave_close_dow": "friday",
  "freeze_hour_local": 12,
  "close_hour_local": 17,
  "current_wave_id": null,
  "wave_branch_prefix": "wave/",
  "worker_branch_prefix": "worker/",
  "publish_branch_prefix": "publish/",
  "release_tag_prefix": "release/",
  "prod_tag_prefix": "prod/",
  "dev_branch": "dev",
  "main_branch": "master",
  "promote": {
    "trigger": "publish_soak",
    "soak_days": 3,
    "regression_label_prefix": "regression/"
  },
  "worker_branches": {
    "Claude":   "worker/claude",
    "Claude2":  "worker/claude2",
    "Codex":    "worker/codex",
    "Codex2":   "worker/codex2",
    "Gemini":   "worker/gemini",
    "Gemini2":  "worker/gemini2",
    "Copilot":  "worker/copilot",
    "Qwen":     "worker/qwen"
  }
}
```

These keys are the contract between scripts, CI workflows, and the orchestrator.

---

## 11. References

- `.github/workflows/wave-ci.yml`
- `.github/workflows/publish-promote.yml`
- `.github/workflows/master-release.yml`
- `.github/workflows/orchestrator-sync.yml`
- `.githooks/pre-commit`, `.githooks/commit-msg`
- `scripts/ai-status.sh wave <subcommand>`
- `scripts/git/check_commit_trailers.py`
- `AI_COLLABORATION_GUIDE.md` § 2 Multi-Branch Integration Policy
