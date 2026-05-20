# Execution Materialization — Phase 8 Track A (2026-05-19 baseline)

Date: 2026-05-19
Author: Claude (facilitator)
Source: [`docs/04/pantheon_design_blueprint_supplement_2026-05-19/`](../../../../04/pantheon_design_blueprint_supplement_2026-05-19/)

This file is the materialization manifest for Track A — direct dispatch — under the 2026-05-19 baseline. Track B (BLA/HA/CBL, 27 tasks) is held in `planning-session.json:deferred_tasks_pending_subsession`.

## Conventions

- All task IDs carry the `-V2` suffix to avoid colliding with historical archived bare IDs (`EP5-001`, `QLIB-ACT-001`, etc., already `review_approved` and archived).
- `phase` field: `Phase 8 / EPIC-<NAME>` where EPIC is one of `EPIC-EP5`, `EPIC-LSP`, `EPIC-RES-ACT`, `EPIC-OODA-CANARY`, `EPIC-HG`, `EPIC-LIVE-GATE`.
- `task_class = "primary"` for engineering work; `task_class = "human_gate"` for the three LIVE-gate placeholders.
- Initial `status = "todo"` for engineering tasks; `status = "blocked"` for LIVE-gate placeholders.
- `mutates_canonical = false` for all Track A tasks.
- `auto_created_by = "phase8-2026-05-19-pantheon-live-ready-engineering"`.

## Track A — 33 tasks

### EPIC-EP5 (10 tasks)

| ID | Title | Owner | Reviewer | Depends on | Key artifact |
|---|---|---|---|---|---|
| `EP5-001-V2` | PromotionReadinessPacket schema | Codex | Codex2 | — | `services/governance/promotion_readiness/packet_model.py` |
| `EP5-002-V2` | Readiness validator + blocking_reasons | Codex | Codex2 | `EP5-001-V2` | `services/governance/promotion_readiness/validator.py` |
| `EP5-003-V2` | Signoff record API (HumanGateDecision schema embedded) | Codex | Codex2 | `EP5-001-V2` | `services/governance/promotion_readiness/signoff_api.py`, `services/governance/human_gate/decision_model.py` |
| `EP5-004-V2` | Revoke/expire semantics + evidence-hash binding | Codex | Codex2 | `EP5-003-V2` | `services/governance/promotion_readiness/revoke_expire.py`, `services/governance/human_gate/signature_lifecycle.py` |
| `EP5-005-V2` | EP5ProofPacket generator | Codex | Codex2 | `EP5-001-V2` | `services/governance/ep5_proof/packet_generator.py` |
| `EP5-006-V2` | Canary dry-run command (no live side effects) | Codex | Codex2 | `EP5-005-V2` | `services/governance/ep5_proof/dry_run.py` |
| `EP5-007-V2` | Rollback drill harness | Codex | Codex2 | `EP5-005-V2` | `services/governance/ep5_proof/rollback_drill_harness.py` |
| `EP5-008-V2` | Kill-switch demo harness | Codex | Codex2 | `EP5-005-V2` | `services/governance/ep5_proof/kill_switch_harness.py` |
| `EP5-009-V2` | Canary observation report builder | Codex2 | Codex | `EP5-005-V2`, `EP5-007-V2`, `EP5-008-V2` | `services/governance/ep5_proof/observation_report.py` |
| `EP5-010-V2` | EP5 closeout renderer (Markdown + JSON) | Codex2 | Codex | `EP5-002-V2`, `EP5-005-V2`, `EP5-009-V2` | `services/governance/ep5_proof/closeout_renderer.py`, `docs/deployment/evidence/ep5-canary/README.md` |

### EPIC-LSP (6 tasks)

| ID | Title | Owner | Reviewer | Depends on | Key artifact |
|---|---|---|---|---|---|
| `LSP-001-V2` | CI wrapper around audit script | Codex2 | Claude | — | `scripts/lovable/ci_strict_publish_audit.sh`, `.github/workflows/strict-publish-audit.yml` |
| `LSP-002-V2` | Browser probe runner | Claude | Claude2 | — | `scripts/lovable/browser_probe.py` |
| `LSP-003-V2` | Hosted bundle hash recorder | Codex2 | Claude | — | `scripts/lovable/capture_bundle_hashes.py` |
| `LSP-004-V2` | Forbidden runtime path scanner | Codex2 | Claude | — | `scripts/lovable/forbidden_path_scanner.py` |
| `LSP-005-V2` | Final audit evidence packet generator | Codex2 | Claude | `LSP-001-V2`, `LSP-002-V2`, `LSP-003-V2`, `LSP-004-V2` | `scripts/lovable/strict_publish_audit.py`, `support/evidence/lsp-final-audit/` |
| `LSP-006-V2` | Publish gate checker | Codex2 | Claude | `LSP-005-V2` | `scripts/lovable/publish_gate_checker.py` |

### EPIC-RES-ACT (7 tasks)

| ID | Title | Owner | Reviewer | Depends on | Key artifact |
|---|---|---|---|---|---|
| `RES-ACT-001-V2` | Production data proof schema | Codex | Gemini | — | `services/governance/research_activation/production_data_proof.py` |
| `RES-ACT-002-V2` | PIT / license / freshness checker | Codex | Gemini | `RES-ACT-001-V2` | `services/governance/research_activation/pit_license_freshness.py` |
| `RES-ACT-003-V2` | Candidate artifact admission gate | Codex | Codex2 | `RES-ACT-001-V2` | `services/governance/research_activation/admission_gate.py` |
| `RES-ACT-004-V2` | Repeated OOS evidence runner | Codex | Gemini | `RES-ACT-001-V2` | `services/governance/research_activation/oos_runner.py` |
| `RES-ACT-005-V2` | No-order-route scanner | Codex | Codex2 | — | `services/governance/research_activation/no_order_route_scanner.py` |
| `RES-ACT-006-V2` | Governance review handoff packet | Codex | Codex2 | `RES-ACT-003-V2`, `RES-ACT-004-V2` | `services/governance/research_activation/handoff_packet.py` |
| `WNB-ACT-001-V2` | W&B credentialed sync proof | Codex | Gemini | — | `integrations/wandb/credentialed_sync_proof.md`, `tests/integrations/test_wandb_sync.py` |

### EPIC-OODA-CANARY (5 tasks)

| ID | Title | Owner | Reviewer | Depends on | Key artifact |
|---|---|---|---|---|---|
| `OODA-CANARY-001-V2` | Canary OODA packet schema | Codex | Codex2 | — | `services/ooda/canary_packet_model.py` |
| `OODA-CANARY-002-V2` | Canary transition tests | Codex | Codex2 | `OODA-CANARY-001-V2` | `tests/ooda/test_canary_transitions.py` |
| `OODA-CANARY-003-V2` | Canary telemetry-to-evolution test | Codex | Codex2 | `OODA-CANARY-001-V2` | `tests/e2e/test_canary_telemetry_to_evolution.py` |
| `OODA-CANARY-004-V2` | Canary rollback drill linkage | Codex | Codex2 | `OODA-CANARY-001-V2`, `EP5-007-V2` | `services/ooda/canary_rollback_drill_linkage.py` |
| `OODA-CANARY-005-V2` | Canary packet closure renderer | Codex2 | Codex | `OODA-CANARY-001-V2`, `OODA-CANARY-002-V2`, `OODA-CANARY-003-V2`, `OODA-CANARY-004-V2` | `services/ooda/canary_closure_renderer.py` |

### EPIC-HG (2 tasks — residual after consolidation)

| ID | Title | Owner | Reviewer | Depends on | Key artifact |
|---|---|---|---|---|---|
| `HG-005-V2` | Human gate audit log projection (AuditAction) | Codex | Codex2 | `EP5-003-V2` | `services/audit/projections/human_gate.py` |
| `HG-006-V2` | Management Console UI read model for human gate status | Claude | Claude2 | `EP5-003-V2` | `apps/management/src/screens/HumanGate/HumanGateStatus.tsx` |

### EPIC-LIVE-GATE (3 placeholder tasks)

`task_class: human_gate`, `status: blocked`. Not dispatchable to AI workers; visible in dashboard for tracking the actual human go/no-go event.

| ID | Title | Owner | Reviewer | Waiting for | Notes |
|---|---|---|---|---|---|
| `BLA-LIVE-001-V2` | Broker production live enable (human gate) | Claude | Codex | `risk_owner_signoff,operator_signoff,BLA-track-engineering-complete` | Not engineering; flips `BROKER_PRODUCTION_LIVE_ENABLED=true` after human approval |
| `CBL-LIVE-001-V2` | Capital binding live enable (human gate) | Claude | Codex | `risk_owner_signoff,operator_signoff,CBL-track-engineering-complete,BLA-LIVE-001-V2` | Capital binding live cannot precede broker live |
| `HA-PROD-001-V2` | Production HA cutover (human gate) | Claude | Codex | `infra_decision_maker_signoff,HA-track-engineering-complete` | Requires infra-decision-maker approval in addition to risk-owner / operator |

## Track B held — 27 tasks

| ID | Title |
|---|---|
| `BLA-001-V2` | Broker live activation criteria JSON + validator |
| `BLA-002-V2` | Risk-owner checklist generator |
| `BLA-003-V2` | Operator checklist generator |
| `BLA-004-V2` | Rollback drill dry-run |
| `BLA-005-V2` | Kill-switch demo evidence collector |
| `BLA-006-V2` | Broker credential vault readiness spec |
| `BLA-007-V2` | First-week observation report builder |
| `BLA-008-V2` | Approval revoke / withdraw model |
| `BLA-009-V2` | Live activation simulator |
| `BLA-010-V2` | Broker go/no-go dashboard |
| `CBL-001-V2` | CapitalBindingLiveReadiness schema |
| `CBL-002-V2` | Sponsor responsibility model |
| `CBL-003-V2` | Conflict resolution log gate |
| `CBL-004-V2` | Binding TTL / revoke / suspend semantics |
| `CBL-005-V2` | Live binding simulator |
| `CBL-006-V2` | Evidence collector |
| `CBL-007-V2` | Capital binding go/no-go dashboard |
| `HA-001-V2` | BFF HA topology doc |
| `HA-002-V2` | SLA JSON |
| `HA-003-V2` | Degraded mode matrix implementation |
| `HA-004-V2` | Failover runbook |
| `HA-005-V2` | Observability spec |
| `HA-006-V2` | Cost ceiling monitor |
| `HA-007-V2` | Multi-replica dev PoC |
| `HA-008-V2` | SSE Last-Event-ID replay test |
| `HA-009-V2` | Idempotency under multi-replica test |
| `HA-010-V2` | Failover demo |

## Acceptance template (Track A primary tasks)

```yaml
acceptance:
  - Schema/code matches 2026-05-19 supplement section <N>
  - Unit tests cover happy path and at least one fail-closed case
  - Reviewer signs off via AI_NAME=<Reviewer> ./scripts/ai-status.sh approve <id>
  - Artifact / evidence path exists in the worktree at closeout
  - No L1 canonical doc modified
```

## Dispatch order recommendation

```text
Wave 1 (independent, parallel):
  EP5-001-V2, EP5-005-V2 (depends only on -001)
  LSP-001-V2, LSP-002-V2, LSP-003-V2, LSP-004-V2
  RES-ACT-001-V2, RES-ACT-005-V2
  WNB-ACT-001-V2
  OODA-CANARY-001-V2

Wave 2 (depends on Wave 1):
  EP5-002-V2, EP5-003-V2
  RES-ACT-002-V2, RES-ACT-003-V2, RES-ACT-004-V2
  OODA-CANARY-002-V2, OODA-CANARY-003-V2

Wave 3:
  EP5-004-V2, EP5-006-V2, EP5-007-V2, EP5-008-V2
  RES-ACT-006-V2
  HG-005-V2, HG-006-V2

Wave 4:
  EP5-009-V2
  OODA-CANARY-004-V2 (after EP5-007-V2)
  LSP-005-V2

Wave 5:
  EP5-010-V2
  OODA-CANARY-005-V2
  LSP-006-V2

Throughout:
  BLA-LIVE-001-V2 / CBL-LIVE-001-V2 / HA-PROD-001-V2 remain status=blocked
```

Supervisor + auto worker do not need this ordering enforced; `depends_on` does it. Shown as a sanity check only.
