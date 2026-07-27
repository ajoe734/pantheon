# SUP-PROVIDER-POOL-PROBE-GATE-001

## Context

The active Pantheon supervisor/fleet control plane is dispatching real auto
workers from the dev VM, but the runtime architecture has three coupled
control-plane hazards that must be fixed together instead of patched
per-symptom:

1. Supervisor runtime roots split: the live supervisor was observed running from
   `/home/lupin/pantheon-ci-deploy/dev-root-6692d51c9bc5` while worker runners
   were launched from `/home/lupin/pantheon-ci-deploy/dev-root-29054ab270d5`.
   The stale supervisor root was 63 commits behind `origin/dev` before a
   temporary live sync repaired the active root to `a6966b13d...`.
2. Provider capability refresh is too expensive for the control loop. The live
   config has `provider_auth.probe_interval_seconds=900`, but
   `provider_capabilities()` currently forces Codex provider probes during full
   report generation (`_codex_auth_probe(..., force=True)`). The supervisor
   calls `probe_provider_reports()` before every loop, so an intended telemetry
   refresh can repeatedly invoke CLI smokes such as `codex exec` / `agy
   --prompt`.
3. The Antigravity pool is over-counted. `antigravity`, `antigravity1-1` ...
   `antigravity1-4` all use the same `/home/lupin` OAuth token, so they are not
   independent quota capacity. Live probe evidence at 2026-07-27T18:04Z showed
   all of those aliases failing with `Individual quota reached`; `antigravity2`
   used `/home/lupin/.gemini-agy2` but failed eligibility. The scheduler should
   not treat these aliases as independent healthy worker lanes.
4. Worker worktree dispatch can still fail with
   `base_ref_not_prefetched:origin/dev` even when `origin/dev` resolves in the
   relevant repositories. The current dispatch precondition trusts the
   per-loop `_PREFETCHED_WORKER_BASE_REFS` context as the only proof that the
   base is ready; after provider probes, worker failures, redispatches, or
   split-root restarts, that proof can be absent while the git ref itself is
   available. The result is a scheduler stall that looks like a missing fetch
   but is actually a control-loop invariant bug.
5. Owner workers can exit 0 after pushing a prepared PR head without advancing
   the canonical task to `review`/handoff. The supervisor then records
   "Worker exited before the task reached a terminal status" and redispatches
   the owner repeatedly, producing token loops instead of reviewer dispatch.

## Goal

Make provider readiness and runtime-root coherence safe enough for long-running
fleet execution:

- full provider capability reports must be bounded, cached, and non-blocking for
  dispatch when no probe is due;
- fresh forced auth probes must happen only at the targeted launch gate for the
  concrete provider/account about to receive a worker;
- provider aliases that share the same credential/quota group must not multiply
  schedulable capacity;
- watchdog/sync logic must keep the actual live supervisor root and command root
  coherent with `origin/dev` and must expose evidence when the active root is
  stale or split from worker runner root.
- worker worktree base-ref readiness must be a durable git/ref invariant, not a
  fragile per-loop context flag;
- an owner run that cleanly prepares an exact review head must either hand off
  to the reviewer or fail with a concrete missing-handoff error that does not
  endlessly redispatch the same run shape.

## Required architecture constraints

- Do not change Pantheon product services, Agora contracts, or dev deployment as
  part of this task.
- Do not deploy FE/BFF as part of this task.
- Do not rely on GitHub custom status contexts as authority for this fix.
- Do not disable provider auth globally as the permanent solution.
- Preserve targeted `probe_provider_auth(..., force=True)` semantics for the
  final pre-dispatch check of a selected provider.
- Preserve provider failure pausing, but make it quota-account aware rather than
  alias-count aware.

## Acceptance

1. Add tests proving `provider_capabilities()` reuses recent Codex auth probe
   results by default and does not call `codex exec` again while
   `provider_auth.probe_interval_seconds` has not elapsed.
2. Add tests proving targeted `probe_provider_auth(config, provider, force=True)`
   still performs a fresh provider probe for the exact provider being launched.
3. Add tests proving Antigravity aliases that share the same OAuth token/home or
   declared quota group are not counted as independent schedulable provider
   capacity after a quota/auth failure.
4. Add tests or smoke evidence proving supervisor `run_once` can complete a loop
   using cached provider capabilities without spawning fresh provider CLI smoke
   probes when probes are not due.
5. Add tests or smoke evidence proving the watchdog/sync path reports or repairs
   the actual active supervisor cwd/root, not only a default `dev-root` path.
6. Add tests proving worktree creation/refresh accepts a resolved and freshly
   fetched `origin/dev` ref even when `_PREFETCHED_WORKER_BASE_REFS` context is
   empty because the dispatch crosses a recovery/redispatch boundary; still fail
   closed when the ref truly does not resolve.
7. Add tests or lifecycle evidence proving a clean owner worker exit after PR
   preparation cannot cause infinite `owned_in_progress_dispatch` loops; the
   task must move to review/handoff or expose an actionable missing-handoff
   blocker.
8. Produce a PR against `dev`, wait for checks, and merge only after independent
   review. After merge, perform one temporary live repair step to resync the
   actual supervisor root and restart it, then record evidence that the live root
   HEAD equals `origin/dev` and the queue is still healthy.

## Suggested files

- `.orchestrator/provider_permissions.py`
- `.orchestrator/supervisor.py`
- `.orchestrator/supervisor_watchdog.py`
- `scripts/sync-dev-root.sh`
- `.orchestrator/test_provider_permissions.py`
- `scripts/test_supervisor.py`
- `.orchestrator/test_supervisor_watchdog.py`
- `docs/deployment/evidence/supervisor/SUP-PROVIDER-POOL-PROBE-GATE-001/`
