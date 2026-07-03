# MGMT-FLEET-003 - Decision Workbench

Owner: Gemini
Reviewer: Claude2
Depends on: `MGMT-FLEET-001`
Type: frontend workflow integration

## Purpose

Cluster the repeated decision and operations queues into one coherent operator
workbench while preserving canonical deep links.

## Scope

- Recluster human inbox, interventions, approvals, sentinel, governance,
  incidents, alerts, and jobs into a shared Management workbench frame.
- Keep existing canonical routes resolving, but align filters, status labels,
  severity, owner, evidence links, and next-action affordances.
- Add route-specific panels instead of another generic repeated list page.
- Prefer tabs, cross-links, and contextual drilldowns over more first-level
  navigation.

## Acceptance

- Existing bookmarked decision and operations routes still resolve.
- Each queue exposes domain-specific columns and empty/degraded state copy.
- Actions are either receipt-backed or visibly disabled as non-production.
- Route and browser probes show no blank route, nav failure, or mock truth
  presented as live data.

## Validation

```sh
npm --prefix execute-plans test -- --runInBand --testPathPattern=management
npm --prefix execute-plans run build:management
git diff --check
```
