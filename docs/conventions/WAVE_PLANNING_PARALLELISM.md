# Wave Planning — Maximize Parallel Frontier Width

**Audience:** whoever authors a `*-PLAN` gap task or a `scripts/dispatch_*.py` wave dispatcher.

**Why this exists:** the fleet's throughput is bounded by *frontier width* — how many tasks are dependency-ready at the same moment — NOT by worker count or CPU. Waves observed in 2026-07 (TJ-E2E, PERF-IA) ran a 12-core box at load ~4 with only 5 tasks in flight because every remaining task was gated behind a serial chain. Adding cores/workers does nothing when 0 todos are ready. The lever is the **dependency graph you author at plan time.**

---

## Rule 1 (primary): split cross-repo tasks into backend + frontend

A task that touches **both** `pantheon` (backend/BFF) and `execute-plans` (frontend) — i.e. `target_repo = "pantheon+execute-plans"` — almost always has a backend half whose only real dependency is the backend contract, plus a frontend half that additionally needs the frontend workbench/foundation.

Authored as one unit, the whole task inherits the *frontend's* dependency (the workbench), so the backend half is needlessly blocked.

**Do:** emit two tasks.
- `<ID>-BE` — `target_repo: pantheon`, `depends_on:` only the backend contract task.
- `<ID>-FE` — `target_repo: execute-plans`, `depends_on:` `[<ID>-BE, <workbench task>]`.

**Concrete example (from TJ-E2E, the case that motivated this):**

```
# BEFORE — backend blocked on the frontend workbench (006):
TJ-E2E-007 "Live SSE and attention model"   depends_on [005, 006]   pantheon+execute-plans
TJ-E2E-008 "Governed journey actions"        depends_on [005, 006]   pantheon+execute-plans

# AFTER — backend halves run in parallel with 006's build:
TJ-E2E-007-BE "SSE server + attention model" depends_on [005]        pantheon
TJ-E2E-007-FE "SSE wiring in workbench"       depends_on [007-BE, 006] execute-plans
TJ-E2E-008-BE "Governed-action endpoints"     depends_on [005]        pantheon
TJ-E2E-008-FE "Governed actions UI"           depends_on [008-BE, 006] execute-plans
```
005 (canonical BFF read API) is already done, so both `-BE` tasks become ready *immediately* and run while 006 (workbench) is still in progress — widening the frontier from 1→3 during the foundation stage.

## Rule 2: split monolithic foundation tasks that many features depend on

If ≥3 tasks all `depends_on` a single "P0 workbench" / "foundation" task, that foundation is a frontier choke point. Where the foundation has separable sections (layout shell vs. per-panel widgets), split it so each feature depends only on the section it builds on, not the whole shell.

## Rule 3: declare the *true* build-on-top edge, not conservative ordering

`depends_on` must encode a real "B consumes A's output" relationship, not "B feels safer after A." A backend data pipeline (inventory → contract → propagation → materializer → read-API) is genuinely serial — leave it. But "regression cleanup" listing every sibling as a dep, or a feature depending on an unrelated sibling, is conservative padding — drop it.

## Guardrail: do NOT over-cut — collisions are worse than serialization

Two tasks that edit the **same live surface** must stay ordered, even across repos. Real incident: `MGMT-PERF-IA-001` (route-migration redirect of `/management/promotion-allocation`) and `PPL-ALLOC-006` (expanding that same page into a workbench) were authored to run concurrently and collided — a failing integration-gate + a product IA-precedence decision. When two tasks' file/route/contract footprints overlap, add the dependency even if it narrows the frontier. Parallelism is only free when footprints are disjoint.

---

## Quick checklist per task row in a dispatcher

- [ ] Is `target_repo == "pantheon+execute-plans"`? → split into `-BE` / `-FE` (Rule 1).
- [ ] Do ≥3 tasks depend on this one? → can it be sectioned (Rule 2)?
- [ ] Is every entry in `depends_on` a real output-consumes edge (Rule 3)?
- [ ] Does its footprint overlap a concurrent task's? → keep/add the edge (Guardrail).

The goal is the widest frontier whose members have **disjoint footprints** — not the shortest dependency list.
