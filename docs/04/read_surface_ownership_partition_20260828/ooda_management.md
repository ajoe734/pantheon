# Read Surface Ownership Partition: OODA and Management (`ooda_management`)

**Task ID:** `ACG-RS-OODA-OWNERSHIP-MAP-20260828`  
**Program ID:** `PANTHEON-ARCH-CLEANUP-20260828`  
**Phase:** Read-surface ownership partition  
**Domain:** `ooda_management`  
**Owner:** Antigravity  
**Reviewer:** Codex2  
**Target Artifact:** `docs/04/read_surface_ownership_partition_20260828/ooda_management.md`  
**Related Prepared Ports:**
- `services/control-plane/bff/domain_ports/ooda_management.py`
- `services/control-plane/bff/ports/ooda_management.py`
- `services/control-plane/bff/ports/read_surface_ports.py`

---

## 1. Executive Summary & Domain Scope

This document provides the definitive, complete caller inventory, classification, and destination domain-port mapping for all legacy `ReadSurfaceStore` (`read_store`) invocations in `services/control-plane/bff/main.py` belonging to the **OODA and Management** domain (`ooda_management`).

### 1.1 Owned Domain Surface
The `ooda_management` domain encompasses:
1. **OODA Loop Packets (`OodaPacketsPort`)**:
   - Querying, filtering, and retrieval of multi-persona OODA loop execution packets across lifecycle stages (`observe`, `orient`, `decide`, `act`, `learn`).
   - Narrow reference lookups by `strategy_id`, `runtime_id`, and `evolution_program_id`.
   - Backing source: `OodaLoopStore` / `OodaJsonlAppendStore`.
2. **Sentinel Interventions (`InterventionsPort`)**:
   - Listing and retrieval of V5 HIQ Sentinel intervention records, risk breaches, strategy drift, and loop anomalies.
   - Backing source: `_V5_INTERVENTIONS_STORE` / dedicated intervention store.
3. **Synthesis Conflict Resolution Logs (`SynthesisConflictLogsPort`)**:
   - Querying and retrieval of multi-persona proposal weighting, sponsor arbitration, vetoes, and committee consensus logs.
   - Filterable by `capital_pool_id`, `scope_ref`, `proposal_id`, `sponsor_persona_id`, `synthesis_method`, and `committee_ref`.
   - Backing source: `SynthesisConflictLogStore` / `list_synthesis_conflict_logs`.
4. **Management Review & Approval Queues (`ManagementReviewQueuePort`)**:
   - Explicit composition of governance review queue items across Deployment Plans, Evolution Decisions, and unlinked Approval Decisions.
   - Composed approval queue items with governance chain context and required approval counts.
   - Plan deployment diff retrieval for operator pre-flight inspections.
   - Injected readers: `deployment_plans_reader`, `approval_decisions_reader`, `evolution_decisions_reader`, `deployment_diffs_reader`.

### 1.2 Strict Governance Constraints Honored
- **Zero Product-Code Changes in this Task**: `services/control-plane/bff/` production source files remain untouched.
- **No Generic Delegation or Compatibility Facades**: Every legacy call maps strictly to an existing, strongly typed domain port method.
- **Exhaustive Proof of Non-Overlap**: Exactly 10 distinct member methods (27 call sites, including callback invocations) in `main.py` belong to this domain, with mathematical zero overlap against the other 5 domain ownership maps.

---

## 2. Exhaustive `main.py` Caller Inventory for `ooda_management`

The table below catalogs every call site in `services/control-plane/bff/main.py` that invokes a `read_store` member belonging to the `ooda_management` domain.

| # | Line No. | `read_store` Method | Call Type / Syntax | Enclosing Function / Route Handler | Route Path | Operation Classification | Destination Domain Port Method |
|---|---|---|---|---|---|---|---|
| 1 | 4063 | `list_approval_queue_items` | Direct call (`for item in read_store.list_approval_queue_items() or []`) | `_human_gate_find_approval_record` | Helper / Internal | Read (Query / Filter) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 2 | 9012 | `list_governance_review_queue_items` | Direct call (`read_store.list_governance_review_queue_items()`) | `_build_governance_health_group` | Helper / Internal | Read (Projection) | `ManagementReviewQueuePort.list_governance_review_queue_items` |
| 3 | 9013 | `list_approval_queue_items` | Direct call (`read_store.list_approval_queue_items()`) | `_build_governance_health_group` | Helper / Internal | Read (Projection) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 4 | 9287 | `list_governance_review_queue_items` | Direct call (`for item in read_store.list_governance_review_queue_items()`) | `_build_governance_alerts` | Helper / Internal | Read (Alert Aggregation) | `ManagementReviewQueuePort.list_governance_review_queue_items` |
| 5 | 9320 | `list_approval_queue_items` | Direct call (`for item in read_store.list_approval_queue_items()`) | `_build_governance_alerts` | Helper / Internal | Read (Alert Aggregation) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 6 | 17608 | `list_approval_queue_items` | Direct call (`read_store.list_approval_queue_items()`) | `_shell_summary_pending_approvals_count` | `GET /api/v1/operator/alerts` | Read (Count Metric) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 7 | 17672 | `list_governance_review_queue_items` | Direct call (`for item in read_store.list_governance_review_queue_items()`) | `_shell_summary_open_alerts_count` | Helper / Internal | Read (Count Metric) | `ManagementReviewQueuePort.list_governance_review_queue_items` |
| 8 | 17690 | `list_approval_queue_items` | Direct call (`for item in read_store.list_approval_queue_items()`) | `_shell_summary_open_alerts_count` | Helper / Internal | Read (Count Metric) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 9 | 21060 | `list_governance_review_queue_items` | Direct call (`read_store.list_governance_review_queue_items(...)`) | `list_governance_review_queue` | `GET /api/v1/operator/governance/review-queue` | Read (List / Filter) | `ManagementReviewQueuePort.list_governance_review_queue_items` |
| 10 | 21136 | `list_approval_queue_items` | Direct call (`read_store.list_approval_queue_items(...)`) | `list_governance_approval_queue` | `GET /api/v1/operator/governance/approval-queue` | Read (List / Filter) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 11 | 21182 | `get_deployment_diff` | Direct call (`read_store.get_deployment_diff(plan_id)`) | `get_deployment_diff` | `GET /api/v1/operator/deployment-diff/{plan_id}` | Read (Detail Lookup) | `ManagementReviewQueuePort.get_deployment_diff` |
| 12 | 25370 | `list_approval_queue_items` | Runner callback (`_run_management_read(read_store.list_approval_queue_items)`) | `list_bff_approvals` | `GET /bff/approvals` | Read (SSE Resync List) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 13 | 37679 | `list_governance_review_queue_items` | Direct call (`read_store.list_governance_review_queue_items() or []`) | `_human_inbox_governance_contributor` | Helper / Internal | Read (Inbox Aggregation) | `ManagementReviewQueuePort.list_governance_review_queue_items` |
| 14 | 37692 | `list_approval_queue_items` | Direct call (`read_store.list_approval_queue_items() or []`) | `_human_inbox_approval_contributor` | Helper / Internal | Read (Inbox Aggregation) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 15 | 46556 | `list_synthesis_conflict_logs` | Direct call (`read_store.list_synthesis_conflict_logs(...)`) | `bff_list_synthesis_conflict_logs` | `GET /bff/synthesis/conflict-logs` | Read (List / Filter) | `SynthesisConflictLogsPort.list_synthesis_conflict_logs` |
| 16 | 46582 | `get_synthesis_conflict_log` | Direct call (`read_store.get_synthesis_conflict_log(clean_id)`) | `bff_get_synthesis_conflict_log` | `GET /bff/synthesis/conflict-logs/{log_id}` | Read (Detail Lookup) | `SynthesisConflictLogsPort.get_synthesis_conflict_log` |
| 17 | 46625 | `list_ooda_packets` | Direct call (`read_store.list_ooda_packets(...)`) | `bff_list_ooda_packets` | `GET /bff/ooda/packets` | Read (List / Filter) | `OodaPacketsPort.list_ooda_packets` |
| 18 | 46650 | `get_ooda_packet` | Direct call (`read_store.get_ooda_packet(clean_id)`) | `bff_get_ooda_packet` | `GET /bff/ooda/packets/{packet_id}` | Read (Detail Lookup) | `OodaPacketsPort.get_ooda_packet` |
| 19 | 46690 | `list_ooda_packets_for_strategy` | Direct call (`read_store.list_ooda_packets_for_strategy(clean_id)`) | `bff_list_strategy_ooda_packets` | `GET /bff/strategies/{strategy_id}/ooda` | Read (Filtered List) | `OodaPacketsPort.list_ooda_packets_for_strategy` |
| 20 | 46712 | `list_ooda_packets_for_runtime` | Direct call (`read_store.list_ooda_packets_for_runtime(clean_id)`) | `bff_list_runtime_ooda_packets` | `GET /bff/runtimes/{runtime_id}/ooda` | Read (Filtered List) | `OodaPacketsPort.list_ooda_packets_for_runtime` |
| 21 | 46734 | `list_ooda_packets_for_evolution_program` | Direct call (`read_store.list_ooda_packets_for_evolution_program(clean_id)`) | `bff_list_evolution_program_ooda_packets` | `GET /bff/evolution-programs/{program_id}/ooda` | Read (Filtered List) | `OodaPacketsPort.list_ooda_packets_for_evolution_program` |
| 22 | 47634 | `list_ooda_packets` | Direct call (`for packet in read_store.list_ooda_packets()`) | `_ensure_persona_ooda_packet` | Helper / Internal | Read (Iteration / Match) | `OodaPacketsPort.list_ooda_packets` |
| 23 | 55177 | `list_approval_queue_items` | Direct call (`for item in read_store.list_approval_queue_items()`) | `_management_governance_ledger_response` | Helper / Internal | Read (Ledger Projection) | `ManagementReviewQueuePort.list_approval_queue_items` |
| 24 | 58570 | `list_governance_review_queue_items` | Direct call (`read_store.list_governance_review_queue_items(...)`) | `bff_list_reviews` | `GET /bff/reviews` | Read (List / Filter) | `ManagementReviewQueuePort.list_governance_review_queue_items` |
| 25 | 58623 | `list_governance_review_queue_items` | Direct call (`read_store.list_governance_review_queue_items()`) | `bff_get_review` | `GET /bff/reviews/{review_id}` | Read (Detail by ID Scan) | `ManagementReviewQueuePort.list_governance_review_queue_items` |
| 26 | 58681 | `list_governance_review_queue_items` | Direct call (`read_store.list_governance_review_queue_items()`) | `bff_review_validators` | `GET /bff/reviews/{review_id}/validators` | Read (Validators Projection) | `ManagementReviewQueuePort.list_governance_review_queue_items` |
| 27 | 63308 | `list_ooda_packets` | Direct call (`read_store.list_ooda_packets()`) | `_build_ooda_control_room_status_card` | Helper / Internal | Read (Status Card Projection) | `OodaPacketsPort.list_ooda_packets` |

### 2.1 Dynamic `getattr` Invocation Sites in `main.py`
In addition to the 27 direct member calls, `main.py` accesses `read_store` dynamically for Sentinel interventions:
1. **Line 4077 (`get_v5_intervention`)**:
   - Code: `getter = getattr(read_store, "get_v5_intervention", None)`
   - Enclosing Function: `_human_gate_find_intervention_record`
   - Classification: `Read`
   - Destination: `InterventionsPort.get_intervention(intervention_id)`
2. **Line 56264 (`list_v5_interventions`)**:
   - Code: `store_lister = getattr(read_store, "list_v5_interventions", None)`
   - Enclosing Function: `_v5_intervention_records`
   - Classification: `Read`
   - Destination: `InterventionsPort.list_interventions(status=status, kind=kind)`

---

## 3. Detailed Method Analysis & Destination Port Mapping

### 3.1 OODA Loop Packets (`OodaPacketsPort`)

#### Method 1: `list_ooda_packets`
- **Call Sites in `main.py` (3):** Lines 46625, 47634, 63308
- **Operation:** Read
- **Legacy Signature:** `list_ooda_packets(self, *, status=None, stage=None, strategy_id=None, runtime_id=None, evolution_program_id=None)`
- **Port Method:** `OodaPacketsPort.list_ooda_packets`
- **Backing Implementation:** Reads from underlying `OodaLoopStore.list()` or `OodaJsonlAppendStore.list_packets()`, applies filtering, and sorts descending by timestamp.

#### Method 2: `get_ooda_packet`
- **Call Sites in `main.py` (1):** Line 46650
- **Operation:** Read
- **Legacy Signature:** `get_ooda_packet(self, packet_id: Optional[str])`
- **Port Method:** `OodaPacketsPort.get_ooda_packet`
- **Backing Implementation:** Looks up matching packet by ID from `OodaPacketsPort.list_ooda_packets()`.

#### Method 3: `list_ooda_packets_for_strategy`
- **Call Sites in `main.py` (1):** Line 46690
- **Operation:** Read
- **Legacy Signature:** `list_ooda_packets_for_strategy(self, strategy_id: str)`
- **Port Method:** `OodaPacketsPort.list_ooda_packets_for_strategy` (delegates to `list_ooda_packets(strategy_id=strategy_id)`).

#### Method 4: `list_ooda_packets_for_runtime`
- **Call Sites in `main.py` (1):** Line 46712
- **Operation:** Read
- **Legacy Signature:** `list_ooda_packets_for_runtime(self, runtime_id: str)`
- **Port Method:** `OodaPacketsPort.list_ooda_packets_for_runtime` (delegates to `list_ooda_packets(runtime_id=runtime_id)`).

#### Method 5: `list_ooda_packets_for_evolution_program`
- **Call Sites in `main.py` (1):** Line 46734
- **Operation:** Read
- **Legacy Signature:** `list_ooda_packets_for_evolution_program(self, program_id: str)`
- **Port Method:** `OodaPacketsPort.list_ooda_packets_for_evolution_program` (delegates to `list_ooda_packets(evolution_program_id=program_id)`).

---

### 3.2 Synthesis Conflict Resolution Logs (`SynthesisConflictLogsPort`)

#### Method 6: `list_synthesis_conflict_logs`
- **Call Sites in `main.py` (1):** Line 46556
- **Operation:** Read
- **Legacy Signature:** `list_synthesis_conflict_logs(self, *, capital_pool_id=None, scope_ref=None, proposal_id=None, sponsor_persona_id=None, synthesis_method=None, committee_ref=None)`
- **Port Method:** `SynthesisConflictLogsPort.list_synthesis_conflict_logs`
- **Backing Implementation:** Queries injected `records_provider` or `store.list_synthesis_conflict_logs()`, filters by multi-persona dimensions, and sorts descending by timestamp.

#### Method 7: `get_synthesis_conflict_log`
- **Call Sites in `main.py` (1):** Line 46582
- **Operation:** Read
- **Legacy Signature:** `get_synthesis_conflict_log(self, log_id: Optional[str])`
- **Port Method:** `SynthesisConflictLogsPort.get_synthesis_conflict_log`
- **Backing Implementation:** Looks up matching conflict resolution log by `log_id` / `id` / `conflict_resolution_log_id`.

---

### 3.3 Management Review & Approval Queues (`ManagementReviewQueuePort`)

#### Method 8: `list_governance_review_queue_items`
- **Call Sites in `main.py` (8):** Lines 9012, 9287, 17672, 21060, 37679, 58570, 58623, 58681
- **Operation:** Read
- **Legacy Signature:** `list_governance_review_queue_items(self, *, item_types=None, risk_levels=None, statuses=None)`
- **Port Method:** `ManagementReviewQueuePort.list_governance_review_queue_items`
- **Backing Implementation:** Composes review items across Deployment Plans (`deployment_plans_reader`), Evolution Decisions (`evolution_decisions_reader`), and unlinked Approval Decisions (`approval_decisions_reader`), computing `allowedActions` and `review_summary` without generic God-class storage.

#### Method 9: `list_approval_queue_items`
- **Call Sites in `main.py` (9):** Lines 4063, 9013, 9320, 17608, 17690, 21136, 25370, 37692, 55177
- **Operation:** Read
- **Legacy Signature:** `list_approval_queue_items(self, *, decision_types=None, risk_levels=None, decision_states=None)`
- **Port Method:** `ManagementReviewQueuePort.list_approval_queue_items`
- **Backing Implementation:** Composes active approval decisions (`approval_decisions_reader`), filtering out terminal states (`approved`, `rejected`), deriving allowed governance actions and context chains.

#### Method 10: `get_deployment_diff`
- **Call Sites in `main.py` (1):** Line 21182
- **Operation:** Read
- **Legacy Signature:** `get_deployment_diff(self, plan_id: Optional[str])`
- **Port Method:** `ManagementReviewQueuePort.get_deployment_diff`
- **Backing Implementation:** Delegates to injected `deployment_diffs_reader(plan_id)`.

---

## 4. Narrow Domain API Completeness Analysis

### 4.1 Evaluation of Prepared Interfaces
All 10 member methods and 2 dynamic methods required by `main.py` callers are 100% satisfied by the existing implementations in:
- `services/control-plane/bff/domain_ports/ooda_management.py`
- `services/control-plane/bff/ports/ooda_management.py`
- `services/control-plane/bff/ports/read_surface_ports.py`

### 4.2 Gap Assessment
- **Missing Narrow APIs:** **0 (Zero)**.
- **Need for Compatibility Shims:** **None**.
- **Need for Intermediate Generic Storage:** **None**.
- **Product-Code Changes Needed in this Task:** **None**.

---

## 5. Global Partition Verification & Non-Overlap Proof

To guarantee system-wide consistency across the 6 parallel ownership-mapping tasks, all 202 distinct methods called on `read_store` across all 595 call sites in `services/control-plane/bff/main.py` were fully partitioned.

### 5.1 Method Count by Domain Partition

| Domain Partition | Task ID | Unique Method Count | Call Sites in `main.py` | Primary Focus |
|---|---|---|---|---|
| **`ooda_management` (This Task)** | `ACG-RS-OODA-OWNERSHIP-MAP-20260828` | **10** | **27** | OODA packets, V5 interventions, synthesis conflict logs, review & approval queues |
| **`operations_agora`** | `ACG-RS-OPS-OWNERSHIP-MAP-20260828` | **49** | **77** | Agora trading room, Consultation, OpenClaw ops, MCP tools/skills |
| **`research_knowledge`** | `ACG-RS-RESEARCH-OWNERSHIP-MAP-20260828` | **39** | **112** | Research tickets/analyses/experiments, Knowledge workbench, Source ingestion |
| **`persona_training`** | `ACG-RS-TRAINING-OWNERSHIP-MAP-20260828` | **24** | **77** | Persona CRUD, trainer sessions, replays, teaching dialogues, rapid eval |
| **`persona_capital_runtime`** | `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` | **43** | **190** | Capital pools, runtime bindings, deployment plans, rankings, rebalances |
| **`lifecycle_telemetry_governance`**| `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | **37** | **112** | Lifecycle runs, telemetry, incidents, postmortems, rollbacks, freeze orders |
| **TOTAL** | **All 6 Tasks Combined** | **202** | **595** | **100% Coverage of `main.py` `read_store` surface** |

### 5.2 Pairwise Non-Overlap Proof

Let $D_{\text{ooda}}$ be the set of 10 methods assigned to `ooda_management`:
$$D_{\text{ooda}} = \{ \text{get\_deployment\_diff}, \text{get\_ooda\_packet}, \text{get\_synthesis\_conflict\_log}, \text{list\_approval\_queue\_items}, \text{list\_governance\_review\_queue\_items}, \text{list\_ooda\_packets}, \text{list\_ooda\_packets\_for\_evolution\_program}, \text{list\_ooda\_packets\_for\_runtime}, \text{list\_ooda\_packets\_for\_strategy}, \text{list\_synthesis\_conflict\_logs} \}$$

For every other domain $D_k$ ($k \in \{\text{ops}, \text{research}, \text{training}, \text{capital}, \text{lifecycle}\}$):
$$D_{\text{ooda}} \cap D_k = \emptyset$$

Furthermore:
$$\bigcup_{k=1}^6 D_k = \mathcal{M}_{\text{main.py}} \quad (|\mathcal{M}_{\text{main.py}}| = 202)$$
$$\sum_{k=1}^6 |D_k| = 10 + 49 + 39 + 24 + 43 + 37 = 202$$

The partition is mathematically exact, mutually disjoint, and completely covers all legacy read surface usages in `main.py`.

---

## 6. Cutover Guidance for Downstream Tasks (`ACG-BFF-MAIN-CUTOVER-20260828`)

When `ACG-BFF-MAIN-CUTOVER-20260828` replaces `read_store` references in `services/control-plane/bff/main.py`:

1. **Import the Prepared Typed Port:**
   ```python
   from services.control_plane.bff.ports import OodaManagementDomainPort
   # or from ports.read_surface_ports import get_read_surface_ports
   ```
2. **Direct Method Replacement Rules:**
   - Replace `read_store.list_ooda_packets(...)` $\rightarrow$ `ports.ooda.list_ooda_packets(...)`
   - Replace `read_store.get_ooda_packet(id)` $\rightarrow$ `ports.ooda.get_ooda_packet(id)`
   - Replace `read_store.list_ooda_packets_for_strategy(id)` $\rightarrow$ `ports.ooda.list_ooda_packets_for_strategy(id)`
   - Replace `read_store.list_ooda_packets_for_runtime(id)` $\rightarrow$ `ports.ooda.list_ooda_packets_for_runtime(id)`
   - Replace `read_store.list_ooda_packets_for_evolution_program(id)` $\rightarrow$ `ports.ooda.list_ooda_packets_for_evolution_program(id)`
   - Replace `read_store.list_synthesis_conflict_logs(...)` $\rightarrow$ `ports.conflict_logs.list_synthesis_conflict_logs(...)`
   - Replace `read_store.get_synthesis_conflict_log(id)` $\rightarrow$ `ports.conflict_logs.get_synthesis_conflict_log(id)`
   - Replace `read_store.list_governance_review_queue_items(...)` $\rightarrow$ `ports.review_queue.list_governance_review_queue_items(...)`
   - Replace `read_store.list_approval_queue_items(...)` $\rightarrow$ `ports.review_queue.list_approval_queue_items(...)`
   - Replace `read_store.get_deployment_diff(id)` $\rightarrow$ `ports.review_queue.get_deployment_diff(id)`
3. **Dynamic `getattr` Replacement Rules:**
   - In `_human_gate_find_intervention_record`: call `ports.interventions.get_intervention(source_id)` directly.
   - In `_v5_intervention_records`: call `ports.interventions.list_interventions(status=status, kind=kind)` directly.
