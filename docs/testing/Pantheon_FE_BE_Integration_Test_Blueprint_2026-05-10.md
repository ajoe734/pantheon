# Pantheon FE/BE Integration Test Blueprint (2026-05-10)

Status: task-scoped FE integration gate blueprint

This blueprint records the browser-facing pass conditions for the execute-plans
frontend integration gate. The source test for F01 is
`execute-plans/e2e/01-startup-session.spec.ts`.

## 1. Scope

The FE/BE integration gate verifies that the execute-plans operator shell is
wired to live BFF contracts in strict mode. Mock or seed data may only appear in
explicit mock/hybrid modes with visible operator disclosure.

## 2. Shared Defaults

- Frontend base URL: `FRONTEND_BASE_URL` or `PLAYWRIGHT_BASE_URL`.
- BFF base URL: `BFF_BASE_URL` or `VITE_BFF_BASE_URL`.
- Strict fallback: `VITE_BFF_FALLBACK=strict` or `BFF_FALLBACK=strict`.
- Auth: `BFF_AUTH_TOKEN` when the target BFF requires a real bearer token.

## 3. F01 Startup Session Pass Condition

F01 passes only when all startup/session checks below are true:

- `GET /bff/me` returns a frontend-ready `MeResponse`.
- `MeResponse.data.tenant` includes `id`, `default_id`, `allowed_ids`, and
  `scope`.
- `MeResponse.data.environment` includes `name`, `deployment_stage`,
  `auth_mode`, `timezone`, and `strict_auth`.
- `MeResponse.data.user`, `data.currentUser`, and `data.current_user` describe
  the same operator identity with `id`, `operator_id`, `display_name`, `roles`,
  `capabilities`, and `mfa_verified`.
- `MeResponse.data.capabilities` mirrors `data.user.capabilities` and includes
  the read capability needed by the runtime shell.
- `MeResponse.data.session` includes `id`, `authenticated`, `session_kind`,
  `auth_mode`, `fresh`, `mfa_verified`, and `checked_at`.
- In strict mode, the first rendered frontend page does not show a
  serving-mock, mock-data, or seed-fallback banner.
- A browser-native `EventSource` opens `/bff/events/stream?channel=system`.
- If `/bff/me` is forced to return a typed `401`, the frontend must not fall
  back to mock current-user data or a serving-mock banner.

## 4. F01 Review Evidence

The reviewer should treat the F01 Playwright spec as the executable evidence
for this blueprint section. A valid handoff must report the exact Playwright or
focused static verification command used for `01-startup-session.spec.ts`.
