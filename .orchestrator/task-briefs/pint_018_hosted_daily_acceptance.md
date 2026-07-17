# PINT-018 — Hosted daily product acceptance and closeout

Canonical packet: `docs/product/persona-interaction-daily-strict-operator-delivery-plan.md`
and `docs/bff/execution-tasks/2026-07-17-persona-daily-strict-operator/INDEX.md`.

## Repository and dependencies

- Evidence owner repository: `ajoe734/pantheon`
- Exact paired deploy inputs: merged Pantheon BFF and execute-plans frontend
- Hard dependencies: merged `PINT-013` through `PINT-017`

## Owned scope

- Deploy exact paired SHAs to Pantheon dev and record manifest/digests.
- Authenticated strict desktop/mobile UI acceptance for ask, challenge,
  two-Persona disagreement, Journal reflection, Persona-generated candidate,
  modify, accept-for-review, reject, validation, and eligible reviewer.
- Refresh/relogin/SSE reconnect/idempotent replay/BFF restart readback;
  viewer/unauth/self-approval/provider-outage negatives and zero-side-effect
  proof; checksummed evidence and task-truth reconciliation.

## Acceptance

- No direct-API fallback for claimed UI steps, browser write override,
  permissive stub, fake provider response, retry masking, or skipped case.
- Final manifest remains `operator-live`, real writes true, stub writes false,
  no embedded token, BFF strict, BFF stub false.
- Evidence records exact PRs, merge SHAs, deploy runs, desktop/mobile artifacts,
  restart/readback, authority-negative counts, and distinct review.
- Shared workflows remain active; do not disable them or cancel another task's
  runs. Only after independent review may the correction program close.

## Excluded

No production/live capital or broker/order execution and no unrelated runtime
repair hidden inside evidence collection.
