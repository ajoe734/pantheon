# PKT-001 Governance Review Queue Lovable Change Feedback

Reviewed `ajoe734/front-ai-trading-system` at replayable publication commit `56ecdd48bb2fd422a6b1618b65906f02640c938a` against the mirrored PKT-001 contract-ready bundle and the canonical Pantheon BFF/degradation policy.

## Outcome

Pantheon review result: accepted for review handoff.

The Governance Review Queue screen is implemented against the published queue contract. The list, detail drawer, server-backed filters, and routing forms all use the existing BFF client and do not create parallel queue state in the browser.

## Verified Against Pantheon

- `GET /api/v1/operator/governance/review-queue` is consumed through `operatorApi.listGovernanceReviewQueue()` in the shared BFF client.
- `POST /api/v1/operator/commands` is sent through the existing `operatorApi.sendCommand()` client only.
- No raw `fetch()` calls were added inside the governance queue page or detail drawer components.
- Filter selections are passed through to the BFF as `item_type`, `risk_level`, and `status` query parameters; there is no client-side queue filtering.
- Queue row reviewability and all routing CTA visibility come only from `allowedActions`.
- When any `meta.surfaces` entry is `degraded` or `unavailable`, the degradation banner renders and all routing actions become read-only.
- The detail drawer renders from the embedded `review_summary` payload and does not perform a second fetch for item details.
- Loading, empty, error, contract-gap, and degraded states are explicit.

## Notes

- The route is wired at `/governance-review-queue` and linked from the main sidebar so operators can reach the screen without inventing a secondary navigation flow.
- The drawer uses the canonical evidence-ref shape from the contract example payload (`ref_id`, `type`, `url`) rather than the shorthand string-array pseudo-type in the frontend change spec.
- This review was static against the checked-in code and Pantheon contract/example payload. It did not include a live browser session against a running Pantheon BFF instance.

## Pantheon Follow-up

- No Pantheon API gap is requested in this cycle.
- The request pair is now published from the same replayable front-repo commit as this feedback bundle and the governance queue files.
- The Pantheon side can move this task into formal review using this feedback bundle.
