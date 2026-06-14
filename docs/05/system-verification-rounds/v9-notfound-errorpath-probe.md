# V9 — Not-Found Error-Path Probe (live 500 → deploy-drift)

**Round:** V9 of the 10-round system-verification campaign
**Direction:** error-log scan of deployed services → runtime not-found error-path coverage
**Date:** 2026-06-14
**Branch / PR:** task/verify-v9-notfound-probe

## Non-duplication check

- `probe_bff_authenticated_live.py` already covers happy-path **collection** GETs
  (`/bff/strategies`, `/bff/personas`, …). It does **not** exercise detail-by-id
  routes with a non-existent id. → no overlap.
- `audit_deploy_drift.sh` (V1) detects drift **statically** (image build timestamp
  vs service-path commits); it cannot prove the running container serves fixed code.
- `VERIFY-SYS-CAMPAIGN-R3` (commit `45f2b735`, merged) already fixed the
  persona-league 500→404 **in code** and added an `ErrorCode` reference guard.
  → the code fix and a guard test would duplicate that work, so they were **dropped**.

The distinct, non-duplicate contribution of V9 is therefore: (a) detecting the
**live** 500 via deployed-log scan, (b) completing the **redeploy** that the
stale container had missed, and (c) a **runtime** not-found error-path probe that
complements both the happy-path probe and the static drift audit.

## What the log scan found

Deployed `operator-bff` was emitting `KeyError: 'OBJECT_NOT_FOUND'` (×6) →
HTTP 500 on `GET /bff/persona-league/{id}` for any missing persona. Root cause:
the running container's `main.py` still referenced `ErrorCode.OBJECT_NOT_FOUND`
after the enum member was renamed to `RESOURCE_NOT_FOUND`. The repo (origin/dev)
was already correct — this was **deploy drift**: repo fixed, container stale,
live endpoint still crashing. This is exactly the failure mode V1 warned about
but could only catch statically.

## Fix

1. **Ops (no PR):** rebuilt + redeployed `operator-bff` from synced dev-root
   with the auth stub preserved (`PANTHEON_BFF_AUTH_STUB=true`,
   `PANTHEON_BFF_AUTH_MODE=permissive`). Deployed `OBJECT_NOT_FOUND` count → 0.
2. **Code (this PR):** `scripts/probe_bff_notfound_paths.py` — enumerates every
   `GET /bff/.../{id}` route from the live OpenAPI spec, requests each with a
   non-existent id, and fails on any 5xx. This would have caught the persona-league
   500 before users did.

## Live verification (post-redeploy)

```
GET /bff/persona-league/persona-nonexistent-xyz  ->  404   (was 500)
probe_bff_notfound_paths.py: probed 48 detail-by-id routes
  status distribution: {200: 9, 404: 35, 410: 2, 422: 2}
  OK: every not-found detail path returns a clean client status (no 5xx)
  EXIT=0
```

All 48 detail-by-id routes return clean client statuses; zero 5xx remain.

## Follow-ups (logged, not blocking)

- 9 detail routes return **200** for a non-existent id (empty/default object
  rather than 404). Not a crash, but arguably wrong semantics — candidate for a
  future tightening round, distinct from this probe's 5xx focus.
- Consider wiring this probe into a post-deploy smoke gate so deploy drift that
  reintroduces a stale `ErrorCode` is caught at deploy time, not by users.
