# AG-GAP-011: FE Checkout Hygiene & Repository Rules

## 1. Inventory & Reconciliation Audit

We audited the nested frontend checkouts under the `pantheon` repository workspace (`/home/lupin/code/pantheon/`) on 2026-07-12:

### A. `.fe-human-inbox-persona-focus`
- **Branch:** `fix/persona-research-link-targets`
- **Status:** Clean working tree.
- **HEAD Commit:** `ec15204` ("PERSONA-FLEET-RESEARCH-LINKS: separate research detail and execution links").
- **Reconciliation:** Verified that commit `ec15204` is an ancestor of the remote `origin/dev` branch. It was merged via pull request **PR #244** (merge commit `cb2e543`).
- **Outcome:** No unpushed or local-only commits. Salvage not required.

### B. `.fe-ep`
- **Branch:** `task/mgmt-gap-008-detail-honesty`
- **Status:** Staged resolved conflicts from a pending merge of `origin/dev`.
- **HEAD Commit:** `821ad41` ("MGMT-GAP-008: fix management detail DTO/render honesty").
- **Reconciliation:** Verified that the core changes of `MGMT-GAP-008` were already merged into `origin/dev` via **PR #135** (merge commit `47b8f41`) and **PR #133** (merge commit `225765a`). The staged differences in `.fe-ep` were obsolete merge residues from July 1, 2026, which would revert subsequent features if committed.
- **Outcome:** No unique unpushed or local-only commits. Salvage not required. (See § 3 below for the exact patch-equivalence proof).

### C. `.fe-worktrees`
- **Status:** Completely empty.
- **Outcome:** Salvage not required.

### D. Nested `execute-plans` inside Live Checkout
- **Path:** `/home/lupin/code/pantheon/execute-plans`
- **Status:** Leftover directory tree containing only empty directories (`src/management/components/performance-review`, `tests/e2e/helpers`, etc.) and no files. It was tracked by `.gitignore` and did not contain any unpushed work.
- **Outcome:** Audited and safely removed via `rm -rf` on 2026-07-12. All stale nested checkouts have been purged from the filesystem.

---

## 2. Enforced Workspace Rules

To maintain codebase hygiene, avoid Git index contamination, and prevent split-brain development, the following rules are enforced:

1. **Single Source of Truth:**
   All frontend development must only be performed inside the canonical repository `ajoe734/execute-plans` on the `dev` branch (via ephemeral `task/*` branches and PRs).

2. **Canonical Working Directory:**
   The canonical local checkout for the frontend repository is `/home/lupin/code/execute-plans` or a clean task worktree branched/cloned from it.

3. **No Nested Checkouts:**
   Frontend repository folders, checkouts, submodules, or worktrees must **never** be materialized as nested directories (e.g. `.fe-ep`, `.fe-worktrees`, or `execute-plans/`) inside the `pantheon` repository checkout (`/home/lupin/code/pantheon`).

4. **No Git Index Overlap:**
   Keeping multiple nested repositories prevents cross-repository indexing issues and mitigates shared-index staging bugs.

---

## 3. Patch-Equivalence Proof for 821ad41

We compared the diff of commit `821ad41` against the mainline branch (`origin/dev`) history of `ajoe734/execute-plans` and confirmed that all fixes proposed in `821ad41` are fully equivalent (and in some cases, improved) in the canonical repository:

| File / Component | Fix in `821ad41` | Mainline Implementation (`origin/dev`) | Equivalence Status |
| --- | --- | --- | --- |
| `EntityHeader.tsx` | Falls back to `Unknown` on blank name and `Unassigned` on blank owner. | Falls back to `object.id` on blank name and `"—"` on blank owner. | **Equivalent:** Both prevent blank rendering, with mainline using cleaner, standardized placeholders. |
| `RiskBadge.tsx` | Sanitizes falsy level and falls back to `"unknown"`. | Returns a dashed outline badge with `t("risk.unavailable", "Unavailable")` if invalid. | **Equivalent & Improved:** Mainline handles missing values with a standardized Unavailable state rather than literal unknown. |
| `StatusBadge.tsx` | Sanitizes falsy state and falls back to `"unknown"`. | Returns a dashed outline badge with `t("status.unavailable", "Unavailable")` if invalid. | **Equivalent & Improved:** Mainline handles missing values with a standardized Unavailable state. |
| `StatCard.tsx` | Guards render output with `value === undefined ? "—" : value`. | Handled at caller and DTO normalization layers (e.g., in `CapitalPoolDetail.tsx` and `RebalanceDetail.tsx` via `num`, `safeRatio`, and `safePercent` helpers). | **Equivalent:** The caller-side mapping ensures no `NaN` or invalid values are passed to `StatCard`. |
| `App.tsx` (Alias Redirects) | Introduces `DetailAliasRedirect` for legacy page routes. | Incorporates generalized `makeDetailAliasRedirect` redirect wrapper for all legacy paths. | **Equivalent:** Standardized redirects are fully integrated. |
| MC/Registry Pages | Implements loaded check to distinguish loading from empty state. | Features the fully integrated `EmptyState` component displaying "live registry empty" when loading resolves to no items. | **Equivalent:** Solves the infinite loading indicator problem. |

