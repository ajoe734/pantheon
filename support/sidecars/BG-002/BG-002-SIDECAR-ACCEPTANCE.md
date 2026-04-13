# BG-002 Acceptance Packet (Sidecar)

**Parent Task**: `BG-002` — Publish research backend maturity matrix and production-path mapping
**Parent Owner**: Claude
**Parent Reviewer**: Qwen
**Parent Status**: `in_progress`
**Sidecar Owner**: Qwen
**Sidecar Reviewer**: Claude
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-13T02:35:40Z

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations.

Shared-truth sources used in this packet:
- `ai-status.json` — task registry and agent state
- `current-work.md` — derived sprint snapshot
- `ai-activity-log.jsonl` — activity history
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` — primary BG-002 artifact
- `support/sidecars/BG-002/BG-002-SIDECAR-REVIEW.md` — companion review packet

---

## 1. Dependency Map

### 1.1 Parent Dependencies

`BG-002` depends on `PLAN-002` (done). The planning session materialized the blueprint-gap convergence wave that produced this task.

### 1.2 What BG-002 Was Supposed to Deliver

The parent task title is "Publish research backend maturity matrix and production-path mapping." Shared truth and the primary artifact support the following delivered capabilities:

| Capability | Evidence |
|---|---|
| Maturity matrix for all 13 research backends | `RESEARCH_BACKEND_MATURITY_MATRIX.md` §Research Backend Maturity Matrix — 13 rows with integration status, production-path tier, owner, example strategy family, and missing proof to advance |
| Production-path mapping with explicit tiers | `RESEARCH_BACKEND_MATURITY_MATRIX.md` §Production-Path Mapping — ASCII diagram showing active path (DSPy → imitation → MLflow → registry) and ordered activation queue |
| Research problem type → primary backend mapping | `RESEARCH_BACKEND_MATURITY_MATRIX.md` §Research Problem Type → Primary Backend Mapping — 10 problem types classified |
| Cross-backend consistency assessment | `RESEARCH_BACKEND_MATURITY_MATRIX.md` §Cross-Backend Consistency Assessment — production-path gate table + activation-ready gate table + 4 inconsistency risks identified |
| GAP-02 closure response in blueprint format | `RESEARCH_BACKEND_MATURITY_MATRIX.md` §GAP-02 Response — Current Status, Existing Evidence, Why It Is a Real Gap, Proposed Owner, Source of Truth, Planned Closure Work, Acceptance Evidence, Target Wave, Production Sign-off Impact |
| Integration status codes aligned to OSS checklist | Status codes (`not-started`, `source-selected`, `version-pinned`, `adapter-started`, `criteria-defined`, `smoke-tested`, `governed`) match `OSS_INTEGRATION_CHECKLIST.md` vocabulary |
| Canonical registry vocabulary used throughout | `artifact_state` and `deployment_summary.current_stage` vocabulary consistent with REG-001/REG-003/EX-001 |

### 1.3 Downstream Consumers Waiting On BG-002

| Consumer | Task ID | Phase | Owner | Why BG-002 matters |
|---|---|---|---|---|
| Define golden replay scenario and acceptance runbook | `BG-005` | Blueprint Gap P0 | Codex | Depends on `BG-000`, `BG-001`, `BG-003`; BG-002's maturity matrix informs which backends can participate in replay scenarios |
| Publish operator acceptance matrix | `BG-006` | Blueprint Gap P1 | Qwen | Depends on `PLAN-002`; BG-002's production-path mapping informs which research operators are acceptance-ready |
| Future Qlib activation task | _(not yet materialized)_ | — | Qwen (proposed) | BG-002 documents Qlib as activation-ready with explicit OSS-003 entry gates |
| Future TRL activation task | _(not yet materialized)_ | — | Copilot (proposed) | BG-002 documents TRL as activation-ready after imitation baseline |
| Future RL stack activation task | _(not yet materialized)_ | — | Copilot (proposed) | BG-002 documents RL stack as activation-ready after Qlib plateau |

### 1.4 Readiness Verdict On Dependencies

**`BG-002` is dependency-complete.** `PLAN-002` is done, and the primary artifact is published at `RESEARCH_BACKEND_MATURITY_MATRIX.md`.

---

## 2. Acceptance Checklist for Parent Task (`BG-002`)

The parent task acceptance criteria derived from the task title, GAP-02 requirements, and the companion review packet:

| # | Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | `maturity_matrix_published` | `RESEARCH_BACKEND_MATURITY_MATRIX.md` exists at repo root with 13 backend rows | ✅ Verified |
| 2 | `integration_status_codes_correct` | Status codes match `OSS_INTEGRATION_CHECKLIST.md` vocabulary; each backend has a valid code | ✅ Verified |
| 3 | `production_path_tiers_assigned` | Three tiers (Production Research Path, Activation-Ready, Not Integrated) applied consistently | ✅ Verified |
| 4 | `production_path_mapping_explicit` | ASCII diagram shows active research path and ordered activation queue | ✅ Verified |
| 5 | `problem_type_to_backend_mapping` | 10 research problem types mapped to primary + fallback backends | ✅ Verified |
| 6 | `cross_backend_consistency_assessed` | Production-path and activation-ready gate tables completed; 4 inconsistency risks documented | ✅ Verified |
| 7 | `gap02_response_complete` | GAP-02 response covers all required blueprint format sections | ✅ Verified |
| 8 | `evidence_sources_cited` | 9+ evidence files referenced with specific contributions | ✅ Verified |
| 9 | `canonical_vocabulary_used` | `artifact_state` and `deployment_summary.current_stage` vocabulary aligns with REG-001/REG-003/EX-001 | ✅ Verified |
| 10 | `known_gaps_flagged` | Missing `integration.md`/`governance.md`, Qlib smoke test, vectorbt/statsmodels/QuantLib task materialization all flagged as follow-on | ✅ Verified |
| 11 | `review_packet_companion` | `support/sidecars/BG-002/BG-002-SIDECAR-REVIEW.md` provides structured reviewer checklist | ✅ Verified |

---

## 3. Risk Assessment

| Risk | Impact | Mitigation |
|---|---|---|
| Missing `integration.md` / `governance.md` for DSPy, imitation, MLflow | Integration evidence is incomplete per OSS checklist; audit trail gap | Flagged in Planned Closure Work table; follow-on task needed |
| OpenClaw adapter not yet smoke-tested | All downstream backends depend on orchestration semantics; cannot prove cross-backend consistency until OpenClaw is governed | Flagged as activation order #1; owner should prioritize |
| vectorbt / statsmodels / QuantLib have no materialized tasks | Blueprint names these backends but no execution tasks exist; planning gap | Flagged in Known Gaps; next planning wave should materialize |
| Ray Tune version-pinned but no governed adapter | False sense of integration readiness; `DockerfileLeanFoundationARM` pins `ray[tune]` but no adapter exists | Flagged in Inconsistency Risks §4 |
| W&B listed as activation-ready but MLflow not yet generalized | W&B should only activate after MLflow adapter is generalized across research paths | Activation order #8 (last); explicit dependency on MLflow generalization noted |

---

## 4. Execution Wave Readiness

BG-002's production-path mapping directly informs the blueprint-gap execution wave sequencing:

### Active Production Path (smoke-tested)
```
DSPy (persona policy) ──→ MLflow registry
imitation (behavior cloning) ──→ MLflow registry
MLflow (experiment lifecycle) ──→ REG-001/REG-003/EX-001 promotion chain
```

### Activation Queue (ordered)
```
1. OpenClaw    → orchestration for all paths
2. Qlib        → supervised alpha (first learning framework)
3. TRL         → preference learning (after imitation baseline)
4. FinRL/RLlib/Ray Tune → sequential RL (after Qlib plateaus)
5. vectorbt    → rapid prototyping (task materialization needed)
6. statsmodels → regime research (task materialization needed)
7. QuantLib    → derivatives (task materialization needed)
8. W&B         → optional (after MLflow generalization)
```

This ordering should be respected during BG task dispatch to avoid slipping the critical path.

---

## 5. Artifacts Inventory

| Artifact | Path | Purpose |
|---|---|---|
| Primary BG-002 artifact | `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Canonical maturity matrix + production-path mapping |
| Review packet | `support/sidecars/BG-002/BG-002-SIDECAR-REVIEW.md` | Structured reviewer checklist for Qwen |
| This acceptance packet | `support/sidecars/BG-002/BG-002-SIDECAR-ACCEPTANCE.md` | Acceptance checklist + dependency map |
| OSS integration checklist | `OSS_INTEGRATION_CHECKLIST.md` | Source of truth for status codes |
| OSS integration audit | `OSS_INTEGRATION_AUDIT.md` | Audit context for real vs. conceptual integration |
| Qlib activation criteria | `services/learning/qlib/ACTIVATION_CRITERIA.md` | Qlib entry gate (OSS-003) |
| TRL activation criteria | `services/learning/trl/ACTIVATION_CRITERIA.md` | TRL entry gate (OSS-003) |
| RL path definition | `services/learning/rl/PATH_DEFINITION.md` | RL stack entry criteria (LP-005) |
| W&B activation criteria | `services/registry/experiments/WANDB_ACTIVATION.md` | W&B entry criteria |
| OpenClaw integration | `integrations/openclaw/integration.md` | OpenClaw adapter evidence |
| OSS-002 regrade report | `integrations/oss-002/regrade_report.md` | DSPy/imitation/MLflow regrade evidence |

---

## 6. Handoff Note to Reviewer (Claude)

Claude, this packet confirms that shared truth supports the parent-task delivery story:

1. ✅ Maturity matrix published with 13 backends classified across 3 tiers
2. ✅ Production-path mapping explicit with ASCII diagram and activation queue
3. ✅ 10 research problem types mapped to primary + fallback backends
4. ✅ Cross-backend consistency assessed with 4 documented inconsistency risks
5. ✅ GAP-02 response covers all required blueprint format sections
6. ✅ Known gaps flagged as follow-on work (not blockers)
7. ✅ Companion review packet provides structured checklist for Qwen's review pass
8. ✅ No canonical truth files were modified by this sidecar

**Recommended next step**: approve this sidecar task. The parent task `BG-002` is currently `in_progress` (Claude owns, Qwen reviews). Once this sidecar is approved, the parent owner should use this packet + the review packet as support evidence when handing `BG-002` to Qwen for formal review closure.

---

*Generated by Qwen as a sidecar `acceptance_packet` helper for `BG-002`. This file is a support artifact and does not modify canonical truth.*
