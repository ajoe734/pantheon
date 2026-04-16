# PKT-002 Incident Detail — UI Decisions

- Built the Incident Detail composed view as `src/pages/operator/IncidentDetail.tsx`. The page receives `incident_id` from the route parameter and fetches the full composed view in a single call through the BFF client. No client-side surface joins.
- Added `src/components/operator/AffectedBindings.tsx` as a dedicated sub-component to keep the affected bindings surface state logic (ok/degraded/empty variants) isolated from the page host.
- Added `src/components/operator/KillSwitchStatusPanel.tsx` to encapsulate the three kill switch surface states (`ok`, `degraded`, `unavailable`) and the staleness note rendering.
- Added `src/components/operator/ActionEntryStrip.tsx` for the read-only action authority summary. CTA eligibility is read exclusively from the `allowedActions` block — no local derivation.
- `meta.surfaces` values are read as objects (`meta.surfaces.incident.status`) to match the actual example payload shape in `docs/examples/PKT-002-incident-detail.json`.
- Treated any absent `meta.surfaces` key as a contract problem and rendered an explicit `bff-gap` alert state instead of guessing surface health.
- The **Open Action Drawer** CTA mounts the reusable `IncidentActionDrawer` component already delivered in the PKT-002 action-drawer cycle. No logic is duplicated.
- Degraded affected-bindings state renders available partial records followed by the named degradation notice string from `meta.degradation.affected_bindings_reason`. The empty-success copy ("No affected bindings recorded") is gated explicitly on `meta.surfaces.affected_bindings = ok` — degraded reads never produce the empty-success message.
