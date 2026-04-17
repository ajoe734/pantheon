# BP6-LUV-015 — BFF & Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `BP6-LUV-015` — Close the F-042 Promotion Review UI loop  
**Sidecar task:** `BP6-LUV-015-SIDECAR-BFF-HANDOFF`  
**Prepared by:** Claude  
**Reviewer:** Codex  
**Date:** 2026-04-17  
**Mutates canonical:** no

---

## 1. Purpose

This packet collects the BFF query gap history, operator journey map, and frontend handoff requirements for the `F-042 Promotion Review` screen. It is a support artifact only — it does not alter L1 contracts, BFF implementation, or the coordination request files it references.

---

## 2. Current Loop State

| Item | Value |
|---|---|
| Feature ID | `F-042` |
| Screen | Promotion Review (`screen-governance-promotion-review`) |
| Workbench | Governance Workbench |
| Lovable task status | `loop-complete` (as of latest `F-042-lovable-ui-task.yaml`) |
| BFF gap status | All resolved (`BP6-BFF-001` done) |
| Parent task (`BP6-LUV-015`) status | Open — review found replay and type-alignment blockers |
| Next frontend action | Republish from a truthful committed Git tuple with all cycle-2 fixes applied |

---

## 3. BFF Gap History

### 3.1 Original Gap Request

File: `.coordination/requests/F-042-bff-gap.yaml`

Three gaps were raised against `source_commit: c34048e2...`:

| Gap | Root cause | Resolution |
|---|---|---|
| `Authorization` header missing | BFF client did not inject bearer token on stateful requests | Fixed: `src/lib/bffClient.ts` now sends `Authorization: Bearer <token>` |
| Error envelope mismatch | Frontend used `errors[]` envelope; published contract requires `detail.error.*` | Fixed: client now parses `detail.error.*` envelope; must not regress to `errors[]` |
| Surface status type drift | Frontend used `'error'`; published contract requires `'unavailable'` (full union: `ok \| degraded \| unavailable`) | Fixed: `src/pages/promotion/types.ts` uses `'unavailable'`; must not regress to `'error'` |

### 3.2 Resolution

All three gaps were resolved as part of `BP6-BFF-001`. The Lovable prompt and `FRONTEND_CHANGE_SPEC.md` were updated and republished. The BFF server-side implementation requires no further changes for this feature.

Resolution artifacts:
- `.coordination/responses/F-042-lovable-prompt.md`
- `.coordination/responses/F-042-lovable-ui-task.yaml`
- `docs/pantheon-handoffs/F-042/FRONTEND_CHANGE_SPEC.md`

### 3.3 Open: Source Commit Replay Issue

The BP6-LUV-015 review (`.coordination/reviews/BP6-LUV-015-review.md`) found that the frontend's republished `source_commit` (`bfb87a9...`) does not match the on-disk committed state of `.coordination/requests/F-042-ui-done.yaml` and `.coordination/requests/F-042-frontend-feedback.yaml`. Replaying at `bfb87a9` shows the request pair still points at the older commit tuple `c34048e2...`.

**Impact:** The supervisor cannot reconstruct an accepted F-042 cycle from the advertised payload path + source commit tuple, so the loop cannot be marked `loop-complete` by the supervisor in a verified way.

**Required fix (frontend-owned):**  
Publish one truthful, Git-visible commit to `ajoe734/front-ai-trading-system` that:
- Contains all four file changes listed in §5
- Is referenced consistently by both `.coordination/requests/F-042-ui-done.yaml` and `.coordination/requests/F-042-frontend-feedback.yaml` via `source_commit`
- Also anchors `docs/pantheon-feedback/F-042/*` at the same commit

---

## 4. Operator Journey — Promotion Review Screen

The Promotion Review screen is visited by a governance-track operator after a deployment plan has been reviewed and an approval decision has been issued. The operator's journey:

```
Operator selects a pending deployment plan
    │
    ▼
Screen loads: GET /api/v1/operator/deployment-review/{plan_id}
    │
    ├─ Renders deployment plan identity (id, stage, artifact_id)
    ├─ Renders approval decision (outcome, reviewer, decided_at, risk_level, state)
    │       └─ Source of truth: response.data.approval_decision.*
    ├─ Renders governance outcome (review.riskSummary, review.governanceOutcome)
    ├─ Renders capital pool status
    ├─ Renders runtime binding status
    ├─ Renders latest run progress bar (progress: number | null)
    │       └─ If null → show explicit "unavailable" message, not a contract gap
    └─ Renders CTA: "Promote to Paper" if allowedActions.canPromoteToPaper == true
             │
             ▼
    Operator clicks "Promote to Paper"
             │
             ▼
    POST /api/v1/operator/commands
    {
      "command": "ApproveDeployment",
      "target": { "type": "DeploymentPlan", "id": "<plan_id>" },
      "action": "approve",
      "params": {
        "deployment_plan_id": "<plan_id>",
        "approval_decision": "approve",
        "verification_notes": "Promotion review approved in UI.",
        "verification_timestamp": "<iso-timestamp>"
      },
      "audit_context": {
        "reason": "Promotion review approval.",
        "timestamp": "<iso-timestamp>"
      }
    }
```

Use the published command contract only. Do not invent a screen-local body shape such as `{ action: 'promote_to_paper', plan_id }` — that shape is not supported.

Surface status panel (from `meta.surfaces`):

| Surface key | Possible states |
|---|---|
| Any surface | `ok` \| `degraded` \| `unavailable` |

When a surface is `degraded` or `unavailable`, the screen must show the degraded panel for that section — it must not invent fallback data.

---

## 5. Frontend Files — Required Changes

All four files must be updated in a single committed state.

### 5.1 `src/lib/bffClient.ts`

- Send `Authorization: Bearer <token>` on every stateful Pantheon BFF request.
- Parse the standard `detail.error.*` envelope from Pantheon BFF error responses.  
  Do not regress to an `errors[]` client contract.

### 5.2 `src/pages/promotion/types.ts`

```typescript
// Correct cycle-2 type shapes (per F-042-lovable-ui-task.yaml and FRONTEND_CHANGE_SPEC.md):

interface LatestRun {
  progress: number | null;          // null is valid; render as "unavailable"
}

type SurfaceStatus = "ok" | "degraded" | "unavailable";   // NOT "error" — per published frontend handoff

interface ReviewMeta {
  riskSummary: string;
  governanceOutcome: string;
  decisionState?: string;           // optional — not required to render decision
  decidedAt?: string;               // optional
  reviewer?: string;                // optional
}
```

### 5.3 `src/pages/promotion/PromotionReview.tsx`

- Tolerate `latestRun.progress === null`; render an explicit "unavailable" state instead of throwing or treating it as a contract gap.
- Render decision metadata from `approval_decision.state`, `approval_decision.reviewer`, and `approval_decision.decided_at` as authoritative.  
  Do not require optional `review.decisionState`, `review.reviewer`, or `review.decidedAt` to be present in order to render the decision summary.

### 5.4 `src/auth/AuthProvider.tsx`

- Persist `pantheon_operator_token` to allow the shared BFF client to inject the Authorization header in normal application flow.
- Clear `pantheon_operator_token` on logout.

---

## 6. Example Payload Reference

Full example payload: `docs/examples/F-042-review-page.json`

The file contains the canonical happy-path payload. Abbreviated key fields:

```json
{
  "data": {
    "approval_decision": {
      "id": "approval-042",
      "outcome": "approved",
      "reviewer": "governance",
      "decided_at": "2026-04-11T07:55:00Z",
      "risk_level": "low",
      "state": "decided"
    },
    "latestRun": {
      "progress": 0.82
    },
    "review": {
      "riskSummary": "No unresolved severity-1 or severity-2 incidents.",
      "governanceOutcome": "approved",
      "decisionState": "decided",
      "decidedAt": "2026-04-11T07:55:00Z",
      "reviewer": "governance"
    }
  },
  "meta": {
    "surfaces": {
      "deployment_plan": { "status": "ok" },
      "approval_decision": { "status": "ok" },
      "allowedActions": { "status": "ok" },
      "latestRun": { "status": "ok" },
      "review": { "status": "ok" },
      "runtime_binding": { "status": "ok" }
    }
  }
}
```

This is the all-ok happy-path example (`progress: 0.82`, all surfaces `ok`). In runtime scenarios where the BFF cannot populate `latestRun`, `progress` may be `null`; the frontend must tolerate that without throwing (see §5.3). When any surface status is `degraded` or `unavailable`, the frontend must show the degraded panel for that section and must not invent fallback content.

---

## 7. BFF Endpoints — No Further Changes Required

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/operator/deployment-review/{plan_id}` | Fetch full Promotion Review view model |
| `POST` | `/api/v1/operator/commands` | Submit operator command (e.g. promote_to_paper) |

The BFF implementation of both endpoints was completed and verified in `BP6-BFF-001`. No new endpoints or response shape changes are needed for F-042.

---

## 8. Remaining Blockers

| Blocker | Owner | Notes |
|---|---|---|
| Frontend source commit replay issue | Lovable (front-ai-trading-system) | Must publish a single committed Git state that contains all four fixed files and is cited by both coordination request files |
| `latestRun.progress` type drift | Lovable | Must accept `number \| null`, not just `number` |
| Surface status type drift | Lovable | Must use `'unavailable'` (full union `ok \| degraded \| unavailable`), not `'error'` (per F-042-lovable-ui-task.yaml / FRONTEND_CHANGE_SPEC.md) |
| Decision metadata rendering | Lovable | Must use `approval_decision.*` as authoritative; `review.*` decision fields are optional |

Once the frontend republishes from a truthful committed state with all four fixes applied, `BP6-LUV-015` may be re-reviewed for closure.

---

## 9. Handoff Checklist (for Reviewer)

- [ ] All items in §5 are addressed in the frontend's new committed state
- [ ] `.coordination/requests/F-042-ui-done.yaml` cites the new `source_commit` accurately
- [ ] `.coordination/requests/F-042-frontend-feedback.yaml` cites the same `source_commit`
- [ ] `docs/pantheon-feedback/F-042/*` is anchored to the same commit
- [ ] `latestRun.progress: null` renders correctly (no thrown error)
- [ ] Decision metadata renders from `approval_decision.*` without requiring `review.decisionState`
- [ ] Surface status union in `types.ts` is `ok | degraded | unavailable` (must not include `'error'`)
- [ ] Authorization header is emitted in normal app flow via `pantheon_operator_token`
- [ ] No raw fetch calls introduced in component files
- [ ] No new BFF endpoint changes were requested (this is a frontend-only closure)

---

*This is a support artifact. It does not modify any L1 canonical document, BFF implementation, or coordination request file. Absorption into the main BP6-LUV-015 delivery is at the discretion of the parent task owner.*
