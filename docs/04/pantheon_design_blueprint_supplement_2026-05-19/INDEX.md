# Pantheon Design Blueprint Supplement — 2026-05-19

Date archived: 2026-05-19
Source: System Design team handoff to development team (consolidated SA + SD + Task Specification packet)
Original language: Traditional Chinese; the original narrative was lost to encoding corruption in transit. **YAML schemas, Mermaid diagrams, task ID tables, JSON contracts, and acceptance matrices are byte-faithful to the source.** Narrative sections have been rewritten in English by the receiving Claude lane on 2026-05-19 to make the archive readable; the engineering intent (workstream definition, schemas, runbooks, task breakdown) is preserved.

## Relationship to prior packets

This packet is a **re-baseline**, not an incremental delta. An earlier 2026-05-18 packet (`docs/04/pantheon_go_live_standard_2026-05-18/`) was drafted but the worktree was reset to `origin/dev` overnight, removing both the archive directory and the 31 dispatched `-GOLIVE` tasks. Effective state today: **clean dispatch from this packet only**; there is no 2026-05-18 packet remaining in the repo to supersede.

Material differences vs 2026-05-18:

| Area | 2026-05-18 | 2026-05-19 | Net change |
|---|---|---|---|
| EP5 tasks | 7 (EP5-001..007) | 10 (EP5-001..010) + `EP5ProofPacket` schema split out from `PromotionReadinessPacket` | +3, IDs re-shuffled |
| Human Gate tasks | 6 explicit (HG-001..006) | 0 explicit — folded into EP5-003 (signoff record API) + EP5-004 (revoke/expire) | Consolidated |
| OPS-WAVE tasks | 5 explicit (OPS-WAVE-001..005) | 0 explicit — wave cadence schema kept in Part H1 but no task IDs enumerated | Consolidated to ops |
| LSP tasks | 6 (focus: manifest + audit run) | 6 (focus: CI wrapper + browser probe + bundle hash + scan + evidence + gate checker) | Re-spec, same count |
| Research activation | 7 adapter-specific (QLIB-ACT-*, TRL-ACT-*, RL-ACT-001, WNB-ACT-001, RES-GATE-001) | 7 generic (RES-ACT-001..006 + WNB-ACT-001) | Re-spec, same count |
| Canary OODA proof | (absent) | 5 new tasks (OODA-CANARY-001..005) | +5 new workstream |
| Broker live engineering | 11 (BLA-001..011) | 10 + 1 LIVE gate placeholder (BLA-001..010, BLA-LIVE-001) | -1 net (rolled into LIVE gate task) |
| Capital binding live | 8 (CBL-001..008) | 7 + 1 LIVE gate placeholder (CBL-001..007, CBL-LIVE-001) | -1 net |
| BFF HA | 11 (HA-001..011) | 10 + 1 LIVE gate placeholder (HA-001..010, HA-PROD-001) | -1 net |
| Workstream framing | EPIC-* | WS1..WS8 (Workstream taxonomy) | Same scope, new label |

## Files

| File | Role | Highlights |
|---|---|---|
| [pantheon_blueprint_supplement.md](pantheon_blueprint_supplement.md) | Consolidated SA + SD + Task Specification | 8 workstreams (WS1..WS8); 13 sections; Final Acceptance Matrix |

## Dispatch routing (decided 2026-05-19)

This packet lands in a fresh planning session: [`phase8-2026-05-19-pantheon-live-ready-engineering`](../../02-architecture/consensus/sessions/phase8-2026-05-19-pantheon-live-ready-engineering/).

Track A (direct dispatch, recommended for re-dispatch):
- WS1 EP5 Canary Proof — EP5-001..010 (10 tasks)
- WS5 Strict Lovable Publish — LSP-001..006 (6 tasks)
- WS6 Research Production Activation — RES-ACT-001..006 + WNB-ACT-001 (7 tasks)
- WS7 Canary/Live OODA — OODA-CANARY-001..005 (5 tasks) — **new workstream**
- (HG and OPS-WAVE absorbed per new doc; existing HG-*-GOLIVE / OPS-WAVE-*-GOLIVE need disposition decision)

Track B (`discussion_planning` — same routing as 2026-05-18):
- WS2 Broker Live — BLA-001..010 + BLA-LIVE-001 (11 tasks)
- WS3 Capital Binding Live — CBL-001..007 + CBL-LIVE-001 (8 tasks)
- WS4 BFF HA — HA-001..010 + HA-PROD-001 (11 tasks)

LIVE-gate placeholder tasks (BLA-LIVE-001, CBL-LIVE-001, HA-PROD-001) are anchors for the actual human go/no-go event — they are not engineering work and should be created with `task_class: human_gate`.

## ID convention

To avoid colliding with the historical archived `EP5-001` / `EP5-002` / `QLIB-ACT-001` / `QLIB-ACT-002` / `EP5-BROKER-TW-*` task IDs (all `review_approved` and archived earlier this sprint), the new tasks dispatched against this 2026-05-19 packet carry the **`-V2` suffix**: `EP5-001-V2`, `LSP-001-V2`, `RES-ACT-001-V2`, `OODA-CANARY-001-V2`, etc.

## Core principle (unchanged from 2026-05-18)

> **Gates block activation, not engineering.**

All pre-gate engineering may dispatch immediately. Only the following actions require human go/no-go:

- Broker production live enable (`BLA-LIVE-001`)
- Capital binding live enable (`CBL-LIVE-001`)
- Production BFF HA cutover (`HA-PROD-001`)
- Production real writes enable
- Live capital scale-up
- Canary activation (per readiness packet flags)
