# Agora Gap Closure Execution Packet — 2026-07-12

Status: active.

## Wave 0

| Task | Owner | Reviewer | Goal |
|---|---|---|---|
| `AG-GAP-001` | Codex | Codex2 | Pin the dev workshop backend to Postgres and gate deployment on restart persistence. |

Artifact: `AG-GAP-001-workshop-postgres-live.md`.

Completion requires a Pantheon task PR merged to `dev`, successful focused
validation, and live workflow evidence showing that a newly created workshop
survives an `operator-bff` restart.
