# Supervisor, Auto Worker, and Dashboard

The sole current control-plane architecture is
[`docs/02-architecture/supervisor-authority-v2.md`](02-architecture/supervisor-authority-v2.md).

This path is retained because older task archives and operational links point
to it. It is not a second specification. The pre-V2 chair, discussion-planning,
failure-streak, helper, sidecar-materialization, and alternate-dispatch modes
described by earlier revisions are retired.

The dashboard is a projection only. Task truth comes from TaskStore; process
and lease truth comes from Worker Manager; dispatch decisions come from the one
planner; provider readiness comes from Account Health. Dashboard liveness or
its generated bundle never grants task, lease, dispatch, review, or rollout
authority.
