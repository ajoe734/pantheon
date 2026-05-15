# EXEC-FRONT-EW05-001 Re-review

Review date: 2026-04-20
Reviewer: Codex
Status: changes requested

## Findings

1. Nested contract gaps still render instead of failing closed

- `.coordination/responses/EW-05-mutation-review-lovable-ui-task.yaml:21` requires the EW-05 screen to fail closed when required nested fields inside `change_details`, `threshold_triggers`, `required_approvals`, `review_chain`, `evidence_refs`, or a non-null `rollback_followthrough` are missing.
- [`MutationReview.tsx`](/home/lupin/code/front-ai-trading-system/src/pages/evolution/MutationReview.tsx:136) only checks that those containers exist; it does not validate the required nested members inside them.
- The page then renders those nested members directly in the proposed-changes table, evidence rail, rollback panel, threshold table, approvals checklist, and review-chain audit trail at [`MutationReview.tsx`](/home/lupin/code/front-ai-trading-system/src/pages/evolution/MutationReview.tsx:701), [`MutationReview.tsx`](/home/lupin/code/front-ai-trading-system/src/pages/evolution/MutationReview.tsx:776), [`MutationReview.tsx`](/home/lupin/code/front-ai-trading-system/src/pages/evolution/MutationReview.tsx:816), [`MutationReview.tsx`](/home/lupin/code/front-ai-trading-system/src/pages/evolution/MutationReview.tsx:860), [`MutationReview.tsx`](/home/lupin/code/front-ai-trading-system/src/pages/evolution/MutationReview.tsx:901), and [`MutationReview.tsx`](/home/lupin/code/front-ai-trading-system/src/pages/evolution/MutationReview.tsx:954).
- Result: a malformed but non-empty payload can leak partial or `undefined` rows into the operator UI instead of tripping the contract-gap path the handoff explicitly requires.
- Required fix: extend the contract guard to validate the required nested fields for every item in those arrays and for every property of a non-null `rollback_followthrough`, then fail closed before rendering if any are absent.

## Verification

- Previous blocking review findings are fixed: `approval_decision_id` now lands on mounted approval-decision routes, and `linked_postmortem_id` now deep-links through `?postmortem=...` for the destination screen's actual contract.
- `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/evolution/MutationReview.tsx src/pages/evolution/MutationReviewTypes.ts` passed in `front-ai-trading-system`.
- `npm run build` passed in `front-ai-trading-system`.
