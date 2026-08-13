# Quality backlog after open-PR triage — 2026-08-13

This document preserves seven useful engineering ideas without keeping seven
stale implementation branches open. It is not a canonical task list and does
not authorize automatic dispatch.

- `#2550`: add archive backfill only if a current retention audit demonstrates
  a real gap; do not restore dated archive machinery by default.
- `#1722`: keep producer-chain verification as a behavioral E2E property. The
  current verifier is the implementation owner; the old documentation PR is
  not.
- `#1635`: solve submodule object sharing when worktrees are created. Do not
  add a recurring repack job that can race active workers.
- `#1552`: use a maintained OpenAPI linter if schema drift becomes a measured
  problem; do not merge the old bespoke audit script.
- `#1551`: enforce dependency policy through declared lockfiles and supported
  update tooling rather than a point-in-time dependency list.
- `#1548`: use a maintained secret scanner or repository secret-scanning
  service. A grep-only script is not a sufficient security boundary.
- `#1544`: evolve CI toward a reproducible required matrix. Do not accept
  `continue-on-error` or install-failure fallbacks as proof of verification.
