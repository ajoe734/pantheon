# L12 Fleet Runtime Reliability Readback

Date: 2026-07-29
Task ID: `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729`
Owner: `Codex`
Reviewer: `Codex2`

## Scope and cutoffs

This is a read-only runtime inventory. It does not change
`.orchestrator/config.json`, supervisor code, provider settings, or runtime
state.

The fleet outcome table is frozen at `2026-07-29T09:46:32Z`. Supervisor
liveness was checked again at `2026-07-29T09:47:25Z`. Dynamic files were read
from:

- status and canonical-state root: `/home/lupin/pantheon`
- governed command and supervisor code root:
  `/home/lupin/pantheon-ci-deploy/dev-root`
- worker outcomes:
  `/home/lupin/pantheon/.orchestrator/worker-runtime/status`
- watchdog decisions:
  `/home/lupin/pantheon/.orchestrator/metrics/supervisor-watchdog.jsonl`
- watchdog contention decisions:
  `/home/lupin/pantheon/.orchestrator/metrics/supervisor-watchdog-contention.jsonl`

## Readback decision

The supervisor and all four requested provider lanes have positive execution
evidence, but the fleet was not reliable during this window.

- The supervisor was healthy at the final liveness check, with PID `3330999`,
  a successful heartbeat, and one live worker.
- Antigravity, Claude2, Codex, and Codex2 all completed at least one run on
  2026-07-29.
- The same day also contains 223 worker outcomes with `exit_code=143` and
  `signal=15`.
- Antigravity emitted direct `Error: context canceled` failures.
- The retained supervisor state contains repeated missing-process
  reconciliation failures, most heavily in Codex2.
- No explicit supervisor or worker-runtime `no-op`/`noop` failure event was
  found. The watchdog's non-mutating decisions are instead named
  `observe_only` and `skip`.

Provider readiness is therefore necessary but not sufficient. A readiness
probe must not be reported as proof that a dispatched worker will complete.

## 1. Governed command root and live supervisor identity

The current worker inherited:

| Field | Verified value |
| --- | --- |
| `PANTHEON_STATUS_ROOT` | `/home/lupin/pantheon` |
| `PANTHEON_COMMAND_ROOT` | `/home/lupin/pantheon-ci-deploy/dev-root` |
| `PANTHEON_COMMAND_RUNTIME_SHA` | `a6d56c366f7436574e6d2d241b47564558beac74` |
| command-root `git rev-parse HEAD` | `a6d56c366f7436574e6d2d241b47564558beac74` |
| status-root `git rev-parse HEAD` | `f1d8c708ae7e113db3bfaae26330dbdecbc61b54` |

The current run
`codex-20260729T094257Z-c271606a` records the same command root and source SHA
in `status_command_runtime`. The watchdog's `supervisor_root` readback at
`2026-07-29T09:47:25Z` also reports:

- `expected_root=/home/lupin/pantheon-ci-deploy/dev-root`
- `active_root=/home/lupin/pantheon-ci-deploy/dev-root`
- `split_from_expected=false`
- worker-runner root `/home/lupin/pantheon-ci-deploy/dev-root`

`/home/lupin/pantheon` is the status/canonical-state root, not the active
supervisor code root. Historical versioned command roots in old worker records
do not override this current identity.

## 2. Provider readiness and positive run evidence

`.orchestrator/provider_capabilities.json` was generated at
`2026-07-28T19:29:22Z`. Its last probes report:

| Lane | Auth ready | Local worker supported | Selected model |
| --- | --- | --- | --- |
| Antigravity / `antigravity1-1` | yes | yes | `gemini-3.6-flash-low` |
| Claude2 | yes | yes | provider default |
| Codex / `codex1-1` | yes | yes | provider default |
| Codex2 / `codex2-1` | yes | yes | provider default |

The generic `claude` lane was not auth-ready. That does not negate Claude2's
separate positive evidence.

Representative successful runs after the readiness file was generated:

| Lane | Run | Task | Finished UTC |
| --- | --- | --- | --- |
| Antigravity | `antigravity1-1-20260729T090839Z-b3f63931` | `OPS-PROMOTE-PR-CI-TRIGGER-001` | `09:09:14Z` |
| Claude2 | `claude2-20260729T083653Z-31131607` | `L12-VERIFY-OBS-001` | `08:39:01Z` |
| Codex | `codex-20260729T090948Z-3c8eb2b3` | `OPS-PROMOTE-PR-CI-TRIGGER-001` | `09:17:38Z` |
| Codex2 | `codex-20260729T082714Z-849e9984` | this task | `08:35:32Z` |

Codex versus Codex2 attribution in this table comes from the matching command
log filenames: `codex1_1` for the Codex row and `codex2_1` for the Codex2 row.

## 3. Fleet outcomes at the frozen cutoff

All 308 status JSON files started on 2026-07-29 and present by
`2026-07-29T09:46:32Z` were grouped by their structured outcome fields.

| Status `agent` | Total | Exit 0 | Exit 143 / signal 15 | Other failed | Running |
| --- | ---: | ---: | ---: | ---: | ---: |
| `antigravity1-1` | 70 | 44 | 25 | 1 (`exit_code=1`) | 0 |
| `claude2` | 52 | 19 | 33 | 0 | 0 |
| `codex` | 186 | 20 | 165 | 0 | 1 |
| **Total** | **308** | **83** | **223** | **1** | **1** |

The status schema deliberately collapses Codex and Codex2 slots into
`agent: "codex"`. It does not support an exact all-day Codex-versus-Codex2
split. Command-log correlation is valid for individual examples, but an exact
per-account total must retain an unknown bucket unless every run is
unambiguously correlated. This report does not publish an unsupported split.

## 4. Failure-loop readback

### SIGTERM and supersession

`exit_code=143` with `signal=15` is the dominant structured outcome: 223 of
308 runs at the frozen cutoff.

Run-ID correlation confirms supervisor supersession for individual cases. For
example, the supervisor log records:

```text
[2026-07-29 17:22:00] worker superseded:
task=SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729
provider=codex1-3 run=codex-20260729T091154Z-1c566ce2
```

The matching status JSON is a failed `exit_code=143`, `signal=15` run. This
proves the lifecycle correlation for that run. It does not prove that all 223
SIGTERM outcomes had the same cause; intentional deploy restarts and boot
reconciliation also terminate workers.

### Direct `context canceled`

Eight 2026-07-29 Antigravity command logs contain the direct one-line runtime
error `Error: context canceled`. This is distinct from the same text being
quoted later in a prompt or review log.

The two latest direct examples are both for this task:

| Run | Started UTC | Finished UTC | Outcome |
| --- | --- | --- | --- |
| `antigravity1-1-20260729T094021Z-c4b19802` | `09:40:21Z` | `09:40:24Z` | exit 143 / signal 15 |
| `antigravity1-1-20260729T094136Z-efe4deae` | `09:41:36Z` | `09:41:39Z` | exit 143 / signal 15 |

The supervisor then reassigned this task from Antigravity to Codex2 at
`09:42:32Z` and helper-claimed it for Codex at `09:42:41Z`. That sequence is a
concrete repeated-failure/fallback loop, not a provider-readiness failure.

### Missing process and missing PID

The earlier conclusion that missing-process evidence was absent was wrong.
Current retained supervisor state contains:

- 107 worker records whose `last_error` is
  `Worker process missing during supervisor boot reconciliation.`
  - 97 `codex2-1`
  - 9 `codex1-1`
  - 1 `claude2`
- 33 active task/provider failure-streak rows whose `last_reason` is the same
  missing-process message:
  - 13 Codex
  - 11 Codex2
  - 7 Claude2
  - 1 Antigravity
  - 1 generic Claude

These are retained-state counts, not unique all-day run totals. They show the
provider concentration and repeated-task streaks without pretending every row
is a new incident.

The watchdog has a separate supervisor-PID layer. On 2026-07-29 its metrics
record seven `restart_supervisor/pid_not_alive` decisions. Six were later
reclassified as Human/Ops repair restarts, while one remains a normal
`pid_not_alive` restart at `08:07:36Z`. Five additional restarts were explicitly
classified `intentional_deploy_restart`.

### No-op hypothesis

No direct runtime failure named `no-op` or `noop` was found in:

- 308 day-of worker status files
- 110 status-root supervisor logs
- current task failure-streak reasons

Raw command-log text is not suitable evidence here: its `no-op` matches are
worker prompts, source/test content, and CLI help such as `--no-open-unblock`.

The watchdog's actual no-action vocabulary on 2026-07-29 is:

| Decision | Reason | Count |
| --- | --- | ---: |
| `observe_only` | `supervisor_healthy` | 369 |
| `skip` | `lock_contention` | 112 |
| `suppress_restart` | `restart_budget_window_exhausted` | 1 |
| `suppress_restart` | `watchdog_circuit_open` | 2 |

`observe_only` is healthy no-action. `skip/lock_contention` is fail-closed
contention handling. Neither should be relabeled as a worker `no-op` failure
without a structured event saying so.

## 5. Current state and residual risk

At `2026-07-29T09:47:25Z`, the watchdog reported:

- `decision=observe_only`
- `reason=supervisor_healthy`
- supervisor PID `3330999`
- one live worker and one runtime-state worker
- expected, active, and worker-runner roots aligned
- restart counters for the current window/hour at zero

The live supervisor is therefore healthy at readback time. Residual risk
remains high because readiness probes coexist with:

- a 72.4% SIGTERM-class outcome rate in the frozen day-of snapshot
  (`223 / 308`)
- direct Antigravity `context canceled` repeats
- concentrated Codex2 missing-process reconciliation records
- repeated intentional runtime restarts during active work

This task records those facts only. Configuration and runtime repair remain
outside scope.

## 6. Verification commands

The readback was assembled with read-only commands:

```text
git -C "$PANTHEON_COMMAND_ROOT" rev-parse HEAD
git -C "$PANTHEON_STATUS_ROOT" rev-parse HEAD
jq ... "$PANTHEON_STATUS_ROOT/.orchestrator/state.json"
jq ... "$PANTHEON_STATUS_ROOT/.orchestrator/provider_capabilities.json"
jq -s ... "$PANTHEON_STATUS_ROOT"/.orchestrator/worker-runtime/status/*20260729T*.json
rg ... "$PANTHEON_STATUS_ROOT/.orchestrator/logs"
rg ... "$PANTHEON_COMMAND_ROOT/.orchestrator/logs"
jq -s ... "$PANTHEON_STATUS_ROOT/.orchestrator/metrics/supervisor-watchdog.jsonl"
jq -s ... "$PANTHEON_STATUS_ROOT/.orchestrator/metrics/supervisor-watchdog-contention.jsonl"
```

Repository scope check: no `.orchestrator/config.json` change is present in
this task branch diff.
