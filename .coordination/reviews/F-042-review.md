# F-042 Promotion Review Packet

## Date

2026-04-24

## Reviewer

Codex

## Findings

### 1. Blocking: the published Promotion Review route does not supply the `plan_id` required by the F-042 read contract

- `git show 5444be87c1eb52d9a622d3ff521d66ebf5631b43:src/pages/promotion/PromotionReview.tsx`
  lines `109-111` read only `?plan=<deployment_plan_id>` from the query string.
- `git show 5444be87c1eb52d9a622d3ff521d66ebf5631b43:src/App.tsx`
  lines `209-211` mount `/governance/promotion/:artifact_id` and
  `/promotion-review`, but no mounted route provides the required deployment
  plan identifier.
- Impact: the canonical screen path cannot reliably drive
  `GET /api/v1/operator/deployment-review/{plan_id}` without a manual query
  parameter, so the current wiring is not contract-complete.

### 2. Blocking: the current F-042 types and render path regress the published contract and can raise false contract-gap errors on valid payloads

- `git show 5444be87c1eb52d9a622d3ff521d66ebf5631b43:src/pages/promotion/types.ts`
  lines `45-59` make `latestRun.progress` non-nullable, make
  `review.decisionState`, `review.decidedAt`, and `review.reviewer` required,
  and regress `SurfaceStatus.status` to `ok | degraded | error` instead of
  `ok | degraded | unavailable`.
- `git show 5444be87c1eb52d9a622d3ff521d66ebf5631b43:src/pages/promotion/PromotionReview.tsx`
  lines `94-95` treat `latestRun.progress` as required numeric data, which can
  turn a valid `null` progress into a false `bff-gap` state.
- The same screen uses `review.decisionState`, `review.reviewer`, and
  `review.decidedAt` as rendered summary fields at lines `353-359`, even
  though the current packet treats them as optional echoes and requires the UI
  to rely on canonical `approval_decision.state`, `approval_decision.reviewer`,
  and `approval_decision.decided_at`.
- Impact: a valid F-042 payload can be rejected or mis-rendered even though no
  Pantheon contract gap exists.

### 3. Blocking: the shared Bearer-auth bridge expected by `bffClient` is no longer wired by `AuthProvider`

- `git show 5444be87c1eb52d9a622d3ff521d66ebf5631b43:src/lib/bffClient.ts`
  lines `158-159` and `217-255` still read `pantheon_operator_token` from
  `localStorage` and attach `Authorization: Bearer <token>` for GET and POST
  requests.
- `git show 5444be87c1eb52d9a622d3ff521d66ebf5631b43:src/auth/AuthProvider.tsx`
  lines `70-118` no longer persist or clear `pantheon_operator_token` during
  session refresh, sign-in, auth-state changes, or sign-out.
- Impact: the F-042 screen still targets the intended BFF surfaces, but the
  documented auth bridge is no longer reliable for stateful Pantheon requests.

### 4. Medium: the GitHub-visible request pair and feedback bundle still describe an older accepted cycle, not the current reviewed `main`

- `git show 5444be87c1eb52d9a622d3ff521d66ebf5631b43:.coordination/requests/F-042-ui-done.yaml`
  still advertises `source_commit:
  03833940782906b7f28cdb3867352793a1ad7d6c`.
- `git show 5444be87c1eb52d9a622d3ff521d66ebf5631b43:.coordination/requests/F-042-frontend-feedback.yaml`
  still advertises `source_commit:
  c34048e2e096d3fe9bde1c216c0613535d71f07d`.
- The latest published front `main` is
  `5444be87c1eb52d9a622d3ff521d66ebf5631b43`, so the coordination pair no
  longer truthfully identifies the current reviewed screen state.
- The published feedback bundle still claims "accepted for review handoff" and
  `status: "no_open_gaps"` against the older follow-up commit:
  - `docs/pantheon-feedback/F-042/LOVABLE_CHANGE_FEEDBACK.md:3-9,22-29`
  - `docs/pantheon-feedback/F-042/API_GAP_REQUESTS.json`
- Impact: the GitHub-visible coordination artifacts understate the remaining
  front-owned work and cannot be treated as a truthful closeout packet.

## Confirmed Positives

- `operatorApi.getDeploymentReview()` still points at
  `GET /api/v1/operator/deployment-review/{plan_id}` in
  `src/lib/bffClient.ts:906-911`.
- `operatorApi.sendCommand()` still points at
  `POST /api/v1/operator/commands` in `src/lib/bffClient.ts:1113-1128`.
- `PromotionReview.tsx` does not introduce raw component-level `fetch()` or
  `axios` calls.
- CTA visibility still keys off `allowedActions.canPromoteToPaper`, so the
  screen does not derive promotion authority from plan stage or runtime status.
- The published front `main`
  `5444be87c1eb52d9a622d3ff521d66ebf5631b43` does contain the canonical F-042
  request pair and `docs/pantheon-feedback/F-042/*`; the remaining transport
  issue is stale `source_commit` truth, not missing files.

## Reviewed Artifacts

- Pantheon coordination and contract files:
  - `docs/bff/F-042-promotion-review.md`
  - `docs/examples/F-042-review-page.json`
  - `.coordination/responses/F-042-contract-ready.yaml`
  - `.coordination/responses/F-042-lovable-ui-task.yaml`
  - `.coordination/responses/F-042-backend-delivery.yaml`
- Front repo GitHub `main` @ `5444be87c1eb52d9a622d3ff521d66ebf5631b43`:
  - `.coordination/requests/F-042-ui-done.yaml`
  - `.coordination/requests/F-042-frontend-feedback.yaml`
  - `docs/pantheon-feedback/F-042/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/F-042/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/F-042/UI_DECISIONS.md`
  - `docs/pantheon-feedback/F-042/QA_STATUS.md`
  - `src/pages/promotion/PromotionReview.tsx`
  - `src/pages/promotion/types.ts`
  - `src/lib/bffClient.ts`
  - `src/auth/AuthProvider.tsx`
  - `src/App.tsx`
- Dispatch surface checked for this review:
  - `/tmp/front-origin-main-verify/.coordination/requests/F-042-ui-done.yaml`

## Decision

`F-042` is **follow-up-required**.

No new Pantheon endpoint, BFF aggregation, or contract expansion is needed.
The remaining work is front-owned on the existing contract: fix route-to-plan
wiring, restore the shared auth token bridge, realign types/rendering to the
published F-042 shape, then republish the F-042 request pair and feedback
bundle from that corrected GitHub-visible commit.
