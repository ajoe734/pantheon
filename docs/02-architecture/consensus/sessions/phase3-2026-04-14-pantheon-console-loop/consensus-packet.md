# Consensus Packet

## Decision Summary

- Session: `phase3-2026-04-14-pantheon-console-loop`
- Scope: formalize the Pantheon Console closed loop and execution backlog without mutating the already accepted phase2 blueprint-gap session.
- Accepted architecture draft:
  - keep `.coordination` as the canonical machine protocol
  - treat Lovable as a human-triggered UI accelerator, not a headless executor
  - add `frontend-feedback` and `backend-delivery` as first-class loop artifacts around the existing `contract-ready`, `lovable-ui-task`, `bff-gap`, and `ui-done` flow
  - use GitHub `repository_dispatch` for cross-repo machine triggers and `workflow_dispatch` for replay or manual recovery
  - treat `front-ai-trading-system` as `Pantheon Console`, not as a single admin page
- Delivery order:
  1. closed-loop infra and trigger contract
  2. packetize the APP-002-backed screens
  3. expand into the 8-workbench backlog
  4. materialize execution tasks only after human gate approval

## Agreed Task Slices

- `LOOP-001` to `LOOP-003`: closed-loop protocol, GitHub automation target, and front-repo bootstrap.
- `PKT-001` to `PKT-005`: canonical packet families for the already-backed APP-002 slices.
- `WB-001` to `WB-008`: workbench backlog definition for Operator, Persona, Research, Knowledge, Trainer, Consultation, Governance, and Evolution.

## Open Questions / Human Gate

- Confirm whether `backend-delivery` should always carry version-lock fields even before a formal front-end SDK exists.
- Confirm whether GitHub label bootstrap is a hard prerequisite for enabling dispatch automation, or merely a compatibility requirement for the old issue bus.
- Confirm the initial wave boundary for non-APP-002 workbenches whose backend support is still blueprint-only.

## Acceptance Note

- Draft packet only. Multi-lane readouts and cross-review are still pending, so this session remains in active discussion planning.
