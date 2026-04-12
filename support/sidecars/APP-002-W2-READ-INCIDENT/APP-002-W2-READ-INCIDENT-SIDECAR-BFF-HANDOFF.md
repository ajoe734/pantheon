# APP-002-W2-READ-INCIDENT BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002-W2-READ-INCIDENT` — Implement Incident Response read surfaces and composed view
**Parent Owner**: Qwen
**Parent Reviewer**: Claude
**Parent Status**: `done`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Qwen
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-11
**Last Updated**: 2026-04-11
**Review Status**: Approved by Qwen (2026-04-11)

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations. It summarizes the current incident read surfaces, highlights gaps vs the Wave 2 scope, and provides front-end handoff notes.

---

## 1. Parent Task Summary

Wave 2 incident read work must make the Incident Response page fully data-backed by Pantheon. The scope is anchored in the consensus packet and the BFF contract.

**Acceptance criteria (from ai-status)**:
- `incident_read_surfaces_live`
- `incident_response_view_implemented`
- `degraded_states_explicit`

**Scope from consensus packet**:
- Implement `IN-02`, `RT-03`, `TL-02`, `RT-04`, `EV-04`, `IN-05`
- Implement `GET /api/v1/operator/incident-response/{incident_id}`

**Primary artifacts in scope**:
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/BFF_API_CONTRACT.md`

---

## 2. Current Implementation Snapshot (Code-Backed)

### 2.1 Read Surfaces Implemented in `main.py`

| Surface | Endpoint | Status | Notes |
|---|---|---|---|
| IN-01 | `GET /api/v1/incidents` | Implemented | Supports `status`, `severity`, `affected_pool_id` filters and `meta.total`.
| IN-02 | `GET /api/v1/incidents/{incident_id}` | Implemented | Returns incident detail with `meta.staleness`.
| IN-03 | `GET /api/v1/postmortems` | Implemented | Lists postmortems, `meta.total`.
| IN-04 | `GET /api/v1/postmortems/{report_id}` | Implemented | Returns postmortem + `linked_incident` when available.
| IN-05 | `GET /api/v1/kill-switch/status` | Implemented | Returns `active_freeze_orders`, `safe_mode_status`, plus `meta.active` and `meta.last_checked_at`.
| RT-04 | `GET /api/v1/runtimes/{runtime_id}/rollbacks` | Implemented | Rollback list by runtime.

### 2.2 Composed Views Already Present

| View | Endpoint | Composes | Status |
|---|---|---|---|
| Incident Response | `GET /api/v1/operator/incident-response/{incident_id}` | IN-02, RT-03, TL-02, RT-04, EV-04, IN-05 | Implemented (uses read-store joins + per-surface status)
| Post-Incident Review | `GET /api/v1/operator/post-incident-review/{incident_id}` | IN-04, EV-01, EV-02, LN-01, TL-03 | Implemented early (Wave 3 scope)

### 2.3 Seed Data Available in `ReadSurfaceStore`

- `incidents`, `postmortems`, `kill_switch`
- `telemetry_summaries`, `rollbacks_by_incident`, `evolution_decisions`
- Example incident ID: `inc-20260410-001`

---

## 3. Gaps vs Wave 2 Scope

1. **RT-03 endpoint missing**
`GET /api/v1/runtimes/{runtime_id}/status` is in `BFF_API_CONTRACT.md` but not implemented in `main.py`.
   - Current substitute: `GET /api/v1/runtime-bindings/{binding_id}` returns `RuntimeBinding` and is used by the composed view.

2. **TL-02 endpoint missing**
`GET /api/v1/telemetry/{runtime_id}/summary` is referenced in contract and read-store links but not implemented.

3. **EV-04 endpoint missing**
`GET /api/v1/rollbacks` (global rollback record list) is part of the contract and Wave 2 scope but not implemented.

4. **Runtime mapping placeholder**
`incident-response` resolves runtime binding via `incident.binding_id` and falls back to `incident.runtime_id` if present. There is no runtime lookup by persona in this view; post-incident review still uses `incident.affected_persona_id` as a telemetry placeholder.

5. **Degraded-state fidelity**
`meta.surfaces` is present but `kill_switch`, `rollbacks`, and `evolution_decisions` are always marked `ok` even when absent. To satisfy `degraded_states_explicit`, consider `status=degraded` or `unavailable` when the read-store lacks data or when `BFF_READ_SURFACE_STATE` is not `fresh`.

---

## 4. Operator Journey (Incident Response)

1. List or locate incident: `GET /api/v1/incidents?status=active`
2. Open incident detail: `GET /api/v1/incidents/{incident_id}`
3. Load composed incident response: `GET /api/v1/operator/incident-response/{incident_id}?snapshot=preferred`
4. Use `meta.surfaces` to render degraded panels and show fallback instructions when critical surfaces are unavailable.

---

## 5. Frontend Handoff Notes (Incident Response)

### 5.1 Recommended Data Source

Use the composed view for the incident response screen.

- Primary: `GET /api/v1/operator/incident-response/{incident_id}`
- Optional list for routing: `GET /api/v1/incidents?status=active`

### 5.2 Example Request

```http
GET /api/v1/operator/incident-response/inc-20260410-001?snapshot=preferred
Authorization: Bearer op-42:operator
```

### 5.3 Example Response (Page-Shaped)

```json
{
  "data": {
    "incident": {
      "id": "inc-20260410-001",
      "title": "Unexpected drawdown in persona-alpha",
      "severity": "high",
      "status": "active",
      "affected_pool_id": "pool-main",
      "affected_persona_id": "persona-alpha",
      "created_at": "2026-04-10T14:30:00Z",
      "updated_at": "2026-04-10T15:00:00Z",
      "description": "Persona-alpha experienced a 12% drawdown exceeding the 10% threshold.",
      "mitigation_actions": ["runtime-042 paused", "kill-switch evaluated"],
      "evidence_refs": [
        {"type": "telemetry", "ref": "tl-001", "link": "/api/v1/telemetry/runtime-042/summary"},
        {"type": "runtime_binding", "ref": "runtime-042", "link": "/api/v1/runtime-bindings/runtime-042"}
      ]
    },
    "runtime_binding": {
      "id": "runtime-042",
      "runtime_id": "runtime-042",
      "deployment_stage": "none",
      "status": "idle",
      "plan_id": "plan-F-042"
    },
    "telemetry_summary": {
      "runtime_id": "runtime-042",
      "window": "1h",
      "pnl": -0.12,
      "drawdown": 0.125,
      "sharpe_ratio": -0.8,
      "total_trades": 47,
      "fill_rate": 0.94,
      "avg_slippage_bps": 3.2,
      "collected_at": "2026-04-10T15:00:00Z"
    },
    "rollbacks": [
      {
        "id": "rb-001",
        "runtime_id": "runtime-042",
        "action_type": "rollback",
        "from_version": "v2.1.0",
        "to_version": "v2.0.0",
        "status": "completed",
        "initiated_at": "2026-04-10T14:45:00Z",
        "completed_at": "2026-04-10T14:50:00Z",
        "initiated_by": "operator-oncall",
        "reason": "Excessive drawdown triggered automatic rollback"
      }
    ],
    "evolution_decisions": [
      {
        "id": "evo-dec-001",
        "action_type": "retrain",
        "risk_level": "medium",
        "status": "approved",
        "incident_ref": "inc-20260410-001",
        "artifact_id": "artifact-042",
        "created_at": "2026-04-10T16:00:00Z"
      }
    ],
    "kill_switch": {
      "active_freeze_orders": [],
      "safe_mode_status": "off"
    }
  },
  "meta": {
    "snapshot_at": "2026-04-11T12:00:00Z",
    "surfaces": {
      "runtime_binding": {"status": "ok"},
      "telemetry_summary": {"status": "ok"},
      "rollbacks": {"status": "ok"},
      "evolution_decisions": {"status": "ok"},
      "kill_switch": {"status": "ok"}
    }
  }
}
```

### 5.4 UI Gating Rules

- If `meta.surfaces.*.status` is `degraded` or `unavailable`, show an explicit degraded panel instead of “no data”.
- If `BFF_READ_SURFACE_STATE` is `unavailable`, treat incident data as unverifiable and instruct operator to use the secondary control path.
- Kill-switch status must never be implied by empty data; show “status unknown” if `meta.last_checked_at` is missing.
- Use the secondary control path for actions (CLI or internal admin endpoints) when the composed view is degraded.

---

## 6. Follow-up Checklist (If Contract Parity Required)

1. Add RT-03 endpoint: `GET /api/v1/runtimes/{runtime_id}/status`.
2. Add TL-02 endpoint: `GET /api/v1/telemetry/{runtime_id}/summary`.
3. Add EV-04 endpoint: `GET /api/v1/rollbacks` with filters `runtime_id`, `action_type`, `time_range` (v1 may ignore filters but must accept them).
4. If post-incident telemetry needs a runtime binding rather than persona fallback, add a runtime lookup path in the post-incident view (incident-response already uses `binding_id` + `runtime_id` fallback).
5. Ensure `meta.surfaces` explicitly marks missing data as `degraded` or `unavailable` and carries staleness metadata when needed.
6. Add a sample payload artifact (suggested): `docs/examples/incident-response-page.json`.
7. Extend `services/control-plane/bff/smoke_test.py` with incident response coverage to lock behavior.

---

## 7. Reviewer Checklist (Qwen)

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only this sidecar file created |
| Canonical truth untouched | PASS | No L1 docs or core runtime files edited |
| Gap list matches contract | PASS | Gaps map to `BFF_API_CONTRACT.md` + `main.py` |
| Frontend handoff clear | PASS | Example request/response + UI gating rules |

---

## 8. Handoff to Reviewer (Qwen)

Review approved and recorded. This packet remains as the reference for remaining contract gaps and front-end handoff notes.
