# OSS-002 Regrade Report: DSPy, Imitation, MLflow

**Task:** OSS-002
**Owner:** Qwen
**Reviewer:** Codex
**Date:** 2026-04-10
**Status:** COMPLETE

## Purpose

This report regrades DSPy, imitation, and MLflow against the canonical OSS integration model
defined in `OSS_INTEGRATION_CHECKLIST.md`. Each component is assessed on:

1. Source selection
2. Version pinning
3. Dependency/integration path
4. Local adapter implementation
5. Governed I/O boundaries
6. Smoke test execution
7. Documentation completeness

## Grading Scale

| Grade | Meaning |
|---|---|
| `governed` | All 7 criteria met; production-ready within v1 scope |
| `smoke-tested` | Criteria 1-6 met; documentation gaps remain |
| `adapter-ready` | Criteria 1-4 met; governance boundaries or smoke tests incomplete |
| `partial` | Some criteria met but significant gaps remain |

---

## 1. DSPy (`services/learning/dspy/`)

**Task:** LP-001
**Grade:** `smoke-tested` → nearly `governed`

### Criterion Assessment

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Source selected | ✅ | `dspy-ai==2.4.5` pinned in `DSPY_VERSION_PIN` |
| 2 | Version pinned | ✅ | `DSPY_VERSION_PIN = "2.4.5"` in adapter.py; `requirements.txt` present |
| 3 | Dependency added | ✅ | `services/learning/dspy/requirements.txt` lists `dspy-ai>=2.4.5` |
| 4 | Local adapter built | ✅ | `adapter.py` (827 lines), `worker.py`, `smoke_test.py`, `test_adapter.py` |
| 5 | Governed I/O boundaries | ✅ | Strict FB-001 filtering, ALLOWED_ACTOR_ROLES, ALLOWED_PROMOTION_STATES, DENY_INTENTS enforcement |
| 6 | Smoke test | ✅ | Passes: 4 training + 4 evaluation examples, intent_accuracy=1.0, tool_selection_precision=1.0, deny_coverage_delta=0.0 |
| 7 | Documentation | ⚠️ | `README.md` comprehensive; missing dedicated `integration.md` and `governance.md` per canonical checklist |

### Strengths

- **Governed I/O boundary is excellent**: The `GovernedPreferenceAdapter` enforces strict role
  filtering (`operator`/`approver`), promotion state gating (`candidate`/`paper` only), and
  mandatory deny-case coverage validation.
- **Registry-ready output**: Emits both `artifact_bundle` (with `prompt_bundle.schema.json`
  validation) and `registry_entry` with full lineage, checksum, and lifecycle state.
- **Dual backend design**: Stub backend for deterministic CI + real DSPy backend with
  `BootstrapFewShot` compilation.
- **Evaluation rigor**: Tracks intent accuracy, tool selection precision, and mandatory deny
  coverage delta — all zero-violation in smoke test.

### Gaps

1. **Missing `integration.md`**: No dedicated file listing selected upstream, version pin,
   packaging notes in canonical checklist format. (Partially covered by README.md)
2. **Missing `governance.md`**: No dedicated file describing how promotion, permissions, and
   rollback apply to DSPy artifacts. (Governance logic is embedded in code but not separately
   documented per checklist §Required Evidence)
3. **Schema reference drift**: `PROMPT_BUNDLE_SCHEMA_PATH` references
   `services/control-plane/persona/lp001/prompt_bundle.schema.json` — should verify this path
   still exists after any reorganization.

### Recommendation

Upgrade to `governed` once `integration.md` and `governance.md` are added. All code-level
criteria are already met and verified.

---

## 2. Imitation (`services/learning/imitation/`)

**Task:** LP-002
**Grade:** `smoke-tested` → nearly `governed`

### Criterion Assessment

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Source selected | ✅ | `HumanCompatibleAI/imitation` pinned |
| 2 | Version pinned | ✅ | `IMITATION_VERSION_PIN = "1.0.1"` in adapter.py; `requirements.txt` present |
| 3 | Dependency added | ✅ | `services/learning/imitation/requirements.txt` lists `imitation>=1.0.1` |
| 4 | Local adapter built | ✅ | `adapter.py` (450+ lines), `worker.py`, `smoke_test.py`, `test_adapter.py` |
| 5 | Governed I/O boundaries | ✅ | Strict FB-001 trajectory filtering, ALLOWED_ACTOR_ROLES, ALLOWED_PROMOTION_STATES, ELIGIBLE_DECISIONS |
| 6 | Smoke test | ✅ | Passes: 2 trajectories, 4 transitions, training_accuracy=1.0, action_coverage_ratio=1.0 |
| 7 | Documentation | ⚠️ | `README.md` comprehensive; missing dedicated `integration.md` and `governance.md` |

### Strengths

- **Governed trajectory pipeline**: `GovernedTrajectoryAdapter` filters on actor_role, decision
  type (approve/edit only), promotion_state (candidate/paper), and observation dimensionality
  consistency.
- **BC-first design**: v1 is intentionally behavior-cloning only; DAgger/GAIL/AIRL are
  explicitly deferred — clean scope boundary.
- **Registry alignment**: Output includes `artifact_type=model_artifact`, `model_family=imitation_policy`,
  full lineage to source datasets and strategy spec, and REG-001-compatible checksum.
- **Dual backend**: Stub nearest-centroid for CI + real `imitation` BC backend.

### Gaps

1. **Missing `integration.md`**: Same pattern as DSPy — README covers the content but canonical
   checklist expects a separate file.
2. **Missing `governance.md`**: Governance logic is embedded in code (ALLOWED_ACTOR_ROLES,
   ELIGIBLE_DECISIONS, etc.) but not separately documented per checklist format.
3. **Observation dimension validation**: Adapter enforces consistent dimensionality across
   trajectories but does not document minimum recommended dimensionality or feature engineering
   expectations for production use.

### Recommendation

Upgrade to `governed` once `integration.md` and `governance.md` are added. All code-level
criteria are met and verified.

---

## 3. MLflow (`services/registry/experiments/`)

**Task:** LP-003
**Grade:** `smoke-tested` → nearly `governed`

### Criterion Assessment

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Source selected | ✅ | MLflow 2.11.0 → updated to 3.10.1 in code |
| 2 | Version pinned | ✅ | `MLFLOW_VERSION_PIN = "3.10.1"` in adapter.py |
| 3 | Dependency added | ✅ | Version pin in code; README documents installation path |
| 4 | Local adapter built | ✅ | `adapter.py` (350+ lines), `smoke_test.py`, `test_adapter.py` |
| 5 | Governed I/O boundaries | ✅ | Strict lifecycle validation, rollback metadata enforcement, lineage requirements |
| 6 | Smoke test | ✅ | Passes: memory backend, registry→MLflow→promoted_metadata round-trip |
| 7 | Documentation | ⚠️ | `README.md` comprehensive; missing dedicated `integration.md` and `governance.md` |

### Strengths

- **Registry-first design**: MLflow is explicitly a mirror, not a source of truth. The
  `RegistryExperimentAdapter` maps governed registry entries into MLflow runs and returns
  `promoted_metadata` back to the registry — correct authority direction.
- **Rollback enforcement**: `live` entries are rejected without proper rollback metadata
  (REG-003 alignment). Both `metadata.rollback` and `rollback_target_registry_id` forms
  supported.
- **Alias policy**: Clean lifecycle→alias mapping (`candidate`→`["candidate"]`, etc.), with
  `draft` entries producing no promoted metadata.
- **Lineage validation**: `live`/`paper`/`candidate` entries require lineage pointing to a run,
  dataset, or strategy spec before sync.
- **Execution projection paths**: Handoff includes canonical `openclaw/registry/{strategy_id}/{version}/`
  paths aligned with EX-001.

### Gaps

1. **Version pin discrepancy**: README says `mlflow==3.10.1` but `OSS_INTEGRATION_CHECKLIST.md`
  says "Selected MLflow 2.11.0". The code (`MLFLOW_VERSION_PIN = "3.10.1"`) is authoritative —
  the checklist is stale.
2. **Missing `integration.md`**: Same pattern as DSPy/imitation.
3. **Missing `governance.md`**: Governance rules (rollback enforcement, lifecycle validation,
   lineage requirements) are in code but not separately documented.
4. **W&B deferred status**: Documented in README as deferred, but no activation criteria
  document exists for the W&B alternative path (should be covered by OSS-003).

### Recommendation

Upgrade to `governed` once `integration.md` and `governance.md` are added. Version pin in
checklist should be updated from 2.11.0 to 3.10.1.

---

## Cross-Cutting Findings

### What's Working Well

1. **All three components pass smoke tests** — no dead code paths.
2. **Governance is embedded in code** — ALLOWED_ACTOR_ROLES, ALLOWED_PROMOTION_STATES, lifecycle
   validation, and registry alignment are all enforced at runtime, not just documented.
3. **Dual backend pattern** — every component has a stub backend for deterministic CI plus an
   optional real upstream backend for production workers.
4. **Registry lifecycle alignment** — all three emit `registry_entry` objects with proper
   `lifecycle_state`, `lineage`, `checksum`, and `storage_ref` fields.

### Systemic Gaps

1. **Documentation format mismatch**: The canonical checklist expects separate `integration.md`,
   `governance.md`, and `smoke_test.md` files per component. All three components instead have
   comprehensive `README.md` files that cover the same content but in a different structure.
   - **Recommendation**: Either (a) add symlink/copy files to match checklist format, or (b) update
     the checklist to accept `README.md` as the canonical documentation location.
2. **Checklist staleness**: `OSS_INTEGRATION_CHECKLIST.md` still shows DSPy, imitation, and MLflow
   as `not-started` or early-stage despite all three having full implementations with passing tests.
3. **Governance documentation in code vs. policy files**: Governance rules are enforced in
   adapter code but not extracted into standalone policy documents that operators can read without
   examining Python source.

---

## Updated Component Status

| Component | Previous Status | New Status | Rationale |
|---|---|---|---|
| `DSPy` | `not-started` | `smoke-tested` | Full adapter, version pin, governed I/O, passing smoke test; missing checklist-format docs |
| `imitation` | `not-started` | `smoke-tested` | Full adapter, version pin, governed I/O, passing smoke test; missing checklist-format docs |
| `MLflow` | `source-selected` | `smoke-tested` | Full adapter, version pin (3.10.1), governed I/O, passing smoke test; missing checklist-format docs; checklist version stale |
| `TRL` | `not-started` | `not-started` | Still deferred; LP-004 exists as separate task |
| `Qlib` | `not-started` | `not-started` | Still deferred; covered by OSS-003 |
| `FinRL` | `not-started` | `not-started` | Still deferred; covered by OSS-003 |
| `RLlib` | `not-started` | `not-started` | Still deferred; LP-005 covers RL path |
| `Ray Tune` | `version-pinned` | `version-pinned` | Still waiting for adapter path; LP-005 covers RL path |
| `W&B` | `not-started` | `not-started` | Still deferred; covered by OSS-003 |

---

## Remaining Follow-ups

| ID | Follow-up | Priority | Owner | Depends On |
|---|---|---|---|---|
| `OSS-002A` | Add `integration.md` and `governance.md` for DSPy, imitation, MLflow | Low | TBD | None |
| `OSS-002B` | Update OSS_INTEGRATION_CHECKLIST.md MLflow version from 2.11.0 to 3.10.1 | Trivial | TBD | None |
| `OSS-003` | Define activation criteria for deferred Qlib, TRL, RL paths | Medium | Gemini | OSS-002 |

---

## Verification

All smoke tests verified at time of regrade:

```bash
# DSPy (LP-001)
python3 services/learning/dspy/smoke_test.py
# → intent_accuracy=1.0, tool_selection_precision=1.0, deny_coverage_delta=0.0

# Imitation (LP-002)
python3 services/learning/imitation/smoke_test.py
# → training_accuracy=1.0, action_coverage_ratio=1.0, num_trajectories=2, num_transitions=4

# MLflow/Experiments (LP-003)
python3 services/registry/experiments/smoke_test.py
# → LP-003 smoke test passed with backend=memory
```
