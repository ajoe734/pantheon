# PKT-004 Module C — Deployment / Approval Drilldowns Frontend Change Spec

## Feature

- Feature ID: `PKT-004-deployment-approval-drilldowns`
- Module: `Module C — Deployment / Approval Drilldowns`
- Workbench: Persona Workbench (shared with Governance Workbench)
- Packet status: ready

## Summary

Build four read-only Deployment / Approval Drilldown surfaces in `front-ai-trading-system`: Deployment Plan List, Deployment Plan Detail, Approval Decision List, and Approval Decision Detail. These surfaces provide read-only context from persona and binding journeys and link to PKT-001 governance screens for action workflows.

**Governance write actions (approve, reject, promote) are NOT in scope for this module.** All governance commands route through the `PKT-001 Deployment Review Console` and `PKT-001 Governance Review Queue` screens.

## Files to Create or Modify

```
src/pages/persona/DeploymentPlanList.tsx        — new deployment plan list page (DP-01)
src/pages/persona/DeploymentPlanDetail.tsx      — new deployment plan detail page (DP-02, read-only)
src/pages/persona/ApprovalDecisionList.tsx      — new approval decision list page (DP-03)
src/pages/persona/ApprovalDecisionDetail.tsx    — new approval decision detail page (DP-04, read-only)
src/pages/persona/types.ts                      — add deployment/approval drilldown types
src/lib/bffClient.ts                            — add deployment/approval fetch calls
```

## API Integration

Use the existing BFF client in `src/lib/bffClient.ts`.

| Surface | Endpoint |
|---|---|
| DP-01 Deployment Plan List | `GET /api/v1/deployment-plans` |
| DP-02 Deployment Plan Detail | `GET /api/v1/deployment-plans/{plan_id}` |
| DP-03 Approval Decision List | `GET /api/v1/approval-decisions` |
| DP-04 Approval Decision Detail | `GET /api/v1/approval-decisions/{decision_id}` |

See `docs/examples/PKT-004-deployment-approval-drilldowns.json` for full example payloads.

## Component Structure

### `DeploymentPlanList.tsx` (DP-01)

- Fetches from `GET /api/v1/deployment-plans` on mount and on filter change.
- Supports `status` and `capital_pool_id` filters passed as query params.
- Renders plan rows: `id`, `artifact_id`, `artifact_version`, `target_stage`, `status`, `capital_pool_id`.
- Clicking a row navigates to `DeploymentPlanDetail`.
- Renders loading, empty, and error states.

### `DeploymentPlanDetail.tsx` (DP-02)

- Receives `plan_id` as a route param.
- Fetches from `GET /api/v1/deployment-plans/{plan_id}` on mount.
- Renders plan identity block and embedded approval decision summary (read-only).
- Renders a cross-workbench link: "View in Governance Console" → `PKT-001 Deployment Review Console` for the same `plan_id`.
- Does NOT render approve, reject, or promote CTAs — those belong to PKT-001 only.

### `ApprovalDecisionList.tsx` (DP-03)

- Fetches from `GET /api/v1/approval-decisions` on mount and on filter change.
- Supports `outcome` and `state` filters passed as query params.
- Renders decision rows: `id`, `outcome`, `state`, `reviewer`, `decided_at`, `risk_level`.
- Clicking a row navigates to `ApprovalDecisionDetail`.

### `ApprovalDecisionDetail.tsx` (DP-04)

- Receives `decision_id` as a route param.
- Fetches from `GET /api/v1/approval-decisions/{decision_id}` on mount.
- Renders decision identity block (read-only).
- Renders a cross-workbench link: "View in Governance Console" → `PKT-001 Governance Review Queue`.

## Constraints

- Use the existing BFF client only.
- Do not add raw `fetch` or `axios` in component files.
- **Do not implement approve, reject, or promote CTAs in this module.** These belong to PKT-001 screens.
- Do not import or use any demo provider or mock data layer.
- Filters must be passed as query parameters — no client-side filtering.
- If a required response field is absent, write `.coordination/requests/PKT-004-deployment-approval-drilldowns-bff-gap.yaml` and stop.

## Completion Handoff

When ready, write `.coordination/requests/PKT-004-deployment-approval-drilldowns-ui-done.yaml` using `.coordination/requests/PKT-004-deployment-approval-drilldowns-ui-done.example.yaml` as the template. Sync back to GitHub.

## References

- BFF contract: `docs/bff/PKT-004-deployment-approval-drilldowns.md`
- Screen spec: `docs/screens/PKT-004-deployment-approval-drilldowns.md`
- Example payload: `docs/examples/PKT-004-deployment-approval-drilldowns.json`
- PKT-001 governance screens: `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md`
- Contract-ready: `.coordination/responses/PKT-004-deployment-approval-drilldowns-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-004-deployment-approval-drilldowns-lovable-ui-task.yaml`
