# services/learning/dspy

**Purpose**: Integrate governed persona-policy optimization into Pantheon using upstream DSPy.

**Task**: LP-001  
**Owner**: Codex  
**Reviewer**: Claude  
**Status**: IMPLEMENTED

## What ships in v1

LP-001 now has a concrete adapter instead of only a contract:

- governed FB-001 example filtering for persona optimization
- canonical `services/learning/dspy/` implementation path
- `BootstrapFewShot` workflow with an optional upstream DSPy backend
- deterministic stub backend for local smoke tests and CI
- `prompt_bundle` packaging plus registry-ready metadata
- unit tests and smoke test using governed sample examples

This implementation keeps DSPy scoped to persona policy only. It never writes to
LEAN, SignalStore, or live registry state directly.

## Input boundary

The adapter consumes a governed dataset with:

- `training_examples`
- `evaluation_examples`
- `source_dataset_refs`
- `target.promotion_state` restricted to `candidate` or `paper`
- `actor_role` restricted to `operator` or `approver`

Each example captures:

- the user message and channel
- the preferred `intent` + `tool`
- optional baseline output for deny-regression checks
- linkage back to the governed feedback event and target artifact

## Output boundary

`run_dspy_workflow()` emits two governed objects:

1. `artifact_bundle`
   - `artifact_family=prompt_bundle`
   - `framework=dspy`
   - inline `prompt_bundle` manifest validated against
     `services/control-plane/persona/lp001/prompt_bundle.schema.json`
2. `registry_entry`
   - `artifact_type=prompt_bundle`
   - lifecycle starts at `draft`
   - lineage points back to the feedback dataset and optimization run

The bundle contains the governed envelope and a program payload summary. A
future object-store worker can persist the compiled DSPy program bytes using the
same manifest and registry entry.

## Backends

- `StubBootstrapFewShotBackend`
  - deterministic token-overlap router for smoke tests and CI
  - proves packaging, lineage, and governance checks without external model access
- `DSPyBootstrapFewShotBackend`
  - optional real backend on top of upstream `dspy-ai==2.4.5`
  - requires a model endpoint via `PANTHEON_DSPY_MODEL`
  - uses `dspy.BootstrapFewShot(...).compile(student, trainset=trainset)`

## Commands

Local smoke path:

```bash
python3 services/learning/dspy/smoke_test.py
```

Optional upstream DSPy smoke path after installing service dependencies and
providing a model endpoint:

```bash
PANTHEON_DSPY_MODEL=openai/gpt-4o-mini python3 services/learning/dspy/smoke_test.py --backend dspy
```

Unit tests:

```bash
python3 -m unittest discover -s services/learning/dspy -p 'test_*.py'
```

## Canonical path

`services/learning/dspy/` is the canonical LP-001 implementation directory.
`services/research/dspy/requirements.txt` remains as a deprecated compatibility
shim for older docs and worker references.

