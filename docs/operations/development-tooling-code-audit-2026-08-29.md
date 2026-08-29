# Development Tooling Code Audit — 2026-08-29

**Scope:** Supervisor, V2 TaskStore, auto-worker lifecycle, delivery/review,
development bridge, worktree recovery, and tooling CI. Product runtime and the
Pantheon twelve business loops are outside this audit.

## Result

The canonical V2 path remains the only executable development-task path. The
code audit found no second live scheduler, worker merge path, mutable restart
path, chair/sidecar scheduler, or product-BFF task authority. Four functional
gaps were found and corrected:

| Area | Code finding | Disposition |
|---|---|---|
| Runtime delivery queue | Terminal records accumulated forever in `state.json`, so every cycle rewrote roughly 17 MB in the observed live state. | Terminal cache rows are bounded; every non-terminal intent and active-worker reference remains intact. Activity/TaskStore audit remains durable truth. |
| Task assignment contract | Automatic fallback could replace a named reviewer while immutable acceptance still required that old identity. | New acceptance must be role-based. Legacy identity-pinned tasks cannot silently reassign the named role; they require a successor/supersede contract. |
| Worker-worktree recovery | A missing registered path outside the current worktree base root was skipped before the missing-path check. | Missing registry leases are removed regardless of historical base root. Existing out-of-root directories remain untouched. |
| Historical V1 audit | A relocated immutable archive could not be supplied, and a missing archive was reported as a generic operational error. | Offline verifier accepts an explicit relocated path but still verifies the immutable size/hash. Missing exact bytes are classified `historical_archive_unavailable`; V2 hot state is unaffected. |

## Verification ownership

Tooling CI now runs the supervisor/runtime/approval tests, process recovery,
watchdog, task-contract rules, development-bridge inbox/reliability tests, and
the existing status/config/boundary tests. `.orchestrator` remains excluded
from repository-wide default pytest discovery intentionally; the branch gate
names its tooling suites explicitly so exclusion cannot silently omit them.

## Historical data limitation

The current host does not contain the exact legacy archive bytes named by the
immutable anchor. Code can now verify those bytes at a relocated path, but it
cannot recreate or substitute them. This is an offline historical-audit data
availability limitation, not a supervisor dispatch, TaskStore hot-read, worker,
review, merge, or recovery failure.

## Non-goals and removed alternatives

- Do not add a second queue, scheduler, merge runner, or product API task path.
- Do not revive chair/sidecar scheduling or generic task-amendment machinery.
- Do not auto-delete an existing worktree outside the configured worker root.
- Do not treat the legacy archive as runtime authority or a hot-path dependency.
