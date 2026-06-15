# Round 26 — Results

**Executed:** 2026-06-15 (UTC). **Note:** the `dev`-branch state snapshot is a
frozen older copy (archive `updated_at` 2026-05-15); the live main-tree state is
churned by the orchestrator. The invariants checked here hold regardless of
snapshot age.

## H1 — workload_summary derivation: PASS

Recomputed per-owner task totals from `ai-status.json` `tasks` and diffed against
`workload_summary`: **0 mismatches**. e.g. `Copilot: 2`, `Claude/Claude2/Codex/
Codex2/Gemini2: 1` each — all match.

## H2 — owner coverage: PASS

Every owner appearing in `tasks` is present in `workload_summary`; agents with no
tasks (`Gemini`, `Human/Ops`) are correctly shown with `total: 0`.

## H3 — consistency detector: PASS

`dashboard-bundle.json` `truth_mismatches` carries **1** well-formed entry
(`{id, type, severity, title}` all present) — a `worker_assignment_mismatch`
(`MGMT-EVO-005` assigned to Claude while the live worker is Codex). The detector
is functioning: it honestly surfaces a real assignment divergence rather than
hiding it. This is the consistency feature working, not a defect.

## Net

H1–H3 **PASS** — the canonical state derivation is internally consistent and the
self-consistency detector emits valid findings. No defect. (Deep cross-checks
against the live churned state are out of scope — those files are orchestrator-
owned and continuously rewritten.)
