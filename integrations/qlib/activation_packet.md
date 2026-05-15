# Qlib Production Activation Packet

Last updated: 2026-05-12
Owner: QLIB-ACT-001 (Claude)
Reviewer: Codex2
Status: QLIB-ACT-001 RS-003 baseline StrategySpec Codex2 review approved 2026-05-12 (done); governed dataset and LightGBM run pending (QLIB-ACT-002, QLIB-ACT-003)

## 1. Purpose

This packet is the reviewable activation surface for the `Qlib` row after the
adapter moved from smoke-only packaging to an activation-ready offline worker
and then gained production-data proof validation.

It does two things:

1. records the current activation truth in one place
2. defines the exact evidence bundle required before the first governed
   production LightGBM alpha run may start

This packet does **not** authorize registry writes, paper/canary/live
deployment, broker sessions, capital binding, or order routing. It formalizes
that Qlib can now produce a reviewable `draft -> candidate` handoff from a
governed production-data proof when the caller supplies the required RS-003,
dataset, StrategySpec, entitlement, PIT, freshness, storage, and audit evidence.

## 2. Current Disposition

Current checklist row status remains `smoke-tested` because the checklist has no
separate `production-data-packet-ready` state and production registry admission
is not open.

Repo-local truth as of 2026-05-01:

- the governed Qlib adapter exists at `services/research/qlib/adapter/qlib_adapter.py`
- the production-data proof validator and packet builder exist at
  `services/research/qlib/adapter/production_activation.py`
- `services/research/qlib/production_activation_smoke.py` runs a real/stub-selectable
  activation packet smoke against caller-supplied dataset/proof JSON
- the offline pre-activation preflight scaffold exists at `services/research/qlib/preflight.py`
- `validate_activation_ready_dataset()` enforces the >=50 instrument, >=2 year,
  >=504 daily-period production data floors before training when
  `enforce_activation_ready=True`
- `validate_production_dataset_proof()` requires provider/source-class,
  entitlement/license/allowed-use, freshness, point-in-time fields, durable
  storage refs/checksum, rate-limit/audit evidence, and explicit no-order-route
  controls before the production activation packet can be built
- `build_production_activation_packet()` attaches that proof to the candidate
  handoff, keeps `artifact_state=draft`, requests only `candidate`, preserves
  `deployment_summary.current_stage=none`, and declares
  `registry_service_only` as write authority
- `persist_qlib_run_artifacts()` writes `artifact_bundle.json`, `registry_entry.json`,
  `candidate_packet.json`, and `manifest.json` without writing registry truth
- `services/research/qlib/worker.py` is fail-closed unless
  `PANTHEON_QLIB_ACTIVATION_READY_ENABLED=1` is set, and it requires explicit
  `QLIB_BACKEND=stub|real`
- selecting `QLIB_BACKEND=real` runs `QlibLightGBMBackend` or returns the explicit
  `Install services/research/qlib/requirements.txt first` error
- `services/research-worker-gateway` can execute the Qlib worker only under
  `PANTHEON_OFFLINE_GATE_ENABLED=true`; production/paper/canary/live remain
  rejected
- the default smoke path still passes via `python3 services/research/qlib/smoke_test.py`
- unit coverage still passes via
  `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
- registry admission and any later production use remain blocked on review and
  the entry gates from `services/learning/qlib/ACTIVATION_CRITERIA.md §1`

The gate is therefore cleared only in the truthful sense:

- all repo-local safety gates are closed by default
- the first governed LightGBM activation-ready handoff is now prepared
- the actual production LightGBM run must wait until the upstream strategy and
  governed dataset gates are proven

## 3. Activation Gate Read

| Activation criterion | Current read | Evidence | Gap to close |
|---|---|---|---|
| RS-003 baseline StrategySpec candidate exists in registry | **Codex2 review approved 2026-05-12** (QLIB-ACT-001 done) | `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md` — registry ID `qlib-tw-cross-sectional-alpha-spec-v1`, `artifact_state=draft`, `deployment_summary.current_stage=none`; problem statement, universe (TWSE + TPEx, ≥50 instruments), label (5d forward return, z-scored), horizon, evaluation metrics, and why-LightGBM rationale all defined in §1–§7; RS-003 gate steps cited in §8; review file: `docs/reviews/2026-05-12-qlib-act-001-codex2-review.md` | advance spec artifact_state to `candidate` only when QLIB-ACT-002 dataset packet is ready and admitted |
| Governed dataset of ≥50 instruments with ≥2 years OHLCV history is available | packet validator implemented | `validate_activation_ready_dataset()` enforces the numerical floors, while `validate_production_dataset_proof()` now requires provider entitlement, freshness, PIT, storage, audit, and no-order-route evidence. The repo sample remains smoke-only and does not claim production data. | supply the target run's actual governed dataset/proof JSON and run `production_activation_smoke.py --backend stub` or `--backend real` (QLIB-ACT-002) |
| Supervised alpha framing is documented for the target strategy | **addressed** (QLIB-ACT-001) | `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md §6.2–§6.3` documents why LightGBM supervised cross-sectional prediction is the correct framing; RL deferred (sequential decision-making not required); TRL not applicable (no preference feedback events); universe bound to TWSE + TPEx listed equities | no gap for this criterion; resolved by QLIB-ACT-001 StrategySpec |
| No upstream dependency conflicts | satisfied | `services/research/qlib/requirements.txt`, `integrations/qlib/integration.md`, and the passing smoke/unit baselines show the pinned package path is compatible with the current governed research stack | keep this revalidated when dependency pins change |

## 3.0 RS-003 Baseline StrategySpec — QLIB-ACT-001 Summary

**StrategySpec artifact**: `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md`
**Candidate registry ID**: `qlib-tw-cross-sectional-alpha-spec-v1`
**Strategy ID**: `tw-cross-sectional-equity-alpha`
**Artifact state**: `draft` (Codex2 review approved 2026-05-12; advancing to `candidate` pending QLIB-ACT-002)
**Deployment stage**: `none`

Key bindings for downstream tasks:

| Attribute | Value |
|---|---|
| Universe | TWSE listed + TPEx listed; ≥50 instruments; ≥2 years daily OHLCV |
| Label | 5-day forward return, z-scored cross-sectionally |
| Horizon | 5 trading days (primary); 1-day IC evaluation secondary |
| Model | LightGBM (`LGBModel` via `pyqlib==0.9.6`) |
| Features | 13 OHLCV-derived features (momentum, volatility, volume, RSI, cross-sectional z-scores) |
| Evaluation gate | Test IC ≥ 0.03; test Sharpe ≥ 80% of validation Sharpe |
| Why LightGBM not RL | Prediction target, not sequential decision; tabular data; RL path (LP-005) not yet activated |
| Why not TRL | No preference feedback events; TRL governed separately |

QLIB-ACT-002 (governed dataset packet) must cite:
- `source_strategy_spec_id = "qlib-tw-cross-sectional-alpha-spec-v1"`

QLIB-ACT-003 (LightGBM activation run) must cite:
- `lineage.source_strategy_spec_id = "qlib-tw-cross-sectional-alpha-spec-v1"` in the model artifact candidate packet

## 3.1 Offline Preflight

`services/research/qlib/preflight.py` provides a repo-local readiness report for
the three production activation blockers above:

1. RS-003 candidate registry ref and pass evidence
2. governed dataset manifest with >=50 instruments, >=2 years of OHLCV history,
   allowed frequency, and lineage refs
3. concrete StrategySpec binding with supervised label/target framing

The preflight is deliberately non-writing and fail-closed. Missing probes return
`activation_allowed=false`; they do not query or update registry/governance, and
they do not execute `QlibLightGBMBackend` or the production LightGBM path.

## 3.2 Production Dataset Proof Contract

The production activation smoke requires a separate proof JSON with these
fields before it will build a candidate handoff:

| Proof area | Required evidence |
|---|---|
| Provider | provider name, source class (`research_grade` or `internal_can`), provider dataset ID |
| Entitlement/license | entitlement ref or tags, license scope, allowed use including `research` and `model_training`, and no order-capable allowed-use target |
| Freshness | `status=fresh`, `as_of`, `last_ingested_at`, and a positive freshness SLA |
| PIT | `point_in_time=true`, event-time field, available-time field, and source watermark |
| Storage | durable backend, dataset ref matching the Qlib workflow source refs, snapshot ref, path, and `sha256:` checksum |
| Audit | ingest run, normalization run, evidence bundle ref, and rate-limit policy ref |
| Controls | `no_order_route=true` and no broker/Lean/order/paper/canary/live/capital execution targets |

Canonical command shape:

```bash
python3 services/research/qlib/production_activation_smoke.py \
  --dataset /path/to/governed_ohlcv_dataset.json \
  --proof /path/to/production_dataset_proof.json \
  --backend stub \
  --output-dir /tmp/pantheon/research/qlib/prod-activation
```

To exercise the upstream backend, use `--backend real`. If `pyqlib==0.9.6` or
its runtime dependencies are unavailable, the command returns the explicit
`Qlib backend unavailable. Install services/research/qlib/requirements.txt first.`
error instead of silently falling back to the stub.

## 4. First Governed LightGBM Activation Bundle

Before the first production Qlib run starts, the owner should attach all of the
following evidence to the execution/review lane:

1. RS-003 candidate proof
   - candidate registry ID
   - strategy family / problem statement
   - replication-gate pass timestamp or evidence ref
2. governed dataset proof
   - dataset ref(s)
   - instrument count
   - history window
   - frequency and market scope
3. target-supervision proof
   - label definition
   - why supervised alpha is appropriate for this target
   - why RL / TRL are not the correct first lane
4. LightGBM run bundle
   - backend used (`StubLightGBMBackend` for deterministic packet smoke or
     `QlibLightGBMBackend` for upstream real-backend smoke)
   - config version and key hyperparameters
   - artifact checksum and storage path
   - holdout metrics / backtest summary
5. registry admission packet
   - canonical `artifact_state=draft`
   - `deployment_summary.current_stage=none`
   - lineage refs back to source dataset and source strategy spec
   - non-writing `candidate_packet` requesting only `draft -> candidate`
   - artifact manifest with checksum and paths for the persisted handoff files
   - `production_activation_packet.json` when the production dataset proof is attached

The governed output target remains unchanged:

- workflow entrypoint: `run_qlib_workflow()`
- artifact family: `qlib_alpha`
- registry artifact type: `model_artifact`
- initial registry state: `artifact_state=draft`
- deployment stage: `deployment_summary.current_stage=none`
- lifecycle for Qlib alpha artifacts: `draft` → `candidate` → `approved` → `retired`

## 5. Verification Snapshot

Revalidated in this session on 2026-05-01:

1. `python3 services/research/qlib/smoke_test.py`
   - Result: passed
   - Dataset: governed sample dataset from `services/research/qlib/examples/equity_dataset_sample.json`
   - Output confirms `artifact_state=draft`, `deployment_stage=none`, and
     governed storage under `research/qlib/`
2. `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
   - Result: 32 tests passed, including preflight, activation-ready data floors,
     production dataset proof validation, candidate packet, persistence, explicit
     backend error, and fail-closed worker checks
3. `production_activation_smoke.py --backend stub` is covered by
   `services/research/qlib/test_production_activation.py`
   - Result: writes `artifact_bundle.json`, `registry_entry.json`,
     `candidate_packet.json`, `manifest.json`, and
     `production_activation_packet.json`
   - Confirms provider/entitlement/freshness/PIT/storage/audit proof is attached
   - Confirms `artifact_state=draft`, requested state `candidate`,
     `deployment_stage=none`, `registry_service_only`, and `order_route=none`
4. `pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_qlib_activation.py`
   - Result: 2 tests passed
   - Closed gate: Qlib offline dispatch is rejected
   - Open gate: Qlib worker runs with explicit env gate, enforces data floors,
     persists handoff artifacts, and leaves production activation disabled
5. `python3 -m pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_qlib_activation.py services/research-worker-gateway/tests/test_research_worker_gateway_gate_dispatch.py`
   - Result: 11 tests passed
   - Confirms closed-gate rejection, open-gate offline subprocess execution,
     stdout/stderr/exit-code persistence, capability gate metadata, and
     paper/canary/live fail-closed behavior
6. `python3 -m pytest -q services/research-worker-gateway/tests/test_research_worker_gateway_rejection_policy.py services/research-worker-gateway/tests/test_research_worker_gateway_http_service.py`
   - Result: 9 tests passed
   - Confirms the pre-existing rejection and HTTP contract still fail closed

These checks prove the adapter can produce a production-data activation packet
when supplied complete governed evidence. They do not write registry truth or
open an order-capable path.

## 6. Disposition

`Qlib` should remain `smoke-tested` in `OSS_INTEGRATION_CHECKLIST.md` until QLIB-ACT-003
completes the LightGBM activation run and registry admission is granted.

### Updated next-action sequence (2026-05-12)

| Step | Task | Status |
|---|---|---|
| 1. RS-003 baseline StrategySpec authored | QLIB-ACT-001 | **done** (Codex2 review approved 2026-05-12) |
| 2. StrategySpec advanced to `candidate` | awaits QLIB-ACT-001 Codex2 reviewer approval and QLIB-ACT-002 dataset packet admission | pending |
| 3. Governed ≥50-instrument, ≥2-year OHLCV dataset manifest and production dataset proof | QLIB-ACT-002 | pending |
| 4. Bind StrategySpec + supervised label to the LightGBM run | QLIB-ACT-003 cites `qlib-tw-cross-sectional-alpha-spec-v1` | pending |
| 5. Execute first governed LightGBM activation via `--backend real` | QLIB-ACT-003 | pending |
| 6. Submit `qlib_alpha` artifact packet for registry admission review | QLIB-ACT-003 output | pending |

Until review admits the packet, the row is production-data packet-ready behind
explicit gates but still blocked from production registry use.
