# Sidecar Acceptance Packet: P2-RL-UPSTREAM-RUNTIME-SMOKE-001

**Sidecar Task**: P2-RL-UPSTREAM-RUNTIME-SMOKE-001-SIDECAR-ACCEPTANCE
**Parent Task**: P2-RL-UPSTREAM-RUNTIME-SMOKE-001 — FinRL RLlib Ray Tune governed runtime activation smoke
**Packet Owner**: Claude2
**Reviewer**: Codex
**Date**: 2026-05-01
**Status**: Review approved — finalized 2026-05-01

---

## 1. Packet Scope

This is a support-only artifact. It documents the acceptance checklist, dependency map, and current readiness posture for the parent task P2-RL-UPSTREAM-RUNTIME-SMOKE-001. It does not modify any canonical truth, runtime implementation, registry/governance state, or activation gate decision.

---

## 2. Parent Task Summary

**Goal**: Move FinRL, RLlib, and Ray Tune from dormant/deferred prep to bounded governed runtime smoke — running bounded train/search when the real backend is available, or leaving explicit dependency/config error evidence otherwise.

**Acceptance criteria from `ai-status.json`**:
1. FinRL, RLlib, and Ray Tune enabled paths run bounded governed train/search smoke or produce explicit upstream dependency/config evidence.
2. Reward environment, dataset, and artifact schemas are enforced with persisted checksums and an evaluator packet.
3. Outputs remain research artifacts only — no broker session, order routing, paper/canary/live promotion, or capital binding.

**Hard exclusion boundary** (non-negotiable; confirmed by `services/learning/rl/RL_PATH_APPROVAL_GATE.md`):
- No active production training loop, registry/governance write, paper/canary/live execution path, or broker/capital-bound route may open until the RL path approval gate is explicitly changed from `closed` to `approved_for_adapter_work`.
- The gate is currently `closed` (2026-04-17 human gate decision); re-entry requires Qlib reaching `artifact_state=approved` with ≥3 months stable evaluation evidence.

---

## 3. Dependency Map

### 3.1 Upstream Package Dependencies

| Component | Package Pin | Location | Gate Env Var |
|---|---|---|---|
| FinRL | `finrl==0.3.6` | `services/research/finrl/requirements.txt` | `PANTHEON_FINRL_PREP_ENABLED=1` |
| RLlib | `ray[rllib]>=2.9.0,<3.0.0` | `services/research/rllib/requirements.txt` | `PANTHEON_RLLIB_PREP_ENABLED=1` |
| Ray Tune | `ray[tune]>=2.9.0,<3.0.0` | `services/research/rllib/requirements.txt` | `PANTHEON_RAYTUNE_PREP_ENABLED=1` |

All three packages require explicit `--enable-deferred-prep` CLI flag for smoke paths. Workers require the env var. Smoke paths error-closed without these gates.

### 3.2 Canonical Gate Dependencies

| Gate | Current state | Required for full runtime smoke |
|---|---|---|
| RL Path Approval Gate (`services/learning/rl/RL_PATH_APPROVAL_GATE.md`) | `closed` | Must reach `approved_for_adapter_work` before production train/eval |
| Qlib supervised alpha exhaustion | Not met — Qlib not yet at `artifact_state=approved` | Required before RL re-entry |
| Qlib ≥3 months stable evaluation history | Not met | Required before RL re-entry |
| Governed OHLCV dataset (≥2 years intraday, ≥50 instruments) | Not proven | Required for production RLlib/Ray Tune train/eval |
| Sequential decision justification | Not assembled | Required for RL path approval packet |
| RL env contract instantiation (`services/learning/rl/ENV_CONTRACT.md`) | Prep-only scaffold exists | Required before production activation |
| Downstream registry consumer mapping for `rl_policy` artifacts | Prep-only (evaluation/optimizers/contract.md references exist) | Required before promotion beyond `draft` |

### 3.3 Transitive Dependencies

```
P2-RL-UPSTREAM-RUNTIME-SMOKE-001
  └── P2-OSS-ACTIVATE-001 [done]         -- OSS posture and activation research baseline
  └── RL_PATH_APPROVAL_GATE [closed]     -- blocks governed production training
       └── Qlib artifact_state=approved  -- blocked on RS-003 candidate + dataset
       └── 3 months Qlib stable eval     -- not yet started
  └── finrl==0.3.6 package import        -- resolves when installed; currently prep-only
  └── ray[rllib]>=2.9.0 package import   -- resolves when installed; currently prep-only
  └── ray[tune]>=2.9.0 package import    -- resolves when installed; currently prep-only
```

### 3.4 What the Parent Task Can Prove Now (Without Gate Reopening)

The parent task may execute bounded offline smoke using the stub or import-path backends — these do not require the gate to open and do not trigger governed production training. The explicit gate errors serve as documented `dependency/config evidence` per acceptance criterion 1.

---

## 4. Acceptance Checklist

The following items map to the parent task's three acceptance criteria and the hard exclusion boundary.

### Criterion 1 — Bounded governed runtime smoke or explicit upstream dependency/config evidence

| Item | Expected evidence | Current posture |
|---|---|---|
| AC-1.1 FinRL smoke path runs (stub or import-path backend) under explicit gate | `run_finrl_workflow()` completes with `artifact_state=draft`, `gate_state=closed`, explicit `--enable-deferred-prep` gate required | Prep-only scaffold confirmed runnable. Stub backend verified 2026-05-01 (see §6). |
| AC-1.2 RLlib smoke path runs under explicit gate | `run_rllib_workflow()` completes with `artifact_state=draft`, `gate_state=closed` | Prep-only scaffold confirmed runnable. Stub backend verified 2026-05-01 (see §6). |
| AC-1.3 Ray Tune smoke path runs under explicit gate | `run_ray_tune_workflow()` completes with `artifact_state=draft`, `gate_state=closed` | Prep-only scaffold confirmed runnable. Stub backend verified 2026-05-01 (see §6). |
| AC-1.4 Upstream package import failure produces explicit error, not silent stub delegation | `FinRLPPOBackend` / `RLlibPPOBackend` / `RayTuneImportBackend` raise `FinRLDeferredPrepError` / `RLlibDeferredPrepError` with explicit message if package not installed | Verified in adapter code: `FinRLPPOBackend.train()` and `RLlibPPOBackend.train_and_evaluate()` raise on `ImportError`. |
| AC-1.5 Worker env gate enforced without explicit env var | `DeferredPrepGate.require_env()` raises `EnvironmentError` if `PANTHEON_*_PREP_ENABLED` not set | Verified in adapter code for all three components. |
| AC-1.6 CLI gate enforced for smoke paths | `DeferredPrepGate.require_cli_flag()` raises `EnvironmentError` without `--enable-deferred-prep` | Verified in adapter code for FinRL and RLlib. |

### Criterion 2 — Reward environment, dataset, and artifact schemas enforced with checksums and evaluator packet

| Item | Expected evidence | Current posture |
|---|---|---|
| AC-2.1 Required OHLCV fields validated | Adapter raises on missing `open/high/low/close/volume` fields | `REQUIRED_OHLCV_FIELDS` check present in `GovernedFinRLPolicyAdapter.prepare()` and `GovernedRLlibTrainEvalAdapter.prepare()`. |
| AC-2.2 Minimum instrument count enforced | Adapter raises when fewer than `MIN_INSTRUMENTS` (2) instruments provided | Enforced in all three adapters. |
| AC-2.3 Minimum period count enforced | Adapter raises when fewer than `MIN_PERIODS` + lookback periods present per instrument | Enforced per instrument in all three adapters. |
| AC-2.4 Artifact bundle carries SHA-256 checksum | Registry entry includes `checksum: sha256:<hex>` field | `_sha256_json()` applied to artifact bundle in `_build_registry_entry()` for both FinRL and RLlib adapters. |
| AC-2.5 Evaluator/candidate packet produced with gate-state=closed | `candidate_packet` includes `gate_state=closed`, `allowed_next_action=offline_registry_review_only`, and required check list | Present in `_build_candidate_packet()` for both FinRL and RLlib. |
| AC-2.6 Decision focus validated against allowed set | Adapter raises on invalid `decision_focus` value | `ALLOWED_DECISION_FOCUS` checked in all three adapters. |
| AC-2.7 Reward schema validated for RLlib | `reward_fn_config` fields type-checked; missing fields fall back to `DEFAULT_REWARD_SPEC` | Present in `GovernedRLlibTrainEvalAdapter._normalize_reward_spec()`. |

### Criterion 3 — Research-only artifact output; no broker/order/live/paper/capital path

| Item | Expected evidence | Current posture |
|---|---|---|
| AC-3.1 `artifact_state=draft` output only | Registry entry `artifact_state` must be `draft`; `deployment_stage` must be `none` | Hardcoded in all adapters. Verified in §6. |
| AC-3.2 `gate_state=closed` in governance block | `artifact_bundle.governance.gate_state` must be `closed` | Hardcoded in all adapters. Verified in §6. |
| AC-3.3 `direct_live_influence=false` in governance block | `artifact_bundle.governance.direct_live_influence` must be `False` | Present in all adapters. |
| AC-3.4 `lean_consumption=scoring_only_not_direct_action` | Governance block must restrict Lean usage to scoring-only | Present in all adapters. |
| AC-3.5 No broker session, order routing, or capital binding path reachable from smoke | Adapter outputs remain in-memory envelopes only; no broker/paper/canary/live dispatch path | Confirmed: adapters return `FinRLRunResult` / `RLlibRunResult` / `RayTuneRunResult` in-memory only; no network call, registry write, or broker dispatch is present in deferred-prep paths. |
| AC-3.6 Candidate packet allowed_next_action is offline review only | `candidate_packet.allowed_next_action` must be `offline_registry_review_only` (FinRL/RLlib) or `offline_search_review_only` (Ray Tune) | Hardcoded in all three adapters. FinRL and RLlib use `offline_registry_review_only`; Ray Tune uses `offline_search_review_only`. Both values preserve the research-only/no-broker boundary. Reviewer caveat: parent owner (Codex) should preserve this naming difference in mainline implementation — see NB-002. |

---

## 5. Component Readiness Summary

| Component | Checklist status | Package pin | Governed adapter | Stub backend | Import-path backend | Explicit gate | Smoke passes (stub) |
|---|---|---|---|---|---|---|---|
| FinRL | `criteria-defined` | `finrl==0.3.6` | `GovernedFinRLPolicyAdapter` | `StubFinRLBackend` | `FinRLPPOBackend` | `PANTHEON_FINRL_PREP_ENABLED=1` + `--enable-deferred-prep` | Yes — verified 2026-05-01 |
| RLlib | `version-pinned` | `ray[rllib]>=2.9.0,<3.0.0` | `GovernedRLlibTrainEvalAdapter` | `StubRLlibBackend` | `RLlibPPOBackend` | `PANTHEON_RLLIB_PREP_ENABLED=1` + `--enable-deferred-prep` | Yes — verified 2026-05-01 |
| Ray Tune | `version-pinned` | `ray[tune]>=2.9.0,<3.0.0` | `GovernedRayTuneSearchAdapter` | `StubRayTuneBackend` | `RayTuneImportBackend` | `PANTHEON_RAYTUNE_PREP_ENABLED=1` + `--enable-deferred-prep` | Yes — verified 2026-05-01 |

**Blocking gate**: All three components share the same RL path approval gate (currently `closed`). Governed production training/search cannot proceed until Qlib reaches `artifact_state=approved` with ≥3 months stable evaluation evidence and the full re-entry evidence packet is assembled and approved.

---

## 6. Smoke Verification Evidence

Verification performed by Claude2 on 2026-05-01 using the stub backends (offline, no package install required).

**Command pattern used**:
```python
python3 -c "
import sys; sys.path.insert(0, 'services/research/<component>')
from adapter.<module> import run_<component>_workflow
result = run_<component>_workflow(sample_dataset)
assert result.registry_entry['artifact_state'] == 'draft'
assert result.registry_entry['deployment_summary']['current_stage'] == 'none'
assert result.artifact_bundle['governance']['gate_state'] == 'closed'
"
```

**Results**:

| Component | Outcome | artifact_state | deployment_stage | gate_state | backend used |
|---|---|---|---|---|---|
| FinRL | OK | `draft` | `none` | `closed` | `stub_finrl` |
| RLlib | OK | `draft` | `none` | `closed` | `stub_rllib` |
| Ray Tune | OK | `draft` | `none` | `closed` | `stub_ray_tune` |

**FinRL stub metrics** (sample dataset, 2 instruments, 19 periods):
- `num_steps`: 16, `num_instruments`: 2
- `mean_reward_proxy`: 0.03587415, `reward_proxy_stddev`: 0.00235096
- `max_action_prior`: 0.90626 (exit_timing, hold-biased)

**RLlib stub metrics** (sample dataset, 2 instruments, 24 periods):
- `train_steps`: 14, `eval_steps`: 7
- `artifact_state`: draft, train/eval split present, rollout summary produced

**Ray Tune stub metrics** (sample dataset, 2 instruments, 24 periods):
- `artifact_state`: draft, `backend`: stub_ray_tune
- `search_result` fields: `backend`, `best_trial`, `notes`, `run_id`, `search_space_schema`, `summary`, `trial_results`

---

## 7. Open Items and Non-Blocking Notes

| ID | Item | Priority | Owner |
|---|---|---|---|
| NB-001 | `RayTuneSearchResult` has no `.metrics` attribute — `search_result.summary` or `.best_trial` should be used instead in any tooling that mirrors the FinRL/RLlib pattern. This is not a blocker; it is a naming divergence to note. | Non-blocking | Parent task owner (Codex) at implementation time |
| NB-002 | **Reviewer caveat (Codex, 2026-05-01):** Ray Tune `_build_candidate_packet()` uses `allowed_next_action=offline_search_review_only` rather than `offline_registry_review_only` used by FinRL/RLlib. This does not break the research-only/no-broker boundary, but the naming difference should be preserved in the mainline implementation. Parent task should verify the full candidate packet shape and document this naming divergence explicitly. | Non-blocking | Parent task owner (Codex) at mainline implementation time |
| NB-003 | Production package install not verified in this sidecar — `FinRLPPOBackend` and `RLlibPPOBackend` import-path smoke requires packages installed in Docker containers. If packages fail to import, explicit `FinRLDeferredPrepError`/`RLlibDeferredPrepError` is raised (constitutes `dependency/config evidence` per AC-1). | Non-blocking | Docker smoke environment (Gemini/Gemini2 lane) |
| NB-004 | RL gate is `closed`; this sidecar does not recommend opening it. Re-entry packet assembly remains Copilot's lane per `RL_PATH_APPROVAL_GATE.md`. | Informational | Copilot (LP-005/RL path owner) |

---

## 8. What the Parent Task Owner (Codex) Should Do

1. **Verify stub-backend smoke passes** end-to-end in the CI/container environment (NB-003 above covers the install-path case).
2. **Run import-path backends** (`FinRLPPOBackend`, `RLlibPPOBackend`, `RayTuneImportBackend`) when packages are installed, capturing either:
   - successful bounded offline run evidence, or
   - explicit `FinRLDeferredPrepError`/`RLlibDeferredPrepError` import failure message as dependency evidence.
3. **Record artifact checksums and evaluator packets** from at least one run per component as evidence artifacts.
4. **Confirm no broker/paper/canary/live/capital path was touched** and document this explicitly in the task delivery message.
5. **Do not attempt to open the RL approval gate** as part of this task — gate change is Copilot's lane with full re-entry evidence packet.

---

## 9. Canonical References

| Document | Role |
|---|---|
| `services/learning/rl/RL_PATH_APPROVAL_GATE.md` | RL activation gate — currently `closed`; defines re-entry evidence requirements |
| `services/learning/rl/PATH_DEFINITION.md` | RLlib/Ray Tune runtime boundary (LP-005) |
| `services/learning/rl/ENV_CONTRACT.md` | RL environment contract |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Component posture table and follow-up task map |
| `services/learning/OSS_ACTIVATION_NOTES.md` | Control surface read and component-level activation posture |
| `OSS_INTEGRATION_CHECKLIST.md` | Per-row checklist status |
| `services/research/finrl/adapter/finrl_adapter.py` | GovernedFinRLPolicyAdapter, StubFinRLBackend, FinRLPPOBackend |
| `services/research/rllib/adapter/rllib_adapter.py` | GovernedRLlibTrainEvalAdapter, StubRLlibBackend, RLlibPPOBackend |
| `services/research/rllib/adapter/ray_tune_adapter.py` | GovernedRayTuneSearchAdapter, StubRayTuneBackend, RayTuneImportBackend |
| `support/sidecars/BP5-OSS-004/BP5-OSS-004-SIDECAR-ACCEPTANCE.md` | Prior OSS activation acceptance packet (context) |

---

*This packet is a support artifact only. It does not modify canonical truth, change activation gate state, or authorize any production training, broker routing, paper/canary/live deployment, or capital binding. All findings are prep-only and research-scoped.*
