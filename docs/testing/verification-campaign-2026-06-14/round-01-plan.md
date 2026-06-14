# Round 1 — Live dev stack reachability & control-plane health

**Date:** 2026-06-14
**Scope:** The deployed dev environment's control plane, as observed through
the operator BFF. Verify the stack is reachable, healthy, fail-closed, and that
its read-model surfaces report internally-consistent health.

## Why this round (and why it's not a duplicate)

The 2026-04-28 `runtime-verification-*` docs verified reconciliation and
consultation/operator residuals on the *pre-migration* stack. Since then the
system moved GCP projects/IPs (2026-05-30) and added the OODA control-room
surfaces. No existing doc verifies the **live 06-14 dev control plane health and
surface self-consistency** against the new sslip URLs. That is this round.

## Hypotheses to test

- H1: Dev FE and BFF are reachable over HTTPS and report healthy.
- H2: BFF auth gate is enforced (unauth → 401) and dev stub auth admits.
- H3: The BFF contract surface (`openapi.json`) loads and is non-trivial.
- H4: Control-plane read surfaces (`/bff/v5/control-room`, `/bff/v5/loop-runs`)
  respond and report a coherent OODA posture.
- H5: Safety posture is fail-closed — no live-capital side effects on paper.
- H6 (consistency): Every surface's reported `status` is internally consistent
  with the data it actually has (no false-green when a source is missing).

## Method

1. `curl` health/readyz on FE and BFF; record HTTP codes.
2. `curl` a protected route with and without stub auth; record 401 vs 200.
3. Fetch `openapi.json`; count paths; record title/version.
4. Fetch `/bff/v5/control-room` and `/bff/v5/loop-runs`; inspect
   `ooda_status`, `loops`, `sentinel`, `interventions`, and `meta.surfaces`.
5. Inspect the BFF code path that composes `ooda_control_room_status` for
   status-derivation correctness.

## Pass criteria

- H1–H5 pass when the probes return the expected codes/shape and the safety
  posture is fail-closed.
- H6 passes only if no surface reports `status: ok` while its backing source is
  `missing`/`unavailable`.

## Out of scope (fleet-owned, recorded as findings not fixed here)

- Empty OODA loop (0 packets / 0 trades) — the known upstream build-gap
  (no signal producer, no market data, no real artifacts). Verified, not fixed.
