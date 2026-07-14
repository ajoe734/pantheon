# Evolution Generative Loop Gap Execution Packet - 2026-07-14

Status: ready for fleet dispatch

Source gap spec:

- `docs/04/pantheon_evolution_generative_loop_gap_2026-07-14/EVOLUTION_GENERATIVE_LOOP_GAP.md`

Extends:

- `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`
  (EVOCHAIN = observation half; this packet = generative half)

Supersedes:

- the discussed-but-never-dispatched `EVOCHAIN-012` (folded into `EVOLOOP-002`)

## Dispatch Command

Validate without mutating live status:

```sh
python3 scripts/dispatch_evolution_generative_loop_gap_2026-07-14.py --dry-run
```

Dispatch into the live supervisor status root:

```sh
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/dispatch_evolution_generative_loop_gap_2026-07-14.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py sync
```

The dispatch script is idempotent: it preserves progress fields for already
started tasks and only appends assignment events for newly created tasks.

## Execution Order

Frontier per `docs/conventions/WAVE_PLANNING_PARALLELISM.md` — only true
build-on-top edges are declared.

| Wave | Task | Owner | Reviewer | Summary |
|---|---|---|---|---|
| 0 | `EVOLOOP-001` | Codex | Claude | Deploy evolution-dispatch-worker (code exists); approved decisions auto-execute through the gated path. |
| 0 | `EVOLOOP-002` | Claude | Codex | Real performance telemetry: PnL mark-to-market fix + drawdown computation, pnl/drawdown_snapshot events per binding. |
| 0 | `EVOLOOP-003` | Claude | Codex2 | Minimal evolvable strategy artifact contract + one genuine v1 artifact registered for an existing persona binding. |
| 1 | `EVOLOOP-004` | Codex | Claude | Research plane consumes the dispatched retrain and produces artifact v2 with lineage (real session, real parameter delta). |
| 1 | `EVOLOOP-005` | Codex2 | Codex | Governed baselines: expected_drawdown for v1; enable calibrated rolling_pnl_floor after 002. |
| 1 | `EVOLOOP-006` | Codex2 | Claude | Promote pipeline: registry -> deployment plan -> replace one rescue binding with a pipeline-managed binding (rollback tested). |
| 2 | `EVOLOOP-007` | Claude | Codex | Strategy-driven signals for the promoted binding via normal ingest; generic feeder disabled for that binding only. |
| 2 | `EVOLOOP-008` | Codex | Claude | Full-cycle live verifier (breach -> ... -> artifact v2 trading -> journal); added to run_e2e_verifiers.sh. |
| 1 | `EVOLOOP-010` | Codex2 | Claude | Conversation-plane proposal intake: seven-stage discussion-loop spec + unified suggestion -> EvolutionDecisionProposal contract; first adapter = consultation sponsor_decision_bridge caller. |
| 2 | `EVOLOOP-011` | Claude | Codex | Learning feedback: executed-decision / postmortem outcomes written into persona memory (OpenClaw SOUL/trainer) so personas reference real outcomes in future discussions. |
| 3 | `EVOLOOP-009` | Codex2 | Human/Ops | Deploy + closeout: hosted console evidence, live curl, residual risks. |

## Dependencies

```text
EVOLOOP-001: none
EVOLOOP-002: none
EVOLOOP-003: none
EVOLOOP-004: EVOLOOP-001, EVOLOOP-003
EVOLOOP-005: EVOLOOP-002, EVOLOOP-003
EVOLOOP-006: EVOLOOP-003
EVOLOOP-007: EVOLOOP-006
EVOLOOP-008: EVOLOOP-002, EVOLOOP-004, EVOLOOP-006
EVOLOOP-010: EVOLOOP-001
EVOLOOP-011: EVOLOOP-004
EVOLOOP-009: EVOLOOP-005, EVOLOOP-007, EVOLOOP-008, EVOLOOP-010, EVOLOOP-011
```

## Convergence with LOOP-PROD (2026-07-14 sponsor ruling)

The `2026-07-13-loop-product-level-remediation` packet (36 tasks) covers the
same execution spine at full-matrix product depth. Division of labor:

- **EVOLOOP is the thin vertical slice**: prove ONE loop end-to-end fast on
  one binding/artifact. **LOOP-PROD is the full-matrix productization**: it
  MUST consume EVOLOOP outputs, not recreate them.
- Overlap map (EVOLOOP goes first; LOOP-PROD consumes and generalizes):
  - `EVOLOOP-001` deploys the dispatch worker → `LOOP-PROD-EVO-001` replaces
    synthetic SUBMITTED with target-plane readback on that deployed worker.
  - `EVOLOOP-003`/`EVOLOOP-004` define the artifact contract and first
    retrain → `LOOP-PROD-DIST-001`/`LOOP-PROD-ALPHA-001` build the durable
    consumers on that contract.
  - `EVOLOOP-006` promotes one binding via service APIs →
    `LOOP-PROD-DEP-001` generalizes the same runtime-manager path with
    outbox apply + readback.
  - `EVOLOOP-007` moves one binding to strategy signals →
    `LOOP-PROD-CAP-001`'s first-class producer adopts that binding as its
    first tenant.
  - `EVOLOOP-008` full-cycle verifier → consumed/extended by
    `LOOP-PROD-VERIFY-EXEC-001`.
  - `EVOLOOP-009` closes THIS packet only; `LOOP-PROD-CLOSE-001` is global.
- `EVOLOOP-002`/`EVOLOOP-005` (PnL mark-to-market, drawdown, governed
  baselines) exist ONLY here — the LOOP-PROD catalog has zero coverage of
  performance-metric supply; its TEL/CAP tasks must treat them as upstream
  dependencies.
- Any LOOP-PROD addendum must list consumed `EVOCHAIN-*`/`EVOLOOP-*` IDs as
  external dependencies instead of re-dispatching the same scope.

## Hard Rules

- **No stage may be faked**: every transition goes through the service APIs
  the loop will use unattended. Hand-editing stores disqualifies the
  evidence (June rescue was an emergency, not a precedent).
- Fail-closed everywhere: missing marks/telemetry produce diagnostics, never
  fabricated values or breaches.
- Threshold values and baselines are live config with `policy_source` notes;
  no image rebuild to tune.
- Do not modify any existing supervisor/poll cadence; new workers own their
  interval envs.
- Host cron feeders (`feed_signals*.sh`, `tw_signal_producer.py`) stay in
  place for bindings not covered by EVOLOOP-007's proof.
- The RuntimeBinding contract from the paper-binding-rescue runbook applies
  to EVOLOOP-006 (runtime_id must match container PANTHEON_RUNTIME_ID).
- Deploy tasks are not done until live curl evidence is archived
  (babysit rule).
- Cross-checks with the EVOCHAIN packet: do not duplicate or conflict with
  EVOCHAIN-010/011 scope; the observation-half verifier stays in EVOCHAIN.

## Global Acceptance

Every task records: branch + PR target; changed files and owned scope; local
validation output; hosted evidence for UI-visible changes; reviewer approval;
merge SHA; residual risk with owner and expiry.

The packet is complete only when one full generative cycle is proven on
hosted dev with linked ids at every stage:

```text
breach -> incident -> proposal -> approve -> auto-execute -> retrain
  -> artifact v2 -> promoted binding -> strategy-driven trades
  -> real PnL/drawdown telemetry -> Evolution Journal shows the cycle
```
