# Agent Orchestrator

The current control-plane specification is
[`docs/02-architecture/supervisor-authority-v2.md`](02-architecture/supervisor-authority-v2.md).
This page is only the short operator entry point.

Start the supervisor:

```bash
python3 .orchestrator/supervisor.py
```

Run one complete cycle:

```bash
python3 .orchestrator/supervisor.py --once
```

Inspect why a canonical task is or is not dispatchable:

```bash
python3 scripts/explain_dispatch.py TASK-ID --json
```

Verify runtime identity and readiness:

```bash
python3 scripts/supervisor_runtime_health.py --require-watchdog --json
```

There is no watcher-only dispatch, manual wake event, GitHub `/dispatch`,
chair lane, or coordination queue replay in V2. Create or update a canonical
task through `scripts/ai-status.sh`; the shared planner is the only producer of
delivery intents, and the delivery queue is the only worker-launch route.

Provider permission and approval tools remain separate safety controls for the
worker process. They do not assign tasks or create dispatch intents.
