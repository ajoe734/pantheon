Build the `PKT-005-degradation-banner` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/PKT-005-degradation-banner-bff-gap.yaml` using `.coordination/requests/PKT-005-degradation-banner-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `global-degradation-banner`.
Workbench: `operator-console`.
Screen ID: `surface-operator-global-degradation-banner`.
Published Pantheon dependencies:
- .coordination/responses/PKT-005-degradation-banner-contract-ready.yaml
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
Required feedback bundle:
- docs/pantheon-feedback/PKT-005-degradation-banner/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/PKT-005-degradation-banner/API_GAP_REQUESTS.json
- docs/pantheon-feedback/PKT-005-degradation-banner/UI_DECISIONS.md
- docs/pantheon-feedback/PKT-005-degradation-banner/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/PKT-005-degradation-banner-ui-done.yaml` using `.coordination/requests/PKT-005-degradation-banner-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/PKT-005-degradation-banner-frontend-feedback.yaml` using `.coordination/requests/PKT-005-degradation-banner-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/PKT-005-degradation-banner.md
- docs/pantheon-handoffs/PKT-005-degradation-banner/FRONTEND_CHANGE_SPEC.md
- docs/bff/PKT-005-degradation-banner.md
- docs/pantheon-handoffs/PKT-005-degradation-banner
- docs/examples/PKT-005-degradation-banner.json
