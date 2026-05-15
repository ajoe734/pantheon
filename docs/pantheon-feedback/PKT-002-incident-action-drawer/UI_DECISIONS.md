# PKT-002 UI Decisions

- Added a reusable `IncidentActionDrawer` component under `src/components/operator/` so the future Incident Detail screen can mount the same control surface without duplicating logic.
- Added a temporary standalone host route at `/incident-action-drawer` because the canonical incident-detail host is not present in this repo yet; the host only forwards URL parameters and does not invent incident state.
- Accepted both published surface envelope variants for `meta.surfaces` during validation by reading either a direct status string or an object with `status`, because the Markdown contract and example payload disagree on that detail.
- Kept primary action eligibility backend-driven by `allowedActions`, while using surface health only to decide whether the UI is in primary, fallback, or fully unavailable mode.
- Treated missing required PKT-002 fields as a contract problem and rendered an explicit `bff-gap` alert state instead of enabling or disabling actions on assumption.
