# Evolution Generative Loop Gap - 2026-07-14

Status: gap specification and execution source of truth

Owner: Human/Ops (spec), fleet (execution)

Scope:

- `services/evolution/dispatch_worker.py` (exists, never deployed)
- `services/research-orchestrator/`, `services/training-session/`, `services/optimizer-svc/` (deployed, never fed)
- `services/registry/`, `services/deployment/` (promote pipeline)
- `services/telemetry/` (real PnL / drawdown supply)
- paper runtime binding contract (`pantheon-paper-runtime`, RuntimeBinding store)
- signal production (`/home/lupin/paper-loop/feed_signals*.sh`, `tw_signal_producer.py` host crons — to be superseded per-binding by strategy-driven signals)
- `services/evolution/config/threshold_sweep_baselines.json` (governance-approved baselines)

Related packets:

- `docs/04/pantheon_evolution_journal_producer_gap_2026-07-13/EVOLUTION_JOURNAL_PRODUCER_GAP.md`
  (EVOCHAIN: the **observation half** — trades → telemetry → threshold → incident →
  sweep → proposal → journal. This packet closes the **generative half**.)
- `.orchestrator/task-briefs/evochain_001_upstream_decision.md`
  (2026-07-14 ruling: telemetry performance supply is upstream work, folded into
  this packet as EVOLOOP-002; do NOT create a separate EVOCHAIN-012.)

Execution packet:

- `docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/INDEX.md`

## Problem Statement

The full OODA loop this system is designed around is circular:

```text
evolution decision (approve/execute)
  -> research plane retrains / mutates
  -> new strategy artifact (versioned, with lineage)
  -> promote: registry -> deployment plan -> LEAN paper runtime binding
  -> strategy generates signals -> trades
  -> performance telemetry -> threshold breach -> incident -> sweep
  -> evolution proposal -> (back to top)
```

EVOCHAIN (2026-07-13) closed the observation half. Live verification on
2026-07-14 shows the generative half has **never fired once**:

- The paper trades that exist are NOT produced by evolved persona strategies.
  All 14 live bindings run placeholder artifacts (`paper-artifact-persona-*`
  rescue placeholders, `plan-*-rescue-*` plans); signals come from three host
  cron scripts (`feed_signals.sh` every minute, `feed_signals_l1.sh`,
  `tw_signal_producer.py` daily). Personas are name-tags on bindings, not
  strategy authors.
- No evolution has ever produced a strategy: zero real artifacts in the
  registry lineage, zero retrains executed, zero promote runs.

## Root Causes (verified 2026-07-14)

1. **evolution-dispatch-worker is not deployed.**
   `services/evolution/dispatch_worker.py` (LOOP-AUTO-EVO-004) polls approved
   EvolutionDecisions and dispatches each through the gated execute path to
   the research plane. It has tests and a contract — and no docker-compose
   service. Approved decisions park at `approved` forever.
2. **The research plane is healthy but starved.**
   `research-orchestrator-svc`, `training-session-svc`, `optimizer-svc` all
   run and pass health checks; because (1) is missing, no retrain work item
   has ever been created.
3. **No evolvable strategy artifact exists.**
   Persona bindings reference placeholder artifact ids with no LEAN algorithm
   content behind them. There is no defined "minimal evolvable strategy"
   artifact contract (algo template + parameter set) for a retrain to mutate.
4. **The promote pipeline has never run.**
   Current bindings were hand-restored after the June binding-store wipe.
   registry → deployment plan → runtime binding replacement is unexercised.
5. **Performance telemetry cannot feed thresholds.**
   All 14 runtime summaries carry `drawdown=None`; nothing computes or emits
   `drawdown_snapshot` events. All 14 carry `pnl=0.0` — including a binding
   with 7,325 executed trades — so the PnL supply (mark-to-market) is
   effectively dead. `threshold_sweep_baselines.json` ships empty by design
   and no artifact has an approved `expected_drawdown`.

## Sponsor Decisions (2026-07-14)

1. EVOLOOP-002 (telemetry performance supply) **replaces** the previously
   discussed EVOCHAIN-012 — one owner for PnL mark-to-market fix + drawdown
   events, since both derive from the same fills+marks computation.
2. The first evolved artifact may be a **minimal parameter mutation** of a
   real but simple strategy (e.g. the TW momentum logic already proven by
   `tw_signal_producer.py`) — the goal is a genuine end-to-end cycle, not
   alpha quality.
3. Host-cron signal feeders stay for the bindings they serve; EVOLOOP-007
   moves ONE binding to strategy-driven signals as the proof, it does not
   rip out the feeders fleet-wide.
4. Threshold values and baselines remain live config; `rolling_pnl_floor`
   gets enabled only after EVOLOOP-002 makes PnL real (calibration recorded
   in the baselines/thresholds file with policy_source).

## Target Contract (per stage)

### EVOLOOP-001 — deploy the dispatch worker

- compose service (default-on for dev, own interval env, fail-closed) running
  `services/evolution/dispatch_worker.py` against the evolution service API.
- Evidence: an approved decision transitions to `executed` with dispatch
  metadata, without human curl.

### EVOLOOP-002 — real performance telemetry supply

- Paper runtime side computes per-binding rolling PnL (mark-to-market from
  fills + market data already ingested via source-ingest) and rolling
  drawdown; emits schema-valid `pnl_snapshot` / `drawdown_snapshot` telemetry
  events with as-of stamps.
- Root-cause and fix the `pnl=0.0` supply; runtime summaries show numeric,
  moving `pnl` and `drawdown` for active bindings.
- Fail-closed: no marks → no snapshot + diagnostic (never fabricate).

### EVOLOOP-003 — minimal evolvable strategy artifact

- Define the strategy artifact contract: LEAN-compatible algorithm reference +
  named parameter set + version + lineage fields, registered in registry.
- Produce ONE genuine v1 artifact for one existing persona binding (contract
  documented so retrain can mutate parameters programmatically).

### EVOLOOP-004 — research plane produces artifact v2

- evolution dispatch → research-orchestrator work item → training-session /
  optimizer executes a real minimal retrain (parameter mutation) → artifact
  v2 registered with lineage {v1, decision_id, work_item_id}.
- No fake outputs: v2 must differ from v1 in recorded parameters and carry
  the producing session id.

### EVOLOOP-005 — governed baselines + threshold activation

- Populate `expected_drawdown` for the v1 artifact via the documented
  governance flow; enable `rolling_pnl_floor` with a calibrated value once
  EVOLOOP-002 lands. Both edits are live config with policy_source notes.

### EVOLOOP-006 — promote pipeline to LEAN

- registry artifact → deployment plan → runtime binding update replaces ONE
  rescue-placeholder binding with a pipeline-managed binding (respect the
  RuntimeBinding contract: runtime_id must match container
  PANTHEON_RUNTIME_ID; see paper binding rescue runbook).
- Rollback path documented and tested (re-bind previous artifact).

### EVOLOOP-007 — strategy-driven signals

- The promoted binding's signals originate from its strategy artifact
  (parameterized logic), entering signal-store through the normal ingest
  path; the per-minute generic feeder is disabled for that binding only.
- Evidence: trades on that binding trace to strategy-emitted signals.

### EVOLOOP-008 — full-cycle live verifier

- One scripted pass proves: breach (real or producer-injected) → incident →
  sweep proposal → approve → dispatch worker executes → research work item →
  artifact v2 → promote → binding v2 live → trades → journal shows the whole
  cycle with real ids. Each segment failure reported distinctly. Added to
  `scripts/run_e2e_verifiers.sh`.

### EVOLOOP-009 — deploy + closeout

- All packet PRs merged and deployed to dev; hosted console evidence:
  Evolution Journal shows the executed decision, Persona Fleet 最近 MUTATION
  links to it, the promoted binding shows artifact v2.
- Residual risks recorded with owner and expiry. Live curl evidence per the
  babysit rule.

## Definition of Done (packet-level)

```text
a real threshold breach (or producer-injected breach through the formal
inlet) completes one full generative cycle on hosted dev:
  incident -> proposal -> approve -> auto-execute -> retrain -> artifact v2
  -> promoted LEAN binding -> strategy-driven trades -> real PnL/drawdown
  telemetry -> Evolution Journal records every stage with linked ids
```

No stage may be satisfied by hand-editing stores; every transition must go
through the service APIs the loop will use unattended.

## Addendum 2026-07-14: conversation-plane connectors + LOOP-PROD convergence

Sponsor-approved additions after cross-checking the
`2026-07-13-loop-product-level-remediation` (LOOP-PROD) program:

- **EVOLOOP-010 — conversation-plane proposal intake.** The seven-stage
  discussion loop (討論→建議→模擬→決策→執行→回顧→學習) has no committed
  spec anywhere in the repo, and none of the three conversation surfaces
  (management-console persona chat, Agora workshops, consultation committee)
  has an outlet into evolution governance. EVOLOOP-010 writes the umbrella
  spec and implements the surface-agnostic intake, starting with a caller
  for `services/consultation/sponsor_decision_bridge`.
- **EVOLOOP-011 — persona learning feedback.** Executed-decision and
  postmortem outcomes flow into persona memory (OpenClaw SOUL/trainer) so
  the 學習 stage exists on the persona side, not only as machine retrain.
- **Convergence ruling** with LOOP-PROD lives in
  `docs/bff/execution-tasks/2026-07-13-loop-product-level-remediation/CONVERGENCE-EVOCHAIN-EVOLOOP-2026-07-14.md`:
  EVOLOOP is the thin vertical slice and goes first on the seven overlap
  points; LOOP-PROD consumes and generalizes. EVOLOOP-002/005 are the only
  performance-metric supply tasks in any program.
