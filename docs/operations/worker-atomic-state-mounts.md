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
2. All commit locks and temporary publication files (`*.tmp`) remain entirely inside `task-state/`.
3. `_append_task_store_mounts` mounts the dedicated `task-state/` directory rather than scanning individual sibling files, eliminating `ENOENT` races from disappearing temp files.
4. The outer runtime directory (`runtime_parent`) is mounted `--ro-bind-try` with conflict checks against `workspace_path` and `coordination_root` to ensure runtime configuration files remain read-only without clobbering the leased worktree.

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
1. `_migrate_storage_paths(incumbent_config, rendered_config)` inspects the incumbent configuration.
2. If task-store paths are in the legacy root (`runtime_parent`) and the new configuration targets `runtime_parent/task-state/`, the script:
   - Acquires the task-state store lock.
   - Ensures `runtime_parent/task-state/` exists with `0o700` permissions.
   - Migrates existing `task-state-events-v2.jsonl`, `head.json`, `lock`, and `legacy-anchor.json` files.
3. If supervisor launch fails during promotion, single-writer rollback restores the migrated files to their original paths.

### Verification Commands
```bash
# Verify worker sandbox mount behavior and concurrent publication
python3 -m pytest .orchestrator/test_worker_runner_heartbeat.py -k "test_bwrap"

# Verify common authority preconditions
python3 -m pytest .orchestrator/test_common.py -k "TestWriteStatusPrecondition"

# Verify promotion storage migration and rollback
python3 -m pytest scripts/test_promote_supervisor_runtime.py -k "storage_migration"
```
