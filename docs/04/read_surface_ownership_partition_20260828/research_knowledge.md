# Research & Knowledge Domain: Read-Surface Ownership Partition Inventory

- **Task ID:** `ACG-RS-RESEARCH-OWNERSHIP-MAP-20260828`
- **Phase:** Read-surface ownership partition
- **Owner:** `Antigravity`
- **Reviewer:** `Codex2`
- **Target Repository:** `ajoe734/pantheon`
- **Delivery Scope:** `docs/04/read_surface_ownership_partition_20260828/research_knowledge.md`
- **Production Code Status:** Zero modifications to `services/control-plane/bff/` production source (strictly documentation & ownership mapping).

---

## 1. Executive Summary & Global Baseline

This document establishes the authoritative ownership mapping and partition boundary for the **Research, Knowledge, Memory, Search, and Source** domain across the Pantheon BFF read surface.

### 1.1 Executable Global BFF Baseline
To provide complete architectural context across `services/control-plane/bff/main.py`, AST analysis establishes the executable global baseline across all domains:
- **Direct `read_store` Member References:** Exactly **598** AST member attribute references (`ast.Attribute` where `node.value.id == "read_store"`).
- **Distinct Member Names:** Exactly **202** distinct member attribute names accessed across `read_store`.
- **Direct Call Expressions:** Exactly **595** direct AST `Call` expressions (`read_store.<method>(...)`).
- **Callable Non-Call References:** Exactly **3** instances where `read_store.<attr>` is referenced as a callable object without immediate call syntax (lines 25370, 45027, 45051).
- **Lexical vs. Executable Scope:** Lexical regex analysis (`read_store\.[a-zA-Z0-9_]+`) yields 600 occurrences across 203 names; the delta (2 occurrences / 1 name) represents comment/docstring-only coverage.

### 1.2 Research Domain Partition Objective & Scope
As part of the legacy `ReadSurfaceStore` decoupling and migration to typed domain ports, this inventory:
1. Catalogs every member call and access in `services/control-plane/bff/main.py` belonging to Research, Knowledge Workbench, Institutional Memory, Search, and Source Ingestion.
2. Classifies each call as a **Read** (query) or **Write** (mutation/command) operation and identifies its target typed domain port or command-owner service destination:
   - **82 Total Read Operations:** 68 direct/attribute read references (67 direct Calls + 1 callable reference at line 45027), 1 legacy `getattr` read at line 23754, and 13 research `dataset_source` Calls.
   - **6 Write Operations:** State-mutating commands directly served by `DefaultResearchKnowledgeSourcePort`.
   - **88 Total Domain Operations** across `main.py`.
3. Evaluates narrow domain API coverage and confirms 100% interface parity with `ResearchKnowledgeSourcePort` without introducing generic delegation, compatibility fallback layers, or product code modifications.
4. Accounts for all static `read_store.dataset_source(...)` access sites (37 total across `main.py`), categorizing the 13 research-owned sites and explicitly partitioning the 24 excluded cross-domain metadata calls.
5. Classifies both dynamic `getattr(read_store, ...)` access sites in `main.py`:
   - Line 23754: legacy domain compatibility lookup `getattr(read_store, "get_insight_card", None)`.
   - Line 62356: generic cross-domain dynamic lookup `getattr(read_store, "dataset_source", None)` in SEM helper `_sem_read_records`.
6. Demonstrates strict non-overlap and mutual exclusion against the other 5 domain ownership partitions.

---

## 2. Six-Domain Partition Taxonomy & Non-Overlap Proof

The Pantheon BFF read surface is partitioned into 6 disjoint domain areas. Each method of `ReadSurfaceStore` is assigned to exactly one domain port:

| Domain Partition | Owning Port Interface | Port Class | Method Count | Task Ownership |
|---|---|---|---|---|
| **Research, Knowledge & Source** | `ResearchKnowledgeSourcePort` | `DefaultResearchKnowledgeSourcePort` | **39** | `ACG-RS-RESEARCH-OWNERSHIP-MAP-20260828` *(This Task)* |
| **Persona Training & Evaluation** | `PersonaTrainingDomainPort` | `TrainingSessionTrainerPort`, `RapidEvaluationPort` | **20** | `ACG-RS-TRAINING-OWNERSHIP-MAP-20260828` |
| **Persona, Capital & Runtime** | `PersonaCapitalRuntimeDomainPort` | `PersonaFleetPort`, `CapitalPoolPort`, `DeploymentPlanPort`, `RuntimePort`, `RankingProjectionPort`, `EvolutionProjectionPort` | **30** | `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` |
| **Lifecycle, Telemetry & Governance** | `CompositeLifecycleTelemetryGovernancePort` | `IncidentReaderPort`, `LifecycleReaderPort`, `GovernanceReaderPort`, `LineageReaderPort`, `TelemetryReaderPort` | **37** | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` |
| **Operations & Consultation** | `OperationsConsultationPort` | `WorkflowHookCatalogReaderPort`, `DomainWorkflowCatalogPort`, `OpenClawOperationsReaderPort`, `ConsultationReaderPort` | **27** | `ACG-RS-OPERATIONS-OWNERSHIP-MAP-20260828` |
| **OODA & Management** | `OodaManagementDomainPort` | `OodaPacketsPort`, `InterventionsPort`, `SynthesisConflictLogsPort`, `ManagementReviewQueuePort` | **12** | `ACG-RS-OODA-OWNERSHIP-MAP-20260828` |

### Disjointness & Non-Overlap Guarantee
- **Zero Method Collisions:** Every domain method in `ResearchKnowledgeSourcePort` is exclusively owned by the Research/Knowledge domain.
- **Shared Dataset Metadata Accounting:** Static code analysis identifies **37 total calls** to `read_store.dataset_source(...)` in `main.py`. Of these, **13 calls** query research/knowledge datasets and are owned by this task (§5.1). The remaining **24 calls** belong to companion domain partitions and are formally excluded and accounted for in §5.2.
- **Boundary Clarifications:**
  - `get_research_oss_preactivation_snapshot`: Owned by `OperationsConsultationPort` (`OpenClawOperationsReaderPort`) because it queries dormant OSS runtime containers and dispatch gates, not research artifact data.
  - `artifact_exists`: Owned by `CompositeLifecycleTelemetryGovernancePort` (`TelemetryReaderPort`) because it checks physical telemetry artifact storage existence on disk.
  - Agora committee and session messages (`get_agora_session`, `list_agora_signals`, etc.): Owned by `PersonaTrainingDomainPort` / `OperationsConsultationPort` / `PersonaCapitalRuntimeDomainPort`.

---

## 3. `ResearchKnowledgeSourcePort` Method Inventory (39 Methods)

The domain port `ResearchKnowledgeSourcePort` in `services/control-plane/bff/domain_ports/research_knowledge_source.py` (re-exported via `services/control-plane/bff/ports/research_knowledge_source.py`) defines 39 typed methods across 7 functional subdomains:

### A. Knowledge & Evidence (KW-02, KW-03, KW-04, KW-05) - 14 Methods
1. `list_research_notes() -> List[Dict[str, Any]]`
2. `get_research_note(note_id: Optional[str]) -> Optional[Dict[str, Any]]`
3. `create_research_note(note: Dict[str, Any]) -> Optional[Dict[str, Any]]`
4. `list_evidence_refs(...) -> List[Dict[str, Any]]`
5. `get_evidence_ref(ref_id: Optional[str]) -> Optional[Dict[str, Any]]`
6. `get_evidence_ref_detail(ref_id: Optional[str]) -> Optional[Dict[str, Any]]`
7. `list_insight_cards() -> List[Dict[str, Any]]`
8. `get_insight_card(insight_id: Optional[str]) -> Optional[Dict[str, Any]]`
9. `get_insight_card_detail(insight_id: Optional[str]) -> Optional[Dict[str, Any]]`
10. `list_strategy_specs(...) -> List[Dict[str, Any]]`
11. `get_strategy_spec(strategy_id: Optional[str]) -> Optional[Dict[str, Any]]`
12. `get_strategy_spec_detail(strategy_id: Optional[str], *, version_selector: Optional[str]) -> Optional[Dict[str, Any]]`
13. `list_strategy_spec_versions(strategy_id: Optional[str]) -> List[Dict[str, Any]]`
14. `compare_strategy_spec_versions(strategy_id: Optional[str], *, left_selector: str, right_selector: str) -> Optional[Dict[str, Any]]`

### B. Institutional Memory - 2 Methods
15. `list_institutional_memory_entries() -> List[Dict[str, Any]]`
16. `get_institutional_memory_entry(entry_id: Optional[str]) -> Optional[Dict[str, Any]]`

### C. Research Tickets (RW-01) - 4 Methods
17. `list_research_tickets(*, statuses: Optional[List[str]], owner: Optional[str], include_fixture_pack: bool) -> List[Dict[str, Any]]`
18. `get_research_ticket(ticket_id: Optional[str]) -> Optional[Dict[str, Any]]`
19. `create_research_ticket(*, title: str, description: str, priority: str, owner: str, actor_id: str, created_at: Optional[str]) -> Dict[str, Any]`
20. `patch_research_ticket(ticket_id: str, *, patch: Dict[str, Any], actor_id: str, updated_at: Optional[str]) -> Optional[Dict[str, Any]]`

### D. Research Analyses, Experiments & Artifacts (RW-03, RW-04, RW-05) - 9 Methods
21. `list_research_analyses(*, ticket_id: Optional[str], experiment_id: Optional[str], statuses: Optional[List[str]], date_range: Optional[str]) -> List[Dict[str, Any]]`
22. `get_research_analysis(analysis_id: Optional[str]) -> Optional[Dict[str, Any]]`
23. `list_research_experiments(*, ticket_id: Optional[str], status: Optional[str]) -> List[Dict[str, Any]]`
24. `get_research_experiment(experiment_id: Optional[str]) -> Optional[Dict[str, Any]]`
25. `create_research_experiment(*, ticket_id: str, experiment_name: str, strategy_selector: Dict[str, Any], parameter_set: Dict[str, Any], run_config: Dict[str, Any], launch_context: Dict[str, Any], queued_at: Optional[str]) -> Dict[str, Any]`
26. `cancel_research_experiment(experiment_id: str, *, completed_at: Optional[str]) -> Optional[Dict[str, Any]]`
27. `list_research_artifacts(*, artifact_type: Optional[str], status: Optional[str], tags: Optional[List[str]], author: Optional[str], date_range: Optional[str]) -> List[Dict[str, Any]]`
28. `get_research_artifact(artifact_id: Optional[str]) -> Optional[Dict[str, Any]]`
29. `compare_research_artifacts(artifact_ids: List[str]) -> Dict[str, Any]`

### E. Search & Governed Search (RW-02) - 4 Methods
30. `get_research_search_index() -> Optional[Dict[str, Any]]`
31. `list_research_search_results(*, query: str, match_type: str, status: Optional[str], date_range: Optional[str]) -> List[Dict[str, Any]]`
32. `get_last_governed_search_refs() -> Dict[str, Dict[str, Any]]`
33. `get_search_ops_snapshot(*, pipeline_run_limit: int) -> Dict[str, Any]`

### F. Source Ingestion & Connectors - 4 Methods
34. `get_source_connector_registry() -> Dict[str, Any]`
35. `get_source_change_proposals(*, status: Optional[str], proposal_type: Optional[str], source_kind: Optional[str]) -> Dict[str, Any]`
36. `get_source_ops_snapshot(*, crawl_run_limit: int, dlq_status: Optional[str], frontier_status: Optional[str], audit_limit: int) -> Dict[str, Any]`
37. `get_source_health_usage_snapshot() -> Dict[str, Any]`

### G. Surface Metadata & Dataset Status - 2 Methods
38. `dataset_source(dataset: str) -> str`
39. `dataset_surface_status(dataset: str, *, snapshot_at: str, source: Optional[str], has_data: bool, missing_message: Optional[str]) -> Dict[str, Any]`

---

## 4. Comprehensive Inventory of `main.py` Call Sites (75 Member Access Sites)

An AST scan of `services/control-plane/bff/main.py` identifies **75 member access sites** accessing non-metadata methods belonging to the Research, Knowledge, Memory, Search, and Source domain:
- **74 direct/attribute member accesses** (`read_store.<attr>`):
  - **68 read references:** 67 direct `Call` expressions (`read_store.<method>(...)`) and 1 callable reference at `main.py:45027` (`read_store.list_evidence_refs` passed to `asyncio.to_thread`).
  - **6 write calls:** state-mutating member calls (`create_research_ticket`, `patch_research_ticket`, `create_research_experiment`, `cancel_research_experiment`, `create_research_note`).
- **1 legacy `getattr` read access** at `main.py:23754` (`getattr(read_store, "get_insight_card", None)` in `_agora_get_insight`).

Across these 75 sites, 37 distinct method names on `ResearchKnowledgeSourcePort` are accessed (covering 37 of the 39 methods on `ResearchKnowledgeSourcePort`; together with the 13 research-owned `dataset_source` calls in §5.1, 38 of 39 port methods are accessed, leaving only `dataset_surface_status` unused in `main.py`).

| Line # | Member Method | Enclosing Function | Route / Context | Type | Target Domain Port / Command Destination |
|---|---|---|---|---|---|
| 11598 | `list_evidence_refs` | `_build_management_evidence_payload` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.list_evidence_refs` |
| 13465 | `get_research_ticket` | `_kw02_resolve_attachment_target` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_research_ticket` |
| 13474 | `get_strategy_spec` | `_kw02_resolve_attachment_target` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec` |
| 13490 | `get_institutional_memory_entry` | `_kw02_validate_memory_anchors` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_institutional_memory_entry` |
| 13573 | `get_evidence_ref` | `_kw02_resolve_evidence_links` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_evidence_ref` |
| 13608 | `get_institutional_memory_entry` | `_kw02_resolve_memory_anchors` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_institutional_memory_entry` |
| 18895 | `get_source_ops_snapshot` | `get_source_ops` | `GET /api/v1/operator/source/ops` | **Read** | `ResearchKnowledgeSourcePort.get_source_ops_snapshot` |
| 18926 | `get_search_ops_snapshot` | `get_search_ops` | `GET /api/v1/operator/search/ops` | **Read** | `ResearchKnowledgeSourcePort.get_search_ops_snapshot` |
| 19100 | `create_research_ticket` | `create_research_ticket` | `POST /api/v1/research/tickets` | **Write** | `DefaultResearchKnowledgeSourcePort.create_research_ticket (services/control-plane/bff/domain_ports/research_knowledge_source.py:1751)` |
| 19132 | `list_research_tickets` | `list_research_tickets` | `GET /api/v1/research/tickets` | **Read** | `ResearchKnowledgeSourcePort.list_research_tickets` |
| 19168 | `get_research_ticket` | `get_research_ticket` | `GET /api/v1/research/tickets/{ticket_id}` | **Read** | `ResearchKnowledgeSourcePort.get_research_ticket` |
| 19209 | `get_research_ticket` | `patch_research_ticket` | `PATCH /api/v1/research/tickets/{ticket_id}` | **Read** | `ResearchKnowledgeSourcePort.get_research_ticket` |
| 19219 | `patch_research_ticket` | `patch_research_ticket` | `PATCH /api/v1/research/tickets/{ticket_id}` | **Write** | `DefaultResearchKnowledgeSourcePort.patch_research_ticket (services/control-plane/bff/domain_ports/research_knowledge_source.py:1791)` |
| 19263 | `get_research_search_index` | `search_research_corpus` | `GET /api/v1/research/search` | **Read** | `ResearchKnowledgeSourcePort.get_research_search_index` |
| 19274 | `list_research_search_results` | `search_research_corpus` | `GET /api/v1/research/search` | **Read** | `ResearchKnowledgeSourcePort.list_research_search_results` |
| 19312 | `get_last_governed_search_refs` | `search_research_corpus` | `GET /api/v1/research/search` | **Read** | `ResearchKnowledgeSourcePort.get_last_governed_search_refs` |
| 19333 | `get_source_connector_registry` | `list_source_connectors` | `GET /api/v1/research/source-connectors` | **Read** | `ResearchKnowledgeSourcePort.get_source_connector_registry` |
| 19366 | `get_source_change_proposals` | `list_source_change_proposals` | `GET /api/v1/research/source-change-proposals` | **Read** | `ResearchKnowledgeSourcePort.get_source_change_proposals` |
| 19407 | `list_research_analyses` | `list_research_analysis` | `GET /api/v1/research/analysis` | **Read** | `ResearchKnowledgeSourcePort.list_research_analyses` |
| 19464 | `get_research_analysis` | `get_research_analysis` | `GET /api/v1/research/analysis/{analysis_id}` | **Read** | `ResearchKnowledgeSourcePort.get_research_analysis` |
| 19654 | `create_research_experiment` | `launch_experiment` | `POST /api/v1/experiments/launch` | **Write** | `DefaultResearchKnowledgeSourcePort.create_research_experiment (services/control-plane/bff/domain_ports/research_knowledge_source.py:2041)` |
| 19693 | `list_research_experiments` | `api_v1_list_experiments` | `GET /api/v1/experiments` | **Read** | `ResearchKnowledgeSourcePort.list_research_experiments` |
| 19740 | `get_research_experiment` | `api_v1_get_experiment` | `GET /api/v1/experiments/{experiment_id}` | **Read** | `ResearchKnowledgeSourcePort.get_research_experiment` |
| 19785 | `get_research_experiment` | `cancel_experiment` | `POST /api/v1/experiments/{experiment_id}/cancel` | **Read** | `ResearchKnowledgeSourcePort.get_research_experiment` |
| 19803 | `cancel_research_experiment` | `cancel_experiment` | `POST /api/v1/experiments/{experiment_id}/cancel` | **Write** | `DefaultResearchKnowledgeSourcePort.cancel_research_experiment (services/control-plane/bff/domain_ports/research_knowledge_source.py:2079)` |
| 19835 | `list_research_artifacts` | `list_artifacts` | `GET /api/v1/artifacts` | **Read** | `ResearchKnowledgeSourcePort.list_research_artifacts` |
| 19893 | `get_research_artifact` | `compare_artifacts` | `GET /api/v1/artifacts/compare` | **Read** | `ResearchKnowledgeSourcePort.get_research_artifact` |
| 19925 | `compare_research_artifacts` | `compare_artifacts` | `GET /api/v1/artifacts/compare` | **Read** | `ResearchKnowledgeSourcePort.compare_research_artifacts` |
| 19942 | `get_research_artifact` | `get_artifact` | `GET /api/v1/artifacts/{artifact_id}` | **Read** | `ResearchKnowledgeSourcePort.get_research_artifact` |
| 20024 | `create_research_note` | `create_research_note` | `POST /api/v1/knowledge/notes` | **Write** | `DefaultResearchKnowledgeSourcePort.create_research_note (services/control-plane/bff/domain_ports/research_knowledge_source.py:499)` |
| 20070 | `list_research_notes` | `list_research_notes` | `GET /api/v1/knowledge/notes` | **Read** | `ResearchKnowledgeSourcePort.list_research_notes` |
| 20133 | `get_research_note` | `get_research_note_detail` | `GET /api/v1/knowledge/notes/{note_id}` | **Read** | `ResearchKnowledgeSourcePort.get_research_note` |
| 20210 | `list_evidence_refs` | `list_evidence_refs` | `GET /api/v1/knowledge/evidence` | **Read** | `ResearchKnowledgeSourcePort.list_evidence_refs` |
| 20304 | `get_evidence_ref_detail` | `get_evidence_ref_detail` | `GET /api/v1/knowledge/evidence/{ref_id}` | **Read** | `ResearchKnowledgeSourcePort.get_evidence_ref_detail` |
| 20432 | `list_insight_cards` | `list_insight_cards` | `GET /api/v1/knowledge/insights` | **Read** | `ResearchKnowledgeSourcePort.list_insight_cards` |
| 20544 | `get_insight_card_detail` | `get_insight_card_detail` | `GET /api/v1/knowledge/insights/{insight_id}` | **Read** | `ResearchKnowledgeSourcePort.get_insight_card_detail` |
| 20613 | `list_strategy_specs` | `list_strategy_specs` | `GET /api/v1/knowledge/strategy-specs` | **Read** | `ResearchKnowledgeSourcePort.list_strategy_specs` |
| 20658 | `get_strategy_spec` | `get_strategy_spec_detail` | `GET /api/v1/knowledge/strategy-specs/{strategy_id}` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec` |
| 20667 | `get_strategy_spec_detail` | `get_strategy_spec_detail` | `GET /api/v1/knowledge/strategy-specs/{strategy_id}` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec_detail` |
| 20726 | `list_strategy_spec_versions` | `list_strategy_spec_versions` | `GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions` | **Read** | `ResearchKnowledgeSourcePort.list_strategy_spec_versions` |
| 20727 | `get_strategy_spec` | `list_strategy_spec_versions` | `GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec` |
| 20779 | `get_strategy_spec_detail` | `compare_strategy_spec_versions` | `GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec_detail` |
| 20780 | `get_strategy_spec_detail` | `compare_strategy_spec_versions` | `GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec_detail` |
| 20799 | `compare_strategy_spec_versions` | `compare_strategy_spec_versions` | `GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare` | **Read** | `ResearchKnowledgeSourcePort.compare_strategy_spec_versions` |
| 20867 | `list_institutional_memory_entries` | `list_institutional_memory` | `GET /api/v1/knowledge/memory` | **Read** | `ResearchKnowledgeSourcePort.list_institutional_memory_entries` |
| 20919 | `get_institutional_memory_entry` | `get_institutional_memory_entry` | `GET /api/v1/knowledge/memory/{entry_id}` | **Read** | `ResearchKnowledgeSourcePort.get_institutional_memory_entry` |
| 23754 | `get_insight_card` | `_agora_get_insight` | `(internal helper; legacy getattr)` | **Read** | `ResearchKnowledgeSourcePort.get_insight_card` |
| 23892 | `list_research_tickets` | `bff_agora_daily` | `GET /bff/agora/daily` | **Read** | `ResearchKnowledgeSourcePort.list_research_tickets` |
| 24729 | `list_research_tickets` | `bff_agora_research_tasks` | `GET /bff/agora/research-tasks, GET /bff/research/tasks` | **Read** | `ResearchKnowledgeSourcePort.list_research_tickets` |
| 30892 | `get_strategy_spec_detail` | `_strategy_routed_persona_count` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec_detail` |
| 30902 | `list_strategy_specs` | `_routed_strategies_for_persona` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.list_strategy_specs` |
| 30908 | `list_strategy_specs` | `_list_strategy_summaries` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.list_strategy_specs` |
| 31050 | `list_strategy_specs` | `_list_strategy_spec_match_candidates` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.list_strategy_specs` |
| 31058 | `get_strategy_spec_detail` | `_list_strategy_spec_match_candidates` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec_detail` |
| 31301 | `create_research_ticket` | `_persona_strategy_match_action_response` | `(internal helper)` | **Write** | `DefaultResearchKnowledgeSourcePort.create_research_ticket (services/control-plane/bff/domain_ports/research_knowledge_source.py:1751)` |
| 45027 | `list_evidence_refs` | `bff_management_nl_ask` | `POST /bff/management/nl/ask` (callable ref in `asyncio.to_thread`) | **Read** | `ResearchKnowledgeSourcePort.list_evidence_refs` |
| 45988 | `get_strategy_spec_detail` | `bff_list_strategies` | `GET /bff/strategies` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec_detail` |
| 46075 | `get_strategy_spec` | `bff_get_strategy` | `GET /bff/strategies/{strategy_id}` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec` |
| 46083 | `get_strategy_spec_detail` | `bff_get_strategy` | `GET /bff/strategies/{strategy_id}` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec_detail` |
| 46113 | `get_strategy_spec` | `bff_patch_strategy` | `PATCH /bff/strategies/{strategy_id}` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec` |
| 46124 | `get_strategy_spec_detail` | `bff_patch_strategy` | `PATCH /bff/strategies/{strategy_id}` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec_detail` |
| 46146 | `get_strategy_spec` | `_ensure_strategy_exists` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_strategy_spec` |
| 46165 | `list_strategy_spec_versions` | `bff_list_strategy_specs` | `GET /bff/strategies/{strategy_id}/specs` | **Read** | `ResearchKnowledgeSourcePort.list_strategy_spec_versions` |
| 46234 | `list_research_experiments` | `bff_list_strategy_experiments` | `GET /bff/strategies/{strategy_id}/experiments` | **Read** | `ResearchKnowledgeSourcePort.list_research_experiments` |
| 46257 | `list_research_artifacts` | `bff_list_strategy_artifacts` | `GET /bff/strategies/{strategy_id}/artifacts` | **Read** | `ResearchKnowledgeSourcePort.list_research_artifacts` |
| 49844 | `list_evidence_refs` | `_pm12_public_quarter_evidence_refs` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.list_evidence_refs` |
| 62336 | `list_research_artifacts` | `bff_list_artifacts` | `GET /bff/artifacts` | **Read** | `ResearchKnowledgeSourcePort.list_research_artifacts` |
| 63506 | `get_source_connector_registry` | `_source_ingest_truth_by_connector` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_source_connector_registry` |
| 63517 | `get_source_health_usage_snapshot` | `_source_ingest_truth_by_connector` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_source_health_usage_snapshot` |
| 65971 | `list_research_artifacts` | `_sem_final_generic_list_for_path` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.list_research_artifacts` |
| 66006 | `list_research_analyses` | `_sem_final_generic_list_for_path` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.list_research_analyses` |
| 66114 | `list_strategy_specs` | `_sem_final_generic_list_for_path` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.list_strategy_specs` |
| 66145 | `get_research_artifact` | `_sem_final_generic_detail_for_path` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_research_artifact` |
| 66194 | `get_research_analysis` | `_sem_final_generic_detail_for_path` | `(internal helper)` | **Read** | `ResearchKnowledgeSourcePort.get_research_analysis` |
| 66454 | `list_strategy_specs` | `bff_v5_execution_strategy_health` | `GET /bff/v5/execution/strategy-health` | **Read** | `ResearchKnowledgeSourcePort.list_strategy_specs` |

---

## 5. Domain Dataset Surface Metadata Call Sites & Non-Overlap Partition Accounting (37 Total Calls)

Static code analysis of `services/control-plane/bff/main.py` reveals exactly **37 direct calls** to `read_store.dataset_source(...)`. To prove complete domain disjointness and non-overlap, these 37 calls are partitioned into:
- **13 Research-Owned Calls** (§5.1) querying Research, Knowledge, Memory, and Strategy datasets.
- **24 Excluded Cross-Domain Metadata Calls** (§5.2) querying datasets belonging to companion domain partitions.

### 5.1 Research & Knowledge Domain Dataset Call Sites (13 Call Sites)

| Line # | Dataset Queried | Enclosing Function | Purpose / Surface State |
|---|---|---|---|
| 11600 | `evidence_refs` | `_build_management_evidence_payload` | Management console evidence table degraded/unavailable envelope |
| 19402 | `research_analyses` | `list_research_analysis` | Research analysis list endpoint source status check |
| 19459 | `research_analyses` | `get_research_analysis` | Research analysis detail endpoint source status check |
| 19698 | `research_experiments` | `api_v1_list_experiments` | Experiment list endpoint source status check |
| 20071 | `research_notes` | `list_research_notes` | Research notes list endpoint source status check |
| 20211 | `evidence_refs` | `list_evidence_refs` | Evidence refs list endpoint source status check |
| 20433 | `insight_cards` | `list_insight_cards` | Insight cards list endpoint source status check |
| 20620 | `strategy_specs` | `list_strategy_specs` | Strategy specs list endpoint source status check |
| 20833 | *(dynamic KW dataset)* | `_kw01_surface_state` | Dynamic surface check for KW-01 through KW-05 datasets |
| 31065 | `strategy_specs` | `_list_strategy_spec_match_candidates` | Strategy matching candidate selection source check |
| 31073 | `strategy_specs` | `_list_strategy_spec_match_candidates` | Strategy matching candidate fallback source check |
| 49845 | `evidence_refs` | `_pm12_public_quarter_evidence_refs` | PM12 portfolio book evidence ref source verification |
| 60409 | `research_experiments` | `_research_experiments_surface_source` | Research experiment surface source resolution |

### 5.2 Excluded Cross-Domain Metadata Call Sites & Non-Overlap Partition Mapping (24 Call Sites)

The 24 non-research `read_store.dataset_source(...)` call sites belong exclusively to companion ownership partitions:

| Line # | Dataset Queried | Enclosing Function | Owning Domain Partition | Companion Task ID | Owning Port Interface / Subsystem |
|---|---|---|---|---|---|
| 5119 | `evolution_decisions` | `_mutation_review_surface_state` | Persona, Capital & Runtime | `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` | `EvolutionProjectionPort` |
| 5142 | `dataset` *(evolution/ranking/deploy)* | `_mutation_review_surface_state` | Persona, Capital & Runtime | `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` | `EvolutionProjectionPort` / `DeploymentPlanPort` |
| 7726 | `loop_runs` | `_loop_run_truth_source` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `LineageReaderPort` / `LifecycleReaderPort` |
| 7729 | `incidents` | `_loop_run_truth_source` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 7769 | `dataset` *(generic helper)* | `_dataset_surface_status` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `CompositeLifecycleTelemetryGovernancePort` |
| 7862 | `dataset` *(generic helper)* | `_dataset_source_after_read` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `CompositeLifecycleTelemetryGovernancePort` |
| 10920 | `incidents` | `_build_management_anomalies_payload` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 11155 | `incidents` | `_build_management_sentinel_pulse_response` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 12834 | `inspiration_graphs` | `_ew04_inspiration_surface_state` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `LineageReaderPort` |
| 12901 | `lineage_edges` | `_ew04_inspiration_projection_from_lineage_edges` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `LineageReaderPort` |
| 21183 | `deployment_diffs` | `get_deployment_diff` | Persona, Capital & Runtime | `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` | `DeploymentPlanPort` |
| 25632 | `capital_allocations` | `bff_list_capital_pools` | Persona, Capital & Runtime | `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` | `CapitalPoolPort` |
| 25860 | `capital_allocations` | `bff_get_capital_pool` | Persona, Capital & Runtime | `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` | `CapitalPoolPort` |
| 46583 | `synthesis_conflict_logs` | `bff_get_synthesis_conflict_log` | OODA & Management | `ACG-RS-OODA-OWNERSHIP-MAP-20260828` | `SynthesisConflictLogsPort` |
| 46651 | `ooda_packets` | `bff_get_ooda_packet` | OODA & Management | `ACG-RS-OODA-OWNERSHIP-MAP-20260828` | `OodaPacketsPort` |
| 61790 | `incidents` | `bff_v5_sentinel_findings_list` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 62315 | `incidents` | `bff_get_sentinel_finding` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 63309 | `ooda_packets` | `_build_ooda_control_room_status_card` | OODA & Management | `ACG-RS-OODA-OWNERSHIP-MAP-20260828` | `OodaPacketsPort` |
| 66029 | `incidents` | `_sem_final_generic_list_for_path` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 66036 | `incidents` | `_sem_final_generic_list_for_path` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 66222 | `incidents` | `_sem_final_generic_detail_for_path` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 66353 | `incidents` | `bff_v5_control_room` | Lifecycle, Telemetry & Governance | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | `IncidentReaderPort` |
| 66625 | `approval_decisions` | `bff_approvals_decide` | Operations & Consultation | `ACG-RS-OPERATIONS-OWNERSHIP-MAP-20260828` | `GovernanceReaderPort` / `WorkflowHookCatalog` |
| 66792 | `approval_decisions` | `bff_approvals_batch_decide` | Operations & Consultation | `ACG-RS-OPERATIONS-OWNERSHIP-MAP-20260828` | `GovernanceReaderPort` / `WorkflowHookCatalog` |

### 5.3 Complete `dataset_source` Partition Summary

| Domain Partition | Assigned Task ID | Call Count | Partition Status |
|---|---|---|---|
| **Research, Knowledge & Source** | `ACG-RS-RESEARCH-OWNERSHIP-MAP-20260828` | **13** | **Owned (This Task)** |
| **Lifecycle, Telemetry & Governance** | `ACG-RS-LIFECYCLE-OWNERSHIP-MAP-20260828` | **14** | Excluded / Non-Overlap |
| **Persona, Capital & Runtime** | `ACG-RS-CAPITAL-OWNERSHIP-MAP-20260828` | **5** | Excluded / Non-Overlap |
| **OODA & Management** | `ACG-RS-OODA-OWNERSHIP-MAP-20260828` | **3** | Excluded / Non-Overlap |
| **Operations & Consultation** | `ACG-RS-OPERATIONS-OWNERSHIP-MAP-20260828` | **2** | Excluded / Non-Overlap |
| **Persona Training & Evaluation** | `ACG-RS-TRAINING-OWNERSHIP-MAP-20260828` | **0** | Excluded / Non-Overlap |
| **Total `dataset_source` Calls in `main.py`** | | **37** | **100% Accounted For** |

---

## 6. Read vs Write Classification & Verified Owner Mapping

The domain operations across `main.py` are strictly partitioned into 82 Read operations and 6 Write operations (88 total domain interaction sites):

### A. Read Operations (68 Direct/Attribute Read References + 1 Legacy getattr Read + 13 Research Dataset Source Calls = 82 Total Read Sites)
- **Classification:** Strict Queries (Idempotent, Side-Effect Free).
- **Read Subtotals:**
  - **68 Direct/Attribute Read References:** 67 direct AST `Call` expressions (`read_store.<method>(...)`) plus 1 callable reference at `main.py:45027` (`read_store.list_evidence_refs` passed as a callable target to `asyncio.to_thread`).
  - **1 Legacy `getattr` Read Reference:** Line 23754 (`getattr(read_store, "get_insight_card", None)` in `_agora_get_insight`).
  - **13 Research-Owned `dataset_source` Calls:** Line locations detailed in §5.1.
  - **Total Reads = 68 + 1 + 13 = 82.**
- **Resolution Strategy:** Directly served by `ResearchKnowledgeSourcePort` methods.
- **Underlying Authoritative Stores & Port Implementation:**
  - `evidence_refs`: `services/knowledge/evidence/repository.py` (`JsonlEvidenceRepository` / `InMemoryEvidenceRepository`).
  - `institutional_memory_entries`: `services/memory/institutional_memory_store.py` (`InstitutionalMemoryStore`).
  - `search_ops` / `research_search_results`: `services/search/gateway.py` (`SearchGateway`) and `services/search/index_store.py` (`JsonlSearchIndexStore`).
  - `data_sources` / `source_connectors`: `services/source_ingestion/registry/data_source_registry.py` (`DataSourceRegistry`).
  - `research_tickets`, `research_analyses`, `research_experiments`, `research_artifacts`, `research_notes`, `insight_cards`, `strategy_specs`: Served directly by typed stores and state projection in `DefaultResearchKnowledgeSourcePort` (`services/control-plane/bff/domain_ports/research_knowledge_source.py`).

### B. Write Operations (6 Member Calls)
The 6 write call sites in `main.py` perform state mutations:
1. `create_research_ticket` (Line 19100): Endpoint `POST /api/v1/research/tickets`. Creates a new research ticket.
2. `patch_research_ticket` (Line 19219): Endpoint `PATCH /api/v1/research/tickets/{ticket_id}`. Modifies an existing ticket.
3. `create_research_experiment` (Line 19654): Endpoint `POST /api/v1/experiments/launch`. Queues/launches an experiment run.
4. `cancel_research_experiment` (Line 19803): Endpoint `POST /api/v1/experiments/{experiment_id}/cancel`. Cancels a queued/running experiment.
5. `create_research_note` (Line 20024): Endpoint `POST /api/v1/knowledge/notes`. Appends a new research note.
6. `create_research_ticket` (Line 31301): Internal action helper `_persona_strategy_match_action_response`. Auto-creates a ticket upon persona match.

**Verified Implementation & Owner Destination:**
- At the current repository head, these write operations are implemented directly via the in-memory mutation methods of `DefaultResearchKnowledgeSourcePort` in `services/control-plane/bff/domain_ports/research_knowledge_source.py` (re-exported at `services/control-plane/bff/ports/research_knowledge_source.py`):
  - `create_research_ticket`: `DefaultResearchKnowledgeSourcePort.create_research_ticket` (`services/control-plane/bff/domain_ports/research_knowledge_source.py:1751`)
  - `patch_research_ticket`: `DefaultResearchKnowledgeSourcePort.patch_research_ticket` (`services/control-plane/bff/domain_ports/research_knowledge_source.py:1791`)
  - `create_research_experiment`: `DefaultResearchKnowledgeSourcePort.create_research_experiment` (`services/control-plane/bff/domain_ports/research_knowledge_source.py:2041`)
  - `cancel_research_experiment`: `DefaultResearchKnowledgeSourcePort.cancel_research_experiment` (`services/control-plane/bff/domain_ports/research_knowledge_source.py:2079`)
  - `create_research_note`: `DefaultResearchKnowledgeSourcePort.create_research_note` (`services/control-plane/bff/domain_ports/research_knowledge_source.py:499`)
- There are no separate backend command services defined at this head; all verified mutation capabilities reside within `DefaultResearchKnowledgeSourcePort`.

---

## 7. Narrow Domain API Gap Analysis & Dynamic Access Classification

### 7.1 Verification Findings & Complete Interface Coverage
1. **Zero Missing Domain APIs:** Every one of the 37 distinct non-metadata member methods (and 38 total accessed methods including `dataset_source`) called in `services/control-plane/bff/main.py` is fully implemented and tested on `ResearchKnowledgeSourcePort` and `DefaultResearchKnowledgeSourcePort`. Across the 39 methods defined in `ResearchKnowledgeSourcePort`:
   - 37 non-metadata domain methods are actively accessed across 75 member sites in `main.py` (74 direct/attribute call sites and 1 legacy `getattr` access site).
   - 1 shared metadata method (`dataset_source`) is actively accessed across 13 research-owned call sites in `main.py` (out of 37 total static `read_store.dataset_source` sites across all domains).
   - 1 method (`dataset_surface_status`) is defined on the port interface for structured dataset status reporting but is currently not directly invoked in `main.py`.
2. **Strict Type Safety:** All signatures return typed DTO dictionaries or lists adhering to OpenAPI schema requirements.

### 7.2 Classification of Dynamic `getattr(read_store, ...)` Access Sites in `main.py`
Static inspection of all `getattr(read_store, ...)` patterns across `main.py` identifies two distinct sites relevant to research operations and metadata:

1. **Domain-Specific Legacy Compatibility Access (`main.py:23754`):**
   - **Code:** `getattr(read_store, "get_insight_card", None)` in `_agora_get_insight`.
   - **Classification:** Domain-specific read query on `insight_cards`.
   - **Resolution:** The typed port method `ResearchKnowledgeSourcePort.get_insight_card(insight_id: Optional[str]) -> Optional[Dict[str, Any]]` exists, is fully defined in the port contract, and is tested with 100% pass rate in `DefaultResearchKnowledgeSourcePort`. During port cutover, this legacy `getattr` pattern can be replaced directly with `port.get_insight_card(insight_id)`.

2. **Generic Dynamic Cross-Domain SEM Helper (`main.py:62356`):**
   - **Code:** `source_fn = getattr(read_store, "dataset_source", None)` in `_sem_read_records(dataset: str)`.
   - **Classification:** Generic cross-domain SEM dataset dispatch / fallback reader helper (infrastructure utility, not domain-specific business logic).
   - **Resolution:** When SEM endpoints query research datasets (such as `research_artifacts` or `research_analyses`), `dataset_source` is dynamically evaluated. `ResearchKnowledgeSourcePort` already implements `dataset_source`, ensuring full compatibility when `read_store` is replaced by domain ports. Generic reflection will be cleanly retired upon unified port container cutover.

---

## 8. Test & Verification Evidence

The port implementation and cutover verification were validated via automated test suites:
- `pytest services/control-plane/bff/tests/test_research_knowledge_source_ports.py`: **15 passed** (100% test pass rate for Research, Knowledge, Memory, Search, and Source domain ports).
- `pytest services/control-plane/bff/tests/test_*port*.py`: **140 passed** (100% test pass rate across all 6 BFF domain port test suites).
- AST analysis confirms zero production code edits in `services/control-plane/bff/main.py` or other production source files during this task.

---

## 9. Conclusion & Next Steps

This inventory provides the immutable baseline for the upcoming `main.py` port cutover phase:
- Callers in `main.py` can be refactored to consume `ports.research_knowledge_source` or the unified `read_surface_ports` container directly.
- The boundary between Read queries and Command writes is clearly delineated (82 reads vs 6 writes).
- Mutual exclusion with all companion ownership maps (`ACG-RS-TRAINING-*`, `ACG-RS-CAPITAL-*`, `ACG-RS-LIFECYCLE-*`, `ACG-RS-OPERATIONS-*`, `ACG-RS-OODA-*`) is formally established.
