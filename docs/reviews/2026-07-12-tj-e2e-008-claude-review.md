# Review: TJ-E2E-008 — Governed Journey Actions

Reviewer: Claude
Owner: Codex
Date: 2026-07-12
Status: CHANGES REQUESTED

## Scope

Review of `POST /bff/management/trade-journeys/{journey_id}/actions` per the
owner handoff (`docs/reviews/2026-07-12-tj-e2e-008-codex-handoff.md`, anchor
`800b37ea8`): no-bypass dispatch boundary, receipt/readback contract, RBAC,
and stale/idempotency/live-gate semantics.

## Verification performed

```text
python3 -m pytest -q \
  services/control-plane/bff/test_tj_e2e_008_governed_journey_actions.py \
  services/control-plane/bff/test_tj_e2e_005_trade_journeys_read_api.py
```

Result: `30 passed, 4 warnings` — confirms the owner's reported evidence.

## What checks out

- **No-bypass dispatch**: the router only ever calls the injected
  `dispatch_action(command)`; the default (`_unconfigured_action_dispatcher`)
  fails closed with `partial_failure` / `ACTION_DISPATCH_UNAVAILABLE` and no
  optimistic success is fabricated. `main.py` does not wire a production
  dispatcher yet, which the handoff correctly discloses as a deliberate
  boundary.
- **Receipt/readback**: a `succeeded` status with `readback=None` is
  downgraded to `partial_failure`/`READBACK_REQUIRED` before it reaches the
  client (`trade_journeys.py:741-745`); every response sets
  `meta.refetch_required = true`.
- **RBAC (as wired)**: `main.py` passes `require_operator_role=_require_operator_role`
  into the router factory (anchor commit `800b37ea8`), so the shipped
  configuration correctly requires operator/admin for the action endpoint;
  the viewer-403 test passes against that wiring.
- **Idempotency**: same key + same request hash replays the cached receipt
  (`idempotent_replay: true`); same key + different request hash is a
  `409 IDEMPOTENCY_CONFLICT`. Matches the contract tests.
- **Live-capital gate**: `pause`/`cancel`/`reconciliation_retry` in the
  `live` environment are fail-closed behind
  `PANTHEON_TRADE_JOURNEY_LIVE_ACTIONS`; `escalate`/`human_review`/
  `incident_acknowledge` are intentionally not capital-moving and are not
  gated.

## Blocking finding

### Stale-revision check compares the wrong revision (`trade_journeys.py:706`)

```python
if request.expected_revision != materializer.revision:
    return _err(409, "STALE_JOURNEY_REVISION", ...)
```

`materializer.revision` is the **event-store-wide** counter — it increments
once per `_rematerialize()` call, i.e. once per ingested event *for any
journey, tenant, or environment in the whole store*
(`services/trade_journey/materializer.py:127`).

The value the client actually reads back as `revision` from list/detail
responses is the **per-journey** count returned in
`_list_row()` (`trade_journeys.py:459`, `snapshot.get("revision")` ==
`len(events)` for that one journey,
`services/trade_journey/materializer.py:171`).

These are different numbers in any store with more than one journey or more
than one ingest call. Reproduced directly against `JourneyMaterializer`:

```text
>>> materializer.ingest(event_for_tj-8)
>>> materializer.ingest(event_for_tj-9)   # unrelated journey
>>> materializer.ingest(event_for_tj-9)   # unrelated journey, 2nd event
>>> materializer.get('tj-8', ...).snapshot['revision']   # what the client saw: 1
1
>>> materializer.revision                                # what the server checks: 3
3
```

An operator who fetches `tj-8`, sees `revision: 1`, and immediately submits
`expected_revision=1` gets `409 STALE_JOURNEY_REVISION` the moment *any*
other journey anywhere in the tenant/environment ingests an event — even
though `tj-8` itself did not change. In a live event-sourced store this
counter advances continuously, so the guard as written will reject nearly
every legitimate action, not just genuinely stale ones.

This is fail-closed (no real staleness goes undetected), so it is not an
integrity/bypass risk, but it defeats the optimistic-concurrency contract
the endpoint advertises and would make the action endpoint effectively
unusable once more than one journey exists. The existing focused tests do
not catch this because every fixture has exactly one journey built by a
single `rebuild()` call, where the two counters coincidentally match.

**Required fix**: compare `request.expected_revision` against the
journey's own `snapshot.get("revision")` (or an equivalent stable
per-journey version marker), not `materializer.revision`. Add a regression
test with ≥2 journeys / ≥2 ingest calls so the two counters diverge and the
guard is exercised against the correct value.

## Non-blocking observations

- `(require_operator_role or require_read_role)(identity)` at
  `trade_journeys.py:693` silently falls back to read-role RBAC if the
  router is composed without `require_operator_role`. Current `main.py`
  wiring is correct, but the factory default is a footgun for any future
  composition site (e.g. a test harness or another service) that forgets to
  pass it explicitly. Consider making `require_operator_role` required
  (non-`Optional`) for this router, or asserting it is set for the
  `/actions` route specifically.
- `JourneyActionLedger` is explicitly documented as process-local and
  non-durable; that's an accepted, disclosed boundary for this task, not a
  finding, but it should stay tracked as a follow-up before multi-instance
  BFF deployment.

## Decision

**CHANGES REQUESTED** — the no-bypass dispatch, receipt/readback, RBAC (as
wired), and idempotency/live-gate semantics are sound, but the stale
-revision guard is comparing the wrong field and must be fixed (with a
multi-journey regression test) before this can be approved. Production
dispatcher composition remains correctly out of scope per the owner's
disclosed boundary and does not need to block this task.
