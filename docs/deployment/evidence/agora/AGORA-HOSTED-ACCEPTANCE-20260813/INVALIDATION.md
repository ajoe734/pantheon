# Invalidation of AGORA-HOSTED-ACCEPTANCE-20260813

## Decision

The hosted acceptance emitted by PR #4935 is invalid and must not be used to
close Agora deployment or integration work.

## Why it was invalid

The original verifier accepted `simulated-hosted` as a passing mode and did
not change behavior when `--mode hosted` was selected:

- frontend and backend SHAs were copied from constants instead of the served
  `deployment.json` and `/bff/version`;
- `/healthz`, `/livez`, and `/readyz` were represented by a local dictionary;
- the 14-stage journey ran against in-process stores, not hosted services;
- frontend routes and desktop/mobile results were hard-coded as passed;
- security controls were local booleans rather than hosted negative probes;
- restart and readback were simulated without restarting a service; and
- rollback safety was asserted without a failure drill.

## Contradicting live observation

A read-only observation at `2026-08-15T08:42:12Z` showed three different
identities:

- served frontend manifest FE SHA:
  `6a8d2d9b4f725056735eefd7165ef47b52cda53d`;
- served frontend manifest BFF SHA:
  `be956c07aca889043ef301389412b6744452f20b`; and
- public BFF `/bff/version` SHA:
  `ed4a14348e5b167a55801b98d2a2eeca218ee726`.

The invalid artifact instead claimed FE
`0a1df3300d09bc98b3c45d9558839e217b2c2ff4` and BFF
`b146968e615bdb5e6dcd07997265a5df3db0388f`.

## Replacement acceptance contract

`AGORA-HOSTED-REAL-ACCEPTANCE-20260815` may pass only when all of these are
fresh and bound to the same FE/BFF exact pair:

1. direct public manifest, BFF version, health, liveness, and readiness reads;
2. an authenticated 14-stage hosted service-journey artifact;
3. desktop and mobile Playwright artifacts from `execute-plans`;
4. hosted negative controls proving tenant and authority boundaries;
5. an actual BFF restart with before/after instance identity and durable
   resource readback; and
6. gate-before-switch plus failed-candidate rollback evidence.

Each non-public artifact must identify a successful exact-head GitHub Actions
run. Missing, stale, mismatched, in-process, or simulated evidence fails
closed.
