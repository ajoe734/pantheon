# Task Brief: AG-FE-DB-001B

## Task
- Title: Deliver Agora dashboard widget runtime to execute-plans (FE-DB-001 FE half)
- Status: in_progress → review
- Owner: Claude
- Reviewer: Claude2

## Summary

Supplement delivery of the missing frontend half of AG-FE-DB-001 into execute-plans repo
(Pantheon side was marked done but the FE artefacts were tracked under a separate delivery task).
Per SD §9.8 + design-closure A3 (widget_registry.v1.json + widget_spec/chart_spec grammar) +
contract-closure 05 (chart lib decision) + v1.3 workshop_card/widget schema:

- `execute-plans/src/agora/widgets/registry.ts` — WidgetRegistry loader, checksum constants,
  active-widget filter, ChartSpec grammar validators, and interaction/sensitivity guards.
- `execute-plans/src/agora/widgets/WidgetRenderer.tsx` — registry-gated widget shell; dispatches
  to ChartSpecRenderer (chart_spec renderer) or BuiltinWidgetRenderer (builtin renderer).
- `execute-plans/src/agora/widgets/ChartSpecRenderer.tsx` — declarative ChartSpec dispatch:
  Recharts for metric/line/area/bar; ECharts for heatmap/network/sankey/candlestick/gauge/scatter;
  builtin renderers for table/timeline/stacked_bar. No eval, no innerHTML, no arbitrary HTML/JS.
- `execute-plans/package.json` — echarts ^5.6.0, echarts-for-react ^3.0.2,
  react-grid-layout ^1.5.0, @types/react-grid-layout ^1.3.5 already present.

## Delivery Evidence

All artefacts delivered in commit `6062cb2c` (AG-FE-DB-001, Codex, merged to master):

```
6062cb2c AG-FE-DB-001: add Agora widget renderers
```

### Acceptance Verification (2026-06-22, Claude)

| Criterion | Status |
|---|---|
| `registry.ts`, `WidgetRenderer.tsx`, `ChartSpecRenderer.tsx` present in execute-plans | ✅ verified |
| Only active registry widgets rendered (status gate in validateWidgetSpecAgainstRegistry) | ✅ verified |
| Recharts: metric/line/area/bar; ECharts: heatmap/network/sankey/candlestick/gauge/scatter | ✅ verified |
| Builtin: table/timeline/stacked_bar | ✅ verified |
| react-grid-layout / echarts / echarts-for-react in package.json | ✅ verified |
| No eval/innerHTML/arbitrary code injection (UNSAFE_KEY_PATTERN + UNSAFE_STRING_PATTERN guards) | ✅ verified |
| Frontend/backend registry checksum consistent (AGORA_WIDGET_CONTRACT_HASHES) | ✅ verified |
| Data via BFF client props only — no direct fetch in component tree | ✅ verified |

### Test Results

```
npx vitest run src/agora/widgets/
  ✓ registry.test.ts (5 tests)
  ✓ WidgetRenderer.test.tsx (4 tests)
  ✓ ChartSpecRenderer.test.tsx (5 tests)
  ✓ WidgetRevisionDrawer.test.tsx (3 tests)
  Test Files  4 passed | Tests 17 passed
```

TypeScript: no errors in `src/agora/` (pre-existing e2e playwright type errors unrelated to this task).

## Composed With
- AG-BE-DB-001: backend dashboard persistence/validator
- AG-FE-DB-003: WidgetRevisionDrawer (delivered separately, Codex2)
- AG-XR-OPENAPI-002: Agora v1.2 contract bundle including widget_registry.v1.json
