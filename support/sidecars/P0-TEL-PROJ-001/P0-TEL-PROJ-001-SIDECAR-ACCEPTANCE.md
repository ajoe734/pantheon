---
sidecar_for: P0-TEL-PROJ-001
helper_kind: acceptance_packet
prepared_by: Claude2
reviewer: Codex
created_at: 2026-05-01
reviewed_at: 2026-05-01
status: accepted
review_outcome: approved
mutates_canonical: false
---

# P0-TEL-PROJ-001 Acceptance Packet

## 1. Task Summary

**Title:** Project paper telemetry into runtime status  
**Owner:** Codex  **Reviewer:** Claude  
**Phase:** Pantheon P0 Paper Loop  
**Depends on:** P0-TEL-001 (done — commit 9884be6)

### Acceptance criteria (from ai-status.json)

1. BFF/runtime summary shows non-mock last heartbeat
2. Projection includes bridge repo, bridge commit, runtime_binding_id, and deployment_stage

---

## 2. Dependency Map

```
P0-TEL-001 (done) ── paper runtime emitter + ingest shock-absorber
    │
    └─► P0-TEL-PROJ-001 (in_progress)  ◄── THIS TASK
           │  telemetry ingest → runtime projection
           │
           ├─► P0-LOOP-001 (todo)       — paper operating loop smoke
           ├─► P0-FE-SOURCE-001 (todo)  — source mode / identity on critical UI
           └─► P1-KILL-001 (todo)       — KillSwitchBridge secondary path + telemetry ack
```

**P0-TEL-PROJ-001 is a critical-path blocker.** P0-LOOP-001 and two other downstream tasks cannot start until this projection layer exists.

---

## 3. What P0-TEL-001 Delivered (upstream dependency)

Commit: `9884be6` — "P0-TEL-001 add paper telemetry emitter"

| Deliverable | Location |
|---|---|
| `RuntimeTelemetryEmitter` | `services/execution/lean_runtime/paper_runtime.py` |
| `TelemetryIngestService` (schema + binding + dedup) | `services/telemetry/ingest_svc.py` |
| Contract tests | `services/telemetry/test_paper_runtime_ingest_contract.py` |
| Design document | `docs/04/pantheon_p0_sd/SD-P0-04_Paper_Runtime_TelemetryEvent_Contract.md` |

### Key invariants already enforced by P0-TEL-001

- `deployment_stage` must equal `paper` on every emitted event
- `binding_id` is required and validated against the binding store
- `engine_bridge_repo` and `engine_bridge_commit` are included in event metadata
- Duplicate `event_id` is idempotently deduplicated

### Fields emitted in metadata by `RuntimeTelemetryEmitter._base_metadata()`

```json
{
  "engine_bridge_repo":      "ajoe734/pantheon-lean.git",
  "engine_bridge_path":      "pantheon/lean",
  "engine_bridge_commit":    "<git-sha>",
  "runtime_adapter_version": "0.1.0",
  "context_source":          "launch_manifest | env_vars",
  "runtime_role":            "paper"
}
```

---

## 4. What P0-TEL-PROJ-001 Must Add

### 4.1 Gap analysis

`TelemetryIngestService` currently:
- ✅ receives events over HTTP
- ✅ validates schema, binding, deployment_stage
- ✅ deduplicates by `event_id`
- ✅ writes to in-memory batch writer (or Postgres via injected `write_fn`)
- ❌ does **not** project heartbeat events into a queryable runtime summary
- ❌ no endpoint exposes last-heartbeat, bridge fields, or deployment_stage to BFF

`PaperRuntimeService.snapshot()` currently:
- ✅ exposes `last_heartbeat_at`, `binding_lookup`, `runtime_context` at `/api/runtime/state`
- ❌ this is the emitter's own local state — it is not updated from ingested telemetry
- ❌ BFF cannot read this to get a canonical runtime summary across sessions

### 4.2 Required deliverables

| # | Deliverable | Notes |
|---|---|---|
| D-1 | `RuntimeStatusProjection` — updates on ingested `heartbeat` event | Owned by telemetry service or runtime-manager |
| D-2 | Projection stores: `runtime_binding_id`, `deployment_stage`, `bridge_repo`, `bridge_commit`, `last_heartbeat_at` | Sourced from event metadata |
| D-3 | Read endpoint: `GET /api/runtime/projection/{runtime_id}` (or equivalent BFF path) | Returns non-mock projection |
| D-4 | BFF integration: runtime summary reads from projection, not from emitter local state | Required for AC-1 |
| D-5 | Non-mock evidence test: inject a valid heartbeat event into ingest → assert projection reflects it | Required for AC-1 and AC-2 |

### 4.3 Minimal projection shape (SD-P0-04 §6)

```json
{
  "runtime_id":         "rt-...",
  "runtime_binding_id": "rtb-...",
  "deployment_stage":   "paper",
  "state":              "active | degraded | terminated",
  "last_heartbeat_at":  "RFC3339",
  "engine_bridge_repo": "ajoe734/pantheon-lean.git",
  "engine_bridge_commit": "<sha>",
  "source":             "telemetry_ingest"
}
```

### 4.4 Scope boundary

- Projection is read-only output — no mutation of TelemetryEvent schema or ingest pipeline.
- Do not wire live broker telemetry — paper-only scope.
- `ReconciliationRecord` and `DriftReport` are P0-REC-001 scope (downstream).
- BFF command split is P0-BFF-CMD-001 scope.

---

## 5. Acceptance Checklist

The reviewer (Claude) should verify all items before approving.

### AC-1: Non-mock last heartbeat visible in runtime summary

- [ ] Injecting a valid `heartbeat` event into `TelemetryIngestService` causes the runtime projection to update `last_heartbeat_at`
- [ ] The projection endpoint returns a timestamp that originated from the telemetry event `created_at`, not from a local clock or stub value
- [ ] `source` field in the projection is `"telemetry_ingest"` (not `"mock"`, `"stub"`, or `"local_state"`)

### AC-2: Projection includes required identity fields

- [ ] Projection contains `bridge_repo` sourced from event metadata field `engine_bridge_repo`
- [ ] Projection contains `bridge_commit` sourced from event metadata field `engine_bridge_commit`
- [ ] Projection contains `runtime_binding_id` sourced from event field `binding_id`
- [ ] Projection contains `deployment_stage` = `"paper"` sourced from event field `deployment_stage`

### AC-3: Paper-only guard honoured

- [ ] A `heartbeat` event with `deployment_stage != "paper"` is rejected by ingest and does NOT update the projection
- [ ] A `heartbeat` event without `binding_id` is rejected and does NOT update the projection

### AC-4: Ingest→projection path is testable

- [ ] A unit or integration test exists that: sends a heartbeat event → checks projection → confirms all four required fields are present and non-empty
- [ ] Test does not use a mock/stub projection; it exercises the actual projection write path

### AC-5: No canonical truth mutation

- [ ] No changes to L1 policy docs (`TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`, etc.)
- [ ] No changes to `TelemetryEvent` schema beyond what the task strictly requires
- [ ] New projection state is scoped to telemetry service or runtime-manager service boundary

---

## 6. Suggested Verification Commands

Run these after implementation to produce evidence for the closeout message:

```bash
# Schema + ingest contract tests from P0-TEL-001 must still pass
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.telemetry.test_paper_runtime_ingest_contract -v

# New projection contract test (to be written by P0-TEL-PROJ-001)
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services.telemetry.test_runtime_projection -v

# Smoke: inject heartbeat via HTTP and query projection endpoint
# (requires telemetry service running)
python3 services/telemetry/smoke_test_projection.py
```

---

## 7. Downstream Impact

Once P0-TEL-PROJ-001 is done:

| Unblocked task | What it needs from this projection |
|---|---|
| P0-LOOP-001 | Smoke test queries projection to confirm loop produced a heartbeat |
| P0-FE-SOURCE-001 | UI reads `bridge_repo`, `bridge_commit`, `runtime_binding_id`, `deployment_stage` from projection |
| P1-KILL-001 | KillSwitchBridge telemetry ack must correlate with this projection |

P0-FE-SOURCE-001 also depends on P0-FE-DEMO-001 — that is a separate dependency outside this packet's scope.

---

## 8. Handoff Note

This packet is a support artifact only.  
It does not modify canonical truth, runtime code, or service contracts.  
Codex (parent task owner) should use this checklist to guide implementation and acceptance sign-off.

After Codex completes P0-TEL-PROJ-001 and obtains Claude's review approval, Codex should reference this packet in the closeout commit body.

**Reviewer for this sidecar packet:** Codex  
**Handoff target:** Codex (P0-TEL-PROJ-001 owner)

---

## 9. Finalization Note (Claude2, 2026-05-01)

**Reviewer approval received:** Codex approved 2026-05-01T07:41:49Z.  
Approval message: "Approved: acceptance packet is support-only, covers dependency map, gap analysis, AC-1 through AC-5, suggested verification, and downstream impact; no canonical truth or runtime implementation changes required."

**Post-creation context:** After this packet was prepared, Codex implemented the P0-TEL-PROJ-001 projection layer and submitted the parent task for review (status: `review` as of 2026-05-01T07:38:16Z). The gap analysis in §4 describes the pre-implementation state and should be read as pre-implementation support context; the implementation addressed all identified gaps.

**Sidecar task closeout:** This sidecar packet is now finalized. The accepted artifact has been committed as task-scoped record. The packet remains available as a reference for the parent task's reviewer (Claude) and downstream task owners.
