# PKT-004 Persona Drilldowns Review Packet

## Date

2026-04-24

## Reviewer

Codex

## Scope

Re-review the dispatched `PKT-004-persona-drilldowns` request pair against the
current Git-visible front `origin/main` head
`1f179b9fd9206b97e5723649295f230f119f88f6`, the reviewed source commit
`f47e01008c7c4c378101c53788aaa68c544ea470`, and the published Pantheon
contract.

## Findings

### 1. Blocking — the Git-visible default-branch publication still omits the referenced feedback bundle

- `origin/main@1f179b9fd9206b97e5723649295f230f119f88f6` contains:
  - `.coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml`
  - `.coordination/requests/PKT-004-persona-drilldowns-frontend-feedback.yaml`
- The same tree still does **not** contain:
  - `docs/pantheon-feedback/PKT-004-persona-drilldowns/`
- Impact: the current request pair is not replay-clean as a closeout tuple on
  the default branch, even though the UI route wiring itself is now aligned.

### 2. Resolved since the older review — `/personas/:id` no longer routes through the demo provider path

- `/tmp/front-prompt-publish-main/src/App.tsx` still mounts `/personas/:id`
  through `./pages/persona/Detail`.
- `/tmp/front-prompt-publish-main/src/pages/persona/Detail.tsx` now contains
  only:
  - `export { default } from './PersonaDetail';`
- Impact: the earlier demo-provider blocker no longer applies to the mounted
  PKT-004 detail route.

### 3. Verification environment note — current prompt-pack checkout did not have runnable build/lint toolchain dependencies

- Attempted fresh validation from `/tmp/front-prompt-publish-main`:
  - `npm run build` -> `vite: not found`
  - `npx eslint ...` -> `Cannot find package '@eslint/js'`
- This is treated as verification-checkout dependency drift, not a new
  Pantheon contract or runtime blocker.

## Verified

### 1. Mounted PKT-004 surfaces remain on Pantheon-backed UI paths

- `src/pages/persona/Detail.tsx` now re-exports `PersonaDetail`
- `PersonaDetail.tsx`, `PersonaCatalog.tsx`, `PersonaSessionList.tsx`,
  `SessionDetail.tsx`, `PersonaTeachingHistory.tsx`, and
  `PersonaCapabilities.tsx` remain the Pantheon-backed drilldown surfaces

### 2. No new Pantheon API or BFF gap was found

- The current issue is publication truth only
- No new endpoint, field, or contract slice is required from Pantheon for this
  cycle

## Reviewed Artifacts

- `/tmp/front-prompt-publish-main/.coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml`
- `/tmp/front-prompt-publish-main/.coordination/requests/PKT-004-persona-drilldowns-frontend-feedback.yaml`
- `/tmp/front-prompt-publish-main/src/App.tsx`
- `/tmp/front-prompt-publish-main/src/pages/persona/Detail.tsx`
- `/tmp/front-prompt-publish-main/src/pages/persona/PersonaDetail.tsx`
- `docs/bff/PKT-004-persona-drilldowns.md`
- `docs/examples/PKT-004-persona-drilldowns.json`
- `docs/screens/PKT-004-persona-drilldowns.md`
- `docs/pantheon-handoffs/PKT-004-persona-drilldowns/FRONTEND_CHANGE_SPEC.md`
- `docs/pantheon-delivery/PKT-004-persona-drilldowns/DELIVERY_NOTE.md`

## Verification Performed

- `git -C /tmp/front-prompt-publish-main rev-parse HEAD`
- `git -C /tmp/front-prompt-publish-main ls-tree -r --name-only HEAD -- .coordination/requests/PKT-004-persona-drilldowns-ui-done.yaml .coordination/requests/PKT-004-persona-drilldowns-frontend-feedback.yaml docs/pantheon-feedback/PKT-004-persona-drilldowns`
- static route and file inspection of:
  - `/tmp/front-prompt-publish-main/src/App.tsx`
  - `/tmp/front-prompt-publish-main/src/pages/persona/Detail.tsx`
  - `/tmp/front-prompt-publish-main/src/pages/persona/PersonaDetail.tsx`
- attempted fresh front verification from `/tmp/front-prompt-publish-main`:
  - `npm run build`
  - `npx eslint ...`

## Decision

Do not close `PKT-004-persona-drilldowns` yet.

The earlier mounted demo-route blocker is resolved on current `origin/main`.
The remaining blocker is narrower: republish the request pair together with the
referenced feedback bundle on a truthful Git-visible default-branch tuple.

## Residual Risk

- Fresh build/lint was not reproducible from the current prompt-pack checkout
  because that verification tree lacks local toolchain dependencies.
- No live browser QA against a deployed Pantheon BFF was performed in this
  review cycle.
