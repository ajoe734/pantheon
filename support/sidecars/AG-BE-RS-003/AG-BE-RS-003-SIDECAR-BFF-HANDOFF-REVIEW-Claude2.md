# Review: AG-BE-RS-003-SIDECAR-BFF-HANDOFF

> Reviewer: Claude2  
> Reviewed artifact: `support/sidecars/AG-BE-RS-003/AG-BE-RS-003-SIDECAR-BFF-HANDOFF.md`  
> Commit reviewed: f6916e31  
> Date: 2026-06-20  
> Verdict: **APPROVED**

---

## Summary

The handoff packet is complete, accurately scoped, and contains actionable specs for the BFF implementer and frontend team. No canonical files were modified. This is a clean support artifact.

---

## Findings

### Strengths

1. **Gap coverage is complete** — RS-GAP-01 through RS-GAP-06 map one-to-one to the missing BFF surface for `agora-expert-consult`. RS-GAP-05 (empty router stub) and RS-GAP-06 (context-build status via field rather than separate route) are practical and correct.

2. **Authority chain distinction is sound** — §3.3 correctly separates `/bff/agora/research/expert-consult/...` (research-workbench, OpenClaw skill projection) from `/api/v1/consult/...` (governance-layer, Persona Plane projection per PERSONA_RUNTIME_MODEL.md §13–14). No conflation.

3. **Privacy invariants are correctly enforced** — `rawPromptIncluded: false` and `userIdentityIncluded: false` are declared as hard invariants with BFF returning 500/POLICY_SUPPRESSED on violation. This matches the skill's privacy boundary rule.

4. **Disagreements UX requirement is correct** — §5.5 preserves the skill's *"disagreement 必須保留，不可由僕人偷偷消除"* rule. The requirement to display, not merge, disagreements is explicit.

5. **Status enum is complete** — `dispatched | partial | complete | degraded | failed` covers all lifecycle transitions including the degraded path (expert unavailable) and failure path.

6. **Idempotency is correctly specified** — `X-Idempotency-Key` on RS-GAP-01 with `replayed: true` flag in response prevents duplicate dispatch on frontend retry. Well-placed requirement.

7. **Polling guidance is reasonable** — 2 s for first 30 s, back-off to 5 s, max 120 s before timeout. Correct stop conditions (`complete | degraded | failed`).

8. **Privacy manifest inline recommendation** — §5.4 correctly recommends inlining `privacyManifest` in RS-GAP-02 rather than a separate route. Avoids over-engineering a separate audit fetch for most cases.

9. **Context bundle scope allowlist** — `["strategySpecRef", "question", "relevantSymbols", "evidenceRefs", "dataCutoff"]` matches the skill contract. 400 CONTEXT_SCOPE_VIOLATION error code is clear.

10. **Verification checklist is actionable** — V-01 through V-08 map directly to the gaps and privacy invariants. V-07 (code review) is the right method for a structural check.

11. **"Do not modify" list is protective** — Explicitly names `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `CONSULTATION_SURFACE_CONTRACT.md`, `PERSONA_RUNTIME_MODEL.md`, and `main.py`. These are the right boundaries to protect.

### Minor Observations (non-blocking)

- RS-GAP-03 and RS-GAP-04 are correctly marked optional; the implementer can defer unless committee mode with many participants is required.
- The packet's "read-only guidance" note in §7 is accurate — it does not attempt to create the implementation files, only lists what the implementer should create.
- The `status: "failed"` arm in §5.2 references `blocking_reasons` but does not specify the shape of that field. The implementer should define it when adding error handling.

---

## Scope Verification

| Check | Result |
|---|---|
| No L1 canonical files modified | ✓ Confirmed |
| No BFF `main.py` modified | ✓ Confirmed |
| Support artifact only | ✓ Confirmed |
| Sidecar docs scoped to `support/sidecars/AG-BE-RS-003/` | ✓ Confirmed |
| Parent task (AG-BE-RS-003) determines absorption | ✓ Correctly delegated |

---

## Verdict

**Approved.** The packet is ready for parent task absorption. No blocking issues. The one minor observation about `blocking_reasons` shape is left for the implementer to resolve during implementation — it does not block this review cycle.

Returning to owner (Claude) for closeout.
