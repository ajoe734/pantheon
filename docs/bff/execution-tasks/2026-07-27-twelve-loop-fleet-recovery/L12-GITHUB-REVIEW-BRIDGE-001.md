# L12-GITHUB-REVIEW-BRIDGE-001 — Bind fleet reviewer decisions to GitHub review gates

Owner: Codex
Reviewer: Codex2
Parallel group: wave-0-control

Repair the #4269 class of bug: activity log can show an independent approval
while GitHub still reports `REVIEW_REQUIRED` and `latestReviews` is empty.

Acceptance:

- A governed reviewer approval for a PR-backed task either submits a GitHub
  review or records an explicit branch-policy-recognized alternative.
- The supervisor/dashboard must not display internal activity approval as PR
  completion while GitHub review is still missing.
- Include tests for an internal approval with GitHub review gate still blocked.

