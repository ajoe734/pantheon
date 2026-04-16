# F-042 Promotion Review — Lovable Change Feedback

Feature ID: `F-042`
Screen: `promotion-review`
Workbench: `governance-review`
Loop status: **followup-required (3 BFF client gaps identified)**

## Review Summary

Pantheon reviewed the returned UI implementation (commit `c34048e2`). While the UI claims `no_open_gaps`, the backend review identified 3 critical integration gaps in the BFF client and types that must be resolved before the feature can be considered integrated.

## Identified Gaps

1. **Missing Authorization Header**: `src/lib/bffClient.ts` is not sending the `Authorization: Bearer <token>` header, causing all stateful requests to fail in a real environment.
2. **Error Envelope Mismatch**: `src/lib/bffClient.ts` is parsing the error envelope incorrectly. It expects a single `error` field, but the Pantheon BFF contract specifies an `errors` array.
3. **Status Typing Drift**: `src/pages/promotion/types.ts` uses `unavailable` for surface status, while the backend contract specifies `error`.

## Decision

The backend contract is fixed and authoritative. The UI implementation must be updated to align with the existing `F-042-promotion-review.md` spec.

## Follow-up Action

- A `bff-gap` handoff has been filed: `.coordination/requests/F-042-bff-gap.yaml`.
- The UI lane should perform another implementation cycle on the restored front-repo checkout to fix these 3 items.
- Once fixed, emit a new `ui-done` or `frontend-feedback` payload.
