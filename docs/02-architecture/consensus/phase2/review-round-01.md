# Review Round 01

Use cited comments only. Do not directly rewrite `starter-draft.md` unless you currently hold the baton.

Session: `phase2-2026-04-12-blueprint-gap-convergence`
Completed: 2026-04-12T13:30:00Z

## Reviewer Order

- Qwen ✓
- Gemini ✓
- Copilot — waived (quota exhausted; covered by Codex + Claude per fallback policy)
- Claude ✓

---

## Qwen Comments

**On BG-001/BG-003 object boundaries (CONFIRMED, no objection):**
- Starter draft correctly states no `SecurityMaster`, `ContractMaster`, `RegimeState`, `UniverseSelection`, or `SignalInference` exist in `services/**`. Qwen readout independently verified this via grep — zero hits for all seven Data Plane objects and five Decision-front objects. (`qwen-readout.md §Risks/Contradictions`)
- `StrategySpec.market_scope` free-form arrays (`symbols[]`, `asset_classes[]`, `venues[]`) must reference SecurityMaster/ContractMaster by ID once BG-001 lands. BG-001 must explicitly add a backward-compatibility note for this migration. (`services/control-plane/specs/strategy_spec.schema.json:38-69`)
- Telemetry schema will need an optional `data_refs[]` extension once Data Plane objects exist. BG-001 should note this as a follow-on concern for TEL-001 revision. (`services/telemetry/telemetry_event.schema.json:7-117`)

**On BG-006 scope (CONFIRMED, minor note):**
- GAP-06 is a documentation/acceptance packaging gap, not a missing capability. `BFF_SURFACE_INVENTORY.md`, `BFF_API_CONTRACT.md`, and `DEGRADED_OPERATOR_PATH.md` already supply all needed raw material. BG-006 should consolidate rather than redefine. (`qwen-readout.md §Slice 3`)

**On wave ordering disagreement (AGREED with Codex correction):**
- BG-005 should be treated as P0 acceptance gate, not P2. The blueprint priority table is authoritative. The session JSON phase label is a seed artifact that the consensus-packet should correct. (`Pantheon_Blueprint_Gap_Review_v1.md:714-726`)

---

## Gemini Comments

**On BG-002 scope (PARTIALLY CONFIRMED — scope clarification needed):**
- Gemini readout proposes `OSS-002-DOC` as a sub-task within BG-002 to create `integration.md` and `governance.md` for DSPy, imitation, and MLflow. (`gemini-readout.md §Slice 1`)
- **Facilitator resolution**: BG-002 scope = maturity matrix + production-path mapping only. Documentation hardening to graduate adapters from `smoke-tested` to `governed` is a follow-on task (`OSS-002-DOC`) to be activated after BG-002 closes. This keeps BG-002 scoped to the policy artifact, not implementation work.

**On BG-005 replay anchor (CONFIRMED, not just synthetic telemetry):**
- BG-005 replay must be anchored to real Data Plane objects (dataset version, security/contract IDs), not just synthetic telemetry events. Without BG-000 + BG-001 + BG-003, the replay has no market truth. (`gemini-readout.md §Risks Risk 1`; `services/execution/artifact-loader/contract.md §6`)
- LEAN algorithm-level smoke coverage remains deferred (`EX-001 §6`). BG-005 acceptance should explicitly exclude LEAN algorithm-level smoke as a prerequisite — that is a separate gate.

**On market/symbol mapping risk (CONFIRMED):**
- Without a formal Symbol Master and Calendar truth, Research results and Replay data can drift across providers. BG-000 is correctly Wave 0 before BG-001 and BG-003 can stabilize.

---

## Copilot — waived

Copilot lane hit quota exhaustion before submitting a readout (116-byte log, exit code 402).
Per `planning-session.json §fallback_policy`, Copilot is waived and its gap coverage (GAP-00, GAP-02, GAP-07) is absorbed by Codex (market/source scope, BG-002 task slicing) and Claude (facilitator synthesis).
Issue recorded: `DISC-COPILOT-PLANNING`.

---

## Claude (Facilitator) Resolution

All three open disagreements from `starter-draft.md` are now resolved:

**Disagreement 1 — BG-005 priority:**
- RESOLVED: BG-005 is P0, not P2. Blueprint priority table (`Pantheon_Blueprint_Gap_Review_v1.md:714-718`) is authoritative. consensus-packet.md and ai-status.json will use phase `Blueprint Gap P0` for BG-005.

**Disagreement 2 — BG-005 scope (real anchor vs. synthetic replay):**
- RESOLVED: BG-005 must produce a runbook that pins real `DatasetVersion` IDs (from BG-001) and real `RegimeState`/`UniverseSelection`/`SignalInference` chain refs (from BG-003). Synthetic telemetry replay does not satisfy GAP-05. Acceptance must include at least one equities path and one derivatives-aware path per market-data brief. LEAN algorithm-level smoke is explicitly out of scope.

**Disagreement 3 — BG-002 scope:**
- RESOLVED: BG-002 = maturity matrix + production-path mapping document only. Doc hardening to graduate adapters is a follow-on (`OSS-002-DOC`), tracked as an unresolved item in this packet but not a blocker for BG-002 acceptance.

**Priority corrections (BG-004 and BG-007):**
- BG-004 and BG-007 stay P2 per blueprint table. Session seed labels them P1 in error. consensus-packet corrects this.

Round 1 is complete. Proceeding to consensus-packet synthesis.
