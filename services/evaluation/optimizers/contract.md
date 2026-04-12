# Optimizer Output and Registry Handoff Contract

**Task:** EV-002
**Owner:** Claude
**Reviewer:** Codex
**Status:** Draft — revised, ready for Codex re-review
**Depends on:** EV-001, REG-001

---

## 1. Purpose

Optimizers in the Pantheon evolution plane produce updated models, policies, and bundles from
governed learning runs. Without explicit governance rules, an optimizer could silently overwrite
a live strategy or push untested weights into execution.

This contract ensures that:

- Every optimizer run produces a **governed provenance artifact** (`optimizer_result`)
- Every optimized model or policy enters the registry as a **new `candidate` entry** — never
  directly as `paper` or `live`
- Promotion from `candidate` onward requires evidence from the **evaluation plane** (EV-001)
- No optimizer may mutate an artifact already at `paper` or `live` in-place

---

## 2. Scope

This contract covers all optimizer types in the Pantheon learning stack:

| Optimizer | Task | Output Artifact Type |
|---|---|---|
| DSPy BootstrapFewShot | Persona policy optimization (LP-001) | `prompt_bundle` |
| Imitation / BC | Trader behavior cloning (LP-002) | `model_artifact` |
| TRL preference learning | Preference model training (LP-004) | `model_artifact` |
| RLlib / FinRL | Sequential RL policy search (LP-005) | `model_artifact` |

Any future optimizer that produces an artifact intended for registry admission or execution must
follow this contract.

---

## 3. Design Principles

### 3.1 Optimizer outputs are candidates, not mutations

An optimizer run produces a **new candidate artifact**. It does not modify the incumbent artifact.

```
Incumbent artifact (candidate or paper)
    |
    |  Optimizer run (uses telemetry + feedback)
    v
New candidate artifact  ──> registry (lifecycle_state = candidate)
    |
    |  Evaluation (EV-001 evaluator + critic)
    v
Promotion gate (REG-002)
    |
    v
Paper / Live (only with gate approval)
```

### 3.2 Every run is auditable

The `optimizer_result` artifact captures:
- The source artifact the optimizer started from
- The data windows and governance inputs consumed
- The hyperparameters and method configuration used
- Before/after metric comparisons
- All output artifacts produced

This record is non-optional. An optimizer run without a committed `optimizer_result` entry is not
a governed run.

### 3.3 Optimizer results are advisory metadata, not executable artifacts

`optimizer_result` artifacts are non-executable. They serve the same governance role as
`evaluation_result` and `critique_result` from EV-001: they are registry entries that document
a governed action and provide lineage to downstream promotion decisions.

### 3.4 No optimizer bypasses the evaluation gate

Regardless of optimizer confidence or internal metrics, every optimized artifact must pass through
at least one EV-001 evaluation before promotion to `paper`, and a separate evaluation before
promotion to `live`.

---

## 4. Optimizer Result Output Contract

### 4.1 Artifact Type

```
artifact_type: optimizer_result
```

### 4.2 Required Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `registry_id` | string | yes | unique id for this optimizer_result entry |
| `artifact_type` | string | yes | always `"optimizer_result"` |
| `strategy_id` | string | yes | stable family id of the strategy being optimized |
| `optimizer_id` | string | yes | stable id of the optimizer system (e.g., `dspy_persona_optimizer_v1`) |
| `optimizer_version` | string | yes | semantic version of the optimizer (e.g., `1.0.0`) |
| `optimizer_method` | string | yes | method used (see §4.3 for valid values) |
| `optimization_timestamp` | string | yes | RFC3339 timestamp when the optimization run completed |
| `optimization_objective` | string | yes | what metric or outcome was being optimized |
| `trigger` | string | yes | what initiated this run (see §4.4 for valid values) |
| `source_artifact` | object | yes | the input artifact that was optimized (see §4.5) |
| `governance_inputs` | object | yes | governed data consumed during the run (see §4.6) |
| `optimization_run_config` | object | yes | hyperparameters and configuration used (see §4.7) |
| `output_artifacts` | array | yes | list of governed artifacts produced (see §4.8) |
| `metrics` | object | yes | before/after metric comparison (see §4.9) |
| `rationale` | string | no | human-readable explanation of why this run was triggered and what it achieved |
| `run_id` | string | no | optimizer run identifier for cross-referencing logs |

### 4.3 Optimizer Method Values

| Value | Framework | Task |
|---|---|---|
| `dspy_bootstrap_fewshot` | DSPy BootstrapFewShot | Persona policy prompt optimization |
| `imitation_bc` | imitation / BC | Trader behavior cloning |
| `trl_ppo` | TRL PPO | Preference model training |
| `rllib_ppo` | RLlib PPO | Sequential RL policy search |
| `finrl_ppo` | FinRL PPO | Single-agent portfolio optimization |
| `stub` | stub backend | Testing and CI use only |

### 4.4 Trigger Values

| Value | Meaning |
|---|---|
| `evaluation_recommendation` | An `evaluation_result` recommended optimization or triggered by low score |
| `scheduled` | Cron or orchestration-scheduled periodic retraining |
| `manual` | Operator explicitly requested an optimization run |
| `feedback_threshold` | New feedback events exceeded a threshold since last training |
| `drift_detected` | Performance drift detected in telemetry, triggering reoptimization |

### 4.5 Source Artifact Object

Captures the artifact that the optimizer started from.

```json
{
  "source_artifact": {
    "registry_id": "prompt_bundle_persona_v1.2.0",
    "artifact_type": "prompt_bundle",
    "version": "1.2.0",
    "lifecycle_state": "paper",
    "checksum": "sha256:abc123..."
  }
}
```

The source artifact must be at `candidate` or `paper`. Optimizers do not consume `live` artifacts
directly as their training baseline — they optimize from paper-or-below to avoid feedback loops
from live execution influencing live weights.

### 4.6 Governance Inputs Object

Documents what governed data the optimizer consumed.

```json
{
  "governance_inputs": {
    "feedback_events": {
      "event_types": ["approve", "edit", "reject", "rationale"],
      "count": 48,
      "window_start": "2026-03-01T00:00:00Z",
      "window_end": "2026-04-06T23:59:59Z",
      "store_path": "/data/feedback_store"
    },
    "telemetry_events": {
      "event_types": ["pnl_snapshot", "drawdown_snapshot", "slippage_observation"],
      "count": 120,
      "window_start": "2026-03-01T00:00:00Z",
      "window_end": "2026-04-06T23:59:59Z"
    },
    "evaluation_results_referenced": [
      "eval-persona_v1-2-0"
    ],
    "preference_model_used": {
      "model_artifact_id": "pm_persona_v1.0.0",
      "model_version": "1.0.0",
      "lifecycle_state": "paper"
    }
  }
}
```

Rules:
- Feedback events must come from the FB-002 store
- Telemetry events must come from the FB-003 store
- Preference models must be at `paper` lifecycle state (per LP-004)
- Raw market data, ungoverned logs, and live execution PnL feeds are **not allowed** as
  direct optimizer training inputs

### 4.7 Optimization Run Config Object

Captures hyperparameters used so the run is reproducible.

```json
{
  "optimization_run_config": {
    "max_iterations": 20,
    "num_demos": 4,
    "trainset_size": 48,
    "valset_size": 12,
    "metric": "intent_accuracy",
    "backend": "dspy_bootstrap_fewshot",
    "seed": 42,
    "extra_params": {}
  }
}
```

All fields in `extra_params` are optimizer-specific and do not affect contract compliance.

### 4.8 Output Artifacts Array

Lists every registry-ready artifact produced by this run.

```json
{
  "output_artifacts": [
    {
      "candidate_registry_id": "prompt_bundle_persona_v1.3.0",
      "artifact_type": "prompt_bundle",
      "version": "1.3.0",
      "lifecycle_state": "candidate",
      "checksum": "sha256:def456...",
      "storage_ref": {
        "backend": "object_store",
        "path": "optimizers/dspy_persona_optimizer_v1/prompt_bundle_persona/1.3.0/artifact.json"
      },
      "metrics_snapshot": {
        "intent_accuracy": 0.95,
        "tool_selection_precision": 0.92,
        "deny_coverage_delta": 0.0
      }
    }
  ]
}
```

Rules:
- Every output artifact **must** enter the registry with `lifecycle_state: candidate`
- The `candidate_registry_id` referenced here must match the `registry_id` of the registry entry
  created for that artifact
- Multiple output artifacts are allowed (e.g., an ensemble search that produces N candidate policies)

### 4.9 Metrics Object

Records before/after comparison using the optimization objective.

```json
{
  "metrics": {
    "objective": "intent_accuracy",
    "before": {
      "artifact_id": "prompt_bundle_persona_v1.2.0",
      "value": 0.88,
      "measured_at": "2026-04-06T12:00:00Z"
    },
    "after": {
      "artifact_id": "prompt_bundle_persona_v1.3.0",
      "value": 0.95,
      "measured_at": "2026-04-07T08:00:00Z"
    },
    "improvement_delta": 0.07,
    "secondary_metrics": {
      "tool_selection_precision": {
        "before": 0.88,
        "after": 0.92
      },
      "deny_coverage_delta": {
        "before": 0.0,
        "after": 0.0
      }
    }
  }
}
```

---

## 5. Registry Handoff Rules

### 5.1 Handoff sequence

Each optimizer run must complete these steps in order before the output artifact is considered
a valid registry candidate:

1. **Commit `optimizer_result`**: Register the `optimizer_result` artifact in the registry
   (lifecycle_state `candidate`) before or atomically with any output artifacts.
2. **Register output artifacts**: Each output artifact is registered with `lifecycle_state: candidate`.
3. **Link lineage**: Each output artifact's registry entry must reference the `optimizer_result`
   entry via `lineage.source_run_ids` and carry the `optimizer_result_id` in metadata.

### 5.2 Required lineage fields for optimizer-produced artifacts

When an optimizer produces a `prompt_bundle`, `model_artifact`, or similar artifact, the registry
entry for that artifact must include:

| Field | Requirement |
|---|---|
| `lineage.parent_registry_ids` | Must include the `registry_id` of the source artifact used as optimization input |
| `lineage.source_run_ids` | Must include the optimizer run id |
| `metadata.optimizer_result_id` | Must match the `registry_id` of the committed `optimizer_result` |
| `metadata.optimizer_id` | The stable optimizer identifier |
| `metadata.optimizer_method` | The optimization method (see §4.3) |
| `metadata.optimization_objective` | The metric or outcome that was optimized |
| `metadata.source_artifact_version` | Version of the source artifact |

### 5.3 Forbidden handoff patterns

The following patterns are rejected by the promotion gate:

| Forbidden Pattern | Reason |
|---|---|
| Output artifact registered with `lifecycle_state: paper` or `live` | Optimizers must not self-promote |
| Output artifact missing `lineage.source_run_ids` | No traceability to the optimizer run |
| Output artifact missing `metadata.optimizer_result_id` | No link to the provenance record |
| `optimizer_result` registered after output artifact(s) | Provenance must precede or be atomic with output |
| Source artifact at `lifecycle_state: live` used as training baseline | Prevents live → live feedback loop |

### 5.4 Storage reference conventions

Object store paths follow this convention:

```
optimizers/{optimizer_id}/{artifact_type}/{strategy_id}/{version}/artifact.{ext}
optimizers/{optimizer_id}/{artifact_type}/{strategy_id}/{version}/optimizer_result.json
```

For example:
```
optimizers/dspy_persona_optimizer_v1/prompt_bundle/persona/1.3.0/artifact.json
optimizers/dspy_persona_optimizer_v1/prompt_bundle/persona/1.3.0/optimizer_result.json
```

### 5.5 Optimizer result lifecycle

`optimizer_result` artifacts follow a simplified lifecycle:

```
candidate  (active; referenced by downstream evaluations and promotions)
     |
  retired  (superseded by a newer optimizer run for the same strategy)
```

`optimizer_result` entries do not progress to `paper` or `live`. They are reference artifacts.

---

## 6. Promotion Gate Dependencies

### 6.1 Overview

Optimizer-produced artifacts at `candidate` cannot be promoted without evaluation evidence.

```
optimizer_result + candidate artifact
     |
     |  EV-001 evaluator assesses candidate
     v
evaluation_result  ─────────────────────────────────┐
     |                                               │
     |  (if borderline or risk_flagged)              │
     v                                               │
critique_result                                      │
     |                                               │
     └─────────────────────────────────────────────>│
                                                     │
                              REG-002 Promotion Gate │
                                                     │
                             candidate → paper       │ requires evaluation_result with
                                                     │ recommendation: candidate_to_paper
                                                     │
                             paper → live            │ requires evaluation_result with
                                                     │ recommendation: paper_to_live
                                                     │ + explicit operator/gateway approval
```

### 6.2 Candidate → Paper requirements

Before an optimizer-produced artifact can be promoted to `paper`:

1. At least one `evaluation_result` must exist in the registry with:
   - `target_artifact_id` matching the candidate
   - `recommendation: candidate_to_paper`
   - `target_promotion_state: candidate` (evaluated when it was a candidate)
2. If the evaluation `recommendation` is `needs_critique`, a `critique_result` must also exist
   with `decision_guidance.recommended_action: approve_candidate_to_paper`.
3. The `overall_score` must meet or exceed the threshold configured in the promotion gate
   (default: 0.70).

### 6.3 Paper → Live requirements

Before an optimizer-produced artifact can be promoted to `live`:

1. A **separate** `evaluation_result` must exist with:
   - `target_promotion_state: paper` (evaluated during paper execution)
   - `recommendation: paper_to_live`
   - `overall_score` ≥ 0.80 (default threshold for live promotion)
2. Explicit **operator or gateway approval** is required in all cases — evaluator scores alone
   do not promote to live.
3. The `optimizer_result` linked in the candidate's lineage must still be `candidate` lifecycle
   state (not `retired`). A retired optimizer_result means the optimization basis is superseded.

### 6.4 Blocked promotion cases

| Condition | Promotion blocked |
|---|---|
| No `evaluation_result` referencing the candidate | candidate → paper |
| `evaluation_result` recommendation is `candidate_hold` or `retire` | candidate → paper |
| `evaluation_result` recommendation is `needs_critique` and no `critique_result` exists | candidate → paper |
| `evaluation_result` is for `paper` state but `overall_score` < 0.80 | paper → live |
| No operator approval recorded | paper → live |
| Linked `optimizer_result` has been `retired` | paper → live (without re-evaluation) |

### 6.5 Preference model optimizer outputs (LP-004)

Preference models produced by TRL optimizers have an additional gate:

- The preference model `model_artifact` must be at `paper` lifecycle state before any evaluator
  may use it to produce an `evaluation_result` (per LP-004 §4).
- A preference model at `candidate` may not be used for scoring promotion-relevant artifacts.

This prevents circular logic where an untested preference model influences the evaluation that
would promote that same model.

---

## 7. Registry Integration

### 7.1 New artifact type

`optimizer_result` is added to the REG-001 artifact type set:

```
artifact_type: "optimizer_result"
```

Like `evaluation_result` and `critique_result`, it is a non-executable reference artifact.

### 7.2 Registry entry for optimizer_result

```python
entry = registry.register({
    "registry_id": "opt-persona-v1-3-0",
    "artifact_type": "optimizer_result",
    "strategy_id": "persona",
    "version": "1.0.0",
    "lifecycle_state": "candidate",
    "lineage": {
        "parent_registry_ids": ["prompt_bundle_persona_v1.2.0"],
        "source_run_ids": ["dspy_run_2026_04_07_001"],
        "source_strategy_spec_id": "persona"
    },
    "storage_ref": {
        "backend": "object_store",
        "path": "optimizers/dspy_persona_optimizer_v1/prompt_bundle/persona/1.3.0/optimizer_result.json"
    },
    "checksum": "sha256:xyz789...",
    "metadata": {
        "optimizer_id": "dspy_persona_optimizer_v1",
        "optimizer_method": "dspy_bootstrap_fewshot",
        "optimization_objective": "intent_accuracy",
        "output_artifact_ids": ["prompt_bundle_persona_v1.3.0"]
    }
})
```

### 7.3 Registry entry for optimizer-produced artifact

```python
entry = registry.register({
    "registry_id": "prompt_bundle_persona_v1.3.0",
    "artifact_type": "prompt_bundle",
    "strategy_id": "persona",
    "version": "1.3.0",
    "lifecycle_state": "candidate",
    "lineage": {
        "parent_registry_ids": ["prompt_bundle_persona_v1.2.0"],
        "source_run_ids": ["dspy_run_2026_04_07_001"]
    },
    "storage_ref": {
        "backend": "object_store",
        "path": "optimizers/dspy_persona_optimizer_v1/prompt_bundle/persona/1.3.0/artifact.json"
    },
    "checksum": "sha256:def456...",
    "metadata": {
        "optimizer_result_id": "opt-persona-v1-3-0",
        "optimizer_id": "dspy_persona_optimizer_v1",
        "optimizer_method": "dspy_bootstrap_fewshot",
        "optimization_objective": "intent_accuracy",
        "source_artifact_version": "1.2.0"
    }
})
```

---

## 8. Per-Optimizer Implementation Notes

### 8.1 DSPy (LP-001): Persona policy optimization

- Source artifact: `prompt_bundle` at `candidate` or `paper`
- Output artifact: `prompt_bundle` at `candidate`
- Governance inputs: FB-002 feedback events (approve, edit, reject, rationale)
- Metrics objective: `intent_accuracy`, `tool_selection_precision`, `deny_coverage_delta`
- Optimizer method: `dspy_bootstrap_fewshot`

### 8.2 Imitation / BC (LP-002): Trader behavior cloning

- Source artifact: `model_artifact` (imitation_policy) at `candidate` or `paper`, or first run
  from scratch with `source_artifact.registry_id: null`
- Output artifact: `model_artifact` (model_family: `imitation_policy`) at `candidate`
- Governance inputs: FB-001 trajectory events for actor_role ∈ {operator, approver}
- Metrics objective: `behavioral_cloning_accuracy`, `approval_rate_match`
- Optimizer method: `imitation_bc`

For first-run artifacts (no prior incumbent), `source_artifact.registry_id` may be `null`.
The run still requires a valid `optimizer_result` entry.

### 8.3 TRL preference learning (LP-004)

- Source artifact: `model_artifact` (preference_model) at `candidate` or `paper`, or first run
- Output artifact: `model_artifact` (model_family: `preference_model`) at `candidate`
- Governance inputs: FB-002 feedback events, lifecycle_state ∈ {candidate, paper}
- Metrics objective: `approval_probability`, `preference_alignment_score`
- Optimizer method: `trl_ppo`
- Additional gate: output must reach `paper` before it may be used in evaluator scoring

### 8.4 RLlib / FinRL (LP-005)

- Source artifact: `model_artifact` (rl_policy) at `candidate` or `paper`, or first run
- Output artifact: `model_artifact` (model_family: `rl_policy`) at `candidate`
- Governance inputs: FB-003 telemetry events; optionally preference model at `paper`
- Metrics objective: strategy-specific (e.g., `sharpe_ratio`, `max_drawdown`, `annualized_return`)
- Optimizer method: `rllib_ppo` or `finrl_ppo`
- Entry gate: LP-005 entry criteria (§1 of PATH_DEFINITION.md) must be satisfied before an RL run

---

## 9. Acceptance Criteria

✓ **Optimizer outputs are versionable artifacts**:
  - `optimizer_result` artifact type defined with required fields
  - JSON Schema defined for machine validation
  - Each optimizer run produces exactly one `optimizer_result` registry entry
  - Output artifacts (prompt_bundle, model_artifact) enter at `candidate` lifecycle state
  - Output artifacts carry full lineage to optimizer run and source artifact

✓ **Registry handoff rules documented**:
  - Required lineage fields for optimizer-produced artifacts (§5.2)
  - Forbidden handoff patterns listed (§5.3)
  - Storage reference naming convention defined (§5.4)
  - Optimizer_result lifecycle defined (§5.5)
  - Per-optimizer implementation notes (§8)

✓ **Promotion gate dependencies identified**:
  - Candidate → Paper gate requirements (§6.2)
  - Paper → Live gate requirements (§6.3)
  - Blocked promotion cases enumerated (§6.4)
  - Preference model circular dependency guard (§6.5)

---

## 10. Open Items for Future Refinement

1. **Multi-output reconciliation**: When an optimizer produces N candidates from a hyperparameter
   search, the promotion gate may need to select which candidate to advance. A reconciliation
   step (similar to EV-001's ensemble evaluator guidance) is needed.

2. **Automatic re-evaluation trigger**: When an `optimizer_result` is retired (superseded),
   any evaluation results linked to its output artifacts should be flagged for re-evaluation
   if those artifacts are still at `candidate`.

3. **Optimizer ensemble**: Multiple optimizer methods may produce candidates for the same
   strategy. The promotion gate should support multi-source candidate comparison.

4. **Approval gate automation**: Routine optimizations (e.g., scheduled preference model
   retraining) may qualify for automated paper promotion within pre-approved score bands,
   pending operator configuration.

---

## 11. Review Focus

Codex should review this contract for:

- **Lineage completeness**: Do the required fields in §5.2 provide sufficient audit coverage?
- **Promotion gate alignment**: Is the dependency chain in §6 compatible with REG-002 and REG-003?
- **Preference model guard (§6.5)**: Does the LP-004 gate correctly prevent circular scoring?
- **Per-optimizer coverage (§8)**: Is any optimizer type missing, misconfigured, or misaligned?
- **Schema fit**: Does `optimizer_result.schema.json` validate the examples correctly?
- **Forbidden patterns (§5.3)**: Are the rejection rules enforceable by the REG-002 gate?

---

**Document Status**: Draft — revised, ready for Codex re-review
**Owner**: Claude
**Reviewer**: Codex
**Created**: 2026-04-07
**Last Updated**: 2026-04-07
