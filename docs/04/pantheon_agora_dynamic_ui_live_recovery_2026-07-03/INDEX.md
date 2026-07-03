# Agora Dynamic UI Live Recovery Archive - 2026-07-03

Status: ready for fleet execution

Execution packet:

- `docs/bff/execution-tasks/2026-07-03-agora-dynamic-ui-live-auth/INDEX.md`

Primary follow-up task:

- `AG-DYNUI-LIVE-AUTH-003` - Agora Trading Room frontend BFF auth headers

## Situation

The live Agora route at `/agora/trading-room` still renders the wrong production
state. The visual refresh and backend fallback work have both been merged, but
the live page still cannot load Trading Room data.

This is not a static-page conversion task. The implementation must preserve the
dynamic Agora UI model: BFF-driven Trading Room state, decision events,
workspace mutations, dynamic strategy/persona state, strict auth, and live probe
verification.

## Completed Work

### Execute Plans UI refresh

- Repository: `ajoe734/execute-plans`
- PR: `https://github.com/ajoe734/execute-plans/pull/147`
- Merge commit: `aa071d6fbdb5746f6c41ec714cd424c4a32c72ea`
- Result: old white-layout markers were removed from the live bundle after the
  FE deploy and integration gate completed.

### Pantheon backend cookie fallback

- Repository: `ajoe734/pantheon`
- PR: `https://github.com/ajoe734/pantheon/pull/2808`
- Merge commit: `056f5cd8f2ca2a05b4bd577d479ae5e3736ef067`
- Nonprod deploy run: `28646219401`
- Deployed OpenAPI evidence: Trading Room BFF routes now expose the
  `pantheon_session` cookie parameter.

## Current Live Failure

After PR #2808 deployed, live probing still showed:

- `/bff/agora/trading-room` returns `401`.
- `/bff/agora/trading-room/decision-events` returns `401`.
- The page still shows a Trading Room load failure.
- Screenshot artifact: `/tmp/agora-live-after-auth002.png`.
- JSON artifact: `/tmp/agora-live-after-auth002.json`.

Header-level browser probing isolated the root cause:

- `/bff/me`: sends `Authorization`, no cookie, returns `200`.
- `/bff/management/shell-summary`: sends `Authorization`, no cookie, returns
  `200`.
- `/bff/agora/trading-room`: sends no `Authorization`, no cookie, returns `401`.
- `/bff/agora/trading-room/decision-events`: sends no `Authorization`, no
  cookie, returns `401`.

The backend cookie fallback is valid but insufficient for the current live
browser session because no cookie is present. The frontend Agora Trading Room
client still uses direct `fetch` calls that include `credentials: "include"` but
do not call the shared BFF header builder that injects bearer auth.

## Design References

The user-referenced file names were checked in the repository root:

- `AI%20Trading%20Desk%20Design.zip`: not present.
- `AI Trading Desk Design.zip`: not present.

Available local Agora design archives:

- `Pantheon_Agora_Design_Closure_Pack_2026-06-20.zip`
- `Pantheon_Agora_Contract_Layer_Closure_2026-06-20.zip`

Fleet workers must use the available design closure and contract closure packs,
plus the existing Agora design/spec documents referenced by the execution
packet. If any required design detail is missing, unclear, or conflicts with
code, the worker must raise a blocker instead of inventing UI behavior.

## Root Cause

`execute-plans/src/lib/bff-v1/agora/tradingRoom.ts` contains direct Trading
Room BFF calls that do not use `src/lib/bff-v1/headers.ts`.

Known direct fetch locations include reads and writes around:

- Trading Room snapshot load.
- decision event list.
- strategy/workspace mutation routes.
- position/action mutation routes.

The fix belongs in `execute-plans`, not in a new static UI rewrite.

## Required Development Plan

1. Update the Agora Trading Room BFF client so every read and write uses the
   shared BFF auth/header path.
2. Preserve existing dynamic state flow and API contracts.
3. Add unit tests that prove `Authorization` is sent for Trading Room reads and
   mutations.
4. Run the execute-plans local validation relevant to the changed client.
5. Open a PR against the correct execute-plans base branch.
6. Review, merge, and wait for the dev FE deploy.
7. Run hosted live probes against `/agora/trading-room`.
8. Do not close the task until the live BFF calls return `200` and the page no
   longer renders the Trading Room load failure.

## Out Of Scope

- Rebuilding Agora UI from scratch.
- Replacing dynamic BFF state with static mock data.
- Changing backend auth policy to paper over missing frontend headers.
- Relaxing auth on Agora BFF endpoints.
- Treating old marker absence as final acceptance.

## Completion Definition

This recovery is complete only when all of the following are true:

- execute-plans PR is merged.
- Required checks are green or explicitly waived with evidence.
- Dev FE deploy from the merged commit succeeds.
- Live `/agora/trading-room` no longer shows the old layout or load failure.
- Live `/bff/agora/trading-room` returns `200` from the browser session.
- Live `/bff/agora/trading-room/decision-events` returns `200` from the browser
  session.
- Closeout records PR number, merge commit, deploy run, live probe artifacts,
  and residual risks.

