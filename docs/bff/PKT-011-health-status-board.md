# PKT-011 Operator Health Status Board BFF Contract

## Purpose

Provide a single operator-owned health board payload so the UI does not compute overall control-plane health, safe-mode state, or secondary control path guidance from arbitrary existing responses.

## Primary Read Route

- `GET /api/v1/operator/health-status`

Required response fields:

- `overall_status` (`ok` | `degraded` | `unavailable`)
- `headline`
- `message`
- `group_counts`
  - `ok`
  - `degraded`
  - `unavailable`
- `safe_mode_state`
  - `status` (nullable)
  - `kill_switch_status` (nullable)
  - `active` (nullable boolean)
  - `last_confirmed_at` (nullable RFC3339)
  - `last_triggered_at` (nullable RFC3339)
  - `secondary_path_available` (nullable boolean)
- `secondary_control_path`
  - `mode` (`hidden` | `advisory` | `recommended`)
  - `reason` (nullable string)
  - `targets[]`
    - `operation`
    - `channel`
    - `command`
    - `api_path`
    - `required_role`
    - `requires_mfa`
- `groups[]`
  - `group_id` (`runtime` | `telemetry` | `incident` | `governance` | `kill_switch`)
  - `label`
  - `status` (`ok` | `degraded` | `unavailable`)
  - `summary`
  - `details` (group-specific object)
  - `surface_refs[]`
    - `surface_key`
    - `status`
    - `source`
    - `message` (optional)
  - `target_refs[]`
    - `label`
    - `href`
    - `min_role` (optional)
- `meta.snapshot_at`
- `meta.surfaces.health_status`
- `meta.surfaces.runtime`
- `meta.surfaces.telemetry`
- `meta.surfaces.incident`
- `meta.surfaces.governance`
- `meta.surfaces.kill_switch`

## Surface-Group Taxonomy

The UI must use this backend-owned group taxonomy exactly as published:

| `group_id` | Meaning |
|---|---|
| `runtime` | runtime roster and runtime identity health |
| `telemetry` | telemetry coverage and freshness |
| `incident` | incident list and active incident state |
| `governance` | governance review and approval queue health |
| `kill_switch` | kill-switch and safe-mode state |

## Degraded-State Rules

- `overall_status = ok` means all five health groups are healthy.
- `overall_status = degraded` means one or more health groups are degraded or unavailable, but the board still has enough verified data to render.
- `overall_status = unavailable` means all primary health groups are unavailable. The UI must render the explicit unavailable state and show the secondary control path.
- The UI must not derive health groups from raw `meta.surfaces` on unrelated screens as a substitute for this route.
- If any required `groups[]` field or `meta.surfaces.*` entry is missing, the frontend must emit a `bff-gap` handoff.

## Secondary Control Path Contract

- `secondary_control_path.mode = hidden`: do not show the fallback card.
- `secondary_control_path.mode = advisory`: show the fallback card because some health groups are degraded and operators may need direct verification.
- `secondary_control_path.mode = recommended`: show the fallback card prominently because one or more critical health groups are unavailable or safe mode is active.
- The targets are backend-shaped. The UI must not invent alternate fallback commands or links.

## Design Rules

- This board is the only Operator Console health summary owner for `OC-03`.
- The UI must not build a page-level health board by calling `PKT-010`, `IN-01`, `PKT-001`, `PKT-006`, and `IN-05` separately and merging them client-side.
- `safe_mode_state` is backend-shaped and may be unavailable independently from other groups.
- `target_refs` link to existing authoritative owner surfaces only; they do not introduce new write authority.
- Inherits degradation semantics from `PKT-005 Degradation Banner`.

## Example Payload

- `docs/examples/PKT-011-health-status-board.json`
