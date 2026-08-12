# Pantheon Orchestrator Logic

Status: superseded by Supervisor Authority V2

The proposal previously kept a watcher producer, retry/fallback ladder,
failure-streak reassignment, and optional helper paths. Those intermediate
designs are retired. The single current specification is
[`docs/02-architecture/supervisor-authority-v2.md`](02-architecture/supervisor-authority-v2.md).

The operative control flow is canonical TaskStore snapshot → shared pure
planner → one durable delivery queue → one launch call → exact worker lease.
Status watchers, manual wake events, GitHub direct dispatch, retry direct
launch, chair, and discussion-planning are not alternate producers.
