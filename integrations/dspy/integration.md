# DSPy Integration — Runnable Adapter Baseline

Last updated: 2026-04-15
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: governed runnable adapter verified
Implementation home: `services/learning/dspy/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `stanfordnlp/dspy` |
| Package source | `https://pypi.org/project/dspy-ai/` |
| Selected package | `dspy-ai==2.4.5` |
| Version pin source | `services/learning/dspy/adapter.py` (`DSPY_VERSION_PIN`) |
| Service dependency file | `services/learning/dspy/requirements.txt` |
| Worker image | `services/learning/dspy/Dockerfile` |

This integration consumes DSPy as a pinned Python package. Pantheon does not vendor
DSPy source and does not treat DSPy as an authority for registry lifecycle,
promotion, or execution routing.

## 2. Adapter Mode

Pantheon uses DSPy only for governed persona-policy optimization under `LP-001`.

Accepted mode:

- governed feedback examples are normalized before DSPy sees them
- DSPy optimization runs inside `services/learning/dspy/`
- the adapter emits a governed `prompt_bundle` plus a registry-ready metadata envelope
- the default CI path uses a deterministic stub backend, while worker/runtime use can switch to the real upstream backend

Rejected mode:

- direct DSPy writes into registry truth
- direct DSPy influence on LEAN, SignalStore, or live deployment paths
- using DSPy artifacts as live execution authority without registry promotion

## 3. Verified Local Adapter Surface

The runnable adapter path is already implemented and exercised:

- `services/learning/dspy/adapter.py`
  - `GovernedPreferenceAdapter`
  - `StubBootstrapFewShotBackend`
  - `DSPyBootstrapFewShotBackend`
  - `run_dspy_workflow()`
- `services/learning/dspy/smoke_test.py`
- `services/learning/dspy/test_adapter.py`
- `services/learning/dspy/examples/preference_dataset_sample.json`
- `services/learning/dspy/worker.py`

The adapter takes governed FB-001-style examples and emits:

1. `artifact_bundle`
   - `artifact_family=prompt_bundle`
   - `framework=dspy`
   - inline prompt-bundle manifest validated against `services/control-plane/persona/lp001/prompt_bundle.schema.json`
2. `registry_entry`
   - `artifact_type=prompt_bundle`
   - lifecycle starts at `draft`
   - lineage points back to the source dataset refs and optimization run

## 4. Runtime and Packaging Notes

- Base worker image: `python:3.11-slim`
- Default command: `python worker.py`
- Optional real backend requires installing `services/learning/dspy/requirements.txt`
- Optional upstream execution also requires `PANTHEON_DSPY_MODEL` or an explicit model name

Two execution modes are intentionally supported:

1. stub backend for deterministic CI and local smoke tests
2. real DSPy `BootstrapFewShot` backend for dedicated learning workers

## 5. Why This Counts As Runnable

`BP5-OSS-003` requires runnable adapter proof, not just narrative architecture.

That proof exists because:

- the adapter path is real code, not a placeholder contract
- the service has a dedicated dependency file and Docker worker image
- the repo ships an executable smoke test with a governed sample dataset
- unit tests validate the governed filtering and packaging logic

## 6. Evidence References

- implementation overview: `services/learning/dspy/README.md`
- governance overlay: `integrations/dspy/governance.md`
- smoke procedure: `integrations/dspy/smoke_test.md`
