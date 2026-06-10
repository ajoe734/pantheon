# Supervisor archive split-brain → single-worker dispatch stall (2026-06-09)

## Symptom
The autoworker fleet collapsed to a single running worker (only `Claude2` on
`OPS-RTEL-004`) even though `Claude` and `Codex` were `ready` with unblocked
tasks assigned and quota headroom (claude=3, codex1=4 concurrent allowed).

## Root cause: status-root split-brain
The live supervisor runs from the **dev-root** checkout
(`/home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/supervisor.py`) under a
config whose `paths.*` deliberately point at the **canonical working repo**
(`/home/lupin/code/pantheon/...`). State files (`ai-status.json`, etc.) are
therefore read from the canonical repo — but the task **archive** is not.

`.orchestrator/task_archive.py` computes its archive root from
`PANTHEON_STATUS_ROOT`, falling back to its own module location when the env var
is unset:

```python
def status_root() -> Path:
    raw = os.environ.get("PANTHEON_STATUS_ROOT")
    return Path(os.path.expanduser(raw)).resolve() if raw else ROOT  # ROOT = parents[1]
STATUS_ROOT = status_root()
ARCHIVE_DIR = STATUS_ROOT / "ai-task-archive"
```

`PANTHEON_STATUS_ROOT` was **not** set in the supervisor's environment, so
`ARCHIVE_DIR` resolved to `dev-root/ai-task-archive` while workers archive
completed tasks into `code/pantheon/ai-task-archive` (the configured status
root). Split-brain.

### Why that stalls dispatch
`ready_dispatcher` gates a `todo` task on `dependencies_satisfied()`, which calls
`TaskResolver.dependency_status(dep)`. When a dependency has been archived (moved
out of the live `ai-status.json` task list), the resolver looks it up in the
archive dir. Reading the **dev-root** archive, the just-completed blockers
(`MPOS-P0-VAL-001`, `DATASTRAT-CONTRACT-001`, `ASST-SKILL-004`, …) were
**missing** → treated as not-`done` → every downstream task stayed "blocked" and
was never dispatched.

`OPS-RTEL-004` survived only because it was already `in_progress` with a clean
worker **self-claim chain** (`owned_in_progress_dispatch`), which doesn't
re-evaluate the archived-dependency gate. The other workers had been
SIGTERM-**superseded** during a dedup wave (so they never ran self-claim), and the
supervisor's ready-dispatch couldn't re-launch them because of the stale archive.

### Amplifier: sync lag
The dev-root archive is only refreshed indirectly:
`auto_commit_archive.py` (≥5 files **or** ≥4h old) opens a PR → merge to `dev` →
`sync-dev-root.sh` (hourly at `:30`) fast-forwards dev-root. So a burst of task
completions can stall the entire next wave for up to ~1h+ until the archive
metadata propagates into dev-root.

## Fix (applied 2026-06-09)
**A — point the supervisor's archive at the canonical status root.** Added a
crontab environment line so cron → watchdog → supervisor (Popen inherits env,
no `env=` override) all see it:

```cron
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon
```

This removes the autosync lag from the dispatch critical path entirely: the
supervisor reads/writes the same archive that workers and `ai-status.json` use.
Verified: post-restart the first tick went from **1 → 5 active workers**, and the
previously-stuck `ASST-SKILL-006` / `MPOS-P0-E2E-001` / `MPOS-P1-MEM-001`
dispatched immediately. The crontab env persists across watchdog restarts,
`sync-dev-root` code-change restarts, and reboots.

**B — drain the stranded archive debt.** Merged the in-flight
`OPS-ARCHIVE-AUTO-COMMIT` PR (was `BEHIND`, auto-merge stuck) and let
`auto_commit_archive.py` commit the remaining untracked
`ai-task-archive/tasks/*.json` + `.orchestrator/task-briefs/*.md`.

## Follow-up (defense in depth)
The crontab env is deployment-specific. To survive a crontab rebuild, the
watchdog should spawn the supervisor with `PANTHEON_STATUS_ROOT` derived from
`config.paths.status_file`'s parent when the env var is unset
(`start_supervisor` in `.orchestrator/supervisor_watchdog.py`, pass `env=`).
Tracked as the code-level hardening for this incident.

## Tripwire
If the fleet collapses to one worker while agents are `ready` with assigned
tasks, check the running supervisor's env:
`tr '\0' '\n' < /proc/<pid>/environ | grep PANTHEON_STATUS_ROOT`
and compare `dependency_status()` of a just-completed blocker from dev-root vs
the canonical repo.
