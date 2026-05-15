# Claude Readout

## Lane

- Agent: Claude (Facilitator)
- Capability focus: Consensus synthesis, cited disagreement identification, human gate readiness assessment.

## Canonical Sources Read

- L0 (session):
  - `planning-session.json`
  - `README.md`
  - `starter-draft.md`
  - `consensus-packet.md`
- L1 (SA):
  - `SA-01_executive_summary.md`
  - `SA-11_operating_loop_gap_analysis.md`
  - `SA-13_contract_schema_gap_analysis.md`
  - `SA-20_v2_risk_register_corrected.md`
- L2 (SD):
  - `SD-P0-01_ADR_EXEC_Official_Pantheon_LEAN_Bridge.md`
  - `SD-P0-02_DeploymentPlan_to_RuntimeBootstrap_Contract.md`
  - `SD-P0-04_Paper_Runtime_TelemetryEvent_Contract.md`
  - `docs/04/SUPERVISOR_PLANNING_P0_NEXT_DEV_WORK.md`

## Working Interpretation

### Architecture summary

The SA/SD corpus converges on a single architectural truth: Pantheon is not proven until one closed loop is demonstrable end-to-end — `DeploymentPlan → RuntimeBinding → runtime_bootstrap → PantheonAlgoBase → paper TelemetryEvent → runtime projection`. The UI shell, BFF contract, and TelemetryEvent schema are sufficiently mature; the critical gap is in the cross-boundary integration: the paper runtime has no verified telemetry producer, and RuntimeBinding has no verified injection into runtime context. The official execution bridge is `pantheon/lean` submodule (`ajoe734/pantheon-lean.git`); `lean-platform` is not the current target.

**Evidence**: SA-01 §5 names "Runtime contract gap" and "Telemetry producer gap" as High severity; SA-11 §13 names the Promotion/Deployment and Telemetry/Evolution loops as "Critical" with the biggest gaps at DeploymentPlan → Lean runtime and Lean TelemetryEvent exporter; SA-13 §15 concludes that BFF Read Contract and TelemetryEvent schema are high maturity, but Runtime Launch Contract is "低 / 缺"; SA-20 R-ARCH-001 is Critical severity and directly points to the blueprint-vs-actual bridge drift.

### Delivery order

The 5-wave order in the starter-draft is architecturally sound and supported by all read sources:

1. **Wave 0 — Repo authority + CI guardrails**: Must precede everything. A Codex patch against the wrong repo would invalidate all downstream work. [SD-P0-01 §7 INV-EXEC-001 through INV-EXEC-007; SA-20 R-ARCH-001]
2. **Wave 1 — Runtime contract** (`DeploymentPlan → RuntimeBinding → RuntimeBootstrapRequest → PantheonRuntimeContext`): Enables testable launch path; blocks telemetry and reconciliation. [SD-P0-02 §3; SA-11 §9.4]
3. **Wave 2 — Paper telemetry producer + ingest/projection**: Proves runtime facts return to Pantheon with identity. [SD-P0-04; SA-11 §11.4]
4. **Wave 3 — Paper loop smoke + basic reconciliation**: Converts schemas into a minimum verifiable operating loop. [SA-11 §12.2 MVP; SUPERVISOR_PLANNING P0 Wave 3]
5. **Wave 5 — Front/BFF honesty cleanup**: Prevents operators reading demo/mock as production truth. [SA-20 R-FE-001, R-FE-002]

### Ownership boundaries

Per `planning-session.json` `lane_focus`:
- **Codex**: Grounds plan in repo evidence; materializes execution slices.
- **Codex2**: Audits schemas, object boundaries, contract formalization.
- **Claude** (this lane): Facilitates consensus; synthesizes cited disagreements; prepares human gate packet.
- **Claude2**: Reviews governance boundary, safety posture, human-gate readiness.
- **Gemini**: Stress-tests runtime/replay/tooling feasibility.
- **Copilot**: Pressure-tests research readiness and acceptance wording.

## Risks / Contradictions

### Risk 1 — RuntimeBinding context propagation is the load-bearing P0 invariant

`RuntimeBinding` is the pivot object: without it, telemetry cannot attribute events to a deployment, and reconciliation/incident/evolution cannot close. SA-13 §3.3 states: "RuntimeBinding 是整條 chain 的 pivot." SA-20 R-EXE-004 is Critical severity. The paper runtime may start today, but a heartbeat without `runtime_binding_id` in staging/prod must be rejected per SD-P0-04 INV-TEL-PAPER-003/010. This means P0-BOOT-001, P0-CTX-001/002, and P0-LEAN-CTX-001 are a gapless dependency chain — none can be accepted standalone.

**Facilitator note**: Cross-lane reviewers should verify whether the acceptance criteria in P0-CTX-001/002 and P0-LEAN-CTX-001 explicitly require the binding propagation path to be tested end-to-end before marking any one task complete.

### Risk 2 — "BFF read/command split" open disagreement has a documented root cause

The starter-draft marks as open whether `P0-BFF-CMD-001` is P0 or P1. SA-13 §4 provides a cited basis for keeping it P0: command paths (deploy, rollback, pause, liquidate) that lack idempotency_key, actor_ref, and audit are named risks (§4.4 Gap table). If commands reach the runtime without formal command contract, the paper loop's audit trail is incomplete. The starter-draft recommendation (keep P0) is defensible under SA-13 §4 and SD-P0-02 §6.1 which requires every runtime-affecting command to carry `command_id`, `actor_ref`, `idempotency_key`, `trace_id`, `reason`.

**Facilitator note**: This is a scope judgment, not an architecture contradiction. P0-BFF-CMD-001 can remain P0 if reviewers accept that the command contract coverage boundary is "commands that reach runtime" rather than "all BFF commands." If Codex2 or Copilot finds this too wide, we can narrow to the deployment/rollback/pause subset.

### Risk 3 — lean-platform orphan risk amplified by empty task board

SA-20 R-ARCH-003 flags that `lean-platform` is cloned but not integrated, and can still be mistaken as the target. The current task board is empty (0 active tasks; SUPERVISOR_PLANNING current_facts). Without CI guards (P0-CI-BRIDGE-001) materialized before any runtime work begins, there is non-zero risk that any agent or engineer could target the wrong repo. This makes P0-EXEC-ADR-001 → P0-CI-BRIDGE-001 the true unblocking dependency gate, not just a documentation step.

### Risk 4 — Bracket order log-only status must be explicit in paper telemetry contract

SA-20 R-EXE-003 and SD-P0-04 INV-TEL-PAPER-005 both specify that `bracket_order_logged` must not be confused with broker order submission. If the UI or an operator reads the paper telemetry projection and interprets the logged order as a submitted order, this creates a false safety posture. The acceptance criteria for P0-TEL-001 should explicitly validate that `bracket_order_logged` events are labeled and not co-mingled with fill/order events.

## Suggested Task Slices

The proposed task slices in the starter-draft and consensus-packet align with the SD task packets and are broadly correct. Claude lane observations:

- **Slice 1 — Preserve sequential gating between P0-EXEC-ADR-001 → P0-CI-BRIDGE-001 → P0-BOOT-001**: These three tasks form a repo-authority gate. Only after CI can assert the correct bridge should runtime code be modified. No exceptions.
  [SD-P0-01 §13 AC-001–006; SUPERVISOR_PLANNING Depends On column]

- **Slice 2 — P0-STATE-001 dependency clarification**: The supervisor plan lists P0-STATE-001 with no dependency. However, meaningful artifact/deployment invariant tests require schema definitions from P0-CTX-001 (PantheonRuntimeContext model) to exist. Recommend adding P0-CTX-001 as an implicit prerequisite or confirming P0-STATE-001 can be drafted as schema-independent tests with stubs. [SD-P0-02 §12; SUPERVISOR_PLANNING task table]

- **Slice 3 — Minimal reconciliation scope guard**: If basic reconciliation (P0-REC-001) is included in the first materialization wave, the acceptance criteria must explicitly cap it at "one paper run produces a ReconciliationRecord; threshold breach opens an IncidentCase; no automatic evolution action." SA-11 §12.3 item 9 states "EvolutionDecision 可 proposed，但不自動執行 live action." This guard should be on the face of P0-REC-001.
  [SA-11 §12.3; consensus-packet.md Open Questions #2]

- **Slice 4 — Frontend/BFF tasks can proceed independently if BFF contract is respected**: P0-FE-DEMO-001 and P0-FE-SOURCE-001 have no hard dependency on runtime contract tasks (per supervisor plan). They can proceed in parallel once paper telemetry projection exists (P0-TEL-PROJ-001 for source mode display). This is correct and no change needed.

## Citations

- [SA-01 §5 table] "Runtime contract gap" and "Telemetry producer gap" listed as High severity largest differences.
- [SA-01 §6] "Codex 可能在看『檔案存在』；SA 必須看『閉環是否可執行、可回放、可治理』."
- [SA-11 §13 Loop Gap Matrix] Promotion/Deployment loop: Critical, biggest gap = "DeploymentPlan → Lean runtime"; Telemetry/Evolution loop: Critical, biggest gap = "Lean telemetry exporter / reconciliation writer".
- [SA-11 §12.3] MVP verification items: RuntimeBinding_id in Lean heartbeat, heartbeat visible in BFF runtime summary, ReconciliationRecord from one paper run, threshold breach opens IncidentCase, EvolutionDecision proposed-only.
- [SA-13 §3.3] "RuntimeBinding 是整條 chain 的 pivot."
- [SA-13 §4.4] Command API gap table: Read API / Command API not layered, command idempotency unconfirmed, command RBAC incomplete.
- [SA-13 §15] BFF Read Contract: High maturity. Runtime Launch Contract: Low / missing. Lean Runtime Producer Contract: Low / unverified.
- [SA-20 R-ARCH-001] Critical: blueprint says lean-platform, actual bridge is pantheon/lean — "Certain likelihood, Critical impact."
- [SA-20 R-ARCH-003] High: lean-platform orphan risk — "High likelihood."
- [SA-20 R-EXE-001] Critical: live role is health-only but may be mistaken as live-ready.
- [SA-20 R-EXE-003] High: bracket order log-only — UI/operator may misread as submitted.
- [SA-20 R-EXE-004] Critical: RuntimeBinding propagation unverified.
- [SA-20 R-TEL-001] High: TelemetryEvent schema exists, paper producer unverified.
- [SD-P0-01 §7] INV-EXEC-001: "P0 execution tasks MUST target pantheon/lean unless ADR-EXEC-001 is revised."
- [SD-P0-01 §7] INV-EXEC-005: "live role MUST remain fail-closed until explicit live activation criteria are approved."
- [SD-P0-02 §6.1] Command contract: every runtime-affecting command must include command_id, actor_ref, idempotency_key, trace_id, reason.
- [SD-P0-02 §8] INV-BOOT-001/002/010: live not started by default; bracket order must remain logged_only.
- [SD-P0-04 INV-TEL-PAPER-003/005/010] binding_id required for managed paper runtime events; bracket_order_logged must not be confused with broker submission; missing binding fields in staging/prod must be rejected.
- [SUPERVISOR_PLANNING Hard Invariants] "No broker secret appears in frontend, artifact payload, launch manifest, telemetry, or OpenClaw memory."
- [SUPERVISOR_PLANNING P0 Wave Order] Wave 0 first reason: "Prevent work from landing in the wrong repo or enabling live accidentally."
