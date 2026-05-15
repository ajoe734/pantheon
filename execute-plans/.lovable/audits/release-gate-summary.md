<!-- pantheon-release-gate-summary -->
# Pantheon FE-BFF Release Gate Summary
Generated: 2026-05-13T18:54:37.186Z
Overall: FAIL
| Gate | Status | Open checks | Owner | Evidence |
|---|---|---:|---|---|
| Gate 0 | FAIL | 6 | Codex | [.](.) |
| Gate 1 | MISSING | 5 | Gemini | [.lovable/audits/npm-ci.log](.lovable/audits/npm-ci.log) |
| Gate 2 | MISSING | 6 | Codex | [.lovable/audits/contract-drift.log](.lovable/audits/contract-drift.log) |
| Gate 3 | MISSING | 10 | Codex | [.lovable/audits/bff-route-probe-anonymous.log](.lovable/audits/bff-route-probe-anonymous.log) |
| Gate 4 | MISSING | 7 | Gemini | [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log) |
| Gate 5 | MISSING | 16 | Codex | [.lovable/audits/release-gate-summary.json](.lovable/audits/release-gate-summary.json) |
| Gate 6 | MISSING | 5 | Codex2 | [.lovable/audits/release-gate-summary.json](.lovable/audits/release-gate-summary.json) |
| Gate 7 | FAIL | 3 | Codex | [.lovable/audits/release-gate-summary.json](.lovable/audits/release-gate-summary.json) |
## Gate 0 - Preconditions

- [ ] `execute-plans` branch is clean and points to release candidate SHA. - FAIL; owner: Codex; evidence: [.](.); note: tracked worktree changes present
- [ ] `pantheon` backend/BFF SHA is recorded. - MISSING; owner: Codex; evidence: [.](.); note: set PANTHEON_BFF_SHA or PANTHEON_BACKEND_SHA
- [ ] `PANTHEON_FE_BASE_URL` points to intended Lovable deployment. - MISSING; owner: Codex; evidence: [.](.); note: missing
- [ ] `PANTHEON_BFF_BASE_URL` points to intended BFF. - MISSING; owner: Codex; evidence: [.](.); note: missing
- [ ] No obsolete BFF URL appears in hosted JS bundle. - MISSING; owner: Gemini; evidence: [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log); note: hosted browser probe markdown evidence missing
- [ ] Auth token or test OIDC path available for authenticated smoke. - MISSING; owner: Codex; evidence: [.](.); note: PANTHEON_BFF_SMOKE_BEARER_TOKEN or test OIDC path missing
## Gate 1 - Static / Build / Unit

- [ ] `npm ci` completed. - MISSING; owner: Gemini; evidence: [.lovable/audits/npm-ci.log](.lovable/audits/npm-ci.log); note: step outcome missing
- [ ] `npm run lint` passes. - MISSING; owner: Gemini; evidence: [.lovable/audits/npm-run-lint.log](.lovable/audits/npm-run-lint.log); note: step outcome missing
- [ ] `npm run test` passes. - MISSING; owner: Gemini; evidence: [.lovable/audits/npm-run-test.log](.lovable/audits/npm-run-test.log); note: step outcome missing
- [ ] `npm run build` passes. - MISSING; owner: Gemini; evidence: [.lovable/audits/npm-run-build.log](.lovable/audits/npm-run-build.log); note: step outcome missing
- [ ] `npm run test:contract` passes. - MISSING; owner: Codex; evidence: [.lovable/audits/contract-drift.log](.lovable/audits/contract-drift.log); note: step outcome missing
## Gate 2 - Contract Drift

- [ ] `paths.ts` canonical paths exist in OpenAPI. - MISSING; owner: Codex; evidence: [.lovable/audits/contract-drift.log](.lovable/audits/contract-drift.log); note: contract drift outcome missing
- [ ] `ActionCommandStatus` is named schema. - MISSING; owner: Codex; evidence: [.lovable/audits/contract-drift.log](.lovable/audits/contract-drift.log); note: contract drift outcome missing
- [ ] ErrorCode list matches 26-code master. - MISSING; owner: Codex; evidence: [.lovable/audits/contract-drift.log](.lovable/audits/contract-drift.log); note: contract drift outcome missing
- [ ] SSE channels match AsyncAPI. - MISSING; owner: Codex; evidence: [.lovable/audits/contract-drift.log](.lovable/audits/contract-drift.log); note: contract drift outcome missing
- [ ] EvidenceKind capability map matches DTO catalog. - MISSING; owner: Codex; evidence: [.lovable/audits/contract-drift.log](.lovable/audits/contract-drift.log); note: contract drift outcome missing
- [ ] `correlationId` required in backend-facing AsyncAPI. - MISSING; owner: Codex; evidence: [.lovable/audits/contract-drift.log](.lovable/audits/contract-drift.log); note: contract drift outcome missing
## Gate 3 - BFF Route Probes

- [ ] Anonymous: `/health` or `/healthz` returns 200. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-route-probe-anonymous.log](.lovable/audits/bff-route-probe-anonymous.log); note: anonymous route probe markdown evidence missing
- [ ] Anonymous: `/openapi.json` returns 200. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-route-probe-anonymous.log](.lovable/audits/bff-route-probe-anonymous.log); note: anonymous route probe markdown evidence missing
- [ ] Anonymous: `/bff/events/stream` returns 200 or proper stream open. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-route-probe-anonymous.log](.lovable/audits/bff-route-probe-anonymous.log); note: anonymous route probe markdown evidence missing
- [ ] Anonymous: canonical protected routes return 401/403, not 404. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-route-probe-anonymous.log](.lovable/audits/bff-route-probe-anonymous.log); note: anonymous route probe markdown evidence missing
- [ ] Anonymous: no canonical route returns 404. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-route-probe-anonymous.log](.lovable/audits/bff-route-probe-anonymous.log); note: anonymous route probe markdown evidence missing
- [ ] Authenticated: `/bff/me` returns MeResponse. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-authenticated-live-smoke.log](.lovable/audits/bff-authenticated-live-smoke.log); note: authenticated smoke markdown evidence missing
- [ ] Authenticated: entity list endpoints return ListResponse. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-authenticated-live-smoke.log](.lovable/audits/bff-authenticated-live-smoke.log); note: authenticated smoke markdown evidence missing
- [ ] Authenticated: v5 endpoints return expected DTO envelope. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-authenticated-live-smoke.log](.lovable/audits/bff-authenticated-live-smoke.log); note: authenticated smoke markdown evidence missing
- [ ] Authenticated: write/precondition tests return expected BffErrorEnvelope. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-authenticated-live-smoke.log](.lovable/audits/bff-authenticated-live-smoke.log); note: authenticated smoke markdown evidence missing
- [ ] Authenticated: safe write / dry-run endpoints do not create live capital side effects. - MISSING; owner: Codex; evidence: [.lovable/audits/bff-authenticated-live-smoke.log](.lovable/audits/bff-authenticated-live-smoke.log); note: authenticated smoke markdown evidence missing
## Gate 4 - Browser Hosted E2E

- [ ] Hosted page loads. - MISSING; owner: Gemini; evidence: [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log); note: hosted browser probe markdown evidence missing
- [ ] Hosted JS bundle contains intended BFF URL. - MISSING; owner: Gemini; evidence: [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log); note: hosted browser probe markdown evidence missing
- [ ] Hosted JS bundle does not contain obsolete BFF URL. - MISSING; owner: Gemini; evidence: [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log); note: hosted browser probe markdown evidence missing
- [ ] CORS preflight passes. - MISSING; owner: Gemini; evidence: [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log); note: hosted browser probe markdown evidence missing
- [ ] Browser receives responses for all BFF requests. - MISSING; owner: Gemini; evidence: [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log); note: hosted browser probe markdown evidence missing
- [ ] No failed BFF requests. - MISSING; owner: Gemini; evidence: [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log); note: hosted browser probe markdown evidence missing
- [ ] No CORS console errors. - MISSING; owner: Gemini; evidence: [.lovable/audits/hosted-browser-bff-probe.log](.lovable/audits/hosted-browser-bff-probe.log); note: hosted browser probe markdown evidence missing
## Gate 5 - Playwright User Flows

- [ ] F01 Startup / Session Bootstrap. - MISSING; owner: Codex; evidence: missing evidence; note: F01 not found in Playwright JSON report
- [ ] F02 Control Room. - MISSING; owner: Codex; evidence: missing evidence; note: F02 not found in Playwright JSON report
- [ ] F03 Execution Loop. - MISSING; owner: Codex; evidence: missing evidence; note: F03 not found in Playwright JSON report
- [ ] F04 Optimization Loop. - MISSING; owner: Codex; evidence: missing evidence; note: F04 not found in Playwright JSON report
- [ ] F05 Sentinel. - MISSING; owner: Codex; evidence: missing evidence; note: F05 not found in Playwright JSON report
- [ ] F06 HIQ. - MISSING; owner: Codex; evidence: missing evidence; note: F06 not found in Playwright JSON report
- [ ] F07 Entity Registry. - MISSING; owner: Codex; evidence: missing evidence; note: F07 not found in Playwright JSON report
- [ ] F08 Create Write Intent. - MISSING; owner: Codex; evidence: missing evidence; note: F08 not found in Playwright JSON report
- [ ] F09 High-Risk Confirm. - MISSING; owner: Codex; evidence: missing evidence; note: F09 not found in Playwright JSON report
- [ ] F10 Rollback Saga, or marked backend-not-ready. - MISSING; owner: Codex; evidence: missing evidence; note: F10 not found in Playwright JSON report
- [ ] F11 Handoff SLA, or marked backend-not-ready. - MISSING; owner: Codex; evidence: missing evidence; note: F11 not found in Playwright JSON report
- [ ] F12 Approval Governance. - MISSING; owner: Codex; evidence: missing evidence; note: F12 not found in Playwright JSON report
- [ ] F13 Agora. - MISSING; owner: Codex; evidence: missing evidence; note: F13 not found in Playwright JSON report
- [ ] F14 SSE reconnect. - MISSING; owner: Codex; evidence: missing evidence; note: F14 not found in Playwright JSON report
- [ ] F15 strict/hybrid fallback. - MISSING; owner: Codex; evidence: missing evidence; note: F15 not found in Playwright JSON report
- [ ] F16 audit/correlation. - MISSING; owner: Codex; evidence: missing evidence; note: F16 not found in Playwright JSON report
## Gate 6 - A11y / Perf

- [ ] v5 axe smoke critical/serious = 0. - MISSING; owner: Codex2; evidence: missing evidence; note: axe/a11y report missing or last run failed
- [ ] overlay focus handling works. - MISSING; owner: Codex2; evidence: missing evidence; note: focus evidence missing
- [ ] reduced motion respected. - MISSING; owner: Codex2; evidence: missing evidence; note: reduced-motion evidence missing
- [ ] Control Room and entity list are within performance budget. - MISSING; owner: Codex2; evidence: missing evidence; note: performance evidence missing
- [ ] SSE stream does not trigger unbounded rerender. - MISSING; owner: Codex2; evidence: missing evidence; note: SSE rerender evidence missing
## Gate 7 - Release Decision

- [ ] All critical gates pass. - FAIL; owner: Codex; evidence: [.lovable/audits/release-gate-summary.json](.lovable/audits/release-gate-summary.json); note: 55 failing or missing check(s)
- [ ] Exceptions documented with owner and expiry. - FAIL; owner: Codex; evidence: [.lovable/audits/release-gate-summary.json](.lovable/audits/release-gate-summary.json); note: exceptions missing
- [x] Evidence written to `.lovable/audits/`. - PASS; evidence: [.lovable/audits](.lovable/audits); note: 2 audit file(s) found
- [ ] Backend SHA + frontend SHA + BFF URL recorded. - MISSING; owner: Codex; evidence: [.](.); note: one or more release identifiers missing