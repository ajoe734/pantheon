# BP5-CICD-002 Review

Status: changes requested
Reviewer: Codex2
Date: 2026-04-16

## Findings

1. `cloudbuild.yaml` and `.github/workflows/gcp-deploy.yml` are not wired to the Stage 0 build target IDs, so changed-path gating will skip or misroute real builds.
   - `gcp-deploy.yml` forwards `scripts/ci_stage0.py detect-changes` `build_ids` directly into `_SERVICES` (`.github/workflows/gcp-deploy.yml:101-112`, `.github/workflows/gcp-deploy.yml:136-139`).
   - The canonical Stage 0 matrix defines build IDs such as `router`, `persona`, `mlflow-server`, `lean`, `dspy-worker`, `imitation-worker`, `research-base`, `research-dspy`, `research-finrl`, `research-imitation`, and `research-qlib` (`.github/pantheon-stage0-matrix.json:32-72`, `.github/pantheon-stage0-matrix.json:258-355`, `.github/pantheon-stage0-matrix.json:358-408`).
   - `cloudbuild.yaml` accepts a different set of IDs and names, including `operator-bff`, `governance`, and `telemetry`, while omitting the research images that Stage 0 can request (`cloudbuild.yaml:33-41`, `cloudbuild.yaml:72-80`).
   - Concrete breakage examples:
     - Stage 0 can emit `bff`, but Cloud Build only recognizes `operator-bff`, so BFF changes would produce `Built: 0`.
     - Stage 0 can emit `mlflow-server`, `research-dspy`, `research-finrl`, `research-imitation`, `research-qlib`, `lean`, `dspy-worker`, and `imitation-worker`, but Cloud Build does not define them at all.
     - Stage 0 does not emit `governance` or `telemetry`, so the current Cloud Build entries are not reachable through the changed-path flow.

2. The review doc claims the changed-path flow builds "only the affected services", but that statement is not true with the current wiring.
   - Section 19.5 says `_SERVICES` carries the changed-path result into `cloudbuild.yaml` so only affected services are built (`Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md:1280-1284`).
   - Because the `_SERVICES` namespace in `cloudbuild.yaml` does not match the Stage 0 matrix IDs, that acceptance statement is currently false for part of the repo and misleading for operators.

## Required fix

- Align `cloudbuild.yaml` with the canonical build IDs from `.github/pantheon-stage0-matrix.json`, or add an explicit translation layer in `gcp-deploy.yml` before invoking `gcloud builds submit`.
- Update Section 19 so the documented image/service inventory matches the real accepted IDs and image names.
