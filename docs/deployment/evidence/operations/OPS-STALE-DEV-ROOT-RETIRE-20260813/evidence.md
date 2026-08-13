# Evidence: OPS-STALE-DEV-ROOT-RETIRE-20260813

**Task Title:** Retire the stale mutable dev-root checkout and remove obsolete files  
**Owner:** Antigravity2  
**Reviewer:** Codex  
**Date:** 2026-08-13  
**Status:** review  

---

## 1. Summary

This task successfully retired the stale mutable `dev-root` checkout (`/home/lupin/pantheon-ci-deploy/dev-root`), removed obsolete untracked files and residue, and refreshed `dev-root` to exact tip of `origin/dev` (`12a8dd18a78ec7bf1716b4b80226152ad3ffd533`).

---

## 2. Pre-flight Consumer Audit

Before performing any mutation on `/home/lupin/pantheon-ci-deploy/dev-root`, a full read-only audit was conducted:

1. **Process & CWD Audit:**
   - Command `ps aux | grep -i dev-root` returned 0 active worker or supervisor processes running from `dev-root`.
   - Command `lsof +D /home/lupin/pantheon-ci-deploy/dev-root` returned 0 open file handles or working directory references.
2. **Worktree Registration Audit:**
   - `git worktree list` showed `dev-root` is not registered as a worktree for `pantheon` or any other repository.
3. **Live Supervisor Audit:**
   - Supervisor PID `3915223` cwd: `/home/lupin/pantheon-ci-deploy/command-runtimes/16e39431ce8ccf8c76c08ec1f6bccd13bb3ce2bf`
   - Active Command Runtime SHA: `16e39431ce8ccf8c76c08ec1f6bccd13bb3ce2bf`
4. **System & Runtime Config Audit:**
   - `live-supervisor-mainroot-config.json` points directly to the immutable command runtime.
   - 0 systemd, nginx, or supervisor units reference `dev-root`.

**Audit Outcome:** `PASS` (0 active consumers found; safe to mutate and rebuild `dev-root`).

---

## 3. Before Inventory & Removal Manifest

### Before Inventory
- **HEAD SHA:** `5d5aa72a229e0393070106a0021bf154ef40249f` (detached at `release/v2026.08.12.4`)
- **Distance:** 51 commits behind `origin/dev` (`12a8dd18a78ec7bf1716b4b80226152ad3ffd533`)
- **Untracked / Ignored Residue Identified:**
  - Queue & supervisor locks (`.orchestrator/supervisor.lock`, `.orchestrator/status-derived-views.lock`)
  - 25 stale task briefs (`.orchestrator/task-briefs/*.md`)
  - 681 historical worker evidence receipts (`.orchestrator/evidence/*.json` from July 28 - Aug 10)
  - Bridge inbox residue (`.orchestrator/assistant-dev-packets/`)

### Retention & Removal Strategy
- **Governed Sync Fix:** Enhanced `scripts/sync-dev-root.sh` to execute `git clean -fdx` following `git reset --hard "$REF"`, ensuring all untracked and ignored residue is purged when `dev-root` is refreshed.
- **Retention Decision:** Authoritative task history is preserved in central TaskStore (`/home/lupin/pantheon` & `task-state-events-v2.jsonl`). Temporary worker locks, stale briefs, and local caches in `dev-root` require no archiving.

---

## 4. Execution & Source Changes

1. **Source Fix (`scripts/sync-dev-root.sh`):**
   - Added `git -C "$root" clean -fdx >/dev/null 2>&1 || true` to `sync_root()`.
2. **Unit Test Added (`scripts/test_sync_dev_root.py`):**
   - Added `test_sync_cleans_untracked_and_ignored_residue_in_dev_root`.
   - Test suite verification: `8 passed in 6.92s`.
3. **Execution Run:**
   - Command: `bash scripts/sync-dev-root.sh /home/lupin/pantheon-ci-deploy/dev-root`
   - Log output:
     ```text
     [sync-dev-root 2026-08-13T13:21:24Z] ACTIVE_ROOT_SPLIT_PROTECTED: live supervisor pid=3915223 runs from /home/lupin/pantheon-ci-deploy/command-runtimes/16e39431ce8ccf8c76c08ec1f6bccd13bb3ce2bf, not /home/lupin/pantheon-ci-deploy/dev-root
     [sync-dev-root 2026-08-13T13:21:25Z] dev-root (/home/lupin/pantheon-ci-deploy/dev-root) at 5d5aa72a2, behind origin/dev by 51
     [sync-dev-root 2026-08-13T13:21:27Z] updated dev-root -> 12a8dd18a
     [sync-dev-root 2026-08-13T13:21:27Z] leaving active immutable supervisor root untouched: /home/lupin/pantheon-ci-deploy/command-runtimes/16e39431ce8ccf8c76c08ec1f6bccd13bb3ce2bf
     [sync-dev-root 2026-08-13T13:21:27Z] evidence: dev-root HEAD=12a8dd18a78ec7bf1716b4b80226152ad3ffd533 active HEAD=16e39431ce8ccf8c76c08ec1f6bccd13bb3ce2bf target=12a8dd18a78ec7bf1716b4b80226152ad3ffd533
     ```

---

## 5. After Inventory & Runtime Invariance

### After Inventory
- **HEAD SHA:** `12a8dd18a78ec7bf1716b4b80226152ad3ffd533` (exact tip of `origin/dev`)
- **Git Status:** Clean (`git status --short --ignored` returned empty output)
- **Commits Behind `origin/dev`:** 0

### Runtime Invariance Verification
- **Supervisor PID:** `3915223` (unchanged throughout operation)
- **Supervisor CWD:** `/home/lupin/pantheon-ci-deploy/command-runtimes/16e39431ce8ccf8c76c08ec1f6bccd13bb3ce2bf` (unchanged)
- **Command Runtime SHA:** `16e39431ce8ccf8c76c08ec1f6bccd13bb3ce2bf` (unchanged)
- **Canonical TaskStore Status:** Fully operational and readable
- **Product Services Redeployed:** `false` (0 services restarted or redeployed)

---

## 6. Rollback Procedure

If any issue arises:
1. `git -C /home/lupin/pantheon-ci-deploy/dev-root checkout 5d5aa72a229e0393070106a0021bf154ef40249f`
2. The live supervisor runtime remains immutable and running under `command-runtimes/16e39431ce8ccf8c76c08ec1f6bccd13bb3ce2bf`, so live system operations are completely unaffected.
