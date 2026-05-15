# Review: FE-INT-GATE-A11Y-BREADCRUMB

Reviewer: Codex2
Owner: Claude
Task: FE-INT-GATE-A11Y-BREADCRUMB - Fix Breadcrumb list semantic violation
Reviewed: 2026-05-15
Disposition: Approved

## Findings

No blocking findings.

## Scope Checked

- `execute-plans/src/platform/components/PageHeader.tsx` now maps breadcrumb nodes through `Fragment` instead of a rendered `span.contents` wrapper, so `BreadcrumbItem` and `BreadcrumbSeparator` render as direct `<li>` children of the `<ol>`.
- `execute-plans/src/components/ui/breadcrumb.tsx` keeps `<nav aria-label="breadcrumb">`, keeps `BreadcrumbItem` as a real `<li>`, and has no `contents` class on breadcrumb list items.
- `rg` found no `contents` class or `display: contents` usage under `src/platform` or `src/components` after the fix.
- The task brief's `execute-plans/src/platform/components/Breadcrumb.tsx` artifact path does not exist in this worktree; the actual platform integration point is `execute-plans/src/platform/components/PageHeader.tsx`.

## Verification

Commands run from `/home/lupin/code/execute-plans` against local Vite at `http://127.0.0.1:8081`:

```bash
PANTHEON_FE_BASE_URL=http://127.0.0.1:8081 npx playwright test e2e/17-a11y-v5.spec.ts --reporter=list
PANTHEON_FE_BASE_URL=http://127.0.0.1:8081 npx playwright test e2e/17-a11y-v5.spec.ts --grep "execution loop PersonaHealthMatrix" --timeout=180000 --reporter=list
npm run build
npx tsc --noEmit
```

Results:

- Full F17 run: 8 passed, 1 timeout on `execution loop PersonaHealthMatrix`; the failure was `Test timeout of 60000ms exceeded`, not a `list`/`listitem` axe violation.
- Focused execution-loop rerun with higher timeout: 1 passed.
- Combined F17 coverage confirms critical/serious axe violations are zero on control room, research loop, execution loop PersonaHealthMatrix, optimization loop, sentinel, and interventions.
- Build passed with existing dynamic-import/chunk-size warnings.
- TypeScript check passed.

## Decision

Approved for owner finalization. Acceptance is met: no rendered breadcrumb `span` wrapper remains between `<ol>` and `<li>`, the breadcrumb nav landmark remains, and the F17 v5 axe gate passes when the slow execution-loop case is allowed enough time.
