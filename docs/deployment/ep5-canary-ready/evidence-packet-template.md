# EP5-002 Evidence Packet Template

Use this template when archiving the first canary/live proof packet under
`docs/deployment/evidence/ep5-*`.

## Required Header

- proof mode: `canary` or `live`
- timestamp window
- operator / approver identity
- code commit
- env-file revision
- credentials revision
- broker / venue target
- deployment plan id
- runtime binding id

## Required Artifacts

1. deployment plan / runtime binding identifiers
2. runtime stage transition proof
3. telemetry and lineage excerpt proving the execution path was real
4. rollback drill transcript or command log
5. post-rollback state snapshot
6. operator acceptance note
7. exception / follow-up note, if any

## Minimal Packet Layout

```text
docs/deployment/evidence/ep5-<timestamp>/
  README.md
  deployment-plan.json
  runtime-stage-transition.json
  telemetry-lineage-excerpt.json
  rollback-drill.request.json
  rollback-drill.response.json
  post-rollback-state.json
  operator-signoff.md
  followups.md
```

## Closeout Reminder

A successful run without this archived packet is still not a truthful `EP5` claim.
