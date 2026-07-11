# TJ-E2E-009 - Cross-entry IA Integration

Owner: Claude  
Reviewer: Antigravity  
Wave: 3  
Repository: `ajoe734/execute-plans`  
Dependencies: `TJ-E2E-006`

## Goal

Make Trade Journey the operational backbone while preserving existing domain
pages as drill-downs.

## Required work and acceptance

- Add View Trade Journey(s) to Persona, Strategy, Candidate, Human Inbox,
  Binding, Deployment, Runtime, Trading Pulse, Order, Fill, Incident and Evidence.
- Update sidebar, command palette, breadcrumbs and Cockpit destinations.
- Preserve search/filter/return context and handle one-to-many ambiguity.
- Pass route crawl, deep-link, back-navigation, query and mobile tests.
- Merge to `execute-plans/main` with hosted route evidence.
