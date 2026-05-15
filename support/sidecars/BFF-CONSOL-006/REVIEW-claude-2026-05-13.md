# BFF-CONSOL-006 Review — Role Vocabulary Mapping Doc

**Reviewer:** Claude
**Date:** 2026-05-13
**Task:** BFF-CONSOL-006
**Artifact:** docs/bff/role-vocabulary-mapping-2026-05-13.md
**Outcome:** APPROVED

---

## Prior Round Issues (Now Resolved)

Two issues were raised in the first review pass:

1. **Line 144 (capability override claim):** Originally stated "explicit capability claims override the fallback map." The corrected text now reads: "auth-service claims extend the fallback map, not replace it" with the correct formula `capabilities = dedupe([*claim_caps, *_capabilities_for_identity(identity)])`. Verified against `main.py:3067–3071` — both sets are merged via `_dedupe_nonblank_strings`, with claim caps prepended. ✓

2. **Line 150 (`_RISK_GATE_ROLES` reference):** Non-existent variable name removed. Section 7 now correctly cites `_MUTATION_APPROVAL_ROLES`, `_MUTATION_REJECTION_ROLES`, and `_require_admin_mfa`. ✓

---

## Source Verification

Checked against live `main.py` and `models.py`:

| Document Claim | Source Location | Status |
|---|---|---|
| `_READ_ROLES = {"operator","approver","admin","reviewer"}` | `main.py:2731` | ✓ |
| `_WRITE_ROLES = {"operator","approver","admin","reviewer"}` | `main.py:2732` | ✓ |
| `admin` capabilities = all `EVIDENCE_CAPABILITY_MAP.values()` | `main.py:2762`, `models.py:544–560` | ✓ |
| `approver` caps: `approval.read, postmortem.read, policy.read` | `main.py:2763–2766` | ✓ |
| `operator` caps: `runtime.read, risk.incident.read, risk.alert.read, artifact.read` | `main.py:2768–2773` | ✓ |
| `reviewer` caps: `approval.read, strategy.view, persona.view` | `main.py:2774–2778` | ✓ |
| `analyst` caps: `metric.read, job.read, audit.read` | `main.py:2779–2783` | ✓ |
| `viewer` caps: none | `main.py:2784` | ✓ |
| Mutation approval roles (low/medium/high) | `main.py:824–828` | ✓ |
| Mutation rejection roles (low/medium/high) | `main.py:830–834` | ✓ |
| Admin+MFA for `LiquidateAll`/`IssueSafeMode` | `main.py:897,1536,1568` | ✓ |
| Capabilities deduped as `[*claim_caps, *_capabilities_for_identity()]` | `main.py:3067–3071` | ✓ |

---

## Acceptance Criteria Check

- [x] 表格列出 backend canonical role — Section 2 (admin/approver/operator/reviewer/analyst/viewer)
- [x] 表格列出 frontend mock role — Section 3 (platform_admin/portfolio_manager/research_lead/ops/viewer)
- [x] 對應關係明確 — Section 4 Role Mapping Table with status + consumer rule columns
- [x] 涵蓋 MeResponse.roles 預期輸出 — Section 5 with TypeScript type skeleton and two example JSON responses
- [x] 標出哪些 frontend-only role 屬於 deprecated — Section 9 lists all 4 deprecated roles with replacements
- [x] doc 在 PR review 中由 Claude 簽核 — this review

---

## Notes

- Section 8 (session_kind) is correctly marked as planned (BFF-CONSOL-013 dependency). No issues.
- The `viewer` name-collision note in Section 4 and Section 9 is accurate and useful for migration.
- EVIDENCE_CAPABILITY_MAP capabilities list in Section 6 matches `models.py:544–560` exactly.

Review complete. No further changes required.
