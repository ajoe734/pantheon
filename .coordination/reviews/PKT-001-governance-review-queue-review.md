# PKT-001 Governance Review Queue Review Packet

## Date

2026-04-17

## Reviewer

Codex

## Findings

### 1. Pantheon does not currently serve the published `GET /api/v1/operator/governance/review-queue` route

Pantheon's published PKT-001 contract still points the screen at:

- `GET /api/v1/operator/governance/review-queue`
- `POST /api/v1/operator/commands`

The returned UI code is statically aligned with that contract, but the current
Pantheon BFF runtime does not actually expose the read route yet:

- repo search finds the route only in PKT-001 docs and delivery artifacts, not
  in a live FastAPI route declaration under `services/control-plane/bff/`
- a direct FastAPI `TestClient(main.app)` probe of
  `/api/v1/operator/governance/review-queue` returns `404 Not Found`, both
  without auth and with operator auth

Impact:

- the UI cannot complete end-to-end acceptance against the current Pantheon BFF
- this is Pantheon-owned runtime follow-up, not a front-end request for a new
  endpoint
- no shadow state or alternate client-side endpoint is authorized

### 2. The canonical front-repo payloads in commit `56ecdd48...` still pin the older `faa1bc2...` source commit

Pantheon's local request copies now advertise:

- `source_commit: 56ecdd48bb2fd422a6b1618b65906f02640c938a`

But the canonical front-owned payloads at that same GitHub-visible commit still
contain:

- `source_commit: faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7`

Verified via:

- `git show 56ecdd48bb2fd422a6b1618b65906f02640c938a:.coordination/requests/PKT-001-governance-review-queue-ui-done.yaml`
- `git show 56ecdd48bb2fd422a6b1618b65906f02640c938a:.coordination/requests/PKT-001-governance-review-queue-frontend-feedback.yaml`

This means the handoff is still not replay-clean under the coordination spec:

- the front commit does contain the request pair, feedback bundle, and queue
  files
- but the payload bodies still point at the old pre-implementation checkpoint
- Pantheon's local mirror was transit-edited, so GitHub truth and Pantheon
  mirror truth diverge

Impact:

- replay and audit cannot rely on a single canonical `payload_path + source_commit`
  tuple
- supervisor/manual re-dispatch cannot truthfully reconstruct the reviewed UI
  cycle from GitHub-visible artifacts alone

## Reviewed Artifacts

- Pantheon request copies:
  - `.coordination/requests/PKT-001-governance-review-queue-ui-done.yaml`
  - `.coordination/requests/PKT-001-governance-review-queue-frontend-feedback.yaml`
- Front-repo canonical payload and feedback paths at commit
  `56ecdd48bb2fd422a6b1618b65906f02640c938a`:
  - `.coordination/requests/PKT-001-governance-review-queue-ui-done.yaml`
  - `.coordination/requests/PKT-001-governance-review-queue-frontend-feedback.yaml`
  - `docs/pantheon-feedback/PKT-001-governance-review-queue/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/PKT-001-governance-review-queue/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/PKT-001-governance-review-queue/UI_DECISIONS.md`
  - `docs/pantheon-feedback/PKT-001-governance-review-queue/QA_STATUS.md`
- Contract sources:
  - `docs/bff/PKT-001-governance-review-queue.md`
  - `docs/screens/PKT-001-governance-review-queue.md`
  - `docs/examples/PKT-001-governance-review-queue.json`
  - `docs/pantheon-handoffs/PKT-001-governance-review-queue/FRONTEND_CHANGE_SPEC.md`
  - `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/coordination-loop-spec.md`
  - `docs/delivery-coordination-bus.md`
- Front UI implementation at commit
  `56ecdd48bb2fd422a6b1618b65906f02640c938a`:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/governance/GovernanceReviewQueue.tsx`
  - `src/pages/governance/ReviewItemDetail.tsx`
  - `src/pages/governance/types.ts`
- Pantheon BFF runtime:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/smoke_test.py`

## Verified Positives

- The front commit `56ecdd48bb2fd422a6b1618b65906f02640c938a` contains the request
  pair, feedback bundle, and queue UI files referenced in the Pantheon packet.
- The route is wired in `src/App.tsx` and the sidebar entry exists in
  `src/components/AppSidebar.tsx`.
- `src/lib/bffClient.ts` uses the published route through
  `operatorApi.listGovernanceReviewQueue()` and sends `item_type`,
  `risk_level`, `status`, `page_token`, and `page_size` as query params.
- `src/pages/governance/GovernanceReviewQueue.tsx` keeps filter state in URL
  params and sends it to the BFF; it does not client-filter the queue rows.
- `src/pages/governance/ReviewItemDetail.tsx` renders from the embedded
  `item.review_summary` payload and submits routing actions only through
  `operatorApi.sendCommand()`.
- Degraded or unavailable `meta.surfaces` entries keep the queue visible while
  disabling routing CTAs in both the page and the drawer.
- Pantheon targeted BFF tests still pass on the current repo state:
  - `pytest -q services/control-plane/bff/test_w4_remaining_catalog.py services/control-plane/bff/test_read_store_deployment.py services/control-plane/bff/smoke_test.py`
  - result: `23 passed`

## Decision

`PKT-001-governance-review-queue` is **not loop-complete yet**.

The UI implementation is acceptable on the requested static review axes, but
Pantheon follow-up is still required before the loop can close:

1. Pantheon must implement the published review-queue BFF read route and cover
   it with regression or smoke tests.
2. The front repo must republish the canonical `frontend-feedback` and
   `ui-done` payload bodies from a GitHub-visible commit whose internal
   `source_commit` matches the published implementation commit.

## Required Follow-up

1. Dispatch Pantheon runtime/BFF work for the missing
   `GET /api/v1/operator/governance/review-queue` route; preserve the current
   PKT-001 contract and do not invent new endpoints.
2. Keep `POST /api/v1/operator/commands` as the only routing write surface for
   the queue UI.
3. After the BFF route exists, require a replay-clean front republish of:
   - `.coordination/requests/PKT-001-governance-review-queue-frontend-feedback.yaml`
   - `.coordination/requests/PKT-001-governance-review-queue-ui-done.yaml`
4. Re-review against the unchanged contract and example payload once both the
   runtime route and the canonical payload pair are truthful.

## 2026-04-19 Closeout Addendum

Pantheon re-verified this packet after the replay-clean front republish on
`ajoe734/front-ai-trading-system` branch `pkt-004-detail-fix`.

- The front repo now publishes the canonical request pair at commit
  `c9c1e20726bfc1d35f3ddcbb4f7552859f1d8f5d`.
- Both request payloads now point `source_commit` at the replay-clean transport
  commit `77ab876e05dbb206f4fd4abc39051df86f6127c2`, whose tree contains the
  governance review queue UI, the feedback bundle, and the current navigation
  updates.
- Pantheon's PKT-001 contract route is live and re-verified via
  `python3 -m pytest services/control-plane/bff/test_pkt001_governance_review_queue_contract.py -q`
  with `3 passed`.

## Final Decision

**APPROVED.**

Residual live-browser QA remains non-blocking and does not prevent loop
closeout.
