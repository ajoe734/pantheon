# SVC-CAP-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `SVC-CAP-001-SIDECAR-ACCEPTANCE`  
**Helper parent:** `SVC-CAP-001` - add Dockerfile and compose entry for capital service  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex2`  
**Refreshed by:** `Codex`  
**Date:** `2026-04-17`  
**Packet status:** `review-approved support packet; refreshed against current repo snapshot and archived parent state, ready for owner closeout`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, task
> planning truth, service contracts, or the parent implementation. It records the current
> acceptance surface for `SVC-CAP-001` from the task-scoped context plus the live repo snapshot.

---

## 1. Purpose

This sidecar exists to help the assigned reviewer close the parent slice without reopening global
history:

1. restate the parent task and its final archived state from task-scoped context
2. capture the current capital-service packaging snapshot
3. separate direct acceptance evidence from dependency observations
4. flag one remaining wording drift in the task acceptance text without mutating the task itself

---

## 2. Parent Task Truth

The generated parent brief now shows `SVC-CAP-001` as `review_approved` at
[svc_cap_001.md](/home/edna/code/pantheon/.orchestrator/task-briefs/svc_cap_001.md:7), but that
brief stopped updating before the owner finalized the task.

`SVC-CAP-001` is no longer present in the active `ai-status.json` task list. The durable parent
state is the archived snapshot returned by `python3 scripts/ai_status.py show SVC-CAP-001` and
stored at
[SVC-CAP-001.json](/home/edna/code/pantheon/ai-task-archive/tasks/SVC-CAP-001.json:1).

The current durable parent state is:

- owner: `Claude`
- reviewer: `Codex`
- phase: `Phase 7: Deployment`
- terminal status: `done`
- terminal outcome: `completed`
- archived at: `2026-04-17T18:05:25Z`
- delivery commit: `5916270382b34bc4678ea0d7eb9dbc575cf4a8a6`
- review note: the literal `/__health__` acceptance text drift was accepted as non-blocking because
  the task brief, compose healthcheck, implementation, and live runtime evidence consistently use
  `/health`

This packet therefore supports an already-closed parent slice. It is no longer an input to a
pending parent review decision.

---

## 3. Scope Boundary

In scope for the parent slice:

- add [services/capital/Dockerfile](/home/edna/code/pantheon/services/capital/Dockerfile:1)
- add a `capital` service entry to
  [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:500)
- provide `PORT=8092` and `CAPITAL_DATA_DIR=/data/capital`
- expose a container health surface that matches the service implementation
- prove the compose entry can actually build and come up healthy

Outside this sidecar:

- editing the parent implementation
- changing service contract truth or canonical governance semantics
- correcting acceptance wording directly inside `ai-status.json`
- changing the parent's archived `done` disposition

---

## 4. Current Repo Snapshot

### 4.1 Compose entry exists and is internally coherent

Current [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:500) contains a
`capital` service with:

- build context `.`
- Dockerfile `services/capital/Dockerfile`
- environment `PORT: "8092"`
- environment `CAPITAL_DATA_DIR: /data/capital`
- volume mount `capital-data:/data/capital`
- port mapping `18092:8092`
- healthcheck probing `http://127.0.0.1:8092/health`

This matches the archived parent delivery surface and the compose/runtime evidence reproduced in
this review.

### 4.2 Docker image wiring matches the compose entry

[services/capital/Dockerfile](/home/edna/code/pantheon/services/capital/Dockerfile:1) shows:

- base image `python:3.11-slim`
- repo-root build context copied into `/workspace`
- `EXPOSE 8092`
- `uvicorn services.capital.main:app --host 0.0.0.0 --port 8092`

The repo-root `COPY . /workspace` matters here because
[services/capital/main.py](/home/edna/code/pantheon/services/capital/main.py:21) prepends
`services/control-plane/governance` to `sys.path`. That means the parent slice has a code-level
dependency on the repo-wide build context even though the compose entry has no explicit
`depends_on`.

### 4.3 Health surface is consistent across implementation and contract

[services/capital/main.py](/home/edna/code/pantheon/services/capital/main.py:564) exposes
`GET /health` and returns `{"status":"ok","service":"pantheon-capital"}`.

[services/capital/contract.md](/home/edna/code/pantheon/services/capital/contract.md:55) also
lists `GET /health`.

That means the implementation contract and the compose healthcheck agree with each other.

### 4.4 Static compose validation passes

Reviewer-side static checks were rerun successfully:

- `docker compose -f docker-compose.yml config --services` includes `capital`
- the same command output also shows `governance`, `postgres`, `minio`, and `nats`, which confirms
  the capital slice is being validated inside the existing compose graph

### 4.5 Live compose bring-up evidence exists

This sidecar now includes a fresh reviewer-side rerun of the live runtime evidence:

- `docker compose -f docker-compose.yml up -d --build capital` completed successfully
- `docker compose -f docker-compose.yml ps capital` reported `Up ... (healthy)`
- `curl -fsS http://127.0.0.1:18092/health` returned
  `{"status":"ok","service":"pantheon-capital"}`
- `docker inspect --format '{{json .State.Health}}' pantheon-capital-1` reported
  `"Status":"healthy"` and `FailingStreak: 0`
- `docker compose -f docker-compose.yml logs --tail=40 capital` showed `GET /health HTTP/1.1" 200 OK`

This is stronger than config-only proof: it shows the reviewed packaging still builds and passes
its health gate in the current workspace even after the parent task was archived.

### 4.6 One acceptance-text drift remains visible

The archived parent acceptance text in
[SVC-CAP-001.json](/home/edna/code/pantheon/ai-task-archive/tasks/SVC-CAP-001.json:1) still says
`docker compose up capital 後 /__health__ 回傳 200`.

The actual implementation and runtime evidence are consistently `/health`, not `/__health__`:

- compose healthcheck uses `/health`
- service implementation exposes `/health`
- contract doc lists `/health`
- live curl proof succeeded on `/health`

The archived parent review notes already record this as wording drift and accept it as
non-blocking. This sidecar keeps the observation only so later readers can reconcile the literal
acceptance text with the real runtime surface.

---

## 5. Acceptance Checklist

### 5.1 Sidecar acceptance

| Check | Evidence | Status |
|---|---|---|
| support artifact created under `support/sidecars/SVC-CAP-001/` | this packet | Met |
| packet stays inside support-only scope | no canonical/runtime edits in this sidecar | Met |
| packet is ready for assigned reviewer handoff | reviewer remains `Codex` in [ai-status.json](/home/edna/code/pantheon/ai-status.json:1091) | Met |

### 5.2 Parent acceptance evidence

| Check | Evidence | Status |
|---|---|---|
| `services/capital/Dockerfile` exists | [Dockerfile](/home/edna/code/pantheon/services/capital/Dockerfile:1) | Met |
| `capital` service exists in compose | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:500) | Met |
| compose entry provides `PORT=8092` | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:504) | Met |
| compose entry mounts capital data storage | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:507) | Met |
| healthcheck matches implementation path | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:511), [main.py](/home/edna/code/pantheon/services/capital/main.py:564) | Met |
| `docker compose up -d --build capital` succeeds | local command run in this sidecar | Met |
| runtime health endpoint returns `200` | `curl http://127.0.0.1:18092/health` | Met |
| literal parent acceptance wording matches runtime evidence | archived [SVC-CAP-001.json](/home/edna/code/pantheon/ai-task-archive/tasks/SVC-CAP-001.json:1) vs `/health` evidence | Drift recorded; accepted in parent review |

### Acceptance summary

The packaging/runtime part of the parent slice is acceptance-proven:

- the Dockerfile exists
- the compose entry exists and is coherent with the service
- the container builds and reaches healthy state
- the only discrepancy still visible from this sidecar is the archived task wording using
  `/__health__` while the actual service surface is `/health`, and that discrepancy was already
  accepted in the parent review notes

---

## 6. Dependency Map

### 6.1 Durable task dependencies

Per both the brief and durable task state, `SVC-CAP-001` has no explicit task-level `depends_on`
entries.

This packet does not invent one.

### 6.2 Direct packaging dependencies

The parent slice directly depends on:

| Dependency | Evidence | Why it matters |
|---|---|---|
| repo-root build context | [Dockerfile](/home/edna/code/pantheon/services/capital/Dockerfile:11) | image copies the full repo, not only `services/capital/` |
| compose service entry | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:500) | runtime packaging is delivered through compose |
| writable capital data volume | [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:507) | service persists pool, binding, and audit data under `/data/capital` |

### 6.3 Code-level dependency not expressed in compose

The capital service has a code import dependency on the governance package path:

| Dependency | Evidence | Why it matters |
|---|---|---|
| `services/control-plane/governance` Python modules | [main.py](/home/edna/code/pantheon/services/capital/main.py:21) | service inserts that path into `sys.path` and imports `capital_pool` and `persona_capital_binding` from there |

This explains why the parent `next` note calls out the full build context as important. Even
without compose `depends_on`, the image would fail if the Docker build stopped copying the repo
root.

### 6.4 Observed runtime graph during validation

The capital service itself has no explicit compose `depends_on`. During this sidecar run, the
service still validated inside the existing stack where `governance`, `postgres`, `minio`, and
`nats` are present in the compose graph.

That is supporting context only, not a claim that the parent task requires those services to be
started for the container health endpoint to answer.

---

## 7. Reviewer Handoff Notes

- Assigned reviewer for this sidecar task: `Codex`
- Review focus should be narrow: verify that this packet now accurately captures the archived parent
  state plus fresh reviewer-side runtime evidence
- The `/__health__` wording drift no longer needs a new parent decision here; it has already been
  accepted in the archived parent review notes
- Reviewer approval is already recorded in the durable task state; the remaining lifecycle step is
  owner finalization of this sidecar task to `done`
