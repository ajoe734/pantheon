# ACG-RS-OPS-CONSULT-20260828 Review Evidence Manifest

Task ID: ACG-RS-OPS-CONSULT-20260828
Program ID: PANTHEON-ARCH-CLEANUP-20260828
Design Units: ACG-02-006, ACG-02-007, ACG-02-008
Owner: Antigravity
Reviewer: Claude
Date: 2026-08-28

## 1. Summary of Changes

Converged workflow/catalog, OpenClaw, and Consultation reads to focused domain providers and typed ports, resolving disposition matrix items ACG-02-006 through ACG-02-008:

1. **Workflow & Automation/Governance Catalogs (`ACG-02-006`)**:
   - Implemented `WorkflowHookCatalogReaderPort` protocol and `DomainWorkflowCatalogPort` in `services/control-plane/bff/domain_ports/operations_consultation.py`.
   - Supports typed access for `workflow_templates`, `hook_registry`, `governance_permissions`, `memory_governance_rules`, `consult_rules`, `route_policies`, `alpha_factory_cards` (with lane filter and pagination), `skills`, `tools`, `mcp_servers`, and `mcp_tools`.
   - Updated `services/control-plane/bff/console_gap/workflows_hooks.py` router factory (`create_workflows_hooks_router`) to accept `workflow_hook_port: Optional[WorkflowHookCatalogReaderPort | ReadStoreProvider | Any]` for direct focused domain port injection while preserving backward compatibility.

2. **OpenClaw Operations & Truthful Error Read Models (`ACG-02-007`)**:
   - Implemented `OpenClawOperationsReaderPort` and `DomainOpenClawOperationsPort` wrapping `OpenClawOpsClient` and dormant OSS specs.
   - Provides `get_openclaw_ops_snapshot`, `get_openclaw_broker_adapter_readiness`, and `get_research_oss_preactivation_snapshot`.
   - Truthful error surface: `OpenClawOpsClientError` / HTTP status codes / missing configurations are preserved and surfaced directly without masking or returning fake success payloads.
   - Live/canary broker adapter readiness enforces fail-closed semantics (`fail_closed_explicit_gate_required`, `live_execution_enabled: False`, `is_real_capital: False`, `is_real_order: False`).

3. **Consultation Lifecycle & Transcript Read Models (`ACG-02-008`)**:
   - Implemented `ConsultationReaderPort` and `DomainConsultationPort` directly backed by `ConsultationServiceClient` and `ConsultationStore` (from `services/consultation/`).
   - Supports:
     - Consult requests (`list_consult_requests`, `get_consult_request`, `create_consult_request`, `cancel_consult_request`).
     - Consult memos (`list_consult_memos`, `get_consult_memo`) with memo review payload redactions stripping persona-internal state (`policy_internals`, `memory_trace`, `secret_credentials`, `capability_map_internals`).
     - Consult sessions & participants (`list_consultations_for_persona`, `get_consultation`, `get_consultation_participants`, `get_consultation_outcome`, `get_consultation_evidence`).
     - Consult transcripts (`get_consult_transcript`) with sequence ordering, pagination offsets, and gap detection (`surface_state: "degraded"` on gaps, `"ok"` when contiguous).

4. **Composite, In-Memory Fakes & Factory Functions**:
   - Implemented `CompositeOperationsConsultationPort`, `InMemoryOperationsConsultationPort`, `create_operations_consultation_port`, and `create_in_memory_operations_consultation_port`.
   - Implemented 17 comprehensive unit and integration tests in `services/control-plane/bff/tests/test_operations_consultation_ports.py`.

## 2. Verification Results

- `pytest services/control-plane/bff/tests/test_operations_consultation_ports.py`: `17 passed`
- `pytest services/control-plane/bff/tests/test_bff_workflows_hooks.py`: `passed`
- `pytest services/consultation/`: `64 passed`
- Architecture boundaries and route uniqueness validated.

## 3. Modified Files Inventory

- Created:
  - `services/control-plane/bff/domain_ports/operations_consultation.py`
  - `services/control-plane/bff/tests/test_operations_consultation_ports.py`
  - `support/reviews/ACG-RS-OPS-CONSULT-20260828-review-evidence.md`
- Modified:
  - `services/control-plane/bff/console_gap/workflows_hooks.py`
