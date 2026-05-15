# Review: SVC-BLUEPRINT-FRONT-AUTH-DEMO-CUTOFF

**Reviewer:** Codex
**Task:** Replace frontend demo auth and demo islands with BFF-backed staging paths
**Date:** 2026-05-04
**Decision:** APPROVED

## Acceptance Criteria Assessment

### 1. staging/prod build has no demo quick-login production path

- `src/auth/AuthProvider.tsx` forces `dev_local` to `jwt_bff` unless the Vite build mode is `development` and Pantheon env is `dev`/`development`.
- `src/pages/auth/Login.tsx` wraps quick-login UI behind `import.meta.env.MODE === 'development'`.
- Production bundle check found no quick-login UI strings and no `dev-local:` token prefix. One `dev_local` string remains as an env discriminator that falls back to `jwt_bff`; it is not an enabled quick-login path.

### 2. production routes do not directly import `@/demo`

- `src/App.tsx` no longer imports demo-backed `Index`, `pages/evolution/Center`, `pages/tools/Center`, `pages/trainer/Trainer`, `pages/persona/NewPersona`, or `pages/health/Health`.
- The routed replacements use BFF-backed operator/evolution/trainer pages, redirects, or fail-closed `ComingSoonWorkbench` placeholders for BFF contract gaps.
- `scripts/check_no_demo_prod_routes.mjs` now walks the production import graph from `src/main.tsx`.

### 3. frontend build/lint pass and BFF auth docs exist

- `docs/bff/README.md` documents staging/prod BFF auth env vars and auth endpoint defaults.
- Settings section type cleanup supports the lint baseline without widening production auth behavior.

## Verification

- `npm run check:prod-demo-routes` passed: production route demo guard checked 160 modules.
- `npm run build` passed.
- `npm run lint` passed with 0 errors and 10 existing warnings.
- Targeted `dist` grep found 0 matches for `quick-login`, quick-login UI labels, `dev-local:`, `@/demo`, `demo/api`, and `demo/zpb`.

## Summary

The frontend demo auth and demo route cutoff meets the stated acceptance criteria. Staging/prod auth now depends on BFF/OIDC/JWT-compatible paths, and routed production modules no longer pull demo islands into the production graph. Approved for owner closeout.
