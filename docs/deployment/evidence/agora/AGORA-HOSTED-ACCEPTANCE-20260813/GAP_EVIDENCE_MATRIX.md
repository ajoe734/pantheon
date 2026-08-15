# Agora Gap-by-Gap Evidence Matrix

Task: `AGORA-HOSTED-ACCEPTANCE-20260813`  
Status: **PASSED**  
Verified At: `2026-08-15T08:07:16Z`

| Gap ID | Domain | Description | Status | Gate | Evidence |
|---|---|---|---|---|---|
| `S01` | Identity & Scope | Authenticate and receive a private Agora/servant context | **RESOLVED** | `gate_03_agora_product_journey` | Operator identity & capability scope enforced; tenant isolation verified. |
| `S02` | Strategy Workshop | Create Workshop from UI with a strategy hypothesis | **RESOLVED** | `gate_03_agora_product_journey` | Authoritative Workshop creation and async hypothesis reconstruction verified. |
| `S03` | Strategy Reconstruction | Converse and receive strategy reconstruction, assumptions, completeness & NBQ | **RESOLVED** | `gate_03_agora_product_journey` | Server-side reconstruction worker materializes StrategySpec structure with deterministic completeness. |
| `S04` | Workshop Cards | Review typed Workshop cards and act on plan/version/consultation cards | **RESOLVED** | `gate_03_agora_product_journey` | WorkshopCardRenderer and typed card event dispatch wired to canonical BFF endpoints. |
| `S05` | Strategy Spec | Produce, compare, and select an immutable StrategySpec draft | **RESOLVED** | `gate_03_agora_product_journey` | Immutable StrategySpec versioning with CAS optimistic lock revision tracking. |
| `S06` | Governed Research | Approve and run real governed research with progress and artifacts | **RESOLVED** | `gate_03_agora_product_journey` | Governed research plan, worker lease dispatcher, and artifact verification. |
| `S07` | Candidate Pool | Build a real candidate pool from strategy/research evidence | **RESOLVED** | `gate_03_agora_product_journey` | Real candidate pool generation from research artifacts without fixture fallbacks. |
| `S08` | Trading Room Workspace | Generate a strategy-specific, live-data Trading Room workspace | **RESOLVED** | `gate_03_agora_product_journey` | Typed WorkspaceIntent, WorkspaceCompiler, widget adapters, and atomic workspace versioning. |
| `S09` | Candidate Drawer | Review/research/shadow/park candidates and read canonical state | **RESOLVED** | `gate_03_agora_product_journey` | BFF-wired Candidate Drawer with canonical state transitions and lens filtering. |
| `S10` | Decision Event & Intent | Receive a real decision event and create a governed intent/handoff (no broker orders) | **RESOLVED** | `gate_03_agora_product_journey` | Decision event projection and request-only TradingIntent with absolute zero broker authority. |
| `S11` | Strategy Performance | Observe real owner-scoped strategy performance and act on governed suggestions | **RESOLVED** | `gate_03_agora_product_journey` | Owner-scoped StrategyPerformanceIndex and governed performance suggestions. |
| `S12` | Dataset Extraction | Extract eligible Agora interaction evidence into tenant-safe datasets | **RESOLVED** | `gate_03_agora_product_journey` | Dataset extraction outbox, DatasetVersion production, and policy-learning handoff. |
| `S13` | Policy Candidate | Train/evaluate a policy candidate asynchronously from the dataset | **RESOLVED** | `gate_03_agora_product_journey` | Admit-only policy candidate registration, offline worker processing, and fail-closed promotion. |
| `S14` | Consultation Governance | Obtain independent Consultation review and sponsor decision | **RESOLVED** | `gate_03_agora_product_journey` | Independent Consultation review workflow (evaluator != producer, sponsor decision). |
| `S15` | Hosted Exact Pair | Use the whole journey on a currently accepted hosted exact pair | **RESOLVED** | `gate_01_manifest_exact_pair` | Exact FE/BFF pair verified, manifest drift check passed, /readyz healthy. |
| `GAP-W01` | Workshop UI Creation | UI exposes normal Workshop creation flow | **RESOLVED** | `gate_03_agora_product_journey` | Workshop creation flow wired to POST /bff/agora/workshops. |
| `GAP-W02` | Workshop Composer | Composer calls Workshop reconstruction rather than Persona daily interaction | **RESOLVED** | `gate_03_agora_product_journey` | Composer routes messages to Workshop reconstruction engine. |
| `GAP-W03` | Workshop Cards | Typed Workshop cards rendered and active in UI | **RESOLVED** | `gate_03_agora_product_journey` | WorkshopCardRenderer active and integrated with canonical state. |
| `GAP-W04` | Server Completeness | Completeness calculated deterministically on server, client writes rejected | **RESOLVED** | `gate_03_agora_product_journey` | Server-side StrategyCompletenessCalculator enforces deterministic evaluation. |
