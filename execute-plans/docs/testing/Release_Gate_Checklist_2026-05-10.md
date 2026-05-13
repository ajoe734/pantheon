# Pantheon FE-BFF Release Gate Checklist

Blueprint date: 2026-05-10
Auto-ticked by: `scripts/aggregate-release-gate.mjs` on each CI run.

> Items tagged `<!-- release-gate:N -->` are ticked automatically when Gate N passes.
> Items without a tag must be verified and ticked manually before sign-off.

## Release Identifiers

- [ ] Frontend SHA (`PANTHEON_FRONTEND_SHA`) recorded
- [ ] Backend / BFF SHA (`PANTHEON_BFF_SHA`) recorded
- [ ] BFF base URL (`PANTHEON_BFF_BASE_URL`) configured
- [ ] Release evidence bundle (`release-evidence-<sha>.zip`) uploaded as CI artifact

## Gate Results

- [ ] Gate 0 — Preconditions: branch clean, SHAs recorded, no obsolete BFF URL in bundle <!-- release-gate:0 -->
- [ ] Gate 1 — Static / Build / Unit: npm ci, lint, test, build all pass <!-- release-gate:1 -->
- [ ] Gate 2 — Contract Drift: paths.ts, ActionCommandStatus, ErrorCode list, SSE channels, EvidenceKind map all matched <!-- release-gate:2 -->
- [ ] Gate 3 — BFF Route Probes: anonymous + authenticated routes respond as expected <!-- release-gate:3 -->
- [ ] Gate 4 — Browser Hosted E2E: hosted page loads, intended BFF URL present, no obsolete URL, no CORS errors <!-- release-gate:4 -->
- [ ] Gate 5 — Playwright User Flows: F01–F16 all pass <!-- release-gate:5 -->
- [ ] Gate 6 — A11y / Perf: axe critical/serious = 0, focus and motion correct, within performance budget <!-- release-gate:6 -->
- [ ] Gate 7 — Release Decision: all critical gates pass, evidence written, SHAs and URL recorded <!-- release-gate:7 -->

## Manual Sign-off

- [ ] Release gate run URL reviewed by approver
- [ ] Exceptions documented with owner and expiry (if any gate fails)
- [ ] Approved for deployment
