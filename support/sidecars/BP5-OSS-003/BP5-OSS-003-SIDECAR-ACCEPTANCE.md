# BP5-OSS-003 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `BP5-OSS-003-SIDECAR-ACCEPTANCE`  
**Helper parent:** `BP5-OSS-003` - Convert DSPy, imitation, and MLflow rows into runnable adapters or explicit defer proofs  
**Parent owner:** `Gemini`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex`  
**Reviewer:** `Claude`  
**Date:** `2026-04-15`  
**Status:** `done` — review_approved by Claude (2026-04-15); closed by Codex (2026-04-15)

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, registry semantics, or checklist truth. It records live repo evidence for the
> BP5-OSS-003 support slice.

---

## 1. Purpose

This packet gives the reviewer and parent owner a compact handoff for BP5-OSS-003:

1. a criterion-by-criterion acceptance checklist
2. a runnable evidence snapshot for `DSPy`, `imitation`, and `MLflow`
3. a dependency map for downstream activation work
4. a coordination note about the current planning-vs-status gap

The key outcome is simple: **no explicit defer proof is needed for these three rows anymore**.
All three currently satisfy BP5-OSS-003 through runnable adapter paths with executable smoke and
unit coverage.

---

## 2. Acceptance Checklist

Formal acceptance criteria from the planning session:

- AC-1: `each of DSPy, imitation, and MLflow has either a runnable adapter path or an explicit defer proof tied to evidence`
- AC-2: `checklist maturity no longer relies on stale or purely narrative evidence`

### AC-1: Runnable adapter path or explicit defer proof

| Row | Repo state | Evidence of runnable path | 2026-04-15 verification | Status |
|---|---|---|---|---|
| `DSPy` | `governed` in `OSS_INTEGRATION_CHECKLIST.md` | `integrations/dspy/integration.md`, `integrations/dspy/governance.md`, `integrations/dspy/smoke_test.md`; adapter surface in `services/learning/dspy/adapter.py` with `DSPY_VERSION_PIN = "2.4.5"` and `run_dspy_workflow()` | `python3 services/learning/dspy/smoke_test.py` passed; `python3 -m unittest discover -s services/learning/dspy -p 'test_*.py'` ran 3 tests, `OK` | PASS |
| `imitation` | `governed` in `OSS_INTEGRATION_CHECKLIST.md` | `integrations/imitation/integration.md`, `integrations/imitation/governance.md`, `integrations/imitation/smoke_test.md`; adapter surface in `services/learning/imitation/adapter.py` with `IMITATION_VERSION_PIN = "1.0.1"` and `run_imitation_workflow()` | `python3 services/learning/imitation/smoke_test.py` passed; `python3 -m unittest discover -s services/learning/imitation -p 'test_*.py'` ran 3 tests, `OK` | PASS |
| `MLflow` | `governed` in `OSS_INTEGRATION_CHECKLIST.md` | `integrations/mlflow/integration.md`, `integrations/mlflow/governance.md`, `integrations/mlflow/smoke_test.md`; adapter surface in `services/registry/experiments/adapter.py` with `MLFLOW_VERSION_PIN = "3.10.1"`, `InMemoryMlflowBackend`, and `RegistryExperimentAdapter.from_tracking_uri(...)` | `python3 services/registry/experiments/smoke_test.py` passed; `python3 -m unittest discover -s services/registry/experiments -p 'test_*.py'` ran 4 tests, `OK` | PASS |

**AC-1 assessment:** PASSED. BP5-OSS-003 no longer needs defer-proof handling for these rows because
all three are backed by runnable local adapters and executable verification paths.

### AC-2: Checklist maturity is backed by fresh evidence

| Check | Evidence | Status |
|---|---|---|
| Checklist rows are no longer stale placeholders | `OSS_INTEGRATION_CHECKLIST.md` now marks `DSPy`, `imitation`, and `MLflow` as `governed` and points at the exact `integrations/*/{integration,governance,smoke_test}.md` evidence family | PASS |
| Evidence is executable, not narrative-only | All three rows have live code under `services/learning/` or `services/registry/experiments/`, plus smoke entrypoints and unit suites re-run on 2026-04-15 | PASS |
| Deferred-vs-runnable distinction is honest | `W&B` remains `criteria-defined` in the same checklist and is still gated by `services/registry/experiments/WANDB_ACTIVATION.md`, showing the checklist is distinguishing runnable rows from deferred rows rather than promoting everything uniformly | PASS |

**AC-2 assessment:** PASSED. The checklist state is now tied to re-runnable implementation evidence
instead of legacy regrade prose.

---

## 3. Executable Evidence Snapshot

### 3.1 Smoke and unit results re-run for this sidecar

| Framework | Smoke result | Unit result |
|---|---|---|
| `DSPy` | `LP-001 smoke test complete`, backend `stub_bootstrap_fewshot`, `registry_id=reg-persona-router-prompt-bundle-0.1.0`, storage path `learning/dspy/persona-router/0.1.0/prompt_bundle.json`, `intent_accuracy=1.0`, `tool_selection_precision=1.0`, `deny_coverage_delta=0.0`, `mandatory_deny_violation_count=0` | `Ran 3 tests` -> `OK` |
| `imitation` | `LP-002 smoke test complete`, backend `stub_bc`, `registry_id=reg-alpha-mean-reversion-imitation-0.1.0`, storage path `learning/imitation/alpha-mean-reversion/0.1.0/artifact_bundle.json`, `training_accuracy=1.0`, `action_coverage_ratio=1.0` | `Ran 3 tests` -> `OK` |
| `MLflow` | `LP-003 smoke test passed with backend=memory: registry metadata mapped into experiment metadata.` | `Ran 4 tests` -> `OK` |

### 3.2 Why these rows count as runnable

| Row | Runnable proof |
|---|---|
| `DSPy` | Dedicated worker image and requirements file, governed adapter implementation, deterministic stub smoke, optional real upstream backend path, registry-ready `prompt_bundle` output |
| `imitation` | Dedicated worker image and requirements file, governed BC adapter implementation, deterministic stub smoke, optional real upstream `imitation` backend path, registry-ready `imitation_policy` output |
| `MLflow` | Registry-first adapter implementation, deterministic in-memory smoke backend, real `from_tracking_uri(...)` execution path for self-hosted tracking, governed `promoted_metadata` return path |

---

## 4. Dependency Map

### 4.1 Formal upstream dependency

| Dependency | Status | Relevance |
|---|---|---|
| `BP5-OSS-001` | done | BP5-OSS-003 was planned to build on the already-governed OSS integration pattern and evidence discipline established by BP5-OSS-001 |

### 4.2 Direct downstream dependency

| Downstream task or artifact | Depends on BP5-OSS-003 for | Evidence |
|---|---|---|
| `BP5-OSS-004` | keeping only truly deferred rows in `criteria-defined` state while `DSPy`, `imitation`, and `MLflow` are treated as runnable/governed | `planning-session.json` shows `BP5-OSS-004` depends on `BP5-OSS-003` |
| `services/learning/trl/ACTIVATION_CRITERIA.md` | a real imitation baseline and MLflow experiment tracking substrate before TRL activation | doc explicitly requires active imitation baseline and MLflow-compatible experiment tracking |
| `services/registry/experiments/WANDB_ACTIVATION.md` | MLflow-first stabilization before any alternative backend is activated | doc explicitly defers `W&B` until the current MLflow-first adapter surface is generalized |

### 4.3 Repo-local evidence anchors

| Row | Key code/docs that now serve as evidence anchors |
|---|---|
| `DSPy` | `services/learning/dspy/adapter.py`, `services/learning/dspy/smoke_test.py`, `services/learning/dspy/test_adapter.py`, `integrations/dspy/` |
| `imitation` | `services/learning/imitation/adapter.py`, `services/learning/imitation/smoke_test.py`, `services/learning/imitation/test_adapter.py`, `integrations/imitation/` |
| `MLflow` | `services/registry/experiments/adapter.py`, `services/registry/experiments/smoke_test.py`, `services/registry/experiments/test_adapter.py`, `integrations/mlflow/` |

---

## 5. Coordination Note

There is one durable coordination anomaly worth recording for the reviewer and parent owner:

- `BP5-OSS-003` exists in `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/planning-session.json`
- `BP5-OSS-003` is also referenced in `execution-materialization.md`
- but **current `ai-status.json` does not contain a standalone `BP5-OSS-003` task entry**
- current `ai-status.json` only contains:
  - this helper task: `BP5-OSS-003-SIDECAR-ACCEPTANCE`
  - the downstream dependency reference from `BP5-OSS-004`

This packet does not attempt to repair that state mismatch. It records it so `Claude` and the
parent owner `Gemini` can decide whether to:

1. absorb this packet into a future BP5-OSS-003 parent closeout once that task is materialized, or
2. use the packet as support evidence while resolving the missing parent task entry separately

---

## 6. Reviewer Handoff

Recommended reviewer focus for `Claude`:

1. Confirm the packet does not overstate scope: it is support evidence only, not a parent-task closeout
2. Confirm the three re-run command results match the live repo state
3. Confirm the dependency map is sufficient for `BP5-OSS-004` and adjacent deferred-path work
4. Confirm the coordination note about the missing `BP5-OSS-003` task entry is worth preserving

If approved, return this sidecar task to the owner for finalization and let the parent owner decide
whether to absorb the packet into the main BP5-OSS-003 execution lane.

---

## 7. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified
- No runtime or registry implementation was changed
- No checklist truth was edited by this sidecar
- The only artifact created by this slice is this acceptance packet
