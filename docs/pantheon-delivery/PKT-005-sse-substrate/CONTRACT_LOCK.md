# PKT-005 SSE Reconciliation Substrate — Contract Lock

Status: `delivered`
Locked at: 2026-04-24
Locked by: Codex

## Review Anchor

- Pantheon payload:
  `.coordination/requests/PKT-005-sse-substrate-frontend-feedback.yaml`
- Companion completion payload:
  `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml`
- Reviewed front source commit:
  `eb1a6cbb727a681db21ecd4b121348605fb8a4d3`
- Canonical front request-pair republish:
  `42dc4856b36a7c92f5c40cafd94bf8ef09665bbe`
- Current remote branch head preserving the same request pair and feedback
  bundle:
  `1a1a42eebda033a1fbda4696df5b81271f5eed9b`
- Pantheon contract reference:
  `pantheon-working-tree@0ee754bf0228a6998ace115f3277fdc30bcb15e1`

## Current Lock State

### Front publication state

- The reviewed front source commit contains the tracked PKT-005 SSE files, the
  canonical request pair, and the four-file feedback bundle under
  `docs/pantheon-feedback/PKT-005-sse-substrate/`.
- Canonical republish commit
  `42dc4856b36a7c92f5c40cafd94bf8ef09665bbe` changes only the two request files
  and pins both of them back to reviewed source commit
  `eb1a6cbb727a681db21ecd4b121348605fb8a4d3`.
- Current remote head `1a1a42eebda033a1fbda4696df5b81271f5eed9b` still carries
  that same approved request pair and feedback bundle.

### Locked implementation state

- Replay state now advances only after host apply:
  `SseClient.acknowledgeEvent()` and `SseReconciler.markApplied()` are called
  only after the host accepts and applies an event.
- Required host surfaces surface realtime contract gaps explicitly instead of
  silently dropping malformed payloads.
- Required host surfaces reconcile accepted runtime, incident, and kill-switch
  events into visible state or explicit refresh paths.
- Required host surfaces render the delayed-update note after 60 seconds of
  inactivity while connected.
- `API_GAP_REQUESTS.json` reports no open PKT-005 SSE gap.

## Contract References Reviewed

- `docs/pantheon-handoffs/PKT-005-sse-substrate/bff/PKT-005-sse-substrate.md`
- `docs/pantheon-handoffs/PKT-005-sse-substrate/screens/PKT-005-sse-substrate.md`
- `docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md`

## Locked Outcome

Pantheon does not authorize any new endpoint, SSE payload expansion, or
client-side shadow state from this closeout.

The approved PKT-005 SSE loop is locked to the Git-visible tuple above:

1. reviewed source commit
   `eb1a6cbb727a681db21ecd4b121348605fb8a4d3`
2. canonical request-pair republish
   `42dc4856b36a7c92f5c40cafd94bf8ef09665bbe`
3. current remote branch head preserving the same request pair and feedback
   bundle:
   `1a1a42eebda033a1fbda4696df5b81271f5eed9b`

Any future republish that changes the PKT-005 request-pair `source_commit`
must return through Pantheon review as a fresh loop rather than inheriting this
lock automatically.
