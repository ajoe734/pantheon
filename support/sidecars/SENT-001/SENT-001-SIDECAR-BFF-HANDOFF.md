# SENT-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SENT-001` — `/bff/v5/sentinel/findings` endpoint
**Parent Owner**: `Claude2`
**Parent Reviewer**: `Codex`
**Parent Status**: `done` (archived `2026-05-16T11:08:06Z`, commit `bd9a735183740eb20fbae28c8664d6d79884c278`)
**Sidecar Task**: `SENT-001-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Claude2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-05-16`

> Support artifact only. Does not modify canonical truth, L1 policy documents, or core
> runtime/registry/governance implementations. Packages the completed SENT-001 BFF
> reality into a single reviewer-ready handoff packet for frontend integration.

---

## 1. Executive Summary

SENT-001 is complete and archived. The `/bff/v5/sentinel/findings` list endpoint now
exposes three optional query filters (`kind`, `status`, `severity`) and enriches each
`SentinelFinding` record with a `kind` field derived either from the incident payload
or inferred from title keywords. The OpenAPI spec correctly documents these query
parameters under `operationId: bff_v5_sentinel_findings_list_bff_v5_sentinel_findings_get`.

This packet gives the frontend (and Codex reviewer) one bounded place to understand
the full BFF contract surface, the response shape, the operator journey, and the
remaining query gaps — without re-reading the full parent closeout.

---

## 2. Source References

| Source | Purpose |
|---|---|
| `ai-task-archive/tasks/SENT-001.json` | Archived parent truth: done, commits, handoff history |
| `support/evidence/SENT-001/README.md` | Implementation notes, OpenAPI fix detail, test matrix |
| `support/reviews/SENT-001-review-codex.md` | Codex review approval and acceptance wording |
| `services/control-plane/bff/main.py` (L24362–24417) | Dedicated list route with filter validation |
| `services/control-plane/bff/main.py` (L24297–24358) | Status/remediation command routes |
| `services/control-plane/bff/main.py` (L25754–25756) | Generic alias routes for loop-runs + finding detail |
| `services/control-plane/bff/read_store.py` (L1436–1576) | `_derive_sentinel_finding`, `list_sentinel_findings`, `_apply_sentinel_filters` |
| `services/control-plane/bff/test_sent001_sentinel_findings_contract.py` | 16 contract tests covering all filter combinations |

---

## 3. Endpoint Inventory

### 3.1 Read Endpoints

| Method | Path | operationId | Auth |
|---|---|---|---|
| `GET` | `/bff/v5/sentinel/findings` | `bff_v5_sentinel_findings_list_bff_v5_sentinel_findings_get` | read-role required |
| `GET` | `/bff/v5/sentinel/findings/{id}` | *(generic alias)* | read-role required |

### 3.2 Command Endpoints

| Method | Path | Command type | Auth |
|---|---|---|---|
| `POST` | `/bff/v5/sentinel/findings/{id}/status` | `SENTINEL_FINDING_STATUS` | read-role required |
| `POST` | `/bff/v5/sentinel/remediation/build` | `SENTINEL_REMEDIATION_BUILD` | read-role required |
| `POST` | `/bff/v5/sentinel/remediation/{actionId}/execute` | `SENTINEL_REMEDIATION_EXECUTE` | read-role required |

---

## 4. BFF Query Contract

### 4.1 List Endpoint — `GET /bff/v5/sentinel/findings`

**Query parameters** (all optional):

| Parameter | Type | Allowed values | Behaviour on invalid value |
|---|---|---|---|
| `kind` | string | `hiq_sentinel`, `risk_breach`, `strategy_drift`, `loop_anomaly` | HTTP 400 with `ErrorCode.INVALID_REQUEST` |
| `status` | string | `open`, `resolved`, `dismissed`, `escalated` | HTTP 400 with `ErrorCode.INVALID_REQUEST` |
| `severity` | string | `critical`, `high`, `medium`, `low` | HTTP 400 with `ErrorCode.INVALID_REQUEST` |

Filter matching is **case-insensitive**. Multiple filters compose as AND intersection.
Omitting a filter returns all matching records for the remaining dimensions.

**Example requests:**
```
GET /bff/v5/sentinel/findings
GET /bff/v5/sentinel/findings?kind=risk_breach
GET /bff/v5/sentinel/findings?status=open
GET /bff/v5/sentinel/findings?severity=critical
GET /bff/v5/sentinel/findings?kind=hiq_sentinel&severity=high
```

### 4.2 Response Shape

The response shape varies by data availability. Three representative states:

**Normal (ok) — data served from a backend read store:**

```json
{
  "data": [...],
  "items": [...],
  "page_info": {
    "next_page_token": null,
    "total": 3
  },
  "meta": {
    "snapshot_at": "2026-05-16T11:00:00Z",
    "surfaces": {
      "sentinel_findings": {
        "status": "ok",
        "source": "<read-store-provenance>"
      }
    },
    "total": 3
  }
}
```

**Degraded — data served from local BFF snapshot fallback:**

```json
{
  "data": [...],
  "items": [...],
  "page_info": {
    "next_page_token": null,
    "total": 2
  },
  "meta": {
    "snapshot_at": "2026-05-16T11:00:00Z",
    "surfaces": {
      "sentinel_findings": {
        "status": "degraded",
        "source": "local_snapshot",
        "note": "Served from local BFF snapshot fallback instead of a backend-owned read store.",
        "staleness": {
          "served_from": "local_snapshot",
          "last_known_at": "2026-05-16T11:00:00Z"
        }
      }
    },
    "total": 2,
    "degradation": {
      "reason": "sentinel findings is degraded and may be stale."
    }
  }
}
```

**Unavailable — no data in any tier:**

```json
{
  "data": [],
  "items": [],
  "page_info": {
    "next_page_token": null,
    "total": 0
  },
  "meta": {
    "snapshot_at": "2026-05-16T11:00:00Z",
    "surfaces": {
      "sentinel_findings": {
        "status": "unavailable",
        "source": "missing"
      }
    },
    "total": 0,
    "degradation": {
      "reason": "sentinel findings is currently unavailable."
    }
  }
}
```

Notes:
- `data` and `items` are identical arrays (both present for client compatibility).
- `meta.surfaces.sentinel_findings.source` is a **read-store provenance tag** emitted
  by `read_store.dataset_source()`. It reflects *how* the data reached the BFF, not
  which logical dataset was queried internally. Common provenance values include
  `"local_snapshot"` (BFF local fallback), `"consultation_service_client"`,
  `"consultation_service_store"`, and `"missing"` (no data). The logical dataset names
  `"incidents"` and `"sentinel_findings"` are **not** emitted as `source` values.
- `meta.degradation` is **only present** when `status` is `"degraded"` or
  `"unavailable"`. It is absent from ok responses.
- `page_info.next_page_token` is always `null` (no cursor pagination on this surface).

### 4.3 SentinelFinding Record Shape

```json
{
  "id": "sentinel-finding-1",
  "status": "open",
  "kind": "risk_breach",
  "derived_from_incident_id": "incident-42",
  "runtime_id": "runtime-abc",
  "binding_id": "binding-xyz",
  "severity": "high",
  "title": "Capital risk breach detected"
}
```

| Field | Source | Notes |
|---|---|---|
| `id` | `incident_id` or `id` from incident | Falls back to positional override for numbered IDs |
| `status` | `incident.status` | Defaults to `"unknown"` if absent |
| `kind` | `incident.kind` → `_infer_sentinel_kind(title)` | Inferred via title keyword matching; `null` if unrecognised |
| `derived_from_incident_id` | `incident_id` or `id` from incident | Always the raw incident ID |
| `runtime_id` | `incident.runtime_id` | May be `null` |
| `binding_id` | `incident.binding_id` | May be `null` |
| `severity` | `incident.severity` | May be `null` |
| `title` | `incident.title` | May be `null` |

**Kind inference keywords:**

| kind | Matched title keywords |
|---|---|
| `hiq_sentinel` | `hiq`, `sentinel` |
| `risk_breach` | `risk`, `breach`, `capital` |
| `strategy_drift` | `drift`, `strategy` |
| `loop_anomaly` | `loop`, `anomaly` |

---

## 5. Data Source Fallback Logic

The `list_sentinel_findings` implementation uses a two-tier dataset resolution:

1. **Primary — `incidents` dataset**: If available, derives SentinelFinding records
   from all incidents whose title does **not** contain `"loop"` (loop runs are
   excluded from the sentinel surface).
2. **Fallback — `sentinel_findings` dataset**: Used only when the `incidents` dataset
   is unavailable. Records are used as-is without derivation.

**Important:** this two-tier selection is an **internal BFF implementation detail**.
The logical dataset keys `"incidents"` and `"sentinel_findings"` are **not** exposed
as `meta.surfaces.sentinel_findings.source` in the response. The emitted `source`
value comes from `read_store.dataset_source()` which returns a storage-layer
provenance tag for the chosen dataset (e.g. `"local_snapshot"`,
`"consultation_service_client"`, `"consultation_service_store"`).

The sentinel findings route (`main.py:24415-24417`) sets:
- `source = "missing"` explicitly when no data is available (neither tier has records).
- `source = None` when data is available, delegating provenance resolution to
  `_dataset_surface_status → dataset_source()` at response-build time.

Frontend-relevant mapping:
- `source == "missing"` → `status` is `"unavailable"`; no data rendered.
- `source == "local_snapshot"` → `status` is `"degraded"`; stale data, show badge.
- Any other provenance value → `status` is `"ok"` unless the surface state itself
  is degraded; render normally.

---

## 6. Operator Journey

### 6.1 Sentinel Triage Flow

The typical operator journey for the sentinel findings surface is:

```
1. Operator opens Sentinel panel
   → GET /bff/v5/sentinel/findings
   (unfiltered — shows all active findings)

2. Operator filters by kind
   → GET /bff/v5/sentinel/findings?kind=risk_breach
   (or hiq_sentinel / strategy_drift / loop_anomaly)

3. Operator filters open critical findings
   → GET /bff/v5/sentinel/findings?status=open&severity=critical

4. Operator opens finding detail
   → GET /bff/v5/sentinel/findings/{id}

5. Operator triggers status update (e.g. escalate)
   → POST /bff/v5/sentinel/findings/{id}/status
      { "action": "escalate" }

6. Operator requests remediation plan
   → POST /bff/v5/sentinel/remediation/build
      { "finding_id": "<id>" }

7. Operator executes approved remediation action
   → POST /bff/v5/sentinel/remediation/{actionId}/execute
      { "confirmed": true }
```

### 6.2 Frontend Rendering Rules

| State | Frontend rule |
|---|---|
| `meta.surfaces.sentinel_findings.status == "ok"` | Render findings list normally |
| `meta.surfaces.sentinel_findings.status == "degraded"` | Show stale badge; render available data |
| `meta.surfaces.sentinel_findings.status == "unavailable"` or `source == "missing"` | Show empty-state panel; do not render stale data |
| `meta.degradation.reason` present | Display inline warning message to operator |
| `kind == null` on a finding | Render as "Unknown" kind; do not hide the record |
| Filter returns `data: []` | Show "No findings match this filter" — not an error state |

---

## 7. BFF Query Gap Analysis

The following gaps are noted for future sprint work. None block the current SENT-001
delivery; all are post-SENT-001 backlog items.

| Gap ID | Area | Description | Priority |
|---|---|---|---|
| SF-GAP-001 | Pagination | `page_info.next_page_token` is always `null`; large sentinel datasets cannot be paginated | P2 |
| SF-GAP-002 | Multi-value filter | `kind` / `status` / `severity` accept only a single value; frontend cannot request `kind=risk_breach,hiq_sentinel` in one call | P2 |
| SF-GAP-003 | Sort order | No `sort_by` or `sort_dir` parameter; findings are returned in dataset iteration order | P3 |
| SF-GAP-004 | Time-range filter | No `created_after` / `created_before` filter; operator cannot narrow to recent findings | P2 |
| SF-GAP-005 | Kind inference coverage | Incidents with titles not matching any keyword produce `kind: null`; frontend must handle null gracefully | P1 (FE defence) |
| SF-GAP-006 | Detail command responses | `POST /bff/v5/sentinel/findings/{id}/status` returns a command envelope (202); no polling endpoint for command resolution yet | P1 |

---

## 8. Frontend Integration Checklist

Items for the frontend implementer before wiring the Sentinel panel to the live BFF:

| # | Check | Status |
|---|---|---|
| FE-1 | Call `GET /bff/v5/sentinel/findings` with no filters for the default panel view | ⬜ Pending |
| FE-2 | Read `meta.surfaces.sentinel_findings.status` before rendering — gate on `ok` / `degraded` | ⬜ Pending |
| FE-3 | Handle `kind: null` on individual findings gracefully (render "Unknown") | ⬜ Pending |
| FE-4 | Validate filter values client-side before sending to avoid 400 responses | ⬜ Pending |
| FE-5 | Display `meta.degradation.reason` when present | ⬜ Pending |
| FE-6 | Use `items` array (not `data`) as the canonical list field (both are present but `items` is the v5 standard) | ⬜ Pending |
| FE-7 | Do not cache filter results across `kind` / `status` / `severity` combinations | ⬜ Pending |
| FE-8 | Post `finding_id` in the body of `POST /bff/v5/sentinel/remediation/build` to get a stable `target_id` | ⬜ Pending |

---

## 9. OpenAPI Registration Confirmation

Confirmed after commit `4050626a`:

```
operationId: bff_v5_sentinel_findings_list_bff_v5_sentinel_findings_get
parameters:
  - name: kind
    in: query
    description: "Filter by kind: hiq_sentinel, risk_breach, strategy_drift, loop_anomaly"
  - name: status
    in: query
    description: "Filter by status: open, resolved, dismissed, escalated"
  - name: severity
    in: query
    description: "Filter by severity: critical, high, medium, low"
  - name: authorization
    in: header
```

The generic alias `sem_final_generic_read_alias` no longer owns
`/bff/v5/sentinel/findings` (it was removed in the same commit), so the OpenAPI
spec reflects the dedicated filtered route only.

---

## 10. Test Coverage Summary

| File | Tests | What it covers |
|---|---|---|
| `test_sent001_sentinel_findings_contract.py` | 16 | kind field, all three filter dimensions, combined filters, no-filter baseline, OpenAPI regression |
| `test_bff_v5_loop_sentinel_contract.py` | part of 33 | Loop/sentinel surface regression: detail and list routes, source-aware response |
| `test_read_store_loop_sentinel.py` | part of 33 | ReadStore `list_sentinel_findings`, `get_sentinel_finding`, `_apply_sentinel_filters` |

All 49 tests pass as of the closeout verification (2026-05-16).

---

## 11. Reviewer Notes

**Revision v2 (2026-05-16):** Addresses Codex review findings from
`support/reviews/SENT-001-SIDECAR-BFF-HANDOFF-review-codex.md` (commit `797ddcb1`):

- §4.2 response-shape example split into three states (ok / degraded / unavailable).
  The erroneous combined `status: "ok"` + `meta.degradation` example is removed.
  The `source: "incidents"` example value is replaced with `"<read-store-provenance>"`
  and a note explaining that `source` is a provenance tag, not a logical dataset name.
- §5 Data Source Fallback Logic clarified: the two-tier dataset selection is internal
  BFF logic; the emitted `source` field comes from `dataset_source()` provenance, not
  the dataset key strings `"incidents"` / `"sentinel_findings"`. Frontend-relevant
  source values (`"missing"`, `"local_snapshot"`, others) are documented.

Original reviewer questions (still open for post-SENT-001 backlog):

1. **Gap completeness**: Are there BFF query gaps not captured in §7 that Codex
   considers blocking for the EPIC-EVOLUTION frontend milestone?
2. **FE-6 field name**: Is there a project-wide decision on `data` vs `items` as the
   primary list key for v5 endpoints, and does this endpoint comply?
3. **SF-GAP-005 severity**: Should `kind: null` findings be filtered server-side
   (hidden from operator) or surfaced with a UI label? This affects the kind-filter
   semantics when `kind=null` is not a valid filter value.
4. **Command polling**: SF-GAP-006 notes that no command-resolution polling endpoint
   exists. Should the frontend poll `/bff/v5/sentinel/findings/{id}` to observe
   status changes after a `POST /bff/v5/sentinel/findings/{id}/status` command?

Parent task SENT-001 is `done` and archived; these questions affect post-SENT-001
backlog sizing only.

---

## 12. Closeout Record

| Field | Value |
|---|---|
| Closeout date | 2026-05-16 |
| Owner | Claude2 |
| Reviewer | Codex |
| Review file | `support/reviews/SENT-001-SIDECAR-BFF-HANDOFF-review-codex.md` |
| Final artifact commit | `1f31fd5e` (v2 — response-shape/provenance fix) |
| Review outcome | Approved (no blocking findings) |
| Verification | 16/16 tests passed (`test_sent001_sentinel_findings_contract.py`) |
| Canonical changes | None — support artifact only |
