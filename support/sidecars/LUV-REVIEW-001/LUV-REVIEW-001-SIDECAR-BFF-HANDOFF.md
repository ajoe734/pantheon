# LUV-REVIEW-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `LUV-REVIEW-001` - Review returned frontend feedback and close loop for promotion-review
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `done` (archived)
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-17`

> This is a support artifact only. It does not modify canonical truth, L1 policy files, or core runtime, registry, governance, or BFF implementations. It packages the current `F-042` review state into a reviewer-ready handoff packet.

---

## 1. Parent Task Summary

`LUV-REVIEW-001` is the Pantheon-side review closeout for the returned Lovable/frontend bundle for `F-042` (`promotion-review`).

The parent task is already finalized as `done` and archived. Its accepted disposition is:

- `follow_up`
- no Pantheon API expansion is required
- the remaining blocker is front-repo replay cleanliness, not a missing Pantheon BFF contract

This sidecar stays narrower than the parent task. It exists to:

- restate the resolved BFF gap versus the still-open frontend republish gap
- summarize the operator journey the screen is already allowed to use
- give the reviewer a compact handoff packet that the parent owner can absorb or ignore

---

## 2. Source References

| Document | Why it matters |
|---|---|
| `ai-status.json` | live parent-task status, reviewer approval, and sidecar assignment |
| `.orchestrator/task-briefs/luv_review_001_sidecar_bff_handoff.md` | task-scoped sidecar scope and artifact path |
| `.coordination/requests/F-042-bff-gap.yaml` | original blocking BFF-gap record and its resolution note |
| `.coordination/responses/F-042-lovable-prompt.md` | exact fix instructions Pantheon already handed back to frontend |
| `.coordination/responses/F-042-lovable-ui-task.yaml` | current frontend-loop constraints and truthful publication rule |
| `.coordination/responses/F-042-frontend-feedback.yaml` | Pantheon review disposition and acceptance results |
| `../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml` | returned frontend claim that triggered the replay-clean follow-up |
| `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md` | original frontend handoff boundary for screen `promotion-review` |

---

## 3. Gap Timeline

| Stage | What was blocked | Current truth |
|---|---|---|
| Initial BFF gap | frontend drift against Pantheon integration rules: missing bearer propagation, wrong error-envelope handling, and wrong surface-status enum | resolved in `.coordination/requests/F-042-bff-gap.yaml` via explicit Lovable/frontend instructions |
| Returned cycle-2 frontend bundle | returned request pair claimed replay-clean at `source_commit=79dc1b5...` | Pantheon review found that commit is not the integrated `F-042` publication commit and still shows the older render path |
| Current closeout state | Pantheon closeout already finalized | finalized as `follow_up`; the next required action is a truthful front-repo republish from one Git-visible commit |

### Key distinction

The blocking condition moved:

- it is no longer "Pantheon owes a new BFF field or endpoint"
- it is now "frontend must republish from a truthful Git-visible commit that actually contains the promoted fixes and feedback bundle"

That distinction matters because the parent task should not reopen canonical BFF scope.

---

## 4. Operator Journey Snapshot

### 4.1 Read path already approved

The screen is already scoped to one read surface:

- `GET /api/v1/operator/deployment-review/{plan_id}`

Expected behavior from the existing Pantheon handoff:

1. page shell, review summary, supporting evidence, and action affordances read from the backend-shaped deployment review payload
2. approval metadata must render from `approval_decision.state`, `approval_decision.reviewer`, and `approval_decision.decided_at`
3. `latestRun.progress = null` is a valid unavailable state, not a contract failure

### 4.2 Command path already approved

The screen is already scoped to one write surface:

- `POST /api/v1/operator/commands`

Expected behavior from the existing Pantheon handoff:

1. CTA visibility stays backend-shaped from `allowedActions`
2. command submission continues through the shared `operatorApi.sendCommand()` path
3. no component-local fetch helper or alternate endpoint is required

### 4.3 Authentication boundary already approved

The frontend is already expected to:

- persist and clear `pantheon_operator_token`
- send `Authorization: Bearer <token>` on Pantheon BFF requests

This was part of the resolved BFF gap. It should not come back as a new Pantheon-side task unless the front repo shows a fresh regression after truthful republish.

---

## 5. What Is Actually Missing

The parent review packet shows four failing acceptance items, but they collapse to one operational requirement:

1. commit the current working-tree fixes in `src/pages/promotion/PromotionReview.tsx` and `src/pages/promotion/types.ts`
2. publish `.coordination/requests/F-042-ui-done.yaml`
3. publish `.coordination/requests/F-042-frontend-feedback.yaml`
4. publish `docs/pantheon-feedback/F-042/*`
5. ensure all four publication elements point at the same final front-repo commit

If that one truthful republish happens, the remaining parent-task blocker should disappear without any Pantheon-side contract change.

---

## 6. Frontend Handoff Guidance

### Safe guidance for the next frontend pass

- Keep `SurfaceStatus` as `ok | degraded | unavailable`.
- Keep `latestRun.progress` typed as `number | null`.
- Treat null progress as explicit unavailable UI, not as missing data.
- Render decision metadata from `approval_decision.*`, not optional `review.*` mirrors.
- Continue using the shared BFF client only.
- Do not ask Pantheon for new endpoints, alternate envelopes, or mock fallback fields.

### What would justify reopening a real BFF gap

Only reopen a Pantheon-side BFF gap if, after truthful republish against the committed screen code, one of these is still true:

- the live deployment review payload omits fields already promised in the published handoff packet
- the command endpoint requires a new backend field or workflow not covered by `allowedActions`
- bearer propagation or standard error-envelope behavior is impossible through the shared client despite the published boundary

Nothing in the current review evidence proves any of those cases.

---

## 7. Reviewer Focus For `Codex`

The highest-signal review questions for this sidecar are:

1. does the packet keep the distinction clear between "resolved Pantheon BFF gap" and "still-untruthful frontend publication"
2. does the operator journey summary stay inside the already-published `F-042` boundary
3. does the handoff avoid implying that Pantheon owes new canonical work before the frontend republish lands

If those checks pass, this packet is ready to serve as a support-only reviewer map for the parent closeout.

---

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | only this file is added under `support/sidecars/LUV-REVIEW-001/` |
| No canonical truth edited | PASS | packet references existing coordination artifacts only |
| BFF gap vs frontend republish gap separated clearly | PASS | sections 3, 5, and 6 isolate the remaining blocker as truthful front publication |
| Operator journey stays within existing F-042 contract | PASS | section 4 uses only the already-approved GET/POST surfaces from the published handoff |

---

## 9. Handoff To Reviewer (`Codex`)

This sidecar reduces `LUV-REVIEW-001` to one practical reading:

1. the original `F-042` BFF gap is already resolved
2. the finalized parent disposition is correctly `follow_up`, not `blocked-on-Pantheon`
3. the only remaining loop-close action is a truthful front-repo republish from one Git-visible commit containing the screen fixes, request pair, and feedback bundle

Recommended use:

- keep this packet as support-only review context
- treat this packet as support-only context alongside the archived parent closeout; no canonical or mainline note changes are required
- do not expand `F-042` canonical scope unless a new post-republish BFF mismatch appears
