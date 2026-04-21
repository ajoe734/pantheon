# Weights & Biases (W&B) Activation Criteria

**Task**: OSS-003 (W&B path), EXEC-OSS-WANDB-001 (execution-slice closeout)
**Owner**: Qwen (gate), Codex (execution slice)
**Reviewer**: Claude
**Scope**: Define activation criteria for Weights & Biases as an alternative experiment tracking backend to MLflow, and record the current defer/reopen truth as a reviewable execution slice
**Status**: APPROVED gate, DEFER remains in force
**Last Updated**: 2026-04-21

---

## Executive Summary

This document defines the activation criteria for **Weights & Biases (W&B)** as an optional alternative experiment tracking backend, complementing the MLflow-first strategy already implemented in `services/registry/experiments/`.

**Key Principle**: W&B is an **alternative backend**, not an additional capability. It must emit the same governed outputs as the MLflow path, but it may not activate until the current MLflow-first adapter surface is generalized enough to support a second backend without forking registry semantics.

---

## 1. Entry Criteria for W&B Activation

### When to Activate W&B

W&B is justified when:

1. **MLflow Integration Stable**: The MLflow adapter (`services/registry/experiments/`) is at `governed` status per `OSS_INTEGRATION_CHECKLIST.md` with at least 30 days of operational history.
   - Rationale: We need a working reference implementation before adding an alternative backend.

2. **Operator Preference Documented**: At least one human operator or team explicitly requests W&B over MLflow, with documented reasons (e.g., existing W&B workspace investment, team familiarity, superior visualization needs).
   - Non-reason: "W&B is popular." Must be a specific operational or workflow requirement.

3. **Adapter Generalization Complete**: The current MLflow-first `RegistryExperimentAdapter` / `ExperimentBackend` split in `services/registry/experiments/adapter.py` has been generalized so a second backend can be selected without changing registry-facing semantics.
   - No registry consumer may depend on MLflow-only class names, aliases, or tag formats.
   - The returned `ExperimentSyncResult` and `promoted_metadata` shape must remain identical regardless of backend.
   - Follow-on implementation may generalize the existing adapter or add a backend-neutral sibling, but W&B cannot activate while the repo still exposes only an MLflow-first registry adapter.

4. **Canonical State Mapping Landed**: The experiment bridge must mirror canonical registry `artifact_state` and derived `deployment_stage`, not treat `paper` / `live` as registry lifecycle states.
   - If legacy `lifecycle_state` or `promotion_state` fields remain during the migration window, they must be generated as compatibility projections only.
   - W&B activation is blocked until this semantic split is explicit in the experiment bridge plan.

5. **W&B SDK Compatibility**: W&B SDK (`wandb>=0.16.0` recommended) and its dependencies do not conflict with already-integrated packages (MLflow, DSPy, imitation, Qlib, TRL).
   - Key check: Both MLflow and W&B can be installed in the same environment without dependency conflicts (even if only one is active).

6. **Network/Infrastructure Ready**: The deployment environment has outbound network access to `api.wandb.ai` (or a self-hosted W&B server instance is available).
   - This is a hard infrastructure dependency that MLflow (which can run locally) does not have.

---

## 2. Target Adapter Design

### 2.1 Current Repo State

Today the repo exposes:

- `ExperimentBackend` protocol in `services/registry/experiments/adapter.py`
- MLflow-specific `RegistryExperimentAdapter`
- `EXPERIMENT_BACKEND` selector stub in `services/registry/experiments/config.py` (default `"mlflow"`, rejects unsupported backends)
- no W&B backend implementation

That means W&B activation criteria must target a **follow-on refactor**, not claim a backend-neutral interface already exists. The backend selector ambiguity is closed, but the adapter surface is still MLflow-first.

### 2.2 Required Target Surface

The minimum target surface for W&B activation is:

```python
class ExperimentBackend(Protocol):
    def record(self, record: ExperimentRecord) -> ExperimentRef:
        ...


class RegistryExperimentAdapter:
    def sync_registry_entry(self, entry: Mapping[str, Any]) -> ExperimentSyncResult:
        ...
```

W&B activation may satisfy this either by:

- extending `RegistryExperimentAdapter` to accept a W&B-capable backend, or
- introducing a backend-neutral façade with the same registry-facing behavior.

### 2.3 Required Equivalence

| Concern | MLflow reference behavior | W&B activation requirement |
|---|---|---|
| Registry-facing API | `sync_registry_entry(entry)` returns `ExperimentSyncResult` | same shape, same validation boundary |
| Backend contract | `ExperimentBackend.record(record)` returns `ExperimentRef` | same contract |
| Metadata handoff | registry writes governed `promoted_metadata` back into Object Store | identical shape and required fields |
| Rollback enforcement | adapter rejects insufficient rollback metadata before backend call | same rule |
| State semantics | must converge on canonical `artifact_state` + derived `deployment_stage` | same rule |

### 2.4 Canonical State / Alias Mapping

The target W&B mirror must map Pantheon state as follows:

| Pantheon field | W&B mirror requirement |
|---|---|
| `artifact_state=draft` | run exists but has no promoted alias |
| `artifact_state=candidate` | candidate alias/tag present |
| `artifact_state=approved` | approved alias/tag present |
| `artifact_state=retired` | retired alias/tag present; removed from active promotion aliases |
| `deployment_stage` | stored as derived metadata/tag only, never used as registry lifecycle replacement |

### 2.5 Rollback Enforcement

The W&B path must enforce the same rollback constraints as the MLflow reference path:

- artifacts staged for derived `deployment_stage=live` are rejected without proper rollback metadata.
- Both `metadata.rollback` and `rollback_target_registry_id` forms supported.
- Validation happens **before** any W&B API call (governance-first design).

---

## 3. Registry and Promotion Constraints

### 3.1 Backend Selection

The repo now has a selector stub in `services/registry/experiments/config.py`, but it does **not**
yet make W&B selectable. The current state is:

- `EXPERIMENT_BACKEND` exists and defaults to `"mlflow"`.
- `"wandb"` is intentionally not in `_SUPPORTED_BACKENDS`.
- no backend factory exists yet to instantiate a non-MLflow backend without changing the
  registry-facing adapter surface.

The expected target shape is:

```python
# services/registry/experiments/config.py
EXPERIMENT_BACKEND = os.getenv("EXPERIMENT_BACKEND", "mlflow")  # "mlflow" or "wandb"
```

The implementation must also add a factory that selects the backend without changing registry-facing behavior.

### 3.2 Output Equivalence

The registry consumer code receives **identical** `promoted_metadata` regardless of backend:

```python
# Registry consumer:
result = adapter.sync_registry_entry(entry)
metadata = result.promoted_metadata
# metadata shape is the same whether backend is mlflow or wandb
```

### 3.3 Registry Gate

W&B artifacts pass through the **same** REG-001 gate as MLflow artifacts:

- Gate 1: Lineage verification (same validation).
- Gate 2: Canonical `artifact_state` validation (same state machine).
- Gate 3: Rollback enforcement for artifacts staged `live` (same metadata requirements).
- Gate 4: Promotion alias validation (same alias policy).

The only W&B-specific check is: W&B run/artifact exists and is accessible.

---

## 4. W&B-Specific Considerations

### 4.1 Advantages Over MLflow

- **Visualization**: W&B provides superior visualization for training runs (charts, comparators, reports).
- **Collaboration**: W&B has built-in workspace sharing and team features.
- **Sweeps**: W&B Sweeps provides hyperparameter search as a managed service.

### 4.2 Disadvantages vs. MLflow

- **Cloud dependency**: W&B requires outbound network access to W&B servers (or self-hosted infrastructure).
- **Cost**: W&B has a commercial pricing model; MLflow is fully open-source.
- **Data residency**: W&B stores run data on W&B servers; MLflow can be fully self-hosted.

### 4.3 When to Prefer W&B

- Team already uses W&B for other ML experiments and wants unified dashboards.
- Superior visualization is needed for stakeholder reviews.
- W&B Sweeps is preferred over manual Ray Tune integration for hyperparameter search.

### 4.4 When to Stay with MLflow

- No existing W&B investment or team preference.
- Data residency requirements prevent cloud storage of experiment data.
- Cost sensitivity (MLflow is free, W&B requires a paid plan for teams).
- Offline/air-gapped deployment environments.

---

## 5. Success Criteria (OSS-003 W&B Acceptance)

- [x] Entry criteria documented (§1)
- [x] Target adapter design defined against the real MLflow-first repo state (§2)
- [x] Registry and promotion constraints specified (§3)
- [x] W&B-specific considerations documented (§4)
- [x] Required output equivalence with MLflow defined (§3.2)

---

## 6. Next Steps

1. **Keep the defer gate explicit**: Do not open adapter or SDK implementation work while any §7.3 re-entry condition remains unmet.
2. **W&B Version Selection**: Pin W&B SDK version (`wandb>=0.16.0` recommended) only after reopen is authorized.
3. **Adapter Generalization**: Generalize the current MLflow-first `RegistryExperimentAdapter` surface so a second backend can plug in without forking registry semantics.
4. **Canonical State Migration**: Land `artifact_state` / `deployment_stage` support in the experiment bridge before any W&B backend is considered active.
5. **Adapter Implementation**: Build a W&B backend that satisfies the same `ExperimentBackend.record()` and `ExperimentSyncResult` contract.
6. **Smoke Test**: Run a single registry entry through the W&B path with mocked W&B API to validate metadata equivalence and rollback enforcement.
7. **Backend Selection Factory**: Extend the existing `EXPERIMENT_BACKEND` selector so the adapter factory can instantiate a W&B backend without changing registry-facing behavior.

---

## 7. OSS-NEXT-004 Decision: Formal Defer (2026-04-17)

**Task**: OSS-NEXT-004
**Decision date**: 2026-04-17
**Decision**: **DEFER** — W&B backend parity does not enter the current development wave.

### 7.1 Decision Rationale

All six entry criteria from §1 are unmet as of the decision date:

| Entry criterion | Status | Detail |
|---|---|---|
| MLflow ≥30 days operational history | **Not met** | MLflow reached `governed` status on 2026-04-15 — fewer than 2 days of operational history as of this decision. Earliest eligible reopen: 2026-05-15. |
| Explicit operator preference documented | **Not met** | No operator or team has filed a documented request for W&B over MLflow. |
| `RegistryExperimentAdapter` generalized for configurable backends | **Not met** | Current adapter exposes `PRIMARY_BACKEND = "mlflow"` with no pluggable backend factory. |
| Canonical `artifact_state` / `deployment_stage` migration landed | **Not met** | Experiment bridge still uses legacy `lifecycle_state` / `paper` / `live` aliases in the MLflow adapter path. |
| W&B SDK pin (`wandb>=0.16.0`) | **Not met** | No `wandb` entry in any `requirements.txt`. |
| Network / infrastructure readiness (`api.wandb.ai`) | **Not verified** | No infrastructure review has been recorded. |

Additional context from OSS ecosystem gap analysis (`docs/reviews/2026-04-16-oss-ecosystem-gap-analysis.md §7`):
- W&B is ranked #5 of 5 in priority for the current wave, after Qlib, TRL, vectorbt/statsmodels/QuantLib materialization, and RL stack.
- Gap analysis explicitly warns: "W&B have stronger conditionality and should not accidentally balloon the next wave unless explicitly approved."
- W&B is an **optional alternative backend**, not additive capability — MLflow already covers the governed experiment registry path.

### 7.2 What Is Already In Place

The following work from BP5-OSS-004 and OSS-003 remains intact and does not regress:

- `EXPERIMENT_BACKEND` env-var selector in `services/registry/experiments/config.py` (default `"mlflow"`, raises `EnvironmentError` for unsupported backends — W&B is not in `_SUPPORTED_BACKENDS`).
- Activation criteria in this document remain approved and authoritative.
- `DEFERRED_OSS_ACTIVATION_MAP.md §5` documents the concrete blocking conditions.

### 7.3 Re-Entry Gate

W&B backend parity work may reopen **only when all of the following are simultaneously true**:

1. **MLflow 30-day history met**: MLflow has been running in a `governed`-status deployment for at least 30 consecutive days with no critical incidents. Earliest eligible date: 2026-05-15.
2. **Operator preference on file**: At least one human operator or team has filed a documented request (team name, workflow reason, and W&B workspace investment or visualization requirement) — "W&B is popular" is not sufficient.
3. **Adapter generalization task completed**: A separate execution task has generalized `RegistryExperimentAdapter` to accept a pluggable backend factory, and that task is in `done` status.
4. **Canonical state migration completed**: The experiment bridge no longer exposes legacy `lifecycle_state` / `paper` / `live` aliases anywhere in the MLflow or adapter path (or exposes them as compatibility projections only, with canonical `artifact_state` / `deployment_stage` as primary).
5. **SDK compatibility verified**: `wandb>=0.16.0` pinned and confirmed non-conflicting with `mlflow==3.10.1`, `dspy==2.4.5`, `imitation==1.0.1`, `trl>=0.8.0`, `pyqlib==0.9.6` in a single environment.
6. **Infrastructure readiness confirmed**: Deployment environment has confirmed outbound access to `api.wandb.ai` (or a self-hosted W&B instance is provisioned and documented).

### 7.4 Re-Entry Trigger

When all six re-entry conditions above are met, the gate doc owner (Qwen) should:

1. File a new execution task referencing this section as the authorization gate.
2. Update this document's status from `DEFER` to `REOPEN`.
3. Assign the adapter generalization and W&B backend implementation as separate scoped tasks.
4. Record the operator preference citation in §1.2 of this document before any implementation begins.

### 7.5 Execution Slice Closeout (EXEC-OSS-WANDB-001)

`EXEC-OSS-WANDB-001` does **not** authorize implementation. It closes the ambiguity about what the next reviewable step actually is.

Current execution-slice conclusion:

- W&B remains formally deferred for the current wave.
- The repo now has an `EXPERIMENT_BACKEND` selector stub, so the remaining blocker is not "missing config toggle"; it is the combination of unmet re-entry criteria plus an MLflow-first adapter surface.
- No adapter-generalization, SDK pin, or W&B smoke task should be opened from this slice alone.

Reviewer-ready next-step recommendation:

1. Keep W&B in `deferred` / `decision-held` status until all six §7.3 conditions are simultaneously satisfied.
2. Treat the first executable follow-up as a **reopen packet**, not as backend implementation:
   - cite the operator preference record
   - confirm MLflow's 30-day governed history window has actually passed
   - point to the completed adapter-generalization task
   - point to the completed canonical-state migration task
   - attach SDK compatibility evidence
   - attach infrastructure/network readiness evidence
3. Only after that reopen packet is accepted should implementation split into separate adapter-generalization / W&B-backend tasks.

---

## References

- `TARGET_ARCHITECTURE.md`: Registry and governance plane, preferred frameworks.
- `services/registry/experiments/adapter.py`: current MLflow-first adapter implementation and backend contract.
- `services/registry/experiments/README.md`: MLflow integration documentation.
- `OSS_INTEGRATION_CHECKLIST.md`: W&B component status and required evidence artifacts.
- `REG-001`: Registry contract — artifact lifecycle and metadata requirements.

---

**Document Status**: Approved for OSS-003 activation-gate lock; 2026-04-21 execution-slice truth refreshed for Codex/Claude review handoff
**Reviewer Verification**:
- [x] Entry criteria align with `TARGET_ARCHITECTURE.md` and keep W&B optional behind MLflow-first stabilization
- [x] The document now references the real `RegistryExperimentAdapter` / `ExperimentBackend` split instead of a nonexistent `ExperimentAdapter` API
- [x] Canonical `artifact_state` / `deployment_stage` semantics are preserved
- [x] W&B-specific constraints (network, cost, data residency) remain realistic
