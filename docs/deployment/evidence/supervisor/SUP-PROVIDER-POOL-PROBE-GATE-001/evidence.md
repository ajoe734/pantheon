# SUP-PROVIDER-POOL-PROBE-GATE-001 — provider pool probe gate and root coherence

Owner: Claude2 · Reviewer: Claude · Repository: `ajoe734/pantheon` · Base: `dev`
Task branch: `task/SUP-PROVIDER-POOL-PROBE-GATE-001`

## What was wrong

Five coupled control-plane hazards, all observed on the live dev VM fleet:

1. **The capability report was a launch gate.** `provider_capabilities()` called
   `_codex_auth_probe(..., force=True)`. The supervisor calls
   `probe_provider_reports()` before every loop, so an intended telemetry refresh
   re-ran a real `codex exec` smoke for every Codex alias on every tick,
   regardless of `provider_auth.probe_interval_seconds=900`.
2. **The Antigravity pool was over-counted.** `antigravity` and
   `antigravity1-1` … `antigravity1-4` all resolve to the same `/home/lupin`
   OAuth token. Each alias ran its own `agy --prompt` smoke and published its own
   `auth_ready` lane, so one exhausted quota account looked like five independent
   healthy worker lanes. Live probe evidence at 2026-07-27T18:04Z had every one
   of those aliases failing with `Individual quota reached`.
3. **Worktree base-ref readiness was a per-loop flag, not an invariant.**
   `_worker_base_ref_precondition` accepted only the in-cycle
   `_PREFETCHED_WORKER_BASE_REFS` context as proof. After a provider probe, a
   worker failure, a redispatch, or a split-root restart, a dispatch could cross
   into a cycle whose context never listed the base even though `origin/dev`
   resolved fine — producing a `base_ref_not_prefetched:origin/dev` scheduler
   stall that looked like a missing fetch.
4. **A clean owner exit after PR preparation looped.** An owner run that pushed
   the exact review head and exited 0 without advancing the task was recorded as
   a generic worker exit. The task stayed `in_progress` with the same owner, so
   the next tick reissued `owned_in_progress_dispatch` and the run reproduced the
   identical clean exit — tokens burned instead of reviewer dispatch.
5. **Sync repaired a path, not the live process.** `scripts/sync-dev-root.sh`
   only ever knew a default `dev-root` argument. The live supervisor was observed
   running from `dev-root-6692d51c9bc5` while worker runners launched from
   `dev-root-29054ab270d5`, and the stale root sat 63 commits behind `origin/dev`
   while the sync log reported success.

## What changed

| Layer | File | Change |
|---|---|---|
| Provider telemetry | `.orchestrator/provider_permissions.py` | `provider_capabilities()` no longer forces the Codex probe; it honours `provider_auth.probe_interval_seconds`. |
| Provider pool | `.orchestrator/provider_permissions.py` | `_antigravity_credential_group` / `_antigravity_provider_reports`: aliases sharing one OAuth token+home, or one declared `account`/`account_group`/`quota_group`, share a single probe result and publish `account_group`. |
| Worktree dispatch | `.orchestrator/supervisor.py` | `_worker_base_ref_precondition(base_ref, repo_root)` falls back to a recovery fetch plus a git ref-resolution check; fails closed only when the ref does not resolve. |
| Worker lifecycle | `.orchestrator/supervisor.py` | `worker_prepared_review_head` / `record_missing_handoff_blocker`: a clean owner exit with a provenance-backed pushed PR head records an actionable `missing_handoff` blocker and moves the task off `in_progress`; mention-only scraped PR URLs and finalize exits stay out of this blocker path. |
| Root coherence | `.orchestrator/supervisor_watchdog.py` | `process_working_directory`, `scan_worker_runner_roots`, `supervisor_root_report`; every watchdog decision now carries `supervisor_root`. |
| Root coherence | `scripts/sync-dev-root.sh` | Resolves the live supervisor's real root from `/proc/<pid>/cwd`, syncs it as well as `dev-root`, and logs `ACTIVE_ROOT_SPLIT` plus HEAD/target evidence. `SYNC_ACTIVE_ROOT=0` reports without repairing. |

### Constraints honoured

- `probe_provider_auth(config, provider, force=True)` keeps forced semantics; it
  remains the authoritative pre-dispatch check
  (`refresh_provider_auth_before_dispatch` is untouched).
- Provider auth is not disabled anywhere; provider failure pausing is unchanged
  and now benefits from the published `account_group`, which
  `provider_dispatch_group_id` already consumes for quota-account-aware pausing.
- No Pantheon product service, Agora contract, dev deployment, FE/BFF, or GitHub
  custom status context was touched.

## Acceptance mapping

| # | Acceptance | Evidence |
|---|---|---|
| 1 | `provider_capabilities()` reuses a recent Codex probe inside the interval | `ProviderProbeGateTest.test_codex_probe_reuses_recent_result_inside_probe_interval`, `…_reruns_exec_once_the_probe_interval_elapsed`, and the updated `force` assertion in `test_provider_capabilities_marks_codex_revoked_token_auth_down` |
| 2 | Targeted `probe_provider_auth(..., force=True)` still probes fresh | `ProviderProbeGateTest.test_targeted_probe_provider_auth_force_still_runs_a_fresh_probe` |
| 3 | Shared-credential Antigravity aliases are not independent capacity | `ProviderProbeGateTest.test_antigravity_aliases_sharing_a_token_share_one_probe`, `…_declared_quota_group_shares_capacity` |
| 4 | A supervisor loop report spawns no fresh provider CLI smoke when none is due | `CachedProviderCapabilityLoopTests.test_loop_report_reuses_cached_probes_when_none_are_due` (drives the real `supervisor.probe_provider_reports`) |
| 5 | Watchdog/sync report or repair the actual active root | `SupervisorRootCoherenceTests` (5 tests) plus `raw/sync-dev-root-split-smoke.txt` |
| 6 | Base-ref readiness survives a recovery/redispatch boundary; still fails closed | `WorkerBaseRefPreconditionTests` (8 tests, incl. a real `git worktree add` across an empty context, a bounded recovery fetch, and an unresolvable-ref fail-closed case) |
| 7 | A clean owner exit cannot loop `owned_in_progress_dispatch` | `MissingHandoffBlockerTests` (3 tests) plus `PollWorkersRecoveryTests.test_completion_stage_blocks_prepared_head_without_handoff`, `…_keeps_generic_exit_when_no_head_was_prepared`, `…_ignores_mention_only_pr_url`, `…_does_not_block_finalize_exit`, `test_worker_prepared_review_head_requires_an_owner_dispatch` |
| 8 | PR against `dev`, checks, independent review, merge, live root repair | Recorded in `evidence.json` `delivery` / `live_repair` once the PR merges |

## Verification

```bash
python3 scripts/dev/provision_python_distribution.py
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
cd .orchestrator && "$PANTHEON_PY" -m pytest -q \
  test_provider_permissions.py test_supervisor.py test_supervisor_watchdog.py
# 576 passed, 9 subtests passed
```

Raw output: `raw/orchestrator-suites.txt`, `raw/acceptance-selections.txt`.

### 2026-07-27 reviewer rework

Claude review found that the acceptance-4 loop regression depended on the
worker shell `PATH` containing `codex` and `agy`. The test now creates temporary
executable stubs and points the provider config at their absolute paths, so the
cache assertion is independent of the worker environment. Human/Ops also removed
the stale reviewer label from this evidence file; reviewer remains `Claude` for
the re-requested review.

Claude's follow-up AC7 review found that a regex-scraped PR URL could falsely
trigger the missing-handoff blocker when an owner run merely mentioned a PR URL,
and that `owned_finalize_dispatch` could be moved from `review_approved` to
`blocked` while waiting for auto-merge. The blocker now requires structured
`result` payload provenance (or a future explicit prepared-head flag), and
finalize dispatches are excluded from the prepared-head path. Regression tests
cover both false positives.

### Known pre-existing failure, out of scope

`scripts/test_supervisor.py::SupervisorRuntimeStateTests::test_run_once_reconciles_execution_mode_state_from_running_workers`
fails with `AttributeError: module 'supervisor' does not have the attribute
'dispatch_underutilization_sidecars'`. That symbol does not exist on
`origin/dev` either (`git show origin/dev:.orchestrator/supervisor.py | grep -c
dispatch_underutilization_sidecars` → `0`), so the test is stale on the mainline
and unrelated to this task. It is not a CI gate: `branch-ci.yml` runs only the
packaging tests and the smoke gate. Left untouched rather than folded into this
task's scope.
