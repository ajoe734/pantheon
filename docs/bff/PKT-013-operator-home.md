# PKT-013 Operator Home Dashboard BFF Contract

## Purpose

Provide one truthful operator-home aggregation route so the UI does not invent card hierarchy, escalation ordering, or cross-surface freshness rules in the browser.

## Primary Read Route

- `GET /api/v1/operator/home`

Required response fields:

- `overall_status` (`ok` | `degraded` | `unavailable`)
- `headline`
- `message`
- `safe_mode_state`
  - `status`
  - `kill_switch_status`
  - `active`
  - `last_confirmed_at`
  - `last_triggered_at`
  - `secondary_path_available`
- `cards[]`
  - `card_id` (`alerts` | `incidents` | `governance` | `runtime` | `health`)
  - `label`
  - `status`
  - `summary`
  - `details`
  - `target_refs[]`
- `escalation_shortcuts[]`
  - `shortcut_id`
  - `label`
  - `reason`
  - `href`
  - `priority`
- `meta.snapshot_at`
- `meta.surfaces.operator_home`
- `meta.surfaces.alerts`
- `meta.surfaces.health_status`
- `meta.surfaces.incident`
- `meta.surfaces.governance`
- `meta.surfaces.runtime`
- `meta.surfaces.telemetry`
- `meta.surfaces.kill_switch`

## Degraded-State Rules

- When `meta.surfaces.operator_home = unavailable`, the UI must render the explicit unavailable state and preserve the backend-owned cards and copy. It must not collapse into a calm empty dashboard.
- When `alerts` or `health_status` is degraded, the home screen remains readable but the shared degradation banner must render.
- `cards[]` are returned in backend-owned order. The UI must not reorder them as a substitute for the published hierarchy.
- `escalation_shortcuts[]` are backend-owned. The UI must not invent new priority ordering or suppress shortcuts based on client-only heuristics.

## Design Rules

- The Operator Home dashboard reads from `GET /api/v1/operator/home` only.
- The dashboard summarizes already-published `OC-02`, `OC-03`, and `OC-04` truth. It must not become a client-side mega-join of alerts, health, runtime, incidents, or governance queues.
- `safe_mode_state` remains backend-owned and must be rendered exactly as supplied.
- Each card links only through backend-supplied `target_refs[]`.
- `target_refs[]` and `escalation_shortcuts[].href` publish browser-ready owner-screen destinations; the UI must render them verbatim.
- This packet is read-only and introduces no new write authority.
- If any required field or `meta.surfaces.*` entry is missing, the frontend must emit a `bff-gap` handoff instead of inventing summary logic locally.

## Example Payload

- `docs/examples/PKT-013-operator-home.json`
