Build the `PKT-005-degradation-banner` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-005-degradation-banner-bff-gap.yaml` using `.coordination/requests/PKT-005-degradation-banner-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `global-degradation-banner`.
Workbench: `operator-console`.
Screen ID: `surface-operator-global-degradation-banner`.
Constraints:
- use existing bff client only; do not add a dedicated health-check fetch
- banner state must come exclusively from meta.staleness and meta.surfaces in the current screen's composed view response
- do not add raw fetch in components
- do not import demo providers
- if any meta.surfaces key is absent from the BFF response, emit a bff-gap handoff instead of assuming ok
- all five banner variants must be implemented (none, degraded, stale, partial, critical)
- banner is non-dismissable
Acceptance:
- GlobalDegradationBanner component is implemented as a shared primitive
- banner renders correctly in all five variants based on meta.surfaces content
- banner is wired into Deployment Review (PKT-001), Incident Home and Incident Response (PKT-002), and Post-Incident Review (PKT-003)
- no dedicated health-check fetch is added
- banner disappears when all surfaces return to ok
- STALE variant shows humanised age from meta.staleness.last_known_at
- PARTIAL variant lists each surface by humanised name and status
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-005-degradation-banner-ui-done.yaml` using `.coordination/requests/PKT-005-degradation-banner-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-005-degradation-banner.md
- docs/pantheon-handoffs/PKT-005-degradation-banner/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-005-degradation-banner.md
- docs/pantheon-handoffs/PKT-005-degradation-banner
- docs/examples/PKT-005-degradation-banner.json
