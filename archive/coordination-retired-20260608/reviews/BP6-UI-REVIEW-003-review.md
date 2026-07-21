# BP6-UI-REVIEW-003 Review

## Findings

1. High: the published `ui-done` handoff is not replayable from its advertised transport commit.

- The returned request points `source_commit` at `faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7` in [PKT-004-persona-drilldowns-ui-done.yaml](/home/lupin/code/front-ai-trading-system/.coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml:5).
- That commit is docs-only for PKT-004 and does not contain the claimed drilldown UI files. `git ls-tree -r faa1bc2d1bd02e0a3d9fc1e1e5c35bc510182ea7 -- src/pages/persona src/lib/bffClient.ts` returns only legacy tracked files, not the new drilldown surfaces.
- The current sibling front HEAD `37ebcafacb68ff617f097271c46eaac4a478cbb8` still does not contain the `ui-done` request path or the new drilldown page files, while the working tree shows those files as modified or untracked.
- Impact: the GitHub-visible transport tuple is broken, supervisor replay cannot rely on the published commit, and the acceptance criterion requiring GitHub-visible coordination artifacts is not met.

2. High: the shared BFF client still omits `Authorization`, so the persona drilldown screens cannot satisfy live Pantheon acceptance.

- The reviewed client sends only `Accept` on GET and `Content-Type` plus `Accept` on POST in [bffClient.ts](/home/lupin/code/front-ai-trading-system/src/lib/bffClient.ts:117) and [bffClient.ts](/home/lupin/code/front-ai-trading-system/src/lib/bffClient.ts:148).
- Pantheon requires a Bearer token on all persona drilldown routes. `_extract_identity()` rejects missing auth with `INVALID_TOKEN` in [main.py](/home/lupin/code/pantheon/services/control-plane/bff/main.py:67), and each PKT-004 endpoint extracts `authorization` before reading data in [main.py](/home/lupin/code/pantheon/services/control-plane/bff/main.py:890).
- Impact: the screens may build statically, but they cannot render real PKT-004 data from a live Pantheon BFF until the shared client propagates the authenticated operator token.

3. Medium: the shared BFF client still parses the wrong Pantheon error envelope.

- The frontend parser expects `{ status, code, message }` in [bffClient.ts](/home/lupin/code/front-ai-trading-system/src/lib/bffClient.ts:77).
- Pantheon emits errors as `detail.error.{code,message,details}` via [models.py](/home/lupin/code/pantheon/services/control-plane/bff/models.py:74) and [main.py](/home/lupin/code/pantheon/services/control-plane/bff/main.py:91).
- Impact: real Pantheon failures collapse to `UNKNOWN_ERROR` or generic HTTP text instead of preserving `INVALID_TOKEN`, `INSUFFICIENT_ROLE`, or other contract-level error codes.

4. Medium: the required paired `frontend-feedback` request is missing.

- The front repo contains the support bundle under `docs/pantheon-feedback/PKT-004-persona-drilldowns/`, but `.coordination/requests/PKT-004-persona-drilldowns-frontend-feedback.yaml` is absent.
- `docs/pantheon-feedback/PKT-004-persona-drilldowns/QA_STATUS.md` claims the UI cycle is statically verified, but the coordination bus requires a machine-readable `frontend-feedback` request for completed UI cycles.
- Impact: Pantheon can read the prose bundle locally, but the GitHub-visible machine summary for the feedback cycle is missing.

## Positives

- The working tree wiring does follow the published PKT-004 route family and uses the shared `personaDrilldownApi` namespace rather than component-level raw `fetch()` calls.
- The catalog `lifecycle_state` filter and session-list `status` filter are forwarded as query params.
- Local acceptance commands on the sibling working tree passed in this review cycle:
  - `npm run build`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/Personas.tsx src/pages/persona/Detail.tsx src/pages/persona/PersonaCatalog.tsx src/pages/persona/PersonaDetail.tsx src/pages/persona/PersonaSessionList.tsx src/pages/persona/SessionDetail.tsx src/pages/persona/PersonaTeachingHistory.tsx src/pages/persona/PersonaCapabilities.tsx src/pages/persona/drilldownSupport.tsx src/pages/persona/drilldownUtils.ts src/pages/persona/types.ts src/components/operator/IncidentActionDrawer.tsx`

## Review Outcome

Do not approve this `ui-done` cycle yet.

The next front-owned cycle should:

- commit the actual PKT-004 UI files and the request payloads into one replayable commit
- publish the missing paired `frontend-feedback` request
- republish `ui-done` with `source_commit` pointing at a commit that actually contains the listed files and payload paths
- propagate `Authorization: Bearer ...` through the shared BFF client
- align shared error parsing to Pantheon’s `detail.error.*` envelope
