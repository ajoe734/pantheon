# PKT-004 Persona Drilldowns — Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon re-reviewed the Git-visible PKT-004 Persona Drilldowns handoff bundle
after the front repo republished the canonical request pair at
`de1f86a30b11b9c02f1baa15f50132204f960d22`.

That republish now points both
`.coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml` and
`.coordination/requests/PKT-004-persona-drilldowns-frontend-feedback.yaml` to
the same reviewed front source commit:
`6c27d009836601657709f33064e8e4cc9c27f9ab`.

The older front-owned blockers are resolved:

- `/personas/:id` no longer routes through the legacy demo-backed detail view
- the feedback bundle is now Git-visible under
  `docs/pantheon-feedback/PKT-004-persona-drilldowns/`
- the republished request pair is replay-clean and anchored to a real source
  commit

No new Pantheon endpoint, contract expansion, or API-gap handoff is required
for this loop.

## Front-End Review Outcome

- Pantheon review result: accepted for closeout
- No Pantheon API gap is requested from this pass
- The six PKT-004 persona drilldown surfaces remain aligned with the published
  contract and example payload
- Remaining work is non-blocking only:
  - live browser and live BFF verification
  - runtime authorization QA across role variants
  - optional bundle-size optimization

## Verified UI Alignment

- `GET /api/v1/personas`
- `GET /api/v1/personas/{persona_id}`
- `GET /api/v1/personas/{persona_id}/sessions`
- `GET /api/v1/sessions/{session_id}`
- `GET /api/v1/personas/{persona_id}/teaching`
- `GET /api/v1/personas/{persona_id}/capabilities`

Pantheon re-confirmed the front publication truth:

- request-pair republish commit:
  `de1f86a30b11b9c02f1baa15f50132204f960d22`
- reviewed UI source commit:
  `6c27d009836601657709f33064e8e4cc9c27f9ab`
- `src/pages/persona/Detail.tsx` now re-exports `PersonaDetail`
- the Git-visible feedback bundle contains:
  - `LOVABLE_CHANGE_FEEDBACK.md`
  - `API_GAP_REQUESTS.json`
  - `UI_DECISIONS.md`
  - `QA_STATUS.md`

The accepted front bundle stays within the intended PKT-004 boundary:

- no raw `fetch()` or `axios` calls were added to persona drilldown components
- filters remain query-param driven through the BFF client
- missing required fields still route to explicit contract-gap handling instead
  of client-side invention
- no demo-provider import remains on the mounted persona detail route

## Verification Performed

- Reviewed the Git-visible front request pair:
  - `git -C ../front-ai-trading-system show de1f86a30b11b9c02f1baa15f50132204f960d22:.coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml`
  - `git -C ../front-ai-trading-system show de1f86a30b11b9c02f1baa15f50132204f960d22:.coordination/requests/PKT-004-persona-drilldowns-frontend-feedback.yaml`
- Verified the reviewed source commit resolves and remains reachable:
  - `git -C ../front-ai-trading-system rev-parse 6c27d009836601657709f33064e8e4cc9c27f9ab`
- Verified the feedback bundle is Git-visible from the republish commit:
  - `git -C ../front-ai-trading-system ls-tree -r --name-only de1f86a30b11b9c02f1baa15f50132204f960d22 -- .coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml .coordination/requests/PKT-004-persona-drilldowns-frontend-feedback.yaml docs/pantheon-feedback/PKT-004-persona-drilldowns src/pages/persona/Detail.tsx src/pages/persona/PersonaDetail.tsx`
- Re-checked the mirrored Pantheon review packet:
  - `docs/bff/PKT-004-persona-drilldowns.md`
  - `docs/examples/PKT-004-persona-drilldowns.json`
  - `docs/screens/PKT-004-persona-drilldowns.md`
  - `docs/pantheon-handoffs/PKT-004-persona-drilldowns/FRONTEND_CHANGE_SPEC.md`

## Residual Risk

- This closeout did not rerun live browser QA against a running Pantheon BFF.
- Role-based runtime verification remains deferred to live-environment QA.
