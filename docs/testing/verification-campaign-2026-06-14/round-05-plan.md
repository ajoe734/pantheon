# Round 5 — SSE streaming substrate behavior

**Date:** 2026-06-14
**Depth/breadth step:** Rounds 1–4 covered request/response surfaces. Round 5
verifies the **streaming** substrate — a behavior (not just a status code):
do the Server-Sent Events endpoints serve the right media type, the right
keep-alive headers, and handle reconnection (`last_event_id`) correctly?

## Why this round (not a duplicate)

`test_pkt005_sse_substrate_contract.py` and `test_sse_live.py` exist as unit
contracts, but no doc verifies the **live deployed** SSE surface end-to-end
across all stream routes, including the replay/reconnect negative path. Round 2
just un-shadowed `incidents/stream`; Round 5 confirms the broader stream family
is live and well-behaved.

## Hypotheses

- H1: every live SSE GET serves `content-type: text/event-stream` with 200 and
  keep-alive headers (`Cache-Control: no-cache`, `X-Accel-Buffering: no`).
- H2: a reconnect with an unreplayable `last_event_id` returns a clean **409
  RESOURCE_CONFLICT** (canonical envelope, resync hints) — never 200/500.
- H3 (deploy parity): routes fixed on `dev` are reflected live, or the gap is
  explicitly attributed to a pending deploy.

## Method

1. Enumerate SSE GET routes from live OpenAPI (~20; 15 param-free).
2. Probe each within a short window: capture status, content-type, first bytes.
3. Probe a representative subset with a bogus `last_event_id`; expect 409.
4. Cross-check `incidents/stream` (Round 2 fix) live vs in-code resolution.

## Pass criteria

- H1: all reachable streams serve `text/event-stream` 200.
- H2: reconnect-replay-unavailable yields 409 across sampled streams.
- H3: any live/dev divergence is a deploy-lag finding with a clear owner.
