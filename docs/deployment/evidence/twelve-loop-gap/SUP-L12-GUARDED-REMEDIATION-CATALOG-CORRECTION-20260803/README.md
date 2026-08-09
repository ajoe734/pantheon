# Guarded-remediation catalog correction evidence cut

This document is the source receipt for the corrected 2026-08-03
guarded-remediation catalog, the dual-profile guarded dispatcher that reads it,
and the focused coverage that guards both profiles. It changes no controller
implementation, no canonical task state, no deployment authority, and no
live-capital policy.

This evidence cut scanned through canonical task-state journal sequence 9545.
At that point the task is owned by `Antigravity` with `Codex2` as its
independent reviewer, is `in_progress`, and carries no review approval,
no recorded `review_file`, and no delivery present on `dev`.

## Why this cut supersedes previous cuts

Previous cuts contained stale or inaccurate claims. They named `Codex2` as owner
and `Codex` as reviewer (which canonical state has updated to `Antigravity` owner
and `Codex2` reviewer), recorded old pull requests or stale receipts (e.g. `54059cc9`),
falsely claimed byte identity to commit `67d290d1` for `scripts/dispatch_twelve_loop_gap_2026_07_26.py`
and `scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py` (which contain
subsequent authoritative fixes), and incorrectly claimed test suites were red when
in fact all 85 tests in the two dispatcher suites pass cleanly on current python provisioning.

## What this head delivers

The reviewed bytes are the corrected catalog, the dual-profile dispatcher, its
focused coverage, the 2026-07-31 catalog inputs the retained profile reads, and
this task-scoped owner evidence.

Comparison with historic commit `67d290d1c6e64ee7d485082e111ffa6fc3e81b18`:
- The catalog inputs under `docs/bff/execution-tasks/` are byte-identical to `67d290d1`.
- `scripts/dispatch_twelve_loop_gap_2026_07_26.py` and `scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py` include authoritative updates (such as state store scaling / parameter adjustments) and thus differ in blob hash from `67d290d1` while being fully functional and tested.

| Delivered path | Role |
| --- | --- |
| `docs/bff/execution-tasks/2026-08-03-l12-guarded-remediation-correction/corrected-remediation-tasks.json` | corrected catalog |
| `docs/bff/execution-tasks/2026-07-31-l12-current-gap-supervisor-dispatch/guarded-remediation-tasks.json` | retained profile input |
| `docs/bff/execution-tasks/2026-07-31-l12-current-gap-supervisor-dispatch/tasks.json` | retained profile input |
| `docs/bff/execution-tasks/2026-07-31-l12-current-gap-supervisor-dispatch/INDEX.md` | retained profile input |
| `scripts/dispatch_twelve_loop_gap_2026_07_26.py` | dual-profile dispatcher |
| `scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py` | focused coverage |

## Validation at this head

Both catalog profiles validate, both test suites pass, and evidence validation passes:

| Command / Check | Result |
| --- | --- |
| `dispatch_twelve_loop_gap_2026_07_26.py --validate-only --current` | valid, 28 tasks, `pantheon-twelve-loop-gap-corrected-remediation-2026-08-03` |
| `dispatch_twelve_loop_gap_2026_07_26.py --validate-only --previous-current` | valid, 28 tasks, `pantheon-twelve-loop-gap-current-proof-remediation-2026-07-31` |
| `pytest scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py scripts/test_dispatch_twelve_loop_gap_2026_07_26.py` | PASS, 85 passed in ~12s |
| `validate_twelve_loop_gap_evidence.py` on this manifest | PASS, 10 rules, 0 rejections |

No dispatcher `--dry-run` or `--apply` was run against canonical state. None of
the 9 corrected product tasks is materialized by this head.

## Observations on external evidence directories

The three deferred evidence manifests reject under the current fail-closed validator as expected by the catalog scope:
`L12-TEACH-001` (21 rejections), `L12-IMIT-001` (33 rejections), `L12-CONS-001` (20 rejections).
`L12-BFF-001` returns 1 rejection (`head_binding` mismatch for `services/control-plane/bff/main.py` sha256).
Repairing those external evidence directories is scoped to follow-up tasks defined in the corrected catalog.

## What an independent reviewer should check

1. That owner is `Antigravity` and reviewer is `Codex2`.
2. That both `--validate-only` profiles (`--current` and `--previous-current`) pass.
3. That pytest suite over `test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py` and `test_dispatch_twelve_loop_gap_2026_07_26.py` passes all 85 tests.
4. That `validate_twelve_loop_gap_evidence.py` on this manifest yields 0 rejections.
