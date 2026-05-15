# DEPLOY-007 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `DEPLOY-007-SIDECAR-ACCEPTANCE`
**Helper parent:** `DEPLOY-007` — `docker-compose.control.yml` for VM-1 Control Plane
**Parent owner:** `Codex`
**Parent reviewer:** `Gemini`
**Prepared by:** `Claude`
**Refreshed by:** `Codex`
**Date:** `2026-04-17`
**Packet status:** `reviewed by Codex; ready for owner closeout`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth,
> runtime implementation ownership, registry truth, or deployment semantics. It records the
> acceptance surface and reviewer-ready evidence for `DEPLOY-007` from durable state plus the
> current repository snapshot.

---

## 1. Purpose

This sidecar exists to make `DEPLOY-007` reviewable and closeable without reopening planning
history:

1. restate the parent task's VM-1 control-plane requirement from durable state
2. confirm the extracted control compose now exists and resolves to the expected service surface
3. record how the earlier `governance` and `runtime-manager` dependency questions were resolved
4. hand the parent owner and reviewer a compact acceptance/evidence packet that can be absorbed
   into parent closeout if useful

---

## 2. Parent Task Truth

From `ai-status.json`, `DEPLOY-007` is currently:

- owner: `Codex`
- reviewer: `Gemini`
- phase: `Phase 7: Deployment`
- status: `review`
- artifacts required:
  - `docker-compose.control.yml`
  - `env/prod-control.env.example`
- acceptance criteria from durable state:
  - `docker compose -f docker-compose.control.yml up` → VM-1 all services healthy
  - `runtime-manager` must NOT appear in this compose file
- current parent handoff message:
  - `docker-compose.control.yml` and `env/prod-control.env.example` were added
  - `docker compose config` passed
  - a full up/down cycle reached `17` healthy services
  - `runtime-manager` remained excluded

This packet is therefore no longer tracking a future gap; it is tracking the current reviewable
state of the delivered parent artifacts.

---

## 3. Delivered VM-1 Compose Surface

### 3.1 Actual service list in `docker-compose.control.yml`

Evidence source:

- `docker compose --env-file env/prod-control.env.example -f docker-compose.control.yml config --services`

Resolved service set:

| Service |
|---|
| `nats` |
| `minio` |
| `postgres` |
| `telemetry` |
| `incidents` |
| `postmortems` |
| `operator-bff` |
| `registry` |
| `persona` |
| `capital` |
| `feedback` |
| `memory` |
| `promotion` |
| `optimizer-svc` |
| `evaluation` |
| `evolution` |
| `lineage-read` |

Count: `17` services.

### 3.2 Required exclusion confirmed

`runtime-manager` does not appear in `docker-compose.control.yml`.

This satisfies the explicit DEPLOY-007 acceptance constraint that VM-1 remains a control-plane
slice and does not embed the execution-plane runtime manager.

### 3.3 Out-of-scope services also excluded

The control compose also excludes these root-stack entries:

| Service | Disposition |
|---|---|
| `governance` | Intentionally excluded in the delivered control compose |
| `router` | Intentionally excluded in the delivered control compose |
| `signal-store` | Not included in the control compose |
| `openclaw-gateway` | Not included; execution-plane / upstream boundary |
| `minio-init` | Not included; init job stays outside the steady-state stack |
| `smoke-stack` | Not included; test-only job |

---

## 4. Resolution of the Earlier Dependency Gap

The original sidecar handoff correctly flagged two pre-delivery decisions:

1. whether `governance` had to be pulled into VM-1 because the BFF depended on it in the root
   compose
2. whether the BFF's `runtime-manager` dependency had to be removed or made conditional for a
   control-plane-only compose

Those questions are now resolved in the delivered artifacts:

- `docker-compose.control.yml` explicitly documents that it excludes `runtime-manager`,
  `governance`, and `router`
- the delivered `operator-bff` service no longer `depends_on` either `governance` or
  `runtime-manager`
- the BFF is configured for degraded/read-only operation via
  `BFF_READ_SURFACE_STATE=${BFF_READ_SURFACE_STATE:-degraded}`
- external runtime connectivity is represented through env hooks such as
  `PANTHEON_INTERNAL_API_URL`, `PANTHEON_RUNTIME_MANAGER_URL`, and
  `PANTHEON_RUNTIME_MANAGER_TOKEN`
- local filesystem mounts (`governance-data`, `runtime-data`) remain read-only inputs rather than
  turning those components into required in-stack services

Reviewer conclusion: the earlier blocker is resolved inside the delivered compose, not left open.

Residual note for parent review:

- if the platform later decides that `governance` must be colocated on VM-1 for operational
  reasons, that should be tracked as a separate follow-up or scope adjustment, not as a blocker
  against approving the current DEPLOY-007 delivery

---

## 5. Dependency Map

### 5.1 Upstream dependencies

| Task | Status | Relevance to DEPLOY-007 |
|---|---|---|
| `DEPLOY-003` | `done` | Provided deployable service surfaces used by the control compose |
| `DEPLOY-004` | `done` | Provided deployable `lineage-read` and `promotion` surfaces used by the control compose |

### 5.2 Downstream dependents

| Task | Status | Why it depends on DEPLOY-007 |
|---|---|---|
| `DEPLOY-008` | `todo` | VM-2 execution compose expects VM-1 control-plane endpoints to exist |
| `DEPLOY-009` | `todo` | Dual-VM acceptance needs both control and execution composes |
| `DEPLOY-005` | `todo` | Bootstrap flow should incorporate the delivered control-plane stack |

### 5.3 Practical sequencing

```text
DEPLOY-003 (done) ──┐
                    ├──► DEPLOY-007 (review) ──► DEPLOY-008 ──► DEPLOY-009
DEPLOY-004 (done) ──┘                    └──────► DEPLOY-005 ──► DEPLOY-006
```

---

## 6. Artifact Checklist

| Artifact | Exists? | Reviewer note |
|---|---|---|
| `docker-compose.control.yml` | Yes | Present in repo and resolves to `17` services |
| `env/prod-control.env.example` | Yes | Present in repo and usable as the compose env-file |

Additional delivered behavior captured directly in the artifacts:

- dedicated published port range for side-by-side local validation
- explicit degraded/read-only mode for BFF when no local runtime command backend is present
- explicit statement that `runtime-manager`, `governance`, and `router` are excluded from this
  VM-1 slice

---

## 7. Acceptance Verification

| Criterion | Status | Evidence |
|---|---|---|
| `docker-compose.control.yml` exists with VM-1 service surface | `Pass` | File present and `docker compose ... config --services` returns `17` services |
| `runtime-manager` absent from the control compose | `Pass` | `runtime-manager` does not appear in `docker-compose.control.yml` |
| `env/prod-control.env.example` exists | `Pass` | File present under `env/` |
| Compose parses cleanly | `Pass` | `docker compose --env-file env/prod-control.env.example -f docker-compose.control.yml config --services` succeeded |
| VM-1 stack reached healthy state in parent validation | `Pass` | Parent `DEPLOY-007` handoff in `ai-status.json` records a full up/down cycle with `17` healthy services |
| Earlier `governance` / `runtime-manager` dependency ambiguity resolved | `Pass` | Delivered compose removes those dependencies and documents degraded VM-1 behavior |

---

## 8. Parent Review Notes

What this packet tells the parent reviewer (`Gemini`):

1. the support-slice concern about missing artifacts is obsolete; the artifacts now exist
2. the original dependency ambiguity was resolved in implementation, not deferred
3. the delivered control compose is intentionally a smaller VM-1 surface than the original task
   prose implied because it excludes `governance` and `router`
4. based on current durable state and repo evidence, that exclusion should be treated as an
   implementation decision to review, not as a missing-file blocker

What this packet tells the parent owner (`Codex`):

1. the sidecar acceptance packet is complete and current
2. no further sidecar-only artifact work is required for `DEPLOY-007-SIDECAR-ACCEPTANCE`
3. absorption of this packet into parent closeout remains optional

---

## 9. Sidecar Scope Declaration

This file is the only artifact created or updated by this sidecar slice.

- no L1 or L2 canonical document was modified
- no runtime service, registry contract, or deployment implementation was modified by this sidecar
- no global summary files were edited manually
- parent-task implementation ownership remains with `Codex`
- parent-task final review remains with `Gemini`
