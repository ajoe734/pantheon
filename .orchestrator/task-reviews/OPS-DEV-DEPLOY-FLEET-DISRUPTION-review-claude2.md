# Review: OPS-DEV-DEPLOY-FLEET-DISRUPTION

Reviewer: Claude2
Owner: Claude
Date: 2026-06-16

## Verdict: APPROVED

## Summary

Both fixes correctly address the root cause (full-stack compose rebuild causing
OOM that kills all 15 paper-runtime workers) and the recovery gap (reconciler
counting exit-137 against the application-failure cap, preventing auto-recovery).

The implementation is minimal, correctly scoped, and requires no follow-up.

## Fix (a) — `--component bff` in `deploy_nonprod_vm.sh`: PASS

- ✅ Help text documents the new value with an accurate one-line description.
- ✅ `dev` environment validation block (`root|bff`) correctly gates `bff` to
  dev-only; `staging-live` does not allow it (appropriate — staging runs `all`).
- ✅ `bff` case block calls `snapshot_remote_state` and `prepare_deploy_worktree`
  consistently with the `root` case.
- ✅ `COMPOSE_PROFILES=""` prevents compose profiles from activating additional
  services (especially the paper fleet); this is the right guard.
- ✅ `--no-deps` ensures only `operator-bff` is brought up, even if compose
  would normally pull in declared service dependencies.
- ✅ Env-var set is complete: all `PANTHEON_BFF_*`, `PANTHEON_ASSISTANT_*`,
  `MANAGEMENT_AI_*`, and `PANTHEON_STATUS_ROOT_*` vars forwarded.
- ✅ Health checks on `:18001/health` and `:18001/readyz` are consistent with
  the existing BFF surface.
- ✅ `deploy_dev_bff()` helper and `dev:bff` dispatch case follow the same
  `ssh_bash` pattern as the existing `dev:root` case.

## Fix (c) — Reconciler SIGKILL (exit 137) recovery in `paper_fleet_reconciler.py`: PASS

- ✅ `WorkerEntry.last_exit_code: Optional[int] = None` field confirmed at
  line 103; set at line 239 when `process.poll()` returns a non-None code.
- ✅ `is_sigkill = entry.last_exit_code == 137` correctly identifies the
  infrastructure-kill case (OOM killer / compose recreate sends SIGKILL → 128+9=137).
- ✅ `effective_restarts = 0 if is_sigkill else entry.restart_count` means:
  - SIGKILL path: restart_count resets to 1 after recovery; backoff = 0 so the
    worker is restarted at the next reconcile cycle with no delay.
  - Application-failure path: unchanged; restart_count accumulates toward
    `RECONCILER_MAX_RESTARTS` as before.
- ✅ Passing `restart_count=effective_restarts + 1` to `_start_worker` is correct;
  it resets the counter for SIGKILL and increments normally for app failures.
- ✅ Reconciler full-restart path (compose up replaces the reconciler itself)
  already works by design: `_workers` dict empties and the first reconcile cycle
  re-derives desired state from runtime-manager. No change needed for that path.
- ✅ Persistent-OOM edge case acknowledged: a worker that repeatedly OOM-exits
  will keep restarting. This is acceptable for the dev environment; a persistent
  OOM is an infrastructure issue that requires operator intervention regardless.

## Commit trailers

- `LLM-Agent: Claude` ✅
- `Task-ID: OPS-DEV-DEPLOY-FLEET-DISRUPTION` ✅
- `Reviewer: Claude2` ✅ (matches this reviewer)
- `Verified: static code review; no live VM available in worktree context` ✅

## No required changes

The implementation is correct and complete as reviewed.
