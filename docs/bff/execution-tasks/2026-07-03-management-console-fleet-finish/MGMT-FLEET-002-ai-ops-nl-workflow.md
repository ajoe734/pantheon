# MGMT-FLEET-002 - Management AI/NL Workflow

Owner: Claude
Reviewer: Codex
Depends on: `MGMT-FLEET-001`
Type: frontend plus BFF integration

## Purpose

Turn Management AI/NL from typed backend capability into a visible, routable,
honest operator workflow.

## Scope

- Implement active Management route panels for `/management/nl/ask` and
  `/management/ai/conversations`.
- Use existing Management assistant client paths for `/bff/management/nl/ask`
  and `/bff/management/ai/conversations`.
- Show conversation list, ask form, submission state, accepted job or
  conversation metadata, degraded/error envelopes, and audit/evidence links
  when available.
- Decide whether `/bff/management/nl/ask/stream`,
  `/bff/management/ai/audit`, and AI attachments are in this PR or explicitly
  deferred with linked follow-up tasks.
- Remove any local-only success state from enabled AI actions.

## Acceptance

- Direct visits to `/management/nl/ask` and
  `/management/ai/conversations` render route-specific active panels.
- The AI ask action returns durable metadata or a clear degraded state; it does
  not pretend a local toast is production success.
- Empty, loading, auth failure, backend degraded, and success states are tested.
- Hosted or preview browser evidence proves the intended BFF endpoints are
  called.
- No new management list-contract audit smell is introduced.

## Validation

```sh
npm --prefix execute-plans test -- --runInBand --testPathPattern=management
npm --prefix execute-plans run build:management
python3 scripts/audit_management_list_contract.py \
  --baseline docs/architecture/management-list-contract-baseline.json \
  --fail-on-new
git diff --check
```
