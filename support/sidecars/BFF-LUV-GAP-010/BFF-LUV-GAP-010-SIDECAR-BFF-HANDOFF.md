# BFF-LUV-GAP-010 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-GAP-010-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-GAP-010
Helper kind: bff_handoff_packet
Owner: Codex
Reviewer: Codex2
Prepared: 2026-05-09T13:49:58Z

## Scope

This is a support-only sidecar for the BFF-LUV-GAP-010 parent implementation. It does not define canonical architecture, change route truth, or edit runtime behavior. The parent task already delivered SSE compatibility aliases for the current `execute-plans` frontend; this packet packages the route map, frontend consumption notes, remaining realtime/query gaps, and parent-owner absorption guidance.

## Evidence Snapshot

Commands inspected from `/home/lupin/code/pantheon`:

```bash
sed -n '1,260p' docs/bff/execution-tasks/2026-05-08-execute-plans-gap/BFF-LUV-GAP-010-sse-compatibility.md
sed -n '18140,19200p' services/control-plane/bff/main.py
sed -n '1,360p' services/control-plane/bff/test_pkt005_sse_substrate_contract.py
jq '.entries[] | select(.task_id=="BFF-LUV-GAP-010")' services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json
sed -n '1,220p' ai-task-archive/tasks/BFF-LUV-GAP-010.json
```

Findings:

- Parent BFF-LUV-GAP-010 is archived as `done`; its closeout commit is `38e00dc07a0578250078b4f29c33f0ca3187129b`.
- `services/control-plane/bff/main.py` exposes `/bff/events/stream` and the ten `/bff/sse/*` compatibility routes under the BFF-LUV-GAP-010 alias section.
- The aliases reuse the existing SSE substrate and do not create a second event bus.
- Registry rows for all eleven compatibility routes are `implemented_by_alias` in `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`.
- Focused parent verification recorded `17 passed` for `test_pkt005_sse_substrate_contract.py` plus `test_execute_plans_contract_registry.py`, and a replay-miss probe confirmed all eleven compatibility routes return `409 SSE_REPLAY_UNAVAILABLE` instead of `404` when history is unavailable.

## Delivered Compatibility Route Map

| Compatibility route | Existing substrate | BFF channel | Frontend consumption note |
|---|---|---|---|
| `GET /bff/events/stream` | `GET /api/v1/stream/{channel}` | Query `channel`, default `system` | Use `?channel=<catalog-channel>` when the caller needs a specific feed; invalid channels return the standard BFF validation error. |
| `GET /bff/sse/notifications` | `GET /api/v1/stream/{channel}` | `inbox` | General notification feed. |
| `GET /bff/sse/command-center/kpi` | `GET /api/v1/stream/{channel}` | `ranking` | Command-center KPI updates should read ranking-channel event payloads. |
| `GET /bff/sse/command-center/events` | `GET /api/v1/stream/{channel}` | `loop` | Command-center event feed maps to loop/system progression events. |
| `GET /bff/sse/jobs/{jobId}/progress` | `GET /api/v1/stream/{channel}` | `tool` | The route parameter is compatibility naming only; clients should filter payloads by `jobId` until a job-scoped stream projection exists. |
| `GET /bff/sse/alerts` | `GET /api/v1/stream/{channel}` | `sentinel` | Alert rail should consume sentinel-channel payloads. |
| `GET /bff/sse/incidents/{incidentId}/timeline` | `GET /api/v1/stream/{channel}` | `journal` | The route parameter is compatibility naming only; clients should filter payloads by `incidentId` until an incident-scoped timeline stream exists. |
| `GET /bff/sse/deployment/events` | `GET /api/v1/stream/{channel}` | `artifact` | Deployment event stream maps to artifact/deployment payloads. |
| `GET /bff/sse/review/updates` | `GET /api/v1/approvals/stream` | `approval` | Approval/review updates include resync headers for `/bff/approvals` and `/bff/v5/interventions`. |
| `GET /bff/sse/agora/signals` | `GET /api/v1/stream/{channel}` | `signal` | Agora signal realtime rail maps to signal-channel payloads. |
| `GET /bff/sse/agora/sessions/{sessionId}` | `GET /api/v1/agora/ask/stream` | `ask` | The route parameter is compatibility naming only; clients should filter payloads by `sessionId` and use the ask-session resync route after replay misses. |

## SSE Envelope And Reconnect Contract

The compatibility routes inherit the existing Pack D SSE behavior:

- Successful streams use `text/event-stream`.
- Response headers include `X-SSE-Channel`, `X-SSE-Replay-Supported: true`, `X-SSE-Replay-Window-Events: 500`, `X-SSE-Buffer-Size: 500`, and `X-SSE-Replay-Store: in-memory`.
- Approval and ask streams also expose `X-SSE-Resync-Routes`; generic channel aliases only expose this header when the underlying channel has explicit resync routes.
- Event payload data follows the existing envelope shape with `id`, `type`, `timestamp`, and `data`.
- Reconnect uses the `last_event_id` query parameter.
- If replay history is missing, aliases return the standard `409` BFF error envelope with `error.code == "SSE_REPLAY_UNAVAILABLE"` and metadata for channel, replay window, replay store, and resync routes.
- Auth and role checks are inherited from the existing substrate; without a valid read-authorized token, frontend callers should expect auth errors rather than an unauthenticated stream.

## Operator And Frontend Notes

For `execute-plans`, the compatibility aliases are sufficient to remove route-level `404` failures for the named SSE paths, but they are channel aliases rather than entity-scoped projections.

Frontend behavior should therefore treat SSE as a hint layer over canonical query surfaces:

- Subscribe to the alias route that matches the screen.
- Use `last_event_id` for reconnects when available.
- On `SSE_REPLAY_UNAVAILABLE`, stop assuming continuity, resync via the relevant GET route, then reconnect without the missing cursor.
- For route names containing `jobId`, `incidentId`, or `sessionId`, filter event payloads client-side by the entity id until a future parent task adds server-side scoped stream projection.
- Do not treat empty streams or heartbeat-only streams as proof that the underlying canonical read model is current; query screens should still read their authoritative BFF GET surfaces.
- Because the replay store is in-memory, process restart, deployment restart, or buffer overflow can legitimately force resync.

## Query And Realtime Gaps

This sidecar found no additional route-registration gap for BFF-LUV-GAP-010 itself. Remaining issues are semantic/realtime depth gaps for the parent owner or later semantic slices to decide:

| Gap | Current behavior | Parent-owner decision |
|---|---|---|
| Entity-scoped realtime routes | `jobId`, `incidentId`, and `sessionId` path params are accepted but not used to isolate the server stream. | Decide whether frontend-side filtering is enough for Lovable/live use, or create a future task for scoped stream projections. |
| Generic resync metadata | Only `approval` and `ask` channels currently publish explicit `X-SSE-Resync-Routes`; other aliases inherit generic channel headers. | If operator screens require guided recovery, add channel-specific resync-route metadata in a later semantic task. |
| Durable replay | Replay is `in-memory` with a 500-event window. | If production-grade continuity is required, defer to the broader SSE durability/HA policy work instead of widening this sidecar. |
| Event taxonomy | Compatibility routes expose channels, but not every screen-specific event type is guaranteed by this packet. | Parent owner should pair each screen with its canonical query route and only claim realtime semantics after event producers are verified. |
| `/bff/events/stream` default | The alias defaults to `system` when `channel` is omitted. | Frontend should pass `channel` explicitly when it expects a non-system feed. |

## Parent Absorption Guidance

Recommended parent-owner actions:

1. Keep the BFF-LUV-GAP-010 implementation classified as alias compatibility, not a new SSE bus.
2. Use the route map above in any frontend handoff or Lovable cutover notes so UI owners know which channel each route receives.
3. Preserve the parent artifact's verification references:
   - `python3 -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py services/control-plane/bff/test_execute_plans_contract_registry.py -q`
   - `python3 -m json.tool services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json >/tmp/execute_routes.valid`
   - replay-miss probe proving all eleven compatibility routes return `409 SSE_REPLAY_UNAVAILABLE` instead of `404`
4. Do not broaden canonical architecture docs or registry truth from this sidecar; if deeper realtime semantics are needed, open a semantic completion task with focused acceptance criteria.
5. For live frontend readiness, pair this SSE packet with the later BFF-LUV-SEM tasks, because this packet only proves route compatibility and inherited SSE envelope behavior.

## Suggested Reviewer Checks

Reviewer should verify:

- The packet is support-only and only updates `support/sidecars/BFF-LUV-GAP-010/BFF-LUV-GAP-010-SIDECAR-BFF-HANDOFF.md`.
- The inaccurate old note about missing task brief access is gone.
- The route/channel table matches `services/control-plane/bff/main.py` and the BFF-LUV-GAP-010 registry rows.
- Frontend notes distinguish route compatibility from entity-scoped realtime semantics.
- Parent absorption guidance does not claim canonical truth changes or live readiness beyond the parent evidence.

This packet is ready for Codex2 review and parent-owner absorption decisions.
