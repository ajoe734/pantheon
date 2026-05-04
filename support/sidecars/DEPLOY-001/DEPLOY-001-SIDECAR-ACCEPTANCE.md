# DEPLOY-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `DEPLOY-001-SIDECAR-ACCEPTANCE`  
**Helper parent:** `DEPLOY-001` - compose infrastructure foundation for deployment wave  
**Parent owner:** `Codex2`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex2`  
**Reviewed/refreshed by:** `Codex`  
**Date:** `2026-04-17`  
**Packet status:** `reviewer-validated; ready for owner finalization`

> Scope constraint: support artifact only. This packet does not change L1 canonical truth, runtime
> implementation, registry truth, or governance truth. It records the current acceptance surface for
> `DEPLOY-001` using live durable state plus the current repo snapshot.

---

## 1. Purpose

This sidecar exists to keep `DEPLOY-001` closeout crisp:

1. restate the parent task's actual acceptance targets from durable state
2. capture the current compose / env / bootstrap snapshot without reopening planning history
3. separate what is already present in the repo from what still needs owner closeout evidence
4. hand the parent owner a concise reviewer-validated packet for final verification

---

## 2. Parent Task Truth

From `ai-status.json`, `DEPLOY-001` is currently:

- owner: `Codex2`
- reviewer: `Codex`
- phase: `Phase 7: Deployment`
- artifacts: `docker-compose.yml`, `.env.example`, `scripts/bootstrap.sh`
- acceptance:
  - `docker compose up` leaves `postgres` / `minio` / `nats` healthy
  - DB-facing service dependencies can connect through the new base infrastructure

This sidecar does not widen that scope. It only packages evidence and remaining closeout steps.

---

## 3. Scope Boundary

In scope for the parent slice:

- root compose definitions for `postgres`, `minio`, and `nats`
- persistent volumes, env defaults, and healthchecks for those services
- reusable downstream wiring so control-plane services can depend on the shared substrate
- minimal operator-facing support artifacts needed to boot the infra base locally

Still outside this sidecar:

- changing canonical deployment semantics
- absorbing later deployment slices into this packet
- pretending runtime health was proven if the owner has not yet captured live evidence

---

## 4. Current Repo Snapshot

### 4.1 Base infra is now present in root compose

Current `docker-compose.yml` already includes:

- `postgres` with persistent volume, init SQL mount, host port mapping, and `pg_isready` healthcheck
- `minio` with persistent volume, API/console ports, and live health probe
- `nats` with JetStream storage, monitor port, and `/healthz` probe
- named volumes `postgres-data`, `minio-data`, and `nats-data`

Reference anchors:

- [docker-compose.yml](/home/lupin/code/pantheon/docker-compose.yml:1)
- [docker-compose.yml](/home/lupin/code/pantheon/docker-compose.yml:23)
- [docker-compose.yml](/home/lupin/code/pantheon/docker-compose.yml:42)
- [docker-compose.yml](/home/lupin/code/pantheon/docker-compose.yml:490)

### 4.2 Downstream env wiring is already visible

Current compose wiring already points application services at the new substrate:

- `DATABASE_URL` defaults target `postgres`
- `PANTHEON_NATS_URL` defaults target `nats`
- `PANTHEON_S3_ENDPOINT` defaults target `minio`
- multiple services now declare `depends_on` with `condition: service_healthy` for the new infra

Reference anchors:

- [docker-compose.yml](/home/lupin/code/pantheon/docker-compose.yml:125)
- [docker-compose.yml](/home/lupin/code/pantheon/docker-compose.yml:131)
- [docker-compose.yml](/home/lupin/code/pantheon/docker-compose.yml:154)
- [docker-compose.yml](/home/lupin/code/pantheon/docker-compose.yml:219)

### 4.3 Support artifacts listed on the parent task also exist

The repo now includes:

- `.env.example` with base defaults for Postgres, MinIO, and NATS
- `scripts/bootstrap.sh` that defaults to `postgres minio nats` and prints `docker compose ps`

Reference anchors:

- [.env.example](/home/lupin/code/pantheon/.env.example:1)
- [scripts/bootstrap.sh](/home/lupin/code/pantheon/scripts/bootstrap.sh:1)

### 4.4 Static compose validation passes

Reviewer-side static check completed:

- `docker compose -f docker-compose.yml config --services` includes `postgres`, `minio`, and `nats`
- `docker compose -f docker-compose.yml config` completed successfully

This is useful evidence that the compose file parses, but it is not a substitute for live health
verification.

### 4.5 Redis still exists alongside NATS

`signal-store` / Redis is still present in the compose file. That means the repo currently reflects
coexistence, not a forced migration from Redis to NATS. This is acceptable for `DEPLOY-001`; the
important point is that the NATS substrate now exists for downstream deployment slices.

---

## 5. Acceptance Checklist

### AC-1: Required infra services exist in root compose

| Check | Expected evidence | Status |
|---|---|---|
| `postgres` service exists | compose stanza, persistent volume, healthcheck | Met |
| `minio` service exists | compose stanza, persistent volume, healthcheck | Met |
| `nats` service exists | compose stanza, JetStream storage, healthcheck | Met |
| infra volumes exist | named volumes for DB/object-store/bus durability | Met |

### AC-2: Minimal operator boot path exists

| Check | Expected evidence | Status |
|---|---|---|
| base env defaults exist | `.env.example` carries infra defaults | Met |
| bootstrap entry exists | `scripts/bootstrap.sh` boots infra by default | Met |
| compose file is syntactically valid | `docker compose config` succeeds | Met |

### AC-3: Downstream services can bind to the shared substrate

| Check | Expected evidence | Status |
|---|---|---|
| DB-facing services have a clear path to `postgres` | env defaults and `depends_on` wiring | Met |
| artifact paths have a clear path to `minio` | `PANTHEON_S3_ENDPOINT` / bucket defaults | Met |
| event-driven paths have a clear path to `nats` | `PANTHEON_NATS_URL` wiring | Met |
| downstream deployment slices can reuse the same names | stable compose service names | Met |

### AC-4: Live runtime proof still belongs to parent closeout

| Check | Expected evidence | Status |
|---|---|---|
| `docker compose up -d postgres minio nats` succeeds | owner command output | Pending owner verification |
| all three services reach healthy | `docker compose ps` or inspect evidence | Pending owner verification |
| one DB-facing service can connect through the new base | owner smoke note or log excerpt | Pending owner verification |

### Acceptance summary

Static acceptance for the support packet is satisfied:

- the repo now contains the expected infra foundation
- the support packet accurately reflects the current snapshot
- the only remaining gap for parent-task closeout is live runtime evidence, not missing repo wiring

---

## 6. Dependency Map

### 6.1 Explicit downstream dependencies from durable state

| Task | Relation | Why `DEPLOY-001` matters |
|---|---|---|
| `DEPLOY-002` | explicit `depends_on` | compose-added app services should reuse the shared infra instead of inventing private wiring |
| `DEPLOY-004` | explicit `depends_on` | lineage / promotion / support services need the same DB/object-store/message-bus floor |
| `DEPLOY-005` | explicit `depends_on` | bootstrap and migration flow should target the settled infra names and defaults |

### 6.2 Practical downstream consumers

| Task | Practical dependency | Why it matters |
|---|---|---|
| `DEPLOY-006` | indirect via bootstrap and smoke | single-VM smoke evidence is only meaningful once the base infra exists |
| `DEPLOY-007` | indirect via later packaging | split / packaged deployment should inherit the same infra naming and health model |
| `DEPLOY-009` | indirect via later acceptance | dual-VM review still assumes a settled control-plane substrate contract |

### 6.3 Sequencing note

`DEPLOY-001` should be treated as the infra naming and health baseline. Later slices should build on
these exact compose service names and env defaults unless the parent owner deliberately changes them
and records that change in durable state.

---

## 7. Remaining Closeout For Parent Owner

Recommended owner finalization steps for `Codex2`:

1. run `scripts/bootstrap.sh` or `docker compose up -d postgres minio nats`
2. capture `docker compose ps postgres minio nats`
3. confirm all three services are healthy
4. record one short note that a DB-facing path can bind to the new substrate without renaming env keys
5. move `DEPLOY-001` through review only after that live evidence exists

---

## 8. Reviewer Disposition

Reviewer findings:

- the original sidecar packet had drifted from live state after parent implementation progressed
- this refresh corrects owner/reviewer metadata and replaces the stale "missing infra" snapshot with
  the current repo evidence
- no canonical L1/L2 truth was modified; this remains a support-only artifact

Reviewer conclusion:

- `DEPLOY-001-SIDECAR-ACCEPTANCE` is acceptable as a support slice
- the packet is approved for owner finalization at the sidecar-task level
- parent-task closeout still requires live runtime evidence from the owner

---

## 9. Sidecar Scope Declaration

This file is the only artifact touched by this reviewer refresh.

- no canonical L1 or L2 document was modified by this sidecar
- no runtime, registry, governance, or BFF implementation was modified by this sidecar
- no global summary files were edited manually
- parent-task absorption remains an owner decision; this packet only records the current acceptance surface
