# DEPLOY-005 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `DEPLOY-005-SIDECAR-ACCEPTANCE`  
**Helper parent:** `DEPLOY-005` - single-VM bootstrap / env / migration support slice  
**Parent owner:** `Codex2`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex2`  
**Date:** `2026-04-17`  
**Packet status:** `reviewer_validated`

> Scope constraint: support artifact only. This packet does not change L1 canonical truth, runtime
> implementation, registry truth, or deployment semantics. It records the current acceptance surface
> for `DEPLOY-005` from durable state plus the current repo snapshot.

---

## 1. Purpose

This sidecar exists to make `DEPLOY-005` reviewable without reopening global history:

1. restate the parent task's actual acceptance targets from durable state
2. inventory which parent artifacts already exist in the repo and which are still missing
3. distinguish parse-valid bootstrap support from still-missing migration / runbook work
4. hand `Codex` and the parent owner a dependency-aware closeout packet

---

## 2. Parent Task Truth

From `ai-status.json` after the latest helper reassignment, `DEPLOY-005` is currently:

- owner: `Codex2`
- reviewer: `Codex`
- phase: `Phase 7: Deployment`
- status: `todo`
- depends_on:
  - `DEPLOY-001`
  - `DEPLOY-002`
  - `DEPLOY-003`
  - `DEPLOY-004`
- artifacts:
  - `.env.example`
  - `scripts/bootstrap.sh`
  - `scripts/db_migrate.sh`
  - `docs/deployment/single-vm-runbook.md`
- acceptance:
  - `bash scripts/bootstrap.sh` can complete the single-VM bring-up path on a fresh environment
  - all service migrations run successfully without errors
  - `.env.example` covers the required service variables

This sidecar does not widen that scope. It only packages the current evidence and remaining owner
closeout steps.

---

## 3. Scope Boundary

In scope for the parent slice:

- the root single-VM env contract represented by `.env.example`
- the bootstrap entrypoint for compose bring-up and health waiting
- a dedicated migration entrypoint for DB/schema setup
- an operator-facing runbook for the single-VM boot path

Still outside this sidecar:

- editing the parent runtime implementation itself
- claiming migration success when no migration entrypoint exists yet
- rewriting canonical deployment policy
- replacing the parent owner's final runtime proof

---

## 4. Current Repo Snapshot

### 4.1 Parent artifact inventory

| Artifact | Current state | Notes |
|---|---|---|
| `.env.example` | Present | Base Postgres / MinIO / NATS defaults exist |
| `scripts/bootstrap.sh` | Present | Starts selected compose services, waits for `healthy`, runs `minio-init` when needed |
| `scripts/db_migrate.sh` | Missing | No dedicated migration entrypoint exists at this path |
| `docs/deployment/single-vm-runbook.md` | Missing | `docs/` exists, but `docs/deployment/` and the runbook path are absent in the current snapshot |

Reference anchors:

- [.env.example](/home/edna/code/pantheon/.env.example:1)
- [scripts/bootstrap.sh](/home/edna/code/pantheon/scripts/bootstrap.sh:1)
- [scripts/init-db.sh](/home/edna/code/pantheon/scripts/init-db.sh:1)

### 4.2 What the existing bootstrap script actually covers

Current `scripts/bootstrap.sh` behavior:

- defaults to `postgres minio nats` when no service list is provided
- sources root `.env` if present
- runs `docker compose up -d ...`
- polls `docker compose ps --format json` until each requested service reports `healthy`
- invokes `minio-init` if `minio` was part of the requested set
- prints a final `docker compose ps` table

Important limits of the current script:

- it does **not** call a migration entrypoint
- it does **not** default to the broader application stack
- it does **not** execute service-level smoke probes beyond container health state

So the current script is a useful substrate bootstrap helper, but it does not yet satisfy the full
parent acceptance wording of "start services -> run migration -> verify health."

### 4.3 `.env.example` is close but not complete

Static variable comparison against `docker-compose.yml` shows:

- compose variables currently referenced: 23
- variables already declared in `.env.example`: 21
- variables missing from `.env.example`:
  - `OPENCLAW_GATEWAY_PORT`
  - `OPENCLAW_GATEWAY_TOKEN`

That means `.env.example` already covers the visible Postgres / MinIO / NATS / registry-adjacent
compose defaults, but it does **not** fully cover every env-substituted variable in the current root
compose file.

### 4.4 Parse-valid compose evidence exists

Reviewer-side static checks completed on the current snapshot:

- `docker compose -f docker-compose.yml config`
  - result: pass
- compose/env comparison between `docker-compose.yml` and `.env.example`
  - result: only two missing env keys, both for the optional `openclaw` profile

This is useful evidence that the current stack definition parses. It is not a substitute for parent
acceptance because no migration step was executed here.

### 4.5 Existing DB bootstrap material is narrower than a migration flow

The repo does contain `scripts/init-db.sh`, which:

- creates the application role if needed
- creates the application database if needed
- grants privileges on the target DB and schema

This is helpful substrate setup, but it is not the dedicated `scripts/db_migrate.sh` promised by the
parent task, and it does not prove "all service migrations run successfully without errors."

---

## 5. Acceptance Checklist

### AC-1: Parent artifact set exists

| Check | Expected evidence | Status |
|---|---|---|
| `.env.example` exists | file present | Met |
| `scripts/bootstrap.sh` exists | file present | Met |
| `scripts/db_migrate.sh` exists | file present | Not met |
| `docs/deployment/single-vm-runbook.md` exists | file present | Not met |

### AC-2: `.env.example` covers required compose variables

| Check | Expected evidence | Status |
|---|---|---|
| Postgres variables are declared | `.env.example` keys present | Met |
| MinIO variables are declared | `.env.example` keys present | Met |
| NATS variables are declared | `.env.example` keys present | Met |
| all env-substituted root compose variables are declared | no missing keys vs compose | Not met |

Gap note:

- missing keys are `OPENCLAW_GATEWAY_PORT` and `OPENCLAW_GATEWAY_TOKEN`

### AC-3: Bootstrap flow covers bring-up, migration, and health verification

| Check | Expected evidence | Status |
|---|---|---|
| compose bring-up entry exists | `scripts/bootstrap.sh` present | Met |
| health waiting exists | script polls `docker compose ps --format json` for `healthy` | Met |
| migration step exists in the bootstrap flow | dedicated migration call or script | Not met |
| default target aligns with full single-VM acceptance surface | script defaults reflect broader stack or runbook explains exact invocation | Not met from current snapshot |

### AC-4: Migrations are runnable and documented

| Check | Expected evidence | Status |
|---|---|---|
| DB/schema migration entry exists | `scripts/db_migrate.sh` or equivalent artifact | Not met |
| migration success recorded | command output or reviewer evidence | Not met |
| single-VM operator runbook exists | `docs/deployment/single-vm-runbook.md` | Not met |

### Acceptance summary

Support-packet acceptance is satisfied:

- the packet accurately captures the current repo snapshot
- the repo already contains a useful infra bootstrap baseline
- the remaining parent gaps are concrete and limited

Parent-task acceptance is **not** yet proven from the current snapshot because the migration
entrypoint, runbook, and full env coverage are still incomplete.

---

## 6. Dependency Map

### 6.1 Durable upstream dependencies from `ai-status.json`

| Task | Relation | Why it matters to `DEPLOY-005` |
|---|---|---|
| `DEPLOY-001` | explicit upstream | bootstrap relies on settled infra service names, ports, and health model for `postgres` / `minio` / `nats` |
| `DEPLOY-002` | explicit upstream | full single-VM env/runbook must account for the compose-added core services introduced there |
| `DEPLOY-003` | explicit upstream | migration/bootstrap closeout should include the five service entrypoints now wired into root compose |
| `DEPLOY-004` | explicit upstream | the lineage/promotion slice becomes part of the same single-VM boot and env contract |

### 6.2 Explicit downstream dependency

| Task | Relation | Why `DEPLOY-005` matters |
|---|---|---|
| `DEPLOY-006` | explicit downstream | end-to-end smoke only becomes honest once bootstrap + migration + env coverage are settled |

### 6.3 Practical downstream adjacency

| Task | Practical dependency | Why it matters |
|---|---|---|
| `DEPLOY-007` | packaging/control-plane extraction | control-plane compose should inherit the same env defaults, naming, and operator instructions where applicable |
| later deployment acceptance slices | indirect | single-VM and dual-VM review packets both rely on a truthful bootstrap/runbook baseline |

### 6.4 Sequencing note

The repo evidence suggests the practical order should remain:

1. keep the infra and service compose surface from `DEPLOY-001` through `DEPLOY-004` stable
2. add the missing migration entrypoint and runbook under `DEPLOY-005`
3. only then treat `DEPLOY-006` smoke evidence as authoritative

---

## 7. Recommended Closeout For Parent Owner

Recommended next steps for `Codex2` on the parent task:

1. add `scripts/db_migrate.sh` or record a clearly named equivalent migration entrypoint
2. decide whether `OPENCLAW_GATEWAY_*` belongs in `.env.example` or whether the `openclaw` profile is
   intentionally excluded from single-VM acceptance; if excluded, state that explicitly in the runbook
3. add `docs/deployment/single-vm-runbook.md` with the exact fresh-environment invocation sequence
4. make `scripts/bootstrap.sh` either call the migration step directly or document the split clearly
   enough that acceptance wording can still be met honestly
5. capture live evidence for the final accepted bootstrap path only after the above artifacts exist

---

## 8. Reviewer Handoff Summary

This sidecar should be reviewed as a support packet, not as a parent-task approval.

Reviewer focus:

- confirm that the packet accurately reflects the current repo snapshot
- confirm that the identified blockers are limited to support/bootstrap gaps, not hidden canonical
  runtime changes
- decide whether the openclaw env gap is a true blocker for the parent acceptance wording or a scope
  clarification that the parent owner should document

Suggested reviewer disposition:

- approve this sidecar packet if the evidence and gap framing are accurate
- leave parent-task closeout to `Claude` after the missing artifacts and live evidence are supplied

---

## 9. Sidecar Scope Declaration

This file is the only artifact created for this sidecar.

- no L1 or L2 canonical file was modified
- no runtime, registry, governance, or compose implementation was changed
- no global summary files were edited manually
- parent-task absorption remains an owner decision
