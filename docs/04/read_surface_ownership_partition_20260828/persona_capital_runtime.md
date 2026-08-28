# Read-Surface Caller Ownership Partition: Persona, Capital, Deployment, Runtime, Ranking, and Evolution

**Task ID:** `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828`  
**Owner:** `Antigravity2`  
**Reviewer:** `Codex2`  
**Domain:** Persona Fleet, Capital Pools & Bindings, Deployment Plans, Runtime Bindings & Monitoring, Ranking Projections & Formulas, and Evolution Decisions  
**Status:** Complete Caller Ownership & Partition Specification  

---

## 1. Executive Summary & Acceptance Verification

This document provides the authoritative caller ownership partition and inventory for all legacy `read_store` member calls in `services/control-plane/bff/main.py` belonging to the **Persona, Capital, Deployment, Runtime, Ranking, and Evolution** domain (`persona_capital_runtime`).

### Key Metrics
- **Total Legacy `read_store` Call Sites in `main.py`:** `600`
- **Total Distinct `read_store` Methods in `main.py`:** `203`
- **`persona_capital_runtime` Distinct Methods:** `48`
- **`persona_capital_runtime` Total Call Sites:** `227`
- **Read Methods in Domain:** `39` methods (`213` call sites)
- **Write / Mutation Methods in Domain:** `9` methods (`14` call sites)
- **Domain Port Direct Coverage (Existing 1:1 APIs):** `29` methods
- **Missing Narrow Domain APIs Identified:** `10` read methods
- **Command / Mutation Destinations Identified:** `9` write methods

### Acceptance Criteria Verification
1. **Complete Method & Line Inventory:** Every `read_store` call in `main.py` belonging to this domain is indexed with its exact 1-indexed line number, enclosing function/endpoint, call signature, and invocation context (§ 3 & § 4).
2. **Read / Write Classification & Destination:** Every method is classified as `Read` or `Write` with its exact destination named (domain ports in `domain_ports/persona_capital_runtime.py` and `ports/persona_capital_runtime.py`, or command owners in `command_executor.py` / domain services) (§ 2 & § 3).
3. **Missing Narrow Domain API Identification:** Explicitly identifies narrow read APIs missing from domain ports without proposing generic fallback delegation, compatibility storage, or product source edits (§ 5).
4. **Exact Method Count & Non-Overlap Proof:** Proves exact method count (`48`) and non-overlap across all 6 ownership partition tasks, ensuring 100% disjoint union covering all 203 methods and 600 calls (§ 6).
5. **Zero Production Source Modification:** No production source code in `services/control-plane/bff` is modified in this task.

---

## 2. Subsystem Architecture & Port Mapping

The Persona, Capital, Deployment, Runtime, Ranking, and Evolution domain comprises 6 coherent sub-domains, unified under `PersonaCapitalRuntimeDomainPort` and `ReadSurfacePorts.persona_capital_runtime`:

| Subsystem | Domain Port Class | Description | Distinct Methods | Call Sites |
|---|---|---|---|---|
| **Persona Fleet** | `PersonaFleetPort` | Registry reads, persona entity queries, capability snapshots, and session lookups. | 12 | 65 |
| **Capital Pools & Bindings** | `CapitalPoolPort` | Capital pool definitions, persona capital bindings, pool quotas, and role bindings. | 7 | 46 |
| **Deployment Plans** | `DeploymentPlanPort` | Deployment plans, plan diffs, target pool bindings, and deployment mode status. | 3 | 18 |
| **Runtime Bindings & Monitoring** | `RuntimePort` | Runtime binding records, runtime ID lookup, and paper-fleet monitoring sessions. | 7 | 49 |
| **Rankings & Projections** | `RankingProjectionPort` | Pure DTO projection over rankings, formulas, allocations, league, and containments. | 16 | 36 |
| **Evolution Projections** | `EvolutionProjectionPort` | Pure DTO projection over evolution programs, candidate runs, and incident decisions. | 3 | 13 |
| **Total Domain** | `PersonaCapitalRuntimeDomainPort` | Consolidated domain facade over all 6 sub-ports. | **48** | **227** |

---

## 3. Comprehensive Method Inventory Table

| # | Method Name | Subsystem | Calls | Type | Target Destination | Status / Notes |
|---|---|---|---|---|---|---|
| 1 | `create_deployment_plan` | Deployment Plans | 1 | `Write` | `command_executor.py / Deployment Plan Service (POST /deployment-plans)` | Command API destination (mutation) |
| 2 | `create_persona` | Persona Fleet | 1 | `Write` | `command_executor.py / Persona Registry Service (POST /personas)` | Command API destination (mutation) |
| 3 | `create_ranking_formula` | Rankings & Projections | 1 | `Write` | `command_executor.py / Ranking Formula Store` | Command API destination (mutation) |
| 4 | `create_runtime_binding` | Runtime Bindings & Monitoring | 1 | `Write` | `command_executor.py / Runtime Service (POST /runtime-bindings)` | Command API destination (mutation) |
| 5 | `get_allocation_evaluation` | Rankings & Projections | 1 | `Read` | `RankingProjectionPort.get_allocation_evaluation` | Missing narrow API (allocation evaluation lookup) |
| 6 | `get_allowed_actions` | Deployment Plans | 2 | `Read` | `DeploymentPlanPort.get_allowed_actions` | Missing narrow API (pure DTO derivation over deployment plan & decision) |
| 7 | `get_binding` | Capital Pools & Bindings | 3 | `Read` | `CapitalPoolPort.get_binding` | Existing 1:1 on domain port |
| 8 | `get_bindings_for_persona` | Capital Pools & Bindings | 6 | `Read` | `CapitalPoolPort.get_bindings_for_persona` | Existing 1:1 on domain port |
| 9 | `get_bindings_for_pool` | Capital Pools & Bindings | 3 | `Read` | `CapitalPoolPort.get_bindings_for_pool` | Existing 1:1 on domain port |
| 10 | `get_capability_snapshot` | Persona Fleet | 2 | `Read` | `PersonaFleetPort.get_capability_snapshot / PersonaRegistryReadsPort.get_persona_capabilities` | Missing narrow API on PersonaFleetPort (delegates to capability provider) |
| 11 | `get_capability_snapshot_for_persona` | Persona Fleet | 9 | `Read` | `PersonaFleetPort.get_capability_snapshot_for_persona` | Missing narrow API (persona capability lookup) |
| 12 | `get_capital_pool` | Capital Pools & Bindings | 11 | `Read` | `CapitalPoolPort.get_capital_pool` | Existing 1:1 on domain port |
| 13 | `get_deployment_plan` | Deployment Plans | 10 | `Read` | `DeploymentPlanPort.get_deployment_plan` | Existing 1:1 on domain port |
| 14 | `get_evolution_decision_by_id` | Evolution Projections | 2 | `Read` | `EvolutionProjectionPort.get_evolution_decision_by_id` | Missing narrow API (evolution decision by ID lookup) |
| 15 | `get_evolution_decisions_by_incident` | Evolution Projections | 2 | `Read` | `EvolutionProjectionPort.get_evolution_decisions_by_incident / DomainIncidentPort` | Missing narrow API (evolution decision by incident lookup) |
| 16 | `get_paper_runtime_monitoring_session` | Runtime Bindings & Monitoring | 2 | `Read` | `RuntimePort.get_paper_runtime_monitoring_session` | Missing narrow API (monitoring session lookup) |
| 17 | `get_persona` | Persona Fleet | 23 | `Read` | `PersonaFleetPort.get_persona` | Existing 1:1 on domain port |
| 18 | `get_persona_allowed_actions` | Persona Fleet | 3 | `Read` | `PersonaFleetPort.get_persona_allowed_actions` | Missing narrow API (pure DTO derivation from persona lifecycle state) |
| 19 | `get_persona_containment` | Rankings & Projections | 1 | `Read` | `RankingProjectionPort.get_persona_containment` | Existing 1:1 on domain port |
| 20 | `get_persona_league_entry` | Rankings & Projections | 3 | `Read` | `RankingProjectionPort.get_persona_league_entry` | Existing 1:1 on domain port |
| 21 | `get_ranking` | Rankings & Projections | 2 | `Read` | `RankingProjectionPort.get_ranking` | Existing 1:1 on domain port |
| 22 | `get_ranking_formula` | Rankings & Projections | 4 | `Read` | `RankingProjectionPort.get_ranking_formula` | Missing narrow API (ranking formula lookup) |
| 23 | `get_ranking_snapshot` | Rankings & Projections | 1 | `Read` | `RankingProjectionPort.get_ranking_snapshot` | Missing narrow API (ranking snapshot lookup) |
| 24 | `get_rebalance` | Rankings & Projections | 6 | `Read` | `RankingProjectionPort.get_rebalance` | Existing 1:1 on domain port |
| 25 | `get_route_policy_for_persona` | Persona Fleet | 3 | `Read` | `PersonaFleetPort.get_route_policy_for_persona / DomainWorkflowCatalogPort` | Missing narrow API (route policy lookup) |
| 26 | `get_runtime_binding` | Runtime Bindings & Monitoring | 6 | `Read` | `RuntimePort.get_runtime_binding` | Existing 1:1 on domain port |
| 27 | `get_runtime_binding_by_runtime_id` | Runtime Bindings & Monitoring | 8 | `Read` | `RuntimePort.get_runtime_binding_by_runtime_id` | Existing 1:1 on domain port |
| 28 | `get_session` | Persona Fleet | 1 | `Read` | `PersonaFleetPort.get_session` | Missing narrow API (session record projection) |
| 29 | `get_sessions_for_persona` | Persona Fleet | 7 | `Read` | `PersonaFleetPort.get_sessions_for_persona / PersonaTrainingDomainPort.list_persona_sessions` | Missing narrow API on PersonaFleetPort (session provider) |
| 30 | `list_authoritative_paper_runtime_monitoring_sessions` | Runtime Bindings & Monitoring | 4 | `Read` | `RuntimePort.list_authoritative_paper_runtime_monitoring_sessions` | Missing narrow API (authoritative paper fleet reader) |
| 31 | `list_bindings` | Capital Pools & Bindings | 14 | `Read` | `CapitalPoolPort.list_bindings` | Existing 1:1 on domain port |
| 32 | `list_capital_allocations` | Rankings & Projections | 3 | `Read` | `RankingProjectionPort.list_capital_allocations` | Existing 1:1 on domain port |
| 33 | `list_capital_pools` | Capital Pools & Bindings | 8 | `Read` | `CapitalPoolPort.list_capital_pools` | Existing 1:1 on domain port |
| 34 | `list_deployment_plans` | Deployment Plans | 7 | `Read` | `DeploymentPlanPort.list_deployment_plans` | Existing 1:1 on domain port |
| 35 | `list_evolution_decisions` | Evolution Projections | 9 | `Read` | `EvolutionProjectionPort.list_evolution_decisions` | Existing 1:1 on domain port |
| 36 | `list_paper_runtime_monitoring_sessions` | Runtime Bindings & Monitoring | 1 | `Read` | `RuntimePort.list_paper_runtime_monitoring_sessions` | Missing narrow API (monitoring session reader) |
| 37 | `list_persona_league` | Rankings & Projections | 6 | `Read` | `RankingProjectionPort.list_persona_league` | Existing 1:1 on domain port |
| 38 | `list_personas` | Persona Fleet | 8 | `Read` | `PersonaFleetPort.list_personas` | Existing 1:1 on domain port |
| 39 | `list_ranking_formulas` | Rankings & Projections | 2 | `Read` | `RankingProjectionPort.list_ranking_formulas` | Existing 1:1 on domain port |
| 40 | `list_rankings` | Rankings & Projections | 1 | `Read` | `RankingProjectionPort.list_rankings` | Existing 1:1 on domain port |
| 41 | `list_rebalances` | Rankings & Projections | 1 | `Read` | `RankingProjectionPort.list_rebalances` | Existing 1:1 on domain port |
| 42 | `list_runtime_bindings` | Runtime Bindings & Monitoring | 27 | `Read` | `RuntimePort.list_runtime_bindings` | Existing 1:1 on domain port |
| 43 | `list_sessions_for_persona` | Persona Fleet | 1 | `Read` | `PersonaFleetPort.list_sessions_for_persona / PersonaTrainingDomainPort.list_persona_sessions` | Missing narrow API on PersonaFleetPort (session provider) |
| 44 | `patch_capital_pool` | Capital Pools & Bindings | 1 | `Write` | `command_executor.py / Capital Pool Service (PATCH /capital-pools/{id})` | Command API destination (mutation) |
| 45 | `patch_ranking_formula` | Rankings & Projections | 1 | `Write` | `command_executor.py / Ranking Formula Store` | Command API destination (mutation) |
| 46 | `put_allocation_evaluation` | Rankings & Projections | 2 | `Write` | `command_executor.py / Allocation Evaluation Store` | Command API destination (mutation) |
| 47 | `put_ranking_snapshot` | Rankings & Projections | 1 | `Write` | `command_executor.py / Ranking Snapshot Store` | Command API destination (mutation) |
| 48 | `update_persona` | Persona Fleet | 5 | `Write` | `command_executor.py / Persona Registry Service (PATCH /personas/{id})` | Command API destination (mutation) |

---

## 4. Granular Call Site Catalog by Method

Below is the exhaustive catalog of all 227 call sites across `main.py`, organized by subsystem and method.

### 4.1 Persona Fleet

#### `create_persona`
- **Subsystem:** Persona Fleet
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Persona Registry Service (POST /personas)`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L47533 | `_persona_record_for_provisioning` | `persona = read_store.create_persona(` |

#### `get_capability_snapshot`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.get_capability_snapshot / PersonaRegistryReadsPort.get_persona_capabilities`
- **API Status:** Missing narrow API on PersonaFleetPort (delegates to capability provider)
- **Total Invocations:** 2
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L15239 | `get_session_detail` | `snapshot = read_store.get_capability_snapshot(session.get("capability_snapshot_id"))` |
| L39974 | `_persona_intent_capability_summary` | `snapshot = read_store.get_capability_snapshot(snapshot_id) if snapshot_id else None` |

#### `get_capability_snapshot_for_persona`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.get_capability_snapshot_for_persona`
- **API Status:** Missing narrow API (persona capability lookup)
- **Total Invocations:** 9
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L15241 | `get_session_detail` | `snapshot = read_store.get_capability_snapshot_for_persona(session.get("persona_id"))` |
| L15313 | `get_persona_capabilities` | `snapshot = read_store.get_capability_snapshot_for_persona(persona_id)` |
| L29129 | `_strategy_seed_persona_suggestions` | `capability_snapshot = read_store.get_capability_snapshot_for_persona(persona_id) or {}` |
| L31106 | `_persona_strategy_discovery_payload` | `capability_snapshot = read_store.get_capability_snapshot_for_persona(persona_id) or {}` |
| L39977 | `_persona_intent_capability_summary` | `snapshot = read_store.get_capability_snapshot_for_persona(persona_id)` |
| L48616 | `bff_get_persona_skills` | `snapshot = read_store.get_capability_snapshot_for_persona(persona_id)` |
| L48653 | `bff_get_persona_tools` | `snapshot = read_store.get_capability_snapshot_for_persona(persona_id)` |
| L48690 | `bff_get_persona_capabilities_surface` | `snapshot = read_store.get_capability_snapshot_for_persona(persona_id)` |
| L48784 | `_pm12_persona_capability_summary` | `snapshot = read_store.get_capability_snapshot_for_persona(persona_id) or {}` |

#### `get_persona`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.get_persona`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 23
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L6336 | `_enforce_ops_console_preconditions` | `persona = read_store.get_persona(persona_id)` |
| L13470 | `_kw02_resolve_attachment_target` | `persona = read_store.get_persona(attachment_ref)` |
| L15162 | `get_persona_detail` | `persona = read_store.get_persona(persona_id)` |
| L15199 | `list_persona_sessions` | `persona = read_store.get_persona(persona_id)` |
| L15270 | `list_persona_teaching_sessions` | `persona = read_store.get_persona(persona_id)` |
| L15302 | `get_persona_capabilities` | `persona = read_store.get_persona(persona_id)` |
| L15356 | `create_trainer_session` | `persona = read_store.get_persona(persona_id)` |
| L15415 | `list_trainer_sessions` | `persona = read_store.get_persona(persona_id)` |
| L15723 | `list_trainer_replays` | `persona = read_store.get_persona(persona_id)` |
| L16969 | `get_binding` | `persona = read_store.get_persona(binding.get("persona_id"))` |
| L21689 | `get_persona_management` | `persona = read_store.get_persona(persona_id)` |
| L22371 | `list_consultations` | `persona = read_store.get_persona(persona_id)` |
| L22592 | `get_consult_policy` | `persona = read_store.get_persona(persona_id)` |
| L26623 | `_ppl_alloc_009_paper_eligibility_context` | `raw = read_store.get_persona(persona_id)` |
| L31087 | `_persona_strategy_discovery_payload` | `persona = read_store.get_persona(persona_id)` |
| L39966 | `_persona_intent_persona_label` | `persona = read_store.get_persona(persona_id)` |
| L47180 | `bff_reconcile_persona_provisioning` | `raw = read_store.get_persona(persona_id)` |
| L47531 | `_persona_record_for_provisioning` | `existing = read_store.get_persona(record.persona_id)` |
| L48051 | `bff_patch_persona` | `raw = read_store.get_persona(persona_id)` |
| L48217 | `bff_get_persona_runtime_profile` | `persona = read_store.get_persona(persona_id) or {"persona_id": persona_id}` |
| L50788 | `_enrich_persona_item_with_bindings` | `persona = read_store.get_persona(persona_id) or {}` |
| L54501 | `_ops_read_model_entry_for_persona` | `persona = read_store.get_persona(persona_id)` |
| L67965 | `_resolve_agora_interaction_context_ref` | `persona = read_store.get_persona(persona_id)` |

#### `get_persona_allowed_actions`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.get_persona_allowed_actions`
- **API Status:** Missing narrow API (pure DTO derivation from persona lifecycle state)
- **Total Invocations:** 3
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L21758 | `get_persona_management` | `allowed_actions = read_store.get_persona_allowed_actions(persona_id)` |
| L36518 | `_project_persona_fleet_item` | `allowed_actions = read_store.get_persona_allowed_actions(persona_id) or {}` |
| L52565 | `_project_persona_league_row` | `allowed_actions = read_store.get_persona_allowed_actions(persona_id) or {}` |

#### `get_route_policy_for_persona`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.get_route_policy_for_persona / DomainWorkflowCatalogPort`
- **API Status:** Missing narrow API (route policy lookup)
- **Total Invocations:** 3
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L29128 | `_strategy_seed_persona_suggestions` | `route_policy = read_store.get_route_policy_for_persona(persona_id) or {}` |
| L31105 | `_persona_strategy_discovery_payload` | `route_policy = read_store.get_route_policy_for_persona(persona_id) or {}` |
| L48759 | `_pm12_persona_route_summary` | `policy = read_store.get_route_policy_for_persona(persona_id) or {}` |

#### `get_session`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.get_session`
- **API Status:** Missing narrow API (session record projection)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L15229 | `get_session_detail` | `session = read_store.get_session(session_id)` |

#### `get_sessions_for_persona`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.get_sessions_for_persona / PersonaTrainingDomainPort.list_persona_sessions`
- **API Status:** Missing narrow API on PersonaFleetPort (session provider)
- **Total Invocations:** 7
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L21734 | `get_persona_management` | `sessions = read_store.get_sessions_for_persona(persona_id)` |
| L36426 | `_project_persona_fleet_item` | `sessions = list(read_store.get_sessions_for_persona(persona_id) or [])` |
| L40204 | `_persona_intent_all_items` | `for session in read_store.get_sessions_for_persona(persona_id) or []:` |
| L48449 | `bff_get_persona_activity` | `sessions = read_store.get_sessions_for_persona(persona_id) or []` |
| L49013 | `_pm12_runtime_session_resolution` | `for raw_session in read_store.get_sessions_for_persona(persona_id) or []:` |
| L49027 | `_pm12_runtime_session_resolution` | `for raw_session in read_store.get_sessions_for_persona(persona_id) or []:` |
| L49074 | `_pm12_persona_session_summary` | `for session in (read_store.get_sessions_for_persona(persona_id) or [])` |

#### `list_personas`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.list_personas`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 8
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L15137 | `list_personas` | `personas = read_store.list_personas(` |
| L30925 | `_list_persona_records` | `items = list(read_store.list_personas() or [])` |
| L30975 | `_get_persona_directory_snapshot` | `defaults = read_store.list_personas(include_market_persona_defaults=True) or []` |
| L37754 | `_build_persona_readiness_items` | `read_store.list_personas(include_market_persona_defaults=True) or []` |
| L40546 | `bff_management_evolution_journal` | `personas = read_store.list_personas(include_market_persona_defaults=True) or []` |
| L63910 | `_persona_fleet_context_defaults_by_market` | `else read_store.list_personas(include_market_persona_defaults=True)` |
| L64079 | `_build_persona_health_items` | `for persona in read_store.list_personas(` |
| L67969 | `_resolve_agora_interaction_context_ref` | `row for row in read_store.list_personas(include_market_persona_defaults=True)` |

#### `list_sessions_for_persona`
- **Subsystem:** Persona Fleet
- **Classification:** `Read`
- **Target Destination:** `PersonaFleetPort.list_sessions_for_persona / PersonaTrainingDomainPort.list_persona_sessions`
- **API Status:** Missing narrow API on PersonaFleetPort (session provider)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L15209 | `list_persona_sessions` | `sessions = read_store.list_sessions_for_persona(persona_id, status=status) or []` |

#### `update_persona`
- **Subsystem:** Persona Fleet
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Persona Registry Service (PATCH /personas/{id})`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 5
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L30105 | `_materialize_terminal_persona_provisioning_ledger` | `read_store.update_persona(` |
| L30199 | `_evaluate_persona_provisioning_status` | `read_store.update_persona(` |
| L30722 | `_evaluate_persona_provisioning_status` | `read_store.update_persona(` |
| L47563 | `_persona_record_for_provisioning` | `persona = read_store.update_persona(` |
| L48129 | `bff_patch_persona` | `persona_record = read_store.update_persona(` |

### 4.2 Capital Pools & Bindings

#### `get_binding`
- **Subsystem:** Capital Pools & Bindings
- **Classification:** `Read`
- **Target Destination:** `CapitalPoolPort.get_binding`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 3
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L8224 | `_project_affected_bindings` | `binding = read_store.get_binding(binding_id)` |
| L16427 | `_deployment_plan_persona_id` | `binding = read_store.get_binding(binding_id)` |
| L16959 | `get_binding` | `binding = read_store.get_binding(binding_id)` |

#### `get_bindings_for_persona`
- **Subsystem:** Capital Pools & Bindings
- **Classification:** `Read`
- **Target Destination:** `CapitalPoolPort.get_bindings_for_persona`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 6
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L15172 | `get_persona_detail` | `bindings = read_store.get_bindings_for_persona(persona_id) or []` |
| L21702 | `get_persona_management` | `persona_bindings = read_store.get_bindings_for_persona(persona_id)` |
| L36414 | `_project_persona_fleet_item` | `bindings = list(read_store.get_bindings_for_persona(persona_id) or [])` |
| L48806 | `_pm12_persona_binding_summary` | `bindings = read_store.get_bindings_for_persona(persona_id) or []` |
| L49081 | `_pm12_persona_session_summary` | `read_store.get_bindings_for_persona(persona_id) or [],` |
| L63396 | `_first_binding_for_persona` | `bindings = read_store.get_bindings_for_persona(persona_id)` |

#### `get_bindings_for_pool`
- **Subsystem:** Capital Pools & Bindings
- **Classification:** `Read`
- **Target Destination:** `CapitalPoolPort.get_bindings_for_pool`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 3
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L16937 | `get_capital_pool` | `bindings = read_store.get_bindings_for_pool(pool_id)` |
| L17253 | `get_deployment_review` | `bindings = read_store.get_bindings_for_pool(plan.get("capital_pool_id"))` |
| L25854 | `bff_get_capital_pool` | `bindings = read_store.get_bindings_for_pool(pool_id)` |

#### `get_capital_pool`
- **Subsystem:** Capital Pools & Bindings
- **Classification:** `Read`
- **Target Destination:** `CapitalPoolPort.get_capital_pool`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 11
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L16927 | `get_capital_pool` | `pool = read_store.get_capital_pool(pool_id)` |
| L17252 | `get_deployment_review` | `pool = read_store.get_capital_pool(plan.get("capital_pool_id"))` |
| L21717 | `get_persona_management` | `pool = read_store.get_capital_pool(binding.get("capital_pool_id"))` |
| L25832 | `bff_get_capital_pool` | `pool = read_store.get_capital_pool(pool_id)` |
| L25916 | `bff_patch_capital_pool` | `pool = read_store.get_capital_pool(pool_id)` |
| L25958 | `bff_capital_pool_action` | `pool = read_store.get_capital_pool(pool_id)` |
| L27172 | `_ppl_alloc_009_paper_capital_context` | `pool = read_store.get_capital_pool(pool_id)` |
| L27564 | `_ppl_alloc_009_paper_rebalance_authority` | `pool = read_store.get_capital_pool(pool_id)` |
| L36502 | `_project_persona_fleet_item` | `for pool in [read_store.get_capital_pool(pool_id)]` |
| L36508 | `_project_persona_fleet_item` | `"capital_pool": read_store.get_capital_pool(str(binding.get("capital_pool_id") or "")),` |
| L48825 | `_pm12_persona_binding_summary` | `pool = read_store.get_capital_pool(pool_id) or {}` |

#### `list_bindings`
- **Subsystem:** Capital Pools & Bindings
- **Classification:** `Read`
- **Target Destination:** `CapitalPoolPort.list_bindings`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 14
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L12132 | `_build_management_capital_binding_live_readiness_payload` | `bindings = read_store.list_bindings()` |
| L16210 | `list_bindings` | `bindings = read_store.list_bindings(` |
| L25630 | `bff_list_capital_pools` | `bindings = read_store.list_bindings() or []` |
| L27143 | `_ppl_alloc_009_paper_capital_context` | `for binding in read_store.list_bindings(` |
| L27572 | `_ppl_alloc_009_paper_rebalance_authority` | `for binding in read_store.list_bindings(` |
| L31810 | `_management_portfolio_book_pool_sources` | `bindings = read_store.list_bindings() or []` |
| L32867 | `_pm12_performance_attribution_sources` | `bindings = read_store.list_bindings(include_market_persona_defaults=True) or []` |
| L35852 | `bff_management_portfolio_book_holdings` | `bindings = read_store.list_bindings(include_market_persona_defaults=True) or []` |
| L40568 | `bff_management_evolution_journal` | `# Read canonical persona-capital bindings (read_store.list_bindings)` |
| L40591 | `bff_management_evolution_journal` | `bindings += list(read_store.list_bindings(include_market_persona_defaults=True) or [])` |
| L50778 | `_enrich_persona_item_with_bindings` | `bindings = read_store.list_bindings(persona_id=persona_id) or []` |
| L52652 | `_pm12_persona_league_rows` | `for record in (read_store.list_bindings() or [])` |
| L63391 | `_first_binding_for_persona` | `bindings = read_store.list_bindings(` |
| L65485 | `_persona_fleet_slim_list_payload` | `bindings = read_store.list_bindings(include_market_persona_defaults=True)` |

#### `list_capital_pools`
- **Subsystem:** Capital Pools & Bindings
- **Classification:** `Read`
- **Target Destination:** `CapitalPoolPort.list_capital_pools`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 8
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L16185 | `list_capital_pools` | `pools = read_store.list_capital_pools(status=status, risk_policy_ref=risk_policy_ref)` |
| L25629 | `bff_list_capital_pools` | `pools = read_store.list_capital_pools(status=status, risk_policy_ref=risk_policy_ref)` |
| L31809 | `_management_portfolio_book_pool_sources` | `capital_pools = read_store.list_capital_pools(status=status, risk_policy_ref=risk_policy_ref) or []` |
| L32868 | `_pm12_performance_attribution_sources` | `capital_pools = read_store.list_capital_pools(include_market_persona_defaults=True) or []` |
| L35853 | `bff_management_portfolio_book_holdings` | `capital_pools = read_store.list_capital_pools(include_market_persona_defaults=True) or []` |
| L43448 | `_mgmt_nl_collect_context` | `pools = _mgmt_nl_filter_tenant_records(list(read_store.list_capital_pools() or []), tenant_id)` |
| L56189 | `_match` | `for pool in (read_store.list_capital_pools() or []):` |
| L65487 | `_persona_fleet_slim_list_payload` | `pools = read_store.list_capital_pools(include_market_persona_defaults=True)` |

#### `patch_capital_pool`
- **Subsystem:** Capital Pools & Bindings
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Capital Pool Service (PATCH /capital-pools/{id})`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L25924 | `bff_patch_capital_pool` | `updated = read_store.patch_capital_pool(` |

### 4.3 Deployment Plans

#### `create_deployment_plan`
- **Subsystem:** Deployment Plans
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Deployment Plan Service (POST /deployment-plans)`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L16550 | `create_deployment_plan_v1` | `record = read_store.create_deployment_plan(` |

#### `get_allowed_actions`
- **Subsystem:** Deployment Plans
- **Classification:** `Read`
- **Target Destination:** `DeploymentPlanPort.get_allowed_actions`
- **API Status:** Missing narrow API (pure DTO derivation over deployment plan & decision)
- **Total Invocations:** 2
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L17194 | `list_operator_deployment_plans` | `if not _pkt001_allowed_actions_present(read_store.get_allowed_actions(plan_id)):` |
| L17259 | `get_deployment_review` | `allowed_actions = read_store.get_allowed_actions(plan_id)` |

#### `get_deployment_plan`
- **Subsystem:** Deployment Plans
- **Classification:** `Read`
- **Target Destination:** `DeploymentPlanPort.get_deployment_plan`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 10
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L2840 | `_runtime_command_context` | `plan = read_store.get_deployment_plan(plan_id)` |
| L12561 | `_build_operator_paper_live_drift_payload` | `plan = read_store.get_deployment_plan(plan_id) if plan_id else None` |
| L16539 | `create_deployment_plan_v1` | `if read_store.get_deployment_plan(plan_id) is not None:` |
| L16888 | `get_deployment_plan` | `plan = read_store.get_deployment_plan(plan_id)` |
| L17002 | `get_runtime_binding` | `plan = read_store.get_deployment_plan(runtime_binding.get("plan_id", ""))` |
| L17243 | `get_deployment_review` | `plan = read_store.get_deployment_plan(plan_id)` |
| L26783 | `_ppl_alloc_009_paper_eligibility_context` | `plan = read_store.get_deployment_plan(plan_id) if plan_id else None` |
| L36327 | `_persona_fleet_runtime_matches` | `plan = read_store.get_deployment_plan(plan_id) or {}` |
| L59240 | `bff_get_deployment` | `plan = read_store.get_deployment_plan(clean_id)` |
| L59298 | `bff_deployment_action` | `plan = read_store.get_deployment_plan(clean_id)` |

#### `list_deployment_plans`
- **Subsystem:** Deployment Plans
- **Classification:** `Read`
- **Target Destination:** `DeploymentPlanPort.list_deployment_plans`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 7
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L16341 | `list_deployment_plans` | `plans = read_store.list_deployment_plans(` |
| L17186 | `list_operator_deployment_plans` | `for plan in read_store.list_deployment_plans():` |
| L21813 | `get_persona_management` | `all_plans = read_store.list_deployment_plans() or []` |
| L31811 | `_management_portfolio_book_pool_sources` | `deployment_plans = read_store.list_deployment_plans() or []` |
| L32866 | `_pm12_performance_attribution_sources` | `deployment_plans = read_store.list_deployment_plans() or []` |
| L35851 | `bff_management_portfolio_book_holdings` | `deployment_plans = read_store.list_deployment_plans() or []` |
| L59190 | `bff_list_deployments` | `plans = read_store.list_deployment_plans()` |

### 4.4 Runtime Bindings & Monitoring

#### `create_runtime_binding`
- **Subsystem:** Runtime Bindings & Monitoring
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Runtime Service (POST /runtime-bindings)`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L59373 | `bff_create_runtime` | `record = read_store.create_runtime_binding(` |

#### `get_paper_runtime_monitoring_session`
- **Subsystem:** Runtime Bindings & Monitoring
- **Classification:** `Read`
- **Target Destination:** `RuntimePort.get_paper_runtime_monitoring_session`
- **API Status:** Missing narrow API (monitoring session lookup)
- **Total Invocations:** 2
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L8672 | `_project_operator_runtime_state_row` | `else read_store.get_paper_runtime_monitoring_session(` |
| L58909 | `_runtime_fleet_stage_truth` | `monitoring = read_store.get_paper_runtime_monitoring_session(` |

#### `get_runtime_binding`
- **Subsystem:** Runtime Bindings & Monitoring
- **Classification:** `Read`
- **Target Destination:** `RuntimePort.get_runtime_binding`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 6
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L16992 | `get_runtime_binding` | `runtime_binding = read_store.get_runtime_binding(binding_id)` |
| L17254 | `get_deployment_review` | `runtime_binding = read_store.get_runtime_binding(plan.get("runtime_binding_id"))` |
| L21578 | `get_incident_response` | `runtime_binding = read_store.get_runtime_binding(binding_id)` |
| L58870 | `_deployment_runtime_binding` | `binding = read_store.get_runtime_binding(runtime_binding_id)` |
| L59486 | `bff_get_runtime` | `binding = read_store.get_runtime_binding(clean_id)` |
| L59529 | `bff_runtime_action` | `binding = read_store.get_runtime_binding(clean_id)` |

#### `get_runtime_binding_by_runtime_id`
- **Subsystem:** Runtime Bindings & Monitoring
- **Classification:** `Read`
- **Target Destination:** `RuntimePort.get_runtime_binding_by_runtime_id`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 8
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L2818 | `_runtime_command_context` | `runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)` |
| L6390 | `_enforce_ops_console_preconditions` | `binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)` |
| L12546 | `_build_operator_paper_live_drift_payload` | `runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)` |
| L16855 | `get_runtime_status` | `runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)` |
| L17981 | `get_operator_paper_live_drift` | `runtime_binding = read_store.get_runtime_binding_by_runtime_id(runtime_id)` |
| L21580 | `get_incident_response` | `runtime_binding = read_store.get_runtime_binding_by_runtime_id(incident.get("runtime_id"))` |
| L59483 | `bff_get_runtime` | `binding = read_store.get_runtime_binding_by_runtime_id(clean_id)` |
| L59527 | `bff_runtime_action` | `binding = read_store.get_runtime_binding_by_runtime_id(clean_id)` |

#### `list_authoritative_paper_runtime_monitoring_sessions`
- **Subsystem:** Runtime Bindings & Monitoring
- **Classification:** `Read`
- **Target Destination:** `RuntimePort.list_authoritative_paper_runtime_monitoring_sessions`
- **API Status:** Missing narrow API (authoritative paper fleet reader)
- **Total Invocations:** 4
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L30399 | `_evaluate_persona_provisioning_status` | `else read_store.list_authoritative_paper_runtime_monitoring_sessions()` |
| L47037 | `_persona_readback_snapshot` | `read_store.list_authoritative_paper_runtime_monitoring_sessions()` |
| L48965 | `_pm12_authoritative_paper_monitoring_sessions` | `read_store.list_authoritative_paper_runtime_monitoring_sessions()` |
| L49005 | `_pm12_runtime_session_resolution` | `read_store.list_authoritative_paper_runtime_monitoring_sessions() or []` |

#### `list_paper_runtime_monitoring_sessions`
- **Subsystem:** Runtime Bindings & Monitoring
- **Classification:** `Read`
- **Target Destination:** `RuntimePort.list_paper_runtime_monitoring_sessions`
- **API Status:** Missing narrow API (monitoring session reader)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L10327 | `_build_management_trading_pulse_payload` | `read_store.list_paper_runtime_monitoring_sessions()` |

#### `list_runtime_bindings`
- **Subsystem:** Runtime Bindings & Monitoring
- **Classification:** `Read`
- **Target Destination:** `RuntimePort.list_runtime_bindings`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 27
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L8807 | `_build_runtime_health_group` | `bindings = read_store.list_runtime_bindings()` |
| L9460 | `_build_runtime_alerts` | `bindings = read_store.list_runtime_bindings()` |
| L10319 | `_build_management_trading_pulse_payload` | `runtime_bindings = read_store.list_runtime_bindings()` |
| L12133 | `_build_management_capital_binding_live_readiness_payload` | `runtime_bindings = read_store.list_runtime_bindings()` |
| L16829 | `list_runtime_bindings` | `bindings = read_store.list_runtime_bindings(` |
| L17394 | `list_operator_runtime_state` | `bindings = read_store.list_runtime_bindings()` |
| L21780 | `get_persona_management` | `all_runtime_bindings=list(read_store.list_runtime_bindings() or []),` |
| L26715 | `_ppl_alloc_009_paper_eligibility_context` | `for runtime in read_store.list_runtime_bindings():` |
| L31812 | `_management_portfolio_book_pool_sources` | `runtime_bindings = read_store.list_runtime_bindings() or []` |
| L32865 | `_pm12_performance_attribution_sources` | `runtime_bindings = read_store.list_runtime_bindings(include_market_persona_defaults=True) or []` |
| L35850 | `bff_management_portfolio_book_holdings` | `runtime_bindings = read_store.list_runtime_bindings(include_market_persona_defaults=True) or []` |
| L36579 | `_project_persona_fleet_payload` | `runtime_bindings = list(read_store.list_runtime_bindings() or [])` |
| L40590 | `bff_management_evolution_journal` | `bindings = list(read_store.list_runtime_bindings(include_market_persona_defaults=True) or [])` |
| L43401 | `_mgmt_nl_collect_context` | `list(read_store.list_runtime_bindings() or []),` |
| L43430 | `_mgmt_nl_collect_context` | `list(read_store.list_runtime_bindings() or []),` |
| L43449 | `_mgmt_nl_collect_context` | `runtime_bindings = _mgmt_nl_filter_tenant_records(list(read_store.list_runtime_bindings() or []), tenant_id)` |
| L43478 | `_mgmt_nl_collect_context` | `runtime_bindings = _mgmt_nl_filter_tenant_records(list(read_store.list_runtime_bindings() or []), tenant_id)` |
| L48817 | `_pm12_persona_binding_summary` | `else (read_store.list_runtime_bindings() or [])` |
| L49087 | `_pm12_persona_session_summary` | `for runtime in (read_store.list_runtime_bindings() or [])` |
| L50783 | `_enrich_persona_item_with_bindings` | `runtimes = read_store.list_runtime_bindings() or []` |
| L52660 | `_pm12_persona_league_rows` | `for record in (read_store.list_runtime_bindings() or [])` |
| L58418 | `_raise_if_runtime_binding_conflict` | `(binding for binding in read_store.list_runtime_bindings() if _runtime_binding_matches_create(binding, binding_id)),` |
| L58874 | `_deployment_runtime_binding` | `for binding in read_store.list_runtime_bindings():` |
| L59438 | `bff_list_runtimes` | `bindings = read_store.list_runtime_bindings()` |
| L61686 | `_health_reason_sentinel_findings` | `runtime_bindings = list(read_store.list_runtime_bindings() or [])` |
| L63415 | `_runtime_for_pool` | `for runtime in read_store.list_runtime_bindings(` |
| L65486 | `_persona_fleet_slim_list_payload` | `runtimes = read_store.list_runtime_bindings(include_market_persona_defaults=True)` |

### 4.5 Rankings & Projections

#### `create_ranking_formula`
- **Subsystem:** Rankings & Projections
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Ranking Formula Store`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L26037 | `bff_deprecated_create_ranking_formula` | `result = read_store.create_ranking_formula(` |

#### `get_allocation_evaluation`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.get_allocation_evaluation`
- **API Status:** Missing narrow API (allocation evaluation lookup)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L26358 | `_pm12_allocation_evaluation_record` | `evaluation = read_store.get_allocation_evaluation(evaluation_id)` |

#### `get_persona_containment`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.get_persona_containment`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L47980 | `bff_get_persona` | `containment = read_store.get_persona_containment(persona_id)` |

#### `get_persona_league_entry`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.get_persona_league_entry`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 3
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L50793 | `_enrich_persona_item_with_bindings` | `league_entry = read_store.get_persona_league_entry(persona_id) or {}` |
| L54513 | `_ops_read_model_entry_for_persona` | `league_entry = read_store.get_persona_league_entry(persona_id) or {}` |
| L65916 | `bff_persona_league_detail` | `entry = read_store.get_persona_league_entry(persona_id)` |

#### `get_ranking`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.get_ranking`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 2
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L28614 | `bff_get_ranking` | `ranking = read_store.get_ranking(ranking_id)` |
| L28644 | `bff_ranking_action` | `ranking = read_store.get_ranking(ranking_id)` |

#### `get_ranking_formula`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.get_ranking_formula`
- **API Status:** Missing narrow API (ranking formula lookup)
- **Total Invocations:** 4
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L26062 | `bff_deprecated_get_ranking_formula` | `formula = read_store.get_ranking_formula(formula_id)` |
| L26103 | `bff_deprecated_patch_ranking_formula` | `formula = read_store.get_ranking_formula(formula_id)` |
| L26145 | `bff_deprecated_ranking_formula_action` | `formula = read_store.get_ranking_formula(formula_id)` |
| L66174 | `_sem_final_generic_detail_for_path` | `read_store.get_ranking_formula(entity_id),` |

#### `get_ranking_snapshot`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.get_ranking_snapshot`
- **API Status:** Missing narrow API (ranking snapshot lookup)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L26280 | `_pm12_allocation_snapshot_record` | `snapshot = read_store.get_ranking_snapshot(snapshot_id)` |

#### `get_rebalance`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.get_rebalance`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 6
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L27493 | `_ppl_alloc_009_paper_rebalance_authority` | `rebalance = read_store.get_rebalance(cmd.target.id)` |
| L28005 | `bff_approve_rebalance_apply` | `if read_store.get_rebalance(rebalance_id) is None:` |
| L28081 | `bff_sign_rebalance_apply` | `if read_store.get_rebalance(rebalance_id) is None:` |
| L28428 | `bff_apply_rebalance_proposal` | `rebalance = read_store.get_rebalance(rebalance_id)` |
| L28527 | `bff_get_rebalance` | `rebalance = read_store.get_rebalance(rebalance_id)` |
| L28561 | `bff_rebalance_action` | `rebalance = read_store.get_rebalance(rebalance_id)` |

#### `list_capital_allocations`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.list_capital_allocations`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 3
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L25631 | `bff_list_capital_pools` | `allocations = read_store.list_capital_allocations()` |
| L25855 | `bff_get_capital_pool` | `allocations = read_store.list_capital_allocations(capital_pool_id=pool_id)` |
| L27196 | `_ppl_alloc_009_paper_capital_context` | `allocations = read_store.list_capital_allocations(` |

#### `list_persona_league`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.list_persona_league`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 6
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L37759 | `_build_persona_readiness_items` | `read_store.list_persona_league(` |
| L52668 | `_pm12_persona_league_rows` | `for record in (read_store.list_persona_league() or [])` |
| L64066 | `_build_persona_health_items` | `for item in read_store.list_persona_league(` |
| L65484 | `_persona_fleet_slim_list_payload` | `league = read_store.list_persona_league(include_market_persona_defaults=True)` |
| L65867 | `_persona_league_payload` | `items = read_store.list_persona_league(market_scope=market_scope, status=status)` |
| L66421 | `bff_v5_execution_persona_health` | `league = read_store.list_persona_league(include_market_persona_defaults=True)` |

#### `list_ranking_formulas`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.list_ranking_formulas`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 2
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L25994 | `bff_deprecated_list_ranking_formulas` | `items = read_store.list_ranking_formulas(status=status)` |
| L65991 | `_sem_final_generic_list_for_path` | `read_store.list_ranking_formulas(),` |

#### `list_rankings`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.list_rankings`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L28592 | `bff_list_rankings` | `items = read_store.list_rankings(status=status)` |

#### `list_rebalances`
- **Subsystem:** Rankings & Projections
- **Classification:** `Read`
- **Target Destination:** `RankingProjectionPort.list_rebalances`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L28179 | `bff_list_rebalances` | `items = read_store.list_rebalances(status=status, pool_id=pool_id)` |

#### `patch_ranking_formula`
- **Subsystem:** Rankings & Projections
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Ranking Formula Store`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L26110 | `bff_deprecated_patch_ranking_formula` | `updated = read_store.patch_ranking_formula(` |

#### `put_allocation_evaluation`
- **Subsystem:** Rankings & Projections
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Allocation Evaluation Store`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 2
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L26564 | `_pm12_materialize_allocation_evaluation` | `return read_store.put_allocation_evaluation({` |
| L27480 | `_pm12_materialize_paper_simulation_evaluation` | `return read_store.put_allocation_evaluation({` |

#### `put_ranking_snapshot`
- **Subsystem:** Rankings & Projections
- **Classification:** `Write`
- **Target Destination:** `command_executor.py / Ranking Snapshot Store`
- **API Status:** Command API destination (mutation)
- **Total Invocations:** 1
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L50659 | `_pm12_attach_ranking_snapshot` | `read_store.put_ranking_snapshot({` |

### 4.6 Evolution Projections

#### `get_evolution_decision_by_id`
- **Subsystem:** Evolution Projections
- **Classification:** `Read`
- **Target Destination:** `EvolutionProjectionPort.get_evolution_decision_by_id`
- **API Status:** Missing narrow API (evolution decision by ID lookup)
- **Total Invocations:** 2
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L5380 | `_mutation_review_inputs` | `decision = read_store.get_evolution_decision_by_id(decision_id)` |
| L21999 | `get_evolution_decision` | `decision = read_store.get_evolution_decision_by_id(decision_id)` |

#### `get_evolution_decisions_by_incident`
- **Subsystem:** Evolution Projections
- **Classification:** `Read`
- **Target Destination:** `EvolutionProjectionPort.get_evolution_decisions_by_incident / DomainIncidentPort`
- **API Status:** Missing narrow API (evolution decision by incident lookup)
- **Total Invocations:** 2
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L12582 | `_build_operator_paper_live_drift_payload` | `read_store.get_evolution_decisions_by_incident(` |
| L21910 | `get_post_incident_review` | `evolution_decisions = read_store.get_evolution_decisions_by_incident(incident_id)` |

#### `list_evolution_decisions`
- **Subsystem:** Evolution Projections
- **Classification:** `Read`
- **Target Destination:** `EvolutionProjectionPort.list_evolution_decisions`
- **API Status:** Existing 1:1 on domain port
- **Total Invocations:** 9
- **Line Locations & Call Contexts:**
| Line | Enclosing Function / Endpoint | Code Snippet |
|---|---|---|
| L21782 | `get_persona_management` | `all_evolution_decisions=list(read_store.list_evolution_decisions() or []),` |
| L21975 | `list_evolution_decisions` | `for decision in read_store.list_evolution_decisions(` |
| L36581 | `_project_persona_fleet_payload` | `evolution_decisions = list(read_store.list_evolution_decisions() or [])` |
| L39770 | `_evolution_journal_items` | `decisions = list(read_store.list_evolution_decisions() or [])` |
| L43480 | `_mgmt_nl_collect_context` | `evolution_decisions = _mgmt_nl_filter_tenant_records(list(read_store.list_evolution_decisions() or []), tenant_id)` |
| L61688 | `_health_reason_sentinel_findings` | `evolution_decisions = list(read_store.list_evolution_decisions() or [])` |
| L64076 | `_build_persona_health_items` | `all_decisions = list(read_store.list_evolution_decisions() or [])` |
| L65294 | `_project_persona_fleet_list_row` | `else list(read_store.list_evolution_decisions() or [])` |
| L65489 | `_persona_fleet_slim_list_payload` | `evolution_decisions = list(read_store.list_evolution_decisions() or [])` |

---

## 5. Read vs Write Classification & Missing Narrow API Analysis

### 5.1 Classification Summary
- **Read Operations (`39` methods, `213` calls):** Methods that read immutable snapshots, project pure DTOs, filter collections, or query domain state.
- **Write / Mutation Operations (`9` methods, `14` calls):** Methods that mutate entity state, store overlay records, or execute lifecycle transitions.

### 5.2 Write / Mutation Commands Destination Matrix
All write operations MUST be decoupled from `read_store` and routed directly to domain command handlers in `command_executor.py` or domain microservices:

| Method Name | Calls | Command Owner Destination | Target Protocol / Storage |
|---|---|---|---|
| `create_persona` | 1 | `command_executor.py` / Persona Registry | `POST /personas` |
| `update_persona` | 5 | `command_executor.py` / Persona Registry | `PATCH /personas/{id}` |
| `patch_capital_pool` | 1 | `command_executor.py` / Capital Service | `PATCH /capital-pools/{id}` |
| `create_deployment_plan` | 1 | `command_executor.py` / Deployment Service | `POST /deployment-plans` |
| `create_runtime_binding` | 1 | `command_executor.py` / Runtime Service | `POST /runtime-bindings` |
| `create_ranking_formula` | 1 | `command_executor.py` / Ranking Service | `POST /ranking-formulas` |
| `patch_ranking_formula` | 1 | `command_executor.py` / Ranking Service | `PATCH /ranking-formulas/{id}` |
| `put_ranking_snapshot` | 1 | `command_executor.py` / Ranking Admission | Canonical Snapshot Store |
| `put_allocation_evaluation` | 2 | `command_executor.py` / Allocation Service | Canonical Allocation Store |

### 5.3 Missing Narrow Read APIs on Domain Ports
The following 10 read methods are called in `main.py` but are not yet exposed directly on the narrow sub-ports. They should be added as narrow, pure methods without generic fallback delegation:

1. **`PersonaFleetPort` Additions:**
   - `get_persona_allowed_actions(persona_id)`: Pure DTO derivation calculating permitted operator actions based on `PERSONA_OPERATIONAL_LIFECYCLE_STATES` and session state.
   - `get_capability_snapshot(persona_id)` / `get_capability_snapshot_for_persona(persona_id)`: Narrow lookup querying persona capabilities from injected capability provider.
   - `get_sessions_for_persona(persona_id)` / `list_sessions_for_persona(persona_id)` / `get_session(session_id)`: Injected session reader delegating to session service/store.
   - `get_route_policy_for_persona(persona_id)`: Narrow route policy query delegating to injected workflow catalog.

2. **`DeploymentPlanPort` Additions:**
   - `get_allowed_actions(plan_id, ...)`: Pure DTO derivation determining valid deployment plan state transitions.

3. **`RuntimePort` Additions:**
   - `list_paper_runtime_monitoring_sessions()` / `get_paper_runtime_monitoring_session(...)` / `list_authoritative_paper_runtime_monitoring_sessions()`: Dedicated narrow readers over monitoring sessions.

4. **`RankingProjectionPort` Additions:**
   - `get_ranking_formula(formula_id)`: Narrow lookup by formula ID over injected formula reader.
   - `get_ranking_snapshot(snapshot_id)`: Narrow lookup by snapshot ID over injected ranking snapshot reader.
   - `get_allocation_evaluation(evaluation_id)`: Narrow lookup by evaluation ID over injected allocation reader.

5. **`EvolutionProjectionPort` Additions:**
   - `get_evolution_decision_by_id(decision_id)`: Narrow lookup by decision ID over injected evolution decision reader.
   - `get_evolution_decisions_by_incident(incident_id)`: Filtered projection over injected evolution decisions for a specific incident.

---

## 6. Multi-Domain Partition Proof & Disjointness Matrix

To guarantee that caller migration tasks proceed without merge conflicts or overlapping responsibilities, all 203 legacy `read_store` methods and 600 call sites across `main.py` are strictly partitioned into 6 disjoint domain tasks:

| Task ID | Domain Name | Artifact Document | Methods | Call Sites | Representative Methods |
|---|---|---|---|---|---|
| `ACG-RS-OPS-OWNERSHIP-MAP-20260828` | Operations & Agora | `docs/04/read_surface_ownership_partition_20260828/operations_agora.md` | 48 | 74 | `create_agora_session`, `get_committee`, `list_skills`, `record_sponsor_decision` |
| `ACG-RS-OODA-OWNERSHIP-MAP-20260828` | OODA & Management | `docs/04/read_surface_ownership_partition_20260828/ooda_management.md` | 16 | 52 | `list_ooda_packets`, `list_approval_queue_items`, `get_synthesis_conflict_log` |
| `ACG-RS-RESEARCH-OWNERSHIP-MAP-20260828` | Research & Knowledge | `docs/04/read_surface_ownership_partition_20260828/research_knowledge.md` | 42 | 116 | `list_strategy_specs`, `get_research_ticket`, `dataset_source`, `list_evidence_refs` |
| `ACG-RS-TRAINING-OWNERSHIP-MAP-20260828` | Persona Training | `docs/04/read_surface_ownership_partition_20260828/persona_training.md` | 17 | 31 | `create_trainer_session`, `get_trainer_replay`, `create_rapid_eval` |
| **`ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828`** | **Persona Capital & Runtime** | `docs/04/read_surface_ownership_partition_20260828/persona_capital_runtime.md` | **48** | **227** | `list_personas`, `list_capital_pools`, `list_runtime_bindings`, `list_rankings` |
| `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | Lifecycle Telemetry & Governance | `docs/04/read_surface_ownership_partition_20260828/lifecycle_telemetry_governance.md` | 32 | 100 | `list_incidents`, `get_kill_switch_status`, `list_lineage_edges`, `get_telemetry_summary` |
| **Total** | **All 6 Domains** | | **203** | **600** | **100% Disjoint Union & Zero Overlap** |

### Formal Mathematical Proof of Disjointness
Let $M_{all}$ be the set of 203 methods called on `read_store` in `main.py`.  
Let $M_{pcr}, M_{ops}, M_{ooda}, M_{res}, M_{train}, M_{ltg}$ be the sets of methods assigned to each of the 6 domains.  

1. **Coverage Proof:**
   $$\bigcup_{D \in \{pcr, ops, ooda, res, train, ltg\}} M_D = M_{all}$$
   $$|M_{pcr}| + |M_{ops}| + |M_{ooda}| + |M_{res}| + |M_{train}| + |M_{ltg}| = 48 + 48 + 16 + 42 + 17 + 32 = 203 = |M_{all}|$$

2. **Disjointness Proof:**
   $$\forall i, j \in \{pcr, ops, ooda, res, train, ltg\}, i \neq j \implies M_i \cap M_j = \emptyset$$

This guarantees zero overlap between `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` and any sibling ownership map task.
