# PKT-001 Deployment Review UI Decisions

- The console is routed at `/deployment-review` and keeps `status` plus `plan` in the query string so the list and detail panels can deep-link without inventing alternate state.
- The list panel reads only through `operatorApi.listDeploymentPlans()`; filters are passed to Pantheon as the documented `status` query param.
- The detail panel reads only through `operatorApi.getDeploymentReview(planId)` and submits writes only through `operatorApi.sendCommand()`.
- If runtime live updates are enabled, they subscribe only through the shared `PKT-005` `SseClient` and never replace the PKT-001 snapshot payload as the source of truth.
- CTA visibility is driven exclusively by `allowedActions.canApprove`, `allowedActions.canReject`, and `allowedActions.canPromoteToPaper`.
- When any detail `meta.surfaces` entry is degraded or unavailable, all visible CTAs are disabled and the current payload remains visible in read-only form.
- The command dialog requires `audit_context.reason` for every action and requires `verification_notes` when rejecting.
- The reject and promote flows both use the published `ApproveDeployment` command contract; the UI does not invent a screen-local promotion endpoint.
- Missing required list or detail fields are treated as an explicit contract-gap state instead of silently omitting controls or backfilling client-derived values.
- The detail reader accepts both the packet-local flat envelope and the existing `F-042` `data/meta` envelope for the shared deployment-review route because both are already published against the same BFF path.
