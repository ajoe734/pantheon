# BFF-CONSOL-011 Review - Codex

Reviewed at: 2026-05-13T05:06:00Z
Reviewer: Codex
Owner: Codex2

Disposition: approved

## Findings

No blocking findings.

## Scope Reviewed

- Commit `50b66af7df39b592de88d922287c9998d60be884`
- `scripts/probe_bff_sse_stream.py`
- `services/control-plane/bff/main.py`
- `support/evidence/BFF-CONSOL-011-sse-replay-smoke.json`

## Acceptance Check

- Cookie-session probe records native `EventSource(..., { withCredentials: true })` shape with no Authorization header.
- Bearer probe records polyfill/fetch stream shape with Authorization present.
- First SSE events include id, event type, timestamp, and matching data envelope fields.
- `Last-Event-ID` replay returns the event after the cursor for both cookie and bearer modes.
- Missing replay cursor returns `409 SSE_REPLAY_UNAVAILABLE` with `X-SSE-Resync-Routes`.
- Evidence JSON records publish, open, replay, unavailable, and assertion transcript sections.
- Live-mode mock generator remains closed; the probe seeds the BFF replay buffer through the authenticated internal publish route.

## Verification

- `python3 -m py_compile scripts/probe_bff_sse_stream.py services/control-plane/bff/main.py` -> passed.
- `python3 -m pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q` -> 14 passed.
- `jq '.summary, .assertions, .replay_unavailable.response_headers."X-SSE-Resync-Routes", .open_transcripts.cookie_session.response_headers."X-BFF-Session-Kind", .open_transcripts.bearer_polyfill.response_headers."X-BFF-Session-Kind"' support/evidence/BFF-CONSOL-011-sse-replay-smoke.json` -> summary passed, all assertions true, resync routes present, session kinds `cookie` and `bearer`.
- Route inspection shows `/bff/events/stream` resolves first to `stream_bff_events`; later same-path aliases are existing compatibility routes and are not the first match.
- Valid-JWT direct handler probe verified missing replay `409 /bff/approvals,/bff/v5/interventions`, bearer replay with `X-BFF-Session-Kind: bearer`, and cookie replay with `X-BFF-Session-Kind: cookie`.

## Notes

- I did not rerun the full local uvicorn live smoke because `uvicorn` is not installed in this Python environment. The committed evidence file contains the owner-run live transcript against `http://127.0.0.1:53160`, and the focused local verification above checks the same route handler and replay/error contract.
- The current worktree contains unrelated uncommitted state/archive/config changes and unrelated `services/control-plane/bff/main.py` command-envelope hunks. They were not considered part of this review approval.
