# Consensus Packet

## Decision Summary

- Session: `phase7-2026-04-18-ep4-ep5-execution-proof`
- Scope: Accept an `EP4-first` planning boundary for this round. The accepted scope is the stable-`EP4` path (`OSS-004A` through `OSS-004D`) plus explicit downstream treatment of `EP5-001` as prerequisite-only and `EP5-002` as deferred. This session does not claim `EP5` proof completion. (`planning-session.json:8-9`, `planning-session.json:152-305`, `starter-draft.md:8-21`)
- Accepted architecture: `OSS-004A` hardens runtime auth/authority, workspace isolation, and telemetry parity; `OSS-004B` replaces the bootstrap paper runtime with the truthful paper execution substrate; `OSS-004C` runs the integrated governed paper acceptance including rollback evidence; `OSS-004D` publishes EP4 evidence and reconciles repo status truth. (`planning-session.json:152-254`, `OPENCLAW_RUNTIME_CONTRACT.md:218-221`, `PAPER_CANARY_LIVE_POLICY.md:269-270`, `ROLLBACK_AND_POSITION_SEMANTICS.md:57-69`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md:27`)
- Delivery order: `OSS-004A -> OSS-004B -> OSS-004C -> OSS-004D`, then optionally `EP5-001` as a post-EP4 prerequisite wave. `EP5-002` remains outside the initial materialization batch and requires a later explicit gate. (`planning-session.json:152-305`, `execution-materialization.md:7-26`)

## Agreed Task Slices

- Task 1: `OSS-004A` and `OSS-004B` are the EP4 proof-raising substrate. Their acceptance must explicitly verify per-agent workspace isolation, no implicit credential sharing, truthful paper-runtime packaging, and telemetry parity fields required for later reconciliation. (`planning-session.json:152-202`, `OPENCLAW_RUNTIME_CONTRACT.md:218-221`, `PAPER_CANARY_LIVE_POLICY.md:269-270`)
- Task 2: `OSS-004C` is the first integrated governed paper proof packet. It must archive approval, deployment, runtime binding, paper execution, telemetry, incident/health, and at least one cited `pause_then_replace` rollback drill as one EP4 evidence bundle. (`planning-session.json:203-229`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md:27`, `ROLLBACK_AND_POSITION_SEMANTICS.md:57-69`)
- Task 3: `OSS-004D` publishes the EP4 evidence packet and reconciles status/tracking layers so the repo can truthfully claim stable `EP4` and nothing higher. (`planning-session.json:230-254`)
- Task 4: `EP5-001` stays in the session as a downstream prerequisite-only wave, not as proof completion. (`planning-session.json:257-280`, `execution-materialization.md:14-21`)
- Task 5: `EP5-002` remains explicitly deferred from the first materialization batch and should only be revived behind a later human-approved gate. (`planning-session.json:281-305`, `execution-materialization.md:11-12`, `execution-materialization.md:22-26`)

## Open Questions / Human Gate

- Item 1: Confirm that the first materialization batch for this session is `OSS-004A`, `OSS-004B`, `OSS-004C`, and `OSS-004D` only.
- Item 2: Confirm whether `EP5-001` should remain a downstream tracked slice in this same planning packet or be moved into a later phase-specific planning session after stable `EP4` evidence is published.
- Item 3: Confirm that `EP5-002` is not part of this round's first execution batch and requires a later explicit canary/live proof decision.

## Acceptance Note

- Cross-review has converged on the EP4-first architecture, the required isolation/telemetry/rollback constraints, and the deferred `EP5-002` boundary. This packet is ready for human acceptance on the materialization boundary described above.
