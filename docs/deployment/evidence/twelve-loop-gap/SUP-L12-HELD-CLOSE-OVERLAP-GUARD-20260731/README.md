# SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731 evidence

This packet records the dev-based rebuild of the held-close overlap guard after
PR #4614 diverged from `dev`.

The dispatcher admits one precise live overlap: the 7/31 previous-current
catalog task `L12-CONTROLLER-CATALOG-INTEGRATION-20260731` may overlap held
legacy sink `L12-CLOSE-001` only on the loop-catalog registry artifact. The
predicate binds the previous-current catalog digest, the three release-order
task-contract digests, and every field of the held close row. It evaluates that
specific pair even when a malformed held row removes its own reported overlap,
so spoofing or omission cannot bypass the fail-closed guard.

All other nonterminal live overlap remains rejected, including any extra
overlap on the controller-integration task and all overlaps in the 8/03 current
catalog profile.

## Dev-base composition

The rebuild starts from `origin/dev` `1209682f5`, retaining its 8/03 corrected
profile and authoritative-snapshot work. It adds only the previous-current
held-close admission and regression coverage; it does not restore the old PR's
stale dispatcher tree or alter catalog files.

## Local verification

- `python3 -m py_compile scripts/dispatch_twelve_loop_gap_2026_07_26.py scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py`
- `/tmp/pantheon-sup-l12-venv/bin/python -m pytest -q scripts/test_dispatch_twelve_loop_gap_current_remediation_2026_07_31.py` — 53 passed
- `/tmp/pantheon-sup-l12-venv/bin/python scripts/dispatch_twelve_loop_gap_2026_07_26.py --validate-only --current` — valid, 28 tasks, 25 G1
- `/tmp/pantheon-sup-l12-venv/bin/python scripts/dispatch_twelve_loop_gap_2026_07_26.py --validate-only --previous-current` — valid, 28 tasks, 25 G1

No product tasks were materialized and no deployment occurred.

## Pending delivery gates

Antigravity must independently review the exact rebuilt PR #4614 head. The
required GitHub checks, protected merge into `dev`, and command-root promotion
remain pending. This evidence does not claim any of those gates passed.

The machine-readable record is [evidence.json](evidence.json).
