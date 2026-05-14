# Review: FE-INT-GATE-D05 — F18 Perf and stability soft-fail budget

**Reviewer:** Claude  
**Date:** 2026-05-13  
**Artifact:** execute-plans/e2e/18-perf.spec.ts  
**Decision:** APPROVED

---

## Acceptance Criteria Coverage

| Criterion | Coverage | Notes |
|---|---|---|
| SSE 30s rerender 不超上限 | ✅ | MutationObserver proxy on `#root`, normalized budget `sseMutationBatchesPer30s * (SSE_WINDOW_MS / 30_000)` |
| Control Room 載入 budget | ✅ | `recordBudget` with `controlRoomLoadMs: 4_000`, soft annotation on overrun |
| Entity list first page budget | ✅ | `recordBudget` with `entityFirstPageLoadMs: 4_000`, same soft pattern |
| Sentinel list budget | ✅ | `recordBudget` with `sentinelListLoadMs: 4_000` |
| LineageGraph >500 nodes warning | ✅ | `WIDE_LINEAGE_STRATEGY.personaIds` length 502 → FE computes 506 total nodes; test asserts visible warning text |
| DataTable density stable | ✅ | Checks `style.height === "48px"` on first 10 rows before and after hover |

## Soft-fail Semantics

- Default: overruns annotate as `perf-budget-soft-fail` and warn to console; test continues and passes.
- `FE_INT_GATE_PERF_STRICT=1` converts every overrun into a hard `expect` failure — correct graduation path.
- Test suite timeout correctly scales: `Math.max(75_000, SSE_WINDOW_MS + 45_000)`.

## SSE Harness

- `SsePerfHarness` starts a real `node:http` server on a random port, sets proper SSE headers including CORS.
- `installEventSourceRedirect` patches `window.EventSource` via `addInitScript` to redirect `/bff/events/stream` to the harness — this survives page navigation since it's injected before page context.
- Harness cleanup is in `finally` block — no leak on test failure.
- Soft-gap annotation when no SSE request reaches the harness (e.g., local dev mode without live BFF) is a good defensive touch.

## Fixture Data

- `WIDE_LINEAGE_STRATEGY` 502 personaIds plus strategy/alpha/capital-pool edges totaling 506 satisfies the `> 500` threshold test.
- 120 sentinel findings, 12 interventions, 9 loop runs — sufficient to cover list rendering without being excessive.
- `nowIso()` returns a fixed ISO string — deterministic and correct for mocked fixtures.

## Minor Observations (non-blocking)

1. `dataTableRowStyleHeights` asserts inline `style.height === "48px"`. If the DataTable switches to CSS-class-based density rather than inline style, this assertion silently returns an empty array and the `every()` check vacuously passes. Consider adding `expect(before.length).toBeGreaterThan(0)` before the `every` check — already present ✓.
2. The lineage node count test expects exact regex `/Lineage has 506 nodes/i`. This is coupled to FE node-counting logic. Since the fixture is stable, this is acceptable at integration-gate level.

## Verification Summary

Codex verification passed:
- esbuild bundle: no TypeScript errors
- `playwright --list`: 4 tests discovered
- Runtime smoke 4/4 with `FE_INT_GATE_SSE_WINDOW_MS=3000`
- Focused SSE case 1/1 passed

All acceptance criteria are addressed. Soft-fail design is correct for CI entry. Approved for finalization.
