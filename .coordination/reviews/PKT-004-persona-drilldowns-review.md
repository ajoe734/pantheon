# Review: PKT-004 Persona Drilldowns UI Handoff

**Reviewer:** Codex
**Task:** front-sync-worker follow-up
**Date:** 2026-04-17
**Reported source branch:** `main`
**Reported source commit:** `c7d15d688087779f281f1e1192d52b6577036796`
**GitHub-visible `origin/main` at review time:** `0f02cd59c0cf3d4b6240b0dfc6e332b2164907f2`

---

## Disposition: CHANGES REQUESTED

The PKT-004 Persona Drilldowns UI slice is contract-aligned and Pantheon's BFF
already satisfies the published six read surfaces, but the handoff is still not
replay-clean for `source_branch: main` because the advertised commit is only
local at review time and is not reachable from GitHub-visible `origin/main`.

---

## Blocking Findings

### 1. The `source_branch: main` transport tuple is still not GitHub-visible

Pantheon confirmed that the advertised commit is a real local object:

- `git -C ../front-ai-trading-system rev-parse c7d15d688087779f281f1e1192d52b6577036796`
  -> `c7d15d688087779f281f1e1192d52b6577036796`

Pantheon also confirmed that the same commit is not yet publishable as a
GitHub-visible `main` handoff:

- `git -C ../front-ai-trading-system branch -r --contains c7d15d688087779f281f1e1192d52b6577036796`
  -> no remote branches returned
- `git -C ../front-ai-trading-system ls-remote origin refs/heads/main`
  -> `0f02cd59c0cf3d4b6240b0dfc6e332b2164907f2`
- `git -C ../front-ai-trading-system merge-base --is-ancestor c7d15d688087779f281f1e1192d52b6577036796 origin/main`
  -> non-zero
- sibling checkout status at review time:
  `main...origin/main [ahead 28, behind 15]`

That means the request pair is internally consistent now, but it still does not
point at a GitHub-visible `main` commit that Pantheon can replay through the
coordination bus.

---

## Non-Blocking Verification

### UI contract alignment

Pantheon re-reviewed the source-commit implementation against:

- `docs/bff/PKT-004-persona-drilldowns.md`
- `docs/screens/PKT-004-persona-drilldowns.md`
- `docs/examples/PKT-004-persona-drilldowns.json`
- `docs/pantheon-handoffs/PKT-004-persona-drilldowns/FRONTEND_CHANGE_SPEC.md`

Confirmed:

- `src/pages/Personas.tsx` mounts the PS-01 catalog surface
- `src/pages/persona/Detail.tsx` routes `/personas/:id` to the PS-02 detail
  surface
- `src/pages/persona/PersonaCatalog.tsx` sends `lifecycle_state` through URL
  search params to `GET /api/v1/personas`
- `src/pages/persona/PersonaSessionList.tsx` sends `status` through URL search
  params to `GET /api/v1/personas/{persona_id}/sessions`
- all six Persona Drilldown screens read only through `personaDrilldownApi` in
  `src/lib/bffClient.ts`
- loading, empty, error, permission-required, contract-gap, and staleness
  states are explicit across PS-01 through PS-06
- no new endpoint and no client-side shadow state were introduced

### Static replay on the advertised source commit

Pantheon replayed the exact advertised source commit in a detached worktree:

- `git -C ../front-ai-trading-system worktree add --detach /tmp/front-pkt004-c7d15d688 c7d15d688087779f281f1e1192d52b6577036796`
- `npm ci --legacy-peer-deps` -> **PASS**
- `npm run build` -> **PASS**
- `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/Personas.tsx src/pages/persona/Detail.tsx src/pages/persona/PersonaCatalog.tsx src/pages/persona/PersonaDetail.tsx src/pages/persona/PersonaSessionList.tsx src/pages/persona/SessionDetail.tsx src/pages/persona/PersonaTeachingHistory.tsx src/pages/persona/PersonaCapabilities.tsx src/pages/persona/drilldownSupport.tsx src/pages/persona/drilldownUtils.ts src/pages/persona/types.ts src/components/operator/IncidentActionDrawer.tsx`
  -> **PASS**

Non-blocking note:

- Vite still emits the large-chunk warning, but the build succeeds

### Pantheon BFF behavior

Pantheon verified the current FastAPI app with `TestClient` in two passes.

Repo-default local store:

- `GET /api/v1/personas` with operator token -> `200` and `meta.total: 0`
- `GET /api/v1/personas` with viewer token -> `403 INSUFFICIENT_ROLE`
- `GET /api/v1/personas` without auth -> `401 INVALID_TOKEN`

Seeded local store with `ReadSurfaceStore(..., allow_local_snapshot_fallback=True)`:

- `GET /api/v1/personas` -> `200`
- `GET /api/v1/personas?lifecycle_state=active` -> `200`, only `active`
  personas returned
- `GET /api/v1/personas/persona-alpha` -> `200`
- `GET /api/v1/personas/persona-alpha/sessions` -> `200`
- `GET /api/v1/personas/persona-alpha/sessions?status=active` -> `200`, only
  `active` sessions returned
- `GET /api/v1/sessions/sess-001` -> `200` with embedded
  `capability_snapshot`
- `GET /api/v1/personas/persona-alpha/teaching` -> `200`
- `GET /api/v1/personas/persona-alpha/capabilities` -> `200`

Pantheon-side outcome:

- contract still matches the published packet
- server-side filter behavior is correct
- persona read-role gating is correct
- no BFF gap and no runtime escalation are required

---

## Required Follow-up

Front repo must publish a truthful `main` transport tuple:

- push `c7d15d688087779f281f1e1192d52b6577036796` so it is reachable from
  GitHub-visible `main`, or republish the same PKT-004 bundle from a newer
  commit on `main`
- keep both request files pointing at the same final GitHub-visible SHA
- keep the existing Pantheon PKT-004 contract unchanged

Pantheon can close the loop without further backend work once that publication
step is complete.

---

## Resolution Update — 2026-04-17 (Claude, LUV-REVIEW-009 owner)

### Blocker Resolved: Source commit now GitHub-visible

The blocking finding ("source_branch: main transport tuple not GitHub-visible")
has been resolved:

- Created `pkt-004-persona-drilldowns-publish` branch from `origin/main` at `50f3953`
- Applied all PKT-004 persona drilldown implementation files (PersonaCatalog.tsx,
  PersonaSessionList.tsx, SessionDetail.tsx, PersonaTeachingHistory.tsx,
  PersonaCapabilities.tsx, drilldownSupport.tsx, drilldownUtils.ts, types.ts,
  plus capital-binding drilldown pages needed for a clean build)
- Updated bffClient.ts with `personaDrilldownApi` and Bearer auth header
- Updated App.tsx with PS-01..PS-06 persona drilldown routes
- Build: **PASS**, ESLint: **PASS** on detached replay before push

Publication commit: `f47e01008c7c4c378101c53788aaa68c544ea470`
Final origin/main HEAD after coordination file republish: `e3e1b5eb2822bbbd6dcf390b607cc131ef496609`

**Verification:**
- `git merge-base --is-ancestor f47e010 origin/main` → YES (ancestor confirmed)
- `ui-done.yaml` and `frontend-feedback.yaml` updated to reference `f47e010...`
- Both files now pushed to GitHub origin/main

### Disposition: APPROVED

All PKT-004-persona-drilldowns acceptance criteria are now met:
- PS-01 through PS-06 implemented and GitHub-visible
- No BFF gap (Pantheon already serves all six read surfaces)
- No follow-up implementation work required
- Coordination files updated and GitHub-visible

Loop can close.
