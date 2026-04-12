# APP-002-W3-POSTINCIDENT-EVOLUTION BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-002-W3-POSTINCIDENT-EVOLUTION` — Implement post-incident and evolution review surfaces
**Parent Owner**: Qwen
**Parent Reviewer**: Codex
**Parent Status**: `done`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Claude
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-11
**Last Updated**: 2026-04-11
**Review Status**: Approved (Claude, 2026-04-11)

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations. It summarizes the Wave 3 post-incident + evolution read surfaces, highlights non-blocking gaps, and provides frontend handoff notes for the post-incident review experience.

---

## 1. Parent Task Summary

Wave 3 makes post-incident analysis and evolution review data-backed via Pantheon BFF read surfaces and the post-incident composed view.

**Acceptance criteria (from ai-status)**:
- `postincident_view_live`
- `evolution_review_surfaces_live`
- `lineage_and_telemetry_evidence_shaped`

**Scope (contract + consensus)**:
- Read surfaces: `EV-01`–`EV-04`, `LN-01`–`LN-03`, `TL-01`–`TL-03`
- Composed view: `GET /api/v1/operator/post-incident-review/{incident_id}` (IN-04 + EV + LN + TL)

**Primary artifacts in scope**:
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
- `services/control-plane/bff/test_w3_surfaces.py`

---

## 2. Current Implementation Snapshot (Code-Backed)

### 2.1 Wave 3 Read Surfaces in `main.py`

| Surface | Endpoint | Status | Notes |
|---|---|---|---|
| EV-01 | `GET /api/v1/evolution-decisions` | Implemented | Filters: `action_type`, `risk_level`, `status`.
| EV-02 | `GET /api/v1/evolution-decisions/{decision_id}` | Implemented | 404 when missing.
| EV-03 | `GET /api/v1/freeze-orders` | Implemented | Filters: `status`, `scope`.
| EV-04 | `GET /api/v1/rollbacks` | Implemented | Filters accepted: `runtime_id`, `action_type`, `time_range` (time_range deferred in read_store).
| LN-01 | `GET /api/v1/lineage` | Implemented | Filter: `artifact_id`.
| LN-02 | `GET /api/v1/lineage/edges/{edge_id}` | Implemented | 404 when missing.
| LN-03 | `GET /api/v1/lineage/graph` | Implemented | Filters: `root_type`, `root_id`, `depth` (depth clamped 1–10; root_type is no-op in v1 read_store).
| TL-01 | `GET /api/v1/telemetry` | Implemented | Filters: `pool_id`, `artifact_id`, `time_range` (pool/time_range deferred in read_store).
| TL-02 | `GET /api/v1/telemetry/{runtime_id}/summary` | Implemented | `time_range`, `aggregate_by` accepted but not used in v1 store.
| TL-03 | `GET /api/v1/telemetry/{artifact_id}/performance` | Implemented | `time_range` accepted but not used in v1 store.

**Staleness metadata**:
- Most read endpoints return `meta.staleness` when `BFF_READ_SURFACE_STATE != fresh`.
- Allowed read roles: `operator`, `approver`, `admin`, `reviewer` (viewer tokens are rejected).

### 2.2 Post-Incident Composed View

| View | Endpoint | Composes | Status |
|---|---|---|---|
| Post-Incident Review | `GET /api/v1/operator/post-incident-review/{incident_id}` | IN-04, EV-01, EV-02, LN-01, TL-03 | Implemented |

**Behavior**:
- Loads incident first; 404 if incident missing.
- Fetches postmortem by incident ID. Missing postmortem -> `meta.surfaces.postmortem = degraded` with message.
- Uses `incident.artifact_id` to fetch lineage edges and telemetry performance. Missing or empty results -> `degraded` with staleness markers.
- Returns `meta.snapshot_at` + `meta.surfaces` for panel-level gating.

### 2.3 Seed Data Available in `ReadSurfaceStore`

- Incidents: `inc-20260410-001` (open) and `inc-20260409-002` (resolved)
- Postmortem: `pm-20260409-002` for `inc-20260409-002`
- Evolution decision: `evo-dec-001` linked to `inc-20260410-001`
- Lineage edges: `ln-edge-001` (artifact-041 -> artifact-042), `ln-edge-002` (artifact-042 -> artifact-043)
- Telemetry performance: `artifact-042` chart + summary

These are the IDs used in `test_w3_surfaces.py` and can be used for front-end smoke testing.

---

## 3. Operator Journey (Post-Incident Review)

1. Locate incident or postmortem
   - `GET /api/v1/incidents?status=resolved` or `GET /api/v1/postmortems`
2. Load post-incident review
   - `GET /api/v1/operator/post-incident-review/{incident_id}?snapshot=preferred`
3. Optional drill-down
   - EV decision list/detail: `/api/v1/evolution-decisions` + `/api/v1/evolution-decisions/{decision_id}`
   - Lineage graph: `/api/v1/lineage/graph?root_id=artifact-042&depth=3`
   - Telemetry performance: `/api/v1/telemetry/{artifact_id}/performance`

---

## 4. Frontend Handoff Notes (Post-Incident Review)

### 4.1 Recommended Data Source

Use the composed view for the post-incident review screen.

- Primary: `GET /api/v1/operator/post-incident-review/{incident_id}`
- Secondary drill-down: EV/LN/TL endpoints as needed for panels

### 4.2 Example Request

```http
GET /api/v1/operator/post-incident-review/inc-20260409-002?snapshot=preferred
Authorization: Bearer op-42:operator
```

### 4.3 Example Response (Page-Shaped)

```json
{
  "data": {
    "incident": {
      "incident_id": "inc-20260409-002",
      "title": "Deployment plan plan-F-042 stalled at paper stage",
      "status": "resolved",
      "artifact_id": "artifact-042",
      "artifact_version": "v2.1.0",
      "runtime_id": "runtime-042",
      "trace_id": "trace-inc-20260409-002"
    },
    "postmortem": {
      "postmortem_id": "pm-20260409-002",
      "status": "published",
      "root_cause": "Promotion gate timeout was set too low (30s) for artifact validation under load.",
      "action_items": [
        "Increase promotion gate timeout to 120s",
        "Add queue-depth alerting for promotion gate"
      ]
    },
    "evolution_decisions": [
      {
        "id": "evo-dec-001",
        "action_type": "retrain",
        "risk_level": "medium",
        "status": "approved",
        "incident_ref": "inc-20260410-001",
        "artifact_id": "artifact-042"
      }
    ],
    "lineage_edges": [
      {
        "id": "ln-edge-001",
        "from_artifact_id": "artifact-041",
        "to_artifact_id": "artifact-042",
        "relationship": "derived_from"
      },
      {
        "id": "ln-edge-002",
        "from_artifact_id": "artifact-042",
        "to_artifact_id": "artifact-043",
        "relationship": "promoted_to"
      }
    ],
    "telemetry_performance": {
      "artifact_id": "artifact-042",
      "window": "24h",
      "summary": {
        "total_pnl": -0.12,
        "max_drawdown": 0.125,
        "sharpe_ratio": -0.8
      }
    }
  },
  "meta": {
    "snapshot_at": "2026-04-11T12:00:00Z",
    "surfaces": {
      "postmortem": {"status": "ok"},
      "evolution_decisions": {"status": "ok"},
      "lineage": {"status": "ok"},
      "telemetry_performance": {"status": "ok"}
    }
  }
}
```

### 4.4 UI Gating Rules

- Always respect `meta.surfaces.*.status`. If `degraded` or `unavailable`, show an explicit degraded panel instead of “no data”.
- If `postmortem` is missing, display “Postmortem pending” with incident ID + evidence summary.
- If `lineage_edges` or `telemetry_performance` are empty, display a “No evidence yet” panel and include staleness note.
- Treat `BFF_READ_SURFACE_STATE != fresh` as a warning banner for all read-only panels.

---

## 5. Gaps / Follow-Ups (Non-blocking)

1. **TL-01 filtering is partial**
   - `pool_id` and `time_range` filters are accepted but ignored in v1 read_store.
   - `artifact_id` filter matches runtime IDs in telemetry summaries (v1 mapping).

2. **TL-02/TL-03 ignore `time_range` and `aggregate_by`**
   - Parameters are accepted but not applied in v1 store.

3. **EV-04 time range filtering deferred**
   - `time_range` accepted in endpoint signature but not used in read_store.

4. **LN-03 root_type is a no-op**
   - Root type filtering requires registry metadata; v1 returns edges by `root_id` only.

5. **Viewer role not recognized**
   - BFF read surfaces require `operator`/`approver`/`admin`/`reviewer`. Tokens with only `viewer` will fail role checks.

---

## 6. Reviewer Checklist (Claude)

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | File exists only under `support/sidecars/` |
| Canonical truth untouched | PASS | No L1 or core runtime files edited |
| Snapshot matches code | PASS | `main.py` + `read_store.py` + `test_w3_surfaces.py` |
| Frontend handoff clear | PASS | Example request/response + gating rules |

---

## 7. Handoff Status

Review approved. This packet is ready for the parent owner to absorb as the front-end handoff reference for the post-incident review experience and Wave 3 EV/LN/TL read surfaces.
