# OCLAW-PMEM-005 - Dev Gates And Gap Closeout

Owner: Codex
Reviewer: Claude
Parent: `OCLAW-PMEM-000`
Depends on: `OCLAW-PMEM-002`, `OCLAW-PMEM-003`, `OCLAW-PMEM-004`

## Problem

The current dev checks can let a management page look populated while the real
architecture remains broken. The closeout must prove BFF, OpenClaw, provider
pool, persona routing, and Memory Plane integration end to end.

## Scope

- Add dev gates or smoke probes that cover:
  - BFF persona create/update to OpenClaw agent reconciliation;
  - model routing drift detection/update;
  - `openclaw/{persona_id}` live response identity;
  - canonical Memory Plane retrieval through BFF;
  - canonical memory materialization into OpenClaw workspace;
  - private memory non-leakage between personas;
  - provider auth readiness plus live smoke for Codex and Claude paths.
- Archive hosted dev evidence under the gap archive.
- Close `OCLAW-PMEM-000` only after child task PRs are merged or reviewer-approved
  superseded.

## Acceptance

- Final closeout lists every child task PR, merge SHA, validation command, dev
  evidence artifact, and residual risk.
- The gate fails when provider mount is ready but live provider smoke fails.
- The gate fails when BFF persona memory does not return canonical memory.
- The gate fails when OpenClaw workspace memory lacks source IDs from canonical
  Memory Plane.
- Parent task is reviewer-approved and archived only after the evidence above
  is present.
