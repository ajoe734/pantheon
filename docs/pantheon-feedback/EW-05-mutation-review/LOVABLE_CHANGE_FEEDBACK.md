# EW-05 Mutation Review — Lovable Change Feedback

Reviewed the `front-ai-trading-system` EW-05 mutation-review implementation
against the active Pantheon handoff bundle and the current re-review findings.

## Outcome

Pantheon review target: closeout-ready.

The Mutation Review screen is implemented against the live Pantheon EW-05
surfaces:

- `GET /api/v1/operator/mutation-review/{decision_id}`
- `POST /api/v1/operator/commands` with `ApproveMutation`
- `POST /api/v1/operator/commands` with `RejectMutation`

This return closes the remaining Codex re-review findings:

- the screen now fails closed when required nested fields are absent inside
  `proposed_changes.change_details[]`, `risk_assessment.threshold_triggers[]`,
  `required_approvals[]`, `review_chain[]`, `evidence_refs[]`, or a non-null
  `rollback_followthrough` object, so a malformed but partially present
  payload no longer leaks partial rows into the operator UI
- a live `503 evidence_unavailable` response now renders the explicit
  unavailable-state placeholder instead of the generic load-error branch
- linked postmortems now resolve through
  `/operator/post-incident-review?postmortem=...` all the way to the matching
  resolved incident instead of dropping the query contract after navigation

## Verified Against Pantheon

- `src/pages/evolution/MutationReview.tsx` validates the full nested response
  shape before rendering and surfaces the canonical contract-gap branch when
  any required nested member is absent.
- CTA visibility still comes only from
  `allowedActions.canApproveMutation`,
  `allowedActions.canRejectMutation`, and
  `meta.surfaces.mutation_review`.
- `meta.surfaces.mutation_review = "stale"` keeps the page visible under a
  non-dismissable stale banner.
- `meta.surfaces.mutation_review = "unavailable"` suppresses both CTAs and
  replaces the panel stack with the explicit unavailable-state notice.
- a `503 evidence_unavailable` load failure now maps to that same explicit
  unavailable-state notice instead of the generic fatal-error branch.
- Approve and Reject continue to submit the published operator-command payloads
  and re-fetch the read route after success instead of mutating local decision
  state optimistically.
- Approval-decision, incident, and postmortem deep links remain aligned to the
  mounted app routes introduced in the earlier EW-05 implementation commit, and
  the post-incident-review destination now keeps the `postmortem` query active
  until it resolves the linked incident detail.

## No BFF Gaps Required

No new Pantheon API gap is requested in this cycle. The remaining issues from
review were front-end routing and degradation handling, not a backend contract
mismatch.

## Pantheon Follow-up

- Re-review the current EW-05 front return using the paired `ui-done` and
  `frontend-feedback` payloads from the same publication commit.
- If no further findings remain, advance the Pantheon execution task to
  `review_approved` and final closeout.
