# PKT-004 Persona Management Backend Delivery Note

## Status

`followup-required`

## Summary

Pantheon reviewed the returned Persona Management UI cycle from
`ajoe734/front-ai-trading-system` against the published PKT-004 read contract,
example payload, and live Pantheon BFF implementation.

The composed UI is aligned for the currently published PKT-004 scope:

- it reads only through `GET /api/v1/operator/persona-management/{persona_id}`
  via the shared BFF client
- it sends only the documented `EditPersona`, `RetirePersona`, and
  `TerminateSession` payloads through `POST /api/v1/operator/commands`
- it renders CTA visibility from backend-shaped `allowedActions` only
- it keeps degraded surfaces visible with read-only placeholders instead of
  hiding panels

Pantheon follow-up is still required on two backend-owned items:

1. the non-blocking request to publish canonical command payloads for the
   remaining backend-authorized CTAs
2. normalization of `data.allowedActions` so the live BFF always returns the
   full eight-boolean contract, not only a partially populated object for some
   persona states

## Verified Contract Alignment

- Read route remains `GET /api/v1/operator/persona-management/{persona_id}`
- Write route remains `POST /api/v1/operator/commands`
- No new endpoint, no shadow state, and no client-side joins were added in the
  reviewed UI flow
- The front-end route stays inside the existing IA at
  `/personas/:id/management`
- The current screen keeps unpublished backend-authorized actions visible but
  read-only, which matches the front feedback bundle and avoids inventing write
  shapes locally

## Pantheon-Owned Follow-Up

### 1. Unpublished command payloads for remaining `allowedActions`

The returned front-end feedback bundle correctly records a non-blocking request
for the remaining backend-authorized CTAs whose command payloads were not
published in PKT-004.

Requested write-contract follow-up:

- `canActivate`
- `canPause`
- `canDelete`
- `canPauseSession`

Impact on the current UI:

- The screen is reviewable and safe today because these CTAs remain disabled and
  read-only.
- No front-end guesswork is needed or allowed.

Pantheon action:

- Decide whether PKT-004 should expand its write scope for these actions.
- If yes, publish the canonical operator-command payloads in a refreshed packet
  and then republish `contract-ready` plus `lovable-ui-task` for the next UI
  cycle.

### 2. Live BFF `allowedActions` shaping still under-fills the published contract

The published PKT-004 contract and example payload require all eight
`data.allowedActions.*` keys to be present as booleans on every composed
response.

Current Pantheon implementation evidence:

- `services/control-plane/bff/main.py:1571-1589` returns
  `data.allowedActions` directly from `read_store.get_persona_allowed_actions()`
- `services/control-plane/bff/read_store.py:1896-1924` only populates keys that
  are currently applicable for the persona or session state

Why this matters:

- For `persona-alpha` the seed data happens to produce a full boolean set, so
  the reviewed UI can render the happy path cleanly.
- For draft or retired personas, some `false` keys may be omitted entirely even
  though the contract requires them to be present.
- The front-end correctly treats missing `allowedActions.*` fields as a contract
  gap, so this can turn into a Pantheon-owned failure outside the seed persona
  path.

Pantheon action:

- Normalize `get_persona_allowed_actions()` to always return the full
  eight-boolean matrix:
  - `canActivate`
  - `canEdit`
  - `canDelete`
  - `canRetire`
  - `canPause`
  - `canTerminateSession`
  - `canPauseSession`
  - `canViewTeachingHistory`

## Pantheon-Side Outcome

- Current UI baseline: accepted for the published PKT-004 scope
- Pantheon contract: read route unchanged
- Pantheon response anchor:
  `.coordination/requests/PKT-004-persona-management-frontend-feedback.yaml`
- Next Pantheon action:
  - resolve the backend-owned command-family decision
  - normalize `allowedActions` contract shaping
  - republish `contract-ready` and `lovable-ui-task` only if the command packet
    or read-contract lock changes

## Verification Performed

- Reviewed the returned front-end feedback bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-persona-management/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-persona-management/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-persona-management/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-004-persona-management/QA_STATUS.md`
- Reviewed the touched front-end files in the sibling repo working tree:
  - `src/App.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/Personas.tsx`
  - `src/pages/persona/Detail.tsx`
  - `src/pages/persona/PersonaManagement.tsx`
  - `src/pages/persona/types.ts`
- Cross-checked against:
  - `docs/bff/PKT-004-persona-management.md`
  - `docs/screens/PKT-004-persona-management.md`
  - `docs/examples/PKT-004-persona-management.json`
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/read_store.py`
- Ran front-end validation in the sibling repo:
  - `npm run build -- --mode production`
  - `npx eslint src/pages/persona/PersonaManagement.tsx src/pages/persona/types.ts src/lib/bffClient.ts src/App.tsx src/pages/Personas.tsx src/pages/persona/Detail.tsx`
  - Result: passed

## Not Completed

- Live browser QA against a running Pantheon BFF was not performed in this
  review cycle.
- Pantheon has not yet published the remaining persona-management command
  payloads requested in the feedback bundle.
