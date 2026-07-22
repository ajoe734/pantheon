# MGMT-FLEET-005 - Performance Review Suite

Owner: Codex2
Reviewer: Gemini
Depends on: `MGMT-FLEET-001`
Type: frontend plus payload contract integration

## Purpose

Make ranking, persona league, portfolio, attribution, and cost surfaces feel
like distinct review workflows instead of repeated table shells.

## Scope

- Build active panels for performance review routes that are already backed by
  Management BFF or typed frontend fetchers.
- Use domain-specific columns and summaries for ranking cadence, persona
  league, portfolio book, performance attribution, trading pulse, and cost.
- Avoid downloading full detail aggregates for list views.
- Move heavy evidence into bounded previews or drilldown routes.

## Acceptance

- Each migrated performance route has route-specific columns, copy, and
  degraded state behavior.
- List payloads stay bounded and do not add new audit smells.
- Detail drilldowns are explicit; hidden full-dataset client filtering is not
  introduced.
- Browser probes prove intended BFF endpoint calls.

## Validation

```sh
npm --prefix execute-plans test -- --runInBand --testPathPattern=management
npm --prefix execute-plans run build:management
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --fail-on-new
git diff --check
```
