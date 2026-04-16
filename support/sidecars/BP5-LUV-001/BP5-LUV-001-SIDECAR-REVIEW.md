# BP5-LUV-001 Review Packet

**Sidecar kind:** `review_packet`  
**Sidecar task:** `BP5-LUV-001-SIDECAR-REVIEW`  
**Helper parent:** `BP5-LUV-001` — Review the returned feedback bundles for F-042 and PKT-001 governance review queue  
**Parent owner:** `Codex`  
**Parent reviewer:** `Claude`  
**Prepared by:** `Codex`  
**Reviewer:** `Claude`  
**Date:** `2026-04-16`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> behavior, coordination payload truth, or the parent task's canonical artifacts. It gives `Claude`
> a compact review surface for the current parent-task state in `ai-status.json`, which is already
> back in `review`.

---

## 1. Purpose

This packet narrows the active reviewer check to two questions:

1. Is the current Pantheon disposition for `F-042` correct: keep the existing contract, but treat
   the returned front-end bundle at `source_commit c34048e...` as `followup-required` because the
   shared client still misses live-BFF compatibility details?
2. Is the current Pantheon disposition for `PKT-001-governance-review-queue` correct: convert the
   old blocked feedback into an explicit `delivered` follow-up because the checkout/runtime blocker
   is now gone and no Pantheon-side contract change is needed?

This is a reviewer aid, not a replacement for the parent review and not a second acceptance packet.

---

## 2. Parent Task Snapshot

Current durable task truth from `ai-status.json`:

| Field | Value |
|---|---|
| Task ID | `BP5-LUV-001` |
| Status | `review` |
| Owner | `Codex` |
| Reviewer | `Claude` |
| Current next note | `F-042` should stay `followup-required`; `PKT-001-governance-review-queue` should move to explicit delivered follow-up on the restored checkout |

Primary current evidence:

- `ai-status.json:474-510`
- `docs/pantheon-delivery/F-042/DELIVERY_NOTE.md`
- `docs/pantheon-delivery/F-042/CONTRACT_LOCK.json`
- `docs/pantheon-delivery/PKT-001-governance-review-queue/DELIVERY_NOTE.md`
- `docs/pantheon-delivery/PKT-001-governance-review-queue/CONTRACT_LOCK.json`

---

## 3. Stale Material To Ignore

Two older review aids now describe a superseded world-state and should not drive the final review:

| File | Why it is stale |
|---|---|
| `.coordination/reviews/BP5-LUV-001-review.md:9-67` | assumes both `F-042` and `PKT-001` are still blocked on a mirror-only checkout |
| `support/sidecars/BP5-LUV-001/BP5-LUV-001-SIDECAR-ACCEPTANCE.md:29-113` | repeats the same blocker-only model and still says `F-042` had not genuinely returned |

Current repo evidence no longer matches those conclusions:

- `../front-ai-trading-system/docs/pantheon-feedback/F-042/LOVABLE_CHANGE_FEEDBACK.md:3-29`
- `../front-ai-trading-system/.coordination/requests/PKT-001-governance-review-queue-frontend-feedback.yaml:1-21`
- `docs/pantheon-delivery/PKT-001-governance-review-queue/DELIVERY_NOTE.md:9-30`

Reviewer guidance: treat the older acceptance/review files as historical context only, not as the
active source for parent-task disposition.

---

## 4. F-042 Evidence Pack

### 4.1 What the returned front-end bundle claims

The returned `frontend-feedback` request points Pantheon at a concrete front-end commit and changed
files:

- `.coordination/requests/F-042-frontend-feedback.yaml:1-19`
  - `status: completed`
  - `source_commit: c34048e2e096d3fe9bde1c216c0613535d71f07d`
  - changed files include `src/pages/promotion/PromotionReview.tsx`,
    `src/pages/promotion/types.ts`, and `src/lib/bffClient.ts`

The front-owned feedback bundle self-assesses this state as ready for Pantheon review:

- `../front-ai-trading-system/docs/pantheon-feedback/F-042/LOVABLE_CHANGE_FEEDBACK.md:3-29`
- `../front-ai-trading-system/docs/pantheon-feedback/F-042/API_GAP_REQUESTS.json:1-5`
- `../front-ai-trading-system/docs/pantheon-feedback/F-042/UI_DECISIONS.md:1-7`
- `../front-ai-trading-system/docs/pantheon-feedback/F-042/QA_STATUS.md:5-22`

That bundle explicitly says:

- the reviewed source is `c34048e...`
- `status: no_open_gaps`
- the Pantheon side can move into formal review
- only runtime verification remains

### 4.2 Why Pantheon still kept `followup-required`

The `source_commit c34048e...` code still has three live-BFF compatibility gaps.

1. Missing `Authorization` propagation in the shared client

- `git show c34048e2e096d3fe9bde1c216c0613535d71f07d:src/lib/bffClient.ts`:
  - `71-76`: `get()` only sends `Accept`
  - `102-107`: `postJson()` only sends `Content-Type` and `Accept`
- Pantheon requires bearer auth on both relevant surfaces:
  - `services/control-plane/bff/main.py:61-70`
  - `services/control-plane/bff/main.py:956-959`
  - `services/control-plane/bff/main.py:1993-2007`
- Auth behavior is exercised by smoke tests:
  - `services/control-plane/bff/smoke_test.py:182-190`

2. Wrong Pantheon error-envelope parsing

- `git show c34048e2e096d3fe9bde1c216c0613535d71f07d:src/lib/bffClient.ts:35-43`
  expects `{ status, code, message }`
- Pantheon actually raises `HTTPException(..., detail=body.dict())` where `body` is
  `ErrorResponse(error=...)`:
  - `services/control-plane/bff/main.py:79-98`
  - `services/control-plane/bff/models.py:63-70`

3. `meta.surfaces` status typing still diverges from the handoff contract

- `git show c34048e2e096d3fe9bde1c216c0613535d71f07d:src/pages/promotion/types.ts:57-59`
  uses `'ok' | 'degraded' | 'error'`
- Pantheon handoff contract uses `'ok' | 'degraded' | 'unavailable'`:
  - `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md:75-79`
  - `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md:155-161`

### 4.3 Pantheon-side outcome

The current Pantheon disposition is already materialized and internally consistent:

- `docs/pantheon-delivery/F-042/DELIVERY_NOTE.md:1-85`
- `docs/pantheon-delivery/F-042/CONTRACT_LOCK.json:1-20`

Those artifacts keep the contract unchanged and scope the follow-up to exactly:

- propagate `Authorization` in the shared BFF client
- parse the Pantheon error envelope correctly
- align surface-status typing with the canonical contract

### 4.4 Independent verification from this sidecar run

I re-ran the auth/read-path subset that matters for this review:

```bash
python3 -m pytest services/control-plane/bff/smoke_test.py -q -k \
  'missing_auth_header_submit or missing_auth_header_poll or \
   deployment_review_composed_view or degraded_surface_returns_staleness_warning or \
   submit_and_poll_command'
```

Result: `5 passed, 15 deselected`.

One broader suite rerun did expose an unrelated residual issue:

```bash
python3 -m pytest services/control-plane/bff/test_read_store_deployment.py \
  services/control-plane/bff/smoke_test.py -q
```

Result: `1 failed, 21 passed` because
`services/control-plane/bff/smoke_test.py::TestOperatorBFF::test_concurrent_modification_rejected`
hit a pre-existing in-flight command and returned `409` on the first submit. That failure does not
undercut the auth/error-envelope evidence above, but it is a test-isolation risk worth keeping in
mind.

### 4.5 Reviewer conclusion for F-042

`F-042` should **not** be closed out. The front-end bundle returned real code and self-reported no
open gaps, but Pantheon's follow-up-required delivery note is still the correct disposition because
the shared client remains incompatible with live BFF auth/error semantics and still drifts on
surface-status typing.

---

## 5. PKT-001 Evidence Pack

### 5.1 What the blocked bundle said

The returned blocked feedback request and bundle are real and internally consistent for the earlier
environment:

- `../front-ai-trading-system/.coordination/requests/PKT-001-governance-review-queue-frontend-feedback.yaml:1-21`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-001-governance-review-queue/LOVABLE_CHANGE_FEEDBACK.md:1-24`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-001-governance-review-queue/API_GAP_REQUESTS.json:1`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-001-governance-review-queue/UI_DECISIONS.md:1-5`
- `../front-ai-trading-system/docs/pantheon-feedback/PKT-001-governance-review-queue/QA_STATUS.md:1-5`

That bundle said the repo was mirror-only and no implementation happened.

### 5.2 Why Pantheon converted it to `delivered`

The blocker described by that bundle no longer matches the current environment.

Pantheon's delivery note documents the restored prerequisite:

- `docs/pantheon-delivery/PKT-001-governance-review-queue/DELIVERY_NOTE.md:9-30`
  - `.git` exists
  - `src/` exists
  - `src/lib/bffClient.ts` exists
  - mirrored `lovable-ui-task` exists
  - prior feedback bundle exists

This sidecar independently confirmed the same runtime/repo prerequisites:

- current front repo HEAD: `45ed3dc614e66ffd3aaefc15e3eef2fcda9bb8aa`
- local checks returned:
  - `git_dir present`
  - `src_dir present`
  - `bff_client present`

### 5.3 Pantheon-side outcome

The current Pantheon disposition is already materialized and coherent:

- `docs/pantheon-delivery/PKT-001-governance-review-queue/DELIVERY_NOTE.md:1-52`
- `docs/pantheon-delivery/PKT-001-governance-review-queue/CONTRACT_LOCK.json:1-20`

Those artifacts say:

- contract unchanged
- no new endpoint or API gap requested
- blocked bundle was accurate for the earlier mirror-only state
- next step is a fresh UI implementation cycle on the restored canonical checkout

### 5.4 Reviewer conclusion for PKT-001

`PKT-001-governance-review-queue` should **not** stay in a blocker-only bucket. The correct
Pantheon move is the current one: publish an explicit delivered follow-up that preserves the
existing contract and tells the front repo to rerun the same implementation cycle on the restored
checkout.

---

## 6. Reviewer Handoff

Recommended `Claude` review path:

1. Start from `ai-status.json:474-510`, not from the older blocker-only assessment files.
2. For `F-042`, compare the front bundle's optimistic self-assessment
   (`../front-ai-trading-system/docs/pantheon-feedback/F-042/...`) against the actual
   `source_commit c34048e...` client/type code and the live Pantheon auth/error contract.
3. Confirm that `docs/pantheon-delivery/F-042/DELIVERY_NOTE.md` and
   `docs/pantheon-delivery/F-042/CONTRACT_LOCK.json` correctly keep the contract fixed while
   demanding one more UI cycle.
4. For `PKT-001`, confirm the blocked feedback bundle is historically accurate but now stale, and
   that the current `delivered` packet correctly shifts the next action back to the front repo
   without inventing backend work.

Suggested reviewer note:

`BP5-LUV-001-SIDECAR-REVIEW` correctly narrows the parent review. `F-042` returned a real bundle,
but Pantheon's followup-required disposition is still correct because the reviewed `c34048e`
client/types remain incompatible with live BFF auth/error/status semantics. `PKT-001` no longer
belongs in a mirror-only blocker state; the delivered follow-up correctly preserves the existing
contract and asks the front repo to rerun the same UI cycle on the restored checkout.

---

## 7. Review Approval Snapshot

`Claude` approved this sidecar on `2026-04-16` with the following durable conclusions already
recorded in `ai-status.json`:

- `F-042` should remain `followup-required` because the reviewed `c34048e` shared client/types
  still drift from live BFF auth, error-envelope, and surface-status semantics
- `PKT-001-governance-review-queue` should remain `delivered` because the earlier blocker was
  environmental, the checkout has been restored, and the Pantheon contract is unchanged
- this sidecar stayed within support-artifact scope and did not mutate canonical truth

Owner closeout for this sidecar is limited to archiving this packet and updating task state to
`done`. Whether the parent task absorbs or references this review aid remains the parent owner's
decision.

---

## 8. Sidecar Scope Declaration

This file is a support artifact only.

- No L1 or L2 canonical document was modified by this sidecar
- No Pantheon runtime, registry, governance, or BFF implementation file was modified by this sidecar
- No coordination request/response payload was modified by this sidecar
- The older acceptance packet was left untouched as historical context
- The only new support artifact created by this slice is this review packet
