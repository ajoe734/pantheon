# Round 2 — Design Team Formal Reply (2026-05-19)

Date: 2026-05-19 (later same day)
Source: [`Pantheon_設計團隊_衝突疑義正式回覆_2026-05-19`](../../../../04/pantheon_design_blueprint_supplement_2026-05-19/INDEX.md) — Traditional Chinese reply from the design team to the seven open questions surfaced in this session's README. (Same encoding-corruption pattern; engineering decisions reconstructed in English below.)

## Headline ruling

> **All pre-gate engineering dispatches now. Don't wait for any planning session. Human gate only blocks the final activation actions.**

Concretely, Track B's `discussion_planning` routing is dropped — the 2026-05-19 blueprint supplement is treated as "design team master implementation spec," not as a final L1-amended consensus packet. L1 amendments themselves remain gated, but they're a separate output (draft documents); the engineering work to build their pre-conditions ships immediately.

## Resolutions per surfaced question

| # | Question | Design team ruling | Action taken in round 2 |
|---|---|---|---|
| 1 | Lost 2026-05-18 GOLIVE batch | Clean restart from V2. Do not recover GOLIVE active tasks. Add audit-only recon task. | Created `GOLIVE-RESET-RECON-001` (audit_reconciliation, blocks_execution=false) |
| 2 | HumanGateDecision split | Schema/validator/TTL/revoke in **EP5-003-V2 (core)** and **EP5-004-V2 (lifecycle)**; **HG-005-V2** (audit projection) and **HG-006-V2** (UI read model) remain standalone | Re-spec'd EP5-003-V2 title to "HumanGateDecision core schema + signoff API"; re-spec'd EP5-004-V2 title to "HumanGateDecision lifecycle operations"; HG-005/006-V2 kept as standalone |
| 3 | OPS-WAVE tasks | **Dispatch all 5.** Delivery governance is design-team scope, not ops to self-implement. Note: OPS-WAVE-004/005 carry **new scope** (release branch discipline; evidence retention) vs the 2026-05-18 packet | Dispatched `OPS-WAVE-001-V2..005-V2` per new scope |
| 4 | RES-ACT generic vs adapter-specific | Layered: **generic parent + adapter-specific child evidence**. Parent scope also re-spec'd | Re-spec'd RES-ACT-002/003/004/006-V2 (still todo); RES-ACT-001 in review and RES-ACT-005 already done are left as-is. Dispatched 6 adapter children (QLIB / TRL / RL / WANDB / QUANTLIB / STAT). Dispatched `RES-ACT-DISCLOSURE-001-V2` to cover the design team's re-spec'd RES-ACT-005 scope (real/stub disclosure report) that the originally-delivered RES-ACT-005-V2 didn't address |
| 5 | OODA-CANARY-004 deps | `depends_on: EP5-007-V2` correct as-is. Optional adds (M7-CANARY-CLOSEOUT, EP5-003-V2, EP5-004-V2) noted but not required | No change |
| 6 | LIVE-gate placeholders | Status=todo originally, status=blocked after chair-review intervention; must add: `non_dispatchable: true`, `gate_status: pending_human_go_no_go`, `allowed_workers: []`, `human_required_roles: ["risk_owner", "operator"]`, `activation_effect: ["irreversible_or_high_risk"]` | Applied all 5 metadata fields to `BLA-LIVE-001-V2` / `CBL-LIVE-001-V2` / `HA-PROD-001-V2` via TASK_METADATA_JSON |
| 7 | broker evidence refs | Two **distinct** refs: `broker_sandbox_smoke_ref` (generic broker readiness) and `shioaji_sandbox_evidence_packet_ref` (Shioaji-specific) | No change (already implemented as two refs) |
| 8 | Track B routing | **Direct dispatch.** 2026-05-19 supplement is design-team master spec, not L1-amended consensus. Pre-gate engineering ships now; only `*-LIVE-001` / `*-PROD-001` activations need human gate | Dispatched `BLA-001-V2..010-V2` (10), `CBL-001-V2..007-V2` (7), `HA-001-V2..010-V2` (10) — all 27 Track B engineering tasks |

## Round 2 dispatch summary

**40 new tasks added** (`ai-status.json:tasks` count went from 37 → 67 + some progress turnover; 6 V2 tasks from round 1 already terminal-archived):

| Group | IDs | Count |
|---|---|---:|
| Audit recon | `GOLIVE-RESET-RECON-001` | 1 |
| Delivery governance | `OPS-WAVE-001-V2..005-V2` | 5 |
| Research adapter children | `RES-ACT-QLIB-001-V2`, `RES-ACT-TRL-001-V2`, `RES-ACT-RL-001-V2`, `RES-ACT-WANDB-001-V2`, `RES-ACT-QUANTLIB-001-V2`, `RES-ACT-STAT-001-V2` | 6 |
| Research disclosure (round-1 RES-ACT-005 re-spec) | `RES-ACT-DISCLOSURE-001-V2` | 1 |
| Broker live engineering (Track B) | `BLA-001-V2..010-V2` | 10 |
| Capital binding live engineering (Track B) | `CBL-001-V2..007-V2` | 7 |
| BFF HA engineering (Track B) | `HA-001-V2..010-V2` | 10 |
| **Round 2 new total** | | **40** |

**6 re-specs** (title + summary updated via `assign` for `status=todo` tasks):

- `EP5-003-V2` → "HumanGateDecision core schema + signoff API"
- `EP5-004-V2` → "HumanGateDecision lifecycle operations"
- `RES-ACT-002-V2` → "Research artifact admission gate"
- `RES-ACT-003-V2` → "Production data entitlement and PIT validator"
- `RES-ACT-004-V2` → "No-order-route enforcement test harness"
- `RES-ACT-006-V2` → "Research activation dashboard read model"

**3 metadata updates** (LIVE-gate placeholders):

- `BLA-LIVE-001-V2`, `CBL-LIVE-001-V2`, `HA-PROD-001-V2` now carry `non_dispatchable: true`, `gate_status: pending_human_go_no_go`, `allowed_workers: []`, `human_required_roles: ["risk_owner", "operator"]`, `activation_effect: ["irreversible_or_high_risk"]`.

## Round 1 tasks already terminal (snapshot)

By the time round 2 dispatched (~15:30 UTC), the following round-1 V2 tasks had already completed and been archived:

- `LSP-001-V2`, `LSP-002-V2`, `LSP-003-V2`, `LSP-004-V2`
- `OODA-CANARY-001-V2`
- `RES-ACT-005-V2`

`RES-ACT-005-V2` was delivered against the round-1 spec (no-order-route scanner). The design team's re-spec'd RES-ACT-005 intent (real/stub disclosure report) is captured by the newly-dispatched `RES-ACT-DISCLOSURE-001-V2`.

Other round-1 tasks in `review`/`in_progress` at round-2 dispatch time:

- `RES-ACT-001-V2` (review) — delivered against round-1 spec ("production data proof schema"); the design team's broader re-spec'd intent ("Production research activation evidence framework" including activation_evidence_schema + production_data_proof_schema + no_order_route_proof_schema) is partially covered; gap can be filed as a follow-up if reviewer flags.
- `WNB-ACT-001-V2` (review) — delivered against round-1 spec. The canonical replacement child `RES-ACT-WANDB-001-V2` was dispatched in round 2.

## Dispatch invariant (unchanged)

> Pre-gate engineering all dispatches. Only `*-LIVE-001-V2` / `*-PROD-001-V2` placeholders are non-dispatchable and await human go/no-go.

## Final L1 amendment posture

The 2026-05-19 supplement does not unilaterally amend L1 canonical (`PAPER_CANARY_LIVE_POLICY.md`, `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, etc.). Engineering proceeds against the supplement spec. If implementation surfaces inconsistencies with L1, those will be captured as L1 amendment-draft tasks (a separate output of this session) that then route through the normal `discussion_planning` / human-gate path for L1 changes.
