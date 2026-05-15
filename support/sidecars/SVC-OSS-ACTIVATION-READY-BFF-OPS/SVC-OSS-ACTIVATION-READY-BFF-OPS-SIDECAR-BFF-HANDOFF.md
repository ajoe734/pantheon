# SVC-OSS-ACTIVATION-READY-BFF-OPS BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SVC-OSS-ACTIVATION-READY-BFF-OPS` - Expose OSS activation-ready operations in BFF
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `review_approved`
**Sidecar Task**: `SVC-OSS-ACTIVATION-READY-BFF-OPS-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-30`
**Mutates canonical**: `no`

> This is a support artifact only. It does not change L1 truth, canonical runtime behavior, registry/governance implementations, activation gates, or production execution paths. It packages the current parent-task BFF surface into a reviewer and frontend handoff for the parent owner to absorb or revise.

## 1. Executive Summary

The parent task adds a read-only operator surface for OSS activation-ready operations:

- primary route: `GET /api/v1/operator/research/oss-activation-ready`
- backward-compatible alias: `GET /api/v1/operator/research/oss-preactivation`
- minimum role: `operator`
- query parameter: `activity_limit`, default `20`, range `1..100`

The surface is intentionally an operations view, not an activation control plane. It aggregates capability, gate, run/job, artifact, log, and error metadata from service-owned read APIs and keeps production activation disabled in the response. Frontend consumers should render the surface as an inspection and troubleshooting page for offline activation-ready OSS lanes, not as a launch or promotion page.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable lifecycle truth for the parent task and this sidecar |
| `.orchestrator/task-briefs/svc_oss_activation_ready_bff_ops_sidecar_bff_handoff.md` | Sidecar scope, artifact target, owner/reviewer assignment |
| `services/control-plane/bff/main.py` | Live FastAPI routes and alias metadata envelope |
| `services/control-plane/bff/read_store.py` | Service-backed OSS activation-ready aggregation and non-bypass response shape |
| `services/control-plane/bff/test_research_oss_preactivation_contract.py` | Focused route tests for fail-closed, offline activation-ready, artifact/log/error, and degraded states |
| `services/control-plane/bff/BFF_API_CONTRACT.md` | Route-level BFF contract update for the composed view |
| `services/control-plane/bff/BFF_SURFACE_INVENTORY.md` | RS-04 inventory entry for the task-level research operations surface |

## 3. BFF Query Contract Snapshot

### 3.1 Routes

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/research/oss-activation-ready` | Implemented in parent patch | Canonical route name for the activation-ready operations view |
| `GET /api/v1/operator/research/oss-preactivation` | Implemented alias | Kept for existing consumers; returns the same composed data shape |

Both routes require a read-capable operator identity through the existing BFF authorization path. They return a standard composed-view envelope:

```json
{
  "data": {},
  "meta": {
    "snapshot_at": "2026-04-30T00:00:00Z",
    "surfaces": {}
  }
}
```

`meta.surfaces` includes both the requested route key and its alias key, so either route exposes:

- `research_oss_activation_ready`
- `research_oss_preactivation`
- downstream service surfaces such as `research_orchestrator`, `policy_learning`, `research_worker_gateway`, and `openclaw_gateway_adapter`

### 3.2 Data Fields Frontend Can Use

| Field | Meaning | Frontend handling |
|---|---|---|
| `production_activation` | Always expected to remain `disabled` for this task | Show as a hard disabled production badge |
| `activated` | Always expected to remain `false` | Never infer active production availability from this page |
| `activation_state` | `preactivation_only` or `offline_activation_ready` | Use to label whether offline gates are observable |
| `offline_gate` | `enabled` when service capabilities expose offline readiness | Use for offline-readiness status only |
| `allowed_scope` | Overall scope, currently either metadata-only or offline worker dispatch scope | Display as backend-owned text; do not map to write authority |
| `write_paths` | Disabled state for training dispatch, paper/canary/live, registry, governance, broker, and capital binding writes | Gate or hide any write CTAs from this page |
| `operator_controls` | Explicit read operations and blocked commands | Treat as the UI action policy for this view |
| `backend_inventory[]` | Per-backend state for `openclaw`, `qlib`, `trl`, `finrl`, `rllib`, `ray_tune`, `wandb` | Render capability/gate matrix |
| `safe_dispatch` | Safe dispatcher labels currently exposed by services | Render as read-only capability context |
| `run_history[]` / `activity[]` | Recent service-owned run/job/proposal records | Use for activity timeline or table |
| `artifact_refs[]` | Flattened artifact references extracted from activity records | Link to artifact viewers only when a consumer for the ref type exists |
| `log_summary` | Aggregate event/stdout/stderr counts | Use for compact health/count chips |
| `error_summary` | Aggregate failed/rejected/gateway error counts | Use for alert badges and filters |
| `rejection_verification` | Fail-closed rejection evidence summary | Show as safety evidence, not as activation proof |
| `service_status` | Raw service surface status summary | Render degraded panels per service |

### 3.3 Backend Inventory Semantics

Each `backend_inventory[]` row is deliberately conservative:

- `activated` stays `false`
- `production_activation` stays `disabled`
- `gate_state = activation_ready` means offline readiness was observed, not production activation
- `allowed_scope = offline_worker_dispatch_enabled` means offline worker dispatch metadata is visible through service read APIs
- `openclaw` is expected to remain `facade_only` / `fail_closed` until its separate OpenClaw activation-ready tasks land

Frontend should avoid turning `activation_ready` into a green production-ready affordance. A safer label is "offline ready" or "offline gate observed".

## 4. Operator Journey

Recommended operator flow:

1. Operator opens the OSS activation-ready operations page.
2. UI queries `GET /api/v1/operator/research/oss-activation-ready?activity_limit=20`.
3. UI renders a top-level posture from `production_activation`, `activated`, `activation_state`, `offline_gate`, and `write_paths`.
4. UI renders backend matrix rows from `backend_inventory[]`.
5. UI renders recent work from `run_history[]`, including status, dispatch mode, gateway refs, artifact refs, logs, and per-row error summaries.
6. UI renders service degradation from `meta.surfaces` and `service_status`.
7. UI hides activation, registry promotion, paper/canary/live, broker execution, and capital-binding controls because `operator_controls.activation_commands = not_exposed`.

This page should answer "what is observable and offline-ready?" and "why did a run/job fail or remain blocked?" It should not answer "can I activate production from here?"

## 5. Frontend Rendering Guidance

| Condition | Recommended behavior |
|---|---|
| `meta.surfaces.research_oss_activation_ready.status = ok` | Render the page normally |
| top-level status is `degraded` | Keep the page visible; mark affected downstream service panels from `service_status` |
| top-level status is `unavailable` | Render disabled read-only shell with service-unavailable copy and no empty-success states |
| `backend_inventory[*].gate_state = activation_ready` | Label as offline-ready only |
| `backend_inventory[*].gate_state = fail_closed` | Label as blocked/fail-closed |
| `run_history[*].artifact_refs` exists | Show artifact links or ref chips; do not imply registry promotion |
| `run_history[*].logs` exists | Show event log and stdout/stderr excerpts with truncation |
| `run_history[*].error_summary.has_error = true` | Show row-level error indicator and detail drawer |
| `write_paths.* = disabled` | Do not render enabled write CTAs |

Avoid client-side joins across research-orchestrator, policy-learning, worker-gateway, and OpenClaw APIs. The BFF composed view is the frontend boundary for this operator page.

## 6. Query Gap Matrix

| Area | Current parent patch state | Gap / follow-up |
|---|---|---|
| Route naming | New `/oss-activation-ready` route plus `/oss-preactivation` alias | Frontend should migrate to the new route name while tolerating alias links |
| Capability and gate inventory | Aggregates service capability payloads and offline-ready markers | UI copy must distinguish offline readiness from production activation |
| Run history | Aggregates recent service-owned activity through `activity_limit` | No cursor/pagination yet; use bounded recent timeline only |
| Artifact refs | Extracts explicit `artifact_refs`, `output_refs`, and worker stdout artifact manifests | Artifact detail routing is outside this surface and should stay optional |
| Logs | Projects event records plus bounded stdout/stderr excerpts | No streaming log endpoint in this slice |
| Error summary | Projects rejection, gateway, worker exit, stderr, and cancel summaries | Do not infer retry policy; retries belong to backend/workflow owners |
| Degradation | Returns composed `meta.surfaces` and service status, including alias key | UI should not show empty success state when downstream services are unavailable |
| Commands | Explicitly reports activation commands as not exposed and blocked commands as governance-required | No command route should be added by frontend for this page |

## 7. Parent Reviewer Checklist

For `Codex` as sidecar reviewer:

- confirm this packet is support-only and does not redefine L1/canonical activation truth
- confirm the route and field descriptions match the current parent patch
- confirm frontend guidance preserves fail-closed production, registry, governance, broker, and capital-binding posture
- confirm query gaps are described as handoff boundaries, not as implementation demands for this sidecar

For the parent owner:

- keep the BFF response read-only even when offline gate evidence is present
- keep `/oss-preactivation` as an alias until downstream consumers migrate
- preserve `activity_limit` bounds or document any later pagination change separately
- if parent review changes response shape, update this packet before asking frontend consumers to rely on it

## 8. Verification Notes

Sidecar verification performed:

- Read task-scoped brief and current `ai-status.json` assignment.
- Inspected parent BFF route additions in `services/control-plane/bff/main.py`.
- Inspected parent aggregation behavior in `services/control-plane/bff/read_store.py`.
- Inspected route contract/inventory deltas in `BFF_API_CONTRACT.md` and `BFF_SURFACE_INVENTORY.md`.
- Inspected focused test coverage in `services/control-plane/bff/test_research_oss_preactivation_contract.py`.

No runtime or canonical implementation files were edited by this sidecar.

Closeout verification performed:

- `git diff --check -- support/sidecars/SVC-OSS-ACTIVATION-READY-BFF-OPS/SVC-OSS-ACTIVATION-READY-BFF-OPS-SIDECAR-BFF-HANDOFF.md`
- `pytest services/control-plane/bff/test_research_oss_preactivation_contract.py`

## 9. Sidecar Scope Check

| Check | Result |
|---|---|
| Only support artifact changed | Yes |
| L1 canonical truth changed | No |
| Runtime/registry/governance implementation changed | No |
| BFF implementation changed | No |
| Frontend implementation changed | No |
| Parent owner must decide whether to absorb guidance | Yes |
