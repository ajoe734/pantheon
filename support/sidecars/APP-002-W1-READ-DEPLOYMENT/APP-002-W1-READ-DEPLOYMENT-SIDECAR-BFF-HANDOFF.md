# APP-002-W1-READ-DEPLOYMENT BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002-W1-READ-DEPLOYMENT` — Implement Promotion Review read surfaces and composed view
**Parent Owner**: Codex
**Parent Reviewer**: Qwen
**Parent Status**: `review_approved` → `done`
**Sidecar Owner**: Qwen
**Sidecar Reviewer**: Codex
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-11

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations. It packages the Wave 1 read-surface implementation into a frontend-ready handoff for downstream consumers.

---

## 1. Parent Task Summary

`APP-002-W1-READ-DEPLOYMENT` has completed and been review-approved. It delivers the minimum viable read surfaces for the Promotion Review screen (F-042), enabling the UI to render deployment review data without client-side joins.

**Acceptance criteria met**:
- `dp02_cp02_cp04_rt02_rt04_live` — DP-02, CP-02, CP-04, RT-02, RT-04 read surfaces are live
- `get_operator_deployment_review_implemented` — `GET /api/v1/operator/deployment-review/{plan_id}` returns a composed view
- `f042_renders_without_mocks` — F-042 example payload renders with real data structures

**Parent artifacts**:
| Artifact | Path | Purpose |
|---|---|---|
| BFF implementation | `services/control-plane/bff/main.py` | FastAPI app with 6 read surfaces + 1 composed view + command submit/poll + health |
| BFF API contract | `services/control-plane/bff/BFF_API_CONTRACT.md` | 33 canonical GET surfaces documented |
| F-042 contract | `docs/bff/F-042-promotion-review.md` | Promotion Review page contract |
| F-042 example | `docs/examples/F-042-review-page.json` | Example payload for frontend rendering |

---

## 2. BFF Read Surfaces — Implementation Inventory

These are the **actual implemented GET endpoints** in `services/control-plane/bff/main.py` (Wave 1):

| # | Endpoint | Purpose | Sub-surface (BFF_API_CONTRACT ref) |
|---|---|---|---|
| 1 | `GET /api/v1/deployment-plans/{plan_id}` | Fetch a deployment plan with inline approval decision | DP-02 |
| 2 | `GET /api/v1/capital-pools/{pool_id}` | Fetch a capital pool with inline bindings | CP-02 |
| 3 | `GET /api/v1/bindings/{binding_id}` | Fetch a binding with inline persona | CP-04 |
| 4 | `GET /api/v1/runtime-bindings/{binding_id}` | Fetch a runtime binding with inline deployment plan | RT-02 |
| 5 | `GET /api/v1/runtimes/{runtime_id}/rollbacks` | Fetch rollback history for a runtime | RT-04 |
| 6 | `GET /api/v1/operator/deployment-review/{plan_id}` | **Composed view** — joins all above into a single F-042 payload | F-042 |
| 7 | `GET /api/v1/operator/commands/{command_id}` | Poll command status | CMD-POLL |
| 8 | `GET /health` | Health check | HEALTH |

All non-health endpoints share:
- **Auth**: Bearer token with role extraction (`operator`, `approver`, `admin`, `reviewer`)
- **Role gating**: read surfaces + composed view require a read role; command status requires auth only; command submit enforces per-command role checks
- **Staleness metadata**: DP-02/CP-02/CP-04/RT-02/RT-04 return `meta.staleness` only when the read surface state is not `fresh`; the composed view returns `meta.surfaces` (per-surface status + staleness) instead of `meta.staleness`; command submit responses include `staleness_warning` when the read surface state is not `fresh`
- **Surface health**: composed view includes per-surface status in `meta.surfaces`
- **Read-only guarantee**: no mutation occurs on any GET route

### Not Yet Implemented (Contracted but Stub)

The `BFF_API_CONTRACT.md` references 33+ surfaces. The following are **not yet wired** in `main.py` and remain contract-only for Wave 1:

- Incident surfaces (IN-01 through IN-05) — Wave 2
- Evolution surfaces (EV-01 through EV-04) — Wave 3
- Persona management surfaces — Wave 4
- SSE feeds — Wave 5

---

## 3. Composed View — F-042 Promotion Review

### 3.1 Endpoint

```
GET /api/v1/operator/deployment-review/{plan_id}
```

### 3.2 Response Shape

```json
{
  "data": {
    "deployment_plan": {
      "id": "string",
      "stage": "candidate | paper | live",
      "artifact_id": "string",
      "approval_decision_id": "string"
    },
    "capital_pool": { "...": "inline from CP-02" },
    "bindings": ["...inline from CP-04"],
    "runtime_binding": { "...": "inline from RT-02" },
    "rollbacks": ["...inline from RT-04"],
    "allowedActions": {
      "canPromoteToPaper": "boolean"
    },
    "latestRun": {
      "progress": "number (0.0–1.0)"
    },
    "review": {
      "riskSummary": "string"
    }
  },
  "meta": {
    "snapshot_at": "RFC3339 timestamp",
    "surfaces": {
      "deployment_plan": { "status": "ok | degraded | unavailable", "staleness": "{served_from,last_known_at}?" },
      "capital_pool": { "status": "..." },
      "bindings": { "status": "..." },
      "runtime_binding": { "status": "..." },
      "rollbacks": { "status": "..." }
    }
  }
}
```

### 3.3 ReadSurfaceStore — Data Source

The composed view reads from `ReadSurfaceStore` (in-memory file-backed store at `/tmp/pantheon/bff/read_surfaces.json`). The store provides these query methods:

| Method | Returns | Used in F-042 |
|---|---|---|
| `get_deployment_plan(plan_id)` | `dict | None` | ✅ `deployment_plan` |
| `get_capital_pool(pool_id)` | `dict | None` | ✅ `capital_pool` |
| `get_bindings_for_pool(pool_id)` | `list[dict]` | ✅ `bindings` |
| `get_runtime_binding(binding_id)` | `dict | None` | ✅ `runtime_binding` |
| `get_rollbacks(runtime_id)` | `list[dict]` | ✅ `rollbacks` |
| `get_allowed_actions(plan_id)` | `dict` | ✅ `allowedActions` |
| `get_latest_run(plan_id)` | `dict` | ✅ `latestRun` |
| `get_review_summary(plan_id)` | `dict` | ✅ `review` |
| `get_approval_decision(decision_id)` | `dict | None` | ✅ (inline in DP-02 standalone) |
| `get_persona(persona_id)` | `dict | None` | ✅ (inline in CP-04 standalone) |

---

## 4. Degradation Model

Read-surface state is driven by `BFF_READ_SURFACE_STATE` (`fresh`, `degraded`, `stale`, `unavailable`). The composed view surfaces **collapse** `stale` into status `degraded` with staleness metadata. Frontend **must** gate actions accordingly:

| Surface state (env) | Status returned | Meaning | Frontend action policy |
|---|---|---|---|
| `fresh` | `ok` | All sub-surfaces healthy, data verified current | Normal operation; promote CTA enabled |
| `degraded` | `degraded` | Slower response or partial replica | Show warning; allow read, require confirmation before promote |
| `stale` | `degraded` (with staleness) | Cache-backed last-known state | Block promote CTA; require re-verification |
| `unavailable` | `unavailable` | No verifiable data | Show error banner; redirect to secondary control path |

The composed view returns per-surface status in `meta.surfaces` (currently uniform across surfaces). If **any** sub-surface is not `ok`, the overall view should be treated as degraded.

---

## 5. Command Path — Write Surface Reference

While Wave 1 is read-only, the BFF also exposes the command submission path that Wave 2+ will harden:

| Endpoint | Method | Purpose | Status |
|---|---|---|---|
| `POST /api/v1/operator/commands` | Submit async command | ✅ Live (stub worker) |
| `GET /api/v1/operator/commands/{command_id}` | Poll command status | ✅ Live |

### Supported Command Types

| Command | Required Roles | Required Params | Purpose |
|---|---|---|---|
| `ApproveDeployment` | `approver`, `admin` | `deployment_plan_id`, `approval_decision` | Approve/reject deployment |
| `PauseRuntime` | `operator`, `admin` | `runtime_binding_id`, `pause_action` | Pause/resume runtime |
| `ExecuteRollback` | `admin`, `approver` | `rollback_target_type`, `target_id`, `rollback_to_version` | Execute rollback |
| `ActivateKillSwitch` | `admin` (+ MFA) | `scope`, `activate`, optional `severity` | Activate kill-switch |
| `ApproveEvolutionDecision` | `reviewer`, `approver`, `admin` | `evolution_decision_id`, `approval_action` | Approve/reject evolution |
| `ExecuteEvolutionAction` | `admin`, `approver` | `evolution_decision_id`, `action_type` | Freeze/retrain/mutate/retire |

**Note**: The command worker (`_process_command_stub`) is still a stub. Wave 2 (`APP-002-W1-COMMAND-DEPLOYMENT`) will replace this with a real execution path.

---

## 6. Frontend Handoff Materials

### 6.1 Promotion Review Screen — Data Requirements

The frontend needs **only** the composed view endpoint to render the full F-042 page. No client-side joins required.

| Screen section | Data source | Notes |
|---|---|---|
| Deployment plan header | `data.deployment_plan` | Stage badge, artifact link |
| Capital pool summary | `data.capital_pool` | Pool status, capacity |
| Bindings table | `data.bindings` | Persona → pool mappings |
| Runtime status | `data.runtime_binding` | Current deployment stage, runtime health |
| Rollback history | `data.rollbacks` | Previous rollback events |
| Promotion CTA | `data.allowedActions.canPromoteToPaper` | Enable/disable based on backend value |
| Progress indicator | `data.latestRun.progress` | Show 0–100% progress |
| Risk summary | `data.review.riskSummary` | Human-readable risk assessment |
| Degradation banner | `meta.surfaces` | Show warning if any sub-surface not `ok` |

### 6.2 Staleness Handling

The frontend should **never** silently render an empty or stale state as success.

| Scenario | Recommended behavior |
|---|---|
| `meta.surfaces.*.status == "ok"` | Render normally |
| Any sub-surface `degraded` | Show amber warning banner; allow read (treat `stale` as degraded + staleness) |
| Any sub-surface `unavailable` | Show error page; redirect to fallback |
| Entire response 404 | Show "plan not found" with link to plan list |
| Entire response 403 | Show "insufficient permissions" with role hint |

### 6.3 CTA Gating Rules

| CTA | Backend gate | Frontend condition |
|---|---|---|
| "Promote to Paper" | `data.allowedActions.canPromoteToPaper == true` | All sub-surfaces `ok` + backend allows |
| "View Approval Decision" | `deployment_plan.approval_decision_id` exists | Link to DP-03 decision detail |
| "View Rollback History" | `data.rollbacks` array exists | Show table (empty table is valid) |

---

## 7. BFF Query Gap Analysis

| Surface | Contracted? | Implemented? | Gap owner | Wave |
|---|---|---|---|---|
| Deployment plans (DP-02) | ✅ | ✅ | — | Wave 1 |
| Capital pools (CP-02) | ✅ | ✅ | — | Wave 1 |
| Bindings (CP-04) | ✅ | ✅ | — | Wave 1 |
| Runtime bindings (RT-02) | ✅ | ✅ | — | Wave 1 |
| Rollbacks (RT-04) | ✅ | ✅ | — | Wave 1 |
| Deployment review (F-042) | ✅ | ✅ | — | Wave 1 |
| Incident surfaces (IN-01–05) | ✅ | ❌ | `APP-002-W2-READ-INCIDENT` | Wave 2 |
| Evolution surfaces (EV-01–04) | ✅ | ❌ | `APP-002-W3-POSTINCIDENT-EVOLUTION` | Wave 3 |
| Persona surfaces | ✅ | ❌ | `APP-002-W4-PERSONA-MGMT` | Wave 4 |
| SSE feeds | ✅ | ❌ | `APP-002-W5-SSE-LIVE` | Wave 5 |

---

## 8. Suggested Downstream Consumption

| Consumer | What to use | When |
|---|---|---|
| **Frontend (Lovable)** | `GET /api/v1/operator/deployment-review/{plan_id}` + F-042 example | Immediately — parent is `done` |
| **APP-002-W1-FRONT-HANDOFF (Copilot)** | This packet + F-042 contract | Can proceed now — depends on `APP-002-W1-READ-DEPLOYMENT` |
| **APP-002-W1-COMMAND-DEPLOYMENT (Claude)** | Command submission + poll endpoints | Can proceed now — depends on `APP-002-W1-READ-DEPLOYMENT` |
| **APP-002-W2-READ-INCIDENT (Qwen)** | ReadSurfaceStore pattern + degradation model | Can start design — depends on Wave 1 completion |

---

## 9. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | ✅ PASS | Only `support/sidecars/APP-002-W1-READ-DEPLOYMENT/APP-002-W1-READ-DEPLOYMENT-SIDECAR-BFF-HANDOFF.md` created |
| No canonical truth edited | ✅ PASS | Only references existing canonical artifacts; no modifications to L0/L1 documents |
| Packet anchored to current shared truth | ✅ PASS | Derived from `ai-status.json`, `main.py`, `BFF_API_CONTRACT.md`, F-042 contract/example |
| Reviewer handoff ready | ✅ PASS | Structured for Codex (parent reviewer) and Copilot (W1-FRONT-HANDOFF owner) to consume |

---

## 10. Handoff To Reviewer (Codex)

Codex, this packet documents what Wave 1 actually delivers from a BFF/frontend perspective:

- 6 read endpoints implemented (5 standalone + 1 composed F-042 view)
- Command submission + poll live but stub worker
- Degradation model consistent across all read surfaces
- Frontend can render Promotion Review screen immediately using only the composed view
- Gap analysis identifies which surfaces remain for Waves 2–5

Recommended next step:

- review this sidecar packet
- verify the endpoint inventory matches `main.py` implementation
- if acceptable, mark parent sidecar task as reviewed so the packet can be consumed by `APP-002-W1-FRONT-HANDOFF`

---

*Generated by Qwen as a sidecar `bff_handoff_packet` helper for APP-002-W1-READ-DEPLOYMENT. This file is a support artifact and does not modify canonical truth.*
