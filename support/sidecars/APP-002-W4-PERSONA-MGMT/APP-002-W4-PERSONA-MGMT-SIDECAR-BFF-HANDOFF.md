# APP-002-W4-PERSONA-MGMT BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002-W4-PERSONA-MGMT` - Implement persona management composed view
**Parent Owner**: Qwen
**Parent Reviewer**: Claude
**Parent Status**: `in_progress` (ready for review)
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Claude (reassigned from Qwen due to capacity)
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-11
**Last Updated**: 2026-04-12
**Review Status**: APPROVED (Claude, 2026-04-12)

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations. It summarizes the Wave 4 persona-management composed view, highlights non-blocking gaps, and provides frontend handoff notes for persona lifecycle management.

---

## 1. Parent Task Summary

Wave 4 adds a composed view to support persona lifecycle management without client-side joins.

**Acceptance criteria (from ai-status)**:
- `persona_management_view_live`
- `no_demo_provider_dependency`
- `backend_shaped_persona_actions`

**Scope (contract + implementation)**:
- Composed view: `GET /api/v1/operator/persona-management/{persona_id}`
- Composes: PS-02, CP-03, CP-04, PS-03, PS-05

**Primary artifacts in scope**:
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
- `services/control-plane/bff/test_persona_management.py`

---

## 2. Current Implementation Snapshot (Code-Backed)

### 2.1 Persona Management Composed View in `main.py`

| View | Endpoint | Composes | Status | Notes |
|---|---|---|---|---|
| Persona Management | `GET /api/v1/operator/persona-management/{persona_id}` | PS-02, CP-03, CP-04, PS-03, PS-05 | Implemented | Uses `_require_read_role`; returns `meta.snapshot_at` + `meta.surfaces`. |

**Behavior**:
- 404 if persona not found.
- `meta.surfaces` includes `persona_bindings`.
- `meta.surfaces` includes `capital_pool_bindings`.
- `meta.surfaces` includes `persona_sessions`.
- `meta.surfaces` includes `teaching_sessions`.
- Enriches each binding with `capital_pool` details (CP-04) when available.
- `snapshot` param accepted but not actively used for alignment in v1 (just returns `snapshot_at = utc_now()`).

### 2.2 ReadSurfaceStore Seed Data

Sample IDs available for UI smoke testing:
- Persona: `persona-alpha`
- Binding: `binding-042` (persona `persona-alpha` -> pool `pool-main`)
- Sessions: `sess-001` (active), `sess-002` (idle)
- Teaching session: `teach-001` (completed)

These are defined in `read_store._default_read_data()` and covered by `test_persona_management.py`.

---

## 3. Operator Journey (Persona Management)

1. Obtain `persona_id` from upstream context (persona registry, prior selection, or stored binding context).
2. Load composed view: `GET /api/v1/operator/persona-management/{persona_id}?snapshot=preferred`
3. Optional drill-down (if/when standalone PS/CP endpoints are exposed): Persona detail (PS-02); Persona bindings (CP-03/CP-04); Sessions (PS-03); Teaching sessions (PS-05).

---

## 4. Frontend Handoff Notes (Persona Management)

### 4.1 Recommended Data Source

Use the composed view as the primary source for persona management screens.

- Primary: `GET /api/v1/operator/persona-management/{persona_id}`
- Role requirement: `operator`, `approver`, `admin`, or `reviewer` (viewer-only tokens are rejected).

### 4.2 Example Request

```http
GET /api/v1/operator/persona-management/persona-alpha?snapshot=preferred
Authorization: Bearer op-42:operator
```

### 4.3 Example Response (Page-Shaped)

```json
{
  "data": {
    "persona": {
      "id": "persona-alpha",
      "name": "Alpha Persona",
      "lifecycle_state": "active",
      "mandate": "systematic_crypto_trading",
      "strategy_family": "momentum",
      "created_at": "2026-03-01T00:00:00Z",
      "last_active_at": "2026-04-11T10:00:00Z"
    },
    "bindings": [
      {
        "id": "binding-042",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-main",
        "capital_pool": {
          "id": "pool-main",
          "status": "ready"
        }
      }
    ],
    "sessions": [
      {
        "id": "sess-001",
        "persona_id": "persona-alpha",
        "status": "active",
        "started_at": "2026-04-11T08:00:00Z",
        "last_heartbeat_at": "2026-04-11T11:55:00Z",
        "tools_enabled": ["signal_read", "artifact_load", "telemetry_query"],
        "pool_scope": "pool-main"
      },
      {
        "id": "sess-002",
        "persona_id": "persona-alpha",
        "status": "idle",
        "started_at": "2026-04-10T14:00:00Z",
        "last_heartbeat_at": "2026-04-10T18:00:00Z",
        "tools_enabled": ["signal_read", "artifact_load"],
        "pool_scope": "pool-main"
      }
    ],
    "teaching_sessions": [
      {
        "id": "teach-001",
        "persona_id": "persona-alpha",
        "status": "completed",
        "started_at": "2026-04-09T09:00:00Z",
        "completed_at": "2026-04-09T09:45:00Z",
        "topic": "drawdown_threshold_tuning",
        "operator_id": "operator-oncall",
        "outcomes": [
          "Adjusted drawdown threshold from 10% to 8% for pool-main",
          "Added queue-depth alerting for promotion gate"
        ],
        "session_artifacts": ["artifact-042"]
      }
    ],
    "allowedActions": {
      "canActivate": false,
      "canEdit": true,
      "canDelete": false,
      "canRetire": true,
      "canPause": false,
      "canTerminateSession": true,
      "canPauseSession": true,
      "canViewTeachingHistory": true
    }
  },
  "meta": {
    "snapshot_at": "2026-04-11T12:00:00Z",
    "surfaces": {
      "persona_bindings": {"status": "ok"},
      "capital_pool_bindings": {"status": "ok"},
      "persona_sessions": {"status": "ok"},
      "teaching_sessions": {"status": "ok"},
      "allowed_actions": {"status": "ok"}
    }
  }
}
```

### 4.4 UI Gating Rules

- Respect `meta.surfaces.*.status`. If `degraded` or `unavailable`, show an explicit degraded panel instead of \"no data\".
- If `capital_pool_bindings` is degraded, bindings may be missing pool metadata. Render a placeholder for pool details.
- Treat the composed view as read-only; any destructive actions should still flow through the operator command endpoints.

---

## 5. Gaps / Follow-Ups (Non-blocking)

1. **Standalone persona surfaces are not yet exposed in `main.py`**. Contract lists PS-01/PS-02/PS-03/PS-05/PS-06, but only the composed view is wired today. Follow-up: `APP-002-W4-REMAINING-CATALOG` will address remaining list/detail read surfaces.

2. ~~**No explicit `allowed_actions` payload for persona management**~~. **RESOLVED**: `read_store.get_persona_allowed_actions()` was added post-draft; `main.py` now includes `data.allowedActions` and `meta.surfaces.allowed_actions` in the composed view response. Acceptance criterion `backend_shaped_persona_actions` is met.

3. **`snapshot` is accepted but not enforced**. `snapshot=preferred` does not yet align surface timestamps; it only returns `meta.snapshot_at`.

4. **Read surface staleness is not tied to `BFF_READ_SURFACE_STATE`**. This view marks degradation only when a sub-surface returns `None` or empty results.

---

## 6. Reviewer Checklist (Claude, 2026-04-12)

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | File exists only under `support/sidecars/` |
| Canonical truth untouched | PASS | No L1 or core runtime files edited |
| Endpoint exists in `main.py` | PASS | `GET /api/v1/operator/persona-management/{persona_id}` at line 873 |
| Role gating correct | PASS | `_require_read_role` enforced; viewer tokens rejected |
| Composed surfaces match contract | PASS | PS-02, CP-03, CP-04, PS-03, PS-05, `allowed_actions` all present |
| 404 on missing persona | PASS | Verified at `main.py:890-895` |
| Seed data matches packet | PASS | `persona-alpha`, `binding-042`, `pool-main`, `sess-001/002`, `teach-001` confirmed in `read_store.py` |
| Tests pass | PASS | `python3 test_persona_management.py` → ALL PASSED (12 assertions) |
| Gap #2 resolved | PASS | `allowedActions` now in response; `backend_shaped_persona_actions` acceptance criterion met |
| Example response accurate | PASS | Updated to include `allowedActions` + `meta.surfaces.allowed_actions` |
| Frontend handoff clear | PASS | Example request/response + UI gating rules documented |

---

## 7. Handoff Status

**APPROVED by Claude (2026-04-12)**. All acceptance criteria met:
- `persona_management_view_live` ✅
- `no_demo_provider_dependency` ✅ (seed data in `read_store._default_read_data()`)
- `backend_shaped_persona_actions` ✅ (`data.allowedActions` in composed view response)

This packet is ready to serve as the front-end handoff reference for the Wave 4 persona-management composed view. Parent owner (Qwen) may absorb into main line at their discretion.
