# DEPLOY-008 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `DEPLOY-008-SIDECAR-ACCEPTANCE`  
**Helper parent:** `DEPLOY-008` - VM-2 execution-plane compose split  
**Parent owner:** `Codex`  
**Parent reviewer:** `Claude`  
**Prepared by:** `Codex`  
**Date:** `2026-04-17`  
**Packet status:** `ready for sidecar review`

> Scope constraint: support artifact only. This packet does not change L1 canonical truth, runtime
> contracts, registry/governance truth, or primary deployment implementations. It packages the
> current acceptance surface for `DEPLOY-008` from durable task state plus the repo snapshot.

---

## 1. Purpose

This sidecar exists to make `DEPLOY-008` reviewable before the parent implementation starts:

1. restate the accepted VM-2 execution-plane scope from durable state
2. capture what the repo already provides versus what `DEPLOY-008` still needs to add
3. define a reviewer-facing acceptance checklist and dependency map
4. keep the slice support-only so the parent owner can absorb or ignore it without canonical drift

---

## 2. Parent Task Truth

From [ai-status.json](/home/edna/code/pantheon/ai-status.json:314), `DEPLOY-008` is currently:

- owner: `Codex`
- reviewer: `Claude`
- status: `todo`
- hard dependency: `DEPLOY-007`
- required artifacts:
  - `docker-compose.exec.yml`
  - `env/prod-exec.env.example`
  - `docs/deployment/exec-vm-secrets-guide.md`
- accepted outcomes:
  - `docker compose -f docker-compose.exec.yml up` leaves `runtime-manager` healthy
  - control-plane services such as BFF and registry do not appear in the VM-2 compose
  - paper runtime can start

The accepted VM-2 shape is consistent with the execution-plane deployment notes in
[Pantheon_單VM測試版_雙VM正式版_部署補充說明.md](/home/edna/code/pantheon/Pantheon_單VM測試版_雙VM正式版_部署補充說明.md:310):

- `runtime-manager-svc`
- `pantheon-lean` paper runtime
- `pantheon-lean` prod runtime
- broker / exchange adapter sidecars
- local execution telemetry / health / kill-switch helpers
- explicit exclusion of BFF, registry-core, lineage-read, and promotion API from VM-2

This sidecar does not widen that scope. It only packages the current reviewer intake.

---

## 3. Current Repo Snapshot

### 3.1 The prerequisite control-plane split is complete

`DEPLOY-007` is archived as `done`; its snapshot records that `docker-compose.control.yml` and
`env/prod-control.env.example` were finalized with `runtime-manager` excluded from the VM-1 slice.
Repo-local evidence matches that disposition:

- [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:1) states the file is the dedicated VM-1 control-plane stack
- [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:6) explicitly excludes `runtime-manager`, `governance`, and `router`
- `docker compose -f docker-compose.control.yml config --services` currently returns only VM-1 services and infra; `runtime-manager` is absent

This means `DEPLOY-008` can assume the control-plane side of the split is already locked.

### 3.2 The single-VM compose still carries execution-plane responsibilities

The root compose still bundles execution responsibilities into the all-in-one topology:

- [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:116) defines `runtime-manager`
- [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:171) points `telemetry` at `http://runtime-manager:8081`
- [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:199) points `incidents` at `http://runtime-manager:8081`

`docker compose -f docker-compose.yml config --services` still includes `runtime-manager`,
`governance`, `operator-bff`, `registry`, and `telemetry` together, so the dedicated VM-2 split has
not been realized yet.

### 3.3 Runtime-manager is deployable as a standalone service

The execution-plane service itself already exists and is containerized:

- [services/runtime-manager/Dockerfile](/home/edna/code/pantheon/services/runtime-manager/Dockerfile:1)
- [services/runtime-manager/main.py](/home/edna/code/pantheon/services/runtime-manager/main.py:1)

That gives `DEPLOY-008` an implementation basis for the VM-2 compose, but not the compose split
artifact itself.

### 3.4 LEAN execution code exists, but no dedicated VM-2 compose wiring exists yet

Repo-local execution code is present under `services/execution/lean_runtime/`, but there is no
existing `docker-compose.exec.yml` or VM-2 env file wiring it together. The execution-plane packet
therefore needs to define, not just reference, the VM-2 runtime topology.

### 3.5 The required execution-plane artifacts are currently absent

The parent task's three required artifacts do not exist yet in the repo:

- `docker-compose.exec.yml`
- `env/prod-exec.env.example`
- `docs/deployment/exec-vm-secrets-guide.md`

Sidecar-local existence checks returned non-zero for all three paths, so the parent task should be
treated as not yet implemented rather than partially accepted.

### 3.6 There is already a secrets-handling precedent for execution-only broker credentials

The future VM-2 secrets guide does not need to invent naming or IAM boundaries from scratch:

- [scripts/gcp_nonprod_foundation.sh](/home/edna/code/pantheon/scripts/gcp_nonprod_foundation.sh:32) already provisions `broker-api-key` and `broker-api-secret`
- [docs/gcp-bootstrap-confirmation.md](/home/edna/code/pantheon/docs/gcp-bootstrap-confirmation.md:113) documents how to populate those secrets
- [docs/gcp-bootstrap-confirmation.md](/home/edna/code/pantheon/docs/gcp-bootstrap-confirmation.md:163) records those broker secrets as execution-only access

That is useful support input for `docs/deployment/exec-vm-secrets-guide.md`.

---

## 4. Acceptance Checklist For Parent Task

This checklist is derived from the accepted task scope plus the VM-2 deployment note. Current status
reflects today's repo snapshot, not the desired final state.

| # | Criterion | Verification Method | Status |
|---|---|---|---|
| 1 | `deploy_007_dependency_closed` | `DEPLOY-007` archived `done`; VM-1 control-plane compose exists and excludes `runtime-manager` | Met |
| 2 | `exec_compose_exists` | `docker-compose.exec.yml` exists in repo root | Pending |
| 3 | `exec_env_exists` | `env/prod-exec.env.example` exists and covers VM-2 variables | Pending |
| 4 | `exec_secrets_guide_exists` | `docs/deployment/exec-vm-secrets-guide.md` exists with secret injection instructions | Pending |
| 5 | `runtime_manager_present_on_vm2` | exec compose defines `runtime-manager` with healthcheck | Pending |
| 6 | `paper_runtime_present_on_vm2` | exec compose defines a paper runtime service/process and start path | Pending |
| 7 | `control_plane_services_excluded` | exec compose contains no BFF / registry / lineage-read / promotion API / other VM-1 services | Pending |
| 8 | `broker_sidecars_and_secret_boundary_documented` | broker/exchange adapter sidecars and execution-only secret injection are documented | Pending |
| 9 | `runtime_manager_healthy_under_exec_compose` | `docker compose -f docker-compose.exec.yml up` leaves `runtime-manager` healthy | Pending owner verification |
| 10 | `paper_runtime_boots` | paper runtime starts successfully under the VM-2 slice | Pending owner verification |
| 11 | `single_vm_runtime_coupling_removed_from_exec_acceptance_surface` | reviewer can show VM-2 slice is separate from the single-VM root compose path | Pending |
| 12 | `sidecar_scope_only` | this helper produced support material only and did not modify canonical truth | Met |

### Acceptance summary

What is already true:

- the prerequisite VM-1 split is complete
- runtime-manager has an existing deployable service image
- execution-only secret naming precedent already exists

What remains fully on the parent task:

- create the three missing VM-2 artifacts
- encode the execution-only topology in `docker-compose.exec.yml`
- prove `runtime-manager` health and paper-runtime startup with live command evidence

---

## 5. Dependency Map

### 5.1 Hard upstream dependency

| Task | Relation | Why it matters |
|---|---|---|
| `DEPLOY-007` | explicit `depends_on` | VM-1 control-plane services must already be split out before VM-2 execution services can be isolated cleanly |

### 5.2 Repo-local implementation dependencies

| Input | Type | Why it matters to `DEPLOY-008` |
|---|---|---|
| [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:1) | prior deployment artifact | establishes which services belong on VM-1 and therefore must stay out of VM-2 |
| [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:116) | current all-in-one topology | shows the current runtime-manager coupling that `DEPLOY-008` must peel away into a dedicated exec compose |
| [services/runtime-manager/Dockerfile](/home/edna/code/pantheon/services/runtime-manager/Dockerfile:1) | existing execution service image | provides the container build target for the VM-2 compose |
| `services/execution/lean_runtime/*` | existing execution code | provides the runtime implementation basis that the VM-2 slice will need to package or invoke |
| [docs/gcp-bootstrap-confirmation.md](/home/edna/code/pantheon/docs/gcp-bootstrap-confirmation.md:113) | existing ops guidance | provides secret names and execution-only IAM expectations for the new secrets guide |

### 5.3 Downstream consumers waiting on `DEPLOY-008`

| Task | Relation | Why it matters |
|---|---|---|
| `DEPLOY-009` | explicit `depends_on` | dual-VM acceptance cannot run until the VM-2 execution-plane slice exists |
| `docs/deployment/single-vm-runbook.md` next-step sequence | practical dependency | the runbook already positions `DEPLOY-007/DEPLOY-008` as the split point before dual-VM validation |

### 5.4 Sequencing note

`DEPLOY-008` is not just another compose file. It is the boundary-setting step that decides whether
execution-plane secrets, runtimes, and runtime-manager health live on VM-2 instead of remaining
implicitly bundled inside the single-VM root compose.

---

## 6. Commands Run For This Sidecar

The following repo-local checks were executed while preparing this packet:

```bash
docker compose -f docker-compose.control.yml config --services
docker compose -f docker-compose.yml config --services | rg 'runtime-manager|governance|operator-bff|registry|telemetry'
test -e docker-compose.exec.yml
test -e env/prod-exec.env.example
test -e docs/deployment/exec-vm-secrets-guide.md
rg --files services/execution/lean_runtime lean | head -n 40
```

Observed outcomes:

- VM-1 compose parses and lists only control-plane / shared infra services
- root compose still lists `runtime-manager` alongside control-plane services
- all three parent artifacts are currently absent
- execution runtime code exists in the repo, but no dedicated VM-2 compose exists yet

---

## 7. Reviewer Handoff Note

For `Gemini` as sidecar reviewer:

1. verify this packet stayed within support-artifact scope only
2. confirm the current snapshot is accurate: `DEPLOY-007` done, `DEPLOY-008` still `todo`, VM-2 artifacts absent
3. confirm the acceptance checklist matches the parent task's accepted VM-2 scope without widening it
4. confirm the dependency map is useful for the parent owner and does not change canonical truth

Suggested sidecar review disposition:

- approve this sidecar if the packet accurately reflects the current repo and durable state
- leave the parent task `DEPLOY-008` untouched until the actual VM-2 compose/env/guide artifacts land

---

## 8. Sidecar Scope Declaration

This file is the only artifact created by this sidecar pass.

- no canonical L1 or L2 document was modified by this sidecar
- no runtime-manager, LEAN runtime, registry, governance, or deployment implementation file was modified
- no global summary files were edited manually for this packet
- parent-task absorption remains a parent-owner decision

---

*Generated by Codex as a sidecar `acceptance_packet` helper for `DEPLOY-008`. This file is a support artifact and does not modify canonical truth.*
