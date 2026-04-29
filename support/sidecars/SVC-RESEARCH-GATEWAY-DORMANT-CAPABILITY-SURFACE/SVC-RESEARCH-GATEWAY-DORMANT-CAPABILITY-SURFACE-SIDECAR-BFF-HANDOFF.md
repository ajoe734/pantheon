# BFF and Frontend Handoff Packet
# SVC-RESEARCH-GATEWAY-DORMANT-CAPABILITY-SURFACE

**Sidecar type:** `bff_handoff_packet`
**Parent task:** `SVC-RESEARCH-GATEWAY-DORMANT-CAPABILITY-SURFACE`
**Prepared by:** Claude (owner)
**Reviewer:** Codex2
**Status:** ready for review
**Date:** 2026-04-29

---

## 1. Purpose

This packet summarizes what a BFF (Backend-for-Frontend) or operator UI needs to know about the dormant capability surface delivered by the parent task. It does **not** modify any canonical truth document.

The parent task (`SVC-RESEARCH-GATEWAY-DORMANT-CAPABILITY-SURFACE`, commit `7c1835b`) exposed a fail-closed dormant capability inventory across three research service boundaries:

- `services/research-worker-gateway` — worker dispatch gateway
- `services/research` — research orchestrator (tasks, runs, artifacts, proposals)
- `services/policy-learning` — policy update proposal lifecycle

All seven activation-gated backends (OpenClaw, Qlib, TRL, FinRL, RLlib, Ray Tune, W&B) now declare `gate_state: fail_closed` and `allowed_scope: capability_metadata_read_only` at each boundary.

---

## 2. Delivered Capability Endpoints

The following capability query endpoints are now live:

| Service | Endpoint | Method | Auth Scope |
|---|---|---|---|
| research-worker-gateway | `/api/research-worker-gateway/capabilities` | GET | read-only |
| research-orchestrator | `/api/research-orchestrator/capabilities` | GET | read-only |
| policy-learning | `/api/policy-learning/capabilities` | GET | read-only |

Each returns a capability registry with the same shape (see §4 below).

---

## 3. BFF Query Gap Analysis

**Current state:** No aggregating BFF or operator-facing research query facade exists. The three services expose independent HTTP interfaces. A frontend must make three separate requests to assemble the full dormant capability picture.

### 3.1 Missing Unified Endpoints

The following cross-service queries are absent from any existing BFF or aggregation layer:

| Gap | Description | Impact |
|---|---|---|
| **Unified capability discovery** | No single call to see all dormant adapters/workers across all three services | Operator must fan out to three services manually |
| **Normalized rejection surface** | Each service applies its own token-matching rejection rules independently; no cross-service rejection summary | Hard for UI to show coherent policy view |
| **Cross-service job/run listing** | No unified job/task/run index spanning gateway workers, orchestrator runs, and policy-learning proposals | Operator cannot see research activity at a glance |
| **Aggregated event stream** | Events are per-entity on each service; no ordered global stream of research activity | Audit and operator timeline views are fragmented |
| **Artifact lifecycle tracking** | Artifacts created in orchestrator and proposed for registry are not visible to policy-learning without explicit cross-service linking | Promotion flow is opaque to operator dashboards |
| **Extended health + gate status** | No combined health + dormant capability gate summary across all four research services (including openclaw-gateway-adapter) | Operator monitoring lacks a single-pane health view |

### 3.2 Safe Operations Already Available

These operations are already usable from a frontend without a BFF:

- Reading capability metadata from each service (allowed_scope: `capability_metadata_read_only`)
- Querying job/run/task listings per service (stub mode records are available)
- Checking service health (`/health` on each service)

### 3.3 Operations That Must Remain Blocked

No BFF or frontend shim may route around the fail-closed gates. These paths must remain denied at the service layer regardless of BFF query shape:

- Any dispatch with `worker` or `adapter` in `PRODUCTION_WORKERS` / `PRODUCTION_ADAPTERS`
- Any dispatch containing tokens: `production_training`, `lean`, `signalstore`, `live_trading`
- Any registry or governance writes: `registry_write`, `governance_write`, `governance_stage`
- Any request with `requested_mode` or `dispatch_mode` in `{production, paper, canary, live}`
- Any request using the legacy `ENABLE_PRODUCTION_ADAPTERS` env var (it does NOT enable dormant backends)

---

## 4. Capability Inventory Shape (All Three Services)

Each service returns a capability registry. The normalized cross-service view of the seven gated backends is:

```json
{
  "openclaw": {
    "status": "deferred",
    "gate_state": "fail_closed",
    "allowed_scope": "capability_metadata_read_only",
    "maturity": "governed",
    "tier": "Activation-Ready",
    "activation_owner": "Codex",
    "next_gate": "Repo-authoritative runtime adoption (PER-001-RUNTIME-INTEGRATION-001)"
  },
  "qlib": {
    "status": "deferred",
    "gate_state": "fail_closed",
    "allowed_scope": "capability_metadata_read_only",
    "maturity": "smoke-tested",
    "tier": "Activation-Ready",
    "activation_owner": "Qwen",
    "next_gate": "RS-003 candidate + governed market-data proof (>=50 instruments, 2+ years) + target StrategySpec binding"
  },
  "trl": {
    "status": "deferred",
    "gate_state": "fail_closed",
    "allowed_scope": "capability_metadata_read_only",
    "maturity": "smoke-tested",
    "tier": "Activation-Ready",
    "activation_owner": "Qwen",
    "next_gate": ">=200 FB-002 events, >=100 valid preference pairs, approved LP-002 artifacts, one downstream consumer"
  },
  "finrl": {
    "status": "deferred",
    "gate_state": "fail_closed",
    "allowed_scope": "capability_metadata_read_only",
    "maturity": "criteria-defined",
    "tier": "Activation-Ready",
    "activation_owner": "Copilot",
    "next_gate": "Qlib alpha approved and stable for 3 months; RL gate reopened"
  },
  "rllib": {
    "status": "deferred",
    "gate_state": "fail_closed",
    "allowed_scope": "capability_metadata_read_only",
    "maturity": "version-pinned",
    "tier": "Activation-Ready",
    "activation_owner": "Copilot",
    "next_gate": "FinRL first-lane proof; then governed train/eval activation"
  },
  "ray_tune": {
    "status": "deferred",
    "gate_state": "fail_closed",
    "allowed_scope": "capability_metadata_read_only",
    "maturity": "version-pinned",
    "tier": "Activation-Ready",
    "activation_owner": "Copilot",
    "next_gate": "RLlib follow-on lane after FinRL first-lane proof"
  },
  "wandb": {
    "status": "deferred",
    "gate_state": "fail_closed",
    "allowed_scope": "capability_metadata_read_only",
    "maturity": "criteria-defined",
    "tier": "Activation-Ready",
    "activation_owner": "Qwen",
    "next_gate": "MLflow >=30-day history (earliest 2026-05-15), operator preference documented, SDK pin, infrastructure readiness"
  }
}
```

*Source truth: `RESEARCH_BACKEND_MATURITY_MATRIX.md` and `OSS_INTEGRATION_CHECKLIST.md`. The above is a handoff summary, not an independent authoritative record.*

---

## 5. Operator Journey Map

### Journey A: Viewing the Dormant Capability Surface

**Who:** Platform operator or research lead
**Goal:** Understand which research backends are available now vs. gated

1. Operator navigates to research capability panel
2. Frontend calls `GET /api/research-worker-gateway/capabilities`
   → Returns worker registry with `gate_state` per worker
3. Frontend optionally fans out to `GET /api/research-orchestrator/capabilities` and `GET /api/policy-learning/capabilities`
   → All return consistent `gate_state: fail_closed` for the seven backends
4. UI renders capability cards: green for available (stub/handoff_only/manual), amber for deferred with `gate_state: fail_closed`
5. Clicking a deferred card shows `allowed_scope: capability_metadata_read_only` and the activation gate criteria

**Safe to display:** `status`, `gate_state`, `allowed_scope`
**Do not surface:** activation env vars or any path that implies operator can toggle gates from the UI

### Journey B: Dispatching a Stub Research Job

**Who:** Operator testing the research orchestration path
**Goal:** Submit a stub research run without triggering dormant backends

1. Operator fills in objective and leaves adapter/worker as `stub`
2. Frontend calls `POST /api/research-orchestrator/tasks` with `actor_id: operator`
3. Frontend calls `POST /api/research-orchestrator/tasks/{task_id}/runs` with `adapter: stub`, `dispatch_mode: stub`
4. Orchestrator creates the run; gateway (if invoked) also accepts stub dispatch
5. UI polls `GET /api/research-orchestrator/runs/{run_id}/status` for completion
6. Artifacts are submitted via `POST /api/research-orchestrator/runs/{run_id}/artifacts` with `artifact_state: draft`

**Do not allow in UI:** selecting any dormant adapter from a dropdown — UI must treat gated adapters as display-only (no dispatch form)

### Journey C: Attempting a Gated Dispatch (Expected Rejection)

**Who:** Operator testing rejection behavior or auditing policy
**Goal:** Confirm fail-closed enforcement is visible in the UI

1. Operator (in test/audit context) attempts to dispatch with `adapter: qlib` or `worker: finrl`
2. Service returns `400` with structured rejection: `{"code": "production_adapter_disabled", "gate_state": "fail_closed"}`
3. UI shows rejection reason with the activation gate criteria (link to maturity matrix)
4. Operator sees that the fail-closed gate is enforced correctly

**UI note:** A "Test Rejection" audit path is acceptable as a read-only validation flow. The rejection itself is the expected outcome.

### Journey D: Monitoring Research Activity

**Who:** Operator or team lead
**Goal:** See recent research jobs, runs, and proposals in one view

*This journey currently requires fanning out to three services.* Until a unified BFF endpoint exists:

1. `GET /api/research-worker-gateway/jobs` — recent worker dispatch jobs
2. `GET /api/research-orchestrator/runs` — recent orchestrator runs
3. `GET /api/policy-learning/jobs` — recent policy proposals
4. Frontend merges and sorts by `created_at` for a unified timeline view

---

## 6. Recommended BFF Endpoints (Future Work)

These are design recommendations for a future BFF aggregation layer. They are **not** deliverables of the parent task or this sidecar.

```
GET  /api/bff/research/capabilities
     Aggregate worker/adapter inventory from all three services + openclaw-gateway-adapter
     Enrich with maturity tier and next_gate from RESEARCH_BACKEND_MATURITY_MATRIX

GET  /api/bff/research/activity
     Unified job/run/proposal listing across all three services
     Query params: status, adapter, worker, actor_id, since (ISO8601)
     Sort: created_at DESC
     Response: merged list with service_source field

GET  /api/bff/research/tasks/{task_id}/full
     Task + all runs + all artifacts + all proposals in one call (orchestrator-sourced)

GET  /api/bff/research/health
     Combined /health + /capabilities gate summary across all four research services
     Shows: service_up, gated_backend_count, available_adapter_count

POST /api/bff/research/tasks
     Pass-through to research-orchestrator; adds BFF-level idempotency envelope
```

### BFF Design Constraints

Any BFF aggregation layer must:

1. **Preserve fail-closed semantics.** Never re-route or rewrite a rejected request to a dormant endpoint.
2. **Propagate idempotency keys.** All three services support `idempotency_key`; BFF must pass them through unchanged.
3. **Normalize rejection codes.** Map each service's rejection `code` to a unified error envelope (`{"source_service": ..., "code": ..., "gate_state": ..., "allowed_scope": ...}`).
4. **Do not expose activation toggles.** No BFF endpoint may accept a parameter that enables production adapters.
5. **Reflect governance boundaries.** When surfacing artifact records, include `governance.direct_live_influence`, `governance.lean_consumption`, `governance.write_boundary` from the orchestrator response.

---

## 7. Frontend Display Rules

| Backend | Display Label | Badge | Dispatch Form |
|---|---|---|---|
| stub | Stub Worker | `available` (green) | Show dispatch form |
| handoff_only | Handoff Only | `available` (green) | Show dispatch form |
| manual | Manual | `available` (green) | Show dispatch form |
| openclaw | OpenClaw | `activation-ready` (amber) | Display only; no dispatch |
| qlib | Qlib | `activation-ready` (amber) | Display only; no dispatch |
| trl | TRL | `activation-ready` (amber) | Display only; no dispatch |
| finrl | FinRL | `activation-ready` (amber) | Display only; no dispatch |
| rllib | RLlib | `activation-ready` (amber) | Display only; no dispatch |
| ray_tune | Ray Tune | `activation-ready` (amber) | Display only; no dispatch |
| wandb | W&B | `activation-ready` (amber) | Display only; no dispatch |

For deferred backends, the UI should display:
- `gate_state: fail_closed` prominently
- `allowed_scope: capability_metadata_read_only`
- Activation gate criteria (from §4 above)
- **No** toggle or "request activation" button — operator activation is governed through the research plane governance process, not the UI

---

## 8. Service URL References (Development Defaults)

| Service | Default Base URL | Config Env |
|---|---|---|
| research-worker-gateway | `http://research-worker-gateway-svc:8103` | `RESEARCH_WORKER_GATEWAY_URL` |
| research-orchestrator | `http://research-orchestrator-svc:8101` | `RESEARCH_ORCHESTRATOR_URL` |
| policy-learning | `http://policy-learning-svc:8100` | `POLICY_LEARNING_URL` |
| openclaw-gateway-adapter | `http://openclaw-gateway-adapter:8104` | `OPENCLAW_GATEWAY_ADAPTER_URL` |

*Port assignments are from the individual service configs; verify against compose definitions before wiring the BFF.*

---

## 9. Handoff Notes for Reviewer (Codex2)

- This packet is a support artifact; it does not modify `RESEARCH_BACKEND_MATURITY_MATRIX.md`, BFF policy docs, or any L1 canonical document.
- The BFF endpoint recommendations in §6 are design pointers for a future task, not deliverables here.
- The operator journey map in §5 is sufficient to proceed with frontend wire-framing if needed.
- The maturity data in §4 is sourced from the already-merged `RESEARCH_BACKEND_MATURITY_MATRIX.md`; if that document is updated, §4 should be refreshed as a follow-on.
- No tests were added; this is a documentation-only sidecar.

---

*This file is a support sidecar. It is not canonical truth. Parent owner (Codex2) decides whether to absorb any recommendations into the main line.*
