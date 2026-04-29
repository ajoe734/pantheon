# SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC Evidence

**Task ID**: `SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC`  
**Owner**: Codex2  
**Reviewer**: Claude  
**Prepared**: 2026-04-29  
**Scope**: canonical and derived truth sync for dormant OSS scaffolds after activation-gated prep work.

## Truth Sync Summary

This task records landed dormant/pre-activation scaffolds without changing their activation state.

- `RESEARCH_BACKEND_MATURITY_MATRIX.md` now says OpenClaw has a fail-closed runtime-adoption scaffold, while broker sessions, paper/canary/live routes, capital binding, and execution-kernel use remain closed.
- `OSS_INTEGRATION_CHECKLIST.md` now records TRL's 2026-04-29 evidence as 29 unit tests plus smoke assertions, and distinguishes landed FinRL and W&B prep-only scaffolds from production activation.
- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` now states that the FinRL dormant adapter, worker, Dockerfile, examples, and explicit-gate smoke path are landed while outputs remain draft/none and non-writing.
- `OPENCLAW_RUNTIME_CONTRACT.md` now carries the 2026-04-29 repo truth that the runtime-adoption scaffold exists but does not authorize broker/session/capital or paper/canary/live execution paths.

No SDK-backed W&B backend, governed RL train/eval lane, OpenClaw execution-kernel path, registry write path, governance write path, paper/canary/live route, or capital-bound route was enabled.

## Acceptance Mapping

| Acceptance item | Evidence |
|---|---|
| Docs do not claim missing adapters when dormant code exists | OpenClaw, TRL, FinRL, RLlib/Ray Tune, and W&B rows now identify landed scaffold/preflight/prep surfaces. |
| Docs do not claim production activation for gated rows | Each edited row pairs scaffold presence with the closed gate: OpenClaw contract boundary, Qlib RS-003/dataset/StrategySpec gates, TRL FB-002/preference/downstream gates, RL approval gate, and W&B re-entry gate. |
| Current-work records not enabled but development allowed truth | The task handoff message records the same not-enabled/development-allowed distinction and will be mirrored into `current-work.md` by `scripts/ai-status.sh`. |
| Future activation gates remain explicit and dated | W&B retains earliest eligible reopen 2026-05-15; RL remains gated until Qlib is approved and stable for 3 months; Qlib/TRL gate names remain explicit. |

## Verification

Commands run from `/home/edna/code/pantheon`:

```bash
git diff --check -- RESEARCH_BACKEND_MATURITY_MATRIX.md OSS_INTEGRATION_CHECKLIST.md services/learning/DEFERRED_OSS_ACTIVATION_MAP.md OPENCLAW_RUNTIME_CONTRACT.md services/registry/experiments/WANDB_ACTIVATION.md
python3 scripts/smoke_dormant_oss_matrix.py --json-out /tmp/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC.matrix.json
rg -n "finish repo-authoritative runtime adoption|runtime adoption is tracked separately|16 unit tests|missing dormant scaffold|adapter missing" RESEARCH_BACKEND_MATURITY_MATRIX.md OSS_INTEGRATION_CHECKLIST.md services/learning/DEFERRED_OSS_ACTIVATION_MAP.md OPENCLAW_RUNTIME_CONTRACT.md services/registry/experiments/WANDB_ACTIVATION.md
```

Results:

- Markdown diff check passed.
- Dormant OSS smoke matrix passed: 7 rows, 7 acceptable, `gate_state=closed` for 7/7, `activated=false` for 7/7.
- Stale/missing-scaffold wording scan returned no matches.

Evidence JSON: `/tmp/SVC-OSS-ACTIVATION-GATED-TRUTH-SYNC.matrix.json`.
