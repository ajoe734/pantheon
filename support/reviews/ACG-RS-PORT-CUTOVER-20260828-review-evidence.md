# ACG-RS-PORT-CUTOVER-20260828 Review Evidence Manifest

Task ID: ACG-RS-PORT-CUTOVER-20260828
Program ID: PANTHEON-ARCH-CLEANUP-20260828
Owner: Antigravity
Reviewer: Codex2
Date: 2026-08-28

## 1. Summary of Changes

Cut over narrow read-surface ports away from `ReadSurfaceStore` delegation to resolve directly against typed domain ports and readers:

1. **Unified Ports Layer Package (`services/control-plane/bff/ports/`)**:
   - `services/control-plane/bff/ports/__init__.py`: Package index exporting all domain ports, protocol definitions, and factory helpers.
   - `services/control-plane/bff/ports/operations_consultation.py`: Re-exports typed domain ports for Workflows, Hooks, OpenClaw ops, and Consultation.
   - `services/control-plane/bff/ports/persona_capital_runtime.py`: Re-exports typed domain ports for Persona Fleet, Capital Pools, Deployment Plans, Runtime Bindings, Ranking, and Evolution.
   - `services/control-plane/bff/ports/ooda_management.py`: Re-exports typed domain ports for OODA packets, Interventions, Synthesis conflict logs, and Management review queues.
   - `services/control-plane/bff/ports/research_knowledge_source.py`: Re-exports typed domain ports for Research notes, Knowledge workbench, Institutional memory, Search, and Sources.
   - `services/control-plane/bff/ports/lifecycle_telemetry_governance.py`: Re-exports typed domain ports for Lifecycle, Telemetry, Incidents, Governance, and Lineage.
   - `services/control-plane/bff/ports/persona_training.py`: Re-exports typed domain ports for Persona profiles, Trainer sessions, Replays, and Rapid evaluation.
   - `services/control-plane/bff/ports/read_surface_ports.py`: Unified `ReadSurfacePorts` container combining all 6 domain areas, providing direct delegation methods and `get_surface_status` diagnostic aggregation with zero `ReadSurfaceStore` references.

2. **Decoupling and Scope Guard Enforcement**:
   - Zero import, instantiation, or delegation to `ReadSurfaceStore` in `services/control-plane/bff/ports/`.
   - Strict adherence to scope guards: zero modifications to `read_store.py`, `main.py`, `domain_ports`, or `persona_client.py`.

3. **Comprehensive Test Suite (`services/control-plane/bff/tests/test_read_surface_port_cutover.py`)**:
   - AST / static analysis ensuring zero forbidden `ReadSurfaceStore` references in `bff/ports/`.
   - Unit and contract tests across all 6 domain slices using in-memory doubles and composite facades.
   - 20 dedicated cutover tests, all passing.

## 2. Verification Results

- `pytest services/control-plane/bff/tests/test_read_surface_port_cutover.py`: `20 passed`
- `pytest services/control-plane/bff/tests/test_*ports*.py`: `140 passed` across all BFF port suites.

## 3. Touched Files Inventory

- Created:
  - `services/control-plane/bff/ports/__init__.py`
  - `services/control-plane/bff/ports/lifecycle_telemetry_governance.py`
  - `services/control-plane/bff/ports/ooda_management.py`
  - `services/control-plane/bff/ports/operations_consultation.py`
  - `services/control-plane/bff/ports/persona_capital_runtime.py`
  - `services/control-plane/bff/ports/persona_training.py`
  - `services/control-plane/bff/ports/read_surface_ports.py`
  - `services/control-plane/bff/ports/research_knowledge_source.py`
  - `services/control-plane/bff/tests/test_read_surface_port_cutover.py`
  - `support/reviews/ACG-RS-PORT-CUTOVER-20260828-review-evidence.md`
  - `docs/deployment/evidence/architecture-cleanup/ACG-RS-PORT-CUTOVER-20260828/evidence.json`
- Modified:
  - None (zero edits to existing files, honoring scope guards).
