# OpenClaw Roadmap

Last updated: 2026-04-01
Status: epic roadmap aligned to `TARGET_ARCHITECTURE.md`

<!-- CODEX REVIEW NOTES (2026-04-01, from Claude architecture review)
Four corrections are embedded in this file. Search for [CODEX NOTE] to find each one.
Summary:
  1. EX-001 exit criteria: require LEAN Object Store as artifact transport mechanism (Epic A)
  2. OC-001 exit criteria: O→L direct tool call must be restricted to paper/backtest only (Epic B)
  3. RS-001 exit criteria: ingestion must use structured APIs, not web scraping (Epic E)
  4. v1.5 scope: move imitation (LP-002) from v2 to v1.5 — it is more foundational than TRL (§5)
-->

## 1. Delivery Principle

We should build this system in layers:

1. lock transport and execution boundaries
2. make OpenClaw orchestration real
3. add registry and promotion gates
4. add feedback and evolution loops
5. add learning frameworks only where they clearly fit

This keeps the system governed from day one and avoids a persona directly mutating live trading logic.

## 1.1 Upstream OSS Integration Rule

Several boxes in the architecture are named upstream OSS projects, not abstract placeholders.

By default, these should be treated as external integrations:

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

That means the task is not complete when we only have a local contract or schema.

For those components, "done" should eventually imply:

1. upstream dependency or repo is selected and pinned
2. local adapter boundary is implemented
3. governed I/O is defined
4. smoke tests prove the integration path works

Important correction:

`OpenClaw` work in this roadmap should be interpreted as **integration/config/adaptation against upstream OpenClaw**, not greenfield replacement of the orchestration layer, unless we explicitly decide to replace it.

## 2. Current State

Completed or near-complete:

- collaboration operating system
- dashboard and machine-readable task board
- signal store contract
- signal schema v1 review

Not yet complete:

- LEAN signal consumer
- control-plane routing contract
- strategy and model registry
- promotion gate
- feedback store
- evaluator and optimizer layer
- DSPy, imitation, TRL, and registry integrations
- upstream OpenClaw integration
- upstream research-framework integrations for the named OSS tools

## 3. Epic Sequence

### Epic A: Execution Boundary

Goal:

- make approved signal artifacts executable without skipping governance

Tasks:

- `P2-002` Sync schema docs and examples to the locked execution schema
- `P3-001` Wire LEAN runtime signal consumer
- `P4-001` Draft control-plane routing contract
- `EX-001` Define artifact loader contract for paper and live execution

Exit criteria:

- human-facing docs and machine-facing schema describe the same execution contract
- LEAN can consume approved signal artifacts
- control plane can hand execution requests into governed runtime
- paper and live execution paths are clearly separated
- [CODEX NOTE — EX-001] artifact loader must use LEAN Object Store (or equivalent LEAN-native
  mechanism such as `ObjectStore.Save` / `ObjectStore.Read`) as the transport between registry
  and LEAN runtime. Direct file injection or GCS reads that bypass LEAN's data provider layer
  are not acceptable. Rationale: Object Store is the official organization-shared mechanism for
  passing artifacts between research, backtest, and live in QuantConnect.

### Epic B: OpenClaw Orchestration

Goal:

- turn the current skeleton into a real workflow orchestrator

Interpretation correction:

- these tasks should define and implement how upstream OpenClaw is configured, constrained, and mapped into this repo's governed contracts
- they should not be read as "rewrite OpenClaw from scratch inside Lean"

Tasks:

- `OC-001` Map upstream OpenClaw tool permissions into the local allowlist and denylist model
- `OC-002` Integrate or configure upstream OpenClaw cron workflows for ingest, review, retrain, and deploy
- `OC-003` Map upstream OpenClaw strategy outputs into `StrategySpec` and workflow handoff objects

Exit criteria:

- personas can only use approved tools
- scheduled workflows exist for research and review
- orchestration artifacts are explicit and versionable
- [CODEX NOTE — OC-001] the architecture diagram has a direct edge O (Tools/Skills/Cron) → L (LEAN).
  This edge can bypass the REG → SIG → L promotion path and is a governance vulnerability.
  OC-001 must define that any tool capable of calling LEAN directly is restricted to
  paper/backtest context only. Live execution must always flow through REG → SIG → L.
  Add this as an explicit rule in the tool permission model and denylist contract.

### Epic C: Registry and Promotion Gate

Goal:

- force all strategy evolution through versioned lifecycle control

Tasks:

- `REG-001` Define strategy and model registry contract
- `REG-002` Implement candidate, paper, and live promotion gate
- `REG-003` Add rollback and lineage requirements to promoted artifacts

Exit criteria:

- every promoted strategy has version, lineage, approval, and rollback metadata
- live execution can only load `paper` or `live` approved artifacts
- personas cannot bypass promotion checks

### Epic D: Feedback and Evolution Plane

Goal:

- capture the signals needed for controlled improvement

Tasks:

- `FB-001` Define trajectory and preference store schema
- `FB-002` Capture trader approve, edit, reject, and rationale events
- `FB-003` Capture execution telemetry including pnl, drawdown, slippage, and fills
- `EV-001` Define evaluator and critic contracts
- `EV-002` Define optimizer outputs and links back to registry artifacts

Exit criteria:

- trader feedback and telemetry are persisted in a form usable for learning
- evaluators and optimizers emit governed artifacts instead of mutating live runtime directly

### Epic E: Research Ingestion and Replication

Goal:

- let OpenClaw discover and test ideas without letting raw research touch live

Tasks:

- `RS-001` Integrate governed research ingestion adapters for papers, repos, and notes
- `RS-002` Normalize discovered material into `StrategySpec`
- `RS-003` Implement the first-pass replication gate before registry admission

Exit criteria:

- discovered material becomes normalized, reviewable work units
- only replicated candidates reach the registry
- [CODEX NOTE — RS-001] ingestion must use structured APIs as data sources, not general web scraping.
  Required: OpenAlex API for academic papers, GitHub REST API + repository contents endpoints for
  code repos and notes. Web scraping introduces maintenance fragility and unpredictable content shape.
  Add this as an explicit constraint in RS-001 acceptance criteria and ingestion architecture doc.

### Epic F: Learning Framework Integration

Goal:

- add the right framework to the right learning object

Interpretation correction:

- these tasks target real upstream frameworks and should include packaging/integration work, not only abstract design

Tasks:

- `LP-001` Integrate upstream DSPy for persona policy optimization
- `LP-002` Integrate upstream imitation for trader behavior cloning workflows
- `LP-003` Integrate MLflow or W&B as the experiment lifecycle backend
- `LP-004` Integrate governed TRL preference-learning workflows only after feedback controls exist
- `LP-005` Integrate FinRL or RLlib plus Tune only if sequential RL becomes a primary path

Exit criteria:

- persona policy, alpha policy, and trader imitation are not forced through one mismatched stack
- experiment artifacts are versioned and linked to promotion lifecycle
- each named OSS framework has a selected upstream source, pinned version, local adapter path, and smoke-test plan

## 4. Ownership Proposal

### Claude

Primary focus:

- `P2-001` review
- `P3-001`
- `P4-001`

Why:

- execution-critical work and high-context review are Claude's strongest fit
- Claude should stay on the critical path, but carry materially less total ownership than Codex and Gemini

### Gemini

Primary focus:

- `P2-001`
- `EX-001`
- `OC-002`
- `REG-002`
- `FB-003`
- `EV-001`
- `RS-001`
- `RS-003`
- `LP-003`
- `LP-004`
- `LP-005`

Why:

- runtime packaging, deployment, telemetry, artifact movement, and operational workflow ownership fit Gemini's lane

### Codex

Primary focus:

- `P1-001`
- `P2-002`
- `OC-001`
- `OC-003`
- `REG-001`
- `REG-003`
- `FB-001`
- `FB-002`
- `RS-002`
- `LP-001`
- `LP-002`

Why:

- contracts, schemas, registry interfaces, integration boundaries, and cross-plane object models fit Codex's lane

## 5. Versioned Delivery Plan

### v1

Target stack:

- OpenClaw
- signal store
- schema v1
- LEAN
- basic control plane
- strategy and model registry
- candidate and paper promotion gate

Meaning:

- governed artifact execution exists
- live strategy changes are controlled

### v1.5

Target additions:

- feedback store
- evaluator and optimizer contracts
- DSPy
- imitation workflows  <!-- [CODEX NOTE] moved from v2: imitation (BC from trader trajectories) is
                            more fundamental than TRL. You need demonstrated behavior before you can
                            do preference ranking. DSPy + imitation + MLflow should ship together.
                            LP-002 depends_on FB-001+RS-002 which both land in v1.5 anyway. -->
- MLflow or W&B
- tracing and evaluation tooling

Meaning:

- the system can start learning from feedback without bypassing governance
- persona policy and trader behavior imitation are both operational

### v2

Target additions:

- TRL preference loops  <!-- moved from v1.5: TRL requires imitation baseline to be meaningful -->
- FinRL or RLlib plus Tune when justified

Meaning:

- the platform supports preference learning on top of imitation baseline
- sequential RL is available if it becomes a primary path

## 6. Immediate Next Moves

1. land `P2-002` so `schema.json`, examples, and schema docs become one contract
2. complete `P3-001` and `P4-001`
3. start `OC-001` and a first `REG-001` skeleton in parallel
4. lock `REG-001` after `OC-003` handoff objects are defined
5. define `FB-001`

Those steps move the repo from collaboration foundation into the first governed product slice without stalling registry work behind avoidable serial dependencies.
