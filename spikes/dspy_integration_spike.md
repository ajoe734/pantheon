# DSPy Integration Spike

## Goal

Select and pin the upstream DSPy integration path for persona policy optimization.

## Decision Summary

- selected upstream source: `https://github.com/stanfordnlp/dspy`
- docs/source of truth: `https://dspy.ai/`
- selected stable line: `3.0.x`
- recommended first pin: `3.0.4`
- installation mode: Python package dependency, not vendored source
- v1 optimizer choice: `BootstrapFewShot`
- `prompt_bundle` should have its own schema
- governance regression gate should use:
  - `deny_coverage_delta >= -0.02`
  - `mandatory_deny_violation_count == 0`

## Why DSPy Fits LP-001

LP-001 is about persona policy optimization:

- intent classification
- tool selection
- response framing
- approval rationale

Those are structured language-program problems.

DSPy is a better fit than direct model fine-tuning because it lets us optimize explicit programs while keeping outputs inside our registry and governance boundary.

## Packaging Strategy

Use DSPy as a pinned Python dependency in the learning/runtime environment.

Do not:

- vendor DSPy source into this repo
- hide it behind ad hoc notebooks

Do:

1. pin DSPy in the dependency layer
2. create a local adapter under `services/learning/dspy/`
3. emit governed `prompt_bundle` artifacts into the registry path

## Optimizer Choice

For v1, choose `BootstrapFewShot`.

Why:

- lower operational risk
- easier debugging
- better fit for small, curated governed datasets

Deferred:

- `MIPROv2`
- `GEPA`
- RL-oriented DSPy optimizers

Those can be reconsidered only after the first governed path is stable.

## `prompt_bundle` Schema Decision

Decision: create a dedicated machine-readable schema.

Why:

- registry and rollback semantics should not depend on DSPy internals alone
- promotion checks need a stable governed envelope
- downstream validation should be language-agnostic

First schema fields should include:

- `bundle_id`
- `strategy_id`
- `version`
- `dspy_version`
- `optimizer`
- `program_refs`
- `training_run_id`
- `evaluation_summary`
- `registry_hints`

## Governance Regression Gate

The current phrase "no regression on denial rate" is too vague.

Use two explicit checks:

1. `deny_coverage_delta >= -0.02`
2. `mandatory_deny_violation_count == 0`

This states the real safety property:

- optimized personas may not materially weaken deny-first coverage
- mandatory deny cases must never flip to unsafe allow behavior

## Minimal Smoke Test

The first DSPy smoke test should prove:

1. pinned DSPy installs cleanly
2. one persona program can be optimized from governed examples
3. the optimized output can be serialized into a `prompt_bundle`
4. the `prompt_bundle` validates against local schema
5. the artifact can be represented in `REG-001`

Suggested scope:

- one `intent_classify` program
- one tiny curated dataset derived from `FB-001`
- one evaluation pass with deny-first regression checks

## Follow-up Deliverables

When implementation begins, create:

- `services/learning/dspy/`
- `integrations/dspy/integration.md`
- `integrations/dspy/smoke_test.md`

## Remaining Open Questions

- which dependency file should own the first DSPy pin in this repo layout?
- what is the smallest governed dataset slice from `FB-001` that still makes the smoke test meaningful?
