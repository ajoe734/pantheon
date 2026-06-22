# AG-FE-SW-003 Sidecar Review Notes — Claude2

Reviewer: Claude2
Task: AG-FE-SW-003-SIDECAR-REVIEW
Review date: 2026-06-22
Outcome: **review_approved**

---

## Review Decision

**Approved.** The review packet (`AG-FE-SW-003-SIDECAR-REVIEW.md`) is accurate and complete as a sidecar support artifact. The packet correctly identifies:

- Spec compliance for A5, A6, E12, E13
- CI/PR status (all 3 required gates green)
- Changed files and scope
- Test coverage gaps with appropriate severity ratings
- No canonical truth mutations

---

## Notes for AG-FE-SW-003 Reviewer (Codex)

### N-1: VersionCompareCard.test.tsx is missing (§5.1)

The test gap noted in §5.1 is confirmed accurate. The acceptance criterion for AG-FE-SW-003 explicitly lists "附 UI 測試". The A5 invariant (predicted metrics must never be visually treated as observed) is implemented in render logic but has no dedicated automated guard.

**Recommendation:** Codex should require `VersionCompareCard.test.tsx` before approving AG-FE-SW-003. Option B (approve with a follow-up sidecar) is acceptable only if the test file is explicitly tracked as a required follow-up.

### N-2: hard_blockers not rendered (§5.3)

The `hard_blockers`, `temporary_assumptions`, `assessed_at`, and `valid_until` fields from `PayloadReadinessGate` are present in the TypeScript type but not rendered in the card UI. This is a design choice, not a spec violation.

**Recommendation:** AG-FE-SW-003 owner (Claude2) should clarify in the PR thread whether omitting `hard_blockers` from the card display is intentional per the spec author's design intent, given A6's emphasis on blockers.

---

## Sidecar Scope Confirmation

This sidecar task produced only support artifacts:
- `support/sidecars/AG-FE-SW-003/AG-FE-SW-003-SIDECAR-REVIEW.md`
- `support/sidecars/AG-FE-SW-003/AG-FE-SW-003-SIDECAR-REVIEW-NOTES-CLAUDE2.md` (this file)

No canonical truth files were modified. No L1 policy documents, schemas, BFF contracts, or runtime artifacts were altered.
