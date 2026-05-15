# Pantheon FE × BFF Release Gate Checklist

## Gate 0 — Preconditions

- [ ] `execute-plans` branch is clean and points to release candidate SHA.
- [ ] `pantheon` backend/BFF SHA is recorded.
- [ ] `PANTHEON_FE_BASE_URL` points to intended Lovable deployment.
- [ ] `PANTHEON_BFF_BASE_URL` points to intended BFF.
- [ ] No obsolete BFF URL appears in hosted JS bundle.
- [ ] Auth token or test OIDC path available for authenticated smoke.

## Gate 1 — Static / Build / Unit

- [ ] `npm install` completed.
- [ ] `npm run lint` passes.
- [ ] `npm run test` passes.
- [ ] `npm run build` passes.
- [ ] `npm run test:contract` passes.

## Gate 2 — Contract Drift

- [ ] `paths.ts` canonical paths exist in OpenAPI.
- [ ] `ActionCommandStatus` is named schema.
- [ ] ErrorCode list matches 26-code master.
- [ ] SSE channels match AsyncAPI.
- [ ] EvidenceKind capability map matches DTO catalog.
- [ ] `correlationId` required in backend-facing AsyncAPI.

## Gate 3 — BFF Route Probes

Anonymous:
- [ ] `/health` or `/healthz` returns 200.
- [ ] `/openapi.json` returns 200.
- [ ] `/bff/events/stream` returns 200 or proper stream open.
- [ ] canonical protected routes return 401/403, not 404.
- [ ] no canonical route returns 404.

Authenticated:
- [ ] `/bff/me` returns MeResponse.
- [ ] entity list endpoints return ListResponse.
- [ ] v5 endpoints return expected DTO envelope.
- [ ] write/precondition tests return expected BffErrorEnvelope.
- [ ] safe write / dry-run endpoints do not create live capital side effects.

## Gate 4 — Browser Hosted E2E

- [ ] Hosted page loads.
- [ ] Hosted JS bundle contains intended BFF URL.
- [ ] Hosted JS bundle does not contain obsolete BFF URL.
- [ ] CORS preflight passes.
- [ ] Browser receives responses for all BFF requests.
- [ ] No failed BFF requests.
- [ ] No CORS console errors.

## Gate 5 — Playwright User Flows

- [ ] F01 Startup / Session Bootstrap.
- [ ] F02 Control Room.
- [ ] F03 Execution Loop.
- [ ] F04 Optimization Loop.
- [ ] F05 Sentinel.
- [ ] F06 HIQ.
- [ ] F07 Entity Registry.
- [ ] F08 Create Write Intent.
- [ ] F09 High-Risk Confirm.
- [ ] F10 Rollback Saga, or marked backend-not-ready.
- [ ] F11 Handoff SLA, or marked backend-not-ready.
- [ ] F12 Approval Governance.
- [ ] F13 Agora.
- [ ] F14 SSE reconnect.
- [ ] F15 strict/hybrid fallback.
- [ ] F16 audit/correlation.

## Gate 6 — A11y / Perf

- [ ] v5 axe smoke critical/serious = 0.
- [ ] overlay focus handling works.
- [ ] reduced motion respected.
- [ ] Control Room and entity list are within performance budget.
- [ ] SSE stream does not trigger unbounded rerender.

## Gate 7 — Release Decision

- [ ] All critical gates pass.
- [ ] Exceptions documented with owner and expiry.
- [ ] Evidence written to `.lovable/audits/`.
- [ ] Backend SHA + frontend SHA + BFF URL recorded.
