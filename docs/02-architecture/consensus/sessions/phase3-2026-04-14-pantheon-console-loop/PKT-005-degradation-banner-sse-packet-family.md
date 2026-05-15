# PKT-005 Degradation Banner and SSE Packet Family

## Overview

PKT-005 packetizes two cross-cutting Operator Console substrates from the APP-002 sidecar family and the APP-002-W5-SSE-LIVE sidecar:

1. **Global Degradation Banner** — a non-dismissable banner displayed on all Operator Console screens when any `meta.surfaces` entry is degraded or unavailable.
2. **SSE Reconciliation Substrate** — the live update layer built on the three BFF-backed SSE streams (runtime events, incident events, kill-switch updates) that keeps all Operator Console screens in sync without full-page refreshes.

Both substrates are cross-cutting: they are not standalone pages but shared UI primitives that every Operator Console screen inherits. This packet upgrades the APP-002 frontend state matrix specifications into explicit screen-spec and coordination handoff language.

---

## Surface Inventory

### Operator Console — Global Degradation Banner

| Attribute | Value |
|---|---|
| Workbench | Operator Console (cross-cutting) |
| Surface | Global Degradation Banner |
| Surface ID | `surface-operator-global-degradation-banner` |
| Feature ID | `PKT-005-degradation-banner` |
| Packet status | **ready** |
| BFF backing | Derived from `meta.staleness` and `meta.surfaces.*` present in every BFF composed view response. No dedicated endpoint required. |
| Lovable readiness | Ready — APP-002 Frontend State Matrix §4 defines the full decision tree, copy variants, and button-gating semantics |
| Surface spec | `docs/screens/PKT-005-degradation-banner.md` |
| BFF contract | `docs/bff/PKT-005-degradation-banner.md` |
| Example payload | `docs/examples/PKT-005-degradation-banner.json` |
| Contract-ready | `.coordination/responses/PKT-005-degradation-banner-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-005-degradation-banner-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-005-degradation-banner-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-005-degradation-banner-ui-done.example.yaml` |

**Implementation note:** The degradation banner is not backed by a dedicated read route. It reads the `meta.staleness` and `meta.surfaces` fields that every BFF read already returns. Lovable must not add a separate health-check fetch to render this banner.

- For **composed-view screens** (PKT-001, PKT-003, PKT-004): all signal comes from the single composed view response.
- For **split-read screens** (PKT-002 Incident Home): the UI layer merges `meta.surfaces` from all independent BFF responses into a single map before passing to the banner component. See the BFF contract split-read aggregation rule.

**Per-surface `status` enum:** `ok | degraded | unavailable` only. The `stale` and `partial` values are not valid per-surface status values and must not appear in BFF responses.

**Banner states:**

| System condition | Banner variant | Copy |
|---|---|---|
| All surfaces `ok` and `meta.staleness` null | None | (no banner shown) |
| Any surface `degraded`, `meta.staleness` null or `served_from = "replica"` | Warning — degraded | "SYSTEM STATUS: SOME SERVICES DEGRADED — Real-time data is delayed. [Refresh now]" |
| `meta.staleness.served_from ∈ ["cache", "reconstructed"]`, no surface `unavailable` | Warning — stale | "SYSTEM STATUS: LIMITED MONITORING — Data last verified N minutes ago. [Use admin CLI] [Refresh]" |
| Any surface `unavailable`, others mixed | Partial | "SYSTEM STATUS: PARTIAL DATA — [surface A]: OK | [surface B]: DELAYED | [surface C]: UNAVAILABLE. [View details]" |
| BFF unreachable / all surfaces `unavailable` | Critical | "SYSTEM STATUS: CONTROL PLANE UI DOWN — BFF is offline. Use admin CLI or internal API. [View secondary control path guide]" |

---

### Operator Console — SSE Reconciliation Substrate

| Attribute | Value |
|---|---|
| Workbench | Operator Console (cross-cutting) |
| Surface | SSE Reconciliation Substrate |
| Surface ID | `surface-operator-sse-reconciliation` |
| Feature ID | `PKT-005-sse-substrate` |
| Packet status | **ready** |
| BFF backing | Three live SSE endpoints: `GET /api/v1/runtime/{runtime_id}/events/stream`, `GET /api/v1/incidents/stream`, `GET /api/v1/kill-switch/updates` |
| Lovable readiness | Ready — APP-002-W5-SSE-LIVE sidecar defines wire format, replay semantics, reconnect manager, and reconciler idempotency |
| Surface spec | `docs/screens/PKT-005-sse-substrate.md` |
| BFF contract | `docs/bff/PKT-005-sse-substrate.md` |
| Example payload | `docs/examples/PKT-005-sse-substrate.json` |
| Contract-ready | `.coordination/responses/PKT-005-sse-substrate-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-005-sse-substrate-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-005-sse-substrate-ui-done.example.yaml` |

**SSE streams and event types:**

| Stream | Endpoint | Event types |
|---|---|---|
| Runtime events | `GET /api/v1/runtime/{runtime_id}/events/stream` | `runtime_state_changed` |
| Incident events | `GET /api/v1/incidents/stream` | `incident_created`, `incident_updated` |
| Kill-switch events | `GET /api/v1/kill-switch/updates` | `kill_switch_activated`, `kill_switch_deactivated` |

**Replay and reconnect:**
- All streams support `?last_event_id=` for replay on reconnect.
- In-memory buffer per stream holds up to 500 events (deque). If `last_event_id` is not in the buffer, the full buffer is replayed.
- Heartbeat comment (`: heartbeat`) is emitted every 30 seconds. Client must not treat a heartbeat as a data event.
- Reconnect manager must use exponential backoff: 1 s → 30 s with jitter.

**Known caveats (non-blocking):**
- Runtime stream path includes `{runtime_id}` but the BFF generator does not filter server-side by `runtime_id`. Clients must filter by `data.runtime_id` when multiple runtimes share a connection.
- Event buffers are in-memory. A BFF process restart drops replay history. Clients must handle a reconnect that returns only partial buffer.

---

## Cross-Cutting Inheritance Rules

Every Operator Console screen packet that inherits these substrates must comply with the following:

1. **Degradation banner inheritance:** Any screen that receives BFF `meta` fields must pass `meta.staleness` and `meta.surfaces` into the shared banner component. For split-read screens, merge all per-response `meta.surfaces` maps before evaluating (see BFF contract). The banner decision is the backend's authority — the screen must not derive degradation state locally, and must not re-derive banner state from SSE event payloads. SSE events do not carry `meta` snapshots; the banner reflects the most recently received `meta` from a full BFF read.

2. **SSE subscription lifecycle:** Screens that display live runtime, incident, or kill-switch state must subscribe to the relevant SSE stream on mount and unsubscribe on unmount. Reconnect must use `SSEReconnectManager` (or an equivalent that implements the same backoff and `last_event_id` semantics).

3. **"Never show none" rule:** A screen must never render an empty-success state (e.g., "no incidents detected") when the data source is unavailable or degraded. Empty states and degraded states are visually distinct.

4. **SSE as incremental update, not initial fetch:** SSE events must be applied on top of the initial composed view response, not used as a substitute for the initial read. On reconnect, apply all replayed events through the reconciler; idempotency is guaranteed.

---

## Relationship to Other Packets

| Downstream packet | Dependency type |
|---|---|
| `PKT-001` (Deployment Review Console) | Inherits degradation banner; subscribes to runtime SSE stream |
| `PKT-002` (Incident Response Console) | Inherits degradation banner; subscribes to incident and kill-switch SSE streams |
| `PKT-003` (Post-Incident and Evolution) | Inherits degradation banner; SSE subscription optional for read-only surfaces |
| `PKT-004` (Persona Management) | Inherits degradation banner; SSE subscription optional |
| `WB-001` (Operator Console backlog) | Depends on PKT-005 to lock the shared banner and SSE substrate spec |
| `WB-008` (Evolution Workbench backlog) | Depends on PKT-005 for SSE substrate spec shared with evolution screens |

---

## Wave Framing

| Wave | PKT-005 scope |
|---|---|
| Wave 1 | Both substrates packetized and handed to Lovable as shared primitives |
| Wave 2 and beyond | Substrate spec is inherited by downstream workbench screens without reopening this packet |
