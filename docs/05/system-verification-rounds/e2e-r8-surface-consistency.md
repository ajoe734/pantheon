# E2E-R8 — Operator read-surface cross-consistency

**Round:** E2E-R8 of the e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r8-surface-consistency
**Business flow:** the operator console reads the same fleet through several BFF
surfaces; they must agree or the operator's view of "what is running / healthy"
is wrong.

## Verification program

`scripts/verify_e2e_surface_consistency.py` (+ unit test), wired into
`run-acceptance.sh` full mode as `e2e-surface-consistency-verifier`. Checks:
- runtime ids agree between `/bff/runtimes` and `/api/v1/operator/runtime-state`;
- every persona bound to an ACTIVE runtime-binding appears in
  `/bff/v5/execution/persona-health`.

## Live result (dev, 2026-06-15)

```
runtime ids: /bff/runtimes=16  operator/runtime-state=16  (match=True)
personas: persona-health=15  active-binding-personas=15  (active∖health=4)
FAIL: 4 personas bound to ACTIVE runtimes are absent from persona-health
      e.g. persona-20260531-1715d8d2, persona-20260528-f4650c96, …
```

## Finding

- **Runtime ids are consistent** across the two runtime surfaces — good.
- **persona-health is disconnected from the running fleet.** The operator
  persona-health view shows curated personas (persona-crypto, persona-tw-equity,
  persona-us-equity, …) that have **no** active runtime binding, while **4
  personas that ARE bound to active runtimes are missing** from it. An operator
  reading persona-health sees personas that aren't running and misses ones that
  are — a misleading operational view.

**Root cause:** persona-health is populated from a curated persona set, not
derived from the active runtime-binding fleet (the rescue personas). Same
rescue-placeholder lineage as R1/R3/R5 — the live fleet's personas were never
reflected into the curated persona-health surface.

## Disposition

- **Shipped (code/CI):** the cross-surface consistency verifier + logic test + CI
  gate, so runtime/persona surface divergence is caught going forward (currently
  FAILs on the 4 unreflected active personas).
- **Flagged (upstream build):** persona-health should include (or derive from) the
  personas actually bound to active runtimes, not only the curated set.

## Next round

E2E-R9: evolution / intervention flow integrity, then E2E-R10 consolidation.
