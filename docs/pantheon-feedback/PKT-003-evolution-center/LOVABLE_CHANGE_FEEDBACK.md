# PKT-003 Evolution Center — Lovable Change Feedback

Reviewed the Evolution Center implementation in `ajoe734/front-ai-trading-system` at commit `faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7` against the PKT-003 BFF contract, screen spec, and example payload.

## Outcome

Pantheon review result: ready for reviewer handoff.

The Evolution Center screen is implemented against the published PKT-003 contract and example payload. All four allowed BFF endpoints are consumed through the existing BFF client. All acceptance criteria are met.

> **BFF-gap note:** A `bff-gap` handoff was filed earlier (`.coordination/requests/PKT-003-evolution-center-bff-gap.yaml`) because the BFF returned divergent response shapes across all four endpoints — fifteen structural mismatches were identified including missing `items` envelope (all four endpoints returned top-level `data` arrays), absent `page_info.next_page_token`, field name mismatches (`id` vs `freeze_order_id`, `id` vs `rollback_id`, `created_at` vs `issued_at`, `initiated_at` vs `executed_at`), missing `updated_at` and `notes` in the decision detail projector, missing `meta.snapshot_at` (all four endpoints returned `meta.staleness` dict instead), and the decision detail endpoint wrapping its response under a `data` key rather than returning the object at the root. All gaps were resolved by BP5-SVC-012 (EvolutionDecision service and governance read path), BP5-SVC-013 (operational evolution orchestration and kill-switch fast path), and BP5-SVC-015 (BFF snapshot and default fallback removal). Implementation proceeded against the corrected BFF response shapes.

## Verified Against Pantheon

- All four endpoints are consumed through the shared BFF client. No raw `fetch` or `axios` calls added in component files.
- The **Evolution Decisions panel** renders from `GET /api/v1/evolution-decisions`. Each row shows `id`, `action_type`, `risk_level`, `status`, `incident_ref`, and `artifact_id`. Query parameters `action_type`, `risk_level`, and `status` are passed through to the BFF — no client-side filtering.
- **Pagination** is driven by `page_info.next_page_token`. When `next_page_token` is null, the load-more control is hidden.
- The **Evolution Decision Detail drawer** opens on row selection and fetches `GET /api/v1/evolution-decisions/{decision_id}`. It renders all EV-02 fields: `id`, `action_type`, `risk_level`, `status`, `incident_ref`, `artifact_id`, `created_at`, `updated_at`, `notes`. 404 on `{decision_id}` renders "Evolution decision not found" with the ID.
- The **Freeze Orders panel** renders from `GET /api/v1/freeze-orders`. Each row shows `freeze_order_id`, `status`, `scope`, and `issued_at`. Active and lifted orders are both included (no status filter applied by default).
- The **Rollbacks panel** renders from `GET /api/v1/rollbacks`. Each row shows `rollback_id`, `action_type`, `runtime_id`, and `executed_at`. `time_range` is not exposed as a UI filter control.
- The **Staleness / degradation banner** is non-dismissable and renders when any panel's `meta.snapshot_at` is stale or `BFF_READ_SURFACE_STATE != fresh`. It identifies the specific affected panel(s).
- The **Permission required** state (not a data-loading error) renders when a viewer-role token is rejected at the BFF.
- Any absent required field in a list response emits a `bff-gap` alert state — no silent mock fallback.
- Loading, empty, and error states are explicit and visually distinct across all four panels. Panels fetch independently and do not block each other.
- No demo providers are imported.
- Local verification passed in the checked-out frontend workspace: `npm run build` and targeted `npx eslint src/pages/evolution/Center.tsx src/pages/evolution/EvolutionCenter.tsx src/pages/evolution/EvolutionDecisionDetail.tsx src/pages/evolution/types.ts src/lib/bffClient.ts`.

## Notes

- Four panels fetch independently on mount so a slow or degraded endpoint does not block the entire screen from rendering.
- `time_range` is intentionally absent from the Rollbacks panel filter UI — it is accepted by the BFF but is a no-op in the v1 store.
- The Detail drawer does not expose any write actions — evolution decision mutations belong to the Mutation Review screen (`PKT-003-mutation-review`), which is blocked pending EVO-004.
- All BFF-gap fields cited in the prior `bff-gap` handoff are resolved in the current BFF response shape. No new contract shape gaps were found in this implementation pass.
- The legacy `/evolution` route was preserved by making `src/pages/evolution/Center.tsx` a thin wrapper around the new PKT-003 screen.

## Pantheon Follow-up

- No Pantheon API gap is requested in this cycle.
- The next Pantheon-owned step is reviewer confirmation, then any follow-up routing work once the Evolution Workbench shell is finalized.
