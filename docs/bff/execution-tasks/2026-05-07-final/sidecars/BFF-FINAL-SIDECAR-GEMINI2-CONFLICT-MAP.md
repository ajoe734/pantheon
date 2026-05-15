# BFF-FINAL-SIDECAR-GEMINI2-CONFLICT-MAP

Owner: Gemini2
Reviewer: Claude2
Depends on: BFF-FINAL-001
Parent: BFF-FINAL-010
Mutates canonical code: no

## Scope

Map parallel-edit risks across the active BFF-FINAL workers. This is support-only coordination work. Do not edit `services/control-plane/bff/*` or canonical contract files.

## Deliverable

Update this file with:

1. The files each BFF-FINAL mainline task is expected to touch.
2. Likely overlap points, especially in `main.py`, `models.py`, command handling, and read-store surfaces.
3. Suggested merge or review order to reduce conflicts.
4. Any task boundaries that need clarification before final verification.

## Findings

### Mainline Tasks and Touched Files

Based on the investigation of task definitions, the following mainline tasks and their primary touched files have been identified:

*   **BFF-FINAL-001-contract-foundation**:
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md`
    *   `docs/conventions/BFF_RESPONSE_ENVELOPE.md`
    *   `services/control-plane/bff/test_governance_command_submission.py`
    *   `services/control-plane/bff/test_final_contract_primitives.py`
*   **BFF-FINAL-002-idempotency-command-envelope**:
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/command_queue.py`
    *   `services/control-plane/bff/command_executor.py`
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/bff/test_governance_command_submission.py`
    *   `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md`
    *   `services/control-plane/bff/test_command_executor.py`
*   **BFF-FINAL-003-precondition-errors**:
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/bff/command_executor.py`
    *   `services/control-plane/governance/approval_decision.py`
    *   `services/control-plane/bff/test_governance_command_submission.py`
*   **BFF-FINAL-004-action-catalog**:
    *   `services/control-plane/bff/action_catalog.py`
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/bff/BFF_API_CONTRACT.md`
    *   `docs/bff/README.md`
*   **BFF-FINAL-005-sse-approval-ask**:
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/bff/test_pkt005_sse_substrate_contract.py`
    *   `services/control-plane/bff/BFF_API_CONTRACT.md`
*   **BFF-FINAL-006-mcp-tool-import**:
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/permissions/contract.md`
*   **BFF-FINAL-007-evidence-redaction**:
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/read_store.py`
    *   `services/control-plane/bff/test_kw03_evidence_refs_contract.py`
*   **BFF-FINAL-008-agora-journal-merge-patch**:
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/bff/read_store.py`
*   **BFF-FINAL-009-v5-interventions**:
    *   `services/control-plane/bff/main.py`
    *   `services/control-plane/bff/models.py`
    *   `services/control-plane/bff/command_executor.py`
*   **BFF-FINAL-010-contract-verification**:
    *   `services/control-plane/bff/test_*`
    *   `docs/bff/`
    *   `docs/examples/`
    *   `docs/pantheon-delivery/`
    *   `scripts/verify_bff_local_release.py`

### Likely Overlap Points

The following files and components are likely to have the most contention due to being modified by multiple tasks:

*   **`services/control-plane/bff/main.py`**: Central to command handling and routing; multiple tasks modify its logic.
*   **`services/control-plane/bff/models.py`**: Shared data structures; changes here have a wide impact.
*   **`services/control-plane/bff/command_executor.py`**: Core execution logic for commands.
*   **`services/control-plane/bff/read_store.py`**: Data access layer, shared by several tasks.
*   **`services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md`**: Defines command interface; critical for integration.
*   **`services/control-plane/bff/BFF_API_CONTRACT.md`**: Defines general API interface.

### Suggested Merge/Review Order

To minimize conflicts and ensure a logical progression, the following order is recommended:

1.  **BFF-FINAL-001-contract-foundation**: Establishes core contracts and data models.
2.  **BFF-FINAL-002-idempotency-command-envelope**: Defines command handling and idempotency.
3.  **BFF-FINAL-004-action-catalog**: Introduces the action catalog.
4.  **BFF-FINAL-006-mcp-tool-import**: Integrates the MCP service adapter.
5.  **BFF-FINAL-003-precondition-errors**: Handles precondition error reporting.
6.  **BFF-FINAL-005-sse-approval-ask**: Implements SSE approval mechanism.
7.  **BFF-FINAL-007-evidence-redaction**: Addresses evidence redaction logic.
8.  **BFF-FINAL-008-agora-journal-merge-patch**: Integrates Agora journal features.
9.  **BFF-FINAL-009-v5-interventions**: Implements V5 interventions.
10. **BFF-FINAL-010-contract-verification**: Final contract verification.

*Note*: The sidecar tasks (e.g., `BFF-FINAL-SIDECAR-COPILOT-SPEC-TRACE`, `BFF-FINAL-SIDECAR-GEMINI-SMOKE-MATRIX`, and this task) are support functions. They should be completed to inform or guide the mainline tasks, or after the mainline tasks are sufficiently defined to provide accurate context. This conflict map is intended to guide the mainline tasks.

### Clarifications Needed

*   The exact definition and scope of "active BFF-FINAL workers" need to be explicitly stated to fully understand the context for parallel execution risks.
*   A comprehensive list of all `BFF-FINAL` tasks (mainline and sidecar) and their interdependencies would enhance the accuracy of the suggested order.
*   Specific details on how tasks `BFF-FINAL-001` through `BFF-FINAL-010` are coordinated and executed in parallel would help refine overlap analysis.

## Acceptance

*   Conflict map is specific to the current BFF-FINAL task graph.
*   Recommendations are actionable for reviewers.
*   No canonical code edits are made.
