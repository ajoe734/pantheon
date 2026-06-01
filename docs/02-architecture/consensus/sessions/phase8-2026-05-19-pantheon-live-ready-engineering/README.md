# Phase 8 — Pantheon Live-Ready Engineering (2026-05-19 baseline)

## Session

- Session ID: `phase8-2026-05-19-pantheon-live-ready-engineering`
- Profile: `mixed_dispatch`
- Status: `accepted`
- Date opened: 2026-05-19
- Facilitator: `Claude`
- Source design packet: [`docs/04/pantheon_design_blueprint_supplement_2026-05-19/`](../../../../04/pantheon_design_blueprint_supplement_2026-05-19/)
- Companion planning briefs (for the Track B `discussion_planning` sub-sessions):
  - [`docs/04/planning_briefs_2026-05-17/BROKER_LIVE_ACTIVATION_PLANNING_BRIEF.md`](../../../../04/planning_briefs_2026-05-17/BROKER_LIVE_ACTIVATION_PLANNING_BRIEF.md)
  - [`docs/04/planning_briefs_2026-05-17/BFF_HA_TOPOLOGY_PLANNING_BRIEF.md`](../../../../04/planning_briefs_2026-05-17/BFF_HA_TOPOLOGY_PLANNING_BRIEF.md)

## History

A predecessor session `phase8-2026-05-18-pantheon-live-ready-engineering` was opened against the 2026-05-18 SA/SD packet and 31 `-GOLIVE` tasks were dispatched. The worktree was reset to `origin/dev` overnight (git reflog: `7d21e88c HEAD@{2026-05-19}: reset: moving to HEAD` followed by `merge origin/dev: Fast-forward`), removing both the 2026-05-18 archive directory and the 31 `-GOLIVE` task entries. Activity-log entries for those assigns survive in `ai-activity-log.jsonl`. Today's dispatch is a clean restart from the 2026-05-19 packet.

## Objective

Take Pantheon from EP4 governed paper-ready to **L3 Live-ready** by materializing all pre-gate engineering defined in the 2026-05-19 consolidated blueprint supplement. Human risk-owner / operator / infra decision-maker retain the final activation gate; engineering does not wait on them.

## Hard invariants (inherited from L1)

1. No `BROKER_PRODUCTION_LIVE_ENABLED=true`, `CAPITAL_BINDING_LIVE_ENABLED=true`, or production real-writes flag flips inside this session.
2. Live activation requires human risk-owner + operator dual sign-off and 14d paper + 7d canary evidence chain.
3. Strict mode in BFF / Lovable must remain "no silent fallback"; `VITE_BFF_REAL_WRITES=false` is the correct posture.
4. Research adapters may not emit broker orders or mutate runtime binding.
5. All evidence packets follow the retention class table in 2026-05-19 supplement Part H5.

## Dispatch routing

This session uses a **mixed dispatch model**:

### Track A — direct dispatch (33 tasks)

Each task carries the **`-V2` suffix** to avoid colliding with historical archived task IDs (e.g. the existing archived `EP5-001`, `QLIB-ACT-001`, `EP5-BROKER-TW-*`).

| Workstream | EPIC | Task range | Count |
|---|---|---|---|
| WS1 EP5 Canary Proof | EPIC-EP5 | `EP5-001-V2` … `EP5-010-V2` | 10 |
| WS5 Strict Lovable Publish | EPIC-LSP | `LSP-001-V2` … `LSP-006-V2` | 6 |
| WS6 Research Production Activation | EPIC-RES-ACT | `RES-ACT-001-V2` … `RES-ACT-006-V2`, `WNB-ACT-001-V2` | 7 |
| WS7 Canary/Live OODA | EPIC-OODA-CANARY | `OODA-CANARY-001-V2` … `OODA-CANARY-005-V2` | 5 |
| WS8 Delivery Governance (residual HG bits) | EPIC-HG | `HG-005-V2` audit projection, `HG-006-V2` UI read model | 2 |
| LIVE-gate placeholders | (across WS2/WS3/WS4) | `BLA-LIVE-001-V2`, `CBL-LIVE-001-V2`, `HA-PROD-001-V2` | 3 |

**Direct dispatch total: 33 tasks.**

### Track B — `discussion_planning` (held, 27 tasks)

These workstreams touch L1 canonical files and may NOT be dispatched as single-AI tasks. They route through dedicated `discussion_planning` sub-sessions per `AI_COLLABORATION_GUIDE.md` § 2.5.

| Workstream | Task range | Count | Sub-session (to be opened) | L1 canonical touched |
|---|---|---|---|---|
| WS2 Broker Live engineering | `BLA-001-V2` … `BLA-010-V2` | 10 | `phase8-2026-05-XX-broker-live-activation-criteria` | PAPER_CANARY_LIVE_POLICY, KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY, BINDING_AND_DEPLOYMENT_SEMANTICS, ROLLBACK_AND_POSITION_SEMANTICS |
| WS3 Capital Binding Live engineering | `CBL-001-V2` … `CBL-007-V2` | 7 | (extension round of broker-live session) | PAPER_CANARY_LIVE_POLICY, BINDING_AND_DEPLOYMENT_SEMANTICS, MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION |
| WS4 BFF HA engineering | `HA-001-V2` … `HA-010-V2` | 10 | `phase8-2026-05-XX-bff-ha-topology-poc` | BFF_HA_AND_CONTROL_PLANE_RESILIENCE |

**`discussion_planning` total: 27 tasks (held until consensus packets reach `accepted`).**

## Open questions / conflicts surfaced to design team

Documented here rather than blocked. Implementation may proceed with the default interpretation noted; if the design team disagrees, the affected tasks will re-spec.

1. **HumanGateDecision implementation split.** The 2026-05-19 spec consolidates HumanGateDecision schema, validator, TTL/revoke/expire, and evidence-hash binding into `EP5-003-V2` (signoff record API) + `EP5-004-V2` (revoke/expire semantics). The 2026-05-18 packet had six discrete HG tasks. The audit-log projection and the UI read model are **not covered** by EP5-003/004 — they are dispatched here as `HG-005-V2` (audit projection) and `HG-006-V2` (UI read model). If the design team intended those to also be absorbed elsewhere, mark these two as `not needed` and we will close them.

2. **OPS-WAVE-* tasks not dispatched.** The 2026-05-19 spec defines wave cadence schema in Part H1/H4 but enumerates no engineering tasks. The 2026-05-18 packet had five explicit `OPS-WAVE-*` tasks. **Default:** wave cadence implementation is treated as an ops-led track outside this session. If the design team wants `OPS-WAVE-001-V2..005-V2` dispatched against H1/H4, say so and we will dispatch them.

3. **Research activation: generic vs adapter-specific.** The 2026-05-19 spec generalizes research activation into `RES-ACT-001..006` + `WNB-ACT-001` (no separate Qlib/TRL/FinRL IDs). The 2026-05-18 packet had per-adapter tasks (`QLIB-ACT-*`, `TRL-ACT-*`, `RL-ACT-*`). **Default:** dispatch the generic IDs; implementers produce per-adapter evidence as deliverables of the generic tasks (e.g. `RES-ACT-001-V2` "production data proof schema" outputs include the Qlib production data proof packet, the TRL preference data proof packet, etc.). If the design team wants adapter-specific dispatch, we will split.

4. **OODA-CANARY-* dependency on EP5.** `OODA-CANARY-004-V2` (rollback drill linkage) functionally depends on `EP5-007-V2` (rollback drill harness). **Default:** declare the dependency explicitly so the supervisor doesn't dispatch OODA-CANARY-004-V2 until EP5-007-V2 is `review_approved`. `OODA-CANARY-001/002/003/005-V2` are independent and dispatch immediately.

5. **LIVE-gate placeholder task class.** `BLA-LIVE-001-V2`, `CBL-LIVE-001-V2`, `HA-PROD-001-V2` are not engineering work — they represent the human go/no-go event itself. **Default:** create them with `owner: Human/Ops`, `task_class: human_gate`, `status: blocked`, with `waiting_for` pointing to the relevant readiness packet. Visible in dashboard for tracking, not dispatchable to AI workers.

6. **`broker_sandbox_smoke_ref` vs `shioaji_sandbox_evidence_packet_ref`.** Same question as 2026-05-18: the schema has both fields listed in PromotionReadinessPacket evidence. **Default:** treat them as two distinct refs (the former is generic broker smoke; the latter is Shioaji-specific evidence packet). Distinguishes pluralizes broker without conflating.

7. **Track B routing — does the 2026-05-19 spec satisfy multi-AI consensus?** The new spec is comprehensive on Broker Live, Capital Binding Live, BFF HA. `AI_COLLABORATION_GUIDE.md` § 2.5 requires multi-lane readout + cross-review + Claude reconciliation + human gate for L1-touching work. **Default:** still route Track B through `discussion_planning`. If the design team has already produced this packet as the multi-AI consensus (vs. a single-author proposal), we can authorize direct dispatch of Track B.

## Planning stages

Opened by direct receipt of external SA + SD + Task Specification packet (the design team's handoff), not internal discussion_planning. The 2026-05-19 packet is the consensus document; this README + `planning-session.json` are operational follow-through.

1. Document reconciliation: source archive at [`docs/04/pantheon_design_blueprint_supplement_2026-05-19/`](../../../../04/pantheon_design_blueprint_supplement_2026-05-19/) (English rewrite; original Chinese narrative lost to transit-time encoding error; schemas/IDs/acceptance preserved byte-faithfully).
2. Materialization: 33 Track A tasks added to `ai-status.json` with `-V2` suffix; Track B 27 tasks held in `planning-session.json:deferred_tasks_pending_subsession`.
3. Sub-session opening: Track B sub-sessions (broker-live + capital-binding-live shared, bff-ha-topology separate) must publish their own consensus packets before any held task moves from `planned` to `in_progress`.

## Materialization manifest

See [execution-materialization.md](execution-materialization.md) for the full per-task plan including owner / reviewer / depends_on / artifacts / acceptance.
