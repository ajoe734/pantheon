# L12-FLEET-001 fleet-capacity evidence

Status: in progress; the post-restart gate is not yet satisfied.

This packet records the reconciliation of the reviewed Codex-family fleet
policy into the split-root supervisor runtime. It does not authorize live
capital, bypass approvals, or replace the independent `Codex2` review.

## Reviewed source

- Task: `L12-FLEET-001`
- Owner: `Codex`
- Reviewer: `Codex2`
- Task branch base: `09159159fbac9b43d3e97011a12a224699677620`
  (`origin/dev` at worker dispatch)
- Fleet guard source: prerequisite merge commit
  `09159159fbac9b43d3e97011a12a224699677620`, including
  `2ed67c6d6` (`LOOP-GAP-FLEET-001`)
- Installed command root:
  `/home/lupin/pantheon-ci-deploy/dev-root`
- Canonical status root: `/home/lupin/pantheon`
- Live config:
  `/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json`

## Pre-restart checkpoint

At `2026-07-26T07:28:57Z` through `2026-07-26T07:36:20Z`:

- the installed command root was
  `be956c07aca889043ef301389412b6744452f20b`, 22 commits behind
  `origin/dev`;
- the worker's supervisor-issued command identity was
  `09159159fbac9b43d3e97011a12a224699677620`;
- governed `ai-status.sh show/start` failed closed on that exact SHA mismatch;
- `check_config_drift.py --max-behind 0 --json` reported no config-field
  drift but correctly exited 1 for the 22-commit command-root lag;
- the live ready-dispatcher overlay already matched the reviewed repo values:
  `disabled_agents=["Antigravity2","Copilot"]`,
  `sidecar_only_agents=[]`, `Codex=4`, `Codex2=4`,
  `codex1=4`, `codex2=4`, `max_dispatches_per_tick=10`, and
  `max_concurrent_workers=13`;
- live capability readback reported `auth_ready=true` and
  `verified="verified"` for all eight slots
  `codex1-1..codex1-4` and `codex2-1..codex2-4`;
- supervisor PID `829000` remained running with fresh canonical heartbeat and
  the watchdog timer was enabled and active;
- canonical task count was 25 with zero duplicate IDs;
- the approval queue had zero pending entries, the event queue had two rows,
  and the authoritative task journal had 1,203 rows.

## Deployment-lease safety gate

Pantheon nonprod deploy run
[`30192780633`](https://github.com/ajoe734/pantheon/actions/runs/30192780633)
held lease `f95b33cb-e262-41ff-905b-fdc86567c3a4`, owned by
`pantheon:ajoe734/pantheon:30192780633:1`, from
`2026-07-26T07:26:41Z`.

The lease heartbeat continued to advance while the deploy job was
`in_progress`. No supervisor restart, live-config edit, lease takeover, deploy
cancellation, or dispatcher apply was attempted while that lease was active.
Post-restart evidence remains blocked until the run is terminal and the lease
is released.

## Local validation

The task-scoped source was already complete in the reviewed prerequisite
merge, so this task did not introduce a speculative provisioner or drift-check
change.

```text
/home/lupin/pantheon/.venv/bin/python -m pytest -q \
  scripts/test_provision_live_supervisor_config.py \
  scripts/test_check_config_drift.py
.................................... [100%]
36 passed in 3.78s

/home/lupin/pantheon/.venv/bin/python -m py_compile \
  scripts/provision_live_supervisor_config.py \
  scripts/check_config_drift.py
exit 0
```

## Mutation-free catalog proof

`--validate-only` accepted catalog SHA-256
`a7fbbaa560bd7f2d97750b25cd20af69b64d4f522b293689849b0e1b1763717f`
with 25 tasks.

The guarded `--dry-run` succeeded against the authoritative external journal.
It reported 21 exact tasks, four create candidates, and the still-blocked
external dependency `PPL-ALLOC-009`. It did not apply those candidates.

The following SHA-256 values were byte-identical before and after the dry-run:

| File | SHA-256 |
| --- | --- |
| `ai-status.json` | `53979b4c5f6cf48827ab3cd8c352663f0b7b751828b929cf6297d743df4cf876` |
| `ai-activity-log.jsonl` | `ef21e3cba737ab9939946dbddca70673fcfba3a460926f45b558085e1325229e` |
| `current-work.md` | `eda58f1f681282c5fe28393b062e0867c01235a075de9a98c6bcd988c5218d23` |
| `.orchestrator/event-queue.jsonl` | `4417b5fe9910ed9a21dc83e67669fb8a13413cb47d5c2d357de80e1136870627` |
| `.orchestrator/approval-queue.json` | `e512ae0b0db03b1cb6fe1c7c29bc0f444ce6774aa270be418fa78f7398559646` |
| external `task-state-events.jsonl` | `d86ae20c7f5ab546e6cca5d70388fd6d1d5c839db8da58c6f9a4d3c586052be0` |

## Remaining acceptance

After terminal lease release:

1. sync the exact installed command root to `origin/dev`;
2. let the provisioner render the live overlay and use the governed,
   PID/SHA-bound restart intent;
3. prove a new supervisor PID/heartbeat plus fresh watchdog health;
4. re-read policy and all eight provider slots;
5. compare task IDs, queue event IDs, approvals, and task-journal projection
   across the restart;
6. attach the final machine-readable receipt and hand off to `Codex2`.
