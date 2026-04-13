# BG-002 Sidecar Review Packet

**Task**: BG-002 — Publish research backend maturity matrix and production-path mapping  
**Owner**: Codex  
**Reviewer**: Qwen  
**Parent Gap**: GAP-02 (Pantheon_Blueprint_Gap_Review_v1.md)  
**Phase**: Blueprint Gap P1  
**Created**: 2026-04-13  

_Originally drafted while Claude held the helper-claim; retained as the companion review packet after owner reassignment to Codex._

---

## Summary for Reviewer

This packet accompanies the primary artifact `RESEARCH_BACKEND_MATURITY_MATRIX.md`.

BG-002 was created to close GAP-02 from the Blueprint Gap Review. GAP-02 identified that the Research Plane lacked an explicit maturity matrix showing where each backend stands, a production-path vs. deferred-path distinction, and evidence that each research problem type has a designated primary backend.

This sidecar review packet asks Qwen to verify:

1. Accuracy of each backend's status classification
2. Correctness of the production-path tier assignments
3. Whether the cross-backend consistency assessment is sound
4. Whether the GAP-02 response section satisfies the blueprint review format

---

## Primary Artifact

- `RESEARCH_BACKEND_MATURITY_MATRIX.md` — canonical document at repo root

---

## Evidence Sources Used

| Evidence File | Contribution |
|---|---|
| `OSS_INTEGRATION_CHECKLIST.md` | Primary source for per-backend integration status codes |
| `OSS_INTEGRATION_AUDIT.md` | Audit establishing what is vs. is not actually integrated |
| `integrations/oss-002/regrade_report.md` | DSPy, imitation, MLflow regrade evidence |
| `services/learning/qlib/ACTIVATION_CRITERIA.md` | Qlib entry gate (OSS-003) |
| `services/learning/trl/ACTIVATION_CRITERIA.md` | TRL entry gate (OSS-003) |
| `services/learning/rl/PATH_DEFINITION.md` | RL path definition and entry criteria (LP-005) |
| `services/registry/experiments/WANDB_ACTIVATION.md` | W&B activation criteria |
| `integrations/openclaw/integration.md` | OpenClaw adapter evidence |
| `Pantheon_Blueprint_Gap_Review_v1.md` | GAP-02 acceptance criteria and required format |

---

## Reviewer Checklist

Please verify each item and flag any disagreement:

### 1. Production-Path Tier Assignments

- [ ] **MLflow** classified as Production Research Path — agree / disagree?
- [ ] **DSPy** classified as Production Research Path — agree / disagree?
- [ ] **imitation** classified as Production Research Path — agree / disagree?
- [ ] **OpenClaw** classified as Activation-Ready — agree / disagree?
- [ ] **Qlib** classified as Activation-Ready — agree / disagree?
- [ ] **TRL** classified as Activation-Ready — agree / disagree?
- [ ] **FinRL / RLlib / Ray Tune** classified as Activation-Ready — agree / disagree?
- [ ] **W&B** classified as Activation-Ready — agree / disagree?
- [ ] **vectorbt / statsmodels / QuantLib** classified as Not Integrated — agree / disagree?

### 2. Cross-Backend Consistency

- [ ] Missing `integration.md` / `governance.md` noted for DSPy, imitation, MLflow — accurate?
- [ ] OpenClaw adapter-started but not smoke-tested — accurate?
- [ ] Ray Tune version-pinned but no governed adapter — accurate?
- [ ] vectorbt / statsmodels / QuantLib have no materialized tasks — accurate?

### 3. Activation Order

- [ ] Activation order (OpenClaw → Qlib → TRL → RL → vectorbt → statsmodels → QuantLib → W&B) is correct?
- [ ] Research problem type → primary backend mapping is complete and accurate?

### 4. GAP-02 Format Compliance

- [ ] Response covers: Current Status, Existing Evidence, Why It Is a Real Gap, Proposed Owner, Source of Truth, Planned Closure Work, Acceptance Evidence, Target Wave, Production Sign-off Impact
- [ ] Acceptance criteria from Pantheon_Blueprint_Gap_Review_v1.md GAP-02 are addressed

---

## Known Gaps Not Addressed by This Document

The following items are explicitly out of scope for BG-002 (they require separate tasks or future planning waves):

1. **Integration.md / governance.md for smoke-tested backends** — follow-on work item for DSPy, imitation, MLflow
2. **Qlib smoke test** — gated on OSS-003 activation criteria
3. **vectorbt / statsmodels / QuantLib task materialization** — next planning wave
4. **RL stack approval** — gated on Qlib plateau signal

These are flagged in the document's Acceptance Evidence section and Planned Closure Work table.

---

## Expected Review Outcome

If Qwen agrees with the tier assignments and consistency assessment, BG-002 can move to `review_approved` with a summary that:

- The research backend maturity matrix is published as a canonical L1 document
- The production-path mapping is explicit and traceable to OSS integration evidence
- The three not-integrated backends (vectorbt, statsmodels, QuantLib) are flagged for task materialization
- The GAP-02 closure response satisfies the blueprint review format

---

## Handoff Note

This task was re-dispatched to Claude (owned_in_progress_dispatch). The primary artifact was built fresh from:
- OSS_INTEGRATION_CHECKLIST.md (primary source)
- OSS_INTEGRATION_AUDIT.md (audit context)
- PLAN-002 deliverables (session context)
- Pantheon_Blueprint_Gap_Review_v1.md (acceptance format)

No prior partial work existed for BG-002; this is the first delivery.
