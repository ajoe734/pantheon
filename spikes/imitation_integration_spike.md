# imitation Integration Spike

## Goal

Select and pin the upstream `imitation` integration path for governed trader-behavior cloning.

## Decision Summary

- selected upstream source: `https://github.com/HumanCompatibleAI/imitation`
- package source of record: `https://pypi.org/project/imitation/`
- recommended first pin: `imitation==1.0.1`
- integration mode: pinned Python package in a dedicated learning worker
- first algorithm: `Behavioral Cloning (BC)` only
- deferred algorithms: `DAgger`, `GAIL`, `AIRL`, `preference_comparisons`
- first governed output: cloned-policy artifact plus registry-ready evaluation summary

## Why imitation Fits LP-002

`LP-002` is specifically about learning from trader trajectories.

That is a better fit for `imitation` than for:

- `Qlib`, which is centered on alpha research workflows
- `TRL`, which is centered on language-model preference optimization

Using `imitation` keeps the "learn from human action traces" path separate from persona
policy optimization and separate from market-forecasting research.

## Required Decisions

- upstream package source: `HumanCompatibleAI/imitation` with PyPI release `imitation`
- version pin: `1.0.1`
- mapping from `FB-001` trajectory schema into imitation training inputs
- adapter from trained policy outputs into governed registry artifacts
- smoke-test plan for one BC run

## Packaging Strategy

Use `imitation==1.0.1` as a pinned Python dependency inside a dedicated learning worker.

Do not:

- vendor the upstream source into this repo
- route raw trajectory data directly into live execution
- treat every trader event as BC-ready training data

Do:

1. normalize trajectories through `FB-001` and `RS-002` contracts first
2. train BC in an isolated worker/runtime
3. emit a governed policy artifact into the registry path

## First Algorithm Scope

For the first integration, only support `Behavioral Cloning`.

Why:

- it is the smallest path from trajectory data to learned policy
- it minimizes operational risk
- it is enough to prove the adapter, registry, and evaluation path

Deferred until later:

- `DAgger`
- `GAIL`
- `AIRL`
- `preference_comparisons`

## Data Mapping Decision

The minimal governed BC dataset should be derived from `FB-001` events and normalized into:

- observation or feature payload
- action payload
- optional reward/outcome metadata
- trajectory grouping id
- actor type and approval provenance

Only approved and governance-clean trajectories should be eligible for BC training.

That means the adapter must filter out:

- rejected or incomplete sessions
- trajectories missing required action semantics
- trajectories with untrusted or ambiguous provenance

## Registry Boundary

The cloned-policy output should not be injected directly into live execution.

The local adapter should emit:

- a versioned policy artifact bundle
- evaluation summary
- lineage back to source trajectory dataset
- registry hints for `candidate` admission

Recommended artifact class:

- `model_artifact` for learned weights or serialized policy
- optionally a linked `execution_bundle` later, but not in the first smoke test

## Minimal Smoke Test

The first smoke test should prove:

1. `imitation==1.0.1` installs cleanly
2. one minimal BC training run can consume a governed local dataset
3. the trained output can be serialized as a governed artifact
4. lineage back to the source trajectory slice is preserved
5. the result can be represented in `REG-001`

Suggested scope:

- one tiny local BC dataset
- one single training script
- one exported artifact bundle plus evaluation JSON

## Follow-up Deliverables

When implementation begins, create:

- `services/learning/imitation/`
- `integrations/imitation/integration.md`
- `integrations/imitation/smoke_test.md`

## Remaining Open Questions

- Which observation schema should become the canonical adapter boundary between `FB-001` events and BC datasets?
- Should the first cloned artifact be registry-entered as `candidate` directly, or require an intermediate evaluation-only record first?
