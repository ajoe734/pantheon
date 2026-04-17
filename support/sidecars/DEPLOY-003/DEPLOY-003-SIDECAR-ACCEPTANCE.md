# DEPLOY-003 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `DEPLOY-003-SIDECAR-ACCEPTANCE`  
**Helper parent:** `DEPLOY-003` - minimal deployable HTTP surface for evaluation / feedback / memory / registry / optimizer-svc  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex`  
**Date:** `2026-04-17`  
**Packet status:** `review_approved; owner-finalized for parent-owner absorption`  
**Reviewed by:** `Claude` on `2026-04-17`

> Scope constraint: support artifact only. This packet does not change L1 canonical truth, runtime
> implementation, registry truth, or deployment semantics. It records the current acceptance surface
> for `DEPLOY-003` from durable state plus the current repo snapshot.

---

## 1. Purpose

This sidecar exists to make `DEPLOY-003` reviewable without reopening planning history:

1. restate the parent task's actual deploy-surface requirements from durable state
2. capture which of the five service entrypoints already exist in the repo and how they are wired
3. distinguish static repo completion from the still-missing live runtime proof
4. hand `Claude` a reviewer-ready packet that can be absorbed into parent closeout if useful

---

## 2. Parent Task Truth

From `ai-status.json`, `DEPLOY-003` is currently:

- owner: `Claude`
- reviewer: `Codex`
- phase: `Phase 7: Deployment`
- status: `todo`
- artifacts:
  - `services/evaluation/main.py`
  - `services/evaluation/Dockerfile`
  - `services/feedback/main.py`
  - `services/feedback/Dockerfile`
  - `services/memory/main.py`
  - `services/memory/Dockerfile`
  - `services/registry/main.py`
  - `services/registry/Dockerfile`
  - `services/optimizer-svc/main.py`
  - `services/optimizer-svc/Dockerfile`
  - `docker-compose.yml`
- acceptance:
  - five services each expose `/__health__` and return `200`
  - `docker compose up` leaves the full five-service slice healthy

This sidecar does not widen the scope. It only packages evidence and the remaining owner-closeout
steps.

---

## 3. Scope Boundary

In scope for the parent slice:

- deployable `main.py` entrypoints for `evaluation`, `feedback`, `memory`, `registry`, and `optimizer-svc`
- container packaging for those same five services
- root `docker-compose.yml` entries for those services, including port mapping, dependency wiring, and
  healthchecks
- enough support evidence to let the parent owner close the task honestly

Still outside this sidecar:

- editing the parent implementation itself
- changing L1 deployment policy or runtime ownership rules
- claiming end-to-end runtime health without live compose evidence
- widening `DEPLOY-003` into bootstrap, migration, or dual-VM acceptance work

---

## 4. Current Repo Snapshot

### 4.1 All five service entrypoints exist

Current repo snapshot contains these deployable entrypoints:

| Service | Entry file | Health route | Additional surface |
|---|---|---|---|
| `evaluation` | `services/evaluation/main.py` | `GET /__health__` | `POST /api/evaluation/results`, `GET /api/evaluation/results/{result_id}` |
| `feedback` | `services/feedback/main.py` | `GET /__health__` | `POST /api/feedback/events`, `GET /api/feedback/events` |
| `memory` | `services/memory/main.py` | `GET /__health__` | `POST /api/memory/entries`, `GET /api/memory/entries` |
| `registry` | `services/registry/main.py` | `GET /__health__` | wraps the existing registry API from `services/registry/service.py` |
| `optimizer-svc` | `services/optimizer-svc/main.py` | `GET /__health__` | `POST /api/optimizer/synthesize`, `GET /api/optimizer/policies/{policy_id}` |

Important nuance:

- `registry` is not a fresh stub. It mounts the existing registry FastAPI app and adds a Docker-safe
  `/__health__` route on top.
- the other four services are minimal in-memory HTTP wrappers around existing library surfaces; this
  matches the parent task's "minimal deployable server" intent.

### 4.2 Docker packaging exists for the same five services

The repo now includes:

- `services/evaluation/Dockerfile`
- `services/feedback/Dockerfile`
- `services/memory/Dockerfile`
- `services/registry/Dockerfile`
- `services/optimizer-svc/Dockerfile`
- matching `requirements.txt` files for all five services with `fastapi`, `uvicorn`, and `pydantic`

Packaging notes:

- `evaluation`, `feedback`, `memory`, and `registry` launch through `uvicorn`
- `optimizer-svc` launches via `python /workspace/services/optimizer-svc/main.py`, which internally
  runs `uvicorn`

### 4.3 Root compose wiring exists for all five services

`docker-compose.yml` now contains service stanzas for:

- `evaluation` on host port `18084` / container port `8084`
- `feedback` on host port `18085` / container port `8085`
- `memory` on host port `18086` / container port `8086`
- `registry` on host port `18087` / container port `8087`
- `optimizer-svc` on host port `18088` / container port `8088`

Shared wiring visible in compose:

- all five depend on `postgres`, `minio`, and `nats` with `condition: service_healthy`
- all five define a healthcheck that calls `http://127.0.0.1:<port>/__health__`
- all five inherit the base deployment substrate via `DATABASE_URL`, `PANTHEON_NATS_URL`,
  `PANTHEON_S3_ENDPOINT`, and `PANTHEON_ARTIFACT_BUCKET`
- `registry` additionally carries S3 access-key wiring because it fronts the existing registry API

### 4.4 Static validation completed successfully

Sidecar validation run on the current repo snapshot:

- `python3 -m py_compile services/evaluation/main.py services/feedback/main.py services/memory/main.py services/registry/main.py services/optimizer-svc/main.py`
  - result: pass
- `docker compose -f docker-compose.yml config`
  - result: pass
- `docker compose -f docker-compose.yml config --services | rg '^(evaluation|feedback|memory|registry|optimizer-svc)$'`
  - result: all five service names are present in compose output

This is good static evidence that the deploy surface exists and the compose file parses. It is not a
substitute for runtime health proof.

### 4.5 Current practical consumer already points at `registry`

The current root compose file also shows:

- `persona` sets `PANTHEON_REGISTRY_URL: http://registry:8087`
- `persona` depends on `registry` being healthy before startup

That makes `registry` part of the control-plane path already visible in the repo, even though
`DEPLOY-003` itself is framed as a service-deployability slice.

---

## 5. Acceptance Checklist

### AC-1: Five services each have deployable HTTP entrypoints

| Check | Expected evidence | Status |
|---|---|---|
| `evaluation` has `main.py` and `/__health__` | entry file present | Met |
| `feedback` has `main.py` and `/__health__` | entry file present | Met |
| `memory` has `main.py` and `/__health__` | entry file present | Met |
| `registry` adds `/__health__` on top of the existing API | wrapper entry file present | Met |
| `optimizer-svc` has `main.py` and `/__health__` | entry file present | Met |

### AC-2: Container packaging exists for each service

| Check | Expected evidence | Status |
|---|---|---|
| Dockerfile exists for all five services | five Dockerfiles present | Met |
| minimal runtime deps are declared | five `requirements.txt` files present | Met |
| service entrypoints parse | `py_compile` pass | Met |

### AC-3: Root compose integrates the full five-service slice

| Check | Expected evidence | Status |
|---|---|---|
| compose entries exist for all five services | `docker compose config --services` includes all five | Met |
| each service has a healthcheck hitting `/__health__` | compose stanzas present | Met |
| each service binds to the shared substrate | env + `depends_on` wiring present | Met |
| control-plane consumers can resolve `registry` by compose name | `persona` points at `registry:8087` | Met |

### AC-4: Live health proof still belongs to parent closeout

| Check | Expected evidence | Status |
|---|---|---|
| `docker compose up -d` for infra + five services succeeds | owner command output | Pending owner verification |
| all five service containers reach healthy | `docker compose ps` or inspect evidence | Pending owner verification |
| `curl` against each `/__health__` returns `200` in running containers | owner smoke note or command output | Pending owner verification |
| shared substrate is sufficient for the five-service slice in live compose | owner smoke note | Pending owner verification |

### Acceptance summary

Support-packet acceptance is satisfied:

- the repo contains the expected five-service deploy surface
- compose wiring for the slice is present and parse-valid
- the remaining gap is runtime evidence, not missing static implementation

Parent-task acceptance is not yet fully proven from this sidecar alone because live compose health has
not been captured here.

---

## 6. Dependency Map

### 6.1 Durable dependency truth from `ai-status.json`

`DEPLOY-003` currently has no explicit `depends_on` entries in durable state.

That means this packet should not pretend the task is formally blocked by another parent slice.
However, practical deployment sequencing still matters.

### 6.2 Practical upstream and downstream adjacency

| Task | Relation | Why it matters to `DEPLOY-003` |
|---|---|---|
| `DEPLOY-001` | practical upstream | the five-service slice depends on the shared compose substrate (`postgres`, `minio`, `nats`) being healthy in live runs |
| `DEPLOY-005` | downstream bootstrap | bootstrap, env, and migration scripting should treat these five services as part of the single-VM boot path |
| `DEPLOY-006` | downstream smoke | end-to-end smoke can only be honest if these service containers actually boot and report healthy |
| `DEPLOY-007` | downstream packaging | the control-plane split compose should inherit the same service names, ports, and health model for this slice |

### 6.3 Service-level adjacency inside the current compose file

| Service | Adjacent dependency surfaced in repo | Why reviewer should care |
|---|---|---|
| `registry` | consumed by `persona` through `PANTHEON_REGISTRY_URL=http://registry:8087` | `registry` is already part of a broader control-plane path, not a dead-end stub |
| `evaluation` / `feedback` / `memory` / `optimizer-svc` | wired to `postgres`, `minio`, and `nats` | the shared substrate contract is already assumed by their compose stanzas |

### 6.4 Sequencing note

The honest reading is:

- static implementation for `DEPLOY-003` appears present now
- live acceptance should still be captured in the same environment as the shared infra from
  `DEPLOY-001`
- later deployment/bootstrap slices should reuse the exact compose service names and ports already
  visible here unless a deliberate rename is recorded in durable state

---

## 7. Remaining Closeout For Parent Owner

Recommended next steps for `Claude` on the parent task:

1. run `docker compose up -d postgres minio nats evaluation feedback memory registry optimizer-svc`
2. capture `docker compose ps postgres minio nats evaluation feedback memory registry optimizer-svc`
3. verify that all five service containers are `healthy`
4. hit each service's `/__health__` once from the running stack and record the result
5. move the parent task to `review` only after that live evidence exists

If runtime verification fails, the failure should be recorded against the parent task rather than
papered over inside this sidecar.

---

## 8. Owner Finalization Note

Final owner closeout for this sidecar is complete:

- reviewer approval has already been recorded for the packet contents and scope boundary
- this final pass only aligns the support artifact with that approved state
- parent-task runtime proof still belongs to `DEPLOY-003` owner execution, not to this sidecar
- parent owner may now absorb this packet into the main closeout path if useful

---

## 9. Parent Owner Absorption Handoff For `Claude`

High-signal absorption focus:

1. confirm the repo snapshot described above matches the current branch state
2. decide whether the static evidence here is enough to start the parent task or whether live compose
   proof should be gathered before parent-state movement
3. keep the parent task honest: static completion looks strong, but runtime health still needs direct
   evidence

Suggested disposition:

- use this packet as the support map for `DEPLOY-003`
- absorb any useful wording into the parent task only if it matches live verification
- keep parent closeout on the parent task, not in this support artifact

---

## 10. Sidecar Scope Declaration

This file is the only artifact created by this sidecar.

- no canonical L1 or L2 document was modified
- no runtime or compose implementation was modified by this sidecar
- no global summary files were edited manually
- parent-task absorption remains a parent-owner decision
