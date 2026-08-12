# Retired Execution Queue Isolation

Status: historical evidence only

The 2026-05-03 execution-only local override and its coordination/chair queue
were retired by Supervisor Authority V2. Chair and discussion-planning are not
supervisor modes; planning or governance work that must execute is represented
as an ordinary canonical task and follows the same planner and queue.

The archived queue at
`.orchestrator/backups/retired-queues/20260504-stale-coordination-dispatch/`
remains audit evidence. Do not copy or bulk-append it into the active queue.
Legacy `coordination:*` and `chair_review:*` intents must fail promotion rather
than be translated into a second dispatch path.

The current architecture, cutover rules, and rollback semantics are defined
only in
[`docs/02-architecture/supervisor-authority-v2.md`](../02-architecture/supervisor-authority-v2.md).
