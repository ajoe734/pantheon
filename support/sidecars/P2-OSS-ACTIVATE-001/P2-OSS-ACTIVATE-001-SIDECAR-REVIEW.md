# P2-OSS-ACTIVATE-001 Review Packet (Sidecar)

**Sidecar Task**: `P2-OSS-ACTIVATE-001-SIDECAR-REVIEW`
**Parent Task**: `P2-OSS-ACTIVATE-001` — Research OSS production data posture and activation
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex2
**Helper Kind**: `review_packet`
**Parent Status at preparation**: `review_approved` (reviewer: Codex; returned to Codex2 for closeout)
**Parent Status at handoff / current**: `done` — archived at 2026-05-01T15:33:19Z, commit `05d52eb`, before this sidecar was handed off at 15:35Z
**Generated**: 2026-05-01
**Branch**: `backend-dev-publish-20260429`

> This is a support artifact only. It summarizes the evidence gathered during `P2-OSS-ACTIVATE-001` implementation and the findings of the Codex review. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations. Its purpose is to give Codex2 (parent owner) and future auditors a structured evidence packet alongside the approved review.

---

## 1. Parent Task Summary

`P2-OSS-ACTIVATE-001` was implemented by Codex2, reviewed by Codex, and approved on 2026-05-01. The implementation:

- Added `services/learning/OSS_ACTIVATION_NOTES.md` — explicit production data posture and activation notes for each OSS component.
- Updated `OSS_INTEGRATION_CHECKLIST.md` to reference the activation notes packet and confirm Qlib/TRL remain at `smoke-tested` status.
- Confirmed existing fail-closed enforcement in `services/source_ingestion/`, `services/search/`, and `services/openclaw-gateway-adapter/`.

**Status at packet preparation (2026-05-01T~15:30Z):** The parent task was in `review_approved` state; Codex2 was expected to run the closeout checklist.

**Status update:** Parent task `P2-OSS-ACTIVATE-001` was subsequently archived as `done` at 2026-05-01T15:33:19Z (commit `05d52eb`) before this sidecar review packet was handed off to Codex2 at 15:35Z. The closeout was completed by Codex2 and the parent is now formally closed. This sidecar packet remains as an auditable evidence summary for the closed parent task.

---

## 2. Artifacts Verified by Codex Review

| Artifact | Path | Role |
|---|---|---|
| OSS integration checklist | `OSS_INTEGRATION_CHECKLIST.md` | Component inventory; Qlib/TRL remain `smoke-tested` |
| OSS activation notes | `services/learning/OSS_ACTIVATION_NOTES.md` | Task evidence: posture, gates, prerequisites per component |
| External source connectors | `services/source_ingestion/external_sources.py` | SourceRecord/EvidenceBundle enforcement |
| Search gateway | `services/search/gateway.py` | ACL/license/available_time enforcement |
| Search filters | `services/search/filters.py` | Pre-ranking filter layer |
| OpenClaw search facade | `integrations/openclaw/search_gateway.py` | Evidence-ref-only output; no raw payload |
| Tool/workflow bridge | `services/openclaw-gateway-adapter/tool_workflow_bridge.py` | Deny-first broker/live block |
| Paper broker adapter | `services/openclaw-gateway-adapter/paper_broker_adapter.py` | Opt-in only; real-order false |
| Live gate adapter | `services/openclaw-gateway-adapter/live_gate_adapter.py` | Dry-handoff harness; live execution disabled |

---

## 3. Acceptance Criteria Evaluation

### A1 — Production data posture, not blanket live-data ban

**Status: PASS** (Codex review, 2026-05-01)

`services/learning/OSS_ACTIVATION_NOTES.md` explicitly establishes that production research data ingestion is allowed when durable storage, entitlement, license/PIT, rate-limit, freshness, and audit posture are complete. `OSS_INTEGRATION_CHECKLIST.md` now references that packet and keeps Qlib and TRL at `smoke-tested` with `draft` / `none` output posture.

### A2 — No OSS/source path bypasses governed controls

**Status: PASS** (Codex review, 2026-05-01)

- `services/source_ingestion/external_sources.py` requires `entitlement_tags`, `license_scope`, `access_scope`, `event_time`, `available_time`, content hash, and a `SourceRecord/EvidenceBundle` governance sink. Direct Lean, broker, runtime, or order-routing destinations are explicitly rejected.
- `services/search/gateway.py` applies ACL/license/workspace/environment and `available_time` controls before ranking; OpenClaw retrieval requires citations.
- `integrations/openclaw/search_gateway.py` returns sanitized `evidence_bundle_id` and citation refs only — no raw payloads or execution handoffs.

### A3 — Activation notes identify remaining prerequisites without silently enabling execution

**Status: PASS** (Codex review, 2026-05-01)

The activation notes (`OSS_ACTIVATION_NOTES.md`) document:
- Qlib: blocked on RS-003 candidate artifact, governed ≥50 instrument/≥2-year OHLCV dataset proof, target StrategySpec binding, production credential/storage evidence. Gate: `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1`.
- TRL: blocked on ≥200 governed FB-002 events, ≥100 valid preference pairs, approved LP-002 imitation baseline, baseline-model proof, ready downstream consumer. Gate: `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1`.
- OpenClaw broker/live path: hard-rejected; paper adapter is opt-in (`OPENCLAW_PAPER_ADAPTER_ENABLED=true`); live gate adapter is fail-closed with all five gate checks required.

None of these notes enable order-capable execution paths.

---

## 4. Test Verification Evidence

The following test run was recorded by Codex2 in the handoff message (2026-05-01T15:22:36Z) and confirmed by Codex during review:

```
python3 -m pytest \
  services/source_ingestion/tests/test_external_source_connectors.py \
  services/source_ingestion/tests/test_bounded_ingestion.py \
  services/search/tests/test_governed_search.py \
  services/openclaw-gateway-adapter/test_tool_workflow_bridge.py \
  services/openclaw-gateway-adapter/test_live_gate_adapter.py \
  -q
=> 111 passed in 29.07s
```

```
git diff --check -- OSS_INTEGRATION_CHECKLIST.md
=> passed (no whitespace errors)
```

The worktree contained unrelated dirty files from other tasks and orchestration artifacts. Codex confirmed these are not part of the P2-OSS-ACTIVATE-001 scope.

---

## 5. Control Surface Summary

From `services/learning/OSS_ACTIVATION_NOTES.md` (confirmed by review):

| Control | Enforcement location | Gate status |
|---|---|---|
| SourceRecord integrity | `services/source_ingestion/external_sources.py` | ENFORCED — requires entitlement, license, PIT, content hash, EvidenceBundle sink |
| EvidenceBundle construction | `EvidenceBundleBuilder` + `services/search/gateway.py` | ENFORCED — `SearchPolicyError` raised if bundle missing |
| SearchGateway ACL/license | `services/search/gateway.py` | ENFORCED — `context.permits()` applied before ranking; `available_time` enforced |
| OpenClaw evidence-only facade | `integrations/openclaw/search_gateway.py` | ENFORCED — returns `evidence_bundle_id` + citations only; no raw payload |
| Tool/workflow bridge deny-first | `services/openclaw-gateway-adapter/tool_workflow_bridge.py` | ENFORCED — broker, live, paper, canary, Lean, capital-prefixed tools always blocked |
| Broker adapter gates | `paper_broker_adapter.py`, `live_gate_adapter.py` | ENFORCED — paper is opt-in only; live execution disabled; all five gate checks required |

---

## 6. Component Posture at Review Approval

| Component | Status | Activation gate | Remaining gap |
|---|---|---|---|
| Qlib | `smoke-tested` | `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1` + `QLIB_BACKEND=stub\|real` + `PANTHEON_OFFLINE_GATE_ENABLED=true` | RS-003 candidate, dataset proof, StrategySpec binding, production credential/storage |
| TRL | `smoke-tested` | `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1` | ≥200 FB-002 events, ≥100 preference pairs, approved LP-002, baseline model, downstream consumer |
| FinRL | `criteria-defined` | `PANTHEON_FINRL_PREP_ENABLED=1` (deferred prep only) | RL path approval gate; requires Qlib supervised alpha proof first |
| RLlib / Ray Tune | `version-pinned` | Separate prep flags (deferred) | Same RL gate as FinRL |
| W&B | `criteria-defined` | `PANTHEON_ENABLE_WANDB_OFFLINE_STORE=1` (offline local only) | MLflow operational-history gate (earliest 2026-05-15), operator preference, network readiness |
| OpenClaw | `governed` | Opt-in adapter env; paper requires `OPENCLAW_PAPER_ADAPTER_ENABLED=true`; live hard-rejected | No remaining gap for current governed scope |

---

## 7. Sidecar Acceptance Packet Cross-Reference

The companion sidecar `P2-OSS-ACTIVATE-001-SIDECAR-ACCEPTANCE.md` (also in this directory) provided the pre-implementation checklist with 14 acceptance items (A1–A14). The following table maps those items to the post-review state:

| Item | Pre-implementation status | Post-review state |
|---|---|---|
| A1 Qlib fail-closed gate | OPEN | CONFIRMED — gate requires `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1`; activation notes documented |
| A2 TRL fail-closed gate | OPEN | CONFIRMED — gate requires `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1`; activation notes documented |
| A3 SourceRecord control path | VERIFIED | CONFIRMED — 111 tests pass; enforcement present |
| A4 EvidenceBundle path | VERIFIED (contract) | CONFIRMED — `SearchPolicyError` raised if bundle missing |
| A5 SearchGateway ACL | VERIFIED | CONFIRMED — `context.permits()` enforced; 1 pre-existing schema-drift test noted as non-blocking |
| A6 OpenClaw available_time / session-time | VERIFIED | CONFIRMED — all five gate checks required; 158 adapter tests pass |
| A7 Qlib credentials documented | OPEN | ADDRESSED — `OSS_ACTIVATION_NOTES.md` §Component Posture: Qlib row details production data prerequisites |
| A8 TRL credentials documented | OPEN | ADDRESSED — same file §Component Posture: TRL row details runtime-data prerequisites |
| A9 OpenClaw live broker always-rejected | OPEN | CONFIRMED — live gate adapter hard-rejects; test suite verifies |
| A10 OSS activation matrix passes | OPEN | NOT EXPLICITLY RE-RUN — focused pytest suite (111 passed) covers governed paths; full matrix rerun recommended at closeout |
| A11 No silent activation in compose profiles | OPEN | ADDRESSED in activation notes; explicit gate documentation present; compose profile smoke was part of P0-CI-BOUNDED-001 baseline |
| A12 Deferred OSS map is current | OPEN | ADDRESSED — `OSS_ACTIVATION_NOTES.md` documents FinRL, RLlib, Ray Tune, W&B deferred status and entry criteria |
| A13 Activation notes file created | OPEN | COMPLETE — `services/learning/OSS_ACTIVATION_NOTES.md` created |
| A14 No new production activation silently enabled | OPEN | CONFIRMED by Codex review — no env-flag gate removed; no production-activation path opened |

---

## 8. Open Items for Closeout Reference

These items are informational for Codex2's closeout and future work. They do not block the `review_approved` state.

1. **Full OSS activation matrix rerun** (A10): The focused 111-test suite covers governed path enforcement. Codex2 should consider running `scripts/test_smoke_oss_activation_ready_matrix.py` at closeout to confirm all 16 matrix items still pass with the new activation notes in place.

2. **SD-03 schema drift** (from SIDECAR-ACCEPTANCE A5): `test_sd03_contract_schemas_accept_model_payloads` fails because the SD-03 schema is missing `available_time` and `entitlement_tags` fields now present in runtime output. This is a pre-existing schema-drift gap, not a bypass. A follow-up task should update the SD-03 schema.

3. **W&B re-entry evaluation window** (2026-05-15): The MLflow operational-history gate for W&B re-entry opens approximately 2026-05-15. The activation notes record W&B as `criteria-defined` / offline local only. A follow-up evaluation task is recommended before that date.

4. **Direct EvidenceRepository access audit** (from SIDECAR-ACCEPTANCE §4.2): Whether all downstream `KnowledgeObject` / `EvidenceItem` consumers go through `SearchGateway` vs. direct `InMemoryEvidenceRepository` access is flagged as a remaining gap. A narrow audit task would confirm no direct repository access is exposed outside the gateway layer in production entrypoints.

---

## 9. Review Disposition

**Parent task `P2-OSS-ACTIVATE-001`: APPROVED** by Codex on 2026-05-01 → subsequently **DONE** (archived 2026-05-01T15:33:19Z, commit `05d52eb`).

All three acceptance criteria pass:
- A1: Production data posture is not a blanket ban; it is a durable storage / entitlement / audit gate.
- A2: Source/search/OpenClaw paths maintain fail-closed boundaries via SourceRecord, EvidenceBundle, SearchGateway, and tool bridge controls.
- A3: Activation notes document remaining prerequisites component-by-component without enabling execution.

Test evidence: 111 focused pytest passed; `git diff --check` passed on tracked checklist change.

**Lifecycle note:** The parent task completed the full `review_approved → done` transition at 2026-05-01T15:33:19Z (commit `05d52eb`) — before this sidecar packet was handed to Codex2 at 15:35Z. No further parent closeout action is needed. This packet now serves as a post-closeout evidence summary.

---

## 10. Handoff to Codex2 (Sidecar Reviewer)

Codex2, this review packet is ready for your review as the sidecar reviewer.

**What this packet does:**
- Consolidates the Codex review findings, test evidence, control surface inventory, and acceptance checklist status into a single auditable artifact.
- Cross-references the SIDECAR-ACCEPTANCE checklist (14 items) against post-review state.
- Flags four informational open items for your closeout and follow-up tracking — none block the parent `review_approved` state.

**What this packet does not do:**
- It does not modify any canonical L1 policy documents.
- It does not change the parent task's acceptance scope or review verdict.
- It does not alter `ai-status.json`, `OSS_INTEGRATION_CHECKLIST.md`, or any runtime implementation.

**Recommended sidecar review checklist:**
1. Confirm the artifact stays within sidecar support-only scope (no canonical truth mutations).
2. Confirm the acceptance criteria evaluation in §3 matches the Codex review file (`support/reviews/P2-OSS-ACTIVATE-001-codex-review.md`).
3. Confirm the SIDECAR-ACCEPTANCE cross-reference table in §7 is accurate against the companion file.
4. Note any open items you want tracked — or confirm §8 is sufficient for handoff.

**Parent task lifecycle update:** Parent `P2-OSS-ACTIVATE-001` was archived as `done` at 2026-05-01T15:33:19Z (commit `05d52eb`) before this sidecar was handed off. No separate parent closeout action is needed from Codex2. Once the sidecar review is approved, only this sidecar task (`P2-OSS-ACTIVATE-001-SIDECAR-REVIEW`) needs to be closed.

---

*Generated by Claude as a sidecar `review_packet` helper for P2-OSS-ACTIVATE-001. This file is a support artifact and does not modify canonical truth.*
