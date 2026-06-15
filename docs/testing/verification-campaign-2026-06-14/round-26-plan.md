# Round 26 — Canonical state cross-consistency

**Date:** 2026-06-15
**Depth/breadth step:** Moves to the **orchestrator state plane**. The dashboard
bundle and derived summaries are generated from `ai-status.json`; this round
checks the derivation is internally consistent and that the system's own
consistency detector (`truth_mismatches`) produces well-formed output.

## Hypotheses

- H1: `workload_summary` per-owner totals equal a recomputation from `tasks`.
- H2: every task owner is represented in `workload_summary`.
- H3: `truth_mismatches` entries are well-formed (the consistency detector emits
  valid structured findings).

## Method

1. Load `ai-status.json`; recompute per-owner task totals; diff against
   `workload_summary`.
2. Confirm every owner in `tasks` appears in `workload_summary`.
3. Validate `dashboard-bundle.json` `truth_mismatches` entry schema.

## Pass criteria

- H1–H3 hold; any derivation error is a generator defect fixed via the dev
  workflow.
