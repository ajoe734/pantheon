# SUP-L12 Fleet Dispatch Health Evidence

Observation cut: `2026-07-29T01:14:08Z`

This is a task-scoped, read-only audit of the live supervisor, assistant
DevTaskPacket drain, and provider-first worker behavior. It does not change
provider configuration, credentials, dispatch policy, or product services.

## Verdict

The task acceptance is satisfied, but the observed fleet state is
`degraded_but_failover_working`, not fully healthy.

- The live supervisor process is PID `2082839`, with executable and working
  directory rooted at `/home/lupin/pantheon-ci-deploy/dev-root`. Its live
  command runtime is commit
  `a6d56c366f7436574e6d2d241b47564558beac74`, which was an ancestor of
  `origin/dev` at the observation cut.
- Packet `pkt-l12-current-gap-drain-20260729T0107Z` reached a durable receipt
  and a terminal `failed/` archive. Two task records, including this task,
  were dispatched; two other records failed the artifact-overlap guard.
  Packet-level status is therefore `failed`, admission was not attempted, and
  the result is non-retryable.
- The last persisted Antigravity auth probe said `ready`, but this task's two
  actual Antigravity runs both ended after three seconds with exit `143`,
  signal `15`. The activity trail classified the loop as
  `Error: context canceled` and reassigned the task after the repeated
  terminal outcome. This is not evidence of an auth failure; it is evidence
  that current task execution through the Antigravity slot was unhealthy.
- Claude2 had a live `running` worker with a fresh heartbeat at the cut. The
  provider capability record also said `auth_ready=true`. Claude2 was busy on
  `L12-MANIFEST-001`, so the evidence proves readiness and active dispatch,
  not spare capacity.
- After Antigravity failed twice, the supervisor reassigned the lane and an
  idle Codex helper claimed it. The Codex worker started successfully at
  `2026-07-29T01:09:20Z`, demonstrating that fallback dispatch continued.

## Live Supervisor Root

The following independent facts bind the running supervisor to the governed
command root:

- Process command:
  `/usr/bin/python3.12 -u /home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/supervisor.py --config /home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json --verbose`
- `/proc/2082839/cwd`:
  `/home/lupin/pantheon-ci-deploy/dev-root`
- Process environment:
  `PWD=/home/lupin/pantheon-ci-deploy/dev-root` and
  `PANTHEON_STATUS_ROOT=/home/lupin/pantheon`
- Worker status records:
  `status_command_runtime.command_root=/home/lupin/pantheon-ci-deploy/dev-root`
  and
  `status_command_runtime.source_sha=a6d56c366f7436574e6d2d241b47564558beac74`
- `.orchestrator/state.json`:
  supervisor PID `2082839`, last successful loop
  `2026-07-29T01:13:37Z`, and no last-loop error.

The command root was not at the latest observed `origin/dev` tip and contained
runtime-generated task-brief changes. Neither fact invalidates the process/root
binding, but this evidence does not claim a clean or tip-synchronized command
root.

## Packet Lifecycle

Receipt:

`/home/lupin/pantheon/.orchestrator/assistant-dev-packets/receipts/pkt-l12-current-gap-drain-20260729T0107Z.json`

Archive:

`/home/lupin/pantheon/.orchestrator/assistant-dev-packets/failed/pkt-l12-current-gap-drain-20260729T0107Z.json`

The receipt was written at `2026-07-29T01:06:06Z`. It records:

- dispatched:
  `SUP-L12-ROOT-GATE-4326-20260729`,
  `SUP-L12-FLEET-DISPATCH-HEALTH-20260729`
- rejected by artifact conflict guard:
  `SUP-L12-MANIFEST-REVIEW-BIND-20260729`,
  `SUP-L12-STALE-CLOSEOUT-PR-DRAIN-20260729`
- `status=failed`
- `admissionStatus=not_attempted`
- `retryable=false`

The packet references
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-29T0100Z.md`,
but that document was absent from both this task branch and the live status
root at the cut. The receipt still retained the reference and materialized the
two non-conflicting tasks. This missing source document is a residual
traceability gap, not a reason to rewrite the packet or configuration in this
task.

## Provider-First Runtime Facts

### Antigravity

Persisted capability record at
`/home/lupin/pantheon/.orchestrator/provider_capabilities.json`:

- generated `2026-07-28T19:29:22Z`
- `auth_ready=true`
- auth method `agy_prompt_oauth`
- last probe `2026-07-28T19:29:39Z`
- selected model `gemini-3.6-flash-low`

Task worker records:

| Worker run | Started | Finished | Status | Exit/signal |
|---|---|---|---|---|
| `antigravity1-1-20260729T010630Z-3ff0066c` | `01:06:30Z` | `01:06:33Z` | failed | `143` / `15` |
| `antigravity1-1-20260729T010745Z-1a61ab8e` | `01:07:45Z` | `01:07:48Z` | failed | `143` / `15` |

### Claude2

Persisted capability record:

- `auth_ready=true`
- auth method `claude_auth_status_refresh`
- last probe `2026-07-28T19:29:29Z`

Live worker record
`claude2-20260729T010922Z-561642e1` was `running` on
`L12-MANIFEST-001`; it started at `2026-07-29T01:09:22Z` and its
persisted heartbeat had advanced to `2026-07-29T01:12:53Z`.

## Repeated Failure and Failover Timeline

- `01:06:30Z`: first Antigravity worker started.
- `01:06:33Z`: first worker ended with exit `143` / signal `15`.
- `01:07:21Z`: boot reconciliation recorded
  `Error: context canceled`, scheduled retry 1, and preempted the task to free
  Antigravity for higher-priority review/finalize work.
- `01:07:45Z`: second Antigravity worker started.
- `01:07:48Z`: second worker ended with exit `143` / signal `15`.
- `01:08:32Z`: supervisor reassigned ownership after repeated Antigravity
  terminal outcomes.
- `01:08:46Z`: idle Codex helper claimed the task; Codex2 became reviewer.
- `01:09:20Z`: Codex worker started.
- `01:09:27Z`: governed task state advanced to `in_progress`.

No edit was made to `.orchestrator/config.json`. The evidence records the live
loop and resulting fallback instead of masking it with a configuration
mutation.

## Verification

- Governed `AI_NAME=Codex "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show
  SUP-L12-FLEET-DISPATCH-HEALTH-20260729`
- `ps`, `/proc/2082839/cwd`, and selected supervisor environment inspection
- `jq` checks over the exact packet receipt/archive, capability records, three
  worker-runtime status records, and exact task-id activity events
- `git diff -- .orchestrator/config.json` returned empty
- `python3 -m json.tool evidence.json`
- `sha256sum -c evidence.sha256`
- `git diff --check`
