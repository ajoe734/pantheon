# Sixteen-candidate execution coverage

This is the complete operator-authorized execution specification. Planned, canonical-admitted, worker-running, PR-open and merged are distinct states. See the signed packet receipts and canonical task authority for current state.

| Audit | Scope | Owning tasks (existing owners first; residual successors only) |
|---|---|---|
| 1 | BFF/Persona duplicate and divergent provisioning responsibilities | OVERLAY-RETIRE-001; STRUCT-RETIRE-001; SIMPLIFY-BFF-RESIDUAL-001 |
| 2 | Native FastAPI dependencies and obsolete test shims | BFF-TEST-FULL-MIGRATION-CORRECTIVE-001; STRUCT-RETIRE-001; SIMPLIFY-BFF-RESIDUAL-001 |
| 3 | OpenClaw single ordinary-turn HTTP transport | SIMPLIFY-OPENCLAW-001; OSS-CORE-BASELINE-001; OSS-INFRA-PROFILES-001 |
| 4 | Native SSE and one Pydantic-v2 API baseline | OSS-CORE-BASELINE-001 |
| 5 | TanStack Query replacing handwritten live cache | FE-QUERY-CACHE-001 |
| 6 | Evaluated typed intent/StrategySpec/lesson extraction | SIMPLIFY-EXTRACTION-001 |
| 7 | One real local retrieval backend | SIMPLIFY-RETRIEVAL-MEMORY-001; OSS-INFRA-PROFILES-001 |
| 8 | Shared governed source and memory retrieval | SIMPLIFY-RETRIEVAL-MEMORY-001 |
| 9 | Research framework usefulness and product footprint | OSS-RESEARCH-FOOTPRINT-001; OSS-RESEARCH-BASELINE-001 |
| 10 | Upstream LEAN plus external Pantheon extensions | OSS-EXECUTION-BASELINE-001 |
| 11 | Numerical QuantLib and Ray API convergence | OSS-RESEARCH-BASELINE-001 |
| 12 | Unused dependencies, locks and complete OSS family upgrades | OSS-CORE-BASELINE-001; OSS-TOOLING-BASELINE-001; FE-DEP-CLOSURE-001; FE-TOOLCHAIN-BASELINE-001; FE-UI-DEPS-BASELINE-001 |
| 13 | Production fixture/mock/overlay retirement | FE-STRICTLIVE-001; FE-QUERY-CACHE-001 |
| 14 | Retired BFF routes and exact cron run identity | SIMPLIFY-OPENCLAW-001; STRUCT-RETIRE-001; SIMPLIFY-BFF-RESIDUAL-001 |
| 15 | Legacy review migration and approval exception retirement | OPS-LEGACY-REVIEW-RETIRE-001 |
| 16 | Maintained storage, Compose profiles and infrastructure images | OSS-INFRA-CHOICE-001; OSS-INFRA-PROFILES-001; OSS-OBJECT-STORE-CUTOVER-001 |

## Executable task graph

| Task | Implementer | Independent review | Dependencies |
|---|---|---|---|
| OSS-COVERAGE-PLAN-001 | Claude | Codex | none — eligible for independent dispatch |
| OSS-CORE-BASELINE-001 | Claude | Codex2 | OSS-COVERAGE-PLAN-001; SIMPLIFY-BFF-RESIDUAL-001; SIMPLIFY-OPENCLAW-001; SIMPLIFY-RETRIEVAL-MEMORY-001 |
| OSS-TOOLING-BASELINE-001 | Antigravity | Codex2 | OSS-COVERAGE-PLAN-001; OPS-LEGACY-REVIEW-RETIRE-001; OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001 |
| OSS-RESEARCH-FOOTPRINT-001 | Claude | Codex2 | OSS-COVERAGE-PLAN-001; DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001; SIMPLIFY-EXTRACTION-001 |
| OSS-RESEARCH-BASELINE-001 | Antigravity | Codex | OSS-COVERAGE-PLAN-001; OSS-RESEARCH-FOOTPRINT-001 |
| OSS-EXECUTION-BASELINE-001 | Claude | Codex2 | OSS-COVERAGE-PLAN-001; STRUCT-RETIRE-001 |
| FE-DEP-CLOSURE-001 | Claude | Codex2 | OSS-COVERAGE-PLAN-001; FE-STRICTLIVE-001; FE-EXACT-PAIR-PROTOCOL-001 |
| FE-TOOLCHAIN-BASELINE-001 | Antigravity | Codex2 | FE-DEP-CLOSURE-001; FE-QUERY-CACHE-001 |
| FE-UI-DEPS-BASELINE-001 | Claude | Codex2 | FE-TOOLCHAIN-BASELINE-001 |
| OSS-INFRA-CHOICE-001 | Claude | Codex2 | OSS-COVERAGE-PLAN-001 |
| OSS-INFRA-PROFILES-001 | Antigravity | Codex | OSS-CORE-BASELINE-001; OSS-TOOLING-BASELINE-001; OSS-RESEARCH-BASELINE-001; OSS-EXECUTION-BASELINE-001; OSS-INFRA-CHOICE-001; DEV-DELIVERY-001; GOV-APPROVAL-AUTHORITY-PREREQUISITE-001; SIMPLIFY-RETRIEVAL-MEMORY-001 |
| OSS-OBJECT-STORE-CUTOVER-001 | Claude | Codex2 | OSS-INFRA-PROFILES-001; OSS-INFRA-CHOICE-001 |
| SIMPLIFY-OPENCLAW-001 | Claude2 | Codex | none — eligible for independent dispatch |
| SIMPLIFY-EXTRACTION-001 | Antigravity | Codex2 | SIMPLIFY-OPENCLAW-001; AGORA-CHAIN-001; DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001 |
| SIMPLIFY-RETRIEVAL-MEMORY-001 | Antigravity | Codex | none — eligible for independent dispatch |
| SIMPLIFY-BFF-RESIDUAL-001 | Antigravity | Codex2 | STRUCT-RETIRE-001 |
| FE-QUERY-CACHE-001 | Antigravity | Codex2 | FE-STRICTLIVE-001; FE-EXACT-PAIR-PROTOCOL-001; FE-DEP-CLOSURE-001 |
| OPS-LEGACY-REVIEW-RETIRE-001 | Claude2 | Codex2 | SYS-SIMPLIFY-AUDIT-20260906; OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001 |
| SYS-SIMPLIFY-IMPLEMENTATION-CLOSURE-001 | Claude | Codex | OSS-OBJECT-STORE-CUTOVER-001; OSS-TOOLING-BASELINE-001; FE-UI-DEPS-BASELINE-001; SIMPLIFY-BFF-RESIDUAL-001; SIMPLIFY-EXTRACTION-001; SIMPLIFY-RETRIEVAL-MEMORY-001; SIMPLIFY-OPENCLAW-001; OPS-LEGACY-REVIEW-RETIRE-001 |

## Inventory coverage and source ownership

The supplied oss-version-task-coverage.csv maps all 447 audit rows (195 Python declarations, 44 package summaries, 94 npm dependencies, 63 image declarations and 51 inline installs) to accountable family tasks. The plan owner refreshes current-dev additions; each implementation owner resolves actual source compatibility and delivers its rows. oss-source-artifacts.csv records the initial exact source grant inventory; tasks.json is the final grant authority.

- OPS-LEGACY-REVIEW-RETIRE-001 waits for the concurrently admitted OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001; no concurrent edits to common/supervisor/review authority.
- Historical PPL-ALLOC-007/009 retain broad FE/BFF grants with unresolved old dependencies. They are not superseded by this plan. Recheck actual leases/readiness before edits; any simultaneously active overlapping writer requires a qualified precise source handoff. Do not fabricate completion or add unrelated missing-dependency chains to this program.
- The final catalog/task acceptance governs; draft design appendices are supporting detail and cannot change admitted scope or infer external authorization.

The storage source task is materialized with real choice/profile dependencies. It performs only the accepted local source option and isolated fixture migration; a necessary new paid/hosted/production-data action stays pending genuine operator authorization. No recommendation is treated as an approval of that external action.

The final join is SYS-SIMPLIFY-IMPLEMENTATION-CLOSURE-001; queued work, plan publication and a running worker cannot substitute for its actual source and integration acceptance.
