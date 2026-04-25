# Execution Materialization

This file bridges the EP4/EP5 planning session into execution.

The intent is:

- first materialize the work needed for stable `EP4`
- only then materialize any `EP5` prerequisite or proof work

## Recommended Materialization Boundary

- Initial accepted batch: `OSS-004A`, `OSS-004B`, `OSS-004C`, `OSS-004D`
- Optional follow-on batch after stable `EP4`: `EP5-001`
- Deferred from the first batch by default: `EP5-002`

`EP5-002` remains a later canary/live proof slice. Even if it stays in the session as a proposed task, it should not be included in the first materialization command for this planning round unless a later explicit gate says the session is now making an EP5 proof claim.

## P0

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| `OSS-004A` | Gemini | Codex | - | P1 | Stabilize the runtime auth/authority path for EP4: runtime-manager token flow, paper-runtime identity, telemetry authority references, and OpenClaw/Pantheon adapter boundary needed for a truthful governed paper run. |
| `OSS-004B` | Claude | Gemini | `OSS-004A` | P1 | Replace the VM-2 bootstrap paper runtime with the final paper execution package or final signal-consumer path so `DEPLOY-009` no longer stops at bootstrap health. |

## P1

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| `OSS-004C` | Gemini | Codex | `OSS-004A`, `OSS-004B` | P2 | Run and archive one governed paper execution acceptance proving approval -> deployment -> runtime binding -> paper execution -> telemetry -> incident/health -> kill-switch/rollback as one EP4 packet. |
| `OSS-004D` | Codex | Claude | `OSS-004C` | P2 | Publish the EP4 evidence packet and reconcile status/tracking layers so the repo can truthfully claim stable `EP4` and nothing higher. |

## P2

| Task ID | Owner | Reviewer | Depends On | Wave | Notes |
|---|---|---|---|---|---|
| `EP5-001` | Gemini | Claude | `OSS-004C` | P3 | Prepare canary-ready execution path: real broker/venue config, scaled capital gate, operator approval checklist, and rollback drill harness. |
| `EP5-002` | Claude | Codex | `EP5-001` | P4 | Execute and archive the first canary/live proof packet, including rollback drill and operator signoff, if human gate and infrastructure prerequisites are satisfied. |
