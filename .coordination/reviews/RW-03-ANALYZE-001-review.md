# RW-03-ANALYZE-001 Review

Reviewer: Claude
Date: 2026-04-19
Status: **REOPEN — two issues must be resolved before approval**

---

## Content Quality — PASS

All three acceptance criteria are met in the authored content:

1. **Analysis list and detail contracts are published** — `docs/bff/RW-03-analyze.md` defines `GET /api/v1/research/analysis` and `GET /api/v1/research/analysis/{analysis_id}` with complete field shapes, filter semantics, and degradation rules. The BFF routes are seeded in `main.py` and backed by `ReadSurfaceStore.list_research_analyses` / `get_research_analysis` with proper projection.

2. **Metric grouping is backend-owned** — `metric_groups[]` is returned pre-grouped from the BFF. The invariant is explicitly stated: "The frontend must not bucket metrics by prefix, substring, or naming convention." The read-store projection copies the backend-grouped array without client-side reordering.

3. **Comparative summary no longer relies on client-side aggregation** — `comparative_summary` is a backend-shaped field on the detail response. The non-goal is explicit: "The frontend must not fetch two detail routes and compute its own comparison."

Contract tests in `test_rw03_analyze_contract.py` cover list shape, detail shape with metric groups and comparative summary, and rejection of invalid status filter values.

PACKET_FAMILY.md correctly marks RW-03 as `contract-published — pending BFF implementation` and lists the three routes in the backend gap matrix.

---

## Issues — MUST FIX before approval

### Issue 1 — FRONTEND_SA route table inconsistency (content)

`docs/lovable/PANTHEON_FRONTEND_SA.md` line 283 (top route table) still reads:

```
| `/research/analyze` | Analyze | Research | blocked shell only |
```

But line 573 (module status table) correctly reads:

```
| Analyze | `/research/analyze` | `RW-03` | contract-published | pending-bff placeholder only |
```

The top table must be updated to match the pattern used for RW-01 and RW-02:

```
| `/research/analyze` | Analyze | Research | contract published — add "coming soon / blocked by Pantheon BFF" placeholder; production page pending BFF routes |
```

### Issue 2 — Uncommitted files (blocker, same class as TW-01)

`git status --short` shows all RW-03 files are either untracked or have only unstaged / staged-but-not-committed changes. Nothing is in the git history:

Untracked:
- `docs/bff/RW-03-analyze.md`
- `docs/examples/RW-03-analyze.json`
- `services/control-plane/bff/test_rw03_analyze_contract.py`

Unstaged or staged-but-not-committed:
- `docs/lovable/PANTHEON_FRONTEND_SA.md`
- `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`

A contract bundle is not canonical until it is committed. Fix Issue 1, then commit all seven files with a single commit referencing `RW-03-ANALYZE-001`, and return for re-review.

---

## Re-review Gate

After Codex2 commits with both fixes applied, return the task to Claude for a re-review. Approval will be granted once the commit hash is visible and the FRONTEND_SA route table is consistent.
