# Round 28 — Complete the fleet audit (package-import services)

**Date:** 2026-06-15
**Depth/breadth step:** Rounds 21/22 audited the importable services but
deferred 5 that the file-import harness could not load (`search`,
`consultation`, `memory`, `source_ingestion`, `registry` — package-relative
imports). Round 28 imports them as **packages** and closes the coverage gap.

## Hypotheses

- H1: no route shadowing / duplicate registration in the deferred services.
- H2: no input-driven 500 (baseline GET / malformed body) in them.

## Method

1. `importlib.import_module("services.<x>.main")` with the repo root on
   `PYTHONPATH` so absolute and package imports resolve.
2. Walk routes (shadow + dup audit); drive `TestClient` with bad bodies and
   query values; flag `== 500`.

## Pass criteria

- H1/H2: clean, completing whole-fleet coverage; any defect fixed via the dev
  workflow.
