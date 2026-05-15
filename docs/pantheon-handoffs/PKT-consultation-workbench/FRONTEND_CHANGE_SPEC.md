# PKT Consultation Workbench Overview Frontend Change Spec

## Scope

Implement the Consultation Workbench landing page from one overview route.

## Allowed Endpoint

- `GET /api/v1/workbench/consultation`

## Constraints

- Use only the published overview payload.
- Do not add consult-request forms, committee-room state, or red-team memo panels in this packet.
- Do not derive readiness from packet-family prose; render backend-owned module status and missing-contract lists as supplied.
- If any required field is missing, emit a `bff-gap` handoff instead of inventing workbench state locally.

## Acceptance

- The page renders the workbench header, module order, support refs, and next steps from the single route.
- The module pipeline stays backend-owned.
- The page makes it obvious that this is an overview packet, not a full Consultation Workbench implementation.
