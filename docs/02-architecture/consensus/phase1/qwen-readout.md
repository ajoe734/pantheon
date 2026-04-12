# Qwen Readout

## Lane

- Agent: Qwen
- Capability focus: BFF API contract-to-implementation gap analysis, service implementation depth, command execution path reality, and front-end integration prerequisites.

## Canonical Sources Read

- L0: `docs/02-architecture/consensus/phase1/README.md`
- L1: `docs/02-architecture/consensus/phase1/planning-session.json`
- L2: `docs/02-architecture/consensus/phase1/pantheon-backend-completion-checklist.md`; `docs/02-architecture/consensus/phase1/starter-draft.md`; `docs/02-architecture/consensus/phase1/consensus-packet.md`; `docs/02-architecture/consensus/phase1/codex-readout.md`
- L2 (codebase evidence): `services/control-plane/bff/main.py`; `services/control-plane/bff/BFF_API_CONTRACT.md`; `services/control-plane/bff/smoke_test.py`; `services/control-plane/bff/command_queue.py`; `services/control-plane/persona/main.py`; `services/control-plane/persona/persona_registry.py`; `services/control-plane/feedback/main.py`; `services/control-plane/governance/` (deployment_plan.py, deployment_saga.py, capital_pool.py, approval_decision.py, evolution_decision.py, evolution_controller.py, persona_capital_binding.py); `services/execution/runtime-manager/runtime_binding.py`; `services/execution/runtime-manager/kill_switch_controller.py`; `services/control_plane/internal_api.py`; `services/control_plane/internal_api_min.py`; `tools/pantheon_admin/cli.py`; `services/frontend/sse_reconciler.py`; `services/frontend/adapter.py`; `.orchestrator/lovable_task_publisher.py`; `docs/screens/F-042-promotion-review.md`; `docs/bff/F-042-promotion-review.md`

## Working Interpretation

### Architecture Summary

The Pantheon backend has **deep governance domain models** but a **thin BFF API surface**. Specifically:

- **49 contract endpoints** exist in `BFF_API_CONTRACT.md` (33 read surfaces + 4 composed views + 3 SSE streams + 6 consultation surfaces + health + operator commands). Only **3 are implemented**: `GET /health`, `POST /api/v1/operator/commands`, `GET /api/v1/operator/commands/{command_id}`. (source: `services/control-plane/bff/main.py` lines 315-440; `services/control-plane/bff/BFF_API_CONTRACT.md`)
- **Operator commands** support 6 command types (`ApproveDeployment`, `PauseRuntime`, `ExecuteRollback`, `ActivateKillSwitch`, `ApproveEvolutionDecision`, `ExecuteEvolutionAction`) with real RBAC validation, concurrent modification detection, degraded-mode warnings, and JSONL audit persistence. (source: `services/control-plane/bff/main.py` lines 133-275, 325-440; `services/control-plane/bff/smoke_test.py` 15+ tests)
- **Background command worker is a stub** — `_process_command_stub()` uses `asyncio.sleep` to simulate `SUBMITTED -> PROCESSING -> EXECUTED` and always returns success. (source: `services/control-plane/bff/main.py` lines 431-448)
- **Governance domain objects are real and tested**: `DeploymentPlan` (845 lines), `DeploymentSaga` (983 lines), `CapitalPool`, `ApprovalDecision`, `EvolutionDecision`, `EvolutionController` (1102 lines), `PersonaCapitalBinding`, `KillSwitchController` (670 lines), `RuntimeBinding`, `Incident`, `TelemetryIngest`, `Feedback`, `PersonaRegistry`, `PortfolioSynthesis`, `SignalConsumer`, `SignalExecutor`. All have smoke/unit tests. (source: respective files in `services/control-plane/governance/`, `services/execution/runtime-manager/`, `services/incident/`, `services/telemetry/`, `services/control-plane/feedback/`, `services/control-plane/persona/persona_registry.py`, `services/optimizer-svc/`, `services/execution/lean_runtime/`)
- **Persona agent (`invoke`) is a scaffold** — all LangGraph nodes (`intent_classify`, `skill_select`, `memory_lookup`, `respond`) return hardcoded placeholders like `"[system not ready -- upstream schemas not locked]"`. (source: `services/control-plane/persona/main.py`)
- **Internal API is scaffold** — both the Flask version (`services/control_plane/internal_api.py`) and pure-Python version (`services/control_plane/internal_api_min.py`) return placeholder IDs and statuses with `"type": "placeholder"`, stub Bearer/MFA validation. (source: both files)
- **`pantheon-admin` CLI is skeleton** — every command prints dry-run output to stdout, makes no HTTP calls. Docstring: *"This is a scaffold: each command currently prints intent and returns appropriate exit codes."* (source: `tools/pantheon_admin/cli.py`)
- **SSE has zero server-side implementation** — only a 20-line placeholder `sse_reconciler.py` and a 30-line scaffold `adapter.py`. No `text/event-stream`, no `StreamingResponse`, no endpoint registration. (source: `services/frontend/sse_reconciler.py`, `services/frontend/adapter.py`)
- **Multi-repo orchestrator is complete on Pantheon side**: Lovable task publisher, coordination repo mirror, GitHub coordination bus, cross-repo issue mapper, portable orchestrator bundle all exist and are functional. (source: `.orchestrator/lovable_task_publisher.py`, `.orchestrator/coordination_repo_mirror.py`, `.orchestrator/cross_repo_issue_mapper.py`, `.orchestrator/multi_repo_registry.py`, `scripts/orchestrator_bundle.py`)

### Delivery Order

My analysis suggests the following dependency graph, which differs slightly from the checklist's flat listing:

1. **Command execution hardening is a prerequisite for composed views that include action panels.** The F-042 Promotion Review page requires `allowedActions.canPromoteToPaper` to be backend-shaped, and CTA visibility depends on real governance/runtime state — not stub responses. (source: `docs/bff/F-042-promotion-review.md`)
2. **A minimal set of read surfaces must be implemented before any front-end page can render without mocks.** The checklist says "33 read surfaces" but the first front-end page (F-042) only needs: `DP-02`, `CP-02`, `RT-02`, `RT-04` (deployment review composed view). (source: `docs/bff/F-042-promotion-review.md` lists required fields: `deployment_plan`, `capital_pool`, `bindings`, `runtime_binding`)
3. **SSE is not a blocking prerequisite for the first UI integration.** The F-042 acceptance criteria do not mention real-time feeds. SSE can be deferred to Wave 2 or later.
4. **`pantheon-admin` CLI upgrade and internal API hardening are important for operator safety but not blocking for the first Lovable UI page.** They belong in a parallel wave, not a sequential blocker.

### Ownership Boundaries

- The **BFF read surfaces** should stay in Pantheon — they compose existing governance/runtime objects into page-shaped responses. (source: `BFF_API_CONTRACT.md` §2: "The BFF is read-oriented. It must never create, modify, or delete canonical state.")
- **Command execution workers** should hand off to runtime/engine repos — the stub worker needs to call real `DeploymentSaga`, `RuntimeBinding`, `KillSwitchController` objects, which live in their respective service directories.
- **Composed views** are pure BFF aggregation logic and belong in Pantheon.
- **SSE transport** is a BFF concern, but event sourcing comes from runtime/incident services — a shared contract boundary.

## Risks / Contradictions

- **Risk 1**: The checklist treats all 33 read surfaces as a single block. In reality, they have a steep dependency curve: composed views depend on multiple read surfaces, which depend on backing service health. Implementing them all-at-once is high-risk; a surface-by-surface approach with the composed view as the integration test is safer.
- **Risk 2**: The persona service is a scaffold with hardcoded responses. If any read surface (e.g., PS-01 through PS-06) depends on persona runtime state, those surfaces will return meaningless data until persona agent wiring is complete. (source: `services/control-plane/persona/main.py` — all nodes return `"unknown"` / `"[system not ready]"`)
- **Risk 3**: The internal API scaffold returns placeholder responses. If the BFF read surfaces call the internal API as a downstream dependency (which the architecture implies), they will receive `"type": "placeholder"` data and propagate it to the front-end. (source: `services/control_plane/internal_api_min.py`)
- **Risk 4**: The orchestrator can publish Lovable task packets and mirror contracts, but the `front-ai-trading-system` checkout is not present in this workspace. The mirror will fail until the sibling repo exists. (source: `.orchestrator/coordination_repo_mirror.py` writes to `../front-ai-trading-system/docs/pantheon-handoffs/`)

## Suggested Task Slices

- **Slice A (Wave 1 — Minimal Viable Read + Composed View)**: Implement the composed view `GET /api/v1/operator/deployment-review/{plan_id}` end-to-end. This requires implementing its constituent read surfaces (DP-02, CP-02, CP-04, RT-02, RT-04) against real backing services, and returning the F-042 page-shaped payload. This is the smallest credible front-end integration target. (source: `docs/bff/F-042-promotion-review.md`, `BFF_API_CONTRACT.md` composed view spec)
- **Slice B (Wave 2 — Read Surface Expansion)**: Implement remaining read surfaces grouped by backing service: persona cluster (PS-01 to PS-06), incident cluster (IN-01 to IN-05), telemetry (TL-01 to TL-03), lineage (LN-01 to LN-03), capital (CP-01, CP-03), runtime (RT-01, RT-03), evolution (EV-01 to EV-04), deployment (DP-01, DP-03, DP-04). Implement composed views for incident response, post-incident review, and persona management.
- **Slice C (Wave 2 — Command Execution Hardening)**: Replace `_process_command_stub` with real workers that call `DeploymentSaga`, `RuntimeBinding`, `KillSwitchController`, `EvolutionController`. Upgrade internal API from scaffold to real protected control path. (source: `services/control-plane/bff/main.py` line 431; `services/control_plane/internal_api.py`)
- **Slice D (Wave 3 — SSE + CLI)**: Implement 3 SSE streams. Upgrade `pantheon-admin` from print-only scaffold to real HTTP-calling operator CLI. (source: `tools/pantheon_admin/cli.py`, `services/frontend/sse_reconciler.py`)
- **Slice E (Decision Item)**: Decide whether first-wave writes stay on generic `POST /api/v1/operator/commands` or expose resource-shaped routes (e.g., `POST /api/v1/deployments/{plan_id}/promote`). The F-042 contract implies `allowedActions.canPromoteToPaper` drives a CTA, but the actual promotion action would go through the generic command endpoint. This is acceptable for Wave 1 but should be revisited for Wave 2.

## Citations

- [services/control-plane/bff/main.py] Only 3 routes implemented: GET /health (line 315), POST /api/v1/operator/commands (line 325), GET /api/v1/operator/commands/{command_id} (line 407). All other 37 contract endpoints are absent.
- [services/control-plane/bff/main.py lines 431-448] `_process_command_stub` is an async stub using `asyncio.sleep` that always transitions to EXECUTED with `"Command executed successfully (stub worker)"`.
- [services/control-plane/bff/smoke_test.py] 15+ tests covering auth, RBAC, MFA, concurrent modification, degraded mode, and all 6 command types — proving the command path is real, not scaffold.
- [services/control-plane/persona/main.py] All 4 LangGraph nodes return hardcoded values: `"unknown"`, `"[system not ready -- upstream schemas not locked]"`.
- [services/control_plane/internal_api.py] All 5 endpoints return placeholder IDs. Stub Bearer/MFA validation. Comment: "Minimal Protected Internal API scaffold for APP-002".
- [tools/pantheon_admin/cli.py] Every command prints dry-run output to stdout. Docstring: "This is a scaffold: each command currently prints intent and returns appropriate exit codes."
- [services/frontend/sse_reconciler.py] 20-line placeholder. Docstring: "This file is a placeholder to host SSE subscription and reconciliation logic."
- [services/frontend/adapter.py] 30-line scaffold with in-memory pub/sub. No SSE transport.
- [services/control-plane/bff/BFF_API_CONTRACT.md] Defines 49 total endpoints: 33 read surfaces, 4 composed views, 3 SSE streams, 6 consultation surfaces, health, operator commands.
- [docs/bff/F-042-promotion-review.md] Requires: deployment_plan, capital_pool, bindings, runtime_binding, allowedActions.canPromoteToPaper, latestRun.progress, review.riskSummary.
- [docs/screens/F-042-promotion-review.md] Acceptance: "Page renders with no mock data", "Promote to paper CTA visibility is backend-driven only", "If required data is missing, the front-end must emit a bff-gap handoff instead of inventing local state."
- [.orchestrator/lovable_task_publisher.py] publish_lovable_task_packet() produces YAML packets + Markdown prompts. render_lovable_prompt() includes constraints: "use existing bff client only", "do not add raw fetch in components", "do not import demo providers".
- [services/control-plane/governance/deployment_plan.py] 845 lines with DeploymentPlanStore, stage planner, transition derivation, rollback linkage, execution projections. Tested.
- [services/execution/runtime-manager/kill_switch_controller.py] 670 lines with soft/hard trigger classification, action selection matrix, safe-mode state machine, immutable audit trail. Tested.
- [services/control-plane/governance/evolution_controller.py] 1102 lines with threshold evaluator, classification rules, freeze/rollback dispatch. Tested.
