# APP-003-F042-FOLLOWUP-001 Revalidation Packet

**Task:** `APP-003-F042-FOLLOWUP-001`  
**Owner:** `Codex2`  
**Reviewer:** `Codex`  
**Date:** `2026-04-24`

## Scope

Review the latest Git-visible F-042 reopen bundle and verify whether it is
truthful enough to approve the follow-up task itself.

This packet is support-only. It does not modify canonical truth or front-repo
coordination files.

## Verification Surface

- Pantheon task brief:
  `.orchestrator/task-briefs/app_003_f042_followup_001.md`
- Front repo coordination files:
  - `.coordination/requests/F-042-frontend-feedback.yaml`
  - `docs/pantheon-feedback/F-042/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/F-042/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/F-042/UI_DECISIONS.md`
  - `docs/pantheon-feedback/F-042/QA_STATUS.md`
- Git-visible front repo commit under review:
  - intended short commit: `d306f85`
  - actual full commit: `d306f850a8e04982862405e4435855bf11e008e4`

## Findings

### 1. The reopen disposition is substantively correct

The updated F-042 frontend-feedback bundle correctly repositions this cycle as
front-owned reopen evidence rather than closeout approval.

The checked-in commit `d306f850a8e04982862405e4435855bf11e008e4` still shows:

- `src/App.tsx` mounts F-042 only at `/promotion-review`
- `src/pages/promotion/PromotionReview.tsx` still depends on `?plan=...`
- `src/pages/promotion/PromotionReview.tsx` still renders decision state,
  reviewer, and decided-at from `review.*`
- `src/auth/AuthProvider.tsx` still does not synchronize
  `pantheon_operator_token`

Result: the task should not close out as a resolved UI follow-up, and no new
Pantheon BFF gap is justified from this evidence surface.

### 2. The bundle is not yet truthful enough for approval because the recorded full commit hash is invalid

The updated `F-042-frontend-feedback.yaml` records:

- `source_commit: d306f85cb7bf8c9c12376840f775f973a0cb566d`

That object does not exist in the front repo.

The real full commit for short hash `d306f85` is:

- `d306f850a8e04982862405e4435855bf11e008e4`

Because the task acceptance explicitly requires truthful Git-visible evidence,
this invalid commit anchor is a review blocker even though the surrounding
reopen narrative is otherwise directionally correct.

## Reviewer Recommendation

Reopen `APP-003-F042-FOLLOWUP-001` to the owner and request a corrected
Git-visible commit anchor across the F-042 reopen bundle.

Expected fix:

1. Replace the invalid full hash in
   `../front-ai-trading-system/.coordination/requests/F-042-frontend-feedback.yaml`
   with `d306f850a8e04982862405e4435855bf11e008e4`.
2. Re-check any matching F-042 feedback docs for the same typo.
3. Return the task to review without changing the substantive disposition:
   reopen remains correct, and no new Pantheon BFF gap is requested.
