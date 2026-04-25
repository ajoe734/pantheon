# Review: BP5-SVC-014-SIDECAR-BFF-HANDOFF — BFF and Frontend Handoff Packet

**Reviewer:** Claude
**Task:** BP5-SVC-014-SIDECAR-BFF-HANDOFF
**Date:** 2026-04-15
**Decision:** APPROVED

---

## Acceptance Criteria Verification

### AC-1: Create support artifacts only

**PASSED.**

Only `support/sidecars/BP5-SVC-014/BP5-SVC-014-SIDECAR-BFF-HANDOFF.md` was created. No L1 policy files, canonical truth documents, core runtime, registry, or governance implementations were modified.

### AC-2: Do not edit canonical truth

**PASSED.**

Git status shows no changes to any L1 canonical file. The packet is clearly marked as a support artifact throughout and explicitly states it does not modify canonical truth.

### AC-3: Hand off the packet to the assigned reviewer

**PASSED.**

Handoff was written via `scripts/ai-status.sh handoff` and is recorded in `ai-status.json` with `status: pending` targeting Claude. The task is in `review` status with `reviewer: Claude`.

---

## Code-Backed Claim Verification

### Claim: PS-01 to PS-06 persona surfaces are live in main.py

**CONFIRMED.** Routes verified directly:
- `GET /api/v1/personas` — present (`main.py:452`)
- `GET /api/v1/personas/{persona_id}` — present (`main.py:477`)
- `GET /api/v1/personas/{persona_id}/sessions` — present (`main.py:504`)
- `GET /api/v1/personas/{persona_id}/teaching` — present (`main.py:564`)
- `GET /api/v1/personas/{persona_id}/capabilities` — present (`main.py:593`)
- `GET /api/v1/operator/persona-management/{persona_id}` — present (`main.py:1220`)

### Claim: CS-01 to CS-06 consultation routes are NOT present in main.py

**CONFIRMED.** Grep for "consultation" and "consult" in `main.py` returns no matches. The gap is real.

### Claim: Tests pass

**CONFIRMED.** Ran `python3 services/control-plane/bff/test_persona_management.py` — all 12 assertions pass, covering PS-02, CP-03, CP-04, PS-03, PS-05, allowed-actions, and persistence reload.

### Claim: PS-01 RBAC mismatch (contract says viewer-readable, code requires operator+)

**CONFIRMED.** `_READ_ROLES = {"operator", "approver", "admin", "reviewer"}` at `main.py:299`. "viewer" is absent from the set. The contract divergence is real and correctly flagged.

---

## Packet Quality Assessment

| Section | Assessment |
|---|---|
| Implementation snapshot (§2) | Accurate; route table and read-store helpers verified against live code |
| Consultation gap matrix (§3) | Accurate; CS-01 to CS-06 are genuinely absent from code |
| Operator journey guidance (§4) | Actionable and safe; correctly warns against consultation routes and viewer tokens |
| Suggested implementation sequence (§5) | Practical and low-risk; does not propose canonical overrides |
| Verification evidence (§6) | Honest; clearly distinguishes what the tests prove and what they do not |

The packet correctly scopes the real remaining parent-task work: consultation read-store primitives and CS-01 to CS-06 route exposure. It does not overstate the persona side or understate the consultation gap.

---

## Observations (Non-blocking)

1. **`snapshot=preferred` is best-effort only.** The packet correctly flags that cross-surface snapshot alignment is not enforced. This should be revisited when the consultation surfaces land so that persona + consultation reads can be coherently snapshotted together.

2. **PS-01 viewer RBAC.** The mismatch is explicitly flagged. The parent task (BP5-SVC-014) should either implement viewer-scoped `PS-01` or add a contract note deferring it. Either path is acceptable; what is not acceptable is silent divergence between `BFF_API_CONTRACT.md` and `main.py`.

3. **No HTTP-layer smoke tests for PS-01 to PS-06.** The existing focused tests are read-store level only. Adding at least one TestClient-backed HTTP test per surface (or a parametrized sweep) would strengthen the evidence base for the parent task's acceptance.

---

## Verdict

The sidecar achieves its purpose. It delivers an accurate, code-verified snapshot of the BFF reality, a clear consultation gap matrix, safe frontend handoff guidance, and a concrete suggested sequence for the parent owner. No canonical truth was touched. All three acceptance criteria are met.

**Approved and returned to Codex for finalization.**
