Patch the existing `F-042` Promotion Review flow in `front-ai-trading-system`.
Do not rebuild the screen from scratch. Close the remaining Pantheon re-review
findings and republish the handoff from one truthful Git-visible commit.

Required code fixes before republishing:

- `src/pages/promotion/types.ts`
  - keep `SurfaceStatus` aligned to `ok | degraded | unavailable`
  - type `latestRun.progress` as `number | null`
  - keep `review.decisionState`, `review.decidedAt`, and `review.reviewer`
    optional
- `src/pages/promotion/PromotionReview.tsx`
  - accept `latestRun.progress = null` without raising a contract-gap error
  - render an explicit unavailable message when progress is null
  - render decision metadata from `approval_decision.state`,
    `approval_decision.reviewer`, and `approval_decision.decided_at`
  - do not require optional `review.*` mirrors when the canonical
    `approval_decision.*` fields are present

Already-correct behavior to keep unchanged:

- `src/lib/bffClient.ts` sends `Authorization: Bearer <token>` on Pantheon BFF
  requests
- `src/lib/bffClient.ts` parses the Pantheon `detail.error.*` envelope
- no raw `fetch()` calls in page or component files
- CTA visibility remains backend-shaped from `allowedActions`

Publication requirement:

- publish `.coordination/requests/F-042-ui-done.yaml`,
  `.coordination/requests/F-042-frontend-feedback.yaml`, and
  `docs/pantheon-feedback/F-042/*` from the same final front-repo commit that
  contains the `PromotionReview.tsx` and `types.ts` fixes
- both request payloads must set `source_commit` to that exact publication
  commit; do not point at an earlier code-only commit or an unrelated metadata
  commit
- if the feedback bundle still says "replay-clean", it must be truthful for the
  advertised `source_commit`

If backend fields are missing or the live payload diverges from the synced
contract, stop implementation and write `.coordination/requests/F-042-bff-gap.yaml`
using `.coordination/requests/F-042-bff-gap.example.yaml` as the template. Then
sync that file back to GitHub through the normal Lovable flow so Pantheon
supervisor can continue the loop.

Screen: `promotion-review`.
Workbench: `governance-review`.
Screen ID: `screen-governance-promotion-review`.

Allowed endpoints:
- GET /api/v1/operator/deployment-review/{plan_id}
- POST /api/v1/operator/commands

Constraints:
- use existing bff client only
- do not add raw fetch in components
- do not import demo providers
- if any required field is missing, emit a bff-gap handoff instead of mocking
- do not regress to `errors[]` or `error` surface status handling

Completion handoff:
- when the fix + republish is ready, update both F-042 request payloads so their
  `source_commit` matches the final front-repo publication commit exactly, then
  stop so Pantheon supervisor can pick up review/integration automatically

References:
- docs/screens/F-042-promotion-review.md
- docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md
- docs/bff/F-042-promotion-review.md
- docs/examples/F-042-review-page.json
- .coordination/responses/F-042-lovable-ui-task.yaml
- .coordination/responses/F-042-frontend-feedback.yaml
