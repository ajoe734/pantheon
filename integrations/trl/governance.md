# TRL Integration — Governance Overlay

Last updated: 2026-04-17
Owner: OSS-NEXT-002 (Claude)
Reviewer: Gemini
Status: governed runtime boundary documented
Related task: `LP-004`

## 1. Governance Principle

> TRL may learn from governed preference events. It may not bypass Pantheon's registry, approval, or execution gates.

The TRL adapter is a research-time path for preference-learning (DPO) only. It does not own
deployment stage, runtime execution, or live replacement semantics. TRL models are
non-executable governed artifacts that feed evaluators, reward shapers, and persona policy
optimizers — they do not drive positions directly.

## 2. Input Governance

The pair construction adapter validates every FB-002 event before any preference pair is built.

Mandatory constraints:

- `actor_role` must be in `{"operator", "approver"}` — no system-generated or anonymous events
- `promotion_state` must be in `{"candidate", "paper"}` — no events from live or retired artifacts
- `artifact.artifact_id` must be present and non-empty (artifact linkage required)
- `feedback_event_id` must be present and non-empty (lineage tracking)
- `strategy_family` must be present and non-empty (strategy diversity gate)
- For `edit` events: `artifact_edited.artifact_id` must be present and non-empty

Events that fail validation raise `TRLWorkflowError` and halt the workflow — no partial processing.

## 3. Output Governance

The TRL workflow emits a governed artifact bundle and a registry-ready preference model entry.

Governed output rules:

- `artifact_state` starts at `draft`
- `artifact_family` is `trl_preference_model`
- `deployment_summary.current_stage` is `none`
- lineage must include `source_feedback_event_ids` and `source_run_ids`
- `governance.direct_live_influence` is `false`
- `governance.execution_stage` is `none`

TRL preference models never use `paper`, `canary`, or `live` as registry states.
Their lifecycle is: `draft` → `candidate` → `approved` → `retired`.

## 4. Preference Model Consumption Contract

TRL produces **preference scores** (P(A preferred over B)), not **actions** (buy/sell/hold).

Downstream consumers and their permitted usage:

| Consumer | Role | Constraint |
|---|---|---|
| EV-001 evaluator | Preference score as one factor in composite evaluation | Not a veto; blended with performance metrics |
| LP-005 RL reward shaping | Preference-aligned reward component | Blended with task reward (default weight: 0.3 preference, 0.7 task) |
| LP-001 DSPy persona policy | Intent signal for prompt bundle scoring | Auxiliary signal; not the sole optimization target |

TRL models are not:

- direct position-sizing inputs
- execution routing authorities
- replacement for performance-based evaluation
- veto mechanisms over other evaluator criteria

## 5. Scope Guardrails

Only DPO (Direct Preference Optimization) with a small transformer encoder is in scope for v1.

Explicitly deferred:

- PPO-based RLHF (higher variance, more infrastructure)
- Constitutional AI (requires different feedback structure)
- Reward model training (covered by simpler logistic/GBT baseline per ACTIVATION_CRITERIA §1.4)
- Online/streaming DPO (requires real-time feedback infrastructure)

If Pantheon later enables those paths, they need separate smoke evidence and a governance refresh.

## 6. Authority Boundary

The TRL integration never receives authority over:

- registry truth (write-owner is registry service)
- deployment-stage changes
- runtime-manager actions
- OpenClaw runtime orchestration
- LEAN execution decisions
- rollback semantics
- position sizing or order generation

Its responsibility ends at packaging a governed `draft` preference model artifact with
complete lineage back to its source FB-002 feedback events.

## 7. Upgrade Rules

When changing the version pin, backend behavior, or pair-construction logic:

1. update `services/learning/trl/requirements.txt` and `TRL_VERSION_PIN`
2. rerun `python3 services/learning/trl/smoke_test.py`
3. rerun `python3 -m unittest discover -s services/learning/trl -p 'test_*.py'`
4. update `integration.md`, this governance file, and `OSS_INTEGRATION_CHECKLIST.md`

Any future upstream backend run must preserve the same FB-002 validation filters,
draft-only lifecycle, and registry-first authority boundary.
