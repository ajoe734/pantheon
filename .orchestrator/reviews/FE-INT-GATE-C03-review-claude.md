# Review: FE-INT-GATE-C03

- **Task**: F12 new — Approvals decide/two-man/batch
- **Reviewer**: Claude
- **Owner**: Codex
- **Artifact**: execute-plans/e2e/12-approvals.spec.ts
- **Decision**: APPROVED

## Acceptance Criteria Verification

### 1. single decide 更新 approval+HIQ ✓

Test "single decide updates approval and linked HIQ" (line 936):
- Initial state asserted from DOM: approval="pending", HIQ="pending_review"
- POST /bff/approvals/{id}/decide returns 202 with full CommandResponse shape
- CommandResponse.data contains: approval.state="approved", command="ApprovalGovernanceDecision", hiq.status="resolved" with resolution="approved_by_governance"
- meta.durable=true, meta.liveCapitalSideEffects=false verified
- DOM post-update asserts: approval-state="approved", hiq-state="resolved"
- Server-side state harness.approval() and harness.linkedHiq() both verified
- Audit log entry checked (action="approval.approve")
- Authorization header sent as AUTH_HEADER verified
- Idempotency-Key sent with "f12-" prefix verified

### 2. two-man-sign quorum 可見 ✓

Test "two-man sign enforces distinct signer and shows quorum progress" (line 990):
- Initial quorum DOM shows "1/2" (operator stage pre-seeded as approved) ✓
- distinctFamilyRequired shows "2-fam" badge ✓
- Same-signer (OPERATOR_ID) attempt returns 409 with TWO_MAN_REQUIRED, reason=TWO_MAN_DISTINCT_OPERATOR_REQUIRED ✓
- Quorum stays at "1/2" after rejection ✓
- Distinct signer "risk-owner-f12" / roleFamily="risk" returns 202 ✓
- Response quorum: approved=2, min=2, distinctFamilyRequired=true, state="approved" ✓
- DOM post-update: quorum="2/2", stage-risk="risk approved", approval-state="approved" ✓
- Server-side state harness.approval().quorum verified ✓

### 3. batch partial failure 開 BulkResultDrawer ✓

Test "batch partial failure opens BulkResultDrawer and keeps failed selected" (line 1055):
- Two items selected (ok candidate + blocked candidate), "2 selected" shown ✓
- batch-approve click triggers POST /bff/approvals/batch-decide ✓
- BFF returns 207 with partial=true, summary 1 succeeded / 1 failed ✓
- bulk-result-drawer is visible ✓
- Dialog text shows "1 succeeded / 1 failed / 2 total (partial)" ✓
- Individual result rows: OK candidate shows "OK", blocked candidate shows "FAIL" + "PRECONDITION_FAILED" ✓

### 4. failed item 保留 selected ✓

- After batch: "1 selected" shown ✓
- BATCH_FAILED_APPROVAL_ID checkbox is checked ✓
- BATCH_OK_APPROVAL_ID checkbox is not checked ✓
- state.selected = [BATCH_FAILED_APPROVAL_ID] ✓
- state.drawerOpen = true, state.bulkResult.partial = true ✓

## Code Quality

- **TypeScript types**: Well-typed throughout (ApprovalDto, HiqDto, CommandResponse, BulkActionResponse, etc.). No `any` except necessary window cast in page.evaluate.
- **Harness design**: Self-contained ApprovalHarness with in-memory Maps; clone() used consistently to prevent reference mutation between harness state and responses.
- **Quorum logic**: refreshApprovalState() correctly counts approved stages and computes state from quorum threshold.
- **Two-man enforcement**: existingSignerIds derived from approved stages.decidedBy — correctly catches same signer regardless of roleFamily.
- **CORS**: Proper preflight OPTIONS handler and CORS headers on all responses.
- **Idempotency**: crypto.randomUUID() per request — correct for a write-once idempotency pattern.
- **Audit trail**: Recorded at harness level for all decide and two-man-sign actions.

## Verification Claimed by Owner

- esbuild bundle: passed
- Playwright --list: found 3 tests
- Playwright run: 3/3 passed

Count matches: exactly 3 `test(...)` calls in spec (single decide, two-man-sign, batch partial failure).

## Decision

All 4 acceptance criteria satisfied. No changes requested. Returning to Codex for finalization.
