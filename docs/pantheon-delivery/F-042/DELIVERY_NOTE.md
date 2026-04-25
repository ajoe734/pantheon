# F-042 Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon re-reviewed the dispatched F-042 cycle-2 `ui-done` and
`frontend-feedback` mirrors against the published Promotion Review contract and
the GitHub-visible state of `ajoe734/front-ai-trading-system`.

No new Pantheon endpoint, runtime layer, or contract change is required for
this cycle. The returned request pair claims the front repo now includes:

- `pantheon_operator_token` persistence in `src/auth/AuthProvider.tsx`
- Bearer auth propagation in `src/lib/bffClient.ts`
- `detail.error.*` parsing in the shared BFF client
- `ok | degraded | unavailable` Promotion Review surface typing

However, the GitHub-visible front repo still does not publish the advertised
cycle-2 source tuple:

- both mirrored Pantheon request files now advertise
  `source_commit: 79dc1b5ecef69125fb51f1519893c2e87020864d`
- GitHub returns `No commit found for SHA: 79dc1b5ecef69125fb51f1519893c2e87020864d`
  for repo `ajoe734/front-ai-trading-system`
- GitHub `main` still serves the older canonical request pair anchored to
  `03833940782906b7f28cdb3867352793a1ad7d6c` and
  `c34048e2e096d3fe9bde1c216c0613535d71f07d`

Because of that transport gap, Pantheon cannot yet close F-042 as
`loop-complete`.

## Verified Pantheon Contract

- Read route remains `GET /api/v1/operator/deployment-review/{plan_id}`
- Write route remains `POST /api/v1/operator/commands`
- Canonical error envelope remains `detail.error.*`
- Canonical surface-status typing remains `ok | degraded | unavailable`
- No new endpoint, no shadow state, and no Pantheon-side contract expansion is
  authorized from this review

## Findings Requiring Another Front-Owned Publish

### 1. GitHub does not yet publish the advertised cycle-2 source commit

Evidence:

- Pantheon-mirrored requests:
  - `.coordination/requests/F-042-ui-done.yaml`
  - `.coordination/requests/F-042-frontend-feedback.yaml`
- GitHub commit lookup:
  - `ajoe734/front-ai-trading-system@79dc1b5ecef69125fb51f1519893c2e87020864d`
  - Result: `No commit found for SHA`
- GitHub `main` still serves:
  - `.coordination/requests/F-042-ui-done.yaml` with
    `source_commit: 03833940782906b7f28cdb3867352793a1ad7d6c`
  - `.coordination/requests/F-042-frontend-feedback.yaml` with
    `source_commit: c34048e2e096d3fe9bde1c216c0613535d71f07d`

Impact:

- supervisor/manual replay still cannot reconstruct a GitHub-visible F-042
  cycle from the current `payload_path + source_commit` tuple
- Pantheon cannot truthfully mark the F-042 loop closed

### 2. No Pantheon contract or runtime follow-up is open from this cycle

Evidence:

- the canonical F-042 packet remains unchanged:
  - `docs/bff/F-042-promotion-review.md`
  - `docs/examples/F-042-review-page.json`
  - `docs/screens/F-042-promotion-review.md`
  - `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md`
- `.coordination/requests/F-042-needs-runtime.yaml` is already `status: resolved`
- the returned request pair claims the remaining cycle-2 work is front-side
  auth/session, error-envelope, and surface-typing publication rather than a
  new Pantheon BFF change

Impact:

- the next step is front-owned GitHub publication hygiene, not a Pantheon
  backend implementation pass

## Pantheon-Side Outcome

- Pantheon contract: unchanged
- Pantheon endpoints: unchanged
- Pantheon review anchor:
  - `.coordination/requests/F-042-ui-done.yaml`
  - `.coordination/requests/F-042-frontend-feedback.yaml`
- Pantheon will not rewrite `docs/pantheon-feedback/F-042/` from inference,
  because the front repo remains canonical for that bundle
- Next action: the front repo must push a GitHub-visible commit that contains:
  - `.coordination/requests/F-042-ui-done.yaml`
  - `.coordination/requests/F-042-frontend-feedback.yaml`
  - `docs/pantheon-feedback/F-042/`
  - the integrated Promotion Review files cited by the returned cycle
  - `source_commit` fields in both request bodies that point at that actual
    GitHub-visible publication commit

## Verification Performed

- Reviewed the current Pantheon coordination requests:
  - `.coordination/requests/F-042-ui-done.yaml`
  - `.coordination/requests/F-042-frontend-feedback.yaml`
- Reviewed the canonical Pantheon contract sources:
  - `docs/bff/F-042-promotion-review.md`
  - `docs/examples/F-042-review-page.json`
  - `docs/screens/F-042-promotion-review.md`
  - `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md`
- Queried the GitHub-visible front repo state for:
  - commit `79dc1b5ecef69125fb51f1519893c2e87020864d`
  - `.coordination/requests/F-042-ui-done.yaml` on `main`
  - `.coordination/requests/F-042-frontend-feedback.yaml` on `main`
- Confirmed that the local sibling checkout is stale relative to the mirrored
  Pantheon request pair and did not use it as the source of truth for this
  closeout

## Not Completed

- No new Pantheon BFF code change was needed or implemented in this cycle.
- No live browser QA against a running Pantheon BFF instance was performed in
  this review step.
- Pantheon cannot yet mark F-042 `loop-complete` until the front repo publishes
  the claimed cycle-2 source tuple to GitHub.
