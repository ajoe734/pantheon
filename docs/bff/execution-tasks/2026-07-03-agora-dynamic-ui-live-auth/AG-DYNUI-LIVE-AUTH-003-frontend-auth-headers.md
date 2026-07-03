# AG-DYNUI-LIVE-AUTH-003 - Agora Trading Room Frontend Auth Headers

Owner: Claude
Reviewer: Codex
Parent: live Agora dynamic UI recovery
Depends on: `AG-DYNUI-LIVE-DEFAULT-001`, `AG-DYNUI-LIVE-AUTH-002`

## Problem

Live `/agora/trading-room` still fails after the UI refresh PR and backend
cookie fallback PR were merged and deployed.

Browser header evidence shows that normal BFF calls send `Authorization`, but
Agora Trading Room calls do not:

- `/bff/me`: `Authorization` present, `200`.
- `/bff/management/shell-summary`: `Authorization` present, `200`.
- `/bff/agora/trading-room`: `Authorization` missing, `401`.
- `/bff/agora/trading-room/decision-events`: `Authorization` missing, `401`.

The frontend Trading Room client currently relies on `credentials: "include"`.
That is not enough for the live browser session because there is no cookie
present. The shared BFF header builder must be used.

## Scope

- Repository: `ajoe734/execute-plans`.
- Primary file:
  `execute-plans/src/lib/bff-v1/agora/tradingRoom.ts`.
- Primary test file:
  `execute-plans/src/lib/bff-v1/agora/tradingRoom.test.ts`.
- Shared header helper:
  `execute-plans/src/lib/bff-v1/headers.ts`.

Update all Trading Room BFF reads and writes so they use the same shared header
path as other authenticated BFF clients. Preserve `credentials: "include"` as a
fallback, but do not rely on cookies as the only auth transport.

## Required Test Coverage

Add or update tests proving that `Authorization` is sent for:

- `getTradingRoom`.
- `listDecisionEvents`.
- at least one decision event mutation.
- at least one strategy/workspace mutation.

Tests must also keep mutation-specific headers intact, including caller-provided
`If-Match`, `Idempotency-Key`, and request/correlation IDs where applicable.

## Dynamic UI Requirement

This task must not replace the Trading Room with static design output. The
worker must keep the dynamic UI contract intact:

- Trading Room state comes from BFF.
- decision event queue comes from BFF.
- strategy/workspace actions still mutate through BFF.
- auth state comes from the existing app/session/auth providers.
- empty, loading, degraded, and error states remain honest and data-driven.

If a design reference is missing or conflicts with code, stop and raise a
blocker with the exact missing file, route, component, or acceptance question.

## Acceptance

- All Trading Room BFF calls in `tradingRoom.ts` use shared BFF auth headers.
- Unit tests prove `Authorization` on the required read and mutation calls.
- Relevant execute-plans tests pass locally.
- execute-plans PR is opened, reviewed, merged, and records the merge commit.
- Dev FE deploy from the merged commit succeeds.
- Hosted live probe against `/agora/trading-room` passes.
- Browser-session network evidence shows:
  - `/bff/agora/trading-room` returns `200`.
  - `/bff/agora/trading-room/decision-events` returns `200`.
  - page does not show `Failed to load Trading Room`.
  - old white layout markers remain absent.

## Closeout Fields

The worker/reviewer closeout must record:

- PR URL.
- merge commit SHA.
- validation commands.
- deploy run URL.
- live probe artifact paths.
- residual risks, if any, with owner and expiry.

