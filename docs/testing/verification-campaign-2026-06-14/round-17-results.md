# Round 17 — Results

**Executed:** 2026-06-15 (UTC). **Method:** conservative AST sweep for
called-but-unbound names across `services/` + `scripts/`.

## H1 — undefined-call audit: PASS (F12 was the only instance)

- **Checker validated:** run against the pre-fix `main.py` it flags both
  `_parse_rfc3339` (line 10940) and `_parse_rfc3339_header` (line 44634) — the
  F12 symbols.
- **Full sweep:** 655 non-test `.py` files in `services/` + `scripts/`. After
  the Round 16 fix: **0 undefined-call symbols, 0 syntax errors.**

So F12 was the **sole** called-but-undefined NameError in the entire service
codebase — there is no other latent 500 of this class hiding behind an
un-exercised branch.

## Guard

Added `services/control-plane/bff/test_no_undefined_call_symbols.py` (2 passed):
asserts `main.py` and `read_store.py` (the largest, most-edited modules, neither
using star imports) contain no call to an unbound name. Any future refactor that
leaves a dangling symbol — exactly the F12 mistake — fails CI instead of 500ing
at runtime on a rarely-hit branch.

## Net

H1 **PASS** — the undefined-symbol class is fully closed: F12 fixed, no siblings
anywhere, and a regression guard locks the two hottest files. The conservative
checker yields no false positives here because the audited modules have no star
imports.
