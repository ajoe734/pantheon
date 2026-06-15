# Round 30 — Python footgun audit (mutable defaults, prod asserts)

**Date:** 2026-06-15
**Depth/breadth step:** Static audit for two classic Python bug classes:
mutable default arguments (shared state across calls) and `assert`-based
validation in production code (stripped under `python -O`).

## Hypotheses

- H1: no function uses a mutable literal default (`x=[]`, `x={}`, `x=set()`).
- H2: production service code does not rely on `assert` for security/input
  validation, or the affected services don't run under `-O`.

## Method

1. AST-scan all non-test `.py`; flag defaults that are list/dict/set literals.
2. AST-scan for `assert` statements in non-test service/script code; for any in
   a real service, read the assert and check it is not security/input validation
   and whether the service runs `-O`.

## Pass criteria

- H1: zero mutable-literal defaults.
- H2: no security-relevant assert that would be stripped under `-O` in a service
  that runs `-O`.
