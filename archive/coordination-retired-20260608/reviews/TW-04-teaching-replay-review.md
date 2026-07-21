# TW-04 Teaching Replay Review Packet

## Date

2026-04-21

## Reviewer

Codex

## Findings

No blocking findings remain for `EXEC-RUNTIME-TW04-001`.

## Reviewed Artifacts

- Pantheon contract bundle:
  - `docs/bff/TW-04-teaching-replay.md`
  - `docs/examples/TW-04-teaching-replay.json`
  - `docs/screens/TW-04-teaching-replay.md`
  - `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md`
  - `.coordination/responses/TW-04-teaching-replay-contract-ready.yaml`
  - `.coordination/responses/TW-04-teaching-replay-lovable-ui-task.yaml`
- Pantheon coordination state:
  - `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml`
  - `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml`
  - `.coordination/responses/TW-04-teaching-replay-frontend-feedback.yaml`
- Pantheon BFF verification:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/read_store.py`
  - `services/control-plane/bff/test_tw04_teaching_replay_contract.py`

## Verified Positives

- `python3 -m pytest services/control-plane/bff/test_tw04_teaching_replay_contract.py -q`
  now returns `34 passed`.
- `GET /openapi.json` on `http://127.0.0.1:18001` advertises the full TW-04
  route family:
  - `GET /api/v1/trainer/replay`
  - `GET /api/v1/trainer/replay/{session_id}`
  - `POST /api/v1/trainer/sessions/{session_id}/commit`
  - `POST /api/v1/trainer/sessions/{session_id}/discard`
- Authenticated live list verification succeeds on the refreshed runtime:
  - `GET /api/v1/trainer/replay?persona_id=persona-alpha&status=completed`
    returns `200` with browser-facing `links.replay_detail`.
- Authenticated live detail verification succeeds on the refreshed runtime:
  - `GET /api/v1/trainer/replay/trn-20260418-003` returns `200`
  - `links.self = /trainer/replay/trn-20260418-003`
  - `links.session_detail = /trainer/sessions/trn-20260418-003`
- The previously unresolved telemetry evidence target is fixed on live HTTP:
  - `event.evidence_ref.url_pattern = /operator/paper-live-drift/runtime-042`
  - This matches the mounted front owner route for the paper-live-drift surface.
- Live commit/discard POST handlers are mounted and enforce backend authority:
  - authenticated `POST .../commit` on the already committed replay returns
    `PRECONDITION_NOT_MET` on `allowedActions.canCommit`
  - authenticated `POST .../discard` on the already committed replay returns
    `PRECONDITION_NOT_MET` on `allowedActions.canDiscard`
- The TW-04 gap handoff is now truthfully resolved:
  - `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml`
    records the live route-topology fix and no longer leaves the gap open.
- The TW-04 runtime handoff is now truthfully resolved:
  - `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml`
    records the live-route verification and clears the runtime blocker.

## Decision

`TW-04-teaching-replay` is approved for this runtime-refresh review cycle.

The active operator-bff runtime now serves the TW-04 replay route family over
live HTTP, browser-facing replay/session links match the mounted front routes,
and the previously incorrect telemetry evidence target now resolves to a
deployed owner route on the live runtime. The contract suite also continues to
cover degraded and unavailable replay semantics.

`EXEC-RUNTIME-TW04-001` can move from `review` to `review_approved`. The owner
may finalize the task once the normal closeout flow is ready.

## Residual Risk

- The live seeded dataset on `:18001` still reports
  `meta.surfaces.trainer_replay = stale`; this review treated that as
  non-blocking for the current task because the acceptance bar here is live
  route exposure, link topology, and evidence-target reachability rather than
  service-backed freshness.
- The live list proof used the current seeded runtime identity
  `persona-alpha`, while the canonical example payload still illustrates
  `p-breakout-trainer`. That identity/example drift does not block this runtime
  refresh task, but it remains worth cleaning up if example-payload parity is
  required for a later packet closeout.
- No deployed browser session was exercised against the refreshed runtime in
  this review pass; browser QA remains a separate follow-up concern.
