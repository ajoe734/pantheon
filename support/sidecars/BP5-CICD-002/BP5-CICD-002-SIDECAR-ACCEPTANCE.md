# BP5-CICD-002 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Helper parent:** `BP5-CICD-002` — Implement Cloud Build to Artifact Registry publish flow
**Prepared by:** Claude (owner: BP5-CICD-002-SIDECAR-ACCEPTANCE)
**Reviewer:** Codex
**Date:** 2026-04-16 (rev 4: corrected runtime-manager Dockerfile evidence path; restructured AC-5 third table to separate manual-only cloudbuild.yaml targets from matrix-only services; fixed false "no phantom IDs" claim per Codex rev-3 review findings)
**Status:** approved and closed (Codex approved rev 4 on 2026-04-16; Claude finalized 2026-04-16)

> **Scope constraint:** This packet is a support artifact only. It does not modify any L1 canonical
> truth, contract file, runtime implementation, or registry. All decisions here are advisory inputs
> for the BP5-CICD-002 owner and reviewer to accept, amend, or reject.

---

## 1. Purpose

This packet provides BP5-CICD-002 with:

1. A structured **acceptance checklist** mapping each acceptance criterion to verifiable evidence
2. A **dependency map** showing which upstream tasks BP5-CICD-002 depends on and which downstream
   tasks depend on it
3. A **delivery inventory** of every file produced by BP5-CICD-002
4. **Open questions** that the parent-task owner should resolve or document before requesting review

---

## 2. Acceptance Checklist

BP5-CICD-002 formal scope (from execution-materialization.md):
> "Implement Cloud Build to Artifact Registry publish flow"

The parent task has **two explicit acceptance criteria** recorded in the archived task snapshot
(`ai-task-archive/tasks/BP5-CICD-002.json`):

> AC-A: "GitHub CI, Cloud Build, and Artifact Registry form one repo-to-build-to-image path"
> AC-B: "the publish flow works without long-lived GCP keys embedded in GitHub"

The five derived checks below (AC-1 through AC-5) are the structured sub-checks that map to these
two canonical acceptance criteria. AC-1 and AC-5 together satisfy **AC-A**; AC-1 (auth step) and
AC-2 also directly satisfy **AC-B**. The final acceptance summary in Section 6 uses the canonical
AC-A / AC-B framing.

### AC-1: GitHub Actions triggers Cloud Build on every push to `main` (and via `workflow_dispatch`)

| Check | Evidence present | Gap / note |
|---|---|---|
| Workflow file exists | `.github/workflows/gcp-deploy.yml` — triggers on `push: branches: [main]` and `workflow_dispatch` | None; fully wired. |
| OIDC / Workload Identity auth step present | `google-github-actions/auth@v2` with `workload_identity_provider` + `service_account` variables | GCP-side WIF pool setup is out-of-band (documented in `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md`). No long-lived key is stored in GitHub. |
| Cloud SDK setup step present | `google-github-actions/setup-gcloud@v2` | None. |
| `gcloud builds submit` step present | Yes; passes `--config=cloudbuild.yaml`, `--substitutions=_ENV_TAG,_SERVICES`, `--timeout=3600s` | None. |
| Concurrency guard prevents parallel publishes | `concurrency: group: gcp-deploy-${{ github.ref }}, cancel-in-progress: false` | Correct; in-flight publishes are never cancelled. |

**AC-1 is MET** as written in the workflow.

---

### AC-2: Changed-path detection routes only changed services into Cloud Build

| Check | Evidence present | Gap / note |
|---|---|---|
| `detect` step re-uses Stage 0 CI detector | `python3 scripts/ci_stage0.py detect-changes --github-output /tmp/stage0_out.txt` | The `gcp-deploy.yml` detect step sources `/tmp/stage0_out.txt` instead of writing to `$GITHUB_OUTPUT` directly; the final `echo "services=…" >> "$GITHUB_OUTPUT"` is present and correct. |
| Workflow consumes `build_ids`, not `target_ids` | `gcp-deploy.yml` reads `${build_ids:-}` from the sourced output | **Routing gap:** `ci_stage0.py` emits `build_ids` only for targets with a `build` block in the matrix (`build_ids = [t["id"] for t in matched if t.get("build")]`). `bff`, `governance-api`, `telemetry-ingest`, and `runtime-manager` have only `verify` blocks in `pantheon-stage0-matrix.json`; they appear in `target_ids`/`verify_ids` but NOT in `build_ids`. A targeted push to their paths (without a global-path change) sets `SERVICES=""` → Cloud Build is skipped for those services. |
| `global_changed=true` triggers full rebuild | If `global_changed=true`, `SERVICES="all"` is set | Correct; this path DOES build the four verify-only services as part of the full fleet rebuild. |
| Empty `SERVICES` skips Cloud Build submission | `if: steps.detect.outputs.services != ''` guard on the `Submit Cloud Build job` step | Correct; a `Skip notice` step confirms the skip. |
| Manual `workflow_dispatch` override respected | `MANUAL_SERVICES` check bypasses detection | Correct; explicit `_SERVICES` values also build the four services if listed. |

**AC-2 is PARTIALLY MET.** The detection and routing mechanics work correctly for all services that have `build` blocks in the Stage 0 matrix (`router`, `persona`, research services, `lean`). However, targeted changed-path routing to Cloud Build is NOT wired for `bff`, `governance-api`, `telemetry-ingest`, and `runtime-manager` because they have no `build` block in the matrix. Those four services are only built via `global_changed=true` (full-fleet rebuild) or a manual `workflow_dispatch` with an explicit `_SERVICES` value.

---

### AC-3: `cloudbuild.yaml` builds all Dockerfile-bearing service profiles and publishes to Artifact Registry

| Profile | Services in `cloudbuild.yaml` registry | Dockerfiles present in repo | Notes |
|---|---|---|---|
| core-vm (build-aligned) | `router`, `persona` | `router` ✅, `persona` ✅ | Have `build` blocks in `pantheon-stage0-matrix.json`; appear in `build_ids` for targeted path routing. |
| core-vm (verify-only in matrix) | `bff`, `governance-api`, `telemetry-ingest`, `runtime-manager` | `bff` ✅, `governance-api` ✅ (services/governance/), `telemetry-ingest` ✅ (services/telemetry/), `runtime-manager` ✅ (services/runtime-manager/Dockerfile) | **Routing gap:** in `pantheon-stage0-matrix.json` as `verify`-only targets (no `build` block); NOT emitted in `build_ids` by `ci_stage0.py`; built only on `global_changed=true` or explicit `_SERVICES` override. Dockerfiles and `cloudbuild.yaml` entries are present and correct — the gap is in the Stage 0 matrix, not in `cloudbuild.yaml` itself. |
| research | `mlflow-server`, `dspy-worker`, `imitation-worker`, `research-base`, `research-dspy`, `research-finrl`, `research-imitation`, `research-qlib` | All Dockerfiles present per matrix `build` entries | All have `build` blocks in the matrix; fully aligned for targeted routing. |
| execution-lab (build-aligned) | `lean` | `lean/Dockerfile` ✅ | Has a `build` block in the matrix; targeted routing works. |
| execution-lab (verify-only in matrix) | `runtime-manager` | ✅ (listed above under core-vm verify-only) | Same routing gap as the core-vm verify-only group. |
| manual-only | `incidents`, `postmortems` | `services/incidents/Dockerfile`, `services/postmortems/Dockerfile` | Not in Stage 0 matrix; trigger with explicit `_SERVICES` override. |

**Image naming:** `${_REGISTRY}/${PROJECT_ID}/pantheon/<service-id>` — dual-tag model:
- `:<COMMIT_SHA>` — immutable artifact identity
- `:<_ENV_TAG>` — mutable environment pointer (default: `dev-candidate`)

**Layer-cache strategy:** Each service pulls its `<env-tag>` image before build; failure is non-fatal
(first build produces no cache to seed from).

**AC-3 is MET** as written: `cloudbuild.yaml` contains build entries and Dockerfiles for all 17 services in its registry (6 core-vm + 8 research + 1 execution-lab + 2 manual-only). The AC-3 check is about `cloudbuild.yaml` build coverage, not Stage 0 matrix routing. All Dockerfiles are present. The routing gap noted above is a Stage 0 matrix gap, not a `cloudbuild.yaml` gap.

---

### AC-4: A build manifest is emitted to GCS after every successful build

| Check | Evidence present | Gap / note |
|---|---|---|
| `emit-build-manifest` step present | Yes — Step 2 in `cloudbuild.yaml`, waits for `build-and-push-core` | None. |
| Manifest path follows `gs://<project>-pantheon-builds/manifests/<sha>.json` | Yes | Bucket provisioning is deferred to `BP5-GCP-001` (the `cloudbuild.yaml` comment on the best-effort upload step explicitly reads: "will be provisioned in BP5-GCP-001"); the step is non-fatal if the bucket does not exist yet (`2>/dev/null` + best-effort logic). |
| Manifest content covers artifact identity | `commit_sha`, `env_tag`, `services_filter`, `registry`, `build_id`, `repo_name`, `branch_name`, `built_at` | None. |

**AC-4 is MET** as written; GCS bucket availability depends on BP5-GCP-001 (per source-code evidence).

---

### AC-5: Service IDs in `cloudbuild.yaml` align with `pantheon-stage0-matrix.json` for changed-path routing

#### Services with full build-block alignment (targeted routing works)

| Service / group | In `cloudbuild.yaml` | `build` block in matrix | In `build_ids` on path change | Result |
|---|---|---|---|---|
| `router` | ✅ | ✅ | ✅ | Fully aligned |
| `persona` | ✅ | ✅ | ✅ | Fully aligned |
| All 8 research services | ✅ | ✅ | ✅ | Fully aligned |
| `lean` (execution-lab) | ✅ | ✅ | ✅ | Fully aligned |

#### Services with routing gap (verify-only in Stage 0 matrix)

| Service | In `cloudbuild.yaml` | `build` block in matrix | In `build_ids` on path change | Gap |
|---|---|---|---|---|
| `bff` | ✅ | ❌ (`verify`-only) | ❌ | Path change → `SERVICES=""` → Cloud Build skipped |
| `governance-api` | ✅ | ❌ (`verify`-only) | ❌ | Path change → `SERVICES=""` → Cloud Build skipped |
| `telemetry-ingest` | ✅ | ❌ (`verify`-only) | ❌ | Path change → `SERVICES=""` → Cloud Build skipped |
| `runtime-manager` | ✅ | ❌ (`verify`-only) | ❌ | Path change → `SERVICES=""` → Cloud Build skipped |

These four services ARE built when `global_changed=true` (full-fleet rebuild, e.g. when matrix/workflow files change) or when a manual `workflow_dispatch` lists them explicitly in `_SERVICES`. But targeted changed-path routing via `build_ids` is NOT wired for them.

#### Services in Stage 0 matrix but correctly absent from `cloudbuild.yaml` (no Dockerfile)

| Service | In matrix | Reason absent from `cloudbuild.yaml` |
|---|---|---|
| `runtime-control` | ✅ (`verify`-only) | No Dockerfile; verify-only service |
| `feedback`, `lineage-read`, `signal-store`, `web`, `cron` | ✅ (`verify`-only) | No Dockerfile; verify-only services |

#### Manual-only targets in `cloudbuild.yaml` but absent from Stage 0 matrix (intentional)

| Service | In `cloudbuild.yaml` | In Stage 0 matrix | Notes |
|---|---|---|---|
| `incidents` | ✅ (`DOCKERFILES[incidents]="services/incidents/Dockerfile"` — `cloudbuild.yaml` line 114) | ❌ | Intentional manual-only target; not auto-detected by `ci_stage0.py`; must be triggered via explicit `_SERVICES` override. |
| `postmortems` | ✅ (`DOCKERFILES[postmortems]="services/postmortems/Dockerfile"` — `cloudbuild.yaml` line 115) | ❌ | Intentional manual-only target; not auto-detected by `ci_stage0.py`; must be triggered via explicit `_SERVICES` override. |

**AC-5 has a routing gap.** All auto-detected `cloudbuild.yaml` service IDs (those with `build` blocks in the Stage 0 matrix) are fully aligned between the two files. `incidents` and `postmortems` are manual-only targets present in `cloudbuild.yaml` but intentionally absent from the Stage 0 matrix — they are not unintentional phantom IDs. However, `bff`, `governance-api`, `telemetry-ingest`, and `runtime-manager` exist in the matrix as `verify`-only targets (no `build` block), so `ci_stage0.py` does NOT include them in `build_ids` for targeted path changes. `gcp-deploy.yml` reads only `build_ids` to populate `SERVICES`, so these four services are NOT automatically published to Artifact Registry when only their own paths change. The routing gap requires adding `build` blocks to the Stage 0 matrix for these services (see OQ-6).

---

## 3. Dependency Map

### 3a. Upstream dependencies (must be done before BP5-CICD-002 can fully operate)

| Task | Title | Status | What BP5-CICD-002 requires from it |
|---|---|---|---|
| BP5-CICD-001 | Implement GitHub Actions stage-0 CI and changed-path gating | **done** | `scripts/ci_stage0.py detect-changes` command used in the detect step; `pantheon-stage0-matrix.json` used for service-ID alignment |
| BP5-SVC-016 | Package the honest service stack into Docker, compose, and smoke topology | **done** | All core-vm Dockerfiles that `cloudbuild.yaml` builds |

Both upstream dependencies are confirmed done in `ai-status.json`.

### 3b. Downstream tasks unblocked by BP5-CICD-002

| Task | Title | Owner | What it needs from BP5-CICD-002 |
|---|---|---|---|
| BP5-GCP-001 | Stand up workload identity and Secret Manager baseline | (Wave 4) | OIDC auth in `gcp-deploy.yml` uses the Workload Identity pool that BP5-GCP-001 provisions; GCS bucket `${PROJECT_ID}-pantheon-builds` (build manifests) is also attributed to BP5-GCP-001 per the `cloudbuild.yaml` best-effort upload comment |
| BP5-GCP-002 | Stand up Cloud SQL, Pub/Sub, ingress, and nonprod environment | (Wave 4) | Broader environment foundation for deployed images; no direct dependency on GCS manifest bucket |

Note: BP5-GCP-001 and BP5-GCP-002 are partially parallel; the gcp-deploy workflow will start
failing at the `auth` step until BP5-GCP-001 sets up the WIF pool and SA bindings.

---

## 4. Delivery Inventory

All files produced or modified by BP5-CICD-002:

| File | Kind | Status |
|---|---|---|
| `.github/workflows/gcp-deploy.yml` | GitHub Actions workflow | Present and fully wired |
| `cloudbuild.yaml` | Cloud Build config | Present; 17-service registry (6 core-vm + 8 research + 1 execution-lab + 2 manual-only); serial build loop within a single Cloud Build step; dual-tag image naming |

Supporting files that BP5-CICD-002 depends on but did not create:

| File | Owned by | Role |
|---|---|---|
| `scripts/ci_stage0.py` | BP5-CICD-001 | Changed-path detection script re-used in `gcp-deploy.yml` |
| `.github/pantheon-stage0-matrix.json` | BP5-CICD-001 | Service-ID registry; aligns `cloudbuild.yaml` build targets |
| `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md` | Architecture docs | Out-of-band GCP setup instructions referenced in `gcp-deploy.yml` comments |

---

## 5. Open Questions for BP5-CICD-002 Owner

| ID | Question | Risk if unresolved |
|---|---|---|
| OQ-1 | Are `incidents` and `postmortems` manual-only builds intentional long-term, or should they eventually be added to `pantheon-stage0-matrix.json` for auto-detection? | If auto-detection is intended, the matrix must be extended and the services verified in Stage 0 CI. |
| OQ-2 | The `gcp-deploy.yml` uses `actions/checkout@v4` but `stage-0-ci.yml` uses `@v6`. Should these be aligned? | Minor version drift; no functional gap today, but a future auto-updater may create inconsistency. |
| OQ-3 | The build manifest is best-effort non-fatal. Should downstream `BP5-GCP-002` make the GCS bucket the formal acceptance gate for BP5-CICD-002, or is best-effort acceptable until GCP-002 lands? | If the manifest is load-bearing for deployment automation, it needs a non-best-effort path before the publish flow is declared production-ready. |
| OQ-4 | `runtime-control` does not have a Dockerfile and is therefore absent from `cloudbuild.yaml`. Is this gap acknowledged and deferred to a future CICD task, or should it be resolved within BP5-CICD-002 scope? | `runtime-control` is a core-vm service; if it needs a container, the owner should add a Dockerfile to `services/control_plane/` and a `cloudbuild.yaml` entry. |
| OQ-5 | `cloudbuild.yaml` uses `machineType: E2_HIGHCPU_8`. Has this been validated as sufficient for the serial Docker build loop over 17 services in a single Cloud Build step? | Builds run serially (for-loop), so memory pressure is bounded to one build at a time, but total wall-clock time may be long. If build timeouts are observed, the owner may consider splitting into parallel Cloud Build steps or upgrading the machine type. |
| OQ-6 | `bff`, `governance-api`, `telemetry-ingest`, and `runtime-manager` have no `build` block in `pantheon-stage0-matrix.json`. As a result, `ci_stage0.py` never emits them in `build_ids`, and `gcp-deploy.yml` does NOT trigger Cloud Build for them on targeted path changes. Should `build` blocks (with Dockerfile paths) be added to the Stage 0 matrix for these four services so that targeted changed-path routing also covers them? | Until resolved, a push that only touches `services/control-plane/bff/**` (for example) will NOT publish a new `bff` image — the change will only be verified by Stage 0 CI, not published. Images are only published for these services on global-path changes or manual `workflow_dispatch` overrides. |

---

## 6. Acceptance Summary

### Canonical acceptance criteria (from archived task snapshot)

| Criterion | Status | Supporting checks | Notes |
|---|---|---|---|
| AC-A: "GitHub CI, Cloud Build, and Artifact Registry form one repo-to-build-to-image path" | **MET with routing gap** | AC-1 (workflow wiring + Cloud Build submission), AC-3 (17-service Artifact Registry publish), AC-5 (ID alignment) | The repo-to-Cloud Build-to-Artifact Registry path is fully wired and works. However, four services (`bff`, `governance-api`, `telemetry-ingest`, `runtime-manager`) are not reached by targeted changed-path routing — they are only built on `global_changed=true` or manual override (OQ-6). Whether this is in scope for AC-A is for the parent-task owner to decide. |
| AC-B: "the publish flow works without long-lived GCP keys embedded in GitHub" | **MET** | AC-1 (OIDC/WIF auth step; no long-lived `GCP_SA_KEY`), AC-2 (no secrets embedded for changed-path detection) | |

The two upstream dependencies (BP5-CICD-001, BP5-SVC-016) are both **done**. Six open questions remain for the parent-task owner to acknowledge or resolve (OQ-1 through OQ-6).

The publish pipeline is architecturally complete but cannot execute end-to-end until BP5-GCP-001
provisions the Workload Identity pool, SA bindings, and GCS manifest bucket
(`${PROJECT_ID}-pantheon-builds`).

**Routing gap summary (for parent-task owner disposition):** `bff`, `governance-api`, `telemetry-ingest`, and `runtime-manager` have Dockerfiles and `cloudbuild.yaml` entries, but lack `build` blocks in `pantheon-stage0-matrix.json`. `ci_stage0.py` therefore does not emit them in `build_ids`, and `gcp-deploy.yml` does not submit Cloud Build for them on targeted path-only pushes. They are covered by full-fleet rebuilds (`global_changed=true`) and manual overrides. The owner should either add `build` blocks to the matrix (closing OQ-6) or document this as an intentional verify-only / manual-publish design decision.

---

*Sidecar prepared by Claude. Helper kind: `acceptance_packet`. Parent task: `BP5-CICD-002`.*
*Hand-off target: Codex (reviewer: BP5-CICD-002-SIDECAR-ACCEPTANCE).*
