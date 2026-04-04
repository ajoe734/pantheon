# OpenClaw Target Architecture

Last updated: 2026-04-01 (v1.1 Refined)
Status: target-state architecture for the governed OpenClaw evolution platform

## 1. Core Principle

OpenClaw should orchestrate workflows.
The evolution plane should learn from trader feedback, telemetry, and research.
LEAN should only execute approved policy artifacts.

Important interpretation rule:

when this document names a real upstream OSS project, that name means an external framework or repo to integrate unless we explicitly say we are replacing it locally.

The system must never allow a persona to rewrite live behavior directly because of short-term PnL swings.
All strategy or model changes must pass through:

`discover -> normalize -> replicate -> approve -> candidate -> paper -> live`

## 2. Responsibility Split

### OpenClaw

OpenClaw is the process and governance layer.

It is responsible for:

- personas
- tool and skill selection
- allowlist and denylist controls
- cron-driven ingestion, review, retraining, and deployment workflows
- human approvals
- policy and operational boundaries

OpenClaw is not the learning framework and is not the execution engine.

### Evolution Plane

The evolution plane is where feedback becomes controlled improvement.

It owns:

- trader feedback capture
- trading telemetry capture
- web research ingestion
- trajectory and preference storage
- evaluators and critics
- optimizers
- strategy and model registry
- promotion state for candidate, paper, and live

### Research / Learning

這層負責產出或改進 Policy。我們將「可學習物件」拆分為三類，各司其職：

1. **Persona Policy**: 負責任務解讀、工具選擇與論文寫作。
   - 工具：**DSPy** (結構化優化)、**TRL** (人類偏好回饋 SFT/DPO)。
   - 核心：不輕易 fine-tune，先透過優化 prompt 與 weights 來演化。
2. **Alpha Policy**: 負責信號、特徵、標籤與投組規則。
   - 工具：**Qlib** (Meta-controller/Task Mgmt)、**FinRL/RLlib** (Sequential RL)。
3. **Trader Imitation**: 負責學習人類交易員的操作風格。
   - 工具：**imitation** (BC, DAgger)。
   - 核心：將人類軌跡轉為 state -> action -> outcome。

### Execution

Execution should stay narrow and controlled.

It owns:

- signal and policy artifacts
- promotion-aware artifact loading
- LEAN runtime execution
- broker interaction

Execution consumes approved artifacts.
It does not discover research, optimize prompts, or self-modify live strategies.

## 3. Target Flow

### A. Research and Learning Intake

1. OpenClaw cron or approved tool workflows ingest papers, repos, and notes.
2. Material is normalized into a `StrategySpec`, dataset reference, or experiment proposal.
3. First-pass replication is run in research frameworks.
4. Only successful candidates are registered.

### B. Evaluation and Registry

1. Research outputs are evaluated against explicit metrics.
2. Evaluators and critics produce scores, failure notes, and rationale.
3. Optimizers generate updated prompts, hyperparameters, or policies.
4. Versioned outputs are written to the strategy and model registry.

### C. Promotion Gate

Every promoted unit must move through explicit stages:

1. `candidate`
2. `paper`
3. `live`

Promotion rules:

- candidate requires replication success
- paper requires risk and operational review
- live requires approval and rollback metadata

### D. Live Execution

1. Registry publishes the approved artifact or signal snapshot.
2. Signal or policy artifacts are loaded by LEAN.
3. LEAN executes with portfolio, risk, and broker logic.
4. Telemetry returns to the evolution plane.

## 4. Learning Objects

### Persona Policy

Definition:

- how a persona interprets tasks
- how it chooses tools
- how it gathers evidence
- how it writes thesis or operational output

Preferred frameworks:

- DSPy for structured program optimization
- TRL when trader approve/edit/reject feedback must become preference learning
- LangGraph only if stronger checkpointing, pause, resume, or HITL orchestration becomes necessary

### Alpha Policy

Definition:

- signals
- factors
- labels
- parameters
- portfolio rules

Preferred frameworks:

- Qlib for supervised alpha and experiment management
- FinRL or RLlib for sequential decision policies
- Ray Tune for search and hyperparameter optimization

### Trader Imitation

Definition:

- learning how a human trader acts from state, features, actions, and outcomes

Preferred frameworks:

- imitation for BC first
- DAgger when iterative human correction is needed
- GAIL or AIRL when behavior distribution matters
- TRL or DPO only for text preference layers, not direct order imitation

## 5. Registry and Artifact Model

The registry is a first-class system, not an afterthought.

It must version:

- strategy specs
- model artifacts
- feature sets
- prompts or DSPy programs
- evaluation results
- promotion state
- rollback targets

Minimum registry states:

- `draft`
- `candidate`
- `paper`
- `live`
- `retired`

Minimum registry metadata:

- version
- lineage
- data sources
- training or optimization run id
- evaluator summary
- approver
- promoted_at
- rollback_target

## 6. Governance Requirements

The governed architecture depends on explicit controls.

Required controls:

- personas cannot directly mutate live strategy
- tool permissions must be constrained by allowlist and denylist
- cron jobs must be separated from live execution permissions
- deployment must require promotion state checks
- live strategy swaps must be reversible
- all high-risk transitions require audit records

## 7. GCP and Runtime View

Target runtime split:

- OpenClaw services on GCP managed services or containers
- research and training jobs on containerized workers
- registry backed by a versioned artifact system
- LEAN runtime deployed separately as execution workers
- telemetry and feedback stored independently from execution state

Deployment principle:

the same artifact promoted in the registry is the artifact executed by LEAN

## 8. What This Means for the Current Repo

The current repo has only the foundation of this architecture.

The named OSS boxes in this document are not just labels.
Most of them represent upstream projects that still need real dependency or repo integration work.

Already present:

- collaboration operating system
- signal-store contract
- signal schema work
- control-plane skeletons
- dashboard and status tracking

Still missing:

- evolution plane
- registry
- promotion gate
- feedback store
- evaluator and optimizer layer
- DSPy or imitation integration
- LEAN artifact loader and paper/live separation
- upstream OpenClaw integration
- upstream research and learning framework integration for the named OSS tools

## 8.1 Upstream OSS Interpretation

Treat these as upstream integrations by default:

- `OpenClaw`
- `DSPy`
- `TRL`
- `Qlib`
- `FinRL`
- `RLlib`
- `Ray Tune`
- `imitation`
- `MLflow`
- `W&B`

Local work in this repo should focus on:

- adapters
- contracts
- config
- governance wrappers
- packaging
- smoke tests

It should not silently drift into accidental re-implementation of those frameworks.

## 9. Non-Negotiable Guardrail

OpenClaw may discover, critique, optimize, and recommend.
It may not directly self-edit live behavior outside the registry and promotion gate.
