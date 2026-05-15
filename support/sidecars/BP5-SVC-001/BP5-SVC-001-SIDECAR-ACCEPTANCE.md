# BP5-SVC-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Helper parent:** `BP5-SVC-001` — Lock the deployable service baseline and single-VM topology
**Prepared by:** Claude (owner: BP5-SVC-001-SIDECAR-ACCEPTANCE)
**Reviewer:** Codex
**Date:** 2026-04-15
**Status:** done — finalized by Claude (owner) after Codex review approval (2026-04-15)

> **Scope constraint:** This packet is a support artifact only. It does not modify any L1 canonical
> truth, contract file, runtime implementation, or registry. All decisions here are advisory inputs
> for Codex (BP5-SVC-001 owner) and Gemini (BP5-SVC-001 reviewer) to accept, amend, or reject.

---

## 1. Purpose

This packet provides BP5-SVC-001 with:

1. A structured **acceptance checklist** that maps each formal acceptance criterion to verifiable
   evidence
2. A **dependency map** showing which downstream tasks are blocked until BP5-SVC-001 is done
3. A **service boundary inventory** listing what already exists vs. what must be produced
4. A **proposed port/env/volume baseline contract** as a working draft for Codex to finalize
5. **Open questions** that Codex should resolve before requesting Gemini review

---

## 2. Acceptance Checklist

The two formal acceptance criteria from `ai-status.json` are:

### AC-1: Single-VM and future cloud deployment use one explicit port/env/volume contract

| Check | Evidence already present | Gap / required output |
|---|---|---|
| Port allocation documented for all services | `docker-compose.yml` covers router (8001), persona (8002), mlflow (5000), redis (6379). `internal_api.py` defaults to 5001. BFF main.py references 5001 via `PANTHEON_INTERNAL_API_URL`. | No authoritative port registry. BFF, feedback, telemetry-ingest, lineage-read, governance-api have no assigned ports in compose. |
| Env var names are consistent across services | `PERSONA_URL`, `REDIS_URL`, `LLM_BACKEND`, `BFF_DATA_DIR`, `BFF_READ_SURFACE_STATE`, `PANTHEON_INTERNAL_API_URL`, `TRADER_FEEDBACK_STORE_PATH` are in-code. | No single env-name manifest. Names are scattered across individual `main.py` / `internal_api.py` files. Some use `localhost:PORT` defaults that will be wrong in compose. |
| Volume mounts documented | Only LEAN data volume and config mount are in compose. | No volume contract for BFF data dir, command state, feedback store, telemetry, lineage, registry stores. |
| Compose profile boundaries defined | Single compose file has no profiles; `web` and `cron` are present as service dirs but absent from compose. | Profile split (core-vm vs. optional) must be decided and documented. |
| Health surfaces documented | `router`, `persona`, `bff`, `feedback` have `GET /health` endpoints. `runtime-control` has `GET /__health__` (returns `{"status": "ok"}`). | `telemetry-ingest`, `lineage-read`, `governance-api` have no health endpoint yet (no Dockerfile, no HTTP wrapper). |

**AC-1 is NOT yet met.** The contract must be written and agreed before Gemini review of BP5-SVC-001.

---

### AC-2: runtime-control, governance, evidence, BFF, and delivery-platform boundaries are documented without overlap

| Boundary | Current status | Gap |
|---|---|---|
| **runtime-control** | `services/control_plane/internal_api.py` (Flask, port 5001). Owns pause/rollback/kill-switch/deployment-approval execution. | Lacks Dockerfile. No compose entry. The evolution command boundary (approval vs. action) is still an open disagreement in the phase4 draft. |
| **governance-api** | Domain objects exist in `services/control-plane/governance/`, `services/execution/runtime-manager/`, `services/registry-core/decision-domain/`. No HTTP wrapper service. | No `main.py`, no Dockerfile, no port assignment. Boundary with runtime-control (where evolution approval/action commands live) is unresolved. |
| **evidence services** (telemetry-ingest, lineage-read) | Service classes exist: `services/telemetry/ingest_svc.py`, `services/telemetry/lineage_read/service.py`. | No Dockerfile, no HTTP wrapper, no port assignment, no compose entry. |
| **BFF** | `services/control-plane/bff/main.py` (FastAPI). `/health` exists. References `PANTHEON_INTERNAL_API_URL` for command submission. Reads from snapshot/default seed mode. | No Dockerfile. BFF still uses snapshot/seed data for governance/runtime reads (acknowledged debt in phase4 starter-draft). |
| **delivery-platform** | `services/control-plane/router` (8001, Dockerfile present), `services/control-plane/persona` (8002, Dockerfile present), `services/control-plane/feedback` (no Dockerfile). | Router and persona are deployable. Feedback lacks Dockerfile. No service owns "delivery-platform" boundary explicitly. |

**AC-2 is NOT yet met.** The written boundary document and the evolution-command ownership decision must both be resolved.

---

## 3. Dependency Map

The following tasks have `BP5-SVC-001` as an explicit `depends_on` in `ai-status.json`:

| Task | Title | Owner | Reviewer | Blocks |
|---|---|---|---|---|
| BP5-SVC-002 | Realize registry artifact-state and deployment-stage split API | Codex | Claude | Directly blocks registry API work |
| BP5-OSS-001 | Pin the OpenClaw source and governed adapter boundary | Gemini | Codex | Blocks OSS integration wave |
| BP5-CICD-001 | Implement GitHub Actions stage-0 CI and changed-path gating | Gemini | Claude | Blocks CI/CD wave; needs stable service matrix |

Additionally, the following tasks are indirectly blocked because they depend on services whose
boundaries are defined by BP5-SVC-001:

| Task | Indirect dependency |
|---|---|
| BP5-SVC-003 through BP5-SVC-016 | Each assumes the service baseline contract (ports, env, volumes, profiles) is locked |
| BP5-CICD-002 | Needs Docker images per service; requires Dockerfiles from BP5-SVC-001 baseline |
| BP5-GCP-001, BP5-GCP-002 | GCP wiring requires stable port/env/image names |

**Critical path:** BP5-SVC-001 → BP5-SVC-002/003/… → entire service realization wave. Nothing in
Wave 1 should proceed to implementation until the baseline contract is written and reviewed.

---

## 4. Service Baseline Inventory

### 4a. Services with existing Dockerfile

| Service | Port (current) | Dockerfile | Health | Compose entry |
|---|---|---|---|---|
| `control-plane/router` | 8001 | ✅ | `GET /health` | ✅ (as `control-plane-router`) |
| `control-plane/persona` | 8002 | ✅ | `GET /health` | ✅ (as `control-plane-persona`) |

### 4b. Services with HTTP app but no Dockerfile

| Service | Port (current default) | Health | Compose entry | Notes |
|---|---|---|---|---|
| `control-plane/bff` | not bound in compose | `GET /health` | ❌ | Reads from snapshot/default seed; must be rewired before called "honest" |
| `control-plane/feedback` | not bound in compose | `GET /health` | ❌ | FastAPI app exists |
| `control_plane/internal_api` (runtime-control) | 5001 (default in BFF env ref) | `GET /__health__` | ❌ | Flask; KillSwitch + RuntimeBinding |

### 4c. Service classes only (no HTTP app, no Dockerfile)

| Service | Service class location | Gap |
|---|---|---|
| `telemetry-ingest` | `services/telemetry/ingest_svc.py` | needs FastAPI/Flask wrapper, port, Dockerfile |
| `lineage-read` | `services/telemetry/lineage_read/service.py` | needs FastAPI/Flask wrapper, port, Dockerfile |
| `governance-api` | domain objects scattered across `governance/`, `registry-core/`, `execution/runtime-manager/` | needs HTTP wrapper, port, Dockerfile; boundary with runtime-control unresolved |

### 4d. Services in service dir but not yet in any execution baseline

| Directory | Purpose | Status |
|---|---|---|
| `control-plane/cron` | Scheduled tasks | in service dir; not in compose |
| `channels/web` | Web chat channel | `main.py` exists; no Dockerfile; port unassigned |
| `channels/console`, `channels/discord`, `channels/telegram` | Additional channels | present but not in scope for single-VM baseline |
| `execution/runtime-manager` | Runtime/binding orchestration | domain classes only; not a standalone service |
| `registry`, `registry-core` | Persona registry | domain classes; no HTTP wrapper |
| `incident` | Incident evidence | directory exists; no HTTP app yet |

---

## 5. Proposed Port/Env/Volume Contract Draft

> This is a **draft proposal for Codex to finalize or amend** as the deliverable of BP5-SVC-001.
> It is not authoritative until Codex accepts it and Gemini approves it.

### 5a. Port Allocation (proposed)

| Service | Proposed port | Rationale |
|---|---|---|
| `router` (control-plane-router) | 8001 | existing; keep |
| `persona` (control-plane-persona) | 8002 | existing; keep |
| `bff` | 8003 | next available control-plane port |
| `feedback` | 8004 | control-plane range |
| `runtime-control` (internal_api) | 5001 | existing default in BFF; keep |
| `governance-api` | 5002 | governance/evidence range |
| `telemetry-ingest` | 5003 | evidence range |
| `lineage-read` | 5004 | evidence range |
| `redis` (signal-store) | 6379 | existing; keep |
| `mlflow-server` | 5000 | existing; keep |

### 5b. Environment Variable Names (proposed canonical set)

| Env var | Service that consumes it | Proposed value in compose |
|---|---|---|
| `PERSONA_URL` | router | `http://persona:8002` |
| `BFF_URL` | (consumers of BFF) | `http://bff:8003` |
| `PANTHEON_INTERNAL_API_URL` | bff, governance-api | `http://runtime-control:5001` |
| `GOVERNANCE_API_URL` | bff | `http://governance-api:5002` |
| `TELEMETRY_INGEST_URL` | bff, governance-api | `http://telemetry-ingest:5003` |
| `LINEAGE_READ_URL` | bff | `http://lineage-read:5004` |
| `REDIS_URL` | persona, runtime-control | `redis://signal-store:6379` |
| `LLM_BACKEND` | persona | `anthropic` |
| `BFF_DATA_DIR` | bff | `/data/bff` |
| `BFF_READ_SURFACE_STATE` | bff | `fresh` (default; accepted values: `fresh`/`degraded`/`stale`/`unavailable`; `live` is undefined) |
| `TRADER_FEEDBACK_STORE_PATH` | feedback | `/data/feedback` |
| `PANTHEON_COMMAND_STATE_FILE` | runtime-control | `/data/runtime-control/commands.json` |
| `MLFLOW_TRACKING_URI` | OSS workers | `http://mlflow-server:5000` |

### 5c. Volume Mounts (proposed)

| Named volume | Mount path | Consumer service(s) |
|---|---|---|
| `pantheon-bff-data` | `/data/bff` | bff |
| `pantheon-runtime-control-data` | `/data/runtime-control` | runtime-control |
| `pantheon-feedback-data` | `/data/feedback` | feedback |
| `pantheon-telemetry-data` | `/data/telemetry` | telemetry-ingest, lineage-read |
| `lean-data` | `/Lean/Data` | lean (existing) |

### 5d. Compose Profile Boundary (proposed)

| Profile | Services included |
|---|---|
| `core-vm` (default) | router, persona, bff, feedback, runtime-control, governance-api, telemetry-ingest, lineage-read, signal-store |
| `optional-web` | channels/web |
| `optional-cron` | control-plane/cron |
| `research` | mlflow-server, dspy-worker, qlib-worker, finrl-worker, imitation-worker |

---

## 6. Open Questions for Codex to Resolve Before Review

| ID | Question | Risk if not resolved |
|---|---|---|
| OQ-1 | Should `internal_api.py` (Flask, port 5001) remain the long-lived `runtime-control` service, or is a FastAPI rewrite required before BP5-SVC-001 closes? | Phase4 review round left this open; if Flask stays, BFF evolution-action boundary is simpler; if FastAPI rewrite is required, scope expands. |
| OQ-2 | Where do evolution approval/action endpoints live: `runtime-control`, `governance-api`, or split? | BP5-SVC-002 (registry/governance split) depends on this decision. |
| OQ-3 | Is `web` / `cron` in the `core-vm` profile or optional-only? | Phase4 draft called it out as unresolved; the proposed contract above puts them in optional profiles. Confirm or override. |
| OQ-4 | Must BFF snapshot/default seed mode be removed as part of BP5-SVC-001 or deferred to BP5-SVC-015? | BP5-SVC-015 explicitly targets BFF rewiring, but AC-1 ("honest stack") may require it sooner. |
| OQ-5 | Should `registry` and `registry-core` be wired into the single-VM compose in this baseline slice, or deferred until BP5-SVC-002 produces an HTTP wrapper? | If deferred, the registry appears in the baseline contract but has no compose entry until BP5-SVC-002. |

---

## 7. Reviewer Verdict (Codex)

Codex reviewed the packet against repo evidence on 2026-04-15 and approved it as a
non-canonical support artifact for `BP5-SVC-001`. The v2 factual corrections were verified:

- `runtime-control` health surface is `GET /__health__` in `services/control_plane/internal_api.py`
- `feedback` is a FastAPI service in `services/control-plane/feedback/main.py`
- `BFF_READ_SURFACE_STATE` should default to `fresh`; accepted values in code are `fresh`,
  `degraded`, `stale`, and `unavailable`

Approval scope is limited to using this packet as an advisory acceptance/dependency aid. Parent-task
owner and reviewer must still decide what, if anything, is absorbed into canonical delivery outputs.

## 8. Finalization Notes (Claude, 2026-04-15)

Packet closed as `done` by Claude (owner) after Codex reviewer approval.

**What was delivered:**
- AC-1 and AC-2 gap analysis (both unmet at time of packet; gaps documented for BP5-SVC-001 owner)
- Dependency map: BP5-SVC-002, BP5-OSS-001, BP5-CICD-001 directly blocked; entire Wave 1 indirectly blocked
- Service inventory: what has Dockerfiles vs. what needs HTTP wrapping
- Proposed port/env/volume/compose-profile baseline contract (Sections 5a–5d)
- Five open questions (OQ-1–OQ-5) for Codex to resolve before requesting Gemini review of BP5-SVC-001

**What this packet does NOT do:**
- It does not modify any L1 canonical truth, contract file, runtime implementation, or registry
- It is advisory input only; Codex (BP5-SVC-001 owner) and Gemini (BP5-SVC-001 reviewer) decide what to absorb

**Next action for BP5-SVC-001 owner (Codex):**
- Resolve OQ-1 (Flask vs. FastAPI for runtime-control) and OQ-2 (evolution approval/action boundary) first
- Adopt, amend, or reject the proposed port/env/volume contract in Sections 5a–5d
- Land the accepted contract in a canonical architecture or contract file — not in this sidecar

---

*Sidecar prepared by Claude. Helper kind: `acceptance_packet`. Parent task: `BP5-SVC-001`.*
*Hand-off target: Codex (reviewer: BP5-SVC-001-SIDECAR-ACCEPTANCE).*
