# PPLG-008 - End-To-End Release Gate And Fleet Closeout

Priority: P0

Area: Verification, supervisor closeout

Depends on: `PPLG-002`, `PPLG-003`, `PPLG-004`, `PPLG-005`, `PPLG-006`, `PPLG-007`

## Goal

Prove the complete paper-first persona lifecycle and produce closeout evidence
for fleet development.

## Required Work

- Run end-to-end create paper persona flow.
- Prove paper evaluation and eligibility.
- Prove promotion recommendation requires human approval.
- Prove canary and live allocation changes require human decisions.
- Prove quarterly ranking generates proposal but does not auto-rebalance.
- Prove automatic risk guardrails act immediately and create review evidence.
- Prove no live broker authority exists in paper flow.
- Produce closeout packet with PRs, commits, tests, screenshots or API evidence,
  and residual risk.

## Acceptance Criteria

- One E2E proves create -> paper runtime -> evaluation -> eligible.
- One E2E proves paper recommendation cannot start canary without approval.
- One E2E proves approved canary can start within allocation caps.
- One E2E proves quarterly proposal cannot execute without approval.
- One E2E proves risk-off can interrupt canary/live automatically and requires
  review to resume.
- Closeout packet maps every gap spec requirement to evidence.

## Artifacts

- `tests/e2e/*persona_paper_live*`
- `docs/deployment/evidence/*persona-paper-live*`
- `.orchestrator/task-briefs/*`
- `dashboard-bundle.json`
