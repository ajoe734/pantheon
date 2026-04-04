# DSPy Persona Policy Optimization Contract

**Task:** LP-001  
**Owner:** Claude (helper claim; original owner: Codex)  
**Reviewer:** Codex  
**Status:** DRAFT — ready for Codex review

---

## 1. Purpose

OpenClaw personas make decisions about which tools to invoke, how to frame responses, and how to interpret user intent. Over time, these decisions should improve based on observed outcomes and explicit human feedback.

DSPy provides a principled way to optimize such decision-making programs without manually engineering prompts.

This contract defines:
- what DSPy is allowed to optimize
- what the training signal is
- what the output artifact looks like
- how the governance boundary is maintained

Machine-readable schema:

- `services/control-plane/persona/lp001/prompt_bundle.schema.json`

---

## 2. Non-Negotiable Constraint

DSPy optimization in OpenClaw is scoped to **persona policy only**.

It must not:
- directly modify live trading strategy parameters
- produce signals that bypass the REG → SIG → L execution path
- write to SignalStore or LEAN runtime directly

It must:
- produce a versioned `prompt_bundle` artifact
- route that artifact through REG-001 registry (lifecycle: `draft → candidate → paper`)
- require explicit operator approval before any optimized persona policy enters production

This constraint mirrors OC-001 Rule 3 (governance actions require operator role) and OC-003 governance_context.

---

## 3. What DSPy Optimizes

### 3.1 Persona decision programs

| Program type | Description | Example |
|---|---|---|
| `intent_classify` | classify user message into intent classes | "buy AAPL" → `execution.signal` |
| `tool_select` | given intent + context, pick appropriate tool | `research.*` → QlibTool or VectorbtTool |
| `response_frame` | structure response for channel (console vs telegram) | verbose for console, concise for chat |
| `approval_rationale` | generate rationale for approval-required actions | "this action requires approval because..." |

### 3.2 What is NOT optimized by DSPy

- order parameters (quantity, price, direction) — those belong to signal schema v1
- promotion gates — those belong to REG-002
- tool permission rules — those belong to OC-001

---

## 4. Training Signal

Training signal comes exclusively from governed feedback in the FB-001 preference store.

| Signal source | Event type | How used |
|---|---|---|
| Trader `approve` | `trader_feedback_event` | positive label for (message → intent → tool → outcome) trajectory |
| Trader `reject` | `trader_feedback_event` | negative label |
| Trader `edit` | `trader_feedback_event` | partial correction — the edited version is the preferred output |
| Trader `rationale` | `trader_feedback_event` | free-text explanation used to construct preference pairs |

DSPy requires preference pairs (or labeled examples) for optimization. These are constructed by joining:
- original persona decision (from audit log)
- trader feedback event (approve / reject / edit)
- target outcome description (from rationale field)

### 4.1 Training data quality gates

Before feeding data to DSPy, the preference store reader must:
- filter to events where `actor_role` ∈ `["operator", "approver"]` only (not `persona` or `system`)
- require `target.promotion_state` ∈ `["candidate", "paper"]` (feedback on draft artifacts excluded)
- exclude events with missing `strategy_id`

---

## 5. Output Artifact — `prompt_bundle`

A DSPy optimization run produces a `prompt_bundle` artifact.

### 5.1 Artifact structure

```
prompt_bundle/
  dspy_program.json         # serialized DSPy program (signatures + compiled modules)
  program_metadata.json     # governed metadata (see §5.2)
  eval_summary.json         # evaluator scores that justified promotion
```

The governed envelope for this artifact family is formalized in:

- `services/control-plane/persona/lp001/prompt_bundle.schema.json`

### 5.2 program_metadata.json

| Field | Required | Description |
|---|---|---|
| `bundle_id` | yes | unique id for this optimization run output |
| `base_strategy_id` | yes | which strategy family this persona policy is for |
| `dspy_optimizer` | yes | which DSPy optimizer was used (`BootstrapFewShot`, `MIPRO`, etc.) |
| `training_run_id` | yes | connects to the preference store query that generated training data |
| `optimized_programs` | yes | list of which programs were optimized (e.g. `["intent_classify", "tool_select"]`) |
| `eval_metrics` | yes | key metrics: intent accuracy, tool selection F1, etc. |
| `base_bundle_ref` | no | prior prompt_bundle this run built on top of |
| `created_at` | yes | RFC3339 timestamp |

### 5.3 Registry path

The artifact enters REG-001 as:

```
artifact_type:    prompt_bundle
lifecycle_state:  draft       (output of optimization run)
                → candidate   (passes automated eval gate)
                → paper       (operator review, deployed to paper persona)
```

`live` promotion for a `prompt_bundle` requires operator approval (identical to live strategy promotion).

---

## 6. Evaluation Gate Before Registry Admission

Before a `prompt_bundle` can transition from `draft` to `candidate`, it must pass an automated evaluation:

| Metric | Minimum threshold (v1) | Notes |
|---|---|---|
| Intent classification accuracy | ≥ 0.85 on held-out eval set | measured against gold-labeled examples |
| Tool selection precision | ≥ 0.80 | false positives here mean wrong tool invocations |
| Deny coverage regression | `deny_coverage_delta >= -0.02` | optimized bundle must not reduce deny coverage by more than 2 percentage points against baseline |
| Mandatory deny violations | `0` | any violation on mandatory deny-rule eval cases is an automatic failure |

The governance invariant is that an optimized persona must not become materially more permissive than the current baseline.

For v1, we express that with:

1. baseline-relative `deny_coverage_delta`
2. zero tolerance on mandatory deny-rule violations

This is computed against OC-001 deny rules applied to the eval message set.

---

## 7. Governance Integration

| Touchpoint | How LP-001 connects |
|---|---|
| OC-001 | optimized persona must still satisfy deny-first rules; eval gate checks denial rate |
| OC-003 | optimized persona uses StrategySpec + WorkflowHandoff as output objects |
| FB-001 | training data read from preference store; quality gates applied |
| REG-001 | `prompt_bundle` artifact tracked through registry lifecycle |
| REG-002 | same promotion gate mechanics apply to `prompt_bundle` as to `execution_bundle` |
| P4-001 router | `intent_classify` output feeds into router dispatch; optimized classifier must be drop-in compatible |

---

## 8. What Is Deferred

| Item | Notes |
|---|---|
| MIPRO / advanced DSPy optimizers | v1 uses `BootstrapFewShot` only (simpler, fewer requirements) |
| Automated rollback of persona policy | manual rollback via registry `rollback_target` for v1 |
| Online / continuous optimization | v1 is batch-only; triggered by cron or operator request |
| Multi-persona optimization | v1 optimizes one persona family at a time |
| TRL preference learning | separate task LP-004; requires more complex reward model infrastructure |

---

## 9. Codex Decisions

1. **Optimizer choice**
   - choose `BootstrapFewShot` for v1
   - reason: lower operational risk and easier debugging than more advanced optimizers

2. **`prompt_bundle` schema**
   - create a dedicated machine-readable schema
   - reason: registry lifecycle and governance checks should not depend on DSPy internals alone

3. **Governance regression metric**
   - use:
     - `deny_coverage_delta >= -0.02`
     - `mandatory_deny_violation_count == 0`
   - reason: this expresses the actual safety requirement more clearly than a vague denial-rate phrase

## 10. Remaining Follow-up

1. Pin the upstream DSPy version in the dependency layer.
2. Build the first adapter under `services/learning/dspy/`.
3. Add a minimal smoke test using governed examples from `FB-001`.
