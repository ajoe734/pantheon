# Loop Product-Level Remediation — Sequencing Addendum (2026-07-16)

Status: authoritative amendment to `archive/LOOP_PRODUCT_LEVEL_REMEDIATION_PLAN_2026-07-13.md`.
This document does not rewrite the original plan; it re-sequences its gates. Where
this addendum and the original plan conflict on ordering, this addendum wins.

## 1. Why this amendment exists

The original plan sequences its gates in this order:

| gate | content |
|---|---|
| G1 release and security | strict scoped dev auth, no browser bearer, viewer/privileged-negative matrix, MFA, two-person |
| G2 real execution | default deployment owner, durable trigger, canonical side effect, terminal readback for every loop |
| … | … |

That is, **G1 (security) is a foundational gate that every functional task must
pass before it counts as done.** In practice every task's acceptance was written
as "prove this under strict auth", which made the strict-auth cutover an upstream
dependency of the entire program.

This ordering is inverted for the current state of the system:

- `PANTHEON_LIVE_BROKER_ENABLED` is hard-`false` (docker-compose.yml). Live broker
  authority is fail-closed off.
- There are **no broker / exchange / live-trading credentials** provisioned
  anywhere (checked: repo secrets, dev environment secrets). The system cannot
  place a real-money order.
- The loop has not yet been shown to produce paper trades end to end under G2.

Proving RBAC / MFA / two-person governance (G1) on a system that cannot yet
execute a single trade (G2) spends the critical path on the security of an empty
vault. It also actively harmed the fleet: on 2026-07-16 workers auto-deployed a
strict cutover to dev, which rejected the console's baked dev bearer and broke the
management console, and spawned deploy-workflow-disable contention across the
fleet — all to advance a governance gate ahead of the functionality it governs.

## 2. Amended sequencing

The gate order is amended as follows:

1. **G2 real execution is the new foundational wave.** The loop must demonstrably
   produce paper trades end to end (signal → order → fill → telemetry → loop-run
   projection) before any security/governance gate is treated as blocking.
2. **G1 (release and security), the governance portions of G5, and G6 evidence
   are deferred to a Hardening Wave** that opens only after G2 is met — i.e. after
   the loop is shown to produce paper trades.
3. Until the Hardening Wave opens, **dev runs in permissive auth** (`AUTH_STUB=true`,
   `AUTH_MODE=permissive`). Functional tasks are accepted under a dev-token /
   permissive posture. Strict-auth, no-browser-bearer, MFA, two-person, and the
   negative-identity matrix are **explicitly deferred**, not cancelled.
4. No task may be dispatched that flips dev to strict, removes the browser dev
   bearer, or requires strict-auth evidence, until the Hardening Wave is open.

## 3. Effect on the current board

- Functional loop tasks (real execution, telemetry/loop-run projection, evolution
  dispatcher, persona provisioning, trade-journey backend, BFF health) are to be
  worked and accepted under permissive dev auth. Their strict-auth acceptance
  clauses are deferred to the Hardening Wave.
- Governance / proof / verifier / closeout tasks whose sole purpose is to prove
  strict auth, MFA, two-person, or the negative matrix are **parked** until the
  Hardening Wave opens. They are not deleted; they move to the deferred wave.
- `LOOP-PROD-FE-001` (remove browser bearer / strict cutover FE) stays parked; it
  is Hardening-Wave work.

## 4. What opens the Hardening Wave

The Hardening Wave opens when there is machine evidence that the dev loop produced
at least one paper trade end to end under G2 (a real signal producing an order,
fill, telemetry event, and a projected loop-run). At that point strict auth and
the full governance matrix become worth proving, because there is finally
something real to govern.

## 5. Rationale for using an addendum

The 2026-07-13 plan is an archived artifact with its own commit provenance. It is
amended, not overwritten, so the original sequencing decision — and the reason it
was corrected — both remain on the record for future planning and SA passes.
