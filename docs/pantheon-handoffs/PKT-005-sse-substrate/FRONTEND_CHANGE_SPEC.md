# PKT-005 SSE Reconciliation Substrate — Frontend Change Spec

## Feature

- Feature ID: `PKT-005-sse-substrate`
- Screen ID: `surface-operator-sse-reconciliation`
- Workbench: Operator Console
- Packet status: ready

## Summary

Implement the shared SSE reconciliation substrate inside `front-ai-trading-system`. This packet does not add a standalone page. It adds a reusable real-time layer that existing Operator Console screens use after their normal Pantheon BFF reads complete. The substrate must keep runtime, incident, and kill-switch state fresh without inventing endpoints, without introducing shadow state, and without treating SSE as the initial source of truth.

## Files to Create or Modify

```text
src/lib/sseClient.ts                         — shared SSE client wrapper and connection state surface
src/lib/sseReconnectManager.ts               — exponential backoff, jitter, and last_event_id replay logic
src/lib/sseReconciler.ts                     — idempotent event validation, buffering, and per-screen apply helpers
src/pages/operator/DeploymentReviewConsole.tsx
src/pages/operator/IncidentDetail.tsx
src/components/operator/IncidentActionDrawer.tsx
src/pages/operator/PostIncidentReviewConsole.tsx
src/pages/operator/types.ts                  — shared SSE-derived event and footer-state types if needed
```

If the front repo already uses different filenames for these screens, keep the existing screen files and add the SSE substrate behind them. Do not create a parallel page tree just for this packet.

## API Integration

Use the existing BFF client and host-screen read flows from the packet families that already own the composed views:

- `PKT-001-deployment-review`
- `PKT-002-incident-detail`
- `PKT-002-incident-action-drawer`
- `PKT-003-post-incident-review`

This packet adds only the three live SSE feeds below. It does not replace the host screen reads.

### Runtime events stream

```text
GET /api/v1/runtime/{runtime_id}/events/stream
Query param: last_event_id (optional)
```

Wire format:

```typescript
interface RuntimeStateChangedEvent {
  id: string;
  type: "runtime_state_changed";
  timestamp: string;
  data: {
    runtime_id: string;
    previous_state: "paper" | "canary" | "live" | "paused" | "halted";
    current_state: "paper" | "canary" | "live" | "paused" | "halted";
    surface_id: string;
  };
}
```

Important rule: server-side filtering by `{runtime_id}` is not active yet. The client must filter by `event.data.runtime_id` before applying a runtime event.

### Incident events stream

```text
GET /api/v1/incidents/stream
Query param: last_event_id (optional)
```

Wire format:

```typescript
type IncidentEvent =
  | {
      id: string;
      type: "incident_created";
      timestamp: string;
      data: {
        incident_id: string;
        title: string;
        severity: "critical" | "high" | "medium" | "low";
        artifact_id: string;
      };
    }
  | {
      id: string;
      type: "incident_updated";
      timestamp: string;
      data: {
        incident_id: string;
        status: "active" | "investigating" | "mitigated" | "resolved";
        updated_at: string;
      };
    };
```

### Kill-switch updates stream

```text
GET /api/v1/kill-switch/updates
Query param: last_event_id (optional)
```

Wire format:

```typescript
type KillSwitchEvent =
  | {
      id: string;
      type: "kill_switch_activated";
      timestamp: string;
      data: {
        scope: string;
        activated_by: string;
        activated_at: string;
      };
    }
  | {
      id: string;
      type: "kill_switch_deactivated";
      timestamp: string;
      data: {
        scope: string;
        deactivated_by: string;
        deactivated_at: string;
      };
    };
```

## Shared Substrate Behavior

### `sseClient.ts`

- Own all `EventSource` creation here. No component file may create its own `EventSource`.
- Expose connection state as `connected`, `reconnecting`, or `disconnected`.
- Parse each `message.data` payload as JSON and ignore heartbeat comments or empty events.
- Track the most recently applied `event.id` per stream and pass it back as `?last_event_id=...` on reconnect.

### `sseReconnectManager.ts`

- On disconnect, close the current `EventSource`.
- Reconnect with exponential backoff starting at 1 second, doubling until 30 seconds max, with jitter.
- Reset the backoff timer after a successful `open`.
- Treat replay as normal event delivery; do not fork a separate replay code path in the UI.

### `sseReconciler.ts`

- Validate required top-level fields: `id`, `type`, `timestamp`, `data`.
- Validate required `data` fields for each event type before applying.
- Keep an applied-event set keyed by `event.id`; skip duplicates.
- If an event arrives before the host screen's initial composed view has finished loading, buffer it and flush the buffer once the screen state is hydrated.
- Runtime events must be discarded when `event.data.runtime_id` does not match the active runtime context for the host screen.
- SSE events are incremental patches only. They must never replace the host screen's composed view wholesale.

## Host Screen Wiring

### Deployment Review Console

- Continue to fetch the composed view from `PKT-001`.
- After the relevant runtime context is known, subscribe to the runtime stream.
- Apply `runtime_state_changed` incrementally to the visible deployment/runtime state.
- Show shared SSE connection state in the screen footer.

### Incident Response surfaces

- Continue to fetch the composed incident view from `PKT-002`.
- After the incident detail view resolves, subscribe to:
  - runtime stream
  - incident stream
  - kill-switch stream
- `kill_switch_activated` must disable runtime action buttons immediately.
- This is CTA gating only. Do not update or re-derive the degradation banner from SSE payloads.
- The action drawer must consume the shared substrate or host-screen state. It must not open its own raw SSE connection.

### Post-Incident Review Console

- Continue to fetch the composed view from `PKT-003`.
- After the resolved-incident detail view is hydrated, subscribe to the incident stream.
- Apply `incident_updated` events idempotently to the visible review state.
- Show shared SSE connection state in the screen footer.

## Constraints

- Use the existing BFF client only. Do not add raw `fetch` or `axios` in component files.
- Do not add raw `EventSource` calls in component files.
- Do not import or use demo providers or mock state.
- Do not use SSE as the initial data source on mount. Always fetch the composed view first, then reconcile SSE on top.
- Do not create shadow degradation-banner state from SSE payloads. Banner state remains owned by the latest full BFF `meta` snapshot.
- If no SSE event is received for 60 seconds on a screen where events are expected, show a footer note that real-time updates may be delayed. This is not a degradation-banner input.
- If a required event field is missing in the live stream or in the example fixture validation pass, stop and emit a `bff-gap` handoff instead of guessing.

## Completion Handoff

When the UI implementation is ready, write `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml` using `.coordination/requests/PKT-005-sse-substrate-ui-done.example.yaml` as the template.

## References

- Screen spec: `docs/screens/PKT-005-sse-substrate.md`
- BFF contract: `docs/bff/PKT-005-sse-substrate.md`
- Example payload: `docs/examples/PKT-005-sse-substrate.json`
- Contract-ready: `.coordination/responses/PKT-005-sse-substrate-contract-ready.yaml`
- Lovable UI task: `.coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml`
- BFF-gap template: `.coordination/requests/PKT-005-sse-substrate-bff-gap.example.yaml`
- UI-done template: `.coordination/requests/PKT-005-sse-substrate-ui-done.example.yaml`
