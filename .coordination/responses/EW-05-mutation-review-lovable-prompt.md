Patch the existing `EW-05-mutation-review` UI flow in `front-ai-trading-system`.
Do not rebuild the screen from scratch. Close the remaining Pantheon re-review
findings and republish the return truthfully.

Required code fixes before republishing:

- `src/pages/evolution/MutationReview.tsx`
  - render the live
    `GET /api/v1/operator/mutation-review/{decision_id}` `503 { error:
    "evidence_unavailable" }` path as the explicit unavailable-state
    placeholder instead of the generic load-error branch
  - keep both mutation CTAs suppressed on that unavailable path
  - fail closed when required nested fields are missing inside
    `proposed_changes.change_details[]`,
    `risk_assessment.threshold_triggers[]`,
    `required_approvals[]`,
    `review_chain[]`,
    `evidence_refs[]`, or a non-null `rollback_followthrough`
- `src/lib/bffClient.ts`
  - preserve enough shared BFF error detail for the page to distinguish the
    route's `evidence_unavailable` response without introducing raw fetch logic
    in the component, if additional envelope data is needed

Already-correct behavior to keep unchanged:

- `src/App.tsx` mounts `/evolution/mutation-review/:decision_id`
- `src/App.tsx` mounts `/personas/approval-decisions` and
  `/personas/approval-decisions/:decisionId`
- `linked_postmortem_id` deep-links through
  `/operator/post-incident-review?postmortem=...`, and the destination screen
  resolves that query param before loading detail
- command submission stays on `POST /api/v1/operator/commands`
- the page re-fetches the mutation-review projection after command acceptance
- no raw `fetch()` calls in page or component files

Publication requirement:

- publish the required EW-05 feedback bundle under
  `docs/pantheon-feedback/EW-05-mutation-review/`
- republish `.coordination/requests/EW-05-mutation-review-ui-done.yaml`
  after the unavailable-state and nested-validation fixes land so the return is
  truthful for the reviewed cycle
- if the next return cycle uses the current closed-loop feedback format, pair
  the refreshed ui-done with
  `.coordination/requests/EW-05-mutation-review-frontend-feedback.yaml`

If backend fields are missing or the live payload diverges from the synced
contract, stop implementation and write
`.coordination/requests/EW-05-mutation-review-bff-gap.yaml` using
`.coordination/requests/EW-05-mutation-review-bff-gap.example.yaml` as the
template. Then sync that file back to GitHub through the normal Lovable flow so
Pantheon supervisor can continue the loop.

Screen: `mutation-review`.
Workbench: `evolution-workbench`.
Screen ID: `screen-evolution-mutation-review`.

Allowed endpoints:
- GET /api/v1/operator/mutation-review/{decision_id}
- POST /api/v1/operator/commands (ApproveMutation)
- POST /api/v1/operator/commands (RejectMutation)

Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking

Completion handoff:
- when the fixes are ready, refresh the EW-05 return artifacts, then stop so
  Pantheon supervisor can pick up the next review automatically

References:
- docs/screens/EW-05-mutation-review.md
- docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md
- docs/bff/EW-05-mutation-review.md
- docs/examples/EW-05-mutation-review.json
- .coordination/responses/EW-05-mutation-review-lovable-ui-task.yaml
- .coordination/responses/EW-05-mutation-review-frontend-feedback.yaml
