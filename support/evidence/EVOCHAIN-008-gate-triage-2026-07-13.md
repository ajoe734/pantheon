# EVOCHAIN-008 — commit-trailer gate triage (2026-07-13)

PR #3522 (`task/EVOCHAIN-008` -> `dev`) went `BLOCKED` after merging
`origin/dev` to refresh a stale merge-base: the push-event "Commit
trailers" check range (`before..after` on the raw push) straddled the
merge and re-scanned `e9895fa` ("OPS-DISPATCH-PIDCOUNT-001: count
worker_runner in live worker scan (#3523)"), an already-merged `dev`
commit whose own subject exceeds the 72-char limit. That commit is not
owned by this task and cannot be amended.

The `pull_request`-triggered instance of the same check (which computes
its range from the PR's actual merge-base with `dev`, not the raw push
before/after) passed correctly. This matches the known false-positive
pattern: a fresh commit on top moves the next push-event range forward
past the offending commit. This file is that fresh commit.

See `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/EVOCHAIN-008-fe-badge-semantics.md`
for the task's full evidence record.
