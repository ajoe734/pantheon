Build the `PKT-005-degradation-banner` UI flow in `front-ai-trading-system` using only Pantheon APIs.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-005-degradation-banner-bff-gap.yaml` using `.coordination/requests/PKT-005-degradation-banner-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `global-degradation-banner`.
Workbench: `operator-console`.
Screen ID: `surface-operator-global-degradation-banner`.
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- implement the shared GlobalDegradationBanner component driven by meta.staleness and meta.surfaces
- do not add a separate BFF health-check fetch to render the banner
- banner state must be derived from the current screen's composed view response only
- render all five banner variants (none, degraded, stale, partial, critical)
- banner disappears automatically when all meta.surfaces entries return to ok
- wire the banner into all three existing Operator Console screens (PKT-001, PKT-002, PKT-003)
- if any meta.surfaces key is absent from the BFF response, emit a bff-gap handoff
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-005-degradation-banner-ui-done.yaml` using `.coordination/requests/PKT-005-degradation-banner-ui-done.example.yaml` as the template. Sync that file back to GitHub and stop so Pantheon supervisor can pick up review/integration work automatically.
References:
- docs/screens/PKT-005-degradation-banner.md
- docs/pantheon-handoffs/PKT-005-degradation-banner/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-005-degradation-banner.md
- docs/pantheon-handoffs/PKT-005-degradation-banner
- docs/examples/PKT-005-degradation-banner.json
