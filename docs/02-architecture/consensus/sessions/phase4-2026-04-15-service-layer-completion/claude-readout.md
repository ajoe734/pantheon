# Claude Readout — Phase 4: Service Layer Completion (Round 1 Refresh)

## Lane

- Agent: Claude
- Role: Facilitator + control-plane architecture reviewer
- Refresh trigger: `phase2-phase6-gap-inventory.md` added as canonical planning input at 2026-04-15T11:55:00Z; this readout supersedes the Round 0 version.

## Canonical Sources Read

- L0: `ai-status.json`, `current-work.md`
- L1: `TARGET_ARCHITECTURE.md`, `BINDING_AND_DEPLOYMENT_SEMANTICS.md`, `PERSONA_RUNTIME_MODEL.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, `LINEAGE_AND_TELEMETRY_STORAGE_DECISIONS.md`, `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `CROSS_SERVICE_CONSISTENCY_AND_SAGA_POLICY.md`
- L2: `ROADMAP.md`, `DEVELOPMENT_WORKBREAKDOWN.md`, `OSS_INTEGRATION_CHECKLIST.md`, `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md`, `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md`
- Session: `phase2-phase6-gap-inventory.md`, `starter-draft.md`, `codex-readout.md`, `gemini-readout.md`, `planning-session.json`
- Repo direct: `services/control-plane/bff/command_executor.py`, `services/control-plane/bff/read_store.py`, `services/control_plane/internal_api.py`

---

## 1. What the Gap Inventory Changes

The core interpretation shift from the gap inventory is that phases 2–6 have **canonical baseline complete** but are still **delivery incomplete**. This distinction matters for task scoping.

The inventory makes explicit what the Round 0 readout treated as a risk: the BFF snapshot fallback, the evolution placeholder commands, and the missing Dockerfiles are not deferred cleanup tasks — they are **blocking items** for any honest single-VM smoke test. The six-slice plan in `starter-draft.md` correctly identifies the compose-critical bridge. The remaining question this readout must answer is: does that bridge have the right shape, and are the open disagreements actually risks to execution?

---

## 2. Cited Observations

### OBS-01: Evolution command boundary is a concrete code gap, not a design ambiguity

`services/control-plane/bff/command_executor.py:157–183` (both `_execute_approve_evolution_decision` and `_execute_evolution_action`) explicitly say "internal API not yet defined; record decision locally." These functions return locally constructed dicts with no HTTP dispatch. This is different from the deployment/pause/rollback/kill-switch commands, which already POST to `_INTERNAL_API_BASE` at `:5001`.

Implication: the `SVC-RUNTIME-CONTROL` slice must resolve this. Either the evolution endpoints are added to `internal_api.py` (Flask, `:5001`) or they land in a new `governance-api` service. The open disagreement in `starter-draft.md` about where evolution endpoints live is a real execution blocker, not just an architectural preference.

**Cite**: `services/control-plane/bff/command_executor.py:157–183`; `phase2-phase6-gap-inventory.md §5 Phase 4`

### OBS-02: `internal_api.py` lazy-imports kill_switch_controller via a relative file path

`services/control_plane/internal_api.py:26–56` resolves `_KILL_SWITCH_MODULE_PATH` relative to `__file__` and uses `importlib.util.spec_from_file_location`. In a Docker container, this requires the runtime-manager source to be present at the expected relative path. The Dockerfile for runtime-control must either COPY both modules into the image or mount both paths. If internal_api.py is simply containerized as-is, the service will fail on first kill-switch invocation unless this import path is explicitly tested.

Implication: `SVC-RUNTIME-CONTROL` acceptance criteria must include a live kill-switch invocation through the container, not just a health endpoint.

**Cite**: `services/control_plane/internal_api.py:26–56`

### OBS-03: BFF read_store is definitionally incompatible with a composable service stack

`services/control-plane/bff/read_store.py:43–175` expresses the fallback chain: first try env-addressed JSON snapshot file, else return `_default_read_data()` seed. In a compose stack where `PANTHEON_GOVERNANCE_DATA_DIR` and `PANTHEON_RUNTIME_DATA_DIR` are not populated with live service output, the BFF will silently serve seed data. No compose smoke test can distinguish "BFF backed by real services" from "BFF serving seeds" without explicitly verifying that the backing services' write paths produced data that the BFF then reads.

Implication: `SVC-SURFACES` acceptance criteria should require end-to-end write → read verification (e.g., POST a deployment plan to governance-api, then confirm BFF returns that plan from its read surface), not just a health endpoint.

**Cite**: `services/control-plane/bff/read_store.py:43–175`; `phase2-phase6-gap-inventory.md §5 Phase 5`

### OBS-04: internal_api.py is Flask, not FastAPI

`services/control_plane/internal_api.py:12` imports Flask. All other deployable services in the repo use FastAPI. Containerizing internal_api as the long-lived `runtime-control` service would introduce a mixed-framework dependency surface. This is not a blocking issue, but reviewers should decide whether `SVC-RUNTIME-CONTROL` should: (a) wrap the Flask app as-is in its own Dockerfile, or (b) migrate to FastAPI for consistency.

**Cite**: `services/control_plane/internal_api.py:12`; `services/control-plane/router/Dockerfile:13-15` (FastAPI/uvicorn pattern)

### OBS-05: Port collision is confirmed and must be resolved before Compose assembly

`services/control-plane/router/Dockerfile` exposes `8001`. `services/control-plane/bff/main.py` runs its local dev runner on `8001`. This is confirmed in `codex-readout.md [R2]` and `gemini-readout.md §2.3`. The `SVC-BASELINE` slice must emit a committed port map. Gemini's proposed map (Router:8001, BFF:8003, Runtime-control:8004, Governance:8005, Telemetry:8006, Lineage:8007) is the most concrete proposal on the table and should be the starting point for consensus.

**Cite**: `services/control-plane/router/Dockerfile`; `codex-readout.md [R2]`; `gemini-readout.md §2.3`

### OBS-06: Postgres is completely absent from the current docker-compose.yml

The existing `docker-compose.yml` has nine services: lean, signal-store (Redis), router, persona, dspy-worker, qlib-worker, finrl-worker, imitation-worker, mlflow-server. No Postgres. No MinIO. No ClickHouse. Any governance or telemetry service that writes to persistent storage in the test environment needs this resolved before `SVC-COMPOSE` is meaningful.

**Cite**: `docker-compose.yml:22-100`; `phase2-phase6-gap-inventory.md §5 Phase 3`

### OBS-07: Phase 5 workbench and Phase 6 OSS adapter state

- Phase 5 workbench expansion: `current-work.md` shows 11 Lovable-ready packets, 9 waiting for front-end, 0 returned `ui-done`, 2 with frontend feedback. This is active inflight work. It is correctly scoped as follow-on after the compose stack is runnable.
- Phase 6 OSS: `OSS_INTEGRATION_CHECKLIST.md` places OpenClaw at `adapter-started`, DSPy/imitation/MLflow at `smoke-tested`, and everything else at `criteria-defined` or `version-pinned`. None of these are compose-critical for the first single-VM smoke stack. The `openclaw-adapter-svc` stub approach (health + passthrough) from the starter-draft is correct for this wave.

**Cite**: `phase2-phase6-gap-inventory.md §5 Phase 5 and Phase 6`; `OSS_INTEGRATION_CHECKLIST.md`

---

## 3. Positions on Open Disagreements (starter-draft.md)

### Q1: Should `internal_api.py` become the long-lived `runtime-control` service, or only a temporary adapter?

**Position**: Wrap `internal_api.py` as-is for the first compose-critical wave; schedule a FastAPI migration as a follow-on. Reason: the Flask app already dispatches real kill-switch, pause, rollback, and deployment-approval commands through the KillSwitchController. Rewriting it before the stack is composable adds risk without adding compose-blocking value. The acceptance criteria for `SVC-RUNTIME-CONTROL` should require an explicit test that the lazy import path works correctly from inside the Docker container.

### Q2: Where do evolution approval/action endpoints live — runtime-control or governance-api?

**Position**: Evolution approval/action endpoints belong in `governance-api`. Reason: `TARGET_ARCHITECTURE.md §3` separates the side-effectful runtime command path (kill-switch, pause, rollback, binding mutation) from the approval/governance flow (ApprovalDecision, EvolutionDecision). The runtime-control service owns real-time intervention; governance-api owns the approval lifecycle. Putting evolution commands in runtime-control would conflate these two planes. The concrete fix is: add `/evolution-decisions/{id}/approve` and `/evolution-decisions/{id}/execute-action` to the governance-api service, and update `command_executor.py:157–183` to POST to `PANTHEON_GOVERNANCE_API_URL` instead of recording locally.

### Q3: Must BFF rewiring happen in the same wave as Dockerization?

**Position**: Yes, and the `SVC-SURFACES` acceptance criteria must require it. Reason: as OBS-03 shows, BFF Dockerization without rewiring produces a container that silently serves seed data. The smoke test cannot verify integration unless BFF is genuinely reading from service-produced data. Shipping "BFF Dockerized but still on snapshots/defaults" would be a false positive on compose completion. The additional work is small: replace `CanonicalSnapshotAdapter` read paths with HTTP client calls to governance-api and runtime-control, both of which will be live services at that point.

### Q4: Should `web` and `cron` be in the default single-VM profile?

**Position**: Keep both out of the default profile for this wave. `services/channels/web/main.py` is a thin proxy to router and has no backing state — it adds no integration value to the compose smoke test. `cron` would require its own workflow-runner packaging. Both can be `--profile optional` entries in the compose file, but they should not gate the compose smoke acceptance criteria.

---

## 4. Facilitator Notes: Cross-Readout Coverage

### Readout status as of this refresh

| Lane    | Status          | Key contribution |
|---------|-----------------|-----------------|
| Codex   | submitted       | Repo evidence, concrete service class citations, six-slice table |
| Gemini  | submitted (needs_refresh flag) | Port collision, resource limits, Golden Replay dependency on telemetry/lineage serviceization |
| Qwen    | not yet submitted | Schema/contract boundary review for governance-api and evolution endpoints is still missing |
| Copilot | not yet submitted | Research readiness, external source assumptions, and acceptance wording review is still missing |

### Gaps in current readout coverage

1. **Schema and contract formalization** for the governance-api surface is not yet reviewed. Qwen's lane focus (object boundaries, contract gaps) is specifically the right input for OBS-01 and the evolution endpoint placement decision. The current readouts do not provide any analysis of what the `governance-api` HTTP surface should look like beyond "expose the existing domain objects."

2. **Research and data service acceptance criteria** are not yet reviewed. The `data-ingest-svc`, `data-catalog-svc`, and `feature-svc` stubs are included in the scope but their acceptance wording in starter-draft.md is thin. Copilot's review is needed before the consensus packet can close the `SVC-COMPOSE` acceptance criteria.

3. **Golden Replay acceptance criteria** are referenced by Gemini but not formalized. `GOLDEN_REPLAY_SCENARIO_AND_RUNBOOK.md` (referenced by Gemini but not in the current brief file set) should be confirmed as in-scope or out-of-scope for this wave's smoke test. Clarifying this gates the `SVC-EVIDENCE` acceptance criteria.

### What the consensus packet needs from missing readouts

- From Qwen: proposed HTTP interface shapes for governance-api (approval/deployment/evolution endpoints); opinion on whether evolution command endpoint belongs in runtime-control or governance-api
- From Copilot: acceptance wording for research/data stub services; assessment of whether `data-ingest-svc` as a thin wrapper over existing ingest adapters is safe or introduces a new external dependency path

---

## 5. Risk Synthesis

| Risk | Source | Severity | Resolution slot |
|------|--------|----------|-----------------|
| Evolution commands are local placeholders with no dispatch URL | OBS-01, `command_executor.py:157-183` | Blocking | `SVC-GOVERNANCE-API` acceptance criteria |
| `internal_api.py` lazy file-path import breaks in Docker without explicit path setup | OBS-02, `internal_api.py:26-56` | High | `SVC-RUNTIME-CONTROL` acceptance criteria must include live kill-switch from container |
| BFF silently serves seed data even after Dockerization unless rewiring is in the same wave | OBS-03, `read_store.py:43-175` | High | `SVC-SURFACES` acceptance criteria must require end-to-end write → read verification |
| Flask vs FastAPI mismatch in runtime-control vs all other services | OBS-04 | Low (deferred) | Explicitly called out in `SVC-RUNTIME-CONTROL`; FastAPI migration as follow-on |
| Port collision: router and BFF both bind 8001 | OBS-05 | Blocking for compose | `SVC-BASELINE` must emit committed port map |
| Postgres absent from compose; no persistent storage for governance or telemetry services | OBS-06 | Blocking for compose | `SVC-BASELINE` must include infrastructure services |
| Phase 5 workbench expansion and Phase 6 OSS adapters not in scope of first wave | OBS-07 | Correctly scoped | No action; confirm in consensus packet |

---

## 6. Recommended Additions to Task Acceptance Criteria

These are additive to `starter-draft.md`; I am not rewriting the draft. Codex should incorporate these into the acceptance criteria when the starter-draft is next updated.

**SVC-BASELINE** — must also specify:
- The committed port map for all services (using Gemini's proposal as the base)
- Which infrastructure services (postgres, redis/nats, minio) are in the default compose profile
- The `PANTHEON_GOVERNANCE_DATA_DIR` and `PANTHEON_RUNTIME_DATA_DIR` volume mount contract

**SVC-RUNTIME-CONTROL** — must also specify:
- Live kill-switch command invocation from inside the Docker container (not just `/health` passing)
- That the `_KILL_SWITCH_MODULE_PATH` env var is tested/documented for container use
- Resolution of the evolution command endpoint placement (per Q2 position above)

**SVC-GOVERNANCE-API** — must also specify:
- `/evolution-decisions/{id}/approve` and `/evolution-decisions/{id}/execute-action` endpoints are present
- `command_executor.py:157-183` is updated to dispatch to `PANTHEON_GOVERNANCE_API_URL`

**SVC-SURFACES** — must also specify:
- End-to-end write → read verification: POST a deployment plan to governance-api, confirm BFF reads it back from its read surface (not from default seed)
- BFF `CanonicalSnapshotAdapter` is replaced (or gated behind an explicit "test bootstrap" flag, not the default path)

---

## 7. Facilitator Recommendation for Next Steps

1. **This readout is submitted.** Update planning state accordingly.
2. **Qwen and Copilot are next.** Readout coverage gaps for schema/contract and research acceptance wording are the principal blocking items for consensus packet completion.
3. **Do not draft the consensus packet until Qwen submits.** The evolution endpoint placement decision (Q2) depends on Qwen's schema review. The six-slice table can be accepted as the correct first-wave scope; the internal shape of `SVC-GOVERNANCE-API` and its acceptance criteria cannot be finalized without Qwen.
4. **Gemini's port map proposal** (Router:8001, Persona:8002, BFF:8003, Runtime-control:8004, Governance:8005, Telemetry:8006, Lineage:8007) is the most concrete artifact ready for adoption. Codex should adopt it or propose an explicit alternative in the next starter-draft update.

---

## Citations

- [R1] `services/control-plane/bff/command_executor.py:157–183` — evolution commands are local placeholders with no internal API dispatch
- [R2] `services/control-plane/bff/command_executor.py:21–25` — `PANTHEON_INTERNAL_API_URL` defaults to `http://localhost:5001`; deployment/pause/rollback already dispatch there
- [R3] `services/control_plane/internal_api.py:12` — Flask import; all other deployed services use FastAPI
- [R4] `services/control_plane/internal_api.py:26–56` — lazy file-path import of kill_switch_controller; will break in Docker without explicit path configuration
- [R5] `services/control-plane/bff/read_store.py:43–175` — snapshot → default seed fallback; confirmed as main BFF read path
- [R6] `docker-compose.yml:22-100` — current nine services; Postgres, MinIO, BFF, governance, telemetry absent
- [C1] `phase2-phase6-gap-inventory.md §5` — per-phase residual gap analysis; source for all cross-phase gap claims
- [C2] `phase2-phase6-gap-inventory.md §6` — priority order: SVC-BASELINE → SVC-RUNTIME-CONTROL → SVC-GOVERNANCE-API → SVC-EVIDENCE → SVC-SURFACES → Phase 5 workbench → Phase 6 real integrations
- [C3] `starter-draft.md` — six-slice plan and open disagreements
- [C4] `codex-readout.md [R2, R3, R4]` — repo-evidence citations for port collision, BFF snapshot path, and evolution placeholder
- [C5] `gemini-readout.md §2.3` — port map proposal and resource-limit recommendation
- [C6] `TARGET_ARCHITECTURE.md §3` — responsibility split: runtime-control vs governance plane
- [C7] `OSS_INTEGRATION_CHECKLIST.md` — OpenClaw `adapter-started`; DSPy/imitation/MLflow `smoke-tested`; everything else `criteria-defined` or `version-pinned`
