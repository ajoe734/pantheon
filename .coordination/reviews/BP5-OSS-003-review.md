# Review: BP5-OSS-003 — Convert DSPy, imitation, and MLflow rows into runnable adapters or explicit defer proofs

Reviewer: Claude  
Date: 2026-04-15  
Outcome: **APPROVED**

---

## Acceptance Criteria Assessment

| Criterion | Result |
|---|---|
| DSPy runnable adapter exists | PASS — `services/learning/dspy/adapter.py` is real, non-placeholder code |
| imitation runnable adapter exists | PASS — `services/learning/imitation/adapter.py` is real, non-placeholder code |
| MLflow runnable adapter exists | PASS — `services/registry/experiments/adapter.py` is real, non-placeholder code |
| Canonical evidence packs present | PASS — `integrations/{dspy,imitation,mlflow}/{integration,governance,smoke_test}.md` all present |
| Smoke tests pass | PASS — all three smoke scripts exit 0 with expected output (verified live) |
| Unit coverage passes | PASS — 3/3/4 tests OK for DSPy/imitation/MLflow |
| Checklist upgraded to `governed` | PASS — OSS_INTEGRATION_CHECKLIST.md rows for DSPy, imitation, MLflow all read `governed` |
| Maturity matrix consistent | PASS — RESEARCH_BACKEND_MATURITY_MATRIX.md shows all three as Production Research Path with correct gates checked |

---

## Specific Findings

### DSPy

- `GovernedPreferenceAdapter` enforces `actor_role`, `promotion_state`, and `event_type` gates before any optimization run.
- Deny-first regression checks are correctly placed: `deny_coverage_delta = 0.0`, `mandatory_deny_violation_count = 0`.
- Dual backend design (stub + real DSPy) is appropriate — CI-safe without hiding the real path.
- Output lifecycle starts at `draft`; `direct_live_influence = false` in governance metadata. Authority boundary is clean.

### imitation

- `GovernedTrajectoryAdapter` correctly filters by `actor_role`, `decision`, and `promotion_state` before training.
- BC-only scope is explicit; DAgger/GAIL/AIRL all listed as explicitly deferred with note that they need separate smoke evidence.
- Artifact family `imitation_policy`, lifecycle starts `draft`, lineage includes `source_dataset_refs`. No shortcut to live path.
- Stub nearest-centroid backend is deterministic; real imitation backend is cleanly separated.

### MLflow

- Registry-first authority is clear: MLflow mirrors Pantheon metadata, does not define promotion truth.
- Lineage validation and rollback requirement for `live` entries is enforced at the adapter layer (`ExperimentSyncError` on violation).
- `artifact_handoff.json` projection preserves governed storage paths (`openclaw/registry/…`), keeping MLflow from becoming the artifact store.
- W&B deferred status is correctly documented in `services/registry/experiments/WANDB_ACTIVATION.md`.

### Checklist and Maturity Matrix

- OSS_INTEGRATION_CHECKLIST.md: all three rows correctly read `governed` with evidence pointers.
- RESEARCH_BACKEND_MATURITY_MATRIX.md: cross-backend consistency table is complete and accurate. All three production-path backends show full checkmarks.
- OpenClaw remaining at `adapter-started` is correctly noted as a gap — no false inflation.
- Inconsistency risks (Ray Tune version-pinned without adapter; vectorbt/statsmodels/QuantLib with no tasks) are documented honestly.

---

## Notes

- No concerns. Evidence packs are canonical, adapter code is real, smoke runs are live-verified.
- Follow-on work (OpenClaw gateway adapter, Qlib activation, vectorbt task materialization) is correctly scoped out of this task.
- Owner may finalize to `done`.
