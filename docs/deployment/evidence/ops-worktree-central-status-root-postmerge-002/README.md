# OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002 Evidence

Date: 2026-07-16
Owner: Claude
Reviewer: Codex2
Installed merge: `d4d0f693ec40e2196c47f2601380e1e0fc8b2fb9` (PR #3750,
`OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001`)
Dev runtime root: `/home/lupin/pantheon-ci-deploy/dev-root`
Central status root (`PANTHEON_STATUS_ROOT`): `/home/lupin/code/pantheon`

## Summary

Installed the exact `OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001` merge
into the live Pantheon dev supervisor/runtime via the repo's existing
`scripts/sync-dev-root.sh` deploy procedure (the same idempotent
fetch/fast-forward/graceful-restart script the `pantheon-dev-root-sync` cron
already runs hourly), verified the exact merged SHA is running, and proved
`PANTHEON_STATUS_ROOT` binding/rejection behavior end-to-end from a
disposable isolated worktree with a deliberately conflicting stale local
board.

The full live governed `note`/`handoff` round-trip proof against the central
activity log could not be completed: every `scripts/ai_status.py` invocation
(read-only `show` included) fails inside the pre-existing, unrelated,
fleet-wide activity-log recovery path -- see "Known blocker" below. This is
independent of, and unaffected by, the install performed here.

## 1. Pre-install identity and hashes

```text
dev-root HEAD (pre-install):    135d266b8de855c187d3307d41d86376833d728c
target merge SHA (origin/dev):  d4d0f693ec40e2196c47f2601380e1e0fc8b2fb9
supervisor pid (pre-install):   3565952, started 2026-07-16T15:30:07Z (previous hourly cron sync cycle)
```

```text
sha256  .orchestrator/worker_runner.py  e7b0ebfa5014baf484acd47a7991720fa52322a3cf96c826f1d775587a37f8dd
sha256  .orchestrator/supervisor.py     0476b6e047a0f7aea782ed746c012461d1b17af62fa7c6473df21e7e8d83584e
sha256  .orchestrator/common.py         bee35b85013b9be3b29db20ff66082bee415bc36b90acc20646bfc31021b9d8d
sha256  scripts/ai_status.py            02165280fd037dd5be8bcdd58cd0b3a6ef7e50898387601ce887e7bb904137af
sha256  /home/lupin/code/pantheon/ai-status.json (central)  4d0ed90ddc2203436c47ca07e34b0c436967cee89fe2c42e5f2f747af4240ea8
```

## 2. Install procedure

Ran the repo's normal dev-runtime deployment procedure (the same script the
`pantheon-dev-root-sync` cron job runs every hour at :30, confirmed via
`crontab -l`):

```bash
cd /home/lupin/pantheon-ci-deploy/dev-root
bash scripts/sync-dev-root.sh
```

Output:

```text
[sync-dev-root 2026-07-16T15:46:57Z] dev-root behind origin/dev by 17
[sync-dev-root 2026-07-16T15:46:58Z] stashed dirty tracked changes (recoverable via git stash list)
[sync-dev-root 2026-07-16T15:46:58Z] updated dev-root -> d4d0f693e
OK: no actionable config drift.
[sync-dev-root 2026-07-16T15:46:59Z] restarting supervisor pid=3565952 to load new code (watchdog cron will relaunch)
[sync-dev-root 2026-07-16T15:46:59Z] done (updated=1)
```

The script only sends a graceful `SIGTERM` to the running supervisor; the
`pantheon-supervisor-watchdog` cron (also confirmed via `crontab -l`, runs
every minute with `flock` single-instance protection) relaunches it on the
new code. No task worktree, worker child process, or other repo checkout was
touched.

## 3. Post-install identity and hashes

```text
dev-root HEAD (post-install):   d4d0f693ec40e2196c47f2601380e1e0fc8b2fb9  (exact merge SHA)
supervisor pid (post-install):  3729906, started 2026-07-16T15:47:02Z, ppid 3729689 (watchdog-relaunched)
```

```text
sha256  .orchestrator/worker_runner.py  af3979e085fd9e24c6ce983161f7dea5b0da53317955ed92634a7aca2bc4e2e4  (changed)
sha256  .orchestrator/supervisor.py     c7c549bd778c6f3ea9ba2dc9dfff51f0876044d63288f221bb5314deb81d6889  (changed)
sha256  .orchestrator/common.py         bee35b85013b9be3b29db20ff66082bee415bc36b90acc20646bfc31021b9d8d  (unchanged, not touched by this PR)
sha256  scripts/ai_status.py            d411940420eaf18ea9f3c783e6c5bb65c541ae32befef6c245c14bd4bb748662  (changed)
sha256  /home/lupin/code/pantheon/ai-status.json (central)  4d0ed90ddc2203436c47ca07e34b0c436967cee89fe2c42e5f2f747af4240ea8  (unchanged by the install)
```

`.orchestrator/worker_runner.py` now contains the `PANTHEON_STATUS_ROOT`
validation call chain (`grep -n PANTHEON_STATUS_ROOT` returns the new
`configure_status_root_paths`/`validate_status_root_binding` call sites that
were absent pre-install).

## 4. Isolation proof (disposable worktree, conflicting stale board)

Created a disposable, no-product-change worktree detached at the merge SHA:

```bash
git worktree add --detach /tmp/pantheon-disposable-worktrees/postmerge-002-proof origin/dev
```

Deliberately overwrote its local `ai-status.json` with the content from an
older commit (`135d266b8`, pre-merge) and appended a marker line to
`current-work.md`, so the worktree-local board conflicts with the live
central board. Captured baseline hashes of the worktree-local files.

### 4a. Positive binding path

With `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon` (the real central
root) and auto-worker markers set (`PANTHEON_WORKTREE_ROOT`, `ORCH_RUN_ID`),
`validate_status_root_binding()` passes -- the process proceeds past binding
validation into command dispatch every time (see the traceback in §5, which
fails several frames *after* `validate_status_root_binding()` returns).

### 4b. Negative binding path -- rejects the isolated worktree as root

```bash
AI_NAME=Claude \
PANTHEON_STATUS_ROOT=/tmp/pantheon-disposable-worktrees/postmerge-002-proof \
PANTHEON_WORKTREE_ROOT=/tmp/pantheon-disposable-worktrees/postmerge-002-proof \
ORCH_RUN_ID=proof-test-001 \
python3 scripts/ai_status.py show OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002
```

```text
RuntimeError: PANTHEON_STATUS_ROOT must point at the supervisor coordination root, not the isolated task worktree
```

### 4c. Negative binding path -- rejects a missing root under auto-worker markers

```bash
env -u PANTHEON_STATUS_ROOT AI_NAME=Claude \
PANTHEON_WORKTREE_ROOT=/tmp/pantheon-disposable-worktrees/postmerge-002-proof \
ORCH_RUN_ID=proof-test-002 \
python3 scripts/ai_status.py show OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002
```

```text
RuntimeError: PANTHEON_STATUS_ROOT is required for auto workers running outside the supervisor coordination root
```

### 4d. Worktree-local coordination files remain byte-identical

Hashes of the worktree-local `ai-status.json`, `ai-activity-log.jsonl`, and
`current-work.md` were identical before and after every proof invocation
(4a-4c, all of which either rejected at binding validation or failed later
in the unrelated outbox-recovery path -- neither path performs a write):

```text
sha256  ai-status.json          7bed382e7f2474aab58e7a196e28518c6b112cb6c5f14c0406fbd87301d83906
sha256  ai-activity-log.jsonl   bd242576fb45f1ea3c58f6b8af6f036e4b3fc26626a7167edf2450b3cdca7009
sha256  current-work.md         2cd6532110146cabf83d2cfeefcecc91c2728ff5f85abac4867648a0852be461
```

The central `ai-status.json` hash was also unaffected by these proof
invocations specifically (no write path was reached by any of them).

### 4e. Normal read-only git command runs inside the worktree, not the central checkout

```bash
git rev-parse --show-toplevel
# /tmp/pantheon-disposable-worktrees/postmerge-002-proof
git -C /tmp/pantheon-disposable-worktrees/postmerge-002-proof log -1 --format='%H %s'
# d4d0f693ec40e2196c47f2601380e1e0fc8b2fb9 Merge pull request #3750 from ajoe734/task/OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001
git -C /home/lupin/code/pantheon rev-parse HEAD
# f7507e4fd5dba28b8d6eb3c9d99c97f542d40b54  (different -- proves the worktree operated independently of the central checkout)
```

### Cleanup

```bash
git worktree remove --force /tmp/pantheon-disposable-worktrees/postmerge-002-proof
```

Confirmed removed from `git worktree list`.

## 5. Known blocker -- full show/note/handoff round-trip not provable right now

Every `scripts/ai_status.py` invocation, including read-only `show`, fails
*after* `validate_status_root_binding()` succeeds, inside the unrelated
activity-log recovery path:

```text
File "scripts/ai_status.py", line 4885, in main
    recover_status_activity_outbox(recovery_state)
File "scripts/ai_status.py", line 1568, in recover_status_activity_outbox
    existing = _activity_event_index_unlocked()
File "scripts/ai_status.py", line 1415, in _activity_event_index_unlocked
    raise RuntimeError(f"activity event_id {detail}: {event_id}")
RuntimeError: activity event_id duplicate across sources: worker-commit-deb673789747a71068bff9f2578ad9f41d7b8253
```

**Corrected characterization (per Codex2/Planner review of the first
version of this evidence):** this is not an arbitrary duplicate-event bug,
and the offending `worker-commit-deb673...` event ID is present across two
*rotated* archives (`2026-07-16T0358Z.gz` -> `2026-07-16T1130Z.gz`), not
across the live log and one archive as originally stated here. The
`OPS-ACTIVITY-AUDIT-RECOVERY-PLAN-001` planning task ran a complete
read-only incident inventory: 411 source files, 1,157,457 total rows, 537
rows carrying an event ID, 437 unique event IDs, 100 duplicate IDs, zero
payload mismatches, and zero within-source duplicates. The 100 duplicates
resolve to exactly four adjacent legacy timestamp-rotation suffix/prefix
pairs, each overlapping by exactly 1,000 byte-identical lines:

- `0358Z.gz` -> `1130Z.gz`
- `1301Z.gz` -> `1404Z.gz`
- `1404Z.gz` -> `1450Z.gz`
- `1450Z.gz` -> active `ai-activity-log.jsonl`

This is the legacy timestamp-named rotation's `keep_lines=1000` tail-retention
contract working as designed; the newer reader treats every rotation source
as disjoint and incorrectly flags the legal overlap as corruption. It affects
every agent and every task on the fleet identically and is unrelated to, and
unaffected by, the `PANTHEON_STATUS_ROOT` binding fix installed here
(`.orchestrator/common.py`, which owns the duplicate-event index, was not
touched by the `CORRECTIVE-001` PR -- its hash is identical before and after
this install, §1 vs §3).

Because of this outage, the task brief's requirement to "run governed `show`,
`note` and owner-to-reviewer `handoff`" and "prove the central board and
central activity log receive the events exactly once" cannot be completed
right now for *any* task, by *any* agent. §4 substitutes the strongest
available proof: the new binding validation demonstrably runs and both
accepts the correct central root and rejects the isolated-worktree/missing-root
cases, with the crash trace showing it always gets past validation before
hitting the unrelated outage.

**Dedicated recovery task:** `OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001`
(owner `Antigravity`, reviewer `Claude`, auto-merge disabled) carries the
actual fix -- a shared streaming logical activity reader that folds verified
legacy adjacent-source overlaps into a single logical view without touching
raw archive bytes, then re-validates `recover_status_activity_outbox()`
idempotency against that view. Per Planner/Codex2 review of this PR, this
task (`POSTMERGE-002`) stays open and unmerged until that recovery task
lands, at which point the full governed `show`/`note`/`handoff` isolation
proof (exactly-once central mutation, unchanged worktree sentinel bytes)
must be rerun here and resubmitted for Codex2 exact-head review. Until then
this task cannot reach a full `done` closeout against its original
acceptance criteria.
