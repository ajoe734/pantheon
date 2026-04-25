# PKT-001 Governance Review Queue UI Decisions

- The screen is routed at `/governance-review-queue` and linked from the existing sidebar instead of inventing a second governance navigation surface.
- Filter state is kept in URL search params using the BFF field names (`item_type`, `risk_level`, `status`) so the UI and server query stay aligned.
- The detail drawer renders only from the queue item already returned by the list response; it does not issue a second item-detail fetch.
- Routing actions stay strictly backend-authoritative: the drawer only renders each form when the corresponding `allowedActions.*` flag is `true`.
- Degraded or unavailable surfaces keep the queue visible in read-only mode while disabling all routing submissions in both the page and the drawer.
- Missing required queue fields are treated as a contract problem and surfaced as an explicit error state instead of silently dropping fields or synthesizing fallback values.
- Evidence refs are rendered using the canonical example-payload object shape (`ref_id`, `type`, `url`) because that is the published Pantheon contract truth for PKT-001.
