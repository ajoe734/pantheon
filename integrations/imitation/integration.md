# imitation Integration — Runnable Adapter Baseline

Last updated: 2026-04-15
Owner: BP5-OSS-003 (Codex)
Reviewer: Claude
Status: governed runnable adapter verified
Implementation home: `services/learning/imitation/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `HumanCompatibleAI/imitation` |
| Package source | `https://pypi.org/project/imitation/` |
| Selected package | `imitation==1.0.1` |
| Version pin source | `services/learning/imitation/adapter.py` (`IMITATION_VERSION_PIN`) |
| Service dependency file | `services/learning/imitation/requirements.txt` |
| Worker image | `services/learning/imitation/Dockerfile` |

Pantheon consumes `imitation` as a pinned Python dependency in a dedicated learning worker.
The repo does not vendor upstream source and does not route learned policies directly into live execution.

## 2. Adapter Mode

Pantheon uses `imitation` only for governed trader-behavior cloning under `LP-002`.

Accepted mode:

- FB-001-style trajectory datasets are normalized before training
- v1 scope is `Behavioral Cloning (BC)` only
- the adapter emits a governed imitation-policy artifact bundle plus a registry-ready metadata envelope
- default CI and smoke execution use a deterministic stub backend, while dedicated workers may run the real upstream backend

Rejected mode:

- direct training from ambiguous or ungoverned trader traces
- routing a learned policy directly into LEAN
- treating BC output as automatically promoted or live-ready

## 3. Verified Local Adapter Surface

The runnable adapter path is already implemented and exercised:

- `services/learning/imitation/adapter.py`
  - `GovernedTrajectoryAdapter`
  - `StubBehaviorCloningBackend`
  - `ImitationBehaviorCloningBackend`
  - `run_imitation_workflow()`
- `services/learning/imitation/smoke_test.py`
- `services/learning/imitation/test_adapter.py`
- `services/learning/imitation/examples/trajectory_dataset_sample.json`
- `services/learning/imitation/worker.py`

The adapter emits:

1. `artifact_bundle`
   - `artifact_family=imitation_policy`
   - `algorithm=behavior_cloning`
   - dataset summary, governance filters, policy payload, and evaluation summary
2. `registry_entry`
   - `artifact_type=behavior_policy`
   - `metadata.model_family=imitation_policy`
   - artifact state starts at `draft`
   - lineage points back to the source dataset refs and source strategy spec

## 4. Runtime and Packaging Notes

- Base worker image: `python:3.11-slim`
- Default command: `python worker.py`
- Optional real backend requires installing `services/learning/imitation/requirements.txt`
- Dedicated worker execution is expected when using the real upstream backend

Two execution modes are intentionally supported:

1. stub nearest-centroid backend for deterministic CI and local smoke tests
2. real `imitation` behavioral-cloning backend for dedicated training workers

## 5. Why This Counts As Runnable

This row is not just planned or criteria-defined.

Runnable proof exists because:

- the governed adapter code is implemented
- the service has a dedicated dependency file and worker image
- the repo ships an executable smoke test using governed trajectory samples
- unit tests validate filtering, packaging, and lineage behavior

## 6. Evidence References

- implementation overview: `services/learning/imitation/README.md`
- governance overlay: `integrations/imitation/governance.md`
- smoke procedure: `integrations/imitation/smoke_test.md`
