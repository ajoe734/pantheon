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
- **Reconciliation:** Verified that the core changes of `MGMT-GAP-008` were already merged into `origin/dev` via **PR #135** (merge commit `47b8f41`). The staged differences in `.fe-ep` were obsolete merge residues from July 1, 2026, which would revert subsequent features (like the real backtest engine and skill sandbox runner implemented in later tasks) if committed.
- **Outcome:** No unique unpushed or local-only commits. Salvage not required.

### C. `.fe-worktrees`
- **Status:** Completely empty.
- **Outcome:** Salvage not required.

All stale nested checkouts have been purged from the filesystem.

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
