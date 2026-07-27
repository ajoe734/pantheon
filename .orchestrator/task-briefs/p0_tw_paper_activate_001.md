# P0-TW-PAPER-ACTIVATE-001 — make `persona-tw-equity` actually paper-trade (it is a ghost row today)

Owner: **Codex2** · Reviewer: Codex · Phase: Pantheon P0 Paper Loop
Status: **review_approved** by Codex; Codex2 owner closeout in progress
Source symptom: operator clicked the fleet "Δ 績效 9.50%" cell for `persona-tw-equity` → the
performance-attribution / holdings pages are entirely `—` / 0, and the funding-pool overview is empty.

## Problem

`persona-tw-equity` (Taiwan Equity Persona, research item `qlib-tw-cross-sectional-alpha-model-draft-v1`)
appears on the management console as a paper persona awaiting approval, showing a green **9.50%**
"Δ 績效 / Δ Performance". Clicking through, **every downstream surface is empty**: attribution all `—`,
holdings 0, source rows 0, telemetry runtimes 0/0. It is **not trading, and never has**.

## Root cause — verified against live state (not inference)

The persona is a **display-only fixture**, not a live provisioned entity. The break is at the very first
link of the chain and cascades:

1. **Never promoted / never entered governance.** `persona-tw-equity`, `runtime-tw-equity-paper`,
   `pool-tw-equity-paper` appear in **zero** authoritative live stores — Docker volumes
   `pantheon_bff-data/read_surfaces.json`, `pantheon_governance-data/{approval_decisions,deployment_plans,deployment_sagas}.json`,
   `pantheon_runtime-data/runtime_bindings.json` all return 0 hits. No approval, no plan, no saga, no binding.
   It is **not stuck mid-flight — the pipeline was never started** for this persona.
2. **The console IDs are a hardcoded seed.** They come from the "market persona default" catalog in
   `services/control-plane/bff/read_store.py` (~L1044–1305; live copy at
   `/home/lupin/pantheon-ci-deploy/dev-bff/...`), merged via `_merge_market_persona_records` when
   `include_market_persona_defaults=True`. The seed hardcodes `status: "needs_human_approval"`,
   `deployment_stage: "paper"`, frozen `last_active_at: 2026-06-07T13:00:00Z`, `live_write_enabled: False`.
   Cosmetic projection only.
3. **No binding → no worker → no telemetry.** The paper fleet reconciler
   (`services/execution/runtime-manager/paper_fleet_reconciler.py`, container `pantheon-paper-fleet-reconciler-1`)
   only spawns a `paper_runtime.py` worker per **active RuntimeBinding**. There is no
   `runtime-tw-equity-paper` binding → no worker → no `runtime_summaries.json` key → BFF
   `runtime→plan→binding→pool→persona→telemetry` join resolves all-null → the `—` / 0 the operator sees.
4. **The qlib strategy itself is still `draft`, never admitted to the registry.** `read_store` `_TW_QLIB_ARTIFACT_ID`
   block: `artifact_state: draft`, `registry_admission_status: pending_upstream_task`. The QLIB task chain
   (MGMT-QLIB-001..006, QLIB-ACT-001, OSS-QLIB-V2-001) is all marked `done`, **but every one is explicitly
   review-only / non-writing**: `support/evidence/MGMT-QLIB-005/registry_admission_packet.json` has
   `registry_write_performed: False`, `artifact_state: draft`, `deployment_stage: none`, and only *requests*
   a `draft → candidate` review. The LightGBM run behind it was a "deterministic stub." So **the paperwork
   is done; the actual admission, real training, and deployment never happened.**

### Two additional real bugs found alongside (independent of the above)

- **Fleet "Δ 績效" is a training metric mislabeled as trading performance.** `perfDelta` =
  `_training_improvement_delta(metrics)` = `training_improvement_pct / 100`, set **unconditionally for ALL
  personas** at `services/control-plane/bff/main.py:63501-63502` and `:64464`. So `9.50%` is *training
  improvement*, not a return. It also mis-links to the trading-attribution page
  (`execute-plans/src/management/pages/oversight/personaFleetLinks.ts:809`). This propagates into the ops
  read-model `performance_delta` (`main.py:52687`) and likely rankings.
- **The running paper fleet is a smoke harness, not strategy trading.** The 6 live `paper_runtime.py`
  workers are fed by `SmokeStrategy` in `services/execution/lean_runtime/paper_signal_producer.py` — a fixed
  7-share `AAPL.US` BUY per tick (docstring: "for soak/smoke runs"). No persona is doing real strategy-driven
  paper trading. Real `scripts/tw_signal_producer.py` targets an old dead queue (repoint gated on SRCLIVE-005;
  see archive `DEVLOOP-PAPER-BINDING-RESTORE-001`).

NB: "Supervisor sidecar" deletion (P7C, commit c370370f4) is UNRELATED — it was a dev-fleet task-synthesis
engine; the word "sidecar" is overloaded.

## What must happen to make TW actually paper-trade (full chain, in dependency order)

```
[C1] real qlib production LightGBM training on a governed TW dataset   (replaces the stub)
  → [C2] evaluation/scoring on that run                                (eval needs ≥ candidate/approved state)
  → [C3] registry admission: draft → candidate → approved             (registry_service_only, review-gated)
  → [C4] governance DeploymentPlan + approval                          (needs_human_approval gate)
  → [C5] capital pool + RuntimeBinding(runtime_id=runtime-tw-equity-paper) created
  → [C6] reconciler spawns paper_runtime.py worker
  → [C7] real TW signal source (tw_signal_producer) repointed off dead queue to feed the binding  (SRCLIVE-005)
  → telemetry + holdings populate → console shows real data
```

## Work breakdown & ownership boundary

### Track A — Console honesty fixes (safe, no live-trading mutation) — **Codex can execute now**
- A1. Stop rendering training-improvement as "Δ 績效 / Performance", or relabel it "Training Δ" + tooltip;
  only feed real trading return into that column when telemetry exists. (`main.py` perfDelta sites +
  `execute-plans` `_core.tsx` cell / `personaFleetLinks.ts`.)
- A2. Gate the fleet perf-cell link: only link to `?tab=attribution` when a real binding/telemetry exists;
  otherwise link to the research/mutation detail (or render non-clickable).
- A3. Make market-persona **seed** rows visually distinguishable from live runtimes (they must not look
  identical to a real paper persona). Consider hiding seed rows from live-mode surfaces entirely.
- A4. Regression tests for all three.

### Track B — Real qlib training + registry admission — **research lane; partly data-gated**
- B1. Run real (non-stub) qlib LightGBM production training [C1] + eval [C2]. **Depends on governed TW
  dataset availability (SRCLIVE / data activation).** Confirm data readiness before starting.
- B2. Drive `draft → candidate → approved` via the registry service [C3]. `registry_write_authority=registry_service_only`.

### Track C — Governance / promotion / data — **HUMAN-GATED, do NOT execute without operator sign-off**
- C4 governance DeploymentPlan + approval (`needs_human_approval`), C5 pool+binding creation,
  C7 SRCLIVE-005 real-market TW data + signal repoint. These change live capital/trading state.
  **Surface for operator decision; agents prepare packets only.**

## Acceptance (for the executable slice, Track A + B-prep)
- Fleet "績效" column no longer presents a training metric as trading performance; seed rows are not
  mistakable for live paper personas; the perf-cell link no longer dead-ends on empty attribution.
- A written go/no-go packet for Track C (what the operator must approve to actually promote TW), with the
  exact registry/governance calls enumerated.
- No production registry write, no governance plan, no runtime binding created by this task.

## Evidence / key files
- Live state (Docker volumes): `pantheon_runtime-data/runtime_bindings.json`, `pantheon_bff-data/read_surfaces.json`,
  `pantheon_governance-data/{approval_decisions,deployment_plans,deployment_sagas}.json`.
- Seed source: `services/control-plane/bff/read_store.py` (~L388–520 qlib block, ~L1044–1305 tw-equity block).
- perfDelta: `services/control-plane/bff/main.py:62645-62650, :63501-63502, :64464, :52687`.
- Smoke signal: `services/execution/lean_runtime/paper_signal_producer.py` (`SmokeStrategy`, ~L101).
- Reconciler: `services/execution/runtime-manager/paper_fleet_reconciler.py`.
- Admission packet: `support/evidence/MGMT-QLIB-005/registry_admission_packet.json` (review-only proof).
- Frontend: `execute-plans/src/management/pages/oversight/{_core.tsx,PerformanceAttribution.tsx,personaFleetLinks.ts}`.
- Prior art: archive tasks `DEVLOOP-PAPER-BINDING-RESTORE-001`, `SRCLIVE-005-*`, `QLIB-ACT-001`, `MGMT-QLIB-005`.

## Independent approval and closeout

Codex independently approved the executable slice on 2026-07-27 after reviewing
Pantheon head `6d1c8e04a146a21babfdcd8b6b5a8d3205857be5` and execute-plans head
`90bf8623761aaaf28aee3f83cbaf6e00dbeec120`. The governed task row records
`review_approved` and binds
`docs/deployment/evidence/p0-tw-paper-activate-001/evidence.json` as the review
manifest.

Owner closeout re-verified the approved scope after refreshing the Pantheon task
branch from `origin/dev`:

- backend authority, honesty, deploy-authority, and persona-fleet tests:
  `47 passed`;
- backend focused `py_compile`: passed;
- frontend management adapter, link, and fleet-page tests: `92 passed`;
- frontend typecheck and scoped ESLint: passed.

The finalization remains contract/UI-only. It performed no registry admission,
governance decision, capital-pool mutation, RuntimeBinding creation, signal
repoint, or live/paper trading write.
