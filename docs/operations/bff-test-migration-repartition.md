# BFF Test Migration Repartition and Parent Contract Revision Plan

Status: governed planning prerequisite complete (`BFF-TEST-MIGRATION-REPARTITION-PLAN-001`)
Target repo: `ajoe734/pantheon`
Associated PR: [#5714](https://github.com/ajoe734/pantheon/pull/5714) anchor rebaselined
Delivery scope: documentation and machine-readable evidence only; no production or test code changes

---

## 1. Executive Summary and Problem Statement

The overarching goal of the BFF test migration is to decouple test suites across `services/control-plane/bff/` from the composition root (`services/control-plane/bff/main.py`), global monkeypatching (`read_store`, `command_store`), and obsolete `sys.path` surgery, enforcing architectural layers established in `BFF-TEST-ARCH-001`.

The current parent task, `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001`, is held in `blocked` at generation 24 under worker recovery hold (`lost-lease-972da928c370db43096eb771f9c9440effc6c620542077844dec2c1c985fe087`). While anchor commit `1198bef2b4b514705bc1a9d19c406b4b3c2c7880` in PR #5714 reconciled the catalog to 369 files on disk, two critical blockers prevent child task admission:

1. **Broad Wildcard Artifact Grants**: The parent task row still declares four broad wildcards:
   - `services/control-plane/bff/test_*.py`
   - `services/control-plane/bff/smoke_test*.py`
   - `services/control-plane/bff/tests/**`
   - `services/control-plane/bff/*/test*.py`
   Any admitted child task that touches test files in these directories will trigger supervisor artifact collision locks.
2. **Count Inconsistencies in Delivery Evidence**: Evidence and inventory artifacts report contradictory numbers:
   - PR #5714 `acceptance_state`: 192 total main importers, 8 allowlist, 184 outside allowlist, 182 planned.
   - PR #5714 `source_audit`: 184 direct importers, 172 outside allowlist.
   - Catalog (`bff_test_architecture_inventory.json`): 182 `PLANNED` rows, 141 `DECOUPLED` rows, 12 `ALLOWLIST` rows, 34 `MIGRATED` rows.

This prerequisite resolves the count discrepancies, defines an exact-file partition covering all 184 remaining non-allowlisted importers across 18 pairwise-disjoint batches, separates shared foundation files, proves acyclicity and serialization of overlapping writers, and documents the exact Human/Ops canonical contract revision templates.

---

## 2. Rebaselined Source and Inventory Reconciliation

A reproducible scan was executed against the exact reviewed source head (`1198bef2b4b514705bc1a9d19c406b4b3c2c7880`) and verified against `origin/dev` (`9b867623884b5e8f2322beca801e872f2ce92a82`).

### 2.1 Scan Methodology

The scan evaluates all 369 test and support files under `services/control-plane/bff/` across four detection vectors:
- **AST Imports**: `import main`, `import ...main`, `from main import ...`, `from services.control_plane.bff import main`, `from services.control_plane.bff.main import ...`.
- **Dynamic Imports**: `importlib.import_module(...)` or `__import__(...)` referencing `main`.
- **Subprocess Invocations**: `subprocess.run`, `Popen`, `check_output` passing `main.py` in command arguments.
- **Fixture and Conftest Support**: Shared fixtures and doubles in `services/control-plane/bff/tests/fixtures/` and `services/control-plane/bff/tests/`.

### 2.2 Reconciliation of the 192 / 184 / 172 Count Discrepancy

| Metric | Count | Technical Source & Explanation |
|---|---|---|
| **Total Test & Support Files** | **369** | Exact count of `.py` files in `services/control-plane/bff/` matching test patterns or residing in `tests/`. Matches catalog entries exactly. |
| **Composition Allowlist** | **12** | Explicitly permitted architectural suites in `test_bff_test_architecture.py`. 8 import `main` directly; 4 do not import `main`. |
| **Migrated Suites** | **34** | Successfully decoupled suites across router, incident, research, training, and governance domains. 0 import `main`. |
| **Verified Decoupled Suites** | **139** | Suites tagged `DECOUPLED` in the catalog that genuinely do not import `main` (verified via AST). |
| **Mislabeled Decoupled Suites** | **2** | Suites tagged `DECOUPLED` in the catalog that **actually import main**: `tests/test_management_read_models_router.py` and `tests/test_unhandled_error_cors.py`. |
| **Planned Suites** | **182** | Suites tagged `PLANNED` in the catalog that import `main`. |
| **Total Main Importers** | **192** | 8 allowlist importers + 182 planned + 2 mislabeled decoupled = **192 total main importers**. |
| **Remaining Non-Allowlist Importers** | **184** | 182 planned + 2 mislabeled decoupled = **184 suites requiring migration**. |
| **Stale 172 Figure** | **172** | Stale calculation in parent `evidence.json` `source_audit` (`184 direct importers - 12 allowlist = 172`). Derived from an older 365-file scan before the 28 missing files were incorporated. |

**Audit Conclusion**: There are exactly **184** non-allowlisted test suites that import `main` and must be partitioned and decoupled. Claiming `DECOUPLED` status without an AST scan is invalid; the two mislabeled suites are captured in the partition.

---

## 3. Shared Foundation and Fixture Scopes

To ensure child tasks operate on isolated scopes without modifying core test infrastructure or gates, shared foundation files are segregated from child grants.

### 3.1 Retained Parent Foundation Artifacts

The parent task (`BFF-TEST-FULL-MIGRATION-CORRECTIVE-001`) retains authority over the architectural gate and catalog for final closeout verification:
1. `services/control-plane/bff/tests/test_bff_test_architecture.py` — Architectural invariant test gate (enforces import ceilings and rules).
2. `services/control-plane/bff/tests/bff_test_architecture_inventory.json` — Architectural inventory catalog (tracks disposition and layer classification).
3. `docs/deployment/evidence/BFF-TEST-FULL-MIGRATION-CORRECTIVE-001/evidence.json` — Parent delivery evidence manifest.

### 3.2 Shared Test Fixtures (Read-Only / Inherited)

The following fixtures are already decoupled from `main` globals and are inherited read-only by child tasks:
- `services/control-plane/bff/tests/conftest.py`
- `services/control-plane/bff/tests/fixtures/__init__.py`
- `services/control-plane/bff/tests/fixtures/governance_fixture.py`
- `services/control-plane/bff/tests/fixtures/research_fixture.py`
- `services/control-plane/bff/tests/fixtures/training_fixture.py`
- `services/control-plane/bff/tests/knowledge_read_port_fixtures.py`
- `services/control-plane/bff/tests/management_projection_test_doubles.py`
- `services/control-plane/bff/tests/read_store_fixtures.py`

### 3.3 Shared Support Module: `rebalance_authority_test_support.py`

- **Path**: `services/control-plane/bff/tests/rebalance_authority_test_support.py`
- **Current State**: Directly imports `main as bff_main`, mutates `bff_main.read_store` and `bff_main.command_store`, and instantiates `TestClient(bff_main.app)`.
- **Assignment**: Placed within **B04 (`BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001`)** for decoupling.
- **Consumer Batches**: Imported by test suites in B08 (`test_bff_rebalance_proposals.py`, `test_ppl_alloc_012_ranking_projection.py`), B11 (`test_management_nl_assistant_provider.py`), B12 (`test_management_read_budget.py`), and B18 (`test_bff_emergency_containment.py`).
- **Consumer Dependency Rule**: Because B04 is assigned the decoupling of `rebalance_authority_test_support.py`, consumer batches B08, B11, B12, and B18 must declare an explicit task dependency on `BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001` in addition to `BFF-TEST-MIGRATION-REPARTITION-PLAN-001`. This ensures `rebalance_authority_test_support.py` is safely decoupled and stabilized before any consumer test suites execute.

---

## 4. Machine-Readable Exact-File Partition (B01–B18)

The 184 remaining non-allowlisted test files are grouped into 18 domain-oriented batches. Every file path is repository-relative and unique.

### 4.1 Mathematical Partition Properties

- **Total Non-Allowlist Files**: 184
- **Partition Size**: 18 batches
- **Sum of Files Across Batches**: 184
- **Unique Files**: 184
- **Pairwise Disjointness**: For all i != j, intersection of B_i and B_j is empty (Verified: True)
- **Allowlist Disjointness**: Intersection of partition and Allowlist is empty (Verified: True)
- **Migrated Disjointness**: Intersection of partition and Migrated is empty (Verified: True)
- **Wildcard Grants**: None. Every child artifact is an explicit file path.

### 4.2 Batch Summary Table

| Batch | Domain Group | Task ID | File Count | Dependencies After Plan | Unique Evidence Path |
|---|---|---|:---:|---|---|
| **B01** | Agora Core | `BFF-TEST-MIGRATION-B01-AGORA-CORE-001` | 17 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B01-AGORA-CORE-001/evidence.json` |
| **B02** | Ask Assistant & Workshops | `BFF-TEST-MIGRATION-B02-ASK-ASSISTANT-WORKSHOP-001` | 4 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B02-ASK-ASSISTANT-WORKSHOP-001/evidence.json` |
| **B03** | Auth, Session & JWKS | `BFF-TEST-MIGRATION-B03-AUTH-SESSION-JWKS-001` | 6 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B03-AUTH-SESSION-JWKS-001/evidence.json` |
| **B04** | Security, Errors & Idempotency | `BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001` | 10 | `PLAN-001` (owns `rebalance_authority_test_support.py`) | `docs/deployment/evidence/BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001/evidence.json` |
| **B05** | Governance Approvals | `BFF-TEST-MIGRATION-B05-GOVERNANCE-APPROVALS-001` | 7 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B05-GOVERNANCE-APPROVALS-001/evidence.json` |
| **B06** | Governance Audit Committee | `BFF-TEST-MIGRATION-B06-GOVERNANCE-AUDIT-COMMITTEE-001` | 5 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B06-GOVERNANCE-AUDIT-COMMITTEE-001/evidence.json` |
| **B07** | Persona Provisioning | `BFF-TEST-MIGRATION-B07-PERSONA-PROVISIONING-001` | 17 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B07-PERSONA-PROVISIONING-001/evidence.json` |
| **B08** | Strategy Capital & Ranking | `BFF-TEST-MIGRATION-B08-STRATEGY-CAPITAL-RANKING-001` | 13 | `PLAN-001`, `B04` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B08-STRATEGY-CAPITAL-RANKING-001/evidence.json` |
| **B09** | Research & Knowledge | `BFF-TEST-MIGRATION-B09-RESEARCH-KNOWLEDGE-001` | 10 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B09-RESEARCH-KNOWLEDGE-001/evidence.json` |
| **B10** | Evolution Programs | `BFF-TEST-MIGRATION-B10-EVOLUTION-PROGRAMS-001` | 8 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B10-EVOLUTION-PROGRAMS-001/evidence.json` |
| **B11** | Management Assistant & Ops | `BFF-TEST-MIGRATION-B11-MANAGEMENT-ASSISTANT-OPS-001` | 16 | `PLAN-001`, `B04` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B11-MANAGEMENT-ASSISTANT-OPS-001/evidence.json` |
| **B12** | Management Console Read Models | `BFF-TEST-MIGRATION-B12-MANAGEMENT-CONSOLE-READ-MODELS-001` | 9 | `PLAN-001`, `B04` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B12-MANAGEMENT-CONSOLE-READ-MODELS-001/evidence.json` |
| **B13** | Runtime Health & Readiness | `BFF-TEST-MIGRATION-B13-RUNTIME-HEALTH-READINESS-001` | 10 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B13-RUNTIME-HEALTH-READINESS-001/evidence.json` |
| **B14** | Loops & Paper V5 | `BFF-TEST-MIGRATION-B14-LOOPS-PAPER-V5-001` | 11 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B14-LOOPS-PAPER-V5-001/evidence.json` |
| **B15** | Deployment & Hosted | `BFF-TEST-MIGRATION-B15-DEPLOYMENT-HOSTED-001` | 6 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B15-DEPLOYMENT-HOSTED-001/evidence.json` |
| **B16** | Command Write & Workflow | `BFF-TEST-MIGRATION-B16-COMMAND-WRITE-WORKFLOW-001` | 10 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B16-COMMAND-WRITE-WORKFLOW-001/evidence.json` |
| **B17** | Router & SSE Surfaces | `BFF-TEST-MIGRATION-B17-ROUTER-SSE-SURFACES-001` | 11 | `PLAN-001` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B17-ROUTER-SSE-SURFACES-001/evidence.json` |
| **B18** | Cross-Cutting & Consolidation | `BFF-TEST-MIGRATION-B18-CROSS-CUTTING-CONSOLIDATION-001` | 14 | `PLAN-001`, `B04` | `docs/deployment/evidence/BFF-TEST-MIGRATION-B18-CROSS-CUTTING-CONSOLIDATION-001/evidence.json` |
| **Total** | **All 18 Batches** | — | **184** | — | — |

*The complete file-by-file listing for each batch is preserved in `docs/deployment/evidence/BFF-TEST-MIGRATION-REPARTITION-PLAN-001/partition.json`.*

---

## 5. Canonical Parent Contract Revision Sequence (Human/Ops Governed Flow)

Because `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001` is a canonical task, auto-workers cannot mutate its artifact or dependency contracts directly. Human/Ops must execute the following sequential operations after this planning PR merges.

### Step 1: Planning Prerequisite Merge
Merge `task/BFF-TEST-MIGRATION-REPARTITION-PLAN-001` into `dev` following exact-head review by Codex and passing CI.

### Step 2: Parent Artifact Contract Revision (Remove Wildcards, Narrow to Foundation)
Human/Ops runs `scripts/ai-status.sh artifact-contract` to strip all four wildcards and retain only the exact foundation files:

```bash
AI_NAME=Human/Ops "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" artifact-contract   BFF-TEST-FULL-MIGRATION-CORRECTIVE-001 remove "services/control-plane/bff/test_*.py"   "Remove overbroad wildcard in favor of exact 18-batch repartition"

AI_NAME=Human/Ops "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" artifact-contract   BFF-TEST-FULL-MIGRATION-CORRECTIVE-001 remove "services/control-plane/bff/smoke_test*.py"   "Remove overbroad wildcard in favor of exact 18-batch repartition"

AI_NAME=Human/Ops "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" artifact-contract   BFF-TEST-FULL-MIGRATION-CORRECTIVE-001 remove "services/control-plane/bff/tests/**"   "Remove overbroad wildcard in favor of exact 18-batch repartition"

AI_NAME=Human/Ops "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" artifact-contract   BFF-TEST-FULL-MIGRATION-CORRECTIVE-001 remove "services/control-plane/bff/*/test*.py"   "Remove overbroad wildcard in favor of exact 18-batch repartition"

AI_NAME=Human/Ops "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" artifact-contract   BFF-TEST-FULL-MIGRATION-CORRECTIVE-001 add "services/control-plane/bff/tests/bff_test_architecture_inventory.json"   "Retain architectural catalog for final gate validation and closure"

AI_NAME=Human/Ops "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" artifact-contract   BFF-TEST-FULL-MIGRATION-CORRECTIVE-001 add "services/control-plane/bff/tests/test_bff_test_architecture.py"   "Retain architectural gate test for final gate validation and closure"
```

*Resulting parent artifacts*:
- `services/control-plane/bff/tests/bff_test_architecture_inventory.json`
- `services/control-plane/bff/tests/test_bff_test_architecture.py`
- `docs/deployment/evidence/BFF-TEST-FULL-MIGRATION-CORRECTIVE-001/evidence.json`

### Step 3: Materialize Children via DevTaskPackets

`DevTaskPacket.MAX_TASKS_PER_PACKET = 16`. 18 tasks require two signed packets dispatched to `.orchestrator/assistant-dev-packets/`:
- **Packet 1 (16 tasks)**: `pkt-bff-test-migration-children-part1-20260910` containing `BFF-TEST-MIGRATION-B01-AGORA-CORE-001` through `BFF-TEST-MIGRATION-B16-COMMAND-WRITE-WORKFLOW-001`.
- **Packet 2 (2 tasks)**: `pkt-bff-test-migration-children-part2-20260910` containing `BFF-TEST-MIGRATION-B17-ROUTER-SSE-SURFACES-001` and `BFF-TEST-MIGRATION-B18-CROSS-CUTTING-CONSOLIDATION-001`.

Each child declares its exact source files, a unique evidence path, and:
- All child tasks declare `depends_on: ["BFF-TEST-MIGRATION-REPARTITION-PLAN-001"]`.
- Consumer batches B08, B11, B12, and B18 additionally declare `"BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001"` because they import shared `rebalance_authority_test_support.py` decoupled by B04.

### Step 4: Parent Dependency Contract Revision (Fresh CAS Digest Requirement)

With the children admitted, Human/Ops updates the parent dependency contract to depend on the planning prerequisite task and all 18 children.

> [!IMPORTANT]
> **Fresh Complete-Row CAS Requirement**:
> The parent task row CAS digest `692d7238829f39698b87b54cc8a503c982d8a004b0412a7a07fe79fbd85067f2` is the **pre-revision baseline observation** at generation 24 prior to planning execution.
>
> It is **not executable directly at Step 4** because Step 2 (`artifact-contract`) mutates the parent task row across six sequential remove/add operations, altering `artifacts`, `generation`, and `last_update`. Submitting the baseline `692d` digest after Step 2 will fail canonical CAS verification (`dependency-contract expected-state CAS failed`).
>
> Therefore, the payload below is a **fresh-CAS request template**. Immediately prior to submitting the dependency contract, Human/Ops must compute the fresh complete-row CAS digest from canonical state after Step 2 artifact narrowing and Step 3 child admission are complete.

- **Pre-Revision Baseline CAS Digest**: `692d7238829f39698b87b54cc8a503c982d8a004b0412a7a07fe79fbd85067f2` (recorded for audit traceability; non-executable at Step 4)
- **Request Template** (`/tmp/parent-dependency-contract-request.json`):

```json
{
  "reason": "Operator-authorized parent dependency update: serialize test migration parent after prerequisite planning task BFF-TEST-MIGRATION-REPARTITION-PLAN-001 and all 18 decoupled child batches (B01-B18), preserving existing dependencies (BFF-TEST-ARCH-001, JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001, BFF-AUTH-SESSION-SEAM-PREREQUISITE-001) and blocked hold semantics.",
  "tasks": [
    {
      "task_id": "BFF-TEST-FULL-MIGRATION-CORRECTIVE-001",
      "expected_sha256": "<FRESH_PARENT_ROW_CAS_DIGEST_POST_ARTIFACT_NARROWING_AND_CHILD_ADMISSION>",
      "depends_on": [
        "BFF-TEST-ARCH-001",
        "JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001",
        "BFF-AUTH-SESSION-SEAM-PREREQUISITE-001",
        "BFF-TEST-MIGRATION-REPARTITION-PLAN-001",
        "BFF-TEST-MIGRATION-B01-AGORA-CORE-001",
        "BFF-TEST-MIGRATION-B02-ASK-ASSISTANT-WORKSHOP-001",
        "BFF-TEST-MIGRATION-B03-AUTH-SESSION-JWKS-001",
        "BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001",
        "BFF-TEST-MIGRATION-B05-GOVERNANCE-APPROVALS-001",
        "BFF-TEST-MIGRATION-B06-GOVERNANCE-AUDIT-COMMITTEE-001",
        "BFF-TEST-MIGRATION-B07-PERSONA-PROVISIONING-001",
        "BFF-TEST-MIGRATION-B08-STRATEGY-CAPITAL-RANKING-001",
        "BFF-TEST-MIGRATION-B09-RESEARCH-KNOWLEDGE-001",
        "BFF-TEST-MIGRATION-B10-EVOLUTION-PROGRAMS-001",
        "BFF-TEST-MIGRATION-B11-MANAGEMENT-ASSISTANT-OPS-001",
        "BFF-TEST-MIGRATION-B12-MANAGEMENT-CONSOLE-READ-MODELS-001",
        "BFF-TEST-MIGRATION-B13-RUNTIME-HEALTH-READINESS-001",
        "BFF-TEST-MIGRATION-B14-LOOPS-PAPER-V5-001",
        "BFF-TEST-MIGRATION-B15-DEPLOYMENT-HOSTED-001",
        "BFF-TEST-MIGRATION-B16-COMMAND-WRITE-WORKFLOW-001",
        "BFF-TEST-MIGRATION-B17-ROUTER-SSE-SURFACES-001",
        "BFF-TEST-MIGRATION-B18-CROSS-CUTTING-CONSOLIDATION-001"
      ]
    }
  ]
}
```

Human/Ops generation and execution commands:
```bash
# 1. Compute fresh CAS digest of parent task row from canonical state and write request file
python3 -c "
import json, os, hashlib
status_root = os.environ.get('PANTHEON_STATUS_ROOT', '.')
state = json.load(open(os.path.join(status_root, 'ai-status.json')))
parent = next(t for t in state['tasks'] if t['id'] == 'BFF-TEST-FULL-MIGRATION-CORRECTIVE-001')
fresh_sha = hashlib.sha256(json.dumps(parent, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()
req = {
    'reason': 'Operator-authorized parent dependency update: serialize test migration parent after prerequisite planning task BFF-TEST-MIGRATION-REPARTITION-PLAN-001 and all 18 decoupled child batches (B01-B18), preserving existing dependencies (BFF-TEST-ARCH-001, JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001, BFF-AUTH-SESSION-SEAM-PREREQUISITE-001) and blocked hold semantics.',
    'tasks': [{
        'task_id': 'BFF-TEST-FULL-MIGRATION-CORRECTIVE-001',
        'expected_sha256': fresh_sha,
        'depends_on': [
            'BFF-TEST-ARCH-001',
            'JOURNAL-CONSUMER-ISOLATION-CORRECTIVE-001',
            'BFF-AUTH-SESSION-SEAM-PREREQUISITE-001',
            'BFF-TEST-MIGRATION-REPARTITION-PLAN-001',
            'BFF-TEST-MIGRATION-B01-AGORA-CORE-001',
            'BFF-TEST-MIGRATION-B02-ASK-ASSISTANT-WORKSHOP-001',
            'BFF-TEST-MIGRATION-B03-AUTH-SESSION-JWKS-001',
            'BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001',
            'BFF-TEST-MIGRATION-B05-GOVERNANCE-APPROVALS-001',
            'BFF-TEST-MIGRATION-B06-GOVERNANCE-AUDIT-COMMITTEE-001',
            'BFF-TEST-MIGRATION-B07-PERSONA-PROVISIONING-001',
            'BFF-TEST-MIGRATION-B08-STRATEGY-CAPITAL-RANKING-001',
            'BFF-TEST-MIGRATION-B09-RESEARCH-KNOWLEDGE-001',
            'BFF-TEST-MIGRATION-B10-EVOLUTION-PROGRAMS-001',
            'BFF-TEST-MIGRATION-B11-MANAGEMENT-ASSISTANT-OPS-001',
            'BFF-TEST-MIGRATION-B12-MANAGEMENT-CONSOLE-READ-MODELS-001',
            'BFF-TEST-MIGRATION-B13-RUNTIME-HEALTH-READINESS-001',
            'BFF-TEST-MIGRATION-B14-LOOPS-PAPER-V5-001',
            'BFF-TEST-MIGRATION-B15-DEPLOYMENT-HOSTED-001',
            'BFF-TEST-MIGRATION-B16-COMMAND-WRITE-WORKFLOW-001',
            'BFF-TEST-MIGRATION-B17-ROUTER-SSE-SURFACES-001',
            'BFF-TEST-MIGRATION-B18-CROSS-CUTTING-CONSOLIDATION-001'
        ]
    }]
}
with open('/tmp/parent-dependency-contract-request.json', 'w') as f:
    json.dump(req, f, indent=2)
print('Generated /tmp/parent-dependency-contract-request.json with fresh parent row CAS digest:', fresh_sha)
"

# 2. Execute canonical dependency contract update
AI_NAME=Human/Ops "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" dependency-contract /tmp/parent-dependency-contract-request.json
```

### Step 5: Child Execution
Child tasks execute across auto-workers in parallel or bounded foreground batches. Each child:
- Refactors its assigned suites to remove `main` imports, `read_store` monkeypatching, and `sys.path` manipulation.
- Uses domain-level service fixtures or `ports/read_surface_ports.py` mock doubles.
- Verifies tests pass locally and submits exact-head review evidence to its assigned directory.

### Step 6: Parent Reopen and Final Closure
Once all 18 children are merged into `dev`:
1. Parent task (`BFF-TEST-FULL-MIGRATION-CORRECTIVE-001`) unblocks and reopens.
2. Updates `bff_test_architecture_inventory.json` marking all 184 suites as `DECOUPLED` / `MIGRATED`, reducing `current_main_importers` to 8 (allowlist suites).
3. Verifies `test_bff_test_architecture.py` passes with zero violations across all suites.
4. Generates final parent evidence in `docs/deployment/evidence/BFF-TEST-FULL-MIGRATION-CORRECTIVE-001/evidence.json` and closes out via governed `done`.

---

## 6. Dependency Graph and Overlapping Writer Serialization Proof

### 6.1 Acyclicity Verification

A full topological sort of the proposed prospective canonical state was performed across all 65 nodes (47 existing canonical tasks + 18 prospective children):
- Total graph nodes: 65.
- Visited nodes in topological order: 65 / 65.
- Cycles detected: **0**. The prospective graph is strictly acyclic.
- **B04 Precedence**: B04 (`BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001`, index 11) strictly precedes its consumer batches B08 (index 26), B11 (index 27), B12 (index 28), and B18 (index 29), ensuring `rebalance_authority_test_support.py` is decoupled before consumers execute.
- **Parent Precedence**: All 18 child batches (indices 8-29) strictly precede the parent task (`BFF-TEST-FULL-MIGRATION-CORRECTIVE-001`, index 32).

### 6.2 Serialization of Overlapping BFF Writers

Multiple existing canonical tasks touch `services/control-plane/bff/`. Topological sort proves every overlapping writer is serialized **strictly downstream** of `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001` in the dependency graph:

1. `BFF-TEST-MIGRATION-REPARTITION-PLAN-001` (Index 0 in topological sort)
2. Foundation Child B04: `BFF-TEST-MIGRATION-B04-SECURITY-ERROR-IDEMPOTENCY-001` (Index 11)
3. Independent Child Batches: B01-B03, B05-B07, B09-B10, B13-B17 (Indices 8-10, 12-21)
4. Dependent Child Batches: B08, B11, B12, B18 (Indices 26-29, serialized after B04)
5. Parent Task: `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001` (Index 32 in topological sort, serialized after all children)
6. `JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001` (Index 37 in topological sort)
7. `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001` (Index 39 in topological sort)
8. `BFF-READ-OWNER-WIRING-CORRECTIVE-001` (Index 40 in topological sort)
9. `STRUCT-RETIRE-001` (Index 45 in topological sort)
10. `SIMPLIFY-BFF-RESIDUAL-001` (Index 48 in topological sort)
11. `OSS-CORE-BASELINE-001` (Index 52 in topological sort)

Because the parent task depends on all 18 children, and every overlapping task is transitively dependent on the parent, no concurrent overlapping write can occur during child execution.

---

## 7. Verification and Audit Commands

Operators may verify the partition and graph properties at any time:

```bash
# 1. Validate partition exact cover and disjointness
python3 -c "
import json
d = json.load(open('docs/deployment/evidence/BFF-TEST-MIGRATION-REPARTITION-PLAN-001/partition.json'))
files = [f for c in d['candidate_children'] for f in c['source_artifacts']]
assert len(files) == 184, f'Expected 184, got {len(files)}'
assert len(set(files)) == 184, 'Duplicate file in partition'
print('Partition validation passed: 184 files, pairwise disjoint, exact cover.')
"

# 2. Check commit scope and trailers
python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD --skip-merge
git diff --check
```
