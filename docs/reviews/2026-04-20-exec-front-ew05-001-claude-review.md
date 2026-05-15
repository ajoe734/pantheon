# Review: EXEC-FRONT-EW05-001 — EW-05 Mutation Review Frontend

**Reviewer:** Claude
**Date:** 2026-04-20
**Status:** Changes Requested

## Summary

Code quality PASS. All spec requirements are correctly implemented. One blocking delivery issue: the implementation files are not committed to git, and the `source_commit` in the ui-done handoff points to an unrelated commit.

## Code Review: PASS

Verified against `docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md`:

**Types (`MutationReviewTypes.ts`):** Complete and exact match to BFF contract — all fields, union types, and command payload shapes are correct.

**BFF client (`src/lib/bffClient.ts`):** Committed in HEAD (d51274b). `getMutationReview` → `GET /api/v1/operator/mutation-review/{decision_id}`, `approveMutation` and `rejectMutation` → `POST /api/v1/operator/commands` with typed receipts. No raw fetch or axios in component files.

**Routes (`App.tsx`):** Three routes correctly registered:
- `/evolution/mutation-review/:decision_id` → `MutationReview`
- `/personas/approval-decisions` → `ApprovalDecisionList`
- `/personas/approval-decisions/:decisionId` → `ApprovalDecisionDetail`

Deep links verified:
- `approval_decision_id` → `/personas/approval-decisions/:id` — route is mounted ✓
- `linked_incident_id` → `/operator/incidents/:incidentId` — route is mounted ✓
- `linked_postmortem_id` → `/operator/post-incident-review?postmortem=...` — route is mounted, uses postmortem query param not incident param ✓

**CTA gating:** Approve CTA only when `canApproveMutation === true && !isUnavailable`. Reject CTA only when `canRejectMutation === true && !isUnavailable`. Both suppressed when surface is `"unavailable"`. ✓

**Degradation:** Non-dismissable staleness banner on `"stale"`. Panel content replaced with notice on `"unavailable"`. ✓

**Command flow:** Re-fetches after command via `setRefreshKey`. No optimistic `decision_state` update. Submission errors shown with retry via modal. ✓

**BFF-gap guard:** `getMissingFields` checks all required fields; renders contract-gap alert instead of inventing state. ✓

**All panels present:** Decision Context, Proposed Changes (table), Incident/Postmortem Evidence (with correct empty-state copy), Rollback Follow-Through, Risk Assessment (threshold triggers table), Required Approvals, Review Chain. ✓

**No mock/demo layer imports.** ✓

## Blocking Issue: Uncommitted Files

```
?? src/pages/evolution/MutationReview.tsx
?? src/pages/evolution/MutationReviewTypes.ts
?? .coordination/requests/EW-05-mutation-review-ui-done.yaml
 M src/App.tsx   (unstaged changes — EW-05 routes not committed)
```

`git log --all -- src/pages/evolution/MutationReview.tsx` returns no output. The component has never been committed.

The `source_commit` in `.coordination/requests/EW-05-mutation-review-ui-done.yaml` is `42dc4856b36a7c92f5c40cafd94bf8ef09665bbe`, which is the "Repoint PKT-005 and PKT-002 request pairs" commit — it contains no EW-05 files.

## Required Action Before Approval

1. `git add src/pages/evolution/MutationReview.tsx src/pages/evolution/MutationReviewTypes.ts src/App.tsx .coordination/requests/EW-05-mutation-review-ui-done.yaml`
2. `git commit` with a clear EXEC-FRONT-EW05-001 commit message.
3. Update `source_commit` in `.coordination/requests/EW-05-mutation-review-ui-done.yaml` to the actual commit SHA.
4. Re-submit for final approval.

Code quality is approved once the commit is in place.
