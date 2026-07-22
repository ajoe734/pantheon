# PPL-GOV-007 - Production Closeout And Dev Publish

Owner: Codex
Reviewer: Claude
Depends on: PPL-GOV-002, PPL-GOV-003, PPL-GOV-004, PPL-GOV-005, PPL-GOV-006
Type: production closeout task

Closeout evidence:

- `docs/04/pantheon_persona_promotion_governance_gap_2026-07-05/archive/PPL-GOV-007-PRODUCTION-CLOSEOUT-2026-07-05.md`

## Purpose

Hold the line until the promotion-governance loop is actually shippable: merged
PRs, deployed dev artifacts, hosted smoke proof, and residual risk accounting.

## Scope

- Verify BFF tests and frontend tests passed locally.
- Confirm Pantheon PR is pushed, reviewed, checks pass, and merged.
- Confirm Execute Plans PR is pushed, reviewed, checks pass, and merged.
- Publish or verify dev deployment for both BFF and frontend as applicable.
- Run hosted smoke:
  recommendation list -> submit -> promotion review detail -> human decision.
- Archive evidence under the gap spec archive directory.

## Acceptance

- Final closeout records PR number, merge commit SHA, and deployed commit SHA
  for each repo.
- Hosted smoke includes request/response evidence for BFF submit and decision.
- Hosted browser smoke includes screenshots or route-ready evidence for
  recommendation submit and Human Inbox decision.
- Residual risks have owners and expiry.
- The closeout states whether dev publish is complete or exactly why it is
  blocked.

## Validation

```sh
git status -sb
python3 -m pytest services/control-plane/bff/tests/test_bff_promotion_reviews.py
python3 -m pytest services/control-plane/bff/tests/test_bff_b5_humangate_commands.py
npm test -- src/lib/v5/management/__tests__/pm12.test.ts
npm test -- src/management/pages/oversight/HumanGateDetail.test.tsx
gh pr status
git diff --check
```
