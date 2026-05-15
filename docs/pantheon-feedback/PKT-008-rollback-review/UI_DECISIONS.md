# PKT-008 Governance Rollback Review UI Decisions

- The screen is implemented as a dedicated governance route at `/governance-rollback-review`, with `rollback_id` carried in the query string for deep-linkable review.
- All production data comes through the shared `operatorApi` BFF client; no component-level raw network call was added.
- The position impact table consumes only the backend-supplied `position_impact[]` array. The UI does not derive impact from raw bindings or telemetry state.
- The affected bindings panel consumes only the backend-supplied `affected_bindings[]` array.
- Approval and rejection are the only commands issued directly from this page and use the published `ApproveRollback` and `RejectRollback` command envelopes.
- CTA visibility is backend-shaped through `allowedActions`; if those fields are missing, the screen surfaces a contract-gap state instead of a mocked fallback.
- The Approve CTA is disabled whenever `meta.surfaces.position_data` is `degraded` or `unavailable`, even if `allowedActions.canApproveRollback` is `true`.
- Any degraded or unavailable surface triggers a non-dismissable alert at the top of the page, while preserving read-only access to rollback scope, affected bindings, and position impact rows.
- The trigger evidence drawer renders only `trigger_evidence` content from the rollback review payload.
