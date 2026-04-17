# SVC-CP-RTR-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `SVC-CP-RTR-001-SIDECAR-ACCEPTANCE`  
**Helper parent:** `SVC-CP-RTR-001` - wire control-plane/router into docker-compose  
**Parent owner:** `Codex`  
**Parent reviewer:** `Claude`  
**Prepared by:** `Codex`  
**Date:** `2026-04-17`  
**Packet status:** `review approved by Claude; ready for owner closeout`

> Scope constraint: support artifact only. This packet does not change L1 canonical truth, router
> contract truth, registry truth, or governance/runtime implementations. It records the current
> acceptance surface for `SVC-CP-RTR-001` using the task brief plus the live repo snapshot.

---

## 1. Purpose

This sidecar exists to make the parent closeout explicit without reopening planning history:

1. restate the current parent-task truth from the task-scoped brief
2. show the current compose/router snapshot after the approved review fix landed
3. separate direct acceptance evidence from wider transitive compose dependencies
4. preserve the approved support packet that can be absorbed into the parent finalization decision

---

## 2. Parent Task Truth

From [svc_cp_rtr_001.md](/home/edna/code/pantheon/.orchestrator/task-briefs/svc_cp_rtr_001.md:7),
the parent task is currently:

- owner: `Codex`
- reviewer: `Claude`
- phase: `Phase 7: Deployment`
- status: `review_approved`
- next: review already approved because the router compose entry now has correct `PORT`,
  `depends_on` (`persona` / `operator-bff` / `governance` with `service_healthy`), healthcheck,
  and port mapping
- artifact surface: `docker-compose.yml`

The same brief records the owner handoff and reviewer approval:

- owner handoff said the router compose config now includes `PORT=8001`, `persona` under
  `depends_on`, retained `operator-bff` / `governance` health gates, and passed `docker compose
  config`
- reviewer approval accepted that exact shape and returned the parent to `Codex` for finalization

This sidecar does not widen the scope beyond that approved compose slice.

---

## 3. Scope Boundary

In scope for the parent slice:

- add the `router` service to [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:327)
- wire the service to [services/control-plane/router/Dockerfile](/home/edna/code/pantheon/services/control-plane/router/Dockerfile:1)
- provide `PORT=8001` and `PERSONA_URL=http://persona:8002`
- gate startup on healthy `persona`, `operator-bff`, and `governance`
- expose the router health path and prove the compose entry can actually come up healthy

Still outside this sidecar:

- changing router business logic or contract semantics
- broadening the slice into a full control-plane deployment review
- editing the parent implementation itself
- pretending this packet supersedes the parent owner's formal `review_approved -> done` transition

---

## 4. Current Repo Snapshot

### 4.1 Compose entry matches the approved review state

Current [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:327) contains a `router`
service with:

- build context `services/control-plane/router`
- Dockerfile `Dockerfile`
- environment `PORT: "8001"`
- environment `PERSONA_URL: http://persona:8002`
- `depends_on` health gates for `persona`, `operator-bff`, and `governance`
- port mapping `18003:8001`
- healthcheck probing `http://127.0.0.1:8001/health`

This directly matches the approved parent brief state at
[svc_cp_rtr_001.md](/home/edna/code/pantheon/.orchestrator/task-briefs/svc_cp_rtr_001.md:25).

### 4.2 Router image and health surface are coherent with compose

[Dockerfile](/home/edna/code/pantheon/services/control-plane/router/Dockerfile:1) shows:

- base image `python:3.11-slim`
- `pip install -r requirements.txt`
- `EXPOSE 8001`
- `uvicorn main:app --host 0.0.0.0 --port 8001`

[main.py](/home/edna/code/pantheon/services/control-plane/router/main.py:30) shows:

- `FastAPI(title="Pantheon Router", version="0.3.0")`
- `PERSONA_URL` read from env
- `GET /health` returning `status`, `service`, `persona_url`, and `session_ttl_seconds`

That means the compose healthcheck target and the image's fixed port are internally consistent.

### 4.3 Static compose validation passes

Reviewer-side static checks completed successfully:

- `docker compose -f docker-compose.yml config --services` includes `router`, `persona`,
  `operator-bff`, and `governance`
- `docker compose -f docker-compose.yml config` completed successfully

This confirms the compose file parses with the current router slice.

### 4.4 Live compose bring-up evidence now exists

This sidecar also captured the runtime evidence that the earlier review packet did not yet have:

- `docker compose -f docker-compose.yml up -d --build router` exited successfully
- `docker compose -f docker-compose.yml ps router persona operator-bff governance` showed all four
  services `Up (...) healthy`
- `curl -fsS http://127.0.0.1:18003/health` returned:
  `{"status":"ok","service":"router","persona_url":"http://persona:8002","session_ttl_seconds":86400}`
- `docker inspect --format '{{json .State.Health}}' pantheon-router-1` reported
  `"Status":"healthy"` and `FailingStreak: 0`
- `docker compose logs --tail=40 router` showed `GET /health HTTP/1.1" 200 OK`

This is stronger than config-only evidence: it proves the reviewed compose entry comes up and passes
its health gate in the current workspace.

### 4.5 This packet is fresher than the earlier review sidecar

The existing
[SVC-CP-RTR-001-SIDECAR-REVIEW.md](/home/edna/code/pantheon/support/sidecars/SVC-CP-RTR-001/SVC-CP-RTR-001-SIDECAR-REVIEW.md:1)
correctly captured an earlier snapshot, but two of its highlighted gaps are no longer current:

- missing `persona` under `router.depends_on` has been fixed
- missing `PORT` env has been fixed

This acceptance packet should therefore be treated as the fresher support snapshot for closeout.

---

## 5. Acceptance Checklist

### AC-1: Router compose entry exists and matches reviewed wiring

| Check | Evidence | Status |
|---|---|---|
| `router` service exists in compose | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:327) | Met |
| build points at router Dockerfile | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:328), [Dockerfile](/home/edna/code/pantheon/services/control-plane/router/Dockerfile:1) | Met |
| `PORT=8001` is present | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:331) | Met |
| `PERSONA_URL` is present | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:331), [main.py](/home/edna/code/pantheon/services/control-plane/router/main.py:32) | Met |
| `depends_on` includes `persona`, `operator-bff`, `governance` with `service_healthy` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:334) | Met |
| healthcheck targets `/health` on port `8001` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:343), [main.py](/home/edna/code/pantheon/services/control-plane/router/main.py:194) | Met |

### AC-2: Router container can actually start and pass health

| Check | Evidence | Status |
|---|---|---|
| `docker compose up -d --build router` succeeds | local command run in this sidecar | Met |
| direct upstream services are healthy | `docker compose ps router persona operator-bff governance` | Met |
| router health endpoint returns `200` and expected JSON | `curl http://127.0.0.1:18003/health` | Met |
| container health status is healthy with no failing streak | `docker inspect ... pantheon-router-1` | Met |

### AC-3: Support-slice constraints were respected

| Check | Evidence | Status |
|---|---|---|
| only support artifact work was performed | this packet only; no canonical/runtime edits | Met |
| no L1/L2 truth was modified | file touch limited to sidecar artifact | Met |
| packet reflects current `review_approved` parent state | [svc_cp_rtr_001.md](/home/edna/code/pantheon/.orchestrator/task-briefs/svc_cp_rtr_001.md:9) | Met |

### Acceptance summary

Support-packet acceptance is satisfied:

- the repo contains the reviewed router compose wiring
- live compose/health evidence now exists, not just static config proof
- the packet is sufficient for `Claude` to review as support material
- the parent task already has reviewer approval and now has closeout-grade runtime evidence attached

---

## 6. Dependency Map

### 6.1 Durable dependency truth

Per the parent task brief, `SVC-CP-RTR-001` has no explicit durable `depends_on` entries.

That means the parent slice is not blocked by another task-level prerequisite. This packet should not
invent one.

### 6.2 Direct runtime dependencies of `router`

The compose entry makes three direct health-gated dependencies explicit:

| Dependency | Evidence | Why it matters |
|---|---|---|
| `persona` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:334) | router dispatches to `PERSONA_URL=http://persona:8002` |
| `operator-bff` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:337) | control-plane routing is intentionally started only after the operator read/control surface is healthy |
| `governance` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:339) | router startup stays aligned with governance-plane readiness in the current compose graph |

### 6.3 Transitive compose chain observed during live bring-up

The direct dependencies pulled in a wider healthy chain during `docker compose up -d --build router`:

| Service | Evidence | Dependency path |
|---|---|---|
| `governance` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:134) | depends on `postgres`, `minio`, `nats` |
| `operator-bff` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:256) | depends on `governance`, `runtime-manager`, `incidents`, `postmortems`, `telemetry`, `postgres`, `nats` |
| `persona` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:299) | depends on `operator-bff`, `registry`, `nats`, `minio` |
| `registry`, `runtime-manager`, `telemetry`, `incidents`, `postmortems`, `postgres`, `minio`, `nats` | `docker compose ps ...` captured them all `Up (...) healthy` during this run | transitive prerequisites actually started and passed health |

This matters because the router acceptance check is not just a single-container story; the current
compose graph proves the reviewed entry is compatible with the existing control-plane startup chain.

### 6.4 Sequencing note

The current shape is now:

1. infra and control-plane prerequisites become healthy
2. `operator-bff`, `persona`, and `governance` reach healthy
3. `router` starts
4. router `/health` returns `200`

That sequencing aligns with the parent review approval and removes the earlier ambiguity around
whether router could become healthy before `persona` was ready.

---

## 7. Recommended Closeout Use

For `Claude` as completed sidecar reviewer:

- review already approved this packet as an accurate support snapshot
- keep using it as the fresher companion to the older review packet
- do not treat it as a substitute for the parent owner's formal `done` transition

For `Codex` as parent owner:

- the parent task already sits in `review_approved`
- this packet now adds live runtime evidence on top of the already-approved compose diff
- if no broader deployment-wave objection exists, the parent task has the evidence needed for final
  closeout

---

## 8. Sidecar Scope Declaration

This file is the only artifact created by this sidecar acceptance pass.

- no canonical L1 or L2 document was modified
- no runtime/router code was modified
- no registry, governance, or BFF implementation was modified
- no global summary file was manually edited
- parent absorption remains a parent owner decision; this packet only records the current acceptance
  surface
