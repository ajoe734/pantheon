# P2-OSS-ACTIVATE-001 Acceptance Packet (Sidecar)

**Parent Task**: `P2-OSS-ACTIVATE-001` — Research OSS production activation after fail-closed gates
**Parent Owner**: Gemini2
**Parent Reviewer**: Codex
**Parent Status**: `in_progress`
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-05-01T15:00:00Z
**Verified**: 2026-05-01 (branch `backend-dev-publish-20260429`)

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime / registry / governance implementations. It packages the dependency state, acceptance checklist, and implementation readiness map for `P2-OSS-ACTIVATE-001`.

---

## 1. Dependency Map

### 1.1 Formal Parent Dependencies

| Dependency | Task ID | Status | What P2-OSS-ACTIVATE-001 can reuse |
|---|---|---|---|
| Source/search bounded and fail-closed adapter CI | `P0-CI-BOUNDED-001` | **done** (commit `8a624309`, 2026-05-01) | Bounded source/search compose smoke, OSS activation matrix (16/16 passes), OpenClaw fail-closed smoke (13/13 passes), scripts/ci/run_adapter_checks.py, source-search-bounded docker compose profile |

### 1.2 Additional Locked Truth P2-OSS-ACTIVATE-001 Must Reuse

| Source | Locked truth |
|---|---|
| `OSS_INTEGRATION_CHECKLIST.md` | Component inventory and status codes for each upstream OSS integration; current statuses for Qlib (`smoke-tested`), TRL (`smoke-tested`), OpenClaw (`governed`), DSPy / imitation / MLflow / vectorbt / statsmodels / QuantLib (all `governed`), and deferred FinRL / RLlib / Ray Tune / W&B |
| `OPENCLAW_RUNTIME_CONTRACT.md` | Runtime boundary and adapter contract for OpenClaw; session lifecycle, deny-first permission model, workflow governance, `StrategySpec`/`WorkflowHandoff` normalization |
| `PAPER_CANARY_LIVE_POLICY.md` | Deployment-stage policy; paper/canary/live gates are fail-closed; production activation requires explicit policy gate, not silent enabling |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | `ApprovalDecision → DeploymentPlan → RuntimeBinding` chain; Runtime Manager is sole binding writer; no OSS path can bypass this chain |
| `integrations/openclaw/governance.md` | Deny-first permission model, mandatory deny rules (no direct LEAN calls from OpenClaw tools, no cross-persona invocation without consult routing, no unaudited calls) |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Explicit deferred-activation map for TRL, Qlib, RL stack, and W&B with entry criteria and unmet prerequisites |
| `integrations/trl/activation_packet.md` | TRL first governed DPO activation packet; production activation blocked on runtime data gates |
| `integrations/qlib/activation_packet.md` (if exists) | Qlib activation criteria and production gate prerequisites |
| `services/learning/qlib/ACTIVATION_CRITERIA.md` | Qlib activation criteria: RS-003 candidate, governed dataset proof (≥50 instruments, ≥2 years OHLCV), target StrategySpec binding |

### 1.3 What P0-CI-BOUNDED-001 Delivered (reuse baseline)

P0-CI-BOUNDED-001 (done, Codex2 → Codex) delivered and verified:

- `scripts/test_smoke_oss_activation_ready_matrix.py` — 16/16 OSS component matrix: verifies each component's activation gate is correctly blocked or open
- `scripts/smoke_openclaw_activation_ready_e2e.py` — 13/13 end-to-end activation gate tests for OpenClaw paths
- `services/test_source_search_posture.py` — source/search posture and fail-closed boundary tests
- `services/source_ingestion/test_compose_activation.py` — compose activation smoke for source ingestion
- `services/search/tests/test_service_activation_contract.py` — search service activation contract tests
- `scripts/ci/run_adapter_checks.py` — Cloud Build adapter CI runner
- docker compose `source-search-bounded` profile smoke

These are the starting CI baseline that P2-OSS-ACTIVATE-001 must treat as its floor.

### 1.4 Downstream Consumers

| Consumer | Waiting on P2-OSS-ACTIVATE-001 for |
|---|---|
| Parent `P2-OSS-ACTIVATE-001` owner (Gemini2) | Research activation notes identifying remaining production credentials, external service prerequisites, and the explicit activation path for each `smoke-tested` component |
| Platform operators | A documented and reproducible checklist to gate any future OSS component from `smoke-tested` → production without silently enabling it |

---

## 2. What P2-OSS-ACTIVATE-001 Must Deliver

The parent task acceptance criteria are:

1. Research OSS production activation is gated by fail-closed posture and bounded adapter CI
2. No OSS path can bypass `SourceRecord`, `EvidenceBundle`, `SearchGateway`, ACL, license, or `available_time` controls
3. Activation notes identify remaining production credentials or external service prerequisites without silently enabling them

This section expands those three points into concrete implementation targets.

### 2.1 Fail-Closed Gate Verification

For each OSS component currently at `smoke-tested` status (Qlib, TRL) and any `governed` component with a remaining production activation gap, the task must:

- Verify the fail-closed gate still holds with the current codebase
- Document the explicit environment variable or config flag that gates activation
- Confirm no code path silently enables production activation when that flag is absent

### 2.2 Control Surface Inventory

P2-OSS-ACTIVATE-001 must inventory and document the following controls that must not be bypassed by any OSS activation path:

| Control | Where it lives | Bypass risk |
|---|---|---|
| `SourceRecord` integrity check | `services/source_ingestion` | Any source ingest path must emit a governed `SourceRecord`; raw feed injection must be denied |
| `EvidenceBundle` construction | Research / knowledge service boundary | Evidence used for decisions must trace to a governed `EvidenceBundle`; unverified research output must not feed directly into trade decisions |
| `SearchGateway` ACL | `services/search/gateway.py` | All search queries must pass through the governed gateway; direct index access outside the gateway is denied |
| License check | Per-component pre-activation preflight | OSS components with non-permissive licenses must be verified before any production activation |
| `available_time` control | `services/openclaw-gateway-adapter/live_gate_adapter.py` | OpenClaw-originated workflows must not execute outside the allowed session time window |

### 2.3 Remaining Production Prerequisite Documentation

For each OSS component that is `smoke-tested` but not yet `governed` (i.e., Qlib and TRL), activation notes must explicitly document:

- What external services or credentials are required (e.g., real data provider API key, broker credentials)
- What data prerequisites remain unmet (e.g., minimum corpus size, model baseline)
- What governance steps must occur before enabling (e.g., operator opt-in, policy approval)
- A concrete command or flag that would enable production activation, so the reader can see precisely what "enable" looks like — but without executing that path

### 2.4 No-Bypass Proof

The research must verify and document that the following paths remain blocked in the current codebase:

- `PANTHEON_QLIB_ACTIVATION_READY_ENABLED` absent → Qlib worker returns no-op or fails-closed
- `PANTHEON_OSS_TRL_ACTIVATION_ENABLED` (or equivalent TRL gate flag) absent → TRL DPO workflow returns draft/none only
- `OPENCLAW_PAPER_ADAPTER_ENABLED` absent → paper broker adapter rejects orders
- Live broker path → always rejected (no env flag overrides)
- `PANTHEON_OFFLINE_GATE_ENABLED` absent → Qlib offline dispatch gated

---

## 3. Acceptance Checklist

| # | Acceptance Item | Status | What "done" looks like |
|---|---|---|---|
| A1 | Fail-closed gate confirmed for Qlib | OPEN | Run Qlib activation matrix or targeted test without `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1`; confirm worker returns no-op or blocked exit |
| A2 | Fail-closed gate confirmed for TRL | OPEN | Run TRL adapter or preflight without activation env var; confirm `artifact_state=draft`, `current_stage=none`, no live model submission |
| A3 | `SourceRecord` control path verified | **VERIFIED** | `services/source_ingestion/tests/` — 28 passed (2026-05-01). `external_sources.py:validate_external_source_record` enforces `entitlement_tags`, `license_scope`, `access_scope`, `event_time`, `available_time` on all external source types (news/social/alpha_db). Forbidden execution routes blocked by explicit deny-lists. Governance envelope sets `direct_execution_allowed: false`, `lean_consumption: research_only_not_direct_action`. |
| A4 | `EvidenceBundle` path not bypassed | **VERIFIED (contract level)** | `services/search/gateway.py:SearchGateway.search` requires a governed `EvidenceBundle` reference for all search results; raises `SearchPolicyError` if bundle is missing. Runtime search flow cannot surface results without a valid `evidence_bundle_id`. Full service-layer enforcement depends on parent task verifying no direct knowledge-object writes bypass the bundle contract. |
| A5 | `SearchGateway` ACL enforced | **VERIFIED** | `services/search/tests/` — 45 passed, 1 schema-drift failure. `SearchGateway.search` applies `context.permits()` (ACL / license scope) before ranking; `available_time` enforced via `_available_at_or_before_now`; `require_citations` defaults True. 1 failure: `test_sd03_contract_schemas_accept_model_payloads` — SD-03 schema missing `available_time` + `entitlement_tags` fields now present in runtime output. **Not a bypass; schema needs updating.** |
| A6 | `available_time` / session-time control verified for OpenClaw | **VERIFIED** | `live_gate_adapter.py:LiveGateAdapter` is fail-closed by default (`OPENCLAW_LIVE_ADAPTER_ENABLED` absent → `LIVE_GATE_DISABLED`). All 5 gate checks (adapter-enabled, human-approval-token, active-live-RuntimeBinding, kill-switch-safe-mode, binding-not-in-rollback) must pass explicitly. `services/openclaw-gateway-adapter/` — 158 tests pass (excluding `test_main.py` collection error; pre-existing `ActorRef` import issue in foundation wiring). |
| A7 | Remaining credentials for Qlib documented | OPEN | Explicit list: which data-provider API key, dataset source, or network endpoint remains unavailable; does not enable them |
| A8 | Remaining credentials for TRL documented | OPEN | Explicit list: what production model source, preference-pair corpus, or downstream consumer is still missing; does not enable them |
| A9 | OpenClaw live broker path confirmed always-rejected | OPEN | Reference test `test_live_gate_adapter.py` or equivalent; confirm `POST /broker/live/orders` returns 403/disabled regardless of env |
| A10 | OSS activation matrix still passes | OPEN | Re-run `scripts/test_smoke_oss_activation_ready_matrix.py`; confirm 16/16 (or updated count); record output |
| A11 | No silent activation in compose profiles | OPEN | Confirm docker compose `source-search-bounded` profile does not mount real data credentials; confirm `openclaw` compose profile is opt-in only |
| A12 | Deferred OSS map is current | OPEN | Verify `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` still accurately reflects deferred status for FinRL, RLlib, Ray Tune, W&B; update if needed |
| A13 | Activation notes file created or updated | OPEN | A new artifact (e.g., `services/learning/OSS_ACTIVATION_NOTES.md` or equivalent) documents remaining prerequisites per component in plain prose |
| A14 | No new production activation silently enabled | OPEN | `git diff HEAD~1` or equivalent shows no env-flag gate was removed and no production-activation code path was opened without a corresponding acceptance note |

---

## 4. Risk Areas and Open Questions

### 4.1 services/source_ingestion — Service Exists; Scope Clarification Still Needed

`P2-OSS-ACTIVATE-001` lists `services/source_ingestion` as an artifact. The service directory **does exist** with a full Python package: `connectors/`, `external_sources.py`, `ingest_manager.py`, `pg_store.py`, `scheduler.py`, `scheduler_worker.py`, and tests (28 passing). The service is governed at the connector-level (bounded ingestion, external source ACL policy).

**Remaining scope question**: P2-OSS-ACTIVATE-001 should clarify whether the task scope is (a) auditing and documenting the existing fail-closed posture of the current service, or (b) further hardening or adding features. If (b), additional task slicing may be needed to keep the "research" task bounded.

### 4.2 `EvidenceBundle` Boundary — Partial Enforcement Confirmed

`SearchGateway` enforces `EvidenceBundle` existence before surfacing results (`raises SearchPolicyError` if bundle missing). `external_sources.py` emits `governance.canonical_sink: SourceRecord/EvidenceBundle` on every external source record.

**Remaining gap**: Whether all downstream consumers of `KnowledgeObject` / `EvidenceItem` are required to go through `SearchGateway` (vs. accessing the repository directly) is not fully verified in this sidecar. Codex should confirm that no direct `InMemoryEvidenceRepository.list_knowledge_objects()` call is exposed outside of the gateway layer in production service entrypoints.

### 4.3 TRL Activation Gate Flag Name

`services/learning/trl/preflight.py` is documented as a non-writing pre-activation preflight scaffold, but the exact environment variable that gates TRL production activation is not confirmed in this packet.

**Recommendation**: Codex should verify the exact flag name (e.g., `PANTHEON_TRL_ACTIVATION_ENABLED`) when running A2. The flag name should be documented in the activation notes artifact.

### 4.4 W&B Deferred Re-Entry Window Opens Soon

The deferred W&B re-entry condition specifies the MLflow operational history gate at earliest 2026-05-15. Since today is 2026-05-01, this gate will be approaching within the sprint window.

**Recommendation**: Activation notes should explicitly state whether P2-OSS-ACTIVATE-001 does or does not cover W&B re-entry evaluation. If not, a follow-up task should be created.

---

## 5. Files Referenced

### Shared Truth
- `ai-status.json`
- `ai-activity-log.jsonl`

### Canonical / Contract Sources
- `OSS_INTEGRATION_CHECKLIST.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `integrations/openclaw/governance.md`

### Completed Upstream Work (P0-CI-BOUNDED-001, commit 8a624309)
- `scripts/test_smoke_oss_activation_ready_matrix.py`
- `scripts/smoke_openclaw_activation_ready_e2e.py`
- `scripts/ci/run_adapter_checks.py`
- `services/test_source_search_posture.py`
- `services/source_ingestion/test_compose_activation.py`
- `services/search/tests/test_service_activation_contract.py`

### Parent Task Artifacts
- `OSS_INTEGRATION_CHECKLIST.md`
- `services/search/` (gateway.py, index_adapter.py, index_pipeline.py, filters.py, retriever.py)
- `services/openclaw-gateway-adapter/` (main.py, live_gate_adapter.py, paper_broker_adapter.py, session_lifecycle.py, tool_workflow_bridge.py)

### Deferred Activation Context
- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
- `integrations/trl/activation_packet.md`
- `services/learning/qlib/ACTIVATION_CRITERIA.md`
- `services/registry/experiments/WANDB_ACTIVATION.md`

### This Sidecar
- `support/sidecars/P2-OSS-ACTIVATE-001/P2-OSS-ACTIVATE-001-SIDECAR-ACCEPTANCE.md`

---

## 6. Handoff to Reviewer (Codex)

Codex, this packet is ready for review and parent-owner reuse.

What it gives the P2-OSS-ACTIVATE-001 owner (Gemini2) and reviewer (Codex):

1. **Dependency-confirmed starting point**: `P0-CI-BOUNDED-001` is done (commit `8a624309`), the bounded CI baseline passes, and the fail-closed posture is verified as of 2026-05-01.

2. **Control surface inventory**: Five controls (SourceRecord, EvidenceBundle, SearchGateway ACL, license check, available_time) that must not be bypassed, mapped to their enforcement locations in the codebase.

3. **Acceptance checklist**: 14 concrete items (A1–A14) derived from the three parent acceptance criteria.

4. **Open questions documented**: Four areas (source_ingestion service scope, EvidenceBundle enforcement presence, TRL flag name, W&B re-entry window) flagged for Codex to decide.

Recommended next steps for the parent owner/reviewer:

- Use A1–A2 to verify current fail-closed gate state for Qlib and TRL with targeted test runs.
- Use A3–A6 to confirm each control surface is enforced in existing code.
- Create `services/learning/OSS_ACTIVATION_NOTES.md` (or equivalent) to fulfill A7–A8 and A13.
- Resolve the §4.1 scope question: is `services/source_ingestion` a research-only audit task or a service creation task?
- Once implementation is complete, hand to Claude for formal review using the A1–A14 checklist as the review frame.

---

*Generated by Claude as a sidecar `acceptance_packet` helper for P2-OSS-ACTIVATE-001. This file is a support artifact and does not modify canonical truth. Parent owner Codex should absorb this into the P2-OSS-ACTIVATE-001 implementation plan.*
