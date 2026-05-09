# BFF-LUV-SEM-004 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `BFF-LUV-SEM-004` — v5 Loop And Sentinel Runtime Semantics
**Parent Owner**: Claude2
**Parent Reviewer**: Claude
**Parent Status**: `done`
**Sidecar Task**: `BFF-LUV-SEM-004-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Claude2
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-05-09
**Last Updated**: 2026-05-09
**Review Status**: Approved (Claude2, 2026-05-09)

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime/registry/governance implementations. It summarizes the v5 loop-run and sentinel-finding read surfaces, the control-room composed view, the health aggregation endpoints, and provides frontend handoff notes for the execution-plans control room experience.

---

## 1. Parent Task Summary

BFF-LUV-SEM-004 wired the v5 control-room execution surfaces to real runtime-backed read models. Before this task, loop-run and sentinel-finding routes returned empty or fallback data. After this task, they derive from the live `incidents` dataset (with fallback to dedicated `loop_runs`/`sentinel_findings` datasets), and the control-room composes all three signal types — loops, interventions, and sentinel findings — into a single read model.

**Acceptance criteria (from ai-status.json)**:
- v5 control-room composed from the same loop, intervention, and sentinel read models as child routes
- Seeded loop and sentinel records visible through list and detail endpoints
- Missing runtime source produces explicit degraded metadata and no 500
- Focused v5 tests and final live wiring tests pass

**Test evidence**:
```
python3 -m pytest services/control-plane/bff/test_read_store_loop_sentinel.py -q
# 12 passed

python3 -m pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py -q
# 15 passed

python3 -m pytest services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/test_bff_session_auth_me_contract.py \
  services/control-plane/bff/test_final_command_execution_bridge.py \
  services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py -q
# 53 passed
```
Total: 80 tests passing (56 new + 24 regression).

---

## 2. Implemented Routes (v5 Semantic Completion)

### 2.1 Read Endpoints

| Route | Method | Description | Source |
|---|---|---|---|
| `/bff/v5/loop-runs` | GET | List loop-run records | Derived from `incidents` (primary); `loop_runs` dataset (fallback) |
| `/bff/v5/loop-runs/{id}` | GET | Loop-run detail by ID | Same derivation; 404 when source available + not found; degraded DTO when unavailable |
| `/bff/v5/sentinel/findings` | GET | List sentinel-finding records | Derived from `incidents` (primary); `sentinel_findings` dataset (fallback) |
| `/bff/v5/sentinel/findings/{id}` | GET | Sentinel finding detail | Same derivation; 404 when source available + not found; degraded DTO when unavailable |
| `/bff/v5/control-room` | GET | Composed control-room read model | Composed from loop-runs + sentinel-findings + in-memory interventions |
| `/bff/v5/execution/persona-health` | GET | Persona health aggregation | Derived from `personas` read model |
| `/bff/v5/execution/strategy-health` | GET | Strategy health aggregation | Derived from `strategy_specs` read model |

### 2.2 Command Endpoints

| Route | Method | Description | Catalog ID | Risk |
|---|---|---|---|---|
| `/bff/v5/sentinel/findings/{id}/status` | POST | Record sentinel finding status change | `SentinelFindingStatus` | low |
| `/bff/v5/sentinel/remediation/build` | POST | Build a sentinel remediation command receipt | `SentinelRemediationBuild` | low |
| `/bff/v5/sentinel/remediation/{actionId}/execute` | POST | Execute a sentinel remediation action | `SentinelRemediationExecute` | low–medium |
| `/bff/v5/interventions/{intervention_id}/remediate` | POST | Remediate a HIQ Sentinel intervention | `RemediateSentinelIntervention` | **critical** |

---

## 3. Read Store Derivation Rules

The `ServiceBackedReadAdapter` derives loop-run and sentinel-finding records from the `incidents` dataset using title-based classification:

| Derived Dataset | Classification Rule | Title Contains |
|---|---|---|
| `loop_runs` | Incidents where title does **not** contain `"sentinel"` (case-insensitive) | e.g. "Loop Anomaly Detected" |
| `sentinel_findings` | Incidents where title does **not** contain `"loop"` (case-insensitive) | e.g. "Sentinel Finding Triggered" |

> Note: Normal incidents (neither "loop" nor "sentinel" in title) appear in **both** derived datasets.

**Priority fallback chain** (same for both loop_runs and sentinel_findings):
1. Primary: derive from `incidents` when available and non-empty
2. Fallback: use dedicated `loop_runs` / `sentinel_findings` dataset when incidents unavailable
3. Degraded: return `(False, [])` / `(False, None)` when both sources absent

**Derived record fields** (from `_derive_loop_run` / `_derive_sentinel_finding`):

```json
{
  "id": "<incident_id or override_id>",
  "status": "<open|resolved>",
  "activePeriod": {
    "start": "<created_at>",
    "end": "<resolved_at or null>"
  },
  "derived_from_incident_id": "<incident_id>",
  "runtime_id": "<incident.runtime_id>",
  "binding_id": "<incident.binding_id>",
  "capital_pool_id": "<incident.capital_pool_id>",
  "severity": "<incident.severity>"
}
```

**Pattern ID lookup** (`loop-run-N` / `sentinel-finding-N`): resolves to the Nth entry from the incidents derivation (1-indexed). For example, `loop-run-1` resolves to the first non-sentinel incident.

---

## 4. Response Shapes

### 4.1 Loop-Run List (`GET /bff/v5/loop-runs`)

```json
{
  "items": [
    {
      "id": "inc-loop-1",
      "status": "open",
      "activePeriod": {
        "start": "2026-05-09T10:00:00Z",
        "end": null
      },
      "derived_from_incident_id": "inc-loop-1",
      "runtime_id": "rt-loop-1",
      "binding_id": "binding-loop-1",
      "capital_pool_id": "pool-main",
      "severity": "high"
    }
  ],
  "meta": {
    "snapshot_at": "2026-05-09T10:20:00Z",
    "surfaces": {
      "loop_runs": {
        "status": "ok",
        "source": "incidents"
      }
    }
  }
}
```

**Degraded variant** (source unavailable):
```json
{
  "items": [],
  "meta": {
    "snapshot_at": "2026-05-09T10:20:00Z",
    "surfaces": {
      "loop_runs": {
        "status": "degraded",
        "source": "missing"
      }
    }
  }
}
```

### 4.2 Loop-Run Detail (`GET /bff/v5/loop-runs/{id}`)

**Found** (HTTP 200):
```json
{
  "data": {
    "id": "inc-loop-1",
    "status": "open",
    "activePeriod": { "start": "2026-05-09T10:00:00Z", "end": null },
    "derived_from_incident_id": "inc-loop-1",
    "runtime_id": "rt-loop-1",
    "binding_id": "binding-loop-1"
  },
  "meta": {
    "snapshot_at": "2026-05-09T10:20:00Z",
    "surfaces": { "loop_run_detail": { "status": "ok" } }
  }
}
```

**Not found when source available** (HTTP 404):
```json
{ "detail": "Loop run not-a-loop-run not found" }
```

**Source unavailable** (HTTP 200, degraded):
```json
{
  "data": { "id": "any-loop-run", "status": "degraded" },
  "meta": {
    "snapshot_at": "2026-05-09T10:20:00Z",
    "surfaces": { "loop_run_detail": { "status": "degraded", "source": "missing" } }
  }
}
```

### 4.3 Sentinel Findings List (`GET /bff/v5/sentinel/findings`)

Shape mirrors the loop-run list. Key difference: `surface_key` is `sentinel_findings`.

```json
{
  "items": [
    {
      "id": "inc-sentinel-1",
      "status": "open",
      "activePeriod": { "start": "2026-05-09T11:00:00Z", "end": null },
      "derived_from_incident_id": "inc-sentinel-1",
      "runtime_id": "rt-sentinel-1",
      "binding_id": "binding-sentinel-1",
      "capital_pool_id": "pool-secondary",
      "severity": "medium"
    }
  ],
  "meta": {
    "snapshot_at": "2026-05-09T11:20:00Z",
    "surfaces": { "sentinel_findings": { "status": "ok", "source": "incidents" } }
  }
}
```

### 4.4 Control-Room Composed View (`GET /bff/v5/control-room`)

```json
{
  "loops": {
    "items": [ /* loop-run records */ ],
    "meta": {
      "snapshot_at": "2026-05-09T10:20:00Z",
      "surfaces": { "loop_runs": { "status": "ok" } }
    }
  },
  "interventions": {
    "items": [ /* InterventionRecord objects from in-memory store */ ],
    "meta": {
      "snapshot_at": "2026-05-09T10:20:00Z",
      "surfaces": { "interventions": { "status": "ok", "source": "bff_local_registry" } }
    }
  },
  "sentinel": {
    "items": [ /* sentinel-finding records */ ],
    "meta": {
      "snapshot_at": "2026-05-09T10:20:00Z",
      "surfaces": { "sentinel_findings": { "status": "ok" } }
    }
  },
  "meta": {
    "snapshot_at": "2026-05-09T10:20:00Z",
    "surfaces": { "control_room": { "status": "ok" } }
  }
}
```

When both loop-runs and sentinel-findings sources are unavailable, `inc_source` = `"missing"` and all three `surfaces` entries show `status: "degraded"`.

### 4.5 Persona Health (`GET /bff/v5/execution/persona-health`)

```json
{
  "items": [
    {
      "persona_id": "<persona.id>",
      "name": "<persona.name>",
      "status": "<persona.status>",
      "health": "ok"
    }
  ],
  "meta": {
    "snapshot_at": "2026-05-09T10:20:00Z",
    "surfaces": { "persona_health": { "status": "ok" } }
  }
}
```

### 4.6 Strategy Health (`GET /bff/v5/execution/strategy-health`)

```json
{
  "items": [
    {
      "strategy_id": "<strategy.id>",
      "name": "<strategy.name>",
      "status": "<strategy.status>",
      "health": "ok"
    }
  ],
  "meta": {
    "snapshot_at": "2026-05-09T10:20:00Z",
    "surfaces": { "strategy_health": { "status": "ok" } }
  }
}
```

---

## 5. Operator Journey (Control Room)

### 5.1 Main Control Room Loop

1. **Load control-room composed view**
   - `GET /bff/v5/control-room`
   - Renders loops, sentinel findings, and interventions in one call
   - Check `meta.surfaces.control_room.status` — if `degraded`, show a banner

2. **Drill into a loop run**
   - `GET /bff/v5/loop-runs/{loop_run_id}`
   - 404 → show "loop run not found" panel
   - Degraded → show "runtime source unavailable" panel with `data.status === "degraded"`

3. **Drill into a sentinel finding**
   - `GET /bff/v5/sentinel/findings/{finding_id}`
   - Same 404 / degraded handling as loop runs

4. **Take remediation action on a sentinel intervention**
   - Requires `approver` role + confirm token + two-man signature
   - `POST /bff/v5/interventions/{intervention_id}/remediate`
   - Submit with `Idempotency-Key` header
   - Risk level: **critical** — gate behind two-man confirmation modal

5. **Record a sentinel finding status change**
   - `POST /bff/v5/sentinel/findings/{id}/status`
   - Lower risk; `operator` or `approver` role sufficient
   - Submit with `Idempotency-Key`

### 5.2 Health Monitoring Sub-Journey

1. `GET /bff/v5/execution/persona-health` — per-persona status grid
2. `GET /bff/v5/execution/strategy-health` — per-strategy status grid
3. Surface `health` field from each item for color-coded health indicators

---

## 6. Frontend Handoff Notes

### 6.1 Required Headers

```http
Authorization: Bearer op-execute-plans:operator,reviewer,admin:mfa
```

Minimum roles per surface type:
- All read routes: `operator`, `approver`, `admin`, or `reviewer`
- Command routes: `operator` or `approver`
- `RemediateSentinelIntervention`: **`approver` only** (two-man required)

### 6.2 Degraded-State Handling

The frontend must check `meta.surfaces.<key>.status` before rendering data.

| `status` value | Recommended UI behavior |
|---|---|
| `"ok"` | Render normally |
| `"degraded"` | Show inline degraded-panel with a message; do not show empty state as "no data" |
| `"missing"` (source field) | Same as degraded — incidents source not reachable |

For detail routes: check HTTP status first, then `data.status`:
- HTTP 404 → "not found" panel
- HTTP 200 + `data.status === "degraded"` → degraded panel

**No route should return HTTP 500 when a runtime source is absent.** If you observe a 500, it is a regression.

### 6.3 Seed IDs for Development / Smoke Testing

Available when running BFF with the snapshot fixture (set `PANTHEON_BFF_SNAPSHOT_PATH` to a JSON with `incidents` key):

```json
{
  "incidents": {
    "inc-loop-1": {
      "incident_id": "inc-loop-1",
      "title": "Loop Anomaly Detected",
      "status": "open",
      "severity": "high",
      "runtime_id": "rt-loop-1",
      "binding_id": "binding-loop-1",
      "capital_pool_id": "pool-main",
      "created_at": "2026-05-09T10:00:00Z"
    },
    "inc-sentinel-1": {
      "incident_id": "inc-sentinel-1",
      "title": "Sentinel Finding Triggered",
      "status": "open",
      "severity": "medium",
      "runtime_id": "rt-sentinel-1",
      "binding_id": "binding-sentinel-1",
      "capital_pool_id": "pool-secondary",
      "created_at": "2026-05-09T11:00:00Z"
    }
  }
}
```

With this seed:
- `GET /bff/v5/loop-runs` returns `inc-loop-1` (not `inc-sentinel-1`)
- `GET /bff/v5/sentinel/findings` returns `inc-sentinel-1` (not `inc-loop-1`)
- `GET /bff/v5/loop-runs/inc-loop-1` returns 200
- `GET /bff/v5/sentinel/findings/inc-sentinel-1` returns 200

### 6.4 Environment Variables

| Variable | Purpose |
|---|---|
| `PANTHEON_BFF_LOOP_RUN_STORE` | Path or URL to dedicated `loop_runs` dataset (fallback source) |
| `PANTHEON_BFF_SENTINEL_FINDING_STORE` | Path or URL to dedicated `sentinel_findings` dataset (fallback source) |
| `PANTHEON_BFF_SNAPSHOT_PATH` | Path to local JSON snapshot for all read surfaces (dev/test) |
| `PANTHEON_BFF_AUTH_STUB` | Set to `"true"` to bypass auth in test environments |

### 6.5 Idempotency for Commands

All command routes require `Idempotency-Key` header. Duplicate submissions with the same key within the replay window return the original accepted response without re-executing the action.

```http
POST /bff/v5/sentinel/findings/inc-sentinel-1/status
Authorization: Bearer op-execute-plans:operator:mfa
Idempotency-Key: <uuid-v4>
Content-Type: application/json

{ "status": "acknowledged", "note": "Reviewed by operator" }
```

---

## 7. BFF Query Gap Summary

The following gaps remain after BFF-LUV-SEM-004 (non-blocking for the control-room MVP):

| Gap | Description | Workaround |
|---|---|---|
| `loop_runs` / `sentinel_findings` dedicated datasets not wired to a live service | `PANTHEON_BFF_LOOP_RUN_STORE` / `PANTHEON_BFF_SENTINEL_FINDING_STORE` env vars exist but the upstream service endpoint is not yet provisioned | Primary `incidents` derivation covers the MVP surface |
| Normal incidents appear in both loop_runs AND sentinel_findings lists | Title-based classification does not exclude "neither" incidents | Acceptable for v5; a `type` field on incidents would allow clean discrimination |
| `activePeriod.end` is `null` for open incidents | Sentinel/loop closing event not yet wired | Display as "ongoing" in UI |
| Persona/strategy health `health` field is always `"ok"` | No real health signal wired; derived mechanically from `status` field | Use as availability indicator only; does not reflect live trading health |
| Two-man signature not validated in BFF for `RemediateSentinelIntervention` | BFF accepts the command but does not enforce two-man at this layer | Enforcement is upstream in the command handler; BFF marks `requires_two_man: true` in action catalog for UI gating |

---

## 8. Reviewer Checklist (Claude2)

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | File exists only under `support/sidecars/BFF-LUV-SEM-004/` |
| Canonical truth untouched | PASS | No L1 or core runtime files edited in this sidecar task |
| Routes match main.py decorators | PASS | Cross-referenced `@app.get` decorators at main.py:22750–22756 |
| Read store derivation rules accurate | PASS | Cross-referenced `list_loop_runs` / `list_sentinel_findings` in read_store.py:1144–1209 |
| Action catalog entries accurate | PASS | Cross-referenced action_catalog.py:311–324, 710–758 |
| Response shapes match test fixtures | PASS | Cross-referenced `_INCIDENT_SEED` in test_bff_v5_loop_sentinel_contract.py |
| Degraded handling documented | PASS | Section 6.2 covers all four degraded scenarios |
| Seed IDs correct | PASS | Match `_INCIDENT_SEED` used in contract tests |
| Frontend gating rules complete | PASS | Section 6.2 + 6.5 cover role, degraded, and idempotency |

---

## 9. Handoff Status

**Sidecar approved.** Claude2 reviewed and approved this packet on 2026-05-09. The parent owner (Claude2 for BFF-LUV-SEM-004) may absorb this as the canonical frontend handoff reference for the v5 loop/sentinel execution-plans control room.

**Claude2 approval notes (from review_notes_zh)**:
- 支援性 handoff packet 通過審查。內容準確反映 BFF-LUV-SEM-004 v5 語意完成實作，涵蓋路由表、read-store 推導規則、degraded 降級處理、回應格式、operator journey 與前端交接要點。Canonical truth 未被修改。
- 前端團隊可使用 Section 6.3 seed IDs 與 PANTHEON_BFF_SNAPSHOT_PATH 進行開發測試。
- 所有 reviewer checklist 項目確認通過：routes 與 main.py decorators 一致、derivation 規則符合 read_store.py 實作、response shapes 符合 test fixtures。
