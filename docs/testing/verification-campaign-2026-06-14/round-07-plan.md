# Round 7 — Test-suite import/collection & execution health

**Date:** 2026-06-14
**Depth/breadth step:** Rounds 1–6 verified the live BFF surface. Round 7 opens
a **new plane** — the repository's own test suite — and verifies the service
layer is structurally sound: every module imports/collects, and the fast
service suites actually pass. The full `pytest --co` over `services` exceeds a
2-minute collection budget (volume, not breakage); this round confirms that is
volume by collecting/executing per service.

## Why this round (not a duplicate)

`SA-18` and CI-verification docs describe CI design, not a point-in-time health
snapshot of the suite across services. No prior doc records which service
suites are green on 2026-06-14.

## Hypotheses

- H1: no service module has a collection/import error (broken module).
- H2: the fast, logic-heavy service suites pass.

## Method

1. `pytest --co` per service dir + `scripts`; flag any ImportError/collection
   error.
2. `pytest` (execute) each service dir that runs within a 150s budget; record
   pass/fail counts.

## Pass criteria

- H1: zero collection/import errors across all service dirs + `scripts`.
- H2: all executed service suites green (failures root-caused + fixed via the
  dev workflow).
