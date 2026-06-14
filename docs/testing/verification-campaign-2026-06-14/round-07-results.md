# Round 7 — Results

**Executed:** 2026-06-14 (UTC). **Method:** per-service `pytest` collection +
execution from the campaign worktree (off `origin/dev`).

## Collection/import health (H1: PASS)

No ImportError or collection error in any service dir or `scripts`. The lines
flagged by a naive `grep error` were test *names* containing "error", not
failures. `scripts` collected 348 tests in 13s. The full-suite `--co` timeout
seen earlier is **volume** (BFF tests repeatedly importing the ~50k-line
`main.py`), not broken modules.

## Execution health (H2: PASS)

| Service dir | Result |
|---|---|
| governance | 28 passed |
| capital | 14 passed |
| evolution | 76 passed |
| incident | 116 passed |
| lineage-read | 28 passed |
| control-plane/ooda | 45 passed |
| control_plane | 11 passed |
| broker | 83 passed |
| consultation | 35 passed |
| execution | 252 passed, 2 skipped |
| feedback | 23 passed |
| knowledge | 7 passed |
| learning | 59 passed |
| memory | 56 passed (+2 subtests) |
| policy-learning | 14 passed |
| optimizer-svc | 39 passed |
| evaluation | 44 passed |
| deployment | 37 passed |
| data-plane | 56 passed |
| foundation | 34 passed |

**Total executed: ~1,150 tests green, 0 failures, 2 skipped.** (`audit` and
`persona`/`ooda` top dirs collected 0 tests — their tests live under sibling
paths already covered.)

## Net

H1/H2 **PASS** — the service layer is structurally sound and green on
2026-06-14. No defect this round. This establishes a baseline: subsequent
rounds that touch service code can diff against a known-green suite.
