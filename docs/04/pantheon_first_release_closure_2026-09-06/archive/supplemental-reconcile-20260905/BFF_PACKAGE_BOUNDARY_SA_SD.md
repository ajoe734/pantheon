# BFF-PACKAGE-BOUNDARY-CORRECTIVE-PREREQUISITE-001

## SA: independently reproduced current-state failure

The original BFF-PACKAGE-001 is already done/archived: approved head
4a69d649de2fe567dd72276ba9d0f172168078ba, merge
341e226d90ef8f838d2a540a6280e16b0941050c (PR #5575). Do not rewrite that
archive, reopen a terminal row, or call its past acceptance current proof.

Current GitHub dev and local origin/dev were independently read as
161f0a0d7c179fb5d5299dc9d4bdcaa2f5b11926 on 2026-09-05.
A Git-blob AST audit (not the dirty shared filesystem) covered 215 selected
non-test-named BFF Python files with zero parse errors. It recorded 197
unqualified BFF-local imports in 51 files. This selection includes two
diagnostic/report scripts, accounting for two of those imports and all
three observed sys.path mutations. Do not falsely describe those three as
production domain/router mutations. The inventory also flags three globals
calls: namespace forwarding in Persona router and module-global store
rebinding in Persona service, which belong to existing structural owners,
not this import-only corrective.

Inventory: /tmp/pantheon-bff-current-production-import-audit-20260905.json.
Each finding has exact path, line and SHA-256 of the source Git blob.

Root reproduced a real lazy-import failure in a clean Python process with
the canonical installed package, no main import and no unqualified models
alias: calling agora.dashboard.router._raise_cross_user_forbidden with a
synthetic bff_error callback raised ModuleNotFoundError: No module named
'models', rather than the expected HTTPException 403. Its source matches the
dev blob. Similar bare imports occur inside capability-denial, validation,
assistant/provider and worker branches; successful app import or collection
does not exercise these branches.

This is a missing part of the original stable-package invariant, not a new
business capability. BFF-TEST-ARCH-001 is actively migrating tests to
standalone domain factories, where these hidden ambient imports obstruct
meaningful negative/contract tests. Its test-only scope does not authorize
production import fixes.

## SD: consolidate imports onto the existing canonical package

Use the existing services.control_plane.bff package root (mapped to the
existing services/control-plane/bff tree). Preserve the one physical source
tree. Change each recorded BFF-local import to the correct package-relative
or fully qualified import, after verifying the intended symbol/module.
Remove only obsolete import fallback branches whose sole purpose was the
old bare-module namespace. Do not keep an unqualified-first/qualified-second
dual mechanism, and do not replace real shared enums/models with locally
copied classes on import failure.

Exercise delayed imports explicitly. Keep permission errors and unavailable
provider behavior semantically intact; unavailable optional third-party
dependencies must not be confused with missing internal package paths.
Do not flatten business logic, modify confidence/holding projections, alter
auth decisions, replace typed ports, delete asserted scenarios or silence
exceptions merely to turn tests green.

Bring the two already-existing diagnostic/report entrypoints onto their
documented canonical module invocation without sys.path surgery. Keep the
entrypoints and their source ownership; do not create a second launcher.
Existing contract_snapshots.execute_plans_bff_contract is the report helper;
qualify its local import too if needed by this entrypoint (it is within the
same artifact). The new regression suite lives under scripts/ to avoid
concurrent edits to BFF-TEST-ARCH-001's BFF test files.

Scope is the explicit files below, one focused test file, one runbook and one
JSON evidence manifest. Do not expand it silently. If the AST record turns
out to be an intentional non-BFF module with the same basename, demonstrate
the resolved module identity and document an exact semantic exception, not a
broad allowlist.

## Boundaries and coordination

- Owner Codex, independent reviewer Claude; source-only functional corrective.
- Depends on original archived BFF-PACKAGE-001, not BFF-TEST-ARCH-001.
- BFF-TEST-ARCH-001 keeps its full migration acceptance and current independent
  batches; do not change its canonical dependency JSON or claim this task
  replaces its hundreds of migrations.
- Existing BFF-ROUTER-STRUCT-001 owns Persona namespace forwarding/structural
  split, and MGMT-READ-001 owns canonical Management projections. This task
  does not take over those business changes.
- Specifically exclude the unauthorized nine-line positions market-value
  fallback currently in BFF-TEST-ARCH anchor 5b1199c833b09afa1be672b9dea58f1590173eda.
  Base on current dev, not that test branch; do not merge or duplicate its hunk.
- No second package tree, sys.modules aliases, path shims, new service,
  supervisor/TaskStore changes, cron, hosted deployment or real product writes.

## Acceptance and evidence

1. Reproduce the failing cross-user forbidden branch first; then prove the
   exact 403/error contract through canonical imports in a fresh subprocess
   without importing main or creating top-level module aliases.
2. Re-audit all listed import sites and every changed production file.
   Unqualified BFF-local imports and legacy import-only fallback branches
   in scope reach zero, or carry individually reviewed semantic identity
   exceptions. Preserve module/class identity across consumers. Include
   nested/function-local imports, not just module-level imports.
3. Add focused regressions for representative capability-denied,
   validation-error, assistant-disabled/provider-unavailable and standalone
   worker import paths. Do not trigger live providers, real commands or data
   writes. Verify changed diagnostic entrypoints can import through the
   canonical package without sys.path surgery.
4. Run focused tests, canonical packaging and relevant existing smoke tests
   with hard timeouts. Report executed vs collected counts separately.
   Protect named paths against recurrence with a precise AST/import-boundary
   check in scripts/test_bff_package_boundary_prerequisite.py; do not add a
   parallel global architecture framework.
5. Deliver source, runbook and genuine task-scoped JSON evidence through the
   existing branch/PR, independent exact-head review, required checks and
   integrator flow. State remaining Persona-global and Management-projection
   gaps honestly; this corrective does not prove product/hosted readiness.
6. After merge, BFF-TEST-ARCH owner rebases onto the reviewed dev change and
   verifies the actual migrated negative-path suites. Root coordinates this
   readback. No undocumented workaround or skipped test is acceptance.

## Exact existing source artifacts (51)

- `services/control-plane/bff/agora/candidate_decisions/router.py`
- `services/control-plane/bff/agora/dashboard/router.py`
- `services/control-plane/bff/agora/dataset_extraction/router.py`
- `services/control-plane/bff/agora/identity/router.py`
- `services/control-plane/bff/agora/interaction/persona_client.py`
- `services/control-plane/bff/agora/interaction/router.py`
- `services/control-plane/bff/agora/interaction/runner.py`
- `services/control-plane/bff/agora/interaction/worker.py`
- `services/control-plane/bff/agora/research/router.py`
- `services/control-plane/bff/agora/router.py`
- `services/control-plane/bff/agora/servant/router.py`
- `services/control-plane/bff/agora/service.py`
- `services/control-plane/bff/agora/strategy_workshop/_admission.py`
- `services/control-plane/bff/agora/strategy_workshop/_common.py`
- `services/control-plane/bff/agora/strategy_workshop/routes/execution.py`
- `services/control-plane/bff/agora/strategy_workshop/routes/session.py`
- `services/control-plane/bff/agora/strategy_workshop/routes/stream.py`
- `services/control-plane/bff/agora/strategy_workshop/routes/versions.py`
- `services/control-plane/bff/agora/trading_room/router.py`
- `services/control-plane/bff/assistant/tool_contracts.py`
- `services/control-plane/bff/capital/router.py`
- `services/control-plane/bff/command_adapters/base.py`
- `services/control-plane/bff/command_executor.py`
- `services/control-plane/bff/console_gap/consult_rules.py`
- `services/control-plane/bff/console_gap/datasources.py`
- `services/control-plane/bff/console_gap/memory_governance.py`
- `services/control-plane/bff/console_gap/permissions.py`
- `services/control-plane/bff/console_gap/route_policies.py`
- `services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py`
- `services/control-plane/bff/control_loops/router.py`
- `services/control-plane/bff/control_loops/service.py`
- `services/control-plane/bff/deployment/service.py`
- `services/control-plane/bff/events/router.py`
- `services/control-plane/bff/evolution/router.py`
- `services/control-plane/bff/incidents/router.py`
- `services/control-plane/bff/incidents/service.py`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/management_read_models/ranking_router.py`
- `services/control-plane/bff/management_read_models/router.py`
- `services/control-plane/bff/management_read_models/service.py`
- `services/control-plane/bff/personas/reconciliation.py`
- `services/control-plane/bff/personas/service.py`
- `services/control-plane/bff/ports/__init__.py`
- `services/control-plane/bff/ports/lifecycle_telemetry_governance.py`
- `services/control-plane/bff/ports/operations_consultation.py`
- `services/control-plane/bff/ports/read_surface_ports.py`
- `services/control-plane/bff/postmortems/router.py`
- `services/control-plane/bff/reproduce_sse_gap.py`
- `services/control-plane/bff/strategies/router.py`
- `services/control-plane/bff/tools_integrations/router.py`
- `services/control-plane/bff/tools_integrations/service.py`

Additional artifacts:
- scripts/test_bff_package_boundary_prerequisite.py
- docs/operations/bff-package-boundary.md
- docs/deployment/evidence/BFF-PACKAGE-BOUNDARY-CORRECTIVE-PREREQUISITE-001/evidence.json
