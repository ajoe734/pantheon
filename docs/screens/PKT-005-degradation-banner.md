# PKT-005 Global Degradation Banner

## Classification

- Workbench: Operator Console (cross-cutting)
- Surface ID: `surface-operator-global-degradation-banner`
- Feature ID: `PKT-005-degradation-banner`
- Packet status: ready

## User Goal

Give operators an immediate, non-dismissable signal when Pantheon's control plane is serving degraded, stale, partial, or unavailable data — before they take any action that depends on that data being current. The banner must not require a separate fetch; it reads the meta fields already present in every BFF composed view response.

## Banner States and Copy

The banner resolves to one of five visual variants based on the combined `meta.surfaces.*` status from the screen's BFF read(s). For screens backed by a single composed view, this is the composed view `meta`. For split-read screens (currently PKT-002 Incident Home), this is the merged surface map assembled from all independent BFF responses — see the BFF contract split-read aggregation rule.

### No banner (fresh)

Condition: all `meta.surfaces` entries are `ok` and `meta.staleness` is null.

Display: no banner. Normal operation.

### Warning — degraded

Condition: one or more `meta.surfaces` entries have `status = "degraded"` and no entry is `unavailable`.

Display:
```
⚠️  SYSTEM STATUS: SOME SERVICES DEGRADED
Real-time data is delayed. Reviews are reliable but not realtime.
[Refresh now]
```

### Warning — stale

Condition: `meta.staleness` is non-null AND `meta.staleness.served_from ∈ ["cache", "reconstructed"]`, AND at least one surface has `status = "degraded"`, AND no surface is `"unavailable"`. The STALE variant is distinguished from DEGRADED by the `meta.staleness.served_from` field. A response where all surfaces are `ok` but `served_from = "cache"` falls through to `none` because there is no degradation signal to show.

Display:
```
⚠️  SYSTEM STATUS: LIMITED MONITORING
Data last verified N minutes ago. Do not rely on this for critical decisions.
[Use admin CLI]  [Refresh]
```

The value of N is derived from `meta.staleness.last_known_at` relative to the current time. If `last_known_at` is absent, display "some time ago".

### Partial — mixed surface health

Condition: surfaces report a mix of `ok`, `degraded`, and `unavailable`.

Display:
```
⚠️  SYSTEM STATUS: PARTIAL DATA
[surface A]: OK  |  [surface B]: DELAYED (N min)  |  [surface C]: UNAVAILABLE
[View details]
```

Surface names are derived from the `meta.surfaces` key names, humanised (e.g., `runtime_binding` → "Runtime Binding", `kill_switch` → "Kill Switch").

### Critical — BFF unreachable

Condition: the BFF request itself failed (network error, 5xx, or all `meta.surfaces` entries are `unavailable`).

Display:
```
❌  SYSTEM STATUS: CONTROL PLANE UI DOWN
The BFF is offline. You can still manage operations via:
  • Admin CLI (SSH): pantheon-admin ...
  • Internal API: control-plane-internal/api/internal/v1/...
[View secondary control path guide]
```

## Banner Behaviour

- Non-dismissable. The banner disappears automatically when the underlying data returns to `fresh`.
- Position: top of every Operator Console screen, below the global navigation rail.
- Width: full-width stripe.
- The banner does not duplicate per-panel staleness indicators. Per-panel indicators remain on individual cards. The banner summarises the overall screen health.
- Banner state is updated when the screen receives a fresh BFF `meta` snapshot — from initial load, polling, or an explicit full refetch triggered after a significant SSE event. SSE event payloads do not carry `meta` snapshots and must not be used to update banner state directly. It is never derived locally from timestamps.

## Decision Tree (Implementation Reference)

Per-surface `status` values are `ok | degraded | unavailable` only. The STALE variant is derived from `meta.staleness.served_from`, not from a per-surface `status` value.

```
if BFF request failed or all meta.surfaces[*].status == "unavailable":
    show_banner("CRITICAL")
elif any meta.surfaces[*].status == "unavailable":
    show_banner("PARTIAL", surfaces=meta.surfaces)
elif meta.staleness is not null and meta.staleness.served_from in ["cache", "reconstructed"] and any meta.surfaces[*].status == "degraded":
    show_banner("STALE", staleness=meta.staleness)
elif any meta.surfaces[*].status == "degraded":
    show_banner("DEGRADED")
else:
    show_nothing()
```

For split-read screens (PKT-002 Incident Home): use the merged surface map from all independent BFF responses as input to this tree. Treat any in-flight surface key as `"unavailable"` until its response arrives.

## Interaction Rules

- Banner state comes exclusively from BFF response `meta` fields. For composed-view screens this is the single composed view response. For split-read screens this is the merged surface map from all independent BFF responses. Do not add a separate `GET /api/v1/system/health` fetch.
- When a user clicks "Refresh now", re-fetch the screen's primary composed view. Do not reinitialise the full page.
- "Use admin CLI" opens the secondary control path runbook in the docs site. The link target is static; it does not require a BFF call.
- "View secondary control path guide" links to `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` rendered in the docs site.

## Acceptance

- Banner is not visible when all `meta.surfaces` entries are `ok`.
- DEGRADED variant appears when any surface is degraded.
- STALE variant appears when `meta.staleness.served_from ∈ ["cache", "reconstructed"]` and at least one surface is `degraded` and no surface is `unavailable`; shows humanised age from `meta.staleness.last_known_at`.
- PARTIAL variant lists each degraded or unavailable surface by humanised name.
- CRITICAL variant appears when the BFF request itself fails or all surfaces are `unavailable`.
- Banner disappears automatically when the screen refreshes and all surfaces return to `ok`.
- No separate health-check fetch is added to the BFF client.
- Banner renders on all Operator Console screens: Deployment Review (`PKT-001`), Incident Home and Incident Response (`PKT-002`), and Post-Incident Review (`PKT-003`).
- On PKT-002 Incident Home (split-read screen), the banner evaluates the merged surface map from `GET /api/v1/incidents` and `GET /api/v1/kill-switch/status`; either response returning a degraded surface triggers the banner.
