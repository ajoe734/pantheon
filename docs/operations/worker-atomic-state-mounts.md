# Worker Sandbox Atomic State and Task-Store Mounts Architecture

## Overview

This document describes the worker sandbox filesystem mount architecture implemented in `OPS-WORKER-MOUNTS-001`. It resolves container isolation and filesystem concurrency issues arising from individual inode bind mounts, transient file publication races, and stale journal recreation.

## Problem Statement

### 1. Inode Bind Mounts and Atomic File Replacement (`EROFS`)
Workers use atomic file replacement (`os.replace` / `atomic_write`) to write state files such as `state.json` and `approval-queue.json`. Atomic replacement writes to a temporary file (`<filename>.tmp.<pid>.<thread>`) in the target directory and executes an atomic rename over the destination.

Previously, individual file paths were passed to Bubblewrap (`bwrap`) using `--bind <host_file> <dest_file>`, while the parent directory (`.orchestrator/`) was mounted read-only using `--ro-bind`. This caused:
- **`EROFS` on temp file creation**: Creating the temporary file in the read-only `.orchestrator/` directory failed with `EROFS` (Read-only file system).
- **Stale inode references**: When the supervisor or host atomically replaced a state file, the worker's inode bind mount continued pointing to the old unlinked inode.
- **Race conditions on startup**: Ephemeral file existence checks and mount table lookups failed if a file was in the middle of being replaced.

### 2. Transient File Publication Races in Task-Store Sibling Mounts
The V2 TaskState store writes commit events and head snapshots atomically, creating temporary files (e.g., `<event_id>.head.json.<pid>.<thread>.tmp`). Previously, `_append_task_store_mounts` scanned the parent directory and attempted to bind sibling files. If a publication temp file vanished between directory listing and `bwrap` process execution, worker sandbox spawn failed with `ENOENT`.

### 3. Stale Journal Recreation
If `common.write_status` executed without verifying an existing initialized task authority, a host writer could inadvertently recreate an empty or retired journal if the event log was cleared or migrated, conflicting with canonical V2 TaskStore authority.

---

## Architecture Design

### 1. Dedicated Mutable Worker Runtime Directory (`.orchestrator/worker-runtime/`)
Mutable runtime state files are grouped into a dedicated directory:
- Path: `<status_root>/.orchestrator/worker-runtime/`
- Contents:
  - `state.json`: Cached worker runtime state.
  - `approval-queue.json`: Worker approval queue marker and requests.

#### Bubblewrap Mount Strategy:
1. `.orchestrator/` is mounted as `--ro-bind` (read-only), ensuring worker code, supervisor scripts, and configuration files cannot be modified by worker sandboxes.
2. `.orchestrator/worker-runtime/` is mounted as `--bind` (writable directory mount).
3. Workers create temporary files and atomically replace `state.json` and `approval-queue.json` directly within `worker-runtime/` without `EROFS`.

### 2. Dedicated Task State Directory (`task-state/`)
External V2 TaskStore files are placed in a dedicated directory:
- Path: `<runtime_parent>/task-state/`
- Contents:
  - `task-state-events-v2.jsonl`: Append-only event log.
  - `head.json`: Snapshot head state.
  - `lock`: Inter-process transaction lock.
  - `legacy-anchor.json`: Migration anchor metadata.

#### Bubblewrap Mount Strategy:
1. `task-state/` is mounted as a single writable directory (`--bind`).
2. Layout qualification check: The event log must reside in a dedicated directory named `task-state`. Legacy or mixed parent layouts are rejected fail-closed.
3. Strict containment: The `task-state/` directory must contain only allowed TaskStore files (`events.jsonl`, `.head.json`, `.lock`, `.legacy-anchor.json`) and transient publication temporary files (`*.tmp`). Sibling files such as supervisor configuration or certificates are forbidden and trigger immediate rejection.
4. Outer runtime sibling protection: The outer runtime directory (`runtime_parent`) is mounted `--ro-bind-try` (read-only) at the directory level, preventing atomic file replacement, linking, unlinking, or modification of siblings (e.g. `live-supervisor.json`).
5. Overlapping workspace mount policy propagation: If the leased worktree (`workspace_path`) is nested under `runtime_parent`, the workspace mount is re-asserted after the read-only outer runtime mount respecting the caller's `read_only_worktree` flag (`--ro-bind` when `read_only_worktree=True` such as for reviewer or finalize steps, `--bind` when writable), ensuring reviewer/finalize sandboxes cannot modify the worktree.
6. Transient file resilience: Mounting the dedicated directory eliminates `ENOENT` races from ephemeral temp files that may disappear during sandbox launch. In addition, directory qualification handles transient legitimate publication temporary files (`*.tmp`) that vanish mid-scan due to concurrent atomic replacement, while strictly failing closed on symlinks and unrecognized sibling entries.

### 3. Strict Authority Precondition in `common.write_status`
To prevent retired or uninitialized state journals from being recreated:
- `common.write_status` executes inside `task_state_store.snapshot_transaction` under an exclusive lock.
- It validates that `store.event_count > 0`. If `event_count == 0` or the journal does not exist, it raises a `RuntimeError`.
- Genesis and initialization of the store must be performed explicitly via `task_state_store.append_state_commit` or bootstrap scripts (`seed_task_state_v2.py`).

### 4. Supervisor Control Files Anchored to Canonical Coordination Root
Supervisor process and lifecycle management files must remain strictly outside worker-writable directories:
- `supervisor.pid`: Located at `<status_root>/.orchestrator/supervisor.pid`.
- `supervisor.lock`: Located at `<status_root>/.orchestrator/supervisor.lock`.
- Watchdog logs and state: Located at `<status_root>/.orchestrator/`.
- Model rotation cooldowns: Located at `<status_root>/.orchestrator/rotation-cooldowns.json`.

These paths are derived directly from the coordination root (`status_root / .orchestrator`), preventing accidental drift into `worker-runtime/`.

---

## Promotion & Migration Runbook

### Automatic Storage Migration (`promote_supervisor_runtime.py`)
When promoting a new supervisor runtime version:
1. **Watchdog Fencing**: `_replace_supervisor_locked` acquires an exclusive `fcntl.flock` on `.orchestrator/runtime-admission.lock` covering supervisor shutdown, storage migration, approval queue marker verification, configuration installation, and supervisor launch.
2. **Preflight Validation (`_preflight_storage_migration`)**:
   - Ensures all source and destination paths are absolute.
   - Rejects any symlinks across the entire path hierarchy for incumbent and rendered files.
   - Verifies target destination files and all related sidecars (`.head.json`, `.lock`, `.legacy-anchor.json`) do not already exist, rejecting collisions before any moves occur.
   - Verifies source and destination reside on the same filesystem (`st_dev` check) to guarantee atomic renames.
3. **Writer Drain & Lock Acquisition (`_migrate_storage_paths`)**:
   - Acquires `events.jsonl.lock` nonblocking via `fcntl.flock(LOCK_EX | LOCK_NB)` to ensure no active writers or legacy processes are mutating state during cutover. Fails closed if the lock is held.
   - Creates destination parent directories with strict `0o700` permissions.
   - Atomically relocates store files (`events.jsonl`, `.head.json`, `.lock`, `.legacy-anchor.json`, and worker runtime paths) using `os.replace`.
   - Flushes directory metadata changes durably using `_fsync_dir` on source and target directory hierarchies (including enclosing parents). `_fsync_dir` propagates all durability errors directly, and all fsynced directories are published in the migration record.
4. **Durable Rollback & Lock Retention**:
   - If an unexpected error occurs during migration (e.g. partial rename failure), all moved files are rolled back in reverse order, directory changes are fsynced via `_fsync_dir`, and single recoverable authority is restored at the incumbent path.
   - `_migrate_storage_paths` raises `StorageMigrationError` carrying forward and rollback diagnostics, `restoration_verified`, and `lock_fd`.
   - If rollback cannot be verified or leaves split storage, `_replace_supervisor_locked` retains the single-writer lock exclusion and refuses incumbent restart against unverified restoration.
   - If candidate supervisor launch fails after verified migration, `_replace_supervisor_locked` restores migrated files, writes back incumbent configuration, fsyncs rollback directories, and restarts the qualified incumbent supervisor. Any rollback or restart errors are accumulated and raised rather than silently swallowed.
5. **Strict Incumbent Source Qualification**:
   - `qualify_incumbent_identity` validates exact immutable command roots using `validated_immutable_command_root` prior to shutdown, failing closed if validation rejects the incumbent root or if incumbent identity is absent or unqualified.
   - Fabricated heads from directory basenames and silent candidate identity substitutions are removed.
   - `_replace_supervisor_locked` validates the incumbent identity before calling `stop_existing_supervisor`, failing closed before any shutdown.
6. **Retained Old-Path Writer Drain and Anti-Recreation Fencing**:
   - Prior to storage migration and incumbent shutdown, `qualify_and_drain_incumbent_writers` drains running worker processes and fails closed on active reservations or in-flight queue events.
   - `_migrate_storage_paths` creates non-regular FIFO fences at retired state and queue paths, causing old writers running prior immutable code to fail closed on leaf regular-file assertions without recreating state files.
   - `runtime_state` strictly adheres to configured authority (`config_path(config, key)`), preserving symlink, split-root, and outside-root leaf validation without existence-based path redirection.
7. **Post-Rename Config Durability and Rollback Verification**:
   - In case of directory fsync EIO after atomic replacement of live config, the rollback handler restores and verifies incumbent config on disk, rolls back migrated storage files, and verifies disk state before restarting incumbent supervisor.
   - If restoration or verification fails, incumbent supervisor restart is refused and the single-writer exclusion lock is retained.

### Verification Commands
```bash
# Verify worker sandbox mount behavior, layout qualification, and governed writes
python3 -m pytest .orchestrator/test_worker_runner_heartbeat.py -k "test_bind_worker_sandbox or test_governed_writer"

# Verify common authority preconditions
python3 -m pytest .orchestrator/test_common.py -k "TestWriteStatusPrecondition"

# Verify promotion storage migration, lock drain, preflight, and rollback
python3 -m pytest scripts/test_promote_supervisor_runtime.py -k "migrate_storage_paths or storage_migration"
```
