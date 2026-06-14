# Round 5 — Results

**Executed:** 2026-06-14 (UTC). **Target:** dev BFF
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`.

## Stream-liveness sweep (15 param-free SSE routes)

`text/event-stream` 200 confirmed for: `/api/v1/agora/ask/stream`,
`/api/v1/approvals/stream`, `/api/v1/kill-switch/updates`,
`/bff/events/stream`, `/bff/sse/agora/signals`, `/bff/sse/alerts`,
`/bff/sse/command-center/events`, `/bff/sse/command-center/kpi`,
`/bff/sse/deployment/events`, `/bff/sse/notifications`,
`/bff/sse/review/updates`. **H1 PASS.**

Three "events"/"stream"-named routes are intentionally **list/snapshot** JSON,
not SSE: `/bff/events` (`{items,page_info}`), `/bff/audit/events`
(`{events:[...]}`), `/bff/management/intervention-stream` (`{data:{items}}`).
Naming-only overlap; not a defect.

The streams send no bytes within a 6s window — expected: `_sse_stream` replays
history (buffers are empty) then heartbeats only every 30s
(`asyncio.wait_for(..., timeout=30.0)` → `: heartbeat`). Healthy idle behavior,
not a hang.

## Reconnect negative path

Reconnecting with `?last_event_id=bogus-evt-999999` returns **409
RESOURCE_CONFLICT** with the canonical envelope (and resync hints) across
`approvals/stream`, `kill-switch/updates`, `sse/alerts`, `sse/notifications`,
`events/stream`. **H2 PASS** — `_handle_sse_stream` pre-checks replayability and
fails closed with a 409 instead of starting an un-resumable stream.

## Findings

### F6 — Round 2 fix (`/api/v1/incidents/stream`) merged but not yet live (deploy-lag)

On live dev, `/api/v1/incidents/stream` still returns **404** ("Incident stream
does not exist") because the running BFF image predates PR #1541 (merged to
`dev` 2026-06-14 13:02Z). In-code the route now resolves to
`stream_incident_events` (Round 2 test + TestClient verified). **H3:** the gap
is purely deploy-lag — the live BFF must be redeployed from `dev` to close it.

Owner: OPS/deploy (BFF redeploy). Not triggered here — redeploys are babysat
OPS actions and out of scope for an in-repo verification round. Tracked as a
post-deploy re-verification item for the campaign.

## Net

H1/H2 **PASS** — the live SSE substrate is healthy: correct media type,
keep-alive headers, 30s heartbeat, replay support, and a fail-closed 409 on
unreplayable reconnect. H3 surfaced one deploy-lag item (F6) for the Round 2
fix. No new code defect this round.
