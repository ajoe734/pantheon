# OPS-ACTIVITY-V2-SUPERSEDED-RECOVERY-001

## Objective

Recover a schema-v2 content-addressed activity rotation that was superseded by
a legacy timestamp rotation, preserve every post-intent append, and prevent
auto workers from invoking stale status-command runtimes that can recreate the
incident.

Owner: `Codex`. Reviewer: `Claude`. Priority: `P0`.
Target: `pantheon/dev`.

scope:
- .orchestrator/activity_pending_intent_recovery.py
- .orchestrator/common.py
- .orchestrator/permission_broker.py
- .orchestrator/test_activity_pending_intent_recovery.py
- .orchestrator/test_provider_permissions.py
- .orchestrator/task-briefs/ops_activity_v2_superseded_recovery_001.md
- docs/deployment/evidence/ops-activity-v2-superseded-recovery-001

## Incident contract

- Accept only an exact schema-v1 or schema-v2 pending-intent shape.
- For schema-v2, prove the lineage predecessor, staged archive and tail,
  installed archive, unique legacy superseding archive, retained overlap, and
  all active suffixes before mutation.
- Preserve the original active log, lineage, intent, stages, and any append
  observed after the inventory pin.
- Publish the resolution, reconstructed active log, and intended lineage in a
  crash-safe order; remove the pending intent and stages last.
- Validate the complete logical stream with zero missing or duplicate event
  IDs before reporting success.
- Deny auto-worker status commands that resolve outside the installed
  `PANTHEON_COMMAND_ROOT`; leave non-worker operator commands unchanged.

## Validation

- Schema-v1 recovery and inventory compatibility.
- Schema-v2 exact incident, tamper rejection, crash/retry, and late-append
  conservation.
- Resolution filtering and complete activity logical-stream validation.
- Permission-broker stale runtime denial, pinned runtime allow, and non-worker
  compatibility.
- Post-install supervisor process, heartbeat, loop, dispatch, quota, and
  blocked-backlog readback.

