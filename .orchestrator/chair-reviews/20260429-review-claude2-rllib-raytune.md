# Task Review: SVC-RLLIB-RAYTUNE-DORMANT-SCAFFOLD-CLOSEOUT

**Reviewer**: Claude2
**Task Owner**: Codex
**Reviewed at**: 2026-04-29
**Decision**: APPROVE

---

## Acceptance Criteria Check

### 1. RLlib and Ray Tune workers require explicit deferred-prep env gates ✓

- `worker.py`: calls `DeferredPrepGate.require_env()` at the top of `main()`.
  Without `PANTHEON_RLLIB_PREP_ENABLED=1`, exits with code 2 and prints the gate message.
- `ray_tune_worker.py`: calls `RayTuneDeferredPrepGate.require_env()` at the top of `main()`.
  Without `PANTHEON_RAYTUNE_PREP_ENABLED=1`, exits with code 2 and prints the gate message.
- Both gates verified: running workers without the env vars fails closed as expected.

### 2. Smoke paths require explicit CLI flags ✓

- `smoke_test.py`: passes `args.enable_deferred_prep` to `DeferredPrepGate.require_cli_flag()`.
  Without `--enable-deferred-prep`, exits with code 2 and prints the gate message.
- `ray_tune_smoke_test.py`: passes `args.enable_deferred_prep` to `RayTuneDeferredPrepGate.require_cli_flag()`.
  Without `--enable-deferred-prep`, exits with code 2 and prints the gate message.
- Both smoke paths verified: running without the CLI flag fails closed.

### 3. Dockerfile and docs do not claim adapter missing when code exists ✓

- `Dockerfile`: header correctly says "deferred-prep scaffold — adapters and explicit-gate smoke paths are present".
  Default `CMD` is inert; no claim of missing adapters.
- `DEFERRED_OSS_ACTIVATION_MAP.md` §4 (RLlib + Ray Tune): correctly describes the prep-only scaffold
  with worker gates and smoke gates, and does not understate the landed code.
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` RLlib row: "Dormant train/eval adapter, worker, and
  explicit-gate smoke path exist under `services/research/rllib`" — accurate.
- `README.md`: lists all present files accurately; usage commands require the explicit gates.

### 4. All outputs remain draft/none/non-writing ✓

- Both workers produce `artifact_state: "draft"`, `deployment_stage: "none"`, `gate_state: "closed"`.
- `direct_live_influence: false` in both adapter bundles.
- Candidate packets target `requested_artifact_state: "candidate"` but `deployment_summary.current_stage`
  remains `"none"` — no production promotion path is opened.
- No registry/governance file writes occur; all output is in-memory JSON to stdout only.
- Verified by inspecting smoke test assertions and live worker output.

---

## Test Verification

```
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3 -m pytest \
  services/research/rllib/test_adapter.py \
  services/research/rllib/test_ray_tune_adapter.py -q
# 29 passed in 0.30s ✓
```

Gate fail-closed checks:
- `python3 services/research/rllib/smoke_test.py` → exit 2 ✓
- `python3 services/research/rllib/ray_tune_smoke_test.py` → exit 2 ✓
- `python3 services/research/rllib/worker.py` → exit 2 ✓
- `python3 services/research/rllib/ray_tune_worker.py` → exit 2 ✓

Explicit-gate pass checks:
- `python3 services/research/rllib/smoke_test.py --enable-deferred-prep` → assertions: OK ✓
- `python3 services/research/rllib/ray_tune_smoke_test.py --enable-deferred-prep` → assertions: OK ✓
- `PANTHEON_RLLIB_PREP_ENABLED=1 python3 services/research/rllib/worker.py` → artifact_state: draft, deployment_stage: none ✓
- `PANTHEON_RAYTUNE_PREP_ENABLED=1 python3 services/research/rllib/ray_tune_worker.py` → artifact_state: draft, deployment_stage: none ✓

---

## No Concerns

Implementation is clean:
- Gate classes (`DeferredPrepGate`, `RayTuneDeferredPrepGate`) are explicit and symmetric.
- Stub backends are deterministic and offline-only.
- Import-path backends (`RLlibPPOBackend`, `RayTuneImportBackend`) delegate to stubs and only verify import availability, no production training.
- All artifact state fields use canonical vocabulary.
- `RL_PATH_APPROVAL_GATE.md` is correctly referenced throughout.

---

## Decision

**APPROVE** — All four acceptance criteria met. Implementation is fail-closed, offline-only, explicit-gate, and non-writing. Returned to Codex for finalization.
