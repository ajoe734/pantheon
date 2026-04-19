# RW-05 Artifact Compare — BFF Contract

**Published by:** Claude (RW-05-ARTIFACT-COMPARE-001)  
**Reviewed by:** Codex  
**Status:** contract-published — pending BFF implementation  
**Depends on:** RW-04-EXPERIMENT-001 (`docs/bff/RW-04-experiment-launch.md`)

---

## Overview

This document defines the canonical BFF contract for the Research Workbench Artifact Compare module (RW-05). It covers:

- Artifact registry list and detail routes
- Artifact versioning and identity semantics
- Backend-composed comparison diff route
- Degradation and non-goal rules

All artifact data and comparison output must come from the Pantheon BFF. The frontend must not construct artifact lists from experiment run data, derive version ancestry client-side, or compute diffs by comparing raw JSON payloads.

---

## 1. Artifact Identity and Versioning Semantics

### 1.1 Identity

- **Primary key:** `artifact_id` — globally unique, stable, immutable once assigned
- **Version key:** `version` — monotonically incrementing integer within a given lineage chain; starts at `1`
- **Lineage key:** `lineage_id` — groups all versions of the same logical artifact across multiple experiment runs
  - Two artifacts with the same `lineage_id` represent successive refinements of the same research output
  - Two artifacts with different `lineage_ids` are independent outputs even if produced by the same ticket or strategy

### 1.2 Immutability Rules

- An artifact record is **immutable** once its `status` transitions to `sealed`
- Fields `artifact_id`, `version`, `lineage_id`, `produced_by_experiment_id`, `linked_ticket_id`, and `created_at` are write-once and must not change after creation
- Only `meta.*` staleness fields may be updated after sealing (BFF-internal freshness tracking)

### 1.3 Version Ancestry

- `parent_artifact_id` holds the `artifact_id` of the immediately preceding version in the same lineage chain; `null` for the first version
- The BFF resolves the ancestry chain and surfaces it as `version_chain[]` in the detail read model
- Frontend must not reconstruct the version chain from raw experiment run data

### 1.4 Lifecycle States

| State | Meaning |
|---|---|
| `pending` | Artifact created by an experiment launch; data is still being written |
| `sealed` | Artifact data is complete and immutable; eligible for compare |
| `superseded` | A newer version in the same lineage exists; this version remains readable but is not the current tip |
| `failed` | Artifact creation failed; record is retained for audit; data must not be compared |

Only `sealed` and `superseded` artifacts may appear in compare selections. `pending` and `failed` artifacts must be surfaced with a non-selectable indicator.

---

## 2. Artifact Registry List Route

### `GET /api/v1/artifacts`

Returns a paginated list of artifacts visible to the requesting operator.

**Query parameters:**

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `experiment_id` | string | no | Filter to artifacts produced by a specific experiment run |
| `ticket_id` | string | no | Filter to artifacts linked to a specific research ticket |
| `lineage_id` | string | no | Filter to all versions of a single lineage chain |
| `status` | string | no | One of `pending`, `sealed`, `superseded`, `failed`; default returns all |
| `page_token` | string | no | Opaque cursor for next-page navigation |
| `page_size` | integer | no | Default 20; max 100 |

**Response shape:**

```json
{
  "artifacts": [
    {
      "artifact_id": "art_2024_abc123",
      "lineage_id": "lin_xyz987",
      "version": 3,
      "status": "sealed",
      "name": "MACD-momentum-v3",
      "artifact_type": "strategy_model",
      "produced_by_experiment_id": "exp_9876",
      "linked_ticket_id": "tkt_5432",
      "created_at": "2026-04-18T14:22:00Z",
      "metric_summary": {
        "sharpe_ratio": 1.42,
        "max_drawdown": -0.08,
        "annualized_return": 0.18
      },
      "is_current_version": true
    }
  ],
  "next_page_token": "eyJvZmZzZXQiOjIwfQ==",
  "total_count": 47,
  "meta": {
    "surfaces": {
      "artifact_list": "ok"
    },
    "snapshot_at": "2026-04-18T14:30:00Z"
  }
}
```

**`meta.surfaces.artifact_list` values:**

| Value | UI behavior |
|---|---|
| `ok` | Render normally |
| `degraded` | Show results with non-dismissable staleness banner |
| `unavailable` | Show unavailable banner; do not render artifact rows |

**Non-goals:**

- Frontend must not derive artifact lists by iterating over `artifact_ids` from `GET /api/v1/experiments`
- Frontend must not filter or sort the artifact list client-side

---

## 3. Artifact Detail Route

### `GET /api/v1/artifacts/{artifact_id}`

Returns the full read model for a single artifact including version chain, provenance, metrics, and lineage refs.

**Response shape:**

```json
{
  "artifact_id": "art_2024_abc123",
  "lineage_id": "lin_xyz987",
  "version": 3,
  "parent_artifact_id": "art_2024_abc122",
  "status": "sealed",
  "name": "MACD-momentum-v3",
  "artifact_type": "strategy_model",
  "description": "Third iteration of MACD-momentum strategy after parameter tuning",
  "produced_by_experiment_id": "exp_9876",
  "linked_ticket_id": "tkt_5432",
  "created_at": "2026-04-18T14:22:00Z",
  "sealed_at": "2026-04-18T14:25:10Z",
  "is_current_version": true,
  "version_chain": [
    {
      "artifact_id": "art_2024_abc121",
      "version": 1,
      "status": "superseded",
      "produced_by_experiment_id": "exp_9800",
      "created_at": "2026-04-10T09:00:00Z"
    },
    {
      "artifact_id": "art_2024_abc122",
      "version": 2,
      "status": "superseded",
      "produced_by_experiment_id": "exp_9840",
      "created_at": "2026-04-14T11:30:00Z"
    },
    {
      "artifact_id": "art_2024_abc123",
      "version": 3,
      "status": "sealed",
      "produced_by_experiment_id": "exp_9876",
      "created_at": "2026-04-18T14:22:00Z"
    }
  ],
  "metrics": {
    "sharpe_ratio": 1.42,
    "sortino_ratio": 1.87,
    "max_drawdown": -0.08,
    "annualized_return": 0.18,
    "win_rate": 0.54,
    "avg_trade_duration_days": 3.2,
    "total_trades": 412
  },
  "parameters": {
    "fast_period": 12,
    "slow_period": 26,
    "signal_period": 9,
    "position_sizing": "fixed_fractional",
    "risk_per_trade": 0.01
  },
  "provenance": {
    "linked_experiment": {
      "experiment_id": "exp_9876",
      "display_label": "MACD tuning run 3 — 2026-04-18"
    },
    "linked_ticket": {
      "ticket_id": "tkt_5432",
      "title": "Momentum strategy parameter optimization"
    },
    "lineage_refs": [
      {
        "ref_type": "inspired_by",
        "target_artifact_id": "art_2020_base01",
        "resolved_link": "/research/compare?artifact_ids=art_2024_abc123,art_2020_base01"
      }
    ]
  },
  "allowedActions": {
    "canCompare": true,
    "canViewDetail": true
  },
  "meta": {
    "surfaces": {
      "artifact_detail": "ok"
    },
    "snapshot_at": "2026-04-18T14:30:00Z"
  }
}
```

**`meta.surfaces.artifact_detail` values:** same set as `artifact_list` above.

**Non-goals:**

- Frontend must not reconstruct `version_chain` by issuing multiple `GET /api/v1/artifacts` calls filtered by `lineage_id`
- Frontend must not resolve `provenance.lineage_refs` from raw lineage storage refs
- Frontend must not derive `allowedActions.canCompare` from artifact `status` client-side

---

## 4. Compare Diff Route

### `GET /api/v1/artifacts/compare`

Returns a backend-composed structured diff between two or more selected artifacts. The BFF owns all comparison computation; the frontend renders the provided diff shape without constructing its own comparison logic.

**Query parameters:**

| Parameter | Type | Required | Notes |
|---|---|---|---|
| `artifact_ids` | string (comma-separated) | yes | Two to four `artifact_id` values to compare; order determines the left-to-right panel order |

**Response shape:**

```json
{
  "comparison_id": "cmp_20260418_001",
  "artifacts": [
    {
      "artifact_id": "art_2024_abc121",
      "version": 1,
      "name": "MACD-momentum-v1",
      "status": "superseded"
    },
    {
      "artifact_id": "art_2024_abc123",
      "version": 3,
      "name": "MACD-momentum-v3",
      "status": "sealed"
    }
  ],
  "field_pairs": [
    {
      "field_key": "metrics.sharpe_ratio",
      "display_label": "Sharpe Ratio",
      "group": "performance",
      "values": [
        { "artifact_id": "art_2024_abc121", "value": 0.98 },
        { "artifact_id": "art_2024_abc123", "value": 1.42 }
      ],
      "change_label": "improved",
      "delta_magnitude": 0.44,
      "delta_direction": "up",
      "delta_display": "+0.44 (+44.9%)"
    },
    {
      "field_key": "metrics.max_drawdown",
      "display_label": "Max Drawdown",
      "group": "risk",
      "values": [
        { "artifact_id": "art_2024_abc121", "value": -0.14 },
        { "artifact_id": "art_2024_abc123", "value": -0.08 }
      ],
      "change_label": "improved",
      "delta_magnitude": 0.06,
      "delta_direction": "up",
      "delta_display": "+0.06 (42.9% reduction)"
    },
    {
      "field_key": "parameters.fast_period",
      "display_label": "Fast Period",
      "group": "parameters",
      "values": [
        { "artifact_id": "art_2024_abc121", "value": 10 },
        { "artifact_id": "art_2024_abc123", "value": 12 }
      ],
      "change_label": "changed",
      "delta_magnitude": 2,
      "delta_direction": "up",
      "delta_display": "+2"
    }
  ],
  "change_summary": {
    "total_fields_compared": 14,
    "fields_changed": 5,
    "fields_unchanged": 9,
    "dominant_change_label": "improved"
  },
  "provenance_pairs": [
    {
      "artifact_id": "art_2024_abc121",
      "linked_experiment": {
        "experiment_id": "exp_9800",
        "display_label": "MACD baseline run — 2026-04-10"
      },
      "linked_ticket": {
        "ticket_id": "tkt_5432",
        "title": "Momentum strategy parameter optimization"
      }
    },
    {
      "artifact_id": "art_2024_abc123",
      "linked_experiment": {
        "experiment_id": "exp_9876",
        "display_label": "MACD tuning run 3 — 2026-04-18"
      },
      "linked_ticket": {
        "ticket_id": "tkt_5432",
        "title": "Momentum strategy parameter optimization"
      }
    }
  ],
  "meta": {
    "surfaces": {
      "artifact_compare": "ok"
    },
    "snapshot_at": "2026-04-18T14:30:00Z",
    "computed_at": "2026-04-18T14:30:05Z"
  }
}
```

**`change_label` vocabulary (BFF-defined, frontend must render as-is):**

| Value | Meaning |
|---|---|
| `improved` | Change is directionally positive by the system's metric orientation |
| `degraded` | Change is directionally negative |
| `changed` | Change is neutral or non-directional (e.g., parameter tuning with no clear direction) |
| `unchanged` | No difference between artifact versions for this field |

**`delta_direction` values:** `up`, `down`, `none`

**`group` vocabulary:** `performance`, `risk`, `parameters`, `metadata` — used by the frontend to organize field pairs into visual groups; group keys are BFF-defined and must not be hard-coded client-side.

**Error cases:**

| Scenario | Response |
|---|---|
| Fewer than 2 `artifact_ids` provided | `400 Bad Request` |
| More than 4 `artifact_ids` provided | `400 Bad Request` |
| Any `artifact_id` is `pending` or `failed` | `422 Unprocessable Entity` with a `non_comparable_artifacts[]` list explaining why |
| Artifacts from different `lineage_id`s are compared | Allowed — cross-lineage compare is a valid use case |

**`meta.surfaces.artifact_compare` values:**

| Value | UI behavior |
|---|---|
| `ok` | Render comparison normally |
| `degraded` | Show last-known comparison data with non-dismissable staleness banner |
| `unavailable` | Show unavailable state; do not render comparison panels |

**Non-goals:**

- Frontend must not compute field diffs from two raw artifact JSON payloads
- Frontend must not derive `change_label` or `delta_magnitude` from raw metric values
- Frontend must not determine `group` assignments from field key naming conventions

---

## 5. Degradation Rules

- When `meta.surfaces.artifact_list` is `degraded` or `unavailable`, the artifact selector must reflect that state with the canonical degradation banner
- When `meta.surfaces.artifact_compare` is `degraded`, the comparison panel shows the last-known diff with a non-dismissable staleness banner
- When `meta.surfaces.artifact_compare` is `unavailable`, no comparison panels are rendered
- `allowedActions.canCompare` must be `false` for any artifact whose `status` is `pending` or `failed`; BFF enforces this and the frontend must not override it

---

## 6. Non-Goals (Canonical List)

1. Frontend must not construct artifact lists by iterating over `artifact_ids` from experiment run records
2. Frontend must not reconstruct `version_chain` from multiple BFF calls
3. Frontend must not resolve `provenance.lineage_refs` from raw lineage storage refs or `ref_id` values
4. Frontend must not compute comparison diffs by comparing raw JSON payloads between two artifact records
5. Frontend must not derive `change_label`, `delta_magnitude`, or `group` assignments client-side
6. Frontend must not derive `allowedActions.canCompare` from artifact `status` client-side

---

## 7. Screen Handoff Prerequisites

Before a Lovable screen spec can be opened for RW-05, the following must be true:

- All three routes above are implemented with agreed field shapes
- `meta.surfaces.artifact_list`, `meta.surfaces.artifact_detail`, and `meta.surfaces.artifact_compare` are wired through to the canonical degradation banner
- `allowedActions.canCompare` is backend-shaped and documented
- An example payload JSON exists (see `docs/examples/RW-05-artifact-compare.json`)
- RW-04 Experiment Launch is Lovable-ready (so versioned `artifact_id` values are stable and resolvable)
