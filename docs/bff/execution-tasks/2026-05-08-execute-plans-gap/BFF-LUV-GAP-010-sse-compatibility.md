# BFF-LUV-GAP-010 - SSE Compatibility Routes

Priority: P0

Area: Realtime and event-stream compatibility

## Goal

Expose the `/bff/events/stream` and `/bff/sse/*` route names referenced by `execute-plans` while reusing Pantheon's existing SSE substrate.

## Missing Routes

Part 06:

- `GET /bff/events/stream`

Current `execute-plans/src/lib/v3/medium-low/B3-console.ts` references:

- `/bff/sse/notifications`
- `/bff/sse/command-center/kpi`
- `/bff/sse/command-center/events`
- `/bff/sse/jobs/{jobId}/progress`
- `/bff/sse/alerts`
- `/bff/sse/incidents/{incidentId}/timeline`
- `/bff/sse/deployment/events`
- `/bff/sse/review/updates`
- `/bff/sse/agora/signals`
- `/bff/sse/agora/sessions/{sessionId}`

Related already-present routes:

- `/api/v1/stream/{channel}`
- `/api/v1/approvals/stream`
- `/api/v1/incidents/stream`
- `/api/v1/runtime/{runtimeId}/events/stream`
- `/api/v1/kill-switch/updates`
- `/api/v1/agora/ask/stream`

## Implementation Notes

- Implement aliases/adapters instead of a second event bus.
- Keep Pack D SSE envelope and replay semantics.
- Approval and ask channels from the final contract must continue to resync through `/bff/approvals` and `/bff/v5/interventions`.

## Acceptance Criteria

- Every listed `/bff/sse/*` path returns an SSE response or a final `SSE_REPLAY_UNAVAILABLE`/auth error envelope, never 404.
- Existing SSE tests remain green.
- At least one focused test proves a `/bff/sse/*` alias maps to the same envelope shape as `/api/v1/stream/{channel}`.

## Delivered Behavior

- Added execute-plans SSE compatibility aliases in `services/control-plane/bff/main.py`.
- `/bff/events/stream` delegates to the existing generic SSE substrate and defaults to the `system` channel when no `channel` query parameter is supplied.
- `/bff/sse/notifications`, `/command-center/kpi`, `/command-center/events`, `/jobs/{jobId}/progress`, `/alerts`, `/incidents/{incidentId}/timeline`, `/deployment/events`, `/review/updates`, `/agora/signals`, and `/agora/sessions/{sessionId}` delegate to existing SSE channels instead of adding a second event bus.
- Replay-miss handling continues to use the final `SSE_REPLAY_UNAVAILABLE` error envelope and existing resync metadata.
- Updated the execute-plans BFF route registry snapshot so the BFF-LUV-GAP-010 rows are `implemented_by_alias` with focused proof references.

## Channel Mapping

| Compatibility route | Existing substrate |
|---|---|
| `GET /bff/events/stream` | `GET /api/v1/stream/{channel}`; default `system` |
| `GET /bff/sse/notifications` | `inbox` channel |
| `GET /bff/sse/command-center/kpi` | `ranking` channel |
| `GET /bff/sse/command-center/events` | `loop` channel |
| `GET /bff/sse/jobs/{jobId}/progress` | `tool` channel |
| `GET /bff/sse/alerts` | `sentinel` channel |
| `GET /bff/sse/incidents/{incidentId}/timeline` | `journal` channel |
| `GET /bff/sse/deployment/events` | `artifact` channel |
| `GET /bff/sse/review/updates` | `approval` channel |
| `GET /bff/sse/agora/signals` | `signal` channel |
| `GET /bff/sse/agora/sessions/{sessionId}` | `ask` channel |

## Verification

- `python3 -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q` -> `12 passed`
- `python3 -m json.tool services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json >/tmp/execute_routes.valid`
- `python3 -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py services/control-plane/bff/test_execute_plans_contract_registry.py -q` -> `17 passed`

## Closeout Verification

- `python3 -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py services/control-plane/bff/test_execute_plans_contract_registry.py -q` -> `17 passed in 11.01s`
- `python3 -m json.tool services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json >/tmp/execute_routes.valid` -> passed
- `PANTHEON_BFF_AUTH_STUB=true python3 -c '<TestClient replay-miss check for all 11 BFF-LUV-GAP-010 compatibility routes>'` -> all routes returned `409 SSE_REPLAY_UNAVAILABLE`; no `404`
