# Claude Readout — Phase 5: Full Blueprint Gap Closure

## Lane

- Agent: Claude
- Capability focus: Facilitate consensus, synthesize cited disagreements, and prepare the human gate packet. This readout focuses on cross-cutting gap analysis, delivery order rationale, and surfacing items that require human decision before materialization.

## Canonical Sources Read

- L0: `current-work.md`, `ai-status.json`
- L1: `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/README.md`, `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`
- L2: `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`, `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/codex-readout.md`, `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/execution-materialization.md`, `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/planning-session.json`, `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/README.md`

## Working Interpretation

### Architecture summary

Phase 5 is not a redesign session. It is the first session that asks all four gap clusters to converge in a single delivery frame:

1. **Service exposure** — phase 2–4 domain objects still lack HTTP wrappers, Dockerfiles, health checks, and compose wiring. The canonical six slices proposed in the phase4 starter-draft (`SVC-BASELINE`, `SVC-RUNTIME-CONTROL`, `SVC-GOVERNANCE-API`, `SVC-EVIDENCE`, `SVC-SURFACES`, `SVC-COMPOSE`) are the right first wave. They have not been materialized yet because the phase4 consensus packet was never finalized.

2. **Command-plane convergence** — the BFF still dispatches evolution approval and action commands as local placeholders rather than to a real internal API endpoint. This is the single most important command-path gap because it means the operator console cannot honestly complete evolution workflows end to end.

3. **Surface and packet coverage** — the Lovable coordination tracker shows 11 packets in `lovable-ready` or `waiting_for_lovable` state, 0 returned `ui-done`, and 2 returned `frontend_feedback`. Large workbench families (Research, Knowledge, Trainer, Consultation, Persona) still have missing packet families entirely.

4. **OSS real integration** — phase6 criteria are documented but only three components have moved past criteria: `OpenClaw` is `adapter-started`, `DSPy`/`imitation`/`MLflow` are `smoke-tested`. The remaining six frameworks (`TRL`, `Qlib`, `FinRL`, `RLlib`, `Ray Tune`, `W&B`) are still criteria-only or version-pinned-only.

The architecture of this session is therefore a sequenced three-wave plan:
- **Wave 1 (blocking)**: service-layer operational baseline (the six SVC-* slices)
- **Wave 2 (parallel once Wave 1 is composeable)**: workbench expansion and Lovable queue closure
- **Wave 3 (downstream of Wave 1)**: OSS adapter realization beyond criteria

### Delivery order

The delivery order constraint is structural, not preference-based:

1. `SVC-BASELINE` first — without a shared port map, env contract, and volume scheme, every service implementation risks collision or silent integration failure. The Codex readout confirms the current compose file has BFF and router both attempting `:8001`.
2. `SVC-RUNTIME-CONTROL` second — BFF operator commands already depend on `PANTHEON_INTERNAL_API_URL` defaulting to `http://localhost:5001`. Evolution approval/action are the only missing dispatch paths.
3. `SVC-GOVERNANCE-API` + `SVC-EVIDENCE` third — these are independent of each other and can be parallelized, but both depend on `SVC-BASELINE`.
4. `SVC-SURFACES` fourth — rewiring BFF off snapshot/default mode cannot be honest until the backend services it would call actually exist and are reachable.
5. `SVC-COMPOSE` fifth — the single-VM compose is the integration smoke test; it should come after all services it depends on are individually packaged.
6. **Workbench expansion** sixth — additional packet families and Lovable handoffs can proceed once BFF is rewired away from snapshots, because packet testing becomes meaningful only when BFF routes are backed by real service reads.
7. **OSS adapter realization** seventh — OpenClaw adapter completion, DSPy/imitation/MLflow governance normalization, and deferred framework smoke tests do not block the service stack assembly but do represent real phase-6 delivery risk if left as criteria documents indefinitely.

### Ownership boundaries

These are the ownership boundaries that reviewers should validate, not rewrite:

| Boundary | Owner | Authority |
|---|---|---|
| Side-effectful operator commands | `runtime-control` | exclusive write authority for runtime-binding mutation, kill-switch, and deployment commands |
| Evolution approval/action | currently split; must converge | either `runtime-control` or `governance-api` — this is the primary open disagreement from the phase4 starter-draft |
| Governance read/write APIs | `governance-api` | approval decisions, capital pool, persona binding, deployment plans, evolution decisions |
| Telemetry event intake | `telemetry-ingest` | buffering, retry, DLQ |
| Lineage queries | `lineage-read` | read-only projection endpoints |
| Operator read aggregation | `bff` | must stop reading from JSON snapshots or seeded defaults once Wave 1 services are runnable |
| Lovable front-end packets | per-packet lanes | no shared ownership; each packet returns to the issuing lane when `ui-done` fires |

## Risks / Contradictions

- **Risk 1 — Phase4 consensus packet was never finalized.** The phase4 session shows `consensus_status: draft` and `human_gate_status: not_requested`, and the consensus packet file contains only empty stubs. Phase5 cannot safely start materializing Wave 1 work until either phase4's six slices are formally accepted or phase5 explicitly absorbs and supersedes the phase4 planning scope. This is the most important structural issue for human review.

- **Risk 2 — BFF snapshot fallback masks missing integration.** The `read_store.py` code prefers JSON snapshot files and silently falls back to `_default_read_data()` when they are absent. This means Dockerizing BFF before Wave 1 backends exist would produce a testable but dishonest service stack. The delivery order in this readout avoids that outcome, but the risk is real if any lane advocates for shortcutting BFF Dockerization to get a faster compose smoke.

- **Risk 3 — Evolution command boundary is unresolved.** The phase4 starter-draft lists this as an explicit open disagreement: should evolution approval/action live in `runtime-control`, `governance-api`, or be split? This is not a preference question — it determines the ownership topology for one of the most critical operator workflows. It must be resolved before `SVC-RUNTIME-CONTROL` and `SVC-GOVERNANCE-API` are materialized, or both slices will implement overlapping surfaces.

- **Risk 4 — Lovable queue is stalled at zero `ui-done`.** With 9 packets in `waiting_for_lovable` and 0 returned `ui-done`, there is a risk that Lovable coordination has become passive rather than active. Wave 2 workbench expansion may add more packets to the queue before existing ones complete. A policy decision on Lovable queue management (e.g., cap in-flight, block new handoffs until queue drains, or explicit escalation SLA) should be part of the consensus packet.

- **Risk 5 — OSS criteria accumulation without integration gates.** `OSS_INTEGRATION_CHECKLIST.md` explicitly warns against treating a component as integrated just because contracts were written around it. The session should establish a hard gate: once a component has been in `criteria-defined` or `version-pinned` state for more than one planning cycle, it must either advance to `adapter-started` or be explicitly deferred with a named reason.

- **Risk 6 — Phase5 scope is broader than phase4 but there is no Codex starter-draft yet.** This readout is being written in round 0 before Codex seeds `starter-draft.md`. The cross-session relationship (phase4 slices absorbed into phase5, or phase4 completing first, then phase5 covering workbench + OSS) is an open sequencing question that the starter-draft must address.

## Suggested Task Slices

As facilitator, I am not proposing competing slices to the Codex starter-draft. Instead, I am naming the scope categories that the consensus packet must cover and flagging what is still missing from the phase4 scope:

- **Wave 1 (absorbing phase4 scope)**: `SVC-BASELINE`, `SVC-RUNTIME-CONTROL`, `SVC-GOVERNANCE-API`, `SVC-EVIDENCE`, `SVC-SURFACES`, `SVC-COMPOSE` — these are already proposed in the phase4 starter-draft and need formal acceptance.

- **Wave 2 (workbench expansion)**: one or more slices targeting the unpacketized workbench families identified in `pantheon-console-workbench-backlog.md` — specifically Persona, Research, Knowledge, Trainer, Consultation, and the missing Evolution Workbench families (inspiration, mutation review).

- **Wave 3 (OSS realization)**: one or more slices for `OpenClaw` adapter completion, `DSPy`/`imitation`/`MLflow` governance normalization, and at least one of the deferred frameworks reaching `adapter-started`.

- **Lovable coordination policy** (cross-cutting): a process slice or policy document that sets a cap or escalation rule for `waiting_for_lovable` items to prevent indefinite queue growth.

The starter-draft from Codex should confirm, modify, or replace this wave structure. Reviewers should validate whether Wave 2 and Wave 3 can genuinely begin in parallel with late Wave 1 work, or whether they must be fully sequential.

## Citations

- [P5-README] `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/README.md`: phase5 is a full blueprint delivery-gap convergence session covering serviceization, OSS integrations, infrastructure, and Lovable execution; Claude is named facilitator and consensus-packet owner.
- [P5-JSON] `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/planning-session.json`: `current_round: 0`, `consensus_status: draft`, all readout statuses `pending`, no starter-draft content yet, Codex is baton owner.
- [P4-GAP] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md §3`: the four gap clusters are service exposure, command-plane convergence, surface/packet coverage, and OSS integration; all canonical phase 2-6 task records are archived done but delivery completeness is still missing across all four clusters.
- [P4-GAP §4] `phase2-phase6-gap-inventory.md §4 Phase Snapshot Table`: phase 2 — service exposure for runtime-control and governance/runtime APIs outstanding; phase 3 — HTTP wrapping and packaging for telemetry/lineage/incident outstanding; phase 4 — command-plane convergence and evolution service boundary outstanding; phase 5 — BFF rewiring and workbench expansion outstanding; phase 6 — real OSS adapter execution outstanding.
- [P4-SD] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md §Open disagreements`: three unresolved items — internal_api.py long-term fate, evolution approval/action endpoint ownership, and BFF snapshot temporary vs. wave-blocking question.
- [P4-CP] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/consensus-packet.md`: file contains only stub placeholders — `consensus_status` never advanced to accepted.
- [P4-MAT] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/execution-materialization.md`: six slices seeded but pending reviewer focus on whether the wave boundary is correct and whether phase5/6 work belongs in the first wave.
- [P4-CX] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/codex-readout.md §Risks`: port collision risk (BFF + router both at `:8001`), BFF-hides-missing-backend risk, evolution boundary unresolved risk — all three remain open.
- [CW] `current-work.md §Lovable Coordination`: 11 tracked features, 11 lovable-ready packets, 9 waiting for Lovable/front-end, 0 returned ui-done, 2 returned frontend feedback — queue has not drained since last planning cycle.
- [CW §Discussion Planning]: phase4 session still shows `status: active`, `current_round: 1`, `ready_for_human: True`, `consensus: draft` — the human gate was never opened.
