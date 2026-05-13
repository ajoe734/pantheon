# BFF-CONSOL-012 Review - Codex

Reviewed at: 2026-05-13T05:28:58Z
Reviewer: Codex
Owner: Codex2

Disposition: approved

## Findings

No blocking findings.

## Scope Reviewed

- `services/control-plane/bff/tests/test_sse_backpressure.py`
- `support/evidence/BFF-CONSOL-012-sse-backpressure.json`
- SSE helper behavior in `services/control-plane/bff/main.py`
- `EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md`

## Acceptance Check

- Replay buffers are explicitly bounded at `deque(maxlen=500)`.
- Slow subscriber queues are bounded at `asyncio.Queue(maxsize=1000)`.
- Saturated subscriber queues drop newest events instead of growing unbounded.
- Disconnect cleanup removes the subscriber queue through the stream generator `finally` path.
- Replay window eviction drops oldest events and missing cursors raise `SSE_REPLAY_UNAVAILABLE`.
- Test coverage preserves per-aggregate ordering fields inside the replay window and does not assume global ordering.
- Evidence records measured replay and subscriber high-water marks plus drop strategy.

## Verification

- `pytest services/control-plane/bff/tests/test_sse_backpressure.py -q` -> 3 passed.
- `pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py services/control-plane/bff/tests/test_sse_backpressure.py -q` -> 17 passed.
- `python3 -m json.tool support/evidence/BFF-CONSOL-012-sse-backpressure.json >/dev/null` -> passed.

## Notes

- The current worktree contains unrelated dirty files from other active tasks, including `services/control-plane/bff/main.py`; this approval is scoped to the BFF-CONSOL-012 test/evidence artifacts and the existing SSE helper contract they exercise.
