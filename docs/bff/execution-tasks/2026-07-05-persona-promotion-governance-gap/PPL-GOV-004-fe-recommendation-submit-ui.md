# PPL-GOV-004 - Frontend Recommendation Submit UI

Owner: Codex2
Reviewer: Claude
Depends on: PPL-GOV-002, PPL-GOV-003
Type: frontend implementation task

## Purpose

Make Persona League and Quarterly Ranking submit recommendations through BFF
governance instead of pretending a local deterministic inbox id is enough.

## Scope

- Update the ranking-governance adapter to call BFF when real writes are
  enabled.
- Keep local/disabled behavior only when the write gate is explicitly disabled.
- Update Persona League recommendation buttons.
- Update Quarterly Ranking recommendation buttons.
- Navigate or deep-link to the returned promotion review / human inbox item.
- In Persona Fleet and review links, label paper references as paper ledgers and
  reserve capital pool labels for explicit canary/live targets.
- Surface failure and disabled-write states honestly.

## Acceptance

- Button text/state reflects submitting, submitted, failed, and write-disabled.
- Successful BFF submit shows a review link or navigates to Human Inbox detail.
- Paper rows show isolated `paper_ledger_id` and do not display legacy paper
  pool ids as shared capital pools.
- Local-only fallback is visibly disabled/local and cannot be mistaken for a
  real approval queue write.
- Tests cover Persona League and Quarterly Ranking submit flows.
- Tests cover BFF write disabled behavior.

## Validation

```sh
npm test -- src/lib/v5/management/__tests__/pm12.test.ts
npm test -- src/management/pages/oversight/QuarterlyRanking.test.tsx
npm test -- src/management/pages/oversight/PersonaLeague.test.tsx
npm run lint
git diff --check
```
