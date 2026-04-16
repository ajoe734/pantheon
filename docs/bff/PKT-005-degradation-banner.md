# PKT-005 Global Degradation Banner BFF Contract

## Purpose

Define the `meta.staleness` and `meta.surfaces` fields that every BFF composed view must include so that the Global Degradation Banner can derive system health without a dedicated endpoint.

## No Dedicated Endpoint

The degradation banner does not require a new BFF route. Every existing composed view response already carries `meta.staleness` and `meta.surfaces`. This contract specifies the required shape and semantics for those fields across all Operator Console composed views.

---

## Required `meta` Fields (All Composed Views)

Every composed view consumed by an Operator Console screen must include:

```json
{
  "meta": {
    "snapshot_at": "<ISO 8601 timestamp>",
    "staleness": null | { ... },
    "surfaces": {
      "<surface_key>": { "status": "ok" | "degraded" | "unavailable" }
    }
  }
}
```

### `meta.snapshot_at`

- Type: ISO 8601 string
- When: always present
- Meaning: the wall-clock time at which the BFF assembled this response

### `meta.staleness`

- Type: null or object
- When: null if all surfaces are `ok`; an object when any surface is degraded, stale, or unavailable

Staleness object shape:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `served_from` | string | yes | `"primary"`, `"replica"`, `"cache"`, `"reconstructed"`, or `"none"` |
| `last_known_at` | ISO 8601 string | no | the timestamp of the last verifiable primary read; omit if never available |
| `max_age_minutes` | integer | no | maximum age of the cache or replica data, in minutes |

### `meta.surfaces`

- Type: object
- When: always present
- Each key is a logical sub-surface name for that composed view.
- Each value is an object with a required `status` field.

Surface status values and their UI meaning:

| Status | BFF meaning | Banner impact |
|---|---|---|
| `"ok"` | Primary service responded within SLA | No banner contribution |
| `"degraded"` | Replica-backed, cache-backed, or minor latency | Contributes to DEGRADED or STALE banner depending on `meta.staleness.served_from` |
| `"unavailable"` | No verifiable data for this sub-surface | Contributes to PARTIAL or CRITICAL banner |

> **Note:** `stale` and `partial` are not valid per-surface `status` values. The STALE banner variant is derived from `meta.staleness.served_from ∈ ["cache", "reconstructed"]` combined with at least one `degraded` surface. The PARTIAL banner variant is derived from a mix of `ok`, `degraded`, and `unavailable` surface statuses. These conditions are resolved client-side in the banner decision tree, not encoded directly in per-surface `status`.

---

## Split-Read Screen Aggregation

Some Operator Console screens issue multiple independent BFF reads rather than a single composed view. The banner contract must cover these screens.

**Currently affected screen:** `PKT-002` Incident Home (`GET /api/v1/incidents` + `GET /api/v1/kill-switch/status`)

### Rule

For split-read screens, the UI layer must merge the `meta.surfaces` maps from all independent BFF responses into a single map before passing it to the banner component. The merged map is then evaluated by the standard banner decision tree exactly as if it had come from a single composed view.

**Merge procedure:**

1. Collect `meta.surfaces` from each independent response as it arrives.
2. Maintain a screen-level surface map that accumulates surface keys from every response. Later responses do not overwrite earlier keys — each response owns only its own surface keys.
3. For `meta.staleness`: if any response returns a non-null `meta.staleness`, use the staleness object with the oldest `last_known_at` (or any non-null one if `last_known_at` is absent) as the screen-level `meta.staleness` for banner evaluation.
4. A surface key that has not yet received a response (still in-flight) must be treated as `"unavailable"` in the merged map. Do not defer banner render until all reads complete — show a partial/unavailable state for the in-flight surfaces.
5. Emit a `bff-gap` handoff if any expected surface key is absent from its response when the response arrives.

**PKT-002 Incident Home surface map:**

| Surface key | Source response |
|---|---|
| `incident_list` | `GET /api/v1/incidents` → `meta.surfaces.incident_list` |
| `kill_switch` | `GET /api/v1/kill-switch/status` → `meta.surfaces.kill_switch` |

The merged map `{ incident_list: ..., kill_switch: ... }` is the input to the banner decision tree for PKT-002 Incident Home. Per-surface status values follow the same `ok | degraded | unavailable` enum as all other screens.

---

## Per-Composed-View Surface Key Reference

### `GET /api/v1/operator/deployment-review/{plan_id}`

Required `meta.surfaces` keys:

| Key | Sub-surface it guards |
|---|---|
| `deployment_plan` | Deployment plan details |
| `capital_pool` | Capital pool state |
| `runtime_binding` | Planned bindings |
| `approval_decisions` | Approval decision history |

### `GET /api/v1/operator/incident-response/{incident_id}`

Required `meta.surfaces` keys:

| Key | Sub-surface it guards |
|---|---|
| `incident` | Incident case record |
| `affected_bindings` | Affected binding records |
| `kill_switch` | Kill-switch current state |
| `allowedActions` | Action authority flags |

### `GET /api/v1/operator/post-incident-review/{incident_id}`

Required `meta.surfaces` keys:

| Key | Sub-surface it guards |
|---|---|
| `postmortem` | Postmortem report |
| `evolution_decisions` | Linked evolution decisions |
| `lineage` | Artifact lineage edges |
| `telemetry_performance` | Telemetry performance window |

### `GET /api/v1/operator/persona-management/{persona_id}`

Required `meta.surfaces` keys:

| Key | Sub-surface it guards |
|---|---|
| `persona` | Persona summary and lifecycle |
| `bindings` | Active binding list |
| `capital_pool` | Capital pool metadata |
| `sessions` | Recent session history |

---

## UI Gating Rules

- If any `meta.surfaces` key is missing from the response entirely, the UI must treat that key as `"unavailable"` and emit a `bff-gap` handoff.
- A surface with `status = "unavailable"` must never be rendered as empty success. The panel guarded by that surface must display an explicit "data unavailable" state.
- `meta.staleness.last_known_at` is the authoritative source for the humanised age shown in the STALE banner variant. Do not compute age from `meta.snapshot_at` alone.

## Write Actions

None. This contract is read-only — the banner reads existing response fields and produces no commands.
