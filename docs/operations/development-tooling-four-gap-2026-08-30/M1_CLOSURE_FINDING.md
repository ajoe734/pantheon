# M1 dependency-closure finding (DTG-CLEAN-M0, 2026-08-30)

## What SD.md characterized

Section 7.4 describes M1 as moving "current-work/dashboard rendering,
view-only normalization, and projection file writes" into one narrow status
projection module, implying a small, self-contained unit. `GAP_AUDIT.md`'s
mechanical scan and this task's own first-pass keyword classifier both landed
on roughly 5-6 candidate functions (~480 lines).

## What manual verification found

`write_current_work` and `build_dashboard_bundle` are pure functions of
`state`/`logs`/`orchestrator_state` in the sense that they take no hidden
positional coupling — but their **bodies** call a large set of other
`ai_status.py` top-level helpers. Computing the transitive call closure
(same-module function calls only, via AST) from the five confirmed rendering
entry points gives:

- **53 functions, 1,629 lines** — roughly 3.4x the naive estimate.

The closure includes:

- Core infrastructure used throughout the whole file and far beyond
  dashboard rendering: `load_config`, `canonical_agent_name`,
  `assert_task_archive_root_binding`, `parse_timestamp`. Moving these would
  ripple into every other subsystem, not just status projection.
- M3-adjacent evidence/mismatch logic: `detect_truth_mismatches` (254
  lines), `github_review_bridge_evidence_matches`,
  `operator_acceptance_evidence_matches`, `exact_head_acceptance_evidence_matches`,
  `merged_delivery_evidence`, `delivery_binding_stale_evidence`. These are
  delivery/review evidence validators (SD.md 7.6, M3's actual target), not
  view rendering; they happen to be called for a "mismatch" section of the
  dashboard bundle, but their canonical owner is the M3 evidence module, not
  a status projection module.
- Legitimate rendering-support helpers that plausibly belong with the moved
  functions: `format_display_timestamp`, `localize_embedded_timestamps`,
  `display_task_status`, `display_task_title`, `activity_log_message`,
  `canonical_file_set`, `canonical_tier_labels`, `task_delivery_layer`,
  `pending_status_write_count`, and several `normalize_*`/`terminal_*`
  helpers that shape data specifically for display.

## Why this blocks a same-session physical move

Two extraction strategies were considered:

1. **Move the full 1,629-line closure.** This pulls M3's actual target
   symbols (evidence/mismatch validation) into a "status projection" module,
   violating "one canonical owner per behavior" (SD.md 7.1) and creating a
   second, competing home for M3's work before M3 has even run. It also
   drags core infrastructure (`load_config`) into a module that is supposed
   to be narrow and dashboard-only.
2. **Move only the 5 top-level rendering functions, importing their helper
   dependencies back from `ai_status.py`.** This is what "narrow module"
   suggests, but it makes the new module import from `ai_status.py` while
   `ai_status.py` must import the 5 moved functions back from the new
   module to keep calling them (its own internal callers, e.g.
   `refresh_derived_status_views`, and ~25 existing test call sites in
   `scripts/test_ai_status.py`) — a circular import between the two modules,
   which SD.md 4.2 invariant 9 and the M0 gate explicitly forbid.

Neither strategy is a same-session MIGRATE without a module-boundary
decision beyond what SD.md specifies in the text: either M1 must be
resequenced to run **after** M3 has claimed the evidence/mismatch symbols
(so M1's closure shrinks to genuinely rendering-only helpers with no
onward dependency on unmoved evidence functions), or the "narrow status
projection module" needs an explicit sub-boundary (e.g. a
`status_projection` package importing a stable, already-migrated
`review_gate_evidence` module rather than reaching into `ai_status.py`).

## Disposition applied this session

- The 5 confirmed rendering entry points (`write_current_work`,
  `build_dashboard_bundle`, `write_dashboard_bundle`,
  `dashboard_orchestrator_state`, `sync_docs_site`) are recorded as
  **MIGRATE** with `target_owner: .orchestrator/rewrite/status_projection.py`
  in `MONOLITH_SYMBOL_DISPOSITION.json`, but the physical code move was
  **not executed** this session for the reason above.
- `refresh_derived_status_views` is **KEEP** in `ai_status.py`: it depends
  on `load_logs()`, an `ai_status.py`-native reader tied to that module's
  own `STATUS_ROOT`/env resolution, and stays as the thin caller that will
  import the moved functions by name once M1 actually executes.
- The 47 closure-only symbols are **VERIFY** with `wave: M1-closure`: they
  need a human decision on final ownership (rendering-support vs. M3
  evidence vs. stays-in-`ai_status.py` core infra) before any of them gets
  a MIGRATE disposition.

## Recommendation for whoever executes M1

Run M3 (delivery/review evidence extraction) before M1, or explicitly
split the M1 target module into a rendering layer plus a dependency on
M3's already-relocated evidence module. Re-run the transitive-closure
script below after M3 lands; the M1 closure should shrink substantially
once `detect_truth_mismatches` and its evidence-matcher dependencies have
a canonical home elsewhere.

```python
# .orchestrator/rewrite/... or a throwaway script:
# same approach as used to produce this finding -- AST-walk the five
# entry points, collect ast.Call(func=Name) targets that resolve to
# other top-level functions in the same file, recurse.
```
