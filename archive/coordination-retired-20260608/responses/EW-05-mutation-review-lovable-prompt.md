Build the `EW-05-mutation-review` UI flow in `front-ai-trading-system` using only Pantheon APIs.
Pantheon has already published the contract-ready handoff for this feature.
If backend fields are missing or the live payload diverges from the synced contract, stop implementation and write `.coordination/requests/EW-05-mutation-review-bff-gap.yaml` using `.coordination/requests/EW-05-mutation-review-bff-gap.example.yaml` as the template. Then sync that file back to GitHub through the normal Lovable flow so Pantheon supervisor can continue the loop.
Screen: `mutation-review`.
Workbench: `evolution-workbench`.
Screen ID: `screen-evolution-mutation-review`.
Allowed endpoints:
- GET /api/v1/operator/mutation-review/{decision_id}
- POST /api/v1/operator/commands (ApproveMutation)
- POST /api/v1/operator/commands (RejectMutation)
Published Pantheon dependencies:
- .coordination/responses/EW-05-mutation-review-contract-ready.yaml
Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
Acceptance:
- fetch decision projection from GET /api/v1/operator/mutation-review/{decision_id} only
- render Approve CTA only when allowedActions.canApproveMutation is true and meta.surfaces.mutation_review is not unavailable
- render Reject CTA only when allowedActions.canRejectMutation is true and meta.surfaces.mutation_review is not unavailable
- submit ApproveMutation or RejectMutation via POST /api/v1/operator/commands
- suppress both CTAs when meta.surfaces.mutation_review is unavailable
- render non-dismissable staleness banner when meta.surfaces.mutation_review is stale
- do not compute authority signals, risk level, or change semantics client-side
- re-fetch read route after command submission to confirm state; do not optimistically update decision_state
Required feedback bundle:
- docs/pantheon-feedback/EW-05-mutation-review/LOVABLE_CHANGE_FEEDBACK.md
- docs/pantheon-feedback/EW-05-mutation-review/API_GAP_REQUESTS.json
- docs/pantheon-feedback/EW-05-mutation-review/UI_DECISIONS.md
- docs/pantheon-feedback/EW-05-mutation-review/QA_STATUS.md
Completion handoff:
- When the UI implementation is ready, write `.coordination/requests/EW-05-mutation-review-ui-done.yaml` using `.coordination/requests/EW-05-mutation-review-ui-done.example.yaml` as the template. This handoff alone is not enough to close the loop.
Feedback return:
- After the UI handoff, write `.coordination/requests/EW-05-mutation-review-frontend-feedback.yaml` using `.coordination/requests/EW-05-mutation-review-frontend-feedback.example.yaml` as the template. Use the same Git-visible `source_commit` as the reviewed UI slice, include the refreshed feedback bundle paths, sync the files back to GitHub, and stop.
- Pantheon supervisor polls the coordination and GitHub-visible return loop on a fixed cadence; once both `ui-done` and `frontend-feedback` land, supervisor will decide closeout vs. another follow-up cycle automatically.
References:
- docs/screens/EW-05-mutation-review.md
- docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md
- docs/bff/EW-05-mutation-review.md
- docs/pantheon-handoffs/EW-05-mutation-review
- docs/examples/EW-05-mutation-review.json
