# Round 29 — Error-handling discipline audit

**Date:** 2026-06-15
**Depth/breadth step:** A code-quality/reliability round. Bare `except:` and
broad silent-swallow handlers can hide real failures.

## Hypotheses

- H1: no bare `except:` (which also swallows `KeyboardInterrupt`/`SystemExit`).
- H2: broad `except Exception: pass` sites are intentional graceful-degradation,
  not hidden critical-path failures.

## Method

1. AST-scan all non-test `.py` in `services/` + `scripts/` for `ExceptHandler`
   with no type (bare except).
2. Scan for broad (`Exception`/`BaseException`) handlers whose body is a silent
   `pass` with no log/raise; spot-read their `try` contexts.

## Pass criteria

- H1: zero bare excepts.
- H2: broad silent swallows are explained (optional-body parse, best-effort
  reads); any that hides a critical failure is fixed via the dev workflow.
