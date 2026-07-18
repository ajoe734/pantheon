# Supervisor Singleton Lock and Status Root Boundaries

Status: canonical
Last updated: 2026-07-18
Tier: L1 Platform Architecture & Policy

## Purpose

To prevent split-brain coordination loops and conflicting worker dispatches, the supervisor loop must run as a strict singleton across the entire automated trading platform coordination plane. 

This document defines the singleton lock semantics, the authority boundaries of the status root, and the consistency rules between the runtime environment and configuration.

## Singleton Lock Scope

The supervisor singleton lock is implemented via advisory `flock` on a single file:
- **Authoritative Path**: `<status_root>/.orchestrator/supervisor.lock`

Any supervisor instance pointing to the same status root (regardless of its working directory, execution codebase, or launch path) must try to acquire this lock exclusively at startup. If another instance already holds the lock, the process must immediately exit without modifying any state or pid files.

## Status Root and Environment Consistency

The status root (containing `ai-status.json`, the activity logs, and state files) is the coordination plane's single source of truth. 

To prevent configuration errors where a supervisor launched from a task worktree inherits the host environment's live paths but operates on local lock files (leading to duplicate parallel supervisors), the following consistency gate is enforced at startup:

1. **Gate Check**: If the environment variable `PANTHEON_STATUS_ROOT` is set, it must match the status root path resolved by the supervisor's configuration.
2. **Fail-Fast**: If they do not match, the supervisor will write an error message to `stderr` and exit with code `1`.
3. **Bypass Flag**: To run in mismatched testing or sandbox conditions, the supervisor must be launched with the explicit `--allow-isolated-status-root` flag, or the `PANTHEON_STATUS_ROOT` environment variable must be cleared.

## Watchdog & Health Check Coordination

The supervisor watchdog (`supervisor_watchdog.py`) and runtime health checking scripts (`supervisor_runtime_health.py`) use the same status root resolution logic to probe for supervisor liveness. They must resolve the lock path dynamically using the configured paths and env variables to check liveness on the authoritative status root lock, preventing false negatives or duplicate launches.
