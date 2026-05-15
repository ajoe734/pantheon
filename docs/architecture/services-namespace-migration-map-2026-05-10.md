# services/ Namespace Migration Map

**Task**: SVC-RENAME-001
**Author**: Claude2
**Date**: 2026-05-10
**Status**: Plan only — no code changed
**Reviewer**: Codex2

---

## 1. Inventory — All Duplicate or Ambiguous Directory Pairs

This map covers every directory pair in `services/` where the same concept appears twice
under different naming conventions, or where role separation is not obvious from the names.

---

### Pair A — `control_plane` (snake) vs `control-plane` (kebab)

| Property | `services/control_plane/` | `services/control-plane/` |
|---|---|---|
| Naming | snake_case (Python-importable) | kebab-case (canonical service tree) |
| Role | **Legacy** Flask internal API for operator incident control (APP-002) | **Canonical** control-plane multi-service tree (bff, router, persona, governance, feedback, etc.) |
| Key files | `internal_api.py`, `internal_api_min.py` | bff/, cron/, feedback/, governance/, permissions/, persona/, router/, skills/, specs/ |
| Python module | `services.control_plane.internal_api` | path-injected (`sys.path.insert`) |
| Docker | Not a Docker service | Sub-directories are Docker services |
| Classification | **True duplicate name conflict** — two distinct things both called "control plane" |

**Import sites for `services.control_plane` (snake):**

| File | Line | Import |
|---|---|---|
| `services/control_plane/test_internal_api_incident.py` | 38 | `import services.control_plane.internal_api` |
| `services/runtime-manager/internal_api_routes.py` | 210 | `from services.control_plane import internal_api as legacy` |
| `services/runtime-manager/smoke_test.py` | 416 | `import_module("services.control_plane.internal_api")` |
| `services/runtime-manager/test_internal_api_routes.py` | 70 | `importlib.import_module("services.control_plane.internal_api")` |
| `services/runtime-manager/test_runtime_hardening.py` | 343 | `importlib.import_module("services.control_plane.internal_api")` |
| `tests/run_internal_api_smoke.py` | 4 | `from services.control_plane import internal_api_min` |
| `scripts/smoke_operator_fallback_drills.py` | 384 | string prefix check `services.control_plane.bff` |

**Migration target:** Move the legacy implementation files into
`services/control-plane/internal/`, but keep an importable snake-case shim package under
`services/control_plane/`. The executable import target is
`services.control_plane.internal.*`; the shim package must load the implementation files from the
kebab-case service tree because `services/control-plane` cannot be imported as a normal Python
package.

| Current path | Target path | Import rewrite |
|---|---|---|
| `services/control_plane/internal_api.py` | `services/control-plane/internal/internal_api.py` | Implementation file moves; import target becomes `services.control_plane.internal.internal_api` |
| `services/control_plane/internal_api_min.py` | `services/control-plane/internal/internal_api_min.py` | Implementation file moves; import target becomes `services.control_plane.internal.internal_api_min` |
| `services/control_plane/__init__.py` | keep `services/control_plane/__init__.py` | Parent compatibility package; do not move into the kebab tree |
| new file | `services/control_plane/internal/__init__.py` | Declares the executable shim package |
| new file | `services/control_plane/internal/internal_api.py` | `importlib.util.spec_from_file_location(...)` loader for `../../control-plane/internal/internal_api.py` |
| new file | `services/control_plane/internal/internal_api_min.py` | Loader for `../../control-plane/internal/internal_api_min.py` |
| transition file | `services/control_plane/internal_api.py` | Legacy wrapper: `from services.control_plane.internal.internal_api import *` until callers are rewritten |
| transition file | `services/control_plane/internal_api_min.py` | Legacy wrapper: `from services.control_plane.internal.internal_api_min import *` until callers are rewritten |

The shim modules contain no business logic; they only make the kebab-case service tree executable
through the snake-case Python namespace. This avoids two diverging copies of the legacy control
surface during the transition.

---

### Pair B — `registry` vs `registry-core`

| Property | `services/registry/` | `services/registry-core/decision-domain/` |
|---|---|---|
| Role | Full deployable registry service | JSON schema definitions (no `__init__.py`, no runtime code) |
| Key files | Dockerfile, main.py, service.py, models.py, experiments/, lineage/, promotion/, strategy-specs/ | `*.schema.json` (allocation_decision, regime_state, risk_adjudication, signal_inference, universe_selection) |
| Python module | `services.registry.*` | Not imported as Python; referenced by path only |
| Docker | `registry:` service | Not a Docker service |
| Classification | **Role-separated** — service vs schema library; `registry-core` name is misleading |

**Import sites for `registry-core`:**

| File | Line | Reference |
|---|---|---|
| `scripts/validate_bg003.py` | 11 | `SCHEMA_DIR = Path("services/registry-core/decision-domain")` |

**Migration target:** Move schemas under the registry service tree.

| Current path | Target path | Import/path rewrite |
|---|---|---|
| `services/registry-core/decision-domain/*.schema.json` | `services/registry/decision_domain/*.schema.json` | `scripts/validate_bg003.py:11` SCHEMA_DIR path |
| `services/registry-core/decision-domain/validate_schemas.py` | `services/registry/decision_domain/validate_schemas.py` | script invocation path |
| `services/registry-core/decision-domain/README.md` | `services/registry/decision_domain/README.md` | — |

---

### Pair C — `incident` (singular) vs `incidents` (plural)

| Property | `services/incident/` | `services/incidents/` |
|---|---|---|
| Role | Domain library — IncidentCase, IncidentStore, EvidenceCollector, pg_store | Deployable FastAPI HTTP service — wraps the domain library |
| Key files | incident.py, evidence_collector.py, pg_store.py, reference_validation.py | Dockerfile, main.py, models.py, requirements.txt |
| Python module | `services.incident.*` (heavily imported) | Imports from `services.incident.*` |
| Docker | Not a Docker service | `incidents:` at port 8090 |
| Classification | **Role-separated** — singular = domain library, plural = HTTP service. Naming is intentional. |

**Import sites for `services.incident` (domain library):**

| Importer | Count | Pattern |
|---|---|---|
| `services/incidents/main.py` | 5+ | `from services.incident.incident import …` |
| `services/postmortems/main.py` | 4+ | `from services.incident.incident import …`, `from services.incident.pg_store import …` |
| `services/control-plane/governance/test_*.py` | 2 | `from services.incident.incident import …` |
| `services/foundation/tests/` | 1 | `from services.incident.pg_store import …` |
| `services/incident/` (self) | Internal | `from services.incident.incident import …` |

**Migration target:** No directory rename needed. The plural/singular convention is clear.
Recommend adding a `README.md` to `services/incident/` explicitly labeling it a domain library
and noting that `services/incidents/` is the HTTP service that exposes it.

---

### Pair D — `source_ingestion` (directory, snake) vs `source-ingest` (Docker service name, kebab)

| Property | `services/source_ingestion/` | Docker service `source-ingest` |
|---|---|---|
| Role | Python package — connectors, scheduler, ingest manager | HTTP service name used in docker-compose and env vars |
| Key files | Dockerfile, main.py, connectors/, scheduler_worker.py | — |
| Python module | `services.source_ingestion.*` (Python requires snake_case) | `SOURCE_INGEST_API_URL`, `PANTHEON_SOURCE_INGEST_API_URL` |
| Docker | `source-ingest:` service using `services/source_ingestion/Dockerfile` | `source-ingest:` service |
| Classification | **Snake-kebab split** — Python directory must be snake_case; Docker service is kebab. Not a conflict, a known two-name pattern. |

**Docker-compose files referencing `source_ingestion` Dockerfile:**

| File | Usage |
|---|---|
| `docker-compose.yml` | `dockerfile: services/source_ingestion/Dockerfile` (2 places: service + scheduler) |

**Migration target:** No change required. The snake/kebab split is intentional:
- Python imports use `services.source_ingestion.*`
- HTTP service URLs use `http://source-ingest:8097`
- A `README.md` in `services/source_ingestion/` should note the two-name convention.

---

### Pair E — `feedback` (root) vs `control-plane/feedback`

| Property | `services/feedback/` | `services/control-plane/feedback/` |
|---|---|---|
| Role | Domain library (models.py, store.py, schema/) **plus** a stub HTTP service (main.py, Dockerfile) | FB-002 trader feedback ingestion API (full FastAPI service with schema validation, governance audit hook) |
| Key files | models.py, store.py, schema/, main.py, Dockerfile, requirements.txt | Dockerfile, main.py, schema_validation.py, store.py |
| Python module | `services.feedback.models`, `services.feedback.store` (imported by trl, tests) | Imports `services.feedback.models` and `services.feedback.store` as deps |
| Docker (main compose) | `feedback:` uses **`services/control-plane/feedback/Dockerfile`** | — |
| Docker (control compose) | `feedback:` uses **`services/feedback/Dockerfile`** | — |
| Classification | **True name collision and split Dockerfile** — two docker-compose files point `feedback:` at different Dockerfiles |

**Docker-compose build split:**

| Compose file | `feedback:` builds from |
|---|---|
| `docker-compose.yml` (default) | `services/control-plane/feedback/Dockerfile` |
| `docker-compose.control.yml` | `services/feedback/Dockerfile` |

**Import sites for `services.feedback.*`:**

| File | Import |
|---|---|
| `services/feedback/tests/test_feedback_store.py` | `from services.feedback.models import …`, `from services.feedback.store import …` |
| `services/learning/trl/activation_smoke.py` | `from services.feedback.models import …`, `from services.feedback.store import …` |
| `services/learning/trl/test_adapter.py` | `from services.feedback.models import …` |

**Downstream import sites for the control-plane feedback service package:**

| File | Current dependency | Required rewrite |
|---|---|---|
| `services/telemetry/feedback_adapter.py:18-23` | Inserts `services/control-plane` into `sys.path`, then `from feedback.store import TraderFeedbackStore, parse_rfc3339` | Replace the path mutation and bare `feedback.store` import with `from services.trader_feedback.store import TraderFeedbackStore, parse_rfc3339` (or an equivalent importable shim) |
| `services/control-plane/feedback/Dockerfile` | Sets `PYTHONPATH=/workspace:/workspace/services/control-plane/feedback` and runs `uvicorn main:app --app-dir /workspace/services/control-plane/feedback` | Update PYTHONPATH/app-dir to the new service directory and keep local service imports executable |

**Migration target:** Rename `services/control-plane/feedback/` -> `services/trader-feedback/`
as the deployable service tree, and add an importable snake-case package
`services/trader_feedback/` for downstream Python consumers. The `feedback:` Docker service name
can remain stable during the directory move; this task only normalizes the source tree.

| Current path | Target path | Rewrite needed |
|---|---|---|
| `services/control-plane/feedback/Dockerfile` | `services/trader-feedback/Dockerfile` | `docker-compose.yml` build path |
| `services/control-plane/feedback/main.py` | `services/trader-feedback/main.py` | Module path references inside file |
| `services/control-plane/feedback/store.py` | `services/trader-feedback/store.py` | — |
| `services/control-plane/feedback/schema_validation.py` | `services/trader-feedback/schema_validation.py` | — |
| new file | `services/trader_feedback/__init__.py` | Python package marker for importable downstream consumers |
| new file | `services/trader_feedback/store.py` | Loader/re-export for `services/trader-feedback/store.py` |
| new file | `services/trader_feedback/schema_validation.py` | Loader/re-export for `services/trader-feedback/schema_validation.py` |
| `services/telemetry/feedback_adapter.py` | no move | Rewrite to `services.trader_feedback.store`; remove `services/control-plane` sys.path injection |

---

### Pair F — `governance` (root HTTP service) vs `control-plane/governance` (domain library)

| Property | `services/governance/` | `services/control-plane/governance/` |
|---|---|---|
| Role | Deployable FastAPI governance HTTP service | Governance domain library (schemas, contracts, approval, capital pool, deployment saga) |
| Key files | Dockerfile, main.py, models.py, pg_store.py, audit_log.py, authz.py, write_authority.py | approval_decision.py, capital_pool.py, deployment_plan.py, evolution_decision.py, persona_capital_binding.py, schema JSONs |
| Python module | `services.governance.*` | Path-injected: `_CP_GOV = Path(…) / "control-plane" / "governance"` |
| Docker | `governance:` at port 8082 using `services/governance/Dockerfile` | Not a Docker service; library only |
| Classification | **Role-separated** — root service exposes HTTP API; `control-plane/governance` is the domain object library used by many services |

**Import sites for `control-plane/governance` (path-injected, no Python module name):**

| File | Pattern |
|---|---|
| `services/governance/main.py` | `_CP_GOV = Path(…) / "control-plane" / "governance"` |
| `services/governance/pg_store.py` | same pattern |
| `services/governance/test_governance_api.py` | same pattern |
| `services/capital/main.py` | `_CP_GOV = Path(…) / "control-plane" / "governance"` |
| `services/capital/pg_store.py` | same pattern |
| `services/deployment/service.py` | same pattern |
| `services/evolution/main.py` | same pattern |
| `services/evolution/seed_data.py` | same pattern |
| `services/evolution/test_evolution_service.py` | same pattern |
| `services/promotion/main.py` | same pattern (`GOVERNANCE_DIR = …`) |
| `services/promotion/pg_store.py` | same pattern |

**Migration target:** No rename required; role separation is correct. However, `control-plane/governance`
could be renamed to `control-plane/governance-domain` or `control-plane/gov-objects` to make the
library-vs-service distinction visible. If renamed, all `_CP_GOV` path strings must be updated.
Low priority — defer to follow-up task.

---

### Pair G — `lineage-read` (root service) vs `telemetry/lineage_read` (domain library)

| Property | `services/lineage-read/` | `services/telemetry/lineage_read/` |
|---|---|---|
| Role | Deployable HTTP wrapper for the LIN-002 read engine | Domain library: `LineageReadService` class, used by the HTTP service |
| Key files | Dockerfile, main.py, requirements.txt, test_main.py | service.py, benchmark.py |
| Python module | Imports `services.telemetry.lineage_read.LineageReadService` | `services.telemetry.lineage_read.*` |
| Docker | `lineage-read:` at port 8094 | Not a Docker service |
| Classification | **Role-separated and clean** — the root service file explicitly documents it wraps `telemetry/lineage_read`. No action needed. |

---

### Pair H — `promotion` (root HTTP service) vs `registry/promotion` (domain library)

| Property | `services/promotion/` | `services/registry/promotion/` |
|---|---|---|
| Role | Deployable FastAPI promotion HTTP service | Promotion domain library: PromotionGate, PromotionState, cli.py |
| Key files | Dockerfile, main.py, pg_store.py, requirements.txt | gate.py, cli.py, smoke_test_gate.py, test_gate.py, README.md |
| Python module | `services.promotion.*` | `services.registry.promotion.gate`, `services.registry.promotion.cli` |
| Docker | `promotion:` using `services/promotion/Dockerfile` | Not a Docker service |
| Classification | **Role-separated** — root is HTTP service, `registry/promotion` is domain library |

**Import sites for `services.registry.promotion`:**

| File | Import |
|---|---|
| `cli.py` (repo root) | `from services.registry.promotion.cli import main` |
| `gate.py` (repo root) | `from services.registry.promotion.gate import …` |
| `services/execution/smoke_test_artifact_loader.py` | `from services.registry.promotion.gate import PromotionGate, PromotionState` |
| `services/execution/test_artifact_loader.py` | `from services.registry.promotion.gate import PromotionGate, PromotionState` |

**Migration target:** No rename required. Pattern is clear.

---

### Pair I — `runtime-manager` (root HTTP service) vs `execution/runtime-manager` (domain library)

| Property | `services/runtime-manager/` | `services/execution/runtime-manager/` |
|---|---|---|
| Role | Deployable FastAPI runtime manager HTTP service | Domain library: runtime_binding.py, kill_switch_controller.py |
| Key files | Dockerfile, main.py, service.py, runtime_manager_client.py | runtime_binding.py, kill_switch_controller.py, kill_switch_controller.schema.json |
| Python module | `services.runtime_manager.*` (path-injected) | Not imported from outside; used only inside `services/runtime-manager/` via path |
| Docker | `runtime-manager:` at port 8081 | Not a Docker service |
| Classification | **Role-separated** — root is HTTP service, `execution/runtime-manager` is domain library |

**Migration target:** No rename required. Consider documenting `execution/runtime-manager` as the
domain library backing the runtime-manager service, and `execution/artifact-loader` as similar.

---

### Pair J — `learning` vs `research` (OSS framework overlap)

| Property | `services/learning/` | `services/research/` |
|---|---|---|
| Role | Dormant/deferred OSS activation implementations | Active research service with OSS adapters and research ingestion |
| Key files | DEFERRED_OSS_ACTIVATION_MAP.md, dspy/, qlib/, imitation/, trl/, rl/ | Dockerfile, main.py, adapters/, dspy/, finrl/, imitation/, mlflow/, qlib/, quantlib/, rllib/, statsmodels/, vectorbt/, strategy_spec/ |
| Python module | `services.learning.trl.*` (some tests import this) | `services.research.*` |
| Docker | No top-level Dockerfile | `research:` service via Dockerfile |
| Classification | **True functional overlap** — `learning/` predates `research/`; it owns executable DSpy/imitation/TRL activation code while `research/` owns the active research service and framework container stubs |

**Overlap sub-directories (both have these):**

| Sub-dir | `learning/` state | `research/` state |
|---|---|---|
| `dspy/` | adapter.py, README, examples, smoke/test, worker.py, Dockerfile, requirements.txt | Dockerfile and requirements.txt only |
| `qlib/` | ACTIVATION_CRITERIA.md only | adapter/, examples, Dockerfile, full activation suite |
| `imitation/` | adapter.py, README, examples, smoke/test, worker.py, Dockerfile, requirements.txt | Dockerfile and requirements.txt only |
| `trl/` | adapter/, preflight.py, activation_smoke, worker.py, tests, activation criteria/docs | **No `services/research/trl` directory exists today** |

**Import sites for `services.learning.*`:**

| File | Import |
|---|---|
| `services/learning/trl/activation_smoke.py` | `from services.learning.trl.adapter import …` |
| `services/learning/trl/smoke_test.py` | `from services.learning.trl.adapter import …` |
| `services/learning/trl/test_adapter.py` | `from services.learning.trl.adapter import …` |
| `services/learning/trl/test_preflight.py` | `from services.learning.trl.preflight import …` |
| `scripts/smoke_dormant_oss_matrix.py` | string: `from services.learning.trl.preflight import …` |

**Runtime/path references for `services/learning/trl`:**

| File | Reference |
|---|---|
| `scripts/smoke_dormant_oss_matrix.py:275` | Executes `services/learning/trl/smoke_test.py` |
| `scripts/run_research_activation_gates.py:119-121` | Requires `services/learning/trl/adapter/trl_adapter.py`, smoke test, and requirements file |
| `services/research-worker-gateway/main.py:101,105` | Worker entrypoint and preflight path point to `services/learning/trl/worker.py` and `preflight.py` |
| `services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py:136` | Asserts the current TRL worker entrypoint path |
| `services/research/main.py:41` | Activation gate path points to `services/learning/trl/ACTIVATION_CRITERIA.md` |
| `services/policy-learning/main.py:39` | Activation gate path points to `services/learning/trl/ACTIVATION_CRITERIA.md` |

**Additional non-import path references to rewrite after the move:**

| Area | Current references |
|---|---|
| DSpy | `services/research/requirements.txt`, `services/research/dspy/requirements.txt`, `services/control-plane/persona/lp001/contract.md`, and self-references inside `services/learning/dspy/README.md` / `adapter.py` point at `services/learning/dspy` |
| imitation | `services/research/requirements.txt`, `services/research/imitation/requirements.txt`, and self-references inside `services/learning/imitation/README.md` / `adapter.py` point at `services/learning/imitation` |
| TRL | `services/learning/trl/Dockerfile`, README/activation docs, adapter install-error strings, and `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` point at `services/learning/trl` |

**Migration target:** Merge `services/learning/dspy` and `services/learning/imitation` into
`services/research/dspy` and `services/research/imitation` respectively, preserving the existing
research Dockerfile/requirements stubs or consciously replacing them after diff review. Move
`services/learning/trl` -> a **new** `services/research/trl` target; do not assume there is an
existing research TRL implementation to merge with.
Retire `services/learning/` after content merge.

Import rewrites needed:

| Current import | Target import |
|---|---|
| `services.learning.trl.adapter` | `services.research.trl.adapter` |
| `services.learning.trl.preflight` | `services.research.trl.preflight` |

No active external `services.learning.dspy.*` or `services.learning.imitation.*` imports were found
under `services/`, `scripts/`, or `tests/`; those implementations use local `from adapter import ...`
inside their own directories. Their follow-up task is mostly file movement plus non-import path/doc
rewrite.

---

## 2. Priority Classification

| Priority | Pair | Reason |
|---|---|---|
| **P1 — Must fix** | A (`control_plane` snake vs kebab) | Active Python module path collision; runtime-manager depends on legacy path |
| **P1 — Must fix** | E (`feedback` split Dockerfile) | Two docker-compose files build different images for the same service name |
| **P2 — Should fix** | J (`learning` vs `research` overlap) | Duplicate OSS adapter dirs confuse activation tracking |
| **P2 — Should fix** | B (`registry-core`) | Misleading name; schemas should live under registry service |
| **P3 — Document only** | C (`incident`/`incidents`) | Intentional pattern; needs README clarification |
| **P3 — Document only** | D (`source_ingestion`/`source-ingest`) | Intentional snake/kebab split; needs README |
| **P3 — Document only** | F (`governance` split) | Role-separated; optional rename to `governance-domain` |
| **P3 — Document only** | G (`lineage-read`/`telemetry/lineage_read`) | Clean separation; no action |
| **P3 — Document only** | H (`promotion` split) | Role-separated; no action |
| **P3 — Document only** | I (`runtime-manager` split) | Role-separated; no action |

---

## 3. Migration Map — File Moves

### Pair A: `control_plane` → `control-plane/internal`

| Source file | Destination file | Notes |
|---|---|---|
| `services/control_plane/internal_api.py` | `services/control-plane/internal/internal_api.py` | Canonical implementation move; leave a transition wrapper at the old path |
| `services/control_plane/internal_api_min.py` | `services/control-plane/internal/internal_api_min.py` | Canonical implementation move; leave a transition wrapper at the old path |
| `services/control_plane/__init__.py` | keep in place | Parent package remains the importable snake-case namespace |
| new file | `services/control_plane/internal/__init__.py` | New executable shim package |
| new file | `services/control_plane/internal/internal_api.py` | Loads/re-exports `services/control-plane/internal/internal_api.py` |
| new file | `services/control_plane/internal/internal_api_min.py` | Loads/re-exports `services/control-plane/internal/internal_api_min.py` |
| `services/control_plane/test_internal_api_incident.py` | `services/control-plane/internal/test_internal_api_incident.py` or `tests/test_internal_api_incident.py` | Update imports to `services.control_plane.internal.internal_api` |

### Pair B: `registry-core/decision-domain` → `registry/decision_domain`

| Source file | Destination file |
|---|---|
| `services/registry-core/decision-domain/*.schema.json` | `services/registry/decision_domain/*.schema.json` |
| `services/registry-core/decision-domain/validate_schemas.py` | `services/registry/decision_domain/validate_schemas.py` |
| `services/registry-core/decision-domain/README.md` | `services/registry/decision_domain/README.md` |
| `services/registry-core/decision-domain/examples/` | `services/registry/decision_domain/examples/` |

### Pair E: `control-plane/feedback` → `trader-feedback`

| Source file | Destination file |
|---|---|
| `services/control-plane/feedback/Dockerfile` | `services/trader-feedback/Dockerfile` |
| `services/control-plane/feedback/main.py` | `services/trader-feedback/main.py` |
| `services/control-plane/feedback/store.py` | `services/trader-feedback/store.py` |
| `services/control-plane/feedback/schema_validation.py` | `services/trader-feedback/schema_validation.py` |
| `services/control-plane/feedback/requirements.txt` | `services/trader-feedback/requirements.txt` |
| `services/control-plane/feedback/test_feedback_api.py` | `services/trader-feedback/test_feedback_api.py` |
| `services/control-plane/feedback/test_feedback_store.py` | `services/trader-feedback/test_feedback_store.py` |
| `services/control-plane/feedback/README.md` | `services/trader-feedback/README.md` |
| new file | `services/trader_feedback/__init__.py` |
| new file | `services/trader_feedback/store.py` |
| new file | `services/trader_feedback/schema_validation.py` |
| no move | `services/telemetry/feedback_adapter.py` import rewrite |

### Pair J (partial): `learning` → `research`

| Source path | Destination path |
|---|---|
| `services/learning/dspy/` | `services/research/dspy/` (merge into existing Dockerfile/requirements stub after diff review) |
| `services/learning/imitation/` | `services/research/imitation/` (merge into existing Dockerfile/requirements stub after diff review) |
| `services/learning/trl/` | `services/research/trl/` (new target; `services/research/trl` does not currently exist) |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | `services/research/DEFERRED_OSS_ACTIVATION_MAP.md` |

`services/learning/rl/` and `services/learning/qlib/` (ACTIVATION_CRITERIA.md only) — review separately;
`research/` already has full qlib and rllib implementations.

---

## 4. Import Path Rewrite Rules

### Pair A rewrites

| File | Old import | New import |
|---|---|---|
| `services/runtime-manager/internal_api_routes.py:210` | `from services.control_plane import internal_api as legacy` | `from services.control_plane.internal import internal_api as legacy` ¹ |
| `services/runtime-manager/smoke_test.py:416` | `import_module("services.control_plane.internal_api")` | `import_module("services.control_plane.internal.internal_api")` |
| `services/runtime-manager/test_internal_api_routes.py:70` | `importlib.import_module("services.control_plane.internal_api")` | `importlib.import_module("services.control_plane.internal.internal_api")` |
| `services/runtime-manager/test_runtime_hardening.py:343` | `importlib.import_module("services.control_plane.internal_api")` | same |
| `tests/run_internal_api_smoke.py:4` | `from services.control_plane import internal_api_min` | `from services.control_plane.internal import internal_api_min` |

¹ `control-plane` is not a valid Python package identifier (contains a hyphen). The new import path
must be backed by real files under `services/control_plane/internal/`; moving files only to
`services/control-plane/internal/` is not enough.

**Compat shim approach (zero-downtime):**
1. Move implementation files to `services/control-plane/internal/`.
2. Keep `services/control_plane/__init__.py` as the importable parent package.
3. Add `services/control_plane/internal/__init__.py`.
4. Add loader modules `services/control_plane/internal/internal_api.py` and
   `services/control_plane/internal/internal_api_min.py` that load the implementation files from
   `services/control-plane/internal/` with `importlib.util.spec_from_file_location`.
5. Keep legacy wrappers `services/control_plane/internal_api.py` and
   `services/control_plane/internal_api_min.py` re-exporting the new shim modules until all imports
   have been rewritten.
6. Update callers to `services.control_plane.internal.*`; delete the top-level legacy wrappers in a
   later cleanup after CI confirms no old imports remain.

### Pair B rewrites

| File | Old path | New path |
|---|---|---|
| `scripts/validate_bg003.py:11` | `Path("services/registry-core/decision-domain")` | `Path("services/registry/decision_domain")` |

### Pair E rewrites

| File | Old path | New path |
|---|---|---|
| `docker-compose.yml` feedback build | `dockerfile: services/control-plane/feedback/Dockerfile` | `dockerfile: services/trader-feedback/Dockerfile` |
| `services/control-plane/feedback/Dockerfile` | `PYTHONPATH=/workspace:/workspace/services/control-plane/feedback` and app-dir `/workspace/services/control-plane/feedback` | `PYTHONPATH=/workspace:/workspace/services/trader-feedback` and app-dir `/workspace/services/trader-feedback` |
| `services/control-plane/feedback/main.py` internal refs | `services/control-plane/feedback/…` | `services/trader-feedback/…` |
| `services/telemetry/feedback_adapter.py:18-23` | `sys.path.insert(.../services/control-plane)` plus `from feedback.store import …` | `from services.trader_feedback.store import TraderFeedbackStore, parse_rfc3339`; remove the control-plane path injection |
| new shim package | — | `services/trader_feedback/store.py` and `services/trader_feedback/schema_validation.py` re-export the moved service modules |

### Pair J rewrites

| File | Old import | New import |
|---|---|---|
| `services/learning/trl/activation_smoke.py:36` | `from services.learning.trl.adapter import …` | `from services.research.trl.adapter import …` |
| `services/learning/trl/activation_smoke.py:47` | `from services.learning.trl.preflight import …` | `from services.research.trl.preflight import …` |
| `services/learning/trl/smoke_test.py:21` | `from services.learning.trl.adapter import …` | `from services.research.trl.adapter import …` |
| `services/learning/trl/test_adapter.py:23` | `from services.learning.trl.adapter import …` | `from services.research.trl.adapter import …` |
| `services/learning/trl/test_preflight.py:6` | `from services.learning.trl.preflight import …` | `from services.research.trl.preflight import …` |
| `scripts/smoke_dormant_oss_matrix.py:275` | `services/learning/trl/smoke_test.py` | `services/research/trl/smoke_test.py` |
| `scripts/smoke_dormant_oss_matrix.py:292` | string `services.learning.trl.preflight` | `services.research.trl.preflight` |
| `scripts/run_research_activation_gates.py:119-121` | `services/learning/trl/...` required files | `services/research/trl/...` |
| `services/research-worker-gateway/main.py:101,105` | `services/learning/trl/worker.py`, `services/learning/trl/preflight.py` | `services/research/trl/worker.py`, `services/research/trl/preflight.py` |
| `services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py:136` | `services/learning/trl/worker.py` | `services/research/trl/worker.py` |
| `services/research/main.py:41` | `services/learning/trl/ACTIVATION_CRITERIA.md` | `services/research/trl/ACTIVATION_CRITERIA.md` |
| `services/policy-learning/main.py:39` | `services/learning/trl/ACTIVATION_CRITERIA.md` | `services/research/trl/ACTIVATION_CRITERIA.md` |

---

## 5. Docker-Compose Service Reference Changes

### Pair A

No docker-compose service name changes; `control-plane-persona` and `control-plane-router` service
names in compose already use the kebab form and do not reference `control_plane`.

### Pair B

No docker-compose changes; `registry-core` has no Docker service.

### Pair E

| Compose file | Current | After rename |
|---|---|---|
| `docker-compose.yml` (feedback service build) | `dockerfile: services/control-plane/feedback/Dockerfile` | `dockerfile: services/trader-feedback/Dockerfile` |
| `docker-compose.control.yml` (feedback service build) | `dockerfile: services/feedback/Dockerfile` | **No change** — this still points to the library stub, which may need review |

**Note:** After the rename, `feedback:` in `docker-compose.yml` consistently builds the
trader-feedback ingestion service. `docker-compose.control.yml` may need to be updated to also
point at `services/trader-feedback/Dockerfile` if both files should deploy the same service. The
rename PR must also update `services/control-plane/feedback/Dockerfile` internals after the file is
moved, because its current PYTHONPATH and `--app-dir` still point at the old directory.

### Pair J

No docker-compose service changes for the `learning` → `research` merge; neither `learning/dspy`
nor `learning/imitation` nor `learning/trl` have active Docker service entries.

---

## 6. Risk Table

| Risk | Severity | Affected Pairs | Mitigation |
|---|---|---|---|
| `services/control-plane` is not a valid Python package name (hyphen). Moving files there does not create an importable Python module. | **High** | A | Keep `services/control_plane/` as the real Python namespace and add `services/control_plane/internal/*` loader modules that execute files from `services/control-plane/internal/`. |
| `feedback:` service in `docker-compose.yml` vs `docker-compose.control.yml` builds from different Dockerfiles; a rename could desync if only one compose file is inspected. | **High** | E | Inspect both compose files in the rename PR; either converge both `feedback:` builds on `services/trader-feedback/Dockerfile` or explicitly document why `docker-compose.control.yml` keeps the root stub. Add a CI check if they should match. |
| `services/telemetry/feedback_adapter.py` currently imports `feedback.store` by injecting `services/control-plane` into `sys.path`; moving `control-plane/feedback` would break telemetry event persistence. | **High** | E | Add `services.trader_feedback.store` as an importable shim/package and rewrite the telemetry adapter in the same PR as the move. |
| `services/learning/trl/` tests are run by scripts that import `services.learning.trl.*`; moving without a shim would break CI immediately. | **Medium** | J | Add a compat `services/learning/trl/__init__.py` shim that re-imports from `services.research.trl` during transition. |
| `services/research-worker-gateway` and research/policy services contain literal `services/learning/trl` paths for worker entrypoint, preflight, and activation-gate metadata. | **Medium** | J | Rewrite these literal paths with the TRL move and update the gateway rejection-policy test in the same changeset. |
| `scripts/validate_bg003.py` is likely not in CI; the schema path change might go unnoticed. | **Low** | B | Grep all scripts for `registry-core` before finalizing; add a smoke test for schema validation. |
| `governance:` service in docker-compose imports from `control-plane/governance` via path injection; a rename there would break multiple services simultaneously. | **Medium** | F | Defer Pair F (governance domain library rename) — the risk/benefit ratio is low. |
| Pair J overlap merge may silently drop review packets and activation criteria docs. | **Low** | J | Run `diff` on overlapping files before merge to ensure the richer version is kept; remember that `services/research/trl` is a new target, not an existing implementation. |
| `incident` vs `incidents` naming may confuse new developers who add to the wrong directory. | **Low** | C | Add README files to both directories. |

---

## 7. Roll-Forward Plan (Execution Order)

This task delivers a plan only. Code changes are out of scope for SVC-RENAME-001.

Suggested execution order for follow-up tasks:

1. **SVC-RENAME-002 (P1)**: Pair E — rename `control-plane/feedback` → `trader-feedback`
   - Directory rename, compose/Dockerfile path rewrites, and `services/telemetry/feedback_adapter.py`
     import rewrite
   - Add `services/trader_feedback/` importable shim package before removing the old path

2. **SVC-RENAME-003 (P1)**: Pair A — add `control-plane/internal/` and compat shim for `control_plane`
   - Create `services/control-plane/internal/` with files
   - Keep `services/control_plane/` as the real importable namespace and add
     `services/control_plane/internal/*` loader modules
   - Update imports in runtime-manager and tests

3. **SVC-RENAME-004 (P2)**: Pair B — move `registry-core/decision-domain` → `registry/decision_domain`
   - Single path string edit in `scripts/validate_bg003.py`
   - Move JSON schema files

4. **SVC-RENAME-005 (P2)**: Pair J — merge `learning/` into `research/`
   - File moves + compat shims
   - Import and literal path rewrites across TRL scripts, research-worker-gateway, research service
     metadata, policy-learning metadata, and tests

Each follow-up task should:
- Have no code logic changes — file moves and import path updates only
- Run existing tests before and after to confirm nothing breaks
- Produce a single scoped commit per task

---

## 8. Files That Do NOT Need Changes

The following pairs are correctly role-separated and require only documentation:

- `services/incident/` and `services/incidents/` — singular/plural library-service pattern; add READMEs
- `services/source_ingestion/` and `source-ingest` Docker service — intentional Python/Docker name split
- `services/lineage-read/` and `services/telemetry/lineage_read/` — clean service-wraps-library pattern
- `services/promotion/` and `services/registry/promotion/` — HTTP service vs domain library
- `services/runtime-manager/` and `services/execution/runtime-manager/` — HTTP service vs domain library
- `services/governance/` and `services/control-plane/governance/` — HTTP service vs domain objects (defer rename)
