# PKT Knowledge Workbench Overview Frontend Change Spec

## Scope

Implement the Knowledge Workbench landing page from one overview route.

## Allowed Endpoint

- `GET /api/v1/workbench/knowledge`

## Constraints

- Use only the published overview payload.
- Do not invent registry tables, evidence-browser joins, or strategy-spec compare logic in this packet.
- Do not infer readiness from schemas or storage hints; render backend-owned module status and missing-contract lists as supplied.
- If any required field is missing, emit a `bff-gap` handoff instead of inventing workbench browse state locally.

## Acceptance

- The page renders the workbench header, module order, support refs, and next steps from the single route.
- The module pipeline stays backend-owned.
- The page makes it obvious that this is an overview packet, not a full Knowledge Workbench implementation.
