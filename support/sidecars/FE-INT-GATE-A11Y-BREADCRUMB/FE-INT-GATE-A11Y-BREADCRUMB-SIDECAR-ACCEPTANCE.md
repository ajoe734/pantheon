# Acceptance Packet - FE-INT-GATE-A11Y-BREADCRUMB

**Sidecar Task ID:** FE-INT-GATE-A11Y-BREADCRUMB-SIDECAR-ACCEPTANCE
**Parent Task:** FE-INT-GATE-A11Y-BREADCRUMB - Fix Breadcrumb list semantic violation
**Helper Kind:** acceptance_packet
**Prepared by:** Gemini (2026-05-15T)
**Reviewer:** Claude
**Parent Owner:** Claude
**Parent Reviewer:** Codex2

---

## 1. Scope Reminder

This sidecar is support-only. It does not edit L1 canonical truth, core contract truth, runtime implementation, registry implementation, governance implementation, or execute-plans source.

The purpose is to provide the parent owner/reviewer with a concrete acceptance checklist and dependency map for closing `FE-INT-GATE-A11Y-BREADCRUMB`.

---

## 2. Parent State

Current parent state from `ai-status.json`:

| Field | Value |
|---|---|
| Status | `in_progress` |
| Owner | `Claude` |
| Reviewer | `Codex2` |
| Phase | `Pantheon FE Integration Gate 2026-05-13` |
| Primary artifacts | `execute-plans/src/platform/components/Breadcrumb.tsx`<br>`execute-plans/src/components/ui/breadcrumb.tsx` |

Parent problem summary:

- Breadcrumb component uses `<ol>` directly containing `<li class="contents">`.
- Rendered result causes `<span>` to be a direct child of `<ol>`, violating WCAG 1.3.1 (cat.structure).
- Specifically, `li.contents` (display: contents) breaks the list semantics in the accessibility tree.

---

## 3. Acceptance Checklist For Parent Closeout

Do not close `FE-INT-GATE-A11Y-BREADCRUMB` until all parent criteria below are satisfied.

| # | Criterion | Required Evidence |
|---|---|---|
| 1 | Semantic List Structure | `<ol>` must only have `<li>` elements as direct children in the rendered DOM. No `<span>` or other non-listitem elements as direct children. |
| 2 | Remove `display: contents` | The `contents` class or `display: contents` style must be removed from `<li>` elements within the Breadcrumb to preserve list semantics. |
| 3 | Accessibility Audit (axe) | Run `axe` on all v5 pages (control-room, research/execution/optimization loops, sentinel, interventions) and confirm 0 violations for `list` and `listitem` rules. |
| 4 | `nav` Wrapper | The `<nav aria-label="breadcrumb">` wrapper must be maintained for proper landmark navigation. |
| 5 | Visual Regression | Confirm that removing `display: contents` does not break the layout (spacing, wrapping, alignment) of the breadcrumbs across different screen sizes. |
| 6 | Verification Commands | Record the specific `npx playwright test` or `axe` commands used to verify the fix in the closeout message. |

---

## 4. Dependency Map

| Dependency | Role | Current Status |
|---|---|---|
| `FE-INT-GATE-A11Y-BREADCRUMB` | Parent implementation task | `in_progress` |
| `execute-plans` workspace | Source repository for Breadcrumb components | Active |
| `axe-core` / Playwright | Tooling for accessibility verification | Available |
| `e2e/17-a11y-v5.spec.ts` | Relevant E2E test for a11y regressions | Exists (referenced in related tasks) |

---

## 5. Recommended Verification Workflow

1. **Local Development:**
   - Modify `Breadcrumb.tsx` and `ui/breadcrumb.tsx` to ensure `<li>` is a direct child of `<ol>` and not using `display: contents`.
   - Verify visually in the browser.

2. **Automated Testing:**
   - Run the relevant a11y spec:
     ```bash
     npx playwright test e2e/17-a11y-v5.spec.ts
     ```
   - If a specific breadcrumb test doesn't exist, add a focused test case or use `axe-core` directly on a page with breadcrumbs.

3. **DOM Inspection:**
   - Use browser dev tools to inspect the Breadcrumb structure and ensure the Accessibility Tree correctly identifies the list and its items.

---

## 6. Reviewer Notes For Claude

Please verify:

1. This sidecar packet correctly captures the requirements from the parent task brief.
2. The checklist is exhaustive for the reported semantic violation.
3. The dependency map reflects the current workspace state.

Once reviewed, this packet can be used by the parent owner (Claude) to guide the finalization and closeout of `FE-INT-GATE-A11Y-BREADCRUMB`.
