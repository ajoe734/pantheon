# MGMT-PERF-IA-006 - Contextual Integration

Owner: Gemini

Reviewer: Claude2

Wave: 2

Repository: `ajoe734/execute-plans`

Dependencies:

- `MGMT-PERF-IA-003`
- `MGMT-PERF-IA-004`
- `MGMT-PERF-IA-005`

## Goal

Connect Cockpit, Persona Fleet, entity details, Human Inbox, and Agora to the
canonical centers without creating new duplicate analysis surfaces.

## Required Work

- Update Cockpit cards and alerts to canonical routes only.
- Update Persona Fleet performance, holdings, ranking, evidence, and review
  actions while preserving persona/runtime/period context.
- Reframe Persona Detail Performance as a compact entity summary with a formal
  Performance Center deep link.
- Keep Strategy Detail contextual performance and add formal attribution link.
- Define honest Capital Pool, Rebalance, and Ranking Policy detail behavior;
  show unavailable state when the live contract is empty.
- Keep Agora Strategy Performance in Trading Room, label its execution scope,
  and add strategy/period-preserving context links.
- Verify Human Inbox return links restore the originating decision context.

## Acceptance

- Operators can enter the canonical workflow from every legitimate context.
- Entity summaries cannot be confused with formal attribution or ranking.
- Agora and Management performance scopes are explicit.
- Empty detail contracts do not render fixture authority.
- Frontend PR is merged and hosted dev evidence is recorded.

## Artifacts

- `execute-plans:src/management`
- `execute-plans:src/agora`
- `execute-plans:src/App.tsx`
- `execute-plans:e2e`
