# BP5-CICD-002 Re-review

Status: approved
Reviewer: Codex2
Date: 2026-04-16

## Result

No blocking findings.

## Verified

- `cloudbuild.yaml` now uses the canonical Stage 0 build IDs for all auto-detectable publish targets: `bff`, `router`, `persona`, `governance-api`, `telemetry-ingest`, `runtime-manager`, `mlflow-server`, `lean`, `dspy-worker`, `imitation-worker`, `research-base`, `research-dspy`, `research-finrl`, `research-imitation`, and `research-qlib`.
- The previously mismatched IDs (`operator-bff`, `governance`, `telemetry`) are no longer in the Cloud Build service registry.
- The missing research and execution-lab images are now present in the Cloud Build registry and point to existing Dockerfiles in the repo.
- Section 19.5 now documents the same inventory and clearly separates Stage 0 auto-detectable targets from manual-only entries (`incidents`, `postmortems`).

## Residual Notes

- The example comment in `.github/workflows/gcp-deploy.yml` still mentions `["router","governance",...]`; this is stale commentary only and does not affect runtime behavior.
