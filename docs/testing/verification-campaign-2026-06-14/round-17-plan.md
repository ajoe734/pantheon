# Round 17 — Static undefined-symbol audit (latent NameError 500s)

**Date:** 2026-06-15
**Depth/breadth step:** Round 16 found F12 — a function called but defined
nowhere (`NameError` → 500). Round 17 generalizes: does the **whole codebase**
contain other called-but-undefined symbols that would 500 when their branch is
hit? This is a static, AST-level audit.

## Hypotheses

- H1: no `.py` file in `services/` or `scripts/` calls a name that is bound
  nowhere in its module (no def/class/import/assignment/arg/target) and is not a
  builtin.

## Method

1. AST-parse every non-test `.py` under `services/` and `scripts/` (655 files).
2. For each, collect all bound names (conservative: any binding form) plus
   builtins; report any `Call` whose `func` is a bare `Name` not in that set.
3. Validate the checker against the pre-fix `main.py` (must flag F12).

## Pass criteria

- H1: zero undefined-call symbols across the codebase (after the F12 fix).
- Add a regression guard on the largest, most-edited modules (`main.py`,
  `read_store.py`; neither uses star imports, so the conservative check is
  reliable).
