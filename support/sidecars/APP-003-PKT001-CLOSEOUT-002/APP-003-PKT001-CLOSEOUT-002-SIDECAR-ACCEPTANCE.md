# APP-003-PKT001-CLOSEOUT-002 Acceptance Packet

**Parent task:** `APP-003-PKT001-CLOSEOUT-002`  
**Parent owner:** `Codex3`  
**Parent reviewer:** `Codex`  
**Parent status snapshot:** `review_approved` in `ai-status.json` on `2026-04-24`  
**Sidecar task:** `APP-003-PKT001-CLOSEOUT-002-SIDECAR-ACCEPTANCE`  
**Sidecar owner:** `Codex`  
**Sidecar reviewer:** `Codex3`  
**Sidecar status snapshot:** `review_approved`  
**Helper kind:** `acceptance_packet`  
**Prepared:** `2026-04-24`

> Scope constraint: support artifact only. This packet does not modify
> canonical truth, L1 policy, coordination truth, or runtime / registry /
> governance implementations. It is a reviewer-ready acceptance summary for the
> reopened PKT-001 closeout follow-up only.

## 1. Bottom Line

This sidecar should stay narrow:

- `APP-003-PKT001-CLOSEOUT-002` is a truthful reopened execution task, not a
  new BFF gap claim.
- The remaining blocker is front-owned fail-closed validation of required
  `meta.surfaces` keys for PKT-001 list and detail payloads.
- A truthful closeout of the parent task should end with refreshed front return
  artifacts, not with canonical-truth edits in Pantheon.
- The current board truth already has the parent task in `review_approved`; this
  packet remains support evidence for the closeout rather than a new reopen
  request.

## 2. Evidence Basis

The packet below is anchored to these sources:

- `ai-status.json`
- `docs/reviews/2026-04-24-cross-repo-reopen-execution-packet.md`
- `.coordination/reviews/PKT-001-deployment-review-review.md`
- `../front-ai-trading-system/docs/lovable/2026-04-24-pkt001-pkt003-followup-prompt.md`
- `support/sidecars/APP-003-PKT001-SURFACE-VALIDATION-001/APP-003-PKT001-SURFACE-VALIDATION-001-SIDECAR-BFF-HANDOFF.md`

These references are sufficient to describe the reopened truth without touching
global summaries or changing parent-task scope.

## 3. Parent Task Truth Readback

The current parent task in `ai-status.json` is correctly framed as:

- `PKT-001` is not a new BFF gap.
- The current front default branch still accepts partial `meta.surfaces`
  payloads instead of failing closed on the required key set.
- The reopen exists so the remaining front-owned follow-up is represented by a
  named execution task instead of a generic residual note.
- The latest board state places the parent task in `review_approved`, so the
  remaining lifecycle step is owner finalization rather than additional scope
  discovery.

The cross-repo reopen packet says the same thing in execution language:

- `PKT-001` remains in front-owned closeout because the UI still does not
  validate the required `meta.surfaces` key sets fail-closed.
- The packet is about truthful reopen and rebaseline work, not a claim that
  Pantheon BFF routes are missing.

## 4. Acceptance Checklist For The Parent Task

For `APP-003-PKT001-CLOSEOUT-002` to move cleanly toward `done`, the parent
owner's final readback should still preserve all of the following:

| Acceptance target | What to verify |
|---|---|
| Named execution task remains visible | `APP-003-PKT001-CLOSEOUT-002` stays on the execution board as the explicit PKT-001 reopen. |
| No scope drift into a new BFF gap | Review evidence continues to show PKT-001 list/detail/command routes are already live in Pantheon. |
| List payload validates required surfaces fail-closed | Front list reader requires `meta.surfaces.deployment_plans` and `meta.surfaces.allowedActions`. |
| Detail payload validates required surfaces fail-closed | Front detail reader requires `meta.surfaces.deployment_plan`, `meta.surfaces.approval_decision`, `meta.surfaces.allowedActions`, `meta.surfaces.latestRun`, `meta.surfaces.review`, and `meta.surfaces.runtime_binding`. |
| Shared helper is used | Front implementation uses `findMissingSurfaceFields()` from `src/lib/degradationBanner.ts` rather than ad-hoc existence checks. |
| Front return is refreshed truthfully | If the front cycle is completed, the returned request pair / feedback bundle is republished with a Git-visible, immutable `source_commit`. |

Important boundary:

- the parent task acceptance is about front fail-closed validation plus
  truthful republish
- it is not a license to reopen canonical PKT-001 route ownership, runtime SSE
  ownership, or Pantheon contract truth

## 5. Dependency Map

### 5.1 Durable board dependencies

The current `ai-status.json` entry lists no explicit `depends_on` items for the
parent task. This sidecar should preserve that truth.

### 5.2 Evidence and support dependencies

| Reference | Role |
|---|---|
| `docs/reviews/2026-04-24-cross-repo-reopen-execution-packet.md` | Materializes the reopen and proves why the task exists. |
| `.coordination/reviews/PKT-001-deployment-review-review.md` | Records the exact front-owned blocker and the already-confirmed Pantheon positives. |
| `../front-ai-trading-system/docs/lovable/2026-04-24-pkt001-pkt003-followup-prompt.md` | Preserves the exact required PKT-001 list/detail surface keys and republish rules for the front lane. |
| `support/sidecars/APP-003-PKT001-SURFACE-VALIDATION-001/APP-003-PKT001-SURFACE-VALIDATION-001-SIDECAR-BFF-HANDOFF.md` | Earlier support packet that already narrowed the issue to fail-closed surface validation rather than BFF repair. |

### 5.3 Downstream truth impact

| Task / area | Why it matters |
|---|---|
| `APP-003-TRUTH-SYNC-004` | Rebaseline work should keep this reopen visible as an active PKT-001 residual until the front lane actually closes it. |
| EP5 / later productization proof | Later proof packets should inherit the truthful statement that PKT-001 still required front fail-closed validation as of `2026-04-24`. |

## 6. Sidecar Review Checklist

This sidecar itself is acceptable only if all four checks remain true:

| Check | Result | Evidence |
|---|---|---|
| Support-only scope preserved | PASS | Packet lives only under `support/sidecars/APP-003-PKT001-CLOSEOUT-002/`. |
| No canonical truth mutation | PASS | No L0/L1 policy or coordination truth files are edited by this packet itself. |
| Parent truth is described accurately | PASS | Reopen stays framed as front fail-closed validation follow-up, not as a new BFF gap. |
| Acceptance criteria are concrete | PASS | Required list/detail `meta.surfaces` keys and truthful republish expectation are explicit. |

## 7. Recorded Disposition

Recorded reviewer note for this sidecar:

`Support packet is accurate and stays within support-only scope: PKT-001 remains a front-owned fail-closed meta.surfaces validation follow-up, not a new Pantheon BFF gap, and the parent closeout should end with truthful front republish artifacts.`

Recorded next handoff:

- this sidecar may be finalized by its owner because the support packet has
  already been approved in `ai-status.json`
- the parent execution task remains separately owned by `Codex3` and can be
  finalized on its own lifecycle without widening this support slice beyond
  acceptance-packet scope
