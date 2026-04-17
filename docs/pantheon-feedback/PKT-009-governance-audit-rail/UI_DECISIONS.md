# PKT-009 Governance Audit Rail UI Decisions

- The screen is implemented as a dedicated governance route at `/governance-audit-rail`, with row selection deep-linked through the `entry` query parameter.
- All production data comes through the shared `operatorApi` BFF client; no component-level raw network call was added.
- Actor, action-type, target-type, and date-range filters are server-backed only. The UI never filters or sorts the returned entries locally.
- Action-type filters are represented as checkbox selections in the rail, then serialized to the documented comma-separated `action_type` query parameter before fetch.
- The audit list renders Pantheon-supplied labels exactly as returned. No client-side mapping layer for actor names, action labels, or evidence labels was added.
- The detail drawer is sourced from the already-fetched list payload instead of issuing a second read route or synthesizing detail data in the browser.
- `meta.surfaces.audit_trail` uses a dedicated delayed-data banner for `degraded` and an unavailable-data replacement state for `unavailable`, matching the packet semantics.
- Non-audit degraded or unavailable surfaces are routed through the shared global degradation banner so the page stays aligned with the PKT-005 degradation substrate.
- Missing required response fields are treated as a contract-gap state that directs the operator back to the canonical `bff-gap` handoff path instead of rendering a partial fallback.
